"""Explicit-universe screens and filing-date event classification with original evidence."""

import json
from decimal import ROUND_UP, localcontext
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import FACTS, SUBMISSIONS, TICKERS, SECProvider, Snapshot
from atlas_terminal.research import ResearchSecFixtures
from atlas_terminal.research_discovery import (
    ScreenFilter,
    event_catalog,
    screen_catalog,
    screen_units,
)

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"


def raw(name):
    return json.loads(RESOURCE.joinpath(name).read_bytes())


class ScreenFixtures(ResearchSecFixtures):
    def __init__(self, transform=None):
        super().__init__()
        self.documents = {}
        tickers = {}
        for index, ticker in enumerate(["ALPHA", "BETA"]):
            cik = str(9999999999 - index)
            facts = json.loads(json.dumps(raw("companyfacts.json")).replace("9999999999", cik))
            submissions = json.loads(json.dumps(raw("submissions.json")).replace("9999999999", cik))
            submissions["filings"]["files"] = []
            submissions["tickers"] = [ticker]
            if transform:
                transform(index, facts, submissions)
            self.documents[SUBMISSIONS + f"CIK{cik}.json"] = submissions
            self.documents[FACTS + f"CIK{cik}.json"] = facts
            tickers[str(index)] = {
                "cik_str": int(cik),
                "ticker": ticker,
                "title": ticker + " (SYNTHETIC)",
            }
        self.documents[TICKERS] = tickers

    def get(self, url, ttl):
        return Snapshot(
            json.dumps(self.documents[url]).encode(),
            "https://fixtures.atlas.invalid/screen/" + url.split("://", 1)[1],
            "2026-10-20T00:00:00Z",
        )


def screen_query(**extra):
    return {
        "dataset": "company_screen",
        "identifiers": ["ALPHA", "BETA"],
        "metrics": ["revenue_growth_yoy"],
        "filters": [{"metric": "revenue", "operator": "gte", "value": "150000000", "unit": "USD"}],
    } | extra


def test_screen_matches_and_exclusions_retain_all_issuers_and_original_facts():
    def transform(index, facts, submissions):
        if index:
            facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][2]["val"] = 125000000

    with Atlas("fixture", research_source=SECProvider(ScreenFixtures(transform))) as atlas:
        result = atlas.get_research(screen_query(sort_by="revenue", sort_order="asc"))
    assert [row["identifier"] for row in result["records"]] == ["BETA", "ALPHA"]
    assert [row["screen_status"] for row in result["records"]] == ["excluded", "matched"]
    assert [row["matches"] for row in result["records"]] == [False, True]
    assert result["coverage"]["matched_issuers"] == result["coverage"]["excluded_issuers"] == 1
    assert result["alignment"]["period_end"] == "2025-12-31"
    for issuer in result["records"]:
        cell = next(row for row in issuer["records"] if row["metric"] == "revenue")
        assert cell["source"]["json_pointer"].endswith("/units/USD/2")
        assert cell["period"] == {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"}
        assert issuer["filter_results"][0]["value"] == cell["value"]
        assert issuer["filter_results"][0]["threshold"] == "150000000"


@pytest.mark.parametrize(
    "operator,expected", [("lt", False), ("lte", True), ("eq", True), ("gte", True), ("gt", False)]
)
def test_threshold_boundaries_use_exact_decimal_values(operator, expected):
    enormous = 10**40 + 1

    def transform(index, facts, submissions):
        facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][2]["val"] = enormous

    with (
        Atlas("fixture", research_source=SECProvider(ScreenFixtures(transform))) as atlas,
        localcontext() as context,
    ):
        context.prec = 6
        context.rounding = ROUND_UP
        result = atlas.get_research(
            screen_query(
                metrics=["revenue"],
                filters=[
                    {
                        "metric": "revenue",
                        "operator": operator,
                        "value": str(enormous),
                        "unit": "USD",
                    }
                ],
            )
        )
    assert all(row["matches"] is expected for row in result["records"])


