"""Deterministic business-quality heuristics with fixed weights and explicit gaps."""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from statistics import median
from typing import TYPE_CHECKING, Any

from atlas_terminal.buffett_catalog import MODEL_VERSION, RULES, buffett_catalog
from atlas_terminal.buffett_models import METRICS, ScoreFinancials, ScoreValuation
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import SUBMISSIONS, SECProvider, Snapshot
from atlas_terminal.util import canonical, digest

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery

CONTEXT = Context(prec=100, rounding=ROUND_HALF_EVEN)
QUANTUM = Decimal("1e-18")
NONNEGATIVE = {
    "capital_expenditure",
    "interest_expense",
    "assets",
    "liabilities",
    "stock_based_compensation",
}


class Unavailable(ValueError):
    pass


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(QUANTUM), "f")


def cell_id(end: str, metric: str) -> str:
    return end + ":" + metric


def supplied_document(payload: dict[str, Any], captured: str, kind: str) -> tuple[Snapshot, dict]:
    body = canonical(payload)
    snapshot = Snapshot(
        body, f"https://operator-input.atlas.invalid/{kind}/{digest(body)}.json", captured
    )
    return snapshot, {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": captured,
        "json_pointer": "",
        "source_kind": "operator_supplied",
        "publisher_values_verified": False,
    }


def supplied_financials(data: ScoreFinancials) -> dict[str, Any]:
    payload = data.model_dump(mode="json")
    _, source = supplied_document(payload, data.captured_at, "annual-financials")
    inputs, annual = {}, []
    for index, row in enumerate(data.annual):
        annual.append({"start": row.start, "end": row.end, "available_on": row.available_on})
        for metric in METRICS:
            value = row.values.get(metric)
            inputs[cell_id(row.end, metric)] = {
                "metric": metric,
                "value": value,
                "unit": data.currency,
                "status": "operator_supplied" if value is not None else "unavailable",
                "period": {"start": row.start, "end": row.end},
                "source": source | {"json_pointer": f"/annual/{index}/values/{metric}"}
                if metric in row.values
                else None,
            }
    return {
        "identifier": data.identifier,
        "currency": data.currency,
        "business_type": data.business_type,
        "annual": annual,
        "inputs": inputs,
        "sources": [source],
        "input_document": payload,
        "source_id": "multiple",
        "input_origin": "operator_supplied_unverified",
        "original_source_url": data.original_source_url,
        "history_truncated": False,
    }


def sec_financials(source: SECProvider, identifier: str, years: int) -> dict[str, Any]:
    from atlas_terminal.research_financials import reference, statement_data
    from atlas_terminal.research_models import ResearchQuery

    original = source.financial_inputs(identifier)
    statements = statement_data(
        source,
        ResearchQuery(
            dataset="statements",
            identifier=identifier,
            period="annual",
            metrics=list(METRICS),
            count=years + 1,
            allow_partial=True,
        ),
        reported_inputs=original,
    )
    company = original[0]
    submissions = source.transport.get(f"{SUBMISSIONS}CIK{company.cik}.json", 900)
    if str(submissions.data.get("cik", "")).zfill(10) != company.cik:
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Classification issuer differs from the financial issuer."
        )
    sic = str(submissions.data.get("sic", ""))
    business = (
        "unspecified"
        if not sic.isdigit() or len(sic) != 4 or sic == "0000"
        else ("financial" if 6000 <= int(sic) <= 6999 else "nonfinancial")
    )
    inputs, annual = {}, []
    for end in statements["period_ends"]:
        cells = [row for row in statements["records"] if row["period"]["end"] == end]
        durations = [row for row in cells if row["period"]["start"] and row["status"] == "reported"]
        if not durations:
            continue
        anchor = next((row for row in durations if row["metric"] == "revenue"), durations[0])
        start = anchor["period"]["start"]
        available = max(row["source"]["filed_on"] for row in cells if row["source"])
        annual.append({"start": start, "end": end, "available_on": available})
        for row in cells:
            cell = dict(row)
            if row["period"]["start"] not in {None, start}:
                cell = cell | {"status": "ambiguous", "reason": "Annual input periods differ."}
            inputs[cell_id(end, row["metric"])] = cell
    return {
        "identifier": identifier,
        "currency": "USD",
        "business_type": business,
        "sic": sic,
        "annual": annual,
        "inputs": inputs,
        "sources": [*statements["sources"], reference(submissions, "/sic")],
        "company": statements["company"],
        "source_id": "sec",
        "input_origin": "sec_primary_annual_facts",
        "history_truncated": statements["coverage"]["history_truncated"],
    }


