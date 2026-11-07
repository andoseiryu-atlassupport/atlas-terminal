"""Fictional JPX-shaped data: exact units, report identity, arithmetic and interfaces."""

import io
import json
import re
import subprocess
import sys
from copy import deepcopy
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pypdf import PdfReader, PdfWriter, Transformation

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.margin_sources import (
    MARGIN_INDEX,
    margin_identity,
    margin_report_url,
    margin_reports,
)
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_margin import (
    FIELDS,
    ISSUE,
    cell_value,
    extract_margin_pages,
    parse_issue,
)
from atlas_terminal.research_views import SourceEvidence
from atlas_terminal.server import create_app

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DAY = "2026-11-05"
QUERY = {"dataset": "margin_balances", "identifier": "7203.T"}


def source():
    return PublicFixtures().get(margin_report_url(DAY), 0)


def pages():
    return deepcopy(extract_margin_pages(source().body))


def parse(data=None, identifier="7203.T", snapshot=None):
    return parse_issue(snapshot or source(), identifier, DAY, data or pages())[0]


def change_cell(data, field, value, *, amount=False):
    lines = data[0]["text"].splitlines()
    for index, line in enumerate(lines):
        match = ISSUE.search(line)
        if not match or match["code"] != "72030" or bool(match["amount"]) != amount:
            continue
        cells = list(re.finditer(r"▲ ?\S+|\S+", line[match.end() :]))
        cell = cells[FIELDS.index(field)]
        start, end = match.end() + cell.start(), match.end() + cell.end()
        lines[index] = line[:start] + value + line[end:]
        data[0]["text"] = "\n".join(lines)
        return
    raise AssertionError("Fictional row is missing")


def test_exact_units_components_money_and_share_ratio():
    row = parse()
    assert row["source_security_code"] == "72030"
    assert row["date"] == DAY and row["publication_date"] == "2026-11-06"
    assert row["published_at"] is None and row["frequency"] == "daily"
    assert row["shares"]["buy_balance"] == "1600"  # B lot prefix is not a multiplier.
    assert row["shares"]["sell_balance"] == "400"
    assert row["shares"]["general_buy_balance"] == "600"
    assert row["shares"]["standardized_buy_balance"] == "1000"
    assert row["amount_jpy"]["buy_balance"] == "12000"
    assert row["margin_ratio"]["value"] == "4.000000000000000000"  # Not 12000 / 2500.
    assert row["margin_ratio"]["formula"] == "buy_balance_shares / sell_balance_shares"
    assert row["margin_ratio"]["inputs"]["sell_balance"]["value"] == "400"


def test_every_reported_cell_resolves_to_exact_pdf_layout_text():
    data, snapshot = pages(), source()
    row = parse(data)
    for kind in ("shares", "amount_jpy"):
        for field, locator in row[kind]["field_sources"].items():
            SourceEvidence.model_validate(locator)
            assert locator["raw_sha256"] == snapshot.sha256
            assert locator["raw_source_url"] == snapshot.url
            assert locator["retrieved_at"] == snapshot.retrieved_at
            line = data[locator["pdf_page"] - 1]["text"].splitlines()[locator["text_line"] - 1]
            text = line[locator["text_column_start"] - 1 : locator["text_column_end"]]
            assert text == locator["reported_text"]
            assert cell_value(text, field, amount=kind == "amount_jpy") == row[kind][field]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", "0"),
        ("1,234", "1234"),
        ("1234", "1234"),
        ("▲ 2,300", "-2300"),
        ("▲2,300", "-2300"),
        ("-80", "-80"),
        ("-", None),
        ("*", None),
    ],
)
def test_exact_signed_integer_cells(value, expected):
    assert cell_value(value, "buy_change", amount=False) == expected


@pytest.mark.parametrize("value", ["12,34", "1 234", "33,3 00", "1.0", "1e3", "NaN", "", "+4"])
def test_ambiguous_or_noninteger_cells_fail(value):
    with pytest.raises(AtlasError, match="supported exact integer"):
        cell_value(value, "buy_balance", amount=False)


