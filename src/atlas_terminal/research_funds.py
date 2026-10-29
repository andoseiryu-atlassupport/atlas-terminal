"""One explicitly selected SEC N-PORT portfolio, with reported values and identifiers."""

import re
from dataclasses import asdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from xml.etree.ElementTree import Element

from atlas_terminal.errors import AtlasError
from atlas_terminal.normalize import number
from atlas_terminal.providers import (
    DOCUMENT_PATTERN,
    SEC_ARCHIVES,
    SUBMISSIONS,
    Filing,
    SECProvider,
    Snapshot,
)
from atlas_terminal.public_sources import local_name, xml_root
from atlas_terminal.research_financials import reference
from atlas_terminal.research_models import ResearchQuery

NPORT_NAMESPACE = "http://www.sec.gov/edgar/nport"
NPORT_FORMS = {"NPORT-P", "NPORT-P/A"}
MAX_POSITIONS = 50_000
MAX_CANDIDATES = 6
BASE_PATH = "/edgarSubmission/formData"


def _invalid(message: str) -> AtlasError:
    return AtlasError("SOURCE_UNAVAILABLE", message)


def _children(node: Element, name: str) -> list[Element]:
    matches = [child for child in node if local_name(child) == name]
    if any(child.tag != "{" + NPORT_NAMESPACE + "}" + name for child in matches):
        raise _invalid("Unexpected namespace for a selected N-PORT field.")
    return matches


def _one(node: Element, path: str) -> Element | None:
    current = node
    for name in path.split("/"):
        children = _children(current, name)
        if len(children) > 1:
            raise _invalid("Ambiguous repeated N-PORT field.")
        if not children:
            return None
        current = children[0]
    return current


def _text(node: Element, path: str) -> str | None:
    element = _one(node, path)
    if element is None:
        return None
    if len(element):
        raise _invalid("Expected a scalar N-PORT field.")
    text = (element.text or "").strip()
    if len(text) > 4096:
        raise _invalid("N-PORT text field exceeds its supported bound.")
    return text or None


def _numeric(text: str | None) -> str | None:
    if text is None:
        return None
    if not re.fullmatch(r"[+-]?[0-9]{1,40}(?:\.[0-9]{1,30})?", text):
        raise _invalid("Invalid N-PORT decimal value.")
    return number(Decimal(text))


def _date(text: str | None) -> str:
    try:
        if text is None or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            raise ValueError
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise _invalid("Missing or invalid N-PORT reporting date.") from None


def _field(
    node: Element,
    path: str,
    snapshot: Snapshot,
    base: str,
    sources: dict[str, Any],
    key: str,
    *,
    numeric: bool = False,
) -> str | None:
    text = _text(node, path)
    if _one(node, path) is not None:
        sources[key] = reference(snapshot, base + "/" + path, xml=True)
    return _numeric(text) if numeric else text


def _category(
    node: Element, tag: str, snapshot: Snapshot, base: str, sources: dict[str, Any], key: str
) -> str | None:
    """The N-PORT choice can be a scalar code or an attributed OTHER element."""
    conditional = "assetConditional" if tag == "assetCat" else "issuerConditional"
    regular, other = _one(node, tag), _one(node, conditional)
    if regular is not None and other is not None:
        raise _invalid("Conflicting N-PORT classification alternatives.")
    if other is not None:
        value = other.attrib.get(tag)
        if value is not None:
            sources[key] = reference(snapshot, base + "/" + conditional + "/@" + tag, xml=True)
        return value
    return _field(node, tag, snapshot, base, sources, key)


def _attribute(
    node: Element,
    path: str,
    attribute: str,
    snapshot: Snapshot,
    base: str,
    sources: dict[str, Any],
    key: str,
    *,
    numeric: bool = False,
) -> str | None:
    child = _one(node, path)
    if child is None or attribute not in child.attrib:
        return None
    value = child.attrib[attribute]
    if len(value) > 4096:
        raise _invalid("N-PORT attribute exceeds its supported bound.")
    sources[key] = reference(snapshot, f"{base}/{path}/@{attribute}", xml=True)
    return _numeric(value) if numeric else value


def _identifiers(node: Element, snapshot: Snapshot, base: str) -> list[dict[str, Any]]:
    container = _one(node, "identifiers")
    if container is None:
        return []
    if len(container) > 32:
        raise _invalid("N-PORT identifier count exceeds its supported bound.")
    results = []
    counts: dict[str, int] = {}
    for child in container:
        kind = local_name(child)
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in {"isin", "ticker", "other"}:
            continue
        value = child.attrib.get("value")
        if value is None or len(value) > 256:
            raise _invalid("Invalid reported N-PORT identifier.")
        path = f"{base}/identifiers/{kind}[{counts[kind]}]"
        results.append(
            {
                "type": kind,
                "value": value,
                "description": child.attrib.get("otherDesc"),
                "source": reference(snapshot, path + "/@value", xml=True),
            }
        )
    return results


