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
