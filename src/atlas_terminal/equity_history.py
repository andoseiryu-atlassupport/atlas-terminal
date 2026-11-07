"""Bounded multi-year stock reads and private exports through shared research."""

import csv
import os
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import ValidationError

from atlas_terminal.equity_sources import yahoo_profile
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import canonical, digest, stamp

MAX_HISTORY_YEARS = 30
CSV_FIELDS = (
    "identifier",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "currency",
    "denomination_currency",
    "quote_unit_scale",
    "unit",
    "volume_unit",
    "price_basis",
    "adjusted_close_basis",
    "date_basis",
    "observation_at",
    "status",
    "ohlc_consistent",
    "quality_flags",
    "raw_sha256",
    "close_json_pointer",
)
IDENTITY_FIELDS = (
    "symbol",
    "source_symbol",
    "currency",
    "denomination_currency",
    "quote_unit_scale",
    "asset_class",
    "price_unit",
    "volume_unit",
    "exchange",
    "exchange_timezone",
    "source_instrument_type",
)


def price_history_queries(identifier: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Plan inclusive, nonoverlapping calendar years without reading any source."""
    try:
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if (
            first.isoformat() != start_date
            or last.isoformat() != end_date
            or first > last
            or not 1900 <= first.year <= last.year <= 2100
            or last.year - first.year >= MAX_HISTORY_YEARS
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise AtlasError(
            "INVALID_INPUT", "Use ordered ISO dates within at most 30 calendar years (1900–2100)."
        ) from None
    queries = []
    for year in range(first.year, last.year + 1):
        try:
            q = ResearchQuery.model_validate(
                {
                    "dataset": "equity_prices",
                    "identifier": identifier,
                    "start_date": max(first, date(year, 1, 1)).isoformat(),
                    "end_date": min(last, date(year, 12, 31)).isoformat(),
                    "limit": 500,
                    "requirements": {"allow_truncated": False},
                }
            )
        except ValidationError:
            raise AtlasError(
                "INVALID_INPUT", "Use a supported stock/ETF identifier and ISO dates."
            ) from None
        if yahoo_profile(q.identifier or "")["asset_class"] != "security":
            raise AtlasError("INVALID_INPUT", "Price history requires a registered stock or ETF.")
        queries.append(q.model_dump(mode="json", exclude_unset=True))
    return queries


def iter_price_history(
    queries: list[dict[str, Any]], read: Callable[[dict[str, Any]], dict[str, Any]]
) -> Iterator[dict[str, Any]]:
    """Keep each original result bounded; reject truncation and cross-window changes."""
    identity = None
    for query in queries:
        result = read(query)
        current = tuple(result["instrument"].get(key) for key in IDENTITY_FIELDS)
        rows = result["records"]
        if (
            result["coverage"]["truncated"]
            or result.get("response", {}).get("next_cursor")
            or not rows
            or len(rows) > 366
            or len({row["date"] for row in rows}) != len(rows)
            or any(
                row["identifier"] != query["identifier"]
                or row["frequency"] != "daily"
                or not query["start_date"] <= row["date"] <= query["end_date"]
                for row in rows
            )
        ):
            raise AtlasError("INCOMPLETE_COVERAGE", "A history window is incomplete or misaligned.")
        if identity is not None and identity != current:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Stock identity or quote units changed between windows."
            )
        identity = current
        yield result


def _write_new(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)


def _manifest(path: Path, value: dict[str, Any]) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(value))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_price_history(
    queries: list[dict[str, Any]],
    output: Path,
    read: Callable[[dict[str, Any]], dict[str, Any]],
    read_raw: Callable[[dict[str, Any]], bytes],
) -> dict[str, Any]:
    """Create a new private CSV, original annual results and hash-matched raw bytes."""
    output = output.absolute()
    try:
        output.mkdir(parents=True, mode=0o700)
        (output / "raw").mkdir(mode=0o700)
        (output / "results").mkdir(mode=0o700)
    except OSError:
        raise AtlasError(
            "INVALID_INPUT", "Choose a new writable history output directory."
        ) from None
    manifest: dict[str, Any] = {
        "schema_version": "0.1.0",
        "kind": "equity_price_history_export",
        "identifier": queries[0]["identifier"],
        "status": "incomplete",
        "download_complete": False,
        "planned_queries": queries,
        "windows": [],
        "records": 0,
        "partial": False,
        "first_observation_date": None,
        "last_observation_date": None,
        "exchange_calendar_verified": False,
        "cross_window_adjustment_verified": False,
        "point_in_time": False,
        "created_at": stamp(),
        "limitations": [
            "Completed downloads do not certify exchange-session completeness or listing history.",
            "Historical source quotes are current revised and split-adjusted, not PIT vintages "
            "or independently verified unadjusted trade prices.",
            "Nulls remain blank CSV cells and explicit JSON nulls; gaps are never filled. "
            "No cross-window price or corporate-action adjustment is applied.",
            "Source adjusted prices can differ between query windows or retrievals; "
            "cross-window adjustment consistency is not independently verified.",
        ],
    }
    try:
        _manifest(output / "manifest.json", manifest)
        descriptor = os.open(output / "prices.csv", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for result in iter_price_history(queries, read):
                index = len(manifest["windows"])
                query = queries[index]
                raw = read_raw(result)
                sources = result["sources"]
                if len(sources) != 1 or digest(raw) != sources[0]["raw_sha256"]:
                    raise AtlasError(
                        "SOURCE_UNAVAILABLE", "Original history bytes changed before export."
                    )
                year = query["start_date"][:4]
                raw_file, result_file = f"raw/{year}.json", f"results/{year}.json"
                _write_new(output / raw_file, raw)
                _write_new(output / result_file, canonical(result))
                rows = sorted(result["records"], key=lambda row: row["date"])
                for row in rows:
                    writer.writerow(
                        {
                            **{key: row[key] for key in CSV_FIELDS[:-2]},
                            "quality_flags": "|".join(row["quality_flags"]),
                            "raw_sha256": sources[0]["raw_sha256"],
                            "close_json_pointer": row["field_sources"]["close"]["json_pointer"],
                        }
                    )
                stream.flush()
                manifest["records"] += len(rows)
                manifest["partial"] |= result["coverage"]["partial"]
                manifest["first_observation_date"] = (
                    manifest["first_observation_date"] or rows[0]["date"]
                )
                manifest["last_observation_date"] = rows[-1]["date"]
                manifest["is_synthetic"] = result["is_synthetic"]
                manifest["windows"].append(
                    {
                        "query": query,
                        "raw_file": raw_file,
                        "result_file": result_file,
                        "raw_sha256": sources[0]["raw_sha256"],
                        "result_sha256": digest(canonical(result)),
                        "records": len(rows),
                        "coverage": result["coverage"],
                        "retrieved_at": result["retrieved_at"],
                    }
                )
                _manifest(output / "manifest.json", manifest)
        manifest.update(
            status="ok",
            download_complete=True,
            completed_at=stamp(),
            csv_sha256=digest((output / "prices.csv").read_bytes()),
        )
        _manifest(output / "manifest.json", manifest)
    except (AtlasError, OSError) as error:
        manifest.update(
            status="incomplete",
            download_complete=False,
            error_code=error.code
            if isinstance(error, AtlasError)
            else "HISTORY_EXPORT_UNAVAILABLE",
        )
        try:
            _manifest(output / "manifest.json", manifest)
        except OSError:
            pass
        if isinstance(error, AtlasError):
            raise
        raise AtlasError(
            "HISTORY_EXPORT_UNAVAILABLE", "History export could not be completed."
        ) from None
    return {
        "status": "ok",
        "output": str(output),
        "csv": str(output / "prices.csv"),
        "manifest": str(output / "manifest.json"),
        "windows": len(manifest["windows"]),
        **{
            key: manifest[key]
            for key in (
                "records",
                "partial",
                "is_synthetic",
                "first_observation_date",
                "last_observation_date",
            )
        },
        "download_complete": True,
        "exchange_calendar_verified": False,
        "cross_window_adjustment_verified": False,
        "point_in_time": False,
        "published": False,
    }
