"""Employment Situation definitions and calculations using fictional source bytes only."""

import json
import subprocess
import sys
from decimal import Decimal, localcontext

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import BLS_BASE, BLS_SERIES, PublicFixtures, PublicTransport
from atlas_terminal.research_macro import monthly_analytics
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery
from atlas_terminal.server import create_app


def source_query(series, **kwargs):
    return {"dataset": "economics", "series": series, **kwargs}


def derived_query(series, **kwargs):
    return {"dataset": "macro_analytics", "series": series, **kwargs}


class EmploymentInputs(PublicFixtures):
    """Custom fictional wages preserve existing packaged fixture bytes and hashes."""

    def __init__(self, rows, *, code="CES0500000003"):
        self.rows = rows
        self.code = code
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        body = {
            "status": "REQUEST_SUCCEEDED",
            "message": ["SYNTHETIC TEST DATA"],
            "Results": {"series": [{"seriesID": self.code, "data": self.rows}]},
        }
        return Snapshot(
            json.dumps(body).encode(),
            "https://fixtures.atlas.invalid/employment",
            "2026-10-20T00:00:00Z",
        )


def wage_rows():
    return [
        {"year": "2026", "period": "M01", "value": "32.32"},
        {"year": "2025", "period": "M12", "value": "32.00"},
        {"year": "2025", "period": "M01", "value": "30.00"},
        {"year": "2025", "period": "M13", "value": "900.00"},
    ]


@pytest.mark.parametrize(
    "name,code,unit,survey,basis",
    [
        ("private_payrolls", "CES0500000001", "thousand_jobs", "CES", "payroll_jobs"),
        ("government_payrolls", "CES9000000001", "thousand_jobs", "CES", "payroll_jobs"),
        ("unemployment_u6", "LNS13327709", "percent", "CPS", "persons"),
        ("payrolls", "CES0000000001", "thousand_persons", "CES", "payroll_jobs"),
        ("unemployment", "LNS14000000", "percent", "CPS", "persons"),
    ],
)
def test_identity_survey_units_and_source_evidence_survive_projection(
    name, code, unit, survey, basis
):
    with Atlas("fixture") as atlas:
        full = atlas.get_research(source_query(code, limit=1))
        short = atlas.get_research(
            source_query(name, limit=1, response={"view": "compact", "fields": ["value"]})
        )["records"][0]
        row = full["records"][0]
        for value in (row, short):
            assert value["series_id"] == code and value["unit"] == unit
            assert value["survey"] == survey and value["count_basis"] == basis
            assert value["seasonally_adjusted"] and not value["annualized"]
            assert value["published_at"] is None and value["population"]
            assert value["base_period"] is None  # Employment levels/rates are not rebased indexes.
            assert value["reference_period_kind"] == (
                "pay_period_including_12th"
                if survey == "CES"
                else "reference_week_usually_including_12th"
            )
        if name == "unemployment_u6":
            assert short["denominator"] == "civilian_labor_force_plus_marginally_attached"
            assert short["measure"].endswith("U6")
        evidence = atlas.get_evidence(short["source"]["evidence_id"])["evidence"]
        original = PublicFixtures().get(BLS_BASE + code, 0)
        assert evidence == row["source"] and evidence["raw_sha256"] == original.sha256
        cell = original.data
        for part in evidence["json_pointer"].split("/")[1:]:
            cell = cell[int(part)] if isinstance(cell, list) else cell[part]
        assert row["value"] == cell["value"]
        assert full["is_synthetic"] and not full["coverage"]["point_in_time"]


