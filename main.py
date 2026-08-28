# Manual tool: pulls SenseCAP telemetry for a chosen date range and saves it as monthly CSV files.

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from sensecap_client import MEASUREMENTS, paginate_telemetry

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent


# Downloads one device's telemetry for a date range and saves it as a pivoted CSV file.
def process_custom_range(
    eui, alias, start_dt, end_dt, api_id, api_key, base_url, output_dir
):
    all_data = []

    print(f"\n--- Extracting {alias} ({eui}) ---")
    print(f" Range: {start_dt.date()} to {end_dt.date()}")

    # Prints a progress line each time a page of data is fetched from the API.
    def log_page(page_start, page_end, points_this_call):
        status = "complete" if points_this_call < 1000 else "capped, paging back"
        print(
            f"  {page_start.date()} to {page_end.date()}: {points_this_call} pts ({status})"
        )

    for headers, values_groups in paginate_telemetry(
        eui, start_dt, end_dt, api_id, api_key, base_url, on_page=log_page
    ):
        for i, group in enumerate(values_groups):
            m_id = str(headers[i][1])
            metric = MEASUREMENTS.get(m_id, f"ID_{m_id}")
            for val, ts in group:
                all_data.append(
                    {"Timestamp": ts, "Metric": metric, "Value": val, "Alias": alias}
                )

    if all_data:
        df = pd.DataFrame(all_data).drop_duplicates()
        final_df = df.pivot_table(
            index=["Timestamp", "Alias"], columns="Metric", values="Value"
        ).reset_index()
        final_df = final_df.sort_values(by="Timestamp", ascending=False)
        # e.g. alpha_march_2026.csv
        filename = f"{alias.lower()}_{start_dt.strftime('%B').lower()}_{start_dt.strftime('%Y')}.csv"

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        file_path = Path(output_dir) / filename
        final_df.to_csv(file_path, index=False)
        print(f" SUCCESS: {len(final_df)} records saved to {file_path}")
    else:
        print(f" FAILED: No data found for {alias} in this custom range.")


# --- CUSTOM DATA RANGE CONFIGURATION ---
if __name__ == "__main__":
    # 1. API Credentials
    USER_API_ID = os.getenv("SENSECAP_API_ID")
    USER_API_KEY = os.getenv("SENSECAP_API_KEY")
    STATION_URL = os.getenv("SENSECAP_BASE_URL", "https://sensecap.seeed.cc/openapi")

    DATA_FOLDER = PROJECT_ROOT / "data" / "local-sensors-data" / "months"

    # 2. Sensor Aliases
    SENSOR_CONFIG = {
        "2CF7F1C063700148": "Alpha",
        "2CF7F1C060800087": "Beta",
    }

    # 3. Jan-Apr 2026, one month at a time
    MONTHS = [
        (
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 3, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 4, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 14, tzinfo=timezone.utc),
        ),
    ]

    # 4. Run loop
    for start, end in MONTHS:
        for device_eui, device_alias in SENSOR_CONFIG.items():
            process_custom_range(
                device_eui,
                device_alias,
                start,
                end,
                USER_API_ID,
                USER_API_KEY,
                STATION_URL,
                DATA_FOLDER,
            )
