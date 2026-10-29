"""Exact Yahoo daily observations and action locators, with explicit price bases."""

from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any, cast

from atlas_terminal.economic_dates import window_metadata, within
from atlas_terminal.equity_sources import yahoo_profile, yahoo_timezone, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery

PREFIX = "/chart/result/0"
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "adjusted_close")


def unix_time(value: Any) -> datetime:
    if type(value) is not int:
        raise source_error("Yahoo observation timestamp must be an integer Unix second.")
    try:
        result = datetime.fromtimestamp(value, UTC)
        if not 1900 <= result.year <= 2100:
            raise ValueError("Timestamp outside bounds")
        return result
    except (ValueError, OSError, OverflowError):
        raise source_error("Yahoo observation timestamp is outside supported bounds.") from None


def whole(value: Any, *, positive: bool = False) -> str | None:
    text = numeric(value, positive=positive)
    if text is None:
        return None
    number = Decimal(text)
    if number < 0 or number != number.to_integral_value():
        raise source_error("Yahoo volume must be a nonnegative whole number.")
    return str(int(number))


def chart_result(snapshot: Snapshot, symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
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
        raise source_error("Yahoo did not return one successful chart result.")
    result = results[0]
    meta = result.get("meta")
    if (
        not isinstance(meta, dict)
        or meta.get("symbol") not in ({symbol, "CAD=X"} if symbol == "USDCAD=X" else {symbol})
        or meta.get("currency") != profile["currency"]
        or meta.get("exchangeTimezoneName") != profile["timezone"]
        or meta.get("exchangeName") not in profile["exchanges"]
        or meta.get("instrumentType") not in profile["types"]
        or meta.get("dataGranularity") != "1d"
    ):
        raise source_error(
            "Yahoo symbol, currency, exchange, instrument type or daily frequency differs."
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
        "metadata_source": reference(snapshot, PREFIX + "/meta"),
    }
    for field in ("shortName", "longName", "fullExchangeName"):
        value = meta.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 500):
            raise source_error("Yahoo instrument name is malformed or exceeds bounds.")
        instrument[field] = value
    return result, instrument


