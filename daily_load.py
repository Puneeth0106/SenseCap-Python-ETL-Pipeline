"""
Production loader: fetches a fixed rolling window from SenseCAP and upserts
into sensor_telemetry via telemetry_store.

Usage:
  python daily_load.py --hours 3       # every-10-min job
  python daily_load.py --hours 72      # nightly job
  python daily_load.py --hours 2160    # 90-day backfill
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import psycopg
import requests
from dotenv import dotenv_values

from sensecap_client import paginate_telemetry
from telemetry_store import finish_run, start_run, to_rows, upsert_rows

# Maximum days per API call; the SenseCAP API returns no data for wider windows.
MAX_CHUNK_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)


# Reads required env vars from .env and environment; exits if any are missing.
def _load_config():
    env = {**dotenv_values(".env"), **os.environ}
    required = [
        "SENSECAP_API_ID",
        "SENSECAP_API_KEY",
        "DATABASE_URL",
        "SENSECAP_BASE_URL",
    ]
    missing = [k for k in required if not env.get(k)]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)
    raw_euis = env.get("DEVICE_EUIS", "")
    devices = [e.strip() for e in raw_euis.split(",") if e.strip()]
    if not devices:
        log.error("DEVICE_EUIS is empty — nothing to load")
        sys.exit(1)
    return env, devices


# Splits a wide window into 30-day chunks so the API never sees a range it silently truncates.
def _chunks(start_dt, end_dt):
    chunk_start = start_dt
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=MAX_CHUNK_DAYS), end_dt)
        yield chunk_start, chunk_end
        chunk_start = chunk_end


# Loads one device for the given window; returns (api_calls, rows_fetched, rows_loaded).
def _load_device(conn, eui, alias, start_dt, end_dt, env):
    api_calls = rows_fetched = rows_loaded = 0

    for chunk_start, chunk_end in _chunks(start_dt, end_dt):
        log.info(
            "%s: fetching %s to %s",
            alias,
            chunk_start.isoformat(),
            chunk_end.isoformat(),
        )
        for headers, values_groups in paginate_telemetry(
            eui,
            chunk_start,
            chunk_end,
            env["SENSECAP_API_ID"],
            env["SENSECAP_API_KEY"],
            env["SENSECAP_BASE_URL"],
        ):
            api_calls += 1
            rows = to_rows(headers, values_groups, eui, alias)
            rows_fetched += len(rows)
            with conn.transaction():
                rows_loaded += upsert_rows(conn, rows)

    log.info(
        "%s: api_calls=%d rows_fetched=%d rows_loaded=%d",
        alias,
        api_calls,
        rows_fetched,
        rows_loaded,
    )
    return api_calls, rows_fetched, rows_loaded


# Looks up the device alias from sensor_devices; falls back to the EUI if not found.
def _alias(conn, eui):
    row = conn.execute(
        "SELECT device_alias FROM sensor_devices WHERE device_eui = %s", (eui,)
    ).fetchone()
    return row[0] if row else eui


# Sends a successful-run signal without exposing the check URL in logs.
def _ping(url, name):
    if not url:
        log.warning("%s check URL is missing", name)
        return
    try:
        requests.get(url, timeout=10).raise_for_status()
    except requests.RequestException:
        log.warning("Could not ping %s check", name)


# Checks the latest Alpha reading before signaling sensor freshness.
def _alpha_is_fresh(conn):
    row = conn.execute(
        "SELECT max(timestamp) FROM sensor_telemetry WHERE device_alias = %s",
        ("Alpha",),
    ).fetchone()
    return bool(row and row[0] and timedelta(0) <= datetime.now(UTC) - row[0] < timedelta(minutes=30))


# Runs the fixed-window loader and records its result.
def main():
    parser = argparse.ArgumentParser(
        description="Load SenseCAP telemetry into Supabase."
    )
    parser.add_argument(
        "--hours", type=int, required=True, help="How many hours back to fetch."
    )
    args = parser.parse_args()

    env, device_euis = _load_config()

    now = datetime.now(UTC)
    start_dt = now - timedelta(hours=args.hours)
    log.info(
        "Window: %s to %s (%d hours)", start_dt.isoformat(), now.isoformat(), args.hours
    )

    # Use prepare_threshold=None for pgBouncer / Supabase session pooler compatibility.
    conn = psycopg.connect(env["DATABASE_URL"], prepare_threshold=None)

    run_id = start_run(conn, trigger_source="cli", devices_requested=len(device_euis))
    total_api_calls = total_fetched = total_loaded = 0
    overall_status = "success"
    error_msg = None

    for eui in device_euis:
        alias = _alias(conn, eui)
        try:
            ac, rf, rl = _load_device(conn, eui, alias, start_dt, now, env)
            total_api_calls += ac
            total_fetched += rf
            total_loaded += rl
        except Exception as exc:
            log.error("%s: failed — %s", alias, exc)
            overall_status = "failed"
            error_msg = str(exc)

    finish_run(
        conn,
        run_id,
        status=overall_status,
        api_calls=total_api_calls,
        rows_fetched=total_fetched,
        rows_loaded=total_loaded,
        error_message=error_msg,
    )
    if overall_status != "success":
        conn.close()
        sys.exit(1)

    fresh = _alpha_is_fresh(conn)
    conn.close()
    _ping(env.get("HC_JOB_URL"), "job-ran")
    if fresh:
        _ping(env.get("HC_ALPHA_URL"), "alpha-fresh")

    log.info(
        "Done: api_calls=%d rows_fetched=%d rows_loaded=%d",
        total_api_calls,
        total_fetched,
        total_loaded,
    )


if __name__ == "__main__":
    main()
