"""Original EDINET financial facts and JPX reference rows, without inferred vintages."""

import io
import re
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

import xlrd
from xlrd.biffh import XLRDError

from atlas_terminal.equity_sources import TOKYO
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_document_url, edinet_list_url
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.public_sources import local_name, xml_root
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import digest

XBRLI = "{http://www.xbrl.org/2003/instance}"
NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def disclosure_rows(snapshot: Snapshot) -> list[dict[str, Any]]:
    data = snapshot.data
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("metadata"), dict)
        or str(data["metadata"].get("status")) != "200"
        or not isinstance(data.get("results"), list)
        or len(data["results"]) > 10000
    ):
        raise source_error("EDINET did not return a successful bounded document list.")
    rows, seen = [], set()
    for index, cell in enumerate(data["results"]):
        doc = cell.get("docID") if isinstance(cell, dict) else None
        if not isinstance(doc, str) or not re.fullmatch(r"S[0-9A-Z]{7}", doc) or doc in seen:
            raise source_error("EDINET document identity is missing or duplicated.")
        seen.add(doc)
        submitted = cell.get("submitDateTime")
        if submitted is not None:
            try:
                submitted = (
                    datetime.strptime(submitted, "%Y-%m-%d %H:%M").replace(tzinfo=TOKYO).isoformat()
                )
            except (ValueError, TypeError):
                raise source_error(
                    "EDINET submission timestamp differs from the documented format."
                ) from None
        rows.append(
            {
                "document_id": doc,
                "edinet_code": cell.get("edinetCode"),
                "security_code": cell.get("secCode"),
                "filer_name": cell.get("filerName"),
                "document_type": cell.get("docTypeCode"),
                "description": cell.get("docDescription"),
                "report_period_start": cell.get("periodStart"),
                "report_period_end": cell.get("periodEnd"),
                "filed_at": submitted,
                "published_at": None,
                "xbrl_available": cell.get("xbrlFlag") == "1",
                "withdrawal_status": cell.get("withdrawalStatus"),
                "disclosure_status": cell.get("disclosureStatus"),
                "parent_document_id": cell.get("parentDocID"),
                "source": reference(snapshot, f"/results/{index}"),
            }
        )
    return rows


def xbrl_member(body: bytes) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 128 * 1024 * 1024:
                raise ValueError("Expanded ZIP exceeds bounds")
            for item in entries:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or item.flag_bits & 1
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise ValueError("Unsafe archive member")
            selected = [
                item
                for item in entries
                if "PublicDoc" in PurePosixPath(item.filename).parts
                and item.filename.endswith(".xbrl")
            ]
            if len(selected) != 1 or selected[0].file_size > 32 * 1024 * 1024:
                raise ValueError("Require one bounded PublicDoc XBRL instance")
            return selected[0].filename, archive.read(selected[0])
    except (ValueError, zipfile.BadZipFile, RuntimeError, OSError):
        raise source_error(
            "EDINET XBRL ZIP is malformed, unsafe or outside supported bounds."
        ) from None


def filing_facts(
    snapshot: Snapshot, filing: dict[str, Any], concept: str | None
) -> list[dict[str, Any]]:
    member, body = xbrl_member(snapshot.body)
    root = xml_root(body)
    if root.tag != XBRLI + "xbrl":
        raise source_error("Expected an XBRL instance root.")
    contexts, units = {}, {}
    for child in root:
        ident = child.get("id")
        if child.tag == XBRLI + "context":
            if not ident or ident in contexts:
                raise source_error("XBRL context IDs are missing or duplicated.")
            entity = child.find(XBRLI + "entity/" + XBRLI + "identifier")
            period = child.find(XBRLI + "period")
            if entity is None or period is None:
                raise source_error("XBRL context lacks entity or period.")
            if filing["edinet_code"] is not None and entity.text != filing["edinet_code"]:
                raise source_error("XBRL context entity differs from the selected filing.")
            period_values = {local_name(node): node.text for node in period}
            for key, value in period_values.items():
                if key != "forever":
                    try:
                        datetime.strptime(value or "", "%Y-%m-%d")
                    except ValueError:
                        raise source_error("XBRL period has an unsupported date.") from None
            contexts[ident] = {
                "entity": entity.text,
                "entity_scheme": entity.get("scheme"),
                "period": period_values,
                "dimensions": [
                    {"dimension": node.get("dimension"), "value": "".join(node.itertext())}
                    for node in child.iter()
                    if node.get("dimension") is not None
                ],
            }
        elif child.tag == XBRLI + "unit":
            if not ident or ident in units:
                raise source_error("XBRL unit IDs are missing or duplicated.")
            units[ident] = [
                {"element": local_name(node), "measure": node.text}
                for node in child.iter()
                if local_name(node) == "measure"
            ]
    rows = []
    for index, child in enumerate(root, 1):
        context, unit = child.get("contextRef"), child.get("unitRef")
        name = local_name(child)
        if context is None or unit is None or (concept is not None and concept != name):
            continue
        if context not in contexts or unit not in units or len(child):
            raise source_error("XBRL numeric fact references an unknown context/unit or structure.")
        value = None if child.get(NIL) in {"true", "1"} else numeric(child.text)
        source = reference(snapshot, f"/xbrl/*[{index}]", xml=True)
        source.update(
            archive_member=member,
            member_sha256=digest(body),
            expanded_element=child.tag,
            context_id=context,
            unit_id=unit,
        )
        rows.append(
            {
                "document_id": filing["document_id"],
                "concept": name,
                "taxonomy_namespace": child.tag.split("}")[0].removeprefix("{"),
                "context_id": context,
                "context": contexts[context],
                "unit_id": unit,
                "unit_measures": units[unit],
                "decimals": child.get("decimals"),
                "value": value,
                "status": "unavailable" if value is None else "reported",
                "filed_at": filing["filed_at"],
                "published_at": None,
                "filing_source": filing["source"],
                "source": source,
            }
        )
        if len(rows) > 50000:
            raise source_error("XBRL fact count exceeds bounds.")
    return rows