def dcf(
    base: Decimal, growth: Decimal, discount: Decimal, terminal: Decimal, years: int
) -> Decimal:
    cash = base
    value = Decimal(0)
    for year in range(1, years + 1):
        cash *= 1 + growth
        value += cash / (1 + discount) ** year
    return value + cash * (1 + terminal) / (discount - terminal) / (1 + discount) ** years


def calculate_score(
    data: dict[str, Any], years: int, valuation: ScoreValuation | None
) -> dict[str, Any]:
    with localcontext(CONTEXT):
        return _calculate_score(data, years, valuation)


def _calculate_score(
    data: dict[str, Any], years: int, valuation: ScoreValuation | None
) -> dict[str, Any]:
    history = sorted(data["annual"], key=lambda row: row["end"])
    window = history[-years:]
    latest = window[-1] if window else None
    inputs = dict(data["inputs"])
    if valuation and (
        valuation.currency != data["currency"]
        or (latest and valuation.observed_on < max(row["available_on"] for row in window))
    ):
        raise AtlasError(
            "INVALID_INPUT", "Valuation currency/date must match available financial inputs."
        )
    applicable = data["business_type"] == "nonfinancial"
    continuous = len(window) == years and all(
        (date.fromisoformat(right["start"]) - date.fromisoformat(left["end"])).days == 1
        for left, right in zip(window, window[1:], strict=False)
    )
    components: list[dict[str, Any]] = []
    valuation_detail: dict[str, Any] | None = None
    used_ids: list[str] = []
    concepts: dict[str, set[tuple[str, str, str]]] = {}

    def read(row: dict[str, Any], metric: str) -> Decimal:
        key = cell_id(row["end"], metric)
        used_ids.append(key)
        if key not in inputs:
            inputs[key] = {
                "metric": metric,
                "value": None,
                "unit": data["currency"],
                "status": "unavailable",
                "source": None,
                "period": {"start": row.get("start"), "end": row["end"]},
            }
        cell = inputs.get(key)
        if (
            not cell
            or cell["value"] is None
            or cell["status"] not in {"reported", "operator_supplied"}
        ):
            raise Unavailable("missing_or_ambiguous_input: " + key)
        if cell["unit"] != data["currency"]:
            raise Unavailable("input_currency_mismatch: " + key)
        origin = cell.get("source") or {}
        if origin.get("tag"):
            concepts.setdefault(metric, set()).add(
                (
                    origin.get("taxonomy", ""),
                    origin["tag"],
                    cell["unit"],
                )
            )
            if len(concepts[metric]) > 1:
                raise Unavailable("incompatible_reported_concepts: " + metric)
        value = Decimal(cell["value"])
        if not value.is_finite() or abs(value) > Decimal("1e30"):
            raise Unavailable("invalid_numeric_input: " + key)
        if metric in NONNEGATIVE and value < 0:
            raise Unavailable("invalid_negative_input: " + key)
        return value

    for name, rule in RULES.items():
        used_ids = []
        concepts = {}
        value, points, reason = None, None, None
        try:
            if not applicable:
                raise Unavailable(
                    "financial_business_not_applicable"
                    if data["business_type"] == "financial"
                    else "business_type_unspecified"
                )
            if latest is None:
                raise Unavailable("no_annual_period")
            if name not in {"balance_sheet", "interest_coverage"} and not continuous:
                raise Unavailable("incomplete_or_nonconsecutive_annual_history")
            if name == "sustained_roe":
                returns = []
                for row in window:
                    previous_end = (
                        date.fromisoformat(row["start"]) - timedelta(days=1)
                    ).isoformat()
                    previous = {"end": previous_end}
                    begin, end = (
                        read(previous, "stockholders_equity"),
                        read(row, "stockholders_equity"),
                    )
                    income = read(row, "net_income")
                    if begin <= 0 or end <= 0:
                        points, reason = Decimal(0), "nonpositive_equity"
                    else:
                        returns.append(income / ((begin + end) / 2))
                if points is None:
                    value = median(returns)
            elif name == "operating_margin":
                margins = []
                for row in window:
                    revenue, income = read(row, "revenue"), read(row, "operating_income")
                    if revenue <= 0:
                        raise Unavailable("nonpositive_revenue")
                    margins.append(income / revenue)
                value = median(margins)
            elif name in {"earnings_consistency", "fcf_consistency"}:
                values = [
                    read(row, "net_income")
                    if name == "earnings_consistency"
                    else read(row, "operating_cash_flow") - read(row, "capital_expenditure")
                    for row in window
                ]
                value = Decimal(sum(item > 0 for item in values)) / years
            elif name == "cash_conversion":
                income = sum((read(row, "net_income") for row in window), Decimal(0))
                cash = sum((read(row, "operating_cash_flow") for row in window), Decimal(0))
                if income <= 0:
                    points, reason = Decimal(0), "nonpositive_aggregate_earnings"
                else:
                    value = cash / income
            elif name == "balance_sheet":
                assets, liabilities = read(latest, "assets"), read(latest, "liabilities")
                if assets <= 0:
                    raise Unavailable("nonpositive_assets")
                value = liabilities / assets
            elif name == "interest_coverage":
                income, expense = read(latest, "operating_income"), read(latest, "interest_expense")
                if expense == 0:
                    points = Decimal(str(rule["weight"])) if income > 0 else Decimal(0)
                    reason = "zero_reported_interest_expense"
                else:
                    value = income / expense
            elif name == "stock_compensation":
                compensation = sum(
                    (read(row, "stock_based_compensation") for row in window), Decimal(0)
                )
                cash = sum((read(row, "operating_cash_flow") for row in window), Decimal(0))
                if cash <= 0:
                    points, reason = Decimal(0), "nonpositive_aggregate_operating_cash_flow"
                else:
                    value = compensation / cash
            elif name == "capital_intensity":
                capex = sum((read(row, "capital_expenditure") for row in window), Decimal(0))
                cash = sum((read(row, "operating_cash_flow") for row in window), Decimal(0))
                if cash <= 0:
                    points, reason = Decimal(0), "nonpositive_aggregate_operating_cash_flow"
                else:
                    value = capex / cash
            elif name == "margin_of_safety":
                if valuation is None:
                    raise Unavailable("valuation_input_not_supplied")
                base = (
                    sum(
                        (
                            read(row, "operating_cash_flow")
                            - read(row, "capital_expenditure")
                            - read(row, "stock_based_compensation")
                            for row in window
                        ),
                        Decimal(0),
                    )
                    / years
                )
                valuation_detail = {
                    "method": "cash_flow_proxy_dcf",
                    "base_cash_flow": decimal_text(base),
                    "base_formula": (
                        "mean(operating_cash_flow - total_ppe_capex - stock_based_compensation)"
                    ),
                    "dcf_formula": (
                        "sum(base*(1+g)^t/(1+r)^t,t=1..N) + base*(1+g)^N*(1+tg)/(r-tg)/(1+r)^N"
                    ),
                    "assumptions": valuation.model_dump(),
                    "currency": data["currency"],
                    "input_ids": list(dict.fromkeys(used_ids)),
                    "scenarios": [],
                    "owner_earnings": False,
                    "net_debt_adjustment": False,
                }
                if base <= 0:
                    points, reason = Decimal(0), "nonpositive_cash_flow_proxy"
                else:
                    g, r, tg = (
                        Decimal(v)
                        for v in (
                            valuation.growth_rate,
                            valuation.discount_rate,
                            valuation.terminal_growth_rate,
                        )
                    )
                    cases = [
                        ("base", g, r),
                        (
                            "lower",
                            max(Decimal("-0.1"), g - Decimal("0.02")),
                            min(Decimal("0.30"), r + Decimal("0.02")),
                        ),
                        (
                            "higher",
                            min(Decimal("0.1"), g + Decimal("0.02")),
                            max(tg + Decimal("0.01"), r - Decimal("0.02"), Decimal("0.01")),
                        ),
                    ]
                    for label, growth, discount in cases:
                        estimate = dcf(base, growth, discount, tg, valuation.projection_years)
                        safety = 1 - Decimal(valuation.market_cap) / estimate
                        valuation_detail["scenarios"].append(
                            {
                                "scenario": label,
                                "growth_rate": str(growth),
                                "discount_rate": str(discount),
                                "scenario_equity_value": decimal_text(estimate),
                                "margin_of_safety": decimal_text(safety),
                            }
                        )
                        if label == "base":
                            value = safety
            if points is None and value is not None:
                zero, full = Decimal(str(rule["zero"])), Decimal(str(rule["full"]))
                fraction = max(Decimal(0), min(Decimal(1), (value - zero) / (full - zero)))
                points = fraction * Decimal(str(rule["weight"]))
        except Unavailable as error:
            reason, value, points = str(error), None, None
        components.append(
            {
                "metric": name,
                "value": decimal_text(value) if value is not None else None,
                "points": decimal_text(points) if points is not None else None,
                "max_points": str(rule["weight"]),
                "status": "evaluated" if points is not None else "unavailable",
                "reason": reason,
                "formula": rule["formula"],
                "input_ids": list(dict.fromkeys(used_ids)),
                "rule": dict(rule),
                "derived": True,
            }
        )
    known = sum(
        (Decimal(row["points"]) for row in components if row["points"] is not None), Decimal(0)
    )
    available_weight = sum(
        int(row["max_points"]) for row in components if row["points"] is not None
    )
    quality = [row for row in components if row["metric"] != "margin_of_safety"]
    quality_points = sum(
        (Decimal(row["points"]) for row in quality if row["points"] is not None), Decimal(0)
    )
    complete = available_weight == 100
    score = {
        "identifier": data["identifier"],
        "period_end": latest["end"] if latest else None,
        "model_version": MODEL_VERSION,
        "derived": True,
        "status": "derived" if complete else "unavailable",
        "score": decimal_text(known) if complete else None,
        "max_points": "100",
        "quality_score": decimal_text(quality_points)
        if all(row["points"] is not None for row in quality)
        else None,
        "quality_max_points": "80",
        "known_points": decimal_text(known),
        "score_bounds": {
            "lower": decimal_text(known),
            "upper": decimal_text(known + 100 - available_weight),
        },
        "available_weight": str(available_weight),
        "components": components,
        "reported_inputs": inputs,
        "valuation": valuation_detail,
        "selected_periods": window,
        "business_type": data["business_type"],
        "currency": data["currency"],
        "manual_review": buffett_catalog()["qualitative_not_scored"],
        "formula": "sum(component points); missing weights are not redistributed",
    }
    return score


