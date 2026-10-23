"""Payment planning stays non-spending; task replay survives ambiguous quote responses."""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from test_payments import ORIGIN, QUERY, make_service, policy
from typer.testing import CliRunner

from atlas_terminal.cli import app as cli_app
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.mcp_server import create_mcp
from atlas_terminal.payments.common import ASSET, NETWORK, Settlement, make_pricing
from atlas_terminal.payments.intents import INTENT_ACK_HEADER, INTENT_HEADER
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.policy import BudgetLedger
from atlas_terminal.payments.x402_testnet import TestnetSigner as WalletSigner
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, utcnow


@pytest.fixture
def planning(tmp_path):
    path = tmp_path / "policy.json"
    path.write_bytes(canonical(policy()))
    app = create_app(provider="fixture", payments="mock", directory=tmp_path / "server")
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(ORIGIN, directory=tmp_path / "client", policy_path=path, client=http)
        yield client, app.state.orders, http, path


def forbid(*args, **kwargs):
    raise AssertionError("Planning must not sign, reserve, settle or read upstream data")


def test_intent_replay_keeps_original_snapshot_price_and_capability(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    key = secrets.token_urlsafe(32)
    quote, token = service.prepare(QUERY, intent_key=key)
    monkeypatch.setattr(service.atlas, "get_financials", forbid)
    service.pricing = make_pricing("mock", "20000")
    assert service.prepare(QUERY, intent_key=key) == (quote, token)
    with pytest.raises(AtlasError, match="different query") as error:
        service.prepare(QUERY.model_copy(update={"count": 1}), intent_key=key)
    assert error.value.code == "INTENT_CONFLICT"
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 1
        private_rows = str([tuple(row) for row in conn.execute("SELECT * FROM purchase_intents")])
    assert key not in private_rows and token not in private_rows
    assert token.encode() not in service.db.path.read_bytes()


def test_concurrent_intent_preparation_publishes_one_order(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    result = service.atlas.get_financials(QUERY)
    barrier = Barrier(2)

    def fetch(query):
        barrier.wait(timeout=10)
        return result.model_copy(update={"retrieved_at": utcnow().isoformat()})

    monkeypatch.setattr(service.atlas, "get_financials", fetch)
    key = secrets.token_urlsafe(32)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.prepare, QUERY, intent_key=key) for _ in range(2)]
        first, second = [f.result() for f in futures]
    assert first == second
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM result_snapshots").fetchone()[0] == 1


def test_intent_tombstone_survives_quote_expiry_and_pruning(tmp_path):
    now = [utcnow()]
    service = make_service(tmp_path, clock=lambda: now[0])
    key = secrets.token_urlsafe(32)
    quote, token = service.prepare(QUERY, intent_key=key)
    now[0] += timedelta(minutes=6)
    assert service.prepare(QUERY, intent_key=key) == (quote, token)
    assert service.preflight(quote.quote_id, token).code == "QUOTE_EXPIRED"
    service.prepare(QUERY)  # normal maintenance removes the expired quote, never the intent
    with pytest.raises(AtlasError) as error:
        service.prepare(QUERY, intent_key=key)
    assert error.value.code == "INTENT_RETIRED"
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM purchase_intents").fetchone()[0] == 1


def test_intent_capacity_does_not_evict_existing_authority(tmp_path):
    service = make_service(tmp_path, max_intents=1)
    key = secrets.token_urlsafe(32)
    first = service.prepare(QUERY, intent_key=key)
    with pytest.raises(AtlasError) as error:
        service.prepare(QUERY, intent_key=secrets.token_urlsafe(32))
    assert error.value.code == "CACHE_FULL"
    assert service.prepare(QUERY, intent_key=key) == first


def test_response_loss_and_client_restart_recover_same_quote(planning, tmp_path, monkeypatch):
    client, service, http, path = planning
    request = client.request

    def lost_response(*args, **kwargs):
        request(*args, **kwargs)
        raise AtlasError("SOURCE_UNAVAILABLE", "Response lost after server commit")

    monkeypatch.setattr(client, "request", lost_response)
    with pytest.raises(AtlasError):
        client.quote(QUERY, intent_id="company-review:1")
    restarted = HostedClient(ORIGIN, directory=tmp_path / "client", policy_path=path, client=http)
    quote = restarted.quote(QUERY, intent_id="company-review:1")
    assert restarted.quote(QUERY, intent_id="company-review:1") == quote
    assert restarted.purchase(quote.quote_id) == restarted.purchase(quote.quote_id)
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 1
    assert service.adapter.settlements == 1
    monkeypatch.setattr(restarted, "request", forbid)
    with pytest.raises(AtlasError) as error:
        restarted.quote(QUERY.model_copy(update={"count": 1}), intent_id="company-review:1")
    assert error.value.code == "INTENT_CONFLICT"


