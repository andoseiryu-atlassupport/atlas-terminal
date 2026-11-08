"""Conservative EDINET annual mappings; no fuzzy tags, standalone fallback or zero filling."""

import re
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any

from atlas_terminal.buffett_models import METRICS
from atlas_terminal.edinet_extensions import capex_extensions
from atlas_terminal.edinet_mappings import (
    DEFINITIONS,
    MAPPING_VERSION,
    REVENUE_ALIASES,
    mapped_fact,
    measurement_basis,
    taxonomy_family,
)
from atlas_terminal.edinet_notes import toyota_notes
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_buffett import CONTEXT
from atlas_terminal.research_financials import reference
from atlas_terminal.research_japan_companies import NIL, filing_facts, scoped_xml, xbrl_member
from atlas_terminal.util import canonical, digest

# Exact core concepts only. Cash-flow purchases are signed cash outflows in these taxonomies.
TAGS: dict[str, dict[str, tuple[str, ...]]] = {
    "Japan GAAP": {
        "revenue": ("NetSales",),
        "net_income": ("ProfitLossAttributableToOwnersOfParent",),
        "operating_income": ("OperatingIncome",),
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
        "capital_expenditure": ("PurchaseOfPropertyPlantAndEquipmentInvCF",),
        "stockholders_equity": ("ShareholdersEquity", "AccumulatedOtherComprehensiveIncome"),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "interest_expense": ("InterestExpensesNOE", "InterestExpensesOpeCF"),
        "stock_based_compensation": ("ShareBasedCompensationExpensesOpeCF",),
    },
    "IFRS": {
        "revenue": ("RevenueIFRS",),
        "net_income": ("ProfitLossAttributableToOwnersOfParentIFRS",),
        "operating_income": ("OperatingProfitLossIFRS",),
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivitiesIFRS",),
        "capital_expenditure": ("PurchaseOfPropertyPlantAndEquipmentInvCFIFRS",),
        "stockholders_equity": ("EquityAttributableToOwnersOfParentIFRS",),
        "assets": ("AssetsIFRS",),
        "liabilities": ("LiabilitiesIFRS",),
        "interest_expense": ("InterestExpensesOpeCFIFRS",),
        "stock_based_compensation": ("ShareBasedPaymentExpensesOpeCFIFRS",),
    },
}
INSTANTS = {"stockholders_equity", "assets", "liabilities"}
JPY = "{http://www.xbrl.org/2003/iso4217}JPY"


def consolidation(context: dict[str, Any]) -> str:
    if context.get("unsupported_content"):
        return "unsupported_dimensions"
    dimensions = context["dimensions"]
    if not dimensions:
        return "consolidated_default"
    if len(dimensions) != 1:
        return "unsupported_dimensions"
    dimension = dimensions[0]
    axis, _, name = dimension["expanded_dimension"].rpartition("}")
    namespace = axis.removeprefix("{")
    if (
        dimension["kind"] != "explicitMember"
        or taxonomy_family(namespace) not in {"jppfs", "jpcrp"}
        or name != "ConsolidatedAndNonConsolidatedAxis"
    ):
        return "unsupported_dimensions"
    member = dimension["expanded_member"]
    if member == "{" + namespace + "}NonConsolidatedMember":
        return "standalone"
    if member == "{" + namespace + "}ConsolidatedMember":
        return "consolidated"
    return "unsupported_dimensions"


def metadata(snapshot: Snapshot) -> dict[str, Any]:
    member, body = xbrl_member(snapshot.body)
    root, _ = scoped_xml(body)
    context_ids = {
        child.get("id")
        for child in root
        if child.tag == "{http://www.xbrl.org/2003/instance}context"
    }
    entities = {
        child.get("id"): child.findtext(
            "{http://www.xbrl.org/2003/instance}entity/{http://www.xbrl.org/2003/instance}identifier"
        )
        for child in root
        if child.tag == "{http://www.xbrl.org/2003/instance}context"
    }
    found: dict[str, list[dict[str, Any]]] = {}
    for index, child in enumerate(root, 1):
        namespace, _, tag = child.tag.rpartition("}")
        if taxonomy_family(namespace.removeprefix("{")) != "jpdei":
            continue
        if tag not in {
            "AccountingStandardsDEI",
            "WhetherConsolidatedFinancialStatementsArePreparedDEI",
            "CurrentFiscalYearStartDateDEI",
            "CurrentFiscalYearEndDateDEI",
            "EDINETCodeDEI",
            "SecurityCodeDEI",
        }:
            continue
        found.setdefault(tag, []).append(
            {
                "entity": entities.get(child.get("contextRef")),
                "value": (child.text or "").strip()
                if child.get(NIL) not in {"true", "1"}
                and not len(child)
                and child.get("contextRef") in context_ids
                else None,
                "source": reference(snapshot, f"/xbrl/*[{index}]", xml=True)
                | {
                    "archive_member": member,
                    "member_sha256": digest(body),
                    "expanded_element": child.tag,
                    "context_id": child.get("contextRef"),
                },
            }
        )
    return found