def buffett_score(source: SECProvider | None, q: "ResearchQuery") -> dict[str, Any]:
    years = q.count if "count" in q.model_fields_set else 5
    if q.financial_input is not None:
        data = supplied_financials(q.financial_input)
    else:
        assert source is not None
        data = sec_financials(source, q.identifier or "", years)
    score = calculate_score(data, years, q.valuation)
    sources = data["sources"][:]
    documents = {}
    if "input_document" in data:
        documents["financial_input"] = data["input_document"]
    if q.valuation:
        payload = q.valuation.model_dump(mode="json")
        _, reference = supplied_document(payload, q.valuation.captured_at, "valuation")
        sources.append(reference)
        documents["valuation"] = payload
        if score["valuation"] is not None:
            score["valuation"]["assumptions_source"] = reference
            score["valuation"]["market_cap_source"] = reference | {"json_pointer": "/market_cap"}
    return {
        "records": [score],
        **(
            {"is_synthetic": True}
            if (
                (q.financial_input and q.financial_input.is_synthetic)
                or (q.valuation and q.valuation.is_synthetic)
            )
            else {}
        ),
        "sources": sources,
        "source_id": "multiple" if q.valuation or q.financial_input else "sec",
        "input_origin": data["input_origin"],
        "supplied_documents": documents,
        "scoring_policy": buffett_catalog(),
        "coverage": {
            "requested_years": years,
            "available_annual_periods": len(data["annual"]),
            "returned_records": 1,
            "partial": score["score"] is None,
            "available_weight": score["available_weight"],
            "point_in_time": False,
            "history_truncated": data["history_truncated"],
            "missing_components": [
                row["metric"] for row in score["components"] if row["points"] is None
            ],
        },
        "limitations": [
            "Editorial quantitative proxies inspired by public letters; not Buffett's own "
            "score or an endorsement.",
            "Business understanding, durable moat and management integrity require manual "
            "review; no buy/sell signal or calibrated return probability is supplied.",
            "General-company thresholds do not apply to financial businesses. Sector "
            "conventions, acquisitions, restatements and share splits can distort "
            "comparisons.",
            "CFO less total PP&E capex less stock compensation is a valuation proxy, not "
            "reported owner earnings or FCFE; maintenance capex and future financing are "
            "unknown.",
            "DCF growth/discount/terminal rates are explicit assumptions, not forecasts. "
            "No cash/net-debt adjustment or automatic market-cap/price lookup is made.",
            "Missing or nonconsecutive years remain unavailable, with fixed-weight score "
            "bounds and no reweighting. Current filings do not establish historical "
            "availability.",
            "Operator-supplied currency, business classification, publication dates and "
            "values are declarations; attribution URLs are never fetched and publisher "
            "values are unverified.",
        ],
    }
