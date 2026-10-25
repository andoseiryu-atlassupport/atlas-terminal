"""Bounded macro series and explicit FX quotation conventions."""

from typing import Literal, NotRequired, TypedDict, cast, get_args

Currency = Literal[
    "EUR",
    "USD",
    "JPY",
    "GBP",
    "CHF",
    "AUD",
    "CAD",
    "NZD",
    "CNY",
    "CZK",
    "DKK",
    "HUF",
    "NOK",
    "PLN",
    "SEK",
    "ISK",
    "TRY",
    "BRL",
    "HKD",
    "IDR",
    "ILS",
    "INR",
    "KRW",
    "MXN",
    "MYR",
    "PHP",
    "SGD",
    "THB",
    "ZAR",
    "RON",
    "BGN",
]
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
    title: NotRequired[str]
    country: NotRequired[str]


CurveName = Literal["nominal", "real", "jgb", "ecb_aaa", "ecb_all", "rba"]


class CurveTerm(TypedDict):
    curve: CurveName
    maturity: str
    coefficient: int
    role: str


class CurveSpec(TypedDict):
    terms: list[CurveTerm]
    previous_observation: bool
    interpretation: str


CURVE_SERIES: dict[str, CurveSpec] = {}


class CurveMarket(TypedDict):
    source_id: str
    dataset: str
    country: str
    currency: str
    label: str


CURVE_MARKETS: dict[str, CurveMarket] = {
    "nominal": {
        "source_id": "treasury",
        "dataset": "treasury_yields",
        "country": "US",
        "currency": "USD",
        "label": "US nominal",
    },
    "real": {
        "source_id": "treasury",
        "dataset": "treasury_real_yields",
        "country": "US",
        "currency": "USD",
        "label": "US real",
    },
    "jgb": {
        "source_id": "japan_mof",
        "dataset": "jgb_yields",
        "country": "JP",
        "currency": "JPY",
        "label": "Japanese",
    },
    "ecb_aaa": {
        "source_id": "ecb",
        "dataset": "euro_area_yields",
        "country": "EA",
        "currency": "EUR",
        "label": "Euro area AAA spot",
    },
    "ecb_all": {
        "source_id": "ecb",
        "dataset": "euro_area_yields",
        "country": "EA",
        "currency": "EUR",
        "label": "Euro area all-rating spot",
    },
    "rba": {
        "source_id": "rba",
        "dataset": "australia_yields",
        "country": "AU",
        "currency": "AUD",
        "label": "Australian government",
    },
}


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
}


def register_curve_series(
    name: str,
    title: str,
    terms: list[CurveTerm],
    formula: str,
    *,
    previous_observation: bool = False,
    interpretation: str = "Same-date constant-maturity yield shape, in basis points.",
) -> None:
    market = CURVE_MARKETS[terms[0]["curve"]]
    CURVE_SERIES[name] = {
        "terms": terms,
        "previous_observation": previous_observation,
        "interpretation": interpretation,
    }
    MACRO_SERIES[name] = {
        "source_id": market["source_id"],
        "source_series": market["dataset"],
        "lag_months": 0,
        "formula": formula,
        "unit": "basis_points",
        "title": title,
        "country": market["country"],
    }


for _prefix, _curve_name, _pairs in (
    ("yield", "nominal", (("10YEAR", "2YEAR"), ("10YEAR", "3MONTH"), ("30YEAR", "5YEAR"))),
    (
        "jgb",
        "jgb",
        (("10YEAR", "2YEAR"), ("20YEAR", "10YEAR"), ("30YEAR", "10YEAR"), ("40YEAR", "30YEAR")),
    ),
):
    _curve = cast(CurveName, _curve_name)
    for _long, _short in _pairs:
        _long_label = _long.replace("YEAR", "y").replace("MONTH", "m")
        _short_label = _short.replace("YEAR", "y").replace("MONTH", "m")
        register_curve_series(
            f"{_prefix}_spread_{_long_label}_{_short_label}",
            f"{'Japanese' if _curve == 'jgb' else 'US'} {_long_label} minus {_short_label} yield",
            [
                {"curve": _curve, "maturity": _long, "coefficient": 1, "role": "long"},
                {"curve": _curve, "maturity": _short, "coefficient": -1, "role": "short"},
            ],
            f"({_long} - {_short}) * 100",
        )
    register_curve_series(
        f"{_prefix}_butterfly_2y_5y_10y",
        f"{'Japanese' if _curve == 'jgb' else 'US'} 2y/5y/10y yield butterfly",
        [
            {"curve": _curve, "maturity": "5YEAR", "coefficient": 2, "role": "belly"},
            {"curve": _curve, "maturity": "2YEAR", "coefficient": -1, "role": "short_wing"},
            {"curve": _curve, "maturity": "10YEAR", "coefficient": -1, "role": "long_wing"},
        ],
        "(2 * 5YEAR - 2YEAR - 10YEAR) * 100",
        interpretation="Twice the belly minus both wings; a yield-shape statistic, not trade P&L.",
    )