def parse_fund_holdings(snapshot: Snapshot, filing: Filing, cik: str, limit: int) -> dict[str, Any]:
    """Parse source bytes only; filing identity and actual report date must agree."""
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise AtlasError("INVALID_INPUT", "N-PORT limit must be between 1 and 500.")
    root = xml_root(snapshot.body)
    if root.tag != "{" + NPORT_NAMESPACE + "}edgarSubmission":
        raise _invalid("Expected SEC N-PORT edgarSubmission XML.")
    namespaces = {
        NPORT_NAMESPACE,
        "http://www.sec.gov/edgar/common",
        "http://www.sec.gov/edgar/nportcommon",
    }
    if any(
        element.tag.partition("}")[0].removeprefix("{") not in namespaces for element in root.iter()
    ):
        raise _invalid("Unexpected XML namespace in N-PORT document.")
    if _text(root, "headerData/submissionType") != filing.form or filing.form not in NPORT_FORMS:
        raise _invalid("N-PORT form does not match the selected filing.")
    form = _one(root, "formData")
    if form is None:
        raise _invalid("N-PORT formData is missing.")
    reported_cik = _text(form, "genInfo/regCik")
    if (
        reported_cik is None
        or not re.fullmatch(r"[0-9]{1,10}", reported_cik)
        or reported_cik.zfill(10) != cik.zfill(10)
    ):
        raise _invalid("N-PORT registrant does not match the selected issuer.")
    header_cik = _text(root, "headerData/filerInfo/filer/issuerCredentials/cik")
    if header_cik is not None and (
        not re.fullmatch(r"[0-9]{1,10}", header_cik) or header_cik.zfill(10) != cik.zfill(10)
    ):
        raise _invalid("N-PORT header registrant does not match the selected issuer.")
    header_series = _text(root, "headerData/filerInfo/seriesClassInfo/seriesId")
    if header_series is not None and header_series != _text(form, "genInfo/seriesId"):
        raise _invalid("N-PORT header and reported fund series disagree.")
    report_date = _date(_text(form, "genInfo/repPdDate"))
    if report_date != filing.report_date or report_date > filing.filed_on:
        raise _invalid("N-PORT reporting date does not match the selected filing.")
    fund_sources: dict[str, Any] = {}
    fund: dict[str, Any] = {}
    for key, path in {
        "registrant_name": "genInfo/regName",
        "registrant_cik": "genInfo/regCik",
        "registrant_lei": "genInfo/regLei",
        "series_name": "genInfo/seriesName",
        "series_id": "genInfo/seriesId",
        "series_lei": "genInfo/seriesLei",
        "report_date": "genInfo/repPdDate",
        "fiscal_year_end": "genInfo/repPdEnd",
        "is_final_filing": "genInfo/isFinalFiling",
        "total_assets": "fundInfo/totAssets",
        "total_liabilities": "fundInfo/totLiabs",
        "net_assets": "fundInfo/netAssets",
    }.items():
        fund[key] = _field(
            form, path, snapshot, BASE_PATH, fund_sources, key, numeric=path.startswith("fundInfo/")
        )
    if fund["fiscal_year_end"] is not None:
        _date(fund["fiscal_year_end"])
    fund["monetary_unit"] = "USD"
    fund["field_sources"] = fund_sources
    container = _one(form, "invstOrSecs")
    if container is None:
        raise _invalid("N-PORT investment list is missing; absence is not an empty portfolio.")
    positions = _children(container, "invstOrSec")
    if len(positions) > MAX_POSITIONS:
        raise _invalid("N-PORT investment count exceeds the supported bound.")
    records = []
    missing = 0
    for i, node in enumerate(positions):
        base = f"{BASE_PATH}/invstOrSecs/invstOrSec[{i + 1}]"
        sources: dict[str, Any] = {}
        record: dict[str, Any] = {
            "position": i + 1,
            "report_date": report_date,
            "accession": filing.accession,
            "series_id": fund["series_id"],
            "value_unit": "USD",
            "percentage_unit": "percent_of_net_assets",
            "source": reference(snapshot, base, xml=True),
        }
        for key, tag in {
            "issuer_name": "name",
            "issuer_lei": "lei",
            "security_title": "title",
            "cusip": "cusip",
            "balance": "balance",
            "balance_unit_code": "units",
            "balance_unit_description": "descOthUnits",
            "denomination_currency": "curCd",
            "reported_value": "valUSD",
            "reported_percentage": "pctVal",
            "payoff_profile": "payoffProfile",
            "investment_country": "invCountry",
            "restricted_security": "isRestrictedSec",
            "fair_value_level": "fairValLevel",
        }.items():
            record[key] = _field(
                node,
                tag,
                snapshot,
                base,
                sources,
                key,
                numeric=tag in {"balance", "valUSD", "pctVal"},
            )
        record["asset_category"] = _category(
            node, "assetCat", snapshot, base, sources, "asset_category"
        )
        record["issuer_category"] = _category(
            node, "issuerCat", snapshot, base, sources, "issuer_category"
        )
        for key, path, attr in (
            ("asset_category_description", "assetConditional", "desc"),
            ("issuer_category_description", "issuerConditional", "desc"),
            ("conditional_currency", "currencyConditional", "curCd"),
            ("reported_exchange_rate", "currencyConditional", "exchangeRt"),
        ):
            record[key] = _attribute(
                node, path, attr, snapshot, base, sources, key, numeric=attr == "exchangeRt"
            )
        if (
            record["denomination_currency"] is not None
            and record["conditional_currency"] is not None
        ):
            raise _invalid("Conflicting N-PORT currency alternatives.")
        if record["conditional_currency"] is not None:
            record["denomination_currency"] = record["conditional_currency"]
            sources["denomination_currency"] = sources["conditional_currency"]
        record["identifiers"] = _identifiers(node, snapshot, base)
        record["derivative_details_reported"] = _one(node, "derivInfo") is not None
        record["status"] = "reported" if record["reported_value"] is not None else "unavailable"
        record["field_sources"] = sources
        missing += record["reported_value"] is None
        if i < limit:
            records.append(record)
    return {
        "records": records,
        "fund": fund,
        "allocations": _allocations(records, fund, complete=len(records) == len(positions)),
        "sources": [reference(snapshot, "/edgarSubmission", xml=True)],
        "coverage": {
            "matching_records": len(positions),
            "returned_records": len(records),
            "truncated": len(positions) > limit,
            "missing_reported_values": missing,
            "report_date": report_date,
            "point_in_time": False,
            "scope": "one_selected_fund_series_filing",
        },
        "limitations": [
            "One publicly filed N-PORT portfolio as of its reported date; filing lags and "
            "non-public reporting months limit coverage. Current ETF holdings are not established.",
            "Reported pctVal is percent of net assets, not a normalized weight across returned "
            "positions. Values may be negative and percentages need not sum to 100.",
            "valUSD and fund totals are reported US dollars; curCd is the security denomination. "
            "No currency conversion or thousands multiplier is inferred.",
            "Reported identifiers and classification codes are retained without ticker/ISIN/CUSIP "
            "resolution, industry mapping or a claim that this fund is an ETF.",
            "Derivative metadata is flagged; contract terms, notional, delta and look-through "
            "economic exposures are not normalized. Market values are not economic exposures.",
            "Country and asset-category allocations sum reported USD market values and divide "
            "by reported net assets; they are unavailable for truncated or missing-value "
            "portfolios. They do not normalize weights or include unlisted cash positions.",
            "Amendments are selected explicitly and are not merged with original filings. "
            "Current retrieval does not establish historical publication availability.",
        ],
    }


