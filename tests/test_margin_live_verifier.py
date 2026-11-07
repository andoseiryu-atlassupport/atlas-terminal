"""The private live audit must detect corrupted values independently of the adapter."""

import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.margin_sources import margin_report_url
from atlas_terminal.public_sources import PublicFixtures


@pytest.fixture
def audit(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    verify = runpy.run_path(str(scripts / "verify_margin_live.py"))["verify"]
    with Atlas("fixture") as atlas:
        result = atlas.get_margin_status("7203.T")
    result["is_synthetic"] = False  # Only the fictional audit regression, never a live PASS.
    source = PublicFixtures().get(margin_report_url("2026-11-05"), 0)
    capture = SimpleNamespace(snapshots={source.url: source})
    assert verify(result, capture) == 28
    return verify, result, capture


@pytest.mark.parametrize(
    "field", ["ratio", "unit", "value", "formula", "operand", "hash", "column"]
)
def test_original_cell_and_fraction_audit_rejects_corruption(audit, field):
    verify, result, capture = audit
    result = deepcopy(result)
    row = result["records"][0]
    if field == "ratio":
        row["margin_ratio"]["value"] = "4.8"  # Incorrect money-based ratio.
    elif field == "unit":
        row["shares"]["unit"] = "100 shares"
    elif field == "value":
        row["shares"]["buy_balance"] = "160000"
    elif field == "formula":
        row["margin_ratio"]["formula"] = "buy_amount_jpy / sell_amount_jpy"
    elif field == "operand":
        row["margin_ratio"]["inputs"]["buy_balance"]["value"] = "12000"
    elif field == "hash":
        row["shares"]["field_sources"]["buy_balance"]["raw_sha256"] = "0" * 64
    else:
        row["shares"]["field_sources"]["buy_balance"]["text_column_start"] += 1
    with pytest.raises(AssertionError):
        verify(result, capture)
