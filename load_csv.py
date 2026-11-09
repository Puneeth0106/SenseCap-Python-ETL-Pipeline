"""
Loads monthly CSV exports (from main.py or the old batch downloads) into sensor_telemetry.

Usage:
  python load_csv.py data/local-sensors-data/archive/*.csv

CSV format: Timestamp,Alias,<metric columns>. Blank cells are skipped.
Reuses upsert_rows, so loading the same file twice adds no rows.
Logs each run to ingest_runs as 'csv'. Does not ping Healthchecks.
"""

import argparse
import csv
import logging
import os
import sys
import time

import psycopg
from dotenv import load_dotenv

from telemetry_store import METRIC_NAMES, finish_run, start_run, to_utc, upsert_rows

logging.Formatter.converter = time.gmtime
log = logging.getLogger("load_csv")

# Column name -> metric ID, e.g. "Soil_Temperature" -> "4102".
METRIC_IDS = {name: mid for mid, name in METRIC_NAMES.items()}


# Turns CSV rows (dicts) into sensor_telemetry rows; skips blanks and repeated keys.
def csv_to_rows(records, eui_by_alias):
    rows = []
    seen = set()
    for rec in records:
        alias = rec["Alias"].strip()
        eui = eui_by_alias.get(alias)
        if eui is None:
            raise ValueError(f"Unknown device alias {alias!r}. Add it to sensor_devices first.")
        ts = to_utc(rec["Timestamp"].strip())
        for column, metric_id in METRIC_IDS.items():
            cell = (rec.get(column) or "").strip()
            if not cell:
                continue
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
                    "metric_name": column,
                    "value": float(cell),
                }
            )
    return rows


# Reads one CSV file into sensor_telemetry rows.
def read_csv(path, eui_by_alias):
    with open(path, newline="") as f:
        return csv_to_rows(csv.DictReader(f), eui_by_alias)


# Loads each file in its own transaction and logs one ingest_runs record for the whole call.
def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("files", nargs="+", help="CSV files to load")
    args = parser.parse_args(argv)

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.error("Missing required env vars: DATABASE_URL")
        return 1

    with psycopg.connect(url, prepare_threshold=None) as conn:
        eui_by_alias = dict(conn.execute("SELECT device_alias, device_eui FROM sensor_devices").fetchall())
        conn.commit()
        run_id = start_run(conn, trigger_source="csv", devices_requested=len(eui_by_alias))
        fetched = loaded = 0
        try:
            for path in args.files:
                rows = read_csv(path, eui_by_alias)
                with conn.transaction():
                    n = upsert_rows(conn, rows)
                fetched += len(rows)
                loaded += n
                log.info("%s: %d rows", os.path.basename(path), n)
        except Exception as exc:
            conn.rollback()
            finish_run(conn, run_id, "failed", 0, fetched, loaded, str(exc)[:500])
            log.error("Failed: %s", exc)
            return 1
        finish_run(conn, run_id, "success", 0, fetched, loaded)
        log.info("Done: %d files, %d rows", len(args.files), loaded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
