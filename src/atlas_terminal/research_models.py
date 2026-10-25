"""Validated, bounded queries for free public research datasets."""

from copy import deepcopy
from datetime import date
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import (
    CURVE_SERIES,
    FX_CURRENCIES,
    MACRO_SERIES,
    REFERENCE_SERIES,
    Currency,
)
from atlas_terminal.models import Model, identifier
from atlas_terminal.research_quality import ResearchRequirements
from atlas_terminal.sdmx_sources import CODE_PATTERN, FLOW_PATTERN

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
    "fx_rates",
    "reference_rates",
    "treasury_real_yields",
    "jgb_yields",
    "macro_analytics",
    "national_accounts",
    "japan_economics",
    "sdmx_catalog",
    "international_economics",
    "macro_panel",
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
    "economics": {"series", "limit", "start_date", "end_date"},
    "treasury_yields": {"year", "limit"},
    "federal_debt": {"limit"},
    "policy_news": {"limit"},
    "compare": {"identifiers", "metrics", "period", "allow_partial"},
    "fx_rates": {"base_currency", "quote_currency", "limit", "start_date", "end_date"},
    "reference_rates": {"series", "limit"},
    "treasury_real_yields": {"year", "limit"},
    "jgb_yields": {"year", "limit", "start_date", "end_date"},
    "macro_analytics": {"series", "year", "limit", "start_date", "end_date"},
    "national_accounts": {"series", "limit", "start_date", "end_date"},
    "japan_economics": {"series", "limit", "start_date", "end_date"},
    "sdmx_catalog": {"source_id", "dataflow", "search", "dimension", "limit"},
    "international_economics": {
        "source_id",
        "dataflow",
        "dimensions",
        "start_date",
        "end_date",
        "limit",
    },
    "macro_panel": {"items", "frequency", "start_date", "end_date", "allow_partial"},
}


def dataset_required(name: str) -> list[str]:
    if name == "sdmx_catalog":
        return ["source_id"]
    if name == "international_economics":
        return ["source_id", "dataflow", "dimensions", "start_date", "end_date"]
    if name == "macro_panel":
        return ["items", "start_date", "end_date"]
    if name == "compare":
        return ["identifiers"]
    if name in {
        "economics",
        "reference_rates",
        "macro_analytics",
        "national_accounts",
        "japan_economics",
    }:
        return ["series"]
    if name == "fx_rates":
        return ["base_currency", "quote_currency"]
    if name not in {
        "companies",
        "treasury_yields",
        "treasury_real_yields",
        "jgb_yields",
        "federal_debt",
        "policy_news",
    }:
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
    search: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    frequency: Literal["daily", "monthly", "quarterly", "annual"] | None = None
    source_id: str | None = Field(default=None, pattern=r"^[a-z][a-z_]{0,39}$")
    unit: str | None = Field(default=None, min_length=1, max_length=100)