@pytest.mark.parametrize(
    "suffix,expected,period,role",
    [
        ("mom", "0.01", "M12", "previous_month"),
        ("yoy", "0.07733333333333333333333333333", "M01", "prior_year"),
    ],
)
def test_wages_use_exact_calendar_lags_and_decimal_ratios(suffix, expected, period, role):
    with Atlas("fixture") as atlas, localcontext() as context:
        context.prec = 3
        inputs = EmploymentInputs(wage_rows())
        atlas._research.public = inputs
        result = atlas.get_research(
            derived_query(
                "hourly_earnings_" + suffix, start_date="2026-01-01", end_date="2026-01-31"
            )
        )
    row = result["records"][0]
    assert len(result["records"]) == 1 and row["value"] == expected
    assert row["unit"] == "ratio" and row["decimal_precision"] == 28
    assert row["rounding"] == "ROUND_HALF_EVEN" and row["survey"] == "CES"
    assert row["seasonally_adjusted"] and not row["annualized"]
    assert row["comparison_period"] == {"year": "2025", "period": period, "period_kind": "month"}
    assert row["formula"] == f"(current - {role}) / {role}"
    assert row["inputs"][1]["period"] == period and row["inputs"][1]["role"] == role
    assert all(x["series_id"] == "CES0500000003" for x in row["inputs"])
    assert all(x["unit"] == "USD_per_hour" and x["source"]["raw_sha256"] for x in row["inputs"])
    assert "startyear=2025" in inputs.calls[0]


@pytest.mark.parametrize("suffix,period", [("mom", "M12"), ("yoy", "M01")])
@pytest.mark.parametrize(
    "fault", ["missing", "null", "zero", "negative", "wrong_series", "duplicate"]
)
def test_wage_missing_or_invalid_baselines_are_not_replaced(suffix, period, fault):
    rows = wage_rows()
    prior = next(r for r in rows if r["year"] == "2025" and r["period"] == period)
    code = "CES0500000003"
    if fault == "missing":
        rows.remove(prior)
    elif fault == "wrong_series":
        code = "CES0500000008"  # Production/nonsupervisory employees are a different population.
    elif fault == "duplicate":
        rows.append({**prior, "value": "99.00"})
    else:
        prior["value"] = {"null": "-", "zero": "0", "negative": "-1"}[fault]
    with Atlas("fixture") as atlas:
        atlas._research.public = EmploymentInputs(rows, code=code)
        q = derived_query("hourly_earnings_" + suffix, limit=1)
        if fault in {"wrong_series", "duplicate"}:
            with pytest.raises(AtlasError) as error:
                atlas.get_research(q)
            assert error.value.code == "SOURCE_UNAVAILABLE"
        else:
            row = atlas.get_research(q)["records"][0]
            assert row["value"] is None and row["status"] == "unavailable" and row["reason"]


@pytest.mark.parametrize("field,value", [("unit", "USD_per_week"), ("seasonally_adjusted", False)])
def test_wage_incompatible_units_or_adjustment_are_not_compared(field, value):
    with Atlas("fixture") as atlas:
        atlas._research.public = EmploymentInputs(wage_rows())
        raw = atlas.get_research(source_query("hourly_earnings"))
    previous = next(r for r in raw["records"] if r["period"] == "M12")
    previous[field] = value
    result = monthly_analytics(ResearchQuery(**derived_query("hourly_earnings_mom", limit=1)), raw)
    assert result["records"][0]["value"] is None
    assert result["records"][0]["reason"] == "Input units or seasonal adjustment differ."


@pytest.mark.parametrize(
    "source,unit",
    [
        ("private_payrolls", "thousand_jobs"),
        ("government_payrolls", "thousand_jobs"),
        ("manufacturing_payrolls", "thousand_persons"),
    ],
)
def test_sector_payroll_changes_are_absolute_allow_declines_and_keep_zero_levels(source, unit):
    rows = [
        {"year": "2026", "period": "M01", "value": "0"},
        {"year": "2025", "period": "M12", "value": "12.345"},
        {"year": "2025", "period": "M10", "value": "999"},
    ]
    with Atlas("fixture") as atlas:
        atlas._research.public = EmploymentInputs(rows, code=BLS_SERIES[source][0])
        result = atlas.get_research(derived_query(source + "_change_mom"))
        row = result["records"][0]
        assert row["value"] == "-12.345" and row["unit"] == unit
        assert row["formula"] == "current - previous_month" and row["count_basis"] == "payroll_jobs"
        assert row["decimal_precision"] is None and result["records"][1]["value"] is None
        rows[0]["value"] = "-1"
        assert (
            atlas.get_research(derived_query(source + "_change_mom"))["records"][0]["value"] is None
        )


