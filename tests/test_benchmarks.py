"""Fictional/offline contracts; live source verification is a separate opt-in script."""

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal, localcontext
from importlib.resources import files
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.benchmark_catalog import BENCHMARKS
from atlas_terminal.benchmark_metrics import portfolio_comparison
from atlas_terminal.benchmark_models import BenchmarkPortfolio, BenchmarkSettings
from atlas_terminal.benchmark_sources import topix_identity, topix_url
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import ECB_FX_URL, PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_views import ResearchViews
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical

ROOT = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2026-10-13", "end_date": "2026-10-19"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)


class Capture(PublicFixtures):
    def __init__(self, transform=None):
        self.calls = []
        self.snapshots = {}
        self.transform = transform

    def get(self, url, ttl):
        self.calls.append(url)
        snapshot = super().get(url, ttl)
        if self.transform:
            snapshot = self.transform(url, snapshot, self.calls)
        self.snapshots[snapshot.sha256] = snapshot
        return snapshot


def query(**changes):
    return {"dataset": "benchmark_prices", **DATES, **changes}


def run(transport=None, **changes):
    with Atlas("fixture") as atlas:
        atlas._research.public = transport or Capture()
        return atlas.get_research(query(**changes))


def pointer(document, path):
    for part in path.split("/")[1:]:
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def test_catalog_is_offline_and_nasdaq_composite_is_not_nasdaq100():
    with Atlas("public") as atlas:
        catalog = atlas.benchmark_catalog()
        assert atlas._research.public is None
    rows = {r["benchmark_id"]: r for r in catalog["records"]}
    assert len(rows) == 6
    assert rows["nasdaq_composite"]["symbol"] == "^IXIC"
    assert rows["nasdaq100"]["symbol"] == "^NDX"
    assert rows["topix"]["history"] == "latest_five_published_sessions"
    assert all(r["supported_dividend_treatments"] == ["price_return"] for r in rows.values())


@pytest.mark.parametrize("benchmark", BENCHMARKS)
def test_every_index_uses_its_own_close_and_source_identity(benchmark):
    captured = Capture()
    result = run(
        captured,
        benchmark_id=benchmark,
        benchmark_currency=BENCHMARKS[benchmark]["native_currency"],
    )
    assert len(result["records"]) == 5
    for row in result["records"]:
        evidence = row["inputs"][0]["source"]
        snapshot = captured.snapshots[evidence["raw_sha256"]]
        original = pointer(snapshot.data, evidence["json_pointer"])
        assert Decimal(original.replace(",", "")) == Decimal(row["value"])
        assert row["dividend_treatment"] == "price_return"
        assert row["value"] != "999"  # Deliberately different synthetic adjusted close.
        assert row["value"] != "999.99"  # Separate JPX total-return cell is never selected.
    assert result["is_synthetic"]


def test_fx_conversion_preserves_exact_inputs_and_missing_calendar_days():
    capture = Capture()
    result = run(capture, benchmark_id="sp500")
    rows = {r["date"]: r for r in result["records"]}
    assert rows["2026-10-19"]["index_value"] == "108.125"
    assert Decimal(rows["2026-10-19"]["value"]) == Decimal("12975")
    assert Decimal(rows["2026-10-19"]["fx_value"]) == Decimal("120")
    fx = rows["2026-10-19"]["inputs"][1]
    assert [i["value"] for i in fx["inputs"]] == ["1.2500", "150.00"]
    assert all(i["date"] == "2026-10-19" for i in fx["inputs"])
    assert rows["2026-10-15"]["value"] is None
    assert rows["2026-10-15"]["reason"] == "missing_same_date_fx"
    assert rows["2026-10-14"]["value"] is None
    missing = {r["date"] for r in result["coverage"]["missing_dates"]}
    assert {"2026-10-17", "2026-10-18"} <= missing
    assert Counter(capture.calls)[ECB_FX_URL] == 1
    assert not result["alignment_policy"]["synchronous_closes"]
    assert result["alignment_policy"]["valuation_timezone"] == "UTC"


def test_inverse_jpy_to_usd_and_native_currency_do_not_share_values():
    native = run(benchmark_id="nikkei225")
    inverse = run(benchmark_id="nikkei225", benchmark_currency="USD")
    row = inverse["records"][0]
    assert row["inputs"][1]["formula"] == "quote_per_EUR / base_per_EUR"
    with localcontext() as ctx:
        ctx.prec = 28
        fx = Decimal("1.2500") / Decimal("150.00")
    with localcontext() as ctx:
        ctx.prec = 256
        assert Decimal(row["value"]) == Decimal("508") * fx
    assert inverse["selection_key"] != native["selection_key"]


