"""Curated public national accounts and Japanese statistical series.

Table identifiers and BOJ codes follow the publishers' API manuals and main-series
list. e-Stat indicators are resolved from official metadata, never guessed IDs.
"""

from typing import NotRequired, TypedDict


class AccountSpec(TypedDict):
    table: str
    frequency: str
    title: str
    description_pattern: str
    annualized: bool


NATIONAL_ACCOUNTS: dict[str, AccountSpec] = {
    "gdp_nominal": {
        "table": "T10105",
        "frequency": "Q",
        "title": "Nominal gross domestic product",
        "description_pattern": r"Gross domestic product",
        "annualized": True,
    },
    "gdp_real": {
        "table": "T10106",
        "frequency": "Q",
        "title": "Real gross domestic product",
        "description_pattern": r"Gross domestic product",
        "annualized": True,
    },
    "gdp_growth": {
        "table": "T10101",
        "frequency": "Q",
        "title": "Real GDP change, annualized quarterly rate",
        "description_pattern": r"Gross domestic product",
        "annualized": True,
    },
    "pce_price_index": {
        "table": "T20804",
        "frequency": "M",
        "title": "PCE price index",
        "description_pattern": r"Personal consumption expenditures(?: \(PCE\))?",
        "annualized": False,
    },
    "core_pce_price_index": {
        "table": "T20804",
        "frequency": "M",
        "title": "PCE price index excluding food and energy",
        "description_pattern": (
            "(?:PCE|Personal consumption expenditures) excluding food and energy"
        ),
        "annualized": False,
    },
    "personal_income": {
        "table": "T20600",
        "frequency": "M",
        "title": "Personal income",
        "description_pattern": r"Personal income",
        "annualized": True,
    },
    "disposable_personal_income": {
        "table": "T20600",
        "frequency": "M",
        "title": "Disposable personal income",
        "description_pattern": r"Disposable personal income(?: \(DPI\))?",
        "annualized": True,
    },
    "personal_consumption": {
        "table": "T20600",
        "frequency": "M",
        "title": "Personal consumption expenditures",
        "description_pattern": r"Personal consumption expenditures(?: \(PCE\))?",
        "annualized": True,
    },
}


class JapanSpec(TypedDict):
    source_id: str
    title: str
    database: str
    code: str
    frequency: str
    seasonally_adjusted: bool | None
    annualized: NotRequired[bool]


JAPAN_SERIES: dict[str, JapanSpec] = {
    "call_rate": {
        "source_id": "boj",
        "title": "Uncollateralized overnight call rate, daily average",
        "database": "FM01",
        "code": "STRDCLUCON",
        "frequency": "DAILY",
        "seasonally_adjusted": None,
    },
    "usd_jpy_tokyo": {
        "source_id": "boj",
        "title": "USD/JPY spot rate at 17:00 JST, Tokyo market",
        "database": "FM08",
        "code": "FXERD04",
        "frequency": "DAILY",
        "seasonally_adjusted": None,
    },
    "tankan_manufacturing": {
        "source_id": "boj",
        "title": "Tankan large manufacturers, business conditions, actual DI",
        "database": "CO",
        "code": "TK99F1000601GCQ01000",
        "frequency": "QUARTERLY",
        "seasonally_adjusted": False,
    },
    "tankan_nonmanufacturing": {
        "source_id": "boj",
        "title": "Tankan large nonmanufacturers, business conditions, actual DI",
        "database": "CO",
        "code": "TK99F2000601GCQ01000",
        "frequency": "QUARTERLY",
        "seasonally_adjusted": False,
    },
    "monetary_base": {
        "source_id": "boj",
        "title": "Monetary base, average amounts outstanding",
        "database": "MD01",
        "code": "MABS1AN11",
        "frequency": "MONTHLY",
        "seasonally_adjusted": False,
    },
    "money_stock_m2": {
        "source_id": "boj",
        "title": "M2 money stock, average amounts outstanding",
        "database": "MD02",
        "code": "MAM1NAM2M2MO",
        "frequency": "MONTHLY",
        "seasonally_adjusted": False,
    },
    "bank_loans": {
        "source_id": "boj",
        "title": "Domestic banks, total loans outstanding at period end",
        "database": "MD11",
        "code": "DLCLAEDBLTTO",
        "frequency": "MONTHLY",
        "seasonally_adjusted": False,
    },
    "real_effective_exchange_rate": {
        "source_id": "boj",
        "title": "Real effective exchange rate index",
        "database": "FM09",
        "code": "FX180110002",
        "frequency": "MONTHLY",
        "seasonally_adjusted": None,
    },
    "cpi": {
        "source_id": "estat",
        "title": "Japan consumer price index, all items",
        "database": "00200573",
        "code": "消費者物価指数（総合）",
        "frequency": "MONTHLY",
        "seasonally_adjusted": False,
    },
    "unemployment": {
        "source_id": "estat",
        "title": "Japan unemployment rate, both sexes, seasonally adjusted",
        "database": "00200531",
        "code": "完全失業率（男女計）",
        "frequency": "MONTHLY",
        "seasonally_adjusted": True,
    },
}

