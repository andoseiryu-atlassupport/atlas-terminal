"""Explicit quote/purchase client. Payment and recovery secrets never enter tool output."""

import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import ValidationError

from atlas_terminal.errors import AtlasError, NextAction
from atlas_terminal.models import (
    BudgetStatus,
    Company,
    FinancialQuery,
    PreflightCode,
    PurchasePreflight,
    PurchaseResult,
    Quote,
    ReconcileRequest,
    ReconciliationResult,
)
from atlas_terminal.payments.common import fingerprint, origin, validate_testnet
from atlas_terminal.payments.intents import INTENT_ACK_HEADER, INTENT_HEADER, intent_identifier
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.security import private_library_logs
from atlas_terminal.util import Database, canonical, digest, state_dir, utcnow

CLIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_quotes (
 id TEXT PRIMARY KEY, origin TEXT NOT NULL, quote TEXT NOT NULL, recovery TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_payment_claims (
 quote_id TEXT PRIMARY KEY, kind TEXT NOT NULL, proof_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS client_purchase_intents (
 origin TEXT NOT NULL, intent_id TEXT NOT NULL, query_hash TEXT NOT NULL,
 capability TEXT NOT NULL, quote_id TEXT, PRIMARY KEY(origin,intent_id)
);
"""


def quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise AtlasError("INVALID_INPUT", "Invalid quote identifier.")
    return value


class HostedClient:
    def __init__(
        self,
        base_origin: str,
        *,
        directory: Path | None = None,
        client: Any = None,
        policy_path: Path | None = None,
        session: str = "default",
        payer_address: str | None = None,
    ) -> None:
        private_library_logs()
        self.origin = origin(base_origin)
        directory = directory or state_dir()
        self.db = Database((directory / "hosted-quotes.sqlite3").resolve(), CLIENT_SCHEMA)
        self.budgets = BudgetLedger(directory / "client-budgets.sqlite3")
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.policy_path = policy_path
        self.session = session
        self.payer_address = payer_address

    @classmethod
    def from_env(cls) -> "HostedClient":
        url = os.environ.get("ATLAS_HOSTED_ORIGIN")
        if not url:
            raise AtlasError("CONFIG_REQUIRED", "Configure ATLAS_HOSTED_ORIGIN explicitly.")
        path = os.environ.get("ATLAS_POLICY_FILE")
        return cls(
            url,
            policy_path=Path(path) if path else None,
            session=os.environ.get("ATLAS_SESSION_ID", "default"),
            payer_address=os.environ.get("ATLAS_TESTNET_PAYER_ADDRESS") or None,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(
                method, self.origin + path, follow_redirects=False, **kwargs
            )
        except (httpx.HTTPError, OSError):
            raise AtlasError(
                "SOURCE_UNAVAILABLE",
                "Hosted connection failed; signed purchases remain reserved.",
                retryable=True,
                next_action=(
                    NextAction(action="check_purchase_status", tool="atlas_purchase_status")
                    if path.endswith(("/result", "/reconcile"))
                    else None
                ),
            ) from None
        if 300 <= response.status_code < 400:
            raise AtlasError(
                "POLICY_DENIED", "Hosted redirects are refused; payment proof was not forwarded."
            )
        if len(response.content) > 3 * 1024 * 1024:
            raise AtlasError("SOURCE_UNAVAILABLE", "Hosted response exceeds size limit.")
        return response

    @staticmethod
    def body(response: Any) -> Any:
        try:
            body = response.json()
        except ValueError:
            raise AtlasError("SOURCE_UNAVAILABLE", "Hosted response is not JSON.") from None
        if response.status_code >= 400:
            # Do not relay arbitrary server messages/details into a model or exception.
            code = (
                body.get("error", {}).get("code", "SOURCE_UNAVAILABLE")
                if isinstance(body, dict)
                else "SOURCE_UNAVAILABLE"
            )
            known = {
                "COMPANY_NOT_FOUND",
                "AMBIGUOUS_COMPANY",
                "INCOMPLETE_COVERAGE",
                "SOURCE_UNAVAILABLE",
                "SOURCE_RATE_LIMITED",
                "QUOTE_EXPIRED",
                "QUOTE_NOT_FOUND",
                "PAYMENT_INVALID",
                "PAYMENT_FAILED",
                "PAYMENT_REUSE_CONFLICT",
                "SETTLEMENT_UNKNOWN",
                "UNAUTHORIZED",
                "POLICY_DENIED",
                "PAYMENT_REQUIRED",
                "CONFIG_REQUIRED",
                "LEDGER_UNAVAILABLE",
                "INTENT_CONFLICT",
                "INTENT_RETIRED",
                "CACHE_FULL",
                "INVALID_INPUT",
            }
            if code not in known:
                code = "SOURCE_UNAVAILABLE"
            raise AtlasError(
                code,
                "Hosted request failed; inspect the order status before retrying payment.",
                status=response.status_code,
            )
        return body

    def resolve_company(self, query: str) -> Company:
        from atlas_terminal.models import identifier

        return Company.model_validate(
            self.body(
                self.request("GET", "/v1/companies/resolve", params={"query": identifier(query)})
            )
        )

    def list_filings(self, query: str, form: str, limit: int) -> dict[str, Any]:
        company = self.resolve_company(query)
        return self.body(
            self.request(
                "GET", "/v1/filings", params={"cik": company.cik, "form": form, "limit": limit}
            )
        )

    def quote(self, query: FinancialQuery, *, intent_id: str | None = None) -> Quote:
        capability = None
        if intent_id is not None:
            intent_identifier(intent_id)
            query_hash = digest(canonical(query))
            with self.db.connect(write=True) as conn:
                saved = conn.execute(
                    "SELECT * FROM client_purchase_intents WHERE origin=? AND intent_id=?",
                    (self.origin, intent_id),
                ).fetchone()
                if saved:
                    if saved["query_hash"] != query_hash:
                        raise AtlasError(
                            "INTENT_CONFLICT",
                            "Intent is already bound to a different query.",
                            status=409,
                        )
                    capability = saved["capability"]
                    saved_id = saved["quote_id"]
                else:
                    if (
                        conn.execute("SELECT count(*) FROM client_purchase_intents").fetchone()[0]
                        >= 10000
                    ):
                        raise AtlasError("CACHE_FULL", "Private intent storage is full.")
                    capability, saved_id = secrets.token_urlsafe(32), None
                    conn.execute(
                        "INSERT INTO client_purchase_intents VALUES (?,?,?,?,NULL)",
                        (self.origin, intent_id, query_hash, capability),
                    )
            if saved_id:
                quote, _ = self.saved(saved_id)
                if quote.query != query:
                    raise AtlasError("QUOTE_MISMATCH", "Saved intent query is inconsistent.")
                return quote
        response = self.request(
            "POST",
            "/v1/quotes",
            json=query.model_dump(mode="json"),
            headers={INTENT_HEADER: capability} if capability else {},
        )
        try:
            if capability and response.headers.get(INTENT_ACK_HEADER) != "v1":
                # Old servers must not silently weaken replay protection.
                self.body(response)
                raise ValueError("Intent protocol not acknowledged")
            quote = Quote.model_validate(self.body(response))
            quote_identifier(quote.quote_id)
            if quote.query != query or quote.resource_path != f"/v1/quotes/{quote.quote_id}/result":
                raise ValueError("Quote mismatch")
            token = response.headers.get("Atlas-Recovery-Token")
            if not token or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
                raise ValueError("Missing recovery capability")
        except (ValueError, ValidationError):
            raise AtlasError(
                "QUOTE_MISMATCH", "Hosted quote failed contract or request validation."
            ) from None
        with self.db.connect(write=True) as conn:
            if capability:
                saved = conn.execute(
                    "SELECT quote_id FROM client_purchase_intents WHERE origin=? AND intent_id=?",
                    (self.origin, intent_id),
                ).fetchone()
                if not saved or (
                    saved["quote_id"] is not None and saved["quote_id"] != quote.quote_id
                ):
                    raise AtlasError("QUOTE_MISMATCH", "Intent returned a different quote.")
                conn.execute(
                    "UPDATE client_purchase_intents SET quote_id=? WHERE origin=? AND intent_id=?",
                    (quote.quote_id, self.origin, intent_id),
                )
            existing = conn.execute(
                "SELECT quote,recovery,origin FROM saved_quotes WHERE id=?", (quote.quote_id,)
            ).fetchone()
            if existing:
                if (
                    existing["quote"] != canonical(quote).decode()
                    or existing["recovery"] != token
                    or existing["origin"] != self.origin
                ):
                    raise AtlasError("QUOTE_MISMATCH", "Stored quote or capability changed.")
                return quote
            conn.execute(
                "INSERT INTO saved_quotes VALUES (?,?,?,?)",
                (quote.quote_id, self.origin, canonical(quote).decode(), token),
            )
        return quote

    def _budget_context(self) -> tuple[SpendingPolicy, str]:
        if self.policy_path is None:
            raise AtlasError("PAYMENTS_DISABLED", "Configure an operator spending policy.")
        policy = SpendingPolicy.load(self.policy_path)
        if policy.mode == "mock":
            if policy.network != "mock:0" or policy.asset != "fixture:USDC":
                raise AtlasError("POLICY_DENIED", "Mock policy requires fictional assets.")
            return policy, "fixture:agent"
        validate_testnet(policy.network, policy.asset, policy.pay_to)
        if not self.payer_address or not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.payer_address):
            raise AtlasError(
                "WALLET_NOT_CONFIGURED",
                "Configure the public testnet payer address for budget reads.",
            )
        return policy, self.payer_address

    def budget_status(self) -> BudgetStatus:
        policy, owner = self._budget_context()
        return self.budgets.summary(policy, owner, self.session)

    def preflight(self, quote_id: str, *, max_cost: str | None = None) -> PurchasePreflight:
        quote, token = self.saved(quote_id)
        response = self.request(
            "GET", f"/v1/quotes/{quote_id}/preflight", headers={"Authorization": "Bearer " + token}
        )
        try:
            result = PurchasePreflight.model_validate(self.body(response))
            if (
                result.scope != "order"
                or result.budget is not None
                or result.quote_id != quote_id
                or result.pricing != quote.pricing
                or result.result_sha256 != quote.result_sha256
                or result.expires_at != quote.expires_at
            ):
                raise ValueError("Preflight mismatch")
        except (ValueError, ValidationError):
            raise AtlasError(
                "QUOTE_MISMATCH", "Order preflight differs from the saved quote."
            ) from None
        result.scope = "local_wallet"
        if result.decision != "ready":
            return result
        if result.state != "QUOTED":
            raise AtlasError("QUOTE_MISMATCH", "Only an unstarted quote can be ready.")
        if datetime.fromisoformat(quote.expires_at.replace("Z", "+00:00")) <= utcnow():
            result.decision, result.code = "blocked", "QUOTE_EXPIRED"
            return result
        if quote.pricing.mode == "off":
            return result
        with self.db.connect() as conn:
            claim = conn.execute(
                "SELECT kind FROM client_payment_claims WHERE quote_id=?", (quote_id,)
            ).fetchone()
        reservation = self.budgets.get(quote_id)
        if reservation or (claim and claim["kind"] == "external"):
            result.decision, result.code = "wait", "PURCHASE_ALREADY_STARTED"
            return result
        try:
            policy, owner = self._budget_context()
            result.budget = self.budgets.summary(policy, owner, self.session)
            policy.authorize(quote, self.origin, max_cost)
            if int(quote.pricing.amount_atomic) > min(
                int(result.budget.session.remaining_atomic), int(result.budget.day.remaining_atomic)
            ):
                raise AtlasError("BUDGET_EXCEEDED", "Remaining budget is insufficient.")
        except AtlasError as exc:
            if exc.code not in {
                "PAYMENTS_DISABLED",
                "POLICY_DENIED",
                "WALLET_NOT_CONFIGURED",
                "BUDGET_EXCEEDED",
                "QUOTE_EXPIRED",
            }:
                raise
            result.decision, result.code = "blocked", cast(PreflightCode, exc.code)
        return result

    def saved(self, quote_id: str) -> tuple[Quote, str]:
        quote_identifier(quote_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM saved_quotes WHERE id=?", (quote_id,)).fetchone()
        if not row or row["origin"] != self.origin:
            raise AtlasError(
                "QUOTE_NOT_FOUND", "No saved quote and recovery capability for this service."
            )
        return Quote.model_validate_json(row["quote"]), row["recovery"]

    def validate_purchase(self, response: Any, quote: Quote) -> PurchaseResult:
        try:
            result = PurchaseResult.model_validate(self.body(response))
            receipt = result.receipt
            if (
                digest(canonical(result.data)) != quote.result_sha256
                or result.data.query != quote.query
                or result.data.company != quote.company
                or receipt.quote_id != quote.quote_id
                or receipt.mode != quote.pricing.mode
                or receipt.network != quote.pricing.network
                or receipt.asset != quote.pricing.asset
                or receipt.amount_atomic != quote.pricing.amount_atomic
                or receipt.result_sha256 != quote.result_sha256
                or receipt.simulated != (quote.pricing.mode == "mock")
                or receipt.status != ("NOT_REQUIRED" if quote.pricing.mode == "off" else "SETTLED")
                or (quote.pricing.mode == "testnet" and not receipt.transaction)
                or (quote.pricing.mode != "testnet" and receipt.transaction is not None)
            ):
                raise ValueError("Artifact mismatch")
            return result
        except (ValueError, ValidationError):
            raise AtlasError(
                "QUOTE_MISMATCH", "Purchased artifact or receipt differs from the saved quote."
            ) from None

    def status(self, quote_id: str) -> dict[str, Any]:
        quote, token = self.saved(quote_id)
        result = self.body(
            self.request(
                "GET", f"/v1/quotes/{quote_id}/status", headers={"Authorization": "Bearer " + token}
            )
        )
        # Restrict model-visible output to known public fields; never echo remote extras.
        allowed = {"QUOTED", "EXPIRED", "SETTLED", "NOT_REQUIRED", "FAILED", "SETTLEMENT_UNKNOWN"}
        if (
            not isinstance(result, dict)
            or result.get("quote_id") != quote.quote_id
            or result.get("state") not in allowed
        ):
            raise AtlasError("SOURCE_UNAVAILABLE", "Invalid hosted status response.")
        from atlas_terminal.models import Receipt

        receipt = (
            Receipt.model_validate(result["receipt"]).model_dump(mode="json")
            if result.get("receipt")
            else None
        )
        return {"quote_id": quote_id, "state": result["state"], "receipt": receipt}

    def recover(self, quote: Quote, token: str) -> PurchaseResult:
        response = self.request(
            "GET", quote.resource_path, headers={"Authorization": "Bearer " + token}
        )
        if response.status_code == 202:
            raise AtlasError(
                "SETTLEMENT_UNKNOWN",
                "Purchase remains unresolved; no new signature was created.",
                status=202,
            )
        result = self.validate_purchase(response, quote)
        if self.budgets.get(quote.quote_id):
            self.budgets.state(quote.quote_id, "SPENT")
        return result

    def reconcile(self, quote_id: str, transaction: str | None = None) -> ReconciliationResult:
        quote, token = self.saved(quote_id)
        try:
            request = ReconcileRequest(transaction=transaction)
        except ValidationError:
            raise AtlasError("INVALID_INPUT", "Invalid transaction hint.") from None
        response = self.request(
            "POST",
            f"/v1/quotes/{quote_id}/reconcile",
            headers={"Authorization": "Bearer " + token},
            json=request.model_dump(mode="json"),
        )
        try:
            result = ReconciliationResult.model_validate(self.body(response))
            if result.quote_id != quote_id:
                raise ValueError("Order mismatch")
            if result.state == "SETTLED":
                # Validate the immutable artifact before updating the spending ledger.
                recovered = self.recover(quote, token)
                if result.outcome != "settled" or result.receipt != recovered.receipt:
                    raise ValueError("Receipt mismatch")
            elif result.state == "FAILED":
                if result.outcome != "failed" or not result.evidence_sha256 or result.receipt:
                    raise ValueError("Failure evidence missing")
                self.budgets.state(quote_id, "FAILED")
            elif result.outcome not in {"pending", "not_started", "not_required"}:
                raise ValueError("Outcome mismatch")
            return result
        except (ValueError, ValidationError):
            raise AtlasError("SOURCE_UNAVAILABLE", "Invalid reconciliation response.") from None

    def external_purchase(
        self, quote_id: str, payment: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """Bridge MCP metadata to the bound HTTP resource without invoking a signer."""
        from x402.http.utils import (
            decode_payment_required_header,
            decode_payment_response_header,
            encode_payment_signature_header,
        )
        from x402.schemas import PaymentPayload, PaymentRequired

        from atlas_terminal.payments.x402_testnet import requirements

        quote, token = self.saved(quote_id)
        if quote.pricing.mode != "testnet":
            raise AtlasError("CONFIG_REQUIRED", "Native x402 MCP purchases require testnet mode.")
        with self.db.connect() as conn:
            prior = conn.execute(
                "SELECT * FROM client_payment_claims WHERE quote_id=?", (quote_id,)
            ).fetchone()
        if prior or self.budgets.get(quote_id):
            # A probe after an ambiguous transport failure must not request a new signature.
            status = self.status(quote_id)
            if status["state"] == "FAILED":
                raise AtlasError(
                    "PAYMENT_FAILED", "The bound payment definitively failed.", status=409
                )
            if status["state"] != "SETTLED":
                raise AtlasError(
                    "SETTLEMENT_UNKNOWN",
                    "An existing payment remains bound; reconcile it before retrying.",
                    status=202,
                )
            payment = None
        headers = {"Authorization": "Bearer " + token}
        if payment is not None:
            try:
                payload = PaymentPayload.model_validate(payment)
                if (
                    payload.x402_version != 2
                    or payload.accepted != requirements(quote)
                    or not payload.resource
                    or payload.resource.url != self.origin + quote.resource_path
                ):
                    raise ValueError("Payload mismatch")
                proof = encode_payment_signature_header(payload)
                if len(proof) > 16384:
                    raise ValueError("Oversized proof")
            except (ValueError, ValidationError):
                raise AtlasError(
                    "PAYMENT_INVALID", "MCP payment does not match the saved quote."
                ) from None
            with self.db.connect(write=True) as conn:
                changed = conn.execute(
                    "INSERT OR IGNORE INTO client_payment_claims VALUES (?,'external',?)",
                    (quote_id, digest(proof.encode())),
                ).rowcount
                if changed != 1:
                    raise AtlasError(
                        "SETTLEMENT_UNKNOWN",
                        "Concurrent external payment already claimed this quote.",
                        status=202,
                    )
            headers["PAYMENT-SIGNATURE"] = proof
        response = self.request("GET", quote.resource_path, headers=headers)
        if response.status_code == 402:
            try:
                required = decode_payment_required_header(
                    response.headers.get("PAYMENT-REQUIRED", "")
                )
                if (
                    not isinstance(required, PaymentRequired)
                    or required.x402_version != 2
                    or required.accepts != [requirements(quote)]
                    or not required.resource
                    or required.resource.url != self.origin + quote.resource_path
                ):
                    raise ValueError("Challenge mismatch")
                if prior or payment is not None:
                    raise AtlasError(
                        "SETTLEMENT_UNKNOWN", "A prior payment cannot be signed again.", status=202
                    )
                return required.model_dump(mode="json", by_alias=True), None, True
            except (ValueError, ValidationError):
                raise AtlasError(
                    "QUOTE_MISMATCH", "Invalid native MCP payment challenge."
                ) from None
        if response.status_code == 202:
            raise AtlasError(
                "SETTLEMENT_UNKNOWN",
                "Payment remains unresolved; no new signature is requested.",
                status=202,
            )
        result = self.validate_purchase(response, quote)
        try:
            settlement = decode_payment_response_header(
                response.headers.get("PAYMENT-RESPONSE", "")
            )
            if (
                not settlement.success
                or settlement.network != quote.pricing.network
                or settlement.transaction != result.receipt.transaction
                or (
                    settlement.amount is not None
                    and settlement.amount != quote.pricing.amount_atomic
                )
            ):
                raise ValueError("Settlement mismatch")
        except (ValueError, ValidationError):
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "Invalid native MCP settlement response."
            ) from None
        return (
            result.model_dump(mode="json"),
            settlement.model_dump(mode="json", by_alias=True),
            False,
        )

    def purchase(self, quote_id: str, *, max_cost: str | None = None) -> PurchaseResult:
        quote, token = self.saved(quote_id)
        with self.db.connect() as conn:
            external = conn.execute(
                "SELECT 1 FROM client_payment_claims WHERE quote_id=? AND kind='external'",
                (quote_id,),
            ).fetchone()
        if external:
            if self.status(quote_id)["state"] == "SETTLED":
                return self.recover(quote, token)
            raise AtlasError(
                "SETTLEMENT_UNKNOWN",
                "An external payment is already bound to this quote.",
                status=202,
            )
        if quote.pricing.mode == "off":
            return self.recover(quote, token)
        existing = self.budgets.get(quote_id)
        if existing:
            if existing["fingerprint"] != fingerprint(quote, self.origin):
                raise AtlasError(
                    "QUOTE_MISMATCH", "Purchase fingerprint differs from the saved reservation."
                )
            status = self.status(quote_id)
            if status["state"] == "SETTLED":
                return self.recover(quote, token)
            if status["state"] == "FAILED":
                self.budgets.state(quote_id, "FAILED")
                raise AtlasError("PAYMENT_FAILED", "Confirmed failed order cannot be re-signed.")
            raise AtlasError(
                "SETTLEMENT_UNKNOWN",
                "Existing reservation is retained; use status or manual reconciliation.",
                status=202,
            )
        if self.policy_path is None:
            raise AtlasError(
                "PAYMENTS_DISABLED", "Set an explicit spending policy before purchase."
            )
        policy = SpendingPolicy.load(self.policy_path)
        policy.authorize(quote, self.origin, max_cost)
        challenge = self.request("GET", quote.resource_path)
        if challenge.status_code != 402:
            self.body(challenge)
            raise AtlasError("QUOTE_MISMATCH", "Expected a challenge for this paid quote.")
        if quote.pricing.mode == "mock":
            if challenge.headers.get("Atlas-Mock-Required") != "offline-simulation-only":
                raise AtlasError("QUOTE_MISMATCH", "Mock challenge is missing.")
            signer: Any = None
            owner = "fixture:agent"
        else:
            from atlas_terminal.payments.x402_testnet import TestnetSigner

            signer = TestnetSigner()
            owner = signer.address
            if self.payer_address is not None and owner.lower() != self.payer_address.lower():
                raise AtlasError(
                    "POLICY_DENIED", "Signer differs from the configured budget payer."
                )
        with self.db.connect(write=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_payment_claims VALUES (?,'local',NULL)", (quote_id,)
            )
            claim = conn.execute(
                "SELECT kind FROM client_payment_claims WHERE quote_id=?", (quote_id,)
            ).fetchone()
            if not claim or claim["kind"] != "local":
                raise AtlasError(
                    "SETTLEMENT_UNKNOWN",
                    "An external wallet already claimed this quote.",
                    status=202,
                )
        if not self.budgets.reserve(
            quote, policy, self.origin, owner, self.session, max_cost=max_cost
        ):
            raise AtlasError(
                "SETTLEMENT_UNKNOWN", "Concurrent purchase already reserved this quote."
            )
        # Reservation is committed before any code can invoke the signer. If this
        # process dies at any following point, a retry must never sign again.
        if signer:
            header = "PAYMENT-SIGNATURE"
            proof = signer.sign(
                quote,
                challenge.headers.get("PAYMENT-REQUIRED", ""),
                policy,
                self.budgets,
                self.origin,
                max_cost=max_cost,
            )
        else:
            policy.authorize(quote, self.origin, max_cost)
            self.budgets.begin_signing(quote, self.origin)
            header = MockAdapter.header
            proof = MockAdapter.sign(quote, self.origin)
        self.budgets.save_proof(quote_id, proof, header)
        self.budgets.state(quote_id, "PENDING")
        response = self.request("GET", quote.resource_path, headers={header: proof})
        if response.status_code == 202:
            raise AtlasError(
                "SETTLEMENT_UNKNOWN",
                "Settlement is unknown; budget stays reserved and no new signature is allowed.",
                status=202,
            )
        result = self.validate_purchase(response, quote)
        self.budgets.state(quote_id, "SPENT")
        return result

    def close(self) -> None:
        self.client.close()