def metadata_value(info: dict[str, Any], key: str) -> str | None:
    cells = info.get(key, [])
    identities = {(cell["entity"], cell["value"]) for cell in cells}
    return next(iter(identities))[1] if len(identities) == 1 else None


def fact_identity(fact: dict[str, Any]) -> bytes:
    """Semantic context/unit identity; prefix aliases and XML IDs are not meaning."""
    context = fact["context"]
    dimensions = [
        {key: value for key, value in item.items() if key.startswith("expanded_") or key == "kind"}
        for item in context["dimensions"]
    ]
    unit = fact["unit_definition"]
    return canonical(
        {
            "entity": context["entity"],
            "entity_scheme": context["entity_scheme"],
            "period": context["period"],
            "dimensions": sorted(dimensions, key=canonical),
            "unsupported_content": context.get("unsupported_content", False),
            "unit": {
                part: sorted(item["expanded_measure"] for item in unit[part])
                for part in ("numerator", "denominator")
            },
        }
    )


def annual_period(start: str, end: str) -> bool:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        following = last + timedelta(days=1)
        return following.year == first.year + 1 and (following.month, following.day) == (
            first.month,
            first.day,
        )
    except (ValueError, TypeError):
        return False


def normalize_filing(snapshot: Snapshot, filing: dict[str, Any]) -> dict[str, Any]:
    with localcontext(CONTEXT):
        return _normalize_filing(snapshot, filing)


