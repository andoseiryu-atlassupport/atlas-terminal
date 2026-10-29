"""Canonical activity endpoints, bounded dates and outbound-only credentials."""

import os
import re
from collections.abc import Callable
from datetime import date
from urllib.parse import parse_qs, urlencode

from atlas_terminal.activity_catalog import ACTIVITY_SERIES
from atlas_terminal.errors import AtlasError

CENSUS_BASE = "https://api.census.gov/data/timeseries/eits/"
FRED_BASE = "https://api.stlouisfed.org/fred/"
STATCAN_BASE = "https://www150.statcan.gc.ca/t1/wds/rest/"
WORLD_BANK_BASE = "https://api.worldbank.org/v2/"
G17_BASE = "https://www.federalreserve.gov/releases/g17/Current/ipdisk/"
G17_URLS = (G17_BASE + "ip_sa.txt", G17_BASE + "utl_sa.txt")


def activity_key(source: str) -> str:
    name, pattern, signup = {
        "census": (
            "CENSUS_API_KEY",
            r"[0-9a-fA-F]{40}",
            "https://api.census.gov/data/key_signup.html",
        ),
        "fred": (
            "FRED_API_KEY",
            r"[a-z0-9]{32}",
            "https://fred.stlouisfed.org/docs/api/api_key.html",
        ),
    }[source]
    value = os.environ.get(name, "")
    if not re.fullmatch(pattern, value):
        raise AtlasError(
            "CONFIG_REQUIRED", f"Set {name} privately after free registration at {signup}."
        )
    return value


def census_url(series: str, start: str, end: str) -> str:
    spec = ACTIVITY_SERIES[series]
    return (
        CENSUS_BASE
        + spec["table"]
        + "?"
        + urlencode(
            {
                "get": (
                    "cell_value,data_type_code,category_code,seasonally_adj,error_data,"
                    "program_code,time_slot_id"
                ),
                "for": "us:*",
                "time": "from " + start[:4] + "-01 to " + end[:4] + "-12",
                "category_code": spec["code"],
                "data_type_code": spec["data_type"],
                "seasonally_adj": "yes",
                "error_data": "no",
            }
        )
    )


def fred_url(series: str, start: str | None = None, end: str | None = None) -> str:
    params = {"series_id": ACTIVITY_SERIES[series]["code"], "file_type": "json"}
    if start is not None and end is not None:
        params.update(
            observation_start=start,
            observation_end=end,
            units="lin",
            output_type="1",
            limit="2000",
            offset="0",
            sort_order="asc",
        )
    return (
        FRED_BASE
        + ("series/observations" if start is not None else "series")
        + "?"
        + urlencode(params)
    )


def statcan_url(series: str, start: str | None = None, end: str | None = None) -> str:
    vector = ACTIVITY_SERIES[series]["code"]
    if start is None:
        # Canonical read-only identity. Transport sends the documented POST body.
        return STATCAN_BASE + "getSeriesInfoFromVector?" + urlencode({"vectorId": vector})
    return (
        STATCAN_BASE
        + "getDataFromVectorByReferencePeriodRange?"
        + urlencode(
            {
                "vectorIds": '"' + vector + '"',
                "startRefPeriod": start[:4] + "-01-01",
                "endReferencePeriod": (end or start)[:4] + "-12-31",
            }
        )
    )


def world_bank_url(series: str, start: str | None = None, end: str | None = None) -> str:
    indicator = ACTIVITY_SERIES[series]["code"]
    if start is None:
        return WORLD_BANK_BASE + "indicator/" + indicator + "?format=json"
    return (
        WORLD_BANK_BASE
        + "country/CHN/indicator/"
        + indicator
        + "?"
        + urlencode(
            {
                "format": "json",
                "source": "2",
                "date": start[:4] + ":" + (end or start)[:4],
                "per_page": "1000",
            }
        )
    )


