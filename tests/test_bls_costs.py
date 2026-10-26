"""BLS frequency, measurement and evidence contracts using fictional source bytes."""

import json
import subprocess
import sys
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import (
    BLS_BASE,
    BLS_DETAILS,
    BLS_SERIES,
    PublicFixtures,
    PublicTransport,
)
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.server import create_app
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
QUARTERLY = (
    "employment_cost_compensation",
    "employment_cost_wages",
    "nonfarm_productivity_growth",
    "unit_labor_cost_growth",
)
DATES = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def query(series="nonfarm_productivity_growth", **extra):
    return {"dataset": "economics", "series": series, **DATES, **extra}


def fixture(series):
    return RESOURCE.joinpath("bls_" + BLS_SERIES[series][0] + ".json").read_bytes()


def changed(series, transform, **extra):
    class Changed(PublicFixtures):
        def get(self, url, ttl):
            snapshot = super().get(url, ttl)
            raw = json.loads(snapshot.body)
            transform(raw)
            return Snapshot(json.dumps(raw).encode(), snapshot.url, snapshot.retrieved_at)

    with Atlas("fixture") as atlas:
        atlas._research.public = Changed()
        return atlas.get_research(query(series, **extra))


@pytest.mark.parametrize("series", BLS_DETAILS)
def test_new_selections_keep_exact_reported_values_and_original_evidence(series):
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query(series))
        by_code = atlas.get_research(query(BLS_SERIES[series][0]))
        compact = atlas.get_research(
            query(series, response={"view": "compact", "fields": ["value"]})
        )
        assert full["records"] == by_code["records"]
        for row, short in zip(full["records"], compact["records"], strict=True):
            assert row["published_at"] is None and row["status"] in {"reported", "unavailable"}
            assert row["frequency"] == BLS_DETAILS[series]["frequency"]
            assert row["annualized"] == short["annualized"] == BLS_DETAILS[series]["annualized"]
            assert row["base_period"] == short["base_period"]
            assert row["unit"] == short["unit"] == BLS_SERIES[series][2]
            assert row["value"] == short["value"]
            evidence = atlas.get_evidence(short["source"]["evidence_id"])["evidence"]
            assert evidence == row["source"]
            assert evidence["raw_sha256"] == digest(fixture(series))
            original = json.loads(fixture(series))
            for part in evidence["json_pointer"].split("/")[1:]:
                original = original[int(part)] if isinstance(original, list) else original[part]
            assert row["value"] == (None if original["value"] == "-" else original["value"])
            assert row["footnotes"] == original["footnotes"]
    assert full["is_synthetic"] and not full["coverage"]["point_in_time"]


@pytest.mark.parametrize("series", QUARTERLY)
def test_quarter_ends_and_annual_summary_exclusion_preserve_measurement(series):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(series))
        latest = atlas.get_research(query(series, limit=1))
    assert [r["date"] for r in result["records"]] == [
        "2026-12-31",
        "2026-09-30",
        "2026-06-30",
        "2026-03-31",
    ]
    assert all(r["period_kind"] == "quarter" for r in result["records"])
    assert result["records"][1]["value"] is None and result["records"][2]["value"] == "0"
    assert latest["records"][0]["period"] == "Q04"
    if series.endswith("growth"):
        assert latest["records"][0]["value"] == "-1.250"
        assert latest["records"][0]["reference_period_kind"] == "quarter_over_quarter_change"
        assert result["coverage"]["excluded_annual_summary_rows"] == 1
    else:
        assert latest["records"][0]["value"] == "102.500"
        assert latest["records"][0]["reference_period_kind"] == "quarter_end_reference_month"
        assert result["coverage"]["excluded_annual_summary_rows"] == 0


@pytest.mark.parametrize("period", ["M03", "M13", "Q00", "Q06", "Q1", "A01"])
def test_quarterly_series_reject_monthly_and_malformed_periods(period):
    def transform(raw):
        raw["Results"]["series"][0]["data"][0]["period"] = period

    with pytest.raises(AtlasError) as error:
        changed("employment_cost_compensation", transform)
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("series", ["cpi", "import_prices", "export_prices"])
def test_monthly_series_do_not_silently_adopt_quarterly_periods(series):
    def transform(raw):
        raw["Results"]["series"][0]["data"][0]["period"] = "Q01"

    with pytest.raises(AtlasError) as error:
        changed(series, transform)
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "fault", ["series", "duplicate_quarter", "duplicate_summary", "year", "numeric", "rows"]
)
def test_invalid_quarterly_identity_and_values_fail_closed(fault):
    def transform(raw):
        source = raw["Results"]["series"][0]
        rows = source["data"]
        if fault == "series":
            source["seriesID"] = "OTHER"
        elif fault.startswith("duplicate"):
            rows.append(rows[-1] if fault == "duplicate_summary" else rows[0])
        elif fault == "year":
            rows[0]["year"] = "0000"
        elif fault == "numeric":
            rows[0]["value"] = "NaN"
        else:
            source["data"] = rows * 11

    with pytest.raises(AtlasError) as error:
        changed("nonfarm_productivity_growth", transform)
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_quarterly_date_filter_is_inclusive_at_the_actual_period_end():
    with Atlas("fixture") as atlas:
        before = atlas.get_research(query(end_date="2026-06-29"))
        through = atlas.get_research(query(end_date="2026-06-30"))
    assert [r["date"] for r in before["records"]] == ["2026-03-31"]
    assert [r["date"] for r in through["records"]] == ["2026-06-30", "2026-03-31"]


