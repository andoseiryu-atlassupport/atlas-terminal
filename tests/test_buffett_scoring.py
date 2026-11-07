"""Fictional scorecards verify financial formulas, missingness and shared interfaces."""

import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal, getcontext, localcontext
from fractions import Fraction
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.buffett_catalog import RULES
from atlas_terminal.buffett_models import ScoreFinancials, ScoreValuation
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SECProvider
from atlas_terminal.research import ResearchSecFixtures
from atlas_terminal.research_buffett import calculate_score, supplied_financials
from atlas_terminal.research_financials import STATEMENT_METRICS
from atlas_terminal.research_views import SourceEvidence
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/buffett"


def complete_sec_inputs():
    submission = json.loads(
        files("atlas_terminal").joinpath("resources/research/submissions.json").read_bytes()
    )
    submission["filings"] = {
        "recent": {
            key: []
            for key in (
                "accessionNumber",
                "filingDate",
                "reportDate",
                "form",
                "primaryDocument",
            )
        },
        "files": [],
    }
    taxonomy = {}
    for row in financials()["annual"]:
        accession = f"9999999999-{int(row['end'][:4]) + 1 - 2000:02}-000001"
        recent = submission["filings"]["recent"]
        for key, value in {
            "accessionNumber": accession,
            "filingDate": row["available_on"],
            "reportDate": row["end"],
            "form": "10-K",
            "primaryDocument": "fictional.htm",
        }.items():
            recent[key].append(value)
        for metric, value in row["values"].items():
            spec = STATEMENT_METRICS[metric]
            fact = {
                "end": row["end"],
                "val": int(value),
                "accn": accession,
                "form": "10-K",
                "filed": row["available_on"],
            }
            if not spec.instant:
                fact["start"] = row["start"]
            taxonomy.setdefault(spec.tags[0], {"units": {spec.unit: []}})["units"][
                spec.unit
            ].append(fact)
    return {"cik": "9999999999", "facts": {"us-gaap": taxonomy}}, submission


def sec_score(facts, submissions):
    provider = SECProvider(
        ResearchSecFixtures(
            {
                "companyfacts.json": json.dumps(facts).encode(),
                "submissions.json": json.dumps(submissions).encode(),
            }
        )
    )
    with Atlas("fixture", research_source=provider) as atlas:
        return atlas.get_buffett_score("ATLASDEMO", valuation=valuation())


def test_complete_primary_sec_path_matches_the_declared_engine():
    facts, submissions = complete_sec_inputs()
    result = sec_score(facts, submissions)
    assert result["records"][0]["score"] == "100.000000000000000000"
    assert result["records"][0]["quality_score"] == scored()["quality_score"]
    assert result["input_origin"] == "sec_primary_annual_facts"
    for cell in result["records"][0]["reported_inputs"].values():
        assert cell["source"]["raw_sha256"] == digest(json.dumps(facts).encode())
        assert cell["source"]["accession"]


def test_returned_rules_cannot_mutate_the_versioned_model():
    with Atlas("fixture") as atlas:
        first = atlas.get_research(query(quote=valuation()))
        first["scoring_policy"]["rules"]["balance_sheet"]["weight"] = 99
        first["records"][0]["components"][0]["rule"]["weight"] = 99
        second = atlas.get_research(query(quote=valuation()))
    assert second["records"][0]["score"] == "100.000000000000000000"
    assert second["scoring_policy"]["rules"]["balance_sheet"]["weight"] == 10


@pytest.mark.parametrize("sic", ["6021", "6798", "", "invalid", "0000"])
def test_automatic_sic_gates_financial_and_unknown_businesses(sic):
    facts, submissions = complete_sec_inputs()
    submissions["sic"] = sic
    row = sec_score(facts, submissions)["records"][0]
    assert row["score"] is None and row["available_weight"] == "0"


def test_changed_primary_concepts_are_not_combined_into_one_stable_series():
    facts, submissions = complete_sec_inputs()
    old_tag, other_tag = STATEMENT_METRICS["revenue"].tags[:2]
    units = facts["facts"]["us-gaap"][old_tag]["units"]["USD"]
    selected = units.pop(3)
    facts["facts"]["us-gaap"][other_tag] = {"units": {"USD": [selected]}}
    row = sec_score(facts, submissions)["records"][0]
    assert component(row, "operating_margin")["points"] is None
    assert "incompatible_reported_concepts" in component(row, "operating_margin")["reason"]


def test_mismatched_primary_duration_is_not_used_in_cash_flow():
    facts, submissions = complete_sec_inputs()
    tag = STATEMENT_METRICS["operating_cash_flow"].tags[0]
    facts["facts"]["us-gaap"][tag]["units"]["USD"][-1]["start"] = "2025-01-02"
    row = sec_score(facts, submissions)["records"][0]
    assert component(row, "fcf_consistency")["points"] is None
    assert row["reported_inputs"]["2025-12-31:operating_cash_flow"]["status"] == "ambiguous"


