"""Operator-supplied licensed credit observations with retained private raw versions."""

import os
import stat
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator

from atlas_terminal.credit_catalog import CREDIT_RATINGS_URL, CREDIT_SCALES
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Model, identifier
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_financials import reference
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_history import SCHEMA, retain_version
from atlas_terminal.util import Database, stamp

MAX_CREDIT_BOOK_BYTES = 4 * 1024 * 1024


class CreditObservation(Model):
    issuer_identifier: str = Field(min_length=1, max_length=100)
    issuer_name: str | None = Field(default=None, max_length=200)
    agency: Literal["sp_global", "moodys", "fitch"]
    rating_scope: Literal["issuer", "issue"]
    issue_identifier: str | None = Field(default=None, min_length=1, max_length=100)
    rating_scale: Literal["global_long_term"]
    currency_scope: Literal["local_currency", "foreign_currency", "unspecified"]
    debt_seniority: str | None = Field(default=None, max_length=100)
    rating_status: Literal["rated", "withdrawn", "not_rated"]
    rating: str | None = Field(default=None, max_length=8)
    rating_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    outlook: Literal["positive", "stable", "negative", "developing", "unspecified"]
    watch: Literal["positive", "negative", "evolving", "none", "unspecified"]
    original_source_url: str = Field(min_length=1, max_length=2048)
    published_at: str | None = Field(default=None, max_length=64)

    @field_validator("issuer_identifier")
    @classmethod
    def issuer_code(cls, value: str) -> str:
        return identifier(value)

    @field_validator("original_source_url")
    @classmethod
    def public_reference(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use a public HTTPS source path without query credentials or fragments"
            )
        return value

    @model_validator(mode="after")
    def scoped_rating(self) -> "CreditObservation":
        if (self.rating_scope == "issue") != (self.issue_identifier is not None):
            raise ValueError("An issue rating requires its own issue identifier")
        if self.rating_scope == "issue" and self.rating in {"SD", "RD"}:
            raise ValueError(
                "Selective/restricted default grades identify issuers, not a specific issue"
            )
        if self.rating_status == "rated":
            if self.rating not in CREDIT_SCALES[self.agency]["grades"]:
                raise ValueError("Unsupported grade on the stated agency/global long-term scale")
        elif self.rating is not None:
            raise ValueError("Withdrawn/not-rated records must not assert a current grade")
        when = date.fromisoformat(self.rating_date)
        if not 1900 <= when.year <= 2100 or when > datetime.now(UTC).date():
            raise ValueError("A rating observation needs a valid nonfuture reference date")
        if self.published_at:
            published = datetime.fromisoformat(self.published_at)
            if published.tzinfo is None or published > datetime.now(UTC):
                raise ValueError("Publication time must be timezone-aware and not in the future")
        return self


class CreditBook(Model):
    schema_version: Literal["0.1.0"]
    is_synthetic: bool = Field(strict=True)
    ratings: list[CreditObservation] = Field(max_length=10000)


def credit_snapshot(synthetic: bool, directory: Path) -> tuple[Snapshot, CreditBook]:
    if synthetic:
        body = (
            files("atlas_terminal").joinpath("resources/research/credit_ratings.json").read_bytes()
        )
        retrieved = "2026-10-27T02:00:00Z"
    else:
        configured = os.environ.get("ATLAS_CREDIT_RATINGS_FILE")
        if not configured:
            raise AtlasError(
                "CONFIG_REQUIRED",
                "Configure an operator-owned ATLAS_CREDIT_RATINGS_FILE; no free "
                "issuer-rating feed is configured.",
            )
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(Path(configured).expanduser(), flags)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError("Regular file required")
                body = stream.read(MAX_CREDIT_BOOK_BYTES + 1)
        except (OSError, ValueError, RuntimeError):
            raise AtlasError(
                "CONFIG_REQUIRED",
                "The configured credit-rating file must be a readable regular file.",
            ) from None
        retrieved = stamp()
    if len(body) > MAX_CREDIT_BOOK_BYTES:
        raise AtlasError(
            "SOURCE_UNAVAILABLE", "Credit-rating input exceeds the supported byte limit."
        )
    try:
        book = CreditBook.model_validate_json(body)
    except (ValidationError, ValueError, TypeError, AtlasError):
        raise AtlasError(
            "SOURCE_UNAVAILABLE",
            "Invalid credit-rating input; check agency, scale, subject, grade, date "
            "and public reference fields.",
        ) from None
    if not synthetic:
        store = Database(directory / "public-cache.sqlite3", SCHEMA)
        with store.connect(write=True) as conn:
            retain_version(conn, CREDIT_RATINGS_URL, body, retrieved)
    url = "https://fixtures.atlas.invalid/credit_ratings.json" if synthetic else CREDIT_RATINGS_URL
    return Snapshot(body, url, retrieved), book


def credit_ratings(q: ResearchQuery, directory: Path, *, synthetic: bool) -> dict[str, Any]:
    snapshot, book = credit_snapshot(synthetic, directory)
    rows = []
    for index, observation in enumerate(book.ratings):
        if observation.issuer_identifier != q.identifier or (
            q.agency and observation.agency != q.agency
        ):
            continue
        scale = CREDIT_SCALES[observation.agency]
        grade = observation.rating
        rank = scale["grades"].index(grade) if grade is not None else None
        rows.append(
            {
                **observation.model_dump(),
                "date": observation.rating_date,
                "status": "reported",
                "unit": "agency_credit_grade",
                "investment_grade": rank < 10 if rank is not None else None,
                "agency_grade_order": rank + 1 if rank is not None else None,
                "classification_basis": "within_agency_global_long_term_reference_only",
                "rating_definition_url": scale["reference_url"],
                "source_kind": "operator_supplied",
                "origin_verified": False,
                "currentness_verified": False,
                "source": reference(snapshot, f"/ratings/{index}"),
            }
        )
    if not rows:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "No selected issuer/agency observations in the configured book; absence "
            "does not mean not rated.",
        )
    rows.sort(
        key=lambda r: (r["rating_date"], r["agency"], r["issue_identifier"] or ""), reverse=True
    )
    return {
        "records": rows[: q.limit],
        "sources": [reference(snapshot, "")],
        "is_synthetic": synthetic or book.is_synthetic,
        "data_origin": "operator_supplied_licensed_or_original_observations",
        "coverage": {
            "matching_records": len(rows),
            "returned_records": min(q.limit, len(rows)),
            "truncated": len(rows) > q.limit,
            "point_in_time": False,
            "origin_verified": False,
        },
        "limitations": [
            "These are operator-supplied observations, not an automatically "
            "verified agency feed. Rights and source assertions belong to the "
            "operator.",
            "Issuer/issue, currency scope, seniority, agency, date, outlook and "
            "watch remain separate. No best-rating, current-rating or cross-agency "
            "equivalence is inferred.",
            "Investment-grade classification and grade order apply only to the "
            "stated global long-term agency scale; they are not default "
            "probabilities or observed default events.",
            "The reserved local URL identifies retained file bytes, not a fetched "
            "agency response or public file path. Locally observed versions do not "
            "establish publisher PIT.",
            "Configuring this file makes its selected records and original retained "
            "book available to this research service's clients.",
        ],
    }
