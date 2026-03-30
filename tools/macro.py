"""
FRED API tool for macroeconomic data retrieval.

Requires FRED_API_KEY in .env — free key at https://fred.stlouisfed.org/docs/api/api_key.html
"""

import os
import time
from datetime import datetime, timedelta

import requests


def get_fred_data(series_id: str, limit: int = 12, max_retries: int = 3) -> str:
    """
    Fetch recent observations for a FRED economic data series.

    Args:
        series_id: FRED series identifier, e.g. 'FEDFUNDS', 'T10Y2Y', 'CPIAUCSL', 'GDPC1'
        limit:     Number of most-recent observations to return (default 12)

    Returns:
        Formatted string with series metadata and recent values.
        Returns an error description string on failure (does not raise).

    Key series:
        FEDFUNDS  — Federal Funds Effective Rate (monthly, %)
        T10Y2Y    — 10-Year minus 2-Year Treasury Spread (daily, %)
        CPIAUCSL  — Consumer Price Index, All Urban (monthly, index)
        GDPC1     — Real GDP (quarterly, billions of chained 2017 USD)
        UNRATE    — Unemployment Rate (monthly, %)
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return f"[FRED] FRED_API_KEY not set — cannot fetch {series_id}."

    obs_url = "https://api.stlouisfed.org/fred/series/observations"
    info_url = "https://api.stlouisfed.org/fred/series"

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }

    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            # Fetch series metadata
            info_resp = requests.get(info_url, params={**params, "limit": 1}, timeout=10)
            info_resp.raise_for_status()
            info_data  = info_resp.json().get("seriess", [{}])[0]
            title      = info_data.get("title", series_id)
            units      = info_data.get("units_short", "")
            frequency  = info_data.get("frequency_short", "")

            # Fetch observations
            obs_resp = requests.get(obs_url, params=params, timeout=10)
            obs_resp.raise_for_status()
            observations = obs_resp.json().get("observations", [])

            if not observations:
                return f"[FRED] No observations returned for {series_id}."

            # Build output (observations are desc, show oldest-first for readability)
            lines = [f"FRED Series: {series_id} — {title} ({units}, {frequency})"]
            for obs in reversed(observations):
                date  = obs.get("date", "?")
                value = obs.get("value", ".")
                if value != ".":
                    lines.append(f"  {date}: {value}")

            return "\n".join(lines)

        except requests.exceptions.Timeout:
            last_error = f"[FRED] Request timed out for series {series_id}."
        except requests.exceptions.HTTPError as e:
            last_error = f"[FRED] HTTP error fetching {series_id}: {e}"
            # Only retry on 5xx (server errors); 4xx are permanent failures
            if e.response is not None and e.response.status_code < 500:
                return last_error
        except Exception as e:
            last_error = f"[FRED] Unexpected error fetching {series_id}: {e}"

        if attempt < max_retries:
            wait = 2 ** attempt   # 2s, 4s
            print(f"  [FRED] {series_id} failed (attempt {attempt}/{max_retries}), retrying in {wait}s…")
            time.sleep(wait)

    return last_error
