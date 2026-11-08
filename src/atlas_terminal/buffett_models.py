"""Bounded, declared annual fundamentals and explicit valuation scenarios."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from atlas_terminal.models import Model, identifier

Amount = Annotated[str, Field(pattern=r"^-?(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?$", strict=True)]
ScoreMetric = Literal[
    "revenue",
    "net_income",
    "operating_income",
    "operating_cash_flow",
    "capital_expenditure",
    "stockholders_equity",
    "assets",
    "liabilities",
    "interest_expense",
    "stock_based_compensation",
]
METRICS: list[ScoreMetric] = list(get_args(ScoreMetric))


def calendar_day(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or not 1900 <= parsed.year <= 2100:
        raise ValueError("Use an ISO calendar date from 1900 through 2100")
    return value


class ScoreYear(Model):
    start: str
    end: str
    available_on: str
    values: dict[ScoreMetric, Amount | None] = Field(max_length=len(METRICS))

    @field_validator("start", "end", "available_on")
    @classmethod
    def day(cls, value: str) -> str:
        return calendar_day(value)

    @model_validator(mode="after")
    def annual(self) -> "ScoreYear":
        if not 330 <= (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days <= 400:
            raise ValueError("Each supplied period must be a reported 330-400-day annual period")
        if self.available_on < self.end:
            raise ValueError("Annual financials cannot be available before their period end")
        return self


class ScoreFinancials(Model):
    is_synthetic: bool = Field(strict=True)
    identifier: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    business_type: Literal["nonfinancial", "financial", "unspecified"]
    captured_at: str = Field(max_length=64)
    original_source_url: str = Field(max_length=2048)
    annual: list[ScoreYear] = Field(min_length=1, max_length=11)

    @field_validator("identifier")
    @classmethod
    def identity(cls, value: str) -> str:
        return identifier(value)

    @field_validator("original_source_url")
    @classmethod
    def public_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTPS attribution URL without credentials, query or fragment")
        return value

    @model_validator(mode="after")
    def chronological(self) -> "ScoreFinancials":
        captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None or captured > datetime.now(UTC):
            raise ValueError("Capture time needs a timezone and cannot be in the future")
        ordered = sorted(self.annual, key=lambda row: row.end)
        if len({row.end for row in ordered}) != len(ordered) or any(
            right.start <= left.end for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("Annual periods must be distinct and nonoverlapping")
        if any(row.available_on > captured.date().isoformat() for row in ordered):
            raise ValueError("Source availability cannot follow its declared capture date")
        return self


class ScoreValuation(Model):
    is_synthetic: bool = Field(strict=True)
    market_cap: Amount
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observed_on: str
    observed_at: str | None = Field(default=None, max_length=64)
    captured_at: str = Field(max_length=64)
    growth_rate: Amount = "0.03"
    discount_rate: Amount = "0.10"
    terminal_growth_rate: Amount = "0.02"
    projection_years: int = Field(default=10, ge=1, le=20, strict=True)

    @field_validator("observed_on")
    @classmethod
    def day(cls, value: str) -> str:
        return calendar_day(value)

    @model_validator(mode="after")
    def scenario(self) -> "ScoreValuation":
        captured = datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        if (
            captured.tzinfo is None
            or captured > datetime.now(UTC)
            or (self.observed_at is None and captured.date().isoformat() < self.observed_on)
        ):
            raise ValueError("Valuation capture needs a timezone and cannot precede its quote date")
        if self.observed_at is not None:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
            if (
                observed.tzinfo is None
                or observed > captured
                or observed.date().isoformat() != self.observed_on
            ):
                raise ValueError(
                    "Quote timestamp needs a timezone, matching date and later capture"
                )
        if not 0 < Decimal(self.market_cap) <= Decimal("1e24"):
            raise ValueError("Market capitalization must be a positive whole-equity amount")
        g, r, terminal = (
            Decimal(value)
            for value in (
                self.growth_rate,
                self.discount_rate,
                self.terminal_growth_rate,
            )
        )
        if not (
            Decimal("-0.10") <= g <= Decimal("0.10")
            and Decimal("0.01") <= r <= Decimal("0.30")
            and Decimal("-0.05") <= terminal <= Decimal("0.05")
            and r > terminal
        ):
            raise ValueError("Use bounded scenario rates with discount above terminal growth")
        return self
