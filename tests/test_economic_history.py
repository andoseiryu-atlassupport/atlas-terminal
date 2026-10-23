"""Official-shaped synthetic responses, HTTP wire contracts and history boundaries."""

import json
import logging
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal
from importlib.resources import files
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.economic_sources import bea_url, boj_url, estat_metadata_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import (
    BLS_BASE,
    BLS_SERIES,
    ECB_FX_URL,
    PublicFixtures,
    PublicTransport,
    bls_url,
)
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import PROBES, check_sources
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
KEY = "00000000-0000-0000-0000-000000000000"
DATES = {"start_date": "2025-01-01", "end_date": "2026-12-31"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("BEA_API_KEY", raising=False)


def fixture_json(name):
    return json.loads(RESOURCE.joinpath(name).read_bytes())


def transport(tmp_path, handler):
    clock = [1000.0]
    return PublicTransport(
        cache_path=tmp_path / "public.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + delay),
    )


class ChangedFixtures(PublicFixtures):
    def __init__(self, transform):
        self.transform = transform
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        snapshot = super().get(url, ttl)
        return Snapshot(self.transform(url, snapshot.body), snapshot.url, snapshot.retrieved_at)


def fixture_result(query, transform):
    with Atlas("fixture") as atlas:
        atlas._research.public = ChangedFixtures(transform)
        return atlas.get_research(query)


@pytest.mark.parametrize(
    "dataset,series",
    [("national_accounts", s) for s in NATIONAL_ACCOUNTS]
    + [("japan_economics", s) for s in JAPAN_SERIES],
)
def test_every_new_series_has_units_periods_exact_values_and_original_byte_evidence(
    dataset, series
):
    with Atlas("fixture") as atlas:
        q = {"dataset": dataset, "series": series, **DATES}
        result = atlas.get_research(q)
        compact = atlas.get_research(q | {"response": {"view": "compact"}})
        assert result["records"] and result["is_synthetic"]
        assert result["upstream_data_cost"] == "0" and not result["coverage"]["point_in_time"]
        assert result["coverage"]["requested_start_date"] == DATES["start_date"]
        for row, small in zip(result["records"], compact["records"], strict=True):
            assert isinstance(row["value"], str) and row["unit"] and row["date"]
            assert row["published_at"] is None
            assert small["unit"] == row["unit"] and small["date_basis"] == row["date_basis"]
            source = row["source"]
            body = RESOURCE.joinpath(source["raw_source_url"].rsplit("/", 1)[-1]).read_bytes()
            assert source["raw_sha256"] == digest(body)
            if "json_pointer" in source:
                original = json.loads(body)
                for part in source["json_pointer"].split("/")[1:]:
                    original = original[int(part)] if isinstance(original, list) else original[part]
                raw_value = original["DataValue"] if isinstance(original, dict) else original
                assert Decimal(str(raw_value)) == Decimal(row["value"])
            else:
                root = ET.fromstring(body)
                path = source["xml_local_path"].removeprefix("/" + root.tag + "/")
                assert root.find(path).text == row["value"]


def test_bls_dated_requests_use_post_in_ten_year_chunks_and_preserve_wire_provenance(tmp_path):
    requests = []
    series_id = BLS_SERIES["cpi"][0]

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST" and str(request.url) == BLS_BASE
        assert body["seriesid"] == [series_id]
        rows = [
            {"year": str(y), "period": "M12", "value": f"{y}.25"}
            for y in range(int(body["startyear"]), int(body["endyear"]) + 1)
        ]
        return httpx.Response(
            200,
            json={
                "status": "REQUEST_SUCCEEDED",
                "Results": {"series": [{"seriesID": series_id, "data": rows}]},
            },
        )

    with Atlas("public") as atlas:
        atlas._research.public = transport(tmp_path, handler)
        query = {
            "dataset": "economics",
            "series": "cpi",
            "start_date": "2000-01-01",
            "end_date": "2026-12-31",
            "limit": 500,
        }
        result = atlas.get_research(query)
        atlas.get_research(query)
    assert len(requests) == 3  # The second read uses the private cache.
    assert [
        (json.loads(r.content)["startyear"], json.loads(r.content)["endyear"]) for r in requests
    ] == [("2000", "2009"), ("2010", "2019"), ("2020", "2026")]
    assert len(result["records"]) == 27
    assert result["records"][0]["source"]["request_method"] == "POST"
    assert result["records"][0]["source"]["request_url"] == BLS_BASE
    assert result["coverage"]["first_observation_date"] == "2000-12-31"


def test_history_cpi_analytics_fetch_exact_prior_year_and_filter_period_ends():
    def add_baseline(url, body):
        data = json.loads(body)
        data["Results"]["series"][0]["data"].append(
            {"year": "2025", "period": "M09", "value": "310.000"}
        )
        return json.dumps(data).encode()

    query = {
        "dataset": "macro_analytics",
        "series": "cpi_yoy",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    }
    with Atlas("fixture") as atlas:
        source = ChangedFixtures(add_baseline)
        atlas._research.public = source
        result = atlas.get_research(query)
    assert len(result["records"]) == 1
    row = result["records"][0]
    assert row["date"] == "2026-09-30" and row["value"] is not None
    assert [r["year"] for r in row["inputs"]] == ["2026", "2025"]
    assert source.calls == [bls_url(BLS_SERIES["cpi"][0], 2025, 2026)]


def test_full_ecb_feed_accepts_old_dates_new_currencies_and_keeps_same_date_inputs():
    def change(url, body):
        assert url == ECB_FX_URL and url.endswith("eurofxref-hist.xml")
        root = ET.fromstring(body)
        parent = root.find("{*}Cube")
        day = deepcopy(parent[0])
        day.set("time", "2000-01-03")
        day[0].set("currency", "INR")
        day[0].set("rate", "80.0000")
        parent.append(day)
        return ET.tostring(root)

    result = fixture_result(
        {
            "dataset": "fx_rates",
            "base_currency": "EUR",
            "quote_currency": "INR",
            "start_date": "2000-01-01",
            "end_date": "2000-01-31",
        },
        change,
    )
    assert len(result["records"]) == 1
    assert result["records"][0]["value"] == "80.0000"
    assert result["records"][0]["date"] == "2000-01-03"


def test_bea_private_key_wire_only_echo_not_returned_and_units_not_rescaled(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("BEA_API_KEY", KEY)
    requests = []
    body = fixture_json("bea_T10106.json")
    body["BEAAPI"]["Request"] = {"RequestParam": [{"ParameterValue": KEY}]}
    body["BEAAPI"]["Results"]["Data"][-1]["DataValue"] = "1,234.500"
    raw = json.dumps(body).encode()

    def handler(request):
        requests.append(request)
        assert request.url.params["UserID"] == KEY
        return httpx.Response(200, content=raw)

    with caplog.at_level(logging.DEBUG), Atlas("public") as atlas:
        atlas._research.public = transport(tmp_path, handler)
        result = atlas.get_research({"dataset": "national_accounts", "series": "gdp_real", **DATES})
    assert len(requests) == 1
    row = result["records"][0]
    assert row["value"] == "1234.500" and row["unit_multiplier"] == 9
    assert row["annualized"] and row["period"] == "Q3"
    assert row["source"]["raw_sha256"] == digest(raw)
    assert KEY not in json.dumps(result) + caplog.text
    assert "UserID" not in row["source"]["raw_source_url"]


def test_bea_missing_key_diagnostics_and_transport_make_no_request(tmp_path):
    with Atlas("public") as atlas:
        atlas._research.public = transport(tmp_path, lambda request: pytest.fail("Unexpected HTTP"))
        with pytest.raises(AtlasError) as caught:
            atlas.get_research({"dataset": "national_accounts", "series": "gdp_real"})
    assert caught.value.code == "CONFIG_REQUIRED" and "signup" in str(caught.value)
    with Atlas("public") as atlas:
        report = atlas.research_diagnostics({"sources": ["bea"]})
    assert report["sources"][0]["api_key_required"]
    assert report["sources"][0]["configuration"] == "configuration_required"


@pytest.mark.parametrize(
    "fault",
    ["wrong_table", "wrong_frequency", "missing_scale", "duplicate", "error", "object_value"],
)
def test_bea_rejects_ambiguous_or_unusable_observations(fault):
    def change(url, body):
        data = json.loads(body)
        result = data["BEAAPI"]["Results"]
        row = result["Data"][0]
        if fault == "wrong_table":
            row["TableName"] = "T99999"
        elif fault == "wrong_frequency":
            row["TimePeriod"] = "2025M12"
        elif fault == "missing_scale":
            del row["UNIT_MULT"]
        elif fault == "duplicate":
            result["Data"].append(deepcopy(row))
        elif fault == "error":
            result["Error"] = {"ErrorDetail": "private upstream error"}
        else:
            row["DataValue"] = {"bad": "shape"}
        return json.dumps(data).encode()

    with pytest.raises(AtlasError) as caught:
        fixture_result({"dataset": "national_accounts", "series": "gdp_real", **DATES}, change)
    assert caught.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault", ["database", "frequency", "arrays", "page", "quarter", "unit", "duplicate"]
)
def test_boj_rejects_misaligned_incomplete_or_different_series(fault):
    def change(url, body):
        data = json.loads(body)
        row = data["DATA"][0]
        if fault == "database":
            data["PARAMETER"] = None
        elif fault == "frequency":
            row["FREQUENCY"] = "MONTHLY"
        elif fault == "arrays":
            row["VALUES"]["VALUES"].pop()
        elif fault == "page":
            data["NEXTPOSITION"] = 2
        elif fault == "quarter":
            row["VALUES"]["SURVEY_DATES"][0] = "202513"
        elif fault == "unit":
            row["UNIT"] = None
        else:
            row["VALUES"]["SURVEY_DATES"][1] = row["VALUES"]["SURVEY_DATES"][0]
        return json.dumps(data).encode()

    with pytest.raises(AtlasError) as caught:
        fixture_result(
            {"dataset": "japan_economics", "series": "tankan_manufacturing", **DATES}, change
        )
    assert caught.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault", ["ambiguous", "inactive", "adjustment", "survey", "unit", "partial"]
)
def test_estat_never_guesses_indicator_base_adjustment_or_survey(fault):
    def change(url, body):
        root = ET.fromstring(body)
        if "getIndicatorInfo" in url:
            parent = root.find("METADATA_INF/CLASS_INF")
            if fault == "ambiguous":
                parent.append(deepcopy(parent[0]))
            elif fault == "inactive":
                parent[0][0].set("toDate", "20251200")
        else:
            value = root.find("STATISTICAL_DATA/DATA_INF/DATA_OBJ/VALUE")
            if fault == "adjustment":
                value.set("isSeasonal", "2")
            elif fault == "survey":
                value.set("stat", "99999999")
            elif fault == "unit":
                value.set("unit", "unknown")
            elif fault == "partial":
                root.find("STATISTICAL_DATA/RESULT_INF/TOTAL_NUMBER").text = "100"
        return ET.tostring(root)

    with pytest.raises(AtlasError) as caught:
        fixture_result({"dataset": "japan_economics", "series": "cpi", **DATES}, change)
    assert caught.value.code in {"SOURCE_UNAVAILABLE", "INCOMPLETE_COVERAGE"}


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": "2025-01-01"},
        {"end_date": "2026-01-01"},
        {"start_date": "2026-02-29", "end_date": "2026-12-31"},
        {"start_date": "2026-12-31", "end_date": "2025-01-01"},
        {"start_date": "1990-01-01", "end_date": "2026-12-31"},
        {"start_date": "2025-01-01", "end_date": "2026-12-31", "as_of": "2025-01-01"},
    ],
)
def test_invalid_or_historical_information_requests_rejected_before_source_access(dates):
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research({"dataset": "national_accounts", "series": "gdp_real", **dates})


