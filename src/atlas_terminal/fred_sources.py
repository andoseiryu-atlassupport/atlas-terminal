"""Exact credential-free FRED identities for explicitly selected series."""

import re
from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit

from atlas_terminal.activity_sources import FRED_BASE
from atlas_terminal.errors import AtlasError

FRED_MAX_OBSERVATIONS = 12000


def fred_series_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", value):
        raise AtlasError(
            "INVALID_INPUT",
            "Provide one exact FRED series ID using uppercase letters, digits or underscores.",
            fields=["series"],
        )
    return value


def fred_window(start: str | None, end: str | None) -> tuple[str, str]:
    if start is None and end is None:
        today = date.today()
        return f"{today.year - 2}-01-01", today.isoformat()
    try:
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError
        low, high = date.fromisoformat(start), date.fromisoformat(end)
        if (
            low.isoformat() != start
            or high.isoformat() != end
            or not 1900 <= low.year <= high.year <= 2100
            or low > high
            or high.year - low.year > 29
        ):
            raise ValueError
    except ValueError:
        raise AtlasError(
            "INVALID_INPUT",
            "FRED dates must be ordered ISO dates spanning at most 30 calendar years, 1900–2100.",
            fields=["start_date", "end_date"],
        ) from None
    return start, end


def fred_series_url(series: str, start: str | None = None, end: str | None = None) -> str:
    params = {"series_id": fred_series_id(series), "file_type": "json"}
    endpoint = "series"
    if start is not None or end is not None:
        low, high = fred_window(start, end)
        endpoint = "series/observations"
        params.update(
            observation_start=low,
            observation_end=high,
            units="lin",
            output_type="1",
            limit=str(FRED_MAX_OBSERVATIONS),
            offset="0",
            sort_order="asc",
        )
    return FRED_BASE + endpoint + "?" + urlencode(params)


def fred_series_identity(url: str) -> tuple[str, str] | None:
    """Reject alternate hosts, credentials, fragments, duplicate parameters and transformations."""
    if not isinstance(url, str) or len(url) > 1000 or not url.startswith(FRED_BASE):
        return None
    try:
        parts = urlsplit(url)
        params = parse_qs(parts.query, strict_parsing=True)
        series = params["series_id"][0]
        if url == fred_series_url(series):
            return series, "metadata"
        start, end = params["observation_start"][0], params["observation_end"][0]
        if url == fred_series_url(series, start, end):
            return series, "data"
    except (KeyError, IndexError, ValueError, AtlasError):
        pass
    return None
