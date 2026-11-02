"""Explicit dated-contract curves; no inferred roll, settlement or expiry calendar."""

from decimal import Context, Decimal, localcontext
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.futures_catalog import future_context, future_identity
from atlas_terminal.providers import Transport
from atlas_terminal.research_equities import yahoo_equities
from atlas_terminal.research_models import ResearchQuery


def check_curve(q: ResearchQuery) -> None:
    identities = [future_identity(symbol) for symbol in q.identifiers]
    if (
        not 2 <= len(identities) <= 10
        or len(set(q.identifiers)) != len(q.identifiers)
        or any(ident is None or ident[1] is None for ident in identities)
        or len({ident[0] for ident in identities if ident is not None}) != 1
    ):
        raise AtlasError(
            "INVALID_INPUT", "Choose 2–10 unique dated contracts of one registered underlying."
        )
    if q.observation_date is None:
        raise AtlasError(
            "INVALID_INPUT", "Choose an explicit observation_date; no previous-date fallback."
        )


def futures_prices(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    result = yahoo_equities(
        transport,
        ResearchQuery(
            dataset="equity_prices",
            identifier=q.identifier,
            start_date=q.start_date,
            end_date=q.end_date,
            limit=q.limit,
        ),
    )
    unit = result["instrument"]["price_unit"]
    result["instrument"].update(future_context(q.identifier or ""))
    for row in result["records"]:
        row.update(
            future_context(q.identifier or ""),
            source_unit_label=unit,
            unit="source_quote_units",
            price_basis="Yahoo_reported_future_quote",
        )
    result["limitations"].append(
        "Contract month is encoded by the selected identifier, not an "
        "exchange-confirmed expiry date. "
        "Front references can change contracts. Quotes are not verified settlements, roll-adjusted "
        "total returns or notional values; reference quote units do not assert a "
        "verified source scale."
    )
    return result


def futures_curve(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    check_curve(q)
    points, sources, instruments = [], [], []
    for symbol in sorted(q.identifiers, key=lambda s: future_context(s)["contract_month"]):
        selected = ResearchQuery(
            dataset="futures_prices",
            identifier=symbol,
            start_date=q.observation_date,
            end_date=q.observation_date,
            limit=500,
        )
        try:
            result = futures_prices(transport, selected)
        except AtlasError as error:
            if error.code != "INCOMPLETE_COVERAGE":
                raise
            raise AtlasError(
                "INCOMPLETE_COVERAGE",
                "No completed quote for a selected contract/date; no fallback.",
            ) from None
        matches = [r for r in result["records"] if r["date"] == q.observation_date]
        if len(matches) != 1 or result["coverage"]["truncated"]:
            raise AtlasError("INCOMPLETE_COVERAGE", "Require one exact-date quote per contract.")
        original = matches[0]
        points.append(
            {
                **future_context(symbol),
                "record_type": "contract_quote",
                "identifier": symbol,
                "date": q.observation_date,
                "value": original["close"],
                "unit": "source_quote_units",
                "currency": original["currency"],
                "denomination_currency": original["denomination_currency"],
                "quote_unit_scale": original["quote_unit_scale"],
                "price_type": "reported_close_not_verified_settlement",
                "status": "ambiguous"
                if original["status"] == "ambiguous"
                else "reported"
                if original["close"] is not None
                else "unavailable",
                "quality_flags": original["quality_flags"],
                "ohlc_consistent": original["ohlc_consistent"],
                "source": original["field_sources"]["close"],
            }
        )
        sources.extend(result["sources"])
        instruments.append(result["instrument"])
    if len({(r["currency"], r["quote_unit_scale"]) for r in points}) != 1:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Contract quote denominations differ; no conversion."
        )
    rows = list(points)
    for near, far in zip(points, points[1:], strict=False):
        available = all(r["value"] is not None and r["status"] == "reported" for r in (near, far))
        with localcontext(Context(prec=200)):
            spread = (
                format(Decimal(far["value"]) - Decimal(near["value"]), "f") if available else None
            )
        rows.append(
            {
                "record_type": "calendar_spread",
                "date": q.observation_date,
                "near_identifier": near["identifier"],
                "far_identifier": far["identifier"],
                "value": spread,
                "unit": "source_quote_units",
                "currency": near["currency"],
                "denomination_currency": near["denomination_currency"],
                "quote_unit_scale": near["quote_unit_scale"],
                "status": "derived" if available else "unavailable",
                "derived": True,
                "formula": "far_close - near_close",
                "inputs": [near, far],
                "expiry_date": None,
                "settlement_price_verified": False,
            }
        )
    partial = any(r["value"] is None or r["status"] == "ambiguous" for r in rows)
    if partial and not q.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Missing or ambiguous contract quotes; use allow_partial to inspect.",
        )
    return {
        "records": rows[: q.limit],
        "instruments": instruments,
        "sources": sources,
        "coverage": {
            "matching_records": len(rows),
            "returned_records": min(q.limit, len(rows)),
            "partial": partial,
            "truncated": len(rows) > q.limit,
            "point_in_time": False,
        },
        "limitations": [
            "One explicit observation date and one underlying; no older-date "
            "fallback or interpolation.",
            "Reported daily closes are not verified exchange settlements. Expiry "
            "day, contract multipliers and notional exposure are not inferred.",
            "Calendar spreads are exact quote differences, not carry, convenience "
            "yield, arbitrage value or investment return.",
        ],
    }
