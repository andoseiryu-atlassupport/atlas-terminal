"""Primary and optional secondary source registry with allowlisted HTTP transport."""

import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from atlas_terminal.activity_sources import (
    ACTIVITY_SOURCES,
    G17_URLS,
    STATCAN_BASE,
    activity_identity,
    activity_key,
)
from atlas_terminal.economic_catalog import JAPAN_SERIES
from atlas_terminal.economic_sources import (
    BEA_BASE,
    BOJ_BASE,
    ECONOMIC_SOURCES,
    ESTAT_BASE,
    bea_key,
    validate_economic_url,
)
from atlas_terminal.equity_intraday import intraday_identity
from atlas_terminal.equity_sources import YAHOO_SOURCE, require_yahoo_enabled, yahoo_identity
from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import JGB_BASE, JGB_URLS, parse_jgb_csv
from atlas_terminal.fred_sources import fred_series_identity
from atlas_terminal.global_macro_sources import GLOBAL_AREAS, GLOBAL_INDICATORS, global_wdi_identity
from atlas_terminal.japan_company_sources import (
    COMPANY_SOURCES,
    company_identity,
    edinet_key,
)
from atlas_terminal.margin_sources import MAX_PDF_BYTES, margin_identity, margin_reports
from atlas_terminal.policy_sources import parse_policy, policy_identity
from atlas_terminal.prediction_sources import PREDICTION_SOURCE, prediction_identity
from atlas_terminal.providers import CachedHTTPTransport, Snapshot, decode_json
from atlas_terminal.reference_rate_sources import NYFED_RATES as NYFED_RATES
from atlas_terminal.reference_rate_sources import nyfed_identity
from atlas_terminal.regional_bond_sources import (
    ECB_CURVE_BASE,
    RBA_URL,
    ecb_curve_identity,
    parse_ecb_curve,
    parse_rba_curve,
)
from atlas_terminal.regional_economic_sources import (
    REGIONAL_ECONOMIC_SOURCES,
    regional_economic_identity,
)
from atlas_terminal.sdmx_sources import BASES, SDMX_SOURCES, sdmx_source, valid_sdmx_url
from atlas_terminal.source_deadline import timeout_options
from atlas_terminal.util import stamp, state_dir

BLS_BASE = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
TREASURY_BASE = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value="
)
DEBT_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/"
    "debt_to_penny?format=json&sort=-record_date&page%5Bsize%5D=500"
)
FED_NEWS = "https://www.federalreserve.gov/feeds/press_all.xml"
ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
TREASURY_REAL_BASE = TREASURY_BASE.replace(
    "daily_treasury_yield_curve", "daily_treasury_real_yield_curve"
)
# V1 is registration-free: three years by default, at most ten years per dated POST.
BLS_SERIES = {
    "cpi": ("CUUR0000SA0", "Consumer price index, all urban consumers", "index_1982_84_100", False),
    "cpi_sa": (
        "CUSR0000SA0",
        "Consumer price index, all urban consumers",
        "index_1982_84_100",
        True,
    ),
    "core_cpi": ("CUUR0000SA0L1E", "CPI excluding food and energy", "index_1982_84_100", False),
    "unemployment": ("LNS14000000", "Civilian unemployment rate", "percent", True),
    "participation": ("LNS11300000", "Civilian labor force participation rate", "percent", True),
    "payrolls": ("CES0000000001", "Total nonfarm payroll employment", "thousand_persons", True),
    "hourly_earnings": (
        "CES0500000003",
        "Average hourly earnings, private employees",
        "USD_per_hour",
        True,
    ),
    "job_openings": ("JTS000000000000000JOL", "Total nonfarm job openings", "thousand_jobs", True),
    "ppi_final_demand": ("WPSFD4", "PPI final demand", "index_2009_11_100", True),
    "manufacturing_payrolls": (
        "CES3000000001",
        "Manufacturing payroll employment",
        "thousand_persons",
        True,
    ),
    "average_weekly_hours": (
        "CES0500000002",
        "Average weekly hours, all private employees",
        "hours_per_week",
        True,
    ),
    "employment_population_ratio": (
        "LNS12300000",
        "Employment-population ratio, age 16 and over",
        "percent",
        True,
    ),
    "job_quits": ("JTS000000000000000QUL", "Total nonfarm quits", "thousand_jobs", True),
    "job_hires": ("JTS000000000000000HIL", "Total nonfarm hires", "thousand_jobs", True),
    "job_layoffs": (
        "JTS000000000000000LDL",
        "Total nonfarm layoffs and discharges",
        "thousand_jobs",
        True,
    ),
    "long_term_unemployment": (
        "LNS13008636",
        "Unemployed for 27 weeks and over",
        "thousand_persons",
        True,
    ),
}