def _allocations(
    records: list[dict[str, Any]], fund: dict[str, Any], *, complete: bool
) -> dict[str, Any]:
    reason = None
    if not complete:
        reason = "truncated_positions"
    elif any(row["reported_value"] is None for row in records):
        reason = "missing_reported_values"
    elif fund["net_assets"] is None or Decimal(fund["net_assets"]) <= 0:
        reason = "missing_or_nonpositive_net_assets"
    if reason:
        return {"status": "unavailable", "reason": reason, "records": []}
    denominator = Decimal(fund["net_assets"])
    grouped: list[dict[str, Any]] = []
    with localcontext() as context:
        context.prec = 128
        context.rounding = ROUND_HALF_EVEN
        for dimension in ("investment_country", "asset_category"):
            groups: dict[str | None, list[dict[str, Any]]] = {}
            for record in records:
                groups.setdefault(record[dimension], []).append(record)
            for group, rows in sorted(groups.items(), key=lambda item: item[0] or ""):
                value = sum((Decimal(row["reported_value"]) for row in rows), Decimal(0))
                percent = (value / denominator * 100).quantize(Decimal("0.000000000000000001"))
                grouped.append(
                    {
                        "dimension": dimension,
                        "reported_group": group,
                        "classification_status": "reported" if group is not None else "unavailable",
                        "value": number(percent),
                        "unit": "percent_of_net_assets",
                        "status": "derived",
                        "reported_value_sum": number(value),
                        "reported_value_unit": "USD",
                        "positions": [row["position"] for row in rows],
                        "derivative_positions": sum(
                            row["derivative_details_reported"] for row in rows
                        ),
                    }
                )
    return {
        "status": "derived",
        "basis": "reported_market_value_not_economic_exposure",
        "formula": "100 * SUM(selected input reported_value) / reported net_assets",
        "rounding": "half_even_18_decimal_places",
        "records": grouped,
        "denominator": {
            "value": fund["net_assets"],
            "unit": "USD",
            "source": fund["field_sources"]["net_assets"],
        },
        "inputs": [
            {
                "position": row["position"],
                "value": row["reported_value"],
                "unit": "USD",
                "status": "reported",
                "source": row["field_sources"]["reported_value"],
                "classifications": {
                    field: {"value": row[field], "source": row["field_sources"].get(field)}
                    for field in ("investment_country", "asset_category")
                },
            }
            for row in records
        ],
    }