@pytest.mark.parametrize(
    "changes",
    [
        {"dividend_treatment": "gross_total_return"},
        {"dividend_treatment": "net_total_return"},
        {"benchmark_id": "qqq"},
        {"benchmark_id": "nasdaq"},
        {"benchmark_currency": "EUR"},
        {"alignment": "tokyo_close"},
        {"start_date": "2000-01-01"},
        {"end_date": "2026-02-30"},
        {"identifier": "^GSPC"},
        {"price_field": "adjusted_close"},
    ],
)
def test_unsupported_conditions_fail_before_source_reads(changes):
    capture = Capture()
    with pytest.raises(AtlasError) as error:
        run(capture, **changes)
    assert error.value.code == "INVALID_INPUT"
    assert capture.calls == []


@pytest.mark.parametrize(
    "fault", ["duplicate", "currency", "timezone", "instrument", "symbol", "missing_close"]
)
def test_source_metadata_drift_and_missing_index_values(fault):
    def changed(url, snap, calls):
        if not url.startswith("https://query1.finance.yahoo.com"):
            return snap
        data = json.loads(snap.body)
        result = data["chart"]["result"][0]
        if fault == "duplicate":
            result["timestamp"][1] = result["timestamp"][0]
        elif fault == "missing_close":
            result["indicators"]["quote"][0]["close"][4] = None
        else:
            field, value = {
                "currency": ("currency", "JPY"),
                "timezone": ("exchangeTimezoneName", "Asia/Tokyo"),
                "instrument": ("instrumentType", "ETF"),
                "symbol": ("symbol", "^NDX"),
            }[fault]
            result["meta"][field] = value
        return Snapshot(json.dumps(data).encode(), snap.url, snap.retrieved_at)

    if fault == "missing_close":
        result = run(Capture(changed), benchmark_id="sp500")
        assert result["records"][0]["value"] is None
        assert result["records"][0]["reason"] == "missing_index_close"
    else:
        with pytest.raises(AtlasError) as error:
            run(Capture(changed), benchmark_id="sp500")
        assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("fault", ["rotation", "conflict", "total", "intraday", "future"])
def test_jpx_slot_rotation_conflicts_wrong_index_and_unfinished_data_fail(fault):
    def changed(url, snap, calls):
        body = snap.body
        if url == topix_url(1, time=True):
            if fault == "rotation" and calls.count(url) == 2:
                body = body.replace(b"20261019", b"20261020")
            elif fault == "intraday":
                body = body.replace(b"1659", b"1200")
            elif fault == "future":
                body = body.replace(b"20261019", b"20991019")
        if url == topix_url(1):
            data = json.loads(body)
            if fault == "conflict":
                data["Topix"]["Topix"]["currentPrice"] = "888.88"
            elif fault == "total":
                data["Topix"]["Topix"]["marketName"] = "配当込みTOPIX"
            body = json.dumps(data).encode()
        return Snapshot(body, snap.url, snap.retrieved_at)

    with pytest.raises(AtlasError) as error:
        run(Capture(changed))
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_old_topix_window_is_empty_not_a_proxy_or_fabricated_history():
    result = run(start_date="2000-01-01", end_date="2000-01-03")
    assert result["records"] == []
    assert result["coverage"]["usable_records"] == 0
    assert result["coverage"]["available_start"] == "2026-10-13"
    assert len(result["coverage"]["missing_dates"]) == 3


def portfolio():
    return BenchmarkPortfolio.model_validate(
        {
            "currency": "JPY",
            "dividend_treatment": "price_return",
            "valuation_basis": "utc_day_end",
            "value_basis": "unitized_nav_external_flows_removed",
            "risk_free_rate_annual": "0",
            "observations": [
                {"date": f"2026-10-{i + 13}", "value": value}
                for i, value in enumerate(["100", "110", "99", "108.9"])
            ],
        }
    )


def benchmark(values):
    return [{"date": f"2026-10-{i + 13}", "value": v} for i, v in enumerate(values)]


def test_beta_excess_cagr_and_sharpe_have_independently_checkable_results():
    result = portfolio_comparison(portfolio(), benchmark(["100", "105", "99.75", "104.7375"]))
    relative = result["relative_metrics"]
    own = result["portfolio_metrics"]
    assert Decimal(relative["beta"]["value"]) == 2
    assert Decimal(relative["excess_cumulative_return"]["value"]) == Decimal("0.041625")
    with localcontext() as ctx:
        ctx.prec = 80
        expected = Decimal("1.089") ** (Decimal("365.2425") / 3) - 1
        assert abs(Decimal(own["cagr"]["value"]) - expected) < Decimal("1e-17")
        assert abs(Decimal(own["sharpe_ratio"]["value"]) - Decimal(21).sqrt()) < Decimal("1e-17")


