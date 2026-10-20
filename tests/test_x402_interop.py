"""Official Python/TypeScript HTTP clients and native MCP over offline transports."""

import json
import selectors
import subprocess
from pathlib import Path

import anyio
import httpx
import pytest
from eth_account import Account
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from test_official_x402 import ORIGIN, QUERY, RECIPIENT, FacilitatorFixture, OfficialAdapter
from x402 import x402Client
from x402.extensions.payment_identifier import (
    append_payment_identifier_to_extensions,
    generate_payment_id,
)
from x402.http.clients.httpx import x402_httpx_transport
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.mcp_server import create_mcp
from atlas_terminal.models import Quote
from atlas_terminal.payments.common import NETWORK
from atlas_terminal.payments.mcp_client import create_x402_mcp_session
from atlas_terminal.server import create_app


def app_fixture(tmp_path, *, unknown=False):
    fixture = FacilitatorFixture(unknown=unknown)
    adapter = OfficialAdapter("https://facilitator.atlas.invalid", facilitator=fixture.client)
    return fixture, create_app(
        provider="fixture",
        payments="testnet",
        directory=tmp_path,
        adapter=adapter,
        pay_to=RECIPIENT,
    )


def payment_client():
    client = x402Client()
    client.register(NETWORK, ExactEvmClientScheme(EthAccountSigner(Account.create())))
    calls = []

    async def identifier(context):
        calls.append(1)
        context.payment_required.extensions = append_payment_identifier_to_extensions(
            context.payment_required.extensions, generate_payment_id()
        )

    client.on_before_payment_creation(identifier)
    return client, calls