for _maturity in ("5YEAR", "7YEAR", "10YEAR", "20YEAR", "30YEAR"):
    _label = _maturity.replace("YEAR", "y")
    register_curve_series(
        f"par_inflation_compensation_{_label}",
        f"US {_label} nominal/real par yield gap (inflation-compensation proxy)",
        [
            {"curve": "nominal", "maturity": _maturity, "coefficient": 1, "role": "nominal"},
            {"curve": "real", "maturity": _maturity, "coefficient": -1, "role": "real"},
        ],
        f"(nominal_{_maturity} - real_{_maturity}) * 100",
        interpretation=(
            "Nominal minus real par yields is an indicative inflation-compensation proxy, "
            "not a zero-coupon breakeven, pure expected inflation or an inflation-swap quote. "
            "Par-curve construction, inflation risk and liquidity affect the gap."
        ),
    )

for _prefix, _curve_name, _maturities in (
    ("yield", "nominal", ("2YEAR", "5YEAR", "10YEAR", "30YEAR")),
    ("real_yield", "real", ("5YEAR", "10YEAR", "30YEAR")),
    ("jgb_yield", "jgb", ("2YEAR", "5YEAR", "10YEAR", "20YEAR", "30YEAR", "40YEAR")),
):
    _curve = cast(CurveName, _curve_name)
    for _maturity in _maturities:
        _label = _maturity.replace("YEAR", "y")
        register_curve_series(
            f"{_prefix}_change_{_label}",
            f"{'Japanese' if _curve == 'jgb' else 'US'} {_curve} {_label} yield change",
            [
                {"curve": _curve, "maturity": _maturity, "coefficient": 1, "role": "current"},
                {"curve": _curve, "maturity": _maturity, "coefficient": -1, "role": "previous"},
            ],
            f"(current_{_maturity} - previous_{_maturity}) * 100",
            previous_observation=True,
            interpretation=(
                "Change from the immediately preceding source observation, not a calendar-day "
                "or security-price return. A missing previous yield is not skipped."
            ),
        )

for _prefix, _regional_curve, _regional_pairs, _regional_changes in (
    (
        "euro_aaa",
        "ecb_aaa",
        (("10YEAR", "2YEAR"), ("30YEAR", "5YEAR"), ("30YEAR", "10YEAR")),
        ("2YEAR", "5YEAR", "10YEAR", "30YEAR"),
    ),
    (
        "euro_all",
        "ecb_all",
        (("10YEAR", "2YEAR"), ("30YEAR", "5YEAR"), ("30YEAR", "10YEAR")),
        ("2YEAR", "5YEAR", "10YEAR", "30YEAR"),
    ),
    (
        "australia",
        "rba",
        (("10YEAR", "2YEAR"), ("10YEAR", "3YEAR")),
        ("2YEAR", "3YEAR", "5YEAR", "10YEAR"),
    ),
):
    _curve = cast(CurveName, _regional_curve)
    _label = CURVE_MARKETS[_curve]["label"]
    for _long, _short in _regional_pairs:
        register_curve_series(
            f"{_prefix}_spread_{_long.replace('YEAR', 'y')}_{_short.replace('YEAR', 'y')}",
            f"{_label} {_long} minus {_short}",
            [
                {"curve": _curve, "maturity": _long, "coefficient": 1, "role": "long"},
                {"curve": _curve, "maturity": _short, "coefficient": -1, "role": "short"},
            ],
            f"({_long} - {_short}) * 100",
        )
    register_curve_series(
        f"{_prefix}_butterfly_2y_5y_10y",
        f"{_label} 2y/5y/10y butterfly",
        [
            {"curve": _curve, "maturity": "5YEAR", "coefficient": 2, "role": "belly"},
            {"curve": _curve, "maturity": "2YEAR", "coefficient": -1, "role": "short_wing"},
            {"curve": _curve, "maturity": "10YEAR", "coefficient": -1, "role": "long_wing"},
        ],
        "(2 * 5YEAR - 2YEAR - 10YEAR) * 100",
        interpretation="Twice the belly minus both wings within one source curve; not trade P&L.",
    )
    for _maturity in _regional_changes:
        register_curve_series(
            f"{_prefix}_yield_change_{_maturity.replace('YEAR', 'y')}",
            f"{_label} {_maturity} change",
            [
                {"curve": _curve, "maturity": _maturity, "coefficient": 1, "role": "current"},
                {"curve": _curve, "maturity": _maturity, "coefficient": -1, "role": "previous"},
            ],
            f"(current_{_maturity} - previous_{_maturity}) * 100",
            previous_observation=True,
            interpretation="Preceding-observation change; nulls and calendar gaps remain explicit.",
        )
