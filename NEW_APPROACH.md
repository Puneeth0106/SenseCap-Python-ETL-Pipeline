# New Pipeline Approach

This document explains the updated plan in simple terms.

## Short Version

We will use:

- Python for the ingestion script
- SenseCAP API as the data source
- Supabase as the hosted PostgreSQL database
- GitHub Actions to run the job on a schedule
- Plain PostgreSQL, not TimescaleDB

We will not use Docker for the main database setup.

## Why This Approach

The project does not need a complex time-series database right now.

With two sensors and a few readings per day, the data volume is small. Even if
we collect readings every few minutes, plain PostgreSQL can handle it easily.

Supabase gives us managed PostgreSQL, so we do not need to run a database
server locally or manage Docker containers.

## Data Flow

```text
SenseCAP API
  ↓
daily_load.py
  ↓
Supabase PostgreSQL
  ↓
Dashboard or SQL queries
```

## What daily_load.py Will Do

The production script will:

1. Connect to Supabase.
2. Check the latest timestamp already loaded for each sensor.
3. Fetch new SenseCAP data.
4. Re-fetch the last 24 hours to catch late sensor readings.
5. Insert or update rows in the database.
6. Record the run status in `ingest_runs`.

This makes the job safe to rerun. If the same data is fetched again, it updates
the existing rows instead of creating duplicates.

## Database Design

Raw telemetry will be stored in a narrow table:

```text
timestamp | device_eui | device_alias | metric_id | metric_name | value
```

This is better than a wide table because new sensor metrics can be added later
without changing the schema.

For dashboards, we will use a SQL view called `sensor_telemetry_wide`. That view
can present the data in a more chart-friendly format.

## Raw Data First

The first goal is to store the real sensor data exactly as received.

We will not fill missing values in Phase 2.

Missing-data handling will come later in a separate cleaned or imputed data
table. That keeps the raw source of truth untouched.

Future cleaned data may include fields like:

```text
is_imputed
imputation_method
source_timestamp
```

## Local Development

For local testing, we can run `daily_load.py` from the laptop and connect
directly to Supabase using `DATABASE_URL`.

No Docker is required for this path.

If we want isolated local database testing later, we can install PostgreSQL
with Homebrew. That is optional.

## GitHub Actions

GitHub Actions will run the pipeline automatically on a schedule.

Example:

```text
Every day
  ↓
GitHub Actions starts daily_load.py
  ↓
Data gets written to Supabase
```

The same workflow can also be run manually when needed.

## Why Not TimescaleDB

TimescaleDB is useful for very large time-series workloads.

This project does not need it yet. Plain PostgreSQL is simpler, portable, and
easier to host on Supabase.

If the project grows to many sensors and very frequent readings, we can revisit
partitioning or TimescaleDB later.

## Current Decision

Use Supabase PostgreSQL for the database and keep the ingestion pipeline simple:

```text
GitHub Actions + Python + Supabase PostgreSQL
```

Build reliable raw ingestion first. Add cleaned/imputed data later as a separate
step.
