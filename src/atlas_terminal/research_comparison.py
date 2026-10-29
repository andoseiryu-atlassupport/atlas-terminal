"""Compare explicit issuers at a common reported end with identical input periods."""

from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SECProvider
from atlas_terminal.research_analytics import (
    ANALYTICS_LIMITATIONS,
    ANALYTICS_METRICS,
    analytics_inputs,
    analytics_records,
)
from atlas_terminal.research_financials import (
    DEFAULT_METRICS,
    RATIOS,
    STATEMENT_METRICS,
    ratio_records,
    statement_data,
)
from atlas_terminal.research_models import ResearchQuery


def period_key(record: dict[str, Any]) -> tuple[Any, ...]:
    period = record["period"]
    prior = record.get("comparison_period") or {}
    return (
        record["unit"],
        period["kind"],
        period["start"],
        period["end"],
        prior.get("kind"),
        prior.get("start"),
        prior.get("end"),
    )


def missing_record(metric: str) -> dict[str, Any]:
    derived = metric in ANALYTICS_METRICS or metric in RATIOS
    unit = (
        "ratio"
        if metric in RATIOS
        else ANALYTICS_METRICS[metric].unit
        if metric in ANALYTICS_METRICS
        else STATEMENT_METRICS[metric].unit
    )
    instant = (
        STATEMENT_METRICS[RATIOS[metric][0]].instant
        if metric in RATIOS
        else not derived and STATEMENT_METRICS[metric].instant
    )
    return {
        "metric": metric,
        "value": None,
        "unit": unit,
        "status": "unavailable",
        "reason": "No common period end exists in the retrieved issuer histories.",
        "derived": derived,
        "period": {
            "kind": "instant" if instant else "duration",
            "start": None,
            "end": None,
        },
        "source": None,
        "comparable": False,
    }


def comparison_data(source: SECProvider, query: ResearchQuery) -> dict[str, Any]:
    metrics = query.metrics or DEFAULT_METRICS
    derived_metrics = [metric for metric in metrics if metric in ANALYTICS_METRICS]
    ratio_metrics = [metric for metric in metrics if metric in RATIOS]
    reported_metrics = list(
        dict.fromkeys(
            dependency
            for metric in analytics_inputs(metrics)
            for dependency in (RATIOS[metric] if metric in RATIOS else (metric,))
        )
    )
    histories, errors, sources = [], [], []
    for identifier in query.identifiers:
        try:
            result = statement_data(
                source,
                ResearchQuery(
                    dataset="statements",
                    identifier=identifier,
                    metrics=reported_metrics,
                    period=query.period,
                    allow_partial=True,
                ),
                include_history=True,
            )
            histories.append({"identifier": identifier, **result})
            sources.extend(result["sources"])
        except AtlasError as exc:
            if not query.allow_partial:
                raise
            errors.append({"identifier": identifier, "code": exc.code, "message": exc.message})

    common = set(histories[0]["period_ends"]) if histories else set()
    for history in histories[1:]:
        common.intersection_update(history["period_ends"])
    end = max(common) if common else None
    results = []
    for history in histories:
        if end is None:
            selected = [missing_record(metric) for metric in metrics]
        else:
            reported = {
                record["metric"]: record
                for record in history["records"]
                if record["period"]["end"] == end
            }
            derived = {
                record["metric"]: record
                for record in analytics_records(history, derived_metrics, [end])
            }
            for ratio in ratio_records(history["records"], [end], ratio_metrics):
                derived[ratio["metric"]] = ratio | {"period": ratio["inputs"][0]["period"]}
            selected = [
                dict((derived if metric in derived else reported)[metric]) for metric in metrics
            ]
        results.append(
            history
            | {
                "records": selected,
                "available_period_ends": history["period_ends"],
                "period_ends": [end] if end else [],
            }
        )

    alignment = {}
    for metric in metrics:
        cells = [
            record
            for result in results
            for record in result["records"]
            if record["metric"] == metric
        ]
        available = [record for record in cells if record["value"] is not None]
        signatures = {period_key(record) for record in available}
        mismatched = len(signatures) > 1
        for record in cells:
            record["comparable"] = record["value"] is not None and not mismatched
            if mismatched and record["value"] is not None:
                record.update(
                    value=None,
                    status="unavailable",
                    reason="Issuer inputs do not cover identical actual periods and units.",
                )
        alignment[metric] = {
            "status": "mismatched_periods"
            if mismatched
            else ("aligned" if available else "unavailable"),
            "comparable_issuers": sum(record["comparable"] for record in cells),
        }

    for result in results:
        records = result["records"]
        available_cells = sum(record["value"] is not None for record in records)
        result["coverage"] = {
            "requested_periods": 1,
            "available_periods": 1 if end else 0,
            "requested_cells": len(metrics),
            "reported_cells": sum(record["status"] == "reported" for record in records),
            "derived_cells": sum(record["status"] == "derived" for record in records),
            "available_cells": available_cells,
            "history_truncated": result["coverage"]["history_truncated"],
            "partial": available_cells < len(metrics) or result["coverage"]["history_truncated"],
        }
        result["selection_policy"] = "latest_common_period_exact_dates_v1"
        if derived_metrics:
            result["limitations"] = [*result["limitations"], *ANALYTICS_LIMITATIONS]

    coverage = {
        "requested_issuers": len(query.identifiers),
        "returned_issuers": len(results),
        "requested_cells": len(query.identifiers) * len(metrics),
        "available_cells": sum(result["coverage"]["available_cells"] for result in results),
        "history_truncated": any(result["coverage"]["history_truncated"] for result in results),
        "partial": bool(errors)
        or end is None
        or any(result["coverage"]["partial"] for result in results),
    }
    if coverage["partial"] and not query.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Explicit allow_partial is required for missing or unaligned comparison inputs.",
            details=coverage,
        )
    return {
        "records": results,
        "errors": errors,
        "sources": sources,
        "period_ends": [end] if end else [],
        "alignment": {
            "policy": "latest_common_period_exact_dates_v1",
            "period_end": end,
            "metrics": alignment,
        },
        "coverage": coverage,
        "limitations": [
            (
                "Explicit universe, maximum ten issuers; latest common reported end "
                "in retrieved history."
            ),
            (
                "Different actual start dates, prior-year comparison periods or units "
                "suppress the affected values."
            ),
            (
                "No period interpolation, currency conversion, global-universe screen "
                "or survivorship-free backtest."
            ),
        ],
    }
