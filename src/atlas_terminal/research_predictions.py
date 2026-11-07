"""Source-addressable event prices, with explicit lifecycle and probability semantics."""

import re
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING, Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.prediction_sources import event_slug, event_url, search_term, search_url
from atlas_terminal.providers import Snapshot, Transport, decode_json

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_MARKETS = 500
MAX_RETURNED_MARKETS = 100
MAX_OUTCOMES = 20


def source_error(message: str) -> AtlasError:
    return AtlasError("SOURCE_UNAVAILABLE", message, status=502)


def evidence(snapshot: Snapshot, pointer: str, **extra: Any) -> dict[str, Any]:
    return {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "json_pointer": pointer,
        **extra,
    }


def identity(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise source_error("Prediction event/market identity is missing or malformed.")
    return value


def reported_slug(value: Any) -> str:
    try:
        return event_slug(value)
    except AtlasError:
        raise source_error("Prediction source has an invalid event/market slug.") from None


def text(value: Any, bound: int = 20000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > bound:
        raise source_error("Prediction text exceeds its supported contract.")
    return value


def flag(value: Any) -> bool | None:
    if value is not None and type(value) is not bool:
        raise source_error("Prediction lifecycle flags must be booleans or null.")
    return value


def timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("Invalid timestamp")
        if len(value) == 10:
            date.fromisoformat(value)
        elif datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Missing timezone")
        return value
    except ValueError:
        raise source_error("Prediction source has an invalid date/timestamp.") from None


def numeric(value: Any, *, price: bool = False) -> str | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, Decimal, int))
        or len(str(value)) > 100
    ):
        raise source_error("Prediction numeric field is not an exact bounded decimal.")
    try:
        number = Decimal(value)
        parts = number.as_tuple()
        if (
            not number.is_finite()
            or not isinstance(parts.exponent, int)
            or not -40 <= parts.exponent <= 40
            or len(parts.digits) > 60
            or number < 0
            or (price and number > 1)
        ):
            raise ValueError("Invalid probability/amount")
        return format(number, "f")
    except (InvalidOperation, ValueError):
        raise source_error("Prediction price/amount is outside the supported range.") from None


def array(value: Any, *, optional: bool = False) -> list[Any] | None:
    if value is None and optional:
        return None
    if isinstance(value, str):
        if len(value) > 10000:
            raise source_error("Encoded prediction array exceeds its bound.")
        try:
            value = decode_json(value.encode())
        except ValueError:
            raise source_error("Prediction array is not valid embedded JSON.") from None
    if not isinstance(value, list) or len(value) > MAX_OUTCOMES:
        raise source_error("Prediction arrays must be bounded lists or JSON-encoded lists.")
    return value


def item_source(snapshot: Snapshot, pointer: str, raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, str):
        return evidence(snapshot, pointer, embedded_json_pointer=f"/{index}")
    return evidence(snapshot, pointer + f"/{index}")


def lifecycle(event: dict, market: dict) -> str:
    if event["closed"] is True or market["closed"] is True:
        return "closed"
    if event["archived"] is True or market["archived"] is True:
        return "archived"
    if event["active"] is False or market["active"] is False or market["accepting_orders"] is False:
        return "inactive"
    if (
        event["active"] is True
        and event["closed"] is False
        and market["active"] is True
        and market["closed"] is False
        and market["accepting_orders"] is True
    ):
        return "open"
    return "unknown"