def _filing_source(source: SECProvider, cik: str, filing: Filing) -> dict[str, Any]:
    snapshot = source.transport.get(f"{SUBMISSIONS}CIK{cik}.json", 900)
    pages = [(snapshot, "/filings/recent")]
    for archive in snapshot.data["filings"].get("files", [])[: source.max_pages]:
        pages.append((source.transport.get(SUBMISSIONS + archive["name"], 900), ""))
    for page, base in pages:
        rows = page.data["filings"]["recent"] if base else page.data
        accessions = rows.get("accessionNumber", [])
        if filing.accession in accessions:
            return reference(page, f"{base}/accessionNumber/{accessions.index(filing.accession)}")
    raise _invalid("Selected N-PORT filing lacks submissions evidence.")


def fund_holdings(source: SECProvider, query: ResearchQuery) -> dict[str, Any]:
    """Read a validated issuer's exact accession; never accept arbitrary filing URLs."""
    if not query.identifier or not query.accession:
        raise AtlasError("INVALID_INPUT", "fund_holdings requires identifier and accession.")
    company, history, truncated = source.history(source.resolve(query.identifier))
    matching = [f for f in history if f.accession == query.accession and f.form in NPORT_FORMS]
    if len(matching) != 1:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Selected accession is not an available N-PORT filing."
        )
    filing = matching[0]
    filing_ref = _filing_source(source, company.cik, filing)
    base = f"{SEC_ARCHIVES}{int(company.cik)}/{filing.accession.replace('-', '')}/"
    index_snapshot = source.transport.get(base + "index.json", 86400)
    items = index_snapshot.data.get("directory", {}).get("item", [])
    if not isinstance(items, list) or len(items) > 10_000:
        raise _invalid("Invalid or oversized N-PORT filing directory.")
    names = [item.get("name") if isinstance(item, dict) else None for item in items]
    if any(
        not isinstance(name, str) or "/" in name or not re.fullmatch(DOCUMENT_PATTERN, name)
        for name in names
    ):
        raise AtlasError("INVALID_SOURCE_PATH", "Unsafe N-PORT filing directory filename.")
    if len(names) != len(set(names)):
        raise _invalid("Ambiguous repeated N-PORT filing filename.")
    candidates: list[str] = []
    if filing.primary_document and filing.primary_document.lower().endswith(".xml"):
        candidates.append(filing.primary_document.rsplit("/", 1)[-1])
    candidates.extend(
        sorted(
            (
                name
                for name in names
                if isinstance(name, str)
                and name.lower().endswith(".xml")
                and name not in candidates
            ),
            key=lambda name: (
                not any(part in name.lower() for part in ("nport", "primary", "fund")),
                name,
            ),
        )
    )
    for name in candidates[:MAX_CANDIDATES]:
        snapshot = source.transport.get(base + name, 86400)
        root = xml_root(snapshot.body)
        if root.tag != "{" + NPORT_NAMESPACE + "}edgarSubmission":
            continue
        result = parse_fund_holdings(snapshot, filing, company.cik, query.limit)
        result["company"] = company.model_dump()
        result["filing"] = asdict(filing)
        result["sources"].extend([filing_ref, reference(index_snapshot, "")])
        result["coverage"]["history_truncated"] = truncated
        return result
    raise AtlasError(
        "INCOMPLETE_COVERAGE", "No N-PORT XML in the first six safe filing candidates."
    )
