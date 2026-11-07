"""Fictional filing periods expose absent quarters without creating financial values."""

import json
from copy import deepcopy
from importlib.resources import files

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SECProvider
from atlas_terminal.research import ResearchSecFixtures


def source_facts():
    return json.loads(
        files("atlas_terminal").joinpath("resources/research/companyfacts.json").read_bytes()
    )


def application(facts):
    return Atlas(
        "fixture",
        research_source=SECProvider(
            ResearchSecFixtures({"companyfacts.json": json.dumps(facts).encode()})
        ),
    )


def request(**extra):
    return {
        "dataset": "statements",
        "identifier": "ATLASDEMO",
        "metrics": ["revenue"],
        "count": 1,
        "allow_partial": True,
        **extra,
    }


def test_unanchored_q4_is_explained_without_changing_reported_periods_or_values():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(request(period="quarterly", count=3))
    assert result["period_ends"] == ["2026-06-30", "2026-03-31", "2025-09-30"]
    omitted = result["coverage"]["omitted_filing_periods"]
    assert len(omitted) == 1 and omitted[0]["period_end"] == "2025-12-31"
    assert omitted[0]["reason_code"] == "NO_REPORTED_DISCRETE_QUARTER_ANCHOR"
    assert "10-K" in omitted[0]["forms"]
    assert not result["coverage"]["partial"]
    assert "not_consecutive_calendar_periods" in result["coverage"]["period_selection_basis"]


def test_q4_revenue_missing_with_another_explicit_quarter_anchor_is_not_subtracted():
    facts = source_facts()
    eps = facts["facts"]["us-gaap"]["EarningsPerShareBasic"]["units"]["USD/shares"]
    primary = next(r for r in eps if r["end"] == "2025-12-31" and r["form"] == "10-K")
    eps.append(dict(primary, start="2025-10-01", val=0.5))
    with application(facts) as atlas:
        result = atlas.get_research(request(period="quarterly", count=3))
    q4 = next(r for r in result["records"] if r["period"]["end"] == "2025-12-31")
    assert q4["value"] is None and q4["reason_code"] == "NO_PRIMARY_DISCRETE_QUARTER"
    assert "no discrete quarter derived" in q4["reason"]
    assert not result["coverage"]["omitted_filing_periods"]
    assert result["coverage"]["partial"]


def test_latest_unanchored_filing_is_visible_even_when_a_prior_period_satisfies_count():
    facts = source_facts()
    for concept in facts["facts"]["us-gaap"].values():
        for rows in concept.get("units", {}).values():
            rows[:] = [r for r in rows if not (r.get("start") and r["end"] == "2026-06-30")]
    with application(facts) as atlas:
        result = atlas.get_research(request(period="quarterly"))
    assert result["period_ends"] == ["2026-03-31"]
    assert result["coverage"]["omitted_filing_periods"][0]["period_end"] == "2026-06-30"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("concept", "STANDARD_CONCEPT_UNAVAILABLE"),
        ("unit", "SUPPORTED_UNIT_UNAVAILABLE"),
        ("metadata", "NO_ELIGIBLE_PRIMARY_FACT"),
        ("invalid_quarter", "NO_ELIGIBLE_PRIMARY_FACT"),
        ("conflict", "CONFLICTING_PRIMARY_FACTS"),
    ],
)
def test_missing_reasons_distinguish_concepts_units_primary_identity_and_conflicts(
    change, expected
):
    facts = source_facts()
    taxonomy = facts["facts"]["us-gaap"]
    tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
    if change == "concept":
        del taxonomy[tag]
    elif change == "unit":
        taxonomy[tag]["units"] = {"EUR": taxonomy[tag]["units"]["USD"]}
    elif change == "metadata":
        for r in taxonomy[tag]["units"]["USD"]:
            if r["end"] == "2025-12-31":
                r["accn"] = "9999999999-26-999999"
    elif change == "invalid_quarter":
        rows = taxonomy[tag]["units"]["USD"]
        primary = next(r for r in rows if r["end"] == "2025-12-31" and r["form"] == "10-K")
        rows.append(dict(primary, start="2025-10-01", val=True))
        eps = taxonomy["EarningsPerShareBasic"]["units"]["USD/shares"]
        eps_primary = next(r for r in eps if r["end"] == "2025-12-31" and r["form"] == "10-K")
        eps.append(dict(eps_primary, start="2025-10-01", val=0.5))
    else:
        rows = taxonomy[tag]["units"]["USD"]
        primary = next(r for r in rows if r["end"] == "2025-12-31" and r["form"] == "10-K")
        rows.append(dict(primary, val=99999999))
    with application(facts) as atlas:
        if change == "invalid_quarter":
            result = atlas.get_research(
                request(period="quarterly", count=3, response={"fields": ["value"]})
            )
            # EPS supplies an independent Q4 anchor even when revenue's numeric value is invalid.
        else:
            result = atlas.get_research(request(response={"fields": ["value"]}))
    value = result["records"][-1] if change == "invalid_quarter" else result["records"][0]
    assert value["value"] is None and value["reason_code"] == expected and value["reason"]
    assert result["coverage"]["partial"]


def test_ytd_only_cash_flow_is_distinguished_from_a_missing_standard_concept():
    facts = source_facts()
    rows = facts["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]
    rows[:] = [r for r in rows if not (r["end"] == "2026-06-30" and r["start"] == "2026-04-01")]
    with application(facts) as atlas:
        result = atlas.get_research(request(period="quarterly", metrics=["operating_cash_flow"]))
    assert result["records"][0]["value"] is None
    assert result["records"][0]["reason_code"] == "NO_PRIMARY_DISCRETE_QUARTER"


def test_no_duration_anchor_reports_supported_concepts_without_inventing_a_period():
    facts = deepcopy(source_facts())
    for concept in facts["facts"]["us-gaap"].values():
        for rows in concept.get("units", {}).values():
            rows[:] = [r for r in rows if not r.get("start")]
    with application(facts) as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(request(period="quarterly"))
    assert exc.value.code == "INCOMPLETE_COVERAGE"
    assert exc.value.details["reason_code"] == "NO_REPORTED_DURATION_ANCHOR"
    assert "revenue" in exc.value.details["supported_tags"]
