import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from atlas_terminal.errors import AtlasError


def utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def state_dir() -> Path:
    return Path(os.environ.get("ATLAS_STATE_DIR", str(Path.home() / ".local/state/atlas-terminal")))


class Database:
    """Short connections, private files, WAL and serialized writes across processes."""

    def __init__(self, path: Path, schema: str) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            if os.name != "nt" and path.stat().st_mode & 0o077:
                raise AtlasError("LEDGER_UNAVAILABLE", "State file permissions must be private.")
            with self.connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(schema)
        except (OSError, sqlite3.Error):
            raise AtlasError("LEDGER_UNAVAILABLE", "Private state store is unavailable.") from None

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = None
        try:
            # mode=rw prevents silently recreating a lost ledger after startup.
            conn = sqlite3.connect(f"{self.path.as_uri()}?mode=rw", uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except sqlite3.IntegrityError:
            if conn:
                conn.rollback()
            raise
        except (sqlite3.Error, OSError):
            if conn:
                conn.rollback()
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "State transaction failed; operation stopped."
            ) from None
        finally:
            if conn:
                conn.close()