def activity_identity(url: str) -> tuple[str, str, str] | None:
    if url in G17_URLS:
        return (
            "federal_reserve",
            "us_industrial_production" if url == G17_URLS[0] else "us_capacity_utilization",
            "data",
        )
    if url == STATCAN_BASE + "getCodeSets":
        return "statcan", "codes", "metadata"
    if (
        not url.startswith((CENSUS_BASE, FRED_BASE, STATCAN_BASE, WORLD_BANK_BASE))
        or len(url) > 2000
    ):
        return None
    builders: dict[str, Callable[..., str]] = {
        "census": census_url,
        "fred": fred_url,
        "statcan": statcan_url,
        "world_bank": world_bank_url,
    }
    for series, spec in ACTIVITY_SERIES.items():
        if spec["source_id"] in {"fred", "statcan", "world_bank"} and url == builders[
            spec["source_id"]
        ](series):
            return spec["source_id"], series, "metadata"
    try:
        params = parse_qs(url.split("?", 1)[1], strict_parsing=True)
        for series, spec in ACTIVITY_SERIES.items():
            source = spec["source_id"]
            prefix = {
                "census": CENSUS_BASE,
                "fred": FRED_BASE,
                "statcan": STATCAN_BASE,
                "world_bank": WORLD_BANK_BASE,
            }.get(source)
            if prefix is None or not url.startswith(prefix):
                continue
            builder = builders.get(source)
            if builder is None:
                continue
            if source == "census":
                match = re.fullmatch(
                    r"from ([0-9]{4})-01 to ([0-9]{4})-12", params.get("time", [""])[0]
                )
                if not match:
                    continue
                start, end = match[1] + "-01-01", match[2] + "-12-31"
            elif source == "fred":
                start, end = params["observation_start"][0], params["observation_end"][0]
            elif source == "statcan":
                start, end = params["startRefPeriod"][0], params["endReferencePeriod"][0]
            else:
                first, last = params["date"][0].split(":")
                start, end = first + "-01-01", last + "-12-31"
            low, high = date.fromisoformat(start), date.fromisoformat(end)
            if (
                1900 <= low.year <= high.year <= 2100
                and low <= high
                and high.year - low.year <= 29
                and url == builder(series, start, end)
            ):
                return source, series, "data"
    except (KeyError, ValueError, IndexError):
        return None
    return None


ACTIVITY_SOURCES = {
    "census": {
        "name": "US Census Bureau economic indicators",
        "upstream_price": "0",
        "api_key_required": True,
        "contact_user_agent_required": False,
        "documentation": "https://www.census.gov/data/developers/data-sets/economic-indicators.html",
        "terms": "https://www.census.gov/data/developers/about/terms-of-service.html",
        "use": (
            "US government statistics; credit Census Bureau and retain sampling, "
            "seasonal adjustment and revision caveats."
        ),
        "cadence": (
            "Monthly EITS observations; private free CENSUS_API_KEY required; six-hour cache."
        ),
    },
    "fred": {
        "name": "Federal Reserve Bank of St. Louis FRED",
        "source_kind": "aggregator",
        "upstream_price": "0",
        "api_key_required": True,
        "contact_user_agent_required": False,
        "documentation": "https://fred.stlouisfed.org/docs/api/fred/",
        "terms": "https://fred.stlouisfed.org/legal/",
        "use": (
            "Credit FRED and each series' original producer. Curated ICSA/CCSA are US "
            "Department of Labor data; generic series may carry third-party restrictions. "
            "Free API access grants no blanket redistribution license."
        ),
        "cadence": (
            "Source-declared frequency, current revised snapshots; "
            "private free FRED_API_KEY required; "
            "six-hour cache. No ALFRED vintages."
        ),
    },
    "statcan": {
        "name": "Statistics Canada Web Data Service",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.statcan.gc.ca/en/developers/wds/user-guide",
        "terms": "https://www.statcan.gc.ca/en/reference/licence",
        "use": (
            "Source: Statistics Canada. Retain table/vector, original definitions, "
            "scale and flags; identify Atlas normalization and do not imply "
            "endorsement."
        ),
        "cadence": (
            "Monthly current/revised observations; six-hour cache; metadata cached "
            "one day. Publisher maintenance windows can return unavailable tables."
        ),
    },
    "world_bank": {
        "name": "World Bank World Development Indicators",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation",
        "terms": "https://datacatalog.worldbank.org/public-licenses#cc-by",
        "use": (
            "Credit World Bank, World Development Indicators and original national "
            "producers. CC BY 4.0 and dataset/third-party exceptions apply; no "
            "endorsement."
        ),
        "cadence": (
            "Annual current/revised WDI observations, not a domestic quarterly or "
            "release-time feed; six-hour cache."
        ),
    },
}
