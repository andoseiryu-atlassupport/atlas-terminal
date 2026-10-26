"""Curated official activity indicators; names do not imply comparable definitions."""

from typing import NotRequired, TypedDict


class ActivitySpec(TypedDict):
    source_id: str
    country: str
    title: str
    frequency: str
    unit: str | None
    seasonally_adjusted: bool
    annualized: bool
    code: str
    table: NotRequired[str]
    data_type: NotRequired[str]
    dimensions: NotRequired[dict[str, str]]
    metadata_url: str


ACTIVITY_SERIES: dict[str, ActivitySpec] = {}
for _key, _table, _category, _type, _title, _unit, _annual in (
    (
        "retail_sales",
        "marts",
        "44X72",
        "SM",
        "Advance retail and food services sales",
        "million_USD",
        False,
    ),
    (
        "housing_starts",
        "resconst",
        "TOTAL",
        "STARTS",
        "Privately owned housing starts",
        "thousand_dwellings",
        True,
    ),
    (
        "housing_permits",
        "resconst",
        "TOTAL",
        "PERMITS",
        "Privately owned housing units authorized by permits",
        "thousand_dwellings",
        True,
    ),
    (
        "durable_goods_orders",
        "advm3",
        "MDM",
        "NO",
        "Advance manufacturers' new orders, durable goods",
        "million_USD",
        False,
    ),
):
    ACTIVITY_SERIES["us_" + _key] = {
        "source_id": "census",
        "country": "US",
        "title": _title,
        "frequency": "monthly",
        "unit": _unit,
        "seasonally_adjusted": True,
        "annualized": _annual,
        "code": _category,
        "table": _table,
        "data_type": _type,
        "metadata_url": "https://www.census.gov/data/developers/data-sets/economic-indicators.html",
    }

for _key, _type, _title in (
    ("trade_exports", "EXP", "exports"),
    ("trade_imports", "IMP", "imports"),
    ("trade_balance", "BAL", "balance"),
):
    ACTIVITY_SERIES["us_" + _key] = {
        "source_id": "census",
        "country": "US",
        "title": "US goods and services trade " + _title + ", balance of payments basis",
        "frequency": "monthly",
        "unit": "million_USD",
        "seasonally_adjusted": True,
        "annualized": False,
        "code": "BOPGS",
        "table": "ftd",
        "data_type": _type,
        "metadata_url": "https://api.census.gov/data/timeseries/eits/ftd.html",
    }

for _key, _file, _title, _unit in (
    ("industrial_production", "ip_sa.txt", "Total industrial production", "index_2017_100"),
    ("capacity_utilization", "utl_sa.txt", "Total industry capacity utilization", "percent"),
):
    ACTIVITY_SERIES["us_" + _key] = {
        "source_id": "federal_reserve",
        "country": "US",
        "title": _title,
        "frequency": "monthly",
        "unit": _unit,
        "seasonally_adjusted": True,
        "annualized": False,
        "code": "B50001",
        "table": _file,
        "metadata_url": "https://www.federalreserve.gov/releases/g17/download.htm",
    }

for _key, _code, _title in (
    ("initial_claims", "ICSA", "Initial unemployment insurance claims, regular state programs"),
    ("continued_claims", "CCSA", "Continued unemployment insurance claims, insured unemployment"),
):
    ACTIVITY_SERIES["us_" + _key] = {
        "source_id": "fred",
        "country": "US",
        "title": _title,
        "frequency": "weekly",
        "unit": "number",
        "seasonally_adjusted": True,
        "annualized": False,
        "code": _code,
        "metadata_url": "https://fred.stlouisfed.org/series/" + _code,
    }

for _key, _vector, _table, _title, _unit, _adjusted, _annual in (
    (
        "cpi",
        "41690973",
        "18100004",
        "Canada all-items CPI, unadjusted",
        "index_2002_100",
        False,
        False,
    ),
    (
        "unemployment",
        "2062815",
        "14100287",
        "Canada unemployment rate, age 15+, seasonally adjusted",
        "percent",
        True,
        False,
    ),
    (
        "gdp_real",
        "65201210",
        "36100434",
        "Canada all-industry real GDP at basic prices, monthly annual rate",
        "CAD",
        True,
        True,
    ),
):
    ACTIVITY_SERIES["ca_" + _key] = {
        "source_id": "statcan",
        "country": "CA",
        "title": _title,
        "frequency": "monthly",
        "unit": _unit,
        "seasonally_adjusted": _adjusted,
        "annualized": _annual,
        "code": _vector,
        "table": _table,
        "metadata_url": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=" + _table + "01",
    }

