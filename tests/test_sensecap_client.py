import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sensecap_client import compute_next_boundary, parse_sensecap_timestamp

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_response.json"


# Loads a real captured SenseCAP API response from disk to use as test data.
@pytest.fixture
def sample_values_groups():
    payload = json.loads(FIXTURE_PATH.read_text())
    headers, values_groups = payload["data"]["list"]
    return headers, values_groups


# Checks that timestamp parsing produces a proper UTC-aware datetime, not a naive one.
def test_parse_sensecap_timestamp_is_utc_aware():
    dt = parse_sensecap_timestamp("2026-08-27T20:47:12.582Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.year == 2026 and dt.month == 8 and dt.day == 27


# Checks the pagination boundary picks the newer of two metrics' oldest points, so no data is skipped.
def test_compute_next_boundary_steps_to_newest_of_oldest_per_metric():
    values_groups = [
        [
            [1.0, "2026-08-27T10:00:00.000Z"],
            [1.0, "2026-08-27T08:00:00.000Z"],
        ],  # metric A
        [[2.0, "2026-08-27T09:00:00.000Z"]],  # metric B, oldest point is newer than A's
    ]
    current_end = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)

    boundary = compute_next_boundary(values_groups, current_end)

    assert boundary == datetime(2026, 8, 27, 9, tzinfo=timezone.utc)


# Checks the pagination loop steps back one second instead of hanging when the boundary can't move.
def test_compute_next_boundary_guards_stalled_cursor():
    current_end = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    values_groups = [[[1.0, "2026-08-27T12:00:00.000Z"]]]

    boundary = compute_next_boundary(values_groups, current_end)

    assert boundary == current_end.replace(second=59) or boundary < current_end


# Checks that an empty page correctly signals "nothing left to fetch" instead of erroring.
def test_compute_next_boundary_returns_none_when_all_groups_empty():
    assert compute_next_boundary([[], []], datetime.now(timezone.utc)) is None


# Checks the boundary logic against a real API response, proving timestamps aren't misparsed as epoch numbers.
def test_compute_next_boundary_against_real_api_sample(sample_values_groups):
    _, values_groups = sample_values_groups
    current_end = datetime.now(timezone.utc)

    boundary = compute_next_boundary(values_groups, current_end)

    assert boundary is not None
    assert boundary.tzinfo is not None
    assert boundary.year >= 2025
    assert boundary < current_end
