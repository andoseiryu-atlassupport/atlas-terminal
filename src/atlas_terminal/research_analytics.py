"""Traceable growth and cash-flow calculations from reported primary-period facts."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SECProvider
from atlas_terminal.research_financials import statement_data
from atlas_terminal.research_models import ResearchQuery


@dataclass(frozen=True)
class AnalyticsSpec:
    inputs: tuple[str, ...]
    formula: str
    unit: str = "ratio"
    year_over_year: bool = False


ANALYTICS_METRICS = {
    **{
        f"{metric}_growth_yoy": AnalyticsSpec(
            (metric,),
            f"({metric}[current] - {metric}[prior_year]) / {metric}[prior_year]",
            year_over_year=True,
        )
        for metric in ("revenue", "operating_income", "net_income", "eps_basic", "eps_diluted")
    },
    "free_cash_flow": AnalyticsSpec(
        ("operating_cash_flow", "capital_expenditure"),
        "operating_cash_flow - capital_expenditure",
        unit="USD",
    ),
    "free_cash_flow_margin": AnalyticsSpec(
        ("operating_cash_flow", "capital_expenditure", "revenue"),
        "(operating_cash_flow - capital_expenditure) / revenue",
    ),
}
ANALYTICS_LIMITATIONS = [
    "Growth is a ratio, not percent: 0.25 means 25%; prior-year inputs must be positive.",
    (
        "Year-over-year matching uses actual start/end dates 358-372 days apart, "
        "with duration lengths differing by at most seven days; missing quarters are not filled."
    ),
    "Growth requires the same reported taxonomy, concept and unit in both periods.",
    (
        "FCF is reported operating cash flow minus nonnegative reported PP&E acquisition "
        "payments, not an issuer's adjusted/non-GAAP FCF. Input periods must be identical."
    ),
    "Calculations use selected current-source facts, not historical information availability.",
]


def analytics_catalog() -> dict[str, Any]:
    return {name: asdict(spec) for name, spec in ANALYTICS_METRICS.items()}


def analytics_inputs(metrics: list[str]) -> list[str]:
    """Return reported dependencies, preserving the caller's metric order."""
    return list(
        dict.fromkeys(
            dependency
            for metric in metrics
            for dependency in (
                ANALYTICS_METRICS[metric].inputs if metric in ANALYTICS_METRICS else (metric,)
            )
        )
    )


def exact_difference(left: str, right: str) -> Decimal:
    """Subtract without rounding away significant digits before a ratio is calculated."""
    a, b = Decimal(left), Decimal(right)
    # Enough precision for all places in either operand plus a possible carry.
    precision = max(
        28,
        max(a.adjusted(), b.adjusted())
        - min(int(a.as_tuple().exponent), int(b.as_tuple().exponent))
        + 2,
    )
    with localcontext(Context(prec=precision)):
        return a - b


def prior_year_matches(current: dict[str, Any], prior: dict[str, Any]) -> bool:
    a, b = current["period"], prior["period"]
    if a["kind"] != "duration" or b["kind"] != "duration" or not a["start"] or not b["start"]:
        return False
    start_gap = (date.fromisoformat(a["start"]) - date.fromisoformat(b["start"])).days
    end_gap = (date.fromisoformat(a["end"]) - date.fromisoformat(b["end"])).days
    return 358 <= start_gap <= 372 and 358 <= end_gap <= 372 and abs(start_gap - end_gap) <= 7


