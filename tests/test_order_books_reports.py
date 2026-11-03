"""Fictional market depth and report documents; never live network or licensed data."""

import io
import json
import subprocess
from copy import deepcopy
from decimal import ROUND_UP, Decimal, localcontext
from importlib.resources import files

import httpx
import pytest
from jsonschema import Draft202012Validator
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

import atlas_terminal.analyst_reports as reports
from atlas_terminal.cli import app
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.local_inputs import read_regular
from atlas_terminal.order_books import (
    BOOK_TTL,
    LOCAL_BOOK_URL,
    BookTransport,
    LocalBook,
    bitbank_data,
    book_url,
)
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.source_checks import RecordingTransport, check_sources
from atlas_terminal.source_history import source_history
from atlas_terminal.util import canonical, digest, state_dir

RESOURCE = files("atlas_terminal").joinpath("resources/research")
BOOK = json.loads(RESOURCE.joinpath("order_book.json").read_bytes())
META = json.loads(RESOURCE.joinpath("analyst_report_metadata.json").read_bytes())


def fixture_snapshot(book=None):
    return Snapshot(
        canonical(book or BOOK), "https://fixtures.atlas.invalid/book.json", "2026-10-20T00:00:01Z"
    )


def book_client(tmp_path, book=None):
    atlas = Atlas("public", directory=tmp_path)
    atlas._research.books = BookTransport(
        cache_path=tmp_path / "order-books-cache.sqlite3",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=book or BOOK))
        ),
    )
    return atlas


def pdf_bytes(texts=("SYNTHETIC first page", "SYNTHETIC second page"), password=None):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def imported(tmp_path, body=None, meta=None, file_type="pdf"):
    path, metadata = tmp_path / "private-input", tmp_path / "private-metadata.json"
    path.write_bytes(body if body is not None else pdf_bytes())
    metadata.write_bytes(canonical(meta or META))
    receipt = reports.import_report(path, metadata, state_dir(), file_type=file_type)
    return receipt, path, metadata


