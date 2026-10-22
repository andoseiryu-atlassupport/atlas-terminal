"""Free primary-source registry and allowlisted public HTTP transport."""

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import REFERENCE_SERIES
from atlas_terminal.providers import CachedHTTPTransport, Snapshot, decode_json
from atlas_terminal.util import state_dir

BLS_BASE = "https://api.bls.gov/publicAPI/v1/timeseries/"
TREASURY_BASE = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value="
)
DEBT_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/"
    "debt_to_penny?format=json&sort=-record_date&page%5Bsize%5D=500"
)
FED_NEWS = "https://www.federalreserve.gov/feeds/press_all.xml"
ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
TREASURY_REAL_BASE = TREASURY_BASE.replace(
    "daily_treasury_yield_curve", "daily_treasury_real_yield_curve"
)
NYFED_RATES = {
    name: f"https://markets.newyorkfed.org/api/rates/{kind}/{name}/last/90.json"
    for name, kind in [("effr", "unsecured"), ("sofr", "secured")]
}

# V1 is registration-free: the latest three years, with source-defined revisions.
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
}

SOURCE_CATALOG = {
    "ecb": {
        "name": "European Central Bank euro reference rates",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
        "terms": "https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html",
        "use": (
            "Attribute ECB, identify modifications and retain source conditions; "
            "information-purpose reference rates, not executable quotes."
        ),
        "cadence": (
            "European working-day reference observations; fixed "
            "last-90-calendar-day XML window, no holiday filling."
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
            "last 90 observations for EFFR or SOFR."
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
        "documentation": "https://www.bls.gov/developers/api_signature_v1.htm",
        "terms": "https://www.bls.gov/bls/linksite.htm",
        "use": (
            "US government statistics with source attribution; preserve series"
            " and revision caveats."
        ),
        "cadence": (
            "Release schedule; unregistered V1 latest three years, 25 "
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


def xml_root(body: bytes) -> ET.Element:
    if len(body) > 16 * 1024 * 1024 or re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.I):
        raise AtlasError("SOURCE_UNAVAILABLE", "XML declarations or oversized documents rejected.")
    # Reject other encodings before parsing so UTF-16 cannot bypass the declaration check.
    if b"\x00" in body:
        raise AtlasError("SOURCE_UNAVAILABLE", "Only UTF-8 compatible XML is supported.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise AtlasError("SOURCE_UNAVAILABLE", "Malformed source XML.", status=502) from None
    if sum(1 for _ in root.iter()) > 200_000:
        raise AtlasError("SOURCE_UNAVAILABLE", "XML element limit exceeded.", status=502)
    return root


def local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


class PublicTransport(CachedHTTPTransport):
    """Separate from SEC: never sends the operator's private SEC contact to other hosts."""

    def __init__(self, *, cache_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(
            "AtlasTerminal/0.2 (public-data client)",
            cache_path=cache_path or state_dir() / "public-cache.sqlite3",
            **kwargs,
        )

    @staticmethod
    def validate_url(url: str) -> None:
        if (
            url in {DEBT_URL, FED_NEWS, ECB_FX_URL, *NYFED_RATES.values()}
            or url in {BLS_BASE + item[0] for item in BLS_SERIES.values()}
            or re.fullmatch(re.escape(TREASURY_BASE) + r"(?:199[0-9]|20[0-9]{2}|2100)", url)
            or re.fullmatch(re.escape(TREASURY_REAL_BASE) + r"(?:200[3-9]|20[1-9][0-9]|2100)", url)
        ):
            return
        raise AtlasError("INVALID_INPUT", "URL is outside the free-source endpoint catalog.")

    @staticmethod
    def validate_body(url: str, body: bytes) -> None:
        if url.startswith(BLS_BASE) or url == DEBT_URL or url in NYFED_RATES.values():
            if not isinstance(decode_json(body), dict):
                raise ValueError("Expected JSON object")
        else:
            xml_root(body)

    def before_request(self, url: str) -> None:
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
        PublicTransport.validate_url(url)
        if url.startswith(BLS_BASE):
            name = "bls_" + url.removeprefix(BLS_BASE) + ".json"
        elif url == ECB_FX_URL:
            name = "fx.xml"
        elif url in NYFED_RATES.values():
            series = next(name for name in REFERENCE_SERIES if NYFED_RATES[name] == url)
            name = series + ".json"
        elif url.startswith(TREASURY_REAL_BASE):
            if not url.endswith("2026"):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Synthetic real-yield fixture covers 2026 only."
                )
            name = "real_yields.xml"
        elif url.startswith(TREASURY_BASE):
            if not url.endswith("2026"):
                raise AtlasError("SOURCE_UNAVAILABLE", "Synthetic yield fixture covers 2026 only.")
            name = "yields.xml"
        elif url == DEBT_URL:
            name = "debt.json"
        else:
            name = "fed.xml"
        body = files("atlas_terminal").joinpath("resources", "research", name).read_bytes()
        return Snapshot(
            body, "https://fixtures.atlas.invalid/research/" + name, "2026-10-20T00:00:00Z"
        )

    def close(self) -> None:
        pass
