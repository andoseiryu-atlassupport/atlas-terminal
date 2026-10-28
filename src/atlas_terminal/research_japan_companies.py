"""Original EDINET financial facts and JPX reference rows, without inferred vintages."""

import io
import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

from xlrd.biffh import XLRDError

from atlas_terminal.equity_sources import TOKYO
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_document_url, edinet_list_url
from atlas_terminal.jpx_workbook import workbook_table
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.public_sources import local_name, xml_root
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_archives import bounded_zip, member_bytes, reject_archive_token
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


def xbrl_member(body: bytes, *, forbidden_token: bytes | None = None) -> tuple[str, bytes]:
    with bounded_zip(body) as archive:
        if forbidden_token is not None:
            if forbidden_token in body:
                raise source_error("EDINET echoed a private credential.")
            reject_archive_token(archive, forbidden_token)
        selected = [
            name
            for name in archive.namelist()
            if name.startswith("XBRL/PublicDoc/") and name.endswith(".xbrl")
        ]
        if len(selected) != 1:
            raise source_error("Require one bounded PublicDoc XBRL instance.")
        return selected[0], member_bytes(archive, selected[0])


def scoped_xml(body: bytes) -> tuple[ET.Element, dict[ET.Element, dict[str, str]]]:
    xml_root(body)  # Enforce the common XML encoding, declaration and element bounds.
    scopes: dict[ET.Element, dict[str, str]] = {}
    stack: list[dict[str, str]] = [{}]
    pending: dict[str, str] = {}
    parser = ET.iterparse(io.BytesIO(body), events=("start-ns", "start", "end"))
    for event, node in parser:
        if event == "start-ns":
            prefix, uri = node
            pending[prefix] = uri
        elif event == "start":
            scope = {**stack[-1], **pending}
            pending = {}
            stack.append(scope)
            scopes[node] = scope
        else:
            stack.pop()
    return next(iter(scopes)), scopes


