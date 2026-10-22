"""Immutable artifacts, recovery capabilities and compare-and-set settlement claims."""

import hmac
import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import (
    FinancialQuery,
    FinancialResult,
    Missing,
    Pricing,
    PurchaseResult,
    Quote,
    Receipt,
    ReconciliationResult,
)
from atlas_terminal.payments.common import (
    PaymentAdapter,
    Settlement,
    VerifiedPayment,
    fingerprint,
    origin,
)
from atlas_terminal.payments.reconciliation import SettlementReconciler, settlement_header
from atlas_terminal.util import Database, canonical, digest, stamp, utcnow

ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS result_snapshots (
 id TEXT PRIMARY KEY, body BLOB NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, metadata TEXT NOT NULL,
 recovery_hash TEXT NOT NULL, state TEXT NOT NULL, expires TEXT NOT NULL,
 receipt TEXT, retained_until TEXT, response_header TEXT
);
CREATE TABLE IF NOT EXISTS payment_attempts (
 payment_id TEXT PRIMARY KEY, quote_id TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
 network TEXT NOT NULL, asset TEXT NOT NULL, payer TEXT NOT NULL, nonce TEXT NOT NULL,
 proof_digest TEXT NOT NULL, state TEXT NOT NULL, transaction_id TEXT,
 UNIQUE(network,asset,payer,nonce)
);
CREATE TABLE IF NOT EXISTS quote_rate (id TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reconciliation_audit (
 id INTEGER PRIMARY KEY, quote_id TEXT NOT NULL, payment_id TEXT NOT NULL,
 previous_state TEXT NOT NULL, state TEXT NOT NULL, checked_at TEXT NOT NULL,
 evidence TEXT NOT NULL, sha256 TEXT NOT NULL
);
"""


class OrderService:
    def __init__(
        self,
        atlas: Atlas,
        path: Path,
        pricing: Pricing,
        *,
        base_origin: str,
        adapter: PaymentAdapter | None = None,
        reconciler: SettlementReconciler | None = None,
        clock: Callable[[], datetime] = utcnow,
        ttl_seconds: int = 300,
        max_snapshots: int = 128,
        max_storage_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.atlas, self.pricing, self.adapter = atlas, pricing, adapter
        self.reconciler = reconciler
        self.base_origin, self.clock, self.ttl = origin(base_origin), clock, ttl_seconds
        self.max_snapshots, self.max_storage_bytes = max_snapshots, max_storage_bytes
        self.db = Database(path.resolve(), ORDER_SCHEMA)
        # Legacy attempts have no deadline and cannot be declared expired-unused.
        with self.db.connect(write=True) as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(payment_attempts)")}
            if "valid_before" not in columns:
                conn.execute("ALTER TABLE payment_attempts ADD COLUMN valid_before TEXT")
        if pricing.mode != "off" and adapter is None:
            raise AtlasError("CONFIG_REQUIRED", "Paid mode requires its explicit adapter.")

    def prepare(self, query: FinancialQuery) -> tuple[Quote, str]:
        now = self.clock()
        # Global rate and storage bounds, shared by all callers. No IP/header trust.
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM quote_rate WHERE created < ?", (now.timestamp() - 60,))
            if conn.execute("SELECT count(*) FROM quote_rate").fetchone()[0] >= 30:
                raise AtlasError(
                    "SOURCE_RATE_LIMITED", "Quote preparation rate limit reached.", status=429
                )
            conn.execute("INSERT INTO quote_rate VALUES (?,?)", (uuid4().hex, now.timestamp()))
            conn.execute(
                "DELETE FROM quotes WHERE (state IN ('QUOTED','EXPIRED') AND expires < ?) "
                "OR (state IN ('SETTLED','NOT_REQUIRED') AND retained_until < ?)",
                (stamp(now), stamp(now)),
            )
            conn.execute(
                "DELETE FROM result_snapshots WHERE id NOT IN (SELECT snapshot_id FROM quotes)"
            )
            if conn.execute("SELECT count(*) FROM quotes").fetchone()[0] >= self.max_snapshots:
                raise AtlasError("SOURCE_RATE_LIMITED", "Snapshot quota is full.", status=429)
        result = self.atlas.get_financials(query)
        body = canonical(result)
        if len(body) > 2 * 1024 * 1024:
            raise AtlasError("SOURCE_UNAVAILABLE", "Normalized snapshot exceeds size limit.")
        quote_id, snapshot_id = uuid4().hex, digest(body)
        token = secrets.token_urlsafe(32)
        missing = [
            Missing(metric=f.metric, period_end=f.period.end, reason=f.reason or f.status)
            for f in result.facts
            if f.status != "reported"
        ]
        quote = Quote(
            quote_id=quote_id,
            query=query,
            company=result.company,
            snapshot_id=snapshot_id,
            result_sha256=snapshot_id,
            period_ends=result.period_ends,
            coverage=result.coverage,
            partial=result.partial,
            missing=missing,
            pricing=self.pricing,
            expires_at=stamp(now + timedelta(seconds=self.ttl)),
            resource_path=f"/v1/quotes/{quote_id}/result",
            limitations=result.limitations,
            is_synthetic=result.is_synthetic,
        )
        with self.db.connect(write=True) as conn:
            # Recheck after preparation to cover concurrent quote creation.
            count = conn.execute("SELECT count(*) FROM quotes").fetchone()[0]
            total = conn.execute(
                "SELECT coalesce(sum(length(body)),0) FROM result_snapshots"
            ).fetchone()[0]
            if count >= self.max_snapshots or total + len(body) > self.max_storage_bytes:
                raise AtlasError(
                    "SOURCE_RATE_LIMITED", "Snapshot storage quota is full.", status=429
                )
            conn.execute(
                "INSERT OR IGNORE INTO result_snapshots VALUES (?,?,?)",
                (snapshot_id, body, snapshot_id),
            )
            conn.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,NULL,NULL,NULL)",
                (
                    quote_id,
                    snapshot_id,
                    canonical(quote).decode(),
                    digest(token.encode()),
                    "QUOTED",
                    quote.expires_at,
                ),
            )
        return quote, token

    def row(self, quote_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not row:
            raise AtlasError(
                "QUOTE_NOT_FOUND", "Quote was not found or retention ended.", status=404
            )
        return dict(row)

    def authorized(self, row: dict[str, Any], bearer: str | None, proof: str | None = None) -> bool:
        if bearer and hmac.compare_digest(digest(bearer.encode()), row["recovery_hash"]):
            return True
        if proof:
            with self.db.connect() as conn:
                saved = conn.execute(
                    "SELECT proof_digest FROM payment_attempts WHERE quote_id=?", (row["id"],)
                ).fetchone()
            return bool(saved and hmac.compare_digest(digest(proof.encode()), saved[0]))
        return False

    def status(self, quote_id: str, bearer: str | None) -> dict[str, Any]:
        row = self.row(quote_id)
        if not self.authorized(row, bearer):
            raise AtlasError("UNAUTHORIZED", "Recovery capability is required.", status=401)
        state = row["state"]
        if state == "QUOTED" and row["expires"] <= stamp(self.clock()):
            state = "EXPIRED"
        if state in {"PAYMENT_VERIFIED", "SETTLEMENT_SUBMITTED"}:
            state = "SETTLEMENT_UNKNOWN"
        return {
            "quote_id": quote_id,
            "state": state,
            "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
        }

    def artifact(self, row: dict[str, Any]) -> PurchaseResult:
        body = self.snapshot(row)
        if not row["receipt"]:
            raise AtlasError(
                "SETTLEMENT_UNKNOWN", "Confirmed receipt has not been committed.", status=202
            )
        if row["retained_until"] and row["retained_until"] <= stamp(self.clock()):
            raise AtlasError("QUOTE_NOT_FOUND", "Purchased snapshot retention ended.", status=410)
        return PurchaseResult(
            data=FinancialResult.model_validate_json(body),
            receipt=Receipt.model_validate_json(row["receipt"]),
        )

    def snapshot(self, row: dict[str, Any]) -> bytes:
        with self.db.connect() as conn:
            snap = conn.execute(
                "SELECT * FROM result_snapshots WHERE id=?", (row["snapshot_id"],)
            ).fetchone()
        if not snap or digest(snap["body"]) != snap["sha256"]:
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Purchased artifact failed integrity verification."
            )
        return bytes(snap["body"])

    def result(
        self, quote_id: str, *, bearer: str | None = None, proof: str | None = None
    ) -> tuple[PurchaseResult | dict[str, Any], int, dict[str, str]]:
        row = self.row(quote_id)
        quote = Quote.model_validate_json(row["metadata"])
        if row["state"] != "QUOTED":
            if not self.authorized(row, bearer, proof):
                raise AtlasError(
                    "UNAUTHORIZED", "Order ID is not result access authorization.", status=401
                )
            if row["state"] in {"SETTLED", "NOT_REQUIRED"}:
                headers = (
                    {"PAYMENT-RESPONSE": row["response_header"]} if row["response_header"] else {}
                )
                return self.artifact(row), 200, headers
            if row["state"] == "FAILED":
                raise AtlasError(
                    "PAYMENT_FAILED",
                    "Payment definitively failed; this order cannot be re-signed.",
                    status=409,
                )
            return (
                {"quote_id": quote_id, "state": "SETTLEMENT_UNKNOWN", "code": "SETTLEMENT_UNKNOWN"},
                202,
                {},
            )
        if row["expires"] <= stamp(self.clock()):
            raise AtlasError("QUOTE_EXPIRED", "Unstarted quote has expired.", status=410)
        self.snapshot(row)
        if quote.pricing.mode == "off":
            if not self.authorized(row, bearer):
                raise AtlasError(
                    "UNAUTHORIZED",
                    "Recovery capability is required for the free snapshot.",
                    status=401,
                )
            self.complete(quote, Settlement("SETTLED"), payment_id=None)
            return self.artifact(self.row(quote_id)), 200, {}
        assert self.adapter is not None
        if not proof:
            error = AtlasError(
                "PAYMENT_REQUIRED",
                "Purchase requires the advertised payment mechanism.",
                status=402,
            )
            return error.envelope(), 402, self.adapter.challenge(quote, self.base_origin)
        if len(proof) > 16384:
            raise AtlasError("PAYMENT_INVALID", "Payment proof exceeds limit.")
        payment = self.adapter.verify(quote, self.base_origin, proof)
        claimed = self.claim(quote, payment)
        if not claimed:
            return self.result(quote_id, bearer=bearer, proof=proof)
        try:
            settlement = self.adapter.settle(quote, payment)
        except Exception:
            settlement = Settlement("SETTLEMENT_UNKNOWN")
        if settlement.state == "SETTLED":
            with self.db.connect(write=True) as conn:
                conn.execute(
                    "UPDATE payment_attempts SET transaction_id=? WHERE payment_id=? "
                    "AND state IN ('SETTLEMENT_SUBMITTED','SETTLEMENT_UNKNOWN')",
                    (settlement.transaction, payment.payment_id),
                )
            self.complete(quote, settlement, payment.payment_id)
            return (
                self.artifact(self.row(quote_id)),
                200,
                (
                    {"PAYMENT-RESPONSE": settlement.response_header}
                    if settlement.response_header
                    else {}
                ),
            )
        with self.db.connect(write=True) as conn:
            state = "FAILED" if settlement.state == "FAILED" else "SETTLEMENT_UNKNOWN"
            conn.execute(
                "UPDATE payment_attempts SET state=?,transaction_id=coalesce(?,transaction_id) "
                "WHERE payment_id=? AND state IN ('SETTLEMENT_SUBMITTED','SETTLEMENT_UNKNOWN')",
                (state, settlement.transaction, payment.payment_id),
            )
            conn.execute(
                "UPDATE quotes SET state=? WHERE id=? "
                "AND state IN ('SETTLEMENT_SUBMITTED','SETTLEMENT_UNKNOWN')",
                (state, quote_id),
            )
        return self.result(quote_id, bearer=bearer, proof=proof)

    def claim(self, quote: Quote, payment: VerifiedPayment) -> bool:
        try:
            with self.db.connect(write=True) as conn:
                prior = conn.execute(
                    "SELECT * FROM payment_attempts WHERE payment_id=?", (payment.payment_id,)
                ).fetchone()
                if prior:
                    if (
                        prior["quote_id"] != quote.quote_id
                        or prior["fingerprint"] != fingerprint(quote, self.base_origin)
                        or prior["proof_digest"] != payment.proof_digest
                    ):
                        raise AtlasError(
                            "PAYMENT_REUSE_CONFLICT",
                            "Payment identifier belongs to a different request.",
                            status=409,
                        )
                    return False
                row = conn.execute(
                    "SELECT state,expires FROM quotes WHERE id=?", (quote.quote_id,)
                ).fetchone()
                if not row or row["state"] != "QUOTED":
                    raise AtlasError(
                        "PAYMENT_REUSE_CONFLICT", "Quote already has a payment attempt.", status=409
                    )
                if row["expires"] <= stamp(self.clock()):
                    raise AtlasError(
                        "QUOTE_EXPIRED", "Quote expired before a payment was accepted.", status=410
                    )
                conn.execute(
                    "INSERT INTO payment_attempts "
                    "(payment_id,quote_id,fingerprint,network,asset,payer,nonce,"
                    "proof_digest,state,valid_before) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        payment.payment_id,
                        quote.quote_id,
                        fingerprint(quote, self.base_origin),
                        quote.pricing.network,
                        quote.pricing.asset.lower(),
                        payment.payer.lower(),
                        payment.nonce.lower(),
                        payment.proof_digest,
                        "SETTLEMENT_SUBMITTED",
                        str(payment.valid_before) if payment.valid_before is not None else None,
                    ),
                )
                conn.execute(
                    "UPDATE quotes SET state='SETTLEMENT_SUBMITTED' WHERE id=? AND state='QUOTED'",
                    (quote.quote_id,),
                )
            return True
        except sqlite3.IntegrityError:
            raise AtlasError(
                "PAYMENT_REUSE_CONFLICT",
                "Authorization nonce or payment ID is already bound to an order.",
                status=409,
            ) from None

    def complete(self, quote: Quote, settlement: Settlement, payment_id: str | None) -> None:
        with self.db.connect(write=True) as conn:
            self._complete(conn, quote, settlement, payment_id)

    def _complete(
        self,
        conn: sqlite3.Connection,
        quote: Quote,
        settlement: Settlement,
        payment_id: str | None,
    ) -> None:
        existing = conn.execute("SELECT state FROM quotes WHERE id=?", (quote.quote_id,)).fetchone()
        if not existing:
            raise AtlasError("LEDGER_UNAVAILABLE", "Settlement order is missing.")
        if existing["state"] in {"SETTLED", "NOT_REQUIRED"}:
            return
        if payment_id:
            attempt = conn.execute(
                "SELECT * FROM payment_attempts WHERE payment_id=? AND quote_id=?",
                (payment_id, quote.quote_id),
            ).fetchone()
            if (
                not attempt
                or attempt["fingerprint"] != fingerprint(quote, self.base_origin)
                or attempt["state"] not in {"SETTLEMENT_SUBMITTED", "SETTLEMENT_UNKNOWN"}
                or existing["state"] not in {"SETTLEMENT_SUBMITTED", "SETTLEMENT_UNKNOWN"}
            ):
                raise AtlasError(
                    "LEDGER_UNAVAILABLE", "Settlement claim changed; operation stopped."
                )
        elif quote.pricing.mode != "off" or existing["state"] != "QUOTED":
            raise AtlasError("LEDGER_UNAVAILABLE", "Unpaid completion is not permitted.")
        now = self.clock()
        receipt = Receipt(
            quote_id=quote.quote_id,
            mode=quote.pricing.mode,
            simulated=quote.pricing.mode == "mock",
            status="NOT_REQUIRED" if quote.pricing.mode == "off" else "SETTLED",
            network=quote.pricing.network,
            asset=quote.pricing.asset,
            amount_atomic=quote.pricing.amount_atomic,
            transaction=settlement.transaction,
            settled_at=stamp(now) if payment_id else None,
            result_sha256=quote.result_sha256,
            retained_until=stamp(now + timedelta(hours=24)),
        )
        conn.execute(
            "UPDATE quotes SET state=?,receipt=?,retained_until=?,response_header=? WHERE id=?",
            (
                receipt.status,
                canonical(receipt).decode(),
                receipt.retained_until,
                settlement.response_header,
                quote.quote_id,
            ),
        )
        if payment_id:
            conn.execute(
                "UPDATE payment_attempts SET state='SETTLED',transaction_id=? WHERE payment_id=?",
                (settlement.transaction, payment_id),
            )

    def reconciliation_report(self, quote_id: str) -> ReconciliationResult:
        row = self.row(quote_id)
        with self.db.connect() as conn:
            audit = conn.execute(
                "SELECT * FROM reconciliation_audit WHERE quote_id=? ORDER BY id DESC LIMIT 1",
                (quote_id,),
            ).fetchone()
        if audit and audit["state"] != row["state"]:
            audit = None
        if audit and digest(audit["evidence"].encode()) != audit["sha256"]:
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Reconciliation audit failed integrity verification."
            )
        state = row["state"]
        if state == "QUOTED" and row["expires"] <= stamp(self.clock()):
            state = "EXPIRED"
        outcomes = {
            "SETTLED": "settled",
            "FAILED": "failed",
            "NOT_REQUIRED": "not_required",
            "QUOTED": "not_started",
            "EXPIRED": "not_started",
        }
        if state not in outcomes:
            state = "SETTLEMENT_UNKNOWN"
        evidence = json.loads(audit["evidence"]) if audit else None
        return ReconciliationResult.model_validate(
            {
                "quote_id": quote_id,
                "state": state,
                "outcome": outcomes.get(state, "pending"),
                "reason": evidence["reason"]
                if evidence
                else "No reconciliation evidence was needed or recorded.",
                "checked_at": audit["checked_at"] if audit else stamp(self.clock()),
                "evidence_sha256": audit["sha256"] if audit else None,
                "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
            }
        )

    def reconcile(
        self, quote_id: str, bearer: str | None, transaction: str | None = None
    ) -> ReconciliationResult:
        row = self.row(quote_id)
        if not self.authorized(row, bearer):
            raise AtlasError("UNAUTHORIZED", "Recovery capability is required.", status=401)
        if row["state"] not in {"SETTLEMENT_SUBMITTED", "SETTLEMENT_UNKNOWN", "PAYMENT_VERIFIED"}:
            return self.reconciliation_report(quote_id)
        if self.reconciler is None:
            raise AtlasError("CONFIG_REQUIRED", "Configure a trusted testnet reconciliation RPC.")
        quote = Quote.model_validate_json(row["metadata"])
        self.snapshot(row)
        with self.db.connect() as conn:
            saved = conn.execute(
                "SELECT * FROM payment_attempts WHERE quote_id=?", (quote_id,)
            ).fetchone()
        if (
            not saved
            or saved["fingerprint"] != fingerprint(quote, self.base_origin)
            or saved["network"] != quote.pricing.network
            or saved["asset"] != quote.pricing.asset.lower()
        ):
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Bound payment attempt is missing or inconsistent."
            )
        # RPC reads never hold the SQLite write lock. They do not verify, settle,
        # sign or broadcast a payment, and use no saved payment proof.
        decision = self.reconciler.check(quote, dict(saved), transaction)
        evidence = {
            "state": decision.state,
            "reason": decision.reason,
            "transaction": decision.transaction,
            "details": decision.details,
        }
        encoded = canonical(evidence)
        with self.db.connect(write=True) as conn:
            current = conn.execute("SELECT state FROM quotes WHERE id=?", (quote_id,)).fetchone()
            if current and current["state"] in {"SETTLED", "NOT_REQUIRED", "FAILED"}:
                pass
            elif current:
                if decision.state == "SETTLED":
                    if not decision.transaction:
                        raise AtlasError(
                            "LEDGER_UNAVAILABLE", "Finalized transaction evidence is missing."
                        )
                    self._complete(
                        conn,
                        quote,
                        Settlement(
                            "SETTLED",
                            decision.transaction,
                            settlement_header(quote, saved["payer"], decision.transaction),
                        ),
                        saved["payment_id"],
                    )
                else:
                    conn.execute("UPDATE quotes SET state=? WHERE id=?", (decision.state, quote_id))
                    conn.execute(
                        "UPDATE payment_attempts SET state=? WHERE quote_id=?",
                        (decision.state, quote_id),
                    )
                conn.execute(
                    "INSERT INTO reconciliation_audit "
                    "(quote_id,payment_id,previous_state,state,checked_at,evidence,sha256) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        quote_id,
                        saved["payment_id"],
                        current["state"],
                        decision.state,
                        stamp(self.clock()),
                        encoded.decode(),
                        digest(encoded),
                    ),
                )
            else:
                raise AtlasError("LEDGER_UNAVAILABLE", "Reconciliation order disappeared.")
        return self.reconciliation_report(quote_id)

    def close(self) -> None:
        if self.reconciler:
            self.reconciler.close()
        if self.adapter:
            self.adapter.close()
        self.atlas.close()
