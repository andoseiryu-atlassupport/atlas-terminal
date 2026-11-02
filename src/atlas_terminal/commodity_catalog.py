"""Explicit monthly IMF price concepts; units come from checked indicator labels."""

PCPS_FLOW = "IMF.RES,PCPS,latest"
COMMODITIES = {
    "gold": ("PGOLD", "Gold, US dollars per troy ounce of gold, Unit prices", "USD_per_troy_ounce"),
    "silver": (
        "PSILVER",
        "Silver, US dollars per troy ounce of gold, Unit prices",
        "USD_per_troy_ounce",
    ),
    "copper": ("PCOPP", "Copper, US dollars per metric tonne, Unit prices", "USD_per_metric_tonne"),
    "aluminum": (
        "PALUM",
        "Aluminum, US dollars per metric tonne, Unit prices",
        "USD_per_metric_tonne",
    ),
    "iron_ore": (
        "PIORECR",
        "Iron Ore, US dollars per metric tonne, Unit prices",
        "USD_per_metric_tonne",
    ),
    "platinum": (
        "PPLAT",
        "Platinum, US dollars per troy ounce of gold, Unit prices",
        "USD_per_troy_ounce",
    ),
    "palladium": (
        "PPALLA",
        "Palladium, US dollars per troy ounce of gold, Unit prices",
        "USD_per_troy_ounce",
    ),
    "crude_oil_wti": ("POILWTI", "WTI Crude, US dollars per barrel, Unit prices", "USD_per_barrel"),
    "crude_oil_brent": (
        "POILBRE",
        "Brent Crude, US dollars per barrel, Unit prices",
        "USD_per_barrel",
    ),
    "crude_oil_dubai": (
        "POILDUB",
        "Dubai Crude, US dollars per barrel, Unit prices",
        "USD_per_barrel",
    ),
    "natural_gas_us": (
        "PNGASUS",
        "Natural Gas, US Henry Hub Gas, US dollars per million metric British "
        "thermal units of gas, Unit prices",
        "USD_per_million_Btu",
    ),
    "natural_gas_europe": (
        "PNGASEU",
        "Natural gas, EU, US dollars per million metric British thermal units of gas, Unit prices",
        "USD_per_million_Btu",
    ),
    "wheat": ("PWHEAMT", "Wheat, US dollars per metric tonne, Unit prices", "USD_per_metric_tonne"),
    "corn": ("PMAIZMT", "Corn, US dollars per metric tonne, Unit prices", "USD_per_metric_tonne"),
    "coffee_arabica": (
        "PCOFFOTM",
        "Coffee, Other Mild Arabica, US cents per pound, Unit prices",
        "US_cent_per_pound",
    ),
    "coffee_robusta": (
        "PCOFFROB",
        "Coffee, Robustas, US cents per pound, Unit prices",
        "US_cent_per_pound",
    ),
    "sugar_world": (
        "PSUGAISA",
        "Sugar, No. 11, World, US cents per pound, Unit prices",
        "US_cent_per_pound",
    ),
    "cotton": ("PCOTTIND", "Cotton, US cents per pound, Unit prices", "US_cent_per_pound"),
}
