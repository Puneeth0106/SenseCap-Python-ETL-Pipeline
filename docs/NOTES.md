# Notes

One entry per checklist section: what it does, why, the command, what can break.

## Section 0: Setup (Oct 21, 2026)

**What:** Created the Supabase database, two Healthchecks checks (`job-ran`, `alpha-fresh`), and a local `.venv`. Added `DATABASE_URL`, `HC_JOB_URL`, `HC_ALPHA_URL` to `.env`.

**Why:**
- Supabase is the managed Postgres the pipeline writes to.
- We use the session pooler string because the direct one is IPv6 only and GitHub Actions runners are IPv4. Port 5432 is session mode; 6543 is transaction mode, which we don't use.
- Healthchecks alerts when pings stop, so a dead job is never silent.
- Repo is public, so GitHub Actions minutes are free. History was checked first: no keys, no `.env`, no CSVs.

**Command (connection test):**
```bash
source .venv/bin/activate
python -c "import psycopg; from dotenv import dotenv_values as d; print(psycopg.connect(d('.env')['DATABASE_URL'], prepare_threshold=None).execute('select now()').fetchone())"
```
Prints the database's current UTC time.

**What can break:**
- Square brackets left around the password in the URL (the template shows `[YOUR-PASSWORD]`). Happened once; fixed.
- Special characters (`@ : / #`) in the password break the URL unless URL-encoded.
- Copying the direct connection string instead of the session pooler: works on some networks, fails on GitHub.
- Pinging a Healthchecks check before the job runs starts its timer and causes a false alert.
- A public repo's scheduled workflows are disabled after 60 days with no repo activity. `job-ran` will catch it.

## Section 1: Database (Oct 21, 2026)

**What:** Applied `001_init.sql` (tables, indexes, raw wide view) and `002_views.sql` (10-minute bucketed view, repointed wide view, device seeds, read-only role) to the Supabase database.

**Why:**
- `sensor_readings_10min` groups raw 5-minute readings into 10-minute buckets so the dashboard shows clean, even intervals instead of every raw point.
- `sensor_telemetry_wide` is now a thin wrapper over `sensor_readings_10min` so the pivot logic lives in one place.
- Alpha seeded as active, Beta as inactive. Beta costs one empty API call per run but loads on its own when it comes back.
- `telemetry_reader` role has SELECT only. Dashboard credentials can never write or delete data.

**Command (verify schema):**
```bash
source .venv/bin/activate
python -c "
import psycopg; from dotenv import dotenv_values as d
c = psycopg.connect(d('.env')['DATABASE_URL'], prepare_threshold=None)
print('Tables:', [r[0] for r in c.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name\").fetchall()])
print('Views:', [r[0] for r in c.execute(\"SELECT table_name FROM information_schema.views WHERE table_schema='public' ORDER BY table_name\").fetchall()])
print('Devices:', c.execute('SELECT device_eui, device_alias, is_active FROM sensor_devices ORDER BY device_alias').fetchall())
"
```

**What can break:**
- Running `002_views.sql` before `001_init.sql`: the views and seeds reference tables that do not exist yet.
- Supabase may block `CREATE ROLE` from the SQL editor depending on the plan. If it fails, create `telemetry_reader` in the Supabase dashboard (Authentication > Roles) and re-run the GRANTs.
- The `telemetry_reader` password is `CHANGE_ME_BEFORE_USE`. Set a real password in the Supabase dashboard before using it.

## Section 2: `telemetry_store.py` (Oct 21, 2026)

**What:** Shared database library: `to_utc`, `to_rows`, `upsert_rows`, `start_run`, `finish_run`. Unit tests in `tests/test_telemetry_store.py`.

**Why:**
- One `to_utc` used everywhere so timestamps are never misread.
- `to_rows` flattens the API's nested shape into flat dicts and deduplicates within a page.
- `upsert_rows` uses `ON CONFLICT DO UPDATE` so reruns never create duplicate rows.
- `start_run`/`finish_run` bookend every job with a record in `ingest_runs`; an open record means the job crashed.

**Command:**
```bash
source .venv/bin/activate
python -m pytest . -q
```
Expect: 12 passed.

**What can break:**
- `psycopg` (v3) uses `%s` placeholders and `executemany` with dicts requires `%(key)s` syntax — do not use `?` or positional tuples.
- `upsert_rows` commits nothing itself; the caller must commit (or use a transaction context). `start_run` and `finish_run` each call `conn.commit()` explicitly.
- An unknown metric ID is stored as `ID_<id>` and never raises an error — check logs if a new sensor type appears.

## Section 3: `daily_load.py` (Oct 21, 2026)

**What:** Production loader CLI. `--hours N` sets the window. Fetches all active devices, upserts to Supabase, records the run in `ingest_runs`.

**Why:**
- Fixed rolling window (never `MAX(timestamp)`) so gaps heal on the next run.
- 30-day chunk splitting prevents silent API truncation on wide windows.
- One transaction per page, per device — a mid-run crash leaves partial data but no corruption; the next run fills the gap.
- Exits non-zero on failure so GitHub Actions marks the job red and Healthchecks silence fires.

