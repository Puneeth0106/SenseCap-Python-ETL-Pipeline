# SenseCAP Streaming Pipeline

Loads soil temperature, moisture, and conductivity readings from the SenseCAP
OpenAPI into a Supabase Postgres database. Two LoRaWAN devices: Alpha and Beta.

```text
SenseCAP API -> daily_load.py (GitHub Actions) -> sensor_telemetry -> sensor_readings_10min view -> dashboard
```

- Every 10 minutes: refetch the last 3 hours.
- Once a day: refetch the last 72 hours.
- The upsert makes reruns safe. Gaps heal on the next run.
- Every run is logged to `ingest_runs` and pings Healthchecks.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then fill in the values
```

Apply the migrations in order in the Supabase SQL editor:
`migrations/001_init.sql`, `002_views.sql`, `003_latest_bucket_values.sql`.

## Usage

```bash
python daily_load.py --hours 3      # same as the 10 minute job
python daily_load.py --hours 72     # same as the nightly job
python daily_load.py --hours 2160   # 90 day backfill
python main.py                      # manual CSV export to data/ (dev tool)
```

To fill a gap, run `daily_load.py` with an `--hours` value that covers it.
On GitHub, use the workflow's "Run workflow" button and set `hours`.

## Development

```bash
ruff check .
python -m pytest
```

Tests use fixtures only. They make no live API or database calls.
CI runs both on every push.

## Layout

| Path | Purpose |
|---|---|
| `sensecap_client.py` | SenseCAP fetch, retry, and backward pagination |
| `telemetry_store.py` | `to_utc`, `to_rows`, `upsert_rows`, run logging |
| `daily_load.py` | Production loader, `--hours N` |
| `main.py` | Manual CSV export tool |
| `migrations/` | SQL schema and views, applied in order |
| `tests/` | Unit tests and a captured API response |
| `.github/workflows/` | Scheduled sync and CI |
| `docs/STREAMING_PIPELINE_PLAN.md` | Build checklist and status |
| `docs/BATCH_PIPELINE_ARCHITECTURE.md` | Detailed design reference |
| `docs/NOTES.md` | One entry per checklist section |

## Status

Built: schema and views, shared store, loader, scheduled workflow, Healthchecks pings.

Not yet verified (checklist sections 4 to 7):

- 90 day backfill matches the October CSV, and a rerun adds no rows.
- Scheduled runs work on GitHub with the secrets set.
- Alerts fire when the job fails or Alpha goes quiet.
- Recovery after an interrupted run, and 24 hours unattended.
- Readings arrive within 20 minutes.

Beta is offline and marked inactive. It loads on its own when it returns.

Still to write (section 8): key rotation steps and what each alert means.