def test_screen_does_not_treat_missing_or_period_mismatches_as_zero_or_false():
    def transform(index, facts, submissions):
        if index:
            facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][2]["start"] = "2025-01-02"

    with Atlas("fixture", research_source=SECProvider(ScreenFixtures(transform))) as atlas:
        with pytest.raises(AtlasError, match="allow_partial"):
            atlas.get_research(screen_query())
        result = atlas.get_research(screen_query(allow_partial=True))
    assert result["coverage"]["unavailable_issuers"] == 2
    assert result["coverage"]["partial"]
    for row in result["records"]:
        assert row["matches"] is None
        assert row["filter_results"][0]["passed"] is None
        assert "identical actual periods" in row["filter_results"][0]["reason"]


def test_unknown_issuer_is_returned_unavailable_and_sort_keeps_it_last():
    with Atlas("fixture", research_source=SECProvider(ScreenFixtures())) as atlas:
        result = atlas.get_research(
            screen_query(
                identifiers=["MISSING", "ALPHA"],
                allow_partial=True,
                sort_by="revenue",
                sort_order="desc",
            )
        )
    assert [row["identifier"] for row in result["records"]] == ["ALPHA", "MISSING"]
    missing = result["records"][1]
    assert missing["screen_status"] == "unavailable" and missing["matches"] is None
    assert missing["error"]["code"] == "COMPANY_NOT_FOUND"
    assert result["coverage"]["partial"] and result["coverage"]["returned_issuers"] == 2


def test_screen_rejects_two_aliases_of_same_issuer():
    with Atlas("fixture") as atlas, pytest.raises(AtlasError, match="distinct issuers"):
        atlas.get_research(screen_query(identifiers=["ATLASDEMO", "9999999999"]))


def test_ratio_screen_and_comparison_share_reported_inputs_and_formula():
    with Atlas("fixture", research_source=SECProvider(ScreenFixtures())) as atlas:
        result = atlas.get_research(
            screen_query(
                metrics=["revenue"],
                filters=[
                    {"metric": "net_margin", "operator": "gte", "value": "0.16", "unit": "ratio"}
                ],
                sort_by="current_ratio",
            )
        )
        compared = atlas.get_research(
            {"dataset": "compare", "identifiers": ["ALPHA", "BETA"], "metrics": ["net_margin"]}
        )
    assert all(row["matches"] is True for row in result["records"])
    for issuer, comparison in zip(result["records"], compared["records"], strict=True):
        margin = next(row for row in issuer["records"] if row["metric"] == "net_margin")
        assert margin == comparison["records"][0]
        assert margin["value"] == "0.16" and margin["comparable"]
        assert margin["formula"] == "net_income / revenue"
        assert [cell["value"] for cell in margin["inputs"]] == ["24000000", "150000000"]
        assert all(cell["source"]["raw_sha256"] for cell in margin["inputs"])


def test_ratio_screen_masks_different_actual_input_periods_despite_equal_ratios():
    def transform(index, facts, submissions):
        if index:
            for concept in [REVENUE, "NetIncomeLoss"]:
                for row in facts["facts"]["us-gaap"][concept]["units"]["USD"]:
                    if row["end"] == "2025-12-31":
                        row["start"] = "2025-01-02"

    with Atlas("fixture", research_source=SECProvider(ScreenFixtures(transform))) as atlas:
        result = atlas.get_research(
            screen_query(
                metrics=[],
                filters=[
                    {"metric": "net_margin", "operator": "gte", "value": "0.1", "unit": "ratio"}
                ],
                allow_partial=True,
            )
        )
    assert result["alignment"]["metrics"]["net_margin"]["status"] == "mismatched_periods"
    assert all(row["matches"] is None for row in result["records"])
    assert all(row["records"][0]["value"] is None for row in result["records"])


