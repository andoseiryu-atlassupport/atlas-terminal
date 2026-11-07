"""Exact New York Fed reference-rate endpoints and bounded historical searches."""

import re
from datetime import date
from urllib.parse import urlencode, urlsplit

from atlas_terminal.errors import AtlasError

NYFED_BASE = "https://markets.newyorkfed.org/api/rates/"
NYFED_KINDS = {"effr": "unsecured", "sofr": "secured"}
NYFED_RATES = {
    name: f"{NYFED_BASE}{kind}/{name}/last/90.json" for name, kind in NYFED_KINDS.items()
}


def nyfed_url(series: str, start: str | None = None, end: str | None = None) -> str:
    if series not in NYFED_KINDS:
        raise AtlasError("INVALID_INPUT", "Choose EFFR or overnight SOFR.")
    if start is None and end is None:
        return NYFED_RATES[series]
    try:
        if start is None or end is None:
            raise ValueError("Both observation dates are required")
        for value in (start, end):
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("Use canonical observation dates")
        low, high = date.fromisoformat(start), date.fromisoformat(end)
        if not 1900 <= low.year <= high.year <= 2100 or low > high or low.year != high.year:
            raise ValueError("Each source request must stay within one calendar year")
    except (TypeError, ValueError):
        raise AtlasError("INVALID_INPUT", "Use ordered dates within one calendar year.") from None
    return f"{NYFED_BASE}{NYFED_KINDS[series]}/{series}/search.json?" + urlencode(
        {"startDate": start, "endDate": end}
    )


def nyfed_identity(url: str) -> tuple[str, str | None, str | None] | None:
    for series, latest in NYFED_RATES.items():
        if url == latest:
            return series, None, None
    if not isinstance(url, str) or not url.startswith(NYFED_BASE):
        return None
    parts = urlsplit(url)
    match = re.fullmatch(
        r"startDate=([0-9]{4}-[0-9]{2}-[0-9]{2})&endDate=([0-9]{4}-[0-9]{2}-[0-9]{2})",
        parts.query,
    )
    if match is None:
        return None
    for series in NYFED_KINDS:
        try:
            if url == nyfed_url(series, match[1], match[2]):
                return series, match[1], match[2]
        except AtlasError:
            return None
    return None
