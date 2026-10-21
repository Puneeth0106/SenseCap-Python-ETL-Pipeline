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