def market_record(snapshot: Snapshot, raw: dict, index: int, event: dict) -> dict[str, Any]:
    pointer = f"/markets/{index}"
    names = array(raw.get("outcomes"))
    assert names is not None
    if (
        not 2 <= len(names) <= MAX_OUTCOMES
        or any(not isinstance(name, str) or not name or len(name) > 200 for name in names)
        or len(set(names)) != len(names)
    ):
        raise source_error("Prediction outcomes lack distinct, bounded labels.")
    prices = array(raw.get("outcomePrices"), optional=True)
    tokens = array(raw.get("clobTokenIds"), optional=True)
    if (prices and len(prices) != len(names)) or (tokens and len(tokens) != len(names)):
        raise source_error("Prediction outcome labels, prices and token IDs do not align.")
    if tokens and (
        any(
            not isinstance(token, str) or not re.fullmatch(r"[0-9]{1,100}", token)
            for token in tokens
        )
        or len(set(tokens)) != len(tokens)
    ):
        raise source_error("Prediction token identities are malformed or duplicated.")
    flags = {
        key: flag(raw.get(source))
        for key, source in (
            ("active", "active"),
            ("closed", "closed"),
            ("archived", "archived"),
            ("accepting_orders", "acceptingOrders"),
        )
    }
    state = lifecycle(event, flags)
    outcomes = []
    with localcontext(Context(prec=100)):
        for i, name in enumerate(names):
            price = numeric(prices[i], price=True) if prices else None
            probability = price if state == "open" else None
            outcomes.append(
                {
                    "outcome": name,
                    "reported_price": price,
                    "price_unit": "unit_payout_ratio",
                    "market_implied_probability": probability,
                    "probability_percent": format(Decimal(probability) * 100, "f")
                    if probability is not None
                    else None,
                    "probability_percent_formula": "100 * reported_price",
                    "market_implied_probability_formula": "reported_price; market interpretation",
                    "derived": probability is not None,
                    "unavailable_reason": "reported_price_missing"
                    if price is None
                    else None
                    if state == "open"
                    else "market_not_open_or_status_unknown",
                    "token_id": tokens[i] if tokens else None,
                    "field_sources": {
                        "outcome": item_source(
                            snapshot, pointer + "/outcomes", raw.get("outcomes"), i
                        ),
                        "reported_price": item_source(
                            snapshot, pointer + "/outcomePrices", raw.get("outcomePrices"), i
                        )
                        if prices
                        else evidence(snapshot, pointer + "/outcomePrices")
                        if "outcomePrices" in raw
                        else None,
                        "token_id": item_source(
                            snapshot, pointer + "/clobTokenIds", raw.get("clobTokenIds"), i
                        )
                        if tokens
                        else None,
                    },
                }
            )
        price_sum = (
            sum((Decimal(row["reported_price"]) for row in outcomes), Decimal(0))
            if all(row["reported_price"] is not None for row in outcomes)
            else None
        )
    return {
        "market_id": identity(raw.get("id")),
        "market_slug": reported_slug(raw.get("slug")),
        "question": text(raw.get("question"), 2000),
        "group_label": text(raw.get("groupItemTitle"), 2000),
        "description": text(raw.get("description")),
        "resolution_source": text(raw.get("resolutionSource"), 4000),
        **flags,
        "lifecycle": state,
        "end_date": timestamp(raw.get("endDate")),
        "metadata_updated_at": timestamp(raw.get("updatedAt")),
        "price_observed_at": None,
        "outcomes": outcomes,
        "reported_price_sum": format(price_sum, "f") if price_sum is not None else None,
        "reported_price_sum_formula": "sum(reported_price[i]); no normalization",
        "derived": price_sum is not None,
        "price_sum_is_one": price_sum == 1 if price_sum is not None else None,
        "volume": numeric(raw.get("volume")),
        "liquidity": numeric(raw.get("liquidity")),
        "amount_unit": "publisher_reported_not_currency_normalized",
        "field_sources": {
            key: evidence(snapshot, pointer + "/" + source) if source in raw else None
            for key, source in (
                ("question", "question"),
                ("description", "description"),
                ("volume", "volume"),
                ("liquidity", "liquidity"),
                ("end_date", "endDate"),
                ("metadata_updated_at", "updatedAt"),
                ("active", "active"),
                ("closed", "closed"),
                ("archived", "archived"),
                ("accepting_orders", "acceptingOrders"),
            )
        },
        "source": evidence(snapshot, pointer),
    }


def parse_event(
    snapshot: Snapshot, slug: str, expected_id: str | None, include_closed: bool
) -> dict[str, Any]:
    raw = snapshot.data
    if (
        not isinstance(raw, dict)
        or raw.get("slug") != slug
        or "error" in raw
        or (expected_id is not None and raw.get("id") != expected_id)
    ):
        raise source_error("Prediction event detail differs from its selected identity.")
    event = {
        "event_id": identity(raw.get("id")),
        "event_slug": reported_slug(raw.get("slug")),
        "title": text(raw.get("title"), 2000),
        "description": text(raw.get("description")),
        **{key: flag(raw.get(key)) for key in ("active", "closed", "archived")},
        "event_url": "https://polymarket.com/event/" + slug,
        "end_date": timestamp(raw.get("endDate")),
        "metadata_updated_at": timestamp(raw.get("updatedAt")),
        "price_observed_at": None,
        "snapshot_retrieved_at": snapshot.retrieved_at,
        "source": evidence(snapshot, ""),
    }
    raw_markets = raw.get("markets")
    if (
        not isinstance(raw_markets, list)
        or len(raw_markets) > MAX_MARKETS
        or any(not isinstance(m, dict) for m in raw_markets)
    ):
        raise source_error("Prediction event has no supported bounded market list.")
    markets, filtered = [], 0
    for index, market in enumerate(raw_markets):
        closed = event["closed"] is True or flag(market.get("closed")) is True
        archived = event["archived"] is True or flag(market.get("archived")) is True
        if not include_closed and (closed or archived):
            filtered += 1
            continue
        markets.append(market_record(snapshot, market, index, event))
    if len({m["market_id"] for m in markets}) != len(markets):
        raise source_error("Prediction event has duplicate market IDs.")
    return event | {
        "markets": markets[:MAX_RETURNED_MARKETS],
        "filtered_closed_markets": filtered,
        "available_markets": len(markets),
        "markets_truncated": len(markets) > MAX_RETURNED_MARKETS,
    }


