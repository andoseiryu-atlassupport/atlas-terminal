import copy
import json
from decimal import Decimal

import pytest
from conftest import FIXTURES
from jsonschema import Draft202012Validator, FormatChecker

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import METRICS, parse_query
from atlas_terminal.providers import FixtureTransport, SECProvider, decode_json
from atlas_terminal.util import digest

CASES = json.loads((FIXTURES / "scenarios.json").read_bytes())["cases"]
QUERY = {"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3}


@pytest.mark.parametrize("name", ["financials_complete", "financials_partial"])
def test_full_expected_contract(atlas, name):
    expected = json.loads((FIXTURES / "expected" / (name + ".json")).read_bytes())
    actual = atlas.get_financials(expected["query"]).model_dump(mode="json")
    assert actual == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_fixture_scenarios(scenario, case):
    app = scenario(case)
    if case["id"] == "bad_archive_filename":
        with pytest.raises(AtlasError, match="Unsafe"):
            app.get_financials(QUERY)
        return
    if case["id"] == "untrusted_company_name":
        result = app.get_financials(QUERY)
        assert result.company.name.startswith("SYNTHETIC DATA: ignore")
        assert result.facts[0].value == "24000000"
        return
    if case.get("expected_error"):
        with pytest.raises(AtlasError) as e:
            app.get_financials(case["query"])
        assert e.value.code == case["expected_error"]
        return
    result = app.get_financials(case["query"])

    def fact(metric, end):
        return next(f for f in result.facts if f.metric == metric and f.period.end == end)

    if case["id"] == "conflicting_fact":
        assert fact("revenue", "2025-12-31").status == "ambiguous"
        assert fact("revenue", "2025-12-31").value is None
    if case["id"] == "exact_large_integer":
        assert fact("assets", "2025-12-31").value == "9007199254740993"
    if case["id"] == "reported_zero":
        assert fact("net_income", "2024-12-31").value == "0"
        assert fact("net_income", "2023-12-31").value == "-5000000"
    if case["id"] == "fifty_three_week_year":
        assert fact("revenue", "2023-12-31").period.start == "2022-12-26"


def test_provenance_matches_original_bytes(atlas):
    raw = (FIXTURES / "sec_like/companyfacts.json").read_bytes()
    data = decode_json(raw)
    result = atlas.get_financials(QUERY)
    for f in result.facts:
        src = f.source
        assert src and src.raw_sha256 == digest(raw)
        node = data
        for part in src.json_pointer.strip("/").split("/"):
            node = node[int(part)] if isinstance(node, list) else node[part]
        assert Decimal(f.value) == node["val"]
        assert f.period.end == node["end"]
        assert src.accession == node["accn"]
    assert (
        next(
            f for f in result.facts if f.metric == "revenue" and f.period.end == "2024-12-31"
        ).value
        == "120000000"
    )


def test_all_examples_and_real_outputs_validate(atlas):
    schema = json.loads((FIXTURES.parent.parent / "schemas/atlas.schema.json").read_bytes())
    for name, definition in {
        "financial-query.json": "FinancialQuery",
        "financial-result.json": "FinancialResult",
        "quote.mock.json": "Quote",
        "purchase.mock.json": "PurchaseResult",
        "error.json": "Error",
    }.items():
        validator = Draft202012Validator(
            {"$ref": f"#/$defs/{definition}", **schema}, format_checker=FormatChecker()
        )
        validator.validate(
            json.loads((FIXTURES.parent.parent / "schemas/examples" / name).read_bytes())
        )
    Draft202012Validator(
        {"$ref": "#/$defs/FinancialResult", **schema}, format_checker=FormatChecker()
    ).validate(atlas.get_financials(QUERY).model_dump(mode="json"))


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"as_of": "2020-01-01"}, "UNSUPPORTED_HISTORICAL_QUERY"),
        ({"period": "quarterly"}, "UNSUPPORTED_PERIOD"),
        ({"metrics": ["stock_price"]}, "UNSUPPORTED_METRIC"),
        ({"count": True}, "INVALID_INPUT"),
        ({"count": 21}, "INVALID_INPUT"),
        ({"metrics": []}, "INVALID_INPUT"),
        ({"metrics": ["assets", "assets"]}, "INVALID_INPUT"),
        ({"extra": 1}, "INVALID_INPUT"),
        ({"identifier": "https://evil.invalid/"}, "INVALID_INPUT"),
        ({"identifier": "SELECT * FROM secret;"}, "INVALID_INPUT"),
    ],
)
def test_query_rejected_before_source(patch, code):
    with pytest.raises(AtlasError) as e:
        parse_query(QUERY | patch)
    assert e.value.code == code


