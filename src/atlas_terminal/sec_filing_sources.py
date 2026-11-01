"""Issuer-bound filing members and scoped XBRL numeric facts from SEC archives."""

import re
from dataclasses import asdict
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

from atlas_terminal.errors import AtlasError
from atlas_terminal.filing_catalog import DOCUMENT_NAME_PATTERN, FILING_FORMS, SEGMENT_METRICS
from atlas_terminal.providers import SEC_ARCHIVES, Filing, SECProvider, Snapshot
from atlas_terminal.research_financials import STATEMENT_METRICS, reference
from atlas_terminal.research_japan_companies import expanded_qname, scoped_xml
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.util import canonical

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
ISO4217 = "{http://www.xbrl.org/2003/iso4217}"
SEGMENT_AXES = {
    "StatementBusinessSegmentsAxis": "business",
    "ProductOrServiceAxis": "product_or_service",
    "StatementGeographicalAxis": "geographic",
    "GeographicalAreasAxis": "geographic",
}


def document_identity(root: ET.Element, company: dict[str, Any], filing: Filing) -> None:
    """Bind a selected instance to the issuer and disclosed document metadata."""
    fields: dict[str, set[str]] = {}
    for child in root:
        namespace, _, name = child.tag.removeprefix("{").partition("}")
        if re.fullmatch(r"https?://xbrl\.sec\.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?", namespace):
            if name in {
                "DocumentType",
                "DocumentPeriodEndDate",
                "EntityCentralIndexKey",
                "AmendmentFlag",
            }:
                if len(child):
                    raise source_error("DEI identity fields must be scalar facts.")
                fields.setdefault(name, set()).add((child.text or "").strip())
    required = {"DocumentType", "EntityCentralIndexKey"}
    if filing.report_date:
        required.add("DocumentPeriodEndDate")
    if any(len(fields.get(name, set())) != 1 for name in required):
        raise source_error("A unique DEI issuer, document type and report date are required.")
    cik = next(iter(fields["EntityCentralIndexKey"]))
    form = next(iter(fields["DocumentType"]))
    if (
        not re.fullmatch(r"[0-9]{1,10}", cik)
        or cik.zfill(10) != company["cik"]
        or form not in {filing.form, filing.form.removesuffix("/A")}
        or (filing.report_date and fields["DocumentPeriodEndDate"] != {filing.report_date})
        or (filing.form.endswith("/A") and fields.get("AmendmentFlag") not in ({"true"}, {"1"}))
    ):
        raise source_error("XBRL DEI identity differs from the selected issuer filing.")


def safe_document(value: str) -> bool:
    return bool(re.fullmatch(DOCUMENT_NAME_PATTERN, value)) and value not in {".", ".."}


def filing_directory(
    source: SECProvider, company: dict[str, Any], filing: Filing
) -> tuple[str, Snapshot, list[dict[str, Any]]]:
    base = f"{SEC_ARCHIVES}{int(company['cik'])}/{filing.accession.replace('-', '')}/"
    snapshot = source.transport.get(base + "index.json", 86400)
    raw = snapshot.data
    directory = raw.get("directory") if isinstance(raw, dict) else None
    if (
        not isinstance(directory, dict)
        or directory.get("name", "").rstrip("/")
        != base.removeprefix("https://www.sec.gov").rstrip("/")
        or not isinstance(directory.get("item"), list)
        or len(directory["item"]) > 2000
    ):
        raise source_error("SEC filing directory identity or size is invalid.")
    rows, seen = [], set()
    for i, item in enumerate(directory["item"]):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not safe_document(item["name"])
            or item["name"] in seen
        ):
            raise source_error("SEC directory has unsafe or duplicate member names.")
        seen.add(item["name"])
        rows.append(
            {
                "document_name": item["name"],
                "source_media_type": item.get("type"),
                "source_size": item.get("size"),
                "source_last_modified": item.get("last-modified"),
                "source": reference(snapshot, f"/directory/item/{i}"),
            }
        )
    return base, snapshot, rows


