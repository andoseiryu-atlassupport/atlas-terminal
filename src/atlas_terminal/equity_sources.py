"""Optional international Yahoo Finance source; never Yahoo! JAPAN scraping."""

import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

from atlas_terminal.errors import AtlasError

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_SYMBOL_PATTERN = r"[0-9][0-9A-Z]{3}\.T"
YAHOO_ENABLE_ENV = "ATLAS_ENABLE_YAHOO_FINANCE"
TOKYO = ZoneInfo("Asia/Tokyo")
EQUITY_DATASETS = {"equity_prices", "equity_actions"}
YAHOO_SOURCE: dict[str, Any] = {
    "name": "Yahoo Finance international (optional, unofficial access)",
    "source_kind": "secondary_aggregator",
    "optional": True,
    "operator_enablement": YAHOO_ENABLE_ENV + "=1",
    "personal_use_only": True,
    "upstream_price": "0",
    "api_key_required": False,
    "contact_user_agent_required": False,
    "documentation": "https://ranaroussi.github.io/yfinance/",
    "terms": "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
    "use": (
        "Optional personal-use access only. Yahoo and its data providers retain data rights; "
        "the Atlas code license grants no redistribution or commercial data license. "
        "No Yahoo! JAPAN endpoint, cookie/crumb harvesting or authentication bypass."
    ),
    "cadence": (
        "Tokyo daily prices and reported dividend/split events; latest month by default, "
        "at most ten calendar years per dated request; shared 1 request/second limiter "
        "and 15-minute source cache. Unofficial endpoint availability is not guaranteed."
    ),
}


def yahoo_enabled() -> bool:
    return os.environ.get(YAHOO_ENABLE_ENV) == "1"


def require_yahoo_enabled() -> None:
    if not yahoo_enabled():
        raise AtlasError(
            "CONFIG_REQUIRED",
            "Optional Yahoo Finance reads require ATLAS_ENABLE_YAHOO_FINANCE=1 "
            "in the operator environment for personal use under the source terms.",
        )


def yahoo_symbol(value: str) -> str:
    if not re.fullmatch(YAHOO_SYMBOL_PATTERN, value):
        raise AtlasError(
            "INVALID_INPUT",
            "Use an explicit four-character Tokyo Yahoo symbol, such as 7203.T or 130A.T.",
            fields=["identifier"],
        )
    return value


def yahoo_url(symbol: str, start: str | None = None, end: str | None = None) -> str:
    symbol = yahoo_symbol(symbol)
    if (start is None) != (end is None):
        raise AtlasError("INVALID_INPUT", "Provide both equity observation dates.")
    params: dict[str, str] = {"range": "1mo"}
    if start is not None and end is not None:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if (
            first > last
            or last.year - first.year > 9
            or not 1900 <= first.year <= last.year <= 2100
        ):
            raise AtlasError(
                "INVALID_INPUT", "Equity dates must span at most ten ordered calendar years."
            )
        params = {
            "period1": str(int(datetime.combine(first, time(), TOKYO).timestamp())),
            # Atlas dates are inclusive; Yahoo's upper timestamp is exclusive.
            "period2": str(
                int(datetime.combine(last + timedelta(days=1), time(), TOKYO).timestamp())
            ),
        }
    params.update(interval="1d", events="div,splits", includeAdjustedClose="true")
    return YAHOO_BASE + symbol + "?" + urlencode(params)


def yahoo_identity(url: str) -> tuple[str, str | None, str | None] | None:
    if not url.startswith(YAHOO_BASE):
        return None
    try:
        symbol, query = url.removeprefix(YAHOO_BASE).split("?", 1)
        values = parse_qs(query, strict_parsing=True)
        start = end = None
        if "period1" in values and "period2" in values:
            start = datetime.fromtimestamp(int(values["period1"][0]), TOKYO).date().isoformat()
            end = (
                datetime.fromtimestamp(int(values["period2"][0]), TOKYO).date() - timedelta(days=1)
            ).isoformat()
        return (symbol, start, end) if url == yahoo_url(symbol, start, end) else None
    except (ValueError, OverflowError, OSError, AtlasError):
        return None
