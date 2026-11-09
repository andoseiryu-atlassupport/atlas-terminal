"""Seller operator commands and external-wallet client discovery; no local mainnet signer."""

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import typer

from atlas_terminal.errors import AtlasError
from atlas_terminal.payments.common import MAINNET_ASSET, MAINNET_NETWORK, origin, validate_mainnet
from atlas_terminal.seller_client import SellerClient
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import ProductQuery
from atlas_terminal.util import state_dir

seller_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def emit(value: Any) -> None:
    typer.echo(json.dumps(value.model_dump(mode="json") if hasattr(value, "model_dump") else value))


def buyer() -> SellerClient:
    configured = os.environ.get("ATLAS_SELLER_ORIGIN")
    if not configured:
        raise AtlasError("CONFIG_REQUIRED", "Configure ATLAS_SELLER_ORIGIN for the seller client.")
    return SellerClient(configured)


@seller_app.command()
def refresh(series: str, allow_network: bool = False, fixture: bool = False) -> None:
    """Operator-only publication. Public refresh requires explicit upstream access."""
    data = ProductLibrary(state_dir()).refresh(series, allow_network=allow_network, fixture=fixture)
    emit(
        {
            "series": data.query.series,
            "captured_at": data.captured_at,
            "records": len(data.research.records),
            "is_synthetic": data.is_synthetic,
            "source_access": data.source_access,
        }
    )


@seller_app.command("import-capture")
def import_capture(metadata: Path, body: Path) -> None:
    """Publish a retained BLS body with its URL, retrieval time and expected hash; no network."""
    data = ProductLibrary(state_dir()).import_capture(metadata, body)
    emit(
        {
            "series": data.query.series,
            "captured_at": data.captured_at,
            "records": len(data.research.records),
            "is_synthetic": data.is_synthetic,
            "source_access": data.source_access,
        }
    )


def readiness() -> dict[str, Any]:
    """Configuration only. Never loads a payer key, signs, fetches or calls a facilitator."""
    checks: dict[str, bool] = {}
    try:
        configured = origin(os.environ.get("ATLAS_PUBLIC_ORIGIN", ""))
        parsed = urlsplit(configured)
        checks["public_https_origin"] = parsed.scheme == "https" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
    except AtlasError:
        checks["public_https_origin"] = False
    try:
        validate_mainnet(MAINNET_NETWORK, MAINNET_ASSET, os.environ.get("ATLAS_PAY_TO", ""))
        checks["base_usdc_recipient"] = True
    except AtlasError:
        checks["base_usdc_recipient"] = False
    checks["cdp_credentials_present"] = all(
        os.environ.get(name) for name in ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET")
    )
    checks["seller_extra_installed"] = importlib.util.find_spec("cdp") is not None
    try:
        rpc = urlsplit(os.environ.get("ATLAS_MAINNET_RPC_URL", ""))
        checks["mainnet_rpc_present"] = bool(
            rpc.scheme == "https"
            and rpc.hostname
            and not rpc.username
            and not rpc.password
            and not rpc.query
            and not rpc.fragment
        )
    except ValueError:
        checks["mainnet_rpc_present"] = False
    checks["explicit_mainnet_mode"] = os.environ.get("ATLAS_SELLER_PAYMENTS") == "mainnet"
    checks["explicit_price"] = bool(
        re.fullmatch(r"[1-9][0-9]{0,8}", os.environ.get("ATLAS_PRICE_ATOMIC", ""))
    )
    try:
        entries = ProductLibrary(state_dir()).catalog(
            max_age_seconds=int(os.environ.get("ATLAS_SELLER_MAX_SOURCE_AGE_SECONDS", "172800")),
            allow_synthetic=False,
        )
        checks["prepared_real_series"] = any(row["available"] for row in entries)
    except (AtlasError, ValueError):
        checks["prepared_real_series"] = False
    return {
        "scope": "configuration_only",
        "ready": all(checks.values()),
        "checks": checks,
        "live_facilitator_verified": False,
        "real_transfer_verified": False,
        "notice": (
            "Configuration checks do not verify freshness beyond capture age, hosting, "
            "payment settlement or commercial service availability."
        ),
    }


@seller_app.command()
def doctor() -> None:
    """Inspect missing seller settings without exposing credentials or making requests."""
    emit(readiness())


@seller_app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, payments: str | None = None) -> None:
    """Run the isolated seller app behind operator-managed HTTPS and persistent storage."""
    import uvicorn

    from atlas_terminal.seller_server import create_seller_app

    if host not in {"127.0.0.1", "localhost", "::1"} or not 1 <= port <= 65535:
        raise AtlasError(
            "CONFIG_REQUIRED", "Seller binds only to loopback; use a trusted reverse proxy."
        )
    api = create_seller_app(
        payments=payments or os.environ.get("ATLAS_SELLER_PAYMENTS", "off"),
        base_origin=os.environ.get(
            "ATLAS_PUBLIC_ORIGIN", f"http://{'[::1]' if host == '::1' else host}:{port}"
        ),
        amount=os.environ.get("ATLAS_PRICE_ATOMIC", "10000"),
        max_source_age_seconds=int(os.environ.get("ATLAS_SELLER_MAX_SOURCE_AGE_SECONDS", "172800")),
    )
    uvicorn.run(api, host=host, port=port, workers=1, access_log=False, log_level="critical")


@seller_app.command()
def catalog() -> None:
    with buyer() as client:
        emit(client.catalog())


@seller_app.command()
def quote(series: str, intent_id: str) -> None:
    with buyer() as client:
        emit(client.quote(ProductQuery(series=series), intent_id=intent_id))


@seller_app.command()
def status(quote_id: str) -> None:
    with buyer() as client:
        emit(client.status(quote_id))


@seller_app.command()
def preflight(quote_id: str) -> None:
    with buyer() as client:
        emit(client.preflight(quote_id))


@seller_app.command()
def recover(quote_id: str) -> None:
    with buyer() as client:
        emit(client.recover(quote_id))


@seller_app.command()
def reconcile(quote_id: str, transaction: str | None = None) -> None:
    with buyer() as client:
        emit(client.reconcile(quote_id, transaction))


@seller_app.command("mcp")
def mcp() -> None:
    """Expose seller tools to an external x402-capable wallet over stdio."""
    from atlas_terminal.seller_mcp import create_seller_mcp

    with buyer() as client:
        create_seller_mcp(client).run(transport="stdio")
