"""Fictional observed-price risk integration, missingness, timing and exact returns."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Context, Decimal, Inexact, localcontext

import pytest
from pydantic import ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_connections import RiskConnectionConfig
from atlas_terminal.util import canonical, digest
from atlas_terminal.yahoo_risk import YahooRiskConnector, YahooRiskSpec, panel_from_prices

CAPTURE = "2026-04-23T02:00:00Z"


def spec(**changes):
    return YahooRiskSpec.model_validate(
        {
            "connection_id": "yahoo_jp",
            "kind": "yahoo_finance",
            "symbols": ["7203.T", "6758.T"],
            "factor_symbol": "1306.T",
            "start_date": "2026-01-01",
            "end_date": "2026-05-31",
            "personal_use_acknowledged": True,
            **changes,
        }
    )


def histories():
    dates = []
    day = date(2026, 1, 1)
    while len(dates) < 80:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    results = []
    for j, symbol in enumerate(["7203.T", "6758.T", "1306.T"]):
        source = {
            "raw_source_url": f"https://fixtures.atlas.invalid/{symbol}",
            "raw_sha256": digest(symbol.encode()),
            "retrieved_at": CAPTURE,
            "json_pointer": "",
        }
        rows = []
        for i, day in enumerate(dates):
            price = str(100 + j * 17 + (i * (j + 2)) % 11 + i)
            rows.append(
                {
                    "date": day,
                    "adjusted_close": price,
                    "price_basis": "yahoo_split_adjusted",
                    "adjusted_close_basis": "yahoo_split_and_dividend_adjusted",
                    "session_status": "completed",
                    "field_sources": {"adjusted_close": source | {"json_pointer": f"/prices/{i}"}},
                }
            )
        results.append(
            {
                "instrument": {
                    "symbol": symbol,
                    "currency": "JPY",
                    "source_instrument_type": "ETF" if j == 2 else "EQUITY",
                },
                "coverage": {"truncated": False},
                "is_synthetic": True,
                "sources": [source],
                "records": list(reversed(rows)),
            }
        )
    return results


def test_adjusted_prices_exact_rounded_returns_all_input_evidence_and_no_capitalization():
    results = histories()
    with localcontext(Context(prec=5, traps=[Inexact])):
        loaded = panel_from_prices(spec(), results)
    row = loaded.panel["sessions"][0]
    # First return is 103 / 100 - 1; the factor return is 139 / 134 - 1.
    assert row["observations"][0]["total_return"] == "0.030000000000000000"
    with localcontext(Context(prec=60)):
        expected = (Decimal(139) / 134 - 1).quantize(Decimal("1e-18"))
    assert Decimal(row["factor_returns"]["etf_proxy"]) == expected
    assert all(
        r["available_at"] == CAPTURE.replace("Z", "+00:00") for r in loaded.panel["sessions"]
    )
    assert loaded.panel["manifest"]["factor_convention"] == "total_return"
    assert "fundamentals" not in loaded.panel and not loaded.snapshot_atomic
    for result, document in zip(results, loaded.documents, strict=True):
        assert document["sha256"] == digest(canonical(result))
        symbol = result["instrument"]["symbol"]
        prices = loaded.normalization["price_evidence"][symbol]
        assert len(prices) == 80
        assert prices[0]["source"] == result["records"][0]["field_sources"]["adjusted_close"]
    assert loaded.normalization["point_in_time"] is False
    assert (
        loaded.normalization["factor_basis"] == "selected ETF proxy, not an official market factor"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "mismatch",
        "duplicate",
        "basis",
        "session",
        "zero",
        "negative",
        "nonfinite",
        "truncated",
        "currency",
        "symbol",
        "factor_type",
        "outside",
        "bad_date",
        "naive_capture",
        "early_capture",
    ],
)
def test_risk_input_rejects_missing_misaligned_invalid_or_unavailable_prices(fault):
    results = histories()
    if fault == "missing":
        results[0]["records"][0]["adjusted_close"] = None
    elif fault == "mismatch":
        results[1]["records"].pop()
    elif fault == "duplicate":
        results[0]["records"][1] = deepcopy(results[0]["records"][0])
    elif fault == "basis":
        results[0]["records"][0]["adjusted_close_basis"] = "raw"
    elif fault == "session":
        results[0]["records"][0]["session_status"] = "unknown"
    elif fault in {"zero", "negative", "nonfinite"}:
        results[0]["records"][0]["adjusted_close"] = {
            "zero": "0",
            "negative": "-1",
            "nonfinite": "NaN",
        }[fault]
    elif fault == "truncated":
        results[0]["coverage"]["truncated"] = True
    elif fault == "currency":
        results[0]["instrument"]["currency"] = "USD"
    elif fault == "symbol":
        results[0]["instrument"]["symbol"] = "1111.T"
    elif fault == "factor_type":
        results[2]["instrument"]["source_instrument_type"] = "EQUITY"
    elif fault == "outside":
        results[0]["records"][0]["date"] = "2027-01-01"
    elif fault == "bad_date":
        results[0]["records"][0]["date"] = "2026-02-30"
    elif fault == "naive_capture":
        results[0]["sources"][0]["retrieved_at"] = "2026-10-27"
    elif fault == "early_capture":
        for result in results:
            result["sources"][0]["retrieved_at"] = "2026-01-02T00:00:00Z"
    with pytest.raises(AtlasError):
        panel_from_prices(spec(), results)


@pytest.mark.parametrize(
    "changes",
    [
        {"symbols": ["7203.T", "7203.T"]},
        {"factor_symbol": "7203.T"},
        {"personal_use_acknowledged": False},
        {"symbols": []},
        {"method": "cross_sectional"},
    ],
)
def test_operator_configuration_requires_unique_assets_separate_factor_and_personal_use(changes):
    with pytest.raises((ValidationError, AtlasError)):
        spec(**changes)


def test_named_configuration_preview_does_not_read_or_enable_network(tmp_path, monkeypatch):
    configured = spec()
    config = RiskConnectionConfig(schema_version="0.1.0", connections=[configured])
    assert isinstance(config.connections[0], YahooRiskSpec)
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
    calls = []

    class Connector(YahooRiskConnector):
        def read(self):
            calls.append(True)
            return panel_from_prices(configured, histories())

    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas.register_risk_connection("yahoo_jp", Connector(configured, tmp_path))
        run = {
            "action": "run",
            "connection_id": "yahoo_jp",
            "fit": {"method": "time_series", "as_of": CAPTURE, "lookback": 60},
            "positions": [{"asset_id": "7203.T", "weight": "1"}],
        }
        atlas.risk(run | {"preview": True})
        with pytest.raises(AtlasError) as caught:
            atlas.risk(run)
        assert caught.value.code == "CONFIG_REQUIRED" and not calls


def test_risk_cannot_backdate_availability_and_saves_all_price_receipts(tmp_path):
    configured = spec()

    class Connector(YahooRiskConnector):
        def read(self):
            return panel_from_prices(configured, histories())

    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas.register_risk_connection("yahoo_jp", Connector(configured, tmp_path))
        run = {
            "action": "run",
            "connection_id": "yahoo_jp",
            "allow_network": True,
            "fit": {"method": "time_series", "as_of": "2026-04-23T02:00:01Z", "lookback": 60},
            "positions": [
                {"asset_id": "7203.T", "weight": "0.5"},
                {"asset_id": "6758.T", "weight": "0.5"},
            ],
        }
        with pytest.raises(AtlasError):
            atlas.risk(deepcopy(run) | {"fit": run["fit"] | {"as_of": "2026-04-22T02:00:00Z"}})
        result = atlas.risk(run)
        assert result["data"]["analysis"]["currency"] == "JPY"
        receipts = result["data"]["input_evidence"]["documents"]
        assert {doc["label"] for doc in receipts} == {"7203.T", "6758.T", "1306.T"}


def test_connector_requires_explicit_yahoo_enablement_even_for_a_cached_read(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
    with pytest.raises(AtlasError) as caught:
        YahooRiskConnector(spec(), tmp_path).read()
    assert caught.value.code == "CONFIG_REQUIRED"
