"""Publish source-reported inflation measures without reconstructing their distribution."""

from typing import Any

from atlas_terminal.economic_dates import within
from atlas_terminal.fixed_income_sources import csv_reference
from atlas_terminal.inflation_sources import (
    INFLATION_DOCUMENTATION,
    INFLATION_SERIES,
    INFLATION_URLS,
    parse_inflation_csv,
)
from atlas_terminal.providers import Transport
from atlas_terminal.research_economics import economic_page
from atlas_terminal.research_models import ResearchQuery


def inflation_measures(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.series is not None
    spec = INFLATION_SERIES[q.series]
    snapshot = transport.get(INFLATION_URLS[str(spec["kind"])], 21600)
    records = []
    for observation in parse_inflation_csv(snapshot, str(spec["kind"])):
        if observation["column"] != spec["column"] or not within(
            observation["date"], q.start_date, q.end_date
        ):
            continue
        records.append(
            {
                **{k: v for k, v in observation.items() if k != "column"},
                "series": q.series,
                "country": "US",
                "frequency": "monthly",
                "date_basis": "observation_period_end",
                "unit": spec["unit"],
                "seasonally_adjusted": True,
                "annualized": spec["annualized"],
                "base_period": None,
                "published_at": None,
                "status": "unavailable" if observation["value"] is None else "reported",
                "reason": "Source cell is missing." if observation["value"] is None else None,
                "normalization_formula": "source_value (no rescaling or reannualization)",
                "category_path": spec["category_path"],
                "definition_url": INFLATION_DOCUMENTATION,
                "definition": (
                    (
                        "Expenditure-weighted monthly CPI distribution with 8% "
                        "trimmed from each tail (16% total)."
                    )
                    if spec["kind"] == "trimmed_mean_cpi"
                    else (
                        "Component at the 50th percentile of the "
                        "expenditure-weighted monthly CPI distribution."
                    )
                ),
            }
        )
    return economic_page(
        q,
        records,
        [csv_reference(snapshot, 1, 1, "date ")],
        source_id="cleveland_fed",
        title=str(spec["title"]),
        window="Current published CSV history",
        limitations=[
            (
                "Current revised observations, not release-time vintages; "
                "period-end dates are not publication dates."
            ),
            (
                "Monthly changes are decimal ratios; the separate annualized "
                "column is published percent, not recalculated by Atlas."
            ),
            (
                "Median and 16% trimmed-mean CPI are distribution measures, not "
                "fixed food/energy exclusions or Dallas trimmed-mean PCE."
            ),
        ],
    )