for _key, _flow, _dims, _title, _frequency, _adjusted in (
    (
        "gdp_real",
        "ANA_AGG",
        {"MEASURE": "M1", "DATA_ITEM": "GPM", "TSEST": "20", "REGION": "AUS", "FREQ": "Q"},
        "Australia chain-volume GDP, seasonally adjusted, quarterly",
        "quarterly",
        True,
    ),
    (
        "unemployment",
        "LF",
        {"MEASURE": "M13", "SEX": "3", "AGE": "1599", "TSEST": "20", "REGION": "AUS", "FREQ": "M"},
        "Australia unemployment rate, persons age 15+, seasonally adjusted",
        "monthly",
        True,
    ),
):
    ACTIVITY_SERIES["au_" + _key] = {
        "source_id": "abs",
        "country": "AU",
        "title": _title,
        "frequency": _frequency,
        "unit": None,
        "seasonally_adjusted": _adjusted,
        "annualized": False,
        "code": "ABS," + _flow + ",latest",
        "dimensions": _dims,
        "metadata_url": "https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide",
    }

# ABS moved quarterly CPI to CPI_Q in April 2026; CPI 2.0.0 is monthly.
ACTIVITY_SERIES["au_cpi_quarterly"] = {
    "source_id": "abs",
    "country": "AU",
    "title": "Australia all-groups CPI, weighted average of eight capital cities, quarterly",
    "frequency": "quarterly",
    "unit": None,
    "seasonally_adjusted": False,
    "annualized": False,
    "code": "ABS,CPI_Q,latest",
    "dimensions": {"MEASURE": "1", "INDEX": "10001", "TSEST": "10", "REGION": "50", "FREQ": "Q"},
    "metadata_url": "https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide/updates",
}

ACTIVITY_SERIES["au_cpi_monthly"] = {
    "source_id": "abs",
    "country": "AU",
    "title": "Australia all-groups CPI, monthly",
    "frequency": "monthly",
    "unit": None,
    "seasonally_adjusted": False,
    "annualized": False,
    "code": "ABS,CPI,2.0.0",
    "dimensions": {"MEASURE": "1", "INDEX": "10001", "TSEST": "10", "REGION": "50", "FREQ": "M"},
    "metadata_url": "https://www.abs.gov.au/statistics/application-programming-interfaces-apis/data-api-user-guide/tutorials-links",
}

for _key, _unit, _transform, _title in (
    ("cpi", "IX", "_Z", "China national all-items CPI index, OECD dissemination"),
    (
        "cpi_yoy",
        "PA",
        "GY",
        "China national all-items CPI, year-on-year percent, OECD dissemination",
    ),
):
    ACTIVITY_SERIES["cn_" + _key] = {
        "source_id": "oecd",
        "country": "CN",
        "title": _title,
        "frequency": "monthly",
        "unit": None,
        "seasonally_adjusted": False,
        "annualized": False,
        "code": "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0",
        "dimensions": {
            "REF_AREA": "CHN",
            "FREQ": "M",
            "METHODOLOGY": "N",
            "MEASURE": "CPI",
            "UNIT_MEASURE": _unit,
            "EXPENDITURE": "_T",
            "ADJUSTMENT": "N",
            "TRANSFORMATION": _transform,
        },
        "metadata_url": "https://data-explorer.oecd.org/vis?df[ag]=OECD.SDD.TPS&df[id]=DSD_PRICES@DF_PRICES_ALL",
    }

for _key, _code, _title, _unit in (
    (
        "gdp_growth_annual",
        "NY.GDP.MKTP.KD.ZG",
        "China real GDP growth, annual, World Development Indicators",
        "percent",
    ),
    (
        "gdp_nominal_annual",
        "NY.GDP.MKTP.CN",
        "China nominal GDP, annual, current local currency, World Development Indicators",
        "CNY",
    ),
):
    ACTIVITY_SERIES["cn_" + _key] = {
        "source_id": "world_bank",
        "country": "CN",
        "title": _title,
        "frequency": "annual",
        "unit": _unit,
        "seasonally_adjusted": False,
        "annualized": False,
        "code": _code,
        "metadata_url": "https://data.worldbank.org/indicator/" + _code + "?locations=CN",
    }
