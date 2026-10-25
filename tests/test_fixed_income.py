"""Government curves, original CSV cells, exact arithmetic and real interface execution."""

import csv
import io
import json
import subprocess
import sys
from decimal import Decimal, Inexact, localcontext
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import JGB_URLS, parse_jgb_csv
from atlas_terminal.macro_catalog import CURVE_SERIES, MACRO_SERIES
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import (
    PublicFixtures,
    PublicTransport,
)
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_presets import ResearchPresetQuery, preset_plan
from atlas_terminal.research_views import SourceEvidence
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import source_shapes
from atlas_terminal.util import canonical, digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


class Sources(PublicFixtures):
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        if url in self.overrides:
            return Snapshot(
                self.overrides[url],
                "https://fixtures.atlas.invalid/modified",
                "2026-10-20T00:00:00Z",
            )
        return super().get(url, ttl)


def application(overrides=None):
    atlas = Atlas("fixture")
    atlas._research.public = Sources(overrides)
    return atlas


def query(series, year=2025, **extra):
    return {"dataset": "macro_analytics", "series": series, "year": year, **extra}


@pytest.mark.parametrize(
    "series,expected",
    [
        ("yield_spread_10y_2y", "30"),
        ("yield_spread_10y_3m", "-10"),
        ("yield_spread_30y_5y", "80"),
        ("yield_butterfly_2y_5y_10y", "-50"),
        ("par_inflation_compensation_5y", "310"),
        ("par_inflation_compensation_7y", "305"),
        ("par_inflation_compensation_10y", "300"),
        ("par_inflation_compensation_20y", "280"),
        ("par_inflation_compensation_30y", "290"),
        ("yield_change_2y", "20"),
        ("yield_change_5y", "0"),
        ("yield_change_10y", "10"),
        ("yield_change_30y", "10"),
        ("real_yield_change_5y", "10"),
        ("real_yield_change_10y", "5"),
        ("real_yield_change_30y", "10"),
    ],
)
def test_us_curves_match_hand_calculated_basis_points_and_retain_formula_inputs(series, expected):
    with application() as atlas:
        row = atlas.get_research(query(series, limit=1))["records"][0]
    assert Decimal(row["value"]) == Decimal(expected)
    assert row["country"] == "US" and row["currency"] == "USD"
    assert row["date"] == "2025-12-31" and row["unit"] == "basis_points"
    assert row["formula"] == MACRO_SERIES[series]["formula"]
    assert row["status"] == "derived" and row["published_at"] is None
    assert all(len(part["source"]["raw_sha256"]) == 64 for part in row["inputs"])
    if "change" in series:
        assert row["comparison_date"] == "2025-12-30" and row["elapsed_calendar_days"] == 1
        assert {part["role"] for part in row["inputs"]} == {"current", "previous"}
    else:
        assert {part["date"] for part in row["inputs"]} == {row["date"]}
    if "inflation_compensation" in series:
        assert "not a zero-coupon breakeven" in row["interpretation"]


@pytest.mark.parametrize(
    "series,expected",
    [
        ("jgb_spread_10y_2y", "100"),
        ("jgb_spread_20y_10y", "50"),
        ("jgb_spread_30y_10y", "90"),
        ("jgb_spread_40y_30y", "30"),
        ("jgb_butterfly_2y_5y_10y", "-40"),
        ("jgb_yield_change_2y", "10"),
        ("jgb_yield_change_5y", "10"),
        ("jgb_yield_change_10y", "5"),
        ("jgb_yield_change_20y", "10"),
        ("jgb_yield_change_30y", "10"),
    ],
)
def test_japanese_curves_use_jpy_percent_units_and_correct_weekend_baseline(series, expected):
    with application() as atlas:
        result = atlas.get_research(query(series, year=2026, limit=1))
    row = result["records"][0]
    assert Decimal(row["value"]) == Decimal(expected)
    assert result["source_id"] == "japan_mof"
    assert row["country"] == "JP" and row["currency"] == "JPY"
    if "change" in series:
        assert row["comparison_date"] == "2026-10-16" and row["elapsed_calendar_days"] == 3
    assert "Ministry of Finance" in result["attributions"][0]["notice"]


