# SenseCAP Streaming Pipeline

## What this is

Ingests soil sensor telemetry (temperature, moisture, conductivity) from the
SenseCAP OpenAPI for two LoRaWAN devices (Alpha, Beta) and turns it into
something queryable.

- Build order and status: the checklist in `docs/STREAMING_PIPELINE_PLAN.md`. Follow it top to bottom.
- Detailed design reference (schema, upsert, workflow, read-only role): `docs/BATCH_PIPELINE_ARCHITECTURE.md`.
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

Also add a short entry to `docs/NOTES.md` per section: what it does, why, the
command, what can break. Keep replies short and specific. No em dashes.

## Current state (Nov 5, 2026)

Built (sections 0 to 3, plus the workflow and pings for 5 and 6):
- `sensecap_client.py`: shared fetch engine. Retries transient failures
  (`tenacity`), classifies SenseCAP's body-level `code` into retryable vs.
  permanent, and handles backward pagination for the shared 1000-point cap
  (see `compute_next_boundary`).
- `telemetry_store.py`: `to_utc`, `to_rows`, `upsert_rows`, `start_run`, `finish_run`.
- `daily_load.py --hours N`: the production loader. Pings `job-ran` on success
  and `alpha-fresh` when Alpha's newest row is under 30 minutes old.
- `migrations/001` to `003`, applied to Supabase. `telemetry_reader` role exists.
- `.github/workflows/telemetry_sync.yml`: CI (ruff, pytest) on push, sync on schedule.
- `main.py`: manual CSV export tool. Dev utility, not production.

Next: section 4 (backfill and verify). Not yet verified on GitHub: secrets,
schedules, alerts. No `docs/NOTES.md` entries yet for sections 5 and 6.

Known sensor state (Oct 7, 2026):
- Beta has been offline since before Sept 1, 2026. Known and accepted. Do not
  chase it. Set `is_active = false`; it loads on its own when it returns.
- Alpha has no data on Sept 2 to 10 and Sept 12 to 30. The gaps are real, not
  a script bug.

## Script roles — don't conflate these

| Script | Status | Range | Output |
|---|---|---|---|
| `main.py` | exists | custom, hardcoded `MONTHS` list | CSV files in `data/` |
| `load_csv.py` | exists | any CSV from `main.py` or the old batch downloads | Supabase Postgres (same upsert) |
| `daily_load.py` | exists | fixed window, `--hours N` (3 every 10 minutes, 72 daily, 2136 for the 89 day backfill, SenseCAP keeps only 90 days) | Supabase Postgres |

**The loader never starts from `MAX(timestamp)`.** It refetches a fixed window
and the upsert makes that safe. There is no separate backfill script: a
backfill is `daily_load.py` with a larger `--hours`. Both scripts reuse
`sensecap_client.py` rather than reimplementing fetch/pagination logic.

## Repo layout

Flat layout at repo root (deliberately not nested under `src/` — this was
reverted from a nested `src/local-data-downloaders/` layout per explicit
instruction; keep it flat):

```
sensecap_client.py     # shared SenseCAP fetch + pagination engine
telemetry_store.py     # to_utc, to_rows, upsert_rows, run logging
daily_load.py          # production loader, --hours N
main.py                # manual CSV export tool
conftest.py            # empty; puts repo root on sys.path for pytest
requirements.txt       # runtime deps
requirements-dev.txt   # + pytest, ruff
pyproject.toml         # ruff and pytest config
.env.example           # env var template; real values in .env (git-ignored)
README.md              # setup, usage, status
AGENTS.md              # symlink to CLAUDE.md (for Codex). Edit CLAUDE.md only.
migrations/            # 001_init, 002_views, 003_latest_bucket_values
tests/                 # unit tests + fixtures/sample_response.json
docs/
  STREAMING_PIPELINE_PLAN.md      # build checklist, follow this
  BATCH_PIPELINE_ARCHITECTURE.md  # detailed design reference
  NOTES.md                        # one entry per checklist section
.github/workflows/telemetry_sync.yml
data/                  # CSV exports from main.py, git-ignored
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
ruff check .
python -m pytest
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
