"""Seller SDK with private recovery, immutable-result checks and external-wallet payments."""

import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
    encode_payment_signature_header,
)
from x402.schemas import PaymentPayload, PaymentRequired, PaymentRequirements

from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import PurchasePreflight, ReconcileRequest, ReconciliationResult
from atlas_terminal.payments.common import origin
from atlas_terminal.payments.intents import INTENT_ACK_HEADER, INTENT_HEADER, intent_identifier
from atlas_terminal.security import private_library_logs
from atlas_terminal.seller_models import (
    ProductPurchase,
    ProductQuery,
    ProductQuote,
    product_selection,
)
from atlas_terminal.util import Database, canonical, digest, state_dir

CLIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS seller_intents (
 origin TEXT NOT NULL, intent TEXT NOT NULL, query_hash TEXT NOT NULL, capability TEXT NOT NULL,
 quote_id TEXT, PRIMARY KEY(origin,intent)
);
CREATE TABLE IF NOT EXISTS seller_quotes (
 origin TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, recovery TEXT NOT NULL,
 proof_hash TEXT, PRIMARY KEY(origin,id)
);
"""


class SellerClient:
    """No local signer. The external wallet controls authorization and spending limits."""

    def __init__(
        self, base_origin: str, *, directory: Path | None = None, client: Any = None
    ) -> None:
        private_library_logs()
        self.origin = origin(base_origin)
        self.db = Database(
            ((directory or state_dir()) / "seller-client.sqlite3").resolve(), CLIENT_SCHEMA
        )
        self.http = client or httpx.Client(timeout=30, follow_redirects=False)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "SellerClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with self.http.stream(
                method, self.origin + path, follow_redirects=False, **kwargs
            ) as response:
                if 300 <= response.status_code < 400:
                    raise AtlasError("POLICY_DENIED", "Seller redirects are refused.")
                body = bytearray()
                for part in response.iter_bytes():
                    body.extend(part)
                    if len(body) > 3 * 1024 * 1024:
                        raise AtlasError("SOURCE_UNAVAILABLE", "Seller response exceeds limit.")
                return httpx.Response(
                    response.status_code,
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in {"content-encoding", "content-length"}
                    },
                    content=bytes(body),
                )
        except (httpx.HTTPError, OSError):
            raise AtlasError(
                "SOURCE_UNAVAILABLE",
                "Seller connection failed. Check existing order status; do not sign again.",
            ) from None

    def catalog(self) -> dict[str, Any]:
        return dict(HostedClient.body(self.request("GET", "/v1/seller/catalog")))

    @staticmethod
    def requirements(quote: ProductQuote) -> PaymentRequirements:
        if quote.pricing.mode == "mainnet":
            from atlas_terminal.payments.x402_mainnet import MainnetAdapter

            return MainnetAdapter.requirements(quote)
        if quote.pricing.mode == "testnet":
            from atlas_terminal.payments.x402_testnet import requirements

            return requirements(quote)
        raise AtlasError("CONFIG_REQUIRED", "An external x402 wallet requires a real network mode.")

    def quote(
        self, query: ProductQuery | dict[str, Any], *, intent_id: str | None = None
    ) -> ProductQuote:
        q = ProductQuery.model_validate(query)
        identity = intent_identifier(intent_id or secrets.token_hex(16))
        query_hash = digest(canonical(q))
        with self.db.connect(write=True) as conn:
            saved = conn.execute(
                "SELECT * FROM seller_intents WHERE origin=? AND intent=?", (self.origin, identity)
            ).fetchone()
            if saved:
                if saved["query_hash"] != query_hash:
                    raise AtlasError(
                        "INTENT_CONFLICT", "The saved seller intent has a different query."
                    )
                capability, saved_id = saved["capability"], saved["quote_id"]
            else:
                if conn.execute("SELECT count(*) FROM seller_intents").fetchone()[0] >= 10000:
                    raise AtlasError("CACHE_FULL", "Seller intent store is full.")
                capability, saved_id = secrets.token_urlsafe(32), None
                conn.execute(
                    "INSERT INTO seller_intents VALUES (?,?,?,?,NULL)",
                    (self.origin, identity, query_hash, capability),
                )
        if saved_id:
            return self.saved(saved_id)[0]
        response = self.request(
            "POST",
            "/v1/seller/quotes",
            json=q.model_dump(mode="json"),
            headers={INTENT_HEADER: capability},
        )
        raw = HostedClient.body(response)
        try:
            result = ProductQuote.model_validate(raw)
            token = response.headers.get("Atlas-Recovery-Token", "")
            if (
                result.query != q
                or result.resource_path != f"/v1/seller/quotes/{result.quote_id}/result"
                or result.snapshot_id != result.result_sha256
                or response.headers.get(INTENT_ACK_HEADER) != "v1"
                or not re.fullmatch(r"[a-zA-Z0-9_-]{32,128}", token)
            ):
                raise ValueError("Quote mismatch")
            if result.pricing.mode in {"testnet", "mainnet"}:
                self.requirements(result)
                if result.is_synthetic:
                    raise ValueError("Fictional real-money quote")
            p = result.pricing
            if (
                p.decimals != 6
                or Decimal(p.display_amount) != Decimal(p.amount_atomic).scaleb(-6)
                or (p.mode != "off" and int(p.amount_atomic) <= 0)
                or datetime.fromisoformat(result.expires_at.replace("Z", "+00:00")).tzinfo is None
            ):
                raise ValueError("Invalid displayed price or expiry")
        except (ValueError, InvalidOperation):
            raise AtlasError("QUOTE_MISMATCH", "Seller quote does not match the request.") from None
        body = canonical(result).decode()
        with self.db.connect(write=True) as conn:
            previous = conn.execute(
                "SELECT * FROM seller_quotes WHERE origin=? AND id=?",
                (self.origin, result.quote_id),
            ).fetchone()
            binding = conn.execute(
                "SELECT quote_id FROM seller_intents WHERE origin=? AND intent=?",
                (self.origin, identity),
            ).fetchone()
            if (
                not binding
                or binding[0] not in {None, result.quote_id}
                or (previous and (previous["body"] != body or previous["recovery"] != token))
            ):
                raise AtlasError("QUOTE_MISMATCH", "Seller changed an existing order binding.")
            conn.execute(
                "INSERT OR IGNORE INTO seller_quotes VALUES (?,?,?,?,NULL)",
                (self.origin, result.quote_id, body, token),
            )
            conn.execute(
                "UPDATE seller_intents SET quote_id=? WHERE origin=? AND intent=?",
                (result.quote_id, self.origin, identity),
            )
        return result

    def saved(self, quote_id: str) -> tuple[ProductQuote, str, str | None]:
        if not re.fullmatch(r"[0-9a-f]{32}", quote_id):
            raise AtlasError("INVALID_INPUT", "Invalid seller quote identifier.")
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM seller_quotes WHERE origin=? AND id=?", (self.origin, quote_id)
            ).fetchone()
        if not row:
            raise AtlasError("QUOTE_NOT_FOUND", "No private seller quote is saved for this origin.")
        return ProductQuote.model_validate_json(row["body"]), row["recovery"], row["proof_hash"]

    def _order_request(
        self, method: str, quote_id: str, action: str, **kwargs: Any
    ) -> httpx.Response:
        _, token, _ = self.saved(quote_id)
        return self.request(
            method,
            f"/v1/seller/quotes/{quote_id}/{action}",
            headers={"Authorization": "Bearer " + token},
            **kwargs,
        )

    def status(self, quote_id: str) -> dict[str, Any]:
        raw = HostedClient.body(self._order_request("GET", quote_id, "status"))
        if (
            not isinstance(raw, dict)
            or raw.get("quote_id") != quote_id
            or raw.get("state")
            not in {"QUOTED", "EXPIRED", "SETTLED", "NOT_REQUIRED", "FAILED", "SETTLEMENT_UNKNOWN"}
        ):
            raise AtlasError("QUOTE_MISMATCH", "Seller status failed validation.")
        return {"quote_id": quote_id, "state": raw["state"]}

    def preflight(self, quote_id: str) -> PurchasePreflight:
        value = PurchasePreflight.model_validate(
            HostedClient.body(self._order_request("GET", quote_id, "preflight"))
        )
        quote = self.saved(quote_id)[0]
        if (
            value.quote_id != quote_id
            or value.pricing != quote.pricing
            or value.result_sha256 != quote.result_sha256
        ):
            raise AtlasError("QUOTE_MISMATCH", "Seller preflight changed the order.")
        return value

    def reconcile(self, quote_id: str, transaction: str | None = None) -> ReconciliationResult:
        request = ReconcileRequest(transaction=transaction)
        result = ReconciliationResult.model_validate(
            HostedClient.body(
                self._order_request(
                    "POST", quote_id, "reconcile", json=request.model_dump(mode="json")
                )
            )
        )
        if result.quote_id != quote_id:
            raise AtlasError("QUOTE_MISMATCH", "Seller reconciliation changed the order.")
        return result

    def validate_purchase(self, response: httpx.Response, quote: ProductQuote) -> ProductPurchase:
        raw = HostedClient.body(response)
        if response.status_code == 202:
            raise AtlasError(
                "SETTLEMENT_UNKNOWN", "Payment is unresolved; do not sign again.", status=202
            )
        try:
            result = ProductPurchase.model_validate(raw)
            r, p, d = result.receipt, quote.pricing, result.data
            if (
                r.quote_id != quote.quote_id
                or r.mode != p.mode
                or r.network != p.network
                or r.asset != p.asset
                or r.amount_atomic != p.amount_atomic
                or r.result_sha256 != quote.result_sha256
                or digest(canonical(d)) != quote.result_sha256
                or d.query != quote.query
                or d.is_synthetic != quote.is_synthetic
                or d.captured_at != quote.captured_at
                or len(d.research.records) != quote.record_count
                or product_selection(d) != quote.selection
                or r.simulated != (p.mode == "mock")
                or r.status != ("NOT_REQUIRED" if p.mode == "off" else "SETTLED")
                or (
                    p.mode in {"testnet", "mainnet"}
                    and not re.fullmatch(r"0x[0-9a-fA-F]{64}", r.transaction or "")
                )
                or (p.mode in {"off", "mock"} and r.transaction is not None)
            ):
                raise ValueError("Artifact mismatch")
        except ValueError:
            raise AtlasError(
                "QUOTE_MISMATCH", "Purchased artifact or receipt failed validation."
            ) from None
        return result

    def recover(self, quote_id: str) -> ProductPurchase:
        return self.validate_purchase(
            self._order_request("GET", quote_id, "result"), self.saved(quote_id)[0]
        )

    def external_purchase(
        self, quote_id: str, payment: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        quote, token, prior = self.saved(quote_id)
        expected = self.requirements(quote)
        if prior:
            if self.status(quote_id)["state"] != "SETTLED":
                raise AtlasError(
                    "SETTLEMENT_UNKNOWN",
                    "An existing external payment must be reconciled; do not sign again.",
                    status=202,
                )
            payment = None
        headers = {"Authorization": "Bearer " + token}
        if payment is not None:
            try:
                payload = PaymentPayload.model_validate(payment)
                if (
                    payload.x402_version != 2
                    or payload.accepted != expected
                    or not payload.resource
                    or payload.resource.url != self.origin + quote.resource_path
                ):
                    raise ValueError("Unbound payment")
                proof = encode_payment_signature_header(payload)
                if len(proof) > 16384:
                    raise ValueError("Oversized proof")
            except ValueError:
                raise AtlasError(
                    "PAYMENT_INVALID", "External payment does not match the seller quote."
                ) from None
            with self.db.connect(write=True) as conn:
                if (
                    conn.execute(
                        "UPDATE seller_quotes SET proof_hash=? WHERE origin=? AND id=? "
                        "AND proof_hash IS NULL",
                        (digest(proof.encode()), self.origin, quote_id),
                    ).rowcount
                    != 1
                ):
                    raise AtlasError(
                        "SETTLEMENT_UNKNOWN",
                        "Another caller already claimed this order.",
                        status=202,
                    )
            headers["PAYMENT-SIGNATURE"] = proof
        response = self.request("GET", quote.resource_path, headers=headers)
        if response.status_code == 402:
            if prior or payment is not None:
                raise AtlasError(
                    "SETTLEMENT_UNKNOWN", "A claimed payment cannot be signed again.", status=202
                )
            try:
                required = decode_payment_required_header(
                    response.headers.get("PAYMENT-REQUIRED", "")
                )
                if (
                    not isinstance(required, PaymentRequired)
                    or required.x402_version != 2
                    or required.accepts != [expected]
                    or not required.resource
                    or required.resource.url != self.origin + quote.resource_path
                ):
                    raise ValueError("Unbound challenge")
                return required.model_dump(mode="json", by_alias=True), None, True
            except ValueError:
                raise AtlasError("QUOTE_MISMATCH", "Seller payment terms changed.") from None
        result = self.validate_purchase(response, quote)
        try:
            settled = decode_payment_response_header(response.headers.get("PAYMENT-RESPONSE", ""))
            if (
                not settled.success
                or settled.network != expected.network
                or settled.transaction != result.receipt.transaction
                or settled.amount not in {None, expected.amount}
            ):
                raise ValueError("Receipt mismatch")
        except ValueError:
            raise AtlasError(
                "QUOTE_MISMATCH", "Seller settlement receipt failed validation."
            ) from None
        return result.model_dump(mode="json"), settled.model_dump(mode="json", by_alias=True), False