def jpx_rows(snapshot: Snapshot) -> list[dict[str, Any]]:
    try:
        book = xlrd.open_workbook(file_contents=snapshot.body, on_demand=True)
        try:
            if book.nsheets != 1:
                raise ValueError("Expected one listed-company sheet")
            sheet = book.sheet_by_index(0)
            headers = [str(value).strip() for value in sheet.row_values(0)]
            required = ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分"]
            if (
                sheet.nrows > 10000
                or len(set(headers)) != len(headers)
                or not set(required) <= set(headers)
            ):
                raise ValueError("JPX table headings differ")
            rows, seen = [], set()
            for index in range(1, sheet.nrows):
                values = dict(zip(headers, sheet.row_values(index), strict=True))

                def cell(key: str, values: dict[str, Any] = values) -> str:
                    value = values[key]
                    return (
                        str(int(value))
                        if isinstance(value, float) and value.is_integer()
                        else str(value).strip()
                    )

                code, day = cell("コード"), cell("日付")
                if not re.fullmatch(r"[0-9][0-9A-Z]{3}", code) or code in seen:
                    raise ValueError("JPX security codes are malformed or duplicated")
                date = datetime.strptime(day, "%Y%m%d").date().isoformat()
                seen.add(code)
                source = {
                    "raw_source_url": snapshot.url,
                    "raw_sha256": snapshot.sha256,
                    "retrieved_at": snapshot.retrieved_at,
                    "cell_row": index + 1,
                    "cell_column": headers.index("コード") + 1,
                    "locator_format": "xls_worksheet_cell",
                    "worksheet": sheet.name,
                }
                rows.append(
                    {
                        "identifier": code + ".T",
                        "security_code": code,
                        "name": cell("銘柄名"),
                        "market_product": cell("市場・商品区分"),
                        "industry_classification": "jpx33",
                        "industry_code": cell("33業種コード"),
                        "industry_name": cell("33業種区分"),
                        "reference_date": date,
                        "classification_historical": False,
                        "source": source,
                    }
                )
            return rows
        finally:
            book.release_resources()
    except (ValueError, TypeError, XLRDError, IndexError, UnicodeError):
        raise source_error(
            "JPX XLS table is malformed or differs from the supported contract."
        ) from None


def japan_companies(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    if q.dataset == "japan_securities":
        snapshot = transport.get(JPX_LIST, 21600)
        rows, sources = jpx_rows(snapshot), [reference(snapshot, "")]
        if q.identifier:
            rows = [row for row in rows if row["identifier"] == q.identifier]
    else:
        listing = transport.get(edinet_list_url(q.filing_date or ""), 900)
        rows, sources = disclosure_rows(listing), [reference(listing, "")]
        if q.identifier:
            rows = [
                row for row in rows if q.identifier in {row["edinet_code"], row["security_code"]}
            ]
        if q.dataset == "japan_filing_facts":
            filings = [row for row in rows if row["document_id"] == q.document_id]
            if len(filings) != 1 or not filings[0]["xbrl_available"]:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "Selected daily list has no matching XBRL filing."
                )
            if filings[0]["withdrawal_status"] != "0" or filings[0]["disclosure_status"] != "0":
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "Selected filing is withdrawn or not publicly available."
                )
            snapshot = transport.get(edinet_document_url(q.document_id or ""), 21600)
            rows = filing_facts(snapshot, filings[0], q.concept)
            sources.append(reference(snapshot, ""))
    return {
        "records": rows[: q.limit],
        "sources": sources,
        "coverage": {
            "observed_records": len(rows),
            "returned_records": min(len(rows), q.limit),
            "truncated": len(rows) > q.limit,
            "point_in_time": False,
        },
        "limitations": [
            "Filing report periods are not substituted for individual XBRL contexts.",
            "Original taxonomy, consolidation dimensions and units are not harmonized.",
            (
                "Submission time is not proven first public availability; "
                "revisions are separate documents."
            ),
            "Current JPX membership and industry labels do not establish historical membership.",
        ],
    }
