"""
Shared database helpers used by daily_load.py (and any future loader).

Functions:
  to_utc         -- parse a SenseCAP timestamp into a UTC datetime
  to_rows        -- flatten one API page into a list of row dicts
  upsert_rows    -- write rows to sensor_telemetry, no duplicates
  start_run      -- open an ingest_runs record
  finish_run     -- close it with status and counts
"""

import uuid
from datetime import UTC, datetime

from sensecap_client import MEASUREMENTS

# Maps SenseCAP metric IDs to human-readable names; unknown IDs become "ID_<id>".
METRIC_NAMES = {k: v for k, v in MEASUREMENTS.items()}


# Converts an ISO-8601 "Z" string (or epoch ms int) to a UTC-aware datetime.
def to_utc(raw):
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw / 1000, tz=UTC)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)


# Flattens one API page (headers + values_groups) into a list of row dicts, dropping exact duplicates.
def to_rows(headers, values_groups, eui, alias):
    rows = []
    seen = set()
    for header, group in zip(headers, values_groups):
        metric_id = str(header[1])
        metric_name = METRIC_NAMES.get(metric_id, f"ID_{metric_id}")
        for value, raw_ts in group:
            ts = to_utc(raw_ts)
            key = (ts, eui, metric_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "timestamp": ts,
                    "device_eui": eui,
                    "device_alias": alias,
                    "metric_id": metric_id,
                    "metric_name": metric_name,
                    "value": value,
                }
            )
    return rows


# Upserts rows into sensor_telemetry; conflicts on (timestamp, device_eui, metric_id) update the value.
def upsert_rows(conn, rows):
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sensor_telemetry
                (timestamp, device_eui, device_alias, metric_id, metric_name, value)
            VALUES
                (%(timestamp)s, %(device_eui)s, %(device_alias)s,
                 %(metric_id)s, %(metric_name)s, %(value)s)
            ON CONFLICT (timestamp, device_eui, metric_id) DO UPDATE
                SET value      = EXCLUDED.value,
                    updated_at = now()
            """,
            rows,
        )
    return len(rows)


# Opens an ingest_runs record and returns its UUID string.
def start_run(conn, trigger_source=None, devices_requested=0):
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_runs
                (run_id, started_at, status, trigger_source, devices_requested)
            VALUES (%s, %s, 'running', %s, %s)
            """,
            (run_id, datetime.now(UTC), trigger_source, devices_requested),
        )
    conn.commit()
    return run_id


# Closes an ingest_runs record with final counts and status ('success' or 'failed').
def finish_run(
    conn, run_id, status, api_calls=0, rows_fetched=0, rows_loaded=0, error_message=None
):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest_runs
            SET finished_at   = %s,
                status        = %s,
                api_calls     = %s,
                rows_fetched  = %s,
                rows_loaded   = %s,
                error_message = %s
            WHERE run_id = %s
            """,
            (
                datetime.now(UTC),
                status,
                api_calls,
                rows_fetched,
                rows_loaded,
                error_message,
                run_id,
            ),
        )
    conn.commit()
