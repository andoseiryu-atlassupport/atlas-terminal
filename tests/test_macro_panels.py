"""Calendar comparability, missingness and shared agent-facing entry points."""

import json
import subprocess
import sys
from copy import deepcopy
from decimal import localcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_models import parse_research
from atlas_terminal.research_panel import macro_panel, panel_queries
from atlas_terminal.research_quality import ResearchRequirements, assess_quality
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical

ROOT = Path(__file__).resolve().parents[1]
PANEL = json.loads((ROOT / "examples/research_panels/us_japan.json").read_text())
SOURCE = {
    "raw_source_url": "https://fixtures.atlas.invalid/panel",
    "raw_sha256": "0" * 64,
    "retrieved_at": "2026-10-20T00:00:00Z",
    "json_pointer": "",
}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def panel(**extra):
    return {
        "dataset": "macro_panel",
        "frequency": "quarterly",
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
        "items": [
            {
                "id": "prices",
                "aggregation": "mean",
                "query": {"dataset": "economics", "series": "cpi"},
            }
        ],
        **extra,
    }


def observation(day, value, **extra):
    return {
        "date": day,
        "value": value,
        "unit": "index_2020_100",
        "frequency": "monthly",
        "seasonally_adjusted": False,
        "status": "reported",
        "source": SOURCE,
        **extra,
    }


def read_result(rows, **coverage):
    return {
        "dataset": "economics",
        "source_id": "bls",
        "source_terms_url": "https://www.bls.gov/bls/linksite.htm",
        "sources": [SOURCE],
        "records": rows,
        "coverage": {"truncated": False, **coverage},
    }


def test_two_sources_aggregate_exactly_and_keep_raw_evidence_without_claiming_comparability():
    with Atlas("fixture") as atlas, localcontext() as context:
        context.prec = 3
        result = atlas.get_research(PANEL)
        compact = atlas.get_research(PANEL | {"response": {"view": "compact", "fields": ["value"]}})
        cell = compact["records"][0]["records"][0]
        evidence = atlas.get_evidence(cell["inputs"][0]["source"]["evidence_id"])
    values = result["records"][0]["records"]
    assert [row["value"] for row in values] == ["111.2000", "102.0000"]
    assert values[0]["input_count"] == 3 and values[0]["input_end"] == "2026-03-31"
    assert len(values[0]["inputs"]) == 3 and values[0]["unit_multiplier"] == 0
    assert evidence["evidence"]["xml_local_path"].endswith("/Obs[1]")
    assert cell["id"] == "us_prices" and cell["aggregation"] == "mean"
    assert cell["input_frequency"] == "monthly" and cell["base_period"] == "2020"
    assert result["comparability"]["statistical_comparability"] == "not_established"
    assert result["source_id"] == "multiple" and {s["source_id"] for s in result["sources"]} == {
        "imf",
        "oecd",
    }


def test_last_and_mean_have_explicit_different_results():
    q = deepcopy(PANEL)
    q["items"][0]["aggregation"] = "last"
    with Atlas("fixture") as atlas:
        result = atlas.get_research(q)
    assert result["records"][0]["records"][0]["value"] == "112.3000"
    assert result["records"][0]["records"][0]["derived"]


@pytest.mark.parametrize("null", [False, True])
def test_missing_month_is_never_silently_averaged_or_filled(null):
    rows = [observation("2026-01-31", "10"), observation("2026-03-31", "30")]
    if null:
        rows.append(observation("2026-02-28", None))

    def get(_):
        return read_result(rows)

    with pytest.raises(AtlasError) as exc:
        macro_panel(parse_research(panel()), get)
    assert exc.value.code == "INCOMPLETE_COVERAGE"
    result = macro_panel(parse_research(panel(allow_partial=True)), get)
    cell = result["records"][0]["records"][0]
    assert cell["value"] is None and cell["missing_period_ends"] == ["2026-02-28"]
    assert cell["input_count"] == (3 if null else 2)
    assert result["coverage"]["partial"]


