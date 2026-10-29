"""Exact timestamped intraday quotes with explicit elapsed-bar and source limits."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from atlas_terminal.equity_intraday import (
    INTRADAY_INTERVALS,
    intraday_url,
    validate_intraday_window,
)
from atlas_terminal.equity_sources import yahoo_profile, yahoo_timezone
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_equities import PREFIX, unix_time, whole
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery

FIELDS = ("open", "high", "low", "close", "volume")
MAX_INTRADAY_ROWS = 12_000


def intraday_result(
    snapshot: Snapshot, symbol: str, interval: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = yahoo_profile(symbol)
    document = snapshot.data
    chart = document.get("chart") if isinstance(document, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None
    if (
        not isinstance(chart, dict)
        or chart.get("error") is not None
        or not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
    ):
        raise source_error("Yahoo did not return one successful intraday chart result.")
    result = results[0]
    meta = result.get("meta")
    if (
        not isinstance(meta, dict)
        or meta.get("symbol") not in ({symbol, "CAD=X"} if symbol == "USDCAD=X" else {symbol})
        or meta.get("currency") != profile["currency"]
        or meta.get("exchangeTimezoneName") != profile["timezone"]
        or meta.get("exchangeName") not in profile["exchanges"]
        or meta.get("instrumentType") not in profile["types"]
        or meta.get("dataGranularity") not in ({"60m", "1h"} if interval == "60m" else {interval})
    ):
        raise source_error(
            "Yahoo intraday symbol, currency, exchange, instrument type or interval differs."
        )
    instrument = {
        "symbol": symbol,
        "source_symbol": meta["symbol"],
        "currency": profile["currency"],
        "asset_class": profile["asset_class"],
        "price_unit": profile["price_unit"],
        "volume_unit": profile["volume_unit"],
        "exchange": meta["exchangeName"],
        "exchange_timezone": profile["timezone"],
        "source_instrument_type": meta["instrumentType"],
        "interval": interval,
        "source_interval": meta["dataGranularity"],
        "metadata_source": reference(snapshot, PREFIX + "/meta"),
    }
    for field in ("shortName", "longName", "fullExchangeName"):
        value = meta.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 500):
            raise source_error("Yahoo instrument name is malformed or exceeds bounds.")
        instrument[field] = value
    return result, instrument


def regular_sessions(meta: dict[str, Any]) -> list[tuple[datetime, datetime, str]]:
    """Source regular-session windows, never an inferred exchange holiday calendar."""
    periods = meta.get("tradingPeriods")
    pointer = PREFIX + "/meta/tradingPeriods"
    if isinstance(periods, dict):
        periods = periods.get("regular")
        pointer += "/regular"
    sessions: list[tuple[datetime, datetime, str]] = []
    if periods is not None:
        if not isinstance(periods, list) or len(periods) > 62:
            raise source_error("Yahoo intraday regular-session calendar exceeds bounds.")
        for outer, group in enumerate(periods):
            if not isinstance(group, list) or len(group) > 4:
                raise source_error("Yahoo intraday regular-session group is malformed.")
            for inner, period in enumerate(group):
                if not isinstance(period, dict):
                    raise source_error("Yahoo intraday regular session is malformed.")
                first, last = unix_time(period.get("start")), unix_time(period.get("end"))
                if not timedelta(0) < last - first <= timedelta(days=1):
                    raise source_error("Yahoo intraday regular-session bounds are malformed.")
                sessions.append((first, last, f"{pointer}/{outer}/{inner}"))
    current = meta.get("currentTradingPeriod")
    if current is not None:
        if not isinstance(current, dict) or not isinstance(current.get("regular"), dict):
            raise source_error("Yahoo current regular-session metadata is malformed.")
        period = current["regular"]
        first, last = unix_time(period.get("start")), unix_time(period.get("end"))
        if not timedelta(0) < last - first <= timedelta(days=1):
            raise source_error("Yahoo current regular-session bounds are malformed.")
        if not any(start == first and end == last for start, end, _ in sessions):
            sessions.append((first, last, PREFIX + "/meta/currentTradingPeriod/regular"))
    sessions.sort(key=lambda value: value[0])
    if any(right[0] < left[1] for left, right in zip(sessions, sessions[1:], strict=False)):
        raise source_error("Yahoo regular-session ranges overlap or conflict.")
    return sessions


def intraday_rows(
    result: dict[str, Any],
    snapshot: Snapshot,
    *,
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    profile = yahoo_profile(symbol)
    timezone = yahoo_timezone(symbol)
    seconds = INTRADAY_INTERVALS[interval]["seconds"]
    times = result.get("timestamp", [])
    if times is None:
        times = []
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    if (
        not isinstance(times, list)
        or len(times) > MAX_INTRADAY_ROWS
        or not isinstance(quotes, list)
        or len(quotes) != 1
        or not isinstance(quotes[0], dict)
    ):
        raise source_error("Yahoo intraday timestamp/quote arrays are malformed or exceed bounds.")
    columns: dict[str, Any] = {
        field: [] if not times and quotes[0].get(field) is None else quotes[0].get(field)
        for field in FIELDS
    }
    if any(
        not isinstance(values, list) or len(values) != len(times) for values in columns.values()
    ):
        raise source_error("Yahoo intraday arrays do not align with source timestamps.")
    retrieved = datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00"))
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise source_error("Yahoo capture time must be timezone-aware.")
    sessions = regular_sessions(result["meta"])
    previous: int | None = None
    previous_selected: int | None = None
    previous_bar_end: datetime | None = None
    rows = []
    counts = {
        "source_rows": len(times),
        "excluded_outside_date_rows": 0,
        "excluded_unelapsed_interval_rows": 0,
        "excluded_outside_regular_session_rows": 0,
        "timestamp_gaps": 0,
        "shortened_session_intervals": 0,
        "session_unverified_records": 0,
    }
    for index, timestamp in enumerate(times):
        observed = unix_time(timestamp)
        if previous is not None and timestamp <= previous:
            raise source_error("Yahoo intraday timestamps are duplicated or unordered.")
        previous = timestamp
        local = observed.astimezone(timezone)
        day = local.date().isoformat()
        values = {
            field: whole(columns[field][index])
            if field == "volume"
            else numeric(columns[field][index], positive=profile["asset_class"] != "futures")
            for field in FIELDS
        }
        consistent = None
        if all(values[field] is not None for field in FIELDS[:4]):
            opening, high, low, close = [Decimal(cast(str, values[field])) for field in FIELDS[:4]]
            consistent = low <= min(opening, close) <= max(opening, close) <= high
        if not start <= day <= end:
            counts["excluded_outside_date_rows"] += 1
            continue
        session = next((item for item in sessions if item[0] <= observed < item[1]), None)
        covered = any(
            first.astimezone(timezone).date()
            <= local.date()
            <= (last - timedelta(microseconds=1)).astimezone(timezone).date()
            for first, last, _ in sessions
        )
        if covered and session is None:
            counts["excluded_outside_regular_session_rows"] += 1
            continue
        nominal_end = observed + timedelta(seconds=seconds)
        bar_end = min(nominal_end, session[1]) if session else nominal_end
        if bar_end > retrieved:
            counts["excluded_unelapsed_interval_rows"] += 1
            continue
        if previous_bar_end is not None and observed < previous_bar_end:
            raise source_error("Yahoo intraday elapsed intervals overlap.")
        previous_bar_end = bar_end
        duration = int((bar_end - observed).total_seconds())
        flags = ["ohlc_bounds_conflict"] if consistent is False else []
        if duration < seconds:
            flags.append("shortened_session_interval")
            counts["shortened_session_intervals"] += 1
        if session is None:
            counts["session_unverified_records"] += 1
        gap = timestamp - previous_selected if previous_selected is not None else None
        gap = gap if gap is not None and gap > seconds else None
        counts["timestamp_gaps"] += gap is not None
        previous_selected = timestamp
        field_sources = {
            field: reference(snapshot, f"{PREFIX}/indicators/quote/0/{field}/{index}")
            for field in FIELDS
        }
        field_sources["observation_at"] = reference(snapshot, f"{PREFIX}/timestamp/{index}")
        if session:
            field_sources["session"] = reference(snapshot, session[2])
        rows.append(
            {
                **values,
                "identifier": symbol,
                "date": day,
                "observation_at": observed.isoformat().replace("+00:00", "Z"),
                "observation_local": local.isoformat(),
                "date_basis": "source_timestamp_exchange_local_date",
                "interval": interval,
                "source_interval": result["meta"]["dataGranularity"],
                "frequency": "intraday",
                "currency": profile["currency"],
                "unit": profile["price_unit"],
                "asset_class": profile["asset_class"],
                "volume_unit": profile["volume_unit"],
                "price_basis": "yahoo_reported_intraday",
                "price_adjustment_verified": False,
                "status": "unavailable"
                if any(value is None for value in values.values())
                else "ambiguous"
                if consistent is False
                else "reported",
                "ohlc_consistent": consistent,
                "quality_flags": flags,
                "bar_status": "elapsed_interval",
                "bar_end_at": bar_end.isoformat().replace("+00:00", "Z"),
                "bar_duration_seconds": duration,
                "bar_finality_verified": False,
                "session_verified": session is not None,
                "timestamp_gap_seconds": gap,
                "published_at": None,
                "source_kind": "secondary_aggregator",
                "field_sources": field_sources,
                "source": field_sources["close"],
            }
        )
    return rows, counts


def yahoo_intraday(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.identifier is not None and q.start_date and q.end_date and q.interval
    validate_intraday_window(
        q.identifier,
        q.start_date,
        q.end_date,
        q.interval,
        now=None if transport.synthetic else datetime.now(UTC),
    )
    snapshot = transport.get(intraday_url(q.identifier, q.start_date, q.end_date, q.interval), 900)
    result, instrument = intraday_result(snapshot, q.identifier, q.interval)
    rows, counts = intraday_rows(
        result,
        snapshot,
        symbol=q.identifier,
        interval=q.interval,
        start=q.start_date,
        end=q.end_date,
    )
    if not rows:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "No elapsed intraday price intervals in the window."
        )
    rows.reverse()
    missing = sum(row["status"] == "unavailable" for row in rows)
    ambiguous = sum(row["status"] == "ambiguous" for row in rows)
    return {
        "instrument": instrument,
        "records": rows[: q.limit],
        "sources": [reference(snapshot, "")],
        "coverage": {
            **counts,
            "requested_start": q.start_date,
            "requested_end": q.end_date,
            "observed_start": rows[-1]["observation_at"],
            "observed_end": rows[0]["observation_at"],
            "date_selection": "inclusive_exchange_local_date",
            "observed_records": len(rows),
            "returned_records": min(q.limit, len(rows)),
            "truncated": len(rows) > q.limit,
            "unavailable_records": missing,
            "inconsistent_ohlc_records": ambiguous,
            "partial": bool(missing or ambiguous or counts["excluded_unelapsed_interval_rows"]),
            "interval": q.interval,
            "upstream_window_limited": True,
            "upstream_window": "Yahoo recent intraday observations with regular-session request",
            "exchange_calendar_verified": False,
            "point_in_time": False,
        },
        "limitations": [
            "Optional unofficial international Yahoo Finance access for personal use only; "
            "no redistribution or commercial-data license, Yahoo! JAPAN access "
            "or live-feed guarantee.",
            "OHLC is retained as Yahoo reported intraday data. Split/dividend adjustments and "
            "unadjusted trade-price identity are not verified; no adjusted close or risk return "
            "is manufactured from these bars.",
            "Only intervals elapsed by the source capture time are returned. A source regular "
            "session can shorten its last interval. Elapsed time does not prove exchange finality, "
            "publication time or real-time availability; the source cache lasts 15 minutes.",
            "The request excludes pre/post trading. Source-provided regular-session ranges "
            "filter terminal quote markers when available; otherwise session identity remains "
            "unverified. An exchange calendar, complete session coverage and holidays "
            "are not inferred.",
            "Timestamp gaps can include overnight closures and scheduled breaks; they are "
            "reported without inserting prices or estimating missing bar counts. Nulls and OHLC "
            "conflicts remain explicit. Duplicate/unordered timestamps and overlapping elapsed "
            "intervals fail closed.",
            "1m reads are limited to the latest 7 local calendar days; 5m/15m/30m/60m reads "
            "to the latest 60 days with a 31-day window. These conservative client bounds do "
            "not guarantee upstream retention. Current revised responses are not publisher PIT.",
        ],
    }