@pytest.mark.parametrize("unknown", [False, True])
async def test_official_python_http_transport_automatically_pays_once(tmp_path, unknown):
    fixture, app = app_fixture(tmp_path, unknown=unknown)
    client, signatures = payment_client()
    transport = x402_httpx_transport(client, transport=httpx.ASGITransport(app=app))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
        response = await http.post("/v1/quotes", json=QUERY.model_dump(mode="json"))
        quote = Quote.model_validate(response.json())
        paid = await http.get(quote.resource_path)
        assert paid.status_code == (202 if unknown else 200)
        if not unknown:
            assert paid.json()["receipt"]["result_sha256"] == quote.result_sha256
            assert paid.headers["PAYMENT-RESPONSE"]
        # A fresh no-capability probe cannot initiate another payment for this order.
        assert (await http.get(quote.resource_path)).status_code == 401
    assert len(signatures) == 1 and fixture.calls.count("/settle") == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_official_typescript_fetch_round_trip_without_network(tmp_path, unknown):
    fixture, app = app_fixture(tmp_path, unknown=unknown)
    driver = Path(__file__).parent / "interop/x402_fetch.mjs"
    with TestClient(app, base_url=ORIGIN) as http:
        response = http.post("/v1/quotes", json=QUERY.model_dump(mode="json"))
        quote = Quote.model_validate(response.json())
        proc = subprocess.Popen(
            ["node", str(driver)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            proc.stdin.write(json.dumps({"url": ORIGIN + quote.resource_path}) + "\n")
            proc.stdin.flush()
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                for _ in range(4):
                    assert ready.select(15), "TypeScript client did not respond"
                    message = json.loads(proc.stdout.readline())
                    if message["type"] == "done":
                        break
                    assert message["type"] == "request", "TypeScript client failed"
                    assert message["url"] == ORIGIN + quote.resource_path
                    reply = http.request(
                        message["method"], message["url"], headers=message["headers"]
                    )
                    proc.stdin.write(
                        json.dumps(
                            {
                                "status": reply.status_code,
                                "headers": dict(reply.headers),
                                "body": reply.json(),
                            }
                        )
                        + "\n"
                    )
                    proc.stdin.flush()
                else:
                    pytest.fail("Unexpected TypeScript retry loop")
            assert message["status"] == (202 if unknown else 200)
            assert message["requests"] == 2
            if not unknown:
                assert message["receipt"]["result_sha256"] == quote.result_sha256
                assert message["settlementHeader"]
            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("auto_payment", [False, True])
async def test_official_mcp_client_payment_metadata_and_no_resigning_on_retry(
    tmp_path, unknown, auto_payment
):
    fixture, app = app_fixture(tmp_path / "server", unknown=unknown)
    with TestClient(app, base_url=ORIGIN) as http:
        atlas = Atlas("fixture")
        atlas.provider = "hosted"
        atlas._hosted = HostedClient(ORIGIN, directory=tmp_path / "client", client=http)
        server = create_mcp("hosted", atlas_client=atlas)
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(
                    server._lowlevel_server.run,
                    *server_streams,
                    server._lowlevel_server.create_initialization_options(),
                )
                async with ClientSession(*client_streams, read_timeout_seconds=15) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    native = next(
                        tool for tool in tools.tools if tool.name == "atlas_purchase_x402"
                    )
                    assert set(native.input_schema["properties"]) == {"quote_id"}
                    quoted = await session.call_tool("atlas_quote", QUERY.model_dump(mode="json"))
                    quote = Quote.model_validate(quoted.structured_content)
                    client, signatures = payment_client()
                    paid_session = create_x402_mcp_session(
                        session, client, auto_payment=auto_payment
                    )
                    result = await paid_session.call_tool(
                        "atlas_purchase_x402", {"quote_id": quote.quote_id}
                    )
                    assert result.is_error == (unknown or not auto_payment)
                    if not auto_payment:
                        assert result.raw_result.structured_content["x402Version"] == 2
                    elif not unknown:
                        assert result.payment_response.success
                        assert (
                            result.raw_result.structured_content["receipt"]["result_sha256"]
                            == quote.result_sha256
                        )
                    else:
                        assert (
                            result.raw_result.structured_content["error"]["code"]
                            == "SETTLEMENT_UNKNOWN"
                        )
                    repeated = await paid_session.call_tool(
                        "atlas_purchase_x402", {"quote_id": quote.quote_id}
                    )
                    assert repeated.is_error == (unknown or not auto_payment)
                    assert len(signatures) == int(auto_payment)
                    assert fixture.calls.count("/settle") == int(auto_payment)
                    with atlas._hosted.db.connect() as conn:
                        claim = conn.execute(
                            "SELECT * FROM client_payment_claims WHERE quote_id=?",
                            (quote.quote_id,),
                        ).fetchone()
                    if auto_payment:
                        assert claim["kind"] == "external" and len(claim["proof_sha256"]) == 64
                    else:
                        assert claim is None
                    visible = json.dumps(result.raw_result.structured_content)
                    assert '"signature"' not in visible and '"recovery"' not in visible
                    rejected = await session.call_tool(
                        "atlas_purchase_x402",
                        {
                            "quote_id": quote.quote_id,
                            "payment": {"signature": "must-not-echo"},
                        },
                    )
                    assert rejected.is_error and "must-not-echo" not in json.dumps(
                        rejected.structured_content
                    )
                tasks.cancel_scope.cancel()


def test_external_claim_cannot_switch_to_the_local_signer_after_a_transport_failure(
    tmp_path, monkeypatch
):
    _, app = app_fixture(tmp_path / "server")
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(ORIGIN, directory=tmp_path / "client", client=http)
        quote = client.quote(QUERY)
        with client.db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO client_payment_claims VALUES (?,'external',?)",
                (quote.quote_id, "a" * 64),
            )
        with pytest.raises(AtlasError) as caught:
            client.purchase(quote.quote_id)
        assert caught.value.code == "SETTLEMENT_UNKNOWN"
        with pytest.raises(AtlasError) as caught:
            client.external_purchase(quote.quote_id)
        assert caught.value.code == "SETTLEMENT_UNKNOWN"
