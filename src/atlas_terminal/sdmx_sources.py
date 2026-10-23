"""Bounded official SDMX endpoints; identifiers never become arbitrary URLs."""

import re
from datetime import date
from urllib.parse import parse_qsl, urlencode

BASES = {
    "imf": "https://api.imf.org/external/sdmx/2.1",
    "oecd": "https://sdmx.oecd.org/public/rest",
}
IMF_DATA = "https://api.imf.org/external/sdmx/3.0/data/dataflow"
FLOW_PATTERN = (
    r"[A-Za-z][A-Za-z0-9_.-]{0,79},[A-Za-z][A-Za-z0-9_@-]{0,119},(?:[0-9]+(?:\.[0-9]+){0,3}|latest)"
)
CODE_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_@-]{0,99}"


def structure_url(source: str, flow: str | None = None) -> str:
    path = flow.replace(",", "/") if flow else "all/all/latest"
    return BASES[source] + "/dataflow/" + path + "?references=" + ("all" if flow else "none")


def data_url(source: str, flow: str, key: str, start: str, end: str) -> str:
    if source == "imf":
        return (
            IMF_DATA
            + "/"
            + flow.replace(",", "/")
            + "/"
            + key
            + "?"
            + urlencode(
                {
                    # Request full boundary years; Atlas filters inclusive period ends afterwards.
                    "c[TIME_PERIOD]": f"ge:{start[:4]}-01-01+le:{end[:4]}-12-31",
                    "dimensionAtObservation": "TIME_PERIOD",
                    "includeHistory": "false",
                }
            )
        )
    return (
        BASES[source]
        + "/data/"
        + flow
        + "/"
        + key
        + "?"
        + urlencode(
            {
                "startPeriod": start[:4] + "-01-01",
                "endPeriod": end[:4] + "-12-31",
                "dimensionAtObservation": "TIME_PERIOD",
                "format": "genericdata",
            }
        )
    )


def sdmx_source(url: str) -> str | None:
    for name, base in BASES.items():
        if url.startswith(base + "/") or (name == "imf" and url.startswith(IMF_DATA + "/")):
            return name
    return None


def valid_sdmx_url(url: str) -> bool:
    source = sdmx_source(url)
    if source is None or len(url) > 5000:
        return False
    if url == structure_url(source):
        return True
    base, _, query = url.partition("?")
    structure_prefix = BASES[source] + "/dataflow/"
    if base.startswith(structure_prefix):
        flow = base.removeprefix(structure_prefix).replace("/", ",")
        return bool(re.fullmatch(FLOW_PATTERN, flow)) and url == structure_url(source, flow)
    try:
        prefix = IMF_DATA + "/" if source == "imf" else BASES[source] + "/data/"
        parts = base.removeprefix(prefix).split("/")
        flow = ",".join(parts[:3]) if source == "imf" else parts[0]
        key = parts[3] if source == "imf" else parts[1]
        if (
            not base.startswith(prefix)
            or len(parts) != (4 if source == "imf" else 2)
            or not re.fullmatch(FLOW_PATTERN, flow)
            or not 1 <= len(key.split(".")) <= 32
            or not all(re.fullmatch(CODE_PATTERN, part) for part in key.split("."))
        ):
            return False
        pairs = parse_qsl(query, strict_parsing=True)
        params = dict(pairs)
        if len(params) != len(pairs):
            return False
        if source == "imf":
            low, high = params["c[TIME_PERIOD]"].split("+")
            start, end = low.removeprefix("ge:"), high.removeprefix("le:")
        else:
            start, end = params["startPeriod"], params["endPeriod"]
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        return (
            1900 <= first.year <= last.year <= 2100
            and last.year - first.year <= 29
            and url == data_url(source, flow, key, start, end)
        )
    except (KeyError, ValueError, IndexError):
        return False


SDMX_SOURCES = {
    "imf": {
        "name": "International Monetary Fund Data",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://data.imf.org/en/Resource-Pages/IMF-API",
        "terms": "https://www.imf.org/external/terms.htm",
        "use": "Credit IMF and original producers; retain dataset terms and modifications.",
        "cadence": "Dataset-specific releases/revisions. SDMX single-series reads; no vintages.",
    },
    "oecd": {
        "name": "OECD Data Explorer",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html",
        "terms": "https://www.oecd.org/en/about/terms-conditions.html",
        "use": "Credit OECD and original producers; retain dataset terms and third-party notices.",
        "cadence": (
            "Dataset-specific releases/revisions. Cached metadata; local rolling limit of 60 "
            "HTTP attempts per hour, with separate upstream shared-IP limits."
        ),
    },
}