def select_member(
    filing: Filing, members: list[dict[str, Any]], document_name: str | None, *, numeric_facts: bool
) -> str:
    names = {r["document_name"] for r in members}
    if document_name is not None:
        if not safe_document(document_name) or document_name not in names:
            raise AtlasError("INVALID_INPUT", "Choose an exact member name from filing_documents.")
        selected = document_name
    elif numeric_facts:
        primary = filing.primary_document or ""
        stem = primary.rsplit(".", 1)[0]
        candidates = [name for name in (stem + "_htm.xml", stem + ".xml") if name in names]
        if not candidates:
            candidates = [name for name in names if name.lower().endswith("_htm.xml")]
        if len(candidates) != 1:
            raise AtlasError(
                "INCOMPLETE_COVERAGE", "No unique extracted XBRL instance; select document_name."
            )
        selected = candidates[0]
    else:
        selected = filing.primary_document or ""
        if selected not in names:
            raise AtlasError(
                "INCOMPLETE_COVERAGE", "The primary document is absent from the directory."
            )
    extension = selected.rsplit(".", 1)[-1].lower()
    if (numeric_facts and extension != "xml") or (
        not numeric_facts and extension not in {"htm", "html"}
    ):
        raise AtlasError(
            "INVALID_INPUT", "Select an XML instance or HTML document for this dataset."
        )
    return selected


def parsed_period(node: ET.Element) -> dict[str, Any]:
    values = {child.tag.removeprefix(XBRLI): (child.text or "").strip() for child in node}
    if len(values) != len(node) or set(values) not in (
        {"instant"},
        {"startDate", "endDate"},
        {"forever"},
    ):
        raise source_error("XBRL period is missing or duplicated.")
    for key, value in values.items():
        if key == "forever":
            continue
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise source_error("Unsupported XBRL reference date.")
        try:
            point = date.fromisoformat(value)
            if not 1900 <= point.year <= 2100:
                raise ValueError("date bounds")
        except ValueError:
            raise source_error("Invalid XBRL reference date.") from None
    if "startDate" in values and values["startDate"] > values["endDate"]:
        raise source_error("Reversed XBRL period.")
    return values


