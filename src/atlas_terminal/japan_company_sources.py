"""Fixed official Japanese disclosure and listed-security endpoints."""

import os
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from atlas_terminal.errors import AtlasError

EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2/documents"
JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
COMPANY_DATASETS = {"japan_disclosures", "japan_filing_facts", "japan_securities"}
COMPANY_SOURCES: dict[str, Any] = {
    "edinet": {
        "name": "Financial Services Agency EDINET",
        "source_kind": "primary",
        "upstream_price": "0",
        "api_key_required": True,
        "contact_user_agent_required": False,
        "documentation": "https://disclosure2.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html",
        "terms": "https://disclosure2.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html",
        "use": "Official disclosures under EDINET conditions; third-party rights remain separate.",
        "cadence": (
            "Dated document lists and one original XBRL ZIP; private free EDINET_API_KEY required."
        ),
    },
    "jpx": {
        "name": "Japan Exchange Group listed-company information",
        "source_kind": "primary",
        "upstream_price": "0",
        "api_key_required": False,
        "contact_user_agent_required": False,
        "documentation": "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
        "terms": "https://www.jpx.co.jp/term-of-use/index.html",
        "use": (
            "Public reference table under JPX site conditions; "
            "no historical security master inferred."
        ),
        "cadence": "Current listed-company XLS reference table; cached for six hours.",
    },
}


def edinet_key() -> str:
    key = os.environ.get("EDINET_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9]{16,128}", key):
        raise AtlasError(
            "CONFIG_REQUIRED", "Set a free EDINET_API_KEY privately in the environment."
        )
    return key


def edinet_list_url(filing_date: str) -> str:
    day = date.fromisoformat(filing_date)
    if not 2000 <= day.year <= 2100:
        raise AtlasError("INVALID_INPUT", "Use a supported EDINET filing date.")
    return EDINET_BASE + ".json?" + urlencode({"date": day.isoformat(), "type": "2"})


def edinet_document_url(document_id: str) -> str:
    if not re.fullmatch(r"S[0-9A-Z]{7}", document_id):
        raise AtlasError("INVALID_INPUT", "Use an exact eight-character EDINET document ID.")
    return EDINET_BASE + "/" + document_id + "?type=1"


def company_identity(url: str) -> tuple[str, str] | None:
    if url == JPX_LIST:
        return "jpx", "securities"
    try:
        if url.startswith(EDINET_BASE + ".json?"):
            day = parse_qs(urlsplit(url).query, strict_parsing=True)["date"][0]
            return ("edinet", "list") if url == edinet_list_url(day) else None
        if url.startswith(EDINET_BASE + "/"):
            doc = url.removeprefix(EDINET_BASE + "/").split("?")[0]
            return ("edinet", "xbrl") if url == edinet_document_url(doc) else None
    except (KeyError, ValueError, AtlasError):
        return None
    return None
