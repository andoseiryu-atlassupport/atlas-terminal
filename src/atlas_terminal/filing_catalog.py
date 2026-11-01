"""Explicit filing selections and additive monetary trailing-window concepts."""

TTM_FLOW_METRICS = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "research_and_development",
    "selling_general_administrative",
    "interest_expense",
    "income_tax_expense",
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "capital_expenditure",
    "dividends_paid",
    "share_repurchases",
    "stock_based_compensation",
    "depreciation_amortization",
)
TTM_DERIVED = {
    "free_cash_flow": ("operating_cash_flow", "capital_expenditure"),
    "net_margin": ("net_income", "revenue"),
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "free_cash_flow_margin": ("operating_cash_flow", "capital_expenditure", "revenue"),
}
TTM_METRICS = (*TTM_FLOW_METRICS, *TTM_DERIVED)
SEGMENT_METRICS = (
    "revenue",
    "operating_income",
    "net_income",
    "assets",
    "depreciation_amortization",
)
FILING_DATASETS = {
    "filing_documents",
    "filing_facts",
    "segment_financials",
    "filing_sections",
    "filing_tables",
}
FILING_FORMS = {
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
    "6-K",
    "6-K/A",
    "8-K",
    "8-K/A",
    "S-1",
    "S-1/A",
    "F-1",
    "F-1/A",
}
DOCUMENT_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}"
SECTION_PATTERN = r"(?:all|document|item_[0-9]{1,2}[a-c]?|item_[0-9]\.[0-9]{2})"