def test_original_jgb_csv_cells_overlap_revisions_and_nulls_are_not_silently_filled():
    with application() as atlas:
        data = atlas.get_research({"dataset": "jgb_yields"})
        change = atlas.get_research(query("jgb_yield_change_40y", year=2026))
    rows = {(row["date"], row["maturity"]): row for row in data["records"]}
    assert rows["2026-10-16", "10YEAR"]["value"] == "1.15"
    assert rows["2026-10-16", "40YEAR"]["value"] is None  # History has 2.20 here.
    assert rows["2025-12-30", "1YEAR"]["value"] == "-0.10"
    assert rows["2025-12-30", "2YEAR"]["value"] == "0.00"
    assert change["records"][0]["value"] is None  # Do not skip the missing preceding value.
    assert data["coverage"]["observed_records"] == 45
    assert data["coverage"]["overlapping_dates"] == 1
    for row in rows.values():
        source = row["source"]
        name = source["raw_source_url"].rsplit("/", 1)[1]
        body = RESOURCE.joinpath(name).read_bytes()
        cells = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        original = cells[source["csv_row"] - 1][source["csv_column"] - 1]
        assert original == row["value"] or original in {"-", ""} and row["value"] is None
        assert source["raw_sha256"] == digest(body)
        assert source["csv_header"] == row["maturity"].replace("YEAR", "Y")
        SourceEvidence.model_validate(source)


def test_change_baselines_precede_the_output_window_and_date_filters_precede_limit():
    with application() as atlas:
        result = atlas.get_research(
            {
                "dataset": "macro_analytics",
                "series": "yield_change_10y",
                "start_date": "2025-12-31",
                "end_date": "2025-12-31",
                "limit": 1,
            }
        )
        japanese = atlas.get_research(
            {
                "dataset": "macro_analytics",
                "series": "jgb_yield_change_10y",
                "start_date": "2026-10-19",
                "end_date": "2026-10-19",
                "limit": 1,
            }
        )
        first = atlas.get_research(query("yield_change_10y"))["records"][-1]
    assert result["records"][0]["comparison_date"] == "2025-12-30"
    assert Decimal(result["records"][0]["value"]) == 10
    assert japanese["records"][0]["comparison_date"] == "2026-10-16"
    assert first["value"] is None and first["comparison_date"] is None


def test_same_maturity_par_gaps_do_not_join_different_observation_dates_or_skip_nulls():
    with application() as atlas:
        data = atlas.get_research(query("par_inflation_compensation_10y", year=2026))
        seven = atlas.get_research(query("par_inflation_compensation_7y"))
    assert data["records"][0]["date"] == "2026-10-19" and data["records"][0]["value"] is None
    assert Decimal(data["records"][1]["value"]) == Decimal("395")
    assert seven["records"][1]["value"] is None


def test_arithmetic_and_original_values_ignore_callers_precision_and_inexact_traps():
    queries = [query(name, year=2026 if name.startswith("jgb") else 2025) for name in CURVE_SERIES]
    with application() as atlas:
        expected = [atlas.get_research(data)["records"] for data in queries]
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            assert [atlas.get_research(data)["records"] for data in queries] == expected


