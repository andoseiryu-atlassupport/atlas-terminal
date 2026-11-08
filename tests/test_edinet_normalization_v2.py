"""Fictional structural regressions, including the Toyota 2024–2026 failure shapes.

Issuer IDs exercise scoped rules; amounts, ZIPs and notes here are invented.
No actual captured filing is published as a fixture.
"""

import io
import json
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from test_buffett_japan import component, modified, node, quote, raw

from atlas_terminal.buffett_models import ScoreValuation
from atlas_terminal.edinet_history import EdinetHistory
from atlas_terminal.edinet_mappings import MAPPING_VERSION
from atlas_terminal.edinet_normalization import normalize_filing
from atlas_terminal.edinet_scoring_fixtures import CAPTURE, EdinetScoringFixtures, archive_xml
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_buffett import calculate_score
from atlas_terminal.research_buffett_japan import japan_financials
from atlas_terminal.research_japan_companies import xbrl_member
from atlas_terminal.util import digest

CORE = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2025-11-01/jpigp_cor"
SUMMARY = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor"


def toyota_shape(year=2026):
    row, snapshot, root = raw("9998", year)
    row.update(edinet_code="E02144", security_code="72030")
    for element in root.iter():
        if element.text:
            element.text = element.text.replace("E99998", "E02144").replace("99980", "72030")
    namespace = f"http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/{year}-03-31/01/{year}-06-26"
    return row, snapshot, root, namespace


def normalized(row, snapshot, root, extra=None):
    archive = archive_xml(ET.tostring(root))
    if extra:
        stream = io.BytesIO(archive)
        with ZipFile(stream, "a") as output:
            for name, body in extra.items():
                output.writestr("XBRL/PublicDoc/" + name, body)
        archive = stream.getvalue()
    return normalize_filing(Snapshot(archive, snapshot.url, CAPTURE), row)


@pytest.mark.parametrize("year", [2024, 2025, 2026])
@pytest.mark.parametrize(
    "tag,metric,ctx",
    [("OperatingProfitLossIFRS", "operating_income", "d"), ("AssetsIFRS", "assets", "i")],
)
def test_equal_repeats_keep_every_location_without_double_counting(year, tag, metric, ctx):
    row, snapshot, root, _ = toyota_shape(year)
    fact = node(root, tag, ctx)
    repeated = deepcopy(fact)
    repeated.text += ".000"
    repeated.set("decimals", "INF")
    root.insert(0, repeated)
    cell = normalized(row, snapshot, root)["cells"][metric]
    assert Decimal(cell["value"]) == Decimal(fact.text)
    assert len(cell["fact_inputs"]) == len(cell["source_locations"]) == 2
    assert cell["normalization"]["equivalent_duplicate_count"] == 1
    assert len({s["xml_local_path"] for s in cell["source_locations"]}) == 2
    root.remove(repeated)
    root.append(repeated)
    assert normalized(row, snapshot, root)["cells"][metric]["value"] == cell["value"]


@pytest.mark.parametrize("year", [2024, 2025, 2026])
def test_scoped_revenue_and_business_summary_are_equivalent(year):
    row, snapshot, root, namespace = toyota_shape(year)
    fact = node(root, "RevenueIFRS")
    base = normalized(row, snapshot, root)["cells"]["revenue"]
    fact.tag = "{" + namespace + "}TotalNetRevenuesIFRS"
    alternate = deepcopy(fact)
    alternate.tag = "{" + SUMMARY + "}RevenueIFRSSummaryOfBusinessResults"
    root.append(alternate)
    cell = normalized(row, snapshot, root)["cells"]["revenue"]
    assert cell["value"] == base["value"] and cell["comparability_key"] == base["comparability_key"]
    assert len(cell["fact_inputs"]) == 2


