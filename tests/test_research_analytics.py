"""Financial arithmetic, actual-period matching and cross-interface regressions."""

import json
import subprocess
import sys
from decimal import ROUND_UP, Decimal, localcontext
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import FACTS, SUBMISSIONS, TICKERS, SECProvider, Snapshot
from atlas_terminal.research import ResearchSecFixtures
from atlas_terminal.server import create_app
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
CASH_FLOW = "NetCashProvidedByUsedInOperatingActivities"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"


def raw(name):
    return json.loads(RESOURCE.joinpath(name).read_bytes())


def application(*, facts=None, submissions=None):
    overrides = {}
    for name, data in [("companyfacts.json", facts), ("submissions.json", submissions)]:
        if data is not None:
            overrides[name] = json.dumps(data).encode()
    return Atlas("fixture", research_source=SECProvider(ResearchSecFixtures(overrides)))


def query(metrics=None, **extra):
    result = {"dataset": "financial_analytics", "identifier": "ATLASDEMO", "count": 1}
    if metrics is not None:
        result["metrics"] = metrics
    return result | extra


def values(result):
    return {record["metric"]: record for record in result["records"]}


def add_filing(submissions, *, accession, end, filed="2025-08-01", form="10-Q"):
    row = {
        "accessionNumber": accession,
        "reportDate": end,
        "filingDate": filed,
        "acceptanceDateTime": filed + "T16:00:00Z",
        "form": form,
        "primaryDocument": "report.htm",
    }
    for key, column in submissions["filings"]["recent"].items():
        column.append(row.get(key, ""))


def test_growth_and_cash_flow_use_exact_reported_inputs_with_resolvable_provenance():
    body = RESOURCE.joinpath("companyfacts.json").read_bytes()
    original = json.loads(body, parse_float=Decimal)
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query())
    records = values(result)
    assert records["revenue_growth_yoy"]["value"] == "0.25"
    assert records["net_income_growth_yoy"]["value"] == "0.6"  # amended 24m / original 15m
    assert records["operating_income_growth_yoy"]["value"] == "0"
    assert records["eps_basic_growth_yoy"]["value"] == "0"
    assert records["eps_diluted_growth_yoy"]["value"] == "0"
    assert records["free_cash_flow"]["value"] == "25000000"
    assert records["free_cash_flow"]["unit"] == "USD"
    assert records["free_cash_flow_margin"]["value"] == "0.1666666666666666666666666667"
    assert result["coverage"]["derived_cells"] == 7 and not result["coverage"]["partial"]
    assert result["period_ends"] == ["2025-12-31"]
    for record in result["records"]:
        assert record["derived"] and record["status"] == "derived" and record["formula"]
        for fact in record["inputs"]:
            source = fact["source"]
            assert source["raw_sha256"] == digest(body)
            resolved = original
            for part in source["json_pointer"].strip("/").split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                resolved = resolved[int(key)] if isinstance(resolved, list) else resolved[key]
            assert Decimal(fact["value"]) == resolved["val"]
            assert source["accession"] == resolved["accn"]
    assert records["revenue_growth_yoy"]["inputs"][1]["role"] == "prior_year"
    assert records["revenue_growth_yoy"]["comparison_period"]["end"] == "2024-12-31"


def test_missing_baseline_is_not_replaced_by_another_period_or_zero():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(["revenue_growth_yoy"], count=3, allow_partial=True))
        with pytest.raises(AtlasError, match="allow_partial"):
            atlas.get_research(query(["revenue_growth_yoy"], count=3))
        quarter = atlas.get_research(
            query(["revenue_growth_yoy"], period="quarterly", allow_partial=True)
        )
    assert [r["value"] for r in result["records"]] == ["0.25", "0.2", None]
    assert result["coverage"]["derived_cells"] == 2 and result["coverage"]["partial"]
    assert quarter["records"][0]["value"] is None
    assert quarter["records"][0]["comparison_period"] is None
    assert "2025-12-31" not in quarter["period_ends"]  # no subtraction-derived Q4


@pytest.mark.parametrize("baseline", [0, -1])
def test_growth_rejects_nonpositive_prior_values_but_retains_both_inputs(baseline):
    facts = raw("companyfacts.json")
    facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][1]["val"] = baseline
    with application(facts=facts) as atlas:
        record = atlas.get_research(query(["revenue_growth_yoy"], allow_partial=True))["records"][0]
    assert record["value"] is None and "must be positive" in record["reason"]
    assert record["inputs"][1]["value"] == str(baseline)