**Command:**
```bash
source .venv/bin/activate
python daily_load.py --hours 3
```
Watch for log lines: `fetching ...`, `api_calls=N rows_fetched=N rows_loaded=N`, `Done:`.

**What can break:**
- `DEVICE_EUIS` in `.env` must be comma-separated EUIs matching `sensor_devices.device_eui` exactly.
- A device not in `sensor_devices` will still fetch data but `upsert_rows` will fail the FK constraint. Seed the device first.
- The session pooler URL (port 5432) is required — the direct connection (IPv6) fails on GitHub Actions runners.

## Section 4: Backfill and verify (Nov 5, 2026)

**What:** Loaded 89 days of Alpha history with `daily_load.py --hours 2136`, then checked it against the October CSV.

**Why:**
- There is no separate backfill script. A backfill is the normal loader with a bigger window.
- Matching the CSV row by row proves the loader drops nothing.

**Command:**
```bash
python daily_load.py --hours 2136
```
Result: 43,346 rows. 2,363 of 2,363 CSV timestamps match. A rerun left the count at 43,173.

**What can break:**
- `--hours 2160` or more fails with `code=11202`. SenseCAP keeps only about 90 days.
- Data older than 90 days can only come from the old CSV files (`load_csv.py`, planned).

## Section 5: Automation (Nov 5, 2026)

**What:** `.github/workflows/telemetry_sync.yml` runs `--hours 3` every 10 minutes and `--hours 72` once a day at 02:17 UTC. A "Run workflow" button takes an `hours` input. CI runs ruff and pytest on every push.

**Why:**
- GitHub Actions is free for a public repo and needs no server.
- The 3 hour window means a few missed runs heal by themselves. The 72 hour run covers longer outages.
- `concurrency` stops two runs from overlapping.

**Command:** Actions tab, Telemetry sync, Run workflow, set `hours`.

**What can break:**
- Wrong or pasted-with-spaces secrets. Happened Nov 5 (`code=11104`). Fixed with `gh secret set`.
- GitHub runs schedules late at busy times. Seen: gaps up to 41 minutes. No data is lost.
- Scheduled workflows stop after 60 days with no repo activity. `job-ran` will alert.

## Section 6: Alerts (Nov 5, 2026)

**What:** Each successful run pings `job-ran`. It pings `alpha-fresh` only when Alpha's newest reading is under 30 minutes old. Healthchecks emails on DOWN and UP.

**Why:**
- `job-ran` catches a broken job. `alpha-fresh` catches a quiet sensor while the job still works.
- No Beta check. Beta is offline on purpose.

**Command:** none. Check status on healthchecks.io.

**What can break:**
- A laptop run also pings, so it can hide a broken GitHub job. Don't run locally during tests.
- `alpha-fresh` fires at about 70 minutes, not 60 (10 minute period plus 60 grace).

## Section 7: Reliability tests (Nov 5 to 9, 2026)

**What:** Five break-it tests, all passed.
- Same window twice: 0 new rows.
- Killed run, then rerun: 0 duplicates. Stuck runs are now closed as `abandoned`.
- Wrong database password: `job-ran` alert at 21:36 UTC, on time.
- Workflow off for 2 hours: `alpha-fresh` alert at 22:06 UTC. One run refilled all 13 slots.
- 24 hours unattended: 485 runs since Nov 5, all `success`. Median delay from sensor to database is 9 minutes; 92% under 20.

**Why:** Proves the pipeline heals and alerts without anyone watching.

**Command (health check):**
```sql
SELECT status, count(*) FROM ingest_runs
WHERE started_at > now() - interval '24 hours' GROUP BY 1;
```

**What can break:**
- Late GitHub schedules push a few readings past 20 minutes (worst seen: 40).

## Section 8: Cleanup and runbook (Nov 5 to 9, 2026)

**What:** Removed old docs and `docker-compose.yml`. Added a Runbook to `README.md`: alert meanings, common errors, filling a gap, changing keys.

**Why:** Anyone can fix a failure from the README alone.

**Decision (Nov 9):** Keep the monthly manual CSV download. The CSVs are a backup outside the database.

**What can break:**
- The README goes stale if the workflow or secrets change. Update it in the same PR.

## Section 4 addendum: old CSVs (Nov 9, 2026)

**What:** `load_csv.py` loads CSV exports into `sensor_telemetry`. Loaded the 11 pre-project files from `mdkamrulhasan/precision-farming` (`data/local-sensors-data/`), kept locally in `data/local-sensors-data/archive/`.

**Why:** SenseCAP keeps only 90 days. The old CSVs are the only copy of Oct 2025 to Apr 2026.

**Command:**
```bash
python load_csv.py data/local-sensors-data/archive/*.csv
```
Result: 48,536 rows (Alpha 23,570, Beta 24,966). A second load changed nothing.

**What can break:**
- An Alias not in `sensor_devices` stops the load. Add the device first.
- The old files have gaps. Row counts are exact multiples of 1000: the old downloader hit the 1000 point cap. That data is gone for good.
- No Healthchecks ping, on purpose. A manual load should not hide a broken job.