def test_discovery_and_preset_preview_are_offline_and_schema_valid(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery must not create an upstream client")

    monkeypatch.setattr(PublicTransport, "__init__", forbidden)
    for term, expected in [
        ("平均時給", "hourly_earnings"),
        ("広義失業率", "unemployment_u6"),
        ("民間雇用", "private_payrolls"),
    ]:
        rows = Atlas.research_catalog(search=term, country="US")["series_search"]["records"]
        assert expected in {r["series"] for r in rows}
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    for name in ("private_payrolls", "government_payrolls", "unemployment_u6"):
        validator.validate(source_query(name))
        validator.validate(source_query(BLS_SERIES[name][0]))
    q = {"preset": "us_employment_overview", "preview": True}
    Draft202012Validator(ResearchPresetQuery.model_json_schema()).validate(q)
    with Atlas("public") as atlas:
        result = atlas.get_research_preset(q)
        assert len(result["plan"]["items"]) == 10 and result["batch"] is None
        assert atlas._research.public is None


@pytest.mark.parametrize("name", ["private_payrolls", "government_payrolls", "unemployment_u6"])
@pytest.mark.parametrize("dated", [False, True])
def test_http_transport_identity_and_cache_with_mock_network(tmp_path, name, dated):
    calls = []
    code = BLS_SERIES[name][0]

    def handler(request):
        calls.append(request)
        if dated:
            assert request.method == "POST" and str(request.url) == BLS_BASE
            assert json.loads(request.content) == {
                "seriesid": [code],
                "startyear": "2025",
                "endyear": "2026",
            }
        else:
            assert request.method == "GET" and str(request.url) == BLS_BASE + code
        return httpx.Response(200, content=PublicFixtures().get(BLS_BASE + code, 0).body)

    dates = {"start_date": "2025-01-01", "end_date": "2026-12-31"} if dated else {}
    with Atlas("public") as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        one = atlas.get_research(source_query(name, **dates))
        two = atlas.get_research(source_query(name, **dates))
    assert len(calls) == 1 and one["records"] == two["records"]


def test_sdk_cli_and_rest_share_employment_results():
    with Atlas("fixture") as atlas, TestClient(create_app(provider="fixture")) as http:
        for q in [
            source_query("unemployment_u6", limit=1),
            derived_query("hourly_earnings_mom", limit=1),
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
        preset = {"preset": "us_employment_overview"}
        expected = atlas.get_research_preset(preset)
        response = http.post("/v1/research/preset", json=preset)
        assert response.status_code == 200
        assert [x["result"]["records"] for x in response.json()["batch"]["items"]] == [
            x["result"]["records"] for x in expected["batch"]["items"]
        ]
        assert len(expected["overview"]) == 10
        # Packaged legacy wages have no prior-year monthly value: do not use their M13 row.
        wage_yoy = next(x for x in expected["batch"]["items"] if x["id"] == "hourly_earnings_yoy")
        assert wage_yoy["result"]["records"][0]["value"] is None


@pytest.mark.asyncio
async def test_official_stdio_mcp_delivers_employment_and_resolves_inputs(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path / "mcp")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool(
            "atlas_get_research_preset", {"query": {"preset": "us_employment_overview"}}
        )
        assert not result.is_error and len(result.structured_content["overview"]) == 10
        wage = next(
            x
            for x in result.structured_content["batch"]["items"]
            if x["id"] == "hourly_earnings_mom"
        )["result"]["records"][0]
        assert wage["survey"] == "CES" and Decimal(wage["value"]) > 0
        source = wage["inputs"][0]["source"]
        evidence = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": source["evidence_id"]}
        )
        assert not evidence.is_error
        assert (
            evidence.structured_content["evidence"]["raw_sha256"]
            == PublicFixtures().get(BLS_BASE + "CES0500000003", 0).sha256
        )
