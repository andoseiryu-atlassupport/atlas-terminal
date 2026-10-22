"""Policy checks and atomic budget reservations immediately before signing."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Atomic, Model, Quote
from atlas_terminal.payments.common import fingerprint, origin, validate_testnet
from atlas_terminal.util import Database, stamp, utcnow


class SpendingPolicy(Model):
    enabled: bool = Field(default=False, strict=True)
    mode: Literal["mock", "testnet"]
    allowed_origins: list[str] = Field(min_length=1)
    scheme: Literal["exact"] = "exact"
    network: str
    asset: str
    pay_to: str
    decimals: Literal[6] = 6
    per_purchase_atomic: Atomic
    per_session_atomic: Atomic
    per_day_atomic: Atomic

    @classmethod
    def load(cls, path: Path) -> "SpendingPolicy":
        try:
            if path.stat().st_size > 16384:
                raise ValueError("oversized policy")
            if path.stat().st_mode & 0o022:
                raise ValueError("writable by other users")
            return cls.model_validate_json(path.read_bytes())
        except (OSError, ValueError, ValidationError):
            raise AtlasError(
                "POLICY_DENIED", "Spending policy is missing, unreadable or invalid."
            ) from None

    def authorize(self, quote: Quote, base_origin: str, max_cost: str | None = None) -> None:
        p = quote.pricing
        if not self.enabled or p.mode != self.mode:
            raise AtlasError(
                "PAYMENTS_DISABLED", "Explicit matching spending delegation is required."
            )
        if origin(base_origin) not in [origin(v) for v in self.allowed_origins]:
            raise AtlasError("POLICY_DENIED", "Service origin is not delegated.")
        if (
            p.scheme != self.scheme
            or p.network != self.network
            or p.asset.lower() != self.asset.lower()
            or p.pay_to.lower() != self.pay_to.lower()
            or p.decimals != self.decimals
        ):
            raise AtlasError("POLICY_DENIED", "Price tuple is outside the delegated policy.")
        if p.mode == "testnet":
            validate_testnet(p.network, p.asset, p.pay_to)
        elif p.network != "mock:0" or p.asset != "fixture:USDC":
            raise AtlasError("POLICY_DENIED", "Mock may only use fictional assets.")
        amount = int(p.amount_atomic)
        limit = int(self.per_purchase_atomic)
        if max_cost is not None:
            if not max_cost.isdigit() or len(max_cost) > 40:
                raise AtlasError("INVALID_INPUT", "max-cost must be atomic units.")
            limit = min(limit, int(max_cost))
        if amount <= 0 or amount > limit:
            raise AtlasError(
                "BUDGET_EXCEEDED", "Purchase exceeds the delegated one-purchase limit."
            )
        if datetime.fromisoformat(quote.expires_at.replace("Z", "+00:00")) <= utcnow():
            raise AtlasError("QUOTE_EXPIRED", "Quote expired before signing.")


BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_orders (
 quote_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
 session TEXT NOT NULL, day TEXT NOT NULL, amount TEXT NOT NULL,
 state TEXT NOT NULL, proof TEXT, proof_header TEXT, created TEXT NOT NULL
);
"""


class BudgetLedger:
    def __init__(self, path: Path) -> None:
        self.db = Database(path.resolve(), BUDGET_SCHEMA)

    def get(self, quote_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM client_orders WHERE quote_id=?", (quote_id,)
            ).fetchone()
        return dict(row) if row else None

    def reserve(
        self,
        quote: Quote,
        policy: SpendingPolicy,
        base_origin: str,
        owner: str,
        session: str,
        *,
        max_cost: str | None = None,
    ) -> bool:
        policy.authorize(quote, base_origin, max_cost)
        fp = fingerprint(quote, base_origin)
        day = utcnow().date().isoformat()
        amount = int(quote.pricing.amount_atomic)
        try:
            with self.db.connect(write=True) as conn:
                existing = conn.execute(
                    "SELECT fingerprint FROM client_orders WHERE quote_id=?", (quote.quote_id,)
                ).fetchone()
                if existing:
                    if existing[0] != fp:
                        raise AtlasError(
                            "QUOTE_MISMATCH", "Saved purchase differs from this quote."
                        )
                    return False
                rows = conn.execute(
                    "SELECT day,session,amount,state FROM client_orders "
                    "WHERE owner=? AND state!='FAILED'",
                    (owner.lower(),),
                ).fetchall()
                daily = sum(
                    int(r["amount"]) for r in rows if r["day"] == day or r["state"] != "SPENT"
                )
                session_total = sum(int(r["amount"]) for r in rows if r["session"] == session)
                if daily + amount > int(policy.per_day_atomic) or session_total + amount > int(
                    policy.per_session_atomic
                ):
                    raise AtlasError(
                        "BUDGET_EXCEEDED", "Atomic daily or session budget is exhausted."
                    )
                conn.execute(
                    "INSERT INTO client_orders VALUES (?,?,?,?,?,?,?,NULL,NULL,?)",
                    (
                        quote.quote_id,
                        fp,
                        owner.lower(),
                        session,
                        day,
                        str(amount),
                        "RESERVED",
                        stamp(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            raise AtlasError(
                "LEDGER_UNAVAILABLE", "Budget reservation could not be persisted."
            ) from None

    def begin_signing(self, quote: Quote, base_origin: str) -> None:
        """Consume the reservation once, including across concurrent signer instances."""
        with self.db.connect(write=True) as conn:
            changed = conn.execute(
                "UPDATE client_orders SET state='SIGNING' "
                "WHERE quote_id=? AND fingerprint=? AND state='RESERVED'",
                (quote.quote_id, fingerprint(quote, base_origin)),
            ).rowcount
            if changed != 1:
                raise AtlasError("POLICY_DENIED", "This reservation already entered signing.")

    def save_proof(self, quote_id: str, proof: str, header: str) -> None:
        with self.db.connect(write=True) as conn:
            changed = conn.execute(
                "UPDATE client_orders SET proof=?,proof_header=?,state='SIGNED' "
                "WHERE quote_id=? AND state='SIGNING'",
                (proof, header, quote_id),
            ).rowcount
            if changed != 1:
                raise AtlasError(
                    "LEDGER_UNAVAILABLE", "Signing reservation was lost; payment stopped."
                )

    def state(self, quote_id: str, state: str) -> None:
        if state not in {"PENDING", "SPENT", "FAILED"}:
            raise ValueError("invalid budget state")
        with self.db.connect(write=True) as conn:
            existing = conn.execute(
                "SELECT state FROM client_orders WHERE quote_id=?", (quote_id,)
            ).fetchone()
            if existing and existing["state"] in {"SPENT", "FAILED"}:
                if state in {"SPENT", "FAILED"} and state != existing["state"]:
                    raise AtlasError("LEDGER_UNAVAILABLE", "Conflicting terminal budget state.")
                return
            if state == "SPENT":
                # Charge a delayed confirmation to today's cap, once. Repeated
                # retrieval tomorrow must not move an already confirmed spend.
                conn.execute(
                    "UPDATE client_orders SET state='SPENT',day=? "
                    "WHERE quote_id=? AND state!='SPENT'",
                    (utcnow().date().isoformat(), quote_id),
                )
            else:
                conn.execute("UPDATE client_orders SET state=? WHERE quote_id=?", (state, quote_id))

    def safe_summary(self, quote_id: str) -> dict[str, Any]:
        row = self.get(quote_id)
        return {"quote_id": quote_id, "state": row["state"] if row else "NOT_STARTED"}
