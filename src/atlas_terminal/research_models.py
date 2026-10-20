"""Validated, bounded queries for free public research datasets."""

from copy import deepcopy
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Model, identifier

Dataset = Literal[
    "companies",
    "profile",
    "statements",
    "ratios",
    "financial_analytics",
    "company_facts",
    "filings",
    "insider_transactions",
    "institutional_holdings",
    "ownership_filings",
    "fund_filings",
    "corporate_action_filings",
    "economics",
    "treasury_yields",
    "federal_debt",
    "policy_news",
    "compare",
]

FIELDS = {
    "companies": {"identifier", "limit"},
    "profile": {"identifier"},
    "statements": {"identifier", "metrics", "period", "count", "allow_partial"},
    "ratios": {"identifier", "period", "count", "allow_partial"},
    "financial_analytics": {"identifier", "metrics", "period", "count", "allow_partial"},
    "company_facts": {"identifier", "taxonomy", "concept", "unit", "limit"},
    "filings": {"identifier", "form", "limit"},
    "insider_transactions": {"identifier", "accession", "limit"},
    "institutional_holdings": {"identifier", "accession", "limit"},
    "ownership_filings": {"identifier", "limit"},
    "fund_filings": {"identifier", "limit"},
    "corporate_action_filings": {"identifier", "limit"},
    "economics": {"series", "limit"},
    "treasury_yields": {"year", "limit"},
    "federal_debt": {"limit"},
    "policy_news": {"limit"},
    "compare": {"identifiers", "metrics", "period", "allow_partial"},
}


def dataset_required(name: str) -> list[str]:
    if name == "compare":
        return ["identifiers"]
    if name == "economics":
        return ["series"]
    if name not in {"companies", "treasury_yields", "federal_debt", "policy_news"}:
        return ["identifier"]
    return []


class ResponseOptions(Model):
    """Presentation only: does not expand upstream retrieval or relax coverage."""

    view: Literal["full", "compact"] = "full"
    fields: list[str] = Field(default_factory=list, max_length=40)
    page_size: int | None = Field(default=None, ge=1, le=500, strict=True)
    cursor: str | None = Field(default=None, pattern=r"^r1_[0-9a-f]{64}_[1-9][0-9]{0,5}$")

    @field_validator("fields")
    @classmethod
    def field_names(cls, values: list[str]) -> list[str]:
        import re

        if len(set(values)) != len(values) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) for value in values
        ):
            raise ValueError("Use unique record field names")
        return values

    @model_validator(mode="after")
    def cursor_needs_page_size(self) -> "ResponseOptions":
        if self.cursor and self.page_size is None:
            raise ValueError("Repeat the original page size with a cursor")
        return self


class CatalogQuery(Model):
    dataset: Dataset | None = None
    view: Literal["full", "compact"] = "full"


def dataset_schema(schema: dict[str, Any]) -> None:
    # One source of truth for runtime validation, MCP, HTTP and the catalog.
    from atlas_terminal.public_sources import BLS_SERIES
    from atlas_terminal.research_analytics import ANALYTICS_METRICS
    from atlas_terminal.research_financials import STATEMENT_METRICS

    branches = []
    for name, fields in FIELDS.items():
        properties = {
            key: deepcopy(schema["properties"][key])
            for key in ["dataset", *sorted(fields), "response"]
        }
        properties["dataset"] = {"const": name, "type": "string"}
        for required in dataset_required(name):
            if required in {"identifier", "series"}:
                properties[required] = {"type": "string", "minLength": 1, "maxLength": 100}
            elif required == "identifiers":
                properties[required]["minItems"] = 1
        if name in {"statements", "financial_analytics", "compare"}:
            metrics: set[str] = set()
            if name != "financial_analytics":
                metrics.update(STATEMENT_METRICS)
            if name != "statements":
                metrics.update(ANALYTICS_METRICS)
            properties["metrics"]["items"] = {"type": "string", "enum": sorted(metrics)}
        if name == "economics":
            properties["series"]["enum"] = sorted(
                {*BLS_SERIES, *(value[0] for value in BLS_SERIES.values())}
            )
        branches.append(
            {
                "type": "object",
                "properties": properties,
                "required": ["dataset", *dataset_required(name)],
                "additionalProperties": False,
            }
        )
    schema["oneOf"] = branches


