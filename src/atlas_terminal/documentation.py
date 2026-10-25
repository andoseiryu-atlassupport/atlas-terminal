"""Stable documentation links, separate from original data-source attribution."""

DOCUMENTATION_ORIGIN = "https://github.com/andoseiryu-atlassupport/atlas-terminal/blob/main"
SITE_ORIGIN = "https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site"
PROJECT_TAGLINE = "Free financial data for AI agents through one MCP connection."


def research_documentation(dataset: str) -> dict[str, str]:
    macro = {
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
        "sdmx_catalog",
        "international_economics",
        "macro_panel",
    }
    methodology = "macro" if dataset in macro else "methodology"
    guide = (
        "fixed-income"
        if dataset in {"jgb_yields", "euro_area_yields", "australia_yields"}
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