def test_daily_last_date_does_not_claim_the_month_end_was_observed():
    q = panel(frequency="monthly", end_date="2026-01-31")
    q["items"][0]["aggregation"] = "last"
    rows = [observation("2026-01-20", "2", frequency="daily")]
    result = macro_panel(parse_research(q), lambda _: read_result(rows))
    result["dataset"] = "macro_panel"
    cell = result["records"][0]["records"][0]
    assert cell["date"] == "2026-01-20" and cell["period_end"] == "2026-01-31"
    assert cell["coverage_basis"] == "observed_dates_only_no_business_calendar"
    quality = assess_quality(result, ResearchRequirements(min_observation_date="2026-01-31"))
    assert not quality.meets_requirements and quality.latest_observation_date == "2026-01-20"


@pytest.mark.parametrize(
    "change",
    [
        {"unit": "different"},
        {"unit_multiplier": 9},
        {"seasonally_adjusted": True},
        {"base_period": "2015"},
        {"annualized": True},
        {"frequency": "daily"},
    ],
)
def test_basis_changes_cannot_be_aggregated(change):
    rows = [
        observation("2026-01-31", "10"),
        observation("2026-02-28", "20", **change),
        observation("2026-03-31", "30"),
    ]
    with pytest.raises(AtlasError, match="changes"):
        macro_panel(parse_research(panel()), lambda _: read_result(rows))


@pytest.mark.parametrize(
    "rows,error",
    [
        ([observation("2026-03-31", "10", frequency="quarterly")], "upsample"),
        ([observation("2026-01-31", "10", unit=None)], "units"),
        ([observation("2026-01-31", "10"), observation("2026-01-31", "20")], "duplicate"),
    ],
)
def test_upsampling_unknown_units_and_duplicate_dates_fail(rows, error):
    with pytest.raises(AtlasError, match=error):
        macro_panel(parse_research(panel(frequency="monthly")), lambda _: read_result(rows))


def test_read_failure_is_visible_and_never_retried_or_turned_into_zero():
    calls = []

    def get(read):
        calls.append(read)
        raise AtlasError(
            "SOURCE_RATE_LIMITED", "Try later.", retryable=True, retry_after_seconds=60
        )

    result = macro_panel(parse_research(panel(allow_partial=True)), get)
    assert len(calls) == 1 and result["errors"][0]["error"]["code"] == "SOURCE_RATE_LIMITED"
    assert result["records"][0]["records"][0]["value"] is None
    assert result["coverage"]["partial"]


def test_malformed_and_truncated_inputs_fail_closed_with_partial_results_opt_in():
    for get in (lambda _: read_result([], truncated=True), lambda _: {}):
        result = macro_panel(parse_research(panel(allow_partial=True)), get)
        assert result["errors"] and result["coverage"]["missing_cells"] == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"start_date": "2026-01-02"},
        {"end_date": "2026-03-30"},
        {"items": []},
        {"items": [{"id": "x", "query": {"dataset": "macro_panel"}, "aggregation": "mean"}]},
        {
            "items": [
                {
                    "id": "x",
                    "query": {"dataset": "economics", "series": "cpi", "limit": 1},
                    "aggregation": "mean",
                }
            ]
        },
        {
            "items": [
                {
                    "id": "x",
                    "query": {"dataset": "economics", "series": "cpi", "as_of": "2020-01-01"},
                    "aggregation": "mean",
                }
            ]
        },
        {
            "items": [
                {
                    "id": "x",
                    "query": {"dataset": "economics", "series": "cpi"},
                    "aggregation": "auto",
                }
            ]
        },
    ],
)
def test_invalid_panels_stop_before_any_read(patch):
    with pytest.raises(AtlasError):
        parsed = parse_research(panel(**patch))
        macro_panel(parsed, lambda _: pytest.fail("Invalid panel reached source"))


def test_all_items_are_validated_before_reading_the_first_item():
    q = panel()
    q["items"].append(
        {"id": "bad", "aggregation": "mean", "query": {"dataset": "economics", "series": "unknown"}}
    )
    with pytest.raises(AtlasError):
        parsed = parse_research(q)
        macro_panel(parsed, lambda _: pytest.fail("No source calls before complete validation"))


