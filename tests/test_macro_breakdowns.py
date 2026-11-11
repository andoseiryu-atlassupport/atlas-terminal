"""Definition, evidence and interface regressions; all numeric observations are fictional."""

import csv
import io
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
from pydantic import ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.inflation_sources import INFLATION_SERIES, INFLATION_URLS, parse_inflation_csv
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import BLS_BASE, PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import PRESETS
from atlas_terminal.seller_models import ProductQuery
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import check_sources, source_shapes


@pytest.mark.parametrize(
    "name,code,unit,adjusted,scope",
    [
        (
            "owners_equivalent_rent_cpi_sa",
            "CUSR0000SEHC",
            "index_1982_12_100",
            True,
            {"base_period": "1982-12", "index_scope": "owners_equivalent_rent"},
        ),
        (
            "nondurables_cpi",
            "CUUR0000SAN",
            "index_1982_84_100",
            False,
            {"base_period": "1982-1984"},
        ),
        (
            "employment_state_ca",
            "LASST060000000000005",
            "persons",
            True,
            {"survey": "LAUS", "region_code": "US-CA", "area_type": "state"},
        ),
        (
            "unemployment_state_ny",
            "LASST360000000000003",
            "percent",
            True,
            {"survey": "LAUS", "region_code": "US-NY"},
        ),
        (
            "unemployment_region_northeast",
            "LASRD910000000000003",
            "percent",
            True,
            {"area_type": "census_region", "region_code": "US-CENSUS-NE"},
        ),
        (
            "employment_age_16_19",
            "LNS12000012",
            "thousand_persons",
            True,
            {"survey": "CPS", "age_min": 16, "age_max": 19},
        ),
        (
            "unemployment_age_55_plus",
            "LNS14024230",
            "percent",
            True,
            {"survey": "CPS", "age_min": 55, "age_max": None},
        ),
        (
            "employment_occupation_service",
            "LNU02032204",
            "thousand_persons",
            False,
            {"survey": "CPS", "occupation": "service", "measure": "employment_level"},
        ),
        (
            "unemployment_occupation_service",
            "LNU04032218",
            "percent",
            False,
            {"survey": "CPS", "occupation": "service", "measure": "experienced_unemployment_rate"},
        ),
    ],
)
def test_scope_units_and_original_evidence_survive_projection(name, code, unit, adjusted, scope):
    with Atlas("fixture") as atlas:
        full = atlas.get_research({"dataset": "economics", "series": name, "limit": 1})
        short = atlas.get_research(
            {
                "dataset": "economics",
                "series": code,
                "limit": 1,
                "response": {"view": "compact", "fields": ["value"]},
            }
        )
        for row in (full["records"][0], short["records"][0]):
            assert row["series_id"] == code and row["unit"] == unit
            assert row["seasonally_adjusted"] is adjusted
            assert all(row[k] == v for k, v in scope.items())
            assert row["category_path"]
        evidence = atlas.get_evidence(short["records"][0]["source"]["evidence_id"])["evidence"]
        raw = PublicFixtures().get(BLS_BASE + code, 0)
        assert evidence["raw_sha256"] == raw.sha256
        cell = raw.data
        for part in evidence["json_pointer"].split("/")[1:]:
            cell = cell[int(part)] if isinstance(cell, list) else cell[part]
        assert full["records"][0]["value"] == cell["value"]
        assert full["is_synthetic"] and not full["coverage"]["point_in_time"]


def test_cpi_changes_use_same_component_exact_month_and_adjustment():
    with Atlas("fixture") as atlas:
        for suffix, adjusted, expected in (
            ("mom", True, Decimal(2) / Decimal(102)),
            ("yoy", False, Decimal("0.04")),
        ):
            data = atlas.get_research(
                {"dataset": "macro_analytics", "series": "rent_cpi_" + suffix}
            )
            row = data["records"][0]
            assert Decimal(row["value"]) == expected and row["unit"] == "ratio"
            assert row["seasonally_adjusted"] is adjusted
            assert row["inputs"][0]["index_scope"] == row["inputs"][1]["index_scope"] == "rent"
            assert row["inputs"][0]["series_id"] == ("CUSR" if adjusted else "CUUR") + "0000SEHA"
        assert data["records"][1]["value"] is None  # No February 2025 baseline.


