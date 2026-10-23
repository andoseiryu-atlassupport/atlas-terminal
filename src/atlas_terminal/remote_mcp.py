"""Single-process research-only Streamable HTTP, with bounded public ingress."""

import asyncio
import hmac
import os
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas_terminal import __version__
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.mcp_server import create_mcp
from atlas_terminal.risk_models import MAX_RISK_BYTES


def validated_origin(value: str) -> str:
    """Require a literal origin; never accept paths, wildcards or credentials."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname or ""
        valid_host = host in {"localhost", "::1"} or bool(
            re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host)
        )
        valid = (
            value == value.strip()
            and value.isascii()
            and valid_host
            and not any(c in value for c in ("*", "\\", "\r", "\n", "\t", " "))
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (port is None or 1 <= port <= 65535)
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"})
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise AtlasError("CONFIG_REQUIRED", "Use an HTTPS origin or loopback development origin.")
    normalized_host = f"[{host}]" if ":" in host else host
    if port is not None and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        normalized_host += f":{port}"
    return f"{parsed.scheme}://{normalized_host}"


class IngressGuard:
    """Global quotas intentionally do not trust forwarded client identity headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        public_origin: str,
        allowed_origins: list[str],
        token: str | None,
        requests_per_minute: int,
        concurrency: int,
        max_body_bytes: int = 16384,
    ) -> None:
        self.app = app
        self.host = urlsplit(public_origin).netloc
        self.origins = {public_origin, *allowed_origins}
        self.token = token.encode() if token is not None else None
        self.requests_per_minute = requests_per_minute
        self.requests: deque[float] = deque()
        self.slots = asyncio.Semaphore(concurrency)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)

        async def reject(status: int, message: str) -> None:
            headers = {"Cache-Control": "no-store"}
            if status == 429:
                headers["Retry-After"] = "60"
            if status == 401:
                headers["WWW-Authenticate"] = 'Bearer realm="atlas-research"'
            await JSONResponse({"error": message}, status_code=status, headers=headers)(
                scope, receive, send
            )

        if len(request.headers.getlist("host")) != 1 or request.headers["host"] != self.host:
            await reject(421, "Unrecognized service host.")
            return
        origins = request.headers.getlist("origin")
        if len(origins) > 1 or (origins and origins[0] not in self.origins):
            await reject(403, "Origin is not allowed.")
            return
        if request.url.path == "/healthz" and request.method == "GET":
            await self.app(scope, receive, send)
            return
        if request.url.path != "/mcp":
            await reject(404, "Unknown endpoint.")
            return
        if self.token is not None:
            auth = request.headers.getlist("authorization")
            supplied = auth[0].encode() if len(auth) == 1 else b""
            if not hmac.compare_digest(supplied, b"Bearer " + self.token):
                await reject(401, "A configured bearer token is required.")
                return
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - 60:
            self.requests.popleft()
        if len(self.requests) >= self.requests_per_minute or self.slots.locked():
            await reject(429, "Service request capacity exceeded; retry later.")
            return
        self.requests.append(now)
        async with self.slots:
            # Bound actual bytes even when Content-Length is missing or incorrect.
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > self.max_body_bytes:
                            await reject(413, "Request body exceeds the configured limit.")
                            return
            except TimeoutError:
                await reject(408, "Request body timed out.")
                return
            delivered = False

            async def bounded_receive() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            async def private_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
                await send(message)

            await self.app(scope, bounded_receive, private_send)


def create_remote_mcp(
    *,
    provider: str = "public",
    directory: Path | None = None,
    public_origin: str = "http://127.0.0.1:8001",
    token: str | None = None,
    allow_anonymous: bool = False,
    allowed_origins: list[str] | None = None,
    requests_per_minute: int = 60,
    concurrency: int = 2,
    enable_risk: bool = False,
) -> ASGIApp:
    """Research tools and an optional authenticated risk extension; no payment handlers."""
    if provider not in {"public", "fixture"}:
        raise AtlasError("CONFIG_REQUIRED", "Remote MCP supports public or fixture research.")
    if token is not None and not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", token):
        raise AtlasError("CONFIG_REQUIRED", "Configure a private 32–256 character bearer token.")
    if token is None and not allow_anonymous:
        raise AtlasError(
            "CONFIG_REQUIRED", "Configure bearer authentication or opt into anonymous access."
        )
    if token is not None and allow_anonymous:
        raise AtlasError("CONFIG_REQUIRED", "Choose bearer authentication or anonymous access.")
    if enable_risk and token is None:
        raise AtlasError(
            "CONFIG_REQUIRED", "Risk imports and computation require bearer authentication."
        )
    if not 1 <= requests_per_minute <= 600 or not 1 <= concurrency <= 8:
        raise AtlasError("CONFIG_REQUIRED", "Choose bounded ingress limits.")
    if any(os.environ.get(name, "1") != "1" for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS")):
        raise AtlasError(
            "CONFIG_REQUIRED", "Remote MCP requires one worker and a persistent state volume."
        )
    public_origin = validated_origin(public_origin)
    origins = [validated_origin(value) for value in allowed_origins or []]
    atlas = Atlas(provider, directory=directory)
    server = create_mcp(provider, atlas_client=atlas, research_only=True, enable_risk=enable_risk)
    body_limit = MAX_RISK_BYTES + 2048 if enable_risk else 16384
    app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=body_limit,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(public_origin).netloc],
            allowed_origins=[public_origin, *origins],
        ),
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[Any]:
        try:
            async with original_lifespan(application) as state:
                yield state
        finally:
            atlas.close()

    app.router.lifespan_context = lifespan

    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "transport": "streamable-http",
                "research_only": True,
                "risk_enabled": enable_risk,
                "is_synthetic": provider == "fixture",
            },
            headers={"Cache-Control": "no-store"},
        )

    app.routes.append(Route("/healthz", health, methods=["GET"]))
    return IngressGuard(
        app,
        public_origin=public_origin,
        allowed_origins=origins,
        token=token,
        requests_per_minute=requests_per_minute,
        concurrency=concurrency,
        max_body_bytes=body_limit,
    )
