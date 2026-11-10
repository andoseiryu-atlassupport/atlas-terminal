"""US CPI component definitions and delivery; all source observations are fictional."""

import json
import subprocess
import sys
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import BLS_BASE, PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery
from atlas_terminal.server import create_app

COMPONENTS = [
    ("core_cpi", "CUUR0000SA0L1E", False, "all_items_less_food_and_energy"),
    ("core_cpi_sa", "CUSR0000SA0L1E", True, "all_items_less_food_and_energy"),
    ("core_goods_cpi", "CUUR0000SACL1E", False, "commodities_less_food_and_energy_commodities"),
    ("core_goods_cpi_sa", "CUSR0000SACL1E", True, "commodities_less_food_and_energy_commodities"),
    (
        "core_services_cpi",
        "CUUR0000SASLE",
        False,
        "services_less_energy_services_including_shelter",
    ),
    (
        "core_services_cpi_sa",
        "CUSR0000SASLE",
        True,
        "services_less_energy_services_including_shelter",
    ),
]


def query(series, **extra):
    return {"dataset": "economics", "series": series, **extra}


@pytest.mark.parametrize("name,code,adjusted,scope", COMPONENTS)
def test_cpi_identity_scope_units_and_evidence_survive_compact_projection(
    name, code, adjusted, scope
):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(name, limit=1))
        by_id = atlas.get_research(query(code, limit=1))
        compact = atlas.get_research(
            query(name, limit=1, response={"view": "compact", "fields": ["value"]})
        )
        assert result["records"] == by_id["records"]
        row, short = result["records"][0], compact["records"][0]
        for record in (row, short):
            assert record["series_id"] == code
            assert record["seasonally_adjusted"] is adjusted
            assert record["index_scope"] == scope
            assert record["population"] == "all_urban_consumers"
            assert record["area"] == "US_city_average"
            assert record["unit"] == "index_1982_84_100"
            assert record["base_period"] == "1982-1984"
            assert not record["annualized"] and record["published_at"] is None
        evidence = atlas.get_evidence(short["source"]["evidence_id"])["evidence"]
        assert evidence == row["source"]
        raw = PublicFixtures().get(BLS_BASE + code, 0)
        assert evidence["raw_sha256"] == raw.sha256
        assert evidence["retrieved_at"] == raw.retrieved_at
        cell = raw.data
        for part in evidence["json_pointer"].split("/")[1:]:
            cell = cell[int(part)] if isinstance(cell, list) else cell[part]
        assert row["value"] == cell["value"]
        assert result["is_synthetic"] and not result["coverage"]["point_in_time"]
        assert "supercore" in " ".join(result["limitations"])


@pytest.mark.parametrize(
    "name,expected", [("core_goods_cpi", "-0.01"), ("core_services_cpi", "0.03125")]
)
def test_yoy_uses_exact_same_month_unadjusted_component_not_annual_average(name, expected):
    with Atlas("fixture") as atlas:
        data = atlas.get_research({"dataset": "macro_analytics", "series": name + "_yoy"})
    row = data["records"][0]
    assert row["value"] == expected and row["unit"] == "ratio"
    assert not row["seasonally_adjusted"]
    assert row["comparison_period"] == {"year": "2025", "period": "M09", "period_kind": "month"}
    assert row["formula"] == "(current - prior_year) / prior_year"
    current, prior = row["inputs"]
    assert current["series"] == prior["series"] == name
    assert current["index_scope"] == prior["index_scope"]
    assert Decimal(row["value"]) == (Decimal(current["value"]) - Decimal(prior["value"])) / Decimal(
        prior["value"]
    )
    assert data["records"][1]["value"] is None
    assert data["records"][1]["status"] == "unavailable"
    assert all(r["period"] != "M13" for r in data["records"])


@pytest.mark.parametrize(
    "fault",
    [
        "missing_baseline",
        "null_baseline",
        "zero_baseline",
        "negative_baseline",
        "wrong_series",
        "duplicate_month",
    ],
)
def test_component_bad_inputs_are_never_replaced_by_another_month_or_series(fault):
    class Changed(PublicFixtures):
        def get(self, url, ttl):
            original = super().get(url, ttl)
            data = original.data
            series = data["Results"]["series"][0]
            rows = series["data"]
            prior = next(r for r in rows if r["year"] == "2025" and r["period"] == "M09")
            if fault == "missing_baseline":
                rows.remove(prior)
            elif fault == "wrong_series":
                series["seriesID"] = "CUUR0000SA0"
            elif fault == "duplicate_month":
                rows.append({**prior, "value": "1.000"})
            else:
                prior["value"] = {
                    "null_baseline": "-",
                    "zero_baseline": "0",
                    "negative_baseline": "-1",
                }[fault]
            return Snapshot(json.dumps(data).encode(), original.url, original.retrieved_at)

    with Atlas("fixture") as atlas:
        atlas._research.public = Changed()
        q = {"dataset": "macro_analytics", "series": "core_goods_cpi_yoy", "limit": 1}
        if fault in {"wrong_series", "duplicate_month"}:
            with pytest.raises(AtlasError) as error:
                atlas.get_research(q)
            assert error.value.code == "SOURCE_UNAVAILABLE"
        else:
            row = atlas.get_research(q)["records"][0]
            assert row["value"] is None and row["status"] == "unavailable" and row["reason"]


