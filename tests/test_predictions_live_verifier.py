"""Fictional payloads independently exercise the opt-in original-field audit."""

import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.prediction_sources import event_url, search_url
from atlas_terminal.public_sources import PublicFixtures


@pytest.fixture
def audit(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    verify = runpy.run_path(str(scripts / "verify_predictions_live.py"))["verify"]
    with Atlas("fixture") as atlas:
        result = atlas.get_prediction_markets("Anthropic", include_closed=True)
    result["is_synthetic"] = False  # Audit regression only; never a real-source PASS.
    fixture = PublicFixtures()
    snapshots = [
        fixture.get(search_url("Anthropic", 3, True), 0),
        fixture.get(event_url("fictional-anthropic-event"), 0),
    ]
    capture = SimpleNamespace(snapshots={snapshot.url: snapshot for snapshot in snapshots})
    assert verify(result, capture) == 18
    return verify, result, capture


@pytest.mark.parametrize(
    "field",
    ["price", "percentage", "outcome", "market_id", "hash", "pointer", "closed_probability"],
)
def test_independent_audit_rejects_corrupted_observations(audit, field):
    verify, result, capture = audit
    result = deepcopy(result)
    market = result["records"][0]["markets"][0]
    outcome = market["outcomes"][0]
    if field == "price":
        outcome["reported_price"] = "0.9"
    elif field == "percentage":
        outcome["probability_percent"] = "90"
    elif field == "outcome":
        outcome["outcome"] = "No"
    elif field == "market_id":
        market["market_id"] = "wrong-market"
    elif field == "hash":
        outcome["field_sources"]["reported_price"]["raw_sha256"] = "0" * 64
    elif field == "pointer":
        outcome["field_sources"]["reported_price"]["embedded_json_pointer"] = "/1"
    else:
        closed = result["records"][0]["markets"][-1]["outcomes"][0]
        closed["market_implied_probability"] = "0"
        closed["probability_percent"] = "0"
    with pytest.raises(AssertionError):
        verify(result, capture)
