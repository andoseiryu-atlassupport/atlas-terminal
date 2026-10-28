"""Curated Eurostat and ONS series, with explicit statistical definitions."""

from typing import TypedDict


class EuropeSpec(TypedDict):
    dataset: str
    title: str
    country: str
    frequency: str
    filters: dict[str, str]
    seasonally_adjusted: bool
    base_period: str | None


EUROPE_SERIES: dict[str, EuropeSpec] = {}
for _prefix, _country, _geo, _name in (
    ("ea", "EA", "EA", "Euro area, changing composition"),
    ("eu", "EU", "EU27_2020", "European Union, 27 countries from 2020"),
    ("de", "DE", "DE", "Germany"),
    ("fr", "FR", "FR", "France"),
    ("it", "IT", "IT", "Italy"),
    ("es", "ES", "ES", "Spain"),
):
    for _key, _table, _title, _frequency, _filters, _adjusted, _base in (
        (
            "gdp_nominal",
            "namq_10_gdp",
            "nominal GDP, million EUR",
            "Q",
            {"unit": "CP_MEUR", "s_adj": "SCA", "na_item": "B1GQ"},
            True,
            None,
        ),
        (
            "gdp_real",
            "namq_10_gdp",
            "real GDP, chain-linked million EUR (2020)",
            "Q",
            {"unit": "CLV20_MEUR", "s_adj": "SCA", "na_item": "B1GQ"},
            True,
            "2020",
        ),
        (
            "gdp_growth",
            "namq_10_gdp",
            "real GDP growth, quarter on quarter, not annualized",
            "Q",
            {"unit": "CLV_PCH_PRE", "s_adj": "SCA", "na_item": "B1GQ"},
            True,
            None,
        ),
        (
            "hicp",
            "prc_hicp_minr",
            "HICP all items, ECOICOP version 2",
            "M",
            {"unit": "I25", "coicop18": "TOTAL"},
            False,
            "2025",
        ),
        (
            "hicp_yoy",
            "prc_hicp_minr",
            "HICP all items, annual percent change",
            "M",
            {"unit": "RCH_A", "coicop18": "TOTAL"},
            False,
            None,
        ),
        (
            "unemployment",
            "une_rt_m",
            "unemployment rate, total sex and age",
            "M",
            {"unit": "PC_ACT", "s_adj": "SA", "sex": "T", "age": "TOTAL"},
            True,
            None,
        ),
        (
            "industrial_production",
            "sts_inpr_m",
            "industrial production, index 2021=100",
            "M",
            {"unit": "I21", "s_adj": "SCA", "indic_bt": "PRD", "nace_r2": "B-D"},
            True,
            "2021",
        ),
        (
            "retail_sales",
            "sts_trtu_m",
            "retail sales volume, index 2021=100",
            "M",
            {"unit": "I21", "s_adj": "SCA", "indic_bt": "VOL_SLS", "nace_r2": "G47"},
            True,
            "2021",
        ),
    ):
        EUROPE_SERIES[_prefix + "_" + _key] = {
            "dataset": _table,
            "title": _name + ": " + _title,
            "country": _country,
            "frequency": _frequency,
            "filters": {"freq": _frequency, **_filters, "geo": _geo},
            "seasonally_adjusted": _adjusted,
            "base_period": _base,
        }


# These tables do not expose a changing-composition EA aggregate.
for _key in ("unemployment", "industrial_production", "retail_sales"):
    EUROPE_SERIES["ea_" + _key]["filters"]["geo"] = "EA21"
    EUROPE_SERIES["ea_" + _key]["title"] = EUROPE_SERIES["ea_" + _key]["title"].replace(
        "Euro area, changing composition", "Euro area, 21 countries from 2026"
    )


class UKSpec(TypedDict):
    path: str
    cdid: str
    dataset: str
    title: str
    frequency: str
    seasonally_adjusted: bool
    rolling_months: int


UK_SERIES: dict[str, UKSpec] = {}
for _key, _path, _cdid, _dataset, _title, _frequency, _adjusted, _rolling in (
    (
        "cpi",
        "economy/inflationandpriceindices",
        "D7BT",
        "MM23",
        "UK CPI all-items index",
        "M",
        False,
        1,
    ),
    (
        "cpi_yoy",
        "economy/inflationandpriceindices",
        "D7G7",
        "MM23",
        "UK CPI all-items annual percent change",
        "M",
        False,
        1,
    ),
    (
        "gdp_growth",
        "economy/grossdomesticproductgdp",
        "IHYQ",
        "PN2",
        "UK real GDP quarter-on-quarter growth, not annualized",
        "Q",
        True,
        1,
    ),
    (
        "gdp_real",
        "economy/grossdomesticproductgdp",
        "ABMI",
        "PN2",
        "UK real GDP, chain-linked million GBP",
        "Q",
        True,
        1,
    ),
    (
        "unemployment",
        "employmentandlabourmarket/peoplenotinwork/unemployment",
        "MGSX",
        "LMS",
        "UK unemployment rate, age 16+, three-month rolling periods",
        "M",
        True,
        3,
    ),
):
    UK_SERIES[_key] = {
        "path": _path,
        "cdid": _cdid,
        "dataset": _dataset,
        "title": _title,
        "frequency": _frequency,
        "seasonally_adjusted": _adjusted,
        "rolling_months": _rolling,
    }
