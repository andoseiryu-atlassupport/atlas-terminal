"""Single-process, loopback-by-default reference HTTP service."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from atlas_terminal.client import Atlas, catalog
from atlas_terminal.errors import AtlasError
from atlas_terminal.models import Company, FinancialQuery, PurchaseResult, Quote, parse_query
from atlas_terminal.payments.common import (
    ASSET,
    NETWORK,
    PaymentAdapter,
    make_pricing,
    origin,
    validate_testnet,
)
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import state_dir


def create_app(
    *,
    provider: str = "sec",
    payments: str = "off",
    directory: Path | None = None,
    base_origin: str = "http://127.0.0.1:8000",
    adapter: PaymentAdapter | None = None,
    amount: str = "10000",
    pay_to: str | None = None,
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
    orders = OrderService(
        Atlas(provider),
        (directory or state_dir()) / "server-orders.sqlite3",
        make_pricing(payments, amount, recipient),
        base_origin=base_origin,
        adapter=adapter,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        orders.close()

    app = FastAPI(title="Atlas Terminal reference API", version="0.2.0", lifespan=lifespan)
    app.state.orders = orders

    @app.exception_handler(AtlasError)
    async def domain_error(request: Request, exc: AtlasError) -> JSONResponse:
        return JSONResponse(exc.envelope(), status_code=exc.status)

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
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
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
        return catalog() | {"pricing": orders.pricing.model_dump(mode="json")}

    @app.get("/v1/research/catalog")
    def get_research_catalog() -> dict[str, Any]:
        return orders.atlas.research_catalog()

    @app.post(
        "/v1/research",
        openapi_extra={
            "requestBody": {
                "content": {"application/json": {"schema": ResearchQuery.model_json_schema()}},
                "required": True,
            }
        },
    )
    def get_research(body: dict[str, Any]) -> dict[str, Any]:
        return orders.atlas.get_research(body)

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
                "content": {"application/json": {"schema": FinancialQuery.model_json_schema()}},
                "required": True,
            }
        },
    )
    def quote_financials(body: dict[str, Any]) -> JSONResponse:
        quote, recovery = orders.prepare(parse_query(body))
        return JSONResponse(
            quote.model_dump(mode="json"), headers={"Atlas-Recovery-Token": recovery}
        )

    @app.get("/v1/quotes/{quote_id}/status")
    def purchase_status(quote_id: str, request: Request) -> dict[str, Any]:
        return orders.status(quote_id, bearer(request))

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
