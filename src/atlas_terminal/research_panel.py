"""Explicit calendar aggregation with original inputs, gaps and independent read failures."""

import calendar
from collections.abc import Callable
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Any

from atlas_terminal.economic_catalog import JAPAN_SERIES
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import CURVE_SERIES
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.util import canonical

SUPPORTED = {
    "economics",
    "national_accounts",
    "japan_economics",
    "fx_rates",
    "reference_rates",
    "macro_analytics",
    "international_economics",
}
MAX_PANEL_BYTES = 4 * 1024 * 1024
CONTEXT = (
    "unit",
    "unit_multiplier",
    "seasonally_adjusted",
    "seasonal_adjustment",
    "base_period",
    "annualized",
    "metric_name",
    "base_currency",
    "quote_currency",
)


def period_bounds(observed: str, frequency: str) -> tuple[str, str]:
    point = date.fromisoformat(observed)
    width = {"monthly": 1, "quarterly": 3, "annual": 12}[frequency]
    first = ((point.month - 1) // width) * width + 1
    last = first + width - 1
    return (
        date(point.year, first, 1).isoformat(),
        date(point.year, last, calendar.monthrange(point.year, last)[1]).isoformat(),
    )


def periods(start: str, end: str, frequency: str) -> list[tuple[str, str]]:
    result = []
    current = start
    while current <= end:
        low, high = period_bounds(current, frequency)
        result.append((low, high))
        point = date.fromisoformat(high)
        current = date.fromordinal(point.toordinal() + 1).isoformat()
    return result


def panel_queries(q: ResearchQuery) -> list[list[dict[str, Any]]]:
    if not q.start_date or not q.end_date or q.start_date > q.end_date or not q.items:
        raise AtlasError("INVALID_INPUT", "A panel needs 1–10 items and ordered start/end dates.")
    if int(q.end_date[:4]) - int(q.start_date[:4]) > 29 or len(canonical(q)) > 16384:
        raise AtlasError(
            "INVALID_INPUT", "Panels span at most 30 calendar years and 16 KiB of query data."
        )
    if (
        period_bounds(q.start_date, q.frequency)[0] != q.start_date
        or period_bounds(q.end_date, q.frequency)[1] != q.end_date
    ):
        raise AtlasError(
            "INVALID_INPUT", "Panel dates must bound complete calendar months, quarters or years."
        )
    if len({item.id for item in q.items}) != len(q.items):
        raise AtlasError("INVALID_INPUT", "Panel item IDs must be unique.")
    plan = []
    for item in q.items:
        data = dict(item.query)
        dataset = data.get("dataset")
        if dataset not in SUPPORTED or set(data) & {"response", "limit", "start_date", "end_date"}:
            raise AtlasError(
                "INVALID_INPUT",
                "Use one macro/FX query per item; the panel supplies dates and presentation.",
            )
        japan = JAPAN_SERIES.get(str(data.get("series")))
        daily = (
            dataset == "fx_rates"
            or (dataset == "macro_analytics" and data.get("series") in CURVE_SERIES)
            or (
                dataset == "japan_economics" and japan is not None and japan["frequency"] == "DAILY"
            )
        )
        spans = [(q.start_date, q.end_date)]
        if daily:
            spans = [
                (max(q.start_date, f"{year}-01-01"), min(q.end_date, f"{year}-12-31"))
                for year in range(int(q.start_date[:4]), int(q.end_date[:4]) + 1)
            ]
        reads = []
        for start, end in spans:
            read = {**data, "limit": 500}
            if dataset != "reference_rates":
                read.update(start_date=start, end_date=end)
            # Validate every item before the first source read. Recursion is excluded above.
            parse_research(read)
            reads.append(read)
        plan.append(reads)
    if sum(len(reads) for reads in plan) > 64:
        raise AtlasError(
            "INVALID_INPUT",
            "A panel allows at most 64 dated research reads; reduce its range or items.",
        )
    return plan


def aggregate(
    rows: list[dict[str, Any]],
    item_id: str,
    method: str,
    start: str,
    end: str,
    frequency: str,
    context: dict[str, Any],
    input_frequency: str | None,
) -> dict[str, Any]:
    rows.sort(key=lambda row: row["date"])
    expected = []
    if input_frequency in {"monthly", "quarterly", "annual"}:
        expected = [high for _, high in periods(start, end, input_frequency)]
    available = {row["date"] for row in rows if row.get("value") is not None}
    missing = sorted(set(expected) - available)
    # A null daily observation is not silently dropped from a mean or last-value selection.
    valid = bool(rows) and not missing and all(row.get("value") is not None for row in rows)
    value = None
    if valid:
        if method == "last":
            value = rows[-1]["value"]
        else:
            with localcontext(Context(prec=200)):
                total = sum((Decimal(row["value"]) for row in rows), Decimal(0))
            with localcontext(Context(prec=28)):
                value = format(total / Decimal(len(rows)), "f")
    return {
        "id": item_id,
        **context,
        "period_start": start,
        "period_end": end,
        "date": rows[-1]["date"] if valid else None,
        "frequency": frequency,
        "input_frequency": input_frequency,
        "aggregation": method,
        "value": value,
        "status": "derived" if valid else "unavailable",
        "derived": True,
        "formula": "last available observation within calendar period"
        if method == "last"
        else "sum(observed values) / observed count",
        "decimal_precision": 28 if method == "mean" else None,
        "rounding": "ROUND_HALF_EVEN" if method == "mean" else None,
        "input_count": len(rows),
        "input_start": rows[0]["date"] if rows else None,
        "input_end": rows[-1]["date"] if rows else None,
        "missing_period_ends": missing,
        "coverage_basis": "calendar_periods"
        if expected
        else "observed_dates_only_no_business_calendar",
        "reason": None if valid else "Missing observations; no filling or partial mean.",
        "inputs": rows,
    }


def macro_panel(
    q: ResearchQuery, get: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    plan = panel_queries(q)
    assert q.start_date and q.end_date
    grid = periods(q.start_date, q.end_date, q.frequency)
    records: list[dict[str, Any]] = [
        {"period_start": low, "period_end": high, "records": []} for low, high in grid
    ]
    columns, sources, errors = [], {}, []
    for item, reads in zip(q.items, plan, strict=True):
        rows: list[dict[str, Any]] = []
        coverages = []
        column: dict[str, Any] = {
            "id": item.id,
            "query": item.query,
            "aggregation": item.aggregation,
        }
        failure = None
        try:
            for read in reads:
                try:
                    result = get(read)
                except AtlasError as exc:
                    if exc.code == "INCOMPLETE_COVERAGE":
                        coverages.append(
                            {"query": read, "partial": True, "error": exc.envelope()["error"]}
                        )
                        continue
                    raise
                if result["coverage"].get("truncated"):
                    raise AtlasError(
                        "INCOMPLETE_COVERAGE", "A panel input was truncated; reduce the date range."
                    )
                column.update(
                    source_id=result["source_id"], source_terms_url=result["source_terms_url"]
                )
                coverages.append(result["coverage"])
                for source in result["sources"]:
                    sources[canonical(source)] = {
                        **source,
                        "source_id": result["source_id"],
                        "source_terms_url": result["source_terms_url"],
                    }
                rows.extend(
                    row
                    for row in result["records"]
                    if row.get("frequency") != "annual_average"
                    and q.start_date <= row.get("date", "") <= q.end_date
                )
            if len({row["date"] for row in rows}) != len(rows):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "A panel input has duplicate observation dates."
                )
            contracts = {canonical({key: row.get(key) for key in CONTEXT}) for row in rows}
            frequencies = {row.get("frequency") for row in rows}
            if len(contracts) > 1 or len(frequencies) > 1:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE",
                    "A panel input changes units, scale, adjustment, base or frequency.",
                )
            context = {key: rows[0].get(key) for key in CONTEXT} if rows else {}
            input_frequency = rows[0].get("frequency") if rows else None
            ranks = {"daily": 0, "business_daily": 0, "monthly": 1, "quarterly": 2, "annual": 3}
            if rows and (not context.get("unit") or input_frequency not in ranks):
                raise AtlasError(
                    "INCOMPLETE_COVERAGE",
                    "A panel input needs explicit units and a supported frequency.",
                )
            if rows and ranks[str(input_frequency)] > ranks[q.frequency]:
                raise AtlasError(
                    "INVALID_INPUT", "Panels cannot upsample lower-frequency observations."
                )
        except (AtlasError, KeyError, TypeError, ValueError, AttributeError) as problem:
            failure_error = (
                problem
                if isinstance(problem, AtlasError)
                else AtlasError("SOURCE_UNAVAILABLE", "Panel source response failed validation.")
            )
            if not q.allow_partial:
                raise failure_error from None
            failure = failure_error.envelope()["error"]
            errors.append({"id": item.id, "error": failure})
            rows, context, input_frequency = [], {}, None
        column.update(
            context=context, input_frequency=input_frequency, coverage=coverages, error=failure
        )
        columns.append(column)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            end = period_bounds(row["date"], q.frequency)[1]
            grouped.setdefault(end, []).append(row)
        for record, (start, end) in zip(records, grid, strict=True):
            record["records"].append(
                aggregate(
                    grouped.get(end, []),
                    item.id,
                    item.aggregation,
                    start,
                    end,
                    q.frequency,
                    context,
                    input_frequency,
                )
            )
        if len(canonical({"records": records, "columns": columns})) > MAX_PANEL_BYTES - 65536:
            raise AtlasError(
                "RESPONSE_TOO_LARGE",
                "Panel exceeds 4 MiB; reduce the date range or number of items.",
            )
    missing = sum(cell["value"] is None for row in records for cell in row["records"])
    if missing and not q.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Panel has missing cells; inspect a smaller range or explicitly allow_partial.",
            details={"missing_cells": missing},
        )
    result = {
        "source_id": "multiple",
        "records": records,
        "columns": columns,
        "sources": list(sources.values()),
        "errors": errors,
        "coverage": {
            "returned_records": len(records),
            "truncated": False,
            "partial": bool(missing or errors),
            "missing_cells": missing,
            "requested_cells": len(records) * len(q.items),
            "research_reads": sum(map(len, plan)),
        },
        "comparability": {
            "calendar_aligned": True,
            "statistical_comparability": "not_established",
            "automatic_rebasing": False,
            "currency_conversion": False,
        },
        "limitations": [
            "Independent current/revised reads are not atomic snapshots or release vintages.",
            "Units, base years, seasonal adjustments and definitions may differ across columns.",
            "Daily aggregates use observed dates; complete business-day coverage is not certified.",
            "Last selects the last available observation, without certifying a period-end fixing.",
            "No forward filling, interpolation, upsampling, rebasing or automatic unit conversion.",
        ],
    }
    if len(canonical(result)) > MAX_PANEL_BYTES - 16384:
        raise AtlasError(
            "RESPONSE_TOO_LARGE", "Panel exceeds 4 MiB; reduce the date range or number of items."
        )
    return result
