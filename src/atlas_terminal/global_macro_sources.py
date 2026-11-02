"""Bounded World Development Indicators selections with explicit country membership."""

import re
from typing import TypedDict
from urllib.parse import parse_qs, urlencode


class Area(TypedDict):
    iso3: str
    name: str
    kind: str


G20_AREAS: dict[str, Area] = {
    code: {"iso3": iso3, "name": name, "kind": kind}
    for code, iso3, name, kind in (
        ("AR", "ARG", "Argentina", "country"),
        ("AU", "AUS", "Australia", "country"),
        ("BR", "BRA", "Brazil", "country"),
        ("CA", "CAN", "Canada", "country"),
        ("CN", "CHN", "China", "country"),
        ("DE", "DEU", "Germany", "country"),
        ("FR", "FRA", "France", "country"),
        ("GB", "GBR", "United Kingdom", "country"),
        ("ID", "IDN", "Indonesia", "country"),
        ("IN", "IND", "India", "country"),
        ("IT", "ITA", "Italy", "country"),
        ("JP", "JPN", "Japan", "country"),
        ("KR", "KOR", "Korea, Rep.", "country"),
        ("MX", "MEX", "Mexico", "country"),
        ("RU", "RUS", "Russian Federation", "country"),
        ("SA", "SAU", "Saudi Arabia", "country"),
        ("TR", "TUR", "Turkiye", "country"),
        ("US", "USA", "United States", "country"),
        ("ZA", "ZAF", "South Africa", "country"),
        ("EU", "EUU", "European Union", "regional_aggregate"),
    )
}

GLOBAL_AREAS: dict[str, Area] = G20_AREAS | {
    code: {"iso3": iso3, "name": name, "kind": "country"}
    for code, iso3, name in (
        ("MY", "MYS", "Malaysia"),
        ("TH", "THA", "Thailand"),
        ("SG", "SGP", "Singapore"),
        ("NZ", "NZL", "New Zealand"),
    )
}


class Indicator(TypedDict):
    code: str
    title: str
    unit: str
    definition: str


GLOBAL_INDICATORS: dict[str, Indicator] = {
    key: {"code": code, "title": title, "unit": unit, "definition": definition}
    for key, code, title, unit, definition in (
        (
            "gdp_nominal",
            "NY.GDP.MKTP.CD",
            "GDP (current US$)",
            "USD",
            "Current-price GDP in full US dollars; not PPP or a constant-price series.",
        ),
        (
            "gdp_real",
            "NY.GDP.MKTP.KD",
            "GDP (constant 2015 US$)",
            "constant_2015_USD",
            "Constant 2015 US dollars; retain the publisher's base and conversion conventions.",
        ),
        (
            "gdp_growth",
            "NY.GDP.MKTP.KD.ZG",
            "GDP growth (annual %)",
            "percent",
            "Published annual real GDP growth; not a quarterly or annualized change.",
        ),
        (
            "gdp_per_capita",
            "NY.GDP.PCAP.CD",
            "GDP per capita (current US$)",
            "USD_per_person",
            "Current-price US dollars per person; not a purchasing-power comparison.",
        ),
        (
            "gdp_ppp",
            "NY.GDP.MKTP.PP.CD",
            "GDP, PPP (current international $)",
            "current_international_dollar",
            "Current international dollars based on PPP; not market-exchange-rate US dollars.",
        ),
        (
            "cpi_inflation",
            "FP.CPI.TOTL.ZG",
            "Inflation, consumer prices (annual %)",
            "percent",
            "Annual consumer-price inflation; not monthly CPI or necessarily December-on-December.",
        ),
        (
            "unemployment",
            "SL.UEM.TOTL.ZS",
            "Unemployment, total (% of total labor force) (modeled ILO estimate)",
            "percent",
            "Modeled ILO estimate for the total labour force; not a national monthly release.",
        ),
        (
            "population",
            "SP.POP.TOTL",
            "Population, total",
            "persons",
            "Publisher population estimates; annual labeling does not imply a year-end census.",
        ),
        (
            "current_account",
            "BN.CAB.XOKA.GD.ZS",
            "Current account balance (% of GDP)",
            "percent_of_GDP",
            "Balance of payments current account; negative balances are retained.",
        ),
        (
            "exports",
            "NE.EXP.GNFS.ZS",
            "Exports of goods and services (% of GDP)",
            "percent_of_GDP",
            "National-accounts exports of goods and services relative to GDP.",
        ),
        (
            "imports",
            "NE.IMP.GNFS.ZS",
            "Imports of goods and services (% of GDP)",
            "percent_of_GDP",
            "National-accounts imports of goods and services relative to GDP.",
        ),
        (
            "investment",
            "NE.GDI.TOTL.ZS",
            "Gross capital formation (% of GDP)",
            "percent_of_GDP",
            "Gross capital formation, including inventories; not corporate capex.",
        ),
        (
            "central_government_debt",
            "GC.DOD.TOTL.GD.ZS",
            "Central government debt, total (% of GDP)",
            "percent_of_GDP",
            "Central-government debt only; not general-government debt or a real-time debt stock.",
        ),
        (
            "official_exchange_rate",
            "PA.NUS.FCRF",
            "Official exchange rate (LCU per US$, period average)",
            "LCU_per_USD",
            "Annual period-average official exchange rate; not a spot rate or tradable quote.",
        ),
    )
}