@pytest.mark.parametrize("field", ["sell_balance", "sell_change", "buy_balance", "buy_change"])
@pytest.mark.parametrize("amount", [False, True])
def test_total_component_discrepancies_fail(field, amount):
    data = pages()
    change_cell(data, field, "999", amount=amount)
    with pytest.raises(AtlasError, match="differs from its margin components"):
        parse(data)


def test_missing_reported_total_is_not_derived_from_components():
    data = pages()
    change_cell(data, "buy_balance", "-")
    row = parse(data)
    assert row["shares"]["buy_balance"] is None
    assert row["shares"]["general_buy_balance"] == "600"
    assert row["margin_ratio"]["value"] is None
    assert row["margin_ratio"]["unavailable_reason"] == "missing_reported_share_balance"


def test_zero_sell_balance_preserves_balances_with_undefined_ratio():
    with Atlas("fixture") as atlas:
        result = atlas.get_margin_status("1234.T")
    row = result["records"][0]
    assert row["shares"]["sell_balance"] == "0"
    assert row["shares"]["buy_balance"] == "1600"
    assert row["margin_ratio"]["value"] is None
    assert row["margin_ratio"]["unavailable_reason"] == "zero_sell_balance"
    assert not result["coverage"]["partial"]


@pytest.mark.parametrize(
    "identifier,code", [("7203.T", "72030"), ("72031.T", "72031"), ("130A.T", "130A0")]
)
def test_exact_five_character_issue_mapping(identifier, code):
    row = parse(identifier=identifier)
    assert row["source_security_code"] == code


@pytest.mark.parametrize(
    "old,new",
    [
        ("Unit: 1 share, 1 JPY", "Unit: 100 shares, 1000 JPY"),
        ("2026/11/5 application based", "2026/11/4 application based"),
        ("2026/11/6", "2026/11/4"),
        ("2026/11/6", "2026/12/1"),
        ("Standardized", "Unknown column"),
    ],
)
def test_header_unit_date_and_definition_changes_fail(old, new):
    data = pages()
    data[0]["text"] = data[0]["text"].replace(old, new)
    with pytest.raises(AtlasError):
        parse(data)


def test_mixed_page_dates_and_duplicate_issuer_rows_fail():
    data = pages()
    second = deepcopy(data[0])
    second["page"] = 2
    data.append(second)
    with pytest.raises(AtlasError, match="Duplicate selected issuer"):
        parse(data)
    second["text"] = "\n".join(second["text"].splitlines()[:6]).replace("2026/11/6", "2026/11/7")
    with pytest.raises(AtlasError, match="mixes publication dates"):
        parse(data)


def test_missing_amount_row_and_different_isin_fail():
    data = pages()
    lines = data[0]["text"].splitlines()
    target = next(i for i, line in enumerate(lines) if "72030" in line and "Val." in line)
    lines[target] = lines[target].replace("JP0000000001", "JP0000000999")
    data[0]["text"] = "\n".join(lines)
    with pytest.raises(AtlasError, match="consistent identity"):
        parse(data)
    lines.pop(target)
    data[0]["text"] = "\n".join(lines)
    with pytest.raises(AtlasError, match="consistent identity"):
        parse(data)


def test_split_digits_are_not_repaired_and_negative_balances_fail():
    data = pages()
    change_cell(data, "buy_balance", "1,6 00")
    with pytest.raises(AtlasError):
        parse(data)
    data = pages()
    change_cell(data, "buy_balance", "-1,600")
    with pytest.raises(AtlasError, match="numeric contract"):
        parse(data)


def test_no_issue_or_nearest_date_substitution():
    with Atlas("fixture") as atlas:
        with pytest.raises(AtlasError) as absent:
            atlas.get_margin_status("9999.T")
        assert absent.value.code == "INCOMPLETE_COVERAGE"
        with pytest.raises(AtlasError) as dated:
            atlas.get_margin_status("7203.T", observation_date="2026-11-04")
        assert dated.value.code == "INCOMPLETE_COVERAGE"
        assert dated.value.details["available_application_dates"] == [DAY]


