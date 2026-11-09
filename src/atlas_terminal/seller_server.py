"""Isolated seller HTTP surface: no free research, imports or operator routes."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from atlas_terminal.errors import AtlasError, ErrorEnvelope
from atlas_terminal.models import PurchasePreflight, ReconcileRequest, ReconciliationResult
from atlas_terminal.payments.common import PaymentAdapter, origin, seller_pricing
from atlas_terminal.payments.intents import INTENT_ACK_HEADER, INTENT_HEADER
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.reconciliation import BaseSepoliaReconciler, SettlementReconciler
from atlas_terminal.seller import SellerOrders
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import ProductPurchase, ProductQuery, ProductQuote
from atlas_terminal.util import state_dir

QuoteId = Annotated[str, PathParam(pattern=r"^[0-9a-f]{32}$")]


def create_seller_app(
    *,
    directory: Path | None = None,
    payments: str = "off",
    base_origin: str = "http://127.0.0.1:8000",
    amount: str = "10000",
    pay_to: str | None = None,
    adapter: PaymentAdapter | None = None,
    reconciler: SettlementReconciler | None = None,
    max_source_age_seconds: int = 172800,
) -> FastAPI:
    if any(os.environ.get(key, "1") != "1" for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS")):
        raise AtlasError(
            "CONFIG_REQUIRED", "Seller requires one worker and a persistent state volume."
        )
    base_origin = origin(base_origin)
    parsed = urlsplit(base_origin)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if payments in {"off", "mock"} and not loopback:
        raise AtlasError("POLICY_DENIED", "Unpaid and fictional seller modes are loopback-only.")
    if payments == "mainnet" and (loopback or parsed.scheme != "https"):
        raise AtlasError(
            "CONFIG_REQUIRED", "Mainnet seller requires an explicit public HTTPS origin."
        )
    recipient = (
        "fixture:merchant"
        if payments in {"off", "mock"}
        else pay_to or os.environ.get("ATLAS_PAY_TO", "")
    )
    pricing = seller_pricing(payments, amount, recipient)
    if payments == "mock":
        adapter = adapter or MockAdapter()
    elif payments == "testnet":
        from atlas_terminal.payments.x402_testnet import TestnetAdapter

        if adapter is None:
            facilitator = os.environ.get("ATLAS_FACILITATOR_URL")
            if not facilitator:
                raise AtlasError("CONFIG_REQUIRED", "Configure an explicit testnet facilitator.")
            adapter = TestnetAdapter(facilitator)
        if reconciler is None and os.environ.get("ATLAS_TESTNET_RPC_URL"):
            reconciler = BaseSepoliaReconciler(os.environ["ATLAS_TESTNET_RPC_URL"])
    elif payments == "mainnet":
        from atlas_terminal.payments.x402_mainnet import BaseMainnetReconciler, MainnetAdapter

        if reconciler is None:
            rpc = os.environ.get("ATLAS_MAINNET_RPC_URL", "")
            if urlsplit(rpc).scheme != "https":
                raise AtlasError(
                    "CONFIG_REQUIRED", "Configure a private trusted HTTPS mainnet RPC."
                )
            reconciler = BaseMainnetReconciler(rpc)
        adapter = adapter or MainnetAdapter()
    library = ProductLibrary(directory or state_dir())
    orders = SellerOrders(
        library,
        pricing,
        base_origin=base_origin,
        adapter=adapter,
        reconciler=reconciler,
        max_source_age_seconds=max_source_age_seconds,
    )
    if payments in {"testnet", "mainnet"} and not any(
        row["available"] for row in orders.catalog()["series"]
    ):
        orders.close()
        raise AtlasError(
            "CONFIG_REQUIRED",
            "Prepare a non-fictional, in-date BLS capture before real-payment mode.",
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            orders.close()

    app = FastAPI(
        title="Atlas Terminal BLS seller API",
        version="seller-1.0",
        lifespan=lifespan,
        responses={
            status: {"model": ErrorEnvelope}
            for status in (400, 401, 403, 404, 409, 410, 413, 422, 429, 500, 502, 503)
        },
    )
    app.state.orders = orders

    @app.exception_handler(AtlasError)
    async def known_error(request: Request, exc: AtlasError) -> JSONResponse:
        return JSONResponse(exc.envelope(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            AtlasError("INVALID_INPUT", "Invalid seller query, path or fields.").envelope(),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unknown_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            AtlasError("INTERNAL_ERROR", "Seller operation stopped.").envelope(), status_code=500
        )

    slots, rate_lock = BoundedSemaphore(8), Lock()
    request_times: list[float] = []

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any) -> Any:
        if request.headers.get("host", "").lower() != parsed.netloc.lower() or (
            request.headers.get("origin") not in {None, base_origin}
        ):
            return JSONResponse(
                AtlasError("POLICY_DENIED", "Unrecognized Host or Origin.").envelope(),
                status_code=403,
            )
        with rate_lock:
            now = monotonic()
            request_times[:] = [value for value in request_times if value > now - 60]
            limited = len(request_times) >= 120
            if not limited:
                request_times.append(now)
        if limited or not slots.acquire(blocking=False):
            return JSONResponse(
                AtlasError("SOURCE_RATE_LIMITED", "Seller request limit reached.").envelope(),
                status_code=429,
                headers={"Retry-After": "60"},
            )
        try:
            if request.method == "POST":

                async def read_body() -> bytes:
                    body = bytearray()
                    async for part in request.stream():
                        body.extend(part)
                        if len(body) > 16384:
                            raise AtlasError(
                                "INVALID_INPUT", "Seller body exceeds 16 KiB.", status=413
                            )
                    return bytes(body)

                request._body = await asyncio.wait_for(read_body(), timeout=5)
            for key in ("Authorization", "PAYMENT-SIGNATURE", "Atlas-Mock-Payment"):
                if len(request.headers.get(key, "")) > (256 if key == "Authorization" else 16384):
                    raise AtlasError("INVALID_INPUT", "Seller header exceeds limit.", status=413)
            response = await call_next(request)
        except TimeoutError:
            response = JSONResponse(
                AtlasError("INVALID_INPUT", "Request body timed out.").envelope(), status_code=408
            )
        except AtlasError as exc:
            response = JSONResponse(exc.envelope(), status_code=exc.status)
        finally:
            slots.release()
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def bearer(request: Request) -> str | None:
        value = request.headers.get("authorization", "")
        return value[7:] if value.startswith("Bearer ") else None

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "seller", "payments": payments}

    @app.get("/v1/seller/catalog")
    def catalog() -> dict[str, Any]:
        return orders.catalog()

    @app.post("/v1/seller/quotes", response_model=ProductQuote)
    def quote(
        query: ProductQuery, intent_key: str | None = Header(None, alias=INTENT_HEADER)
    ) -> JSONResponse:
        result, token = orders.prepare_product(query, intent_key=intent_key)
        return JSONResponse(
            result.model_dump(mode="json"),
            headers={
                "Atlas-Recovery-Token": token,
                **({INTENT_ACK_HEADER: "v1"} if intent_key is not None else {}),
            },
        )

    @app.get("/v1/seller/quotes/{quote_id}/preflight", response_model=PurchasePreflight)
    def preflight(quote_id: QuoteId, request: Request) -> PurchasePreflight:
        return orders.preflight(quote_id, bearer(request))

    @app.get("/v1/seller/quotes/{quote_id}/status")
    def status(quote_id: QuoteId, request: Request) -> dict[str, Any]:
        return orders.status(quote_id, bearer(request))

    @app.post("/v1/seller/quotes/{quote_id}/reconcile", response_model=ReconciliationResult)
    def reconcile(
        quote_id: QuoteId, query: ReconcileRequest, request: Request
    ) -> ReconciliationResult:
        return orders.reconcile(quote_id, bearer(request), query.transaction)

    @app.get(
        "/v1/seller/quotes/{quote_id}/result",
        response_model=ProductPurchase,
        responses={
            202: {"description": "Settlement unknown; do not sign again"},
            402: {"description": "x402 payment terms, or fictional mock challenge"},
        },
    )
    def result(quote_id: QuoteId, request: Request) -> JSONResponse:
        header = orders.adapter.header if orders.adapter else "PAYMENT-SIGNATURE"
        body, code, headers = orders.result(
            quote_id, bearer=bearer(request), proof=request.headers.get(header)
        )
        return JSONResponse(
            body.model_dump(mode="json") if hasattr(body, "model_dump") else body,
            status_code=code,
            headers=headers,
        )

    return app
