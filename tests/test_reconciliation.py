"""Finalized-chain recovery; RPC, signatures and settlement are strictly offline."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import httpx
import pytest
from eth_account import Account
from fastapi.testclient import TestClient
from test_official_x402 import (
    ORIGIN,
    QUERY,
    RECIPIENT,
    FacilitatorFixture,
    OfficialAdapter,
    OfficialSigner,
    official_service,
    policy,
)

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.payments.common import ASSET, Settlement
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.payments.policy import BudgetLedger
from atlas_terminal.payments.reconciliation import (
    AUTHORIZATION_USED,
    TRANSFER,
    BaseSepoliaReconciler,
    address_topic,
    settlement_header,
)
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest

TX = "0x" + "a" * 64
BLOCK = "0x" + "b" * 64
FINALIZED = "0x" + "f" * 64


def attempt(service, quote):
    with service.db.connect() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM payment_attempts WHERE quote_id=?", (quote.quote_id,)
            ).fetchone()
        )


class RpcFixture:
    def __init__(self, quote, payment, *, used=True, expired=True):
        self.calls = []
        self.used = used
        self.chain = "0x14a34"
        self.error = None
        self.finalized = {
            "number": hex(1000),
            "hash": FINALIZED,
            "timestamp": hex(int(payment["valid_before"] or 10000) + (10 if expired else -10)),
        }
        self.block = {
            "number": hex(900),
            "hash": BLOCK,
            "timestamp": hex(int(payment["valid_before"] or 10000) - 20),
        }
        common = {
            "address": ASSET,
            "transactionHash": TX,
            "blockNumber": hex(900),
            "blockHash": BLOCK,
            "removed": False,
        }
        authorizer = address_topic(payment["payer"])
        auth = common | {
            "topics": [AUTHORIZATION_USED, authorizer, payment["nonce"]],
            "data": "0x",
            "logIndex": "0x0",
        }
        transfer = common | {
            "topics": [TRANSFER, authorizer, address_topic(quote.pricing.pay_to)],
            "data": "0x" + f"{int(quote.pricing.amount_atomic):064x}",
            "logIndex": "0x1",
        }
        self.receipt = {
            "transactionHash": TX,
            "blockNumber": hex(900),
            "blockHash": BLOCK,
            "status": "0x1",
            "logs": [auth, transfer],
        }
        self.logs = [deepcopy(auth)]

    def handle(self, request):
        data = json.loads(request.content)
        method, params = data["method"], data["params"]
        self.calls.append((method, params))
        assert method in {
            "eth_chainId",
            "eth_getBlockByNumber",
            "eth_call",
            "eth_getLogs",
            "eth_getTransactionReceipt",
        }
        if self.error is not None:
            return self.error(request)
        if method == "eth_chainId":
            result = self.chain
        elif method == "eth_getBlockByNumber":
            result = self.finalized if params[0] in {"finalized", hex(1000)} else self.block
        elif method == "eth_call":
            assert params[0]["to"] == ASSET
            assert params[1] == {"blockHash": FINALIZED, "requireCanonical": True}
            result = "0x" + "0" * 63 + str(int(self.used))
        elif method == "eth_getLogs":
            assert params[0]["address"] == ASSET
            assert int(params[0]["toBlock"], 16) - int(params[0]["fromBlock"], 16) < 10000
            result = self.logs
        else:
            result = self.receipt if params == [TX] else None
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": result})

    def reconciler(self):
        return BaseSepoliaReconciler(
            "https://rpc.atlas.invalid/private-provider-key",
            client=httpx.Client(transport=httpx.MockTransport(self.handle)),
        )


def unresolved(tmp_path):
    fixture, adapter, service = official_service(tmp_path, FacilitatorFixture(unknown=True))
    quote, token = service.prepare(QUERY)
    signer = OfficialSigner(Account.create().key.hex())
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    ledger.reserve(quote, policy(), ORIGIN, signer.address, "test")
    proof = signer.sign(
        quote, adapter.challenge(quote, ORIGIN)["PAYMENT-REQUIRED"], policy(), ledger, ORIGIN
    )
    assert service.result(quote.quote_id, proof=proof)[1] == 202
    return fixture, service, quote, token, proof


def test_lost_settlement_is_recovered_from_finalized_nonce_logs_without_resettling(tmp_path):
    fixture, service, quote, token, proof = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote))
    service.reconciler = rpc.reconciler()
    report = service.reconcile(quote.quote_id, token)
    assert report.state == "SETTLED" and report.outcome == "settled" and report.evidence_sha256
    result, status, headers = service.result(quote.quote_id, bearer=token)
    assert status == 200 and headers["PAYMENT-RESPONSE"]
    assert result.receipt.transaction == TX and result.receipt.result_sha256 == quote.result_sha256
    assert digest(canonical(result.data)) == quote.result_sha256
    assert proof not in canonical(report).decode() and token not in canonical(report).decode()
    assert "private-provider-key" not in canonical(report).decode()
    calls = len(rpc.calls)
    repeated = service.reconcile(quote.quote_id, token)
    assert repeated == report and len(rpc.calls) == calls
    with service.db.connect() as conn:
        rows = conn.execute("SELECT * FROM reconciliation_audit").fetchall()
    assert len(rows) == 1 and digest(rows[0]["evidence"].encode()) == report.evidence_sha256
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_chain",
        "unfinalized",
        "wrong_recipient",
        "wrong_amount",
        "wrong_nonce",
        "wrong_payer",
        "wrong_asset",
        "removed",
        "missing_transfer",
        "multiple_transfers",
        "multiple_authorizations",
        "duplicate_index",
        "reverted",
        "canonical_hash_changed",
        "malformed_log",
        "conflicting_nonce_state",
    ],
)
def test_incomplete_or_conflicting_evidence_never_delivers_or_releases(tmp_path, mutation):
    fixture, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote))
    logs = rpc.receipt["logs"]
    if mutation == "wrong_chain":
        rpc.chain = "0x2105"
    elif mutation == "unfinalized":
        rpc.finalized["number"] = hex(800)
    elif mutation == "wrong_recipient":
        logs[1]["topics"][2] = address_topic("0x" + "2" * 40)
    elif mutation == "wrong_amount":
        logs[1]["data"] = "0x" + f"{9999:064x}"
    elif mutation == "wrong_nonce":
        logs[0]["topics"][2] = "0x" + "c" * 64
    elif mutation == "wrong_payer":
        logs[1]["topics"][1] = address_topic("0x" + "2" * 40)
    elif mutation == "wrong_asset":
        logs[1]["address"] = "0x" + "2" * 40
    elif mutation == "removed":
        logs[1]["removed"] = True
    elif mutation == "missing_transfer":
        logs.pop()
    elif mutation in {"multiple_transfers", "multiple_authorizations"}:
        logs.append(
            deepcopy(logs[1 if mutation == "multiple_transfers" else 0]) | {"logIndex": "0x2"}
        )
    elif mutation == "duplicate_index":
        logs[1]["logIndex"] = "0x0"
    elif mutation == "reverted":
        rpc.receipt["status"] = "0x0"
    elif mutation == "canonical_hash_changed":
        rpc.block["hash"] = "0x" + "c" * 64
    elif mutation == "malformed_log":
        logs[1]["topics"] = [None]
    else:
        rpc.used = False
    service.reconciler = rpc.reconciler()
    result = service.reconcile(quote.quote_id, token, TX)
    assert result.state == "SETTLEMENT_UNKNOWN" and result.outcome == "pending"
    assert service.result(quote.quote_id, bearer=token)[1] == 202
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize(
    "expired,used,expected",
    [
        (False, False, "SETTLEMENT_UNKNOWN"),
        (True, False, "FAILED"),
        (True, True, "SETTLEMENT_UNKNOWN"),
    ],
)
def test_failure_requires_both_expiry_and_unused_nonce_in_finalized_state(
    tmp_path, expired, used, expected
):
    fixture, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote), used=used, expired=expired)
    rpc.receipt = None
    rpc.logs = []
    service.reconciler = rpc.reconciler()
    report = service.reconcile(quote.quote_id, token)
    assert report.state == expected
    assert report.receipt is None
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize("kind", ["timeout", "redirect", "json_error", "malformed", "oversized"])
def test_rpc_failures_are_pending_and_do_not_echo_provider_details(tmp_path, kind):
    _, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote), used=False)

    def failure(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private-provider-key", request=request)
        if kind == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://other.invalid/private-provider-key"}
            )
        if kind == "json_error":
            return httpx.Response(200, json={"error": {"message": "private-provider-key"}})
        return httpx.Response(
            200, content="x" * (2 * 1024 * 1024 + 1) if kind == "oversized" else "["
        )

    rpc.error = failure
    service.reconciler = rpc.reconciler()
    report = service.reconcile(quote.quote_id, token)
    assert report.state == "SETTLEMENT_UNKNOWN"
    assert "private-provider-key" not in canonical(report).decode()
    assert len(rpc.calls) == 1


def test_old_nonce_outside_scan_can_be_recovered_with_a_validated_transaction_hint(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote))
    rpc.logs = []
    service.reconciler = rpc.reconciler()
    assert service.reconcile(quote.quote_id, token).outcome == "pending"
    assert service.reconcile(quote.quote_id, token, TX).outcome == "settled"


def test_legacy_attempts_without_recorded_deadline_are_not_declared_failed(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    with service.db.connect(write=True) as conn:
        conn.execute("ALTER TABLE payment_attempts DROP COLUMN valid_before")
    resumed = OrderService(
        Atlas("fixture"),
        service.db.path,
        service.pricing,
        base_origin=ORIGIN,
        adapter=service.adapter,
    )
    legacy = attempt(resumed, quote)
    assert legacy["valid_before"] is None
    rpc = RpcFixture(quote, legacy, used=False)
    resumed.reconciler = rpc.reconciler()
    assert resumed.reconcile(quote.quote_id, token).outcome == "pending"


def test_completed_order_wins_over_concurrent_pending_reconciliation(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    payment = attempt(service, quote)
    rpc = RpcFixture(quote, payment, used=False, expired=False)
    real = rpc.reconciler()
    service.reconciler = real
    assert service.reconcile(quote.quote_id, token).outcome == "pending"
    ready, release = Event(), Event()

    class PausedReconciler:
        def check(self, *args):
            decision = real.check(*args)
            ready.set()
            assert release.wait(5)
            return decision

    service.reconciler = PausedReconciler()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.reconcile, quote.quote_id, token)
        assert ready.wait(5)
        service.complete(
            quote,
            Settlement("SETTLED", TX, settlement_header(quote, payment["payer"], TX)),
            payment["payment_id"],
        )
        original_receipt = service.row(quote.quote_id)["receipt"]
        release.set()
        report = future.result()
        assert report.state == "SETTLED" and report.evidence_sha256 is None
    assert service.row(quote.quote_id)["receipt"] == original_receipt


@pytest.mark.parametrize(
    "used,expired,expected,budget",
    [
        (True, True, "SETTLED", "SPENT"),
        (False, True, "FAILED", "FAILED"),
        (False, False, "SETTLEMENT_UNKNOWN", "PENDING"),
    ],
)
def test_http_sdk_reconciliation_updates_budget_only_after_definitive_evidence(
    tmp_path, monkeypatch, used, expired, expected, budget
):
    fixture = FacilitatorFixture(unknown=True)
    adapter = OfficialAdapter("https://facilitator.atlas.invalid", facilitator=fixture.client)
    app = create_app(
        provider="fixture",
        payments="testnet",
        directory=tmp_path / "server",
        adapter=adapter,
        pay_to=RECIPIENT,
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(canonical(policy()))
    monkeypatch.setenv("ATLAS_TESTNET_PRIVATE_KEY", Account.create().key.hex())
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(
            ORIGIN, directory=tmp_path / "client", client=http, policy_path=policy_path
        )
        quote = client.quote(QUERY)
        with pytest.raises(AtlasError, match="unknown"):
            client.purchase(quote.quote_id)
        orders = app.state.orders
        rpc = RpcFixture(quote, attempt(orders, quote), used=used, expired=expired)
        orders.reconciler = rpc.reconciler()
        assert http.post(f"/v1/quotes/{quote.quote_id}/reconcile", json={}).status_code == 401
        assert rpc.calls == []
        _, capability = client.saved(quote.quote_id)
        for invalid in [
            {"transaction": "0x1234"},
            {"state": "SETTLED"},
            {"rpc_url": "https://other.invalid"},
        ]:
            rejected = http.post(
                f"/v1/quotes/{quote.quote_id}/reconcile",
                json=invalid,
                headers={"Authorization": "Bearer " + capability},
            )
            assert rejected.status_code == 422
        assert rpc.calls == []
        report = client.reconcile(quote.quote_id)
        assert report.state == expected
        assert client.budgets.get(quote.quote_id)["state"] == budget
        if expected == "SETTLED":
            assert client.purchase(quote.quote_id).receipt == report.receipt
        else:
            with pytest.raises(AtlasError):
                client.purchase(quote.quote_id)
        assert fixture.calls.count("/settle") == 1


def test_reconciliation_requires_capability_and_config_before_any_rpc(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    with pytest.raises(AtlasError) as caught:
        service.reconcile(quote.quote_id, None)
    assert caught.value.code == "UNAUTHORIZED"
    with pytest.raises(AtlasError) as caught:
        service.reconcile(quote.quote_id, token)
    assert caught.value.code == "CONFIG_REQUIRED"


def test_crash_after_transfer_before_receipt_commit_recovers_from_persisted_transaction(
    tmp_path, monkeypatch
):
    fixture, adapter, service = official_service(tmp_path)
    quote, token = service.prepare(QUERY)
    signer = OfficialSigner(Account.create().key.hex())
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    ledger.reserve(quote, policy(), ORIGIN, signer.address, "test")
    proof = signer.sign(
        quote, adapter.challenge(quote, ORIGIN)["PAYMENT-REQUIRED"], policy(), ledger, ORIGIN
    )

    def crash(*args):
        raise AtlasError("LEDGER_UNAVAILABLE", "Simulated crash before receipt commit")

    monkeypatch.setattr(service, "complete", crash)
    with pytest.raises(AtlasError):
        service.result(quote.quote_id, proof=proof)
    payment = attempt(service, quote)
    assert payment["transaction_id"] == TX
    rpc = RpcFixture(quote, payment)
    resumed = OrderService(
        Atlas("fixture"),
        service.db.path,
        service.pricing,
        base_origin=ORIGIN,
        adapter=adapter,
        reconciler=rpc.reconciler(),
    )
    assert resumed.reconcile(quote.quote_id, token).state == "SETTLED"
    assert "eth_getLogs" not in [method for method, _ in rpc.calls]
    assert fixture.calls.count("/settle") == 1


def test_reverted_transaction_before_authorization_expiry_keeps_the_reservation(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote), used=False, expired=False)
    rpc.receipt["status"] = "0x0"
    service.reconciler = rpc.reconciler()
    assert service.reconcile(quote.quote_id, token, TX).outcome == "pending"


@pytest.mark.parametrize("deadline", ["-1", "invalid", "0"])
def test_invalid_recorded_expiry_is_not_failure_evidence(tmp_path, deadline):
    _, service, quote, token, _ = unresolved(tmp_path)
    rpc = RpcFixture(quote, attempt(service, quote), used=False)
    with service.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE payment_attempts SET valid_before=? WHERE quote_id=?",
            (deadline, quote.quote_id),
        )
    service.reconciler = rpc.reconciler()
    assert service.reconcile(quote.quote_id, token).outcome == "pending"


def test_audit_write_failure_rolls_back_the_receipt_and_terminal_state(tmp_path):
    _, service, quote, token, _ = unresolved(tmp_path)
    service.reconciler = RpcFixture(quote, attempt(service, quote)).reconciler()
    with service.db.connect(write=True) as conn:
        conn.execute("DROP TABLE reconciliation_audit")
    with pytest.raises(AtlasError) as caught:
        service.reconcile(quote.quote_id, token)
    assert caught.value.code == "LEDGER_UNAVAILABLE"
    assert service.row(quote.quote_id)["state"] == "SETTLEMENT_UNKNOWN"
    assert service.row(quote.quote_id)["receipt"] is None