WORLD_BANK_BASE = "https://api.worldbank.org/v2/"
MEMBERSHIP_URL = "https://www.g20.org.za/about-g20/g20-members/"


def wdi_metadata_url(series: str) -> str:
    return (
        WORLD_BANK_BASE + "indicator/" + GLOBAL_INDICATORS[series]["code"] + "?format=json&source=2"
    )


def wdi_country_url(country: str) -> str:
    return WORLD_BANK_BASE + "country/" + GLOBAL_AREAS[country]["iso3"] + "?format=json"


def wdi_data_url(country: str, series: str, first: int, last: int) -> str:
    return (
        WORLD_BANK_BASE
        + "country/"
        + GLOBAL_AREAS[country]["iso3"]
        + "/indicator/"
        + GLOBAL_INDICATORS[series]["code"]
        + "?"
        + urlencode({"format": "json", "source": "2", "date": f"{first}:{last}", "per_page": "100"})
    )


def global_wdi_identity(url: str) -> tuple[str, str | None, str | None] | None:
    """Recognize only canonical selected endpoints, without credentials or extra parameters."""
    if not url.startswith(WORLD_BANK_BASE) or len(url) > 500:
        return None
    for series in GLOBAL_INDICATORS:
        if url == wdi_metadata_url(series):
            return "metadata", None, series
    for country in GLOBAL_AREAS:
        if url == wdi_country_url(country):
            return "country", country, None
    match = re.fullmatch(
        re.escape(WORLD_BANK_BASE) + r"country/([A-Z]{3})/indicator/([A-Z0-9.]+)\?(.+)", url
    )
    if not match:
        return None
    selected_country = next(
        (k for k, area in GLOBAL_AREAS.items() if area["iso3"] == match[1]), None
    )
    selected_series = next(
        (k for k, spec in GLOBAL_INDICATORS.items() if spec["code"] == match[2]), None
    )
    try:
        window = parse_qs(match[3], strict_parsing=True)["date"]
        if len(window) != 1 or not re.fullmatch(r"[0-9]{4}:[0-9]{4}", window[0]):
            return None
        first, last = map(int, window[0].split(":"))
        if (
            selected_country
            and selected_series
            and 1900 <= first <= last <= 2100
            and last - first <= 29
            and url == wdi_data_url(selected_country, selected_series, first, last)
        ):
            return "data", selected_country, selected_series
    except (KeyError, ValueError):
        pass
    return None
