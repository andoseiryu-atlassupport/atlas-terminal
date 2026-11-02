"""Short spread names preserve exact same-date calculations and shared interfaces."""

import json
from datetime import UTC, datetime
from decimal import Decimal, Inexact, localcontext
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from typer.testing import CliRunner

from atlas_terminal.cli import app
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.fixed_income_sources import JGB_URLS
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_panel import panel_queries
from atlas_terminal.research_spreads import underlying_query
from atlas_terminal.server import create_app
from atlas_terminal.spread_catalog import SPREADS

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
EXPECTED = {
    "us_10y_2y": "30",
    "us_10y_3m": "-10",
    "us_30y_5y": "80",
    "jp_10y_2y": "100",
    "jp_20y_10y": "50",
    "jp_30y_10y": "90",
    "jp_40y_30y": "30",
    "ea_aaa_10y_2y": "60",
    "ea_aaa_30y_5y": "90",
    "ea_aaa_30y_10y": "60",
    "ea_all_10y_2y": "70",
    "ea_all_30y_5y": "80",
    "ea_all_30y_10y": "60",
    "au_10y_2y": "60",
    "au_10y_3y": "50",
}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def query(series="us_10y_2y", **extra):
    return {
        "dataset": "interest_spreads",
        "series": series,
        "year": 2025 if series.startswith("us_") else 2026,
        **extra,
    }


@pytest.mark.parametrize("series,expected", EXPECTED.items())
def test_short_names_match_hand_calculations_and_keep_original_evidence(series, expected):
    assert set(SPREADS) == set(EXPECTED)
    spec = SPREADS[series]
    data = query(series)
    with Atlas("fixture") as atlas:
        result = atlas.get_spread(series, year=data["year"])
        legacy = atlas.get_research(
            data | {"dataset": "macro_analytics", "series": spec["legacy_series"], "limit": 1}
        )
    row = result["records"][0]
    assert len(result["records"]) == 1 and Decimal(row["value"]) == Decimal(expected)
    assert Decimal(row["percentage_points"]) * 100 == Decimal(expected)
    assert row["inputs"] == legacy["records"][0]["inputs"]
    assert row["formula"] == legacy["records"][0]["formula"]
    assert row["underlying_series"] == spec["legacy_series"]
    assert row["spread_type"] == "yield_curve" and row["direction"] == "long_minus_short"
    assert row["country"] == spec["country"] and row["currency"] == spec["currency"]
    assert result["source_id"] == spec["source_id"]
    assert row["unit"] == "basis_points" and row["published_at"] is None
    assert {part["date"] for part in row["inputs"]} == {row["date"]}
    assert {part["curve"] for part in row["inputs"]} == {spec["curve"]}
    assert [part["coefficient"] for part in row["inputs"]] == [1, -1]
    assert all(len(part["source"]["raw_sha256"]) == 64 for part in row["inputs"])
    assert result["is_synthetic"] and result["coverage"]["truncated"]


def test_history_filters_before_limit_and_can_return_an_older_date_only_when_requested():
    with Atlas("fixture") as atlas:
        history = atlas.get_spread("us_10y_2y", year=2025, limit=3)
        one = atlas.get_spread("us_10y_2y", start_date="2025-12-30", end_date="2025-12-30")
    assert [r["date"] for r in history["records"]] == ["2025-12-31", "2025-12-30", "2025-12-29"]
    assert not history["coverage"]["truncated"]
    assert one["records"][0]["date"] == "2025-12-30"
    assert Decimal(one["records"][0]["value"]) == 40
    assert one["selection"]["effective_start_date"] == "2025-12-30"


class ChangedJapan(PublicFixtures):
    def __init__(self, body):
        self.body = body

    def get(self, url, ttl):
        if url == JGB_URLS[-1]:
            return Snapshot(
                self.body, "https://fixtures.atlas.invalid/changed.csv", "2026-10-20T00:00:00Z"
            )
        return super().get(url, ttl)


