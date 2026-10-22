"""Stable documentation links, separate from original data-source attribution."""

DOCUMENTATION_ORIGIN = "https://github.com/andoseiryu-atlassupport/atlas-terminal/blob/main"
SITE_ORIGIN = "https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site"


def research_documentation(dataset: str) -> dict[str, str]:
    macro = {
        "economics",
        "treasury_yields",
        "treasury_real_yields",
        "federal_debt",
        "reference_rates",
        "fx_rates",
        "macro_analytics",
        "policy_news",
    }
    methodology = "macro" if dataset in macro else "methodology"
    return {
        "guide": f"{DOCUMENTATION_ORIGIN}/docs/{'macro' if dataset in macro else 'research'}.md",
        "methodology": f"{DOCUMENTATION_ORIGIN}/docs/{methodology}.md",
        "sources_and_terms": f"{DOCUMENTATION_ORIGIN}/DATA_SOURCES.md",
        "citation_guidance": f"{DOCUMENTATION_ORIGIN}/docs/citations.md",
    }
