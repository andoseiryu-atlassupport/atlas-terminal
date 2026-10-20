"""Explicit quote/purchase client. Payment and recovery secrets never enter tool output."""

import os
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Company, FinancialQuery, PurchaseResult, Quote
from atlas_terminal.payments.common import fingerprint, origin
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.security import private_library_logs
from atlas_terminal.util import Database, canonical, digest, state_dir

CLIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_quotes (
 id TEXT PRIMARY KEY, origin TEXT NOT NULL, quote TEXT NOT NULL, recovery TEXT NOT NULL
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
    ) -> None:
        private_library_logs()
        self.origin = origin(base_origin)
        directory = directory or state_dir()
        self.db = Database((directory / "hosted-quotes.sqlite3").resolve(), CLIENT_SCHEMA)
        self.budgets = BudgetLedger(directory / "client-budgets.sqlite3")
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.policy_path = policy_path
        self.session = session

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

    def quote(self, query: FinancialQuery) -> Quote:
        response = self.request("POST", "/v1/quotes", json=query.model_dump(mode="json"))
        try:
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
            conn.execute(
                "INSERT INTO saved_quotes VALUES (?,?,?,?)",
                (quote.quote_id, self.origin, canonical(quote).decode(), token),
            )
        return quote

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

    def purchase(self, quote_id: str, *, max_cost: str | None = None) -> PurchaseResult:
        quote, token = self.saved(quote_id)
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