def test_client_intents_are_scoped_to_private_state_and_origin(planning, tmp_path):
    client, service, http, _ = planning
    first = client.quote(QUERY, intent_id="task-1")
    other = HostedClient(ORIGIN, directory=tmp_path / "other-client", client=http)
    second = other.quote(QUERY, intent_id="task-1")
    assert first.quote_id != second.quote_id
    with client.db.connect() as conn:
        first_key = conn.execute("SELECT capability FROM client_purchase_intents").fetchone()[0]
    with other.db.connect() as conn:
        second_key = conn.execute("SELECT capability FROM client_purchase_intents").fetchone()[0]
    assert first_key != second_key
    assert (
        http.get(
            f"/v1/quotes/{first.quote_id}/preflight", headers={"Authorization": "Bearer task-1"}
        ).status_code
        == 401
    )
    assert service.adapter.settlements == 0
    second_origin = "http://127.0.0.1:8002"
    second_app = create_app(
        provider="fixture", base_origin=second_origin, directory=tmp_path / "second-server"
    )
    with TestClient(second_app, base_url=second_origin) as second_http:
        separate = HostedClient(second_origin, directory=tmp_path / "client", client=second_http)
        third = separate.quote(QUERY, intent_id="task-1")
        assert third.quote_id not in {first.quote_id, second.quote_id}


def test_quote_save_failure_rolls_back_client_binding_and_recovers(planning, monkeypatch):
    client, service, _, _ = planning
    connect = client.db.connect
    writes = 0

    @contextmanager
    def fail_save(*, write=False):
        nonlocal writes
        if write:
            writes += 1
        with connect(write=write) as conn:
            yield conn
            if write and writes == 2:
                raise AtlasError("LEDGER_UNAVAILABLE", "Simulated client commit failure")

    monkeypatch.setattr(client.db, "connect", fail_save)
    with pytest.raises(AtlasError):
        client.quote(QUERY, intent_id="task-1")
    with connect() as conn:
        assert conn.execute("SELECT quote_id FROM client_purchase_intents").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM saved_quotes").fetchone()[0] == 0
    monkeypatch.setattr(client.db, "connect", connect)
    quote = client.quote(QUERY, intent_id="task-1")
    assert client.preflight(quote.quote_id).decision == "ready"
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 1


def test_concurrent_clients_share_task_binding_and_purchase_once(planning, tmp_path):
    client, service, http, path = planning
    second = HostedClient(ORIGIN, directory=tmp_path / "client", policy_path=path, client=http)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(c.quote, QUERY, intent_id="task-1") for c in (client, second)]
        first_quote, second_quote = [f.result() for f in futures]
    assert first_quote == second_quote
    assert client.purchase(first_quote.quote_id) == second.purchase(second_quote.quote_id)
    assert service.adapter.settlements == 1


@pytest.mark.parametrize(
    "state,decision,code",
    [
        ("FAILED", "blocked", "PAYMENT_FAILED"),
        ("SETTLEMENT_SUBMITTED", "wait", "SETTLEMENT_UNKNOWN"),
        ("PAYMENT_VERIFIED", "wait", "SETTLEMENT_UNKNOWN"),
    ],
)
def test_order_preflight_reports_non_purchasable_states(planning, state, decision, code):
    client, service, _, _ = planning
    quote = client.quote(QUERY)
    with service.db.connect(write=True) as conn:
        conn.execute("UPDATE quotes SET state=? WHERE id=?", (state, quote.quote_id))
    result = client.preflight(quote.quote_id)
    assert (result.decision, result.code) == (decision, code)
    assert client.budgets.get(quote.quote_id) is None


def test_order_preflight_rejects_corrupt_artifact_and_ends_retention(planning):
    client, service, _, _ = planning
    first = client.quote(QUERY)
    client.purchase(first.quote_id)
    with service.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE quotes SET retained_until='2000-01-01T00:00:00Z' WHERE id=?", (first.quote_id,)
        )
    assert client.preflight(first.quote_id).code == "RESULT_EXPIRED"
    second = client.quote(QUERY)
    with service.db.connect(write=True) as conn:
        conn.execute("UPDATE result_snapshots SET body=?", (b"corrupt",))
    with pytest.raises(AtlasError) as error:
        client.preflight(second.quote_id)
    assert error.value.code == "LEDGER_UNAVAILABLE"


