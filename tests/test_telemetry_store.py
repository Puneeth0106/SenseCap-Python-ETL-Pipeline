import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from telemetry_store import to_rows, to_utc

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_response.json"


@pytest.fixture
def sample_page():
    payload = json.loads(FIXTURE_PATH.read_text())
    headers, values_groups = payload["data"]["list"]
    return headers, values_groups


# to_utc: ISO Z string produces UTC-aware datetime with correct date.
def test_to_utc_iso_string():
    dt = to_utc("2026-08-27T20:47:12.582Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt == datetime(2026, 8, 27, 20, 47, 12, 582000, tzinfo=UTC)


# to_utc: epoch milliseconds convert to the same result as the ISO string.
def test_to_utc_epoch_ms():
    iso_dt = to_utc("2026-08-27T20:47:12.582Z")
    epoch_ms = int(iso_dt.timestamp() * 1000)
    assert to_utc(epoch_ms) == iso_dt


# to_rows: each known metric ID maps to its human-readable name.
def test_to_rows_known_metric_names(sample_page):
    headers, values_groups = sample_page
    rows = to_rows(headers, values_groups, "AABBCCDD", "Alpha")
    names = {r["metric_name"] for r in rows}
    assert "Soil_Temperature" in names
    assert "Soil_Moisture" in names
    assert "Soil_Conductivity" in names


# to_rows: unknown metric ID stored as ID_<id> instead of crashing.
def test_to_rows_unknown_metric_stored_as_id():
    headers = [[1, "9999"]]
    values_groups = [[[42.0, "2026-08-27T20:47:12.582Z"]]]
    rows = to_rows(headers, values_groups, "AABBCCDD", "Alpha")
    assert len(rows) == 1
    assert rows[0]["metric_id"] == "9999"
    assert rows[0]["metric_name"] == "ID_9999"


# to_rows: exact duplicate (same timestamp + metric) appears only once.
def test_to_rows_drops_exact_duplicates():
    headers = [[1, "4102"]]
    values_groups = [
        [
            [35.8, "2026-08-27T20:47:12.582Z"],
            [35.8, "2026-08-27T20:47:12.582Z"],
        ]
    ]
    rows = to_rows(headers, values_groups, "AABBCCDD", "Alpha")
    assert len(rows) == 1


# to_rows: row count against real fixture matches expected unique combinations.
def test_to_rows_count_against_fixture(sample_page):
    headers, values_groups = sample_page
    rows = to_rows(headers, values_groups, "AABBCCDD", "Alpha")
    # 3 metrics x ~333 points each = close to 1000, no duplicates
    assert len(rows) > 900
    keys = [(r["timestamp"], r["device_eui"], r["metric_id"]) for r in rows]
    assert len(keys) == len(set(keys))


# to_rows: device EUI and alias are stamped on every row.
def test_to_rows_stamps_device_fields():
    headers = [[1, "4102"]]
    values_groups = [[[35.8, "2026-08-27T20:47:12.582Z"]]]
    rows = to_rows(headers, values_groups, "EUI123", "TestDevice")
    assert rows[0]["device_eui"] == "EUI123"
    assert rows[0]["device_alias"] == "TestDevice"
