"""Registered overseas securities, quote units, local dates and existing interfaces."""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from importlib.resources import files
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import yahoo_identity, yahoo_profile, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_equities import chart_result, price_rows
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.server import create_app
from atlas_terminal.yahoo_risk import YahooRiskSpec, panel_from_prices

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2026-10-19", "end_date": "2026-10-23"}
MARKETS = [
    ("005930.KS", "KRW", "KSC", "Asia/Seoul"),
    ("247540.KQ", "KRW", "KOE", "Asia/Seoul"),
    ("VOD.L", "GBp", "LSE", "Europe/London"),
    ("SAP.DE", "EUR", "GER", "Europe/Berlin"),
    ("TTE.PA", "EUR", "PAR", "Europe/Paris"),
    ("ASML.AS", "EUR", "AMS", "Europe/Amsterdam"),
    ("ENI.MI", "EUR", "MIL", "Europe/Rome"),
    ("SAN.MC", "EUR", "MCE", "Europe/Madrid"),
    ("NESN.SW", "CHF", "EBS", "Europe/Zurich"),
    ("VOLV-B.ST", "SEK", "STO", "Europe/Stockholm"),
    ("NOVO-B.CO", "DKK", "CPH", "Europe/Copenhagen"),
    ("EQNR.OL", "NOK", "OSL", "Europe/Oslo"),
    ("NOKIA.HE", "EUR", "HEL", "Europe/Helsinki"),
    ("ABI.BR", "EUR", "BRU", "Europe/Brussels"),
    ("OMV.VI", "EUR", "VIE", "Europe/Amsterdam"),
    ("KRX.IR", "EUR", "ISE", "Europe/Dublin"),
    ("EDP.LS", "EUR", "LIS", "Europe/Lisbon"),
    ("PKN.WA", "PLN", "WSE", "Europe/Warsaw"),
]


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)


def query(symbol="005930.KS", dataset="equity_prices", **extra):
    return {"dataset": dataset, "identifier": symbol, **DATES, **extra}


@pytest.mark.parametrize("symbol,currency,exchange,timezone", MARKETS)
def test_registered_venues_have_original_units_local_dates_and_null_zero_distinction(
    symbol, currency, exchange, timezone
):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(symbol))
    instrument = result["instrument"]
    assert (instrument["currency"], instrument["exchange"], instrument["exchange_timezone"]) == (
        currency,
        exchange,
        timezone,
    )
    assert instrument["denomination_currency"] == ("GBP" if currency == "GBp" else currency)
    assert len(result["records"]) == 5 and result["records"][0]["date"] == "2026-10-23"
    assert result["records"][0]["volume"] is None and result["records"][2]["volume"] == "0"
    assert result["records"][0]["close"] == "105"
    assert result["records"][0]["unit"] == currency + "_per_share"
    assert result["records"][0]["source"]["json_pointer"].endswith("/close/4")


@pytest.mark.parametrize("symbol,currency,exchange,timezone", MARKETS)
def test_actions_preserve_source_dates_and_do_not_rescale_cash_amounts(
    symbol, currency, exchange, timezone
):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(symbol, "equity_actions"))
    split, dividend = result["records"]
    assert (split["date"], split["split_ratio"]) == ("2026-10-21", "2:1")
    assert (dividend["date"], dividend["currency"], dividend["value"]) == (
        "2026-10-20",
        currency,
        "0.25",
    )
    assert dividend["currency_basis"] == "chart_quote_currency"


@pytest.mark.parametrize("symbol", [r[0] for r in MARKETS])
@pytest.mark.parametrize(
    "dataset", ["equity_prices", "equity_actions", "price_analytics", "intraday_prices"]
)
def test_shared_query_schema_and_canonical_endpoint_accept_each_registered_venue(symbol, dataset):
    q = query(symbol, dataset)
    if dataset == "intraday_prices":
        q.update(interval="5m")
    parsed = parse_research(q)
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(q)
    assert parsed.identifier == symbol
    url = yahoo_url(symbol, **{k.removesuffix("_date"): v for k, v in DATES.items()})
    assert yahoo_identity(url) == (symbol, DATES["start_date"], DATES["end_date"])
    PublicTransport.validate_url(url)


