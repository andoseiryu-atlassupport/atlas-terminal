"""Fictional inputs check that the opt-in audit detects incorrect normalized data."""

import runpy
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.order_books import book_url
from atlas_terminal.providers import Snapshot
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources/research")


@pytest.fixture
def verifier(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return runpy.run_path(str(scripts / "verify_books_reports_live.py"))


@pytest.mark.parametrize("corruption", ["cumulative_quantity", "spread", "quantity_unit"])
def test_book_audit_rejects_incorrect_calculations_and_units(verifier, corruption):
    with Atlas("fixture") as atlas:
        result = atlas.get_order_book("BTC_JPY", depth=200)
    source = result["sources"][0]
    snapshot = Snapshot(
        RESOURCE.joinpath("order_book.json").read_bytes(),
        source["raw_source_url"],
        source["retrieved_at"],
    )
    checked = verifier["verify_book"](result, snapshot, "BTC_JPY")
    assert checked["calculation_matches"] == 2 * len(result["records"]) + 8
    broken = deepcopy(result)
    if corruption == "spread":
        broken["book_analytics"]["spread"] = "0"
    else:
        broken["records"][0][corruption] = "shares" if corruption == "quantity_unit" else "0"
    with pytest.raises(AssertionError):
        verifier["verify_book"](broken, snapshot, "BTC_JPY")


@pytest.mark.parametrize("corruption", ["published_date", "identifier", "language"])
def test_directory_audit_rejects_wrong_row_metadata(verifier, corruption):
    with Atlas("fixture") as atlas:
        result = atlas.get_reports()
    source = result["sources"][0]
    snapshot = Snapshot(
        RESOURCE.joinpath("analyst_reports.html").read_bytes(),
        source["raw_source_url"],
        source["retrieved_at"],
    )
    checked = verifier["verify_reports"](result, snapshot)
    assert checked["directory_metadata_matches"] == 3 * len(result["records"])
    broken = deepcopy(result)
    broken["records"][0][corruption] = {
        "published_date": "2026-10-18",
        "identifier": "9999",
        "language": "zh",
    }[corruption]
    with pytest.raises(AssertionError):
        verifier["verify_reports"](broken, snapshot)


def test_failed_source_parse_retains_original_private_bytes(verifier, tmp_path):
    body = b'{"success": 1, "data": {"changed": "SYNTHETIC format"}}'
    capture = verifier["BookCapture"](
        raw_directory=tmp_path,
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        ),
    )
    try:
        with pytest.raises(AtlasError):
            capture.get(book_url("BTC_JPY"), 5)
        raw = tmp_path / (digest(body) + ".raw")
        assert raw.read_bytes() == body and raw.stat().st_mode & 0o777 == 0o600
        assert capture.diagnostics[book_url("BTC_JPY")] == {"http_status": 200}
    finally:
        capture.close()


def test_source_audit_retains_network_exception_category(verifier, tmp_path):
    def blocked(request):
        raise httpx.ProxyError("SYNTHETIC proxy denial")

    capture = verifier["BookCapture"](
        raw_directory=tmp_path,
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(blocked)),
    )
    try:
        with pytest.raises(AtlasError):
            capture.get(book_url("BTC_JPY"), 5)
        assert capture.diagnostics[book_url("BTC_JPY")] == {
            "http_status": None,
            "network_exception": "ProxyError",
        }
        assert not list(tmp_path.glob("*.raw"))
    finally:
        capture.close()
