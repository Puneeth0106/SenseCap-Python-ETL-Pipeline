# Production Batch Pipeline Technical Architecture

**System:** SenseCAP telemetry ingestion for local soil sensors  
**Stack:** Python 3.11, SenseCAP OpenAPI, managed PostgreSQL (Supabase), GitHub Actions  
**Primary script:** `daily_load.py`  
**Source script to refactor from:** `main.py`

This document defines the target production architecture for converting the current local CSV downloader into an unattended daily batch ingestion pipeline.

The design goal is simple: every scheduled run should fetch any missing SenseCAP telemetry, handle duplicate or repeated runs safely, preserve the existing API pagination behavior, and load queryable time-series records into PostgreSQL.

---

## 1. Current State

The current downloader in `main.py` is a historical CSV export tool.

Current behavior:

- Reads `API_ID` and `API_KEY` from environment variables.
- Calls the SenseCAP `list_telemetry_data` endpoint.
- Handles SenseCAP's 1000-point response cap by paging backward from `end_dt`.
- Converts the SenseCAP response into metric records.
- Uses Pandas to pivot data into a wide CSV format.
- Writes monthly CSV files under `data/local-sensors-data/months`.

Production gaps:

- No database persistence.
- No high-watermark state.
- No CI/CD workflow.
- No retry handling for transient API failures.
- No structured logs or ingest-run audit table.
- No automated alerting.
- No production dependency definition for database writes.

The existing pagination logic is valuable and must be preserved. The API limit is shared across all returned metric groups, so the production loader must continue paging backward using the newest value among each metric group's oldest returned timestamp.

---

## 2. Target Architecture

### 2.1 Component Overview

```text
GitHub Actions schedule/manual trigger
        |
        v
Python daily_load.py
        |
        |-- reads device config and secrets
        |-- queries database high watermark
        |-- fetches SenseCAP telemetry with overlap
        |-- parses raw SenseCAP response into narrow records
        |-- bulk upserts records into PostgreSQL
        |-- writes run status into ingest_runs
        v
PostgreSQL (Supabase)
```

### 2.2 Responsibility Boundaries

GitHub Actions:

- Owns scheduling.
- Provides isolated runtime.
- Injects secrets.
- Prevents overlapping runs with workflow concurrency.
- Sends failure notifications.

Python ETL:

- Owns extraction, transformation, validation, and loading.
- Preserves SenseCAP pagination constraints.
- Provides retry behavior.
- Emits structured logs.
- Records ingest-run metadata.

PostgreSQL:

- Stores canonical telemetry.
- Enforces idempotency.
- Supports time-series indexing and compression.
- Provides the high-watermark state through existing data.

---

## 3. Data Model

### 3.1 Design Choice: Narrow Telemetry Table

Use a narrow, long-format table instead of a wide table.

Recommended row shape:

```text
timestamp | device_eui | device_alias | metric_id | metric_name | value
```

Reasons:

- New SenseCAP measurement IDs can be stored without schema migration.
- Upsert keys remain stable.
- Querying one metric across devices is straightforward.
- Repeated dimensions (device, metric) stay cheap to index and filter.

Analytics consumers that prefer wide format should use a SQL view, not the raw ingest table.

### 3.2 Schema

```sql
CREATE TABLE IF NOT EXISTS sensor_devices (
    device_eui TEXT PRIMARY KEY,
    device_alias TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sensor_telemetry (
    timestamp TIMESTAMPTZ NOT NULL,
    device_eui TEXT NOT NULL REFERENCES sensor_devices(device_eui),
    device_alias TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value DOUBLE PRECISION,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (timestamp, device_eui, metric_id)
);

CREATE INDEX IF NOT EXISTS ix_sensor_telemetry_device_time
    ON sensor_telemetry (device_eui, timestamp DESC);

CREATE INDEX IF NOT EXISTS ix_sensor_telemetry_metric_time
    ON sensor_telemetry (metric_name, timestamp DESC);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    trigger_source TEXT,
    devices_requested INTEGER NOT NULL DEFAULT 0,
    api_calls INTEGER NOT NULL DEFAULT 0,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_loaded INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
```

### 3.3 Why Plain PostgreSQL (No TimescaleDB)

TimescaleDB was evaluated and deliberately dropped. Projected volume is about
630,000 rows per year even at 5-minute sampling (2 devices x 3 metrics x 288
readings/day x 365). A standard indexed PostgreSQL table handles that without
strain; hypertable partitioning and columnar compression only start paying off
several orders of magnitude higher.

