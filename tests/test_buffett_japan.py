"""Fictional Japanese annual disclosures: normalization, selection and shared contracts."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.edinet_history import EdinetHistory, date_window, japan_identifier, sync_history
from atlas_terminal.edinet_normalization import normalize_filing
from atlas_terminal.edinet_scoring_fixtures import (
    CAPTURE,
    EdinetScoringFixtures,
    archive_xml,
    seed_history,
)
from atlas_terminal.edinet_selection import select_annual_filings
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_document_url, edinet_list_url
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_buffett import calculate_score
from atlas_terminal.research_buffett_japan import japan_financials, resolve_issuer
from atlas_terminal.research_japan_companies import disclosure_rows, xbrl_member
from atlas_terminal.research_views import SourceEvidence
from atlas_terminal.server import create_app
from atlas_terminal.source_deadline import remaining_timeout, source_deadline
from atlas_terminal.util import canonical, digest


def filing(code="9999", year=2026):
    transport = EdinetScoringFixtures()
    listing = transport.get(edinet_list_url(f"{year}-06-26"), 0)
    row = next(row for row in disclosure_rows(listing) if row["security_code"] == code + "0")
    return row | {"filing_date": f"{year}-06-26"}


def raw(code="9999", year=2026):
    row = filing(code, year)
    snapshot = EdinetScoringFixtures().get(edinet_document_url(row["document_id"]), 0)
    root = ET.fromstring(xbrl_member(snapshot.body)[1])
    # Preserve namespaces used only in QName text when reserializing mutations.
    root.set("xmlns:iso4217", "http://www.xbrl.org/2003/iso4217")
    return row, snapshot, root


def modified(change, code="9999"):
    row, snapshot, root = raw(code)
    change(root)
    return normalize_filing(Snapshot(archive_xml(ET.tostring(root)), snapshot.url, CAPTURE), row)


def node(root, name, context="d"):
    return next(
        child
        for child in root
        if child.tag.endswith("}" + name) and child.get("contextRef") == context
    )


def component(row, name):
    return next(item for item in row["components"] if item["metric"] == name)


def quote():
    return {
        "is_synthetic": True,
        "market_cap": "1000000000000",
        "currency": "JPY",
        "observed_on": "2026-07-01",
        "captured_at": CAPTURE,
    }


@pytest.mark.parametrize("code,standard", [("9999", "Japan GAAP"), ("9998", "IFRS")])
def test_full_reported_pipeline_uses_existing_fixed_weights(code, standard):
    with Atlas("fixture") as atlas:
        result = atlas.get_buffett_score(code + ".T", country="JP", years=5)
        valued = atlas.get_buffett_score(code, country="JP", years=5, valuation=quote())
    row = result["records"][0]
    assert result["is_synthetic"] and result["input_origin"] == "edinet_primary_annual_facts"
    assert row["quality_score"] == row["known_points"] == "80.000000000000000000"
    assert (
        row["score"] is None
        and row["available_weight"] == "80"
        and row["unevaluated_weight"] == "20"
    )
    assert component(row, "margin_of_safety")["reason"] == "valuation_input_not_supplied"
    assert valued["records"][0]["score"] == "100.000000000000000000"
    assert (
        valued["records"][0]["valuation"]["market_cap_source"]["publisher_values_verified"] is False
    )
    assert len(row["selected_periods"]) == 5 and row["currency"] == "JPY"
    assert all(item["accounting_standard"] == standard for item in row["reported_inputs"].values())
    assert len(component(row, "sustained_roe")["input_ids"]) == 15
    assert result["source_coverage"]["history"]["latest_amendment_guaranteed"] is False
    assert result["coverage"]["point_in_time"] is False


@pytest.mark.parametrize("identifier", ["9999", "9999.T", "99990", "E99999"])
def test_identifiers_resolve_using_actual_index_mapping(identifier):
    with Atlas("fixture") as atlas:
        result = atlas.get_buffett_score(identifier, country="JP", years=3)
    assert result["issuer"]["edinet_code"] == "E99999"
    assert result["issuer"]["identifier"] == "9999.T"
    assert result["records"][0]["known_points"] == "80.000000000000000000"
    assert japan_identifier("123A.T") == ("security_code", "123A0")


@pytest.mark.parametrize("identifier", ["12345.T", "72031", "7203.US", "E1234", "AAPL", "7203;rm"])
def test_ambiguous_class_or_foreign_codes_rejected(identifier):
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_buffett_score(identifier, country="JP")


@pytest.mark.parametrize(
    "code,reason",
    [("9997", "financial_business_not_applicable"), ("9996", "jpx_industry_not_confirmed")],
)
def test_financial_and_unknown_industries_are_excluded_before_xbrl(code, reason):
    with Atlas("fixture") as atlas:
        result = atlas.get_buffett_score(code, country="JP")
    assert result["issuer"]["reason"] == reason
    assert result["source_coverage"]["filing_reads"] == 0
    assert result["records"][0]["available_weight"] == "0"
    assert not result["records"][0]["applicability"]["applicable"]


def test_colliding_or_changed_codes_do_not_merge_issuers():
    first, second = filing(), filing(year=2025)
    second["edinet_code"] = "E99998"
    assert resolve_issuer([first, second], "9999")["reason"] == "ambiguous_issuer_security_mapping"
    second["edinet_code"], second["security_code"] = "E99999", "99980"
    assert (
        resolve_issuer([first, second], "E99999")["reason"] == "ambiguous_issuer_security_mapping"
    )


def test_precision_is_not_scale_and_signed_capex_has_exact_operands():
    row, snapshot, _ = raw()
    data = normalize_filing(snapshot, row)
    revenue, capex, equity = [
        data["cells"][key] for key in ("revenue", "capital_expenditure", "stockholders_equity")
    ]
    assert revenue["value"] == "1000000000000" and revenue["fact_inputs"][0]["decimals"] == "-6"
    assert capex["value"] == "50000000000" and capex["fact_inputs"][0]["value"] == "-50000000000"
    assert capex["normalization"]["formula"] == "-reported_cash_outflow"
    assert equity["value"] == "1100000000000"
    assert sum(Decimal(f["value"]) for f in equity["fact_inputs"]) == Decimal(equity["value"])
    assert (
        equity["attribution_scope"]
        == data["cells"]["net_income"]["attribution_scope"]
        == "owners_of_parent"
    )


@pytest.mark.parametrize(
    "tag,metric",
    [
        ("InterestExpensesNOE", "interest_expense"),
        ("ShareBasedCompensationExpensesOpeCF", "stock_based_compensation"),
        ("AccumulatedOtherComprehensiveIncome", "stockholders_equity"),
    ],
)
def test_missing_items_are_never_zero_or_borrowed_from_other_totals(tag, metric):
    data = modified(
        lambda root: root.remove(node(root, tag, "i" if metric == "stockholders_equity" else "d"))
    )
    assert data["cells"][metric]["value"] is None and data["cells"][metric]["reason"]


def test_missing_opening_equity_does_not_substitute_prior_filing_or_closing_value(tmp_path):
    class MissingOpening(EdinetScoringFixtures):
        def get(self, url, ttl):
            snapshot = super().get(url, ttl)
            if url == edinet_document_url("S999926A"):
                root = ET.fromstring(xbrl_member(snapshot.body)[1])
                root.set("xmlns:iso4217", "http://www.xbrl.org/2003/iso4217")
                root.remove(node(root, "ShareholdersEquity", "p"))
                return Snapshot(archive_xml(ET.tostring(root)), snapshot.url, CAPTURE)
            return snapshot

    data = japan_financials(MissingOpening(), "9999", 3, tmp_path, synthetic=True)
    score = calculate_score(data, 3, None)
    assert component(score, "sustained_roe")["points"] is None
    assert score["reported_inputs"]["2026-03-31:stockholders_equity"]["value"] == "1100000000000"
    assert score["reported_inputs"]["2025-03-31:stockholders_equity"]["value"] == "1100000000000"
    assert "opening_stockholders_equity" in component(score, "sustained_roe")["reason"]


@pytest.mark.parametrize(
    "value,reason", [("50000000000", "expected_nonpositive_cash_outflow"), ("0", None)]
)
def test_cash_outflow_sign_is_declared_not_absolute_value(value, reason):
    data = modified(
        lambda root: setattr(node(root, "PurchaseOfPropertyPlantAndEquipmentInvCF"), "text", value)
    )
    assert data["cells"]["capital_expenditure"]["reason"] == reason


@pytest.mark.parametrize(
    "kind", ["currency", "divided", "precision", "duplicate", "custom_tag", "period", "nil"]
)
def test_unsupported_and_ambiguous_facts_keep_reasons(kind):
    def change(root):
        fact = node(root, "NetSales")
        if kind == "currency":
            next(n for n in root.iter() if n.tag.endswith("}measure")).text = "USD"
        elif kind == "divided":
            unit = next(n for n in root if n.tag.endswith("}unit"))
            measure = unit[0]
            unit.remove(measure)
            ns = "{http://www.xbrl.org/2003/instance}"
            divide = ET.SubElement(unit, ns + "divide")
            ET.SubElement(divide, ns + "unitNumerator").append(measure)
            ET.SubElement(
                ET.SubElement(divide, ns + "unitDenominator"), ns + "measure"
            ).text = "shares"
        elif kind == "precision":
            fact.set("decimals", "million")
        elif kind == "duplicate":
            conflicting = deepcopy(fact)
            conflicting.text = "999"
            root.append(conflicting)
        elif kind == "custom_tag":
            fact.tag = "{https://issuer.atlas.invalid/custom}NetSales"
        elif kind == "period":
            fact.set("contextRef", "i")
        else:
            fact.set("{http://www.w3.org/2001/XMLSchema-instance}nil", "true")
            fact.text = None

    data = modified(change)
    assert data["cells"]["revenue"]["value"] is None
    assert data["cells"]["revenue"]["reason"]


def test_parent_attribution_does_not_accept_total_group_profit():
    data = modified(
        lambda root: setattr(
            node(root, "ProfitLossAttributableToOwnersOfParent"),
            "tag",
            node(root, "ProfitLossAttributableToOwnersOfParent").tag.replace(
                "ProfitLossAttributableToOwnersOfParent", "ProfitLoss"
            ),
        )
    )
    assert data["cells"]["net_income"]["value"] is None


@pytest.mark.parametrize("mode", ["standalone", "segment", "typed", "false_dei"])
def test_standalone_and_dimensioned_contexts_are_not_consolidated_fallbacks(mode):
    def change(root):
        if mode == "false_dei":
            node(root, "WhetherConsolidatedFinancialStatementsArePreparedDEI").text = "false"
            return
        context = next(n for n in root if n.get("id") == "d")
        ns = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
        root.set("xmlns:dim", ns)
        scenario = ET.SubElement(context, "{http://www.xbrl.org/2003/instance}scenario")
        member = ET.SubElement(
            scenario,
            "{http://xbrl.org/2006/xbrldi}"
            + ("typedMember" if mode == "typed" else "explicitMember"),
            dimension="dim:ConsolidatedAndNonConsolidatedAxis"
            if mode == "standalone"
            else "dim:BusinessSegmentsAxis",
        )
        member.text = "dim:NonConsolidatedMember" if mode == "standalone" else "dim:SegmentMember"

    data = modified(change)
    assert data["cells"]["revenue"]["value"] is None
    assert (
        "consolidat" in data["cells"]["revenue"]["reason"]
        or "dimension" in data["cells"]["revenue"]["reason"]
        or "standalone" in data["cells"]["revenue"]["reason"]
    )


@pytest.mark.parametrize(
    "tag,value",
    [
        ("AccountingStandardsDEI", "US GAAP"),
        ("CurrentFiscalYearEndDateDEI", "2026-12-31"),
        ("EDINETCodeDEI", "E00000"),
    ],
)
def test_report_metadata_is_required_not_guessed_from_numeric_tags(tag, value):
    data = modified(lambda root: setattr(node(root, tag), "text", value))
    assert all(cell["value"] is None for cell in data["cells"].values())
    assert data["reason"]


def test_changed_fiscal_period_is_not_treated_as_an_annual_year():
    row, snapshot, root = raw()
    row["report_period_start"] = "2025-10-01"
    for element in root.iter():
        if element.text == "2025-04-01":
            element.text = "2025-10-01"
    data = normalize_filing(Snapshot(archive_xml(ET.tostring(root)), snapshot.url, CAPTURE), row)
    assert data["reason"] == "fiscal_period_not_twelve_months"
    assert all(cell["value"] is None for cell in data["cells"].values())


def test_source_hashes_xml_positions_and_report_metadata_resolve():
    row, snapshot, root = raw()
    member, body = xbrl_member(snapshot.body)
    data = normalize_filing(snapshot, row)
    for cell in [*data["cells"].values(), data["opening_equity"]]:
        assert cell["document_id"] == row["document_id"] and cell["filed_at"] == row["filed_at"]
        for fact in cell["fact_inputs"]:
            source = fact["source"]
            SourceEvidence.model_validate(source)
            assert source["raw_sha256"] == digest(snapshot.body)
            assert source["archive_member"] == member and source["member_sha256"] == digest(body)
            child = root[int(source["xml_local_path"].split("[")[1][:-1]) - 1]
            assert child.text == fact["value"] and child.tag == source["expanded_element"]
            assert child.get("contextRef") == fact["context_id"]


def amendment(parent, suffix="B", day="2026-07-01"):
    return parent | {
        "document_id": parent["document_id"][:-1] + suffix,
        "document_type": "130",
        "parent_document_id": parent["document_id"],
        "filed_at": day + "T10:00:00+09:00",
        "filing_date": day,
    }


def test_amendment_lineage_selects_latest_whole_document():
    base = filing()
    first = amendment(base)
    second = amendment(first, "C", "2026-08-01")
    rows = select_annual_filings([second, base, first], 5)
    assert rows[0]["selected_document_id"] == second["document_id"]
    assert rows[0]["reason"] == "latest_linked_amendment"
    assert rows[0]["amendment_values_overlay"] is False
    first["withdrawal_status"] = "1"
    assert select_annual_filings([base, first], 5)[0]["selected_document_id"] == base["document_id"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_parent", "amendment_parent_not_indexed"),
        ("no_xbrl", "latest_report_has_no_xbrl"),
        ("issuer", "amendment_parent_identity_or_time_mismatch"),
        ("period", "amendment_period_mismatch"),
        ("two_roots", "multiple_annual_report_roots"),
        ("withdraw_root", "annual_root_withdrawn_or_unavailable"),
    ],
)
def test_unsafe_amendments_and_competing_reports_do_not_revert_to_old_values(change, reason):
    base = filing()
    corrected = amendment(base)
    if change == "missing_parent":
        corrected["parent_document_id"] = "S0000000"
    elif change == "no_xbrl":
        corrected["xbrl_available"] = False
    elif change == "issuer":
        corrected["edinet_code"] = "E00000"
    elif change == "period":
        corrected["report_period_start"] = "2025-01-01"
    elif change == "two_roots":
        corrected["document_type"] = "120"
    else:
        base["withdrawal_status"] = "1"
    result = select_annual_filings([base, corrected], 5)
    assert result[0]["selected_document_id"] is None and result[0]["reason"] == reason


def test_index_cache_is_private_resumable_bounded_and_preserves_source_rows(tmp_path):
    history = EdinetHistory(tmp_path)
    transport = EdinetScoringFixtures()
    result = sync_history(transport, history, "2026-06-01", "2026-06-30")
    assert not result["partial"] and len(result["indexed_dates"]) == 30
    rows, truncated = history.rows("9999.T")
    assert len(rows) == 1 and not truncated and rows[0]["source"]["raw_sha256"]
    assert history.db.path.stat().st_mode & 0o077 == 0
    assert history.coverage()["unindexed_days"] > 0

    class Interrupted(EdinetScoringFixtures):
        def get(self, url, ttl):
            if "2026-05-03" in url:
                raise AtlasError("SOURCE_UNAVAILABLE", "Fictional interruption")
            return super().get(url, ttl)

    partial = sync_history(Interrupted(), history, "2026-05-01", "2026-05-05")
    assert partial["next_date"] == "2026-05-03" and len(partial["indexed_dates"]) == 2
    assert history.rows("9999.T")[0] == rows


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-05-01", "2026-06-01"),
        ("2026-06-02", "2026-06-01"),
        ("2010-01-01", "2010-01-02"),
        ("2099-01-01", "2099-01-02"),
    ],
)
def test_index_date_bounds_are_validated_before_io(start, end):
    with pytest.raises(AtlasError):
        date_window(start, end, today=date(2026, 11, 8))


def test_cold_index_and_short_history_never_claim_complete_coverage(tmp_path):
    transport = EdinetScoringFixtures()
    cold = japan_financials(transport, "9999", 5, tmp_path, synthetic=False)
    assert cold["issuer"]["reason"] == "issuer_not_in_index"
    assert cold["source_coverage"]["daily_list_reads"] <= 7 and not cold["annual"]
    seed_history(EdinetHistory(tmp_path))
    data = japan_financials(transport, "9999", 10, tmp_path, synthetic=False)
    score = calculate_score(data, 10, None)
    assert data["history_truncated"] and len(data["annual"]) == 5
    assert (
        component(score, "sustained_roe")["reason"] == "incomplete_or_nonconsecutive_annual_history"
    )
    assert component(score, "balance_sheet")["points"] is not None
    assert (
        data["source_coverage"]["daily_list_reads"] <= 21
        and data["source_coverage"]["filing_reads"] <= 11
    )


def test_missing_private_key_fails_before_any_network(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_buffett_score("7203.T", country="JP")
    assert error.value.code == "CONFIG_REQUIRED"


def test_elapsed_budget_caps_http_timeouts_and_expires_without_source_io(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", "FictionalEdinetCredential00000001")
    observed = []

    def handler(request):
        observed.append(request.extensions["timeout"])
        return httpx.Response(200, json={"metadata": {"status": "200"}, "results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = PublicTransport(client=client, cache_path=tmp_path / "transport.sqlite3")
        with source_deadline(0.5):
            transport.get(edinet_list_url("2026-06-01"), 0)
        assert all(0 < value <= 0.5 for value in observed[0].values())
        with source_deadline(-1), pytest.raises(AtlasError) as error:
            remaining_timeout()
        assert error.value.details["reason"] == "source_deadline_exceeded"
        transport.close()


def test_compact_evidence_and_projection_keep_web_interpretation():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            {
                "dataset": "buffett_score",
                "identifier": "9998.T",
                "country": "JP",
                "count": 3,
                "response": {"view": "compact", "fields": ["score"]},
            }
        )
        row = result["records"][0]
        assert row["unevaluated_weight"] == "20" and row["missing_inputs"] == {}
        source = row["reported_inputs"]["2026-03-31:revenue"]["source"]
        assert atlas.get_evidence(source["evidence_id"])["evidence"]["xml_local_path"]
        assert result["issuer"] and result["filing_selection"] and result["source_coverage"]


def test_cli_sdk_rest_share_one_score_and_evidence_contract(tmp_path):
    query = {"dataset": "buffett_score", "identifier": "9999.T", "country": "JP", "count": 3}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "buffett",
            "9999.T",
            "--country",
            "JP",
            "--years",
            "3",
            "--provider",
            "fixture",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    observed = json.loads(proc.stdout)
    # Retrieval checkpoints vary by process state; calculations/evidence do not.
    for result in (observed, expected):
        result["source_coverage"]["progress"].pop("reused_normalized_periods")
    assert observed == expected
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        response = client.post("/v1/research", json=query)
        body = response.json()
        body["source_coverage"]["progress"].pop("reused_normalized_periods")
        assert response.status_code == 200 and body == expected


async def test_real_stdio_protocol_uses_the_same_fictional_edinet_pipeline(tmp_path):
    query = {"dataset": "buffett_score", "identifier": "9998.T", "country": "JP", "count": 3}
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool("atlas_get_research", {"query": query})
        assert not result.is_error and result.structured_content == expected


@pytest.mark.parametrize("industry", ["7050", "7100", "7150", "7200", "9990"])
def test_all_financial_industries_and_unrecognized_codes_are_excluded(tmp_path, industry):
    import io
    import zipfile

    class Classified(EdinetScoringFixtures):
        def get(self, url, ttl):
            snapshot = super().get(url, ttl)
            if url != JPX_LIST:
                return snapshot
            output = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(snapshot.body)) as original,
                zipfile.ZipFile(output, "w") as changed,
            ):
                for name in original.namelist():
                    body = original.read(name)
                    if name.endswith("sheet1.xml"):
                        body = body.replace(b">3700<", (">" + industry + "<").encode())
                    changed.writestr(name, body)
            return Snapshot(output.getvalue(), snapshot.url, CAPTURE)

    data = japan_financials(Classified(), "9999", 3, tmp_path, synthetic=True)
    assert data["business_type"] == ("unspecified" if industry == "9990" else "financial")
    assert data["source_coverage"]["filing_reads"] == 0


def test_whole_amendment_is_used_without_filling_missing_interest_from_original(tmp_path):
    from urllib.parse import urlsplit

    history = EdinetHistory(tmp_path)
    seed_history(history)
    base = filing()
    corrected = amendment(base)
    source = {
        "docID": corrected["document_id"],
        "edinetCode": corrected["edinet_code"],
        "secCode": corrected["security_code"],
        "docTypeCode": "130",
        "periodStart": corrected["report_period_start"],
        "periodEnd": corrected["report_period_end"],
        "submitDateTime": "2026-07-01 10:00",
        "xbrlFlag": "1",
        "withdrawalStatus": "0",
        "disclosureStatus": "0",
        "parentDocID": base["document_id"],
    }
    listing = Snapshot(
        canonical({"metadata": {"status": "200"}, "results": [source]}),
        edinet_list_url("2026-07-01"),
        CAPTURE,
    )
    history.ingest("2026-07-01", listing)

    class Corrected(EdinetScoringFixtures):
        def get(self, url, ttl):
            if url == listing.url:
                return listing
            if urlsplit(url).path.endswith(corrected["document_id"]):
                _, snapshot, root = raw()
                node(root, "NetSales").text = "2000000000000"
                root.remove(node(root, "InterestExpensesNOE"))
                return Snapshot(archive_xml(ET.tostring(root)), url, CAPTURE)
            return super().get(url, ttl)

    data = japan_financials(Corrected(), "9999", 3, tmp_path, synthetic=False)
    score = calculate_score(data, 3, None)
    assert data["filing_selection"][0]["reason"] == "latest_linked_amendment"
    assert score["reported_inputs"]["2026-03-31:revenue"]["value"] == "2000000000000"
    assert score["reported_inputs"]["2026-03-31:interest_expense"]["value"] is None
    assert component(score, "interest_coverage")["points"] is None
    assert component(score, "operating_margin")["points"] is not None


def test_missing_parent_with_no_period_blocks_stale_original():
    base = filing()
    corrected = amendment(base)
    corrected.update(
        parent_document_id="S0000000", report_period_start=None, report_period_end=None
    )
    choices = select_annual_filings([base, corrected], 3)
    assert choices[0]["selected_document_id"] is None
    assert choices[0]["reason"] == "unresolved_amendment_without_period"


def test_same_end_with_conflicting_annual_periods_is_one_unavailable_period():
    base = filing()
    other = base | {"document_id": "S999926Z", "report_period_start": "2025-01-01"}
    choices = select_annual_filings([base, other], 5)
    assert len(choices) == 1 and choices[0]["reason"] == "conflicting_report_periods_for_same_end"
    assert choices[0]["selected_document_id"] is None


def test_accounting_standard_changes_do_not_produce_comparable_history(tmp_path):
    data = japan_financials(EdinetScoringFixtures(), "9999", 3, tmp_path, synthetic=True)
    row, snapshot, _ = raw("9998", 2025)
    normalized = normalize_filing(snapshot, row)
    for metric, cell in normalized["cells"].items():
        data["inputs"]["2025-03-31:" + metric] = cell
    score = calculate_score(data, 3, None)
    assert component(score, "earnings_consistency")["points"] is None
    assert "incompatible_reported_concepts" in component(score, "earnings_consistency")["reason"]


def test_dei_duplicates_and_inline_scaling_are_not_interpreted():
    data = modified(lambda root: root.append(deepcopy(node(root, "AccountingStandardsDEI"))))
    assert data["reason"] is None

    def conflict(root):
        duplicate = deepcopy(node(root, "AccountingStandardsDEI"))
        duplicate.text = "IFRS"
        root.append(duplicate)

    data = modified(conflict)
    assert data["reason"] == "unsupported_or_ambiguous_accounting_standard"
    for attr, value in [("scale", "6"), ("sign", "-")]:
        data = modified(
            lambda root, attr=attr, value=value: node(root, "NetSales").set(attr, value)
        )
        assert data["cells"]["revenue"]["reason"] == "unsupported_inline_scale_or_sign"


def test_index_issuer_document_limit_is_explicit_and_never_scores_truncated_selection(tmp_path):
    transport = EdinetScoringFixtures()
    snapshot = transport.get(edinet_list_url("2026-06-26"), 0)
    payload = json.loads(snapshot.body)
    base = payload["results"][0]
    payload["results"] = [base | {"docID": f"S{i:07d}"} for i in range(257)]

    class Large(EdinetScoringFixtures):
        def get(self, url, ttl):
            if url == edinet_list_url("2026-06-26"):
                return Snapshot(canonical(payload), url, CAPTURE)
            return super().get(url, ttl)

    history = EdinetHistory(tmp_path)
    history.ingest("2026-06-26", Large().get(edinet_list_url("2026-06-26"), 0))
    rows, truncated = history.rows("9999")
    assert truncated and len(rows) == 256
    data = japan_financials(Large(), "9999", 3, tmp_path, synthetic=False)
    assert data["source_coverage"]["issuer_document_limit_reached"]
    assert calculate_score(data, 3, None)["available_weight"] == "0"


def test_history_sync_cli_and_invalid_country_are_bounded(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "edinet-index",
            "--start-date",
            "2026-06-26",
            "--end-date",
            "2026-06-26",
            "--provider",
            "fixture",
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert json.loads(proc.stdout)["indexed_dates"] == ["2026-06-26"]
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_buffett_score("9999", country="GB")


def test_withdrawn_unresolved_amendment_does_not_block_valid_annual():
    base = filing()
    withdrawn = amendment(base)
    withdrawn.update(
        parent_document_id="S0000000",
        report_period_start=None,
        report_period_end=None,
        withdrawal_status="1",
    )
    choices = select_annual_filings([base, withdrawn], 3)
    assert choices[0]["selected_document_id"] == base["document_id"]
    assert choices[1]["reason"] == "amendment_parent_not_indexed"


def test_unknown_annual_period_does_not_silently_move_scoring_to_older_reports():
    base = filing()
    unknown = base | {
        "document_id": "S999926Z",
        "report_period_start": None,
        "report_period_end": None,
    }
    choices = select_annual_filings([base, unknown], 3)
    assert choices[0]["selected_document_id"] is None
    assert choices[0]["reason"] == "unresolved_annual_report_period"