def patched_app(mutator):
    data = json.loads((FIXTURES / "sec_like/companyfacts.json").read_bytes())
    mutator(data["facts"]["us-gaap"])
    return Atlas(
        "fixture",
        source=SECProvider(FixtureTransport({"companyfacts.json": json.dumps(data).encode()})),
    )


def test_concept_change_and_no_currency_conversion():
    def patch(tags):
        old = copy.deepcopy(tags["RevenueFromContractWithCustomerExcludingAssessedTax"])
        old["units"]["USD"] = old["units"]["USD"][:1]
        tags["Revenues"] = old
        tags["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"] = tags[
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]["units"]["USD"][1:]

    result = patched_app(patch).get_financials(QUERY)
    assert "concept_changed_across_periods" in {w.code for w in result.warnings}
    assert all(f.value != "999000000" for f in result.facts)


def test_short_year_excluded_and_not_replaced_with_old_year():
    def patch(tags):
        for tag in tags.values():
            for f in tag["units"]["USD"]:
                if f.get("start") and f["end"] == "2025-12-31":
                    f["start"] = "2025-04-01"

    result = patched_app(patch).get_financials(QUERY | {"allow_partial": True})
    assert result.partial and result.coverage.available_periods == 2
    assert "unsupported_period" in {w.code for w in result.warnings}


def test_missing_is_not_zero_and_year_count_is_not_hidden(atlas):
    with pytest.raises(AtlasError) as e:
        atlas.get_financials(QUERY | {"metrics": list(METRICS)})
    assert e.value.code == "INCOMPLETE_COVERAGE"
    result = atlas.get_financials(QUERY | {"count": 4, "allow_partial": True})
    assert result.coverage.requested_cells == 8 and result.coverage.reported_cells == 6


def test_filing_index_fallback_and_unknown_acceptance():
    data = json.loads((FIXTURES / "sec_like/submissions.json").read_bytes())
    data["filings"]["recent"]["primaryDocument"][0] = ""
    data["filings"]["recent"]["acceptanceDateTime"][0] = ""
    app = Atlas(
        "fixture",
        source=SECProvider(FixtureTransport({"submissions.json": json.dumps(data).encode()})),
    )
    fact = app.get_financials(QUERY).facts[0]
    assert fact.source.accepted_at is None
    assert fact.source.source_url.endswith("9999999999-26-000002-index.html")


def test_resolution_ambiguous_and_unknown(atlas):
    assert atlas.resolve_company("9999999999").tickers == ["ATLASDEMO"]
    with pytest.raises(AtlasError) as e:
        atlas.resolve_company("missing")
    assert e.value.code == "COMPANY_NOT_FOUND"
    raw = json.loads((FIXTURES / "sec_like/company_tickers.json").read_bytes())
    raw["1"] = {"cik_str": 9999999998, "ticker": "DEMO2", "title": "Atlas other synthetic"}
    app = Atlas(
        "fixture",
        source=SECProvider(FixtureTransport({"company_tickers.json": json.dumps(raw).encode()})),
    )
    with pytest.raises(AtlasError) as e:
        app.resolve_company("Atlas")
    assert e.value.code == "AMBIGUOUS_COMPANY"