@pytest.mark.parametrize("change", ["issuer", "namespace", "year", "tag"])
def test_extensions_never_match_by_a_similar_name_or_other_issuer(change):
    row, snapshot, root, namespace = toyota_shape()
    fact = node(root, "RevenueIFRS")
    if change == "issuer":
        namespace = namespace.replace("E02144", "E00000")
    elif change == "namespace":
        namespace = namespace.replace("disclosure.edinet-fsa.go.jp", "issuer.atlas.invalid")
    elif change == "year":
        namespace = namespace.replace("2026-03-31", "2027-03-31")
    fact.tag = (
        "{"
        + namespace
        + "}"
        + ("SegmentTotalNetRevenuesIFRS" if change == "tag" else "TotalNetRevenuesIFRS")
    )
    assert normalized(row, snapshot, root)["cells"]["revenue"]["value"] is None


@pytest.mark.parametrize("mode", ["conflict", "nil", "different_scope", "same_context_alias"])
def test_duplicate_semantics_not_xml_order_or_context_id(mode):
    def change(root):
        duplicate = deepcopy(node(root, "NetSales"))
        if mode == "conflict":
            duplicate.text = "1"
        elif mode == "nil":
            duplicate.text = None
            duplicate.set("{http://www.w3.org/2001/XMLSchema-instance}nil", "true")
        else:
            ctx = deepcopy(next(n for n in root if n.get("id") == "d"))
            ctx.set("id", "alias")
            if mode == "different_scope":
                namespace = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
                root.set("xmlns:dim", namespace)
                scenario = ET.SubElement(ctx, "{http://www.xbrl.org/2003/instance}scenario")
                ET.SubElement(
                    scenario,
                    "{http://xbrl.org/2006/xbrldi}explicitMember",
                    dimension="dim:ConsolidatedAndNonConsolidatedAxis",
                ).text = "dim:ConsolidatedMember"
            root.append(ctx)
            duplicate.set("contextRef", "alias")
        root.append(duplicate)

    cell = modified(change)["cells"]["revenue"]
    assert (cell["value"] is not None) == (mode == "same_context_alias")
    if mode != "same_context_alias":
        assert cell["reason"]


def extensions(namespace, second=True, bad_type=False):
    names = [
        ("FixedPurchase", "有形固定資産の購入＜賃貸資産を除く＞"),
        ("RentalPurchase", "賃貸資産の購入"),
    ]
    if not second:
        names = names[:1]
    xsd = (
        '<schema xmlns="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
        f'targetNamespace="{namespace}">'
    )
    lab = '<linkbase xmlns="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink"><labelLink>'
    for name, label in names:
        kind = "sharesItemType" if bad_type else "monetaryItemType"
        xsd += (
            f'<element id="{name}" name="{name}" type="xbrli:{kind}" xbrli:periodType="duration"/>'
        )
        lab += (
            f'<loc xlink:label="loc{name}" xlink:href="issuer.xsd#{name}"/>'
            f'<label xlink:label="lab{name}" '
            'xlink:role="http://www.xbrl.org/2003/role/label" '
            f'xml:lang="ja">{label}</label>'
            f'<labelArc xlink:from="loc{name}" xlink:to="lab{name}"/>'
        )
    return {"issuer.xsd": xsd + "</schema>", "issuer_lab.xml": lab + "</labelLink></linkbase>"}


@pytest.mark.parametrize("mode", ["both", "missing", "bad_type", "sale", "conflicting_total"])
def test_gross_ppe_requires_both_cash_purchases_and_does_not_net_disposals(mode):
    row, snapshot, root, namespace = toyota_shape()
    original = node(root, "PurchaseOfPropertyPlantAndEquipmentInvCFIFRS")
    first, second = deepcopy(original), deepcopy(original)
    first.tag, second.tag = "{" + namespace + "}FixedPurchase", "{" + namespace + "}RentalPurchase"
    first.text, second.text = "-30000000000", "-20000000000"
    if mode == "sale":
        second.text = "20000000000"
    root.extend([first, second])
    if mode != "conflicting_total":
        root.remove(original)
    else:
        original.text = "-1"
    cell = normalized(
        row, snapshot, root, extensions(namespace, mode != "missing", mode == "bad_type")
    )["cells"]["capital_expenditure"]
    if mode == "both":
        assert cell["value"] == "50000000000"
        assert cell["normalization"]["formula"] == "-(ppe_excluding_rental_assets + rental_assets)"
        assert all(fact["mapping_evidence"] for fact in cell["fact_inputs"])
    else:
        assert cell["value"] is None and cell["reason"]


