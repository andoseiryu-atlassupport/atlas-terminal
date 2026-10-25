"""Searchable query templates generated from the same registries used by retrieval."""

from typing import Any

from atlas_terminal.economic_catalog import JAPAN_SERIES, NATIONAL_ACCOUNTS
from atlas_terminal.macro_catalog import CURVE_SERIES, FX_CURRENCIES, MACRO_SERIES, REFERENCE_SERIES
from atlas_terminal.public_sources import BLS_SERIES, SOURCE_CATALOG
from atlas_terminal.research_models import CatalogQuery


def series_catalog(filters: CatalogQuery) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    def add(
        dataset: str,
        key: str,
        title: str,
        source: str,
        country: str | None,
        frequency: str,
        unit: str | None,
        query: dict[str, Any],
        **extra: Any,
    ) -> None:
        aliases = []
        for term, words in {
            "cpi": ["inflation", "物価", "インフレ"],
            "pce": ["inflation", "物価", "インフレ"],
            "unemployment": ["失業", "雇用"],
            "gdp": ["国内総生産", "成長"],
            "rate": ["金利"],
            "yield": ["bond", "government bond", "債券", "国債", "利回り"],
            "jgb": ["Japan government bond", "日本国債", "金利"],
            "euro": ["euro area", "欧州", "ユーロ圏", "国債"],
            "australia": ["Australian government bond", "豪州", "オーストラリア", "国債"],
            "inflation_compensation": ["breakeven proxy", "inflation", "インフレ補償"],
            "tankan": ["短観"],
        }.items():
            if term in key:
                aliases.extend(words)
        if dataset == "fx_rates":
            aliases.extend(["exchange rate", "為替"])
        rows.append(
            {
                "id": dataset + ":" + key,
                "dataset": dataset,
                "series": key,
                "title": title,
                "source_id": source,
                "country": country,
                "frequency": frequency,
                "unit": unit,
                "aliases": aliases,
                "query": {"dataset": dataset, **query},
                "source_terms_url": SOURCE_CATALOG[source]["terms"],
                "api_key_required": SOURCE_CATALOG[source]["api_key_required"],
                "availability": "registered_not_live_verified",
                **extra,
            }
        )

    for key, (code, title, unit, adjusted) in BLS_SERIES.items():
        add(
            "economics",
            key,
            title,
            "bls",
            "US",
            "monthly",
            unit,
            {"series": key},
            source_series=code,
            seasonally_adjusted=adjusted,
        )
    for key, spec in NATIONAL_ACCOUNTS.items():
        add(
            "national_accounts",
            key,
            spec["title"],
            "bea",
            "US",
            "monthly" if spec["frequency"] == "M" else "quarterly",
            None,
            {"series": key},
            source_table=spec["table"],
            unit_basis="source_metadata",
            annualized=spec["annualized"],
            seasonally_adjusted=True,
        )
    for key, spec_jp in JAPAN_SERIES.items():
        add(
            "japan_economics",
            key,
            spec_jp["title"],
            spec_jp["source_id"],
            "JP",
            spec_jp["frequency"].lower(),
            None,
            {"series": key},
            source_series=spec_jp["code"],
            unit_basis="source_metadata",
            seasonally_adjusted=spec_jp["seasonally_adjusted"],
        )
    for key in REFERENCE_SERIES:
        add(
            "reference_rates",
            key,
            key.upper() + " overnight reference rate",
            "new_york_fed",
            "US",
            "daily",
            "percent_per_annum",
            {"series": key},
            history="latest_90_observations",
        )
    for key, spec_macro in MACRO_SERIES.items():
        add(
            "macro_analytics",
            key,
            spec_macro.get("title", key.replace("_", " ")),
            spec_macro["source_id"],
            spec_macro.get("country", "US"),
            "daily" if key in CURVE_SERIES else "monthly",
            spec_macro["unit"],
            {"series": key},
            derived=True,
        )
    add(
        "jgb_yields",
        "jgb_curve",
        "Japan MOF government bond yield curve, 1–40 years",
        "japan_mof",
        "JP",
        "daily",
        "percent_per_annum",
        {},
    )
    for series, title in (("aaa_spot", "AAA-rated"), ("all_spot", "all-rating")):
        add(
            "euro_area_yields",
            "euro_" + series,
            "Euro area " + title + " government zero-coupon spot curve",
            "ecb",
            "EA",
            "daily",
            "percent_per_annum",
            {"series": series},
            compounding="continuous",
        )
    add(
        "australia_yields",
        "australia_curve",
        "Australian government 2/3/5/10-year yields",
        "rba",
        "AU",
        "daily",
        "percent_per_annum",
        {},
    )
    for base in FX_CURRENCIES:
        for quote in FX_CURRENCIES:
            if base != quote:
                add(
                    "fx_rates",
                    base + "_" + quote,
                    f"{base}/{quote} reference FX",
                    "ecb",
                    None,
                    "daily",
                    f"{quote}_per_{base}",
                    {"base_currency": base, "quote_currency": quote},
                    derived=base != "EUR",
                )
    matches = [
        row
        for row in rows
        if (
            (filters.dataset is None or filters.dataset == row["dataset"])
            and (filters.source_id is None or filters.source_id == row["source_id"])
            and (filters.country is None or filters.country == row["country"])
            and (filters.frequency is None or filters.frequency == row["frequency"])
            and (filters.unit is None or filters.unit == row["unit"])
            and (
                filters.search is None
                or all(
                    term
                    in " ".join(
                        [row["id"], row["title"], row.get("source_series", ""), *row["aliases"]]
                    ).casefold()
                    for term in filters.search.casefold().split()
                )
            )
        )
    ]
    maximum = 20 if filters.view == "compact" else 100
    return {
        "scope": "registered_macro_series_and_supported_fx_pairs",
        "network_access": False,
        "matched": len(matches),
        "returned": min(maximum, len(matches)),
        "truncated": len(matches) > maximum,
        "records": matches[:maximum],
        "limitations": [
            "Templates describe supported queries; availability and publication freshness vary.",
            "Unit/base-year metadata for BEA and Japan is resolved from each source response.",
            "Refine filters when matches exceed the 100 full / 20 compact template limit.",
        ],
        "international_discovery": [
            {"dataset": "sdmx_catalog", "source_id": source} for source in ("imf", "oecd")
        ],
    }
