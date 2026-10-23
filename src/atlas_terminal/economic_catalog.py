"""Curated public national accounts and Japanese statistical series.

Table identifiers and BOJ codes follow the publishers' API manuals and main-series
list. e-Stat indicators are resolved from official metadata, never guessed IDs.
"""

from typing import TypedDict


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