# Additional interpretation fields for the quarterly and trade-price selections.
# The four-field registry remains compatible with existing series-ID consumers.
BLS_DETAILS: dict[str, dict[str, Any]] = {}
for _key, _code, _title, _unit, _frequency, _adjusted, _annualized, _base, _kind in (
    (
        "employment_cost_compensation",
        "CIS1010000000000I",
        "Employment Cost Index, total compensation, all civilian workers",
        "index_2005_12_100",
        "quarterly",
        True,
        False,
        "2005-12",
        "quarter_end_reference_month",
    ),
    (
        "employment_cost_wages",
        "CIS1020000000000I",
        "Employment Cost Index, wages and salaries, all civilian workers",
        "index_2005_12_100",
        "quarterly",
        True,
        False,
        "2005-12",
        "quarter_end_reference_month",
    ),
    (
        "nonfarm_productivity_growth",
        "PRS85006092",
        "Nonfarm business labor productivity, quarter-on-quarter percent change at annual rate",
        "percent",
        "quarterly",
        True,
        True,
        None,
        "quarter_over_quarter_change",
    ),
    (
        "unit_labor_cost_growth",
        "PRS85006112",
        "Nonfarm business unit labor costs, quarter-on-quarter percent change at annual rate",
        "percent",
        "quarterly",
        True,
        True,
        None,
        "quarter_over_quarter_change",
    ),
    (
        "import_prices",
        "EIUIR",
        "Import price index, all commodities, BEA end-use classification",
        "index_2000_100",
        "monthly",
        False,
        False,
        "2000",
        None,
    ),
    (
        "export_prices",
        "EIUIQ",
        "Export price index, all commodities, BEA end-use classification",
        "index_2000_100",
        "monthly",
        False,
        False,
        "2000",
        None,
    ),
):
    BLS_SERIES[_key] = (_code, _title, _unit, _adjusted)
    BLS_DETAILS[_key] = {
        "frequency": _frequency,
        "annualized": _annualized,
        "base_period": _base,
    }
    if _kind is not None:
        BLS_DETAILS[_key]["reference_period_kind"] = _kind