class ResearchQuery(Model):
    model_config = ConfigDict(extra="forbid", json_schema_extra=dataset_schema)
    dataset: Dataset
    identifier: str | None = None
    identifiers: list[str] = Field(default_factory=list, max_length=10)
    metrics: list[str] = Field(default_factory=list, max_length=40)
    period: Literal["annual", "quarterly", "ytd"] = "annual"
    count: int = Field(default=3, ge=1, le=20, strict=True)
    limit: int = Field(default=100, ge=1, le=500, strict=True)
    allow_partial: bool = Field(default=False, strict=True)
    taxonomy: str = Field(default="us-gaap", pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    concept: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
    unit: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_/-]{0,49}$")
    form: str = Field(default="all", pattern=r"^[A-Za-z0-9][A-Za-z0-9 /-]{0,29}$")
    accession: str | None = Field(default=None, pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    series: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]{1,40}$")
    year: int | None = Field(default=None, ge=1990, le=2100, strict=True)
    response: ResponseOptions = Field(default_factory=ResponseOptions)

    @field_validator("identifier")
    @classmethod
    def check_identifier(cls, value: str | None) -> str | None:
        return identifier(value) if value is not None else None

    @field_validator("identifiers")
    @classmethod
    def check_identifiers(cls, values: list[str]) -> list[str]:
        result = [identifier(v) for v in values]
        if len(set(result)) != len(result):
            raise ValueError("Duplicate identifiers")
        return result

    @field_validator("metrics")
    @classmethod
    def check_metrics(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Duplicate metrics")
        return values

    @model_validator(mode="after")
    def check_dataset(self) -> "ResearchQuery":
        if self.model_fields_set - (FIELDS[self.dataset] | {"dataset", "response"}):
            raise AtlasError(
                "INVALID_INPUT",
                "Argument does not apply to this dataset.",
                fields=sorted(
                    self.model_fields_set - (FIELDS[self.dataset] | {"dataset", "response"})
                ),
            )
        if "identifier" in dataset_required(self.dataset) and self.identifier is None:
            raise AtlasError("INVALID_INPUT", "An identifier is required.", fields=["identifier"])
        if self.dataset == "compare" and not self.identifiers:
            raise AtlasError(
                "INVALID_INPUT",
                "Provide an explicit universe of at most ten issuers.",
                fields=["identifiers"],
            )
        if self.dataset == "economics" and self.series is None:
            raise AtlasError(
                "INVALID_INPUT", "Choose an economic series from the catalog.", fields=["series"]
            )
        if self.dataset == "economics":
            from atlas_terminal.public_sources import BLS_SERIES

            if self.series not in BLS_SERIES and self.series not in {
                value[0] for value in BLS_SERIES.values()
            }:
                raise AtlasError(
                    "INVALID_INPUT", "Choose a registered economic series.", fields=["series"]
                )
        if self.unit and not self.concept:
            raise AtlasError(
                "INVALID_INPUT", "A unit filter requires a concept.", fields=["concept"]
            )
        if self.dataset in {"statements", "financial_analytics", "compare"} and self.metrics:
            from atlas_terminal.research_analytics import ANALYTICS_METRICS
            from atlas_terminal.research_financials import STATEMENT_METRICS

            supported: set[str] = set()
            if self.dataset != "financial_analytics":
                supported.update(STATEMENT_METRICS)
            if self.dataset != "statements":
                supported.update(ANALYTICS_METRICS)
            if set(self.metrics) - supported:
                raise AtlasError(
                    "UNSUPPORTED_METRIC",
                    "Choose this dataset's metrics from the research catalog.",
                    fields=["metrics"],
                )
        return self


def parse_research(data: dict[str, Any]) -> ResearchQuery:
    if {"as_of", "published_before", "pit", "point_in_time"} & data.keys():
        raise AtlasError(
            "UNSUPPORTED_HISTORICAL_QUERY",
            "A historical observation date does not establish point-in-time availability.",
        )
    try:
        return ResearchQuery.model_validate(data)
    except ValidationError as exc:
        fields = sorted(
            {
                "response." + str(error["loc"][1])
                if error["loc"][0] == "response"
                and len(error["loc"]) > 1
                and error["loc"][1] in ResponseOptions.model_fields
                else str(error["loc"][0])
                for error in exc.errors(include_input=False, include_context=False)
                if error["loc"] and error["loc"][0] in ResearchQuery.model_fields
            }
        )
        raise AtlasError(
            "INVALID_INPUT",
            "Invalid research query; consult the dataset catalog and limits.",
            fields=fields,
        ) from None