def test_book_depth_preserves_raw_precision_and_exact_financial_meaning(tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas, localcontext() as ctx:
        ctx.prec, ctx.rounding = 6, ROUND_UP
        result = atlas.get_order_book("BTC_JPY", depth=2)
    rows, stats = result["records"], result["book_analytics"]
    assert [(r["side"], r["level"]) for r in rows] == [
        ("bids", 1),
        ("bids", 2),
        ("asks", 1),
        ("asks", 2),
    ]
    assert rows[1]["cumulative_quantity"] == "5"
    assert Decimal(rows[1]["cumulative_notional"]) == Decimal("498")
    assert stats["spread"] == "2.00" and stats["midpoint"] == "101.00"
    assert stats["quantity_imbalance"] == "0.111111111111111111"
    assert stats["spread_bps"] == "198.019801980198019802"
    assert rows[0]["price"] == "100.00"
    assert rows[0]["source"]["json_pointer"] == "/data/bids/0"
    assert result["coverage"]["truncated"] and not result["coverage"]["full_market_book"]
    assert result["snapshot"]["sequence_id"] == "9000000000000001"
    ResearchResult.model_validate(result)


@pytest.mark.parametrize(
    "mutation",
    [
        {"bids": []},
        {"asks": []},
        {"bids": [], "asks": []},
        {"bids": [["102", "1"]]},
        {"bids": [["103", "1"]]},
        {"timestamp": 4102444800000},
    ],
)
def test_unusable_books_never_invent_midpoints_spreads_or_fills(tmp_path, mutation):
    book = deepcopy(BOOK)
    book["data"].update(mutation)
    with book_client(tmp_path, book) as atlas:
        result = atlas.get_order_book("BTC_JPY")
        assert result["book_analytics"]["status"] == "unavailable"
        assert all(
            result["book_analytics"][k] is None
            for k in ("spread", "midpoint", "quantity_imbalance")
        )
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(
                {
                    "dataset": "order_book",
                    "identifier": "BTC_JPY",
                    "requirements": {"allow_missing": False},
                }
            )
        assert exc.value.code == "QUALITY_NOT_MET"


@pytest.mark.parametrize(
    "mutation",
    [
        {"bids": [["99", "1"], ["100", "2"]]},
        {"asks": [["101", "1"], ["101", "2"]]},
        {"bids": [["100", "0"]]},
        {"bids": [["-1", "1"]]},
        {"bids": [[100.0, "2"]]},
        {"bids": [["1e2", "2"]]},
        {"asks": [["NaN", "2"]]},
        {"asks": [["1", "Infinity"]]},
        {"bids": [["1", "2", "3"]]},
        {"bids": None},
        {"timestamp": True},
        {"timestamp": "1792454400000"},
        {"timestamp": -1},
        {"sequenceId": 1.5},
        {"sequenceId": False},
        {"asks_over": "-1"},
    ],
)
def test_malformed_depth_fails_closed(mutation):
    data = deepcopy(BOOK)
    data["data"].update(mutation)
    with pytest.raises(AtlasError):
        bitbank_data(fixture_snapshot(data))


@pytest.mark.parametrize(
    "url",
    [
        "http://public.bitbank.cc/btc_jpy/depth",
        "https://public.bitbank.cc/btc_jpy/depth?key=secret",
        "https://public.bitbank.cc/btc_jpy/depth#fragment",
        "https://public.bitbank.cc.evil/btc_jpy/depth",
        "https://public.bitbank.cc:443/btc_jpy/depth",
        "https://public.bitbank.cc/btc_jpy/../depth",
        "https://user:secret@public.bitbank.cc/btc_jpy/depth",
        "https://public.bitbank.cc/btc_jpy/%64epth",
    ],
)
def test_book_destination_is_exact_and_never_accepts_credentials(url):
    with pytest.raises(AtlasError):
        BookTransport.validate_url(url)


def test_readers_never_follow_redirect_or_inject_operator_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "SECRET contact")
    monkeypatch.setenv("EDINET_API_KEY", "PRIVATE_KEY")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://unapproved.invalid/data"})

    for index, (cls, url) in enumerate(
        ((BookTransport, book_url("BTC_JPY")), (reports.ReportTransport, reports.FISCO_URL))
    ):
        transport = cls(
            cache_path=tmp_path / f"{index}.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )
        with pytest.raises(AtlasError):
            transport.get(url, 5)
        transport.close()
    assert len(requests) == 2
    assert all(
        "SECRET" not in str(r.headers) and "PRIVATE_KEY" not in str(r.headers) for r in requests
    )


def test_book_cache_and_archive_are_separate_and_raw_fields_resolve(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=canonical(BOOK))

    transport = BookTransport(
        cache_path=tmp_path / "order-books-cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    snapshot = transport.get(book_url("BTC_JPY"), BOOK_TTL)
    assert transport.get(snapshot.url, BOOK_TTL) == snapshot
    assert len(requests) == 1 and not (tmp_path / "public-cache.sqlite3").exists()
    history = source_history(
        {"scope": "order_books", "url": snapshot.url, "action": "get", "sha256": snapshot.sha256},
        tmp_path,
    )
    assert json.loads(history["body_utf8"]) == BOOK
    transport.close()


def test_local_book_keeps_shares_and_cannot_fetch_broker_or_replay_without_configuration(
    tmp_path, monkeypatch
):
    path = tmp_path / "book.json"
    path.write_bytes(RESOURCE.joinpath("local_order_book.json").read_bytes())
    monkeypatch.setenv("ATLAS_ORDER_BOOK_FILE", str(path))
    query = {
        "dataset": "order_book",
        "book_source": "local",
        "identifier": "ATLASDEMO",
        "response": {"page_size": 1},
    }
    with Atlas("public") as atlas:
        result = atlas.get_research(query)
        assert atlas._research.books is None
        assert result["records"][0]["quantity_unit"] == "shares"
        assert not result["records"][0]["origin_verified"] and result["is_synthetic"]
        assert result["sources"][0]["raw_source_url"] == LOCAL_BOOK_URL
        cursor = result["response"]["next_cursor"]
        monkeypatch.delenv("ATLAS_ORDER_BOOK_FILE")
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(query | {"response": {"page_size": 1, "cursor": cursor}})
        assert exc.value.code == "CONFIG_REQUIRED"


def test_compact_book_projection_preserves_interpretation_and_derived_gate():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            {
                "dataset": "order_book",
                "identifier": "BTC_JPY",
                "response": {"view": "compact", "fields": ["price"]},
            }
        )
        assert {"side", "level", "quantity_unit", "venue", "derived"} <= result["records"][0].keys()
        with pytest.raises(AtlasError):
            atlas.get_research(
                {
                    "dataset": "order_book",
                    "identifier": "BTC_JPY",
                    "requirements": {"allow_derived": False},
                }
            )


@pytest.mark.parametrize(
    "patch",
    [
        {"quantity_unit": "contracts"},
        {"asset_class": "future"},
        {"asset_class": "crypto", "quantity_unit": "base_asset"},
        {"base_asset": "BTC"},
    ],
)
def test_local_book_rejects_mismatched_asset_units(patch):
    book = json.loads(RESOURCE.joinpath("local_order_book.json").read_bytes())
    with pytest.raises(ValueError):
        LocalBook.model_validate(book | patch)


def test_local_crypto_book_requires_named_base_asset():
    book = json.loads(RESOURCE.joinpath("local_order_book.json").read_bytes())
    parsed = LocalBook.model_validate(
        book | {"asset_class": "crypto", "quantity_unit": "base_asset", "base_asset": "BTC"}
    )
    assert parsed.base_asset == "BTC"


def test_report_directory_selects_english_alphanumeric_codes_and_keeps_dates():
    with Atlas("fixture") as atlas:
        rows = atlas.get_reports(identifier="123A")["records"]
        assert len(rows) == 1
        assert rows[0]["language"] == "en" and rows[0]["published_date"] == "2026-10-18"
        assert rows[0]["sponsored"] and not rows[0]["sponsorship_verified_for_document"]
        assert not rows[0]["body_available"] and rows[0]["analyst"] is None
        assert "research" not in rows[0].get("recommendation", "")
        assert atlas.get_reports(identifier="NONEXISTENT")["records"] == []


def test_directory_ignores_unapproved_links_and_does_not_borrow_previous_date():
    html = '<div>2026年10月19日<a href="https://www.fisco.co.jp/a.pdf">Alpha&lt;1234&gt;</a></div>'
    html += '<a href="https://www.fisco.co.jp/b.pdf">Undated&lt;2345&gt;</a>'
    html += '<script>2026年10月18日<a href="https://www.fisco.co.jp/bad.pdf">Bad</a></script>'
    html += '<div>2026年10月18日<a href="https://evil.invalid/report.pdf">No</a></div>'
    snapshot = Snapshot(html.encode(), reports.FISCO_URL, "2026-10-20T00:00:00Z")
    rows = reports.report_links(snapshot)
    assert len(rows) == 1 and rows[0]["identifier"] == "1234"
    assert rows[0]["source"]["xml_local_path"] == "//a[1]"


@pytest.mark.parametrize(
    "html",
    [
        "<html>Changed layout</html>",
        '<a href="x.pdf">Missing date</a>',
        '2026年02月30日<a href="x.pdf">Bad date</a>',
    ],
)
def test_directory_drift_is_not_silent_success(html):
    with pytest.raises(AtlasError):
        reports.report_links(Snapshot(html.encode(), reports.FISCO_URL, "2026-10-20T00:00:00Z"))


def test_future_directory_date_is_retained_as_ambiguous():
    body = '2026年10月21日<a href="/future.pdf">Synthetic &lt;1234&gt;</a>'.encode()
    row = reports.report_links(Snapshot(body, reports.FISCO_URL, "2026-10-20T00:00:00Z"))[0]
    assert row["status"] == "ambiguous" and row["publication_after_retrieval"]


def test_pdf_import_keeps_original_bytes_and_page_evidence_across_sdk_cli(tmp_path):
    receipt, path, metadata = imported(tmp_path)
    with Atlas("public") as atlas:
        result = atlas.get_report_text(receipt["report_id"], search="second")
        assert len(result["records"]) == 1 and result["records"][0]["page"] == 2
        evidence = result["records"][0]["source"]
        assert evidence["pdf_page"] == 2 and evidence["raw_sha256"] == digest(path.read_bytes())
        assert result["records"][0]["metadata_origin"] == "operator_supplied"
        assert not result["llm_used"] and atlas._research.reports is None
        assert atlas.get_reports(source="local")["records"][0]["body_available"]
        ResearchResult.model_validate(result)
        compact = atlas.get_research(
            {
                "dataset": "analyst_report_text",
                "report_id": receipt["report_id"],
                "search": "second",
                "response": {"view": "compact"},
            }
        )
        evidence_id = compact["records"][0]["source"]["evidence_id"]
        assert atlas.get_evidence(evidence_id)["evidence"]["pdf_page"] == 2
    repeated = reports.import_report(path, metadata, state_dir())
    assert repeated["report_id"] == receipt["report_id"] and repeated["already_imported"]
    cli = CliRunner().invoke(
        app, ["research", "report-text", receipt["report_id"], "--search", "second"]
    )
    assert cli.exit_code == 0 and json.loads(cli.stdout)["records"][0]["page"] == 2


def test_report_revisions_are_immutable_and_integrity_checked(tmp_path):
    receipt, path, metadata = imported(tmp_path, b"SYNTHETIC first edition", file_type="text")
    path.write_bytes(b"SYNTHETIC revised edition")
    revised = reports.import_report(path, metadata, state_dir(), file_type="text")
    assert revised["report_id"] != receipt["report_id"]
    assert "first" in reports.read_report(state_dir(), receipt["report_id"])["pages"][0]["text"]
    db = reports.Database(state_dir() / "analyst-reports.sqlite3", reports.REPORT_SCHEMA)
    with db.connect(write=True) as conn:
        conn.execute(
            "UPDATE analyst_documents SET pages=? WHERE id=?", (b"[]", receipt["report_id"])
        )
    with pytest.raises(AtlasError, match="integrity"):
        reports.read_report(state_dir(), receipt["report_id"])


def test_private_library_capacity_never_evicts_a_previous_document(tmp_path, monkeypatch):
    receipt, path, metadata = imported(tmp_path, b"SYNTHETIC retained", file_type="text")
    monkeypatch.setattr(reports, "MAX_REPORTS", 1)
    path.write_bytes(b"SYNTHETIC would overflow")
    with pytest.raises(AtlasError) as exc:
        reports.import_report(path, metadata, state_dir(), file_type="text")
    assert exc.value.code == "CACHE_FULL"
    assert (
        reports.read_report(state_dir(), receipt["report_id"])["pages"][0]["text"]
        == "SYNTHETIC retained"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"not a PDF",
        pdf_bytes(password="fictional"),
        pdf_bytes(texts=("",)),
        pdf_bytes(texts=("SYNTHETIC",) * 101),
    ],
)
def test_unsupported_pdfs_fail_without_partial_import(tmp_path, body):
    with pytest.raises(AtlasError):
        imported(tmp_path, body)
    assert reports.local_reports(state_dir(), synthetic=False) == []


