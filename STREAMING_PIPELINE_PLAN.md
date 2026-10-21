# SenseCAP Pipeline: Build Checklist

Goal: every Alpha and Beta reading lands in Supabase within 20 minutes, with no duplicates, no silent gaps, and an alert when data stops. No AI.

Replaces `PIPELINE_APPROACH.md` and `NEW_APPROACH.md`. `BATCH_PIPELINE_ARCHITECTURE.md` stays as the detailed reference.

## Rules

1. One table, one key (`timestamp, device_eui, metric_id`), one upsert. Reruns never duplicate.
2. One `to_utc()` used everywhere.
3. Every run refetches a fixed window, not "since last row". Gaps heal themselves.
4. The raw table is never edited. Readers use a 10 minute view.
5. Every run writes to `ingest_runs` and pings Healthchecks. Silence raises an alert.

## How we work

For every section: explain first, build, walk through the code, Puneeth runs one command, read the output together, 3 check questions, one small commit, add an entry to `NOTES.md`. Full loop in `CLAUDE.md`.

## Flow

```text
SenseCAP API -> daily_load.py (GitHub Actions, every 10 minutes) -> Supabase sensor_telemetry -> sensor_readings_10min view -> dashboard or app
```

## 0. Setup

- [x] Create the Supabase project.
- [x] Copy the session pooler connection string (the direct one is IPv6 and fails on GitHub).
- [x] Create a Healthchecks.io account with two checks: `job-ran` and `alpha-fresh`.
- [x] Decide: public repo (free Actions) or private (2,000 minutes a month, so run every 30 minutes). Chosen: public (it already was).
- [x] If public: check that git history holds no keys first. Clean on Oct 7: no API keys, no `.env`, no CSVs ever committed.
- [x] Add `DATABASE_URL`, `HC_JOB_URL`, `HC_ALPHA_URL` to `.env`.

## 1. Database

- [ ] Run `migrations/001_init.sql` in the Supabase SQL editor.
- [ ] Write and run `migrations/002_views.sql`: `sensor_readings_10min`, one row per device and 10 minute bucket, latest value per metric.
- [ ] Point `sensor_telemetry_wide` at the 10 minute view.
- [ ] Insert Alpha and Beta into `sensor_devices`. Set Beta `is_active = false`.
- [ ] Create the `telemetry_reader` read-only role (section 14.2 of the architecture doc).

## 2. Shared code: `telemetry_store.py`

- [ ] `to_utc(raw)`: ISO "Z" strings and epoch milliseconds give the same UTC datetime.
- [ ] `to_rows(headers, values_groups, eui, alias)`: one row per point, unknown metric stored as `ID_<id>`, exact duplicates dropped.
- [ ] `upsert_rows(conn, rows)`: `ON CONFLICT (timestamp, device_eui, metric_id) DO UPDATE`.
- [ ] `start_run()` and `finish_run()` for `ingest_runs`.
- [ ] Unit tests for `to_utc` and `to_rows` using the existing fixture.

## 3. Loader: `daily_load.py`

- [ ] `--hours N` argument. Window is now minus N hours to now.
- [ ] Fail fast if `SENSECAP_API_ID`, `SENSECAP_API_KEY` or `DATABASE_URL` is missing.
- [ ] Load every device in the config. Beta costs one empty call and loads on its own when it returns.
- [ ] Split windows longer than 30 days into 30 day chunks (API limit).
- [ ] Reuse `paginate_telemetry` from `sensecap_client.py`. Do not rewrite it.
- [ ] One transaction per device.
- [ ] Record `api_calls`, `rows_fetched`, `rows_loaded` and `status` in `ingest_runs`.
- [ ] Exit non-zero on failure. Use `logging`. Never log keys or the database URL.

## 4. Backfill and verify

- [ ] Run `python daily_load.py --hours 2160` (90 days).
- [ ] Alpha timestamps in the database for Oct 1 to Oct 7 14:44 UTC equal the 2,363 rows in `alpha_october_2026.csv`.
- [ ] Run it again. Row count does not change.
- [ ] Optional: load older monthly CSVs with a small `load_csv.py`.

## 5. Automation

- [ ] `.github/workflows/telemetry_sync.yml` with two schedules: `3-59/10 * * * *` runs `--hours 3`, and `17 2 * * *` runs `--hours 72`.
- [ ] Add `workflow_dispatch` with an hours input for manual backfills.
- [ ] `concurrency` group, `cancel-in-progress: false`, `timeout-minutes: 10`.
- [ ] GitHub secrets: `SENSECAP_API_ID`, `SENSECAP_API_KEY`, `DATABASE_URL`, `HC_JOB_URL`, `HC_ALPHA_URL`.
- [ ] Separate CI job: `pytest` on every push.

## 6. Alerts

- [ ] End of each successful run: ping `job-ran` (period 10 minutes, grace 30).
- [ ] Ping `alpha-fresh` only when Alpha's newest row is under 30 minutes old (period 10 minutes, grace 60).
- [ ] Set Healthchecks to email you [FILL: or Slack].
- [ ] No check for Beta until it is back in the field.

## 7. Reliability tests

- [ ] Same window twice: 0 new rows.
- [ ] Kill a run midway, then rerun: complete, no duplicates.
- [ ] Wrong database password: run fails and the `job-ran` alert arrives.
- [ ] Disable the workflow for 2 hours: `alpha-fresh` alert arrives. Enable it again and the gap fills.
- [ ] 24 hours unattended: every run is `success` and Alpha has about 144 rows in the 10 minute view.

## 8. Cleanup and runbook

- [x] Delete `docker-compose.yml`.
- [ ] README: how to run, how to backfill a gap (`--hours N`), how to rotate keys, what each alert means.
- [x] Update `CLAUDE.md` to the new current state. Keep it current after each section.
- [x] Remove `PIPELINE_APPROACH.md` and `NEW_APPROACH.md` (replaced by this file).
- [ ] Stop the monthly manual CSV download. Keep `main.py` as a tool.

## 9. Optional: live stream

Only if readings must arrive in seconds. About $2 a month.

- [ ] Get the OrgID and a separate API key from the SenseCAP portal.
- [ ] 20 line MQTT test on the laptop. Confirm timestamps match the HTTP ones.
- [ ] `stream_load.py`, reusing `telemetry_store.py`.
- [ ] Deploy one instance on Fly.io.
- [ ] Keep the 10 minute job running as the repair path.

## Done when

- [ ] A reading is in the database within 20 minutes of the sensor sending it.
- [ ] Any rerun adds 0 duplicate rows.
- [ ] A 2 hour outage refills itself.
- [ ] One hour of silence from Alpha sends an alert.