def growth_record(
    metric: str, current: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep current/prior roles explicit, including when the rate is unavailable."""
    spec = ANALYTICS_METRICS[metric]
    inputs = [{"role": "current", **current}]
    prior = None
    reason = None
    value = None
    if current["status"] != "reported":
        reason = "The current reported input is missing or ambiguous."
    else:
        candidates = [
            record
            for record in history
            if record["metric"] == current["metric"]
            and 358
            <= (
                date.fromisoformat(current["period"]["end"])
                - date.fromisoformat(record["period"]["end"])
            ).days
            <= 372
        ]
        matches = [record for record in candidates if prior_year_matches(current, record)]
        if any(record["status"] == "ambiguous" for record in candidates):
            reason = "A prior-year reported input is ambiguous."
            inputs.extend({"role": "prior_year_candidate", **record} for record in candidates)
        elif len(matches) != 1:
            reason = "No unique prior-year input with corresponding actual start/end dates."
            inputs.extend({"role": "prior_year_candidate", **record} for record in candidates)
        else:
            prior = matches[0]
            inputs.append({"role": "prior_year", **prior})
            if current["unit"] != prior["unit"] or any(
                current["source"][key] != prior["source"][key] for key in ("taxonomy", "tag")
            ):
                reason = "Reported concepts or units differ; no equivalence is assumed."
            elif Decimal(prior["value"]) <= 0:
                reason = (
                    "The prior-year value must be positive; zero/negative-base growth is undefined."
                )
            else:
                difference = exact_difference(current["value"], prior["value"])
                with localcontext(Context(prec=28)):
                    value = format(difference / Decimal(prior["value"]), "f")
    return {
        "metric": metric,
        "value": value,
        "unit": spec.unit,
        "derived": True,
        "status": "derived" if value is not None else "unavailable",
        "reason": reason,
        "formula": spec.formula,
        "decimal_precision": 28,
        "period": current["period"],
        "period_end": current["period"]["end"],
        "comparison_period": prior["period"] if prior else None,
        "inputs": inputs,
    }


def cash_flow_record(metric: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    spec = ANALYTICS_METRICS[metric]
    reason = None
    value = None
    if any(record["status"] != "reported" for record in inputs):
        reason = "A required reported input is missing or ambiguous."
    elif any(record["period"] != inputs[0]["period"] for record in inputs):
        reason = "Reported inputs have different periods."
    elif any(record["unit"] != "USD" for record in inputs):
        reason = "Reported inputs must use USD without currency conversion."
    elif Decimal(inputs[1]["value"]) < 0:
        reason = (
            "Capital expenditure must be a nonnegative reported cash outflow; no sign is inferred."
        )
    elif metric == "free_cash_flow_margin" and Decimal(inputs[2]["value"]) == 0:
        reason = "The revenue denominator is zero."
    else:
        fcf = exact_difference(inputs[0]["value"], inputs[1]["value"])
        if metric == "free_cash_flow_margin":
            with localcontext(Context(prec=28)):
                value = format(fcf / Decimal(inputs[2]["value"]), "f")
        else:
            value = format(fcf, "f")
    return {
        "metric": metric,
        "value": value,
        "unit": spec.unit,
        "derived": True,
        "status": "derived" if value is not None else "unavailable",
        "reason": reason,
        "formula": spec.formula,
        "decimal_precision": 28 if spec.unit == "ratio" else None,
        "period": inputs[0]["period"],
        "period_end": inputs[0]["period"]["end"],
        "inputs": inputs,
    }


def analytics_records(
    history: dict[str, Any], metrics: list[str], ends: list[str]
) -> list[dict[str, Any]]:
    inputs = {(r["metric"], r["period"]["end"]): r for r in history["records"]}
    records = []
    for end in ends:
        for metric in metrics:
            spec = ANALYTICS_METRICS[metric]
            selected = [inputs[(dependency, end)] for dependency in spec.inputs]
            records.append(
                growth_record(metric, selected[0], history["records"])
                if spec.year_over_year
                else cash_flow_record(metric, selected)
            )
    return records


def analytics_data(source: SECProvider, query: ResearchQuery) -> dict[str, Any]:
    metrics = query.metrics or list(ANALYTICS_METRICS)
    result = statement_data(
        source,
        ResearchQuery(
            dataset="statements",
            identifier=query.identifier,
            metrics=analytics_inputs(metrics),
            period=query.period,
            allow_partial=True,
        ),
        include_history=True,
    )
    ends = result["period_ends"][: query.count]
    records = analytics_records(result, metrics, ends)
    available = sum(record["value"] is not None for record in records)
    coverage = {
        "requested_periods": query.count,
        "available_periods": len(ends),
        "requested_cells": len(metrics) * query.count,
        "derived_cells": available,
        "history_truncated": result["coverage"]["history_truncated"],
        "partial": available < len(metrics) * query.count
        or result["coverage"]["history_truncated"],
    }
    if coverage["partial"] and not query.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Explicit allow_partial is required for incomplete financial analytics.",
            details=coverage,
        )
    return result | {
        "records": records,
        "period_ends": ends,
        "coverage": coverage,
        "selection_policy": "reported_inputs_financial_analytics_v1",
        "limitations": [*result["limitations"], *ANALYTICS_LIMITATIONS],
    }
