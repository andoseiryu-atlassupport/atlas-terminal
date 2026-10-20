"""Offline examples using packaged synthetic inputs, without a model, keys or wallet."""

import json

from atlas_terminal.client import Atlas

queries = [
    {
        "dataset": "statements",
        "identifier": "ATLASDEMO",
        "metrics": ["revenue", "eps_basic"],
        "period": "quarterly",
        "count": 3,
    },
    {
        "dataset": "financial_analytics",
        "identifier": "ATLASDEMO",
        "metrics": ["revenue_growth_yoy", "free_cash_flow", "free_cash_flow_margin"],
        "count": 1,
    },
    {
        "dataset": "compare",
        "identifiers": ["ATLASDEMO"],
        "metrics": ["revenue", "revenue_growth_yoy", "free_cash_flow"],
    },
    {"dataset": "insider_transactions", "identifier": "ATLASDEMO"},
    {"dataset": "institutional_holdings", "identifier": "ATLASDEMO"},
    {"dataset": "economics", "series": "unemployment"},
    {"dataset": "treasury_yields", "year": 2026},
]

with Atlas("fixture") as atlas:
    for query in queries:
        result = atlas.get_research(query)
        assert result["is_synthetic"] and result["upstream_data_cost"] == "0"
        print(json.dumps(result, ensure_ascii=False))
