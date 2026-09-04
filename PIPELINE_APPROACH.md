# Proposed Sensor Data Pipeline Approach

## Purpose

We are building a simple pipeline to move SenseCAP soil sensor data into a
database so it can be used by an application or dashboard.

The main goal is to make the data reliable, queryable, and easy to use without
adding unnecessary infrastructure.

## Proposed Architecture

```text
SenseCAP Sensors
  ↓
SenseCAP API
  ↓
Python ingestion script
  ↓
Supabase PostgreSQL database
  ↓
Professor's application or dashboard
```

## Main Decision

We plan to use **Supabase PostgreSQL** as the database.

Supabase gives us a hosted PostgreSQL database, so we do not need to run Docker
or manage a database server ourselves.

For the current data volume, regular PostgreSQL is enough. We do not need
TimescaleDB right now.

## Why Not TimescaleDB Right Now

TimescaleDB is useful for very large time-series datasets.

Our current expected data volume is small:

```text
2 sensors × a few readings per day × a few soil metrics
```

Even if we collect readings more frequently, plain PostgreSQL can handle this
easily with proper indexes.

Keeping the database as plain PostgreSQL makes the system simpler and easier to
host.

## How Data Will Be Loaded

The ingestion script, `daily_load.py`, will run automatically on a schedule.

It will:

1. Connect to the SenseCAP API.
2. Check the latest timestamp already stored in the database.
3. Fetch only new sensor readings.
4. Re-fetch the last 24 hours to catch late-arriving sensor data.
5. Insert new rows or update existing rows safely.
6. Record whether the run succeeded or failed.

This makes the pipeline safe to rerun. If the same sensor data is fetched again,
it will not create duplicate rows.

## Raw Data First

The first database table will store the raw sensor readings exactly as they come
from SenseCAP.

Example row:

```text
timestamp | sensor | metric | value
```

This raw table will be the source of truth.

We will not fill missing values in the first phase. Missing-data handling should
be a separate step so we can clearly tell the difference between real sensor
readings and estimated values.

## Future Cleaned Data

Later, if the application needs complete daily values, we can create a separate
cleaned or imputed dataset.

That cleaned dataset can include labels such as:

```text
is_imputed
imputation_method
source_timestamp
```

This keeps the raw data honest while still allowing the application to use a
cleaner version of the data when needed.

## Application Integration Questions

To connect this pipeline efficiently to the application, we need to confirm:

1. Should the application read directly from PostgreSQL?
2. What table or view format does the application expect?
3. Does the application need raw data, cleaned data, or both?
4. How often does the application need fresh data?
5. Should missing values be left blank or filled?
6. Should data be shown by individual sensor, by location, or both?

