"""Inspectable research recipes using the existing bounded batch service."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.macro_catalog import CURVE_SERIES, MACRO_SERIES, Currency
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

PresetName = Literal[
    "company_overview",
    "us_macro_overview",
    "fx_context",
    "japan_macro_overview",
    "us_japan_overview",
    "us_bond_context",
    "japan_bond_context",
    "us_japan_bond_context",
    "euro_area_bond_context",
    "australia_bond_context",
    "global_bond_context",
    "japan_activity_overview",
    "us_activity_overview",
    "euro_area_macro_overview",
    "uk_macro_overview",
    "global_macro_overview",
]
PRESETS: dict[str, dict[str, Any]] = {
    "us_bond_context": {
        "description": (
            "US nominal/real curves, slopes, butterfly, inflation-compensation proxy and changes."
        ),
        "fields": ["year"],
        "required": [],
        "items": {
            "nominal_curve": {"dataset": "treasury_yields", "limit": 14},
            "real_curve": {"dataset": "treasury_real_yields", "limit": 5},
            "slope_2s10s": {
                "dataset": "macro_analytics",
                "series": "yield_spread_10y_2y",
                "limit": 1,
            },
            "slope_3m10y": {
                "dataset": "macro_analytics",
                "series": "yield_spread_10y_3m",
                "limit": 1,
            },
            "slope_5s30s": {
                "dataset": "macro_analytics",
                "series": "yield_spread_30y_5y",
                "limit": 1,
            },
            "butterfly": {
                "dataset": "macro_analytics",
                "series": "yield_butterfly_2y_5y_10y",
                "limit": 1,
            },
            "inflation_compensation_10y": {
                "dataset": "macro_analytics",
                "series": "par_inflation_compensation_10y",
                "limit": 1,
            },
            "nominal_change_10y": {
                "dataset": "macro_analytics",
                "series": "yield_change_10y",
                "limit": 1,
            },
            "real_change_10y": {
                "dataset": "macro_analytics",
                "series": "real_yield_change_10y",
                "limit": 1,
            },
            "sofr": {"dataset": "reference_rates", "series": "sofr", "limit": 1},
        },
    },
    "japan_bond_context": {
        "description": "Japan JGB curve, slopes, butterfly and changes with BOJ funding and CPI.",
        "fields": ["year"],
        "required": [],
        "items": {
            "jgb_curve": {"dataset": "jgb_yields", "limit": 15},
            "slope_2s10s": {
                "dataset": "macro_analytics",
                "series": "jgb_spread_10y_2y",
                "limit": 1,
            },
            "slope_10s30s": {
                "dataset": "macro_analytics",
                "series": "jgb_spread_30y_10y",
                "limit": 1,
            },
            "slope_30s40s": {
                "dataset": "macro_analytics",
                "series": "jgb_spread_40y_30y",
                "limit": 1,
            },
            "butterfly": {
                "dataset": "macro_analytics",
                "series": "jgb_butterfly_2y_5y_10y",
                "limit": 1,
            },
            "yield_change_10y": {
                "dataset": "macro_analytics",
                "series": "jgb_yield_change_10y",
                "limit": 1,
            },
            "call_rate": {"dataset": "japan_economics", "series": "call_rate", "limit": 1},
            "cpi": {"dataset": "japan_economics", "series": "cpi", "limit": 1},
        },
    },
    "us_japan_bond_context": {
        "description": (
            "US/Japan government curves and changes with US real-yield gap, funding and USD/JPY."
        ),
        "fields": ["year"],
        "required": [],
        "items": {
            "us_curve": {"dataset": "treasury_yields", "limit": 14},
            "jgb_curve": {"dataset": "jgb_yields", "limit": 15},
            "us_slope_2s10s": {
                "dataset": "macro_analytics",
                "series": "yield_spread_10y_2y",
                "limit": 1,
            },
            "jp_slope_2s10s": {
                "dataset": "macro_analytics",
                "series": "jgb_spread_10y_2y",
                "limit": 1,
            },
            "us_change_10y": {
                "dataset": "macro_analytics",
                "series": "yield_change_10y",
                "limit": 1,
            },
            "jp_change_10y": {
                "dataset": "macro_analytics",
                "series": "jgb_yield_change_10y",
                "limit": 1,
            },
            "us_inflation_compensation_10y": {
                "dataset": "macro_analytics",
                "series": "par_inflation_compensation_10y",
                "limit": 1,
            },
            "sofr": {"dataset": "reference_rates", "series": "sofr", "limit": 1},
            "jp_call_rate": {"dataset": "japan_economics", "series": "call_rate", "limit": 1},
            "usd_jpy": {
                "dataset": "fx_rates",
                "base_currency": "USD",
                "quote_currency": "JPY",
                "limit": 1,
            },
        },
    },
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
    "japan_macro_overview": {
        "description": (
            "Japan national prices and unemployment, BOJ overnight rates, Tankan and money stock."
        ),
        "fields": ["start_date", "end_date"],
        "required": [],
        "items": {
            "cpi": {"dataset": "japan_economics", "series": "cpi", "limit": 1},
            "unemployment": {"dataset": "japan_economics", "series": "unemployment", "limit": 1},
            "call_rate": {"dataset": "japan_economics", "series": "call_rate", "limit": 1},
            "tankan": {"dataset": "japan_economics", "series": "tankan_manufacturing", "limit": 1},
            "money_stock_m2": {
                "dataset": "japan_economics",
                "series": "money_stock_m2",
                "limit": 1,
            },
            "usd_jpy_tokyo": {"dataset": "japan_economics", "series": "usd_jpy_tokyo", "limit": 1},
        },
    },
    "us_japan_overview": {
        "description": (
            "US/Japan inflation and employment, US GDP/PCE, Japanese rates and "
            "ECB USD/JPY; BEA requires a free key."
        ),
        "fields": ["start_date", "end_date"],
        "required": [],
        "items": {
            "us_cpi": {"dataset": "economics", "series": "cpi", "limit": 1},
            "us_unemployment": {"dataset": "economics", "series": "unemployment", "limit": 1},
            "us_gdp": {"dataset": "national_accounts", "series": "gdp_real", "limit": 1},
            "us_pce": {"dataset": "national_accounts", "series": "pce_price_index", "limit": 1},
            "us_core_pce": {
                "dataset": "national_accounts",
                "series": "core_pce_price_index",
                "limit": 1,
            },
            "jp_cpi": {"dataset": "japan_economics", "series": "cpi", "limit": 1},
            "jp_unemployment": {"dataset": "japan_economics", "series": "unemployment", "limit": 1},
            "jp_call_rate": {"dataset": "japan_economics", "series": "call_rate", "limit": 1},
            "jp_tankan": {
                "dataset": "japan_economics",
                "series": "tankan_manufacturing",
                "limit": 1,
            },
            "fx": {
                "dataset": "fx_rates",
                "base_currency": "USD",
                "quote_currency": "JPY",
                "limit": 1,
            },
        },
    },
}
PRESETS.update(
    {
        "japan_activity_overview": {
            "description": (
                "Japan GDP, production, consumption, real wages, jobs and housing; independent "
                "observation dates."
            ),
            "fields": ["start_date", "end_date"],
            "required": [],
            "items": {
                key: {"dataset": "japan_economics", "series": key, "limit": 1}
                for key in (
                    "gdp_real",
                    "gdp_nominal",
                    "industrial_production",
                    "industrial_inventories",
                    "retail_sales",
                    "household_consumption_real",
                    "real_wages",
                    "job_offers_ratio",
                    "housing_starts",
                    "core_cpi",
                )
            },
        },
        "us_activity_overview": {
            "description": (
                "US real GDP demand components, real consumption, saving and labor/producer "
                "prices; BEA requires a free key."
            ),
            "fields": ["start_date", "end_date"],
            "required": [],
            "items": {
                **{
                    key: {"dataset": "national_accounts", "series": key, "limit": 1}
                    for key in (
                        "gdp_real",
                        "gdp_consumption_real",
                        "business_investment_real",
                        "residential_investment_real",
                        "exports_real",
                        "imports_real",
                        "pce_real",
                        "personal_saving_rate",
                    )
                },
                "ppi": {"dataset": "economics", "series": "ppi_final_demand", "limit": 1},
                "weekly_hours": {
                    "dataset": "economics",
                    "series": "average_weekly_hours",
                    "limit": 1,
                },
            },
        },
        "euro_area_macro_overview": {
            "description": (
                "Eurostat euro-area GDP, HICP, labor, industry and retail with EUR/USD; inspect "
                "original geography labels."
            ),
            "fields": ["start_date", "end_date"],
            "required": [],
            "items": {
                **{
                    key: {"dataset": "europe_economics", "series": "ea_" + key, "limit": 1}
                    for key in (
                        "gdp_real",
                        "gdp_nominal",
                        "gdp_growth",
                        "hicp",
                        "hicp_yoy",
                        "unemployment",
                        "industrial_production",
                        "retail_sales",
                    )
                },
                "eur_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "EUR",
                    "quote_currency": "USD",
                    "limit": 1,
                },
            },
        },
        "uk_macro_overview": {
            "description": "ONS UK CPI, GDP and three-month rolling unemployment, plus GBP/USD.",
            "fields": ["start_date", "end_date"],
            "required": [],
            "items": {
                **{
                    key: {"dataset": "uk_economics", "series": key, "limit": 1}
                    for key in ("cpi", "cpi_yoy", "gdp_real", "gdp_growth", "unemployment")
                },
                "gbp_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "GBP",
                    "quote_currency": "USD",
                    "limit": 1,
                },
            },
        },
        "global_macro_overview": {
            "description": (
                "US, Japan, euro-area and UK GDP/prices plus German and French GDP; dates and "
                "definitions are independent."
            ),
            "fields": ["start_date", "end_date"],
            "required": [],
            "items": {
                "us_gdp": {"dataset": "national_accounts", "series": "gdp_real", "limit": 1},
                "us_cpi": {"dataset": "economics", "series": "cpi", "limit": 1},
                "jp_gdp": {"dataset": "japan_economics", "series": "gdp_real", "limit": 1},
                "jp_cpi": {"dataset": "japan_economics", "series": "cpi", "limit": 1},
                "ea_gdp": {"dataset": "europe_economics", "series": "ea_gdp_real", "limit": 1},
                "ea_hicp": {"dataset": "europe_economics", "series": "ea_hicp_yoy", "limit": 1},
                "gb_gdp": {"dataset": "uk_economics", "series": "gdp_real", "limit": 1},
                "gb_cpi": {"dataset": "uk_economics", "series": "cpi", "limit": 1},
                "de_gdp": {"dataset": "europe_economics", "series": "de_gdp_growth", "limit": 1},
                "fr_gdp": {"dataset": "europe_economics", "series": "fr_gdp_growth", "limit": 1},
            },
        },
    }
)
PRESETS.update(
    {
        "euro_area_bond_context": {
            "description": "ECB AAA/all-rating spot curves, local shapes/changes and EUR/USD.",
            "fields": ["year"],
            "required": [],
            "items": {
                "aaa_curve": {"dataset": "euro_area_yields", "series": "aaa_spot", "limit": 11},
                "all_curve": {"dataset": "euro_area_yields", "series": "all_spot", "limit": 11},
                "aaa_slope_2s10s": {
                    "dataset": "macro_analytics",
                    "series": "euro_aaa_spread_10y_2y",
                    "limit": 1,
                },
                "aaa_slope_5s30s": {
                    "dataset": "macro_analytics",
                    "series": "euro_aaa_spread_30y_5y",
                    "limit": 1,
                },
                "aaa_butterfly": {
                    "dataset": "macro_analytics",
                    "series": "euro_aaa_butterfly_2y_5y_10y",
                    "limit": 1,
                },
                "aaa_change_10y": {
                    "dataset": "macro_analytics",
                    "series": "euro_aaa_yield_change_10y",
                    "limit": 1,
                },
                "all_change_10y": {
                    "dataset": "macro_analytics",
                    "series": "euro_all_yield_change_10y",
                    "limit": 1,
                },
                "eur_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "EUR",
                    "quote_currency": "USD",
                    "limit": 1,
                },
            },
        },
        "australia_bond_context": {
            "description": "RBA government yields, local shapes/changes and AUD/USD.",
            "fields": ["year"],
            "required": [],
            "items": {
                "government_curve": {"dataset": "australia_yields", "limit": 4},
                "slope_2s10s": {
                    "dataset": "macro_analytics",
                    "series": "australia_spread_10y_2y",
                    "limit": 1,
                },
                "slope_3s10s": {
                    "dataset": "macro_analytics",
                    "series": "australia_spread_10y_3y",
                    "limit": 1,
                },
                "butterfly": {
                    "dataset": "macro_analytics",
                    "series": "australia_butterfly_2y_5y_10y",
                    "limit": 1,
                },
                "change_10y": {
                    "dataset": "macro_analytics",
                    "series": "australia_yield_change_10y",
                    "limit": 1,
                },
                "change_2y": {
                    "dataset": "macro_analytics",
                    "series": "australia_yield_change_2y",
                    "limit": 1,
                },
                "aud_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "AUD",
                    "quote_currency": "USD",
                    "limit": 1,
                },
            },
        },
        "global_bond_context": {
            "description": "Four-region government curves and reference FX.",
            "fields": ["year"],
            "required": [],
            "items": {
                "us_nominal_curve": {"dataset": "treasury_yields", "limit": 14},
                "us_real_curve": {"dataset": "treasury_real_yields", "limit": 5},
                "jgb_curve": {"dataset": "jgb_yields", "limit": 15},
                "euro_aaa_curve": {
                    "dataset": "euro_area_yields",
                    "series": "aaa_spot",
                    "limit": 11,
                },
                "euro_all_curve": {
                    "dataset": "euro_area_yields",
                    "series": "all_spot",
                    "limit": 11,
                },
                "australia_curve": {"dataset": "australia_yields", "limit": 4},
                "usd_jpy": {
                    "dataset": "fx_rates",
                    "base_currency": "USD",
                    "quote_currency": "JPY",
                    "limit": 1,
                },
                "eur_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "EUR",
                    "quote_currency": "USD",
                    "limit": 1,
                },
                "aud_usd": {
                    "dataset": "fx_rates",
                    "base_currency": "AUD",
                    "quote_currency": "USD",
                    "limit": 1,
                },
                "sofr": {"dataset": "reference_rates", "series": "sofr", "limit": 1},
            },
        },
    }
)
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
        if "start_date" in spec["fields"]:
            branch["dependentRequired"] = {"start_date": ["end_date"], "end_date": ["start_date"]}
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
    start_date: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    end_date: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
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
        if self.start_date is not None or self.end_date is not None:
            parse_research(
                {
                    "dataset": "economics",
                    "series": "cpi",
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                }
            )
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
        if query.start_date is not None:
            data.update(start_date=query.start_date, end_date=query.end_date)
        if query.preset == "company_overview":
            data["identifier"] = query.identifier
            if data["dataset"] in {"statements", "financial_analytics"}:
                data.update(period=query.period, count=query.count)
        if data["dataset"] == "fx_rates" and query.preset == "fx_context":
            data.update(base_currency=query.base_currency, quote_currency=query.quote_currency)
        if data["dataset"] in {
            "treasury_yields",
            "treasury_real_yields",
            "jgb_yields",
            "euro_area_yields",
            "australia_yields",
        } or (data["dataset"] == "macro_analytics" and data["series"] in CURVE_SERIES):
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
        source_id = DATASETS[data["dataset"]][1]
        if data["dataset"] == "japan_economics":
            from atlas_terminal.economic_catalog import JAPAN_SERIES

            source_id = JAPAN_SERIES[data["series"]]["source_id"]
        if data["dataset"] == "macro_analytics":
            source_id = MACRO_SERIES[data["series"]]["source_id"]
        items.append(PresetPlanItem(id=name, query=data, source_ids=[source_id]))
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
