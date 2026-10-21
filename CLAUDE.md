# SenseCAP Streaming Pipeline

## What this is

Ingests soil sensor telemetry (temperature, moisture, conductivity) from the
SenseCAP OpenAPI for two LoRaWAN devices (Alpha, Beta) and turns it into
something queryable.

- Build order and status: the checklist in `STREAMING_PIPELINE_PLAN.md`. Follow it top to bottom.
- Detailed design reference (schema, upsert, workflow, read-only role): `BATCH_PIPELINE_ARCHITECTURE.md`.
- Where the two disagree, the checklist wins.

## End goal

An unattended pipeline on GitHub Actions: every 10 minutes it refetches the last
3 hours, and once a day the last 72 hours, and upserts into a managed Supabase
PostgreSQL database. Readers use the `sensor_readings_10min` view through a
read-only role. Target: a reading is in the database within 20 minutes of the
sensor sending it. An MQTT live stream is optional (checklist section 9).

Five rules:
1. One table, one key (`timestamp, device_eui, metric_id`), one upsert.
2. One `to_utc()` used everywhere.
3. Every run refetches a fixed window. Never "since last row" or `MAX(timestamp)`.
4. The raw table is never edited. Readers use the 10 minute view.
5. Every run writes to `ingest_runs` and pings Healthchecks.

## How we work (learning loop)

Puneeth wants to understand everything without writing the code himself. For
every checklist section, in this order:

1. Before: explain in 5 lines what is being built and why.
2. Build: write the code, with a one line comment above every function.
3. Walkthrough: explain the file top to bottom in plain words.
4. Run: give Puneeth one command to run. He pastes the output.
5. Read: explain his output line by line.
6. Check: ask 3 quick questions to confirm it stuck.
7. Commit: one small commit per checklist item, then tick the item in the plan.

Also add a short entry to `NOTES.md` per section: what it does, why, the
command, what can break. Keep replies short and specific. No em dashes.

## Current state (Oct 7, 2026)

Built:
- `sensecap_client.py` — the shared extraction engine. Fetches telemetry from
  SenseCAP, retries transient failures (`tenacity`), classifies SenseCAP's
  body-level `code` field into retryable vs. permanent errors, and handles
  the backward-pagination trick needed because the API's 1000-point cap is
  shared across all three metrics per call (see `compute_next_boundary`).
- `main.py` — manual CSV export tool. Pulls a hardcoded custom date range and
  writes pivoted monthly CSVs. This is a dev utility, not production — see
  below.
- `tests/` — unit tests for the pagination/timestamp logic, including a
  regression test run against a real captured API response
  (`tests/fixtures/sample_response.json`).

Done: Section 0 (Oct 21, 2026). Supabase project on the session pooler
(`us-west-2`, port 5432), Healthchecks checks `job-ran` and `alpha-fresh`
created but not pinged yet, repo is public, history checked for keys.

Not built yet (see the checklist):
- `telemetry_store.py`: shared `to_utc`, `to_rows`, `upsert_rows`, run logging.
- `daily_load.py --hours N`: the production loader. Does not exist yet.
- `migrations/002_views.sql`: the `sensor_readings_10min` view.
- Applying the migrations to Supabase, and the `telemetry_reader` role.
- GitHub Actions workflow (`.github/workflows/telemetry_sync.yml`).
- Healthchecks alerts (`job-ran`, `alpha-fresh`).

Known sensor state (Oct 7, 2026):
- Beta has been offline since before Sept 1, 2026. Known and accepted. Do not
  chase it. Set `is_active = false`; it loads on its own when it returns.
- Alpha has no data on Sept 2 to 10 and Sept 12 to 30. The gaps are real, not
  a script bug.

## Script roles — don't conflate these

| Script | Status | Range | Output |
|---|---|---|---|
| `main.py` | exists | custom, hardcoded `MONTHS` list | CSV files in `data/` |
| `daily_load.py` | **not built** | fixed window, `--hours N` (3 every 10 minutes, 72 daily, 2160 for the 90 day backfill) | Supabase Postgres |

**The loader never starts from `MAX(timestamp)`.** It refetches a fixed window
and the upsert makes that safe. There is no separate backfill script: a
backfill is `daily_load.py` with a larger `--hours`. Both scripts reuse
`sensecap_client.py` rather than reimplementing fetch/pagination logic.

## Repo layout

Flat layout at repo root (deliberately not nested under `src/` — this was
reverted from a nested `src/local-data-downloaders/` layout per explicit
instruction; keep it flat):

```
sensecap_client.py   # shared SenseCAP fetch + pagination engine
main.py               # manual CSV export tool
requirements.txt
conftest.py            # empty; puts repo root on sys.path for pytest
tests/
  test_sensecap_client.py
  fixtures/sample_response.json   # real captured API response
STREAMING_PIPELINE_PLAN.md        # build checklist, follow this
BATCH_PIPELINE_ARCHITECTURE.md    # detailed design reference
migrations/001_init.sql           # tables and wide view
data/                             # CSV exports from main.py, git-ignored
.env                    # SENSECAP_API_ID, SENSECAP_API_KEY, SENSECAP_BASE_URL, DEVICE_EUIS,
                        # DATABASE_URL, HC_JOB_URL, HC_ALPHA_URL
.venv/                  # local Python env, git-ignored
NOTES.md                # one entry per checklist section
```

## Things learned the hard way (don't relitigate)

- **No TimescaleDB, no Docker.** Both were evaluated and dropped. At ~630k
  rows/year the extension buys nothing, it narrows managed-host options, and a
  local container is pointless when GitHub Actions can't reach a laptop anyway.
  The DB is Supabase; the schema is portable standard Postgres. See §3.3.

- SenseCAP timestamps are ISO-8601 strings with a trailing `Z`
  (`"2026-08-27T20:47:12.582Z"`), confirmed against a live API call — not
  epoch milliseconds. Parse with `parse_sensecap_timestamp`, not raw
  `pd.to_datetime`.
- SenseCAP returns HTTP 200 even on logical failure; the real status is in
  the JSON body's `code` field (`"0"` = success). Only `429`/`500`/`503`
  body codes are retryable — everything else (bad credentials, bad device)
  is permanent and should not be retried.
- The pagination boundary must step to the **newest** of each metric's
  oldest returned point, not the oldest — otherwise a metric that hits the
  1000-point cap shallower than the others has its un-returned tail silently
  skipped. This is covered by
  `test_compute_next_boundary_steps_to_newest_of_oldest_per_metric`.
- Env var names are `SENSECAP_API_ID` / `SENSECAP_API_KEY` /
  `SENSECAP_BASE_URL` (not the old `API_ID`/`API_KEY`/`BASE_URL`).

## Running tests

```
python3 -m pytest . -q
```

No live API calls in the test suite — it runs against fixtures and pure
logic only.

## Consumption layer (§14 of the architecture doc)

Resolved design: internal dashboard, single local viewer, direct DB
connection (no API layer), tool undecided (Grafana/Metabase preferred,
Streamlit/Dash fallback), 10 minute freshness, managed cloud Postgres hosting.
It reads the `sensor_readings_10min` view.
The dashboard uses a dedicated read-only `telemetry_reader` role — never the
ETL's write-capable credentials.