@pytest.mark.parametrize(
    "change",
    [
        "header",
        "unit",
        "duplicate_header",
        "duplicate_date",
        "invalid_date",
        "date_format",
        "nan",
        "infinity",
        "unknown_missing",
        "short_row",
        "extra_column",
        "after_trailer",
        "html",
    ],
)
def test_malformed_jgb_source_fails_before_becoming_a_dated_observation(change):
    text = RESOURCE.joinpath("jgb_current.csv").read_text()
    replacements = {
        "header": ("Date,", "Day,"),
        "unit": ("Unit : %", "Unit : ratio"),
        "duplicate_header": ("1Y,2Y", "2Y,2Y"),
        "invalid_date": ("2026/10/19", "2026/2/30"),
        "date_format": ("2026/10/19", "R8.10.19"),
        "nan": ("1.20", "NaN"),
        "infinity": ("1.20", "Infinity"),
        "unknown_missing": ("1.20", "NA"),
        "short_row": (",2.40\n", "\n"),
        "extra_column": (",2.40\n", ",2.40,3.0\n"),
    }
    if change in replacements:
        text = text.replace(*replacements[change])
    elif change == "duplicate_date":
        text = text.replace("2026/10/19", "2026/10/16")
    elif change == "after_trailer":
        text += text.splitlines()[2] + "\n"
    else:
        text = "<html>upstream error</html>"
    with pytest.raises(AtlasError) as caught:
        parse_jgb_csv(Snapshot(text.encode(), JGB_URLS[1], "2026-10-20T00:00:00Z"))
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_jgb_encoding_csv_evidence_and_source_fingerprints_are_consistent():
    text = RESOURCE.joinpath("jgb_current.csv").read_text().replace("Note:", "注記:")
    for body in (text.encode("utf-8-sig"), text.encode("cp932")):
        snapshot = Snapshot(body, JGB_URLS[1], "2026-10-20T00:00:00Z")
        rows, document = parse_jgb_csv(snapshot)
        assert rows["2026-10-19"].record("10YEAR")["value"] == "1.20"
        SourceEvidence.model_validate(document)
        assert source_shapes([snapshot])[0]["format"] == "csv"
    for bad in ({"csv_row": 1}, {"csv_column": 1}, {"csv_row": 0, "csv_column": 1}):
        with pytest.raises(ValueError):
            SourceEvidence.model_validate(
                {
                    "raw_source_url": JGB_URLS[1],
                    "raw_sha256": "0" * 64,
                    "retrieved_at": "2026-10-20T00:00:00Z",
                    **bad,
                }
            )