@pytest.mark.parametrize(
    "prior_start,period,expected",
    [
        ("2025-04-01", "quarterly", "0.5"),
        ("2025-01-01", "ytd", "2"),
        ("2025-04-01", "ytd", None),
    ],
)
def test_growth_matches_actual_prior_year_quarter_or_ytd_not_row_position(
    prior_start, period, expected
):
    facts, submissions = raw("companyfacts.json"), raw("submissions.json")
    accession = "9999999999-25-000012"
    add_filing(submissions, accession=accession, end="2025-06-30")
    facts["facts"]["us-gaap"][REVENUE]["units"]["USD"].append(
        {
            "start": prior_start,
            "end": "2025-06-30",
            "val": 30000000,
            "accn": accession,
            "form": "10-Q",
            "filed": "2025-08-01",
            "fy": 1900,
            "fp": "Q4",  # intentionally misleading labels
        }
    )
    with application(facts=facts, submissions=submissions) as atlas:
        record = atlas.get_research(
            query(["revenue_growth_yoy"], period=period, allow_partial=True)
        )["records"][0]
    assert record["value"] == expected
    if expected is not None:
        assert record["comparison_period"]["end"] == "2025-06-30"
        assert record["comparison_period"]["start"] == prior_start


@pytest.mark.parametrize(
    "current_end,expected",
    [
        ("2025-12-27", "0.25"),  # 52 weeks
        ("2026-01-03", "0.25"),  # 53 weeks
        ("2026-01-10", None),
    ],
)
def test_growth_allows_52_53_week_years_without_matching_different_durations(current_end, expected):
    facts, submissions = raw("companyfacts.json"), raw("submissions.json")
    submissions["filings"]["files"] = []
    submissions["filings"]["recent"] = {key: [] for key in submissions["filings"]["recent"]}
    rows = []
    for year, start, end, value in [
        (2024, "2023-12-31", "2024-12-28", 100),
        (2025, "2024-12-29", current_end, 125),
    ]:
        accession, filed = f"9999999999-{year + 1 - 2000:02}-000001", f"{year + 1}-02-15"
        add_filing(submissions, accession=accession, end=end, filed=filed, form="10-K")
        rows.append(
            {
                "start": start,
                "end": end,
                "val": value,
                "accn": accession,
                "form": "10-K",
                "filed": filed,
            }
        )
    facts["facts"]["us-gaap"] = {REVENUE: {"units": {"USD": rows}}}
    with application(facts=facts, submissions=submissions) as atlas:
        record = atlas.get_research(query(["revenue_growth_yoy"], allow_partial=True))["records"][0]
    assert record["value"] == expected


def test_growth_uses_baseline_beyond_the_requested_twenty_output_periods():
    facts, submissions = raw("companyfacts.json"), raw("submissions.json")
    submissions["filings"]["files"] = []
    submissions["filings"]["recent"] = {key: [] for key in submissions["filings"]["recent"]}
    rows = []
    for year in range(2005, 2026):
        accession, filed = f"9999999999-{year + 1 - 2000:02}-000001", f"{year + 1}-02-15"
        end = f"{year}-12-31"
        add_filing(submissions, accession=accession, end=end, filed=filed, form="10-K")
        rows.append(
            {
                "start": f"{year}-01-01",
                "end": end,
                "val": year,
                "accn": accession,
                "form": "10-K",
                "filed": filed,
            }
        )
    facts["facts"]["us-gaap"] = {REVENUE: {"units": {"USD": rows}}}
    with application(facts=facts, submissions=submissions) as atlas:
        result = atlas.get_research(query(["revenue_growth_yoy"], count=20))
    assert len(result["records"]) == 20 and not result["coverage"]["partial"]
    assert result["records"][-1]["comparison_period"]["end"] == "2005-12-31"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_growth_does_not_assume_equivalence_or_choose_conflicting_prior_facts(ambiguous):
    facts = raw("companyfacts.json")
    taxonomy = facts["facts"]["us-gaap"]
    rows = taxonomy[REVENUE]["units"]["USD"]
    if ambiguous:
        rows.append(dict(rows[1], val=121000000))
    else:
        prior = rows.pop(1)
        taxonomy["Revenues"] = {"units": {"USD": [prior]}}
    with application(facts=facts) as atlas:
        record = atlas.get_research(query(["revenue_growth_yoy"], allow_partial=True))["records"][0]
    assert record["value"] is None
    assert ("ambiguous" if ambiguous else "concepts") in record["reason"]


@pytest.mark.parametrize(
    "change", ["missing", "ambiguous", "period", "negative_capex", "zero_revenue"]
)
def test_cash_flow_requires_reported_compatible_inputs(change):
    facts = raw("companyfacts.json")
    taxonomy = facts["facts"]["us-gaap"]
    capex = taxonomy[CAPEX]["units"]["USD"]
    if change == "missing":
        capex[:] = [r for r in capex if r["end"] != "2025-12-31"]
    elif change == "ambiguous":
        capex.append(dict(capex[2], val=6000000))
    elif change == "period":
        capex[2]["start"] = "2025-01-02"
    elif change == "negative_capex":
        capex[2]["val"] = -5000000
    else:
        taxonomy[REVENUE]["units"]["USD"][2]["val"] = 0
    with application(facts=facts) as atlas:
        record = atlas.get_research(query(["free_cash_flow_margin"], allow_partial=True))[
            "records"
        ][0]
    assert record["value"] is None and record["reason"]
    assert len(record["inputs"]) == 3


