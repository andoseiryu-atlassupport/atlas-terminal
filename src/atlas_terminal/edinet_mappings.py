"""Reviewed concept identities, separate from the unchanged scoring model.

Do not add fuzzy matching here. Issuer extensions must match the issuer, reporting
year and EDINET submission namespace. See docs/japan-buffett.md for definitions.
"""

import re
from typing import Any

MAPPING_VERSION = "edinet_consolidated_annual_v2"
JAPAN_MODEL_VERSION = "buffett_quantitative_jp_disclosures_v2"

# These summary concepts describe the same consolidated revenue, provided that
# the context passes the same entity/period/dimension checks as a statement fact.
REVENUE_ALIASES = {
    "Japan GAAP": (("jppfs", "NetSales"), ("jpcrp", "NetSalesSummaryOfBusinessResults")),
    "IFRS": (("jpigp", "RevenueIFRS"), ("jpcrp", "RevenueIFRSSummaryOfBusinessResults")),
}

DEFINITIONS = {
    "revenue": "consolidated_revenue_v1",
    "net_income": "profit_attributable_to_owners_of_parent_v1",
    "operating_income": "consolidated_operating_profit_v1",
    "operating_cash_flow": "reported_net_operating_cash_flow_v1",
    "capital_expenditure": "gross_cash_purchases_of_ppe_including_rental_assets_v1",
    "stockholders_equity": "equity_attributable_to_owners_of_parent_v1",
    "assets": "consolidated_total_assets_v1",
    "liabilities": "consolidated_total_liabilities_v1",
    "interest_expense": "reported_interest_expense_v1",
    "stock_based_compensation": "share_based_compensation_cf_adjustment_v1",
}


def toyota_extension(namespace: str, filing: dict[str, Any]) -> bool:
    """An exact namespace grammar, not a substring or issuer-name heuristic."""
    if filing["edinet_code"] != "E02144" or filing["security_code"] != "72030":
        return False
    end = filing["report_period_end"]
    if end not in {"2024-03-31", "2025-03-31", "2026-03-31"}:
        return False
    return namespace == (
        "http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/"
        + end
        + "/01/"
        + filing["filed_at"][:10]
    )


def measurement_basis(metric: str, tag: str) -> str:
    if metric == "capital_expenditure":
        return "gross_cash_outflow"
    if metric == "interest_expense":
        return "expense_addback_in_operating_cash_flow" if "OpeCF" in tag else "profit_loss_expense"
    if metric == "stock_based_compensation":
        return "operating_cash_flow_reconciliation_adjustment"
    return (
        "instant_balance"
        if metric in {"assets", "liabilities", "stockholders_equity"}
        else "period_flow"
    )


def taxonomy_family(namespace: str) -> str | None:
    from datetime import date

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


def mapped_fact(
    fact: dict[str, Any], metric: str, standard: str, tags: tuple[str, ...], filing: dict[str, Any]
) -> bool:
    family, tag = taxonomy_family(fact["taxonomy_namespace"]), fact["concept"]
    if metric == "revenue":
        return (family, tag) in REVENUE_ALIASES.get(standard, ()) or (
            standard == "IFRS"
            and tag == "TotalNetRevenuesIFRS"
            and toyota_extension(fact["taxonomy_namespace"], filing)
        )
    return tag in tags and family == ("jpigp" if standard == "IFRS" else "jppfs")
