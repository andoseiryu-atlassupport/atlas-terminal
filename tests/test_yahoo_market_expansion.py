"""Fictional cross-market prices: identity, local dates, units and risk boundaries."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import yahoo_identity, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_equities import chart_result, price_rows, yahoo_equities
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.yahoo_risk import YahooRiskSpec, panel_from_prices


def chart(symbol, kind, currency, exchange, timezone, *, values=None, days=None):
    """Invented numeric observations; names identify requested fixture symbols only."""
    values = values or ["100.123456789012345678", "101.25"]
    days = days or ["2026-10-23", "2026-10-26"]
    tz = ZoneInfo(timezone)
    timestamps = [
        int(datetime.fromisoformat(day + "T09:30:00").replace(tzinfo=tz).timestamp())
        for day in days
    ]
    result = {
        "meta": {
            "symbol": symbol,
            "instrumentType": kind,
            "currency": currency,
            "exchangeName": exchange,
            "exchangeTimezoneName": timezone,
            "dataGranularity": "1d",
        },
        "timestamp": timestamps,
        "indicators": {
            "quote": [
                {field: values for field in ("open", "high", "low", "close")} | {"volume": [0, 12]}
            ],
            "adjclose": [{"adjclose": values}],
        },
    }
    return result


def snapshot(result, retrieved="2026-10-28T12:00:00Z"):
    return Snapshot(
        json.dumps({"chart": {"result": [result], "error": None}}).encode(),
        "https://fixtures.atlas.invalid/market/chart.json",
        retrieved,
    )


@pytest.mark.parametrize(
    "symbol,kind,currency,exchange,timezone,unit,volume_unit",
    [
        ("AAPL", "EQUITY", "USD", "NMS", "America/New_York", "USD_per_share", "shares"),
        ("BRK-B", "EQUITY", "USD", "NYQ", "America/New_York", "USD_per_share", "shares"),
        ("SPY", "ETF", "USD", "PCX", "America/New_York", "USD_per_share", "shares"),
        (
            "^GSPC",
            "INDEX",
            "USD",
            "SNP",
            "America/New_York",
            "index_points",
            "source_reported_volume",
        ),
        ("^N225", "INDEX", "JPY", "OSA", "Asia/Tokyo", "index_points", "source_reported_volume"),
        (
            "^VIX",
            "INDEX",
            "USD",
            "CXI",
            "America/Chicago",
            "index_points",
            "source_reported_volume",
        ),
        (
            "EURUSD=X",
            "CURRENCY",
            "USD",
            "CCY",
            "Europe/London",
            "USD_per_EUR",
            "source_reported_volume",
        ),
        (
            "USDJPY=X",
            "CURRENCY",
            "JPY",
            "CCY",
            "Europe/London",
            "JPY_per_USD",
            "source_reported_volume",
        ),
        ("BTC-USD", "CRYPTOCURRENCY", "USD", "CCC", "UTC", "USD_per_BTC", "source_reported_volume"),
        (
            "GC=F",
            "FUTURE",
            "USD",
            "CMX",
            "America/New_York",
            "USD_per_source_quote_unit",
            "contracts",
        ),
        ("ES=F", "FUTURE", "USD", "CME", "America/New_York", "index_points", "contracts"),
    ],
)
def test_instrument_identity_units_dates_and_exact_evidence(
    symbol, kind, currency, exchange, timezone, unit, volume_unit
):
    result = chart(symbol, kind, currency, exchange, timezone)
    snap = snapshot(result)
    query = ResearchQuery.model_validate({"dataset": "equity_prices", "identifier": symbol})
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(
        query.model_dump(exclude_unset=True)
    )
    _, instrument = chart_result(snap, symbol)
    rows, excluded = price_rows(result, snap, query)
    assert instrument["currency"] == currency and instrument["exchange_timezone"] == timezone
    assert instrument["source_instrument_type"] == kind and excluded == 0
    assert rows[0]["date"] == "2026-10-23"
    assert rows[0]["unit"] == unit and rows[0]["volume_unit"] == volume_unit
    assert Decimal(rows[0]["close"]) == Decimal("100.123456789012345678")
    assert rows[0]["volume"] == "0"
    assert rows[0]["field_sources"]["close"]["json_pointer"].endswith("/quote/0/close/0")
    assert rows[0]["source"]["raw_sha256"] == snap.sha256
    assert rows[0]["price_basis"] == (
        "yahoo_split_adjusted" if kind in {"EQUITY", "ETF"} else "yahoo_reported_daily"
    )
    if kind not in {"EQUITY", "ETF"}:
        assert rows[0]["adjusted_close_basis"] == "yahoo_reported_adjusted_close"


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "CAD"),
        ("exchangeName", "TOR"),
        ("exchangeTimezoneName", "Asia/Tokyo"),
        ("instrumentType", "MUTUALFUND"),
        ("symbol", "MSFT"),
        ("dataGranularity", "1h"),
    ],
)
def test_us_source_identity_cannot_silently_change_market(field, value):
    result = chart("AAPL", "EQUITY", "USD", "NMS", "America/New_York")
    result["meta"][field] = value
    with pytest.raises(AtlasError) as error:
        chart_result(snapshot(result), "AAPL")
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_ny_dates_and_dst_request_boundaries_are_not_tokyo_or_fixed_offset():
    url = yahoo_url("AAPL", "2026-03-06", "2026-03-09")
    params = parse_qs(url.split("?", 1)[1])
    assert params["period1"] == [str(int(datetime(2026, 3, 6, 5, tzinfo=UTC).timestamp()))]
    assert params["period2"] == [str(int(datetime(2026, 3, 10, 4, tzinfo=UTC).timestamp()))]
    assert yahoo_identity(url) == ("AAPL", "2026-03-06", "2026-03-09")
    result = chart("AAPL", "EQUITY", "USD", "NMS", "America/New_York")
    result["timestamp"][0] = int(datetime(2026, 10, 24, 0, 30, tzinfo=UTC).timestamp())
    rows, _ = price_rows(
        result, snapshot(result), ResearchQuery(dataset="equity_prices", identifier="AAPL")
    )
    assert rows[0]["date"] == "2026-10-23"


@pytest.mark.parametrize("symbol", ["^GSPC", "EURUSD=X", "BTC-USD", "CL=F"])
def test_reference_urls_round_trip_with_canonical_encoding_and_no_extra_parameters(symbol):
    url = yahoo_url(symbol, "2026-10-01", "2026-10-23")
    assert yahoo_identity(url) == (symbol, "2026-10-01", "2026-10-23")
    assert yahoo_identity(url + "&crumb=secret") is None
    assert yahoo_identity(url.replace("query1", "query2")) is None
    if "^" in symbol or "=" in symbol:
        assert yahoo_identity(url.replace("%5E", "^").replace("%3D", "=")) is None


def test_negative_and_zero_futures_quotes_are_not_repaired_or_interpreted_as_equity_prices():
    result = chart("CL=F", "FUTURE", "USD", "NYM", "America/New_York", values=["-37.63", "0"])
    query = ResearchQuery(dataset="equity_prices", identifier="CL=F")
    rows, _ = price_rows(result, snapshot(result), query)
    assert [r["close"] for r in rows] == ["-37.63", "0"]
    assert all(r["volume_unit"] == "contracts" for r in rows)


def test_nonsecurity_current_date_is_excluded_even_with_a_reported_regular_session_end():
    result = chart(
        "BTC-USD", "CRYPTOCURRENCY", "USD", "CCC", "UTC", days=["2026-10-26", "2026-10-27"]
    )
    result["meta"]["currentTradingPeriod"] = {
        "regular": {"end": int(datetime(2026, 10, 27, 1, tzinfo=UTC).timestamp())}
    }
    rows, excluded = price_rows(
        result,
        snapshot(result, "2026-10-27T12:00:00Z"),
        ResearchQuery(dataset="equity_prices", identifier="BTC-USD"),
    )
    assert [r["date"] for r in rows] == ["2026-10-26"] and excluded == 1


def test_reference_actions_fail_before_source_access():
    class NeverRead:
        def get(self, *args):
            pytest.fail("Reference corporate actions should fail before fetching")

    with pytest.raises(AtlasError) as error:
        yahoo_equities(NeverRead(), ResearchQuery(dataset="equity_actions", identifier="^GSPC"))
    assert error.value.code == "INVALID_INPUT"


def us_spec(**updates):
    return YahooRiskSpec.model_validate(
        {
            "connection_id": "us_prices",
            "kind": "yahoo_finance",
            "symbols": ["AAPL", "MSFT"],
            "factor_symbol": "SPY",
            "start_date": "2026-04-01",
            "end_date": "2026-10-23",
            "personal_use_acknowledged": True,
            **updates,
        }
    )


def test_us_fixture_prices_feed_usd_risk_with_one_market_and_source_local_boundaries(tmp_path):
    configured = us_spec()
    with Atlas("fixture", directory=tmp_path) as atlas:
        prices = [
            atlas.get_research(
                {
                    "dataset": "equity_prices",
                    "identifier": symbol,
                    "start_date": configured.start_date,
                    "end_date": configured.end_date,
                    "limit": 500,
                }
            )
            for symbol in [*configured.symbols, configured.factor_symbol]
        ]
    loaded = panel_from_prices(configured, prices)
    manifest = loaded.panel["manifest"]
    assert manifest["currency"] == "USD" and manifest["is_synthetic"]
    assert all(row["currency"] == "USD" for row in manifest["universe"])
    assert loaded.panel["sessions"][0]["end_at"].endswith("T04:00:00+00:00")
    assert "America/New_York" in loaded.normalization["session_boundary_basis"]
    assert len(loaded.panel["sessions"]) > 120


@pytest.mark.parametrize(
    "updates",
    [
        {"symbols": ["AAPL", "7203.T"]},
        {"factor_symbol": "1306.T"},
        {"symbols": ["^GSPC"]},
        {"symbols": ["CL=F"]},
        {"factor_symbol": "BTC-USD"},
    ],
)
def test_risk_configuration_rejects_mixed_markets_and_nonsecurity_inputs(updates):
    with pytest.raises((ValueError, AtlasError)):
        us_spec(**updates)


def test_explicit_usdcad_alias_preserves_requested_and_source_identity():
    result = chart("CAD=X", "CURRENCY", "CAD", "CCY", "Europe/London")
    _, instrument = chart_result(snapshot(result), "USDCAD=X")
    assert instrument["symbol"] == "USDCAD=X"
    assert instrument["source_symbol"] == "CAD=X"
    result["meta"]["symbol"] = "EURCAD=X"
    with pytest.raises(AtlasError):
        chart_result(snapshot(result), "USDCAD=X")


def test_fx_inconsistent_ohlc_retains_exact_source_values_and_fails_strict_quality(tmp_path):
    result = chart("GBPUSD=X", "CURRENCY", "USD", "CCY", "Europe/London")
    # Do not alias arrays when changing just one source field in a test.
    result["indicators"]["quote"][0].update(
        open=["100", "101.25"],
        high=["102", "101.25"],
        low=["101", "101.25"],
        close=["100", "101.25"],
    )

    class Source:
        synthetic = True

        def get(self, url, ttl):
            return snapshot(result)

        def close(self):
            pass

    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas._research.public = Source()
        query = {"dataset": "equity_prices", "identifier": "GBPUSD=X"}
        got = atlas.get_research(query)
        bad = got["records"][-1]
        assert bad["close"] == "100" and bad["low"] == "101"
        assert bad["status"] == "ambiguous"
        assert bad["ohlc_consistent"] is False
        assert bad["quality_flags"] == ["ohlc_bounds_conflict"]
        assert got["coverage"]["inconsistent_ohlc_records"] == 1
        assert got["coverage"]["partial"] is True
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query | {"requirements": {"allow_missing": False}})
        assert error.value.code == "QUALITY_NOT_MET"