# Resolve current indicator IDs from publisher metadata; never splice index bases.
for _key, _title, _survey, _label, _frequency, _adjusted in (
    (
        "gdp_nominal",
        "Japan nominal GDP, expenditure approach",
        "00100409",
        "国内総生産（支出側）（名目）",
        "QUARTERLY",
        True,
    ),
    (
        "gdp_real",
        "Japan real GDP, expenditure approach",
        "00100409",
        "国内総生産（支出側）（実質）",
        "QUARTERLY",
        True,
    ),
    (
        "gdp_deflator",
        "Japan GDP deflator, chain-linked",
        "00100409",
        "GDPデフレーター（名目）（連鎖方式）",
        "QUARTERLY",
        True,
    ),
    (
        "core_cpi",
        "Japan CPI excluding fresh food",
        "00200573",
        "消費者物価指数（生鮮食品を除く総合）",
        "MONTHLY",
        False,
    ),
    (
        "industrial_production",
        "Japan industrial production index",
        "00550300",
        "鉱工業生産指数",
        "MONTHLY",
        True,
    ),
    (
        "industrial_shipments",
        "Japan industrial shipments index",
        "00550300",
        "鉱工業出荷指数",
        "MONTHLY",
        True,
    ),
    (
        "industrial_inventories",
        "Japan industrial inventories index",
        "00550300",
        "鉱工業在庫指数",
        "MONTHLY",
        True,
    ),
    (
        "retail_sales",
        "Japan retail sales, nominal",
        "00550030",
        "小売業販売額（名目）",
        "MONTHLY",
        False,
    ),
    (
        "household_consumption_real",
        "Japan real consumption index, two-or-more-person households",
        "00200561",
        "二人以上の世帯 消費支出（季節調整済実質指数）",
        "MONTHLY",
        True,
    ),
    (
        "job_offers_ratio",
        "Japan active job openings-to-applicants ratio",
        "00450222",
        "有効求人倍率",
        "MONTHLY",
        True,
    ),
    (
        "real_wages",
        "Japan real wage index, total cash earnings",
        "00450071",
        "実質賃金指数（現金給与総額）",
        "MONTHLY",
        False,
    ),
    (
        "housing_starts",
        "Japan new dwelling starts",
        "00600120",
        "新設住宅着工戸数",
        "MONTHLY",
        False,
    ),
):
    JAPAN_SERIES[_key] = {
        "source_id": "estat",
        "title": _title,
        "database": _survey,
        "code": _label,
        "frequency": _frequency,
        "seasonally_adjusted": _adjusted,
        "annualized": _key in {"gdp_nominal", "gdp_real"},
    }

for _key, _title, _survey, _label, _frequency, _adjusted in (
    (
        "machinery_orders",
        "Japan private machinery orders excluding ships and electric power",
        "00100401",
        "機械受注額（船舶・電力を除く民需）",
        "MONTHLY",
        True,
    ),
    (
        "trade_exports",
        "Japan customs-basis merchandise exports, nominal",
        "00350300",
        "輸出総額",
        "MONTHLY",
        False,
    ),
    (
        "trade_imports",
        "Japan customs-basis merchandise imports, nominal",
        "00350300",
        "輸入総額",
        "MONTHLY",
        False,
    ),
    (
        "trade_balance",
        "Japan customs-basis merchandise trade balance",
        "00350300",
        "貿易収支",
        "MONTHLY",
        False,
    ),
    (
        "current_account",
        "Japan balance-of-payments current account, unadjusted",
        "00350503",
        "経常収支",
        "MONTHLY",
        False,
    ),
    (
        "corporate_profits",
        "Japan corporate current profits, all nonfinancial industries",
        "00350600",
        "経常利益（全産業（金融業、保険業を除く））",
        "QUARTERLY",
        False,
    ),
    (
        "corporate_capex",
        "Japan corporate capital investment excluding software, all nonfinancial industries",
        "00350600",
        "設備投資（ソフトウェアを除く）（全産業（金融業、保険業を除く））",
        "QUARTERLY",
        False,
    ),
):
    JAPAN_SERIES[_key] = {
        "source_id": "estat",
        "title": _title,
        "database": _survey,
        "code": _label,
        "frequency": _frequency,
        "seasonally_adjusted": _adjusted,
        "annualized": False,
    }

for _key, _title, _table, _frequency, _description, _annualized in (
    (
        "gdp_consumption_real",
        "Real personal consumption expenditures",
        "T10106",
        "Q",
        "Personal consumption expenditures",
        True,
    ),
    (
        "business_investment_real",
        "Real nonresidential fixed investment",
        "T10106",
        "Q",
        "Nonresidential",
        True,
    ),
    (
        "residential_investment_real",
        "Real residential fixed investment",
        "T10106",
        "Q",
        "Residential",
        True,
    ),
    (
        "government_consumption_investment_real",
        "Real government consumption and investment",
        "T10106",
        "Q",
        "Government consumption expenditures and gross investment",
        True,
    ),
    ("exports_real", "Real exports of goods and services", "T10106", "Q", "Exports", True),
    ("imports_real", "Real imports of goods and services", "T10106", "Q", "Imports", True),
    (
        "personal_saving_rate",
        "Personal saving as a percentage of disposable personal income",
        "T20600",
        "M",
        r"Personal saving as a percentage of disposable personal income(?: \(DPI\))?",
        False,
    ),
    (
        "pce_real",
        "Real personal consumption expenditures",
        "T20806",
        "M",
        r"Personal consumption expenditures(?: \(PCE\))?",
        True,
    ),
):
    NATIONAL_ACCOUNTS[_key] = {
        "table": _table,
        "frequency": _frequency,
        "title": _title,
        "description_pattern": _description,
        "annualized": _annualized,
    }
