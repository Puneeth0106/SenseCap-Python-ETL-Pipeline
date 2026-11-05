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
python daily_load.py --hours 2136   # 89 day backfill (SenseCAP keeps 90 days)
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

## Runbook

### What each alert means

| Email | Meaning | What to do |
|---|---|---|
| Healthchecks: `job-ran` is DOWN | No successful run for 40 minutes. The job is failing or not running. | Open GitHub Actions, read the newest failed run, fix the cause below. |
| Healthchecks: `alpha-fresh` is DOWN | Runs work, but Alpha's newest reading is over 30 minutes old (alert after 70 minutes). | Usually the sensor or its gateway. Check Alpha in the SenseCAP portal. If `job-ran` is also down, fix that first. |
| GitHub: "Run failed: Telemetry sync" | One run failed. One failure alone is not urgent; the next run retries. | Act only if it keeps failing or `job-ran` goes down. |
| Healthchecks: is UP | Back to normal. | Nothing. |

### Common failures in the run log

| Error | Cause | Fix |
|---|---|---|
| `code=11104 AccessId or accessKey error` | Wrong SenseCAP ID or key in GitHub secrets | Set the secrets again (see "Change keys") |
| `password authentication failed` | Wrong `DATABASE_URL` | Set the secret again from Supabase |
| `code=11202 Request parameters are invalid` | Window starts more than 90 days back | Use `--hours 2136` or less |
| `Missing required env vars` | A secret is missing | Add it in GitHub, Settings, Secrets, Actions |
| Run log says `abandoned` | A run was killed before it finished | Nothing. The next run refetches the window. |

### Fill a gap

The 10 minute job refetches 3 hours, and the nightly job 72 hours. So any outage
under 3 days heals by itself. For a longer one, run a backfill that covers it:

- On GitHub: Actions, Telemetry sync, Run workflow, set `hours`.
- On a laptop: `python daily_load.py --hours N`.

Max is 2136 (89 days). SenseCAP deletes older data, so a gap older than
90 days cannot be refilled.

### Change keys

1. Make the new key (SenseCAP portal or Supabase).
2. Put it in your local `.env` and test: `python daily_load.py --hours 1`.
3. Copy it to GitHub. Either paste it in Settings, Secrets, Actions,
   or run `gh secret set NAME`. Watch for extra spaces or quotes.
4. Run the workflow once by hand and check that it succeeds.
5. Delete the old key.

Secrets used: `SENSECAP_API_ID`, `SENSECAP_API_KEY`, `SENSECAP_BASE_URL`,
`DEVICE_EUIS`, `DATABASE_URL`, `HC_JOB_URL`, `HC_ALPHA_URL`.

Note: a laptop run also pings Healthchecks, so it can hide a broken GitHub job.

### Check that it is working

```sql
-- Last 10 runs, newest first
SELECT started_at, status, trigger_source, rows_loaded, error_message
FROM ingest_runs ORDER BY started_at DESC LIMIT 10;

-- Newest Alpha reading
SELECT max(timestamp) FROM sensor_telemetry WHERE device_alias = 'Alpha';
```

## Status

Built and running on GitHub every 10 minutes. Verified so far: the backfill
matches the October CSV, reruns add no rows, a killed run recovers, and a
failing job sends the `job-ran` alert. Still to verify: the Alpha silence
alert, outage refill, and 24 hours unattended.

Beta is offline and marked inactive. It loads on its own when it returns.