def test_cash_flow_and_growth_subtraction_preserve_values_larger_than_decimal_precision():
    facts = raw("companyfacts.json")
    taxonomy = facts["facts"]["us-gaap"]
    taxonomy[CASH_FLOW]["units"]["USD"][2]["val"] = 10**40 + 1
    taxonomy[CAPEX]["units"]["USD"][2]["val"] = 10**40
    taxonomy[REVENUE]["units"]["USD"][2]["val"] = 10**40 + 1
    taxonomy[REVENUE]["units"]["USD"][1]["val"] = 10**40
    with application(facts=facts) as atlas, localcontext() as context:
        context.prec = 6
        result = values(atlas.get_research(query(["free_cash_flow", "revenue_growth_yoy"])))
    assert result["free_cash_flow"]["value"] == "1"
    assert result["revenue_growth_yoy"]["value"] == "0." + "0" * 39 + "1"


def test_ratio_rounding_does_not_depend_on_the_sdk_callers_decimal_context():
    facts = raw("companyfacts.json")
    taxonomy = facts["facts"]["us-gaap"]
    taxonomy[CASH_FLOW]["units"]["USD"][2]["val"] = 2
    taxonomy[CAPEX]["units"]["USD"][2]["val"] = 1
    taxonomy[REVENUE]["units"]["USD"][2]["val"] = 3
    with application(facts=facts) as atlas, localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        record = atlas.get_research(query(["free_cash_flow_margin"]))["records"][0]
    assert record["value"] == "0.3333333333333333333333333333"


@pytest.mark.parametrize(
    "cash,capex,expected", [(30000000, 0, "30000000"), (-1, 5000000, "-5000001")]
)
def test_zero_capex_and_negative_cash_flow_are_not_imputed_or_dropped(cash, capex, expected):
    facts = raw("companyfacts.json")
    facts["facts"]["us-gaap"][CASH_FLOW]["units"]["USD"][2]["val"] = cash
    facts["facts"]["us-gaap"][CAPEX]["units"]["USD"][2]["val"] = capex
    with application(facts=facts) as atlas:
        record = atlas.get_research(query(["free_cash_flow"]))["records"][0]
    assert record["value"] == expected


class IssuerFixtures(ResearchSecFixtures):
    """Independent fictional issuer documents exercise the real normalization path."""

    def __init__(self, transform=None):
        super().__init__()
        self.documents = {}
        tickers = {}
        for index, ticker in enumerate(["ALPHA", "BETA"]):
            cik = str(9999999999 - index)
            facts, submissions = raw("companyfacts.json"), raw("submissions.json")
            facts = json.loads(json.dumps(facts).replace("9999999999", cik))
            submissions = json.loads(json.dumps(submissions).replace("9999999999", cik))
            submissions["filings"]["files"] = []
            submissions["tickers"] = [ticker]
            if index and transform:
                transform(facts, submissions)
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
            "https://fixtures.atlas.invalid/issuers/" + url.split("://", 1)[1],
            "2026-10-20T00:00:00Z",
        )


def comparison(transform=None):
    return Atlas("fixture", research_source=SECProvider(IssuerFixtures(transform)))


def compare_query(**extra):
    return {
        "dataset": "compare",
        "identifiers": ["ALPHA", "BETA"],
        "metrics": ["revenue", "revenue_growth_yoy", "free_cash_flow", "free_cash_flow_margin"],
    } | extra


def test_comparison_returns_reported_and_derived_values_at_identical_actual_periods():
    with comparison() as atlas:
        result = atlas.get_research(compare_query())
    assert result["period_ends"] == ["2025-12-31"] and not result["coverage"]["partial"]
    assert result["coverage"]["available_cells"] == 8
    for issuer in result["records"]:
        assert all(record["comparable"] for record in issuer["records"])
        assert values(issuer)["revenue_growth_yoy"]["value"] == "0.25"
        assert values(issuer)["free_cash_flow"]["value"] == "25000000"
        assert values(issuer)["revenue"]["status"] == "reported"
    assert all(
        record["comparable_issuers"] == 2 for record in result["alignment"]["metrics"].values()
    )


