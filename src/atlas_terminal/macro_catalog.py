"""Bounded macro series and explicit FX quotation conventions."""

from typing import Literal, TypedDict, get_args

Currency = Literal["EUR", "USD", "JPY", "GBP", "CHF", "AUD", "CAD", "NZD", "CNY"]
FX_CURRENCIES = get_args(Currency)
REFERENCE_SERIES = {
    "effr": {"type": "EFFR", "rate_type": "overnight_unsecured"},
    "sofr": {"type": "SOFR", "rate_type": "overnight_secured"},
}


class MacroSpec(TypedDict):
    source_id: str
    source_series: str
    lag_months: int
    formula: str
    unit: str


MACRO_SERIES: dict[str, MacroSpec] = {
    "cpi_yoy": {
        "source_id": "bls",
        "source_series": "cpi",
        "lag_months": 12,
        "formula": "(current - prior_year) / prior_year",
        "unit": "ratio",
    },
    "core_cpi_yoy": {
        "source_id": "bls",
        "source_series": "core_cpi",
        "lag_months": 12,
        "formula": "(current - prior_year) / prior_year",
        "unit": "ratio",
    },
    "payrolls_change_mom": {
        "source_id": "bls",
        "source_series": "payrolls",
        "lag_months": 1,
        "formula": "current - previous_month",
        "unit": "thousand_persons",
    },
    "yield_spread_10y_2y": {
        "source_id": "treasury",
        "source_series": "treasury_yields",
        "lag_months": 0,
        "formula": "(10YEAR - 2YEAR) * 100",
        "unit": "basis_points",
    },
}
