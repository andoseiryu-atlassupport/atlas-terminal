"""Versioned editorial heuristics inspired by public Berkshire letters, not an endorsement."""

from copy import deepcopy

MODEL_VERSION = "buffett_quantitative_v1"
RULES = {
    "sustained_roe": {
        "weight": 15,
        "zero": "0.05",
        "full": "0.20",
        "direction": "higher",
        "formula": "median(net_income / mean(previous_equity, equity))",
    },
    "operating_margin": {
        "weight": 10,
        "zero": "0.05",
        "full": "0.20",
        "direction": "higher",
        "formula": "median(operating_income / revenue)",
    },
    "earnings_consistency": {
        "weight": 10,
        "zero": "0",
        "full": "1",
        "direction": "higher",
        "formula": "count(net_income > 0) / requested_years",
    },
    "fcf_consistency": {
        "weight": 10,
        "zero": "0",
        "full": "1",
        "direction": "higher",
        "formula": "count(operating_cash_flow - capital_expenditure > 0) / requested_years",
    },
    "cash_conversion": {
        "weight": 10,
        "zero": "0.5",
        "full": "1.2",
        "direction": "higher",
        "formula": "sum(operating_cash_flow) / sum(net_income)",
    },
    "balance_sheet": {
        "weight": 10,
        "zero": "0.75",
        "full": "0.35",
        "direction": "lower",
        "formula": "latest liabilities / latest assets; liabilities are not debt",
    },
    "interest_coverage": {
        "weight": 5,
        "zero": "1",
        "full": "8",
        "direction": "higher",
        "formula": "latest operating_income / latest interest_expense",
    },
    "stock_compensation": {
        "weight": 5,
        "zero": "0.25",
        "full": "0.05",
        "direction": "lower",
        "formula": "sum(stock_based_compensation) / sum(operating_cash_flow)",
    },
    "capital_intensity": {
        "weight": 5,
        "zero": "0.8",
        "full": "0.2",
        "direction": "lower",
        "formula": "sum(capital_expenditure) / sum(operating_cash_flow)",
    },
    "margin_of_safety": {
        "weight": 20,
        "zero": "0",
        "full": "0.30",
        "direction": "higher",
        "formula": "(cash_flow_proxy_dcf - supplied_market_cap) / cash_flow_proxy_dcf",
    },
}
REFERENCES = [
    {
        "title": "Berkshire 1977: returns on equity and business-selection criteria",
        "url": "https://www.berkshirehathaway.com/letters/1977.html",
    },
    {
        "title": "Berkshire 1986: owner earnings and maintenance capital limitations",
        "url": "https://www.berkshirehathaway.com/letters/1986.html",
    },
    {
        "title": "Berkshire 1992: value and price",
        "url": "https://www.berkshirehathaway.com/letters/1992.html",
    },
]


def buffett_catalog() -> dict:
    return deepcopy(
        {
            "model_version": MODEL_VERSION,
            "range": [0, 100],
            "quality_max_points": 80,
            "valuation_max_points": 20,
            "default_years": 5,
            "year_bounds": [3, 10],
            "rules": RULES,
            "references": REFERENCES,
            "threshold_origin": "Atlas editorial heuristics; not Buffett's published scoring rules",
            "missing_policy": "No reweighting; null total until complete; fixed-weight bounds",
            "qualitative_not_scored": [
                "circle_of_competence",
                "management_integrity",
                "durable_moat",
            ],
            "automatic_source": "SEC US-GAAP USD primary annual facts",
            "supplied_source": "Declared annual inputs and currencies; no currency conversion",
            "valuation": "Optional supplied market cap and cash-flow-proxy DCF assumptions",
            "financial_businesses": "Not applicable; general-company rules are not bank models",
        }
    )
