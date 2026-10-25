"""Bounded research presentation snapshots and content-addressed provenance."""

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Hash, Model
from atlas_terminal.research_models import Dataset, ResearchQuery
from atlas_terminal.research_quality import ResearchQuality, enforce_requirements
from atlas_terminal.util import Database, canonical, digest, stamp, utcnow


class SourceEvidence(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "anyOf": [
                {"required": ["json_pointer"], "properties": {"json_pointer": {"type": "string"}}},
                {
                    "required": ["xml_local_path"],
                    "properties": {"xml_local_path": {"type": "string"}},
                },
                {
                    "required": ["csv_row", "csv_column"],
                    "properties": {
                        "csv_row": {"type": "integer", "minimum": 1},
                        "csv_column": {"type": "integer", "minimum": 1},
                    },
                },
            ]
        },
    )
    raw_source_url: str = Field(json_schema_extra={"format": "uri"})
    raw_sha256: Hash
    retrieved_at: str = Field(json_schema_extra={"format": "date-time"})
    json_pointer: str | None = None
    xml_local_path: str | None = None
    csv_row: int | None = Field(default=None, ge=1, strict=True)
    csv_column: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def source_identity(self) -> "SourceEvidence":
        parsed = urlsplit(self.raw_source_url)
        retrieved = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or retrieved.tzinfo is None
            or (
                self.json_pointer is None
                and self.xml_local_path is None
                and (self.csv_row is None or self.csv_column is None)
            )
            or (self.csv_row is None) != (self.csv_column is None)
        ):
            raise ValueError("Source URL, time and locator are required")
        return self


class EvidenceReference(Model):
    evidence_id: Hash


class ResponseMetadata(Model):
    view: Literal["full", "compact"]
    fields: list[str]
    snapshot_id: Hash
    result_sha256: Hash
    expires_at: str
    total_records: int = Field(ge=0)
    returned_records: int = Field(ge=0)
    offset: int = Field(ge=0)
    page_size: int | None
    next_cursor: str | None
    evidence_count: int = Field(ge=0)


class ResearchResult(BaseModel):
    """Common envelope; dataset-specific records and metadata remain extensible."""

    model_config = ConfigDict(extra="allow")
    schema_version: str
    dataset: Dataset
    query: dict[str, Any]
    source_id: str
    source_terms_url: str
    documentation: dict[str, str] | None = None
    upstream_data_cost: Literal["0"]
    is_synthetic: bool
    source_text_is_untrusted: Literal[True]
    retrieved_at: str
    records: list[dict[str, Any]]
    sources: list[SourceEvidence | EvidenceReference]
    coverage: dict[str, Any]
    limitations: list[str]
    response: ResponseMetadata | None = None
    quality: ResearchQuality | None = None


class EvidenceResult(Model):
    schema_version: Literal["0.2.0"] = "0.2.0"
    evidence_id: Hash
    evidence: SourceEvidence
    expires_at: str
    source_text_is_untrusted: Literal[True] = True


SCHEMA = """
CREATE TABLE IF NOT EXISTS research_snapshots (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, fingerprint TEXT NOT NULL,
 body BLOB NOT NULL, sha256 TEXT NOT NULL, expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_evidence (
 scope TEXT NOT NULL, id TEXT NOT NULL, body BLOB NOT NULL, expires TEXT NOT NULL,
 PRIMARY KEY(scope,id)
);
"""

# Field selection never strips context needed to interpret a selected value.
# Reported inputs/formulas and per-issuer coverage remain intact too.
CONTEXT_FIELDS = {
    "id",
    "dataflow",
    "dimensions",
    "dimension_labels",
    "unit_label",
    "seasonal_adjustment",
    "base_period",
    "observation_status",
    "observation_status_label",
    "source_attributes",
    "period_start",
    "input_frequency",
    "aggregation",
    "input_count",
    "input_start",
    "input_end",
    "missing_period_ends",
    "coverage_basis",
    "metric",
    "field",
    "identifier",
    "cik",
    "ticker",
    "series",
    "series_id",
    "date",
    "maturity",
    "concept",
    "accession",
    "form",
    "report_date",
    "filed_on",
    "accepted_at",
    "period",
    "period_end",
    "period_ends",
    "comparison_period",
    "comparison_date",
    "elapsed_calendar_days",
    "country",
    "currency",
    "interpretation",
    "period_kind",
    "year",
    "unit",
    "reported_value_unit",
    "price_unit",
    "amount_type",
    "status",
    "reason",
    "derived",
    "formula",
    "decimal_precision",
    "comparable",
    "content_is_untrusted",
    "source",
    "sources",
    "inputs",
    "coverage",
    "limitations",
    "footnote_ids",
    "seasonally_adjusted",
    "record_type",
    "security_type",
    "transaction_date",
    "transaction_code",
    "acquired_disposed",
    "direct_indirect",
    "cusip",
    "figi",
    "issuer_name",
    "title_of_class",
    "reference_kind",
    "published_at",
    "published_at_raw",
    "url",
    "base_currency",
    "quote_currency",
    "quote_convention",
    "rate_type",
    "frequency",
    "revision_indicator",
    "source_series",
    "rounding",
    "date_basis",
    "series_title",
    "table",
    "line_number",
    "line_description",
    "metric_name",
    "unit_multiplier",
    "scale_formula",
    "annualized",
    "source_period",
    "source_updated_on",
    "is_provisional",
    "survey_code",
    "survey_name",
    "region_code",
    "annotations",
    "metadata_source",
    "period_source",
}