def test_external_claim_does_not_become_local_permission(planning):
    client, service, _, _ = planning
    quote = client.quote(QUERY)
    with client.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO client_payment_claims VALUES (?,'external',NULL)", (quote.quote_id,)
        )
    result = client.preflight(quote.quote_id)
    assert result.decision == "wait" and result.code == "PURCHASE_ALREADY_STARTED"
    assert result.budget is None and service.adapter.settlements == 0


@pytest.mark.parametrize("value", ["", "bad/key", "x" * 129, "日本語", "token\nvalue"])
def test_invalid_task_ids_rejected_before_request(planning, monkeypatch, value):
    client, _, _, _ = planning
    monkeypatch.setattr(client, "request", forbid)
    with pytest.raises(AtlasError) as error:
        client.quote(QUERY, intent_id=value)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("value", ["", "task-1", "x" * 129, "a/" * 20])
def test_http_intent_capability_validation_and_non_disclosure(planning, value):
    _, service, http, _ = planning
    response = http.post(
        "/v1/quotes", json=QUERY.model_dump(mode="json"), headers={INTENT_HEADER: value}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 0


def test_old_server_missing_intent_ack_stops_before_purchase(planning, monkeypatch):
    client, service, _, _ = planning
    request = client.request

    def old_server(*args, **kwargs):
        response = request(*args, **kwargs)
        response.headers.pop(INTENT_ACK_HEADER, None)
        return response

    monkeypatch.setattr(client, "request", old_server)
    with pytest.raises(AtlasError) as error:
        client.quote(QUERY, intent_id="task-1")
    assert error.value.code == "QUOTE_MISMATCH"
    with client.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM saved_quotes").fetchone()[0] == 0
    assert service.adapter.settlements == 0


def test_preflight_is_non_spending_and_http_exposes_no_client_budget(planning, monkeypatch):
    client, service, http, _ = planning
    quote = client.quote(QUERY, intent_id="task-1")
    monkeypatch.setattr(MockAdapter, "sign", forbid)
    monkeypatch.setattr(service.adapter, "settle", forbid)
    monkeypatch.setattr(service.atlas, "get_financials", forbid)
    monkeypatch.setattr(client.budgets, "reserve", forbid)
    first = client.budget_status()
    result = client.preflight(quote.quote_id)
    assert result.advisory and result.decision == "ready" and result.scope == "local_wallet"
    assert result.budget.day.remaining_atomic == "20000"
    assert result.budget.day.spent_atomic == result.budget.day.reserved_atomic == "0"
    assert client.budgets.get(quote.quote_id) is None
    assert client.budget_status().day == first.day
    assert client.preflight(quote.quote_id, max_cost="9999").code == "BUDGET_EXCEEDED"
    _, token = client.saved(quote.quote_id)
    url = f"/v1/quotes/{quote.quote_id}/preflight"
    assert http.get(url).status_code == 401
    raw = http.get(url, headers={"Authorization": "Bearer " + token})
    assert raw.json()["budget"] is None and raw.json()["scope"] == "order"
    assert raw.headers["Cache-Control"] == "no-store"
    assert token not in raw.text
    with client.db.connect() as conn:
        key = conn.execute("SELECT capability FROM client_purchase_intents").fetchone()[0]
    assert key not in result.model_dump_json() and token not in result.model_dump_json()


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"enabled": False}, "PAYMENTS_DISABLED"),
        ({"allowed_origins": ["https://other.invalid"]}, "POLICY_DENIED"),
        ({"pay_to": "fixture:other"}, "POLICY_DENIED"),
        ({"per_purchase_atomic": "9999"}, "BUDGET_EXCEEDED"),
        ({"per_session_atomic": "9999"}, "BUDGET_EXCEEDED"),
        ({"per_day_atomic": "9999"}, "BUDGET_EXCEEDED"),
    ],
)
def test_preflight_reports_policy_denials_without_reservation(planning, changes, code):
    client, service, _, path = planning
    quote = client.quote(QUERY)
    path.write_bytes(canonical(policy(**changes)))
    result = client.preflight(quote.quote_id)
    assert result.decision == "blocked" and result.code == code
    assert client.budgets.get(quote.quote_id) is None and service.adapter.settlements == 0


def test_preflight_does_not_reserve_or_bypass_changed_budget(planning):
    client, service, _, path = planning
    path.write_bytes(canonical(policy(per_day_atomic="10000")))
    first, second = [client.quote(QUERY, intent_id=f"task-{i}") for i in range(2)]
    assert client.preflight(first.quote_id).decision == "ready"
    assert client.preflight(second.quote_id).decision == "ready"
    client.purchase(first.quote_id)
    assert client.budget_status().day.spent_atomic == "10000"
    assert client.preflight(second.quote_id).code == "BUDGET_EXCEEDED"
    with pytest.raises(AtlasError) as error:
        client.purchase(second.quote_id)
    assert error.value.code == "BUDGET_EXCEEDED"
    assert service.adapter.settlements == 1
    assert client.preflight(first.quote_id).decision == "recover"