def add_notes(root, year, *, amount="10", unit="百万円", repeat=False):
    text = (
        f"<p>株式報酬に係る費用は、前連結会計年度が9{unit}、当連結会計年度が{amount}{unit}であり、"
        "連結損益計算書の「販売費及び一般管理費」に含まれています。</p>"
        f"<p>{year - 1}年3月31日および{year}年3月31日に終了した各1年間における支払利息は、"
        f"それぞれ19{unit}および20{unit}です。なお、金融事業に関連する支払利息は、"
        "連結損益計算書の「金融事業に係る金融費用」に含めて計上しています。</p>"
    )
    block = ET.SubElement(
        root, "{" + SUMMARY + "}FictionalConsolidatedNotesTextBlock", contextRef="d"
    )
    block.text = text
    if repeat:
        root.append(deepcopy(block))


@pytest.mark.parametrize("year", [2024, 2025, 2026])
def test_current_expense_notes_are_separate_evidence_with_exact_conversion(year):
    row, snapshot, root, _ = toyota_shape(year)
    for tag in ("ShareBasedPaymentExpensesOpeCFIFRS", "InterestExpensesOpeCFIFRS"):
        root.remove(node(root, tag))
    add_notes(root, year, repeat=True)
    cells = normalized(row, snapshot, root)["cells"]
    for metric, expected in (
        ("stock_based_compensation", "10000000"),
        ("interest_expense", "20000000"),
    ):
        cell = cells[metric]
        assert cell["value"] == expected and cell["fact_inputs"] == []
        assert cell["evidence_type"] == "xbrl_note_text" and len(cell["note_inputs"]) == 2
        assert cell["measurement_bases"] == ["profit_loss_expense"]
        source = cell["source"]
        html = root[int(source["xml_local_path"].split("[")[1][:-1]) - 1].text
        assert digest(html.encode()) == source["text_block_sha256"]


@pytest.mark.parametrize(
    "mode", ["unit", "conflict", "retained_cash_adjustment", "grant_wording", "standalone"]
)
def test_notes_never_fill_undisclosed_expense_or_overwrite_conflicts(mode):
    row, snapshot, root, _ = toyota_shape()
    if mode != "retained_cash_adjustment":
        root.remove(node(root, "ShareBasedPaymentExpensesOpeCFIFRS"))
    add_notes(root, 2026, unit="千円" if mode == "unit" else "百万円")
    if mode == "conflict":
        add_notes(root, 2026, amount="11")
    if mode == "grant_wording":
        root[-1].text = root[-1].text.replace("株式報酬に係る費用", "株式報酬の付与額")
    if mode == "standalone":
        root[-1].set("contextRef", "NonConsolidatedUnknown")
    cell = normalized(row, snapshot, root)["cells"]["stock_based_compensation"]
    assert cell["value"] is None


def test_cold_deadline_checkpoint_resumes_missing_2024_before_cached_years(tmp_path, monkeypatch):
    import atlas_terminal.research_buffett_japan as adapter
    import atlas_terminal.source_deadline as deadlines

    now = [0.0]
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: now[0])
    monkeypatch.setitem(adapter.LIMITS, "elapsed_seconds", 20)

    class SlowCold(EdinetScoringFixtures):
        def __init__(self):
            self.cached = set()
            self.order = []

        def get(self, url, ttl):
            if "/documents/S" in url:
                self.order.append(url)
                if url not in self.cached:
                    now[0] += 8
                    self.cached.add(url)
            return super().get(url, ttl)

    transport = SlowCold()
    cold = japan_financials(transport, "9998", 3, tmp_path, synthetic=True)
    progress = cold["source_coverage"]["progress"]
    assert progress["missing_periods"] == ["2024-03-31"]
    assert cold["source_coverage"]["failures"][0]["reason"] == "source_deadline_exceeded"
    assert progress["resume"]["document_id"] == "S999824A"
    assert progress["failed_stages"] == ["filing_download"]
    transport.order.clear()
    warm = japan_financials(transport, "9998", 3, tmp_path, synthetic=True)
    assert "S999824A" in transport.order[0]
    assert warm["source_coverage"]["progress"]["reused_normalized_periods"] == [
        "2025-03-31",
        "2026-03-31",
    ]
    assert warm["source_coverage"]["progress"]["missing_periods"] == []
    assert calculate_score(warm, 3, None)["quality_score"] == "80.000000000000000000"
    assert warm["source_coverage"]["history"]["latest_amendment_guaranteed"] is False