def test_annual_summary_cannot_replace_an_absent_fourth_quarter():
    def transform(raw):
        source = raw["Results"]["series"][0]
        source["data"] = [row for row in source["data"] if row["period"] != "Q04"]

    result = changed("nonfarm_productivity_growth", transform)
    assert result["coverage"]["excluded_annual_summary_rows"] == 1
    assert {row["period"] for row in result["records"]} == {"Q01", "Q02", "Q03"}
    assert not any(row["date"] == "2026-12-31" for row in result["records"])


def test_monthly_trade_prices_keep_annual_averages_separate():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("import_prices", start_date="2025-01-01"))
    annual = result["records"][-1]
    assert annual["period"] == "M13" and annual["frequency"] == "annual_average"
    assert annual["date"] == "2025-12-31" and not annual["annualized"]
    assert annual["base_period"] == "2000" and not annual["seasonally_adjusted"]


def test_quarterly_history_uses_bounded_posts_and_reuses_cached_source_bytes(tmp_path):
    requests = []
    clock = [1000.0]

    def handler(request):
        requests.append(request)
        assert request.method == "POST" and str(request.url) == BLS_BASE
        body = json.loads(request.content)
        assert body["seriesid"] == ["PRS85006092"]
        rows = [
            {"year": str(year), "period": f"Q0{quarter}", "value": "-1.125", "footnotes": []}
            for year in range(int(body["startyear"]), int(body["endyear"]) + 1)
            for quarter in range(1, 6)
        ]
        return httpx.Response(
            200,
            json={
                "status": "REQUEST_SUCCEEDED",
                "Results": {"series": [{"seriesID": "PRS85006092", "data": rows}]},
            },
        )

    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )
        q = query(start_date="2000-01-01", end_date="2026-12-31", limit=500)
        result = atlas.get_research(q)
        atlas.get_research(q)
    assert len(requests) == 3
    assert [
        (json.loads(r.content)["startyear"], json.loads(r.content)["endyear"]) for r in requests
    ] == [("2000", "2009"), ("2010", "2019"), ("2020", "2026")]
    assert (
        len(result["records"]) == 108 and result["coverage"]["excluded_annual_summary_rows"] == 27
    )
    assert result["records"][0]["source"]["request_body"]["endyear"] == "2026"
    assert result["records"][-1]["date"] == "2000-03-31"


def test_catalog_schema_and_no_network_preset_plan_describe_actual_frequencies(monkeypatch):
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    for series in BLS_DETAILS:
        validator.validate(query(series))
    catalog = Atlas.research_catalog(dataset="economics", country="US", frequency="quarterly")
    assert {r["series"] for r in catalog["series_search"]["records"]} == set(QUARTERLY)
    for name in QUARTERLY:
        assert catalog["economic_series"][name]["frequency"] == "quarterly"
    with Atlas("public") as atlas:

        def forbidden(*args, **kwargs):
            pytest.fail("Preview performed a source request")

        monkeypatch.setattr(atlas, "get_research", forbidden)
        preview = atlas.get_research_preset(
            {"preset": "us_inflation_costs", **DATES, "preview": True}
        )
        assert atlas._research.public is None
    with Atlas("fixture") as atlas:
        result = atlas.get_research_preset({"preset": "us_inflation_costs", **DATES})
    assert preview["plan"] == result["plan"] and result["status"] == "ok"
    assert len(result["overview"]) == 8
    assert all(row["source_ids"] == ["bls"] for row in result["overview"])


def test_quarterly_panels_preserve_index_and_annualization_and_do_not_fill_missing_values():
    q = {
        "dataset": "macro_panel",
        **DATES,
        "frequency": "quarterly",
        "allow_partial": True,
        "items": [
            {"id": name, "query": {"dataset": "economics", "series": name}, "aggregation": "last"}
            for name in ("employment_cost_compensation", "nonfarm_productivity_growth")
        ],
    }
    with Atlas("fixture") as atlas:
        result = atlas.get_research(q)
        with pytest.raises(AtlasError, match="upsample"):
            atlas.get_research(q | {"frequency": "monthly", "allow_partial": False})
    rows = {row["period_end"]: row["records"] for row in result["records"]}
    assert rows["2026-03-31"][0]["base_period"] == "2005-12"
    assert not rows["2026-03-31"][0]["annualized"] and rows["2026-03-31"][1]["annualized"]
    assert all(cell["value"] == "0" for cell in rows["2026-06-30"])
    assert all(cell["value"] is None for cell in rows["2026-09-30"])
    assert all(cell["input_count"] == 1 for cell in rows["2026-12-31"])


@pytest.mark.parametrize(
    "series", ["employment_cost_compensation", "nonfarm_productivity_growth", "import_prices"]
)
def test_sdk_cli_and_http_share_new_bls_results(series, tmp_path):
    q = query(series)
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(q)
    with TestClient(create_app(provider="fixture", directory=tmp_path / "http")) as http:
        response = http.post("/v1/research", json=q)
        assert response.status_code == 200 and response.json() == expected
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "economics",
            "--series",
            series,
            "--start-date",
            DATES["start_date"],
            "--end-date",
            DATES["end_date"],
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
async def test_actual_mcp_session_executes_cost_preset_and_resolves_quarterly_evidence(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        assert len((await session.list_tools()).tools) == 6
        preset = await session.call_tool(
            "atlas_get_research_preset", {"query": {"preset": "us_inflation_costs", **DATES}}
        )
        assert not preset.is_error and preset.structured_content["status"] == "ok"
        response = await session.call_tool(
            "atlas_get_research",
            {"query": query(response={"view": "compact", "fields": ["value"]})},
        )
        assert not response.is_error
        row = response.structured_content["records"][0]
        assert row["value"] == "-1.250" and row["annualized"] and row["period"] == "Q04"
        evidence = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
        )
        assert not evidence.is_error
        assert evidence.structured_content["evidence"]["raw_sha256"] == digest(
            fixture("nonfarm_productivity_growth")
        )