def test_switching_benchmark_or_its_holidays_cannot_change_portfolio_metrics():
    histories = [
        ["100", "105", "99.75", "104.7375"],
        ["100", "110", "99", "108.9"],
        ["100", None, "99", "108.9"],
    ]
    results = [portfolio_comparison(portfolio(), benchmark(h)) for h in histories]
    assert (
        results[0]["portfolio_metrics"]
        == results[1]["portfolio_metrics"]
        == results[2]["portfolio_metrics"]
    )
    assert [Decimal(r["relative_metrics"]["beta"]["value"]) for r in results[:2]] == [2, 1]
    partial = results[2]
    assert partial["relative_metrics"]["beta"]["value"] is None
    assert partial["relative_metrics"]["excess_cumulative_return"]["value"] is None
    assert partial["comparison_period"]["matched_intervals"] == 1
    assert [r["excess_return"] is None for r in partial["intervals"]] == [True, True, False]


def test_zero_variance_missing_nav_and_missing_risk_free_stay_unavailable():
    p = portfolio()
    result = portfolio_comparison(p, benchmark(["100"] * 4))
    assert result["relative_metrics"]["beta"]["reason"] == "zero_benchmark_return_variance"
    p.risk_free_rate_annual = None
    result = portfolio_comparison(p, benchmark(["100"] * 4))
    assert result["portfolio_metrics"]["sharpe_ratio"]["reason"] == "risk_free_rate_not_supplied"
    p.observations[1].value = None
    result = portfolio_comparison(p, benchmark(["100"] * 4))
    assert result["portfolio_metrics"]["cagr"]["value"] is None
    assert result["portfolio_metrics"]["cumulative_return"]["value"] is None


def test_legacy_selection_persists_defaults_and_all_conditions_bind_cursors(tmp_path):
    assert BenchmarkSettings().model_dump() == {
        "benchmark_id": "topix",
        "benchmark_currency": "JPY",
        "dividend_treatment": "price_return",
        "alignment": "utc_day_end",
    }
    result = run()
    assert result["query"]["benchmark_id"] == "topix"
    views = ResearchViews(tmp_path / "views.sqlite3", synthetic=True)
    implicit = ResearchQuery.model_validate(query())
    explicit = ResearchQuery.model_validate(query(**BenchmarkSettings().model_dump()))
    assert views.fingerprint(implicit) == views.fingerprint(explicit)
    assert views.fingerprint(implicit) != views.fingerprint(
        ResearchQuery.model_validate(query(benchmark_id="sp500"))
    )
    assert views.fingerprint(implicit) != views.fingerprint(
        ResearchQuery.model_validate(query(benchmark_currency="USD"))
    )
    with Atlas("fixture") as atlas:
        first = atlas.get_research(
            query(response={"view": "compact", "page_size": 2, "fields": ["value"]})
        )
        row = first["records"][0]
        assert row["benchmark_id"] == "topix" and row["dividend_treatment"] == "price_return"
        evidence = atlas.get_evidence(row["inputs"][0]["source"]["evidence_id"])
        assert evidence["evidence"]["json_pointer"] == "/MainStockIndex/Topix/currentPrice"
        with pytest.raises(AtlasError):
            atlas.get_research(
                query(
                    benchmark_id="sp500",
                    response={
                        "view": "compact",
                        "page_size": 2,
                        "fields": ["value"],
                        "cursor": first["response"]["next_cursor"],
                    },
                )
            )


