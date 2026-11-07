"""Offline, source-verified technical indicators for complete stock-history exports."""

import csv
import json
import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

from atlas_terminal.equity_history import (
    _manifest,
    _write_new,
    iter_price_history,
    price_history_queries,
)
from atlas_terminal.equity_sources import yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.research_equities import yahoo_equities
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_price_analytics import (
    CALCULATION_PRECISION,
    DEFAULT_TECHNICAL_METRICS,
    MAX_OFFLINE_PRICE_OBSERVATIONS,
    PRICE_ANALYTICS_LIMITATIONS,
    indicator_records,
    price_analytics_catalog,
)
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.util import canonical, digest, stamp

MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_CALCULATION_BYTES = 256 * 1024 * 1024


def _file_digest(path: Path) -> str:
    checksum = sha256()
    with path.open("rb") as stream:
        while body := stream.read(1024 * 1024):
            checksum.update(body)
    return checksum.hexdigest()


class _SavedSource(PublicFixtures):
    def __init__(self, snapshot: Snapshot, expected_url: str) -> None:
        self.snapshot, self.expected_url = snapshot, expected_url

    def get(self, url: str, ttl: int) -> Snapshot:
        if url != self.expected_url:
            raise ValueError("Unexpected replay URL")
        return self.snapshot


def _read_regular(path: Path, root: Path, limit: int) -> bytes:
    if not path.resolve().is_relative_to(root):
        raise ValueError("Input path escapes history directory")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("History file exceeds bounds or is not regular")
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("History file exceeds bounds")
    return body