@pytest.mark.parametrize("ten_year", ["-", "0.20", "-0.10"])
def test_latest_null_zero_and_negative_values_never_fall_back_to_an_older_observation(ten_year):
    body = (
        RESOURCE.joinpath("jgb_current.csv")
        .read_text()
        .replace("0.90,1.20,1.50", f"0.90,{ten_year},1.50")
        .encode()
    )
    with Atlas("fixture") as atlas:
        atlas._research.public = ChangedJapan(body)
        result = atlas.get_spread("jp_10y_2y", year=2026)
    row = result["records"][0]
    assert row["date"] == "2026-10-19" and row["inputs"][1]["value"] == "0.20"
    if ten_year == "-":
        assert row["value"] is None and row["percentage_points"] is None
        assert row["status"] == "unavailable" and result["coverage"]["unavailable_records"] == 1
    else:
        assert Decimal(row["value"]) == (Decimal(ten_year) - Decimal("0.20")) * 100
    assert (
        result["selection"]["missing_latest_policy"] == "retain_unavailable_no_older_value_fallback"
    )


@pytest.mark.parametrize(
    "series,start,end",
    [
        ("us_10y_2y", "2025-01-01", "2026-12-31"),
        ("jp_10y_2y", "2026-01-01", "2027-01-01"),
        ("ea_aaa_10y_2y", "2026-01-01", "2027-01-01"),
        ("au_10y_2y", "2026-01-01", "2027-01-01"),
    ],
)
def test_default_window_uses_source_local_year_and_includes_the_prior_calendar_year(
    series, start, end, monkeypatch
):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 1, 1, 0, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr("atlas_terminal.research_spreads.datetime", Frozen)
    q = parse_research({"dataset": "interest_spreads", "series": series})
    assert q.limit == 1 and "limit" not in q.model_fields_set
    internal = underlying_query(q)
    assert internal.start_date == start and internal.end_date == end
    assert q.start_date is None and q.end_date is None and q.series == series


