"""Convenient latest/history spread queries backed by the existing exact calculator."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from atlas_terminal.providers import Transport
from atlas_terminal.research_fixed_income import curve_analytics
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.spread_catalog import SPREADS


def underlying_query(q: ResearchQuery) -> ResearchQuery:
    spec = SPREADS[q.series or ""]
    args: dict[str, Any] = {
        "dataset": "macro_analytics",
        "series": spec["legacy_series"],
        "limit": q.limit,
    }
    args.update(
        {
            key: getattr(q, key)
            for key in ("year", "start_date", "end_date")
            if getattr(q, key) is not None
        }
    )
    if not any(key in args for key in ("year", "start_date")):
        today = datetime.now(ZoneInfo(spec["timezone"])).date()
        args.update(start_date=f"{today.year - 1}-01-01", end_date=today.isoformat())
    return ResearchQuery.model_validate(args)


def interest_spreads(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    spec = SPREADS[q.series or ""]
    internal = underlying_query(q)
    result = curve_analytics(transport, internal)
    for row in result["records"]:
        row.update(
            series=q.series,
            underlying_series=spec["legacy_series"],
            spread_type="yield_curve",
            curve=spec["curve"],
            long_maturity=spec["long_maturity"],
            short_maturity=spec["short_maturity"],
            direction="long_minus_short",
            negative_means="inverted_yield_curve",
            percentage_points=None if row["value"] is None else _percentage_points(row["value"]),
        )
    result.update(
        spread_definition=spec,
        selection={
            "order": "newest_observation_first",
            "limit": q.limit,
            "effective_start_date": internal.start_date,
            "effective_end_date": internal.end_date,
            "year": internal.year,
            "missing_latest_policy": "retain_unavailable_no_older_value_fallback",
        },
    )
    result["limitations"].append(
        "Default returns one latest source observation within the current and prior "
        "source-local calendar years. A null latest input stays unavailable; no older "
        "value or other date is substituted. This is not a real-time quote."
    )
    return result


def _percentage_points(value: str) -> str:
    from decimal import Decimal, localcontext

    with localcontext() as context:
        context.prec = max(200, len(value) + 8)
        return format(Decimal(value) / 100, "f")
