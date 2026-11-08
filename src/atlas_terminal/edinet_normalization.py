"""Conservative EDINET annual mappings; no fuzzy tags, standalone fallback or zero filling."""

import re
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any

from atlas_terminal.buffett_models import METRICS
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_buffett import CONTEXT
from atlas_terminal.research_financials import reference
from atlas_terminal.research_japan_companies import NIL, filing_facts, scoped_xml, xbrl_member
from atlas_terminal.util import digest

MAPPING_VERSION = "edinet_consolidated_annual_v1"
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


def taxonomy_family(namespace: str) -> str | None:
    match = re.fullmatch(
        r"http://disclosure\.edinet-fsa\.go\.jp/taxonomy/(jpdei|jppfs|jpigp|jpcrp)/"
        r"(20[0-9]{2}-[0-9]{2}-[0-9]{2})/\1_cor",
        namespace,
    )
    if not match:
        return None
    try:
        date.fromisoformat(match[2])
    except ValueError:
        return None
    return match[1]


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
    # Even equal duplicates need review; selection must not depend on XML order.
    return cells[0]["value"] if len(cells) == 1 else None


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
    family = "jpigp" if standard == "IFRS" else "jppfs"

    def cell(metric: str, *, opening: bool = False) -> dict[str, Any]:
        target = (date.fromisoformat(start) - timedelta(days=1)).isoformat() if opening else end
        actual_period = {"start": None if metric in INSTANTS else start, "end": target}
        tags = TAGS.get(standard or "", {}).get(metric, ())
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
            if taxonomy_family(fact["taxonomy_namespace"]) == family
            and fact["context"]["period"] == wanted_period
            and fact["context"]["entity"] in entities
            and consolidation(fact["context"]) in {"consolidated", "consolidated_default"}
        ]
        expected = 2 if metric == "stockholders_equity" and standard == "Japan GAAP" else 1
        if len(candidates) != expected or (
            expected == 2 and {fact["concept"] for fact in candidates} != set(tags)
        ):
            if candidates:
                reason = (
                    "duplicate_or_ambiguous_facts"
                    if len(candidates) >= expected
                    else "incomplete_parent_equity_components"
                )
            elif not possible:
                reason = "supported_tag_not_reported"
            elif not any(taxonomy_family(f["taxonomy_namespace"]) == family for f in possible):
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
        # A component sum must be from one actual context, not just two similarly dated cells.
        if expected == 2 and len({fact["context_id"] for fact in candidates}) != 1:
            result.update(reason="equity_component_contexts_differ", fact_inputs=candidates)
            return result
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
        with localcontext(CONTEXT):
            value = sum((Decimal(fact["value"]) for fact in candidates), Decimal(0))
            formula = "reported_value"
            if expected == 2:
                formula = "ShareholdersEquity + AccumulatedOtherComprehensiveIncome"
            if metric == "capital_expenditure":
                if value > 0:
                    result["reason"] = "expected_nonpositive_cash_outflow"
                    return result
                value = -value
                formula = "-reported_cash_outflow"
            elif (
                metric in {"assets", "liabilities", "interest_expense", "stock_based_compensation"}
                and value < 0
            ):
                result["reason"] = "invalid_negative_input"
                return result
        tag_key = "+".join(sorted(fact["concept"] for fact in candidates))
        result.update(
            value=format(value, "f"),
            status="reported" if formula == "reported_value" else "normalized",
            source=candidates[0]["source"] | {"taxonomy": family, "tag": candidates[0]["concept"]},
            normalization={
                "formula": formula,
                "amount_scale": "1",
                "decimals_role": "precision_only",
            },
            comparability_key=f"{standard}:consolidated:{candidates[0]['context']['entity']}:{metric}:{tag_key}",
        )
        return result

    return {
        "period": period,
        "metadata": info,
        "accounting_standard": standard,
        "consolidation": "consolidated" if consolidated in {"true", "1"} else "unconfirmed",
        "reason": problem,
        "cells": {metric: cell(metric) for metric in METRICS},
        "opening_equity": cell("stockholders_equity", opening=True),
    }
