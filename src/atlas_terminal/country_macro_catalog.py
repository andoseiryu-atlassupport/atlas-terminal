"""Explicit IMF/OECD country series; no definition or country fallback."""

from typing import Any

from atlas_terminal.global_macro_sources import GLOBAL_AREAS

COUNTRIES = ("ZA", "TR", "MY", "TH", "SG", "NZ", "CN")
IMF_NAMES = {
    "ZA": "South Africa",
    "TR": "Türkiye, Republic of",
    "MY": "Malaysia",
    "TH": "Thailand",
    "SG": "Singapore",
    "NZ": "New Zealand",
    "CN": "China, People's Republic of",
}
OECD_NAMES = {"ZA": "South Africa", "NZ": "New Zealand", "CN": "China (People’s Republic of)"}
CPI_FLOW = "IMF.STA,CPI,latest"
RATE_FLOW = "IMF.STA,MFS_IR,latest"
OECD_FLOW = "OECD.SDD.STES,DSD_STES@DF_FINMARK,latest"
INDICATORS = {
    "cpi_index": ("IX", "Index", "index_source_base"),
    "cpi_inflation_yoy": (
        "YOY_PCH_PA_PT",
        "Period average, Year-over-year (YOY) percent change",
        "percent",
    ),
    "monetary_policy_related_rate": (
        "MFS166_RT_PT_A_PT",
        "Monetary policy-related, Rate, Percent per annum",
        "percent_per_annum",
    ),
    "government_bond_rate": (
        "S13BOND_RT_PT_A_PT",
        "Government bonds, Rate, Percent per annum",
        "percent_per_annum",
    ),
    "treasury_bill_yield": (
        "GSTBILY_RT_PT_A_PT",
        "Government securities: Treasury bills yields, Rate, Percent per annum",
        "percent_per_annum",
    ),
    "long_term_government_yield": ("IRLT", "Long-term interest rates", "percent_per_annum"),
}
SERIES_COUNTRIES = {
    "cpi_index": COUNTRIES,
    "cpi_inflation_yoy": COUNTRIES,
    "monetary_policy_related_rate": ("ZA", "TR", "MY", "TH", "NZ"),
    "government_bond_rate": ("ZA", "MY", "TH", "NZ"),
    "treasury_bill_yield": ("ZA", "TH", "NZ"),
    "long_term_government_yield": ("ZA", "NZ", "CN"),
}
ECONOMIC_SERIES = {"cpi_index", "cpi_inflation_yoy", "monetary_policy_related_rate"}
BOND_SERIES = set(INDICATORS) - ECONOMIC_SERIES


def selection(country: str, series: str) -> dict[str, Any] | None:
    if country not in SERIES_COUNTRIES.get(series, ()):
        return None
    code, label, unit = INDICATORS[series]
    iso3 = GLOBAL_AREAS[country]["iso3"]
    cpi = series.startswith("cpi_")
    oecd = series == "long_term_government_yield"
    frequency = "Q" if cpi and country == "NZ" else "M"
    dimensions = (
        {
            "REF_AREA": iso3,
            "FREQ": "M",
            "MEASURE": code,
            "UNIT_MEASURE": "PA",
            "ACTIVITY": "_Z",
            "ADJUSTMENT": "_Z",
            "TRANSFORMATION": "_Z",
            "TIME_HORIZ": "_Z",
            "METHODOLOGY": "N",
        }
        if oecd
        else {
            "COUNTRY": iso3,
            "INDEX_TYPE": "CPI",
            "COICOP_1999": "_T",
            "TYPE_OF_TRANSFORMATION": code,
            "FREQUENCY": frequency,
        }
        if cpi
        else {"COUNTRY": iso3, "INDICATOR": code, "FREQUENCY": "M"}
    )
    return {
        "dataset": "country_economics" if series in ECONOMIC_SERIES else "country_bond_yields",
        "country": country,
        "series": series,
        "source_id": "oecd" if oecd else "imf",
        "dataflow": OECD_FLOW if oecd else CPI_FLOW if cpi else RATE_FLOW,
        "dimensions": dimensions,
        "frequency": "quarterly" if frequency == "Q" else "monthly",
        "unit": unit,
        "title": GLOBAL_AREAS[country]["name"] + ": " + label,
        "source_series": code,
        "source_series_label": label,
        "source_country_label": OECD_NAMES[country] if oecd else IMF_NAMES[country],
        "benchmark_tenor_years": 10 if oecd else None,
        "rate_period_basis": "monthly_average_OECD_definition"
        if oecd
        else "national_definition_no_period_statistic_inferred"
        if not cpi
        else "publisher_period",
    }


def country_series_catalog() -> list[dict[str, Any]]:
    return [
        spec
        for series, countries in SERIES_COUNTRIES.items()
        for country in countries
        if (spec := selection(country, series)) is not None
    ]