Dropping it also removes a hosting constraint — the extension is not offered by
every managed Postgres provider — and removes the need for a local Docker
container during development, since Supabase is reachable from both GitHub
Actions and a local machine.

The schema above is portable standard SQL. If volume ever justifies it,
`create_hypertable()` can be applied to the populated table in a later migration.

Do not add an automatic retention policy unless raw telemetry can be safely discarded.

### 3.4 Wide Analytics View

```sql
CREATE OR REPLACE VIEW sensor_telemetry_wide AS
SELECT
    timestamp,
    device_eui,
    device_alias,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Temperature') AS soil_temperature,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Moisture') AS soil_moisture,
    MAX(value) FILTER (WHERE metric_name = 'Soil_Conductivity') AS soil_conductivity
FROM sensor_telemetry
GROUP BY timestamp, device_eui, device_alias;
```

---

## 4. ETL Runtime Design

### 4.1 Runtime Inputs

Required environment variables:

```text
SENSECAP_API_ID
SENSECAP_API_KEY
DATABASE_URL
```

Optional environment variables:

```text
SENSECAP_BASE_URL=https://sensecap.seeed.cc/openapi
LOOKBACK_DAYS_FOR_NEW_DEVICE=30
OVERLAP_HOURS=24
PAGE_LIMIT=1000
REQUEST_TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
```

The script should fail fast if required environment variables are missing.

### 4.2 Device Configuration

Initial hardcoded config is acceptable for this project size:

```python
SENSOR_CONFIG = {
    "2CF7F1C063700148": "Alpha",
    "2CF7F1C060800087": "Beta",
}
```

For production, the script should upsert this config into `sensor_devices` before telemetry loading. This keeps the database aware of known devices and makes joins predictable.

### 4.3 Metric Mapping

```python
MEASUREMENTS = {
    "4102": "Soil_Temperature",
    "4103": "Soil_Moisture",
    "4108": "Soil_Conductivity",
}
```

Unknown metric IDs should not be dropped. They should be stored as `ID_<metric_id>` so new SenseCAP measurements are visible without a code deploy.

---

## 5. High-Watermark Strategy

### 5.1 Requirement

The loader must resume from the latest successfully loaded telemetry for each device.

Basic high-watermark query:

```sql
SELECT MAX(timestamp)
FROM sensor_telemetry
WHERE device_eui = %s;
```

### 5.2 Overlap Window

Do not fetch from exactly `MAX(timestamp)`.

Production runs should fetch from:

```text
start_time = max_timestamp - overlap_window
```

Recommended overlap:

```text
24 hours
```

Reasons:

- Handles late-arriving telemetry.
- Handles partial previous job failures.
- Handles metric groups that lag behind other metric groups.
- Makes manual reruns safe.

The database primary key and upsert behavior handle duplicates created by overlap.

### 5.3 New Device Bootstrap

If a device has no existing telemetry:

```text
start_time = now_utc - LOOKBACK_DAYS_FOR_NEW_DEVICE
```

Recommended default:

```text
30 days
```

This should remain inside the SenseCAP API's allowed retention and query limits.

---

## 6. SenseCAP Extraction Algorithm

### 6.1 API Request

Endpoint:

```text
GET {SENSECAP_BASE_URL}/list_telemetry_data
```

Parameters:

```text
device_eui=<device EUI>
time_start=<start timestamp in milliseconds>
time_end=<end timestamp in milliseconds>
limit=1000
```

Authentication:

```text
HTTP Basic Auth using SENSECAP_API_ID and SENSECAP_API_KEY
```

### 6.2 Pagination Rules

The SenseCAP API response cap is shared across all metric groups. A response can be saturated even if no individual metric has 1000 points.

Correct behavior:

1. Start with `current_end = end_time`.
2. Request `[start_time, current_end]`.
3. Parse all returned metric groups.
4. Count total points across all groups.
5. If total points is less than `PAGE_LIMIT`, the requested range is drained.
6. If total points equals `PAGE_LIMIT`, move `current_end` backward.
7. The next `current_end` should be the newest timestamp among the oldest returned timestamp from each non-empty metric group.
8. If the calculated boundary does not move backward, subtract one second to avoid an infinite loop.

This preserves the important behavior already implemented in `main.py`.

### 6.3 Retry Policy

Use retries only for transient failures:

- HTTP 408
- HTTP 429
- HTTP 500
- HTTP 502
- HTTP 503
- HTTP 504
- connection timeout
- read timeout

Recommended policy:

```text
max attempts: 5
wait: exponential backoff with jitter
minimum wait: 2 seconds
maximum wait: 60 seconds
```

Do not retry permanent failures such as invalid credentials.

### 6.4 Request Timeout

Every API call must set a timeout.

Recommended:

```python
requests.get(..., timeout=30)
```

Without a timeout, GitHub Actions can hang until the job-level timeout is reached.

---

## 7. Transformation Rules

Input response shape from SenseCAP:

```text
data.list[0] = headers
data.list[1] = values grouped by metric
```

Each telemetry point should become one database row:

```python
{
    "timestamp": parsed_timestamp_utc,
    "device_eui": device_eui,
    "device_alias": alias,
    "metric_id": metric_id,
    "metric_name": metric_name,
    "value": value,
}
```

Rules:

- Convert timestamps to timezone-aware UTC `datetime` values before database insert.
- Keep values as numeric values.
- Drop exact duplicate records within the same API run before insert.
- Preserve unknown metrics using the `ID_<metric_id>` naming convention.
- Do not pivot in the ingestion job.

---

## 8. Load Strategy

### 8.1 Transaction Scope

Use one transaction per device.

Benefits:

- Failure in one device does not corrupt another device's load.
- Logs can identify exactly which device failed.
- The job can be safely rerun due to upsert idempotency.

### 8.2 Bulk Upsert

Use `psycopg`/`psycopg2` bulk insert helpers or SQLAlchemy Core batch insert.

Recommended upsert:

```sql
INSERT INTO sensor_telemetry (
    timestamp,
    device_eui,
    device_alias,
    metric_id,
    metric_name,
    value
)
VALUES %s
ON CONFLICT (timestamp, device_eui, metric_id)
DO UPDATE SET
    device_alias = EXCLUDED.device_alias,
    metric_name = EXCLUDED.metric_name,
    value = EXCLUDED.value,
    updated_at = now();
```

Use `DO UPDATE` instead of `DO NOTHING` so corrected telemetry values from the upstream API can be reflected in the database.

### 8.3 Batch Size

Recommended insert batch size:

```text
500 to 5000 rows
```

For the current Alpha/Beta sensor volume, 1000 rows per database batch is reasonable.

---

## 9. GitHub Actions Orchestration

Create:

```text
.github/workflows/daily_telemetry_sync.yml
```

Recommended workflow:

```yaml
name: Daily SenseCAP Telemetry Sync

on:
  schedule:
    - cron: '0 2 * * *'
  workflow_dispatch:

concurrency:
  group: daily-sensecap-telemetry-sync
  cancel-in-progress: false

jobs:
  run-etl:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
          cache-dependency-path: |
            requirements.txt

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run telemetry ETL
        env:
          SENSECAP_API_ID: ${{ secrets.SENSECAP_API_ID }}
          SENSECAP_API_KEY: ${{ secrets.SENSECAP_API_KEY }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          LOG_LEVEL: INFO
        run: python daily_load.py

      - name: Alert on failure
        if: failure()
        uses: rtCamp/action-slack-notify@v2
        env:
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
          SLACK_MESSAGE: Daily SenseCAP telemetry ETL failed. Check GitHub Actions logs.
```

Important notes:

- Add `psycopg2-binary` or `psycopg`, plus `tenacity`, to the dependency set in `requirements.txt`.
- Keep `cancel-in-progress: false`; a running ingestion should finish rather than be interrupted mid-load.

---

## 10. Dependency Requirements

Recommended `requirements.txt`:

```text
requests>=2.31.0
python-dotenv>=1.0.0
psycopg2-binary>=2.9.9
tenacity>=8.2.3
```

Pandas is not required for the production narrow-table loader. The current CSV backfill script can keep using Pandas, but `daily_load.py` should not depend on Pandas unless there is a clear transformation need.

---

## 11. Observability

### 11.1 Structured Logs

Use Python `logging` instead of `print`.

Each run should log:

- run ID
- start time
- device EUI
- device alias
- selected fetch window
- API calls per device
- rows fetched per device
- rows loaded per device
- final max timestamp per device
- total runtime
- failure details

Example log fields:

```text
run_id=... device_eui=... alias=Alpha start=... end=... api_calls=3 rows_fetched=2800 rows_loaded=2796
```

### 11.2 Ingest Run Audit

At startup:

```sql
INSERT INTO ingest_runs (run_id, started_at, status, trigger_source, devices_requested)
VALUES (%s, now(), 'running', %s, %s);
```

On success:

```sql
UPDATE ingest_runs
SET
    finished_at = now(),
    status = 'success',
    api_calls = %s,
    rows_fetched = %s,
    rows_loaded = %s
WHERE run_id = %s;
```

On failure:

```sql
UPDATE ingest_runs
SET
    finished_at = now(),
    status = 'failed',
    error_message = %s
WHERE run_id = %s;
```

### 11.3 Useful Operational Queries

Latest telemetry per device:

```sql
SELECT device_alias, device_eui, MAX(timestamp) AS latest_timestamp
FROM sensor_telemetry
GROUP BY device_alias, device_eui
ORDER BY latest_timestamp DESC;
```

Rows loaded by day:

```sql
SELECT date_trunc('day', ingested_at) AS ingest_day, COUNT(*) AS rows_loaded
FROM sensor_telemetry
GROUP BY ingest_day
ORDER BY ingest_day DESC;
```

Recent failed runs:

```sql
SELECT started_at, finished_at, error_message
FROM ingest_runs
WHERE status = 'failed'
ORDER BY started_at DESC
LIMIT 20;
```

---

## 12. Failure Handling

### 12.1 Failure Classes

Transient API failure:

- Retry with exponential backoff.
- Fail the job only after retries are exhausted.

Database connection failure:

- Fail fast.
- Let GitHub Actions alert.
- Rerun is safe due to upserts.

Partial device failure:

- Roll back the failed device transaction.
- Mark the run failed.
- Rerun later using high-watermark overlap.

Malformed API payload:

- Log the payload shape and device.
- Fail the run unless the malformed payload is an empty valid response.

No data returned:

- This is not automatically a failure.
- Log zero rows and complete successfully if the API response itself is valid.

### 12.2 Idempotency Guarantees

The pipeline is idempotent when:

- The unique key is `(timestamp, device_eui, metric_id)`.
- The extraction window includes overlap.
- Loads use `ON CONFLICT DO UPDATE`.
- Each device load runs inside a transaction.

With these rules, scheduled reruns, manual reruns, and retried jobs should not create duplicate telemetry.

---

## 13. Security

Secrets must be stored only in GitHub Repository Secrets:

```text
SENSECAP_API_ID
SENSECAP_API_KEY
DATABASE_URL
SLACK_WEBHOOK
```

Do not commit `.env` files.

Database recommendations:

- Use a dedicated database user for ingestion.
- Grant only required permissions on the target schema.
- Require SSL in `DATABASE_URL` if the database is remote.
- Rotate SenseCAP and database credentials if logs ever expose them.

The ETL must never log raw credentials or full database URLs.

---

## 14. Consumption Layer

### 14.1 Scope and Decision

This pipeline's job ends at "queryable rows in PostgreSQL." It does not, by itself, make data visible anywhere — a consuming application has to read from the database. The resolved design for this project:

- **Application type:** an internal dashboard for a single viewer (you), run locally. Not public-facing.
- **Read path:** the dashboard connects **directly to PostgreSQL** — no API/backend service in front of it. A direct DB connection is sufficient because there is exactly one consumer, no browser-based public access, and no need to hide the schema behind an API contract. Revisit this if the dashboard later needs to be shared with other people or exposed over the public internet (see §14.4).
- **Tool:** not yet chosen. Any tool that speaks Postgres works against this schema unchanged. A managed BI tool (e.g. Grafana or Metabase) pointed at `sensor_telemetry_wide` is the fastest path to a working dashboard with no custom code; a custom Python app (Streamlit/Dash) is the fallback if more control over layout is needed. This choice does not affect the schema or the ETL and can be deferred.
- **Freshness:** once-daily is sufficient for this data. No change to the §9 cron schedule is needed to support the dashboard. If freshness requirements change later, tighten the cron interval — the batch design does not need to change to support hourly runs, only near-real-time would require a different architecture.
- **Database hosting:** **Supabase** (managed PostgreSQL) — resolved. Reachable both from GitHub Actions runners (for writes) and from your local machine (for reads) over SSL.

### 14.2 Read-Only Database Role

The dashboard must use its own database credentials, separate from the ETL's write-capable role, so a local dashboard config never carries write access it doesn't need.

```sql
CREATE ROLE telemetry_reader WITH LOGIN PASSWORD '<set via secret manager, not committed>';

GRANT CONNECT ON DATABASE <database_name> TO telemetry_reader;
GRANT USAGE ON SCHEMA public TO telemetry_reader;

GRANT SELECT ON sensor_devices TO telemetry_reader;
GRANT SELECT ON sensor_telemetry TO telemetry_reader;
GRANT SELECT ON sensor_telemetry_wide TO telemetry_reader;

-- Ensure future tables/views default to read-only for this role too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO telemetry_reader;
```