def test_shared_engine_rejects_currency_mismatch_and_does_not_mutate_inputs():
    data = supplied_financials(ScoreFinancials.model_validate(financials()))
    key = "2020-12-31:stockholders_equity"
    data["inputs"].pop(key)
    before = deepcopy(data)
    row = calculate_score(data, 5, None)
    assert data == before and key in row["reported_inputs"]
    assert component(row, "sustained_roe")["points"] is None
    data["inputs"]["2025-12-31:assets"]["unit"] = "JPY"
    assert component(calculate_score(data, 5, None), "balance_sheet")["points"] is None


def test_score_projection_preserves_input_table_and_component_rules():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(quote=valuation()) | {"response": {"fields": ["score"]}})
    row = result["records"][0]
    assert row["score"] == "100.000000000000000000"
    assert row["components"] and row["reported_inputs"] and row["valuation"]
    assert row["model_version"] and row["manual_review"]


def financials():
    return json.loads((EXAMPLES / "financials.synthetic.json").read_text())


def valuation():
    return json.loads((EXAMPLES / "valuation.synthetic.json").read_text())


def query(data=None, quote=None):
    data = financials() if data is None else data
    return {
        "dataset": "buffett_score",
        "identifier": data["identifier"],
        "count": 5,
        "financial_input": data,
        **({"valuation": quote} if quote is not None else {}),
    }


def scored(data=None, quote=None):
    with Atlas("fixture") as atlas:
        return atlas.get_research(query(data, quote))["records"][0]


def component(row, metric):
    return next(item for item in row["components"] if item["metric"] == metric)


def test_complete_fixed_weights_and_independent_exact_dcf():
    row = scored(quote=valuation())
    assert sum(rule["weight"] for rule in RULES.values()) == 100
    assert row["score"] == "100.000000000000000000"
    assert row["quality_score"] == "80.000000000000000000"
    assert row["score_bounds"] == {"lower": row["score"], "upper": row["score"]}
    detail = row["valuation"]
    assert detail["base_cash_flow"] == "260.000000000000000000"
    base, g, r, terminal = map(Fraction, ["260", ".03", ".10", ".02"])
    expected = sum(base * (1 + g) ** year / (1 + r) ** year for year in range(1, 11))
    expected += base * (1 + g) ** 10 * (1 + terminal) / (r - terminal) / (1 + r) ** 10
    with localcontext() as context:
        context.prec = 100
        expected_decimal = Decimal(expected.numerator) / Decimal(expected.denominator)
        actual = Decimal(detail["scenarios"][0]["scenario_equity_value"])
        assert abs(actual - expected_decimal) <= Decimal("5e-19")
    assert detail["owner_earnings"] is False and detail["net_debt_adjustment"] is False
    assert row["manual_review"] == ["circle_of_competence", "management_integrity", "durable_moat"]


def test_missing_valuation_preserves_quality_without_reweighting():
    row = scored()
    assert row["score"] is None and row["quality_score"] == "80.000000000000000000"
    assert row["available_weight"] == "80"
    assert row["score_bounds"] == {
        "lower": "80.000000000000000000",
        "upper": "100.000000000000000000",
    }
    assert component(row, "margin_of_safety")["points"] is None


@pytest.mark.parametrize(
    "metric,affected",
    [
        ("operating_cash_flow", "fcf_consistency"),
        ("net_income", "earnings_consistency"),
        ("operating_income", "operating_margin"),
        ("capital_expenditure", "capital_intensity"),
        ("stock_based_compensation", "stock_compensation"),
    ],
)
def test_missing_middle_year_is_not_ignored_or_filled(metric, affected):
    data = financials()
    data["annual"][3]["values"].pop(metric)
    row = scored(data, valuation())
    assert component(row, affected)["points"] is None and row["score"] is None
    assert Decimal(row["score_bounds"]["upper"]) > Decimal(row["known_points"])
    assert all(
        item["points"] is None or Decimal(item["points"]) <= Decimal(item["max_points"])
        for item in row["components"]
    )


def test_missing_opening_equity_does_not_use_closing_equity_in_roe():
    data = financials()
    data["annual"][0]["values"].pop("stockholders_equity")
    row = scored(data)
    assert component(row, "sustained_roe")["points"] is None
    assert "2020-12-31:stockholders_equity" in component(row, "sustained_roe")["input_ids"]


def test_roe_uses_average_opening_closing_equity():
    data = financials()
    for row in data["annual"]:
        row["values"]["stockholders_equity"] = "2200"
    row = scored(data)
    assert component(row, "sustained_roe")["value"] == "0.100000000000000000"
    assert component(row, "sustained_roe")["points"] == "5.000000000000000000"


