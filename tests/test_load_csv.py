import csv
import io
from datetime import UTC, datetime

import pytest

from load_csv import csv_to_rows

EUIS = {"Alpha": "A1", "Beta": "B1"}


def _records(text):
    return csv.DictReader(io.StringIO(text))


def test_alpha_row_gives_three_metrics():
    rows = csv_to_rows(
        _records(
            "Timestamp,Alias,Soil_Conductivity,Soil_Moisture,Soil_Temperature\n"
            "2025-11-30T23:59:34.820Z,Alpha,0.3,34.6,0.6\n"
        ),
        EUIS,
    )
    assert {r["metric_id"]: r["value"] for r in rows} == {"4108": 0.3, "4103": 34.6, "4102": 0.6}
    assert rows[0]["timestamp"] == datetime(2025, 11, 30, 23, 59, 34, 820000, tzinfo=UTC)
    assert all(r["device_eui"] == "A1" for r in rows)


def test_blank_cells_are_skipped():
    rows = csv_to_rows(
        _records(
            "Timestamp,Alias,Soil_Conductivity,Soil_Moisture,Soil_Temperature\n2026-04-01T17:46:47.078Z,Alpha,,,23.3\n"
        ),
        EUIS,
    )
    assert [(r["metric_name"], r["value"]) for r in rows] == [("Soil_Temperature", 23.3)]


def test_beta_without_conductivity_column():
    rows = csv_to_rows(
        _records("Timestamp,Alias,Soil_Moisture,Soil_Temperature\n2026-04-13T23:57:19.455Z,Beta,15.2,19.0\n"),
        EUIS,
    )
    assert sorted(r["metric_name"] for r in rows) == ["Soil_Moisture", "Soil_Temperature"]


def test_repeated_rows_are_dropped():
    line = "2026-04-13T23:57:19.455Z,Beta,15.2,19.0\n"
    rows = csv_to_rows(_records("Timestamp,Alias,Soil_Moisture,Soil_Temperature\n" + line * 2), EUIS)
    assert len(rows) == 2


def test_unknown_alias_raises():
    with pytest.raises(ValueError, match="Gamma"):
        csv_to_rows(_records("Timestamp,Alias,Soil_Moisture\n2026-01-01T00:00:00Z,Gamma,1\n"), EUIS)