@pytest.mark.parametrize("after_server", [False, True])
def test_preflight_waits_after_ambiguous_submission(planning, monkeypatch, after_server):
    client, service, _, _ = planning
    quote = client.quote(QUERY, intent_id="task-1")
    request = client.request
    if after_server:
        monkeypatch.setattr(
            service.adapter, "settle", lambda *args: Settlement("SETTLEMENT_UNKNOWN")
        )

    def lose_payment(method, path, **kwargs):
        if "Atlas-Mock-Payment" in kwargs.get("headers", {}) and not after_server:
            raise AtlasError("SOURCE_UNAVAILABLE", "Response lost")
        return request(method, path, **kwargs)

    monkeypatch.setattr(client, "request", lose_payment)
    with pytest.raises(AtlasError):
        client.purchase(quote.quote_id)
    monkeypatch.setattr(MockAdapter, "sign", forbid)
    assert client.quote(QUERY, intent_id="task-1") == quote
    assert client.preflight(quote.quote_id).decision == "wait"
    assert client.budget_status().day.reserved_atomic == "10000"


def test_budget_windows_match_reservations_across_days_sessions_and_owners(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    limits = policy(per_session_atomic="50000", per_day_atomic="50000")
    now = utcnow()
    monkeypatch.setattr("atlas_terminal.payments.policy.utcnow", lambda: now)
    quotes = [service.prepare(QUERY)[0] for _ in range(4)]
    for index, quote in enumerate(quotes):
        ledger.reserve(
            quote,
            limits,
            ORIGIN,
            "owner" if index < 3 else "other",
            "current" if index != 2 else "other-session",
        )
    ledger.state(quotes[0].quote_id, "SPENT")
    now += timedelta(days=1)
    summary = ledger.summary(limits, "OWNER", "current")
    assert summary.day.spent_atomic == "0" and summary.day.reserved_atomic == "20000"
    assert summary.session.spent_atomic == "10000" and summary.session.reserved_atomic == "10000"
    ledger.state(quotes[1].quote_id, "FAILED")
    assert ledger.summary(limits, "owner", "current").day.reserved_atomic == "10000"
    ledger.state(quotes[2].quote_id, "SPENT")
    final = ledger.summary(limits, "owner", "current")
    assert final.day.spent_atomic == "10000" and final.day.reserved_atomic == "0"


def test_testnet_budget_uses_only_public_address_and_never_loads_signer(planning, monkeypatch):
    client, _, _, path = planning
    path.write_bytes(
        canonical(policy(mode="testnet", network=NETWORK, asset=ASSET, pay_to="0x" + "1" * 40))
    )
    monkeypatch.setenv("ATLAS_TESTNET_PRIVATE_KEY", "SECRET_SENTINEL_INVALID_KEY")
    monkeypatch.setattr(WalletSigner, "__init__", forbid)
    monkeypatch.setattr(client, "request", forbid)
    with pytest.raises(AtlasError) as error:
        client.budget_status()
    assert error.value.code == "WALLET_NOT_CONFIGURED"
    client.payer_address = "0x" + "2" * 40
    status = client.budget_status()
    assert status.network == NETWORK and status.day.remaining_atomic == "20000"
    assert client.payer_address not in status.model_dump_json()
    assert "SECRET_SENTINEL" not in status.model_dump_json()


def test_off_preflight_needs_no_wallet_or_policy(tmp_path):
    app = create_app(provider="fixture", directory=tmp_path / "server")
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(ORIGIN, directory=tmp_path / "client", client=http)
        quote = client.quote(QUERY, intent_id="free-task")
        result = client.preflight(quote.quote_id)
        assert result.decision == "ready" and result.code == "PAYMENT_NOT_REQUIRED"
        assert result.budget is None and client.budgets.get(quote.quote_id) is None


def test_purchase_cannot_use_a_signer_different_from_inspected_payer(planning, monkeypatch):
    client, service, _, path = planning
    recipient = "0x" + "1" * 40
    service.pricing = make_pricing("testnet", pay_to=recipient)
    path.write_bytes(
        canonical(policy(mode="testnet", network=NETWORK, asset=ASSET, pay_to=recipient))
    )
    client.payer_address = "0x" + "2" * 40
    quote = client.quote(QUERY)
    assert client.preflight(quote.quote_id).decision == "ready"
    monkeypatch.setattr(WalletSigner, "__init__", lambda self: None)
    monkeypatch.setattr(WalletSigner, "address", property(lambda self: "0x" + "3" * 40))
    monkeypatch.setattr(WalletSigner, "sign", forbid)
    with pytest.raises(AtlasError) as error:
        client.purchase(quote.quote_id)
    assert error.value.code == "POLICY_DENIED"
    assert client.budgets.get(quote.quote_id) is None and service.adapter.settlements == 0


def test_preflight_rejects_tampered_server_quote(planning, monkeypatch):
    client, _, _, _ = planning
    quote = client.quote(QUERY)
    request = client.request

    def tamper(*args, **kwargs):
        response = request(*args, **kwargs)
        body = response.json()
        body["pricing"]["amount_atomic"] = "1"
        return httpx.Response(200, json=body)

    monkeypatch.setattr(client, "request", tamper)
    with pytest.raises(AtlasError) as error:
        client.preflight(quote.quote_id)
    assert error.value.code == "QUOTE_MISMATCH"


def test_cli_and_sdk_planning_share_contracts(planning, monkeypatch):
    client, service, _, _ = planning
    atlas = Atlas("fixture")
    atlas._hosted = client
    monkeypatch.setattr(atlas, "close", lambda: None)
    monkeypatch.setattr("atlas_terminal.cli.Atlas", lambda *args: atlas)
    runner = CliRunner()
    quoted = runner.invoke(
        cli_app, ["quote", "financials", "ATLASDEMO", "--intent-id", "cli-task", "--json"]
    )
    assert quoted.exit_code == 0
    quote_id = json.loads(quoted.stdout)["quote_id"]
    assert atlas.quote(QUERY, intent_id="cli-task").quote_id == quote_id
    budget = runner.invoke(cli_app, ["budget-status", "--json"])
    assert budget.exit_code == 0 and json.loads(budget.stdout)["day"]["remaining_atomic"] == "20000"
    checked = runner.invoke(
        cli_app, ["purchase-preflight", quote_id, "--max-cost", "9999", "--json"]
    )
    assert checked.exit_code == 0 and json.loads(checked.stdout)["code"] == "BUDGET_EXCEEDED"
    assert service.adapter.settlements == 0


async def test_mcp_planning_and_intent_replay_keep_secrets_out_of_tools(planning):
    client, service, _, _ = planning
    atlas = Atlas("fixture")
    atlas._hosted = client
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
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                for name in ("atlas_budget_status", "atlas_purchase_preflight"):
                    assert tools[name].annotations.read_only_hint and tools[name].output_schema
                args = QUERY.model_dump(mode="json") | {"intent_id": "mcp-task"}
                first = await session.call_tool("atlas_quote", args)
                second = await session.call_tool("atlas_quote", args)
                assert not first.is_error and first.structured_content == second.structured_content
                quote_id = first.structured_content["quote_id"]
                checked = await session.call_tool(
                    "atlas_purchase_preflight", {"quote_id": quote_id, "max_cost": "10000"}
                )
                assert not checked.is_error and checked.structured_content["decision"] == "ready"
                Draft202012Validator(tools["atlas_purchase_preflight"].output_schema).validate(
                    checked.structured_content
                )
                budget = await session.call_tool("atlas_budget_status", {})
                assert not budget.is_error
                Draft202012Validator(tools["atlas_budget_status"].output_schema).validate(
                    budget.structured_content
                )
                for name, extra in [
                    ("atlas_budget_status", {"payer_address": "SECRET_SENTINEL"}),
                    (
                        "atlas_purchase_preflight",
                        {"quote_id": quote_id, "policy": "SECRET_SENTINEL"},
                    ),
                    ("atlas_quote", args | {"intent_key": "SECRET_SENTINEL"}),
                ]:
                    invalid = await session.call_tool(name, extra)
                    assert invalid.is_error and "SECRET_SENTINEL" not in invalid.model_dump_json()
                purchase = await session.call_tool(
                    "atlas_purchase", {"quote_id": quote_id, "max_cost": "9999"}
                )
                assert (
                    purchase.is_error
                    and purchase.structured_content["error"]["code"] == "BUDGET_EXCEEDED"
                )
                _, token = client.saved(quote_id)
                with client.db.connect() as conn:
                    key = conn.execute("SELECT capability FROM client_purchase_intents").fetchone()[
                        0
                    ]
                output = (
                    first.model_dump_json() + checked.model_dump_json() + budget.model_dump_json()
                )
                assert token not in output and key not in output
            tasks.cancel_scope.cancel()
    assert service.adapter.settlements == 0
