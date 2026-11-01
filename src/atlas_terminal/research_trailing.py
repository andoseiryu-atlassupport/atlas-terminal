"""Exact trailing flows from reported windows, with every source operand retained."""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Literal, cast

from atlas_terminal.errors import AtlasError
from atlas_terminal.filing_catalog import TTM_DERIVED, TTM_FLOW_METRICS
from atlas_terminal.providers import SECProvider
from atlas_terminal.research_analytics import prior_year_matches
from atlas_terminal.research_financials import statement_data
from atlas_terminal.research_macro import numeric
from atlas_terminal.research_models import ResearchQuery


def decimal_number(value: str) -> Decimal:
    checked = numeric(value)
    if checked is None:
        raise AtlasError("SOURCE_UNAVAILABLE", "A monetary operand is missing.")
    return Decimal(checked)


def linear(values: list[tuple[str, int]]) -> str:
    """The bounded input width/exponent fits an exact sum within this context."""
    with localcontext(Context(prec=200)):
        return format(sum((decimal_number(v) * sign for v, sign in values), Decimal(0)), "f")


def ratio(numerator: str, denominator: str) -> str:
    with localcontext(Context(prec=200, rounding=ROUND_HALF_EVEN)):
        value = decimal_number(numerator) / decimal_number(denominator)
        return format(value.quantize(Decimal("1e-18")), "f")


def same_concept(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(
        r["status"] == "reported"
        and r["value"] is not None
        and r["unit"] == "USD"
        and r["source"]
        and r["source"]["taxonomy"] == records[0]["source"]["taxonomy"]
        and r["source"]["tag"] == records[0]["source"]["tag"]
        for r in records
    )


def trailing_record(
    metric: str,
    end: str,
    annual: list[dict[str, Any]],
    quarters: list[dict[str, Any]],
    ytd: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metric": metric,
        "value": None,
        "unit": "USD",
        "status": "unavailable",
        "derived": True,
        "period_type": "ttm",
        "period": {"kind": "duration", "start": None, "end": end},
        "inputs": [],
        "method": None,
        "formula": None,
        "reason": "No complete unambiguous trailing window from compatible reported inputs.",
    }

    def select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if r["metric"] == metric]

    a, q, y = select(annual), select(quarters), select(ytd)
    direct = [r for r in a if r["period"]["end"] == end]
    if len(direct) == 1 and same_concept(direct):
        result.update(
            value=direct[0]["value"],
            method="reported_annual",
            formula="annual",
            period=direct[0]["period"],
            inputs=[{"role": "annual", **direct[0]}],
        )
    else:
        chain, cursor = [], end
        for _ in range(4):
            candidates = [r for r in q if r["period"]["end"] == cursor]
            if len(candidates) != 1 or not same_concept(candidates):
                break
            chain.append(candidates[0])
            cursor = (
                date.fromisoformat(candidates[0]["period"]["start"]) - timedelta(days=1)
            ).isoformat()
        if len(chain) == 4 and same_concept(chain):
            start = chain[-1]["period"]["start"]
            if 330 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 400:
                result.update(
                    value=linear([(r["value"], 1) for r in chain]),
                    method="four_reported_quarters",
                    formula="q0 + q1 + q2 + q3",
                    period={"kind": "duration", "start": start, "end": end},
                    inputs=[{"role": f"q{i}", **r} for i, r in enumerate(chain)],
                )
        if result["value"] is None:
            current = [r for r in y if r["period"]["end"] == end]
            if len(current) == 1 and same_concept(current):
                c = current[0]
                priors = [r for r in y if prior_year_matches(c, r)]
                matches = []
                for p in priors:
                    for previous in a:
                        if (
                            same_concept([c, p, previous])
                            and previous["period"]["start"] == p["period"]["start"]
                            and (
                                date.fromisoformat(previous["period"]["end"]) + timedelta(days=1)
                            ).isoformat()
                            == c["period"]["start"]
                            and p["period"]["end"] < previous["period"]["end"]
                        ):
                            start = (
                                date.fromisoformat(p["period"]["end"]) + timedelta(days=1)
                            ).isoformat()
                            if (
                                330
                                <= (date.fromisoformat(end) - date.fromisoformat(start)).days
                                <= 400
                            ):
                                matches.append((c, p, previous, start))
                if len(matches) == 1:
                    c, p, previous, start = matches[0]
                    result.update(
                        value=linear([(previous["value"], 1), (c["value"], 1), (p["value"], -1)]),
                        method="annual_plus_ytd_minus_prior_ytd",
                        formula="previous_annual + current_ytd - prior_ytd",
                        period={"kind": "duration", "start": start, "end": end},
                        inputs=[
                            {"role": role, **r}
                            for role, r in (
                                ("previous_annual", previous),
                                ("current_ytd", c),
                                ("prior_ytd", p),
                            )
                        ],
                    )
    if result["value"] is not None:
        result.update(status="derived", reason=None)
    return result