def test_ratio_screen_preserves_zero_denominator_as_unavailable():
    def transform(index, facts, submissions):
        if index:
            facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][2]["val"] = 0

    with Atlas("fixture", research_source=SECProvider(ScreenFixtures(transform))) as atlas:
        result = atlas.get_research(
            screen_query(
                metrics=[],
                filters=[
                    {"metric": "net_margin", "operator": "gte", "value": "0", "unit": "ratio"}
                ],
                allow_partial=True,
            )
        )
    assert result["records"][0]["matches"] is True
    assert result["records"][1]["matches"] is None
    assert result["records"][1]["records"][0]["reason"] == "The denominator is zero."


def test_existing_ratio_results_ignore_callers_decimal_rounding_context():
    from atlas_terminal.research_financials import ratio_records

    period = {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"}
    inputs = [
        {"metric": metric, "value": value, "status": "reported", "period": period}
        for metric, value in [("net_income", "1"), ("revenue", "3")]
    ]
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        record = ratio_records(inputs, ["2025-12-31"], ["net_margin"])[0]
    assert record["value"] == "0.3333333333333333333333333333"


def test_missing_ratio_comparison_declares_correct_instant_period_and_unit():
    from atlas_terminal.research_comparison import missing_record

    record = missing_record("current_ratio")
    assert record["unit"] == "ratio" and record["derived"] is True
    assert record["period"] == {"kind": "instant", "start": None, "end": None}


def test_multiple_filters_require_all_inputs_and_keep_input_order_on_sort_ties():
    with Atlas("fixture", research_source=SECProvider(ScreenFixtures())) as atlas:
        result = atlas.get_research(
            screen_query(
                identifiers=["BETA", "ALPHA"],
                filters=[
                    {"metric": "revenue", "operator": "gte", "value": "150000000", "unit": "USD"},
                    {
                        "metric": "revenue_growth_yoy",
                        "operator": "gt",
                        "value": "0.25",
                        "unit": "ratio",
                    },
                ],
                sort_by="revenue_growth_yoy",
            )
        )
    assert [row["identifier"] for row in result["records"]] == ["BETA", "ALPHA"]
    assert all(row["screen_status"] == "excluded" for row in result["records"])
    assert all(
        [condition["passed"] for condition in row["filter_results"]] == [True, False]
        for row in result["records"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"value": "NaN"},
        {"value": "Infinity"},
        {"value": "1e10"},
        {"value": 1},
        {"value": True},
        {"value": "1" * 61},
        {"unit": "millions_USD"},
        {"metric": "pe_ratio"},
        {"operator": "contains"},
    ],
)
def test_screen_filter_rejects_nonfinite_coercions_units_and_unknown_metrics(bad):
    with pytest.raises(ValidationError):
        ScreenFilter.model_validate(
            {"metric": "revenue", "operator": "gt", "value": "1", "unit": "USD"} | bad
        )