def test_default_us_window_uses_last_year_when_the_current_year_has_no_observations(monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 17, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr("atlas_terminal.research_spreads.datetime", Frozen)
    with Atlas("fixture") as atlas:
        result = atlas.get_spread("us_10y_2y")
    assert result["records"][0]["date"] == "2025-12-31"
    assert result["selection"]["effective_end_date"] == "2026-01-01"


def test_decimal_calculation_and_percentage_points_ignore_low_precision_and_inexact_traps():
    body = (
        RESOURCE.joinpath("jgb_current.csv")
        .read_text()
        .replace("0.90,1.20,1.50", "0.90,1.23456789012345678901,1.50")
        .encode()
    )
    with Atlas("fixture") as atlas, localcontext() as context:
        atlas._research.public = ChangedJapan(body)
        context.prec = 2
        context.traps[Inexact] = True
        row = atlas.get_spread("jp_10y_2y", year=2026)["records"][0]
    assert Decimal(row["value"]) == Decimal("103.456789012345678901")
    assert row["percentage_points"] == "1.03456789012345678901"


@pytest.mark.parametrize(
    "bad",
    [
        {"series": "us_jp_10y"},
        {"series": "credit_oas"},
        {"series": "yield_spread_10y_2y"},
        {"series": None},
        {"limit": 0},
        {"limit": True},
        {"limit": 501},
        {"identifier": "AAPL"},
        {"as_of": "2020-01-01"},
        {"start_date": "2025-12-29"},
        {"start_date": "2025-12-29", "end_date": "2025-12-31"},
        {"series": "ea_aaa_10y_2y", "year": 2003},
        {"series": "au_10y_2y", "year": 2012},
        {"series": "us_10y_2y", "year": 1989},
    ],
)
def test_invalid_queries_are_rejected_before_source_access(bad, monkeypatch):
    monkeypatch.setattr(
        PublicFixtures, "get", lambda *args: pytest.fail("Invalid query fetched data")
    )
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(query() | bad)


def test_schema_catalog_templates_and_default_limit_match_runtime():
    schema = ResearchQuery.model_json_schema()
    variant = next(
        v for v in schema["oneOf"] if v["properties"]["dataset"]["const"] == "interest_spreads"
    )
    assert variant["properties"]["limit"]["default"] == 1
    assert set(variant["properties"]["series"]["enum"]) == set(EXPECTED)
    validator = Draft202012Validator(schema)
    for series in SPREADS:
        validator.validate(query(series))
    for series, year in [("ea_aaa_10y_2y", 2003), ("au_10y_2y", 2012)]:
        with pytest.raises(ValidationError):
            validator.validate(query(series, year=year))
    catalog = Atlas.research_catalog("interest_spreads")
    assert set(catalog["interest_spreads"]["series"]) == set(EXPECTED)
    assert set(catalog["datasets"][0]["source_ids"]) == {"treasury", "japan_mof", "ecb", "rba"}
    templates = Atlas.research_catalog("interest_spreads", search="金利スプレッド")
    assert templates["series_search"]["matched"] == 15


def test_compact_projection_and_saved_pages_retain_input_dates_units_and_evidence():
    data = query(response={"view": "compact", "fields": ["value"], "page_size": 1}, limit=3)
    with Atlas("fixture") as atlas:
        page = atlas.get_research(data)
        row = page["records"][0]
        assert row["spread_type"] == "yield_curve" and row["long_maturity"] == "10YEAR"
        assert row["unit"] == "basis_points" and row["currency"] == "USD"
        assert row["inputs"][0]["date"] == row["date"]
        source = atlas.get_evidence(row["inputs"][0]["source"]["evidence_id"])
        assert source["evidence"]["raw_sha256"]
        next_page = atlas.get_research(
            data | {"response": data["response"] | {"cursor": page["response"]["next_cursor"]}}
        )
    assert next_page["records"][0]["date"] == "2025-12-30"


def test_offline_list_never_constructs_an_application_or_fetches_sources(monkeypatch):
    monkeypatch.setattr(
        "atlas_terminal.cli.Atlas", lambda *args: pytest.fail("List created a client")
    )
    result = CliRunner().invoke(app, ["research", "spread", "--list"])
    assert result.exit_code == 0 and set(json.loads(result.stdout)["series"]) == set(EXPECTED)


def test_short_cli_human_json_and_http_share_the_same_sdk_result(tmp_path):
    runner = CliRunner()
    args = ["research", "spread", "us_10y_2y", "--year", "2025", "--provider", "fixture"]
    shown = runner.invoke(app, args)
    assert shown.exit_code == 0 and "SYNTHETIC" in shown.stdout
    assert "2025-12-31  30.00 bp" in shown.stdout
    encoded = runner.invoke(app, [*args, "--json"])
    assert encoded.exit_code == 0
    with Atlas("fixture") as atlas:
        expected = atlas.get_spread("us_10y_2y", year=2025)
    assert json.loads(encoded.stdout) == expected
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        result = client.post("/v1/research", json=query(limit=1))
    assert result.status_code == 200 and result.json() == expected


def test_panels_split_daily_spreads_by_year_and_retain_the_curve_definition():
    data = {
        "dataset": "macro_panel",
        "start_date": "2025-01-01",
        "end_date": "2026-12-31",
        "frequency": "annual",
        "items": [
            {
                "id": "slope",
                "query": {"dataset": "interest_spreads", "series": "us_10y_2y"},
                "aggregation": "last",
            }
        ],
    }
    plan = panel_queries(parse_research(data))
    assert len(plan[0]) == 2
    assert [q["limit"] for q in plan[0]] == [500, 500]
    assert [q["start_date"] for q in plan[0]] == ["2025-01-01", "2026-01-01"]
    with Atlas("fixture") as atlas:
        result = atlas.get_research(data)
    historical = next(row for row in result["records"] if row["period_end"] == "2025-12-31")[
        "records"
    ][0]
    assert Decimal(historical["value"]) == 30
    assert historical["direction"] == "long_minus_short"
    assert historical["curve"] == "nominal" and historical["unit"] == "basis_points"
