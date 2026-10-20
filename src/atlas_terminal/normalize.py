"""Conservative annual primary-filing selection; no fiscal label inference."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import (
    Company,
    Coverage,
    Fact,
    FinancialQuery,
    FinancialResult,
    Metric,
    Period,
    Source,
    Warning,
)
from atlas_terminal.providers import Filing, Snapshot

TAGS: dict[Metric, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
}
INSTANT = {"assets", "liabilities"}
LIMITATIONS = [
    "Annual USD US-GAAP facts only; not investment advice.",
    "latest_primary_filing_per_fact_v1 excludes revisions appearing only as comparative values "
    "in later primary-year filings.",
    "Current retrieval is not point-in-time historical availability.",
]


@dataclass(frozen=True)
class Candidate:
    metric: Metric
    tag: str
    raw: dict[str, Any]
    filing: Filing
    pointer: str
    value: str


def number(value: Any) -> str:
    if isinstance(value, (bool, float)) or not isinstance(value, (int, Decimal)):
        raise ValueError("Raw number must be exact")
    d = Decimal(value)
    if not d.is_finite():
        raise ValueError("Non-finite number")
    return format(d, "f")


def normalize(
    query: FinancialQuery,
    company: Company,
    snapshot: Snapshot,
    filings: list[Filing],
    *,
    synthetic: bool,
    history_truncated: bool = False,
) -> FinancialResult:
    taxonomy = snapshot.data.get("facts", {}).get("us-gaap")
    if not taxonomy:
        raise AtlasError("UNSUPPORTED_TAXONOMY", "No supported US-GAAP facts for this issuer.")
    index = {f.accession: f for f in filings if f.form in {"10-K", "10-K/A"}}
    candidates: list[Candidate] = []
    unsupported_periods: set[str] = set()
    # Scan all duration metrics to anchor real annual periods, even for assets-only queries.
    for metric, tags in TAGS.items():
        for tag in tags:
            for i, raw in enumerate(taxonomy.get(tag, {}).get("units", {}).get("USD", [])):
                filing = index.get(raw.get("accn"))
                if not filing or raw.get("end") != filing.report_date:
                    continue
                if raw.get("form") != filing.form or raw.get("filed") != filing.filed_on:
                    continue
                if metric in INSTANT:
                    if raw.get("start"):
                        continue
                else:
                    try:
                        days = (
                            date.fromisoformat(raw["end"]) - date.fromisoformat(raw["start"])
                        ).days
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not 330 <= days <= 400:
                        unsupported_periods.add(filing.report_date)
                        continue
                try:
                    value = number(raw["val"])
                except (KeyError, ValueError, InvalidOperation):
                    continue
                candidates.append(
                    Candidate(
                        metric, tag, raw, filing, f"/facts/us-gaap/{tag}/units/USD/{i}", value
                    )
                )
    ends = sorted({c.raw["end"] for c in candidates if c.metric not in INSTANT}, reverse=True)[
        : query.count
    ]
    if not ends:
        code = "UNSUPPORTED_PERIOD" if unsupported_periods else "INCOMPLETE_COVERAGE"
        raise AtlasError(code, "No eligible annual USD primary-filing periods.")
    facts: list[Fact] = []
    warnings: list[Warning] = []
    for end in ends:
        for metric in sorted(query.metrics):
            kind: Literal["instant", "duration"] = "instant" if metric in INSTANT else "duration"
            eligible = [c for c in candidates if c.metric == metric and c.raw["end"] == end]
            # Concept priority applies only among eligible primary-year facts.
            for tag in TAGS[metric]:
                selected_tag = [c for c in eligible if c.tag == tag]
                if selected_tag:
                    eligible = selected_tag
                    break
            if not eligible:
                facts.append(
                    Fact(
                        metric=metric,
                        value=None,
                        status="unavailable",
                        unit="USD",
                        period=Period(kind=kind, start=None, end=end),
                        reason="No eligible USD primary-filing fact for this metric and period.",
                        source=None,
                    )
                )
                continue
            newest_day = max(c.filing.filed_on for c in eligible)
            eligible = [c for c in eligible if c.filing.filed_on == newest_day]
            # Missing times cannot be sorted ahead of/behind known times on the same day.
            if all(c.filing.accepted_at for c in eligible):
                newest_time = max(
                    datetime.fromisoformat(c.filing.accepted_at.replace("Z", "+00:00"))
                    for c in eligible
                    if c.filing.accepted_at
                )
                eligible = [
                    c
                    for c in eligible
                    if c.filing.accepted_at
                    and datetime.fromisoformat(c.filing.accepted_at.replace("Z", "+00:00"))
                    == newest_time
                ]
            identities = {(Decimal(c.value), c.raw.get("start"), c.raw["end"]) for c in eligible}
            if len(identities) != 1:
                starts = {c.raw.get("start") for c in eligible}
                facts.append(
                    Fact(
                        metric=metric,
                        value=None,
                        status="ambiguous",
                        unit="USD",
                        period=Period(
                            kind=kind,
                            start=next(iter(starts)) if len(starts) == 1 else None,
                            end=end,
                        ),
                        reason="Conflicting equally ranked primary-filing facts.",
                        source=None,
                    )
                )
                continue
            # The values/periods are equal: choose a stable provenance, not a larger value.
            c = sorted(eligible, key=lambda c: (c.filing.accession, c.pointer))[0]
            facts.append(
                Fact(
                    metric=metric,
                    value=c.value,
                    status="reported",
                    unit="USD",
                    period=Period(
                        kind=kind, start=c.raw.get("start") if kind == "duration" else None, end=end
                    ),
                    reason=None,
                    source=Source(
                        accession=c.filing.accession,
                        form=cast(Literal["10-K", "10-K/A"], c.filing.form),
                        filed_on=c.filing.filed_on,
                        accepted_at=c.filing.accepted_at,
                        tag=c.tag,
                        source_url=c.filing.source_url,
                        raw_source_url=snapshot.url,
                        json_pointer=c.pointer,
                        raw_sha256=snapshot.sha256,
                        retrieved_at=snapshot.retrieved_at,
                    ),
                )
            )
        accessions = {f.source.accession for f in facts if f.period.end == end and f.source}
        if len(accessions) > 1:
            warnings.append(
                Warning(
                    code="mixed_filing_sources",
                    message=f"{end[:4]} metrics use the original filing and the metric-specific "
                    "amendment; each field retains its own source.",
                )
            )
    reported = sum(f.status == "reported" for f in facts)
    partial = reported != len(query.metrics) * query.count
    if partial:
        warnings.append(
            Warning(
                code="incomplete_coverage",
                message="A requested metric is unavailable; it is not replaced with zero "
                "or a derived value.",
            )
        )
    for metric in query.metrics:
        if len({f.source.tag for f in facts if f.metric == metric and f.source}) > 1:
            warnings.append(
                Warning(
                    code="concept_changed_across_periods",
                    message=f"{metric} uses different reported concepts across periods.",
                )
            )
    if unsupported_periods:
        warnings.append(
            Warning(
                code="unsupported_period",
                message="Short or nonannual primary periods were excluded.",
            )
        )
    if history_truncated:
        warnings.append(
            Warning(
                code="history_truncated",
                message="Submission archive page cap reached; coverage may be incomplete.",
            )
        )
        partial = True
    coverage = Coverage(
        requested_periods=query.count,
        available_periods=len(ends),
        requested_cells=query.count * len(query.metrics),
        reported_cells=reported,
    )
    if reported == 0 or (partial and not query.allow_partial):
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Requested annual coverage is incomplete; no quote is available.",
            details={
                "coverage": coverage.model_dump(),
                "period_ends": ends,
                "missing": [
                    {"metric": f.metric, "period_end": f.period.end, "reason": f.reason}
                    for f in facts
                    if f.status != "reported"
                ],
            },
        )
    return FinancialResult(
        company=company,
        query=query,
        provider="fixture" if synthetic else "sec",
        period_ends=ends,
        facts=facts,
        coverage=coverage,
        partial=partial,
        warnings=warnings,
        limitations=LIMITATIONS
        + (
            ["Synthetic fixture: company, identifiers, amounts and links are fictional."]
            if synthetic
            else []
        ),
        retrieved_at=snapshot.retrieved_at,
        is_synthetic=synthetic,
    )
