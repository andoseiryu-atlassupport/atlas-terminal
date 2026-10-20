"""Pinned official x402 V2 / exact / EIP-3009 integration, Base Sepolia only."""

import copy
import os
import re
import time
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from eth_account import Account
from x402 import x402ClientSync, x402ResourceServerSync
from x402.extensions.payment_identifier import (
    PAYMENT_IDENTIFIER,
    append_payment_identifier_to_extensions,
    declare_payment_identifier_extension,
    extract_payment_identifier,
    generate_payment_id,
)
from x402.http import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_response_header,
    encode_payment_signature_header,
)
from x402.interfaces import SchemeNetworkServer
from x402.mechanisms.evm.exact import ExactEvmClientScheme, ExactEvmServerScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.mechanisms.evm.types import AUTHORIZATION_TYPES, TypedDataDomain, TypedDataField
from x402.schemas import PaymentPayload, PaymentRequired, PaymentRequirements, ResourceInfo

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Quote
from atlas_terminal.payments.common import (
    ASSET,
    NETWORK,
    TOKEN_NAME,
    TOKEN_VERSION,
    Settlement,
    VerifiedPayment,
    fingerprint,
    origin,
    validate_testnet,
)
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.security import private_library_logs
from atlas_terminal.util import digest


def requirements(quote: Quote) -> PaymentRequirements:
    p = quote.pricing
    validate_testnet(p.network, p.asset, p.pay_to)
    if p.mode != "testnet" or p.scheme != "exact" or p.decimals != 6:
        raise AtlasError("POLICY_DENIED", "Unsupported testnet price tuple.")
    return PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        asset=ASSET,
        amount=p.amount_atomic,
        pay_to=p.pay_to,
        max_timeout_seconds=300,
        extra={"name": TOKEN_NAME, "version": TOKEN_VERSION, "assetTransferMethod": "eip3009"},
    )


def quiet_payment_libraries() -> None:
    private_library_logs()


class TestnetAdapter:
    header = "PAYMENT-SIGNATURE"

    def __init__(self, facilitator_url: str, *, facilitator: Any = None) -> None:
        quiet_payment_libraries()
        parsed = urlsplit(facilitator_url)
        origin(f"{parsed.scheme}://{parsed.netloc}")
        if (
            parsed.query
            or parsed.fragment
            or not re.fullmatch(r"(/[A-Za-z0-9_-]+)*/?", parsed.path)
        ):
            raise AtlasError("CONFIG_REQUIRED", "Invalid configured facilitator endpoint.")
        endpoint = facilitator_url.rstrip("/")
        # A facilitator may have a fixed path, supplied through trusted process config.
        self.http: httpx.Client | None = None
        if facilitator is None:
            self.http = httpx.Client(timeout=30, follow_redirects=False)
            facilitator = HTTPFacilitatorClientSync(
                FacilitatorConfig(url=endpoint, http_client=self.http, timeout=30)
            )
        self.facilitator = facilitator
        self.server = x402ResourceServerSync(facilitator)
        # 2.25.0 annotates this implementation's payment_flows more narrowly than
        # its own protocol. Runtime binding is exercised by the offline SDK tests.
        self.server.register(NETWORK, cast(SchemeNetworkServer, ExactEvmServerScheme()))
        self.ready = False

    def ensure_ready(self) -> None:
        if self.ready:
            return
        try:
            self.server.initialize()
            supported = self.facilitator.get_supported()
            if not any(
                k.x402_version == 2 and k.scheme == "exact" and k.network == NETWORK
                for k in supported.kinds
            ):
                raise ValueError("Unsupported facilitator")
            self.ready = True
        except Exception:
            raise AtlasError(
                "CONFIG_REQUIRED", "Facilitator did not confirm V2 exact Base Sepolia support."
            ) from None

    def challenge(self, quote: Quote, base_origin: str) -> dict[str, str]:
        self.ensure_ready()
        required = self.server.create_payment_required_response(
            [requirements(quote)],
            resource=ResourceInfo(
                url=origin(base_origin) + quote.resource_path, mime_type="application/json"
            ),
            extensions={PAYMENT_IDENTIFIER: declare_payment_identifier_extension(required=True)},
        )
        return {"PAYMENT-REQUIRED": encode_payment_required_header(required)}

    def verify(self, quote: Quote, base_origin: str, proof: str) -> VerifiedPayment:
        self.ensure_ready()
        try:
            payload = decode_payment_signature_header(proof)
            if not isinstance(payload, PaymentPayload) or payload.x402_version != 2:
                raise ValueError("v2 required")
            req = requirements(quote)
            if (
                payload.accepted != req
                or not payload.resource
                or payload.resource.url != origin(base_origin) + quote.resource_path
            ):
                raise ValueError("Resource or price mismatch")
            payment_id = extract_payment_identifier(payload)
            if not payment_id:
                raise ValueError("Identifier required")
            authorization = payload.payload["authorization"]
            nonce = authorization["nonce"]
            if not re.fullmatch(r"0x[0-9a-fA-F]{64}", nonce):
                raise ValueError("EIP-3009 nonce required")
            # Official low-level verifier. No middleware or implicit settle-on-verify.
            verified = self.facilitator.verify(payload, req)
            if (
                not verified.is_valid
                or not verified.payer
                or verified.payer.lower() != authorization["from"].lower()
            ):
                raise ValueError("Verification failed")
            return VerifiedPayment(
                payment_id, verified.payer.lower(), nonce.lower(), digest(proof.encode()), payload
            )
        except AtlasError:
            raise
        except Exception:
            raise AtlasError(
                "PAYMENT_INVALID", "Official testnet payment verification failed."
            ) from None

    def settle(self, quote: Quote, payment: VerifiedPayment) -> Settlement:
        try:
            # Call the official facilitator SDK once. ResourceServer.settle_payment in
            # 2.25.0 may retry settlement_pending internally; Atlas retains UNKNOWN.
            result = self.facilitator.settle(payment.payload, requirements(quote))
            if (
                result.success
                and result.network == NETWORK
                and re.fullmatch(r"0x[0-9a-fA-F]{64}", result.transaction)
                and (result.amount is None or result.amount == quote.pricing.amount_atomic)
                and (result.payer is None or result.payer.lower() == payment.payer.lower())
            ):
                return Settlement(
                    "SETTLED", result.transaction, encode_payment_response_header(result)
                )
            # A non-success reply can still follow a broadcast. No automatic release.
            return Settlement("SETTLEMENT_UNKNOWN", result.transaction or None)
        except Exception:
            return Settlement("SETTLEMENT_UNKNOWN")

    def close(self) -> None:
        if self.http:
            self.http.close()


