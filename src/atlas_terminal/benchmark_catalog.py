"""Explicit index identities; ETFs, futures and dividend variants never substitute."""

from typing import Any, Literal

BenchmarkId = Literal["topix", "nikkei225", "sp500", "nasdaq_composite", "dow_jones", "nasdaq100"]
DividendTreatment = Literal["price_return", "gross_total_return", "net_total_return"]
BenchmarkCurrency = Literal["JPY", "USD"]

BENCHMARKS: dict[str, dict[str, Any]] = {
    "topix": {
        "name": "TOPIX",
        "native_currency": "JPY",
        "timezone": "Asia/Tokyo",
        "source_id": "jpx",
        "symbol": "Topix",
        "history": "latest_five_published_sessions",
        "definition_url": "https://www.jpx.co.jp/english/markets/indices/topix/",
    },
    "nikkei225": {
        "name": "Nikkei 225",
        "native_currency": "JPY",
        "timezone": "Asia/Tokyo",
        "source_id": "yahoo_finance",
        "symbol": "^N225",
        "history": "source_available_daily",
        "definition_url": "https://indexes.nikkei.co.jp/en/nkave/index/profile?idx=nk225",
    },
    "sp500": {
        "name": "S&P 500",
        "native_currency": "USD",
        "timezone": "America/New_York",
        "source_id": "yahoo_finance",
        "symbol": "^GSPC",
        "history": "source_available_daily",
        "definition_url": "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
    },
    "nasdaq_composite": {
        "name": "NASDAQ Composite",
        "native_currency": "USD",
        "timezone": "America/New_York",
        "source_id": "yahoo_finance",
        "symbol": "^IXIC",
        "history": "source_available_daily",
        "definition_url": "https://indexes.nasdaq.com/Index/Overview/COMP",
    },
    "dow_jones": {
        "name": "Dow Jones Industrial Average",
        "native_currency": "USD",
        "timezone": "America/New_York",
        "source_id": "yahoo_finance",
        "symbol": "^DJI",
        "history": "source_available_daily",
        "definition_url": "https://www.spglobal.com/spdji/en/indices/equity/dow-jones-industrial-average/",
    },
    "nasdaq100": {
        "name": "NASDAQ-100",
        "native_currency": "USD",
        "timezone": "America/New_York",
        "source_id": "yahoo_finance",
        "symbol": "^NDX",
        "history": "source_available_daily",
        "definition_url": "https://indexes.nasdaq.com/Index/Overview/NDX",
    },
}

ALIGNMENT = {
    "id": "utc_day_end_v1",
    "valuation_timezone": "UTC",
    "valuation_time": "23:59:59",
    "date_key": "source_local_session_date",
    "fx_join": "exact_same_observation_date",
    "portfolio_join": "consecutive_portfolio_endpoints_with_both_benchmark_values",
    "missing_policy": "null_no_fill_no_interpolation_no_compressed_return_intervals",
    "holiday_policy": "absent_observation_not_classified_as_verified_exchange_holiday",
    "synchronous_closes": False,
    "point_in_time": False,
    "description": (
        "Retrospective end-of-UTC-day comparison of completed local daily marks. Tokyo and "
        "New York closes and ECB reference fixings are not simultaneous. A same-date US "
        "close must not be used as information available at the earlier Tokyo close. "
        "Daily source timestamps are not publication or closing timestamps."
    ),
}


def benchmark_catalog() -> dict[str, Any]:
    return {
        "catalog_version": "benchmarks-v1",
        "defaults": {
            "benchmark_id": "topix",
            "benchmark_currency": "JPY",
            "dividend_treatment": "price_return",
            "alignment": "utc_day_end",
        },
        "records": [
            {
                "benchmark_id": key,
                **spec,
                "supported_currencies": ["JPY", "USD"],
                "supported_dividend_treatments": ["price_return"],
                "unsupported_dividend_treatments": {
                    "gross_total_return": "No verified total-return history adapter is selected.",
                    "net_total_return": "No verified tax/net-dividend history adapter is selected.",
                },
                "operator_enablement": "ATLAS_ENABLE_YAHOO_FINANCE=1"
                if spec["source_id"] == "yahoo_finance"
                else None,
                "source_kind": "primary" if key == "topix" else "secondary_aggregator",
                "no_etf_or_futures_proxy": True,
            }
            for key, spec in BENCHMARKS.items()
        ],
        "alignment_policy": ALIGNMENT,
        "max_calendar_years": 10,
        "max_returned_records": 500,
        "max_portfolio_observations": 500,
        "history_limit": (
            "TOPIX reads the five rotating JPX historical slots only; older requested dates "
            "remain missing. Yahoo history is bounded by available observations, not an "
            "assertion of complete exchange calendars. Split long exports into annual queries."
        ),
    }
