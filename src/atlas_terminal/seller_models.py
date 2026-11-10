"""Versioned seller product contracts, separate from free research and annual snapshots."""

from typing import Any, Literal

from pydantic import Field, field_validator

from atlas_terminal.models import Hash, Model, Pricing, Receipt
from atlas_terminal.public_sources import BLS_SERIES
from atlas_terminal.research_views import ResearchResult


class ProductQuery(Model):
    product_id: Literal["bls-series-v1"] = "bls-series-v1"
    series: str = Field(min_length=1, max_length=80)

    @field_validator("series")
    @classmethod
    def curated_series(cls, value: str) -> str:
        for key, spec in BLS_SERIES.items():
            if value in {key, spec[0]}:
                return key
        raise ValueError("Select a curated BLS series from the seller catalog")


class ProductData(Model):
    schema_version: Literal["seller-1.0"] = "seller-1.0"
    product_id: Literal["bls-series-v1"] = "bls-series-v1"
    query: ProductQuery
    title: str
    captured_at: str
    prepared_at: str
    source_access: Literal["source_response", "retained_capture", "fictional_fixture"]
    is_synthetic: bool
    attribution: str = "Source: U.S. Bureau of Labor Statistics. Normalized by Atlas Terminal."
    source_terms_url: str = "https://www.bls.gov/bls/linksite.htm"
    research: ResearchResult
    limitations: list[str]


class ProductSelection(Model):
    """Disclose the selected observations before delivering their values."""

    source_query: dict[str, Any]
    first_observation_date: str
    last_observation_date: str
    unit: str
    seasonally_adjusted: bool
    period_kinds: list[str]
    reported_records: int = Field(ge=1, le=130)
    missing_records: int = Field(ge=0, le=130)


def product_selection(data: ProductData) -> ProductSelection:
    rows = data.research.records
    dates = [row["date"] for row in rows]
    reported = sum(row["value"] is not None for row in rows)
    spec = BLS_SERIES[data.query.series]
    return ProductSelection(
        source_query=data.research.query,
        first_observation_date=min(dates),
        last_observation_date=max(dates),
        unit=spec[2],
        seasonally_adjusted=spec[3],
        period_kinds=sorted({row["period_kind"] for row in rows}),
        reported_records=reported,
        missing_records=len(rows) - reported,
    )


class ProductQuote(Model):
    schema_version: Literal["seller-1.0"] = "seller-1.0"
    quote_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    query: ProductQuery
    title: str
    snapshot_id: Hash
    result_sha256: Hash
    record_count: int = Field(ge=1, le=130)
    selection: ProductSelection
    captured_at: str
    source_access: Literal["source_response", "retained_capture", "fictional_fixture"]
    pricing: Pricing
    expires_at: str
    resource_path: str
    is_synthetic: bool
    limitations: list[str]


class ProductPurchase(Model):
    data: ProductData
    receipt: Receipt
