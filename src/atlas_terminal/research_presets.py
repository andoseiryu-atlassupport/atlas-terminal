"""Inspectable research recipes using the existing bounded batch service."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import Currency
from atlas_terminal.models import Model, identifier
from atlas_terminal.public_sources import SOURCE_CATALOG
from atlas_terminal.research_batch import (
    MAX_BATCH_REQUEST_BYTES,
    ResearchBatchResult,
    parse_batch,
)
from atlas_terminal.research_models import parse_research
from atlas_terminal.research_quality import (
    ResearchQuality,
    ResearchRequirements,
    leaf_records,
    missing,
    record_date,
)
from atlas_terminal.util import canonical, stamp, utcnow

PresetName = Literal["company_overview", "us_macro_overview", "fx_context"]
PRESETS: dict[str, dict[str, Any]] = {
    "company_overview": {
        "description": "Issuer profile, reported financials, growth/FCF and filing metadata.",
        "fields": ["identifier", "period", "count"],
        "required": ["identifier"],
        "items": {
            "profile": {"dataset": "profile"},
            "statements": {
                "dataset": "statements",
                "metrics": [
                    "revenue",
                    "net_income",
                    "assets",
                    "liabilities",
                    "operating_cash_flow",
                ],
                "allow_partial": True,
            },
            "analytics": {
                "dataset": "financial_analytics",
                "metrics": ["revenue_growth_yoy", "free_cash_flow", "free_cash_flow_margin"],
                "allow_partial": True,
            },
            "filings": {"dataset": "filings", "form": "all", "limit": 5},
        },
    },
    "us_macro_overview": {
        "description": "US inflation, employment, unemployment, Treasury and overnight rates.",
        "fields": ["year"],
        "required": [],
        "items": {
            "cpi": {"dataset": "economics", "series": "cpi", "limit": 1},
            "cpi_yoy": {"dataset": "macro_analytics", "series": "cpi_yoy", "limit": 1},
            "payrolls": {"dataset": "economics", "series": "payrolls", "limit": 1},
            "unemployment": {"dataset": "economics", "series": "unemployment", "limit": 1},
            "treasury_yields": {"dataset": "treasury_yields", "limit": 20},
            "effr": {"dataset": "reference_rates", "series": "effr", "limit": 1},
            "sofr": {"dataset": "reference_rates", "series": "sofr", "limit": 1},
        },
    },
    "fx_context": {
        "description": "An ECB reference currency pair with explicitly US inflation/rate context.",
        "fields": ["base_currency", "quote_currency", "year"],
        "required": ["base_currency", "quote_currency"],
        "items": {
            "fx": {"dataset": "fx_rates", "limit": 5},
            "us_cpi": {"dataset": "economics", "series": "cpi", "limit": 1},
            "us_treasury_yields": {"dataset": "treasury_yields", "limit": 20},
            "us_effr": {"dataset": "reference_rates", "series": "effr", "limit": 1},
            "us_sofr": {"dataset": "reference_rates", "series": "sofr", "limit": 1},
        },
    },
}
COMMON_FIELDS = {"preset", "include", "requirements", "requirements_by_item", "view", "preview"}


def preset_schema(schema: dict[str, Any]) -> None:
    branches = []
    for name, spec in PRESETS.items():
        properties = {
            key: deepcopy(schema["properties"][key])
            for key in sorted(COMMON_FIELDS | set(spec["fields"]))
        }
        properties["preset"] = {"const": name, "type": "string"}
        for key in spec["required"]:
            properties[key] = properties[key]["anyOf"][0]
        properties["include"]["anyOf"][0]["items"] = {"enum": list(spec["items"])}
        properties["include"]["anyOf"][0]["uniqueItems"] = True
        properties["requirements_by_item"]["propertyNames"] = {"enum": list(spec["items"])}
        branch = {
            "type": "object",
            "properties": properties,
            "required": ["preset", *spec["required"]],
            "additionalProperties": False,
        }
        if name == "fx_context":
            from atlas_terminal.macro_catalog import FX_CURRENCIES

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


class ResearchPresetQuery(Model):
    model_config = ConfigDict(extra="forbid", json_schema_extra=preset_schema)
    preset: PresetName
    identifier: str | None = Field(default=None, min_length=1, max_length=100)
    period: Literal["annual", "quarterly", "ytd"] = "quarterly"
    count: int = Field(default=1, ge=1, le=20, strict=True)
    year: int | None = Field(default=None, ge=1990, le=2100, strict=True)
    base_currency: Currency | None = None
    quote_currency: Currency | None = None
    include: list[str] | None = Field(default=None, min_length=1, max_length=10)
    requirements: ResearchRequirements | None = None
    requirements_by_item: dict[str, ResearchRequirements] = Field(
        default_factory=dict, max_length=10
    )
    view: Literal["full", "compact"] = "compact"
    preview: bool = Field(default=False, strict=True)

    @field_validator("identifier")
    @classmethod
    def issuer(cls, value: str | None) -> str | None:
        return identifier(value) if value is not None else None

    @model_validator(mode="after")
    def applicable_fields(self) -> "ResearchPresetQuery":
        spec = PRESETS[self.preset]
        if self.model_fields_set - (COMMON_FIELDS | set(spec["fields"])):
            raise ValueError("Parameter does not apply to this preset")
        if any(getattr(self, key) is None for key in spec["required"]):
            raise ValueError("Missing required preset parameter")
        if self.preset == "fx_context" and self.base_currency == self.quote_currency:
            raise ValueError("Choose distinct currencies")
        selected = self.include if self.include is not None else list(spec["items"])
        if len(set(selected)) != len(selected) or set(selected) - spec["items"].keys():
            raise ValueError("Choose unique item IDs from this preset")
        if set(self.requirements_by_item) - set(selected):
            raise ValueError("Per-item requirements must refer to selected items")
        return self


class PresetPlanItem(Model):
    id: str
    source_ids: list[str]
    query: dict[str, Any]


class ResearchPresetPlan(Model):
    preset: PresetName
    items: list[PresetPlanItem] = Field(min_length=1, max_length=10)
    excluded_items: list[str]
    year_basis: Literal["explicit", "current_utc_year"] | None
    execution: Literal["sequential_independent_reads"] = "sequential_independent_reads"


class TimeRange(Model):
    earliest: str | None = None
    latest: str | None = None


class ResearchItemOverview(Model):
    id: str
    dataset: str
    source_ids: list[str]
    cadence_and_limits: dict[str, str]
    availability: Literal["available", "partial", "unavailable", "error"]
    is_synthetic: bool | None = None
    returned_records: int | None = None
    usable_records: int | None = None
    units: list[str] = Field(default_factory=list)
    observation_dates: TimeRange = Field(default_factory=TimeRange)
    publication_times: TimeRange = Field(default_factory=TimeRange)
    publication_time_known_records: int = 0
    publication_time_unknown_records: int = 0
    source_retrieval_times: TimeRange = Field(default_factory=TimeRange)
    quality: ResearchQuality | None = None
    error_code: str | None = None


class ResearchPresetResult(Model):
    schema_version: Literal["0.2.0"] = "0.2.0"
    mode: Literal["preview", "executed"]
    status: Literal["preview", "ok", "partial", "error"]
    generated_at: str
    is_synthetic: bool
    upstream_data_cost: Literal["0"] = "0"
    requires_wallet: Literal[False] = False
    point_in_time: Literal[False] = False
    atomic_snapshot: Literal[False] = False
    source_text_is_untrusted: Literal[True] = True
    plan: ResearchPresetPlan
    overview: list[ResearchItemOverview]
    batch: ResearchBatchResult | None
    limitations: list[str]

    @model_validator(mode="after")
    def execution_matches_plan(self) -> "ResearchPresetResult":
        if self.mode == "preview":
            if self.status != "preview" or self.batch is not None or self.overview:
                raise ValueError("A preview cannot contain executed data")
        else:
            if self.batch is None or self.status != self.batch.status:
                raise ValueError("An executed preset must contain its batch status")
            planned = [item.id for item in self.plan.items]
            if planned != [item.id for item in self.batch.items] or planned != [
                item.id for item in self.overview
            ]:
                raise ValueError("Executed items and overview must match the plan order")
        return self


def parse_preset(data: ResearchPresetQuery | dict[str, Any]) -> ResearchPresetQuery:
    try:
        query = (
            data
            if isinstance(data, ResearchPresetQuery)
            else ResearchPresetQuery.model_validate(data)
        )
        if len(canonical(query)) > MAX_BATCH_REQUEST_BYTES:
            raise ValueError("Request exceeds bounds")
        return query
    except (ValidationError, TypeError, ValueError):
        raise AtlasError(
            "INVALID_INPUT",
            "Use a preset's parameters and unique item IDs from research catalog, within 16 KiB.",
            fields=["preset", "include", "requirements_by_item"],
        ) from None


def preset_plan(query: ResearchPresetQuery) -> ResearchPresetPlan:
    from atlas_terminal.research import DATASETS

    spec = PRESETS[query.preset]
    selected = query.include if query.include is not None else list(spec["items"])
    items = []
    year_basis: Literal["explicit", "current_utc_year"] | None = None
    for name in selected:
        data = deepcopy(spec["items"][name])
        if query.preset == "company_overview":
            data["identifier"] = query.identifier
            if data["dataset"] in {"statements", "financial_analytics"}:
                data.update(period=query.period, count=query.count)
        if data["dataset"] == "fx_rates":
            data.update(base_currency=query.base_currency, quote_currency=query.quote_currency)
        if data["dataset"] == "treasury_yields":
            data["year"] = query.year if query.year is not None else utcnow().year
            year_basis = "explicit" if query.year is not None else "current_utc_year"
        # Always include complete-result quality metadata, even in compact responses.
        requirements = (
            query.requirements.model_dump(exclude_unset=True) if query.requirements else {}
        )
        if name in query.requirements_by_item:
            requirements.update(query.requirements_by_item[name].model_dump(exclude_unset=True))
        data.update(requirements=requirements, response={"view": query.view})
        parse_research(data)
        items.append(PresetPlanItem(id=name, query=data, source_ids=[DATASETS[data["dataset"]][1]]))
    parse_batch({"items": [{"id": item.id, "query": item.query} for item in items]})
    return ResearchPresetPlan(
        preset=query.preset,
        items=items,
        excluded_items=[name for name in spec["items"] if name not in selected],
        year_basis=year_basis,
    )


def time_range(values: list[str]) -> TimeRange:
    return TimeRange(earliest=min(values, default=None), latest=max(values, default=None))


def item_overview(item: dict[str, Any], plan: PresetPlanItem) -> ResearchItemOverview:
    overview = ResearchItemOverview(
        id=plan.id,
        dataset=plan.query["dataset"],
        source_ids=plan.source_ids,
        availability="error",
        cadence_and_limits={name: str(SOURCE_CATALOG[name]["cadence"]) for name in plan.source_ids},
    )
    if item["status"] == "error":
        overview.error_code = item["error"]["code"]
        quality = item["error"].get("details", {}).get("quality")
        if quality:
            overview.quality = ResearchQuality.model_validate(quality)
        return overview
    result = item["result"]
    rows = list(leaf_records(result["records"]))
    usable = [row for row in rows if not missing(row, result["dataset"])]
    quality = ResearchQuality.model_validate(result["quality"])
    dates = [value for row in usable if (value := record_date(row, result["dataset"]))]
    publications = []
    for row in rows:
        value = row.get("published_at")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    publications.append(stamp(parsed.astimezone(UTC)))
            except ValueError:
                pass
    overview.availability = (
        "unavailable"
        if not usable
        else "partial"
        if len(usable) < len(rows) or quality.partial_coverage
        else "available"
    )
    overview.is_synthetic = result["is_synthetic"]
    overview.returned_records, overview.usable_records = len(rows), len(usable)
    overview.units = sorted({row["unit"] for row in rows if isinstance(row.get("unit"), str)})
    overview.observation_dates = time_range(dates)
    overview.publication_times = time_range(publications)
    overview.publication_time_known_records = len(publications)
    overview.publication_time_unknown_records = len(rows) - len(publications)
    overview.source_retrieval_times = TimeRange(
        earliest=quality.oldest_source_retrieved_at, latest=quality.newest_source_retrieved_at
    )
    overview.quality = quality
    return overview


def preset_catalog(*, include_schema: bool = True) -> dict[str, Any]:
    from atlas_terminal.research import DATASETS

    result: dict[str, Any] = {
        "tool": "atlas_get_research_preset",
        "http_path": "/v1/research/preset",
        "preview_field": "preview",
        "items": [
            {
                "id": name,
                "description": spec["description"],
                "query_fields": sorted(COMMON_FIELDS | set(spec["fields"])),
                "required_fields": ["preset", *spec["required"]],
                "items": [
                    {
                        "id": key,
                        "dataset": value["dataset"],
                        "source_id": DATASETS[value["dataset"]][1],
                    }
                    for key, value in spec["items"].items()
                ],
            }
            for name, spec in PRESETS.items()
        ],
    }
    if include_schema:
        result["query_schema"] = ResearchPresetQuery.model_json_schema()
        result["response_schema"] = ResearchPresetResult.model_json_schema()
    return result
