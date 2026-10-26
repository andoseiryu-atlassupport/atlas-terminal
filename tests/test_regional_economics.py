"""Fictional official-shaped statistics: definitions, identity, dates and exact evidence."""

import json
import subprocess
import sys
from copy import deepcopy
from importlib.resources import files
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.economic_sources import estat_data_url, estat_metadata_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.regional_economic_catalog import EUROPE_SERIES, UK_SERIES
from atlas_terminal.regional_economic_sources import eurostat_url, ons_url
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.server import create_app
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def raw(name):
    return json.loads(RESOURCE.joinpath(name).read_bytes())


class Changed(PublicFixtures):
    def __init__(self, transform):
        self.transform = transform
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        snapshot = super().get(url, ttl)
        return Snapshot(self.transform(snapshot), snapshot.url, snapshot.retrieved_at)


def result(query, transform):
    with Atlas("fixture") as atlas:
        atlas._research.public = Changed(transform)
        return atlas.get_research(query)


def european(series="ea_hicp_yoy", **extra):
    return {"dataset": "europe_economics", "series": series, **DATES, **extra}


@pytest.mark.parametrize("series", EUROPE_SERIES)
def test_european_dimensions_scale_adjustment_and_cells_remain_explicit(series):
    with Atlas("fixture") as atlas:
        r = atlas.get_research(european(series))
    spec = EUROPE_SERIES[series]
    row = r["records"][0]
    assert row["value"] == "102.5000" and not row["annualized"]
    assert row["dimensions"] == spec["filters"]
    assert row["unit"] == spec["filters"]["unit"]
    assert row["base_period"] == spec["base_period"]
    assert row["country"] == spec["country"] and row["observation_status"] == "p"
    assert row["date"] == "2026-09-30" and row["published_at"] is None
    assert row["source"]["json_pointer"] == "/value/2"
    assert row["source"]["raw_sha256"] == digest(
        RESOURCE.joinpath(f"eurostat_{series}.json").read_bytes()
    )
    assert r["is_synthetic"] and r["source_id"] == "eurostat"


@pytest.mark.parametrize("time_first,dense", [(True, True), (True, False), (False, True)])
def test_jsonstat_dimension_order_and_dense_or_sparse_values_are_independent(time_first, dense):
    body = raw("eurostat_de_gdp_growth.json")
    if time_first:
        body["id"] = ["time", *body["id"][:-1]]
        body["size"] = [3, *body["size"][:-1]]
    if dense:
        body["value"] = ["0.0000", "-1.234567890123456789", None]
    else:
        body["value"] = {"0": "0.0000", "1": "-1.234567890123456789"}
    r = result(european("de_gdp_growth"), lambda s: json.dumps(body).encode())
    assert [r["value"] for r in r["records"]] == [None, "-1.234567890123456789", "0.0000"]
    assert not r["records"][1]["annualized"]
    assert r["records"][0]["source"]["json_pointer"] == ("/value/2" if dense else "/value")


@pytest.mark.parametrize(
    "fault",
    [
        "geo",
        "unit",
        "dataset",
        "agency",
        "source",
        "dimension",
        "size",
        "offset",
        "position",
        "period",
        "out_of_window",
        "numeric",
        "status",
        "classification",
    ],
)
def test_wrong_eurostat_identity_or_malformed_cells_fail_closed(fault):
    body = raw("eurostat_ea_hicp_yoy.json")
    if fault in {"geo", "unit", "classification"}:
        dim = {"geo": "geo", "unit": "unit", "classification": "coicop18"}[fault]
        body["dimension"][dim]["category"]["index"] = {"WRONG": 0}
    elif fault == "dataset":
        body["extension"]["id"] = "PRC_HICP_MANR"
    elif fault == "agency":
        body["extension"]["agencyId"] = "OTHER"
    elif fault == "source":
        body["source"] = "OTHER"
    elif fault == "dimension":
        body["id"].append("geo")
    elif fault == "size":
        body["size"][0] = 2
    elif fault == "offset":
        body["value"]["3"] = "999"
    elif fault == "position":
        body["dimension"]["time"]["category"]["index"]["2026-09"] = 0
    elif fault in {"period", "out_of_window"}:
        codes = body["dimension"]["time"]["category"]["index"]
        del codes["2026-09"]
        codes["2026-13" if fault == "period" else "2025-09"] = 2
    elif fault == "numeric":
        body["value"]["1"] = "NaN"
    else:
        body["status"]["2"] = {"unexpected": "p"}
    with pytest.raises(AtlasError) as exc:
        result(european(), lambda s: json.dumps(body).encode())
    assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("forecast_flag", ["f", "p f", "ef"])