def test_negative_equity_and_negative_earnings_do_not_create_good_roe():
    data = financials()
    for year in data["annual"]:
        year["values"]["stockholders_equity"] = "-1100"
        year["values"]["net_income"] = "-220"
    row = scored(data, valuation())
    assert component(row, "sustained_roe")["points"] == "0.000000000000000000"
    assert component(row, "earnings_consistency")["value"] == "0.000000000000000000"
    assert component(row, "cash_conversion")["reason"] == "nonpositive_aggregate_earnings"


def test_zero_interest_is_reported_and_not_infinity_or_missing():
    data = financials()
    data["annual"][-1]["values"]["interest_expense"] = "0"
    row = scored(data)
    assert component(row, "interest_coverage")["points"] == "5.000000000000000000"
    assert component(row, "interest_coverage")["reason"] == "zero_reported_interest_expense"
    data["annual"][-1]["values"]["operating_income"] = "-1"
    assert component(scored(data), "interest_coverage")["points"] == "0.000000000000000000"


@pytest.mark.parametrize(
    "metric",
    [
        "capital_expenditure",
        "interest_expense",
        "stock_based_compensation",
        "assets",
        "liabilities",
    ],
)
def test_invalid_negative_balance_or_expense_is_not_favorable(metric):
    data = financials()
    data["annual"][-1]["values"][metric] = "-1"
    row = scored(data, valuation())
    assert row["score"] is None
    assert any("invalid_negative_input" in (item["reason"] or "") for item in row["components"])


@pytest.mark.parametrize("business", ["financial", "unspecified"])
def test_general_company_rules_do_not_score_banks_or_unclassified_businesses(business):
    data = financials()
    data["business_type"] = business
    row = scored(data, valuation())
    assert row["score"] is None and row["available_weight"] == "0"
    assert all(item["points"] is None for item in row["components"])


def test_nonconsecutive_years_do_not_become_continuous_history():
    data = financials()
    data["annual"].pop(3)
    row = scored(data)
    assert component(row, "earnings_consistency")["points"] is None
    assert component(row, "balance_sheet")["points"] is not None


def test_sorting_supplied_periods_does_not_change_the_score():
    data = financials()
    expected = scored(data, valuation())
    data["annual"].reverse()
    actual = scored(data, valuation())
    assert [row["points"] for row in actual["components"]] == [
        row["points"] for row in expected["components"]
    ]
    assert actual["score"] == expected["score"]


def test_declared_yen_inputs_are_scale_invariant_without_currency_conversion():
    data, quote = financials(), valuation()
    expected = scored(data, quote)
    data["identifier"] = "7203.T"
    data["currency"] = "JPY"
    quote["currency"] = "JPY"
    for year in data["annual"]:
        for key, value in year["values"].items():
            year["values"][key] = str(Decimal(value) * 1000)
    quote["market_cap"] = str(Decimal(quote["market_cap"]) * 1000)
    actual = scored(data, quote)
    assert actual["score"] == expected["score"] and actual["currency"] == "JPY"
    assert [row["points"] for row in actual["components"]] == [
        row["points"] for row in expected["components"]
    ]


def test_valuation_requires_same_currency_and_after_financial_availability():
    quote = valuation()
    quote["currency"] = "JPY"
    with pytest.raises(AtlasError, match="currency/date"):
        scored(quote=quote)
    quote = valuation()
    quote["observed_on"] = "2025-12-31"
    with pytest.raises(AtlasError, match="currency/date"):
        scored(quote=quote)