def test_public_mof_transport_uses_only_fixed_urls_and_validates_csv_without_private_sec_contact(
    tmp_path,
):
    requests = []

    def handler(request):
        requests.append(request)
        assert "SECRET" not in str(request.headers)
        name = "jgb_history.csv" if str(request.url) == JGB_URLS[0] else "jgb_current.csv"
        return httpx.Response(200, content=RESOURCE.joinpath(name).read_bytes())

    transport = PublicTransport(
        cache_path=tmp_path / "public.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        for url in JGB_URLS:
            assert transport.get(url, 21600).url == url
        for url in (
            JGB_URLS[1] + "?redirect=evil",
            JGB_URLS[1].replace("https:", "http:"),
            JGB_URLS[1] + "/../key",
        ):
            with pytest.raises(AtlasError):
                transport.get(url, 21600)
    finally:
        transport.close()
    assert len(requests) == 2


def test_bond_presets_preview_sources_year_scope_search_and_execute_without_network_in_preview(
    monkeypatch,
):
    with application() as atlas:
        source = atlas._research.public
        for name in ("us_bond_context", "japan_bond_context", "us_japan_bond_context"):
            preview = atlas.get_research_preset({"preset": name, "year": 2025, "preview": True})
            assert source.calls == []
            assert len(preview["plan"]["items"]) <= 10
        plan = preset_plan(ResearchPresetQuery(preset="japan_bond_context", year=2026))
        assert plan.items[0].source_ids == ["japan_mof"]
        assert plan.items[-1].source_ids == ["estat"]
        assert "year" not in plan.items[-1].query  # CPI has its own observation date.
        result = atlas.get_research_preset({"preset": "japan_bond_context", "year": 2026})
        assert result["status"] == "ok" and len(result["overview"]) == 8
        assert result["is_synthetic"] and not result["requires_wallet"]
    found = Atlas.research_catalog(search="日本国債", country="JP", frequency="daily")
    assert found["series_search"]["matched"] > 1
    for row in found["series_search"]["records"]:
        parse_research(row["query"])


def test_bond_panel_aggregates_explicit_daily_observations_with_original_dates():
    with application() as atlas:
        result = atlas.get_research(
            {
                "dataset": "macro_panel",
                "frequency": "monthly",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
                "items": [
                    {
                        "id": "jgb_slope",
                        "aggregation": "last",
                        "query": {
                            "dataset": "macro_analytics",
                            "series": "jgb_spread_10y_2y",
                        },
                    }
                ],
            }
        )
    cell = result["records"][0]["records"][0]
    assert Decimal(cell["value"]) == 100
    assert cell["date"] == "2026-10-19" and cell["period_end"] == "2026-10-31"


@pytest.mark.parametrize(
    "bad",
    [
        {"dataset": "jgb_yields", "identifier": "JP"},
        query("par_inflation_compensation_10y", year=2002),
        query("jgb_spread_10y_2y", year=2026, start_date="2026-10-01", end_date="2026-10-31"),
        {"dataset": "macro_analytics", "series": "breakeven_inflation"},
    ],
)
def test_invalid_bond_queries_fail_before_any_source_request(bad):
    with application() as atlas, pytest.raises(AtlasError) as caught:
        atlas.get_research(bad)
    assert caught.value.code == "INVALID_INPUT" and not atlas._research.public.calls
    if bad.get("year") == 2002:
        with pytest.raises(ValidationError):
            Draft202012Validator(ResearchQuery.model_json_schema()).validate(bad)


def test_csv_evidence_pagination_quality_http_and_cli_share_the_same_results(tmp_path):
    data = {"dataset": "jgb_yields", "year": 2026, "limit": 15}
    app = create_app(provider="fixture", directory=tmp_path / "http")
    try:
        with TestClient(app) as client, application() as atlas:
            expected = atlas.get_research(data)
            response = client.post("/v1/research", json=data)
            assert response.status_code == 200 and response.json() == expected
            compact = client.post(
                "/v1/research",
                json={
                    **data,
                    "response": {"view": "compact", "page_size": 3, "fields": ["value"]},
                    "requirements": {"min_observation_date": "2026-10-19"},
                },
            ).json()
            assert compact["records"][0]["currency"] == "JPY"
            evidence_id = compact["records"][0]["source"]["evidence_id"]
            evidence = client.get("/v1/research/evidence/" + evidence_id).json()
            assert evidence["evidence"]["csv_column"] == 2
            assert digest(canonical(evidence["evidence"])) == evidence_id
            next_page = client.post(
                "/v1/research",
                json={
                    **data,
                    "response": {
                        "view": "compact",
                        "page_size": 3,
                        "fields": ["value"],
                        "cursor": compact["response"]["next_cursor"],
                    },
                    "requirements": {"min_observation_date": "2026-10-19"},
                },
            ).json()
            assert next_page["response"]["offset"] == 3
    finally:
        app.state.orders.close()
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "jgb_yields",
            "--year",
            "2026",
            "--limit",
            "15",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(run.stdout) == expected


async def test_one_actual_mcp_connection_runs_japan_preset_and_resolves_csv_evidence():
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "atlas_terminal",
            "mcp",
            "serve",
            "--provider",
            "fixture",
            "--research-only",
        ],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        catalog = await session.call_tool(
            "atlas_research_catalog", {"dataset": "jgb_yields", "view": "compact"}
        )
        assert catalog.structured_content["datasets"][0]["source_ids"] == ["japan_mof"]
        result = await session.call_tool(
            "atlas_get_research_preset",
            {
                "query": {
                    "preset": "japan_bond_context",
                    "year": 2026,
                    "include": ["jgb_curve", "slope_2s10s", "yield_change_10y"],
                },
            },
        )
        assert not result.is_error and result.structured_content["status"] == "ok"
        batches = result.structured_content["batch"]["items"]
        assert batches[1]["result"]["source_id"] == "japan_mof"
        evidence_id = batches[0]["result"]["records"][0]["source"]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert not evidence.is_error and evidence.structured_content["evidence"]["csv_row"] == 4