def instance_facts(
    snapshot: Snapshot, company: dict[str, Any], filing: Filing, concept: str | None = None
) -> list[dict[str, Any]]:
    root, scopes = scoped_xml(snapshot.body)
    if root.tag != XBRLI + "xbrl":
        raise source_error("The selected member is not an XBRL instance.")
    document_identity(root, company, filing)
    contexts: dict[str, dict[str, Any]] = {}
    units: dict[str, dict[str, Any]] = {}
    for child in root:
        ident = child.get("id")
        if child.tag == XBRLI + "context":
            if not ident or ident in contexts:
                raise source_error("Missing or duplicate XBRL context ID.")
            entities = child.findall(XBRLI + "entity/" + XBRLI + "identifier")
            periods = child.findall(XBRLI + "period")
            if len(entities) != 1 or len(periods) != 1:
                raise source_error("XBRL context requires one entity and period.")
            entity = entities[0]
            code = (entity.text or "").strip()
            if (
                entity.get("scheme") not in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
                or not re.fullmatch(r"[0-9]{1,10}", code)
                or code.zfill(10) != company["cik"]
            ):
                raise source_error("XBRL entity CIK differs from the selected issuer.")
            dimensions: list[dict[str, Any]] = []
            axes: set[str] = set()
            for node in child.iter():
                if node.tag not in {XBRLDI + "explicitMember", XBRLDI + "typedMember"}:
                    continue
                axis = expanded_qname(node.get("dimension"), scopes[node])
                if axis in axes or len(axes) >= 32:
                    raise source_error("Duplicate or oversized XBRL dimensions.")
                axes.add(axis)
                explicit = node.tag == XBRLDI + "explicitMember"
                if (explicit and len(node)) or (not explicit and len(node) != 1):
                    raise source_error("Invalid XBRL dimension member structure.")
                dimensions.append(
                    {
                        "dimension": node.get("dimension"),
                        "expanded_dimension": axis,
                        "kind": "explicitMember" if explicit else "typedMember",
                        "value": "".join(node.itertext()).strip(),
                        "expanded_member": expanded_qname(node.text, scopes[node])
                        if explicit
                        else None,
                        "typed_xml": None if explicit else ET.tostring(node[0], encoding="unicode"),
                    }
                )
            contexts[ident] = {
                "entity": code,
                "entity_scheme": entity.get("scheme"),
                "period": parsed_period(periods[0]),
                "dimensions": dimensions,
            }
        elif child.tag == XBRLI + "unit":
            if not ident or ident in units:
                raise source_error("Missing or duplicate XBRL unit ID.")

            def measures(node: ET.Element) -> list[str]:
                if not len(node) or any(c.tag != XBRLI + "measure" for c in node):
                    raise source_error("Invalid XBRL unit measures.")
                return [expanded_qname(c.text, scopes[c]) for c in node]

            divide = child.find(XBRLI + "divide")
            if divide is None:
                units[ident] = {"numerator": measures(child), "denominator": []}
            else:
                numerator, denominator = (
                    divide.find(XBRLI + "unitNumerator"),
                    divide.find(XBRLI + "unitDenominator"),
                )
                if len(child) != 1 or len(divide) != 2 or numerator is None or denominator is None:
                    raise source_error("Invalid divided XBRL unit.")
                units[ident] = {
                    "numerator": measures(numerator),
                    "denominator": measures(denominator),
                }
    rows = []
    for index, child in enumerate(root, 1):
        context, unit = child.get("contextRef"), child.get("unitRef")
        if context is None or unit is None:
            continue
        local = child.tag.rsplit("}", 1)[-1]
        if concept is not None and local != concept:
            continue
        if context not in contexts or unit not in units or len(child):
            raise source_error("Numeric XBRL fact has an invalid context, unit or nested value.")
        nil = child.get(NIL)
        if nil not in {None, "false", "0", "true", "1"} or (
            nil in {"true", "1"} and (child.text or "").strip()
        ):
            raise source_error("Invalid XBRL nil fact.")
        value = None if nil in {"true", "1"} else numeric((child.text or "").strip())
        if nil not in {"true", "1"} and value is None:
            raise source_error("Non-nil numeric XBRL fact lacks a value.")
        decimals = child.get("decimals")
        if (
            decimals is not None
            and decimals != "INF"
            and not re.fullmatch(r"-?[0-9]{1,3}", decimals)
        ):
            raise source_error("Invalid XBRL decimals attribute.")
        precision = child.get("precision")
        if precision is not None and (
            decimals is not None
            or (precision != "INF" and not re.fullmatch(r"[1-9][0-9]{0,2}", precision))
        ):
            raise source_error("Invalid or conflicting XBRL precision attributes.")
        period = contexts[context]["period"]
        definition = units[unit]
        num, den = definition["numerator"], definition["denominator"]
        label = "source_defined"
        if (
            len(num) == 1
            and not den
            and (
                re.fullmatch(re.escape(ISO4217) + r"[A-Z]{3}", num[0])
                or num[0] in {XBRLI + "shares", XBRLI + "pure"}
            )
        ):
            label = num[0].rsplit("}", 1)[-1]
        rows.append(
            {
                "concept": local,
                "expanded_concept": child.tag,
                "taxonomy_namespace": child.tag.partition("}")[0].removeprefix("{"),
                "context_id": context,
                "context": contexts[context],
                "unit_id": unit,
                "unit_definition": definition,
                "unit": label,
                "decimals": decimals,
                "precision": precision,
                "value": value,
                "status": "reported" if value is not None else "unavailable",
                "period": {
                    "kind": "instant"
                    if "instant" in period
                    else "forever"
                    if "forever" in period
                    else "duration",
                    "start": period.get("startDate"),
                    "end": period.get("endDate", period.get("instant")),
                },
                "accession": filing.accession,
                "filed_on": filing.filed_on,
                "accepted_at": filing.accepted_at,
                "published_at": None,
                "interpretation": "Issuer-reported numeric fact; economic role is not inferred.",
                "source": {
                    **reference(snapshot, f"/xbrl/*[{index}]", xml=True),
                    "expanded_element": child.tag,
                    "context_id": context,
                    "unit_id": unit,
                },
            }
        )
        if len(rows) > 50000:
            raise source_error("XBRL numeric fact limit exceeded.")
    return rows


