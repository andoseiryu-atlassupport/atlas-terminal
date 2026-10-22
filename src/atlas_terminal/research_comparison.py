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
from atlas_terminal.research_financials import DEFAULT_METRICS, STATEMENT_METRICS, statement_data
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
    derived = metric in ANALYTICS_METRICS
    unit = ANALYTICS_METRICS[metric].unit if derived else STATEMENT_METRICS[metric].unit
    return {
        "metric": metric,
        "value": None,
        "unit": unit,
        "status": "unavailable",
        "reason": "No common period end exists in the retrieved issuer histories.",
        "derived": derived,
        "period": {
            "kind": "duration" if derived or not STATEMENT_METRICS[metric].instant else "instant",
            "start": None,
            "end": None,
        },
        "source": None,
        "comparable": False,
    }


def comparison_data(source: SECProvider, query: ResearchQuery) -> dict[str, Any]:
    metrics = query.metrics or DEFAULT_METRICS
    derived_metrics = [metric for metric in metrics if metric in ANALYTICS_METRICS]
    histories, errors, sources = [], [], []
    for identifier in query.identifiers:
        try:
            result = statement_data(
                source,
                ResearchQuery(
                    dataset="statements",
                    identifier=identifier,
                    metrics=analytics_inputs(metrics),
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
