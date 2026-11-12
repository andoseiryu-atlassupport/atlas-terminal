"""Saved benchmark selection and explicitly dated, supplied portfolio NAV contracts."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from atlas_terminal.benchmark_catalog import BenchmarkCurrency, BenchmarkId, DividendTreatment
from atlas_terminal.models import Model


class BenchmarkSettings(Model):
    """Legacy selections without benchmark fields retain TOPIX/JPY/price-return semantics."""

    benchmark_id: BenchmarkId = "topix"
    benchmark_currency: BenchmarkCurrency = "JPY"
    dividend_treatment: DividendTreatment = "price_return"
    alignment: Literal["utc_day_end"] = "utc_day_end"


class PortfolioObservation(Model):
    date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    value: str | None = Field(default=None, max_length=80, pattern=r"^[0-9]+(?:\.[0-9]+)?$")

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not 1900 <= date.fromisoformat(value).year <= 2100:
            raise ValueError("Portfolio dates must be in 1900–2100")
        return value

    @field_validator("value")
    @classmethod
    def positive_value(cls, value: str | None) -> str | None:
        if value is not None:
            number = Decimal(value)
            if not number.is_finite() or not Decimal("1e-30") <= number <= Decimal("1e30"):
                raise ValueError("NAV must be a positive finite decimal string within bounds")
        return value


class BenchmarkPortfolio(Model):
    currency: BenchmarkCurrency
    dividend_treatment: DividendTreatment
    valuation_basis: Literal["utc_day_end"]
    value_basis: Literal["unitized_nav_external_flows_removed"]
    observations: list[PortfolioObservation] = Field(min_length=2, max_length=500)
    risk_free_rate_annual: str | None = Field(
        default=None, max_length=40, pattern=r"^-?[0-9]+(?:\.[0-9]+)?$"
    )
    sessions_per_year: int = Field(default=252, ge=1, le=366, strict=True)

    @field_validator("risk_free_rate_annual")
    @classmethod
    def risk_free_rate(cls, value: str | None) -> str | None:
        if value is not None:
            number = Decimal(value)
            if not number.is_finite() or not Decimal("-1") < number <= Decimal("1"):
                raise ValueError("Risk-free rate is an annual effective ratio in (-1,1]")
        return value

    @model_validator(mode="after")
    def ordered(self) -> "BenchmarkPortfolio":
        days = [row.date for row in self.observations]
        if days != sorted(set(days)):
            raise ValueError("Portfolio dates must be unique and ascending")
        return self
