"""Deployment preparation writes inert templates and rejects configuration injection."""

import runpy
from pathlib import Path

import pytest

from atlas_terminal.errors import AtlasError

prepare = runpy.run_path(str(Path(__file__).parents[1] / "scripts/prepare_seller_deploy.py"))[
    "prepare"
]


def test_seller_deployment_template_is_private_disabled_and_isolated(tmp_path):
    target = tmp_path / "rendered"
    result = prepare("https://seller.example.com", target)
    assert not result["deployed"] and not result["payments_enabled"]
    assert result["real_transfers"] == 0 and result["workers"] == 1
    service = (target / "atlas-seller.service").read_text()
    assert "atlas seller serve" in service and "atlas serve " not in service
    assert "StateDirectory=atlas-seller" in service
    assert "https://seller.example.com" in service
    assert "127.0.0.1:8002" in (target / "Caddyfile.seller").read_text()
    environment = (target / "seller.env.example").read_text()
    assert "ATLAS_SELLER_PAYMENTS=off" in environment
    assert "ATLAS_PAY_TO=\n" in environment and "CDP_API_KEY_SECRET=\n" in environment
    assert all(path.stat().st_mode & 0o077 == 0 for path in target.iterdir())
    with pytest.raises(FileExistsError):
        prepare("https://seller.example.com", target)


@pytest.mark.parametrize(
    "origin",
    [
        "http://seller.example.com",
        "https://127.0.0.1",
        "https://seller.example.com:8443",
        "https://seller.example.com/path",
        "https://seller.example.com?key=value",
        "https://seller.example.com\nEnvironment=evil",
        "https://user@seller.example.com",
    ],
)
def test_seller_template_rejects_unsafe_origins(tmp_path, origin):
    with pytest.raises((AtlasError, ValueError)):
        prepare(origin, tmp_path / "output")
    assert not (tmp_path / "output").exists()