def test_new_endpoint_allowlist_rejects_credentials_unknown_series_and_unbounded_windows():
    for url in [
        bea_url("T10106", "Q", 2025, 2026) + "&UserID=" + KEY,
        bea_url("T10106", "M", 2025, 2026),
        bea_url("T10106", "Q", 2000, 2026),
        boj_url("call_rate", "2025-01-01", "2026-12-31") + "&code=other",
        estat_metadata_url("cpi") + "&StatCode=99999999",
        bls_url(BLS_SERIES["cpi"][0], 2000, 2026),
    ]:
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(url)


def test_japan_presets_share_date_queries_source_ids_and_schema():
    with Atlas("fixture") as atlas:
        for name, count in [("japan_macro_overview", 6), ("us_japan_overview", 10)]:
            plan = atlas.get_research_preset({"preset": name, "preview": True, **DATES})
            result = atlas.get_research_preset({"preset": name, **DATES})
            assert len(plan["plan"]["items"]) == count
            assert result["status"] == "ok"
            for item in plan["plan"]["items"]:
                assert item["query"]["start_date"] == DATES["start_date"]
                if (
                    item["query"].get("series") == "cpi"
                    and item["query"]["dataset"] == "japan_economics"
                ):
                    assert item["source_ids"] == ["estat"]
    for dataset, series in [("national_accounts", "gdp_real"), ("japan_economics", "cpi")]:
        Draft202012Validator(ResearchQuery.model_json_schema()).validate(
            {"dataset": dataset, "series": series, **DATES}
        )
    assert {v[0] for v in PROBES.values()} == set(Atlas.research_catalog()["sources"])
    assert len(check_sources(["bea", "boj", "estat"])["checks"]) == 6


def test_cli_sdk_and_http_accept_same_dated_new_dataset(tmp_path):
    query = {"dataset": "national_accounts", "series": "gdp_real", **DATES}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query)["records"]
    command = [
        sys.executable,
        "-m",
        "atlas_terminal",
        "research",
        "get",
        "national_accounts",
        "--series",
        "gdp_real",
        "--start-date",
        DATES["start_date"],
        "--end-date",
        DATES["end_date"],
        "--provider",
        "fixture",
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["records"] == expected
    with TestClient(create_app(directory=tmp_path / "server", provider="fixture")) as client:
        response = client.post("/v1/research", json=query)
    assert response.status_code == 200 and response.json()["records"] == expected


async def test_real_stdio_mcp_accepts_dated_japan_query_and_returns_units(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool(
            "atlas_get_research",
            {"query": {"dataset": "japan_economics", "series": "cpi", **DATES}},
        )
        assert not result.is_error
        row = result.structured_content["records"][0]
        assert row["unit"] == "2020=100" and row["country"] == "JP"
