"""Stable documentation links, separate from original data-source attribution."""

DOCUMENTATION_ORIGIN = "https://github.com/andoseiryu-atlassupport/atlas-terminal/blob/main"
SITE_ORIGIN = "https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site"
PROJECT_TAGLINE = (
    "Financial research for AI agents through MCP, with traceable data and calculations."
)


def research_documentation(dataset: str) -> dict[str, str]:
    if dataset in {"benchmark_catalog", "benchmark_prices"}:
        return {
            "guide": f"{DOCUMENTATION_ORIGIN}/docs/benchmarks.md",
            "methodology": f"{DOCUMENTATION_ORIGIN}/docs/benchmarks.md",
            "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
            "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
        }
    if dataset == "cpi_effects":
        return {
            "guide": f"{DOCUMENTATION_ORIGIN}/docs/cpi-effects.md",
            "methodology": f"{DOCUMENTATION_ORIGIN}/docs/cpi-effects.md#calculated-core-effects",
            "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
            "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
        }
    if dataset == "prediction_markets":
        return {
            "guide": f"{DOCUMENTATION_ORIGIN}/docs/prediction-markets.md",
            "methodology": f"{DOCUMENTATION_ORIGIN}/docs/prediction-markets.md",
            "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
            "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
        }
    if dataset == "buffett_score":
        return {
            "guide": f"{DOCUMENTATION_ORIGIN}/docs/buffett-scoring.md",
            "methodology": f"{DOCUMENTATION_ORIGIN}/docs/buffett-scoring.md",
            "japan_guide": f"{DOCUMENTATION_ORIGIN}/docs/japan-buffett.md",
            "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
            "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
        }
    if dataset == "margin_balances":
        return {
            "guide": f"{DOCUMENTATION_ORIGIN}/docs/margin-trading.md",
            "methodology": f"{DOCUMENTATION_ORIGIN}/docs/margin-trading.md",
            "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
            "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
        }
    filing = dataset in {
        "trailing_financials",
        "filing_documents",
        "filing_facts",
        "segment_financials",
        "filing_sections",
        "filing_tables",
    }
    macro = {
        "commodity_prices",
        "policy_rates",
        "economics",
        "treasury_yields",
        "treasury_real_yields",
        "jgb_yields",
        "euro_area_yields",
        "australia_yields",
        "federal_debt",
        "reference_rates",
        "fx_rates",
        "macro_analytics",
        "policy_news",
        "national_accounts",
        "japan_economics",
        "europe_economics",
        "uk_economics",
        "activity_economics",
        "global_economics",
        "sdmx_catalog",
        "international_economics",
        "macro_panel",
    }
    equity = dataset in {"equity_prices", "equity_actions"}
    market = dataset in {
        "price_analytics",
        "price_comparison",
        "company_screen",
        "filing_events",
        "fund_holdings",
        "fred_series",
    }
    japan = dataset in {"japan_disclosures", "japan_filing_facts", "japan_securities"}
    history = dataset == "source_history"
    methodology = (
        "order-books-reports"
        if dataset in {"order_book", "analyst_reports", "analyst_report_text"}
        else "news"
        if dataset in {"news_feed", "news_digest"}
        else "interest-spreads"
        if dataset == "interest_spreads"
        else "country-macro-bonds"
        if dataset in {"country_economics", "country_bond_yields"}
        else "credit-ratings"
        if dataset == "credit_ratings"
        else "commodities-futures"
        if dataset in {"commodity_prices", "futures_prices", "futures_curve"}
        else "filing-research"
        if filing
        else "intraday-prices"
        if dataset == "intraday_prices"
        else "market-research"
        if market
        else "japan-companies"
        if japan
        else "source-history"
        if history
        else "yahoo-finance"
        if equity
        else "macro"
        if dataset in macro
        else "methodology"
    )
    guide = (
        "order-books-reports"
        if dataset in {"order_book", "analyst_reports", "analyst_report_text"}
        else "news"
        if dataset in {"news_feed", "news_digest"}
        else "credit-ratings"
        if dataset == "credit_ratings"
        else "commodities-futures"
        if dataset in {"commodity_prices", "futures_prices", "futures_curve"}
        else "filing-research"
        if filing
        else "policy-rates"
        if dataset == "policy_rates"
        else "g20-macro"
        if dataset == "global_economics"
        else "intraday-prices"
        if dataset == "intraday_prices"
        else "market-research"
        if market
        else "japan-companies"
        if japan
        else "source-history"
        if history
        else "yahoo-finance"
        if equity
        else "fixed-income"
        if dataset in {"jgb_yields", "euro_area_yields", "australia_yields"}
        else "economic-indicators"
        if dataset == "activity_economics"
        else "macro"
        if dataset in macro
        else "research"
    )
    return {
        "guide": f"{DOCUMENTATION_ORIGIN}/docs/{guide}.md",
        "methodology": f"{DOCUMENTATION_ORIGIN}/docs/{methodology}.md",
        "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
        "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
    }
