"""Fixed official statistics endpoints; canonical, bounded single-series requests."""

from datetime import date
from urllib.parse import parse_qsl, urlencode

from atlas_terminal.regional_economic_catalog import EUROPE_SERIES, UK_SERIES

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
ONS_BASE = "https://www.ons.gov.uk/"


def eurostat_url(series: str, start: str, end: str) -> str:
    spec = EUROPE_SERIES[series]
    return (
        EUROSTAT_BASE
        + spec["dataset"]
        + "?"
        + urlencode(
            {
                "format": "JSON",
                "lang": "EN",
                **spec["filters"],
                "sinceTimePeriod": start[:4],
                "untilTimePeriod": end[:4],
            }
        )
    )


def ons_url(series: str) -> str:
    spec = UK_SERIES[series]
    return (
        ONS_BASE
        + spec["path"]
        + "/timeseries/"
        + spec["cdid"].lower()
        + "/"
        + spec["dataset"].lower()
        + "/data"
    )


def regional_economic_identity(url: str) -> tuple[str, str] | None:
    for series in UK_SERIES:
        if url == ons_url(series):
            return "ons", series
    if not url.startswith(EUROSTAT_BASE) or len(url) > 2000:
        return None
    try:
        params = dict(parse_qsl(url.split("?", 1)[1], strict_parsing=True))
        first, last = params["sinceTimePeriod"], params["untilTimePeriod"]
        if (
            len(first) != 4
            or len(last) != 4
            or not first.isascii()
            or not last.isascii()
            or not first.isdigit()
            or not last.isdigit()
        ):
            return None
        start, end = date(int(first), 1, 1), date(int(last), 12, 31)
        if not 1900 <= start.year <= end.year <= 2100 or end.year - start.year > 29:
            return None
        for series in EUROPE_SERIES:
            if url == eurostat_url(series, start.isoformat(), end.isoformat()):
                return "eurostat", series
    except (KeyError, ValueError, IndexError):
        pass
    return None


REGIONAL_ECONOMIC_SOURCES = {
    "eurostat": {
        "name": "Eurostat official European economic statistics",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics",
        "terms": "https://ec.europa.eu/eurostat/help/copyright-notice",
        "use": (
            "Credit Eurostat, dataset codes and original producers; identify modifications, "
            "include Eurostat's non-responsibility notice and retain third-party exceptions."
        ),
        "cadence": (
            "Monthly and quarterly current/revised JSON-stat 2.0 observations; six-hour cache, "
            "no vintages."
        ),
    },
    "ons": {
        "name": "UK Office for National Statistics economic time series",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://developer.ons.gov.uk/retirement/",
        "terms": "https://www.ons.gov.uk/help/terms-conditions",
        "use": (
            "Source: Office for National Statistics, licensed under the Open Government Licence "
            "v3.0 except where otherwise stated. Identify modifications; no endorsement."
        ),
        "cadence": (
            "Monthly and quarterly website time series, current/revised snapshots; six-hour "
            "cache. Observe ONS fair-use conditions."
        ),
    },
}
