import asyncio
import json

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.remote_mcp import IngressGuard, create_remote_mcp, validated_origin

TOKEN = "fictional-test-token-for-local-tests-only"
ROOT = "https://atlas.example"


class SDKTransport(httpx2.AsyncBaseTransport):
    """Bridge the pinned official SDK's httpx2 client to an offline ASGI transport."""

    def __init__(self, app):
        self.transport = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request):
        response = await self.transport.handle_async_request(
            httpx.Request(
                request.method,
                str(request.url),
                headers=request.headers.raw,
                content=await request.aread(),
            )
        )
        return httpx2.Response(
            response.status_code,
            headers=response.headers.raw,
            content=await response.aread(),
            request=request,
        )

    async def aclose(self):
        await self.transport.aclose()


async def test_official_streamable_client_presets_evidence_and_no_payment_tools(tmp_path):
    app = create_remote_mcp(provider="fixture", directory=tmp_path, public_origin=ROOT, token=TOKEN)
    async with (
        app.app.router.lifespan_context(app.app),
        httpx2.AsyncClient(
            transport=SDKTransport(app), headers={"Authorization": "Bearer " + TOKEN}
        ) as client,
        streamable_http_client(ROOT + "/mcp", http_client=client) as streams,
        ClientSession(*streams, read_timeout_seconds=15) as session,
    ):
        initialized = await session.initialize()
        assert initialized.server_info.version == "0.3.0"
        assert initialized.server_info.website_url
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "atlas_research_catalog",
            "atlas_get_research",
            "atlas_get_research_batch",
            "atlas_get_research_preset",
            "atlas_research_diagnostics",
            "atlas_get_evidence",
        }
        assert all(tool.annotations.read_only_hint for tool in tools)
        catalog = await session.call_tool("atlas_research_catalog", {"view": "compact"})
        assert len(catalog.structured_content["datasets"]) == 21
        assert catalog.structured_content["documentation"]["citations"].endswith("citations.md")
        preset = await session.call_tool(
            "atlas_get_research_preset",
            {
                "query": {
                    "preset": "fx_context",
                    "base_currency": "USD",
                    "quote_currency": "JPY",
                    "include": ["fx", "us_sofr"],
                }
            },
        )
        assert not preset.is_error
        assert preset.structured_content["batch"]["summary"]["successful"] == 2
        assert preset.structured_content["is_synthetic"]
        sources = preset.structured_content["batch"]["items"][0]["result"]["sources"]
        evidence_id = sources[0]["evidence_id"]
        evidence = await session.call_tool("atlas_get_evidence", {"evidence_id": evidence_id})
        assert not evidence.is_error
        assert evidence.structured_content["evidence"]["raw_sha256"]
        invalid = await session.call_tool(
            "atlas_get_research",
            {
                "query": {
                    "dataset": "fx_rates",
                    "base_currency": "USD",
                    "quote_currency": "JPY",
                    "origin": "https://untrusted.invalid",
                }
            },
        )
        assert invalid.is_error
        assert TOKEN not in invalid.model_dump_json()
    # A new process/service instance using the same volume can resolve saved provenance.
    with Atlas("fixture", directory=tmp_path) as atlas:
        assert atlas.get_evidence(evidence_id)["evidence_id"] == evidence_id
    assert not list(tmp_path.glob("*orders*"))


def test_remote_ingress_checks_before_tool_execution(tmp_path):
    app = create_remote_mcp(provider="fixture", directory=tmp_path, public_origin=ROOT, token=TOKEN)
    with TestClient(app, base_url=ROOT) as client:
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json()["is_synthetic"]
        assert client.post("/mcp", json={}).status_code == 401
        assert (
            client.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={}).status_code
            == 401
        )
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            client.post("/mcp", headers=headers | {"Host": "evil.invalid"}, json={}).status_code
            == 421
        )
        assert (
            client.post(
                "/mcp", headers=headers | {"Origin": "https://evil.invalid"}, json={}
            ).status_code
            == 403
        )
        oversized = client.post("/mcp", headers=headers, content=b"x" * 16385)
        assert oversized.status_code == 413
        assert oversized.headers["cache-control"] == "no-store"
        assert client.get("/v1/purchases", headers=headers).status_code == 404
        assert TOKEN not in json.dumps(health.json())
    assert not list(tmp_path.glob("*.sqlite3"))


def test_anonymous_requires_explicit_choice_and_has_global_quota(tmp_path):
    with pytest.raises(AtlasError):
        create_remote_mcp(directory=tmp_path)
    with pytest.raises(AtlasError):
        create_remote_mcp(directory=tmp_path, token=TOKEN, allow_anonymous=True)
    with pytest.raises(AtlasError):
        create_remote_mcp(directory=tmp_path, token="short")
    app = create_remote_mcp(
        provider="fixture",
        directory=tmp_path,
        public_origin=ROOT,
        allow_anonymous=True,
        requests_per_minute=1,
    )
    with TestClient(app, base_url=ROOT) as client:
        first = client.post("/mcp", json={})
        assert first.status_code != 401
        second = client.post("/mcp", json={})
        assert second.status_code == 429 and second.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/mcp",
        "https://*.example.com",
        "https://example.com?token=secret",
        "https://example.com#part",
        "https://example.com:70000",
        " https://example.com",
        "https://example.com\\evil",
    ],
)
def test_invalid_origins_fail_closed(value):
    with pytest.raises(AtlasError):
        validated_origin(value)


def test_remote_rejects_hosted_provider_and_multiple_workers(tmp_path, monkeypatch):
    with pytest.raises(AtlasError):
        create_remote_mcp(provider="hosted", directory=tmp_path, allow_anonymous=True)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(AtlasError):
        create_remote_mcp(provider="fixture", directory=tmp_path, allow_anonymous=True)


def test_normalize_default_ports_for_standard_client_host_headers():
    assert validated_origin("https://ATLAS.example:443/") == "https://atlas.example"
    assert validated_origin("http://127.0.0.1:80") == "http://127.0.0.1"


async def test_body_timeout_releases_ingress_capacity(monkeypatch):
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: original_timeout(0.01))
    executed = False

    async def application(scope, receive, send):
        nonlocal executed
        executed = True

    app = IngressGuard(
        application,
        public_origin=ROOT,
        allowed_origins=[],
        token=None,
        requests_per_minute=60,
        concurrency=1,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "scheme": "https",
        "headers": [(b"host", b"atlas.example")],
        "query_string": b"",
    }
    sent = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    assert not executed and not app.slots.locked()
    assert sent[0]["status"] == 408
