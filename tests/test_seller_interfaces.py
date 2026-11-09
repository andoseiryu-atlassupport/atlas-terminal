"""Seller SDK/CLI/MCP contracts with memory transports; no API credentials or transfers."""

import json
from contextlib import contextmanager

import anyio
import httpx
import pytest
from eth_account import Account
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from test_seller_mainnet import ORIGIN, RECIPIENT, SellerFacilitator, make_payload
from test_seller_products import simulated_capture
from typer.testing import CliRunner
from x402 import x402Client
from x402.extensions.payment_identifier import (
    append_payment_identifier_to_extensions,
    generate_payment_id,
)
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.schemas import PaymentRequired

from atlas_terminal.cli import app as cli_app
from atlas_terminal.errors import AtlasError
from atlas_terminal.payments.common import MAINNET_NETWORK
from atlas_terminal.payments.mcp_client import create_x402_mcp_session
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.x402_mainnet import MainnetAdapter
from atlas_terminal.seller_cli import readiness
from atlas_terminal.seller_client import SellerClient
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_mcp import create_seller_mcp
from atlas_terminal.seller_server import create_seller_app
from atlas_terminal.util import canonical


class NoRPC:
    def close(self):
        pass


def mainnet_app(tmp_path, *, unknown=False):
    facilitator = SellerFacilitator(unknown=unknown)
    ProductLibrary(tmp_path).publish("cpi", simulated_capture(), source_access="retained_capture")
    app = create_seller_app(
        directory=tmp_path,
        payments="mainnet",
        base_origin=ORIGIN,
        pay_to=RECIPIENT,
        adapter=MainnetAdapter(facilitator=facilitator.client),
        reconciler=NoRPC(),
    )
    return facilitator, app


def test_sdk_mock_quote_replay_recovery_and_no_secret_in_results(tmp_path):
    root = tmp_path / "server"
    ProductLibrary(root).refresh("cpi", fixture=True)
    origin = "http://127.0.0.1:8000"
    app = create_seller_app(directory=root, payments="mock")
    with TestClient(app, base_url=origin) as http:
        client = SellerClient(origin, directory=tmp_path / "client", client=http)
        quote = client.quote({"series": "cpi"}, intent_id="example")
        assert client.quote({"series": "CUUR0000SA0"}, intent_id="example") == quote
        with pytest.raises(AtlasError) as exc:
            client.quote({"series": "payrolls"}, intent_id="example")
        assert exc.value.code == "INTENT_CONFLICT"
        assert client.preflight(quote.quote_id).decision == "ready"
        assert client.status(quote.quote_id)["state"] == "QUOTED"
        token = client.saved(quote.quote_id)[1]
        assert token not in canonical(quote).decode()
        response = http.get(
            quote.resource_path, headers={"Atlas-Mock-Payment": MockAdapter.sign(quote, origin)}
        )
        recovered = client.recover(quote.quote_id)
        assert recovered.model_dump(mode="json") == response.json()
        assert client.preflight(quote.quote_id).decision == "recover"
        assert token not in canonical(recovered).decode()


def test_sdk_lost_quote_response_replays_original_intent_after_restart(tmp_path):
    origin = "http://127.0.0.1:8000"
    ProductLibrary(tmp_path / "server").refresh("cpi", fixture=True)
    with TestClient(
        create_seller_app(directory=tmp_path / "server", payments="mock"), base_url=origin
    ) as http:

        class LoseResponse:
            @contextmanager
            def stream(self, *args, **kwargs):
                with http.stream(*args, **kwargs):
                    raise httpx.ReadTimeout("lost after server saved the quote")
                yield  # pragma: no cover

        client = SellerClient(origin, directory=tmp_path / "client", client=LoseResponse())
        with pytest.raises(AtlasError):
            client.quote({"series": "cpi"}, intent_id="same-task")
        restarted = SellerClient(origin, directory=tmp_path / "client", client=http)
        quote = restarted.quote({"series": "cpi"}, intent_id="same-task")
        assert restarted.quote({"series": "cpi"}, intent_id="same-task") == quote
        with http.app.state.orders.db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_sdk_external_payment_and_unknown_claim_survive_restart(tmp_path, unknown):
    fixture, app = mainnet_app(tmp_path / "server", unknown=unknown)
    with TestClient(app, base_url=ORIGIN) as http:
        client = SellerClient(ORIGIN, directory=tmp_path / "client", client=http)
        quote = client.quote({"series": "cpi"}, intent_id="one-purchase")
        challenge, receipt, required = client.external_purchase(quote.quote_id)
        assert required and receipt is None
        payload = make_payload(PaymentRequired.model_validate(challenge))
        if unknown:
            with pytest.raises(AtlasError) as exc:
                client.external_purchase(
                    quote.quote_id, payload.model_dump(mode="json", by_alias=True)
                )
            assert exc.value.code == "SETTLEMENT_UNKNOWN"
        else:
            result, receipt, required = client.external_purchase(
                quote.quote_id, payload.model_dump(mode="json", by_alias=True)
            )
            assert not required and receipt["network"] == MAINNET_NETWORK
            assert result["receipt"]["quote_id"] == quote.quote_id
        reopened = SellerClient(ORIGIN, directory=tmp_path / "client", client=http)
        if unknown:
            with pytest.raises(AtlasError) as exc:
                reopened.external_purchase(quote.quote_id)
            assert exc.value.code == "SETTLEMENT_UNKNOWN"
        else:
            assert reopened.external_purchase(quote.quote_id)[0] == result
        assert fixture.calls.count("/settle") == 1
        with reopened.db.connect() as conn:
            row = conn.execute("SELECT * FROM seller_quotes").fetchone()
            assert row["proof_hash"]
            assert payload.payload["signature"] not in str(dict(row))


