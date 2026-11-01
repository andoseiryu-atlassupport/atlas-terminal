"""Filing identity, scoped XBRL, visible source text and exact trailing windows."""

import json
from copy import deepcopy
from datetime import date
from importlib.resources import files

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.filing_html import html_records
from atlas_terminal.providers import Filing, SECProvider, Snapshot
from atlas_terminal.research import ResearchSecFixtures
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_trailing import derived_record, ratio, trailing_record
from atlas_terminal.sec_filing_sources import instance_facts, segment_rows

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
ACCESSION = "9999999999-26-000001"
FILING = Filing(
    ACCESSION,
    "10-K",
    "2026-02-15",
    "2025-12-31",
    "2026-02-15T16:30:00Z",
    "atlasdemo-20251231.htm",
    "https://fixtures.atlas.invalid/filing",
    "filing",
)


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def snapshot(name="atlasdemo-20251231_htm.xml", body=None):
    return Snapshot(
        body if body is not None else RESOURCE.joinpath(name).read_bytes(),
        "https://fixtures.atlas.invalid/research/" + name,
        "2026-10-20T00:00:00Z",
    )


def query(dataset="filing_facts", **extra):
    return {"dataset": dataset, "identifier": "ATLASDEMO", "accession": ACCESSION, **extra}


def facts(body=None):
    return instance_facts(snapshot(body=body), {"cik": "9999999999"}, FILING)


def test_scoped_numeric_facts_preserve_periods_dimensions_units_nils_and_original_bytes():
    rows = facts()
    assert len(rows) == 8
    business, crossed = rows[:2]
    assert business["value"] == "1200000" and business["decimals"] == "-3"
    assert business["period"] == {"kind": "duration", "start": "2025-01-01", "end": "2025-12-31"}
    assert (
        crossed["context"]["dimensions"][1]["expanded_dimension"]
        == "{http://fasb.org/srt/2025}StatementGeographicalAxis"
    )
    assert rows[4]["value"] == "100000" and rows[4]["unit"] == "shares"
    assert rows[5]["unit_definition"]["denominator"] == [
        "{http://www.xbrl.org/2003/instance}shares"
    ]
    assert rows[6]["context"]["dimensions"][0]["typed_xml"].endswith("CustomerCode>")
    assert rows[7]["value"] is None and rows[7]["status"] == "unavailable"
    for row in rows:
        assert row["accession"] == ACCESSION and row["source"]["raw_sha256"] == snapshot().sha256
        assert row["source"]["xml_local_path"].startswith("/xbrl/*[")


@pytest.mark.parametrize(
    "before,after",
    [
        (b">9999999999</identifier>", b">9999999998</identifier>"),
        (b'contextRef="business"', b'contextRef="absent"'),
        (b'unitRef="USD"', b'unitRef="absent"'),
        (b'decimals="-3"', b'decimals="1.5"'),
        (b'decimals="-3"', b'decimals="-3" precision="3"'),
        (b">1200000</us-gaap:Revenues>", b">NaN</us-gaap:Revenues>"),
        (b">1200000</us-gaap:Revenues>", b">1e99999</us-gaap:Revenues>"),
        (
            b'xsi:nil="true" decimals="INF"/>',
            b'xsi:nil="true" decimals="INF">1</demo:UnreportedMetric>',
        ),
        (b"<instant>2025-12-31</instant>", b"<instant>2025-02-30</instant>"),
        (b"<endDate>2025-12-31</endDate>", b"<endDate>2024-12-31</endDate>"),
        (b'dimension="us-gaap:StatementBusinessSegmentsAxis"', b'dimension="unknown:Axis"'),
        (b'<unit id="shares">', b'<unit id="USD">'),
        (b'<context id="typed">', b'<context id="business">'),
        (b">10-K</dei:DocumentType>", b">10-Q</dei:DocumentType>"),
        (b">2025-12-31</dei:DocumentPeriodEndDate>", b">2024-12-31</dei:DocumentPeriodEndDate>"),
        (b">9999999999</dei:EntityCentralIndexKey>", b">9999999998</dei:EntityCentralIndexKey>"),
        (b"<xbrl xmlns=", b'<!DOCTYPE xbrl [<!ENTITY secret "x">]><xbrl xmlns='),
    ],
)
def test_malformed_or_cross_issuer_xbrl_fails_closed(before, after):
    raw = snapshot().body
    assert before in raw
    with pytest.raises(AtlasError):
        facts(raw.replace(before, after, 1))