SOURCE_CATALOG = {
    "polymarket": PREDICTION_SOURCE,
    **COMPANY_SOURCES,
    "yahoo_finance": YAHOO_SOURCE,
    "rba": {
        "name": "Reserve Bank of Australia government bond yields",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.rba.gov.au/statistics/tables/",
        "terms": "https://www.rba.gov.au/copyright/",
        "use": (
            "Attribute RBA and original providers; identify Atlas modifications and retain "
            "copyright conditions and third-party exceptions."
        ),
        "cadence": "F2 business-day interpolated nominal government yields at 2/3/5/10 years.",
    },
    "japan_mof": {
        "name": "Japan Ministry of Finance JGB interest rates",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": JGB_BASE + "index.htm",
        "terms": "https://www.mof.go.jp/about_mof/notice/index.html",
        "use": (
            "Attribute Ministry of Finance Japan and identify Atlas normalization or calculations; "
            "retain the source public-data license and third-party exceptions."
        ),
        "cadence": (
            "Business-day constant-maturity yields, 1–40 years. Historical and current-month "
            "CSV files are combined with the current file authoritative for overlapping dates."
        ),
    },
    "ecb": {
        "name": "European Central Bank reference FX and euro area government curves",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
        "yield_curve_documentation": "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_area_yield_curves/html/index.en.html",
        "terms": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "use": (
            "Attribute ECB, identify modifications and retain source conditions; "
            "reference FX and estimated government curves are not executable quotes."
        ),
        "cadence": (
            "European working-day reference observations; full published XML history, "
            "currency-specific start/end dates and no holiday filling. "
            "Separate YC requests provide AAA/all-rating euro area spot curves."
        ),
    },
    "new_york_fed": {
        "name": "Federal Reserve Bank of New York reference rates",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://markets.newyorkfed.org/static/docs/markets-api.html",
        "terms": "https://www.newyorkfed.org/privacy/termsofuse",
        "use": (
            "Retain copyright, reference-rate notices, disclaimers and terms; "
            "identify normalization and do not imply endorsement."
        ),
        "cadence": (
            "Business-day effective observations with possible revisions; "
            "latest 90 by default; explicit observation dates use annual source requests."
        ),
    },
    "sec": {
        "name": "SEC EDGAR",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": True,
        "documentation": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "terms": "https://www.sec.gov/about/developer-resources",
        "use": (
            "Structured facts and filing metadata; third-party filing content "
            "retains its own rights."
        ),
        "cadence": "As filed and as the public SEC endpoints are updated; no latency SLA.",
    },
    "bls": {
        "name": "US Bureau of Labor Statistics API V1",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.bls.gov/developers/api_signature.htm",
        "terms": "https://www.bls.gov/bls/linksite.htm",
        "use": (
            "US government statistics with source attribution; preserve series"
            " and revision caveats."
        ),
        "cadence": (
            "Release schedule; unregistered V1 defaults to three years; dated requests use "
            "ten-year chunks, 25 "
            "queries/day per upstream policy."
        ),
    },
    "treasury": {
        "name": "US Treasury daily par yield curve",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://home.treasury.gov/interest-rates-data-csv-archive",
        "terms": "https://home.treasury.gov/footer/privacy-act",
        "use": (
            "Treasury daily nominal/real indicative par yields, "
            "not security prices or executable quotes."
        ),
        "cadence": "Business-day observations, with possible corrections.",
    },
    "fiscaldata": {
        "name": "US Treasury Fiscal Data",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://fiscaldata.treasury.gov/api-documentation/",
        "terms": "https://fiscaldata.treasury.gov/about-us/",
        "use": "Government Debt to the Penny dataset with original dates and units.",
        "cadence": "Source publication schedule, latest 500 records maximum per request.",
    },
    "federal_reserve": {
        "name": "Federal Reserve press-release RSS",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.federalreserve.gov/feeds/feeds.htm",
        "terms": "https://www.federalreserve.gov/disclaimer.htm",
        "use": "Official release titles, dates and original links; no newswire articles.",
        "cadence": "Feed publication; limited to entries retained by the upstream feed.",
    },
}
SOURCE_CATALOG.update(ECONOMIC_SOURCES)
SOURCE_CATALOG.update(
    {
        "bitbank": {
            "optional": True,
            "name": "bitbank public price-level order book",
            "upstream_price": "0",
            "api_key_required": False,
            "contact_user_agent_required": False,
            "documentation": "https://github.com/bitbankinc/bitbank-api-docs/blob/master/public-api_JP.md",
            "terms": "https://bitbank.cc/doc/tos",
            "use": "Single-venue snapshots; retain source conditions. No redistribution grant.",
            "cadence": "On-demand bounded snapshot, five-second cache; no guaranteed live stream.",
        },
        "fisco": {
            "optional": True,
            "name": "FISCO public company report directory",
            "upstream_price": "0",
            "api_key_required": False,
            "contact_user_agent_required": False,
            "documentation": "https://www.fisco.co.jp/service/report/",
            "terms": "https://www.fisco.co.jp/copyright/",
            "use": "Discovery metadata/links only. No remote PDF extraction or redistribution.",
            "cadence": "Rolling directory, one-hour cache; no archive completeness guarantee.",
        },
    }
)
SOURCE_CATALOG.update(SDMX_SOURCES)
SOURCE_CATALOG.update(REGIONAL_ECONOMIC_SOURCES)
SOURCE_CATALOG.update(ACTIVITY_SOURCES)
SOURCE_CATALOG["federal_reserve"].update(
    name="Federal Reserve press releases and G.17 industrial statistics",
    cadence="RSS feed publication and monthly revised G.17 observations; six-hour data cache.",
    use=(
        "Official releases and G.17 industrial production/capacity utilization, "
        "with original definitions and revised snapshots."
    ),
)


def bls_url(series_id: str, first: int, last: int) -> str:
    return BLS_BASE + series_id + "?" + urlencode({"startyear": first, "endyear": last})


def valid_bls_url(url: str) -> bool:
    base, sep, query = url.partition("?")
    if base not in {BLS_BASE + item[0] for item in BLS_SERIES.values()}:
        return False
    if not sep:
        return True
    try:
        parts = parse_qs(query, strict_parsing=True)
        first, last = int(parts["startyear"][0]), int(parts["endyear"][0])
        return (
            1900 <= first <= last <= 2100
            and last - first <= 9
            and url == bls_url(base.removeprefix(BLS_BASE), first, last)
        )
    except (ValueError, KeyError, IndexError):
        return False