The ETL's role (§13) keeps `INSERT`/`UPDATE` on `sensor_telemetry`, `sensor_devices`, and `ingest_runs`; it should not be reused for dashboard access, and `telemetry_reader` should not be granted write access to anything.

Store the resulting read-only connection string locally (e.g. in a local `.env` for the dashboard tool), never in the repo, and require SSL the same way `DATABASE_URL` does for the ETL.

### 14.3 Recommended Read Surface

Point the dashboard at `sensor_telemetry_wide` (§3.4) for charting — it already presents one row per timestamp/device with metrics as columns, which is what most dashboard/BI tools expect. Query `sensor_telemetry` directly only for per-metric drill-down or when a new metric hasn't been added to the wide view yet. `sensor_devices` is the source for device alias/EUI lookups (e.g. a dropdown filter).

### 14.4 Future Escalation Path

If requirements grow beyond "just me, locally," the read path should change before the schema does:

- **Other people need access:** put the dashboard tool on shared, always-on hosting (e.g. a small VM or the BI tool's managed hosting) rather than distributing DB credentials to more people; keep `telemetry_reader` as the single shared read identity, or issue one read-only role per person if per-user audit matters.
- **Public-facing or needs auth/rate limiting:** introduce a thin API layer (e.g. FastAPI) between the frontend and the database instead of direct DB access, and stop distributing `telemetry_reader` credentials to the client entirely.
- **Freshness needs to be near-real-time:** this is out of scope for the batch design in this document; it would require a different ingestion pattern, not just a faster cron.

---

## 15. Backfill Strategy

Keep historical backfills separate from daily ingestion.

Recommended scripts:

```text
main.py          # local CSV/manual historical export
daily_load.py    # production daily incremental database load
backfill_load.py # optional database historical backfill
```

Backfill behavior:

- Accept explicit `START_DATE` and `END_DATE`.
- Reuse the same SenseCAP pagination function.
- Reuse the same transformation and database upsert functions.
- Run manually with `workflow_dispatch` inputs or locally.
- Never rely on the daily high-watermark calculation.

---

## 16. Testing Strategy

### 16.1 Unit Tests

Test:

- timestamp conversion to UTC
- SenseCAP payload parsing
- unknown metric handling
- high-watermark calculation with overlap
- pagination boundary calculation
- duplicate record removal

### 16.2 Integration Tests

Test against a temporary PostgreSQL database:

- schema creation
- telemetry upsert
- corrected value update
- repeated insert idempotency
- high-watermark query

### 16.3 CI Checks

Minimum checks:

```text
python -m compileall .
pytest
```

---

## 17. Implementation Checklist

1. Add database dependencies to `requirements.txt`.
2. Create database migration SQL for `sensor_devices`, `sensor_telemetry`, and `ingest_runs`.
3. Create `daily_load.py`.
4. Move shared SenseCAP extraction/pagination code into reusable functions.
5. Remove Pandas pivot from production ingestion.
6. Implement high-watermark plus overlap.
7. Implement retrying API client with request timeout.
8. Implement bulk database upsert.
9. Add structured logging.
10. Add GitHub Actions workflow with concurrency and timeout.
11. Add failure alerting.
12. Add tests for parsing, pagination, high-watermark, and upsert behavior.
13. Run one manual workflow against a staging database.
14. Validate latest timestamps and row counts.
15. Enable scheduled production run.
16. Create the `telemetry_reader` read-only role and grant SELECT per §14.2.
17. Point the chosen dashboard tool at `sensor_telemetry_wide` using `telemetry_reader` credentials and confirm it renders yesterday's ingested rows.

---

## 18. Acceptance Criteria

The pipeline is production-ready when:

- A manual run loads data into PostgreSQL successfully.
- A second manual run over the same time window creates no duplicate records.
- A corrected value from the same timestamp updates the existing row.
- The workflow cannot overlap with another run.
- A transient API failure is retried.
- A hard failure writes a failed `ingest_runs` record and triggers an alert.
- The latest timestamp per device advances after new telemetry is available.
- The daily job completes within the configured GitHub Actions timeout.
- The dashboard, connected as `telemetry_reader`, can query `sensor_telemetry_wide` and shows the latest ingested timestamp for each device without any write access to the database.