def test_checkpoint_requires_same_source_mapping_and_filing_metadata(tmp_path):
    row, snapshot, _ = raw()
    history = EdinetHistory(tmp_path)
    data = normalize_filing(snapshot, row)
    history.save_normalized(snapshot, row, MAPPING_VERSION, data)
    assert history.normalized(row, MAPPING_VERSION, snapshot) == data
    assert history.normalized(row, "next_mapping", snapshot) is None
    assert history.normalized(row | {"filed_at": "2026-07-01"}, MAPPING_VERSION, snapshot) is None
    changed = Snapshot(snapshot.body + b" ", snapshot.url, CAPTURE)
    assert history.normalized(row, MAPPING_VERSION, changed) is None
    with history.db.connect(write=True) as conn:
        conn.execute("UPDATE normalized_filings SET body=?", (b"{}",))
    with pytest.raises(AtlasError):
        history.normalized(row, MAPPING_VERSION, snapshot)


@pytest.mark.parametrize("mode", ["valid", "currency", "year", "span", "missing", "segment_block"])
def test_note_table_keeps_physical_cells_units_and_only_gross_cash_purchase_rows(mode):
    row, snapshot, root, _ = toyota_shape()
    root.remove(node(root, "PurchaseOfPropertyPlantAndEquipmentInvCFIFRS"))
    html = (
        "<p>連結キャッシュ・フロー計算書 金額：百万円</p><table>"
        "<tr><td>3月31日に終了した1年間</td><td>2025年</td><td>2026年</td></tr>"
        "<tr><td>有形固定資産の購入＜賃貸資産を除く＞</td><td>△20</td><td>△30</td></tr>"
        "<tr><td>賃貸資産の購入</td><td>△10</td><td>△20</td></tr>"
        "<tr><td>賃貸資産の売却</td><td>100</td><td>200</td></tr>"
        "<tr><td>無形資産の取得</td><td>△100</td><td>△200</td></tr></table>"
    )
    if mode == "currency":
        html = html.replace("百万円", "百万米ドル")
    elif mode == "year":
        html = html.replace("2026年", "2027年")
    elif mode == "span":
        html = html.replace("<td>△30", '<td colspan="2">△30')
    elif mode == "missing":
        html = html.replace("賃貸資産の購入", "賃貸資産の売却")
    name = (
        "SegmentCashFlowTextBlock"
        if mode == "segment_block"
        else "ConsolidatedStatementOfCashFlowsIFRSTextBlock"
    )
    ET.SubElement(root, "{" + SUMMARY + "}" + name, contextRef="d").text = html
    cell = normalized(row, snapshot, root)["cells"]["capital_expenditure"]
    if mode != "valid":
        assert cell["value"] is None
        return
    assert cell["value"] == "50000000" and cell["evidence_type"] == "xbrl_note_table"
    operands = cell["note_inputs"][0]["operands"]
    assert [item["source"]["row_index"] for item in operands] == [2, 3]
    assert all(item["source"]["cell_index"] == 3 for item in operands)
    assert all("△" in item["raw_value"] for item in operands)


def test_verified_tag_change_compares_across_years_but_changed_definition_does_not(tmp_path):
    class Changed(EdinetScoringFixtures):
        def get(self, url, ttl):
            snapshot = super().get(url, ttl)
            if "S999825A" in url:
                root = ET.fromstring(xbrl_member(snapshot.body)[1])
                root.set("xmlns:iso4217", "http://www.xbrl.org/2003/iso4217")
                node(root, "RevenueIFRS").tag = (
                    "{" + SUMMARY + "}RevenueIFRSSummaryOfBusinessResults"
                )
                return Snapshot(archive_xml(ET.tostring(root)), snapshot.url, CAPTURE)
            return snapshot

    data = japan_financials(Changed(), "9998", 3, tmp_path, synthetic=True)
    assert calculate_score(data, 3, None)["quality_score"] == "80.000000000000000000"
    data["inputs"]["2025-03-31:revenue"]["comparability_key"] += ":changed_definition"
    assert component(calculate_score(data, 3, None), "operating_margin")["points"] is None


