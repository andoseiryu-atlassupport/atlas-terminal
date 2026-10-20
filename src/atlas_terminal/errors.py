"""Safe domain errors shared by every interface."""

from typing import Any
from uuid import uuid4


class AtlasError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status = status

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
                "request_id": uuid4().hex,
            }
        }

    @property
    def exit_code(self) -> int:
        if self.code.startswith(("SOURCE_", "HISTORY_")):
            return 3
        if self.code in {"SETTLEMENT_UNKNOWN", "PAYMENT_INVALID", "PAYMENT_FAILED"}:
            return 5
        if self.code in {
            "POLICY_DENIED",
            "BUDGET_EXCEEDED",
            "LEDGER_UNAVAILABLE",
            "CONFIG_REQUIRED",
            "WALLET_NOT_CONFIGURED",
            "PAYMENTS_DISABLED",
        }:
            return 4
        return 2