def test_price_and_fx_sources_are_shared_across_currency_results_and_gated(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_ENABLE_YAHOO_FINANCE", "1")
    requests = []

    def handler(request):
        requests.append(str(request.url))
        name = "fx.xml" if str(request.url) == ECB_FX_URL else "yahoo_chart_^GSPC.json"
        return httpx.Response(200, content=ROOT.joinpath(name).read_bytes())

    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        native = atlas.get_research(query(benchmark_id="sp500", benchmark_currency="USD"))
        converted = atlas.get_research(query(benchmark_id="sp500"))
        assert len(requests) == 2
        assert native["selection_key"] != converted["selection_key"]
        monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE")
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(query(benchmark_id="sp500"))
        assert exc.value.code == "CONFIG_REQUIRED" and len(requests) == 2


def test_sdk_cli_http_and_schema_share_one_contract(tmp_path):
    with Atlas("fixture") as atlas:
        sdk = atlas.benchmark_prices("sp500", **DATES)
    with TestClient(create_app(provider="fixture", directory=tmp_path / "http")) as client:
        response = client.post("/v1/research", json=query(benchmark_id="sp500", limit=500))
        assert response.status_code == 200
        assert response.json()["records"] == sdk["records"]
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "benchmark_prices",
            "--benchmark-id",
            "sp500",
            "--start-date",
            DATES["start_date"],
            "--end-date",
            DATES["end_date"],
            "--limit",
            "500",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(cli.stdout)["records"] == sdk["records"]
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(query(benchmark_id="sp500"))


@pytest.mark.asyncio
async def test_actual_mcp_session_returns_same_catalog_prices_and_evidence():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        catalog = await session.call_tool(
            "atlas_get_research", {"query": {"dataset": "benchmark_catalog"}}
        )
        assert not catalog.is_error
        result = await session.call_tool(
            "atlas_get_research",
            {"query": query(benchmark_id="nasdaq100", response={"view": "compact"})},
        )
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        assert data["records"][0]["benchmark_id"] == "nasdaq100"
        resolved = await session.call_tool(
            "atlas_get_evidence",
            {"evidence_id": data["records"][0]["inputs"][0]["source"]["evidence_id"]},
        )
        assert not resolved.is_error


def test_jpx_url_policy_is_fixed_and_does_not_accept_arbitrary_paths():
    assert topix_identity(topix_url(1)) == (1, False)
    for url in [
        topix_url(1) + "?anything=1",
        topix_url(1).replace(".1.", ".6."),
        "https://evil.invalid/file",
    ]:
        assert topix_identity(url) is None
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(url)


def test_source_timestamp_retains_market_timezone_across_us_dst():
    # Daily bars are localized using the source's IANA zone, not a fixed offset.
    def changed(url, snap, calls):
        data = json.loads(ROOT.joinpath("yahoo_chart_^GSPC.json").read_bytes())
        r = data["chart"]["result"][0]
        days = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]
        r["timestamp"] = [
            int(
                datetime.fromisoformat(d + "T09:30:00")
                .replace(tzinfo=ZoneInfo("America/New_York"))
                .timestamp()
            )
            for d in days
        ]
        return Snapshot(canonical(data), snap.url, "2026-11-06T00:00:00Z")

    result = run(
        Capture(changed),
        benchmark_id="sp500",
        benchmark_currency="USD",
        start_date="2026-10-29",
        end_date="2026-11-04",
    )
    rows = {r["date"]: r for r in result["records"]}
    assert rows["2026-10-30"]["index_observation_at"].endswith("13:30:00Z")
    assert rows["2026-11-02"]["index_observation_at"].endswith("14:30:00Z")


@pytest.mark.parametrize(
    "fault",
    [
        "currency",
        "dividend",
        "date",
        "duplicate",
        "order",
        "zero",
        "float",
        "nan",
        "flows",
        "timing",
    ],
)
def test_portfolio_contract_rejects_mixed_units_bad_values_and_intraday_claims(fault):
    supplied = portfolio().model_dump()
    if fault in {"currency", "dividend", "flows", "timing"}:
        key, value = {
            "currency": ("currency", "USD"),
            "dividend": ("dividend_treatment", "gross_total_return"),
            "flows": ("value_basis", "account_balance"),
            "timing": ("valuation_basis", "tokyo_close"),
        }[fault]
        supplied[key] = value
    elif fault == "date":
        supplied["observations"][0]["date"] = "2020-01-01"
    elif fault == "duplicate":
        supplied["observations"][1]["date"] = supplied["observations"][0]["date"]
    elif fault == "order":
        supplied["observations"].reverse()
    else:
        supplied["observations"][0]["value"] = {"zero": "0", "float": 1.1, "nan": "NaN"}[fault]
    captured = Capture()
    with pytest.raises(AtlasError) as exc:
        run(captured, portfolio=supplied)
    assert exc.value.code == "INVALID_INPUT" and not captured.calls


def test_actual_api_comparison_preserves_portfolio_metrics_with_different_index_dates():
    supplied = portfolio().model_dump()
    jp = run(benchmark_id="topix", portfolio=supplied)["comparison"]
    us = run(benchmark_id="sp500", portfolio=supplied)["comparison"]
    assert jp["portfolio_metrics"] == us["portfolio_metrics"]
    assert jp["relative_metrics"]["beta"]["value"] is not None
    assert us["relative_metrics"]["beta"]["value"] is None
    assert us["comparison_period"]["matched_intervals"] == 0


def test_live_audit_xml_locator_resolves_original_rate_attribute(monkeypatch):
    monkeypatch.syspath_prepend("scripts")
    from verify_benchmarks_live import resolve

    capture = Capture()
    result = run(capture, benchmark_id="sp500")
    for item in result["records"][0]["inputs"][1]["inputs"]:
        assert Decimal(resolve(item["source"], capture)) == Decimal(item["value"])