@pytest.mark.parametrize(
    "symbol",
    [
        "005930",
        "005930.KR",
        "00593.KS",
        "SAP.de",
        "SAP.DE/../../x",
        "*.L",
        "SAP.HK",
        "^FTSE",
        "AA;BB.L",
    ],
)
def test_unsupported_or_unsafe_symbols_are_rejected_before_transport(symbol):
    with pytest.raises(AtlasError):
        parse_research(query(symbol))


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "USD"),
        ("currency", {}),
        ("exchangeTimezoneName", "Asia/Tokyo"),
        ("exchangeName", "KOE"),
        ("exchangeName", []),
        ("instrumentType", "INDEX"),
        ("instrumentType", {}),
        ("symbol", "247540.KQ"),
    ],
)
def test_kospi_cannot_silently_change_currency_venue_timezone_type_or_symbol(field, value):
    raw = json.loads(RESOURCE.joinpath("yahoo_chart_005930.KS.json").read_bytes())
    raw["chart"]["result"][0]["meta"][field] = value
    with pytest.raises(AtlasError):
        chart_result(
            Snapshot(
                json.dumps(raw).encode(),
                "https://fixtures.atlas.invalid/chart",
                "2026-10-27T02:00:00Z",
            ),
            "005930.KS",
        )


@pytest.mark.parametrize("currency,scale", [("GBp", "0.01"), ("GBX", "0.01"), ("GBP", "1")])
def test_british_quote_unit_is_preserved_exactly_without_silent_hundredfold_conversion(
    currency, scale
):
    raw = json.loads(RESOURCE.joinpath("yahoo_chart_VOD.L.json").read_bytes())
    raw["chart"]["result"][0]["meta"]["currency"] = currency
    snap = Snapshot(
        json.dumps(raw).encode(), "https://fixtures.atlas.invalid/uk", "2026-10-27T02:00:00Z"
    )
    chart, instrument = chart_result(snap, "VOD.L")
    rows, _ = price_rows(chart, snap, ResearchQuery(**query("VOD.L")))
    assert instrument["denomination_currency"] == "GBP" and instrument["quote_unit_scale"] == scale
    assert rows[0]["close"] == "101" and rows[0]["currency"] == currency
    assert rows[0]["unit"] == currency + "_per_share"


def test_source_local_calendar_bounds_account_for_the_british_fall_dst_transition():
    url = yahoo_url("VOD.L", "2026-10-25", "2026-10-25")
    params = parse_qs(url.split("?", 1)[1])
    assert int(params["period2"][0]) - int(params["period1"][0]) == 25 * 3600
    assert yahoo_identity(url) == ("VOD.L", "2026-10-25", "2026-10-25")


@pytest.mark.parametrize("symbol", ["005930.KS", "VOD.L", "SAP.DE"])
def test_intraday_retains_market_and_quote_units_without_adding_price_adjustment_claims(symbol):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            query(
                symbol,
                "intraday_prices",
                start_date="2026-10-28",
                end_date="2026-10-28",
                interval="5m",
            )
        )
    assert len(result["records"]) == 3
    assert result["records"][0]["currency"] == result["instrument"]["currency"]
    assert result["records"][0]["unit"] == result["instrument"]["price_unit"]
    assert not result["records"][0]["price_adjustment_verified"]


def test_analytics_keep_the_pence_basis_and_comparisons_reject_different_national_markets():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("VOD.L", "price_analytics", metrics=["return_1d"]))
        assert result["instrument"]["currency"] == "GBp"
        compared = atlas.get_research(
            {"dataset": "price_comparison", "identifiers": ["VOD.L", "ISF.L"], **DATES}
        )
        assert compared["records"]
    for symbols in [["SAP.DE", "TTE.PA"], ["ASML.AS", "OMV.VI"], ["005930.KS", "VOD.L"]]:
        with pytest.raises(AtlasError):
            parse_research({"dataset": "price_comparison", "identifiers": symbols, **DATES})


@pytest.mark.parametrize("symbol", ["005930.KS", "VOD.L", "SAP.DE"])
def test_new_markets_still_require_operator_enablement_before_any_source_read(symbol):
    calls = []

    class NoReads:
        def get(self, *args):
            calls.append(args)
            raise AssertionError("Disabled optional source was accessed")

        def close(self):
            pass

    with Atlas("public") as atlas:
        atlas._research.public = NoReads()
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(query(symbol))
        assert caught.value.code == "CONFIG_REQUIRED" and not calls


