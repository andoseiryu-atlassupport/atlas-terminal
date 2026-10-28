"""Bounded private source versions, using local observation times rather than PIT claims."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Hash, Model
from atlas_terminal.util import Database, canonical, digest, stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_versions (
 url TEXT NOT NULL, sha256 TEXT NOT NULL, body BLOB NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, retrieved_at TEXT NOT NULL,
 PRIMARY KEY(url, sha256)
);
CREATE TABLE IF NOT EXISTS source_observations (
 url TEXT NOT NULL, sha256 TEXT NOT NULL, observed_at TEXT NOT NULL,
 PRIMARY KEY(url,observed_at)
);
"""
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_VERSIONS = 1024


class SourceHistoryQuery(Model):
    action: Literal["list", "get", "as_of", "diff"] = "list"
    url: str = Field(min_length=1, max_length=2048)
    sha256: Hash | None = None
    before_sha256: Hash | None = None
    as_of: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=100, ge=1, le=500, strict=True)

    @model_validator(mode="after")
    def arguments(self) -> "SourceHistoryQuery":
        expected = {
            "list": set(),
            "get": {"sha256"},
            "as_of": {"as_of"},
            "diff": {"sha256", "before_sha256"},
        }[self.action]
        supplied = {
            key for key in ("sha256", "before_sha256", "as_of") if getattr(self, key) is not None
        }
        if supplied != expected:
            raise ValueError("Supply only the arguments required by the history action")
        if self.as_of:
            when = datetime.fromisoformat(self.as_of)
            if when.tzinfo is None:
                raise ValueError("Use a timezone-aware as_of")
            from datetime import UTC

            self.as_of = (
                when.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            )
        return self


def retain_version(conn: Any, url: str, body: bytes, retrieved_at: str) -> None:
    """Keep prior bytes within a quota; never delete a version silently."""
    sha = digest(body)
    existing = conn.execute(
        "SELECT body FROM source_versions WHERE url=? AND sha256=?", (url, sha)
    ).fetchone()
    now = stamp()
    count = conn.execute("SELECT count(*) FROM source_observations").fetchone()[0]
    if count >= 100000:
        raise AtlasError("CACHE_FULL", "Source observation archive quota reached.")
    conn.execute("INSERT INTO source_observations VALUES (?,?,?)", (url, sha, now))
    if existing is not None:
        if digest(existing["body"]) != sha:
            raise AtlasError("SOURCE_UNAVAILABLE", "Archived source failed integrity verification.")
        conn.execute(
            "UPDATE source_versions SET last_seen=? WHERE url=? AND sha256=?", (now, url, sha)
        )
        return
    usage = conn.execute(
        "SELECT count(*), coalesce(sum(length(body)),0) FROM source_versions"
    ).fetchone()
    if usage[0] >= MAX_VERSIONS or usage[1] + len(body) > MAX_ARCHIVE_BYTES:
        raise AtlasError("CACHE_FULL", "Private source version archive quota reached.")
    conn.execute(
        "INSERT INTO source_versions VALUES (?,?,?,?,?,?)", (url, sha, body, now, now, retrieved_at)
    )


def json_changes(before: Any, after: Any, pointer: str = "") -> list[dict[str, Any]]:
    """Bounded exact JSON cell changes; array reordering remains visible."""
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                result.append(
                    {
                        "json_pointer": path,
                        "before_present": key in before,
                        "after_present": key in after,
                        "before": before.get(key),
                        "after": after.get(key),
                    }
                )
            else:
                result.extend(json_changes(before[key], after[key], path))
            if len(result) > 500:
                return result[:501]
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        result = []
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            result.extend(json_changes(left, right, pointer + f"/{index}"))
            if len(result) > 500:
                return result[:501]
        return result
    return [{"json_pointer": pointer, "before": before, "after": after}]