def test_extraction_timeout_returns_safe_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE_COMMAND", 15)

    monkeypatch.setattr(reports.subprocess, "run", timeout)
    with pytest.raises(AtlasError) as exc:
        reports.extract_document(b"%PDF-fake", "pdf")
    assert "PRIVATE_COMMAND" not in str(exc.value)


@pytest.mark.parametrize(
    "patch",
    [
        {"rights_basis": "unknown"},
        {"published_date": "2100-01-01"},
        {"original_source_url": "https://example.invalid/doc?token=PRIVATE"},
        {"original_source_url": "https://user:PRIVATE@example.invalid/doc"},
        {"original_source_url": "file:///private.pdf"},
        {"target_price": "999"},
    ],
)
def test_report_metadata_requires_explicit_rights_and_safe_original_links(tmp_path, patch):
    with pytest.raises(AtlasError):
        imported(tmp_path, b"SYNTHETIC", META | patch, file_type="text")


def test_local_input_rejects_symlinks_and_oversized_files(tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("fictional")
    symlink = tmp_path / "link"
    symlink.symlink_to(path)
    with pytest.raises(AtlasError):
        read_regular(symlink, 100)
    with pytest.raises(AtlasError):
        read_regular(path, 2)


@pytest.mark.parametrize(
    "query",
    [
        {"dataset": "order_book", "identifier": "BTC_JPY", "depth": 0},
        {"dataset": "order_book", "identifier": "BTC_JPY", "depth": 201},
        {"dataset": "order_book", "identifier": "BTC_JPY", "depth": True},
        {"dataset": "order_book", "identifier": "7203.T"},
        {"dataset": "order_book", "identifier": "BTC_JPY", "start_date": "2026-01-01"},
        {"dataset": "analyst_reports", "source_url": "https://unapproved.invalid"},
        {"dataset": "analyst_report_text"},
        {"dataset": "analyst_report_text", "report_id": "/tmp/file"},
    ],
)
def test_query_rejects_unbounded_arbitrary_or_historical_inputs(query):
    with pytest.raises((AtlasError, ValueError)):
        ResearchQuery.model_validate(query)


def test_new_queries_are_schema_valid_and_use_existing_research_interfaces():
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    queries = [
        {"dataset": "order_book", "identifier": "BTC_JPY"},
        {"dataset": "analyst_reports"},
        {"dataset": "analyst_report_text", "report_id": "rp_" + "0" * 64},
    ]
    with Atlas("fixture") as atlas:
        for query in queries:
            validator.validate(query)
            assert atlas.get_research(query)["records"]
        assert atlas.research_diagnostics({"sources": ["bitbank", "fisco"]})["is_synthetic"]
    result = CliRunner().invoke(
        app, ["research", "book", "BTC_JPY", "--provider", "fixture", "--json"]
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["is_synthetic"]


def test_explicit_optional_source_probes_capture_books_and_html_without_macro_requests(tmp_path):
    requested = []
    html = (
        RESOURCE.joinpath("analyst_reports.html")
        .read_bytes()
        .replace(b"reports.fisco.invalid", b"www.fisco.co.jp")
    )

    def handler(request):
        requested.append(str(request.url))
        if str(request.url) == reports.FISCO_URL:
            return httpx.Response(200, content=html)
        assert str(request.url) == book_url("BTC_JPY")
        return httpx.Response(200, json=BOOK)

    def factory(directory, snapshots):
        atlas = Atlas("public", directory=directory)
        for name, cls, filename in (
            ("books", BookTransport, "order-books-cache.sqlite3"),
            ("reports", reports.ReportTransport, "reports-cache.sqlite3"),
        ):
            transport = cls(
                cache_path=directory / filename,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            setattr(atlas._research, name, RecordingTransport(transport, snapshots))
        return atlas

    result = check_sources(
        ["bitbank", "fisco"],
        allow_network=True,
        directory=tmp_path,
        client_factory=factory,
    )
    assert result["status"] == "PASS" and len(requested) == 2
    assert {r["sources"][0]["format"] for r in result["checks"]} == {"json", "html"}
    assert not (tmp_path / "public-cache.sqlite3").exists()
