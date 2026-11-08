"""Private, resumable daily-list index. Scoring never scans an unbounded date history."""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from atlas_terminal.equity_sources import TOKYO
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import edinet_list_url
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_japan_companies import disclosure_rows
from atlas_terminal.source_deadline import remaining_timeout, source_deadline
from atlas_terminal.util import Database, canonical, digest

MAX_HISTORY_DAYS = 12 * 366
MAX_SYNC_DAYS = 31
MAX_ISSUER_DOCUMENTS = 256
SYNC_SECONDS = 90
SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
 day TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 day TEXT NOT NULL REFERENCES days(day) ON DELETE CASCADE,
 document_id TEXT NOT NULL, edinet_code TEXT, security_code TEXT,
 body BLOB NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(day, document_id)
);
CREATE INDEX IF NOT EXISTS issuer_code ON documents(edinet_code);
CREATE INDEX IF NOT EXISTS security_code ON documents(security_code);
"""


def japan_identifier(value: str) -> tuple[str, str]:
    """Four-character listed codes map to the EDINET fifth-character zero convention."""
    if re.fullmatch(r"E[0-9]{5}", value):
        return "edinet_code", value
    if re.fullmatch(r"[0-9][0-9A-Z]{3}(?:\.T)?", value):
        return "security_code", value.removesuffix(".T") + "0"
    if re.fullmatch(r"[0-9][0-9A-Z]{3}0", value):
        return "security_code", value
    raise AtlasError(
        "INVALID_INPUT",
        "Use a four-character Japanese security code, its .T symbol, a five-character "
        "EDINET security code ending in 0, or an E-prefixed EDINET issuer code. "
        "Class-share codes are not inferred.",
    )


def today_japan() -> date:
    return datetime.now(TOKYO).date()


def date_window(start: str, end: str, *, today: date | None = None) -> list[str]:
    today = today or today_japan()
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if (
            first.isoformat() != start
            or last.isoformat() != end
            or not 0 <= (last - first).days < MAX_SYNC_DAYS
            or last > today
            or first < today - timedelta(days=MAX_HISTORY_DAYS)
        ):
            raise ValueError
    except ValueError:
        raise AtlasError(
            "INVALID_INPUT", "Index at most 31 calendar days within the latest twelve years."
        ) from None
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


class EdinetHistory:
    def __init__(self, directory: Path, *, synthetic: bool = False) -> None:
        name = "edinet-history-fixture.sqlite3" if synthetic else "edinet-history.sqlite3"
        self.db = Database((directory / name).resolve(), SCHEMA)

    def ingest(self, day: str, snapshot: Snapshot) -> None:
        rows = disclosure_rows(snapshot)
        if snapshot.url.startswith("https://api.edinet-fsa.go.jp/"):
            if snapshot.url != edinet_list_url(day):
                raise AtlasError("SOURCE_UNAVAILABLE", "EDINET list date differs from its URL.")
        selected = [row for row in rows if row["document_type"] in {"120", "130"}]
        for row in selected:
            if not row["filed_at"] or row["filed_at"][:10] != day:
                raise AtlasError("SOURCE_UNAVAILABLE", "EDINET submission differs from list date.")
            row["filing_date"] = day
        with self.db.connect(write=True) as conn:
            old = conn.execute("SELECT * FROM days WHERE day=?", (day,)).fetchone()
            if old and old["retrieved_at"] > snapshot.retrieved_at:
                return
            conn.execute("DELETE FROM days WHERE day=?", (day,))
            conn.execute(
                "INSERT INTO days VALUES (?,?,?)", (day, snapshot.retrieved_at, snapshot.sha256)
            )
            for row in selected:
                body = canonical(row)
                conn.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    (
                        day,
                        row["document_id"],
                        row["edinet_code"],
                        row["security_code"],
                        body,
                        digest(body),
                    ),
                )
            # Retention is separate from the raw-source cache/history. Never reset that cache.
            floor = (today_japan() - timedelta(days=MAX_HISTORY_DAYS)).isoformat()
            conn.execute("DELETE FROM days WHERE day < ?", (floor,))

    def rows(self, identifier: str) -> tuple[list[dict[str, Any]], bool]:
        field, value = japan_identifier(identifier)
        today = today_japan()
        first = today - timedelta(days=MAX_HISTORY_DAYS)
        with self.db.connect() as conn:
            result = conn.execute(
                f"SELECT * FROM documents WHERE {field}=? AND day BETWEEN ? AND ? "
                "ORDER BY day DESC, document_id LIMIT ?",
                (value, first.isoformat(), today.isoformat(), MAX_ISSUER_DOCUMENTS + 1),
            ).fetchall()
        rows = []
        for record in result[:MAX_ISSUER_DOCUMENTS]:
            if digest(record["body"]) != record["sha256"]:
                raise AtlasError(
                    "SOURCE_UNAVAILABLE", "EDINET history failed integrity validation."
                )
            rows.append(json.loads(record["body"]))
        return rows, len(result) > MAX_ISSUER_DOCUMENTS

    def coverage(self, *, today: date | None = None) -> dict[str, Any]:
        today = today or today_japan()
        first = today - timedelta(days=MAX_HISTORY_DAYS)
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM days WHERE day BETWEEN ? AND ? ORDER BY day",
                (first.isoformat(), today.isoformat()),
            ).fetchall()
        days = {row["day"] for row in rows}
        gaps: list[dict[str, str]] = []
        for offset in range(MAX_HISTORY_DAYS + 1):
            day = (first + timedelta(days=offset)).isoformat()
            if day not in days:
                if (
                    gaps
                    and (date.fromisoformat(day) - date.fromisoformat(gaps[-1]["end"])).days == 1
                ):
                    gaps[-1]["end"] = day
                else:
                    gaps.append({"start": day, "end": day})
        return {
            "search_start": first.isoformat(),
            "search_end": today.isoformat(),
            "indexed_days": len(days),
            "unindexed_days": MAX_HISTORY_DAYS + 1 - len(days),
            "missing_date_ranges": gaps[:32],
            "missing_ranges_truncated": len(gaps) > 32,
            "oldest_capture_at": min((row["retrieved_at"] for row in rows), default=None),
            "latest_capture_at": max((row["retrieved_at"] for row in rows), default=None),
            "selection_scope": "indexed_submission_dates_only",
            "latest_amendment_guaranteed": False,
            "point_in_time": False,
        }


def sync_history(
    transport: Transport,
    history: EdinetHistory,
    start: str,
    end: str,
) -> dict[str, Any]:
    days = date_window(start, end)
    completed: list[str] = []
    failure = None
    with source_deadline(SYNC_SECONDS):
        for day in days:
            try:
                remaining_timeout()
                history.ingest(day, transport.get(edinet_list_url(day), 21600))
                completed.append(day)
            except AtlasError as error:
                if error.code == "CONFIG_REQUIRED":
                    raise
                failure = {"date": day, "code": error.code, "reason": error.details.get("reason")}
                break
    return {
        "requested_days": len(days),
        "indexed_dates": completed,
        "next_date": days[len(completed)] if len(completed) < len(days) else None,
        "partial": len(completed) < len(days),
        "failure": failure,
        "limits": {"max_days": MAX_SYNC_DAYS, "elapsed_seconds": SYNC_SECONDS},
        "history": history.coverage(),
    }