@pytest.mark.parametrize("series", INFLATION_SERIES)
def test_cleveland_reported_ratios_and_annualized_percent_have_csv_cell_evidence(series):
    with Atlas("fixture") as atlas:
        query = {"dataset": "inflation_measures", "series": series}
        data = atlas.get_research(query)
        short = atlas.get_research({**query, "response": {"view": "compact", "fields": ["value"]}})
        rows = data["records"]
        annualized = series.endswith("annualized")
        assert rows[0]["value"] == ("-1.2" if annualized else "-0.001")
        assert rows[0]["unit"] == ("percent" if annualized else "ratio")
        assert rows[0]["annualized"] is annualized and rows[0]["seasonally_adjusted"]
        assert rows[1]["value"] is None and rows[1]["status"] == "unavailable"
        assert rows[0]["date"] == "2026-03-31" and rows[0]["source_period"] == "3/1/2026"
        assert rows[0]["published_at"] is None
        evidence = atlas.get_evidence(short["records"][0]["source"]["evidence_id"])["evidence"]
        kind = "trimmed_mean_cpi" if series.startswith("trimmed") else "median_cpi"
        raw = PublicFixtures().get(INFLATION_URLS[kind], 0)
        table = list(csv.reader(io.StringIO(raw.body.decode())))
        original = table[evidence["csv_row"] - 1][evidence["csv_column"] - 1]
        assert original == rows[0]["raw_value"]
        assert Decimal(original) == Decimal(rows[0]["value"])
        assert raw.sha256 == evidence["raw_sha256"]
        assert data["is_synthetic"] and not data["coverage"]["point_in_time"]
        assert "no rescaling" in short["records"][0]["normalization_formula"]


@pytest.mark.parametrize(
    "body",
    [
        b"<html>blocked</html>",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n",
        b"date,mcpi_monthly_chg,mcpi_ann_monthly_chg\n1/1/2026,0.1,1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/2/2026,0.1,1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n13/1/2026,0.1,1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/1/2026,NaN,1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/1/2026,0.1,Infinity\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/1/2026,0.1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/1/2026,0.1,1\n1/1/2026,0.1,1\n",
        b"date,trim_monthly_chg,trim_ann_monthly_chg\n1/1/2026,0.1,1\n1/1/2026,0.2,2\n",
        b"\xff",
        b"\x00",
        b"x" * (2 * 1024 * 1024 + 1),
    ],
)
def test_cleveland_changed_or_ambiguous_source_fails_closed(body):
    with pytest.raises(AtlasError) as error:
        parse_inflation_csv(
            Snapshot(body, INFLATION_URLS["trimmed_mean_cpi"], "2026-11-11T00:00:00Z"),
            "trimmed_mean_cpi",
        )
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_cleveland_transport_validates_url_body_and_reuses_raw_cache(tmp_path):
    requests = []
    url = INFLATION_URLS["median_cpi"]
    raw = PublicFixtures().get(url, 0).body

    def handler(request):
        requests.append(request)
        assert str(request.url) == url and request.method == "GET"
        return httpx.Response(200, content=raw)

    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "source.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        for series in ("median_cpi_mom", "median_cpi_annualized"):
            atlas.get_research({"dataset": "inflation_measures", "series": series})
    assert len(requests) == 1
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(url + "&arbitrary=1")
    with pytest.raises(AtlasError):
        PublicTransport.validate_body(url, b"<html>error</html>")


def test_catalog_taxonomy_filtering_is_offline_and_does_not_hide_states():
    age = Atlas.research_catalog(topic="employment", breakdown="age")["series_search"]
    assert age["matched"] == age["returned"] == 8 and not age["truncated"]
    state = Atlas.research_catalog(topic="employment", breakdown="state")["series_search"]
    assert state["matched"] == 102 and state["truncated"]
    ca = Atlas.research_catalog(topic="employment", search="US-CA")["series_search"]
    assert ca["matched"] == 2 and not ca["network_access"]
    assert {r["series"] for r in ca["records"]} == {"employment_state_ca", "unemployment_state_ca"}
    assert ca["groups"] == [{"category_path": ["employment", "region", "state"], "matched": 2}]
    housing = Atlas.research_catalog(topic="cpi", breakdown="housing")["series_search"]
    assert housing["matched"] == 12
    assert all(r["country"] == "US" for r in housing["records"])
    trim = Atlas.research_catalog(search="トリム")["series_search"]
    assert trim["matched"] == 2
    rent = Atlas.research_catalog(search="家賃")["series_search"]
    assert rent["matched"] == 8
    assert all("rent_cpi" in row["series"] for row in rent["records"])