def test_namespace_rebinding_cannot_turn_an_issuer_axis_or_unit_into_a_standard_one():
    raw = snapshot().body.replace(
        b'<us-gaap:Revenues contextRef="business"',
        b'<us-gaap:Revenues xmlns:us-gaap="https://fixtures.atlas.invalid/spoof" '
        b'contextRef="business"',
        1,
    )
    rows = facts(raw)
    assert rows[0]["expanded_concept"].startswith("{https://fixtures.atlas.invalid/spoof}")
    assert len(segment_rows(rows, ["revenue"], FILING)) == 1
    raw = snapshot().body.replace(
        b"<measure>iso4217:USD</measure>",
        b'<measure xmlns:iso4217="https://fixtures.atlas.invalid/unit">iso4217:USD</measure>',
    )
    rows = facts(raw)
    assert rows[0]["unit"] == "source_defined"
    assert segment_rows(rows, ["revenue"], FILING) == []


def test_segments_keep_crossed_axes_and_conflicting_duplicates_without_adding_overlapping_totals():
    rows = facts()
    result = segment_rows(rows, ["revenue", "operating_income"], FILING)
    assert len(result) == 3
    assert len(result[1]["segments"]) == 2
    conflict = deepcopy(rows[0])
    conflict["value"] = "100"
    result = segment_rows([rows[0], deepcopy(rows[0]), conflict], ["revenue"], FILING)
    assert len(result) == 1 and result[0]["status"] == "ambiguous"
    assert result[0]["value"] is None and result[0]["duplicate_count"] == 3
    assert len(result[0]["fact_sources"]) == 3


@pytest.mark.parametrize(
    "extra",
    [
        {"accession": None},
        {"document_name": "../outside.xml"},
        {"document_name": "https://example.com/file.xml"},
        {"metrics": ["eps_basic"]},
    ],
)
def test_invalid_filing_selections_fail_before_a_provider_is_created(extra):
    dataset = "segment_financials" if "metrics" in extra else "filing_facts"
    with pytest.raises(AtlasError):
        parse_research(query(dataset, **extra))


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["directory"].update(name="/Archives/edgar/data/123/elsewhere"),
        lambda raw: raw["directory"]["item"].append({"name": "../outside.xml"}),
        lambda raw: raw["directory"]["item"].append(raw["directory"]["item"][0]),
    ],
)
def test_directory_identity_and_safe_unique_member_names_are_required(change):
    raw = json.loads(RESOURCE.joinpath("filing_index.json").read_bytes())
    change(raw)
    with Atlas(
        "fixture",
        research_source=SECProvider(
            ResearchSecFixtures({"filing_index.json": json.dumps(raw).encode()})
        ),
    ) as atlas:
        with pytest.raises(AtlasError):
            atlas.get_research(query("filing_documents"))


def test_explicit_document_selection_is_limited_to_the_issuers_published_directory():
    with Atlas("fixture") as atlas:
        with pytest.raises(AtlasError, match="exact member"):
            atlas.get_research(query(document_name="absent.xml"))
        with pytest.raises(AtlasError, match="selected issuer"):
            atlas.get_research(query(accession="1234567890-26-000001"))
        release = atlas.get_research(
            query("filing_sections", document_name="earnings.htm", section="document")
        )
    assert "expects next-year revenue" in release["records"][0]["text"]
    assert all("guidance" not in row for row in release["records"])


def test_item_occurrences_include_the_table_of_contents_and_do_not_silently_choose_one():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("filing_sections", section="item_7"))
    assert [r["occurrence"] for r in result["records"]] == [1, 2]
    assert "Table of contents" in result["records"][0]["text"]
    assert "Reported revenue" in result["records"][1]["text"]
    raw = RESOURCE.joinpath("atlasdemo-20251231.htm").read_text()
    for row in result["records"]:
        source = row["source"]
        selected = raw[source["html_start_char"] : source["html_end_char"]]
        assert "Item 7" in selected


