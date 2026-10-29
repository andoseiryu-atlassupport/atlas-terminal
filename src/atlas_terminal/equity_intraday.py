"""Bounded canonical Yahoo intraday endpoints, separate from daily prices."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode

from atlas_terminal.equity_sources import YAHOO_BASE, yahoo_symbol, yahoo_timezone
from atlas_terminal.errors import AtlasError

INTRADAY_INTERVALS: dict[str, dict[str, Any]] = {
    "1m": {"seconds": 60, "maximum_days": 7, "lookback_days": 7},
    "5m": {"seconds": 300, "maximum_days": 31, "lookback_days": 60},
    "15m": {"seconds": 900, "maximum_days": 31, "lookback_days": 60},
    "30m": {"seconds": 1800, "maximum_days": 31, "lookback_days": 60},
    "60m": {"seconds": 3600, "maximum_days": 31, "lookback_days": 60},
}


def validate_intraday_window(
    symbol: str,
    start: str,
    end: str,
    interval: str,
    *,
    now: datetime | None = None,
) -> tuple[date, date]:
    """Static bounds always apply; an explicit current time enables retention checks."""
    yahoo_symbol(symbol)
    spec = INTRADAY_INTERVALS.get(interval)
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if (
            spec is None
            or first.isoformat() != start
            or last.isoformat() != end
            or not 1900 <= first.year <= last.year <= 2100
            or not 1 <= (last - first).days + 1 <= spec["maximum_days"]
        ):
            raise ValueError("Unsupported intraday window")
    except (TypeError, ValueError):
        raise AtlasError(
            "INVALID_INPUT",
            "Intraday reads need ordered ISO dates: at most 7 days for 1m, "
            "31 days for 5m/15m/30m/60m.",
            fields=["start_date", "end_date", "interval"],
        ) from None
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Intraday retention time must be timezone-aware")
        today = now.astimezone(yahoo_timezone(symbol)).date()
        if first < today - timedelta(days=spec["lookback_days"] - 1) or last > today:
            raise AtlasError(
                "INVALID_INPUT",
                "Intraday dates must be within the latest 7 local calendar days for 1m "
                "or 60 days for other supported intervals, without future dates.",
                fields=["start_date", "end_date"],
            )
    return first, last


def intraday_url(symbol: str, start: str, end: str, interval: str) -> str:
    first, last = validate_intraday_window(symbol, start, end, interval)
    timezone = yahoo_timezone(symbol)
    params = {
        "period1": str(int(datetime.combine(first, time(), timezone).timestamp())),
        "period2": str(
            int(datetime.combine(last + timedelta(days=1), time(), timezone).timestamp())
        ),
        "interval": interval,
        "includePrePost": "false",
        "includeAdjustedClose": "false",
    }
    return YAHOO_BASE + quote(symbol, safe="") + "?" + urlencode(params)


def intraday_identity(url: str) -> tuple[str, str, str, str] | None:
    if not url.startswith(YAHOO_BASE):
        return None
    try:
        symbol, query = url.removeprefix(YAHOO_BASE).split("?", 1)
        symbol = unquote(symbol)
        timezone = yahoo_timezone(symbol)
        values = parse_qs(query, strict_parsing=True)
        first = datetime.fromtimestamp(int(values["period1"][0]), UTC)
        last = datetime.fromtimestamp(int(values["period2"][0]), UTC)
        start = first.astimezone(timezone).date().isoformat()
        end = (last.astimezone(timezone).date() - timedelta(days=1)).isoformat()
        interval = values["interval"][0]
        return (
            (symbol, start, end, interval)
            if url == intraday_url(symbol, start, end, interval)
            else None
        )
    except (KeyError, IndexError, TypeError, ValueError, OSError, OverflowError, AtlasError):
        return None