def xml_root(body: bytes) -> ET.Element:
    if len(body) > 32 * 1024 * 1024 or re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.I):
        raise AtlasError("SOURCE_UNAVAILABLE", "XML declarations or oversized documents rejected.")
    # Reject other encodings before parsing so UTF-16 cannot bypass the declaration check.
    if b"\x00" in body:
        raise AtlasError("SOURCE_UNAVAILABLE", "Only UTF-8 compatible XML is supported.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise AtlasError("SOURCE_UNAVAILABLE", "Malformed source XML.", status=502) from None
    if sum(1 for _ in root.iter()) > 400_000:
        raise AtlasError("SOURCE_UNAVAILABLE", "XML element limit exceeded.", status=502)
    return root


def local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def contains_private_token(value: Any, token: str) -> bool:
    """Check decoded JSON too, including escaped string values and object keys."""
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(
            token in key or contains_private_token(item, token) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_token(item, token) for item in value)
    return False


class PublicTransport(CachedHTTPTransport):
    """Separate from SEC: never sends the operator's private SEC contact to other hosts."""

    def __init__(self, *, cache_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(
            "AtlasTerminal/0.2 (public-data client)",
            cache_path=cache_path or state_dir() / "public-cache.sqlite3",
            **kwargs,
        )
        self.interval = max(1.0, self.interval)
        with self.db.connect(write=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS oecd_attempts (attempted_at REAL NOT NULL)")

    @staticmethod
    def validate_url(url: str) -> None:
        if (
            url in {DEBT_URL, FED_NEWS, ECB_FX_URL, RBA_URL, *JGB_URLS}
            or nyfed_identity(url) is not None
            or policy_identity(url) is not None
            or ecb_curve_identity(url) is not None
            or valid_bls_url(url)
            or validate_economic_url(url)
            or valid_sdmx_url(url)
            or regional_economic_identity(url) is not None
            or activity_identity(url) is not None
            or global_wdi_identity(url) is not None
            or fred_series_identity(url) is not None
            or company_identity(url) is not None
            or margin_identity(url) is not None
            or prediction_identity(url) is not None
            or yahoo_identity(url) is not None
            or intraday_identity(url) is not None
            or re.fullmatch(re.escape(TREASURY_BASE) + r"(?:199[0-9]|20[0-9]{2}|2100)", url)
            or re.fullmatch(re.escape(TREASURY_REAL_BASE) + r"(?:200[3-9]|20[1-9][0-9]|2100)", url)
        ):
            return
        raise AtlasError("INVALID_INPUT", "URL is outside the free-source endpoint catalog.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        if prediction := prediction_identity(url):
            from atlas_terminal.research_predictions import MAX_BODY_BYTES

            if len(body) > MAX_BODY_BYTES:
                raise ValueError("Oversized prediction response")
            data = decode_json(body)
            key = "events" if prediction[0] == "search" else "markets"
            if not isinstance(data, dict) or key not in data or "error" in data:
                raise ValueError("Malformed Gamma prediction response")
            return
        if margin := margin_identity(url):
            if margin == "index":
                margin_reports(Snapshot(body, url, stamp()))
            elif len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF-"):
                raise ValueError("Expected bounded JPX margin PDF")
            return
        if url.startswith(BEA_BASE + "?"):
            value = decode_json(body)
            if (
                not isinstance(value, dict)
                or bea_key().encode() in body
                or contains_private_token(value, bea_key())
            ):
                raise ValueError("Malformed BEA response or echoed private credential")
            return
        if fred_series_identity(url) is not None:
            value = decode_json(body)
            if (
                not isinstance(value, dict)
                or activity_key("fred").encode() in body
                or contains_private_token(value, activity_key("fred"))
                or "error_code" in value
                or "error" in value
            ):
                raise ValueError("Malformed FRED response or echoed private credential")
            return
        if company := company_identity(url):
            if company[1] == "xbrl":
                from atlas_terminal.research_japan_companies import xbrl_member

                xbrl_member(body, forbidden_token=edinet_key().encode())
            elif company[0] == "jpx":
                from atlas_terminal.research_japan_companies import jpx_rows

                jpx_rows(Snapshot(body, url, stamp()))
            else:
                value = decode_json(body)
                if (
                    not isinstance(value, dict)
                    or edinet_key().encode() in body
                    or contains_private_token(value, edinet_key())
                ):
                    raise ValueError("Malformed EDINET JSON or echoed secret")
            return
        if yahoo_identity(url) is not None or intraday_identity(url) is not None:
            value = decode_json(body)
            if not isinstance(value, dict) or not isinstance(value.get("chart"), dict):
                raise ValueError("Expected international Yahoo chart JSON")
            return
        if global_wdi_identity(url) is not None:
            value = decode_json(body)
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("Expected World Bank metadata/observation JSON")
            return
        if activity_selection := activity_identity(url):
            if url in G17_URLS:
                if b'"B50001:' not in body or b"\x00" in body:
                    raise ValueError("Expected G.17 fixed-width text")
            else:
                value = decode_json(body)
                if not isinstance(value, (dict, list)) or not value:
                    raise ValueError("Expected nonempty official activity JSON")
                if activity_selection[0] in {"census", "fred"}:
                    if activity_key(
                        activity_selection[0]
                    ).encode() in body or contains_private_token(
                        value, activity_key(activity_selection[0])
                    ):
                        raise ValueError("Source echoed a private credential")
                    if isinstance(value, dict) and ("error_code" in value or "error" in value):
                        raise ValueError("Source rejected the activity request")
            return
        if policy_selection := policy_identity(url):
            parse_policy(Snapshot(body, url, "2000-01-01T00:00:00Z"), policy_selection[0])
            return
        if selection := ecb_curve_identity(url):
            parse_ecb_curve(Snapshot(body, url, "2000-01-01T00:00:00Z"), *selection)
            return
        if url == RBA_URL:
            parse_rba_curve(Snapshot(body, url, "2000-01-01T00:00:00Z"))
            return
        if url in JGB_URLS:
            parse_jgb_csv(Snapshot(body, url, "2000-01-01T00:00:00Z"))
            return
        if (
            url.startswith((BLS_BASE, BEA_BASE + "?", BOJ_BASE + "?"))
            or regional_economic_identity(url) is not None
            or url == DEBT_URL
            or nyfed_identity(url) is not None
        ):
            if not isinstance(decode_json(body), dict):
                raise ValueError("Expected JSON object")
        else:
            xml_root(body)

    def get(self, url: str, ttl: int, *, force_refresh: bool = False) -> Snapshot:
        self.validate_url(url)
        if yahoo_identity(url) is not None or intraday_identity(url) is not None:
            require_yahoo_enabled()
        if fred_series_identity(url) is not None:
            activity_key("fred")
        if (company := company_identity(url)) and company[0] == "edinet":
            edinet_key()
        if url.startswith(BEA_BASE + "?"):
            bea_key()
        if (activity := activity_identity(url)) and activity[0] in {"census", "fred"}:
            activity_key(activity[0])
        result = super().get(url, ttl, force_refresh=force_refresh)
        if url.startswith(BEA_BASE + "?"):
            # Legacy caches may predate credential-echo validation. Never expose such bodies.
            try:
                self.validate_body(url, result.body)
            except ValueError:
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Cached BEA response failed credential validation."
                ) from None
        return result

    def request(self, url: str, headers: dict[str, str]) -> Any:
        self.validate_url(url)
        if fred_series_identity(url) is not None:
            return self.client.stream(
                "GET",
                httpx.URL(url).copy_add_param("api_key", activity_key("fred")),
                headers={**headers, "Accept": "application/json"},
                follow_redirects=False,
            )
        if (company := company_identity(url)) and company[0] == "edinet":
            return self.client.stream(
                "GET",
                httpx.URL(url).copy_add_param("Subscription-Key", edinet_key()),
                headers=headers,
                follow_redirects=False,
                **timeout_options(),
            )
        if activity := activity_identity(url):
            source, _, kind = activity
            if source in {"census", "fred"}:
                key_name = "key" if source == "census" else "api_key"
                return self.client.stream(
                    "GET",
                    url + "&" + urlencode({key_name: activity_key(source)}),
                    headers={**headers, "Accept": "application/json"},
                    follow_redirects=False,
                )
            if source == "statcan" and kind == "metadata" and "getSeriesInfoFromVector?" in url:
                return self.client.stream(
                    "POST",
                    STATCAN_BASE + "getSeriesInfoFromVector",
                    headers={**headers, "Accept": "application/json"},
                    json=[{"vectorId": int(parse_qs(url.split("?", 1)[1])["vectorId"][0])}],
                )
        if regional_economic_identity(url) is not None:
            headers = {**headers, "Accept": "application/json"}
        if (
            policy_identity(url) is not None
            or ecb_curve_identity(url) is not None
            or url == RBA_URL
        ):
            headers = {**headers, "Accept": "text/csv"}
        if sdmx_source(url):
            headers = {
                **headers,
                "Accept": (
                    "application/vnd.sdmx.structure+xml;version=2.1"
                    if "/dataflow/" in url
                    and not url.startswith("https://api.imf.org/external/sdmx/3.0/data/")
                    else "application/xml"
                ),
            }
        if url.startswith(BEA_BASE + "?"):
            # Only the outbound request receives the key. Provenance and cache keys never do.
            return self.client.stream(
                "GET",
                url + "&" + urlencode({"UserID": bea_key()}),
                headers=headers,
                follow_redirects=False,
            )
        if url.startswith(BLS_BASE) and "?" in url:
            base, query = url.split("?", 1)
            p = parse_qs(query)
            return self.client.stream(
                "POST",
                BLS_BASE,
                headers=headers,
                json={
                    "seriesid": [base.removeprefix(BLS_BASE)],
                    "startyear": p["startyear"][0],
                    "endyear": p["endyear"][0],
                },
            )
        return super().request(url, headers)

    def save(self, url: str, body: bytes, ttl: int, headers: Any, old: Any = None) -> str:
        if url.startswith(BEA_BASE + "?"):
            # Conditional 304 responses can reuse legacy cached bodies without validate_body.
            self.validate_body(url, body)
        company = company_identity(url)
        activity = activity_identity(url)
        key = (
            bea_key()
            if url.startswith(BEA_BASE + "?")
            else edinet_key()
            if company and company[0] == "edinet"
            else activity_key("fred")
            if fred_series_identity(url) is not None
            else activity_key(activity[0])
            if activity and activity[0] in {"fred", "census"}
            else None
        )
        if key is not None:
            headers = {field: value for field, value in headers.items() if key not in str(value)}
            if old is not None:
                old = dict(old)
                for field in ("etag", "modified"):
                    if key in str(old[field]):
                        old[field] = None
        return super().save(url, body, ttl, headers, old)

    def before_request(self, url: str) -> None:
        if yahoo_identity(url) is not None or intraday_identity(url) is not None:
            require_yahoo_enabled()
        if url.startswith(BASES["oecd"] + "/"):
            now = self.clock()
            with self.db.connect(write=True) as conn:
                conn.execute("DELETE FROM oecd_attempts WHERE attempted_at <= ?", (now - 3600,))
                row = conn.execute(
                    "SELECT COUNT(*) AS used, MIN(attempted_at) AS oldest FROM oecd_attempts"
                ).fetchone()
                if row["used"] >= 60:
                    raise AtlasError(
                        "SOURCE_RATE_LIMITED",
                        "Local OECD rolling hourly quota reached.",
                        retryable=True,
                        status=429,
                        retry_after_seconds=max(1, math.ceil(row["oldest"] + 3600 - now)),
                    )
                conn.execute("INSERT INTO oecd_attempts VALUES (?)", (now,))
        if url.startswith(BLS_BASE):
            day = datetime.fromtimestamp(self.clock(), UTC).date().isoformat()
            with self.db.connect(write=True) as conn:
                row = conn.execute(
                    "SELECT used FROM source_daily_usage WHERE bucket='bls' AND day=?", (day,)
                ).fetchone()
                if row and row["used"] >= 25:
                    raise AtlasError(
                        "SOURCE_RATE_LIMITED",
                        "Local BLS daily quota reached; retry on a later UTC day.",
                        retryable=True,
                        retry_after_seconds=86400 - int(self.clock()) % 86400,
                        status=429,
                    )
                conn.execute(
                    (
                        "INSERT INTO source_daily_usage VALUES ('bls',?,1) ON "
                        "CONFLICT(bucket,day) DO UPDATE SET used=used+1"
                    ),
                    (day,),
                )


class PublicFixtures:
    synthetic = True

    def get(self, url: str, ttl: int) -> Snapshot:
        if prediction := prediction_identity(url):
            name = (
                "prediction_search.json" if prediction[0] == "search" else "prediction_event.json"
            )
            return Snapshot(
                files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-11-07T12:00:00Z",
            )
        if margin := margin_identity(url):
            name = "margin_index.html" if margin == "index" else "margin_daily.pdf"
            return Snapshot(
                files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-11-07T12:00:00Z",
            )
        if company := company_identity(url):
            name = {
                "securities": "jpx_companies.xls",
                "list": "edinet_documents.json",
                "xbrl": "edinet_filing.zip",
            }[company[1]]
            return Snapshot(
                files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-10-27T02:00:00Z",
            )
        PublicTransport.validate_url(url)
        if policy_selection := policy_identity(url):
            name = "policy_" + policy_selection[0] + ".csv"
            return Snapshot(
                files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-10-30T12:00:00Z",
            )
        from atlas_terminal.country_macro_catalog import CPI_FLOW, OECD_FLOW, RATE_FLOW
        from atlas_terminal.sdmx_sources import structure_url

        for country_flow, fixture in ((CPI_FLOW, "cpi"), (RATE_FLOW, "rates"), (OECD_FLOW, "oecd")):
            country_source = "oecd" if fixture == "oecd" else "imf"
            country_prefix = (
                structure_url(country_source, country_flow).split("/latest?", 1)[0] + "/"
            )
            is_metadata = url.startswith(country_prefix)
            flow_id = country_flow.split(",")[1]
            is_data = (
                f"/dataflow/IMF.STA/{flow_id}/" in url and "/3.0/data/" in url
                if country_source == "imf"
                else f"/data/OECD.SDD.STES,{flow_id}," in url
            )
            if not (is_metadata or is_data):
                continue
            name = f"country_{fixture}_{'structure' if is_metadata else 'data'}.xml"
            body = files("atlas_terminal").joinpath("resources", "research", name).read_bytes()
            if is_data:
                country_components = url.split("?", 1)[0].rsplit("/", 1)[-1].split(".")
                country_keys = (
                    (
                        "REF_AREA",
                        "FREQ",
                        "MEASURE",
                        "UNIT_MEASURE",
                        "ACTIVITY",
                        "ADJUSTMENT",
                        "TRANSFORMATION",
                        "TIME_HORIZ",
                        "METHODOLOGY",
                    )
                    if fixture == "oecd"
                    else (
                        "COUNTRY",
                        "INDEX_TYPE",
                        "COICOP_1999",
                        "TYPE_OF_TRANSFORMATION",
                        "FREQUENCY",
                    )
                    if fixture == "cpi"
                    else ("COUNTRY", "INDICATOR", "FREQUENCY")
                )
                for country_key, country_code in zip(country_keys, country_components, strict=True):
                    body = body.replace(("__" + country_key + "__").encode(), country_code.encode())
                quarterly = country_components[-1] == "Q" and fixture == "cpi"
                for index in range(1, 4):
                    period = f"2026-Q{index}" if quarterly else f"2026-M{index:02d}"
                    body = body.replace(f"__PERIOD{index}__".encode(), period.encode())
            return Snapshot(
                body, "https://fixtures.atlas.invalid/research/" + name, "2026-10-27T02:00:00Z"
            )
        if "/dataflow/IMF.RES/PCPS/" in url and sdmx_source(url) == "imf":
            name = "pcps_data.xml" if "/3.0/data/" in url else "pcps_structure.xml"
            body = files("atlas_terminal").joinpath("resources", "research", name).read_bytes()
            if name == "pcps_data.xml":
                pcps_key = url.split("?", 1)[0].rsplit("/", 1)[-1].split(".")
                body = body.replace(b"__INDICATOR__", pcps_key[1].encode())
            return Snapshot(
                body, "https://fixtures.atlas.invalid/research/" + name, "2026-10-27T02:00:00Z"
            )
        if global_selection := global_wdi_identity(url):
            kind, country, series = global_selection
            area = GLOBAL_AREAS[country] if country else None
            global_spec = GLOBAL_INDICATORS[series] if series else None
            template = (
                files("atlas_terminal")
                .joinpath("resources", "research", f"global_wdi_{kind}.json")
                .read_bytes()
            )
            values = {
                "country": country,
                "iso3": area["iso3"] if area else None,
                "country_name": area["name"] if area else None,
                "region": "NA" if area and area["kind"] == "regional_aggregate" else "FIC",
                "indicator": global_spec["code"] if global_spec else None,
                "title": global_spec["title"] if global_spec else None,
            }
            for key, item in values.items():
                template = template.replace(
                    ('"{{' + key + '}}"').encode(), json.dumps(item).encode()
                )
            return Snapshot(
                template,
                f"https://fixtures.atlas.invalid/research/global_wdi/{kind}/{country}/{series}.json",
                "2026-10-30T01:00:00Z",
            )
        if fred := fred_series_identity(url):
            if fred[0] == "DEMO_INDEX":
                name = f"fred_DEMO_INDEX_{fred[1]}.json"
                return Snapshot(
                    files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                    "https://fixtures.atlas.invalid/research/" + name,
                    "2026-10-29T01:00:00Z",
                )
            if activity_identity(url) is None:
                raise AtlasError("SOURCE_UNAVAILABLE", "No fictional FRED data for this series.")
        if intraday := intraday_identity(url):
            name = f"yahoo_intraday_{intraday[0]}_{intraday[3]}.json"
            resource = files("atlas_terminal").joinpath("resources", "research", name)
            if not resource.is_file():
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "No fictional intraday data for this selection."
                )
            return Snapshot(
                resource.read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-10-29T21:00:00Z",
            )
        if yahoo := yahoo_identity(url):
            name = "yahoo_chart_" + yahoo[0] + ".json"
            if not files("atlas_terminal").joinpath("resources", "research", name).is_file():
                raise AtlasError("SOURCE_UNAVAILABLE", "No fictional Yahoo data for this symbol.")
            return Snapshot(
                files("atlas_terminal").joinpath("resources", "research", name).read_bytes(),
                "https://fixtures.atlas.invalid/research/" + name,
                "2026-10-27T02:00:00Z",
            )
        elif activity := activity_identity(url):
            source, series, kind = activity
            name = (
                ("g17_" + ("ip_sa.txt" if series == "us_industrial_production" else "utl_sa.txt"))
                if source == "federal_reserve"
                else f"activity_{series}_{kind}.json"
            )
        elif regional := regional_economic_identity(url):
            name = f"{regional[0]}_{regional[1]}.json"
        elif sdmx_name := sdmx_source(url):
            kind = "structure" if url.startswith(BASES[sdmx_name] + "/dataflow/") else "data"
            from atlas_terminal.activity_catalog import ACTIVITY_SERIES
            from atlas_terminal.sdmx_sources import structure_url

            curated = None
            for activity_name, activity_spec in ACTIVITY_SERIES.items():
                if (
                    activity_spec["source_id"] not in {"abs", "oecd"}
                    or activity_spec["source_id"] != sdmx_name
                ):
                    continue
                activity_codes = list(activity_spec.get("dimensions", {}).values())
                flow_prefix = ",".join(activity_spec["code"].split(",")[:2]) + ","
                structure_urls = {structure_url(sdmx_name, activity_spec["code"])}
                if activity_spec["code"].endswith(",latest"):
                    structure_urls.add(
                        structure_url(
                            sdmx_name, activity_spec["code"].removesuffix(",latest") + ",1.0.0"
                        )
                    )
                if url in structure_urls or (
                    url.startswith(BASES[sdmx_name] + "/data/" + flow_prefix)
                    and "/" + ".".join(activity_codes) + "?" in url
                ):
                    curated = activity_name
                    break
            name = (
                f"activity_{curated}_{kind}.xml"
                if curated is not None
                else f"sdmx_{sdmx_name}_{kind}.xml"
            )
        elif url.startswith(BLS_BASE):
            name = "bls_" + url.removeprefix(BLS_BASE).split("?")[0] + ".json"
        elif url.startswith(BEA_BASE + "?"):
            name = "bea_" + parse_qs(url.split("?", 1)[1])["TableName"][0] + ".json"
        elif url.startswith(BOJ_BASE + "?"):
            p = parse_qs(url.split("?", 1)[1])
            key = next(
                k
                for k, s in JAPAN_SERIES.items()
                if s["source_id"] == "boj"
                and s["database"] == p["db"][0]
                and s["code"] == p["code"][0]
            )
            name = "boj_" + key + ".json"
        elif url.startswith(ESTAT_BASE):
            p = parse_qs(url.split("?", 1)[1])
            if "getIndicatorInfo?" in url:
                key = next(
                    k
                    for k, s in JAPAN_SERIES.items()
                    if s["source_id"] == "estat" and s["code"] == p["SearchIndicatorWord"][0]
                )
                name = "estat_" + key + "_metadata.xml"
            else:
                codes = {"9900000000000000001": "cpi", "9900000000000000002": "unemployment"}
                for key, spec in JAPAN_SERIES.items():
                    if spec["source_id"] == "estat" and key not in {"cpi", "unemployment"}:
                        body = (
                            files("atlas_terminal")
                            .joinpath("resources", "research", f"estat_{key}_metadata.xml")
                            .read_bytes()
                        )
                        code = xml_root(body).find("METADATA_INF/CLASS_INF/CLASS_OBJ")
                        assert code is not None
                        codes[code.get("code", "")] = key
                key = codes[p["IndicatorCode"][0]]
                name = "estat_" + key + ".xml"
        elif url == ECB_FX_URL:
            name = "fx.xml"
        elif (nyfed := nyfed_identity(url)) is not None:
            series = nyfed[0]
            name = series + ".json"
        elif url.startswith(TREASURY_REAL_BASE):
            if not url.endswith(("2025", "2026")):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Synthetic real-yield fixtures cover 2025–2026 only."
                )
            name = "real_yields_2025.xml" if url.endswith("2025") else "real_yields.xml"
        elif url.startswith(TREASURY_BASE):
            if not url.endswith(("2025", "2026")):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Synthetic yield fixtures cover 2025–2026 only."
                )
            name = "yields_2025.xml" if url.endswith("2025") else "yields.xml"
        elif url in JGB_URLS:
            name = "jgb_history.csv" if url == JGB_URLS[0] else "jgb_current.csv"
        elif url.startswith(ECB_CURVE_BASE):
            selection = ecb_curve_identity(url)
            assert selection is not None
            curve, year = selection
            if year != 2026:
                raise AtlasError("SOURCE_UNAVAILABLE", "Fictional ECB curves cover 2026 only.")
            name = curve + "_2026.csv"
        elif url == RBA_URL:
            name = "rba_yields.csv"
        elif url == DEBT_URL:
            name = "debt.json"
        else:
            name = "fed.xml"
        body = files("atlas_terminal").joinpath("resources", "research", name).read_bytes()
        if (nyfed := nyfed_identity(url)) is not None and nyfed[1] is not None:
            assert nyfed[2] is not None
            data = decode_json(body)
            data["refRates"] = [
                row for row in data["refRates"] if nyfed[1] <= row["effectiveDate"] <= nyfed[2]
            ]
            # Dated synthetic responses preserve decoded Decimal spellings as strings.
            body = json.dumps(data, default=str, separators=(",", ":")).encode()
        return Snapshot(
            body, "https://fixtures.atlas.invalid/research/" + name, "2026-10-20T00:00:00Z"
        )

    def close(self) -> None:
        pass
