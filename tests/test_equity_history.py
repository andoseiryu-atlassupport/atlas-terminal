"""Multi-year histories use fictional source bars; no actual quotes are bundled."""

import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from importlib.resources import files
from zoneinfo import ZoneInfo

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.equity_history import export_price_history, price_history_queries
from atlas_terminal.equity_sources import yahoo_identity
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.util import canonical, digest


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)


class FictionalHistory(PublicFixtures):
    def __init__(self, *, fail_year=None, change_units=False):
        self.calls = []
        self.fail_year, self.change_units = fail_year, change_units

    def get(self, url, ttl):
        symbol, start, end = yahoo_identity(url)
        self.calls.append((symbol, start, end))
        if start[:4] == self.fail_year:
            raise AtlasError("SOURCE_RATE_LIMITED", "Fictional stopped source.", retryable=True)
        body = (
            files("atlas_terminal")
            .joinpath("resources", "research", f"yahoo_chart_{symbol}.json")
            .read_bytes()
        )
        document = json.loads(body)
        chart = document["chart"]["result"][0]
        if self.change_units:
            chart["meta"]["currency"] = "GBp" if start[:4] == "2023" else "GBP"
        timezone = ZoneInfo(chart["meta"]["exchangeTimezoneName"])
        days = []
        day = date.fromisoformat(start)
        while day.isoformat() <= end:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        chart["timestamp"] = [
            int(datetime.combine(d, time(12), timezone).timestamp()) for d in days
        ]
        chart["events"] = {}
        quote = {
            "open": ["100.000000000000000001"] * len(days),
            "high": ["102"] * len(days),
            "low": ["99"] * len(days),
            "close": ["101.000000000000000001"] * len(days),
            "volume": [0] * len(days),
        }
        adjusted = [
            None if d.isoformat() == "2024-02-29" else "98.000000000000000001" for d in days
        ]
        chart["indicators"] = {"quote": [quote], "adjclose": [{"adjclose": adjusted}]}
        return Snapshot(canonical(document), url, "2026-11-07T00:00:00Z")


def test_plan_splits_leap_year_and_boundaries_without_overlapping_or_reading_sources():
    plan = price_history_queries("7203.T", "2023-12-29", "2025-01-03")
    assert [(q["start_date"], q["end_date"]) for q in plan] == [
        ("2023-12-29", "2023-12-31"),
        ("2024-01-01", "2024-12-31"),
        ("2025-01-01", "2025-01-03"),
    ]
    assert all(q["limit"] == 500 and q["requirements"] == {"allow_truncated": False} for q in plan)
    assert len(price_history_queries("AAPL", "1997-01-01", "2026-11-06")) == 30


@pytest.mark.parametrize(
    "first,last",
    [
        ("2025-01-01", "2024-12-31"),
        ("2023-02-29", "2024-01-01"),
        ("20240101", "2024-12-31"),
        ("1996-01-01", "2026-12-31"),
        ("1899-01-01", "1900-01-01"),
        ("2100-01-01", "2101-01-01"),
        (None, "2024-12-31"),
    ],
)
def test_invalid_history_bounds_fail_before_a_read(first, last):
    with pytest.raises(AtlasError) as error:
        price_history_queries("7203.T", first, last)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("symbol", ["7203", "^N225", "USDJPY=X", "GC=F", "https://example.invalid"])
def test_history_does_not_guess_a_security_or_accept_nonsecurity_symbols(symbol):
    with pytest.raises(AtlasError) as error:
        price_history_queries(symbol, "2023-01-01", "2025-12-31")
    assert error.value.code == "INVALID_INPUT"


