"""Reported statement concepts and conservative annual / quarter / YTD selection."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.normalize import number
from atlas_terminal.providers import SECProvider, Snapshot
from atlas_terminal.research_models import ResearchQuery


@dataclass(frozen=True)
class MetricSpec:
    tags: tuple[str, ...]
    unit: str = "USD"
    instant: bool = False


STATEMENT_METRICS = {
    "revenue": MetricSpec(
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
    ),
    "cost_of_revenue": MetricSpec(("CostOfRevenue", "CostOfGoodsAndServicesSold")),
    "gross_profit": MetricSpec(("GrossProfit",)),
    "operating_income": MetricSpec(("OperatingIncomeLoss",)),
    "net_income": MetricSpec(("NetIncomeLoss",)),
    "research_and_development": MetricSpec(("ResearchAndDevelopmentExpense",)),
    "selling_general_administrative": MetricSpec(("SellingGeneralAndAdministrativeExpense",)),
    "interest_expense": MetricSpec(("InterestExpense",)),
    "income_tax_expense": MetricSpec(("IncomeTaxExpenseBenefit",)),
    "eps_basic": MetricSpec(("EarningsPerShareBasic",), "USD/shares"),
    "eps_diluted": MetricSpec(("EarningsPerShareDiluted",), "USD/shares"),
    "weighted_average_shares_basic": MetricSpec(
        ("WeightedAverageNumberOfSharesOutstandingBasic",), "shares"
    ),
    "weighted_average_shares_diluted": MetricSpec(
        ("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares"
    ),
    "assets": MetricSpec(("Assets",), instant=True),
    "current_assets": MetricSpec(("AssetsCurrent",), instant=True),
    "cash_and_equivalents": MetricSpec(("CashAndCashEquivalentsAtCarryingValue",), instant=True),
    "short_term_investments": MetricSpec(("ShortTermInvestments",), instant=True),
    "accounts_receivable": MetricSpec(("AccountsReceivableNetCurrent",), instant=True),
    "inventory": MetricSpec(("InventoryNet",), instant=True),
    "property_plant_equipment": MetricSpec(("PropertyPlantAndEquipmentNet",), instant=True),
    "goodwill": MetricSpec(("Goodwill",), instant=True),
    "liabilities": MetricSpec(("Liabilities",), instant=True),
    "current_liabilities": MetricSpec(("LiabilitiesCurrent",), instant=True),
    "accounts_payable": MetricSpec(("AccountsPayableCurrent",), instant=True),
    "long_term_debt_noncurrent": MetricSpec(("LongTermDebtNoncurrent",), instant=True),
    "stockholders_equity": MetricSpec(("StockholdersEquity",), instant=True),
    "retained_earnings": MetricSpec(("RetainedEarningsAccumulatedDeficit",), instant=True),
    "shares_outstanding": MetricSpec(("CommonStockSharesOutstanding",), "shares", True),
    "operating_cash_flow": MetricSpec(("NetCashProvidedByUsedInOperatingActivities",)),
    "investing_cash_flow": MetricSpec(("NetCashProvidedByUsedInInvestingActivities",)),
    "financing_cash_flow": MetricSpec(("NetCashProvidedByUsedInFinancingActivities",)),
    "capital_expenditure": MetricSpec(("PaymentsToAcquirePropertyPlantAndEquipment",)),
    "dividends_paid": MetricSpec(("PaymentsOfDividends",)),
    "share_repurchases": MetricSpec(("PaymentsForRepurchaseOfCommonStock",)),
    "stock_based_compensation": MetricSpec(("ShareBasedCompensation",)),
    "depreciation_amortization": MetricSpec(("DepreciationDepletionAndAmortization",)),
    "dividends_per_share": MetricSpec(("CommonStockDividendsPerShareDeclared",), "USD/shares"),
}
DEFAULT_METRICS = ["revenue", "net_income", "assets", "liabilities", "operating_cash_flow"]
RATIOS = {
    "net_margin": ("net_income", "revenue"),
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "current_ratio": ("current_assets", "current_liabilities"),
    "liabilities_to_equity": ("liabilities", "stockholders_equity"),
    "operating_cash_flow_to_net_income": ("operating_cash_flow", "net_income"),
}


def pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def reference(snapshot: Snapshot, locator: str, *, xml: bool = False) -> dict[str, Any]:
    return {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        "xml_local_path" if xml else "json_pointer": locator,
    }


def eligible_period(raw: dict[str, Any], spec: MetricSpec, period: str) -> bool:
    if spec.instant:
        return not raw.get("start")
    try:
        days = (date.fromisoformat(raw["end"]) - date.fromisoformat(raw["start"])).days
    except (KeyError, ValueError, TypeError):
        return False
    low, high = {"annual": (330, 400), "quarterly": (70, 110), "ytd": (70, 310)}[period]
    return low <= days <= high


def statement_data(
    source: SECProvider, query: ResearchQuery, *, include_history: bool = False
) -> dict[str, Any]:
    """Select reported facts; internal analytics may need older comparison inputs."""
    company, snapshot, filings, truncated = source.financial_inputs(query.identifier or "")
    metrics = query.metrics or DEFAULT_METRICS
    if set(metrics) - STATEMENT_METRICS.keys():
        raise AtlasError(
            "UNSUPPORTED_METRIC", "Choose statement metrics from the research catalog."
        )
    forms = {"10-K", "10-K/A"} if query.period == "annual" else {"10-Q", "10-Q/A"}
    if query.period == "quarterly":
        # A 10-K can anchor Q4 only when a discrete Q4 duration is explicitly reported.
        forms |= {"10-K", "10-K/A"}
    index = {f.accession: f for f in filings if f.form in forms}
    taxonomy = snapshot.data.get("facts", {}).get("us-gaap", {})
    if not taxonomy:
        raise AtlasError("UNSUPPORTED_TAXONOMY", "Normalized statements require US-GAAP concepts.")
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = {}
    anchors: set[str] = set()
    for metric, spec in STATEMENT_METRICS.items():
        for tag in spec.tags:
            rows = taxonomy.get(tag, {}).get("units", {}).get(spec.unit, [])
            for i, raw in enumerate(rows):
                filing = index.get(raw.get("accn"))
                if (
                    not filing
                    or raw.get("end") != filing.report_date
                    or raw.get("form") != filing.form
                    or raw.get("filed") != filing.filed_on
                    or not eligible_period(raw, spec, query.period)
                ):
                    continue
                try:
                    value = number(raw["val"])
                except (KeyError, ValueError, InvalidOperation):
                    continue
                if not spec.instant:
                    anchors.add(raw["end"])
                ptr = f"/facts/us-gaap/{tag}/units/{pointer_part(spec.unit)}/{i}"
                candidates.setdefault((metric, raw["end"]), []).append(
                    {
                        "value": value,
                        "start": raw.get("start"),
                        "tag": tag,
                        "filing": filing,
                        "source": reference(snapshot, ptr)
                        | {
                            "accession": filing.accession,
                            "form": filing.form,
                            "filed_on": filing.filed_on,
                            "accepted_at": filing.accepted_at,
                            "source_url": filing.source_url,
                            "taxonomy": "us-gaap",
                            "tag": tag,
                        },
                    }
                )
    ends = sorted(anchors, reverse=True)
    if not include_history:
        ends = ends[: query.count]
    requested_periods = len(ends) if include_history else query.count
    if not ends:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "No eligible reported periods; no quarter was derived."
        )
    records: list[dict[str, Any]] = []
    for end in ends:
        for metric in metrics:
            spec = STATEMENT_METRICS[metric]
            choices = candidates.get((metric, end), [])
            for tag in spec.tags:
                matching = [c for c in choices if c["tag"] == tag]
                if matching:
                    choices = matching
                    break
            if choices:
                newest = max(c["filing"].filed_on for c in choices)
                choices = [c for c in choices if c["filing"].filed_on == newest]
                if all(c["filing"].accepted_at for c in choices):

                    def accepted(c: dict[str, Any]) -> datetime:
                        return datetime.fromisoformat(
                            c["filing"].accepted_at.replace("Z", "+00:00")
                        )

                    last = max(accepted(c) for c in choices)
                    choices = [c for c in choices if accepted(c) == last]
                # YTD chooses the longest explicitly reported duration in this filing.
                if query.period == "ytd" and not spec.instant:
                    start = min(c["start"] for c in choices)
                    choices = [c for c in choices if c["start"] == start]
            identities = {(Decimal(c["value"]), c["start"]) for c in choices}
            selected = (
                min(choices, key=lambda c: (c["filing"].accession, c["source"]["json_pointer"]))
                if len(identities) == 1
                else None
            )
            status = "reported" if selected else ("ambiguous" if choices else "unavailable")
            records.append(
                {
                    "metric": metric,
                    "value": selected["value"] if selected else None,
                    "unit": spec.unit,
                    "status": status,
                    "period": {
                        "kind": "instant" if spec.instant else "duration",
                        "start": selected["start"] if selected else None,
                        "end": end,
                    },
                    "source": selected["source"] if selected else None,
                    "reason": None
                    if selected
                    else "No unique eligible primary-filing fact; no value imputed.",
                }
            )
    reported = sum(r["status"] == "reported" for r in records)
    partial = reported < len(metrics) * requested_periods or truncated
    coverage = {
        "requested_periods": requested_periods,
        "available_periods": len(ends),
        "requested_cells": len(metrics) * requested_periods,
        "reported_cells": reported,
        "history_truncated": truncated,
        "partial": partial,
    }
    if partial and not query.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Explicit allow_partial is required for incomplete statements.",
            details=coverage,
        )
    return {
        "company": company.model_dump(),
        "records": records,
        "period_ends": ends,
        "coverage": coverage,
        "sources": [reference(snapshot, "")],
        "selection_policy": "reported_primary_period_v1",
        "limitations": [
            "US-GAAP concepts with metric-specific units; concept coverage varies by issuer.",
            (
                "Quarterly durations are 70-110 days; annual 330-400; YTD 70-310, "
                "longest reported start."
            ),
            (
                "No subtraction-derived Q4, currency conversion, comparative-only "
                "revisions or PIT history."
            ),
            "A quarter with no reported duration anchor is omitted, including unreported Q4.",
        ],
    }


def ratio_data(source: SECProvider, query: ResearchQuery) -> dict[str, Any]:
    metrics = list(dict.fromkeys(m for pair in RATIOS.values() for m in pair))
    result = statement_data(
        source,
        ResearchQuery(
            dataset="statements",
            identifier=query.identifier,
            metrics=metrics,
            period=query.period,
            count=query.count,
            allow_partial=True,
        ),
    )
    inputs = {(r["metric"], r["period"]["end"]): r for r in result["records"]}
    records = []
    for end in result["period_ends"]:
        for name, (numerator, denominator) in RATIOS.items():
            a, b = inputs[(numerator, end)], inputs[(denominator, end)]
            reason = None
            value = None
            if a["status"] != "reported" or b["status"] != "reported":
                reason = "A required reported input is missing or ambiguous."
            elif a["period"] != b["period"]:
                reason = "Reported inputs have different periods."
            elif Decimal(b["value"]) == 0:
                reason = "The denominator is zero."
            else:
                with localcontext() as context:
                    context.prec = 28
                    value = format(Decimal(a["value"]) / Decimal(b["value"]), "f")
            records.append(
                {
                    "metric": name,
                    "value": value,
                    "unit": "ratio",
                    "derived": True,
                    "status": "derived" if value is not None else "unavailable",
                    "reason": reason,
                    "formula": f"{numerator} / {denominator}",
                    "decimal_precision": 28,
                    "period_end": end,
                    "inputs": [a, b],
                }
            )
    reported = sum(r["value"] is not None for r in records)
    partial = reported < query.count * len(RATIOS) or result["coverage"]["history_truncated"]
    if partial and not query.allow_partial:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Explicit allow_partial is required for incomplete ratios."
        )
    result["records"] = records
    result["coverage"] = result["coverage"] | {
        "requested_cells": query.count * len(RATIOS),
        "reported_cells": reported,
        "partial": partial,
    }
    result["limitations"].append(
        "Ratios preserve input periods and provenance; no market-price "
        "multiples or consensus estimates."
    )
    return result


def metric_catalog() -> dict[str, Any]:
    return {name: asdict(spec) for name, spec in STATEMENT_METRICS.items()}