def test_cleveland_probes_are_explicit_and_csv_shape_preserves_both_scales():
    assert all(row["source_id"] != "cleveland_fed" for row in check_sources()["checks"])
    selected = check_sources(["cleveland_fed"])
    assert len(selected["checks"]) == 2 and selected["status"] == "NOT_RUN"
    url = INFLATION_URLS["median_cpi"]
    fixture = PublicFixtures().get(url, 0)
    snapshot = Snapshot(fixture.body, url, fixture.retrieved_at)
    shape = source_shapes([snapshot])[0]
    assert shape["format"] == "csv"
    changed = Snapshot(fixture.body.replace(b"0.002", b"0.003"), url, fixture.retrieved_at)
    assert source_shapes([changed])[0]["shape_sha256"] == shape["shape_sha256"]


NEW_PRESETS = [
    "us_cpi_detail",
    "us_cpi_detail_yoy",
    "us_inflation_trend",
    "us_employment_age",
    "us_employment_occupation",
    "us_employment_region",
]


def test_seller_accepts_long_registered_occupation_but_excludes_cleveland():
    key = "unemployment_occupation_natural_resources_construction"
    assert ProductQuery(series=key).series == key
    assert ProductQuery(series="LNU04032222").series == key
    for series in INFLATION_SERIES:
        with pytest.raises(ValidationError):
            ProductQuery(series=series)


def test_monthly_panel_keeps_region_and_occupation_scope_and_null_inflation():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            {
                "dataset": "macro_panel",
                "allow_partial": True,
                "frequency": "monthly",
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
                "items": [
                    {
                        "id": "ca",
                        "query": {"dataset": "economics", "series": "employment_state_ca"},
                        "aggregation": "last",
                    },
                    {
                        "id": "service",
                        "query": {
                            "dataset": "economics",
                            "series": "unemployment_occupation_service",
                        },
                        "aggregation": "last",
                    },
                    {
                        "id": "trim",
                        "query": {
                            "dataset": "inflation_measures",
                            "series": "trimmed_mean_cpi_mom",
                        },
                        "aggregation": "last",
                    },
                ],
            }
        )
    rows = [item for period in result["records"] for item in period["records"]]
    assert all(
        row["region_code"] == "US-CA" and row["unit"] == "persons"
        for row in rows
        if row["id"] == "ca"
    )
    assert all(
        row["occupation"] == "service" and not row["seasonally_adjusted"]
        for row in rows
        if row["id"] == "service"
    )
    missing = next(row for row in rows if row["id"] == "trim" and row["period_end"] == "2026-02-28")
    assert missing["value"] is None and missing["status"] == "unavailable"


@pytest.mark.parametrize("preset", NEW_PRESETS)
def test_presets_execute_bounded_independent_series(preset):
    assert len(PRESETS[preset]["items"]) <= 10
    with Atlas("fixture") as atlas:
        result = atlas.get_research_preset({"preset": preset})
    assert result["status"] == "ok"


def test_sdk_cli_rest_and_schema_share_catalog_and_inflation_contracts():
    query = {"dataset": "inflation_measures", "series": "trimmed_mean_cpi_mom", "limit": 1}
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(query)
    with Atlas("fixture") as atlas, TestClient(create_app(provider="fixture")) as client:
        expected = atlas.get_research(query)
        assert client.post("/v1/research", json=query).json() == expected
        expected_catalog = json.loads(
            json.dumps(Atlas.research_catalog(topic="employment", breakdown="occupation"))
        )
        assert (
            client.get("/v1/research/catalog?topic=employment&breakdown=occupation").json()
            == expected_catalog
        )
        for args, wanted in [
            (
                [
                    "get",
                    "inflation_measures",
                    "--series",
                    query["series"],
                    "--limit",
                    "1",
                    "--provider",
                    "fixture",
                ],
                expected,
            ),
            (["catalog", "--topic", "employment", "--breakdown", "occupation"], expected_catalog),
        ]:
            run = subprocess.run(
                [sys.executable, "-m", "atlas_terminal", "research", *args, "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            assert json.loads(run.stdout) == wanted
        assert (
            client.post("/v1/research", json={**query, "series": "dallas_pce"}).status_code == 400
        )
        assert (
            client.post("/v1/research", json={**query, "start_date": "2026-01-01"}).status_code
            == 400
        )


@pytest.mark.asyncio
async def test_mcp_discovers_breakdown_and_returns_cleveland_cell_provenance(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        catalog = await session.call_tool(
            "atlas_research_catalog", {"topic": "employment", "breakdown": "age"}
        )
        assert not catalog.is_error and catalog.structured_content["series_search"]["matched"] == 8
        result = await session.call_tool(
            "atlas_get_research",
            {
                "query": {
                    "dataset": "inflation_measures",
                    "series": "median_cpi_mom",
                    "limit": 1,
                }
            },
        )
        assert not result.is_error
        assert result.structured_content["records"][0]["source"]["csv_column"] == 2
