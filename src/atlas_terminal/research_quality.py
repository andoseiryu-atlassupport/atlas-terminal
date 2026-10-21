"""Explicit research requirements evaluated against the complete selected result."""

import calendar
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Model
from atlas_terminal.util import stamp, utcnow


class ResearchRequirements(Model):
    max_source_age_seconds: int | None = Field(
        default=None,
        ge=0,
        le=31536000,
        strict=True,
        description=(
            "Maximum age of the oldest source retrieval/check used, "
            "including inputs. Does not force refresh."
        ),
    )
    min_observation_date: str | None = Field(
        default=None,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
        json_schema_extra={"format": "date"},
        description=(
            "Minimum date for the latest usable selected output observation "
            "or period end, not its publication time."
        ),
    )
    allow_missing: bool = Field(
        default=True,
        strict=True,
        description=(
            "Permit Atlas-marked unavailable/ambiguous cells, null value/primary amount fields "
            "and partial coverage; optional metadata is not certified."
        ),
    )
    allow_derived: bool = Field(
        default=True,
        strict=True,
        description="Permit output/dependency records marked as derived.",
    )
    allow_truncated: bool = Field(
        default=True,
        strict=True,
        description=(
            "Permit explicit record, archive or footnote truncation. "
            "A fixed upstream window alone is not truncation."
        ),
    )

    @field_validator("min_observation_date")
    @classmethod
    def observation_date(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 10 or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError("Use an ISO calendar date")
        return value


class QualityCheck(Model):
    requirement: Literal[
        "max_source_age_seconds",
        "min_observation_date",
        "allow_missing",
        "allow_derived",
        "allow_truncated",
    ]
    passed: bool
    required: Any
    actual: Any


class ResearchQuality(Model):
    evaluated_at: str
    scope: Literal["complete_selected_result"] = "complete_selected_result"
    oldest_source_retrieved_at: str | None
    newest_source_retrieved_at: str | None
    max_source_age_seconds: int | None
    latest_observation_date: str | None
    missing_value_count: int = Field(ge=0)
    derived_record_count: int = Field(ge=0)
    partial_coverage: bool
    truncated: bool
    meets_requirements: bool
    checks: list[QualityCheck]


def dictionaries(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)


def leaf_records(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for row in records:
        if isinstance(row.get("records"), list):
            yield from leaf_records(row["records"])
        else:
            yield row


def missing(row: dict[str, Any], dataset: str = "") -> bool:
    amount_fields = {
        "federal_debt": ("tot_pub_debt_out_amt", "debt_held_public_amt", "intragov_hold_amt"),
        "institutional_holdings": ("reported_value", "amount"),
    }.get(dataset, ())
    return (
        row.get("status") in {"unavailable", "ambiguous"}
        or ("value" in row and row["value"] is None)
        or any(key in row and row[key] is None for key in amount_fields)
    )


def record_date(row: dict[str, Any], dataset: str) -> str | None:
    """Observation/period ends, never source retrieval or filing times for XBRL facts."""
    candidates = [row.get("date"), row.get("period_end")]
    if isinstance(row.get("period"), dict):
        candidates.append(row["period"].get("end"))
    if isinstance(row.get("reported_context"), dict):
        candidates.append(row["reported_context"].get("end"))
    candidates.extend([row.get("report_date"), row.get("transaction_date")])
    if dataset in {"filings", "ownership_filings", "fund_filings", "corporate_action_filings"}:
        candidates.insert(0, row.get("filed_on"))
    if dataset == "policy_news" and row.get("published_at"):
        candidates.append(
            datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            .astimezone(UTC)
            .date()
            .isoformat()
        )
    for value in candidates:
        if isinstance(value, str) and len(value) == 10:
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                pass
    year, period = row.get("year"), row.get("period")
    if isinstance(year, str) and year.isdigit() and isinstance(period, str):
        if period in {f"M{month:02d}" for month in range(1, 14)}:
            month = min(int(period[1:]), 12)
            return date(int(year), month, calendar.monthrange(int(year), month)[1]).isoformat()
    return None


def assess_quality(
    result: dict[str, Any],
    requirements: ResearchRequirements | None = None,
    *,
    now: datetime | None = None,
) -> ResearchQuality:
    now = now or utcnow()
    rows = list(dictionaries(result["records"]))
    # Include older calculation inputs and every issuer's source, not just the newest root time.
    sources = [
        row
        for row in [*rows, *dictionaries(result.get("sources", []))]
        if {"raw_source_url", "raw_sha256", "retrieved_at"} <= row.keys()
    ]
    times = [datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00")) for row in sources]
    oldest, newest = (min(times), max(times)) if times else (None, None)
    age = math.ceil((now - oldest).total_seconds()) if oldest is not None else None
    future_source = newest is not None and newest > now
    dates = [
        observed
        for row in leaf_records(result["records"])
        if not missing(row, result["dataset"])
        and (observed := record_date(row, result["dataset"])) is not None
    ]
    if result["dataset"] in {"institutional_holdings", "insider_transactions"}:
        filing = result.get("filing", {})
        if any(not missing(row, result["dataset"]) for row in leaf_records(result["records"])):
            reported = record_date({"report_date": filing.get("report_date")}, result["dataset"])
            if reported:
                dates.append(reported)
    latest = max(dates, default=None)
    missing_count = sum(missing(row, result["dataset"]) for row in rows)
    derived_count = sum(
        row.get("derived") is True or row.get("status") == "derived" for row in rows
    )
    metadata = [*dictionaries(result.get("coverage", {})), *rows]
    partial = any(row.get("partial") is True for row in metadata)
    truncated = any(
        value is True and (key == "truncated" or key.endswith("_truncated"))
        for row in metadata
        for key, value in row.items()
    )
    checks = []
    if requirements is not None:
        if requirements.max_source_age_seconds is not None:
            checks.append(
                QualityCheck(
                    requirement="max_source_age_seconds",
                    passed=age is not None
                    and not future_source
                    and age <= requirements.max_source_age_seconds,
                    required=requirements.max_source_age_seconds,
                    actual=None if future_source else age,
                )
            )
        if requirements.min_observation_date is not None:
            checks.append(
                QualityCheck(
                    requirement="min_observation_date",
                    passed=latest is not None and latest >= requirements.min_observation_date,
                    required=requirements.min_observation_date,
                    actual=latest,
                )
            )
        boolean_checks: list[
            tuple[Literal["allow_missing", "allow_derived", "allow_truncated"], int, bool]
        ] = [
            (
                "allow_missing",
                missing_count + int(partial or not result["records"]),
                requirements.allow_missing,
            ),
            ("allow_derived", derived_count, requirements.allow_derived),
            ("allow_truncated", int(truncated), requirements.allow_truncated),
        ]
        for key, count, accepted in boolean_checks:
            if not accepted:
                checks.append(
                    QualityCheck(
                        requirement=key,
                        passed=count == 0,
                        required=False,
                        actual=count > 0,
                    )
                )
    return ResearchQuality(
        evaluated_at=stamp(now),
        oldest_source_retrieved_at=stamp(oldest) if oldest else None,
        newest_source_retrieved_at=stamp(newest) if newest else None,
        max_source_age_seconds=None if future_source else age,
        latest_observation_date=latest,
        missing_value_count=missing_count,
        derived_record_count=derived_count,
        partial_coverage=partial,
        truncated=truncated,
        meets_requirements=all(check.passed for check in checks),
        checks=checks,
    )


def enforce_requirements(
    result: dict[str, Any], requirements: ResearchRequirements
) -> dict[str, Any]:
    report = assess_quality(result, requirements).model_dump(mode="json")
    if not report["meets_requirements"]:
        raise AtlasError(
            "QUALITY_NOT_MET",
            "Selected research does not meet the requested requirements.",
            details={"quality": report},
            status=409,
            fields=["requirements"],
        )
    return report