def price_rows(
    result: dict[str, Any], snapshot: Snapshot, q: ResearchQuery
) -> tuple[list[dict[str, Any]], int]:
    profile = yahoo_profile(q.identifier or "")
    timezone = yahoo_timezone(q.identifier or "")
    security = profile["asset_class"] == "security"
    times = result.get("timestamp")
    if times is None:
        # Yahoo can omit timestamps for a valid window containing no sessions.
        times = []
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    if (
        not isinstance(times, list)
        or len(times) > 4000
        or not isinstance(quotes, list)
        or len(quotes) != 1
        or not isinstance(quotes[0], dict)
    ):
        raise source_error("Yahoo daily timestamp/quote arrays are malformed or exceed bounds.")
    assert isinstance(indicators, dict)
    quote = quotes[0]
    columns: dict[str, Any] = {
        field: [] if not times and quote.get(field) is None else quote.get(field)
        for field in PRICE_FIELDS[:-1]
    }
    adjusted = indicators.get("adjclose")
    if adjusted is None:
        columns["adjusted_close"] = [None] * len(times)
    elif isinstance(adjusted, list) and len(adjusted) == 1 and isinstance(adjusted[0], dict):
        columns["adjusted_close"] = adjusted[0].get("adjclose")
    else:
        raise source_error("Yahoo adjusted-close array is malformed.")
    if any(
        not isinstance(values, list) or len(values) != len(times) for values in columns.values()
    ):
        raise source_error("Yahoo daily indicator arrays do not align with source timestamps.")
    aligned = cast(dict[str, list[Any]], columns)
    retrieved = datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00"))
    today = retrieved.astimezone(timezone).date()
    trading = result["meta"].get("currentTradingPeriod", {})
    regular = trading.get("regular", {}) if isinstance(trading, dict) else None
    if not isinstance(regular, dict):
        raise source_error("Yahoo regular-session metadata is malformed.")
    end = unix_time(regular["end"]) if "end" in regular else None
    previous: int | None = None
    seen_dates: set[str] = set()
    rows, excluded = [], 0
    for index, timestamp in enumerate(times):
        observed = unix_time(timestamp)
        day = observed.astimezone(timezone).date()
        date = day.isoformat()
        if (previous is not None and timestamp <= previous) or date in seen_dates:
            raise source_error(
                "Yahoo timestamps are duplicated, unordered or share a session date."
            )
        previous = timestamp
        seen_dates.add(date)
        values = {
            field: whole(aligned[field][index])
            if field == "volume"
            else numeric(aligned[field][index], positive=profile["asset_class"] != "futures")
            for field in PRICE_FIELDS
        }
        ohlc_consistent = None
        if all(values[field] is not None for field in ("open", "high", "low", "close")):
            opening, high, low, close = [
                Decimal(cast(str, values[field])) for field in PRICE_FIELDS[:4]
            ]
            ohlc_consistent = low <= min(opening, close) <= max(opening, close) <= high
            if not ohlc_consistent and profile["asset_class"] != "fx":
                raise source_error("Yahoo OHLC values violate the reported high/low bounds.")
        if not within(date, q.start_date, q.end_date):
            continue
        # A daily chart can contain an intraday last bar. Its timestamp is not a close.
        if day >= today and not (
            day == today
            and profile["asset_class"] in {"security", "index"}
            and end is not None
            and end.astimezone(timezone).date() == day
            and retrieved >= end
        ):
            excluded += 1
            continue
        field_sources = {
            field: reference(
                snapshot,
                f"{PREFIX}/indicators/adjclose/0/adjclose/{index}"
                if field == "adjusted_close"
                else f"{PREFIX}/indicators/quote/0/{field}/{index}",
            )
            for field in PRICE_FIELDS
            if field != "adjusted_close" or adjusted is not None
        }
        field_sources["date"] = reference(snapshot, f"{PREFIX}/timestamp/{index}")
        unavailable = any(value is None for value in values.values())
        rows.append(
            {
                **values,
                "identifier": q.identifier,
                "date": date,
                "observation_at": observed.isoformat().replace("+00:00", "Z"),
                "date_basis": "exchange_local_session_date",
                "frequency": "daily",
                "currency": profile["currency"],
                "unit": profile["price_unit"],
                "asset_class": profile["asset_class"],
                "volume_unit": profile["volume_unit"],
                "price_basis": "yahoo_split_adjusted" if security else "yahoo_reported_daily",
                "adjusted_close_basis": "yahoo_split_and_dividend_adjusted"
                if security
                else "yahoo_reported_adjusted_close",
                "adjusted_close_available": values["adjusted_close"] is not None,
                "status": "unavailable"
                if unavailable
                else "ambiguous"
                if ohlc_consistent is False
                else "reported",
                "ohlc_consistent": ohlc_consistent,
                "quality_flags": ["ohlc_bounds_conflict"] if ohlc_consistent is False else [],
                "session_status": "completed",
                "published_at": None,
                "source_kind": "secondary_aggregator",
                "field_sources": field_sources,
                "source": field_sources["close"],
            }
        )
    return rows, excluded


def action_rows(
    result: dict[str, Any], snapshot: Snapshot, q: ResearchQuery
) -> list[dict[str, Any]]:
    profile = yahoo_profile(q.identifier or "")
    timezone = yahoo_timezone(q.identifier or "")
    events = result.get("events", {})
    if not isinstance(events, dict):
        raise source_error("Yahoo event map is malformed.")
    if profile["asset_class"] != "security":
        if any(events.get(kind) for kind in ("dividends", "splits")):
            raise source_error("Security corporate actions are unsupported for this instrument.")
        return []
    rows = []
    retrieved_day = (
        datetime.fromisoformat(snapshot.retrieved_at.replace("Z", "+00:00"))
        .astimezone(timezone)
        .date()
        .isoformat()
    )
    for kind in ("dividends", "splits"):
        cells = events.get(kind, {})
        if not isinstance(cells, dict) or len(cells) > 4000:
            raise source_error("Yahoo event map is malformed or exceeds bounds.")
        seen = set()
        for key, cell in cells.items():
            if not isinstance(cell, dict):
                raise source_error("Yahoo action observation is malformed.")
            timestamp = cell.get("date")
            observed = unix_time(timestamp)
            day = observed.astimezone(timezone).date().isoformat()
            if key != str(timestamp) or day in seen:
                raise source_error("Yahoo action key/date differs or duplicates a session date.")
            seen.add(day)
            value: dict[str, Any]
            if kind == "dividends":
                amount = numeric(cell.get("amount"))
                if amount is None or Decimal(amount) < 0:
                    raise source_error("Yahoo dividend must have a nonnegative reported amount.")
                value = {
                    "value": amount,
                    "unit": profile["price_unit"],
                    "currency_basis": "chart_quote_currency",
                }
            else:
                numerator = numeric(cell.get("numerator"), positive=True)
                denominator = numeric(cell.get("denominator"), positive=True)
                if numerator is None or denominator is None:
                    raise source_error("Yahoo split has no positive numerator and denominator.")
                ratio = cell.get("splitRatio")
                if ratio is not None:
                    parts = ratio.split(":") if isinstance(ratio, str) else []
                    if len(parts) != 2:
                        raise source_error("Yahoo split ratio label is malformed.")
                    left, right = (numeric(part, positive=True) for part in parts)
                    if (
                        left is None
                        or right is None
                        or (
                            Fraction(Decimal(left)) * Fraction(Decimal(denominator))
                            != Fraction(Decimal(right)) * Fraction(Decimal(numerator))
                        )
                    ):
                        raise source_error("Yahoo split label conflicts with its numeric terms.")
                value = {
                    "numerator": numerator,
                    "denominator": denominator,
                    "split_ratio": ratio,
                    "unit": "new_shares_per_old_shares",
                }
            if day > retrieved_day or not within(day, q.start_date, q.end_date):
                continue
            rows.append(
                {
                    **value,
                    "identifier": q.identifier,
                    "date": day,
                    "observation_at": observed.isoformat().replace("+00:00", "Z"),
                    "date_basis": "exchange_local_event_date",
                    "action_type": "dividend" if kind == "dividends" else "split",
                    "currency": profile["currency"] if kind == "dividends" else None,
                    "amount_basis": "yahoo_reported_event",
                    "status": "reported",
                    "published_at": None,
                    "source_kind": "secondary_aggregator",
                    "source": reference(snapshot, f"{PREFIX}/events/{kind}/{key}"),
                }
            )
    return rows