def derived_record(metric: str, end: str, flows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    inputs = [flows[name] for name in TTM_DERIVED[metric]]
    unit = "USD" if metric == "free_cash_flow" else "ratio"
    result: dict[str, Any] = {
        "metric": metric,
        "value": None,
        "unit": unit,
        "status": "unavailable",
        "derived": True,
        "period_type": "ttm",
        "period": inputs[0]["period"],
        "inputs": [{"role": row["metric"], **row} for row in inputs],
        "reason": "Trailing input values or actual periods are missing or inconsistent.",
        "formula": "operating_cash_flow - capital_expenditure"
        if metric == "free_cash_flow"
        else "(operating_cash_flow - capital_expenditure) / revenue"
        if metric == "free_cash_flow_margin"
        else "/".join(TTM_DERIVED[metric]),
        "decimal_places": 18 if unit == "ratio" else None,
        "rounding": "ROUND_HALF_EVEN" if unit == "ratio" else None,
    }
    if any(r["value"] is None for r in inputs) or any(
        r["period"] != inputs[0]["period"] for r in inputs
    ):
        return result
    if metric in {"free_cash_flow", "free_cash_flow_margin"}:
        if Decimal(inputs[1]["value"]) < 0:
            result["reason"] = "Reported PP&E acquisition payments must be nonnegative."
            return result
        numerator = linear([(inputs[0]["value"], 1), (inputs[1]["value"], -1)])
    else:
        numerator = inputs[0]["value"]
    if unit == "ratio" and Decimal(inputs[-1]["value"]) <= 0:
        result["reason"] = "The denominator must be positive."
        return result
    result.update(
        value=numerator if unit == "USD" else ratio(numerator, inputs[-1]["value"]),
        status="derived",
        reason=None,
    )
    return result


def trailing_financials(source: SECProvider, q: ResearchQuery) -> dict[str, Any]:
    metrics = q.metrics or ["revenue", "net_income", "operating_cash_flow", "free_cash_flow"]
    dependencies = list(
        dict.fromkeys(name for metric in metrics for name in TTM_DERIVED.get(metric, (metric,)))
    )
    frozen = source.financial_inputs(q.identifier or "")
    pages = {}
    for period in ("annual", "quarterly", "ytd"):
        try:
            pages[period] = statement_data(
                source,
                ResearchQuery(
                    dataset="statements",
                    identifier=q.identifier,
                    metrics=dependencies,
                    period=cast(Literal["annual", "quarterly", "ytd"], period),
                    count=20,
                    allow_partial=True,
                ),
                include_history=True,
                reported_inputs=frozen,
            )
        except AtlasError as error:
            if error.code != "INCOMPLETE_COVERAGE":
                raise
            pages[period] = {
                "company": frozen[0].model_dump(),
                "records": [],
                "period_ends": [],
                "sources": [],
                "coverage": {"history_truncated": frozen[3]},
            }
    versions: dict[str, str] = {}
    for page in pages.values():
        for ref in page["sources"]:
            if (
                ref["raw_source_url"] in versions
                and versions[ref["raw_source_url"]] != ref["raw_sha256"]
            ):
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "Trailing inputs came from different source versions."
                )
            versions[ref["raw_source_url"]] = ref["raw_sha256"]
    ends = sorted({end for page in pages.values() for end in page["period_ends"]}, reverse=True)[
        : q.count
    ]
    if not ends:
        raise AtlasError("INCOMPLETE_COVERAGE", "No reported trailing-window anchors.")
    records: list[dict[str, Any]] = []
    for end in ends:
        flows = {
            name: trailing_record(
                name,
                end,
                pages["annual"]["records"],
                pages["quarterly"]["records"],
                pages["ytd"]["records"],
            )
            for name in dependencies
            if name in TTM_FLOW_METRICS
        }
        records.extend(
            flows[metric] if metric in flows else derived_record(metric, end, flows)
            for metric in metrics
        )
    available = sum(r["value"] is not None for r in records)
    partial = (
        available != len(records)
        or len(ends) < q.count
        or any(p["coverage"]["history_truncated"] for p in pages.values())
    )
    if partial and not q.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Trailing windows are incomplete; use allow_partial to inspect."
        )
    return {
        "company": pages["annual"]["company"],
        "records": records,
        "period_ends": ends,
        "sources": next(page["sources"] for page in pages.values() if page["sources"]),
        "coverage": {
            "requested_periods": q.count,
            "available_periods": len(ends),
            "requested_cells": len(metrics) * q.count,
            "available_cells": available,
            "partial": partial,
            "point_in_time": False,
        },
        "limitations": [
            "Trailing monetary flows use a reported annual window, four contiguous "
            "reported quarters,"
            "or the direct previous-annual + current-YTD - prior-YTD identity, in that order.",
            "Each monetary input must have the same concept and USD unit and "
            "matching actual dates."
            "EPS, weighted-average shares, balances and per-share dividends are not "
            "additive flows.",
            "Source fiscal years may contain 52/53 weeks. Missing quarters remain unreported; "
            "the direct trailing identity produces a trailing window only.",
            "Source restatement, accounting-policy consistency and historical publication "
            "availability are not independently verified. These are current-source calculations.",
        ],
    }