def test_comparison_finds_latest_shared_end_instead_of_using_each_issuers_latest():
    def remove_latest(facts, submissions):
        recent = submissions["filings"]["recent"]
        keep = [i for i, end in enumerate(recent["reportDate"]) if end != "2025-12-31"]
        submissions["filings"]["recent"] = {
            key: [column[i] for i in keep] for key, column in recent.items()
        }

    with comparison(remove_latest) as atlas:
        result = atlas.get_research(compare_query(metrics=["revenue"]))
    assert result["period_ends"] == ["2024-12-31"]
    assert all(issuer["records"][0]["value"] == "120000000" for issuer in result["records"])


def test_comparison_masks_same_end_values_with_different_start_dates():
    def change_start(facts, submissions):
        facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][2]["start"] = "2025-01-02"

    with comparison(change_start) as atlas:
        with pytest.raises(AtlasError, match="unaligned"):
            atlas.get_research(compare_query())
        result = atlas.get_research(compare_query(allow_partial=True))
    assert result["coverage"]["partial"]
    assert result["alignment"]["metrics"]["revenue"]["status"] == "mismatched_periods"
    for issuer in result["records"]:
        records = values(issuer)
        assert records["revenue"]["value"] is None and not records["revenue"]["comparable"]
        assert records["revenue_growth_yoy"]["value"] is None
        assert records["free_cash_flow"]["value"] == "25000000"  # unrelated aligned metric survives


def test_comparison_also_aligns_growth_baselines():
    def change_prior_start(facts, submissions):
        facts["facts"]["us-gaap"][REVENUE]["units"]["USD"][1]["start"] = "2024-01-02"

    with comparison(change_prior_start) as atlas:
        result = atlas.get_research(compare_query(allow_partial=True))
    for issuer in result["records"]:
        assert values(issuer)["revenue"]["value"] == "150000000"
        assert values(issuer)["revenue_growth_yoy"]["value"] is None
    assert result["alignment"]["metrics"]["revenue_growth_yoy"]["status"] == "mismatched_periods"


def test_comparison_reports_no_common_end_without_leaking_mixed_period_values():
    def remove_annual_filings(facts, submissions):
        recent = submissions["filings"]["recent"]
        keep = [i for i, form in enumerate(recent["form"]) if form not in {"10-K", "10-K/A"}]
        # Keep one unrelated annual filing to make an eligible, disjoint period.
        for key, column in recent.items():
            recent[key] = [column[i] for i in keep]
        add_filing(
            submissions,
            accession="9999999998-24-000001",
            end="2023-12-31",
            filed="2024-02-15",
            form="10-K",
        )

    with comparison(remove_annual_filings) as atlas:
        with pytest.raises(AtlasError, match="unaligned"):
            atlas.get_research(compare_query(metrics=["revenue"]))
        result = atlas.get_research(compare_query(metrics=["revenue"], allow_partial=True))
    assert result["alignment"]["period_end"] is None and result["coverage"]["partial"]
    assert all(
        record["value"] is None for issuer in result["records"] for record in issuer["records"]
    )
    assert all(issuer["available_period_ends"] for issuer in result["records"])


@pytest.mark.parametrize(
    "bad",
    [
        query(["revenue"]),
        query(["free_cash_flow", "free_cash_flow"]),
        query(["free_cash_flow"], count=True),
        query(["free_cash_flow"], as_of="2020-01-01"),
        {"dataset": "statements", "identifier": "ATLASDEMO", "metrics": ["free_cash_flow"]},
        compare_query(metrics=["made_up_metric"]),
    ],
)
def test_invalid_analytics_queries_are_rejected_before_transport(bad, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Invalid query reached a source")

    monkeypatch.setattr(ResearchSecFixtures, "get", fail)
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(bad)


@pytest.mark.parametrize(
    "dataset,extra,arguments",
    [
        (
            "financial_analytics",
            {"identifier": "ATLASDEMO", "count": 1},
            ["--identifier", "ATLASDEMO", "--count", "1"],
        ),
        ("compare", {"identifiers": ["ATLASDEMO"]}, ["--identifiers", "ATLASDEMO"]),
    ],
)
def test_analytics_cli_sdk_and_http_return_identical_traceable_results(
    dataset, extra, arguments, tmp_path
):
    metrics = ["revenue_growth_yoy", "free_cash_flow", "free_cash_flow_margin"]
    request = {"dataset": dataset, "metrics": metrics, **extra}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(request)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            dataset,
            *arguments,
            "--metrics",
            ",".join(metrics),
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0 and not proc.stderr
    assert json.loads(proc.stdout) == expected
    with TestClient(create_app(provider="fixture", payments="mock", directory=tmp_path)) as client:
        response = client.post("/v1/research", json=request)
        assert response.status_code == 200 and response.json() == expected
        assert "PAYMENT-REQUIRED" not in response.headers
    schema = json.loads((Path(__file__).parents[1] / "schemas/research.schema.json").read_bytes())
    Draft202012Validator({**schema, "$ref": "#/$defs/ResearchResult"}).validate(expected)
    assert not expected["coverage"]["partial"]