def test_exact_previous_linked_report_is_selected_without_requesting_latest_pdf():
    listing = PublicFixtures().get(MARGIN_INDEX, 0)
    previous_row = (
        '<tr><th>2026年11月4日申込分</th><td><a href="'
        + margin_report_url("2026-11-04")
        + '">Fictional previous report</a></td></tr>'
    )
    body = listing.body.replace(b"</table>", previous_row.encode() + b"</table>")
    previous = source().body.replace(b"2026/11/5", b"2026/11/4").replace(b"2026/11/6", b"2026/11/5")
    requested = []

    class Linked:
        synthetic = True

        def get(self, url, ttl):
            requested.append(url)
            if url == MARGIN_INDEX:
                return Snapshot(body, url, listing.retrieved_at)
            assert url == margin_report_url("2026-11-04")
            return Snapshot(previous, url, source().retrieved_at)

        def close(self):
            pass

    with Atlas("fixture") as atlas:
        atlas._research.public = Linked()
        result = atlas.get_margin_status("7203.T", observation_date="2026-11-04")
    row = result["records"][0]
    assert row["date"] == "2026-11-04" and row["publication_date"] == DAY
    assert result["coverage"]["available_application_dates"] == [DAY, "2026-11-04"]
    assert requested == [MARGIN_INDEX, margin_report_url("2026-11-04")]


def test_null_cells_mark_partial_and_fail_strict_quality_without_fabricating_totals(monkeypatch):
    data = pages()
    change_cell(data, "buy_balance", "-")
    monkeypatch.setattr("atlas_terminal.research_margin.extract_margin_pages", lambda body: data)
    with Atlas("fixture") as atlas:
        result = atlas.get_margin_status("7203.T")
        assert result["records"][0]["shares"]["buy_balance"] is None
        assert result["coverage"]["partial"]
        assert result["coverage"]["missing_fields"] == ["shares.buy_balance"]
        with pytest.raises(AtlasError) as absent:
            atlas.get_research(QUERY | {"requirements": {"allow_missing": False}})
        assert absent.value.code == "QUALITY_NOT_MET"


def test_nested_ratio_obeys_derived_quality_requirement():
    with Atlas("fixture") as atlas, pytest.raises(AtlasError) as derived:
        atlas.get_research(QUERY | {"requirements": {"allow_derived": False}})
    assert derived.value.code == "QUALITY_NOT_MET"


def test_fixed_source_identity_and_directory_date():
    assert margin_identity(MARGIN_INDEX) == "index"
    assert margin_identity(margin_report_url(DAY)) == DAY
    listing = PublicFixtures().get(MARGIN_INDEX, 0)
    assert margin_reports(listing) == [{"date": DAY, "url": margin_report_url(DAY), "text_line": 4}]
    PublicTransport.validate_body(MARGIN_INDEX, listing.body)
    PublicTransport.validate_body(margin_report_url(DAY), source().body)


@pytest.mark.parametrize("suffix", ["?query=1", "#fragment", "/other", ".exe"])
def test_noncanonical_source_urls_fail(suffix):
    url = margin_report_url(DAY) + suffix
    assert margin_identity(url) is None
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(url)


@pytest.mark.parametrize(
    "old,new",
    [
        ("20261105_mtall.pdf", "20261104_mtall.pdf"),
        ("/markets/statistics-equities/margin/tvdivq0000001rnl-att/", "https://untrusted.invalid/"),
        ("2026年11月5日申込分", "2026年11月4日申込分"),
        ("_mtall.pdf", "_other.pdf"),
    ],
)
def test_ambiguous_or_unsafe_directory_links_fail(old, new):
    snapshot = PublicFixtures().get(MARGIN_INDEX, 0)
    body = snapshot.body.decode().replace(old, new).encode()
    with pytest.raises(AtlasError):
        margin_reports(Snapshot(body, snapshot.url, snapshot.retrieved_at))


