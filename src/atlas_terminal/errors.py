"""Safe domain errors shared by every interface."""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "fix_query",
        "inspect_catalog",
        "resolve_company",
        "review_coverage",
        "review_requirements",
        "retry_read",
        "request_quote",
        "wait",
        "check_purchase_status",
        "contact_operator",
        "restart_query",
        "stop",
    ]
    tool: str | None = None
    fields: list[str] = Field(default_factory=list)
    automatic: bool = False


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]
    request_id: str
    next_action: NextAction | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorPayload


def recovery_action(code: str, retryable: bool, fields: list[str]) -> NextAction:
    if code in {"INVALID_INPUT", "INVALID_CURSOR", "RESPONSE_TOO_LARGE"}:
        return NextAction(action="fix_query", fields=fields)
    if code.startswith("UNSUPPORTED_"):
        return NextAction(action="inspect_catalog", tool="atlas_research_catalog", fields=fields)
    if code in {"COMPANY_NOT_FOUND", "AMBIGUOUS_COMPANY"}:
        return NextAction(action="resolve_company", tool="atlas_resolve_company")
    if code == "INCOMPLETE_COVERAGE":
        return NextAction(action="review_coverage")
    if code == "QUALITY_NOT_MET":
        return NextAction(action="review_requirements", fields=["requirements"])
    if code == "SETTLEMENT_UNKNOWN":
        return NextAction(action="check_purchase_status", tool="atlas_purchase_status")
    if code == "QUOTE_REQUIRED":
        return NextAction(action="request_quote", tool="atlas_quote")
    if code in {"RESULT_EXPIRED", "EVIDENCE_NOT_FOUND"}:
        return NextAction(action="restart_query")
    if code == "SOURCE_RATE_LIMITED":
        return NextAction(action="wait")
    if code.startswith(("SOURCE_", "HISTORY_")) and retryable:
        # Only an idempotent research/read operation may be retried automatically.
        return NextAction(action="retry_read")
    if code in {
        "CONFIG_REQUIRED",
        "WALLET_NOT_CONFIGURED",
        "PAYMENTS_DISABLED",
        "POLICY_DENIED",
        "BUDGET_EXCEEDED",
        "LEDGER_UNAVAILABLE",
        "CACHE_FULL",
    }:
        return NextAction(action="contact_operator")
    return NextAction(action="stop")


class AtlasError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status: int = 400,
        fields: list[str] | None = None,
        retry_after_seconds: int | None = None,
        next_action: NextAction | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status = status
        self.request_id = uuid4().hex
        self.next_action = next_action or recovery_action(code, retryable, fields or [])
        self.retry_after_seconds = (
            min(86400, max(0, retry_after_seconds)) if retry_after_seconds is not None else None
        )

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
                "request_id": self.request_id,
                "next_action": self.next_action.model_dump(mode="json"),
                "retry_after_seconds": self.retry_after_seconds,
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
            "CACHE_FULL",
        }:
            return 4
        return 2
