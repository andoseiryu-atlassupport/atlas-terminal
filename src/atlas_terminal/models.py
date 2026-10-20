"""Atlas domain contracts. These are deliberately separate from x402 wire models."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from atlas_terminal.errors import AtlasError

Metric = Literal["revenue", "net_income", "assets", "liabilities", "operating_cash_flow"]
METRICS = ("revenue", "net_income", "assets", "liabilities", "operating_cash_flow")
Atomic = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$", max_length=40)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def identifier(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[\w][\w .&(),'\-]{0,99}", value, flags=re.ASCII):
        raise AtlasError(
            "INVALID_INPUT", "Use a ticker, CIK or company name, not a URL or command."
        )
    return value


class FinancialQuery(Model):
    identifier: str = Field(min_length=1, max_length=100)
    metrics: list[Metric] = Field(min_length=1, max_length=5)
    period: Literal["annual"] = "annual"
    count: int = Field(default=3, ge=1, le=20, strict=True)
    allow_partial: bool = Field(default=False, strict=True)

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return identifier(value)

    @field_validator("metrics")
    @classmethod
    def unique_metrics(cls, value: list[Metric]) -> list[Metric]:
        if len(set(value)) != len(value):
            raise ValueError("metrics must be unique")
        return value


def parse_query(data: dict[str, Any]) -> FinancialQuery:
    if {"as_of", "published_before", "pit", "point_in_time"} & data.keys():
        raise AtlasError(
            "UNSUPPORTED_HISTORICAL_QUERY",
            "Current snapshots cannot establish historical availability.",
        )
    if data.get("period", "annual") != "annual":
        raise AtlasError("UNSUPPORTED_PERIOD", "Only annual periods are supported.")
    if isinstance(data.get("metrics"), list) and any(x not in METRICS for x in data["metrics"]):
        raise AtlasError("UNSUPPORTED_METRIC", "Supported metrics are listed in the catalog.")
    try:
        return FinancialQuery.model_validate(data)
    except ValidationError:
        raise AtlasError(
            "INVALID_INPUT", "Invalid financial query; check types, fields and limits."
        ) from None


class Company(Model):
    cik: str = Field(pattern=r"^[0-9]{10}$")
    name: str
    tickers: list[str]


class Source(Model):
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: Literal["10-K", "10-K/A"]
    filed_on: str
    accepted_at: str | None
    taxonomy: Literal["us-gaap"] = "us-gaap"
    tag: str
    source_url: str
    raw_source_url: str
    json_pointer: str
    raw_sha256: Hash
    retrieved_at: str


class Period(Model):
    kind: Literal["duration", "instant"]
    start: str | None
    end: str


class Fact(Model):
    metric: Metric
    value: str | None
    status: Literal["reported", "unavailable", "ambiguous"]
    unit: Literal["USD"] = "USD"
    period: Period
    reason: str | None
    source: Source | None


class Coverage(Model):
    requested_periods: int
    available_periods: int
    requested_cells: int
    reported_cells: int


class Warning(Model):
    code: str
    message: str


class FinancialResult(Model):
    schema_version: Literal["0.1.0"] = "0.1.0"
    kind: Literal["financials"] = "financials"
    company: Company
    query: FinancialQuery
    provider: Literal["fixture", "sec"]
    selection_policy: Literal["latest_primary_filing_per_fact_v1"] = (
        "latest_primary_filing_per_fact_v1"
    )
    period_ends: list[str]
    facts: list[Fact]
    coverage: Coverage
    partial: bool
    warnings: list[Warning]
    limitations: list[str]
    retrieved_at: str
    is_synthetic: bool


class Pricing(Model):
    mode: Literal["off", "mock", "testnet"]
    scheme: Literal["none", "exact"]
    network: str
    asset: str
    pay_to: str
    symbol: str
    decimals: int = Field(ge=0, le=18)
    amount_atomic: Atomic
    display_amount: str


class Missing(Model):
    metric: str
    period_end: str
    reason: str


class Quote(Model):
    schema_version: Literal["0.1.0"] = "0.1.0"
    quote_id: str
    query: FinancialQuery
    company: Company
    snapshot_id: str
    result_sha256: Hash
    period_ends: list[str]
    coverage: Coverage
    partial: bool
    missing: list[Missing]
    pricing: Pricing
    expires_at: str
    resource_path: str
    limitations: list[str]
    is_synthetic: bool


class Receipt(Model):
    quote_id: str
    mode: Literal["off", "mock", "testnet"]
    simulated: bool
    status: Literal["NOT_REQUIRED", "SETTLED"]
    network: str
    asset: str
    amount_atomic: Atomic
    transaction: str | None
    settled_at: str | None
    result_sha256: Hash
    retained_until: str


class PurchaseResult(Model):
    data: FinancialResult
    receipt: Receipt
