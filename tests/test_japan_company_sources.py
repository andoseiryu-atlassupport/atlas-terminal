"""Fictional EDINET/JPX contracts, archive safety and private credential boundaries."""

import io
import json
import logging
import os
import struct
import subprocess
import sys
import zipfile
from importlib.resources import files
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_document_url, edinet_list_url
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import DEBT_URL, PublicFixtures, PublicTransport
from atlas_terminal.research_japan_companies import (
    disclosure_rows,
    filing_facts,
    jpx_rows,
    xbrl_member,
)
from atlas_terminal.research_quality import assess_quality
from atlas_terminal.server import create_app
from atlas_terminal.source_checks import source_shapes
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
LIST = RESOURCE.joinpath("edinet_documents.json").read_bytes()
ZIP = RESOURCE.joinpath("edinet_filing.zip").read_bytes()
MEMBER, XML = xbrl_member(ZIP)
WHEN = "2026-10-27T02:00:00Z"
KEY = "FictionalEdinetCredential00000001"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("EDINET_API_KEY", raising=False)


def zipped(body=XML, name=MEMBER, extras=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
        for path, content in extras:
            archive.writestr(path, content)
    return output.getvalue()


def facts(body=XML, document_id="S100DEMO"):
    filing = disclosure_rows(Snapshot(LIST, edinet_list_url("2026-06-25"), WHEN))[0]
    return filing_facts(
        Snapshot(zipped(body), edinet_document_url(document_id), WHEN), filing, None
    )


def test_actual_periods_exact_numeric_units_and_original_member_identity():
    rows = facts()
    by_concept = {r["concept"]: r for r in rows}
    assert by_concept["NetSales"]["value"] == "123456789000"
    assert by_concept["ProfitLoss"]["value"] == "-12345"
    assert by_concept["TotalAssets"]["value"] == "0"
    shares = by_concept["NumberOfIssuedShares"]
    assert shares["context"]["period"] == {"instant": "2026-03-31"}
    assert shares["unit_definition"]["numerator"][0]["expanded_measure"].endswith("}shares")
    assert by_concept["Unavailable"]["value"] is None
    root = ET.fromstring(XML)
    for row in rows:
        source = row["source"]
        assert row["document_id"] == "S100DEMO"
        assert row["filed_at"] == "2026-06-25T09:30:00+09:00"
        assert source["archive_member"] == MEMBER
        assert source["member_sha256"] == digest(XML)
        index = int(source["xml_local_path"].split("[")[1][:-1]) - 1
        assert root[index].tag == source["expanded_element"]
        assert source["context_id"] == root[index].get("contextRef")
        assert row["filing_source"]["raw_sha256"] == digest(LIST)


def test_entity_branch_dimensions_scoped_qnames_divided_units_and_prior_period():
    body = (
        XML.replace(b"E99999<", b"E99999-000<")
        .replace(b"xmlns:demo=", b'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:demo=')
        .replace(
            b"</xbrli:entity>",
            b'<xbrli:segment><xbrldi:explicitMember dimension="demo:ConsolidationAxis">'
            b"demo:NonConsolidatedMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>",
        )
        .replace(b"2025-04-01", b"2024-04-01")
        .replace(b"2026-03-31", b"2025-03-31")
    )
    body = body.replace(
        b"</xbrli:xbrl>",
        b'<xbrli:unit id="perShare"><xbrli:divide><xbrli:unitNumerator>'
        b"<xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unitNumerator>"
        b"<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>"
        b"</xbrli:unitDenominator></xbrli:divide></xbrli:unit>"
        b'<demo:EPS contextRef="CurrentYearDuration" unitRef="perShare" '
        b'xmlns:demo="https://example.invalid/local-taxonomy">12.345</demo:EPS></xbrli:xbrl>',
    )
    rows = facts(body)
    eps = rows[-1]
    assert eps["context"]["period"]["startDate"] == "2024-04-01"
    assert eps["taxonomy_namespace"] == "https://example.invalid/local-taxonomy"
    assert eps["unit_definition"]["denominator"][0]["expanded_measure"].endswith("}shares")
    dimension = eps["context"]["dimensions"][0]
    assert dimension["expanded_dimension"].endswith("fictional-japanese-taxonomy}ConsolidationAxis")
    assert dimension["expanded_member"].endswith("}NonConsolidatedMember")


@pytest.mark.parametrize(
    "before,after",
    [
        (b"E99999", b"E11111"),
        (b"2025-04-01", b"2027-04-01"),
        (b"2025-04-01", b"2025-02-30"),
        (b'contextRef="CurrentYearDuration"', b'contextRef="Unknown"'),
        (b'unitRef="JPY"', b'unitRef="Unknown"'),
        (b"iso4217:JPY", b"unbound:JPY"),
        (b'xsi:nil="true"/>', b'xsi:nil="true">1</demo:Unavailable>'),
        (b'xsi:nil="true"', b'xsi:nil="maybe"'),
        (b"</xbrli:period>", b"<xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>"),
    ],
)
def test_malformed_or_mismatched_xbrl_is_rejected(before, after):
    with pytest.raises(AtlasError):
        facts(XML.replace(before, after))


def test_document_download_must_match_the_daily_list_selection():
    with pytest.raises(AtlasError, match="identity"):
        facts(document_id="S100OTHR")


def test_dtd_and_utf16_instances_are_rejected_before_namespace_parsing():
    for body in (b'<!DOCTYPE x [<!ENTITY a "unsafe">]>' + XML, XML.decode().encode("utf-16")):
        with pytest.raises(AtlasError):
            facts(body)


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "XBRL/../outside", r"XBRL\escape", "C:drive"]
)
def test_archive_rejects_unsafe_members_even_if_instance_is_safe(path):
    with pytest.raises(AtlasError):
        xbrl_member(zipped(extras=[(path, b"x")]))