class RestrictedSigner:
    """Check policy and the exact EIP-712 domain again at the real signing boundary."""

    def __init__(
        self,
        delegate: EthAccountSigner,
        quote: Quote,
        policy: SpendingPolicy,
        ledger: BudgetLedger,
        base_origin: str,
        max_cost: str | None,
    ) -> None:
        self.delegate, self.quote, self.policy = delegate, quote, policy
        self.ledger, self.base_origin, self.max_cost = ledger, base_origin, max_cost
        self.used = False

    @property
    def address(self) -> str:
        return self.delegate.address

    def sign_typed_data(
        self,
        domain: TypedDataDomain,
        types: dict[str, list[TypedDataField]],
        primary_type: str,
        message: dict[str, Any],
    ) -> bytes:
        self.policy.authorize(self.quote, self.base_origin, self.max_cost)
        row = self.ledger.get(self.quote.quote_id)
        if (
            self.used
            or not row
            or row["state"] != "RESERVED"
            or row["owner"] != self.address.lower()
            or row["amount"] != self.quote.pricing.amount_atomic
            or row["fingerprint"] != fingerprint(self.quote, self.base_origin)
        ):
            raise AtlasError("POLICY_DENIED", "A fresh durable reservation is required at signing.")
        if (
            domain.chain_id != 84532
            or domain.verifying_contract.lower() != ASSET.lower()
            or domain.name != TOKEN_NAME
            or domain.version != TOKEN_VERSION
            or primary_type != "TransferWithAuthorization"
            or str(message.get("from", "")).lower() != self.address.lower()
            or str(message.get("to", "")).lower() != self.quote.pricing.pay_to.lower()
            or str(message.get("value")) != self.quote.pricing.amount_atomic
            or not 0 < int(message.get("validBefore", 0)) - time.time() <= 310
            or int(message.get("validAfter", -1)) != 0
            or set(message) != {"from", "to", "value", "validAfter", "validBefore", "nonce"}
            or {
                name: [{"name": f.name, "type": f.type} for f in fields]
                for name, fields in types.items()
            }
            != AUTHORIZATION_TYPES
        ):
            raise AtlasError(
                "POLICY_DENIED", "SDK signing request exceeds the exact delegated transfer."
            )
        self.used = True
        self.ledger.begin_signing(self.quote, self.base_origin)
        quiet_payment_libraries()
        return self.delegate.sign_typed_data(domain, types, primary_type, message)


class TestnetSigner:
    def __init__(self, key: str | None = None) -> None:
        quiet_payment_libraries()
        try:
            key = key or os.environ.get("ATLAS_TESTNET_PRIVATE_KEY")
            if not key:
                raise ValueError("Missing dev wallet")
            self.delegate = EthAccountSigner(Account.from_key(key))
        except Exception:
            raise AtlasError(
                "WALLET_NOT_CONFIGURED",
                "Configure a dedicated testnet signer through private process settings.",
            ) from None

    @property
    def address(self) -> str:
        return self.delegate.address

    def sign(
        self,
        quote: Quote,
        challenge: str,
        policy: SpendingPolicy,
        ledger: BudgetLedger,
        base_origin: str,
        *,
        max_cost: str | None = None,
    ) -> str:
        try:
            required = decode_payment_required_header(challenge)
            expected = requirements(quote)
            if (
                not isinstance(required, PaymentRequired)
                or required.x402_version != 2
                or required.accepts != [expected]
                or not required.resource
                or required.resource.url != origin(base_origin) + quote.resource_path
                or set(required.extensions or {}) != {PAYMENT_IDENTIFIER}
            ):
                raise AtlasError(
                    "QUOTE_MISMATCH", "Payment challenge differs from the saved quote."
                )
            guarded = RestrictedSigner(self.delegate, quote, policy, ledger, base_origin, max_cost)
            client = x402ClientSync()
            client.register(NETWORK, ExactEvmClientScheme(guarded))
            extensions = append_payment_identifier_to_extensions(
                copy.deepcopy(required.extensions or {}), generate_payment_id()
            )
            payload = client.create_payment_payload(required, extensions=extensions)
            return encode_payment_signature_header(payload)
        except AtlasError:
            raise
        except Exception:
            raise AtlasError(
                "PAYMENT_INVALID",
                "Official SDK could not create the constrained testnet authorization.",
            ) from None