class ResearchViews:
    """One private volume; cursors are presentation handles, never authorization."""

    def __init__(
        self,
        path: Path,
        *,
        synthetic: bool,
        clock: Callable[[], datetime] = utcnow,
        ttl_seconds: int = 86400,
        max_bytes: int = 64 * 1024 * 1024,
        max_snapshots: int = 128,
        max_evidence: int = 20000,
    ) -> None:
        self.db = Database(path.resolve(), SCHEMA)
        self.scope = "fixture" if synthetic else "public"
        self.clock, self.ttl = clock, ttl_seconds
        self.max_bytes, self.max_snapshots, self.max_evidence = (
            max_bytes,
            max_snapshots,
            max_evidence,
        )

    def fingerprint(self, q: ResearchQuery) -> str:
        return digest(
            canonical(
                {
                    "scope": self.scope,
                    "query": q.model_dump(mode="json", exclude={"response"}),
                    "response": q.response.model_dump(mode="json", exclude={"cursor"}),
                }
            )
        )

    def load(self, q: ResearchQuery) -> tuple[dict[str, Any], str, str, int]:
        cursor = q.response.cursor or ""
        if not re.fullmatch(r"r1_[0-9a-f]{64}_[1-9][0-9]{0,5}", cursor):
            raise AtlasError(
                "INVALID_CURSOR", "Invalid research page cursor.", fields=["response.cursor"]
            )
        _, snapshot_id, raw_offset = cursor.split("_")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_snapshots WHERE id=? AND scope=?",
                (snapshot_id, self.scope),
            ).fetchone()
        if not row or row["expires"] <= stamp(self.clock()):
            raise AtlasError(
                "RESULT_EXPIRED", "Research page snapshot expired or was evicted.", status=410
            )
        if row["fingerprint"] != self.fingerprint(q):
            raise AtlasError(
                "INVALID_CURSOR",
                "Repeat the original query and response options.",
                fields=["response.cursor"],
            )
        if digest(row["body"]) != row["sha256"]:
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Research snapshot failed integrity verification."
            )
        result = json.loads(row["body"])
        offset = int(raw_offset)
        size = q.response.page_size
        if not size or offset % size or offset >= len(result["records"]):
            raise AtlasError(
                "INVALID_CURSOR", "Cursor is outside the saved result.", fields=["response.cursor"]
            )
        return result, snapshot_id, row["expires"], offset

    @staticmethod
    def leaf_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            leaf
            for record in records
            for leaf in (
                ResearchViews.leaf_records(record["records"])
                if isinstance(record.get("records"), list)
                else [record]
            )
        ]

    def render(self, q: ResearchQuery, result: dict[str, Any] | None = None) -> dict[str, Any]:
        options = q.response
        if options.cursor:
            result, snapshot_id, expires, offset = self.load(q)
        else:
            assert result is not None
            offset = 0
            expires = stamp(self.clock() + timedelta(seconds=self.ttl))
            snapshot_id = digest(canonical({"fingerprint": self.fingerprint(q), "result": result}))
        original = result
        available = {key for row in self.leaf_records(original["records"]) for key in row}
        if options.fields and set(options.fields) - available:
            raise AtlasError(
                "INVALID_INPUT",
                "Selected fields are absent from this result's records.",
                fields=["response.fields"],
            )
        rendered = deepcopy(original)
        if options.cursor and q.requirements is not None:
            rendered["quality"] = enforce_requirements(original, q.requirements)
        total = len(original["records"])
        end = min(total, offset + options.page_size) if options.page_size else total
        rendered["records"] = rendered["records"][offset:end]
        if options.fields:
            for row in self.leaf_records(rendered["records"]):
                for key in list(row):
                    if key not in CONTEXT_FIELDS and key not in options.fields:
                        del row[key]
        evidence: dict[str, bytes] = {}

        def compact(value: Any) -> Any:
            if isinstance(value, dict):
                if {"raw_source_url", "raw_sha256", "retrieved_at"} <= value.keys():
                    SourceEvidence.model_validate(value)
                    body = canonical(value)
                    evidence_id = digest(body)
                    evidence[evidence_id] = body
                    return {"evidence_id": evidence_id}
                return {key: compact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [compact(item) for item in value]
            return value

        if options.view == "compact":
            rendered = compact(rendered)
        if not options.cursor:
            self.save(q, original, snapshot_id, expires, evidence)
        elif evidence:
            self.save_evidence(evidence, expires)
        rendered["response"] = ResponseMetadata(
            view=options.view,
            fields=options.fields,
            snapshot_id=snapshot_id,
            result_sha256=digest(canonical(original)),
            expires_at=expires,
            total_records=total,
            returned_records=end - offset,
            offset=offset,
            page_size=options.page_size,
            next_cursor=f"r1_{snapshot_id}_{end}" if end < total else None,
            evidence_count=len(evidence),
        ).model_dump(mode="json")
        ResearchResult.model_validate(rendered)
        return rendered

    def prune(self, conn: Any) -> None:
        now = stamp(self.clock())
        conn.execute("DELETE FROM research_snapshots WHERE expires <= ?", (now,))
        conn.execute("DELETE FROM research_evidence WHERE expires <= ?", (now,))

    def check_capacity(self, conn: Any) -> None:
        used = conn.execute(
            "SELECT (SELECT coalesce(sum(length(body)),0) FROM research_snapshots) "
            "+ (SELECT coalesce(sum(length(body)),0) FROM research_evidence)"
        ).fetchone()[0]
        snapshots = conn.execute("SELECT count(*) FROM research_snapshots").fetchone()[0]
        evidence = conn.execute("SELECT count(*) FROM research_evidence").fetchone()[0]
        if used > self.max_bytes or snapshots > self.max_snapshots or evidence > self.max_evidence:
            raise AtlasError("CACHE_FULL", "Research presentation cache is full; wait for expiry.")

    def save(
        self,
        q: ResearchQuery,
        result: dict[str, Any],
        snapshot_id: str,
        expires: str,
        evidence: dict[str, bytes],
    ) -> None:
        body = canonical(result)
        if len(body) > 4 * 1024 * 1024:
            raise AtlasError("SOURCE_UNAVAILABLE", "Research presentation snapshot exceeds limit.")
        with self.db.connect(write=True) as conn:
            self.prune(conn)
            conn.execute(
                "INSERT INTO research_snapshots VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET expires=max(expires,excluded.expires)",
                (snapshot_id, self.scope, self.fingerprint(q), body, digest(body), expires),
            )
            self.insert_evidence(conn, evidence, expires)
            self.check_capacity(conn)

    def insert_evidence(self, conn: Any, evidence: dict[str, bytes], expires: str) -> None:
        conn.executemany(
            "INSERT INTO research_evidence VALUES (?,?,?,?) "
            "ON CONFLICT(scope,id) DO UPDATE SET expires=max(expires,excluded.expires)",
            [(self.scope, evidence_id, body, expires) for evidence_id, body in evidence.items()],
        )

    def save_evidence(self, evidence: dict[str, bytes], expires: str) -> None:
        with self.db.connect(write=True) as conn:
            self.prune(conn)
            self.insert_evidence(conn, evidence, expires)
            self.check_capacity(conn)

    def evidence(self, evidence_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_id):
            raise AtlasError(
                "INVALID_INPUT", "Use a returned research evidence ID.", fields=["evidence_id"]
            )
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_evidence WHERE scope=? AND id=?",
                (self.scope, evidence_id),
            ).fetchone()
        if not row or row["expires"] <= stamp(self.clock()):
            raise AtlasError(
                "EVIDENCE_NOT_FOUND",
                "Evidence expired or is absent from this state volume.",
                status=404,
            )
        if digest(row["body"]) != evidence_id:
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Research evidence failed integrity verification."
            )
        value = {
            "schema_version": "0.2.0",
            "evidence_id": evidence_id,
            "evidence": json.loads(row["body"]),
            "expires_at": row["expires"],
            "source_text_is_untrusted": True,
        }
        EvidenceResult.model_validate(value)
        return value