def prediction_markets(transport: Transport, q: "ResearchQuery") -> dict[str, Any]:
    limit = q.limit if "limit" in q.model_fields_set else 3
    sources: list[dict[str, Any]] = []
    selections: list[tuple[str, str | None]] = []
    has_more, total = None, None
    actual_query = search_term(q.search) if q.search is not None else None
    if q.event_slug:
        selections = [(q.event_slug, None)]
    else:
        snapshot = transport.get(search_url(q.search or "", limit, q.include_closed), 60)
        if not transport.synthetic and snapshot.url != search_url(
            q.search or "", limit, q.include_closed
        ):
            raise source_error("Prediction search response URL differs from its request.")
        raw = snapshot.data
        if not isinstance(raw, dict) or "events" not in raw or "error" in raw:
            raise source_error("Prediction search response differs from the supported contract.")
        events = raw["events"] if raw["events"] is not None else []
        if not isinstance(events, list) or len(events) > 20:
            raise source_error("Prediction search has an oversized or malformed event list.")
        for event in events:
            if not isinstance(event, dict):
                raise source_error("Prediction search event is malformed.")
            selections.append((reported_slug(event.get("slug")), identity(event.get("id"))))
        if len({slug for slug, _ in selections}) != len(selections) or len(
            {key for _, key in selections}
        ) != len(selections):
            raise source_error("Prediction search has duplicate event identities.")
        pagination = raw.get("pagination") or {}
        if not isinstance(pagination, dict):
            raise source_error("Prediction search pagination is malformed.")
        has_more = flag(pagination.get("hasMore"))
        total = pagination.get("totalResults")
        if total is not None and (type(total) is not int or total < 0):
            raise source_error("Prediction search total is malformed.")
        sources.append(evidence(snapshot, "/events"))
    records = []
    for slug, event_id in selections[:limit]:
        snapshot = transport.get(event_url(slug), 60)
        if not transport.synthetic and snapshot.url != event_url(slug):
            raise source_error("Prediction response URL differs from the selected event.")
        event = parse_event(snapshot, slug, event_id, q.include_closed)
        sources.append(evidence(snapshot, ""))
        if q.include_closed or (event["closed"] is not True and event["archived"] is not True):
            records.append(event)
    outcomes = [
        outcome
        for event in records
        for market in event["markets"]
        for outcome in market["outcomes"]
    ]
    return {
        "records": records,
        "sources": sources,
        "coverage": {
            "returned_records": len(records),
            "selected_events": min(len(selections), limit),
            "search_query_sent": actual_query,
            "search_total_results": total,
            "upstream_search_has_more": has_more,
            "truncated": (
                has_more is True
                or len(selections) > limit
                or any(event["markets_truncated"] for event in records)
            ),
            "upstream_window_limited": True,
            "point_in_time": False,
            "markets": sum(len(event["markets"]) for event in records),
            "outcomes": len(outcomes),
            "missing_prices": sum(row["reported_price"] is None for row in outcomes),
            "unavailable_probabilities": sum(
                row["market_implied_probability"] is None for row in outcomes
            ),
            "partial": (
                any(row["reported_price"] is None for row in outcomes)
                or any(
                    market["lifecycle"] == "unknown"
                    for event in records
                    for market in event["markets"]
                )
            ),
            "price_observation_timestamp_verified": False,
        },
        "limitations": [
            "Gamma-reported prices are market-implied indicators, not calibrated "
            "forecasts, guaranteed outcomes or executable quotes.",
            "No complement, cross-market sum or probability normalization is inferred. "
            "Event markets can overlap or have cumulative deadlines.",
            "Closed/archived markets are excluded by default. Retained inactive prices "
            "are references with null current probabilities; closed does not prove "
            "resolved.",
            "Metadata update time, contract end date and source retrieval time are "
            "distinct; the actual price observation time remains unknown.",
            "Search covers one bounded publisher-ranked page. Event details are fetched "
            "separately; no historical/PIT, CLOB depth, wallet or trading integration is "
            "supplied.",
            "Polymarket terms apply, including restrictions for Capital Market Clients "
            "and redistribution. API access does not grant a data license.",
        ],
    }
