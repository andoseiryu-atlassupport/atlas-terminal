"""Separate opt-in tests. Never enable these on untrusted CI."""

import os
import subprocess
import sys

import pytest


@pytest.mark.live_sec
@pytest.mark.enable_socket
def test_sec_live():
    if os.environ.get("ATLAS_RUN_LIVE_SEC") != "1":
        pytest.skip("Requires explicit ATLAS_RUN_LIVE_SEC=1 and SEC contact configuration")
    result = subprocess.run(
        [sys.executable, "scripts/smoke.py", "sec", "--allow-network"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0


@pytest.mark.live_x402
@pytest.mark.enable_socket
def test_real_testnet():
    if os.environ.get("ATLAS_RUN_LIVE_X402") != "1":
        pytest.skip("Requires explicit ATLAS_RUN_LIVE_X402=1, dedicated testnet wallet and policy")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke.py",
            "testnet",
            "--allow-network",
            "--allow-testnet-transfer",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
