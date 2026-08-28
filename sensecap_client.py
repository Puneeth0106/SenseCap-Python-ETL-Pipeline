"""
SenseCAP list_telemetry_data client.

Shared by main.py (manual CSV export) and daily_load.py (production DB load):
HTTP fetch with retry, and the backward-paging loop that works around the
API's 1000-point-per-call cap shared across all metric groups.
"""

from datetime import datetime, timedelta, timezone

import requests
from requests.auth import HTTPBasicAuth
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

# Max points the API returns per call, shared across all metrics combined.
PAGE_LIMIT = 1000

MEASUREMENTS = {
    "4102": "Soil_Temperature",
    "4103": "Soil_Moisture",
    "4108": "Soil_Conductivity",
}

# SenseCAP returns HTTP 200 with a JSON body `code` field for logical
# errors (confirmed against a live response: code "0" means success).
# Only rate-limit/server-side codes are worth retrying; anything else
# (bad credentials, bad device_eui, etc.) is permanent.
RETRYABLE_BODY_CODES = {"429", "500", "503"}
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


class TransientSenseCAPError(Exception):
    """Retryable failure: rate limiting, transient server error, timeout."""


class PermanentSenseCAPError(Exception):
    """Non-retryable failure, e.g. bad credentials or bad request."""


# Checks SenseCAP's JSON "code" field and raises the right error type if the call failed.
def _raise_for_body_code(payload):
    code = str(payload.get("code"))
    if code == "0":
        return
    message = f"SenseCAP API returned code={code} msg={payload.get('msg')}"
    if code in RETRYABLE_BODY_CODES:
        raise TransientSenseCAPError(message)
    raise PermanentSenseCAPError(message)


# Decides whether a given error is worth retrying (network blip, timeout, or rate limit).
def _is_retryable(exc):
    if isinstance(exc, TransientSenseCAPError):
        return True
    if isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    ):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status in RETRYABLE_HTTP_STATUS
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
# Calls the SenseCAP API once for a device and time window, retrying automatically on failure.
def fetch_telemetry_chunk(
    eui, start_ms, end_ms, api_id, api_key, base_url, timeout_seconds=30
):
    url = f"{base_url}/list_telemetry_data"
    params = {
        "device_eui": eui,
        "time_start": start_ms,
        "time_end": end_ms,
        "limit": PAGE_LIMIT,
    }
    response = requests.get(
        url, params=params, auth=HTTPBasicAuth(api_id, api_key), timeout=timeout_seconds
    )
    response.raise_for_status()
    payload = response.json()
    _raise_for_body_code(payload)
    return payload


# Turns a SenseCAP timestamp string into a proper UTC datetime Python can work with.
def parse_sensecap_timestamp(raw_ts):
    return datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


# Figures out where to move the "end of window" cursor for the next page, without skipping data.
def compute_next_boundary(values_groups, current_end):
    oldest_per_metric = [
        min(parse_sensecap_timestamp(ts) for _, ts in group)
        for group in values_groups
        if group
    ]
    if not oldest_per_metric:
        return None

    boundary = max(oldest_per_metric)
    # Guard against a stalled cursor if >1000 points share one timestamp.
    if boundary >= current_end:
        boundary = current_end - timedelta(seconds=1)
    return boundary


# Walks backward through time fetching every page of telemetry until the whole range is covered.
def paginate_telemetry(
    eui, start_dt, end_dt, api_id, api_key, base_url, timeout_seconds=30, on_page=None
):
    current_end = end_dt

    while current_end > start_dt:
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(current_end.timestamp() * 1000)

        payload = fetch_telemetry_chunk(
            eui,
            start_ms,
            end_ms,
            api_id,
            api_key,
            base_url,
            timeout_seconds=timeout_seconds,
        )

        raw_list = payload.get("data", {}).get("list", [])
        if len(raw_list) < 2 or len(raw_list[1]) == 0:
            break

        headers, values_groups = raw_list[0], raw_list[1]
        points_this_call = sum(len(group) for group in values_groups)

        yield headers, values_groups

        if on_page:
            on_page(start_dt, current_end, points_this_call)

        if points_this_call < PAGE_LIMIT:
            break

        boundary = compute_next_boundary(values_groups, current_end)
        if boundary is None:
            break
        current_end = boundary
