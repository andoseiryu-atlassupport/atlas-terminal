"""Fictional dated rates verify history, source boundaries and calendar aggregation."""

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from atlas_terminal.cli import app
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import decode_json
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.reference_rate_sources import NYFED_RATES, nyfed_identity, nyfed_url
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_panel import panel_queries
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest


def row(day, value="1.250", series="EFFR"):
    return {"effectiveDate": day, "type": series, "percentRate": value, "revisionIndicator": ""}


def public_atlas(tmp_path, responses):
    calls, now = [], [1000.0]

    def serve(request):
        url = str(request.url)
        calls.append(url)
        assert url in responses
        return httpx.Response(200, content=canonical({"refRates": responses[url]}))

    transport = PublicTransport(
        cache_path=tmp_path / "public.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(serve)),
        clock=lambda: now[0],
        sleep=lambda wait: now.__setitem__(0, now[0] + wait),
    )
    atlas = Atlas("public", directory=tmp_path)
    atlas._research.public = transport
    return atlas, calls


def test_dated_rates_cross_years_preserve_original_cells_nulls_and_cached_versions(tmp_path):
    first = nyfed_url("effr", "2000-12-29", "2000-12-31")
    second = nyfed_url("effr", "2001-01-01", "2001-01-03")
    responses = {
        first: [row("2000-12-29", "2.500")],
        second: [row("2001-01-03", None), row("2001-01-02", "0.000")],
    }
    atlas, calls = public_atlas(tmp_path, responses)
    query = {
        "dataset": "reference_rates",
        "series": "effr",
        "start_date": "2000-12-29",
        "end_date": "2001-01-03",
        "limit": 500,
    }
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(query)
    with atlas:
        result = atlas.get_research(query)
        repeated = atlas.get_research(query)
        compact = atlas.get_research(query | {"response": {"view": "compact"}})
        assert repeated["records"] == result["records"]
        assert calls == [first, second] and len(result["sources"]) == 2
        assert [r["value"] for r in result["records"]] == [None, "0.000", "2.500"]
        assert result["coverage"]["first_observation_date"] == "2000-12-29"
        assert result["coverage"]["last_observation_date"] == "2001-01-03"
        assert not result["coverage"]["truncated"]
        for item in compact["records"]:
            evidence = atlas.get_evidence(item["source"]["evidence_id"])["evidence"]
            body = canonical({"refRates": responses[evidence["raw_source_url"]]})
            raw = decode_json(body)
            for token in evidence["json_pointer"].split("/")[1:]:
                raw = raw[int(token)] if isinstance(raw, list) else raw[token]
            assert raw == item["value"] and digest(body) == evidence["raw_sha256"]
        assert all(r["unit"] == "percent_per_annum" for r in result["records"])
        assert result["attributions"][0]["source_id"] == "new_york_fed"


@pytest.mark.parametrize("count", [91, 366])
def test_annual_history_can_exceed_the_legacy_ninety_observations(tmp_path, count):
    url = nyfed_url("effr", "2000-01-01", "2000-12-31")
    rows = [row((date(2000, 1, 1) + timedelta(days=i)).isoformat()) for i in range(count)]
    atlas, _ = public_atlas(tmp_path, {url: rows})
    with atlas:
        result = atlas.get_research(
            {
                "dataset": "reference_rates",
                "series": "effr",
                "start_date": "2000-01-01",
                "end_date": "2000-12-31",
                "limit": 100,
            }
        )
    assert result["coverage"]["observed_records"] == count
    assert result["coverage"]["truncated"] == (count > 100)
    assert len(result["records"]) == min(count, 100)


@pytest.mark.parametrize(
    "rows",
    [
        [row("1999-12-31")],
        [row("2001-01-01")],
        [row("2000-01-03"), row("2000-01-03")],
        [row("2000-01-03", series="SOFR")],
        [row("2000-01-03", True)],
        [row("2000-01-03", "NaN")],
        [row("2000-01-03")] * 367,
    ],
)
def test_historical_identity_bounds_and_duplicates_fail_closed(tmp_path, rows):
    url = nyfed_url("effr", "2000-01-01", "2000-12-31")
    atlas, _ = public_atlas(tmp_path, {url: rows})
    with atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(
            {
                "dataset": "reference_rates",
                "series": "effr",
                "start_date": "2000-01-01",
                "end_date": "2000-12-31",
            }
        )
    assert exc.value.code == "SOURCE_UNAVAILABLE"


def test_precoverage_empty_history_is_explicit_and_never_falls_back_to_latest(tmp_path):
    url = nyfed_url("sofr", "2000-01-01", "2000-12-31")
    atlas, calls = public_atlas(tmp_path, {url: []})
    with atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(
            {
                "dataset": "reference_rates",
                "series": "sofr",
                "start_date": "2000-01-01",
                "end_date": "2000-12-31",
            }
        )
    assert exc.value.code == "INCOMPLETE_COVERAGE" and calls == [url]


@pytest.mark.parametrize(
    "url",
    [
        "http://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2000-12-31",
        "https://markets.newyorkfed.org.evil/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2000-12-31",
        "https://markets.newyorkfed.org/api/rates/secured/effr/search.json?startDate=2000-01-01&endDate=2000-12-31",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2001-12-31",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2000-02-30",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2000-12-31#fragment",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&endDate=2000-12-31&key=private",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2000-01-01&startDate=2000-02-01&endDate=2000-12-31",
    ],
)
def test_historical_urls_remain_exact_and_bounded(url):
    assert nyfed_identity(url) is None
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(url)


def test_rate_panels_pass_dates_to_each_year_and_aggregate_actual_old_observations(tmp_path):
    query = {
        "dataset": "macro_panel",
        "start_date": "2000-01-01",
        "end_date": "2001-12-31",
        "frequency": "annual",
        "items": [
            {
                "id": "rate",
                "query": {"dataset": "reference_rates", "series": "effr"},
                "aggregation": "mean",
            }
        ],
    }
    plan = panel_queries(parse_research(query))[0]
    assert [(p["start_date"], p["end_date"]) for p in plan] == [
        ("2000-01-01", "2000-12-31"),
        ("2001-01-01", "2001-12-31"),
    ]
    urls = [nyfed_url("effr", p["start_date"], p["end_date"]) for p in plan]
    atlas, calls = public_atlas(
        tmp_path,
        {
            urls[0]: [row("2000-01-03", "2"), row("2000-01-04", "4")],
            urls[1]: [row("2001-01-02", "0"), row("2001-01-03", "1")],
        },
    )
    with atlas:
        result = atlas.get_research(query)
    values = [Decimal(period["records"][0]["value"]) for period in result["records"]]
    assert values == [Decimal("3"), Decimal("0.5")] and calls == urls
    assert NYFED_RATES["effr"] not in calls


def test_dated_fixture_queries_agree_through_cli_sdk_and_http():
    query = {
        "dataset": "reference_rates",
        "series": "effr",
        "start_date": "2026-10-16",
        "end_date": "2026-10-19",
        "limit": 500,
    }
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query)
    cli = CliRunner().invoke(
        app,
        [
            "research",
            "get",
            "reference_rates",
            "--series",
            "effr",
            "--start-date",
            "2026-10-16",
            "--end-date",
            "2026-10-19",
            "--limit",
            "500",
            "--provider",
            "fixture",
            "--json",
        ],
    )
    assert cli.exit_code == 0
    assert decode_json(cli.stdout.encode()) == expected
    with TestClient(create_app(provider="fixture")) as client:
        response = client.post("/v1/research", json=query)
    assert response.status_code == 200 and response.json() == expected