def _normalize_filing(snapshot: Snapshot, filing: dict[str, Any]) -> dict[str, Any]:
    facts = filing_facts(snapshot, filing, None)
    info = metadata(snapshot)
    standard = metadata_value(info, "AccountingStandardsDEI")
    consolidated = metadata_value(info, "WhetherConsolidatedFinancialStatementsArePreparedDEI")
    entities = {cell["entity"] for cells in info.values() for cell in cells}
    start, end = filing["report_period_start"], filing["report_period_end"]
    problem = None
    if metadata_value(info, "EDINETCodeDEI") != filing["edinet_code"]:
        problem = "dei_issuer_missing_or_mismatched"
    elif len(entities) != 1 or None in entities:
        problem = "dei_entities_ambiguous"
    elif metadata_value(info, "SecurityCodeDEI") not in {None, filing["security_code"]}:
        problem = "dei_security_code_mismatched"
    elif info.get("SecurityCodeDEI") and metadata_value(info, "SecurityCodeDEI") is None:
        problem = "dei_security_code_ambiguous"
    elif standard not in TAGS:
        problem = "unsupported_or_ambiguous_accounting_standard"
    elif consolidated not in {"true", "1"}:
        problem = "consolidated_statements_not_confirmed"
    elif (
        metadata_value(info, "CurrentFiscalYearStartDateDEI") != start
        or metadata_value(info, "CurrentFiscalYearEndDateDEI") != end
    ):
        problem = "dei_filing_period_missing_or_mismatched"
    elif not annual_period(start, end):
        problem = "fiscal_period_not_twelve_months"
    period = {"start": start, "end": end}
    extensions = capex_extensions(snapshot, filing) if not problem and standard == "IFRS" else {}

    def cell(metric: str, *, opening: bool = False) -> dict[str, Any]:
        target = (date.fromisoformat(start) - timedelta(days=1)).isoformat() if opening else end
        actual_period = {"start": None if metric in INSTANTS else start, "end": target}
        tags = TAGS.get(standard or "", {}).get(metric, ())
        if metric == "revenue":
            tags = tuple(tag for _, tag in REVENUE_ALIASES.get(standard or "", ())) + (
                "TotalNetRevenuesIFRS",
            )
        if metric == "capital_expenditure":
            tags += tuple(key[1] for key in extensions)
        result: dict[str, Any] = {
            "metric": metric,
            "value": None,
            "unit": "JPY",
            "status": "unavailable",
            "period": actual_period,
            "reason": problem,
            "source": None,
            "document_id": filing["document_id"],
            "filed_at": filing["filed_at"],
            "accounting_standard": standard,
            "consolidation": "consolidated",
            "attribution_scope": "owners_of_parent"
            if metric in {"net_income", "stockholders_equity"}
            else "group",
            "input_origin": "edinet_reported",
            "filing_source": filing["source"],
            "normalization_version": MAPPING_VERSION,
            "definition_id": DEFINITIONS[metric],
            "evidence_type": "numeric_xbrl",
            "expected_tags": list(tags),
            "fact_inputs": [],
        }
        if problem:
            return result
        wanted_period = (
            {"instant": target} if metric in INSTANTS else {"startDate": start, "endDate": target}
        )
        possible = [fact for fact in facts if fact["concept"] in tags]
        candidates = [
            fact
            for fact in possible
            if (
                mapped_fact(
                    fact,
                    metric,
                    standard or "",
                    TAGS.get(standard or "", {}).get(metric, ()),
                    filing,
                )
                or (
                    metric == "capital_expenditure"
                    and (fact["taxonomy_namespace"], fact["concept"]) in extensions
                )
            )
            and fact["context"]["period"] == wanted_period
            and fact["context"]["entity"] in entities
            and consolidation(fact["context"]) in {"consolidated", "consolidated_default"}
        ]
        expected = 2 if metric == "stockholders_equity" and standard == "Japan GAAP" else 1
        if not candidates or (expected == 2 and {f["concept"] for f in candidates} != set(tags)):
            if candidates:
                reason = (
                    "duplicate_or_ambiguous_facts"
                    if len(candidates) >= expected
                    else "incomplete_parent_equity_components"
                )
            elif not possible:
                reason = "supported_tag_not_reported"
            elif not any(mapped_fact(f, metric, standard or "", tags, filing) for f in possible):
                reason = "unsupported_taxonomy"
            elif not any(f["context"]["period"] == wanted_period for f in possible):
                reason = "period_mismatch"
            elif not any(f["context"]["entity"] in entities for f in possible):
                reason = "context_entity_mismatch"
            else:
                reason = "standalone_or_dimensioned_facts_only"
            result.update(
                reason=reason, candidate_facts=possible[:8], candidates_truncated=len(possible) > 8
            )
            return result
        # Deterministic evidence order is presentation only; every fact is checked.
        candidates.sort(key=lambda f: canonical(f["source"]))
        result["fact_inputs"] = candidates
        for fact in candidates:
            if fact.get("scale") is not None or fact.get("sign") is not None:
                result["reason"] = "unsupported_inline_scale_or_sign"
                return result
            unit = fact["unit_definition"]
            if unit["denominator"] or [item["expanded_measure"] for item in unit["numerator"]] != [
                JPY
            ]:
                result["reason"] = "unit_not_whole_jpy"
                return result
            if fact["decimals"] is not None and not re.fullmatch(
                r"INF|-?[0-9]{1,3}", fact["decimals"]
            ):
                result["reason"] = "unsupported_precision"
                return result
            if fact["value"] is None:
                result["reason"] = "reported_nil"
                return result
            value = Decimal(fact["value"])
            if not value.is_finite() or abs(value) > Decimal("1e30"):
                result["reason"] = "invalid_numeric_value"
                return result
        groups: dict[str, list[dict[str, Any]]] = {}
        for fact in candidates:
            # Japan GAAP parent equity has two additive operands. Other registered
            # aliases are alternative representations, never additive components.
            key = fact["concept"] if expected == 2 else metric
            if metric == "capital_expenditure":
                extension = extensions.get((fact["taxonomy_namespace"], fact["concept"]))
                if extension:
                    key = extension["operand"]
                    fact["mapping_evidence"] = extension["evidence"]
            groups.setdefault(key, []).append(fact)
        operands: list[dict[str, Any]] = []
        for key, group in sorted(groups.items()):
            identities = {fact_identity(fact) for fact in group}
            values = {Decimal(fact["value"]) for fact in group}
            if len(identities) != 1 or len(values) != 1:
                result["reason"] = "duplicate_or_ambiguous_facts"
                result["ambiguity"] = (
                    "conflicting_values" if len(values) != 1 else "conflicting_contexts"
                )
                return result
            operands.append(
                {
                    "operand": key,
                    "value": format(next(iter(values)), "f"),
                    "source_locations": [fact["source"] for fact in group],
                }
            )
        if expected == 2 and len({fact_identity(fact) for fact in candidates}) != 1:
            result["reason"] = "equity_component_contexts_differ"
            return result
        split_capex = metric == "capital_expenditure" and any(key != metric for key in groups)
        if split_capex:
            required = {"ppe_excluding_rental_assets", "rental_assets"}
            if not required <= groups.keys() or groups.keys() - required - {metric}:
                result["reason"] = "incomplete_or_ambiguous_gross_ppe_components"
                return result
            if len({fact_identity(fact) for fact in candidates}) != 1:
                result["reason"] = "capex_component_contexts_differ"
                return result
            total = next((item for item in operands if item["operand"] == metric), None)
            operands = [item for item in operands if item["operand"] in required]
            if total and Decimal(total["value"]) != sum(
                (Decimal(item["value"]) for item in operands), Decimal(0)
            ):
                result["reason"] = "conflicting_gross_ppe_total_and_components"
                return result
            result["corroborating_total"] = total
        with localcontext(CONTEXT):
            value = sum((Decimal(operand["value"]) for operand in operands), Decimal(0))
            formula = "reported_value"
            if expected == 2:
                formula = "ShareholdersEquity + AccumulatedOtherComprehensiveIncome"
            if metric == "capital_expenditure":
                if any(Decimal(item["value"]) > 0 for item in operands):
                    result["reason"] = "expected_nonpositive_cash_outflow"
                    return result
                value = -value
                formula = "-reported_cash_outflow"
                if split_capex:
                    formula = "-(ppe_excluding_rental_assets + rental_assets)"
            elif (
                metric in {"assets", "liabilities", "interest_expense", "stock_based_compensation"}
                and value < 0
            ):
                result["reason"] = "invalid_negative_input"
                return result
        result.update(
            value=format(value, "f"),
            status="reported" if formula == "reported_value" else "normalized",
            source=candidates[0]["source"]
            | {
                "taxonomy": taxonomy_family(candidates[0]["taxonomy_namespace"])
                or "issuer_extension",
                "tag": candidates[0]["concept"],
            },
            normalization={
                "formula": formula,
                "amount_scale": "1",
                "decimals_role": "precision_only",
                "operands": operands,
                "equivalent_duplicate_count": len(candidates) - len(operands),
            },
            source_locations=[fact["source"] for fact in candidates],
            measurement_bases=sorted(
                {measurement_basis(metric, fact["concept"]) for fact in candidates}
            ),
            comparability_key=f"{standard}:consolidated:{candidates[0]['context']['entity']}:{DEFINITIONS[metric]}",
        )
        return result

    cells: dict[str, dict[str, Any]] = {metric: cell(metric) for metric in METRICS}
    note_problem = None
    notes: dict[str, list[dict[str, Any]]] = {}
    if not problem and standard == "IFRS":
        eligible = {
            fact["context_id"]
            for fact in facts
            if fact["context"]["period"] == {"startDate": start, "endDate": end}
            and fact["context"]["entity"] in entities
            and consolidation(fact["context"]) in {"consolidated", "consolidated_default"}
        }
        try:
            notes = toyota_notes(snapshot, filing, eligible)
        except AtlasError:
            note_problem = "unsupported_note_structure"
    for metric, observations in notes.items():
        current = cells[metric]
        for observation in observations:
            observation["context"] = next(
                fact["context"]
                for fact in facts
                if fact["context_id"] == observation["source"]["context_id"]
            )
        current["note_inputs"] = observations
        values = {Decimal(item["value"]) for item in observations}
        if len(values) != 1:
            current.update(value=None, status="unavailable", reason="conflicting_note_disclosures")
            continue
        value = next(iter(values))
        if not value.is_finite() or value < 0 or value > Decimal("1e30"):
            current.update(value=None, status="unavailable", reason="invalid_note_amount")
            continue
        if current["value"] is not None and Decimal(current["value"]) != value:
            current.update(
                value=None, status="unavailable", reason="numeric_and_note_measurements_differ"
            )
            continue
        if current["value"] is None and current["reason"] not in {
            "supported_tag_not_reported",
            "unsupported_taxonomy",
            "period_mismatch",
            "standalone_or_dimensioned_facts_only",
        }:
            continue
        definition = (
            "share_based_compensation_expense_v1"
            if metric == "stock_based_compensation"
            else "total_interest_expense_including_financial_operations_v1"
            if metric == "interest_expense"
            else DEFINITIONS[metric]
        )
        current.update(
            value=format(value, "f"),
            status="normalized",
            reason=None,
            evidence_type=observations[0]["evidence_type"],
            definition_id=definition,
            source=observations[0]["source"],
            source_locations=[item["source"] for item in observations]
            + [fact["source"] for fact in current["fact_inputs"]],
            measurement_bases=sorted({item["measurement_basis"] for item in observations}),
            comparability_key=f"{standard}:consolidated:{next(iter(entities))}:{definition}",
            normalization={
                "formula": observations[0]["formula"],
                "amount_scale": "1000000",
                "decimals_role": "not_applicable_to_note_display_unit",
                "equivalent_duplicate_count": len(observations) - 1,
            },
        )
    return {
        "period": period,
        "metadata": info,
        "accounting_standard": standard,
        "consolidation": "consolidated" if consolidated in {"true", "1"} else "unconfirmed",
        "reason": problem,
        "cells": cells,
        "note_extraction_reason": note_problem,
        "opening_equity": cell("stockholders_equity", opening=True),
    }