def test_purchased_data_and_receipts_are_bound_to_saved_quote(tmp_path):
    origin = "http://127.0.0.1:8000"
    ProductLibrary(tmp_path / "server").refresh("cpi", fixture=True)
    with TestClient(
        create_seller_app(directory=tmp_path / "server", payments="mock"), base_url=origin
    ) as http:
        client = SellerClient(origin, directory=tmp_path / "client", client=http)
        quote = client.quote({"series": "cpi"}, intent_id="immutable")
        paid = http.get(
            quote.resource_path, headers={"Atlas-Mock-Payment": MockAdapter.sign(quote, origin)}
        )
        original = paid.json()
        for section, key, value in [
            ("receipt", "amount_atomic", "1"),
            ("receipt", "simulated", False),
            ("data", "captured_at", "2000-01-01T00:00:00Z"),
        ]:
            changed = json.loads(json.dumps(original))
            changed[section][key] = value
            with pytest.raises(AtlasError) as exc:
                client.validate_purchase(httpx.Response(200, json=changed), quote)
            assert exc.value.code == "QUOTE_MISMATCH"


@pytest.mark.parametrize("unknown", [False, True])
async def test_official_mcp_external_wallet_buys_and_never_signs_again(tmp_path, unknown):
    fixture, app = mainnet_app(tmp_path / "server", unknown=unknown)
    with TestClient(app, base_url=ORIGIN) as http:
        client = SellerClient(ORIGIN, directory=tmp_path / "client", client=http)
        server = create_seller_mcp(client)
        wallet = x402Client()
        wallet.register(MAINNET_NETWORK, ExactEvmClientScheme(EthAccountSigner(Account.create())))
        signatures = []

        async def identify(context):
            signatures.append(1)
            context.payment_required.extensions = append_payment_identifier_to_extensions(
                context.payment_required.extensions, generate_payment_id()
            )

        wallet.on_before_payment_creation(identify)
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
                    purchase = next(t for t in tools.tools if t.name == "atlas_seller_purchase")
                    assert set(purchase.input_schema["properties"]) == {"quote_id"}
                    assert not any(t.name == "atlas_get_research" for t in tools.tools)
                    quoted = await session.call_tool(
                        "atlas_seller_quote", {"series": "cpi", "intent_id": "mcp-example"}
                    )
                    assert not quoted.is_error
                    quote_id = quoted.structured_content["quote_id"]
                    paid_session = create_x402_mcp_session(session, wallet, auto_payment=True)
                    result = await paid_session.call_tool(
                        "atlas_seller_purchase", {"quote_id": quote_id}
                    )
                    assert result.is_error == unknown
                    again = await paid_session.call_tool(
                        "atlas_seller_purchase", {"quote_id": quote_id}
                    )
                    assert again.is_error == unknown
                    assert len(signatures) == 1 and fixture.calls.count("/settle") == 1
                    token = client.saved(quote_id)[1]
                    assert token not in str(result.raw_result)
                tasks.cancel_scope.cancel()


def test_cli_fixture_publish_and_doctor_do_not_touch_public_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path))
    for name in (
        "ATLAS_PUBLIC_ORIGIN",
        "ATLAS_PAY_TO",
        "CDP_API_KEY_ID",
        "CDP_API_KEY_SECRET",
        "ATLAS_MAINNET_RPC_URL",
        "ATLAS_SELLER_PAYMENTS",
    ):
        monkeypatch.delenv(name, raising=False)
    runner = CliRunner()
    result = runner.invoke(cli_app, ["seller", "refresh", "cpi", "--fixture"])
    assert result.exit_code == 0 and json.loads(result.stdout)["is_synthetic"]
    assert runner.invoke(cli_app, ["seller", "refresh", "cpi"]).exit_code != 0
    monkeypatch.setenv("CDP_API_KEY_SECRET", "private-test-sentinel")
    result = runner.invoke(cli_app, ["seller", "doctor"])
    assert result.exit_code == 0 and not json.loads(result.stdout)["ready"]
    assert "private-test-sentinel" not in result.stdout
    assert not readiness()["real_transfer_verified"]
