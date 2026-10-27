"""Offline regression against byte-exact responses actually captured on 2026-10-27.

This test cannot establish present live availability and opens no network connection.
"""

import runpy
from pathlib import Path


def test_recorded_bls_responses_match_raw_cells_and_current_shared_service():
    verifier = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/verify_bls_capture.py")
    )
    assert verifier["verify_capture"]() == {
        "status": "PASS",
        "mode": "offline_recorded_response_replay",
        "requests_archived": 14,
        "matched_records": 168,
        "negative_records": 4,
        "excluded_annual_summary_rows": 4,
    }
