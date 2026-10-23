"""Fixed official endpoints and credential-free source/cache identities."""

import os
import re
from datetime import date
from urllib.parse import parse_qsl, urlencode

from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.errors import AtlasError

BEA_BASE = "https://apps.bea.gov/api/data"
BOJ_BASE = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"
ESTAT_BASE = "https://dashboard.e-stat.go.jp/api/1.0/Xml/"


def bea_key() -> str:
    key = os.environ.get("BEA_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{36}", key):
        raise AtlasError(
            "CONFIG_REQUIRED",
            (
                "Set BEA_API_KEY privately after free registration at "
                "https://apps.bea.gov/api/signup/."
            ),
        )
    return key


def bea_url(table: str, frequency: str, first: int, last: int) -> str:
    return (
        BEA_BASE
        + "?"
        + urlencode(
            {
                "method": "GetData",
                "DataSetName": "NIPA",
                "TableName": table,
                "Frequency": frequency,
                "Year": ",".join(str(y) for y in range(first, last + 1)),
                "ResultFormat": "JSON",
            }
        )
    )


def boj_url(series: str, start: str, end: str) -> str:
    spec = JAPAN_SERIES[series]

    def period(value: str) -> str:
        d = date.fromisoformat(value)
        part = (d.month - 1) // 3 + 1 if spec["frequency"] == "QUARTERLY" else d.month
        return f"{d.year}{part:02d}"

    return (
        BOJ_BASE
        + "?"
        + urlencode(
            {
                "format": "json",
                "lang": "en",
                "db": spec["database"],
                "code": spec["code"],
                "startDate": period(start),
                "endDate": period(end),
            }
        )
    )


def estat_metadata_url(series: str) -> str:
    spec = JAPAN_SERIES[series]
    return (
        ESTAT_BASE
        + "getIndicatorInfo?"
        + urlencode(
            {
                "Lang": "JP",
                "StatCode": spec["database"],
                "SearchIndicatorWord": spec["code"],
                "Cycle": "1",
                "RegionalRank": "2",
                "IsSeasonalAdjustment": "2" if spec["seasonally_adjusted"] else "1",
            }
        )
    )


def estat_data_url(series: str, code: str, start: str, end: str) -> str:
    spec = JAPAN_SERIES[series]
    return (
        ESTAT_BASE
        + "getData?"
        + urlencode(
            {
                "Lang": "JP",
                "IndicatorCode": code,
                "RegionCode": "00000",
                "Cycle": "1",
                "RegionalRank": "2",
                "IsSeasonalAdjustment": "2" if spec["seasonally_adjusted"] else "1",
                "TimeFrom": start[:7].replace("-", "") + "00",
                "TimeTo": end[:7].replace("-", "") + "00",
                "MetaGetFlg": "Y",
                "SectionHeaderFlg": "1",
            }
        )
    )


def validate_economic_url(url: str) -> bool:
    """Accept only canonical bounded requests constructed from curated specifications."""
    base, sep, query = url.partition("?")
    if not sep or base not in {
        BEA_BASE,
        BOJ_BASE,
        ESTAT_BASE + "getData",
        ESTAT_BASE + "getIndicatorInfo",
    }:
        return False
    try:
        pairs = parse_qsl(query, strict_parsing=True)
        p = dict(pairs)
        if len(p) != len(pairs):
            return False
        if base == BEA_BASE:
            ys = [int(y) for y in p["Year"].split(",")]
            return (
                1 <= len(ys) <= 5
                and 1900 <= ys[0] <= ys[-1] <= 2100
                and ys == list(range(ys[0], ys[-1] + 1))
                and (p["TableName"], p["Frequency"])
                in {(s["table"], s["frequency"]) for s in NATIONAL_ACCOUNTS.values()}
                and url == bea_url(p["TableName"], p["Frequency"], ys[0], ys[-1])
            )
        if base == BOJ_BASE:
            spec = next(
                s
                for s in JAPAN_SERIES.values()
                if s["source_id"] == "boj" and s["database"] == p["db"] and s["code"] == p["code"]
            )
            low, high = p["startDate"], p["endDate"]
            maximum = 4 if spec["frequency"] == "QUARTERLY" else 12
            return (
                set(p) == {"format", "lang", "db", "code", "startDate", "endDate"}
                and p["format"] == "json"
                and p["lang"] == "en"
                and all(
                    re.fullmatch(r"[0-9]{6}", v)
                    and 1900 <= int(v[:4]) <= 2100
                    and 1 <= int(v[4:]) <= maximum
                    for v in (low, high)
                )
                and low <= high
                and int(high[:4]) - int(low[:4]) <= 29
                and url == base + "?" + urlencode(p)
            )
        if base.endswith("getIndicatorInfo"):
            return url in {
                estat_metadata_url(k) for k, s in JAPAN_SERIES.items() if s["source_id"] == "estat"
            }
        low, high = p["TimeFrom"], p["TimeTo"]
        if not all(re.fullmatch(r"[0-9]{6}00", v) for v in (low, high)):
            return False
        start, end = (date(int(v[:4]), int(v[4:6]), 1).isoformat() for v in (low, high))
        return (
            bool(re.fullmatch(r"[0-9]{19}", p["IndicatorCode"]))
            and "1900-01-01" <= start <= end <= "2100-12-31"
            and int(end[:4]) - int(start[:4]) <= 29
            and url
            in {
                estat_data_url(k, p["IndicatorCode"], start, end)
                for k, s in JAPAN_SERIES.items()
                if s["source_id"] == "estat"
            }
        )
    except (KeyError, ValueError, StopIteration):
        return False


ECONOMIC_SOURCES = {
    "bea": {
        "name": "US Bureau of Economic Analysis national accounts",
        "upstream_price": "0",
        "api_key_required": True,
        "contact_user_agent_required": False,
        "documentation": "https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf",
        "terms": "https://www.bea.gov/about/policies-and-information",
        "use": (
            "Attribute BEA; preserve table, measurement, scale, seasonal "
            "adjustment and revision information."
        ),
        "cadence": (
            "Monthly/quarterly releases and revisions; free BEA_API_KEY "
            "registration required. Five years per upstream table request."
        ),
    },
    "boj": {
        "name": "Bank of Japan Time-Series Data Search",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.stat-search.boj.or.jp/info/api_manual_en.pdf",
        "terms": "https://www.stat-search.boj.or.jp/info/api_notice_en.pdf",
        "use": (
            "Credit the BOJ API and its non-guarantee notice; service "
            "publishers should notify BOJ as requested in the API notice. "
            "Preserve dataset terms and modifications."
        ),
        "cadence": (
            "Daily, monthly or quarterly depending on series; LAST_UPDATE also "
            "changes for metadata and is not a publication timestamp."
        ),
    },
    "estat": {
        "name": "Statistics Bureau of Japan Statistical Dashboard",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://dashboard.e-stat.go.jp/static/api",
        "terms": "https://dashboard.e-stat.go.jp/static/sitePolicy",
        "use": (
            "Credit the Statistical Dashboard API and original government "
            "survey; identify modifications and the government non-guarantee "
            "notice. Only the curated government CPI and Labour Force Survey "
            "series are selected."
        ),
        "cadence": (
            "Monthly statistics; publication and dashboard update dates may "
            "differ. No registration or application ID required for this "
            "separate Dashboard API."
        ),
    },
}