class PanelItem(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    query: dict[str, Any] = Field(description="One macro/FX query; panel supplies dates and limit.")
    aggregation: Literal["last", "mean"] = Field(
        description="Explicit last available observation or arithmetic mean; never forward fills."
    )


def dataset_schema(schema: dict[str, Any]) -> None:
    # One source of truth for runtime validation, MCP, HTTP and the catalog.
    from atlas_terminal.public_sources import BLS_SERIES
    from atlas_terminal.research_analytics import ANALYTICS_METRICS
    from atlas_terminal.research_financials import STATEMENT_METRICS

    branches = []
    for name, fields in FIELDS.items():
        properties = {
            key: deepcopy(schema["properties"][key])
            for key in ["dataset", *sorted(fields), "response", "requirements"]
        }
        properties["dataset"] = {"const": name, "type": "string"}
        for required in dataset_required(name):
            if required in {"identifier", "series"}:
                properties[required] = {"type": "string", "minLength": 1, "maxLength": 100}
            elif required == "identifiers":
                properties[required]["minItems"] = 1
            elif required == "items":
                properties[required]["minItems"] = 1
            elif required == "dimensions":
                properties[required]["minProperties"] = 1
            elif "anyOf" in properties[required]:
                choices = [
                    part for part in properties[required]["anyOf"] if part.get("type") != "null"
                ]
                properties[required] = choices[0] if len(choices) == 1 else {"anyOf": choices}
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
        if name in {"reference_rates", "macro_analytics"}:
            properties["series"]["enum"] = sorted(
                REFERENCE_SERIES if name == "reference_rates" else MACRO_SERIES
            )
        if name in {"national_accounts", "japan_economics"}:
            properties["series"]["enum"] = sorted(
                NATIONAL_ACCOUNTS if name == "national_accounts" else JAPAN_SERIES
            )
        if name == "fx_rates":
            for key in ("base_currency", "quote_currency"):
                properties[key] = {"type": "string", "enum": list(FX_CURRENCIES)}
            properties["quote_currency"]["description"] = (
                "Units of quote currency per one base currency; must differ from base_currency."
            )
        if name == "treasury_real_yields":
            properties["year"]["anyOf"][0]["minimum"] = 2003
        branch: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": ["dataset", *dataset_required(name)],
            "additionalProperties": False,
        }
        if name == "macro_analytics":
            real_series = [
                key
                for key, spec in CURVE_SERIES.items()
                if any(term["curve"] == "real" for term in spec["terms"])
            ]
            branch["allOf"] = [
                {
                    "if": {"properties": {"series": {"enum": real_series}}, "required": ["series"]},
                    "then": {
                        "properties": {
                            "year": {
                                "anyOf": [
                                    {"type": "integer", "minimum": 2003, "maximum": 2100},
                                    {"type": "null"},
                                ]
                            }
                        }
                    },
                }
            ]
        if "start_date" in fields:
            branch["dependentRequired"] = {"start_date": ["end_date"], "end_date": ["start_date"]}
        if name == "fx_rates":
            branch["not"] = {
                "anyOf": [
                    {
                        "properties": {
                            "base_currency": {"const": currency},
                            "quote_currency": {"const": currency},
                        },
                        "required": ["base_currency", "quote_currency"],
                    }
                    for currency in FX_CURRENCIES
                ]
            }
        branches.append(branch)
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
    base_currency: Currency | None = None
    quote_currency: Currency | None = None
    start_date: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    end_date: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    source_id: Literal["imf", "oecd"] | None = None
    dataflow: str | None = Field(default=None, pattern="^" + FLOW_PATTERN + "$", max_length=250)
    dimensions: dict[str, str] = Field(default_factory=dict, max_length=32)
    search: str | None = Field(default=None, min_length=1, max_length=100)
    dimension: str | None = Field(default=None, pattern="^" + CODE_PATTERN + "$")
    frequency: Literal["monthly", "quarterly", "annual"] = "monthly"
    items: list[PanelItem] = Field(default_factory=list, max_length=10)
    response: ResponseOptions = Field(default_factory=ResponseOptions)
    requirements: ResearchRequirements | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def calendar_date(cls, value: str | None) -> str | None:
        if value is not None and not 1900 <= date.fromisoformat(value).year <= 2100:
            raise ValueError("Observation dates must be between 1900 and 2100")
        return value

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
        if self.dataset in {"sdmx_catalog", "international_economics"}:
            if self.source_id is None:
                raise AtlasError("INVALID_INPUT", "Choose imf or oecd.", fields=["source_id"])
            if self.dimension is not None and self.dataflow is None:
                raise AtlasError("INVALID_INPUT", "A dimension filter requires a dataflow.")
        if self.dataset == "international_economics":
            import re

            if not self.dataflow or not self.start_date or not self.end_date or not self.dimensions:
                raise AtlasError(
                    "INVALID_INPUT", "Provide dataflow, exact dimensions and both dates."
                )
            if any(
                not re.fullmatch(CODE_PATTERN, key) or not re.fullmatch(CODE_PATTERN, value)
                for key, value in self.dimensions.items()
            ):
                raise AtlasError(
                    "INVALID_INPUT", "Use exact SDMX dimension codes; wildcards are unavailable."
                )
        if self.dataset == "macro_panel":
            from atlas_terminal.research_panel import panel_queries

            panel_queries(self)
        if (self.start_date is None) != (self.end_date is None) or (
            self.start_date
            and self.end_date
            and (
                self.start_date > self.end_date
                or int(self.end_date[:4]) - int(self.start_date[:4]) > 29
                or self.year is not None
            )
        ):
            raise AtlasError(
                "INVALID_INPUT",
                (
                    "Provide both ordered observation dates spanning at most 30 "
                    "calendar years; do not combine with year."
                ),
                fields=["start_date", "end_date"],
            )
        if self.model_fields_set - (FIELDS[self.dataset] | {"dataset", "response", "requirements"}):
            raise AtlasError(
                "INVALID_INPUT",
                "Argument does not apply to this dataset.",
                fields=sorted(
                    self.model_fields_set
                    - (FIELDS[self.dataset] | {"dataset", "response", "requirements"})
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
        if self.dataset in {"reference_rates", "macro_analytics"}:
            allowed = REFERENCE_SERIES if self.dataset == "reference_rates" else MACRO_SERIES
            if self.series not in allowed:
                raise AtlasError(
                    "INVALID_INPUT",
                    "Choose a registered series for this dataset.",
                    fields=["series"],
                )
        if self.dataset in {"national_accounts", "japan_economics"}:
            selected = NATIONAL_ACCOUNTS if self.dataset == "national_accounts" else JAPAN_SERIES
            if self.series not in selected:
                raise AtlasError(
                    "INVALID_INPUT", "Choose a series from the dataset catalog.", fields=["series"]
                )
        if self.dataset == "fx_rates" and (
            self.base_currency is None
            or self.quote_currency is None
            or self.base_currency == self.quote_currency
        ):
            raise AtlasError(
                "INVALID_INPUT",
                "Choose distinct supported base and quote currencies.",
                fields=["base_currency", "quote_currency"],
            )
        if self.dataset == "treasury_real_yields" and self.year is not None and self.year < 2003:
            raise AtlasError(
                "INVALID_INPUT", "Treasury real-yield coverage starts in 2003.", fields=["year"]
            )
        if self.dataset == "macro_analytics" and self.series in CURVE_SERIES:
            spec = CURVE_SERIES[self.series]
            if MACRO_SERIES[self.series]["source_id"] == "treasury":
                earliest = int(self.start_date[:4]) if self.start_date else self.year
                minimum = 2003 if any(term["curve"] == "real" for term in spec["terms"]) else 1990
                if earliest is not None and earliest < minimum:
                    raise AtlasError(
                        "INVALID_INPUT",
                        f"The requested Treasury curve coverage starts in {minimum}.",
                        fields=["year", "start_date"],
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
                str(error["loc"][0]) + "." + str(error["loc"][1])
                if error["loc"][0] in {"response", "requirements"}
                and len(error["loc"]) > 1
                and error["loc"][1]
                in (
                    ResponseOptions.model_fields
                    if error["loc"][0] == "response"
                    else ResearchRequirements.model_fields
                )
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
