import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Pricing
from atlas_terminal.util import canonical, digest

NETWORK = "eip155:84532"
# Confirmed against the pinned official x402 default_assets.py, not a symbol lookup.
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
TOKEN_NAME = "USDC"
TOKEN_VERSION = "2"
MAINNET_NETWORK = "eip155:8453"
MAINNET_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class PaymentQuote(Protocol):
    """Read-only payment binding shared by annual and seller product orders."""

    @property
    def quote_id(self) -> str: ...

    @property
    def query(self) -> BaseModel: ...

    @property
    def pricing(self) -> Pricing: ...

    @property
    def resource_path(self) -> str: ...

    @property
    def expires_at(self) -> str: ...

    @property
    def result_sha256(self) -> str: ...

    def model_dump(self, *, mode: Literal["json"]) -> dict[str, Any]: ...


def validate_mainnet(network: str, asset: str, pay_to: str) -> None:
    if network != MAINNET_NETWORK or asset.lower() != MAINNET_ASSET.lower():
        raise AtlasError("POLICY_DENIED", "Only exact Base mainnet USDC is supported.")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", pay_to) or int(pay_to, 16) == 0:
        raise AtlasError("CONFIG_REQUIRED", "Configure a valid mainnet recipient address.")


def seller_pricing(mode: str, amount: str, pay_to: str) -> Pricing:
    """Mainnet is available only to the explicitly selected seller service."""
    if mode != "mainnet":
        return make_pricing(mode, amount, pay_to)
    validate_mainnet(MAINNET_NETWORK, MAINNET_ASSET, pay_to)
    if not re.fullmatch(r"[1-9][0-9]{0,8}", amount):
        raise AtlasError("CONFIG_REQUIRED", "Seller price must be 1–999999999 atomic USDC units.")
    return Pricing(
        mode="mainnet",
        scheme="exact",
        network=MAINNET_NETWORK,
        asset=MAINNET_ASSET,
        pay_to=pay_to,
        symbol="USDC",
        decimals=6,
        amount_atomic=amount,
        display_amount=format(Decimal(amount).scaleb(-6), "f"),
    )


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
        or parsed.scheme not in {"http", "https"}
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise AtlasError(
            "POLICY_DENIED", "Use an HTTPS origin or explicit loopback development origin."
        )
    return value.rstrip("/")


def validate_testnet(network: str, asset: str, pay_to: str) -> None:
    if network != NETWORK or asset.lower() != ASSET.lower():
        raise AtlasError(
            "POLICY_DENIED", "Only exact Base Sepolia USDC is supported; mainnet is disabled."
        )
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", pay_to) or int(pay_to, 16) == 0:
        raise AtlasError("CONFIG_REQUIRED", "Configure a valid testnet recipient address.")


def make_pricing(mode: str, amount: str = "10000", pay_to: str = "fixture:merchant") -> Pricing:
    if not re.fullmatch(r"(0|[1-9][0-9]{0,20})", amount):
        raise AtlasError("CONFIG_REQUIRED", "Price must be an unsigned atomic-unit integer string.")
    if mode == "off":
        return Pricing(
            mode="off",
            scheme="none",
            network="none",
            asset="none",
            pay_to="none",
            symbol="USD",
            decimals=6,
            amount_atomic="0",
            display_amount="0",
        )
    if int(amount) <= 0:
        raise AtlasError("CONFIG_REQUIRED", "Paid modes require a positive price.")
    if mode == "testnet":
        validate_testnet(NETWORK, ASSET, pay_to)
    elif mode != "mock":
        raise AtlasError("CONFIG_REQUIRED", "Payment mode must be off, mock or testnet.")
    return Pricing(
        mode="testnet" if mode == "testnet" else "mock",
        scheme="exact",
        network=NETWORK if mode == "testnet" else "mock:0",
        asset=ASSET if mode == "testnet" else "fixture:USDC",
        pay_to=pay_to,
        symbol="USDC",
        decimals=6,
        amount_atomic=amount,
        display_amount=format(Decimal(amount).scaleb(-6), "f"),
    )


def fingerprint(quote: PaymentQuote, base_origin: str) -> str:
    return digest(
        canonical(
            {
                "quote": quote.model_dump(mode="json"),
                "origin": origin(base_origin),
                "method": "GET",
                "resource_path": quote.resource_path,
                "query_hash": digest(canonical(quote.query)),
            }
        )
    )


@dataclass(frozen=True)
class VerifiedPayment:
    payment_id: str
    payer: str
    nonce: str
    proof_digest: str
    payload: Any
    valid_before: int | None = None


@dataclass(frozen=True)
class Settlement:
    # Failure is terminal only when the adapter can prove that nothing settled.
    state: str
    transaction: str | None = None
    response_header: str | None = None


class PaymentAdapter(Protocol):
    header: str

    def challenge(self, quote: PaymentQuote, base_origin: str) -> dict[str, str]: ...

    def verify(self, quote: PaymentQuote, base_origin: str, proof: str) -> VerifiedPayment: ...

    def settle(self, quote: PaymentQuote, payment: VerifiedPayment) -> Settlement: ...

    def close(self) -> None: ...