def expanded_qname(text: str | None, scope: dict[str, str]) -> str:
    text = (text or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?", text):
        raise source_error("Invalid XBRL QName.")
    prefix, _, name = text.rpartition(":")
    if prefix not in scope and prefix:
        raise source_error("Unbound XBRL QName prefix.")
    namespace = scope.get(prefix, "")
    return f"{{{namespace}}}{name}" if namespace else name


def filing_facts(
    snapshot: Snapshot, filing: dict[str, Any], concept: str | None
) -> list[dict[str, Any]]:
    member, body = xbrl_member(snapshot.body)
    root, scopes = scoped_xml(body)
    if root.tag != XBRLI + "xbrl":
        raise source_error("Expected an XBRL instance root.")
    if snapshot.url.startswith(
        "https://api.edinet-fsa.go.jp/"
    ) and snapshot.url != edinet_document_url(filing["document_id"]):
        raise source_error("XBRL download identity differs from the selected filing.")
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
            entity_code = (entity.text or "").strip()
            if (
                entity.get("scheme") != "http://disclosure.edinet-fsa.go.jp"
                or not filing["edinet_code"]
                or re.fullmatch(re.escape(filing["edinet_code"]) + r"(?:-[0-9]{3})?", entity_code)
                is None
            ):
                raise source_error("XBRL context entity differs from the selected filing.")
            period_values = {local_name(node): node.text for node in period}
            if (
                len(period_values) != len(period)
                or set(period_values) not in ({"instant"}, {"startDate", "endDate"}, {"forever"})
                or (
                    "startDate" in period_values
                    and (period_values["startDate"] or "") > (period_values["endDate"] or "")
                )
            ):
                raise source_error("XBRL period is missing, duplicated or reversed.")
            for key, value in period_values.items():
                if key != "forever":
                    try:
                        datetime.strptime(value or "", "%Y-%m-%d")
                    except ValueError:
                        raise source_error("XBRL period has an unsupported date.") from None
            contexts[ident] = {
                "entity": entity_code,
                "entity_scheme": entity.get("scheme"),
                "period": period_values,
                "dimensions": [
                    {
                        "dimension": node.get("dimension"),
                        "expanded_dimension": expanded_qname(node.get("dimension"), scopes[node]),
                        "value": "".join(node.itertext()),
                        "kind": local_name(node),
                        "expanded_member": expanded_qname(node.text, scopes[node])
                        if local_name(node) == "explicitMember"
                        else None,
                        "typed_xml": ET.tostring(node, encoding="unicode")
                        if local_name(node) == "typedMember"
                        else None,
                    }
                    for node in child.iter()
                    if node.get("dimension") is not None
                ],
            }
        elif child.tag == XBRLI + "unit":
            if not ident or ident in units:
                raise source_error("XBRL unit IDs are missing or duplicated.")

            def measures(parent: ET.Element) -> list[dict[str, Any]]:
                result = [
                    {
                        "element": "measure",
                        "measure": node.text,
                        "expanded_measure": expanded_qname(node.text, scopes[node]),
                    }
                    for node in parent
                    if node.tag == XBRLI + "measure"
                ]
                if len(result) != len(parent) or not result:
                    raise source_error("XBRL unit has unsupported measures.")
                return result

            divide = child.find(XBRLI + "divide")
            if divide is None:
                units[ident] = {"numerator": measures(child), "denominator": []}
            else:
                numerator = divide.find(XBRLI + "unitNumerator")
                denominator = divide.find(XBRLI + "unitDenominator")
                if len(child) != 1 or len(divide) != 2 or numerator is None or denominator is None:
                    raise source_error("XBRL divided unit is malformed.")
                units[ident] = {
                    "numerator": measures(numerator),
                    "denominator": measures(denominator),
                }
    rows = []
    for index, child in enumerate(root, 1):
        context, unit = child.get("contextRef"), child.get("unitRef")
        name = local_name(child)
        if context is None or unit is None or (concept is not None and concept != name):
            continue
        if context not in contexts or unit not in units or len(child):
            raise source_error("XBRL numeric fact references an unknown context/unit or structure.")
        value = None if child.get(NIL) in {"true", "1"} else numeric(child.text)
        if child.get(NIL) not in {None, "false", "0", "true", "1"} or (
            child.get(NIL) in {"true", "1"} and (child.text or "").strip()
        ):
            raise source_error("XBRL nil fact has an invalid flag or value.")
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
                "unit_measures": units[unit]["numerator"] + units[unit]["denominator"],
                "unit_definition": units[unit],
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
        worksheet, locator, table = workbook_table(snapshot.body)
        headers = table[0]
        required = ["日付", "コード", "銘柄名", "市場・商品区分", "33業種コード", "33業種区分"]
        if len(set(headers)) != len(headers) or not set(required) <= set(headers):
            raise ValueError("JPX table headings differ")
        rows, seen = [], set()
        reference_dates = set()
        for index, cells in enumerate(table[1:], 2):
            values = dict(zip(headers, cells, strict=True))
            code, day = values["コード"], values["日付"]
            if not re.fullmatch(r"[0-9][0-9A-Z]{3,4}", code) or code in seen:
                raise ValueError("JPX security codes are malformed or duplicated")
            if not re.fullmatch(r"[0-9]{8}", day):
                raise ValueError("JPX reference date differs from YYYYMMDD")
            date = datetime.strptime(day, "%Y%m%d").date().isoformat()
            reference_dates.add(date)
            seen.add(code)

            def cell_source(key: str, index: int = index) -> dict[str, Any]:
                return {
                    "raw_source_url": snapshot.url,
                    "raw_sha256": snapshot.sha256,
                    "retrieved_at": snapshot.retrieved_at,
                    "cell_row": index,
                    "cell_column": headers.index(key) + 1,
                    "locator_format": locator,
                    "worksheet": worksheet,
                }

            rows.append(
                {
                    "identifier": code + ".T",
                    "security_code": code,
                    "name": values["銘柄名"],
                    "market_product": values["市場・商品区分"],
                    "industry_classification": "jpx33",
                    "industry_code": values["33業種コード"],
                    "industry_name": values["33業種区分"],
                    "reference_date": date,
                    "classification_historical": False,
                    "field_sources": {key: cell_source(key) for key in required},
                    "source": cell_source("コード"),
                }
            )
        if not rows or len(reference_dates) != 1:
            raise ValueError("JPX reference date is missing or inconsistent")
        return rows
    except (ValueError, TypeError, XLRDError, IndexError, KeyError, UnicodeError, AttributeError):
        raise source_error(
            "JPX workbook is malformed or differs from the supported contract."
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