def test_sdk_and_csv_retain_more_than_500_daily_rows_all_years_nulls_and_exact_source_cells(
    tmp_path,
):
    source = FictionalHistory()
    target = tmp_path / "history"
    with Atlas("fixture") as atlas:
        atlas._research.public = source
        result = atlas.export_price_history(
            "7203.T", start_date="2023-01-01", end_date="2025-12-31", output=target
        )
    expected_days = []
    day = date(2023, 1, 1)
    while day <= date(2025, 12, 31):
        if day.weekday() < 5:
            expected_days.append(day.isoformat())
        day += timedelta(days=1)
    rows = list(csv.DictReader((target / "prices.csv").open(encoding="utf-8-sig", newline="")))
    assert [r["date"] for r in rows] == expected_days and len(rows) == 783
    assert result["records"] == 783 and result["windows"] == 3 and result["partial"]
    assert result["download_complete"] and result["is_synthetic"]
    assert not result["exchange_calendar_verified"] and not result["point_in_time"]
    assert not result["cross_window_adjustment_verified"]
    assert all(r["volume"] == "0" and r["close"] == "101.000000000000000001" for r in rows)
    missing = next(r for r in rows if r["date"] == "2024-02-29")
    assert missing["adjusted_close"] == "" and missing["status"] == "unavailable"
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert manifest["status"] == "ok" and manifest["download_complete"]
    assert manifest["csv_sha256"] == digest((target / "prices.csv").read_bytes())
    for window in manifest["windows"]:
        raw = (target / window["raw_file"]).read_bytes()
        normalized = (target / window["result_file"]).read_bytes()
        assert digest(raw) == window["raw_sha256"] and digest(normalized) == window["result_sha256"]
        document = json.loads(raw)
        for row in json.loads(normalized)["records"]:
            for field, evidence in row["field_sources"].items():
                assert evidence["raw_sha256"] == digest(raw)
                cell = document
                for key in evidence["json_pointer"].split("/")[1:]:
                    cell = cell[int(key)] if isinstance(cell, list) else cell[key]
                if field != "date":
                    assert (Decimal(str(cell)) if cell is not None else None) == (
                        Decimal(row[field]) if row[field] is not None else None
                    )
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.rglob("*") if p.is_file())


def test_source_failure_keeps_an_incomplete_manifest_and_stops_before_later_years(tmp_path):
    source = FictionalHistory(fail_year="2024")
    target = tmp_path / "stopped"
    with Atlas("fixture") as atlas:
        atlas._research.public = source
        with pytest.raises(AtlasError) as error:
            atlas.export_price_history(
                "7203.T", start_date="2023-01-01", end_date="2025-12-31", output=target
            )
    assert error.value.code == "SOURCE_RATE_LIMITED"
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert not manifest["download_complete"] and manifest["status"] == "incomplete"
    assert manifest["error_code"] == "SOURCE_RATE_LIMITED" and len(manifest["windows"]) == 1
    assert not any(start.startswith("2025") for _, start, _ in source.calls)
    assert not (target / "results/2024.json").exists()


def test_existing_output_is_not_overwritten_and_does_not_trigger_a_source_read(tmp_path):
    source = FictionalHistory()
    (tmp_path / "keep.txt").write_text("untouched")
    with Atlas("fixture") as atlas:
        atlas._research.public = source
        with pytest.raises(AtlasError) as error:
            atlas.export_price_history(
                "7203.T", start_date="2023-01-01", end_date="2025-12-31", output=tmp_path
            )
    assert error.value.code == "INVALID_INPUT" and not source.calls
    assert (tmp_path / "keep.txt").read_text() == "untouched"


def test_different_original_bytes_cannot_complete_an_export(tmp_path):
    plan = price_history_queries("7203.T", "2026-10-19", "2026-10-26")
    target = tmp_path / "changed"
    with Atlas("fixture") as atlas, pytest.raises(AtlasError) as error:
        export_price_history(plan, target, atlas.get_research, lambda _: b"changed original bytes")
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert not json.loads((target / "manifest.json").read_bytes())["download_complete"]


def test_iterator_rejects_cross_year_pound_pence_changes():
    with Atlas("fixture") as atlas:
        atlas._research.public = FictionalHistory(change_units=True)
        parts = atlas.iter_price_history("VOD.L", start_date="2023-01-01", end_date="2024-12-31")
        assert next(parts)["instrument"]["currency"] == "GBp"
        with pytest.raises(AtlasError) as error:
            next(parts)
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_installed_style_cli_preview_and_private_export(tmp_path):
    base = [
        sys.executable,
        "-m",
        "atlas_terminal",
        "research",
        "price-history",
        "7203.T",
        "--start-date",
        "2026-10-19",
        "--end-date",
        "2026-10-26",
    ]
    preview = subprocess.run(
        [*base, "--preview", "--json"], capture_output=True, text=True, check=True
    )
    assert len(json.loads(preview.stdout)["queries"]) == 1
    assert not json.loads(preview.stdout)["network_used"]
    target = tmp_path / "cli"
    exported = subprocess.run(
        [*base, "--output", str(target), "--provider", "fixture", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(exported.stdout)["records"] == 6
    assert (target / "prices.csv").is_file()
