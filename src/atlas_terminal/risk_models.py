"""Bounded, versioned contracts for operator-supplied fundamental risk inputs."""

from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    model_validator,
)

from atlas_terminal.errors import AtlasError
from atlas_terminal.util import canonical

METHODOLOGY: Final = "atlas-us-fundamental-v1"
MAX_RISK_BYTES = 65536
MAX_RISK_SESSIONS = 640
STYLES = ("size", "value", "profitability", "leverage", "momentum", "beta", "residual_volatility")
Style = Literal[
    "size", "value", "profitability", "leverage", "momentum", "beta", "residual_volatility"
]
DEFAULT_STYLES: tuple[Style, ...] = ("size", "value", "profitability", "leverage")
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")]
RiskHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# Strings avoid JSON binary floats and bound both magnitude and precision before Decimal parsing.
Number = Annotated[str, Field(pattern=r"^-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?$")]


def timestamp(value: Any) -> datetime:
    """Accept ISO timestamps in both SDK Python validation and direct JSON validation."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value) <= 64:
        return datetime.fromisoformat(value)
    raise ValueError("Use a timezone-aware ISO timestamp")


RiskTimestamp = Annotated[AwareDatetime, BeforeValidator(timestamp)]


class RiskBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RiskSource(RiskBase):
    source_id: Identifier
    # References are provenance only. No risk operation ever fetches a supplied URL or path.
    uri: Annotated[str, Field(pattern=r"^https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9_./%-]*$")]
    raw_sha256: RiskHash
    retrieved_at: RiskTimestamp
    usage_basis: Literal["operator_supplied_rights_cleared"]


class RiskAsset(RiskBase):
    asset_id: Identifier
    country: Literal["US"] = "US"
    currency: Literal["USD"] = "USD"
    security_type: Literal["common_stock"] = "common_stock"


class RiskManifest(RiskBase):
    kind: Literal["manifest"]
    schema_version: Literal["0.1.0"] = "0.1.0"
    is_synthetic: bool
    universe: list[RiskAsset] = Field(min_length=30, max_length=200)
    sources: list[RiskSource] = Field(min_length=1, max_length=10)
    return_convention: Literal["split_and_dividend_adjusted_simple_total_return"]
    frequency: Literal["daily"] = "daily"
    currency: Literal["USD"] = "USD"

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        for values in (
            [row.asset_id for row in self.universe],
            [row.source_id for row in self.sources],
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate IDs")
        return self


class RiskFundamental(RiskBase):
    asset_id: Identifier
    period_end: RiskTimestamp
    available_at: RiskTimestamp
    source_id: Identifier
    locator: str = Field(min_length=1, max_length=160)
    sic: Annotated[str, Field(pattern=r"^[0-9]{4}$")]
    book_equity: Number
    total_assets: Number
    net_income_ttm: Number
    total_debt: Number

    @model_validator(mode="after")
    def valid_fundamental(self) -> Self:
        if self.period_end > self.available_at:
            raise ValueError("Fundamental predates its period end")
        if Decimal(self.total_assets) <= 0 or Decimal(self.total_debt) < 0:
            raise ValueError("Invalid balance sheet denominator or debt")
        return self


class RiskReturn(RiskBase):
    asset_id: Identifier
    total_return: Number
    market_cap: Number

    @model_validator(mode="after")
    def valid_return(self) -> Self:
        if not Decimal(-1) < Decimal(self.total_return) <= Decimal(10):
            raise ValueError("Return must exceed -1 and be at most 10")
        if Decimal(self.market_cap) <= 0:
            raise ValueError("Market capitalization must be positive")
        return self


class RiskSession(RiskBase):
    start_at: RiskTimestamp
    end_at: RiskTimestamp
    available_at: RiskTimestamp
    source_id: Identifier
    locator: str = Field(min_length=1, max_length=160)
    observations: list[RiskReturn] = Field(min_length=30, max_length=200)

    @model_validator(mode="after")
    def valid_session(self) -> Self:
        if not 0 < (self.end_at - self.start_at).total_seconds() <= 7 * 86400:
            raise ValueError("Use explicit daily session boundaries")
        if self.available_at < self.end_at:
            raise ValueError("Returns cannot be available before the period ends")
        ids = [row.asset_id for row in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate return IDs")
        return self


class RiskFundamentalChunk(RiskBase):
    kind: Literal["fundamentals"]
    previous: RiskHash
    records: list[RiskFundamental] = Field(min_length=1, max_length=40)


class RiskSessionChunk(RiskBase):
    kind: Literal["sessions"]
    previous: RiskHash
    records: list[RiskSession] = Field(min_length=1, max_length=10)


class RiskPanelInput(RiskBase):
    manifest: RiskManifest
    fundamentals: list[RiskFundamental] = Field(min_length=30, max_length=10000)
    sessions: list[RiskSession] = Field(min_length=1, max_length=MAX_RISK_SESSIONS)


RiskChunk = Annotated[
    RiskManifest | RiskFundamentalChunk | RiskSessionChunk, Field(discriminator="kind")
]


class RiskCatalogQuery(RiskBase):
    action: Literal["catalog"]


class RiskPutQuery(RiskBase):
    action: Literal["put"]
    chunk: RiskChunk


class RiskFitQuery(RiskBase):
    action: Literal["fit"]
    head_id: RiskHash
    as_of: RiskTimestamp
    lookback: int = Field(default=60, ge=60, le=252)
    styles: list[Style] = Field(
        default_factory=lambda: list(DEFAULT_STYLES),
        min_length=1,
        max_length=7,
    )
    decay: Number = "0.97"
    covariance_shrinkage: Number = "0.15"
    specific_shrinkage: Number = "0.20"

    @model_validator(mode="after")
    def valid_options(self) -> Self:
        if len(self.styles) != len(set(self.styles)):
            raise ValueError("Duplicate styles")
        if not Decimal("0.90") <= Decimal(self.decay) <= Decimal("0.999"):
            raise ValueError("Invalid EWMA decay")
        for value in (self.covariance_shrinkage, self.specific_shrinkage):
            if not Decimal(0) <= Decimal(value) <= Decimal(1):
                raise ValueError("Invalid shrinkage")
        return self


class RiskGetQuery(RiskBase):
    action: Literal["get"]
    model_id: RiskHash
    view: Literal["summary", "factors", "assets", "history"] = "summary"
    asset_ids: list[Identifier] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        if (self.view == "assets") != bool(self.asset_ids):
            raise ValueError("Select 1 through 20 asset IDs only for the assets view")
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("Duplicate IDs")
        return self


class RiskPosition(RiskBase):
    asset_id: Identifier
    weight: Number


class RiskShock(RiskBase):
    factor: str = Field(min_length=1, max_length=64)
    return_shock: Number


class RiskAnalyzeQuery(RiskBase):
    action: Literal["analyze"]
    model_id: RiskHash
    positions: list[RiskPosition] = Field(min_length=1, max_length=200)
    benchmark: list[RiskPosition] = Field(default_factory=list, max_length=200)
    shocks: list[RiskShock] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_portfolio(self) -> Self:
        for positions in (self.positions, self.benchmark):
            ids = [p.asset_id for p in positions]
            if len(ids) != len(set(ids)) or sum(abs(Decimal(p.weight)) for p in positions) > 10:
                raise ValueError("Duplicate positions or gross weight above 10")
        factors = [s.factor for s in self.shocks]
        if len(factors) != len(set(factors)):
            raise ValueError("Duplicate shocks")
        if any(abs(Decimal(s.return_shock)) > 1 for s in self.shocks):
            raise ValueError("Bound shocks to absolute simple returns of at most 1")
        return self


class RiskValidateQuery(RiskBase):
    action: Literal["validate"]
    model_id: RiskHash
    head_id: RiskHash
    positions: list[RiskPosition] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bounded_portfolio(self) -> Self:
        RiskAnalyzeQuery(action="analyze", model_id=self.model_id, positions=self.positions)
        return self


class RiskQuery(
    RootModel[
        Annotated[
            RiskCatalogQuery
            | RiskPutQuery
            | RiskFitQuery
            | RiskGetQuery
            | RiskAnalyzeQuery
            | RiskValidateQuery,
            Field(discriminator="action"),
        ]
    ]
):
    pass


class RiskResult(BaseModel):
    """Compact operation-specific data; large saved panels are never echoed by default."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["0.1.0"] = "0.1.0"
    methodology_version: Literal["atlas-us-fundamental-v1"] = METHODOLOGY
    action: Literal["catalog", "put", "fit", "get", "analyze", "validate"]
    is_synthetic: bool | None
    data: dict[str, Any]


def parse_risk(value: RiskQuery | dict[str, Any]) -> RiskQuery:
    try:
        if len(canonical(value)) > MAX_RISK_BYTES:
            raise ValueError("Request too large")
        # Validate JSON: strict strings/integers with ISO timezone-aware timestamps.
        with localcontext(Context(prec=42)):
            return RiskQuery.model_validate_json(canonical(value))
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise AtlasError(
            "INVALID_INPUT", "Invalid risk request; use the risk catalog and chunks below 64 KiB."
        ) from None


def utc_key(value: datetime) -> str:
    from datetime import UTC

    return value.astimezone(UTC).isoformat()
