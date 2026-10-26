"""Single-process, loopback-by-default reference HTTP service."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from atlas_terminal import __version__
from atlas_terminal.client import Atlas, catalog
from atlas_terminal.errors import AtlasError, ErrorEnvelope
from atlas_terminal.models import (
    Company,
    FinancialQuery,
    PurchasePreflight,
    PurchaseResult,
    Quote,
    ReconcileRequest,
    ReconciliationResult,
    parse_query,
)
from atlas_terminal.payments.common import (
    ASSET,
    NETWORK,
    PaymentAdapter,
    make_pricing,
    origin,
    validate_testnet,
)
from atlas_terminal.payments.intents import INTENT_ACK_HEADER, INTENT_HEADER
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.payments.reconciliation import BaseSepoliaReconciler, SettlementReconciler
from atlas_terminal.research_batch import ResearchBatchQuery, ResearchBatchResult
from atlas_terminal.research_diagnostics import ResearchDiagnosticsQuery, ResearchDiagnosticsResult
from atlas_terminal.research_models import Dataset, ResearchQuery
from atlas_terminal.research_presets import ResearchPresetQuery, ResearchPresetResult
from atlas_terminal.research_views import EvidenceResult, ResearchResult
from atlas_terminal.risk_models import MAX_RISK_BYTES, RiskQuery, RiskResult
from atlas_terminal.util import state_dir


class AtlasAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        """Publish shared input models with document-resolvable component references."""
        document = super().openapi()
        schemas = document.setdefault("components", {}).setdefault("schemas", {})
        for model in (
            FinancialQuery,
            ResearchQuery,
            ResearchBatchQuery,
            ResearchPresetQuery,
            ResearchDiagnosticsQuery,
            RiskQuery,
        ):
            if model.__name__ not in schemas:
                schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
                schemas.update(schema.pop("$defs", {}))
                schemas[model.__name__] = schema
        return document


def create_app(
    *,
    provider: str = "sec",
    payments: str = "off",
    directory: Path | None = None,
    base_origin: str = "http://127.0.0.1:8000",
    adapter: PaymentAdapter | None = None,
    amount: str = "10000",
    pay_to: str | None = None,
    reconciler: SettlementReconciler | None = None,
    enable_risk: bool = False,
) -> FastAPI:
    if (
        os.environ.get("WEB_CONCURRENCY", "1") != "1"
        or os.environ.get("UVICORN_WORKERS", "1") != "1"
    ):
        raise AtlasError(
            "CONFIG_REQUIRED", "Reference API supports one worker and one state volume."
        )
    base_origin = origin(base_origin)
    if provider == "hosted":
        raise AtlasError("CONFIG_REQUIRED", "Reference API cannot proxy another hosted provider.")
    if payments == "mock" and urlsplit(base_origin).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise AtlasError("POLICY_DENIED", "Mock mode is restricted to loopback development.")
    recipient = pay_to or os.environ.get("ATLAS_PAY_TO") or "fixture:merchant"
    if payments == "testnet":
        validate_testnet(
            os.environ.get("ATLAS_TESTNET_NETWORK", NETWORK),
            os.environ.get("ATLAS_TESTNET_ASSET", ASSET),
            recipient,
        )
        if adapter is None:
            from atlas_terminal.payments.x402_testnet import TestnetAdapter

            facilitator = os.environ.get("ATLAS_FACILITATOR_URL")
            if not facilitator:
                raise AtlasError(
                    "CONFIG_REQUIRED", "Configure ATLAS_FACILITATOR_URL explicitly for testnet."
                )
            adapter = TestnetAdapter(facilitator)
    elif payments == "mock":
        adapter = adapter or MockAdapter()
        recipient = "fixture:merchant"
    elif payments != "off":
        raise AtlasError("CONFIG_REQUIRED", "Payment mode must be off, mock or testnet.")
    if reconciler is not None and payments != "testnet":
        raise AtlasError("CONFIG_REQUIRED", "Chain reconciliation is testnet-only.")
    if payments == "testnet" and reconciler is None:
        rpc_url = os.environ.get("ATLAS_TESTNET_RPC_URL")
        if rpc_url:
            reconciler = BaseSepoliaReconciler(rpc_url)
    orders = OrderService(
        Atlas(provider, directory=directory),
        (directory or state_dir()) / "server-orders.sqlite3",
        make_pricing(payments, amount, recipient),
        base_origin=base_origin,
        adapter=adapter,
        reconciler=reconciler,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        orders.close()

    app = AtlasAPI(
        title="Atlas Terminal reference API",
        version=__version__,
        lifespan=lifespan,
        responses={
            status: {"model": ErrorEnvelope}
            for status in (400, 401, 403, 404, 409, 410, 413, 422, 429, 500, 502, 503)
        },
    )
    app.state.orders = orders

    @app.exception_handler(AtlasError)
    async def domain_error(request: Request, exc: AtlasError) -> JSONResponse:
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)}
            if exc.retry_after_seconds is not None
            else None
        )
        return JSONResponse(exc.envelope(), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            AtlasError("INVALID_INPUT", "Request failed type or field validation.").envelope(),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            AtlasError("INTERNAL_ERROR", "Operation stopped due to an internal error.").envelope(),
            status_code=500,
        )

    @app.middleware("http")
    async def bounded_requests(request: Request, call_next: Any) -> Any:
        if request.method == "POST":
            chunks = []
            size = 0
            limit = MAX_RISK_BYTES if enable_risk and request.url.path == "/v1/risk" else 16384
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse(
                        AtlasError("INVALID_INPUT", "Request body exceeds limit.").envelope(),
                        status_code=413,
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def bearer(request: Request) -> str | None:
        value = request.headers.get("Authorization", "")
        return value[7:] if value.startswith("Bearer ") and len(value) <= 256 else None

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "schema_version": "0.1.0"}

    @app.get("/v1/catalog")
    def get_catalog() -> dict[str, Any]:
        return catalog() | {
            "pricing": orders.pricing.model_dump(mode="json"),
            "risk_enabled": enable_risk,
        }

    if enable_risk:

        @app.post(
            "/v1/risk",
            response_model=RiskResult,
            openapi_extra={
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/RiskQuery"}}
                    },
                    "required": True,
                }
            },
        )
        def risk(body: dict[str, Any]) -> dict[str, Any]:
            return orders.atlas.risk(body)

    @app.get("/v1/research/catalog")
    def get_research_catalog(
        dataset: Dataset | None = None,
        view: Literal["full", "compact"] = "full",
        search: str | None = None,
        country: str | None = None,
        frequency: Literal["daily", "weekly", "monthly", "quarterly", "annual"] | None = None,
        source_id: str | None = None,
        unit: str | None = None,
    ) -> dict[str, Any]:
        return orders.atlas.research_catalog(
            dataset,
            view=view,
            search=search,
            country=country,
            frequency=frequency,
            source_id=source_id,
            unit=unit,
        )

    @app.post(
        "/v1/research",
        response_model=ResearchResult,
        response_model_exclude_unset=True,
        openapi_extra={
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/ResearchQuery"}}
                },
                "required": True,
            }
        },
    )
    def get_research(body: dict[str, Any]) -> dict[str, Any]:
        return orders.atlas.get_research(body)

    @app.post(
        "/v1/research/batch",
        response_model=ResearchBatchResult,
        response_model_exclude_unset=True,
        openapi_extra={
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ResearchBatchQuery"}
                    }
                },
                "required": True,
            }
        },
    )
    def get_research_batch(body: dict[str, Any]) -> dict[str, Any]:
        return orders.atlas.get_research_batch(body)

    @app.post(
        "/v1/research/preset",
        response_model=ResearchPresetResult,
        response_model_exclude_unset=True,
        openapi_extra={
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ResearchPresetQuery"}
                    }
                },
                "required": True,
            }
        },
    )
    def get_research_preset(body: dict[str, Any]) -> dict[str, Any]:
        return orders.atlas.get_research_preset(body)

    @app.post(
        "/v1/research/diagnostics",
        response_model=ResearchDiagnosticsResult,
        openapi_extra={
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ResearchDiagnosticsQuery"}
                    }
                },
                "required": True,
            }
        },
    )
    def research_diagnostics(body: dict[str, Any]) -> dict[str, Any]:
        return orders.atlas.research_diagnostics(body)

    @app.get(
        "/v1/research/evidence/{evidence_id}",
        response_model=EvidenceResult,
        response_model_exclude_unset=True,
    )
    def get_evidence(evidence_id: str) -> dict[str, Any]:
        return orders.atlas.get_evidence(evidence_id)

    @app.get("/v1/companies/resolve", response_model=Company)
    def resolve_company(query: str = Query(min_length=1, max_length=100)) -> Company:
        return orders.atlas.resolve_company(query)

    @app.get("/v1/filings")
    def list_filings(
        cik: str = Query(pattern=r"^[0-9]{10}$"),
        form: str = "10-K",
        limit: int = Query(default=3, ge=1, le=100),
    ) -> dict[str, Any]:
        return orders.atlas.list_filings(cik, form, limit)

    @app.post(
        "/v1/quotes",
        response_model=Quote,
        openapi_extra={
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/FinancialQuery"}}
                },
                "required": True,
            }
        },
    )
    def quote_financials(
        body: dict[str, Any],
        intent_key: str | None = Header(
            default=None,
            alias=INTENT_HEADER,
            description=(
                "Private random replay capability, at least 32 characters. "
                "Never put a public task ID here."
            ),
        ),
    ) -> JSONResponse:
        quote, recovery = orders.prepare(parse_query(body), intent_key=intent_key)
        return JSONResponse(
            quote.model_dump(mode="json"),
            headers={
                "Atlas-Recovery-Token": recovery,
                **({INTENT_ACK_HEADER: "v1"} if intent_key is not None else {}),
            },
        )

    @app.get("/v1/quotes/{quote_id}/preflight", response_model=PurchasePreflight)
    def purchase_preflight(quote_id: str, request: Request) -> PurchasePreflight:
        """Check order readiness only; the caller's wallet enforces its own budget."""
        return orders.preflight(quote_id, bearer(request))

    @app.get("/v1/quotes/{quote_id}/status")
    def purchase_status(quote_id: str, request: Request) -> dict[str, Any]:
        return orders.status(quote_id, bearer(request))

    @app.post("/v1/quotes/{quote_id}/reconcile", response_model=ReconciliationResult)
    def reconcile_purchase(
        quote_id: str, request: Request, body: ReconcileRequest
    ) -> ReconciliationResult:
        return orders.reconcile(quote_id, bearer(request), body.transaction)

    @app.get(
        "/v1/quotes/{quote_id}/result",
        response_model=PurchaseResult,
        responses={
            202: {"description": "Settlement unknown; do not sign again"},
            402: {"description": "Official x402 challenge, or explicit mock challenge"},
            401: {"description": "Recovery capability or matching proof required"},
        },
    )
    def purchase_result(quote_id: str, request: Request) -> JSONResponse:
        header = orders.adapter.header if orders.adapter else "PAYMENT-SIGNATURE"
        result, status, headers = orders.result(
            quote_id, bearer=bearer(request), proof=request.headers.get(header)
        )
        return JSONResponse(
            result.model_dump(mode="json") if hasattr(result, "model_dump") else result,
            status_code=status,
            headers=headers,
        )

    return app