def test_forecast_is_not_an_observed_eurostat_value_and_requirements_reject_missing(forecast_flag):
    body = raw("eurostat_ea_hicp_yoy.json")
    body["status"]["2"] = forecast_flag
    r = result(european(), lambda s: json.dumps(body).encode())
    assert r["records"][0]["value"] is None
    assert r["records"][0]["source_value"] == "102.5000"
    assert r["records"][0]["observation_status"] == forecast_flag
    with pytest.raises(AtlasError, match="requirements"):
        result(european(requirements={"allow_missing": False}), lambda s: json.dumps(body).encode())


@pytest.mark.parametrize("series", UK_SERIES)
def test_ons_exact_identity_and_frequency_do_not_mix_in_annual_values(series):
    with Atlas("fixture") as atlas:
        r = atlas.get_research({"dataset": "uk_economics", "series": series, **DATES})
        compact = atlas.get_research(
            {
                "dataset": "uk_economics",
                "series": series,
                **DATES,
                "response": {"view": "compact", "fields": ["value"]},
            }
        )
    row = r["records"][0]
    assert row["series_id"] == UK_SERIES[series]["cdid"]
    assert row["value"] == "102.5000" and row["value"] != "999999"
    assert row["published_at"] is None and not row["annualized"]
    assert compact["records"][0]["reference_period_kind"] == row["reference_period_kind"]
    assert r["source_id"] == "ons" and r["is_synthetic"]


def test_uk_rolling_unemployment_uses_window_end_and_preserves_middle_month_label():
    q = {"dataset": "uk_economics", "series": "unemployment", **DATES}
    with Atlas("fixture") as atlas:
        row = atlas.get_research(q)["records"][0]
        selected = atlas.get_research(q | {"end_date": "2026-09-30"})["records"]
    assert row["source_period"] == "2026 SEP" and row["source_label_date"] == "2026-09-30"
    assert row["period_start"] == "2026-08-01" and row["date"] == "2026-10-31"
    assert row["reference_period_months"] == 3
    assert selected[0]["source_period"] == "2026 AUG"


@pytest.mark.parametrize("fault", ["cdid", "dataset", "unit", "duplicate", "period", "value"])
def test_ons_mismatches_and_malformed_periods_or_values_fail(fault):
    body = raw("ons_cpi.json")
    if fault in {"cdid", "dataset", "unit"}:
        body["description"][{"cdid": "cdid", "dataset": "datasetId", "unit": "unit"}[fault]] = (
            "" if fault == "unit" else "OTHER"
        )
    elif fault == "duplicate":
        body["months"].append(deepcopy(body["months"][0]))
    elif fault == "period":
        body["months"][0]["date"] = "2026 Q1"
    else:
        body["months"][0]["value"] = True
    with pytest.raises(AtlasError):
        result(
            {"dataset": "uk_economics", "series": "cpi", **DATES},
            lambda s: json.dumps(body).encode(),
        )


def test_japan_quarterly_periods_units_and_annualization_are_not_monthly():
    url = estat_metadata_url("gdp_real")
    assert "Cycle=2" in url and "StatCode=00100409" in url
    data_url = estat_data_url("gdp_real", "9900000000000000011", "2026-02-03", "2026-08-10")
    assert "TimeFrom=20261Q00" in data_url and "TimeTo=20263Q00" in data_url
    PublicTransport.validate_url(data_url)
    with Atlas("fixture") as atlas:
        r = atlas.get_research({"dataset": "japan_economics", "series": "gdp_real", **DATES})
    row = r["records"][0]
    assert row["date"] == "2026-09-30" and row["period"] == "Q3"
    assert row["frequency"] == "quarterly" and row["annualized"]
    assert row["source_period"] == "20263Q00" and row["base_period"] == "2020"
    assert row["unit"] == "billion JPY"


@pytest.mark.parametrize("fault", ["cycle", "geography", "adjustment", "title", "duplicate"])
def test_japanese_new_series_cannot_accept_other_definitions(fault):
    def transform(snapshot):
        if "getIndicatorInfo" in snapshot.url or snapshot.url.endswith("_metadata.xml"):
            return snapshot.body
        root = ET.fromstring(snapshot.body)
        value = root.find("STATISTICAL_DATA/DATA_INF/DATA_OBJ/VALUE")
        if fault == "cycle":
            value.set("cycle", "1")
        elif fault == "geography":
            value.set("regionCode", "13000")
        elif fault == "adjustment":
            value.set("isSeasonal", "1")
        elif fault == "title":
            root.find("STATISTICAL_DATA/CLASS_INF/CLASS_OBJ/CLASS").set("name", "OTHER GDP")
        else:
            group = root.find("STATISTICAL_DATA/DATA_INF")
            group.append(deepcopy(group[0]))
        return ET.tostring(root)

    with pytest.raises(AtlasError):
        result({"dataset": "japan_economics", "series": "gdp_real", **DATES}, transform)