def test_visible_document_excludes_scripts_hidden_content_and_retains_table_text():
    q = ResearchQuery(**query("filing_sections", section="document"))
    rows = html_records(snapshot("atlasdemo-20251231.htm"), q, FILING)
    text = " ".join(r["text"] for r in rows)
    assert "Hidden draft" not in text and "Untrusted executable" not in text
    assert "1,200,000" in text


def test_table_cells_preserve_source_order_spans_and_exact_original_html_coordinates():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query("filing_tables", table_index=1))
    assert len(result["records"]) == 7
    raw = RESOURCE.joinpath("atlasdemo-20251231.htm").read_text()
    for row in result["records"]:
        source = row["source"]
        selected = raw[source["html_start_char"] : source["html_end_char"]]
        assert selected.startswith(("<td", "<th")) and selected.endswith(("</td>", "</th>"))
        assert row["text"] in selected
    assert result["records"][1]["colspan"] == 2
    assert result["records"][2]["rowspan"] == 2
    assert result["records"][5]["cell_index"] == 1


def test_nested_table_caption_and_cells_do_not_leak_into_the_outer_reported_cell():
    body = (
        b"<table><tr><td>Outer<table><caption>Nested caption</caption>"
        b"<tr><td>Inner</td></tr></table>Tail</td></tr></table>"
    )
    rows = html_records(
        snapshot("document.htm", body), ResearchQuery(**query("filing_tables")), FILING
    )
    assert [(r["table_index"], r["text"]) for r in rows] == [(1, "OuterTail"), (2, "Inner")]


def test_html_source_envelope_covers_original_entities_and_nesting_is_bounded():
    body = b"<p>Item 1. Business</p><p>Research &amp; development</p>"
    rows = html_records(
        snapshot("document.htm", body),
        ResearchQuery(**query("filing_sections", section="document")),
        FILING,
    )
    source = rows[0]["source"]
    assert b"&amp;" in body[source["html_start_char"] : source["html_end_char"]]
    assert "Research & development" in rows[0]["text"]
    with pytest.raises(AtlasError, match="nesting"):
        html_records(
            snapshot("document.htm", b"<div>" * 1025),
            ResearchQuery(**query("filing_sections")),
            FILING,
        )


@pytest.mark.parametrize(
    "body",
    [
        b"<table><tr><td>Unclosed",
        b'<table><tr><td rowspan="0">x</td></tr></table>',
        b"<table><td>no row</td></table>",
        b"<p>invalid\x00</p>",
        b"\xff",
    ],
)
def test_unsupported_html_fails_without_lossy_text_or_guessed_table_layout(body):
    with pytest.raises(AtlasError):
        html_records(
            snapshot("document.htm", body), ResearchQuery(**query("filing_tables")), FILING
        )


def operand(start, end, value, *, metric="revenue", tag="Revenues", unit="USD", status="reported"):
    return {
        "metric": metric,
        "period": {"kind": "duration", "start": start, "end": end},
        "value": value,
        "unit": unit,
        "status": status,
        "source": {"taxonomy": "us-gaap", "tag": tag},
    }


def test_trailing_identity_keeps_three_actual_reported_operands_and_does_not_emit_a_synthetic_q4():
    annual = operand("2024-01-01", "2024-12-31", "100.10")
    current = operand("2025-01-01", "2025-06-30", "65.25")
    prior = operand("2024-01-01", "2024-06-30", "40.05")
    row = trailing_record("revenue", "2025-06-30", [annual], [], [current, prior])
    assert row["value"] == "125.30" and row["period_type"] == "ttm"
    assert row["period"] == {"kind": "duration", "start": "2024-07-01", "end": "2025-06-30"}
    assert [r["role"] for r in row["inputs"]] == ["previous_annual", "current_ytd", "prior_ytd"]
    assert row["method"] == "annual_plus_ytd_minus_prior_ytd"
    assert all(
        r["period"]["end"] != "2024-12-31" or r["period"]["start"] == "2024-01-01"
        for r in row["inputs"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"source": {"taxonomy": "us-gaap", "tag": "SalesRevenueNet"}},
        {"unit": "EUR"},
        {"value": None},
        {"status": "ambiguous"},
        {"period": {"kind": "duration", "start": "2024-01-02", "end": "2024-06-30"}},
        {"period": {"kind": "duration", "start": "2023-01-01", "end": "2023-06-30"}},
    ],
)
def test_trailing_identity_rejects_incompatible_or_missing_operands(change):
    a = operand("2024-01-01", "2024-12-31", "100")
    c = operand("2025-01-01", "2025-06-30", "65")
    p = operand("2024-01-01", "2024-06-30", "40") | change
    assert trailing_record("revenue", "2025-06-30", [a], [], [c, p])["value"] is None


