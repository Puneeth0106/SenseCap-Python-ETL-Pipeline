# SenseCAP Streaming Pipeline

## What this is

Ingests soil sensor telemetry (temperature, moisture, conductivity) from the
SenseCAP OpenAPI for two LoRaWAN devices (Alpha, Beta) and turns it into
something queryable. Full target architecture, schema, and rationale live in
`BATCH_PIPELINE_ARCHITECTURE.md` — that file is the source of truth; this one
is the quick-orientation summary.

## End goal

A daily unattended pipeline (GitHub Actions cron) that fetches only new
telemetry since the last successful run and upserts it into a managed
Supabase PostgreSQL database, plus a local dashboard (Grafana/Metabase, undecided)
reading that database directly through a read-only role. Not real-time —
once-daily freshness is the explicit requirement.

## Current state (as of Phase 0 + Phase 1)

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

Not built yet (Phase 2+):
- `daily_load.py` — the actual production script. Does not exist. This is
  what the GitHub Actions cron will run.
- `backfill_load.py` — optional one-off historical backfill into the DB.
- Applying `migrations/001_init.sql` to Supabase, `ingest_runs` audit table.
- GitHub Actions workflow (`.github/workflows/daily_telemetry_sync.yml`).
- The `telemetry_reader` read-only DB role and the dashboard itself.

## Script roles — don't conflate these

| Script | Status | Range | Output |
|---|---|---|---|
| `main.py` | exists | custom, hardcoded `START_DATE`/`END_DATE` | CSV files |
| `daily_load.py` | **not built** | automatic — `MAX(timestamp)` in DB minus 24h overlap | Supabase Postgres |
| `backfill_load.py` | not built, optional | custom, explicit `START_DATE`/`END_DATE` | Supabase Postgres |

**The production daily job never takes a manual date range.** Custom ranges
are only for `main.py` (CSV, dev use) and `backfill_load.py` (DB, one-off
historical loads). Both future scripts should reuse `sensecap_client.py`
rather than reimplementing fetch/pagination logic.

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
BATCH_PIPELINE_ARCHITECTURE.md    # full target design — source of truth
.env                    # SENSECAP_API_ID, SENSECAP_API_KEY, SENSECAP_BASE_URL, DEVICE_EUIS
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
Streamlit/Dash fallback), daily freshness, managed cloud Postgres hosting.
The dashboard uses a dedicated read-only `telemetry_reader` role — never the
ETL's write-capable credentials.
