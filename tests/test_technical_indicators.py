"""Offline technical exports are audited against fictional saved source histories."""

import csv
import json
import os
import subprocess
import sys

import pytest
from test_equity_history import FictionalHistory

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.research_price_analytics import DEFAULT_TECHNICAL_METRICS
from atlas_terminal.technical_indicators import export_technicals
from atlas_terminal.util import canonical, digest


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)


@pytest.fixture
def history(tmp_path):
    target = tmp_path / "prices"
    with Atlas("fixture") as atlas:
        atlas._research.public = FictionalHistory()
        atlas.export_price_history(
            "7203.T", start_date="2023-01-01", end_date="2025-12-31", output=target
        )
    return target


def load_rows(path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))


def test_all_history_dates_and_warmups_are_saved_without_resetting_at_a_new_year(history, tmp_path):
    target = tmp_path / "technicals"
    result = export_technicals(history, target, price_field="close")
    assert result["price_observations"] == 783 and result["calculation_records"] == 7830
    assert result["unavailable_records"] == 264 and not result["network_used"]
    assert result["metrics"] == DEFAULT_TECHNICAL_METRICS
    rows = load_rows(target / "technicals.csv")
    assert len(rows) == 783 and len({r["date"] for r in rows}) == 783
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    assert rows[73]["sma_75"] == "" and rows[74]["sma_75"] == "101.000000000000000001"
    assert rows[14]["rsi_14"] == "50.000000000000000000"
    assert next(r for r in rows if r["date"] == "2024-01-01")["unavailable_metrics"] == ""
    calculated = [
        json.loads(line) for line in (target / "calculations.jsonl").read_text().splitlines()
    ]
    assert len(calculated) == 7830
    boundary = next(r for r in calculated if r["date"] == "2024-01-01" and r["metric"] == "rsi_14")
    assert boundary["input_range"]["first"] == 0 and boundary["input_range"]["last"] > 250
    inputs = json.loads((target / "price_inputs.json").read_bytes())["price_inputs"]
    assert len(inputs) == 783 and inputs[0]["source"]["json_pointer"].startswith(
        "/chart/result/0/indicators/quote/0/close/"
    )
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert manifest["download_complete"] and not manifest["year_boundary_reset"]
    assert not manifest["cross_window_adjustment_verified"]
    for name, expected in manifest["file_hashes"].items():
        assert digest((target / name).read_bytes()) == expected
    for source in manifest["source_files"]:
        assert digest((target / source["file"]).read_bytes()) == source["source"]["raw_sha256"]
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.rglob("*") if p.is_file())


def test_missing_selected_prices_restart_wilder_rsi_with_the_same_new_input_range(
    history, tmp_path
):
    target = tmp_path / "adjusted"
    export_technicals(history, target, metrics=["rsi_14", "sma_25"])
    inputs = json.loads((target / "price_inputs.json").read_bytes())["price_inputs"]
    missing = next(i for i, r in enumerate(inputs) if r["date"] == "2024-02-29")
    rows = load_rows(target / "technicals.csv")
    assert inputs[missing]["value"] is None and rows[missing]["rsi_14"] == ""
    assert (
        rows[missing + 14]["rsi_14"] == ""
        and rows[missing + 15]["rsi_14"] == "50.000000000000000000"
    )
    assert rows[missing + 24]["sma_25"] == "" and rows[missing + 25]["sma_25"] != ""
    records = [
        json.loads(line) for line in (target / "calculations.jsonl").read_text().splitlines()
    ]
    recovered = next(
        r for r in records if r["date"] == rows[missing + 15]["date"] and r["metric"] == "rsi_14"
    )
    assert recovered["input_range"]["first"] == missing + 1


@pytest.mark.parametrize(
    "fault",
    ["incomplete", "raw_hash", "result_hash", "csv_hash", "changed_price", "path", "identity"],
)
def test_corrupt_or_incomplete_source_exports_fail_before_output(history, tmp_path, fault):
    manifest_path = history / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    window = manifest["windows"][0]
    if fault == "incomplete":
        manifest["download_complete"] = False
    elif fault == "raw_hash":
        (history / window["raw_file"]).write_bytes(b"changed bytes")
    elif fault == "result_hash":
        (history / window["result_file"]).write_bytes(b"changed bytes")
    elif fault == "csv_hash":
        (history / "prices.csv").write_bytes(b"changed CSV")
    elif fault == "path":
        window["raw_file"] = "../../outside.json"
    elif fault == "identity":
        manifest["identifier"] = "AAPL"
    else:
        result_path = history / window["result_file"]
        result = json.loads(result_path.read_bytes())
        result["records"][0]["close"] = "777"
        result_path.write_bytes(canonical(result))
        window["result_sha256"] = digest(result_path.read_bytes())
    manifest_path.write_bytes(canonical(manifest))
    target = tmp_path / "rejected"
    with pytest.raises(AtlasError) as error:
        export_technicals(history, target)
    assert error.value.code == "INVALID_INPUT" and not target.exists()


def test_export_limit_failure_keeps_an_explicitly_incomplete_manifest(
    history, tmp_path, monkeypatch
):
    import atlas_terminal.technical_indicators as module

    monkeypatch.setattr(module, "MAX_CALCULATION_BYTES", 1)
    target = tmp_path / "limited"
    with pytest.raises(AtlasError) as error:
        export_technicals(history, target)
    manifest = json.loads((target / "manifest.json").read_bytes())
    assert error.value.code == "RESPONSE_TOO_LARGE"
    assert manifest["status"] == "incomplete" and not manifest["download_complete"]


def test_cli_and_sdk_are_offline_and_cannot_overwrite_existing_output(history, tmp_path):
    target = tmp_path / "sdk"
    with Atlas("fixture") as atlas:
        result = atlas.export_technicals(
            history, output=target, metrics=["sma_25", "rsi_14"], price_field="close"
        )
    assert not result["network_used"]
    original = (target / "technicals.csv").read_bytes()
    with pytest.raises(AtlasError):
        export_technicals(history, target)
    assert (target / "technicals.csv").read_bytes() == original
    cli_target = tmp_path / "cli"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "technicals",
            "--input",
            str(history),
            "--output",
            str(cli_target),
            "--metrics",
            "sma_25,rsi_14",
            "--price-field",
            "close",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(process.stdout)["calculation_records"] == 1566
    assert (cli_target / "technicals.csv").read_bytes() == original