def test_daily_history_is_split_into_bounded_calendar_year_reads_without_overlap():
    q = panel(
        start_date="2024-02-01",
        end_date="2026-03-31",
        frequency="monthly",
        items=[
            {
                "id": "fx",
                "query": {"dataset": "fx_rates", "base_currency": "USD", "quote_currency": "JPY"},
                "aggregation": "mean",
            }
        ],
    )
    reads = panel_queries(parse_research(q))[0]
    assert [(r["start_date"], r["end_date"]) for r in reads] == [
        ("2024-02-01", "2024-12-31"),
        ("2025-01-01", "2025-12-31"),
        ("2026-01-01", "2026-03-31"),
    ]
    assert all(r["limit"] == 500 for r in reads)
    q.update(start_date="1997-01-01", end_date="2026-12-31")
    q["items"] = [dict(q["items"][0], id=str(i)) for i in range(3)]
    with pytest.raises(AtlasError, match="64"):
        parse_research(q)


def test_annual_average_rows_are_excluded_from_monthly_panels():
    rows = [
        observation("2026-12-31", "10"),
        observation("2026-12-31", "999", frequency="annual_average"),
    ]
    q = panel(frequency="monthly", start_date="2026-12-01", end_date="2026-12-31")
    result = macro_panel(parse_research(q), lambda _: read_result(rows))
    assert result["records"][0]["records"][0]["value"] == "10"


def test_all_new_queries_work_through_http_and_cli_with_the_same_contract(tmp_path):
    app = create_app(provider="fixture", directory=tmp_path / "http")
    try:
        with TestClient(app) as client, Atlas("fixture") as atlas:
            expected = atlas.get_research(PANEL)
            response = client.post("/v1/research", json=PANEL)
            assert response.status_code == 200 and response.json()["records"] == expected["records"]
            catalog = client.get(
                "/v1/research/catalog",
                params={
                    "search": "inflation",
                    "country": "US",
                    "source_id": "bls",
                    "dataset": "economics",
                },
            )
            assert catalog.status_code == 200 and catalog.json()["series_search"]["matched"] == 3
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "atlas_terminal",
                "research",
                "run",
                "--input",
                "-",
                "--provider",
                "fixture",
                "--json",
            ],
            input=json.dumps(PANEL),
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(completed.stdout)["records"] == expected["records"]
    finally:
        app.state.orders.close()


@pytest.mark.asyncio
async def test_one_actual_mcp_connection_discovers_queries_aligns_and_resolves_evidence(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp-state")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        found = await session.call_tool(
            "atlas_research_catalog", {"search": "失業", "country": "JP", "view": "compact"}
        )
        assert not found.is_error and found.structured_content["series_search"]["matched"] == 1
        catalog = await session.call_tool(
            "atlas_get_research", {"query": {"dataset": "sdmx_catalog", "source_id": "imf"}}
        )
        assert not catalog.is_error and catalog.structured_content["records"][0][
            "dataflow"
        ].startswith("IMF.DEMO")
        query = PANEL | {"response": {"view": "compact"}}
        result = await session.call_tool("atlas_get_research", {"query": query})
        assert not result.is_error
        values = result.structured_content["records"][0]["records"]
        assert values[0]["value"] == "111.2000"
        evidence = await session.call_tool("atlas_get_evidence", values[0]["inputs"][0]["source"])
        assert (
            not evidence.is_error
            and ".invalid/" in evidence.structured_content["evidence"]["raw_source_url"]
        )


def test_panel_response_remains_within_presentation_budget_and_continuations_do_not_refetch():
    query = PANEL | {"frequency": "monthly", "response": {"view": "compact", "page_size": 1}}
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query)
        assert len(canonical(result)) < 4 * 1024 * 1024
        atlas._research.public.get = lambda *args: pytest.fail("Continuation refetched")
        resumed = atlas.get_research(
            query | {"response": {**query["response"], "cursor": result["response"]["next_cursor"]}}
        )
        assert resumed["records"][0]["period_end"] == "2026-02-28"