def segment_rows(
    facts: list[dict[str, Any]], metrics: list[str], filing: Filing
) -> list[dict[str, Any]]:
    groups: dict[bytes, list[dict[str, Any]]] = {}
    for fact in facts:
        namespace = fact["taxonomy_namespace"]
        if not re.fullmatch(
            r"https?://fasb\.org/us-gaap/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?", namespace
        ):
            continue
        metric = next(
            (name for name in metrics if fact["concept"] in STATEMENT_METRICS[name].tags), None
        )
        if metric is None:
            continue
        dimensions = fact["context"]["dimensions"]
        segments = []
        for dim in dimensions:
            ns, _, local = dim["expanded_dimension"].removeprefix("{").partition("}")
            if local in SEGMENT_AXES and re.fullmatch(
                r"https?://(?:fasb\.org/(?:us-gaap|srt)|xbrl\.sec\.gov/srt)/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?",
                ns,
            ):
                segments.append({"role": SEGMENT_AXES[local], **dim})
        if not segments:
            continue
        unit = fact["unit_definition"]
        if (
            len(unit["numerator"]) != 1
            or unit["denominator"]
            or not re.fullmatch(re.escape(ISO4217) + r"[A-Z]{3}", unit["numerator"][0])
        ):
            continue
        if fact["period"]["kind"] not in {"instant", "duration"} or (
            (fact["period"]["kind"] == "instant") != STATEMENT_METRICS[metric].instant
        ):
            raise source_error("Selected segment concept has an incompatible actual period.")
        if fact["period"]["end"] and fact["period"]["end"] > (
            filing.report_date or filing.filed_on
        ):
            continue
        signature = canonical(
            [
                metric,
                fact["period"],
                fact["unit_definition"],
                sorted(dimensions, key=lambda d: d["expanded_dimension"]),
            ]
        )
        groups.setdefault(signature, []).append({**fact, "metric": metric, "segments": segments})
    rows = []
    for items in groups.values():
        row = dict(items[0])
        values = {r["value"] for r in items}
        if len(values) > 1:
            row.update(
                value=None,
                status="ambiguous",
                reason="Conflicting facts for the same segment, concept and actual period.",
            )
        row["duplicate_count"] = len(items)
        row["fact_sources"] = [r["source"] for r in items]
        rows.append(row)
    return rows


def filing_research(source: SECProvider, filing_inputs: Any, q: Any) -> dict[str, Any]:
    company, history, refs, truncated = filing_inputs(q.identifier or "")
    selected = [f for f in history if f.accession == q.accession and f.form in FILING_FORMS]
    if len(selected) != 1:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "The selected issuer history has no unique supported accession."
        )
    filing = selected[0]
    base, index, members = filing_directory(source, company, filing)
    sources = [refs[filing.accession], reference(index, "")]
    if q.dataset == "filing_documents":
        records = members
    else:
        numeric_facts = q.dataset in {"filing_facts", "segment_financials"}
        name = select_member(filing, members, q.document_name, numeric_facts=numeric_facts)
        snapshot = source.transport.get(base + name, 86400)
        if numeric_facts:
            records = instance_facts(snapshot, company, filing, q.concept)
            if q.dataset == "segment_financials":
                records = segment_rows(records, q.metrics or list(SEGMENT_METRICS), filing)
        else:
            from atlas_terminal.filing_html import html_records

            records = html_records(snapshot, q, filing)
        sources.append(
            reference(snapshot, "/xbrl", xml=True)
            if numeric_facts
            else {
                "raw_source_url": snapshot.url,
                "raw_sha256": snapshot.sha256,
                "retrieved_at": snapshot.retrieved_at,
                "text_line": 1,
            }
        )
    return {
        "company": company,
        "filing": asdict(filing),
        "records": records[: q.limit],
        "sources": sources,
        "coverage": {
            "matching_records": len(records),
            "returned_records": min(q.limit, len(records)),
            "truncated": len(records) > q.limit,
            "history_truncated": truncated,
            "point_in_time": False,
        },
        "limitations": [
            "One explicit accession and issuer-bound directory. No arbitrary URL or "
            "external schema fetch.",
            "Extracted XBRL instances retain actual periods, units, dimensions, nils "
            "and exact values;"
            "decimals are precision metadata, not scaling instructions.",
            "Segment selections recognize standard US-GAAP business/product/geography axes. "
            "Issuer-defined dimensions remain available in filing_facts without a "
            "semantic mapping.",
            "Crossed axes, segment totals and regional components can overlap; no "
            "summation is inferred."
            "Custom facts are not automatically classified as guidance, operational "
            "KPIs or non-GAAP.",
            "HTML tables preserve reported text, row/column spans and source coordinates. "
            "They do not establish a standardized financial statement or presentation hierarchy.",
            "Source document text is untrusted. Current retrieval is not "
            "release-time PIT availability.",
        ],
    }