def test_four_quarters_require_exact_contiguity_and_the_same_concept():
    quarters = [
        operand(s, e, "0.1")
        for s, e in [
            ("2024-07-01", "2024-09-30"),
            ("2024-10-01", "2024-12-31"),
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
        ]
    ]
    row = trailing_record("revenue", "2025-06-30", [], quarters, [])
    assert row["value"] == "0.4" and row["method"] == "four_reported_quarters"
    quarters[0]["period"]["end"] = "2024-09-29"
    assert trailing_record("revenue", "2025-06-30", [], quarters, [])["value"] is None


def test_53_week_annual_source_is_accepted_but_a_missing_target_window_never_falls_back():
    a = operand("2024-01-01", "2025-01-05", "123.00")
    assert (
        date.fromisoformat(a["period"]["end"]) - date.fromisoformat(a["period"]["start"])
    ).days == 370
    assert trailing_record("revenue", "2025-01-05", [a], [], [])["value"] == "123.00"
    assert trailing_record("revenue", "2025-06-30", [a], [], [])["value"] is None


def test_derived_free_cash_flow_and_margins_have_compatible_periods_and_explicit_rounding():
    flows = {
        name: operand("2025-01-01", "2025-12-31", value, metric=name)
        for name, value in [
            ("operating_cash_flow", "0.30"),
            ("capital_expenditure", "0.10"),
            ("revenue", "3"),
        ]
    }
    row = derived_record("free_cash_flow_margin", "2025-12-31", flows)
    assert row["value"] == "0.066666666666666667" and row["rounding"] == "ROUND_HALF_EVEN"
    assert derived_record("free_cash_flow", "2025-12-31", flows)["value"] == "0.20"
    flows["capital_expenditure"]["value"] = "-0.10"
    assert derived_record("free_cash_flow", "2025-12-31", flows)["value"] is None
    assert ratio("1", "8") == "0.125000000000000000"


def test_service_uses_one_frozen_financial_snapshot_and_keeps_missing_trailing_targets_explicit(
    monkeypatch,
):
    source = SECProvider(ResearchSecFixtures())
    original = source.financial_inputs
    calls = []

    def frozen(identifier):
        calls.append(identifier)
        return original(identifier)

    monkeypatch.setattr(source, "financial_inputs", frozen)
    with Atlas("fixture", research_source=source) as atlas:
        result = atlas.get_research(
            {
                "dataset": "trailing_financials",
                "identifier": "ATLASDEMO",
                "metrics": ["revenue"],
                "count": 4,
                "allow_partial": True,
            }
        )
        with pytest.raises(AtlasError, match="incomplete"):
            atlas.get_research(
                {"dataset": "trailing_financials", "identifier": "ATLASDEMO", "count": 1}
            )
    assert len(calls) == 2
    annual = next(r for r in result["records"] if r["period"]["end"] == "2025-12-31")
    assert annual["value"] == "150000000" and annual["method"] == "reported_annual"
    assert result["records"][0]["value"] is None and result["coverage"]["partial"]
    assert annual["inputs"][0]["source"]["json_pointer"].startswith("/facts/")


def test_compact_projection_retains_units_contexts_periods_spans_and_resolvable_evidence():
    with Atlas("fixture") as atlas:
        for dataset, fields in [("filing_facts", ["value"]), ("filing_tables", ["text"])]:
            result = atlas.get_research(
                query(dataset, response={"view": "compact", "fields": fields, "page_size": 2})
            )
            evidence = atlas.get_evidence(result["records"][0]["source"]["evidence_id"])
            assert evidence["evidence"]["raw_sha256"]
            if dataset == "filing_facts":
                assert result["records"][0]["context"] and result["records"][0]["unit_definition"]
            else:
                assert result["records"][1]["colspan"] == 2