def source_history(query: SourceHistoryQuery | dict[str, Any], directory: Path) -> dict[str, Any]:
    from atlas_terminal.equity_sources import require_yahoo_enabled, yahoo_identity
    from atlas_terminal.public_sources import PublicTransport

    try:
        q = (
            query
            if isinstance(query, SourceHistoryQuery)
            else SourceHistoryQuery.model_validate(query)
        )
    except (ValueError, TypeError, ValidationError):
        raise AtlasError(
            "INVALID_INPUT", "Use a valid source-history action and arguments."
        ) from None
    PublicTransport.validate_url(q.url)
    if yahoo_identity(q.url) is not None:
        require_yahoo_enabled()
    store = Database(directory / "public-cache.sqlite3", SCHEMA)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM source_versions WHERE url=? ORDER BY first_seen,sha256", (q.url,)
        ).fetchall()
        observations = conn.execute(
            "SELECT * FROM source_observations WHERE url=? ORDER BY observed_at", (q.url,)
        ).fetchall()
    by_hash = {row["sha256"]: row for row in rows}
    for row in rows:
        if digest(row["body"]) != row["sha256"]:
            raise AtlasError("SOURCE_UNAVAILABLE", "Archived source failed integrity verification.")
    result: dict[str, Any] = {
        "schema_version": "0.1.0",
        "action": q.action,
        "url": q.url,
        "point_in_time": False,
        "source_text_is_untrusted": True,
        "availability_basis": "first_observed_in_this_local_archive",
        "limitations": [
            "Local capture times do not establish publisher first availability.",
            "History begins with retained captures; no past vintages are reconstructed.",
            "Byte/JSON differences may include metadata changes, not only value revisions.",
        ],
    }

    def selected(sha: str | None) -> Any:
        if sha not in by_hash:
            raise AtlasError("SOURCE_UNAVAILABLE", "Requested source version was not retained.")
        return by_hash[sha]

    def metadata(row: Any) -> dict[str, Any]:
        return {key: row[key] for key in ("sha256", "first_seen", "last_seen", "retrieved_at")}

    if q.action == "list":
        result.update(
            versions=[metadata(row) for row in rows[-q.limit :]], truncated=len(rows) > q.limit
        )
    elif q.action == "diff":
        before, after = selected(q.before_sha256), selected(q.sha256)
        result.update(
            before=metadata(before),
            after=metadata(after),
            bytes_changed=before["sha256"] != after["sha256"],
        )
        try:
            # Source numeric tokens remain strings, avoiding binary float conversion.
            left = json.loads(before["body"], parse_float=Decimal)
            right = json.loads(after["body"], parse_float=Decimal)
            changes = json_changes(left, right)

            def decimal_strings(value: Any) -> Any:
                if isinstance(value, Decimal):
                    return str(value)
                if isinstance(value, dict):
                    return {key: decimal_strings(cell) for key, cell in value.items()}
                if isinstance(value, list):
                    return [decimal_strings(cell) for cell in value]
                return value

            changes = decimal_strings(changes)
            result.update(json_changes=changes[:500], truncated=len(changes) > 500)
        except (ValueError, UnicodeError):
            result.update(json_changes=None, diff_basis="raw_byte_hash_only")
    else:
        if q.action == "as_of":
            eligible = [item for item in observations if item["observed_at"] <= (q.as_of or "")]
            if not eligible:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "No local version was observed by this time."
                )
            row = selected(eligible[-1]["sha256"])
            result["observed_at"] = eligible[-1]["observed_at"]
        else:
            row = selected(q.sha256)
        result.update(version=metadata(row), body_utf8=None)
        try:
            result["body_utf8"] = row["body"].decode("utf-8")
        except UnicodeError:
            result["body_encoding"] = "binary_not_rendered"
        if len(canonical(result)) > 4 * 1024 * 1024:
            raise AtlasError("INVALID_INPUT", "Archived body exceeds the 4 MiB response limit.")
    if len(canonical(result)) > 4 * 1024 * 1024:
        raise AtlasError("INVALID_INPUT", "Source history exceeds the 4 MiB response limit.")
    return result