def test_archive_rejects_duplicate_instances_symlinks_and_declared_expansion_limits():
    with pytest.raises(AtlasError):
        xbrl_member(zipped(extras=[("XBRL/PublicDoc/second.xbrl", XML)]))
    link = zipfile.ZipInfo("symlink")
    link.external_attr = 0o120777 << 16
    with pytest.raises(AtlasError):
        xbrl_member(zipped(extras=[(link, b"target")]))
    for expanded in (33 * 1024 * 1024, 129 * 1024 * 1024):
        body = bytearray(zipped())
        central = body.index(b"PK\x01\x02")
        struct.pack_into("<I", body, central + 24, expanded)
        with pytest.raises(AtlasError):
            xbrl_member(bytes(body))


def workbook(rows=None):
    rows = rows or [
        ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分"],
        ["20260930", "123A", "FICTIONAL Alpha", "プライム（内国株式）", "0050", "水産・農林業"],
        ["20260930", "12345", "FICTIONAL Class", "プライム（内国株式）", "0050", "水産・農林業"],
    ]
    ss = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ET.Element("worksheet", xmlns=ss)
    sheet = ET.SubElement(root, "sheetData")
    for i, values in enumerate(rows, 1):
        row = ET.SubElement(sheet, "row", r=str(i))
        for j, value in enumerate(values):
            cell = ET.SubElement(row, "c", r=f"{chr(65 + j)}{i}", t="inlineStr")
            ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
    return zipped(
        ET.tostring(root),
        "xl/worksheets/sheet1.xml",
        extras=[
            (
                "xl/workbook.xml",
                (
                    f'<workbook xmlns="{ss}" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Fictional" sheetId="1" r:id="rId1"/></sheets></workbook>'
                ).encode(),
            ),
            (
                "xl/_rels/workbook.xml.rels",
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Id="rId1" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                b'relationships/worksheet" '
                b'Target="worksheets/sheet1.xml"/></Relationships>',
            ),
        ],
    )