def _load_history(directory: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Reparse original bytes and match each complete saved result before calculation."""
    try:
        root = directory.resolve()
        body = _read_regular(root / "manifest.json", root, 256 * 1024)
        manifest = json.loads(body)
        planned, windows = manifest["planned_queries"], manifest["windows"]
        if (
            manifest["kind"] != "equity_price_history_export"
            or manifest["status"] != "ok"
            or manifest["download_complete"] is not True
            or not 1 <= len(planned) <= 30
            or len(windows) != len(planned)
            or planned
            != price_history_queries(
                manifest["identifier"], planned[0]["start_date"], planned[-1]["end_date"]
            )
        ):
            raise ValueError("A complete history plan is required")
        used = len(body)
        csv_body = _read_regular(root / "prices.csv", root, 16 * 1024 * 1024)
        used += len(csv_body)
        if digest(csv_body) != manifest["csv_sha256"]:
            raise ValueError("History CSV hash differs")
        annual, sources = {}, []
        for query, window in zip(planned, windows, strict=True):
            year = query["start_date"][:4]
            if (
                window["query"] != query
                or window["raw_file"] != f"raw/{year}.json"
                or window["result_file"] != f"results/{year}.json"
            ):
                raise ValueError("Annual source paths differ from the plan")
            raw = _read_regular(root / window["raw_file"], root, 4 * 1024 * 1024)
            normalized = _read_regular(root / window["result_file"], root, 4 * 1024 * 1024)
            used += len(raw) + len(normalized)
            if (
                used > MAX_HISTORY_BYTES
                or digest(raw) != window["raw_sha256"]
                or digest(normalized) != window["result_sha256"]
            ):
                raise ValueError("History bytes exceed bounds or fail integrity checks")
            result = json.loads(normalized)
            ResearchResult.model_validate(result)
            if (
                result["dataset"] != "equity_prices"
                or result["query"] != query
                or result["is_synthetic"] != manifest["is_synthetic"]
                or len(result["sources"]) != 1
            ):
                raise ValueError("Saved result does not match the annual query")
            source = result["sources"][0]
            if source["raw_sha256"] != digest(raw):
                raise ValueError("Saved result names different original bytes")
            snapshot = Snapshot(raw, source["raw_source_url"], source["retrieved_at"])
            replayed = yahoo_equities(
                _SavedSource(
                    snapshot, yahoo_url(query["identifier"], query["start_date"], query["end_date"])
                ),
                ResearchQuery.model_validate(query),
            )
            if any(result[key] != replayed[key] for key in ("records", "instrument", "coverage")):
                raise ValueError("Saved prices do not match their original source bytes")
            if window["records"] != len(result["records"]):
                raise ValueError("Annual record count differs")
            annual[query["start_date"]] = result
            sources.append(
                {"file": f"raw/{year}.json", "body": raw, "source": source, "query": query}
            )
        prices = [
            row
            for result in iter_price_history(planned, lambda q: annual[q["start_date"]])
            for row in sorted(result["records"], key=lambda row: row["date"])
        ]
        if (
            len(prices) != manifest["records"]
            or len(prices) > MAX_OFFLINE_PRICE_OBSERVATIONS
            or prices[0]["date"] != manifest["first_observation_date"]
            or prices[-1]["date"] != manifest["last_observation_date"]
        ):
            raise ValueError("History coverage differs")
        return prices, sources, manifest["is_synthetic"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, AtlasError):
        raise AtlasError(
            "INVALID_INPUT", "Read a completed, intact stock-history export within the size limits."
        ) from None


def export_technicals(
    history: Path,
    output: Path,
    *,
    metrics: list[str] | None = None,
    price_field: str = "adjusted_close",
) -> dict[str, Any]:
    """Calculate one continuous history offline; preserve all dates and warm-up rows."""
    prices, sources, synthetic = _load_history(history)
    selected = list(DEFAULT_TECHNICAL_METRICS if metrics is None else metrics)
    inputs, records = indicator_records(
        prices, selected, price_field=price_field, max_observations=MAX_OFFLINE_PRICE_OBSERVATIONS
    )
    output = output.absolute()
    manifest: dict[str, Any] = {
        "schema_version": "0.1.0",
        "kind": "technical_indicators_export",
        "status": "incomplete",
        "download_complete": False,
        "created_at": stamp(),
        "identifier": prices[0]["identifier"],
        "price_field": price_field,
        "price_basis": inputs[0]["price_basis"],
        "price_observations": len(prices),
        "first_observation_date": prices[0]["date"],
        "last_observation_date": prices[-1]["date"],
        "metrics": {
            name: {**price_analytics_catalog()[name], "price_input": price_field}
            for name in selected
        },
        "decimal_precision": CALCULATION_PRECISION,
        "rounding": "half_even_18_decimal_places",
        "is_synthetic": synthetic,
        "network_used": False,
        "hidden_warmup_requests": False,
        "point_in_time": False,
        "exchange_calendar_verified": False,
        "cross_window_adjustment_verified": False,
        "year_boundary_reset": False,
        "price_inputs_file": "price_inputs.json",
        "calculation_records_file": "calculations.jsonl",
        "input_range": "inclusive zero-based price_inputs indices; see price_inputs.json",
        "unavailable_records": sum(r["value"] is None for r in records),
        "unavailable_by_metric": {
            name: sum(r["metric"] == name and r["value"] is None for r in records)
            for name in selected
        },
        "source_files": [{k: v for k, v in source.items() if k != "body"} for source in sources],
        "limitations": list(PRICE_ANALYTICS_LIMITATIONS),
    }
    try:
        output.mkdir(parents=True, mode=0o700)
    except OSError:
        raise AtlasError(
            "INVALID_INPUT", "Choose a new writable technical-indicator directory."
        ) from None
    try:
        _manifest(output / "manifest.json", manifest)
        (output / "raw").mkdir(mode=0o700)
        for source in sources:
            _write_new(output / source["file"], source["body"])
        _write_new(output / "price_inputs.json", canonical({"price_inputs": inputs}))
        descriptor = os.open(
            output / "calculations.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            written = 0
            for row in records:
                body = canonical(row) + b"\n"
                written += len(body)
                if written > MAX_CALCULATION_BYTES:
                    raise AtlasError(
                        "RESPONSE_TOO_LARGE", "Technical calculations exceed the export size bound."
                    )
                stream.write(body)
        descriptor = os.open(output / "technicals.csv", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "identifier",
                    "date",
                    "currency",
                    "price_field",
                    "price",
                    "price_basis",
                    *selected,
                    "unavailable_metrics",
                ],
            )
            writer.writeheader()
            for index, row in enumerate(prices):
                calculated = records[index * len(selected) : (index + 1) * len(selected)]
                writer.writerow(
                    {
                        "identifier": row["identifier"],
                        "date": row["date"],
                        "currency": row["currency"],
                        "price_field": price_field,
                        "price": row[price_field],
                        "price_basis": inputs[index]["price_basis"],
                        **{r["metric"]: r["value"] for r in calculated},
                        "unavailable_metrics": "|".join(
                            r["metric"] for r in calculated if r["value"] is None
                        ),
                    }
                )
        manifest.update(
            status="ok",
            download_complete=True,
            completed_at=stamp(),
            calculation_records=len(records),
            file_hashes={
                name: _file_digest(output / name)
                for name in ("technicals.csv", "price_inputs.json", "calculations.jsonl")
            },
        )
        _manifest(output / "manifest.json", manifest)
    except (OSError, AtlasError) as error:
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
            "HISTORY_EXPORT_UNAVAILABLE", "Technical-indicator export could not be completed."
        ) from None
    return {
        "status": "ok",
        "output": str(output),
        "csv": str(output / "technicals.csv"),
        "identifier": manifest["identifier"],
        "price_field": price_field,
        "price_observations": len(prices),
        "metrics": selected,
        "calculation_records": len(records),
        "unavailable_records": manifest["unavailable_records"],
        "network_used": False,
        "is_synthetic": synthetic,
        "download_complete": True,
        "published": False,
    }
