"""Explicit local-market conventions and public industry code identifiers; no data fetches."""

from typing import Any, Literal

EquityMarket = Literal["US", "JP", "KR"]
EquityCurrency = Literal["USD", "JPY", "KRW"]
IndustryClassification = Literal["sic_division", "jpx33", "ksic10_section", "ksic11_section"]
FundamentalMethodology = Literal[
    "atlas-us-fundamental-v1", "atlas-jp-fundamental-v1", "atlas-kr-fundamental-v1"
]

MARKET_CURRENCIES: dict[EquityMarket, EquityCurrency] = {"US": "USD", "JP": "JPY", "KR": "KRW"}
MAX_SESSION_DAYS: dict[EquityMarket, int] = {"US": 7, "JP": 14, "KR": 14}
MARKET_CLASSIFICATIONS: dict[EquityMarket, tuple[IndustryClassification, ...]] = {
    "US": ("sic_division",),
    "JP": ("jpx33",),
    "KR": ("ksic10_section", "ksic11_section"),
}
MARKET_METHODOLOGIES: dict[EquityMarket, FundamentalMethodology] = {
    "US": "atlas-us-fundamental-v1",
    "JP": "atlas-jp-fundamental-v1",
    "KR": "atlas-kr-fundamental-v1",
}

# Public SICC sector identifiers, not proprietary vendor classifications or issuer assignments.
JPX33 = {
    "0050": "fishery_agriculture_forestry",
    "1050": "mining",
    "2050": "construction",
    "3050": "foods",
    "3100": "textiles_apparels",
    "3150": "pulp_paper",
    "3200": "chemicals",
    "3250": "pharmaceutical",
    "3300": "oil_coal_products",
    "3350": "rubber_products",
    "3400": "glass_ceramics_products",
    "3450": "iron_steel",
    "3500": "nonferrous_metals",
    "3550": "metal_products",
    "3600": "machinery",
    "3650": "electric_appliances",
    "3700": "transportation_equipment",
    "3750": "precision_instruments",
    "3800": "other_products",
    "4050": "electric_power_gas",
    "5050": "land_transportation",
    "5100": "marine_transportation",
    "5150": "air_transportation",
    "5200": "warehousing_harbor_transportation",
    "5250": "information_communication",
    "6050": "wholesale_trade",
    "6100": "retail_trade",
    "7050": "banks",
    "7100": "securities_commodity_futures",
    "7150": "insurance",
    "7200": "other_financing",
    "8050": "real_estate",
    "9050": "services",
}
# Only supplied top-level letters are accepted. No numeric KSIC/KRX/GICS crosswalk is inferred.
KSIC_SECTIONS = tuple("ABCDEFGHIJKLMNOPQRSTU")


def market_catalog() -> dict[str, Any]:
    return {
        market: {
            "currency": MARKET_CURRENCIES[market],
            "industry_classifications": list(MARKET_CLASSIFICATIONS[market]),
            "methodology_version": MARKET_METHODOLOGIES[market],
            "max_session_calendar_days": MAX_SESSION_DAYS[market],
            "max_capitalization_age_calendar_days": MAX_SESSION_DAYS[market],
            "scope": "one supplied common-stock universe in local currency",
        }
        for market in MARKET_CURRENCIES
    }
