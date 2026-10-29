"""Bounded issuer screening and historical events disclosed in SEC filing metadata."""

import re
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Model
from atlas_terminal.providers import Filing, SECProvider

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery


def _filter_schema(schema: dict[str, Any]) -> None:
    units = screen_units()
    schema["properties"]["metric"]["enum"] = sorted(units)
    schema["properties"]["unit"]["enum"] = sorted(set(units.values()))
    schema["allOf"] = [
        {
            "if": {"properties": {"metric": {"const": metric}}, "required": ["metric"]},
            "then": {"properties": {"unit": {"const": unit}}},
        }
        for metric, unit in sorted(units.items())
    ]


class ScreenFilter(Model):
    """A threshold in the catalog metric's original unit, without numeric coercion."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_filter_schema)
    metric: str = Field(min_length=1, max_length=80)
    operator: Literal["lt", "lte", "eq", "gte", "gt"]
    value: str = Field(pattern=r"^-?(?:0|[1-9][0-9]{0,59})(?:\.[0-9]{1,30})?$", strict=True)
    unit: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def metric_unit(self) -> "ScreenFilter":
        units = screen_units()
        if self.metric not in units:
            raise ValueError("Choose a reported or financial-analytics metric from the catalog")
        if self.unit != units[self.metric]:
            raise ValueError("Threshold unit must match the metric's catalog unit")
        return self


def screen_units() -> dict[str, str]:
    # Lazy imports keep the query model's ScreenFilter import acyclic.
    from atlas_terminal.research_analytics import ANALYTICS_METRICS
    from atlas_terminal.research_financials import RATIOS, STATEMENT_METRICS

    return (
        {name: definition.unit for name, definition in STATEMENT_METRICS.items()}
        | {name: definition.unit for name, definition in ANALYTICS_METRICS.items()}
        | dict.fromkeys(RATIOS, "ratio")
    )


def check_screen_query(query: "ResearchQuery") -> None:
    if not query.identifiers or not query.filters:
        raise AtlasError(
            "INVALID_INPUT",
            "Provide one to ten explicit issuers and at least one threshold filter.",
            fields=["identifiers", "filters"],
        )
    if len(query.identifiers) > 10 or len(query.filters) > 10:
        raise AtlasError("INVALID_INPUT", "Screening accepts at most ten issuers and ten filters.")
    units = screen_units()
    selected = {*query.metrics, *(condition.metric for condition in query.filters)}
    if query.sort_by:
        selected.add(query.sort_by)
    if len(selected) > 40:
        raise AtlasError(
            "INVALID_INPUT",
            "Choose at most forty distinct metrics across output, filters and sorting.",
            fields=["metrics", "filters", "sort_by"],
        )
    if selected - units.keys():
        raise AtlasError(
            "UNSUPPORTED_METRIC",
            "Choose reported or financial-analytics metrics.",
            fields=["metrics"],
        )
    if len({(condition.metric, condition.operator) for condition in query.filters}) != len(
        query.filters
    ):
        raise AtlasError(
            "INVALID_INPUT", "Do not repeat a metric/operator filter.", fields=["filters"]
        )


def screen_catalog() -> dict[str, Any]:
    return {
        "metric_units": screen_units(),
        "operators": ["lt", "lte", "eq", "gte", "gt"],
        "filter_policy": "all_thresholds_exact_decimal_v1",
        "universe": "One to ten explicit issuer identifiers; not the whole market.",
        "example": {
            "dataset": "company_screen",
            "identifiers": ["ATLASDEMO"],
            "metrics": ["revenue", "revenue_growth_yoy"],
            "filters": [
                {"metric": "revenue_growth_yoy", "operator": "gte", "value": "0.1", "unit": "ratio"}
            ],
            "sort_by": "revenue_growth_yoy",
            "sort_order": "desc",
        },
    }


def _predicate(value: Decimal, operator: str, threshold: Decimal) -> bool:
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "eq": value == threshold,
        "gte": value >= threshold,
        "gt": value > threshold,
    }[operator]


def screen_data(source: SECProvider, query: "ResearchQuery") -> dict[str, Any]:
    from atlas_terminal.research_comparison import comparison_data
    from atlas_terminal.research_models import ResearchQuery

    check_screen_query(query)
    metrics = list(dict.fromkeys([*query.metrics, *(item.metric for item in query.filters)]))
    if query.sort_by and query.sort_by not in metrics:
        metrics.append(query.sort_by)
    compared = comparison_data(
        source,
        ResearchQuery(
            dataset="compare",
            identifiers=query.identifiers,
            metrics=metrics,
            period=query.period,
            allow_partial=query.allow_partial,
        ),
    )
    issuers = {issuer["identifier"]: issuer for issuer in compared["records"]}
    ciks = [issuer["company"]["cik"] for issuer in issuers.values()]
    if len(set(ciks)) != len(ciks):
        raise AtlasError(
            "INVALID_INPUT",
            "Screen identifiers must resolve to distinct issuers; aliases share the same CIK.",
            fields=["identifiers"],
        )
    rows: list[dict[str, Any]] = []
    errors = {error["identifier"]: error for error in compared["errors"]}
    for identifier in query.identifiers:
        issuer = issuers.get(identifier)
        if issuer is None:
            rows.append(
                {
                    "identifier": identifier,
                    "screen_status": "unavailable",
                    "matches": None,
                    "records": [],
                    "filter_results": [],
                    "error": errors.get(identifier),
                }
            )
            continue
        cells = {record["metric"]: record for record in issuer["records"]}
        results = []
        for condition in query.filters:
            cell = cells[condition.metric]
            known = (
                cell["value"] is not None
                and cell.get("comparable") is True
                and cell["unit"] == condition.unit
            )
            results.append(
                {
                    "metric": condition.metric,
                    "operator": condition.operator,
                    "threshold": condition.value,
                    "unit": condition.unit,
                    "value": cell["value"],
                    "passed": _predicate(
                        Decimal(cell["value"]), condition.operator, Decimal(condition.value)
                    )
                    if known
                    else None,
                    "reason": None
                    if known
                    else cell.get(
                        "reason", "Comparable input in the specified unit is unavailable."
                    ),
                }
            )
        unknown = any(result["passed"] is None for result in results)
        passed = None if unknown else all(result["passed"] for result in results)
        rows.append(
            issuer
            | {
                "screen_status": "unavailable" if unknown else "matched" if passed else "excluded",
                "matches": passed,
                "filter_results": results,
            }
        )
    if query.sort_by:
        available, unavailable = [], []
        for row in rows:
            cell = next((item for item in row["records"] if item["metric"] == query.sort_by), None)
            if cell is not None and cell["value"] is not None and cell.get("comparable") is True:
                available.append((Decimal(cell["value"]), row))
            else:
                unavailable.append(row)
        available.sort(key=lambda pair: pair[0], reverse=query.sort_order == "desc")
        rows = [row for _, row in available] + unavailable
    return compared | {
        "records": rows,
        "screen": {
            "policy": "all_thresholds_exact_decimal_v1",
            "filters": [condition.model_dump() for condition in query.filters],
            "sort_by": query.sort_by,
            "sort_order": query.sort_order if query.sort_by else None,
            "missing_policy": "unavailable; no imputation or silent exclusion",
            "universe": query.identifiers,
        },
        "coverage": compared["coverage"]
        | {
            "returned_issuers": len(rows),
            "matched_issuers": sum(row["screen_status"] == "matched" for row in rows),
            "excluded_issuers": sum(row["screen_status"] == "excluded" for row in rows),
            "unavailable_issuers": sum(row["screen_status"] == "unavailable" for row in rows),
        },
        "limitations": compared["limitations"]
        + [
            "Every requested issuer is returned, including excluded or unavailable results.",
            (
                "Thresholds use reported units; price multiples and current market "
                "capitalization are unavailable."
            ),
            "All filters must be known and true to match; missing inputs never become zero.",
        ],
    }


EVENT_SERIES = {
    "all": "All classified SEC filing events",
    "earnings_results": "8-K or 8-K/A with exact Item 2.02 (results of operations)",
    "periodic_reports": "Annual and quarterly report filing events",
    "current_reports": "8-K and 6-K current-report filing events",
    "offerings": "Registration and prospectus filings, without inferred IPO dates",
    "shareholder_votes": "8-K or 8-K/A with exact Item 5.07 (voting results)",
    "ownership": "Insider, institutional and beneficial-ownership filing events",
    "fund_reports": "Fund reporting and voting filing events",
}
PERIODIC_FORMS = {"10-K", "10-Q", "20-F", "40-F"}
OFFERING_FORMS = {"S-1", "S-3", "S-4", "S-8", "S-11", "F-1", "F-3", "F-4", "F-10", "F-6"}
OWNERSHIP_FORMS = {
    "3",
    "4",
    "5",
    "13F-HR",
    "13F-NT",
    "SC 13D",
    "SC 13G",
    "SCHEDULE 13D",
    "SCHEDULE 13G",
}
FUND_FORMS = {"NPORT-P", "N-PX", "N-CSR", "N-CSRS", "N-CEN", "24F-2NT"}


def check_event_query(query: "ResearchQuery") -> None:
    if query.series is not None and query.series not in EVENT_SERIES:
        raise AtlasError(
            "INVALID_INPUT", "Choose a filing event series from the catalog.", fields=["series"]
        )


def event_catalog() -> dict[str, Any]:
    return {
        "series": EVENT_SERIES,
        "date_basis": (
            "SEC filingDate; not an earnings announcement, trade or future scheduled date"
        ),
        "example": {
            "dataset": "filing_events",
            "identifier": "ATLASDEMO",
            "series": "earnings_results",
        },
    }


def _event_types(form: str, items: Any) -> tuple[list[str], dict[str, str]]:
    base = form.removesuffix("/A")
    types, rules = [], {}
    if base in {"8-K", "6-K"}:
        types.append("current_reports")
        rules["current_reports"] = "form is 8-K, 8-K/A, 6-K or 6-K/A"
    if base == "8-K" and isinstance(items, str):
        tokens = {item.strip() for item in re.split(r"[,;]", items) if item.strip()}
        for item, name in [("2.02", "earnings_results"), ("5.07", "shareholder_votes")]:
            if item in tokens:
                types.append(name)
                rules[name] = f"form is 8-K or 8-K/A and items contains exact {item}"
    for forms, name in [
        (PERIODIC_FORMS, "periodic_reports"),
        (OWNERSHIP_FORMS, "ownership"),
        (FUND_FORMS, "fund_reports"),
    ]:
        if base in forms:
            types.append(name)
            rules[name] = "form is " + form
    if base in OFFERING_FORMS or re.fullmatch(r"424B[1-8]", base):
        types.append("offerings")
        rules["offerings"] = "form is " + form
    return types, rules


FilingInputs = Callable[[str], tuple[dict[str, Any], list[Filing], dict[str, dict[str, Any]], bool]]


def _field_source(source: dict[str, Any], field: str) -> dict[str, Any]:
    pointer = source.get("json_pointer", "")
    match = re.fullmatch(r"(.*)/accessionNumber/([0-9]+)", pointer)
    if match is None:
        raise AtlasError("SOURCE_UNAVAILABLE", "Filing metadata does not identify its source row.")
    return {key: value for key, value in source.items() if key != "items"} | {
        "json_pointer": f"{match[1]}/{field}/{match[2]}"
    }


def filing_events(filing_inputs: FilingInputs, query: "ResearchQuery") -> dict[str, Any]:
    check_event_query(query)
    company, history, refs, truncated = filing_inputs(query.identifier or "")
    rows = []
    for filing in history:
        if query.start_date and not query.start_date <= filing.filed_on <= (query.end_date or ""):
            continue
        source = refs[filing.accession]
        items = source.get("items")
        types, rules = _event_types(filing.form, items)
        if not types or (query.series not in {None, "all"} and query.series not in types):
            continue
        clean_source = {key: value for key, value in source.items() if key != "items"}
        field_sources = {
            "filed_on": _field_source(source, "filingDate"),
            "form": _field_source(source, "form"),
        }
        if filing.report_date:
            field_sources["report_date"] = _field_source(source, "reportDate")
        if filing.accepted_at:
            field_sources["accepted_at"] = _field_source(source, "acceptanceDateTime")
        if items is not None:
            field_sources["items"] = _field_source(source, "items")
        rows.append(
            asdict(filing)
            | {
                "event_date": filing.filed_on,
                "date_basis": "filing_date",
                "event_types": types,
                "classification_rules": rules,
                "items": items,
                "amendment": filing.form.endswith("/A"),
                "source": clean_source,
                "field_sources": field_sources,
                "status": "reported_metadata",
            }
        )
    rows.sort(
        key=lambda row: (row["filed_on"], row["accepted_at"] or "", row["accession"]), reverse=True
    )
    return {
        "company": company,
        "records": rows[: query.limit],
        "sources": list(
            {
                (source["raw_source_url"], source["raw_sha256"]): {
                    key: value for key, value in source.items() if key != "items"
                }
                for source in refs.values()
            }.values()
        ),
        "coverage": {
            "matching_records": len(rows),
            "returned_records": min(len(rows), query.limit),
            "truncated": truncated or len(rows) > query.limit,
            "history_truncated": truncated,
            "date_basis": "filing_date",
            "scheduled_future_events": False,
        },
        "limitations": [
            (
                "Historical filing metadata only; dates select SEC filingDate, "
                "not announcement or meeting dates."
            ),
            (
                "Item 2.02 identifies a results disclosure, without parsing its financial "
                "amounts or announcing a future earnings date."
            ),
            (
                "A registration/prospectus filing is not evidence of an IPO date "
                "or completed offering."
            ),
            (
                "Amendments remain separate events; archive caps and unclassified "
                "forms can omit events."
            ),
        ],
    }