def yahoo_equities(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.identifier is not None
    if q.dataset == "equity_actions" and yahoo_profile(q.identifier)["asset_class"] != "security":
        raise AtlasError("INVALID_INPUT", "Corporate-action queries require a security symbol.")
    snapshot = transport.get(yahoo_url(q.identifier, q.start_date, q.end_date), 900)
    result, instrument = chart_result(snapshot, q.identifier)
    # Validate aligned daily arrays even when selecting only action events.
    prices, excluded = price_rows(result, snapshot, q)
    actions = action_rows(result, snapshot, q)
    rows = prices if q.dataset == "equity_prices" else actions
    rows.sort(key=lambda row: (row["date"], row.get("action_type", "")), reverse=True)
    if not rows and q.dataset == "equity_prices":
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "No completed daily market observations in the window."
        )
    return {
        "instrument": instrument,
        "records": rows[: q.limit],
        "sources": [reference(snapshot, "")],
        "coverage": {
            **window_metadata(rows, q.start_date, q.end_date),
            "date_selection": "inclusive_exchange_local_date",
            "observed_records": len(rows),
            "returned_records": min(q.limit, len(rows)),
            "truncated": len(rows) > q.limit,
            "unavailable_records": sum(row["status"] == "unavailable" for row in rows),
            "inconsistent_ohlc_records": sum(row.get("ohlc_consistent") is False for row in rows),
            "partial": any(row["status"] in {"unavailable", "ambiguous"} for row in rows),
            "upstream_window_limited": True,
            "upstream_window": "Yahoo daily selected observation dates"
            if q.start_date
            else "Yahoo latest month",
            "excluded_unconfirmed_session_rows": excluded,
            "exchange_calendar_verified": False,
            "unsupported_event_types": sorted(
                set(result.get("events", {})) - {"dividends", "splits"}
            ),
            "point_in_time": False,
        },
        "limitations": [
            "Optional unofficial international Yahoo Finance access for personal use; "
            "Yahoo! JAPAN is not accessed. No redistribution or commercial data license "
            "is granted.",
            "Security OHLC is Yahoo split-adjusted, not an unadjusted trade-price series. "
            "Adjusted close additionally reflects dividends. Atlas does not re-adjust, "
            "repair prices or multiply split-adjusted closes by raw historical shares.",
            "Daily timestamps identify the declared source-local dates, not publication times. "
            "An unconfirmed current-session bar is excluded; gaps and nulls are never filled.",
            "Dividend amounts and split terms are retained as reported, without reconstructing "
            "payment dates, original unadjusted dividends or complete corporate-action history.",
            "FX bars with close/open outside the reported high/low retain every original value "
            "with status ambiguous and an OHLC conflict flag; no tolerance or repair is applied.",
            "Reference index/FX/crypto/futures values retain Yahoo conventions. Non-security "
            "adjusted close has no inferred dividend/split adjustment. Futures are changing "
            "front-contract quotations, not a verified continuous total-return series; no "
            "roll adjustment, contract multiplier, settlement status or expiry is inferred.",
            "Current revised prices/actions are not point-in-time vintages or a "
            "delisting-complete universe. No fundamentals, intraday or real-time feed is supplied.",
        ],
    }