def test_xls_and_xlsx_reference_dates_alphanumeric_and_class_codes_original_cells():
    for body, locator in [
        (RESOURCE.joinpath("jpx_companies.xls").read_bytes(), "xls_worksheet_cell"),
        (workbook(), "xlsx_worksheet_cell"),
    ]:
        rows = jpx_rows(Snapshot(body, JPX_LIST, WHEN))
        assert source_shapes([Snapshot(body, JPX_LIST, WHEN)])[0]["format"] == "workbook"
        assert rows and all(not row["classification_historical"] for row in rows)
        for row in rows:
            source = row["source"]
            assert source["raw_sha256"] == digest(body)
            assert source["cell_row"] >= 2 and source["cell_column"] == 2
            assert source["locator_format"] == locator
            assert row["field_sources"]["33業種コード"]["cell_column"] == 5
    assert [r["security_code"] for r in rows] == ["123A", "12345"]
    assert rows[0]["industry_code"] == "0050"


@pytest.mark.parametrize(
    "rows",
    [
        [["コード"], ["1234"]],
        [
            ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分"],
            ["20260230", "1234", "Fictional", "Market", "0050", "Industry"],
        ],
        [
            ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分"],
            ["20260101", "../../", "Fictional", "Market", "0050", "Industry"],
        ],
    ],
)
def test_invalid_workbook_columns_dates_and_codes_fail_closed(rows):
    with pytest.raises(AtlasError):
        jpx_rows(Snapshot(workbook(rows), JPX_LIST, WHEN))