def test_published_filter_schema_exposes_metric_specific_units():
    validator = Draft202012Validator(ScreenFilter.model_json_schema())
    assert validator.is_valid(
        {"metric": "net_margin", "operator": "gte", "value": "0.1", "unit": "ratio"}
    )
    assert not validator.is_valid(
        {"metric": "net_margin", "operator": "gte", "value": "0.1", "unit": "USD"}
    )
    assert not validator.is_valid(
        {"metric": "unknown", "operator": "gte", "value": "0.1", "unit": "ratio"}
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"filters": []},
        {"identifiers": []},
        {"sort_by": "pe_ratio"},
        {"metrics": ["pe_ratio"]},
        {"sort_order": "random"},
        {"filters": [{"metric": "revenue", "operator": "gt", "value": "1", "unit": "USD"}] * 2},
    ],
)
def test_bad_screens_fail_before_source_access(bad, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Invalid screen fetched a source")

    monkeypatch.setattr(ResearchSecFixtures, "get", fail)
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(screen_query(**bad))


@pytest.mark.parametrize("additional", ["filter", "sort"])
def test_combined_screen_metric_budget_rejects_before_transport(additional, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Over-budget screen fetched a source")

    units = screen_units()
    metrics = list(units)
    condition = metrics[40] if additional == "filter" else metrics[0]
    query = screen_query(
        metrics=metrics[:40],
        filters=[{"metric": condition, "operator": "gte", "value": "0", "unit": units[condition]}],
    )
    if additional == "sort":
        query["sort_by"] = metrics[40]
    monkeypatch.setattr(ResearchSecFixtures, "get", fail)
    with Atlas("fixture") as atlas, pytest.raises(AtlasError, match="forty distinct metrics"):
        atlas.get_research(query)


def event_query(**extra):
    return {
        "dataset": "filing_events",
        "identifier": "ATLASDEMO",
        "series": "earnings_results",
    } | extra


def events(submissions=None, *, max_pages=20):
    overrides = {"submissions.json": json.dumps(submissions).encode()} if submissions else {}
    return Atlas(
        "fixture", research_source=SECProvider(ResearchSecFixtures(overrides), max_pages=max_pages)
    )


def test_results_filing_event_uses_filing_date_and_exact_source_cells():
    with events() as atlas:
        result = atlas.get_research(event_query(start_date="2026-10-16", end_date="2026-10-16"))
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["event_date"] == record["filed_on"] == "2026-10-16"
    assert record["event_types"] == ["current_reports", "earnings_results"]
    assert record["date_basis"] == "filing_date"
    assert record["items"] == "2.02,9.01"
    source = raw("submissions.json")
    mapping = {
        "filed_on": "filingDate",
        "report_date": "reportDate",
        "accepted_at": "acceptanceDateTime",
        "form": "form",
        "items": "items",
    }
    for field, column in mapping.items():
        pointer = record["field_sources"][field]["json_pointer"]
        assert pointer == f"/filings/recent/{column}/6"
        assert source["filings"]["recent"][column][6] == record[field]
    assert not result["coverage"]["scheduled_future_events"]


@pytest.mark.parametrize(
    "items,form,count",
    [
        ("12.02,9.01", "8-K", 0),
        ("2.020,9.01", "8-K", 0),
        ("2.02,9.01", "6-K", 0),
        (None, "8-K", 0),
        (" 2.02 ; 9.01", "8-K/A", 1),
    ],
)
def test_earnings_classification_requires_exact_8k_item(items, form, count):
    submissions = raw("submissions.json")
    submissions["filings"]["recent"]["items"][6] = items
    submissions["filings"]["recent"]["form"][6] = form
    with events(submissions) as atlas:
        result = atlas.get_research(event_query())
    assert len(result["records"]) == count
    if count:
        assert result["records"][0]["amendment"] is True


def test_event_boundaries_filter_filing_date_without_backdating_to_report_date():
    submissions = raw("submissions.json")
    submissions["filings"]["recent"]["filingDate"][6] = "2026-10-18"
    submissions["filings"]["recent"]["acceptanceDateTime"][6] = "2026-10-18T16:00:00Z"
    with events(submissions) as atlas:
        absent = atlas.get_research(event_query(start_date="2026-10-16", end_date="2026-10-17"))
        present = atlas.get_research(
            event_query(
                start_date="2026-10-18",
                end_date="2026-10-18",
                requirements={"min_observation_date": "2026-10-18"},
            )
        )
    assert not absent["records"]
    assert present["records"][0]["report_date"] == "2026-10-16"
    assert present["quality"]["latest_observation_date"] == "2026-10-18"


def test_event_limit_archive_gap_and_amendments_remain_explicit():
    with events(max_pages=0) as atlas:
        result = atlas.get_research(event_query(series="periodic_reports", limit=2))
    assert len(result["records"]) == 2
    assert result["coverage"]["truncated"] and result["coverage"]["history_truncated"]
    assert result["coverage"]["matching_records"] > 2
    with events() as atlas:
        all_reports = atlas.get_research(event_query(series="periodic_reports"))["records"]
    annual = [row for row in all_reports if row["report_date"] == "2025-12-31"]
    assert len(annual) == 2
    assert {row["amendment"] for row in annual} == {True, False}


def test_current_report_can_classify_results_and_shareholder_votes_independently():
    submissions = raw("submissions.json")
    submissions["filings"]["recent"]["items"][6] = "2.02,5.07,9.01"
    with events(submissions) as atlas:
        result = atlas.get_research(event_query(series="shareholder_votes"))
    assert result["records"][0]["event_types"] == [
        "current_reports",
        "earnings_results",
        "shareholder_votes",
    ]


@pytest.mark.parametrize(
    "recent_items,archive_items,pointer",
    [
        ("2.02,9.01", "2.02,9.01", "/filings/recent/items/6"),
        ("2.02,9.01", None, "/filings/recent/items/6"),
        (None, "2.02,9.01", "/items/0"),
    ],
)
def test_duplicate_filing_prefers_complete_coherent_row_and_resolves_items(
    recent_items, archive_items, pointer
):
    submissions = raw("submissions.json")
    archive = {key: [column[6]] for key, column in submissions["filings"]["recent"].items()}
    submissions["filings"]["recent"]["items"][6] = recent_items
    if archive_items is None:
        archive.pop("items")
    else:
        archive["items"] = [archive_items]
    transport = ResearchSecFixtures(
        {
            "submissions.json": json.dumps(submissions).encode(),
            "CIK9999999999-submissions-001.json": json.dumps(archive).encode(),
        }
    )
    with Atlas("fixture", research_source=SECProvider(transport)) as atlas:
        record = atlas.get_research(event_query())["records"][0]
    assert record["items"] == "2.02,9.01"
    assert record["field_sources"]["items"]["json_pointer"] == pointer
    assert {
        source["raw_sha256"] for source in [record["source"], *record["field_sources"].values()]
    } == {record["source"]["raw_sha256"]}


@pytest.mark.parametrize("archive_items", ["5.07,9.01", "", 202])
def test_duplicate_conflicting_or_malformed_filing_items_fail(archive_items):
    submissions = raw("submissions.json")
    archive = {key: [column[6]] for key, column in submissions["filings"]["recent"].items()}
    archive["items"] = [archive_items]
    transport = ResearchSecFixtures(
        {
            "submissions.json": json.dumps(submissions).encode(),
            "CIK9999999999-submissions-001.json": json.dumps(archive).encode(),
        }
    )
    with Atlas("fixture", research_source=SECProvider(transport)) as atlas:
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(event_query())
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_compact_projection_preserves_screen_and_event_interpretation():
    with Atlas("fixture") as atlas:
        screened = atlas.get_research(
            screen_catalog()["example"] | {"response": {"view": "compact", "fields": ["value"]}}
        )
        event = atlas.get_research(
            event_query(response={"view": "compact", "fields": ["accession"]})
        )
        evidence = atlas.get_evidence(event["records"][0]["field_sources"]["items"]["evidence_id"])
    row = screened["records"][0]
    assert row["screen_status"] == "matched" and row["filter_results"][0]["passed"]
    assert event["records"][0]["event_types"] == ["current_reports", "earnings_results"]
    assert event["records"][0]["date_basis"] == "filing_date"
    assert evidence["evidence"]["json_pointer"] == "/filings/recent/items/6"


def test_catalog_examples_execute_and_unknown_event_series_fails_without_transport(monkeypatch):
    with Atlas("fixture") as atlas:
        assert atlas.get_research(event_catalog()["example"])["records"]
        assert atlas.get_research(screen_catalog()["example"])["records"]

    def fail(*args, **kwargs):
        raise AssertionError("Invalid event selector fetched a source")

    monkeypatch.setattr(ResearchSecFixtures, "get", fail)
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(event_query(series="future_earnings"))