@pytest.mark.parametrize(
    "field,value",
    [
        ("discount_rate", "0.02"),
        ("growth_rate", "0.5"),
        ("market_cap", "0"),
        ("terminal_growth_rate", "0.10"),
        ("projection_years", 0),
        ("market_cap", 1000),
        ("growth_rate", "NaN"),
        ("growth_rate", "1e-2"),
        ("captured_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_invalid_valuation_inputs_are_rejected(field, value):
    quote = valuation()
    quote[field] = value
    with pytest.raises(ValueError):
        ScoreValuation.model_validate(quote)


@pytest.mark.parametrize(
    "change", ["duplicate", "overlap", "quarter", "future", "url", "numeric", "unknown_metric"]
)
def test_invalid_annual_inputs_are_rejected(change):
    data = financials()
    if change == "duplicate":
        data["annual"].append(deepcopy(data["annual"][0]))
    elif change == "overlap":
        data["annual"][1]["start"] = "2020-12-30"
    elif change == "quarter":
        data["annual"][0]["start"] = "2020-10-01"
    elif change == "future":
        data["captured_at"] = "2099-01-01T00:00:00Z"
    elif change == "url":
        data["original_source_url"] = "https://name:secret@example.invalid/"
    elif change == "numeric":
        data["annual"][0]["values"]["revenue"] = 1000.0
    else:
        data["annual"][0]["values"]["invented_metric"] = "10"
    with pytest.raises(ValueError):
        ScoreFinancials.model_validate(data)


def test_expensive_price_reduces_only_valuation_and_scenarios_remain_explicit():
    cheap = scored(quote=valuation())
    quote = valuation()
    quote["market_cap"] = "100000"
    expensive = scored(quote=quote)
    assert expensive["quality_score"] == cheap["quality_score"]
    assert expensive["score"] == "80.000000000000000000"
    scenarios = cheap["valuation"]["scenarios"]
    assert (
        Decimal(scenarios[1]["scenario_equity_value"])
        < Decimal(scenarios[0]["scenario_equity_value"])
        < Decimal(scenarios[2]["scenario_equity_value"])
    )


def test_nonpositive_cash_flow_proxy_does_not_generate_positive_intrinsic_value():
    data = financials()
    for year in data["annual"]:
        year["values"]["operating_cash_flow"] = "20"
    row = scored(data, valuation())
    assert row["valuation"]["scenarios"] == []
    assert component(row, "margin_of_safety")["points"] == "0.000000000000000000"


def test_every_supplied_source_pointer_hash_and_component_input_resolves():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(quote=valuation()))
    row = result["records"][0]
    documents = {
        source["raw_sha256"]: result["supplied_documents"][key]
        for source, key in zip(result["sources"], ["financial_input", "valuation"], strict=True)
    }
    for fact in row["reported_inputs"].values():
        source = fact["source"]
        SourceEvidence.model_validate(source)
        document = documents[source["raw_sha256"]]
        assert digest(canonical(document)) == source["raw_sha256"]
        value = document
        for token in source["json_pointer"].split("/")[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
        assert value == fact["value"]
    for item in row["components"]:
        assert all(key in row["reported_inputs"] for key in item["input_ids"])
    assert row["valuation"]["market_cap_source"]["json_pointer"] == "/market_cap"


def test_operator_data_needs_no_sec_or_yahoo_and_preserves_synthetic_flag():
    with Atlas("public") as atlas:
        atlas._research.sec_factory = lambda: pytest.fail("Operator input must not contact SEC")
        result = atlas.get_research(query(quote=valuation()))
    assert result["is_synthetic"] and result["input_origin"] == "operator_supplied_unverified"
    assert result["source_id"] == "multiple"


def test_global_decimal_context_cannot_change_scores():
    expected = scored(quote=valuation())
    with localcontext() as context:
        context.prec = 6
        assert getcontext().prec == 6
        actual = scored(quote=valuation())
    assert actual == expected


def test_compact_evidence_quality_requirements_and_strict_query_limits():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(quote=valuation()) | {"response": {"view": "compact"}})
        source = next(iter(result["records"][0]["reported_inputs"].values()))["source"]
        assert atlas.get_evidence(source["evidence_id"])["evidence"]["json_pointer"].startswith(
            "/annual/"
        )
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query() | {"requirements": {"allow_missing": False}})
        assert error.value.code == "QUALITY_NOT_MET"
        for extra in (
            {"count": 2},
            {"count": 11},
            {"period": "quarterly"},
            {"as_of": "2020-01-01"},
            {"identifier": "WRONG"},
        ):
            with pytest.raises(AtlasError):
                atlas.get_research(query() | extra)


def test_sec_reader_keeps_primary_filing_sources_and_reports_missing_history():
    with Atlas("fixture") as atlas:
        result = atlas.get_buffett_score("ATLASDEMO")
    row = result["records"][0]
    assert result["input_origin"] == "sec_primary_annual_facts"
    assert result["coverage"]["requested_years"] == 5
    assert row["score"] is None and row["quality_score"] is None
    fact = row["reported_inputs"]["2025-12-31:net_income"]
    assert fact["status"] == "reported" and fact["source"]["accession"]
    assert fact["source"]["json_pointer"].startswith("/facts/us-gaap/")


def test_cli_sdk_http_identical_from_declared_inputs(tmp_path):
    q = query(quote=valuation())
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(q)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "buffett",
            "BUFFETTDEMO",
            "--input",
            str(EXAMPLES / "financials.synthetic.json"),
            "--valuation-input",
            str(EXAMPLES / "valuation.synthetic.json"),
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(proc.stdout) == expected and not proc.stderr
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        response = client.post("/v1/research", json=q)
        assert response.status_code == 200 and response.json() == expected


async def test_stdio_mcp_shares_the_declared_input_engine(tmp_path):
    q = query(quote=valuation())
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(q)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool("atlas_get_research", {"query": q})
        assert not result.is_error and result.structured_content == expected