def test_equal_parent_equity_components_are_not_added_twice():
    data = modified(lambda root: root.append(deepcopy(node(root, "ShareholdersEquity", "i"))))
    cell = data["cells"]["stockholders_equity"]
    assert cell["value"] == "1100000000000" and len(cell["fact_inputs"]) == 3
    assert len(cell["normalization"]["operands"]) == 2


@pytest.mark.parametrize("scope", ["standalone", "segment"])
def test_whole_group_fact_is_not_confused_with_other_scopes(scope):
    def change(root):
        namespace = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
        root.set("xmlns:dim", namespace)
        context = deepcopy(next(n for n in root if n.get("id") == "d"))
        context.set("id", "other")
        scenario = ET.SubElement(context, "{http://www.xbrl.org/2003/instance}scenario")
        axis = (
            "ConsolidatedAndNonConsolidatedAxis"
            if scope == "standalone"
            else "BusinessSegmentsAxis"
        )
        member = "NonConsolidatedMember" if scope == "standalone" else "AutomotiveMember"
        ET.SubElement(
            scenario, "{http://xbrl.org/2006/xbrldi}explicitMember", dimension="dim:" + axis
        ).text = "dim:" + member
        root.append(context)
        fact = deepcopy(node(root, "NetSales"))
        fact.set("contextRef", "other")
        fact.text = "1"
        root.append(fact)

    cell = modified(change)["cells"]["revenue"]
    assert cell["value"] == "1000000000000" and len(cell["fact_inputs"]) == 1


def test_same_day_price_needs_time_after_publication_and_keeps_assumptions(tmp_path):
    data = japan_financials(EdinetScoringFixtures(), "9998", 3, tmp_path, synthetic=True)
    valuation = quote() | {"observed_on": "2026-06-26"}
    with pytest.raises(AtlasError):
        calculate_score(data, 3, ScoreValuation(**valuation))
    with pytest.raises(AtlasError):
        calculate_score(
            data, 3, ScoreValuation(**(valuation | {"observed_at": "2026-06-26T00:00:00+09:00"}))
        )
    result = calculate_score(
        data, 3, ScoreValuation(**(valuation | {"observed_at": "2026-06-26T23:00:00+09:00"}))
    )
    assert result["quality_score"] == "80.000000000000000000"
    assert result["score"] == "100.000000000000000000"
    assert result["model_version"] == "buffett_quantitative_jp_disclosures_v2"
    assert component(result, "margin_of_safety")["max_points"] == "20"


def test_explicit_quote_timestamp_uses_absolute_time_across_midnight(tmp_path):
    data = japan_financials(EdinetScoringFixtures(), "9998", 3, tmp_path, synthetic=True)
    data["annual"][0]["available_at"] = "2026-06-26T07:00:00+09:00"
    valuation = quote() | {"observed_on": "2026-06-25", "observed_at": "2026-06-25T23:00:00Z"}
    assert calculate_score(data, 3, ScoreValuation(**valuation))["score"] is not None


def test_missing_key_live_verifier_reports_blocked_without_empty_success(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path

    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    root = Path(__file__).resolve().parents[1]
    (root / "build").mkdir(exist_ok=True)
    # The verifier intentionally allows private build/ output only.
    with tempfile.TemporaryDirectory(dir=root / "build") as directory:
        output = Path(directory) / "audit"
        process = subprocess.run(
            [
                sys.executable,
                "scripts/verify_edinet_score_live.py",
                "--allow-network",
                "--identifier",
                "7203.T",
                "--output",
                str(output),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert process.returncode == 2
        report = json.loads((output / "report.json").read_text())
        assert report["status"] == "BLOCKED" and report["error_code"] == "CONFIG_REQUIRED"
        assert report["passes"] == [] and list((output / "raw").iterdir()) == []
