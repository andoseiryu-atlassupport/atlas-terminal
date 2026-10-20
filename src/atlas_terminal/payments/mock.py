"""Offline simulation only. This is intentionally not an x402 wire implementation."""

import json
from uuid import uuid4

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Quote
from atlas_terminal.payments.common import Settlement, VerifiedPayment, fingerprint
from atlas_terminal.util import canonical, digest


class MockAdapter:
    header = "Atlas-Mock-Payment"

    def __init__(self) -> None:
        self.settlements = 0

    def challenge(self, quote: Quote, base_origin: str) -> dict[str, str]:
        return {"Atlas-Mock-Required": "offline-simulation-only"}

    @staticmethod
    def sign(quote: Quote, base_origin: str) -> str:
        return canonical(
            {
                "quote_id": quote.quote_id,
                "fingerprint": fingerprint(quote, base_origin),
                "payment_id": "mock_" + uuid4().hex,
                "payer": "fixture:agent",
                "nonce": uuid4().hex,
            }
        ).decode()

    def verify(self, quote: Quote, base_origin: str, proof: str) -> VerifiedPayment:
        try:
            data = json.loads(proof)
            if (
                quote.pricing.mode != "mock"
                or data["quote_id"] != quote.quote_id
                or data["fingerprint"] != fingerprint(quote, base_origin)
                or data["payer"] != "fixture:agent"
                or not 16 <= len(data["payment_id"]) <= 128
                or len(data["nonce"]) != 32
            ):
                raise ValueError("mock proof mismatch")
            return VerifiedPayment(
                data["payment_id"], data["payer"], data["nonce"], digest(proof.encode()), data
            )
        except (ValueError, KeyError, TypeError):
            raise AtlasError("PAYMENT_INVALID", "Invalid offline mock proof.") from None

    def settle(self, quote: Quote, payment: VerifiedPayment) -> Settlement:
        self.settlements += 1
        return Settlement("SETTLED")

    def close(self) -> None:
        pass