@pytest.mark.parametrize(
    "url",
    [
        eurostat_url("ea_hicp_yoy", "2026-01-01", "2026-12-31") + "&geo=DE",
        eurostat_url("ea_hicp_yoy", "2026-01-01", "2026-12-31").replace("TOTAL", "CP00"),
        eurostat_url("ea_hicp_yoy", "2026-01-01", "2026-12-31").replace("2026", "1900", 1),
        ons_url("cpi") + "?url=https://evil.invalid/",
        ons_url("cpi").replace("www.ons.gov.uk", "evil.invalid"),
    ],
)
def test_unbounded_or_unregistered_endpoints_are_rejected(url):
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(url)


def test_real_http_wire_contract_accepts_exact_json_without_sec_contact_or_loss(tmp_path):
    calls = []
    body = RESOURCE.joinpath("eurostat_ea_hicp_yoy.json").read_bytes()
    # Exercise JSON numbers through the exact Decimal decoder as well as numeric strings.
    body = body.replace(b'"102.5000"', b"102.500000000000000001")

    def handler(request):
        calls.append(request)
        assert request.headers["Accept"] == "application/json"
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "source.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        row = atlas.get_research(european())["records"][0]
    assert row["value"] == "102.500000000000000001"
    assert len(calls) == 1 and calls[0].url.params["coicop18"] == "TOTAL"
    assert row["source"]["raw_sha256"] == digest(body)


def test_new_statistics_use_catalog_discovery_panels_presets_and_http():
    rows = Atlas.research_catalog(dataset="europe_economics", country="DE")["series_search"][
        "records"
    ]
    assert len(rows) == 8 and all(r["country"] == "DE" for r in rows)
    for row in rows:
        parse_research(row["query"])
    with Atlas("fixture") as atlas:
        panel = atlas.get_research(
            {
                "dataset": "macro_panel",
                "frequency": "quarterly",
                "start_date": "2026-07-01",
                "end_date": "2026-09-30",
                "items": [
                    {
                        "id": "de_growth",
                        "aggregation": "last",
                        "query": {"dataset": "europe_economics", "series": "de_gdp_growth"},
                    },
                    {
                        "id": "uk_cpi",
                        "aggregation": "mean",
                        "query": {"dataset": "uk_economics", "series": "cpi"},
                    },
                ],
            }
        )
        cells = panel["records"][0]["records"]
        assert cells[0]["value"] == "102.5000" and cells[0]["unit"] == "CLV_PCH_PRE"
        assert cells[1]["value"] == "101.2500"
        for name, count in [
            ("japan_activity_overview", 10),
            ("us_activity_overview", 10),
            ("euro_area_macro_overview", 9),
            ("uk_macro_overview", 6),
            ("global_macro_overview", 10),
        ]:
            r = atlas.get_research_preset({"preset": name, **DATES})
            assert r["status"] == "ok" and len(r["batch"]["items"]) == count
    with TestClient(create_app(provider="fixture")) as http:
        response = http.post("/v1/research", json=european())
        assert response.status_code == 200 and response.json()["source_id"] == "eurostat"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "uk_economics",
            "--series",
            "gdp_growth",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["records"][0]["annualized"] is False


def test_new_schema_enforces_series_identity_and_required_fields():
    schema = Draft202012Validator(ResearchQuery.model_json_schema())
    schema.validate(european())
    for bad in [
        european(series="UNKNOWN"),
        european(series=None),
        european() | {"year": 2026},
        european() | {"url": "https://evil.invalid"},
    ]:
        with pytest.raises((AtlasError, ValidationError)):
            schema.validate(bad)
            parse_research(bad)


@pytest.mark.asyncio
async def test_one_official_mcp_connection_reads_japan_europe_uk_and_evidence(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--research-only", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp-state")},
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        assert len((await session.list_tools()).tools) == 6
        for query in [
            european(),
            {"dataset": "uk_economics", "series": "gdp_growth", **DATES},
            {"dataset": "japan_economics", "series": "gdp_real", **DATES},
        ]:
            response = await session.call_tool(
                "atlas_get_research",
                {"query": {**query, "response": {"view": "compact", "page_size": 1}}},
            )
            assert not response.is_error
            row = response.structured_content["records"][0]
            evidence = await session.call_tool(
                "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
            )
            assert not evidence.is_error