def test_price_comparison_rejects_mixed_pound_and_pence_quote_units_without_conversion():
    class MixedUnits(PublicFixtures):
        def get(self, url, ttl):
            source = super().get(url, ttl)
            raw = json.loads(source.body)
            if raw["chart"]["result"][0]["meta"]["symbol"] == "ISF.L":
                raw["chart"]["result"][0]["meta"]["currency"] = "GBP"
            return Snapshot(json.dumps(raw).encode(), source.url, source.retrieved_at)

    with Atlas("fixture") as atlas:
        atlas._research.public = MixedUnits()
        with pytest.raises(AtlasError):
            atlas.get_research(
                {"dataset": "price_comparison", "identifiers": ["VOD.L", "ISF.L"], **DATES}
            )


def risk_histories(symbols, quote_currencies=None):
    results = []
    for symbol in symbols:
        raw = json.loads(RESOURCE.joinpath(f"yahoo_chart_{symbol}.json").read_bytes())
        chart = raw["chart"]["result"][0]
        if quote_currencies and symbol in quote_currencies:
            chart["meta"]["currency"] = quote_currencies[symbol]
        dates = []
        day = datetime(2026, 1, 5, 10, tzinfo=ZoneInfo(yahoo_profile(symbol)["timezone"]))
        while len(dates) < 80:
            if day.weekday() < 5:
                dates.append(int(day.timestamp()))
            day += timedelta(days=1)
        values = [100 + i + (i % 3) for i in range(80)]
        chart["timestamp"] = dates
        chart["indicators"] = {
            "quote": [{k: values for k in ("open", "high", "low", "close", "volume")}],
            "adjclose": [{"adjclose": values}],
        }
        chart["events"] = {}

        class Recorded(PublicFixtures):
            def __init__(self, body):
                self.body = body

            def get(self, url, ttl):
                return Snapshot(self.body, url, "2026-10-27T02:00:00Z")

        with Atlas("fixture") as atlas:
            atlas._research.public = Recorded(json.dumps(raw).encode())
            results.append(
                atlas.get_research(
                    query(symbol, start_date="2026-01-01", end_date="2026-05-31", limit=500)
                )
            )
    return results


@pytest.mark.parametrize(
    "symbols,currency",
    [
        (["VOD.L", "ISF.L"], "GBP"),
        (["005930.KS", "069500.KS"], "KRW"),
        (["SAP.DE", "EXS1.DE"], "EUR"),
    ],
)
def test_named_risk_inputs_use_denomination_currency_and_keep_per_series_quote_units(
    symbols, currency
):
    spec = YahooRiskSpec(
        connection_id="overseas",
        kind="yahoo_finance",
        symbols=symbols[:-1],
        factor_symbol=symbols[-1],
        start_date="2026-01-01",
        end_date="2026-05-31",
        personal_use_acknowledged=True,
    )
    results = risk_histories(symbols, {"ISF.L": "GBP"})
    loaded = panel_from_prices(spec, results)
    assert loaded.panel["manifest"]["currency"] == currency
    assert len(loaded.panel["sessions"]) == 79
    assert loaded.normalization["quote_units"][symbols[0]]["return_scale_cancels"]
    if currency == "GBP":
        assert loaded.normalization["quote_units"]["VOD.L"]["quote_unit_scale"] == "0.01"
        assert loaded.normalization["quote_units"]["ISF.L"]["quote_unit_scale"] == "1"
    results[0]["records"][0]["unit"] = "USD_per_share"
    with pytest.raises(AtlasError):
        panel_from_prices(spec, results)


def test_cli_http_sdk_return_identical_records_and_the_same_source_unit_metadata():
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query("VOD.L"))
    with TestClient(create_app(provider="fixture")) as client:
        r = client.post("/v1/research", json=query("VOD.L"))
        assert r.status_code == 200 and r.json()["records"] == expected["records"]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "equity_prices",
            "--identifier",
            "VOD.L",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["records"] == expected["records"]


@pytest.mark.asyncio
async def test_actual_stdio_mcp_accepts_the_new_markets_and_resolves_original_evidence():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
    )
    async with (
        stdio_client(params) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        await session.initialize()
        for symbol in ["005930.KS", "VOD.L", "SAP.DE"]:
            result = await session.call_tool(
                "atlas_get_research",
                {"query": query(symbol, response={"view": "compact", "fields": ["close"]})},
            )
            assert not result.is_error
            row = result.structured_content["records"][0]
            assert row["quote_unit_scale"] == ("0.01" if symbol == "VOD.L" else "1")
            evidence = await session.call_tool(
                "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
            )
            assert not evidence.is_error