def test_component_catalog_schema_and_preset_preview_need_no_source_or_key(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Source transport constructed during discovery")

    monkeypatch.setattr(PublicTransport, "__init__", forbidden)
    for term, expected in [
        ("コア財", "core_goods_cpi"),
        ("コアサービス", "core_services_cpi"),
        ("コアCPI", "core_cpi"),
    ]:
        catalog = Atlas.research_catalog(search=term, country="US")
        assert expected in {r["series"] for r in catalog["series_search"]["records"]}
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    for name, code, _, _ in COMPONENTS:
        validator.validate(query(name))
        validator.validate(query(code))
    preset = {"preset": "us_cpi_components", "preview": True}
    Draft202012Validator(ResearchPresetQuery.model_json_schema()).validate(preset)
    with Atlas("public") as atlas:
        preview = atlas.get_research_preset(preset)
        assert len(preview["plan"]["items"]) == 8
        assert preview["batch"] is None and atlas._research.public is None


@pytest.mark.parametrize("dated", [False, True])
def test_real_transport_selects_exact_series_and_reuses_cache_without_live_requests(
    tmp_path, dated
):
    requests = []
    code = "CUSR0000SASLE"
    raw = PublicFixtures().get(BLS_BASE + code, 0).body

    def handler(request):
        requests.append(request)
        if dated:
            assert request.method == "POST" and str(request.url) == BLS_BASE
            assert json.loads(request.content) == {
                "seriesid": [code],
                "startyear": "2025",
                "endyear": "2026",
            }
        else:
            assert request.method == "GET" and str(request.url) == BLS_BASE + code
        return httpx.Response(200, content=raw)

    dates = {"start_date": "2025-01-01", "end_date": "2026-12-31"} if dated else {}
    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        first = atlas.get_research(query("core_services_cpi_sa", **dates))
        second = atlas.get_research(query("core_services_cpi_sa", **dates))
    assert len(requests) == 1 and first["records"] == second["records"]
    assert first["records"][0]["series_id"] == code


def test_sdk_cli_and_http_return_the_same_component_and_yoy():
    with Atlas("fixture") as atlas, TestClient(create_app(provider="fixture")) as http:
        for q in [
            query("core_services_cpi_sa", limit=1),
            {"dataset": "macro_analytics", "series": "core_goods_cpi_yoy", "limit": 1},
        ]:
            expected = atlas.get_research(q)
            response = http.post("/v1/research", json=q)
            assert response.status_code == 200 and response.json() == expected
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "atlas_terminal",
                    "research",
                    "get",
                    q["dataset"],
                    "--series",
                    q["series"],
                    "--limit",
                    "1",
                    "--provider",
                    "fixture",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert json.loads(completed.stdout) == expected


@pytest.mark.asyncio
async def test_official_mcp_session_executes_component_preset_and_resolves_sources(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        preset = await session.call_tool(
            "atlas_get_research_preset",
            {
                "query": {
                    "preset": "us_cpi_components",
                    "include": [
                        "core_goods_cpi",
                        "core_services_cpi",
                        "core_goods_cpi_yoy",
                        "core_services_cpi_yoy",
                    ],
                }
            },
        )
        assert not preset.is_error and preset.structured_content["status"] == "ok"
        assert len(preset.structured_content["overview"]) == 4
        response = await session.call_tool(
            "atlas_get_research",
            {
                "query": query(
                    "core_services_cpi", limit=1, response={"view": "compact", "fields": ["value"]}
                )
            },
        )
        assert not response.is_error
        row = response.structured_content["records"][0]
        assert row["index_scope"] == "services_less_energy_services_including_shelter"
        evidence = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
        )
        assert not evidence.is_error
        assert (
            evidence.structured_content["evidence"]["raw_sha256"]
            == PublicFixtures().get(BLS_BASE + "CUUR0000SASLE", 0).sha256
        )
