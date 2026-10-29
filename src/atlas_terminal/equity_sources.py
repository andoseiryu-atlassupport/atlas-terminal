"""Optional international Yahoo Finance source; never Yahoo! JAPAN scraping."""

import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode
from zoneinfo import ZoneInfo

from atlas_terminal.errors import AtlasError

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
TOKYO_SYMBOL_PATTERN = r"[0-9][0-9A-Z]{3}\.T"
US_SYMBOL_PATTERN = r"[A-Z][A-Z0-9]{0,5}(?:-[A-Z])?"
# Only the explicitly identified reference instruments below are accepted outside
# US/Tokyo stocks and ETFs. A symbol spelling alone never establishes identity.
REFERENCE_INSTRUMENTS: dict[str, tuple[str, str, str, str, str, str]] = {
    "^GSPC": ("INDEX", "USD", "America/New_York", "SNP", "index_points", "source_reported_volume"),
    "^DJI": ("INDEX", "USD", "America/New_York", "DJI", "index_points", "source_reported_volume"),
    "^IXIC": ("INDEX", "USD", "America/New_York", "NIM", "index_points", "source_reported_volume"),
    "^RUT": ("INDEX", "USD", "America/New_York", "WCB", "index_points", "source_reported_volume"),
    "^VIX": ("INDEX", "USD", "America/Chicago", "CXI", "index_points", "source_reported_volume"),
    "^N225": ("INDEX", "JPY", "Asia/Tokyo", "OSA", "index_points", "source_reported_volume"),
    "EURUSD=X": (
        "CURRENCY",
        "USD",
        "Europe/London",
        "CCY",
        "USD_per_EUR",
        "source_reported_volume",
    ),
    "GBPUSD=X": (
        "CURRENCY",
        "USD",
        "Europe/London",
        "CCY",
        "USD_per_GBP",
        "source_reported_volume",
    ),
    "AUDUSD=X": (
        "CURRENCY",
        "USD",
        "Europe/London",
        "CCY",
        "USD_per_AUD",
        "source_reported_volume",
    ),
    "NZDUSD=X": (
        "CURRENCY",
        "USD",
        "Europe/London",
        "CCY",
        "USD_per_NZD",
        "source_reported_volume",
    ),
    "USDJPY=X": (
        "CURRENCY",
        "JPY",
        "Europe/London",
        "CCY",
        "JPY_per_USD",
        "source_reported_volume",
    ),
    "USDCHF=X": (
        "CURRENCY",
        "CHF",
        "Europe/London",
        "CCY",
        "CHF_per_USD",
        "source_reported_volume",
    ),
    "USDCAD=X": (
        "CURRENCY",
        "CAD",
        "Europe/London",
        "CCY",
        "CAD_per_USD",
        "source_reported_volume",
    ),
    "BTC-USD": ("CRYPTOCURRENCY", "USD", "UTC", "CCC", "USD_per_BTC", "source_reported_volume"),
    "ETH-USD": ("CRYPTOCURRENCY", "USD", "UTC", "CCC", "USD_per_ETH", "source_reported_volume"),
    "GC=F": ("FUTURE", "USD", "America/New_York", "CMX", "USD_per_source_quote_unit", "contracts"),
    "SI=F": ("FUTURE", "USD", "America/New_York", "CMX", "USD_per_source_quote_unit", "contracts"),
    "CL=F": ("FUTURE", "USD", "America/New_York", "NYM", "USD_per_source_quote_unit", "contracts"),
    "NG=F": ("FUTURE", "USD", "America/New_York", "NYM", "USD_per_source_quote_unit", "contracts"),
    "ES=F": ("FUTURE", "USD", "America/New_York", "CME", "index_points", "contracts"),
    "NQ=F": ("FUTURE", "USD", "America/New_York", "CME", "index_points", "contracts"),
}
YAHOO_SYMBOL_PATTERN = (
    "(?:"
    + "|".join([TOKYO_SYMBOL_PATTERN, US_SYMBOL_PATTERN, *map(re.escape, REFERENCE_INSTRUMENTS)])
    + ")"
)
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
        "US/Tokyo daily/intraday securities, selected indexes, FX, crypto and futures; "
        "reported security dividend/split events. Daily requests default to the latest month "
        "and allow at most ten calendar years. Intraday requests require explicit dates "
        "within recent retention bounds, with 7-day (1m) or 31-day maximum windows. "
        "Shared 1 request/second limiter "
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
            "Use an explicit US/Tokyo security symbol or a supported reference symbol.",
            fields=["identifier"],
        )
    return value


def yahoo_profile(symbol: str) -> dict[str, Any]:
    """Expected identity and units; upstream metadata must agree with this policy."""
    yahoo_symbol(symbol)
    if symbol in REFERENCE_INSTRUMENTS:
        kind, currency, timezone, exchange, unit, volume_unit = REFERENCE_INSTRUMENTS[symbol]
        return {
            "types": {kind},
            "currency": currency,
            "timezone": timezone,
            "exchanges": {exchange},
            "price_unit": unit,
            "volume_unit": volume_unit,
            "asset_class": {
                "INDEX": "index",
                "CURRENCY": "fx",
                "CRYPTOCURRENCY": "crypto",
                "FUTURE": "futures",
            }[kind],
        }
    tokyo = re.fullmatch(TOKYO_SYMBOL_PATTERN, symbol) is not None
    return {
        "types": {"EQUITY", "ETF"},
        "currency": "JPY" if tokyo else "USD",
        "timezone": "Asia/Tokyo" if tokyo else "America/New_York",
        "exchanges": {"JPX", "TYO"} if tokyo else {"NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS"},
        "price_unit": "JPY_per_share" if tokyo else "USD_per_share",
        "volume_unit": "shares",
        "asset_class": "security",
    }


def yahoo_timezone(symbol: str) -> ZoneInfo:
    return ZoneInfo(yahoo_profile(symbol)["timezone"])


def yahoo_url(symbol: str, start: str | None = None, end: str | None = None) -> str:
    symbol = yahoo_symbol(symbol)
    if (start is None) != (end is None):
        raise AtlasError("INVALID_INPUT", "Provide both equity observation dates.")
    timezone = yahoo_timezone(symbol)
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
            "period1": str(int(datetime.combine(first, time(), timezone).timestamp())),
            # Atlas dates are inclusive; Yahoo's upper timestamp is exclusive.
            "period2": str(
                int(datetime.combine(last + timedelta(days=1), time(), timezone).timestamp())
            ),
        }
    params.update(interval="1d", events="div,splits", includeAdjustedClose="true")
    return YAHOO_BASE + quote(symbol, safe="") + "?" + urlencode(params)


def yahoo_identity(url: str) -> tuple[str, str | None, str | None] | None:
    if not url.startswith(YAHOO_BASE):
        return None
    try:
        symbol, query = url.removeprefix(YAHOO_BASE).split("?", 1)
        symbol = unquote(symbol)
        timezone = yahoo_timezone(symbol)
        values = parse_qs(query, strict_parsing=True)
        start = end = None
        if "period1" in values and "period2" in values:
            start = datetime.fromtimestamp(int(values["period1"][0]), timezone).date().isoformat()
            end = (
                datetime.fromtimestamp(int(values["period2"][0]), timezone).date()
                - timedelta(days=1)
            ).isoformat()
        return (symbol, start, end) if url == yahoo_url(symbol, start, end) else None
    except (ValueError, OverflowError, OSError, AtlasError):
        return None
