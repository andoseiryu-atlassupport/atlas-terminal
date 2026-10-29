"""Optional Yahoo contracts with fictional bytes; no live availability assertion."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import (
    YAHOO_BASE,
    YAHOO_ENABLE_ENV,
    yahoo_identity,
    yahoo_url,
)
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, decode_json
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery
from atlas_terminal.server import create_app
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
BODY = RESOURCE.joinpath("yahoo_chart_7203.T.json").read_bytes()
DATES = {"start_date": "2026-10-19", "end_date": "2026-10-27"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv(YAHOO_ENABLE_ENV, raising=False)


def query(dataset="equity_prices", **extra):
    return {"dataset": dataset, "identifier": "7203.T", **extra}


def locate(source, document):
    for part in source["json_pointer"].split("/")[1:]:
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def changed(transform, dataset="equity_prices", retrieved="2026-10-27T02:00:00Z", **extra):
    class Changed(PublicFixtures):
        def get(self, url, ttl):
            original = json.loads(BODY)
            transform(original["chart"]["result"][0])
            return Snapshot(json.dumps(original).encode(), url, retrieved)

    with Atlas("fixture") as atlas:
        atlas._research.public = Changed()
        return atlas.get_research(query(dataset, **extra))


def test_daily_prices_preserve_source_decimals_adjustment_bases_and_every_cell_locator():
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query())
        compact = atlas.get_research(
            query(response={"view": "compact", "fields": ["adjusted_close"]})
        )
        assert len(full["records"]) == 6
        latest = full["records"][0]
        assert latest["close"] == "110.250000000000000001"
        assert latest["adjusted_close"] == "99.12345678901234567890"
        assert latest["date"] == "2026-10-26" and latest["published_at"] is None
        assert latest["price_basis"] == "yahoo_split_adjusted"
        assert latest["adjusted_close_basis"] == "yahoo_split_and_dividend_adjusted"
        assert full["instrument"]["source_instrument_type"] == "EQUITY"
        for row, projected in zip(full["records"], compact["records"], strict=True):
            assert projected["adjusted_close_basis"] == row["adjusted_close_basis"]
            assert projected["price_basis"] == row["price_basis"]
            assert projected["session_status"] == "completed"
            for field, source in row["field_sources"].items():
                evidence = atlas.get_evidence(projected["field_sources"][field]["evidence_id"])
                assert evidence["evidence"] == source and source["raw_sha256"] == digest(BODY)
                cell = locate(source, decode_json(BODY))
                if field != "date":
                    assert (Decimal(row[field]) if row[field] is not None else None) == cell
            assert row["source"] == row["field_sources"]["close"]
        zero_volume = next(r for r in full["records"] if r["date"] == "2026-10-20")
        assert zero_volume["volume"] == "0" and zero_volume["status"] == "reported"
        missing = next(r for r in full["records"] if r["date"] == "2026-10-21")
        assert missing["adjusted_close"] is None and missing["close"] == "104"
        assert missing["status"] == "unavailable"
        assert full["coverage"]["unavailable_records"] == 2
        assert full["coverage"]["excluded_unconfirmed_session_rows"] == 1
        assert full["coverage"]["date_selection"] == "inclusive_exchange_local_date"
        assert full["is_synthetic"] and not full["coverage"]["point_in_time"]


def test_dividends_and_reverse_splits_keep_reported_terms_and_event_locators():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("equity_actions"))
    split, dividend = result["records"]
    assert split["action_type"] == "split" and split["date"] == "2026-10-23"
    assert (split["numerator"], split["denominator"], split["split_ratio"]) == ("1", "5", "1:5")
    assert dividend["value"] == "12.340000000000000001"
    assert dividend["date"] == "2026-10-20" and dividend["unit"] == "JPY_per_share"
    for row in result["records"]:
        cell = locate(row["source"], decode_json(BODY))
        assert row["source"]["raw_sha256"] == digest(BODY)
        assert cell["date"] == int(
            datetime.fromisoformat(row["observation_at"].replace("Z", "+00:00")).timestamp()
        )


def test_no_actions_is_a_successful_empty_result_with_source_evidence():
    result = changed(lambda r: r.pop("events"), "equity_actions")
    assert result["records"] == [] and result["coverage"]["observed_records"] == 0
    assert result["sources"] and not result["coverage"]["partial"]


def test_fractional_split_coefficients_keep_exact_source_terms_and_equivalent_labels():
    def transform(result):
        event = next(iter(result["events"]["splits"].values()))
        event.update(numerator=1.1, denominator=1, splitRatio="11:10")

    result = changed(transform, "equity_actions")
    row = result["records"][0]
    assert (row["numerator"], row["denominator"], row["split_ratio"]) == ("1.1", "1", "11:10")


def test_a_source_window_without_sessions_has_no_invented_prices_or_events():
    def empty(result):
        result.pop("timestamp")
        result.pop("events")
        result["indicators"] = {"quote": [{}]}

    result = changed(empty, "equity_actions")
    assert result["records"] == [] and result["coverage"]["unavailable_records"] == 0
    with pytest.raises(AtlasError) as error:
        changed(empty)
    assert error.value.code == "INCOMPLETE_COVERAGE"


def test_future_reported_events_are_not_presented_as_historical_actions():
    result = changed(lambda r: None, "equity_actions", retrieved="2026-10-21T02:00:00Z")
    assert [r["action_type"] for r in result["records"]] == ["dividend"]


def test_missing_adjusted_close_does_not_substitute_close_or_invent_a_locator():
    result = changed(lambda r: r["indicators"].pop("adjclose"))
    assert all(row["adjusted_close"] is None for row in result["records"])
    assert all(not row["adjusted_close_available"] for row in result["records"])
    assert all("adjusted_close" not in row["field_sources"] for row in result["records"])
    assert result["records"][0]["close"] is not None


def test_current_session_is_excluded_until_the_source_regular_session_end():
    before = changed(lambda r: None, retrieved="2026-10-27T06:10:00Z")
    after = changed(lambda r: None, retrieved="2026-10-27T06:31:00Z")
    assert before["records"][0]["date"] == "2026-10-26"
    assert after["records"][0]["date"] == "2026-10-27"
    assert before["coverage"]["excluded_unconfirmed_session_rows"] == 1
    assert after["coverage"]["excluded_unconfirmed_session_rows"] == 0


def test_dates_use_tokyo_and_date_filtering_is_inclusive_without_holiday_fill():
    def transform(result):
        result["timestamp"][0] = int(datetime(2026, 10, 18, 16, tzinfo=UTC).timestamp())

    result = changed(transform, start_date="2026-10-19", end_date="2026-10-20")
    assert [r["date"] for r in result["records"]] == ["2026-10-20", "2026-10-19"]
    with Atlas("fixture") as atlas:
        weekend = atlas.get_research(query(start_date="2026-10-23", end_date="2026-10-26"))
    assert [r["date"] for r in weekend["records"]] == ["2026-10-26", "2026-10-23"]
    assert not weekend["coverage"]["exchange_calendar_verified"]


@pytest.mark.parametrize(
    "symbol", ["7203", "7203.t", "7203.T?x=1", "7203.T/", "7203.N", "^UNKNOWN", "JPY=X"]
)
def test_only_supported_explicit_symbols_are_accepted_by_runtime_and_schema(symbol):
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    with pytest.raises(ValidationError):
        validator.validate(query(identifier=symbol))
    with Atlas("fixture") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research(query(identifier=symbol))
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "extra",
    [
        {"start_date": "2026-01-01"},
        {"start_date": "2026-01-02", "end_date": "2026-01-01"},
        {"start_date": "2010-01-01", "end_date": "2026-01-01"},
        {"start_date": "2026-01-01", "end_date": "2026-02-30"},
        {"series": "7203.T"},
        {"allow_network": True},
        {"metrics": ["close"]},
    ],
)
def test_invalid_queries_cannot_expand_access_or_change_source_options(extra):
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research(query(**extra))
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "6758.T"),
        ("currency", "USD"),
        ("exchangeTimezoneName", "UTC"),
        ("exchangeName", "NMS"),
        ("instrumentType", "CURRENCY"),
        ("dataGranularity", "1wk"),
    ],
)
def test_source_identity_and_frequency_drift_fail_closed(field, value):
    with pytest.raises(AtlasError) as error:
        changed(lambda r: r["meta"].update({field: value}))
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_date",
        "order",
        "length",
        "multiple_quotes",
        "adjusted_length",
        "zero_price",
        "negative_price",
        "negative_volume",
        "fractional_volume",
        "ohlc",
        "split_zero",
        "split_label",
        "event_key",
        "dividend_missing",
        "dividend_negative",
        "timestamp_type",
    ],
)
def test_malformed_values_arrays_and_actions_fail_instead_of_repairing(fault):
    def transform(result):
        quote = result["indicators"]["quote"][0]
        if fault == "duplicate_date":
            result["timestamp"][1] = result["timestamp"][0]
        elif fault == "order":
            result["timestamp"].reverse()
        elif fault == "length":
            quote["open"].pop()
        elif fault == "multiple_quotes":
            result["indicators"]["quote"].append(quote)
        elif fault == "adjusted_length":
            result["indicators"]["adjclose"][0]["adjclose"].pop()
        elif fault in {"zero_price", "negative_price"}:
            quote["close"][0] = 0 if fault == "zero_price" else -1
        elif fault in {"negative_volume", "fractional_volume"}:
            quote["volume"][0] = -1 if fault == "negative_volume" else 0.5
        elif fault == "ohlc":
            quote["high"][0] = 99
        elif fault == "split_zero":
            next(iter(result["events"]["splits"].values()))["denominator"] = 0
        elif fault == "split_label":
            next(iter(result["events"]["splits"].values()))["splitRatio"] = "5:1"
        elif fault == "event_key":
            event = next(iter(result["events"]["dividends"].values()))
            event["date"] += 1
        elif fault == "dividend_missing":
            next(iter(result["events"]["dividends"].values())).pop("amount")
        elif fault == "dividend_negative":
            next(iter(result["events"]["dividends"].values()))["amount"] = -1
        else:
            result["timestamp"] = "malformed"

    with pytest.raises(AtlasError) as error:
        changed(
            transform,
            "equity_actions"
            if fault.startswith(("split", "event", "dividend"))
            else "equity_prices",
        )
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_enablement_is_operator_only_and_precedes_client_construction(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled Yahoo access constructed a network client")

    monkeypatch.setattr("atlas_terminal.research.PublicTransport", forbidden)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research(query())
    assert error.value.code == "CONFIG_REQUIRED" and YAHOO_ENABLE_ENV in error.value.message
    assert "yahoo_finance" not in {
        r["source_id"] for r in Atlas("public").research_diagnostics({})["sources"]
    }
    with Atlas("public") as atlas:
        explicit = atlas.research_diagnostics({"sources": ["yahoo_finance"]})
        preview = atlas.get_research_preset(
            {"preset": "japan_equity_history", "identifier": "7203.T", "preview": True}
        )
    assert explicit["sources"][0]["configuration"] == "configuration_required"
    assert not explicit["network_checks_executed"] and preview["status"] == "preview"


def test_canonical_endpoint_and_inclusive_dates_cannot_select_other_hosts_or_parameters():
    url = yahoo_url("130A.T", "2026-01-01", "2026-01-02")
    assert yahoo_identity(url) == ("130A.T", "2026-01-01", "2026-01-02")
    parameters = parse_qs(url.split("?", 1)[1])
    assert parameters["period1"] == [str(int(datetime(2025, 12, 31, 15, tzinfo=UTC).timestamp()))]
    assert parameters["period2"] == [str(int(datetime(2026, 1, 2, 15, tzinfo=UTC).timestamp()))]
    for bad in (
        url + "&crumb=private",
        url.replace("query1.", "query2."),
        url.replace("interval=1d", "interval=1m"),
        url + "&period1=1",
        url.replace("https:", "http:"),
        url.replace(".com", ".co.jp"),
    ):
        assert yahoo_identity(bad) is None
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(bad)


def test_public_price_action_and_preset_reads_share_one_cached_body_and_keep_opt_in(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(YAHOO_ENABLE_ENV, "1")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and str(request.url).startswith(YAHOO_BASE)
        assert not request.headers.get("Cookie") and "crumb" not in str(request.url)
        return httpx.Response(200, content=BODY)

    with Atlas("public") as atlas:
        transport = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        atlas._research.public = transport
        prices = atlas.get_research(query())
        actions = atlas.get_research(query("equity_actions"))
        preset = atlas.get_research_preset(
            {"preset": "japan_equity_history", "identifier": "7203.T"}
        )
        assert preset["status"] == "ok" and not preset["is_synthetic"]
        assert len(requests) == 1
        assert prices["sources"] == actions["sources"]
        monkeypatch.delenv(YAHOO_ENABLE_ENV)
        with pytest.raises(AtlasError) as error:
            transport.get(yahoo_url("7203.T"), 900)
        assert error.value.code == "CONFIG_REQUIRED" and len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_upstream_denials_do_not_harvest_cookies_change_hosts_or_return_fixtures(
    status, monkeypatch, tmp_path
):
    monkeypatch.setenv(YAHOO_ENABLE_ENV, "1")
    requests = []

    def denied(request):
        requests.append(request)
        return httpx.Response(status, content=b"upstream denial", headers={"Retry-After": "600"})

    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(denied)),
        )
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query())
    assert error.value.code == ("SOURCE_RATE_LIMITED" if status == 429 else "SOURCE_UNAVAILABLE")
    assert len(requests) == 1 and str(requests[0].url) == yahoo_url("7203.T")
    assert not requests[0].headers.get("Cookie")


@pytest.mark.parametrize("fault", ["error", "multiple_results"])
def test_chart_error_envelopes_and_multiple_symbols_are_rejected(fault):
    class Failed(PublicFixtures):
        def get(self, url, ttl):
            document = json.loads(BODY)
            if fault == "error":
                document["chart"]["error"] = {"code": "Not Found", "description": "untrusted"}
            else:
                document["chart"]["result"] *= 2
            return Snapshot(json.dumps(document).encode(), url, "2026-10-27T02:00:00Z")

    with Atlas("fixture") as atlas:
        atlas._research.public = Failed()
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query())
    assert error.value.code == "SOURCE_UNAVAILABLE" and "untrusted" not in error.value.message


def test_missingness_and_truncation_requirements_are_enforced_on_saved_data():
    with Atlas("fixture") as atlas:
        for changes in (
            {"requirements": {"allow_missing": False}},
            {"limit": 1, "requirements": {"allow_truncated": False}},
        ):
            with pytest.raises(AtlasError) as error:
                atlas.get_research(query(**changes))
            assert error.value.code == "QUALITY_NOT_MET"
        first = atlas.get_research(query(response={"view": "compact", "page_size": 2}))
        second = atlas.get_research(
            query(
                response={
                    "view": "compact",
                    "page_size": 2,
                    "cursor": first["response"]["next_cursor"],
                }
            )
        )
        assert len(second["records"]) == 2 and second["records"][0]["date"] == "2026-10-22"


def test_catalog_queries_preset_schema_and_common_sdk_cli_http_results(tmp_path):
    catalog = Atlas.research_catalog("equity_prices", view="compact")
    assert catalog["datasets"][0]["source_kind"] == "secondary_aggregator"
    assert catalog["sources"]["yahoo_finance"]["optional"]
    Draft202012Validator(ResearchPresetQuery.model_json_schema()).validate(
        {"preset": "japan_equity_history", "identifier": "130A.T"}
    )
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query(**DATES))
    with TestClient(create_app(provider="fixture", directory=tmp_path / "http")) as client:
        response = client.post("/v1/research", json=query(**DATES))
        assert response.status_code == 200 and response.json() == expected
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "equity_prices",
            "--identifier",
            "7203.T",
            "--start-date",
            DATES["start_date"],
            "--end-date",
            DATES["end_date"],
            "--provider",
            "fixture",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == expected


@pytest.mark.asyncio
async def test_actual_stdio_mcp_executes_price_action_preset_and_resolves_original_evidence(
    tmp_path,
):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={**os.environ, "ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        assert len((await session.list_tools()).tools) == 6
        preset = await session.call_tool(
            "atlas_get_research_preset",
            {"query": {"preset": "japan_equity_history", "identifier": "7203.T"}},
        )
        assert not preset.is_error and preset.structured_content["status"] == "ok"
        prices, actions = preset.structured_content["batch"]["items"]
        row = prices["result"]["records"][0]
        assert row["close"] == "110.250000000000000001"
        evidence = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": row["field_sources"]["close"]["evidence_id"]}
        )
        assert not evidence.is_error and evidence.structured_content["evidence"][
            "raw_sha256"
        ] == digest(BODY)
        assert evidence.structured_content["evidence"]["json_pointer"].endswith("/close/5")
        assert len(actions["result"]["records"]) == 2