def test_duplicate_directory_dates_fail():
    snapshot = PublicFixtures().get(MARGIN_INDEX, 0)
    with pytest.raises(AtlasError):
        margin_reports(Snapshot(snapshot.body * 2, snapshot.url, snapshot.retrieved_at))


@pytest.mark.parametrize(
    "extra",
    [
        {"identifier": "AAPL"},
        {"identifier": "7203"},
        {"identifier": "7203.T;rm"},
        {"observation_date": "2026-09-24"},
        {"observation_date": "2026-02-30"},
        {"limit": 2},
        {"start_date": "2026-10-01", "end_date": "2026-11-05"},
        {"as_of": "2026-11-05"},
    ],
)
def test_query_rejects_wrong_market_history_and_irrelevant_fields(extra):
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(QUERY | extra)


def test_published_schema_and_runtime_require_explicit_jpx_identifier():
    schema = Atlas.research_catalog(dataset="margin_balances")["query_schema"]
    validator = Draft202012Validator(schema)
    validator.validate(QUERY)
    for query in ({"dataset": "margin_balances"}, QUERY | {"identifier": "AAPL"}):
        with pytest.raises(ValidationError):
            validator.validate(query)


def test_large_document_evidence_and_separate_import_limits():
    data = pages()
    data[0]["page"] = 109
    row = parse(data)
    assert row["source"]["pdf_page"] == 109
    SourceEvidence.model_validate(row["source"])
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(width=10, height=10)
    body = io.BytesIO()
    writer.write(body)
    proc = subprocess.run(
        [sys.executable, "-m", "atlas_terminal.report_pdf"],
        input=body.getvalue(),
        capture_output=True,
        timeout=20,
    )
    assert proc.returncode != 0 and not proc.stdout


def test_rotated_pdf_and_encrypted_or_oversized_documents():
    reader = PdfReader(io.BytesIO(source().body))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    page.add_transformation(Transformation().rotate(-90))
    page.rotate(90)
    body = io.BytesIO()
    writer.write(body)
    extracted = extract_margin_pages(body.getvalue())
    assert parse(extracted)["shares"]["buy_balance"] == "1600"
    writer.encrypt("fictional-test-password")
    body = io.BytesIO()
    writer.write(body)
    with pytest.raises(AtlasError):
        extract_margin_pages(body.getvalue())
    with pytest.raises(AtlasError):
        extract_margin_pages(b"%PDF-" + b" " * (8 * 1024 * 1024))


def test_compact_evidence_is_resolvable_and_quality_uses_application_date():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(QUERY | {"response": {"view": "compact"}})
        cell = result["records"][0]["shares"]["field_sources"]["buy_balance"]
        resolved = atlas.get_evidence(cell["evidence_id"])["evidence"]
        assert resolved["reported_text"] == "1,600"
        full = atlas.get_research(QUERY | {"requirements": {"min_observation_date": DAY}})
        assert full["quality"]["latest_observation_date"] == DAY
        with pytest.raises(AtlasError) as bad:
            atlas.get_research(QUERY | {"requirements": {"min_observation_date": "2026-11-06"}})
        assert bad.value.code == "QUALITY_NOT_MET"


def test_cli_sdk_http_return_identical_records_and_coverage(tmp_path):
    with Atlas("fixture") as atlas:
        expected = atlas.get_margin_status("7203.T")
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "margin",
            "7203.T",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(cli.stdout) == expected and not cli.stderr
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        response = client.post("/v1/research", json=QUERY)
        assert response.status_code == 200 and response.json() == expected
        assert "PAYMENT-REQUIRED" not in response.headers


async def test_stdio_mcp_reuses_the_same_margin_service(tmp_path):
    with Atlas("fixture") as atlas:
        expected = atlas.get_margin_status("7203.T")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool("atlas_get_research", {"query": QUERY})
        assert not result.is_error and result.structured_content == expected
