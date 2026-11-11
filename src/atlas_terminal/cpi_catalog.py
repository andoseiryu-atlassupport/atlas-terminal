"""Explicit CPI-U expenditure scopes, supported calculations and monthly bounds."""

import calendar
import re
from datetime import date
from typing import Any

VERSION = "bls-cpi-effects-v1"
ARCHIVE_URL = "https://www.bls.gov/bls/news-release/cpi.htm"
METHOD_URL = "https://www.bls.gov/cpi/factsheets/contributions-effects.htm"
WEIGHTS_URL = "https://www.bls.gov/cpi/tables/relative-importance/home.htm"
MAX_MONTHS = 36
MAX_DOCUMENTS = 54
DEADLINE_SECONDS = 90

# Exact expenditure labels, not fuzzy matches or series-name heuristics. Education
# includes goods and services; it is deliberately marked as a cross-cutting scope.
CATEGORIES: dict[str, dict[str, Any]] = {
    "cpi": {"code": "SA0", "label": "All items", "parent": None},
    "food_cpi": {"code": "SAF1", "label": "Food", "parent": "cpi"},
    "energy_cpi": {"code": "SA0E", "label": "Energy", "parent": "cpi"},
    "core_cpi": {"code": "SA0L1E", "label": "All items less food and energy", "parent": "cpi"},
    "core_goods_cpi": {
        "code": "SACL1E",
        "label": "Commodities less food and energy commodities",
        "parent": "core_cpi",
    },
    "core_services_cpi": {
        "code": "SASLE",
        "label": "Services less energy services",
        "parent": "core_cpi",
    },
    "food_at_home_cpi": {"code": "SAF11", "label": "Food at home", "parent": "food_cpi"},
    "food_away_from_home_cpi": {
        "code": "SEFV",
        "label": "Food away from home",
        "parent": "food_cpi",
    },
    "gasoline_cpi": {"code": "SETB01", "label": "Gasoline (all types)", "parent": "energy_cpi"},
    "shelter_cpi": {"code": "SAH1", "label": "Shelter", "parent": "core_services_cpi"},
    "rent_cpi": {"code": "SEHA", "label": "Rent of primary residence", "parent": "shelter_cpi"},
    "owners_equivalent_rent_cpi": {
        "code": "SEHC",
        "label": "Owners' equivalent rent of residences",
        "parent": "shelter_cpi",
    },
    "education_cpi": {
        "code": "SAE1",
        "label": "Education",
        "parent": "core_cpi",
        "cross_cutting": ["core_goods_cpi", "core_services_cpi"],
    },
}
SETS = {
    "all_items_basic": ["food_cpi", "energy_cpi", "core_goods_cpi", "core_services_cpi"],
    "core_basic": ["core_goods_cpi", "core_services_cpi"],
}
COMBINATIONS = [
    {
        "target": "all_items",
        "change": "mom",
        "seasonal_adjustment": "sa",
        "method": "reported_table_6",
    },
    {
        "target": "all_items",
        "change": "yoy",
        "seasonal_adjustment": "nsa",
        "method": "reported_table_7",
    },
    {
        "target": "core",
        "change": "mom",
        "seasonal_adjustment": "nsa",
        "method": "calculated_monthly_relative_importance_chain",
    },
    {
        "target": "core",
        "change": "yoy",
        "seasonal_adjustment": "nsa",
        "method": "calculated_monthly_relative_importance_chain",
    },
]


def shift_month(month: str, offset: int) -> str:
    year, part = (int(v) for v in month.split("-"))
    year, part = divmod(year * 12 + part - 1 + offset, 12)
    return f"{year:04d}-{part + 1:02d}"


def months_between(start: str, end: str) -> list[str]:
    result = []
    month = start
    while month <= end:
        result.append(month)
        month = shift_month(month, 1)
    return result


def valid_month(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value):
        return False
    return 1994 <= int(value[:4]) <= 2100


def period_end(month: str) -> str:
    year, part = (int(v) for v in month.split("-"))
    return date(year, part, calendar.monthrange(year, part)[1]).isoformat()


def ancestors(key: str) -> list[str]:
    result = []
    while parent := CATEGORIES[key]["parent"]:
        result.append(parent)
        key = parent
    return result


def stacking(categories: list[str], target: str) -> dict[str, Any]:
    root = "cpi" if target == "all_items" else "core_cpi"
    outside = [key for key in categories if key != root and root not in ancestors(key)]
    overlaps = []
    for i, left in enumerate(categories):
        for right in categories[i + 1 :]:
            if (
                left in ancestors(right)
                or right in ancestors(left)
                or left in CATEGORIES[right].get("cross_cutting", [])
                or right in CATEGORIES[left].get("cross_cutting", [])
            ):
                overlaps.append([left, right])
            elif "education_cpi" in {left, right}:
                # Unknown intersections of education with more detailed selections
                # are not assumed disjoint merely because their tree parents differ.
                other = right if left == "education_cpi" else left
                if "core_cpi" in ancestors(other):
                    overlaps.append([left, right])
    wanted = SETS["all_items_basic" if target == "all_items" else "core_basic"]
    complete = set(categories) == set(wanted)
    reasons = []
    if overlaps:
        reasons.append("overlapping_or_unverified_intersection")
    if outside:
        reasons.append("category_outside_target")
    if not complete:
        reasons.append("not_a_verified_complete_decomposition")
    return {
        "non_overlapping": not overlaps,
        "overlaps": overlaps,
        "outside_target": outside,
        "covers_target": complete and not outside,
        "classification_stackable": not overlaps and not outside,
        "reasons": reasons,
    }


def cpi_catalog() -> dict[str, Any]:
    return {
        "mapping_version": VERSION,
        "population": "CPI-U",
        "area": "U.S. city average",
        "categories": {
            key: {**spec, "ancestors": ancestors(key), "nsa_series_id": "CUUR0000" + spec["code"]}
            for key, spec in CATEGORIES.items()
        },
        "stacking_sets": SETS,
        "supported_combinations": COMBINATIONS,
        "computed_core_first_month": "2023-01",
        "computed_core_first_yoy_month": "2023-12",
        "availability": "Discovered dated HTML releases; parsed coverage is returned per query.",
        "max_requested_months": MAX_MONTHS,
        "max_documents": MAX_DOCUMENTS,
        "deadline_seconds": DEADLINE_SECONDS,
        "unsupported": [
            "Core SA effects: seasonal aggregation weights and revision vintages unverified.",
            "All-items NSA MoM and SA YoY effects: no supported published analysis table.",
            "Computed core before 2023; index/weight gaps or failed rounding reconciliation.",
            "Median and trimmed-mean CPI are not additive expenditure categories.",
            "PDF/TXT-only archives and supplemental XLSX/ZIP-only editions are not parsed.",
        ],
        "definitions": {
            "rate_pct": "Item price change in percent.",
            "effect_pt": "Effect on the explicitly named target, in percentage points.",
            "relative_importance_pct": "Published share of all-items CPI at weight_base_month.",
            "contribution_share_pct": "100 * effect_pt / target_rate_pct; null for zero change.",
        },
        "method_url": METHOD_URL,
        "weights_url": WEIGHTS_URL,
    }