def test_edinet_credentials_stay_on_exact_api_endpoint_and_out_of_cache_logs(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    requests = []

    def response(request):
        requests.append(request)
        assert request.url.host == "api.edinet-fsa.go.jp"
        assert request.url.params["Subscription-Key"] == KEY
        logging.getLogger("httpx").info("%s", request.url)
        return httpx.Response(200, content=LIST, headers={"etag": KEY})

    transport = PublicTransport(
        cache_path=tmp_path / "public-cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    url = edinet_list_url("2026-06-25")
    with caplog.at_level(logging.DEBUG):
        snapshot = transport.get(url, 900)
        assert snapshot.url == url and KEY not in repr(snapshot)
        monkeypatch.delenv("EDINET_API_KEY")
        with pytest.raises(AtlasError) as caught:
            transport.get(url, 900)
        assert caught.value.code == "CONFIG_REQUIRED"
        for bad in (
            url + "&Subscription-Key=" + KEY,
            url.replace(".go.jp/", ".go.jp.evil.invalid/"),
        ):
            with pytest.raises(AtlasError):
                transport.request(bad, {})
    assert len(requests) == 1 and KEY not in caplog.text
    with transport.db.connect() as conn:
        assert conn.execute("select etag from source_cache").fetchone()[0] is None
        assert all(KEY not in row[0] for row in conn.execute("select url from source_versions"))
    transport.close()


@pytest.mark.parametrize("kind", ["json", "zip", "zip_ancillary"])
def test_edinet_echoed_secret_is_rejected_before_persistence(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    url = edinet_list_url("2026-06-25") if kind == "json" else edinet_document_url("S100DEMO")
    body = (
        json.dumps({"echo": KEY}).encode()
        if kind == "json"
        else zipped(XML.replace(b"123456789000", KEY.encode()))
    )
    if kind == "zip_ancillary":
        body = zipped(extras=[("XBRL/AuditDoc/echo.txt", KEY.encode())])
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    )
    transport = PublicTransport(cache_path=tmp_path / "public-cache.sqlite3", client=client)
    with pytest.raises(AtlasError):
        transport.get(url, 900)
    with transport.db.connect() as conn:
        assert conn.execute("select count(*) from source_versions").fetchone()[0] == 0
    transport.close()


def test_edinet_never_follows_redirects_even_with_a_redirecting_injected_client(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://example.invalid/"})

    client = httpx.Client(transport=httpx.MockTransport(response), follow_redirects=True)
    transport = PublicTransport(cache_path=tmp_path / "public-cache.sqlite3", client=client)
    with pytest.raises(AtlasError):
        transport.get(edinet_list_url("2026-06-25"), 900)
    assert len(requests) == 1
    transport.close()


def test_shared_sdk_http_compact_evidence_and_no_historical_jpx_claims():
    query = {
        "dataset": "japan_filing_facts",
        "filing_date": "2026-06-25",
        "document_id": "S100DEMO",
    }
    with Atlas("fixture") as atlas:
        full = atlas.get_research(query)
        compact = atlas.get_research(query | {"response": {"view": "compact"}})
        projected = atlas.get_research(
            query | {"response": {"view": "compact", "fields": ["value"]}}
        )
        assert len(full["records"]) == 5 and full["is_synthetic"]
        assert assess_quality(full).latest_observation_date == "2026-03-31"
        for row, compact_row in zip(full["records"], compact["records"], strict=True):
            assert (
                atlas.get_evidence(compact_row["source"]["evidence_id"])["evidence"]
                == row["source"]
            )
        assert projected["records"][0]["context"] == full["records"][0]["context"]
        assert projected["records"][0]["unit_definition"] == full["records"][0]["unit_definition"]
        assert "filing_source" in projected["records"][0]
        with pytest.raises(AtlasError):
            atlas.get_research({"dataset": "japan_securities", "as_of": "2025-01-01"})
    with TestClient(create_app(provider="fixture")) as client:
        result = client.post("/v1/research", json=query)
        assert result.status_code == 200 and result.json()["records"] == full["records"]


def test_withdrawn_or_unmatched_document_is_not_downloaded():
    class Withdrawn(PublicFixtures):
        def get(self, url, ttl):
            assert "documents.json?" in url
            raw = json.loads(LIST)
            raw["results"][0]["withdrawalStatus"] = "1"
            return Snapshot(json.dumps(raw).encode(), url, WHEN)

    with Atlas("fixture") as atlas:
        atlas._research.public = Withdrawn()
        with pytest.raises(AtlasError) as caught:
            atlas.get_research(
                {
                    "dataset": "japan_filing_facts",
                    "filing_date": "2026-06-25",
                    "document_id": "S100DEMO",
                }
            )
        assert caught.value.code == "INCOMPLETE_COVERAGE"


def test_cli_accepts_explicit_document_selection():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "japan_filing_facts",
            "--filing-date",
            "2026-06-25",
            "--document-id",
            "S100DEMO",
            "--provider",
            "fixture",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)
    assert len(result["records"]) == 5 and result["is_synthetic"]


@pytest.mark.asyncio
async def test_actual_stdio_mcp_resolves_company_evidence_and_local_as_of(tmp_path):
    transport = PublicTransport(cache_path=tmp_path / "public-cache.sqlite3")
    transport.save(DEBT_URL, b'{"data":[]}', 900, {})
    transport.close()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
        env={**os.environ, "ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        for query in [
            {
                "dataset": "japan_filing_facts",
                "filing_date": "2026-06-25",
                "document_id": "S100DEMO",
            },
            {"dataset": "japan_securities"},
        ]:
            query["response"] = {"view": "compact"}
            response = await session.call_tool("atlas_get_research", {"query": query})
            assert not response.is_error
            row = response.structured_content["records"][0]
            evidence = await session.call_tool(
                "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
            )
            assert not evidence.is_error and evidence.structured_content["evidence"]["raw_sha256"]
        response = await session.call_tool(
            "atlas_get_research",
            {
                "query": {
                    "dataset": "source_history",
                    "source_url": DEBT_URL,
                    "history_action": "as_of",
                    "as_of": "2100-01-01T00:00:00Z",
                }
            },
        )
        assert not response.is_error
        assert response.structured_content["history"]["point_in_time"] is False
