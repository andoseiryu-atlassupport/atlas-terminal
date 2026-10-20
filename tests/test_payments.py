import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import Quote, parse_query
from atlas_terminal.payments.common import Settlement, make_pricing
from atlas_terminal.payments.mock import MockAdapter
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest, utcnow

ORIGIN = "http://127.0.0.1:8000"
QUERY = parse_query({"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3})


def make_service(tmp_path, adapter=None, mode="mock", **kwargs):
    adapter = adapter or MockAdapter()
    return OrderService(
        Atlas("fixture"),
        tmp_path / "orders.sqlite3",
        make_pricing(mode),
        base_origin=ORIGIN,
        adapter=adapter,
        **kwargs,
    )


def policy(**changes):
    return SpendingPolicy.model_validate(
        {
            "enabled": True,
            "mode": "mock",
            "allowed_origins": [ORIGIN],
            "network": "mock:0",
            "asset": "fixture:USDC",
            "pay_to": "fixture:merchant",
            "per_purchase_atomic": "10000",
            "per_session_atomic": "20000",
            "per_day_atomic": "20000",
        }
        | changes
    )


def buy(service, quote):
    proof = MockAdapter.sign(quote, ORIGIN)
    return service.result(quote.quote_id, proof=proof), proof


def test_mock_receipt_recovery_and_contract(tmp_path):
    service = make_service(tmp_path)
    quote, token = service.prepare(QUERY)
    assert "facts" not in quote.model_dump()
    assert '"value":' not in canonical(quote).decode()
    challenge, status, headers = service.result(quote.quote_id)
    assert status == 402 and "PAYMENT-REQUIRED" not in headers
    assert headers["Atlas-Mock-Required"] == "offline-simulation-only"
    (result, status, _), proof = buy(service, quote)
    assert status == 200 and result.receipt.simulated
    assert result.receipt.transaction is None and service.adapter.settlements == 1
    assert digest(canonical(result.data)) == quote.result_sha256
    schema = json.loads((FIXTURES.parent.parent / "schemas/atlas.schema.json").read_bytes())
    for definition, model in [("Quote", quote), ("PurchaseResult", result)]:
        Draft202012Validator(
            {"$ref": f"#/$defs/{definition}", **schema}, format_checker=FormatChecker()
        ).validate(model.model_dump(mode="json"))
    for kwargs in [{"bearer": token}, {"proof": proof}]:
        recovered, status, _ = service.result(quote.quote_id, **kwargs)
        assert recovered == result and status == 200
    assert service.adapter.settlements == 1


def test_quote_id_is_not_authorization_and_token_does_not_pay(tmp_path):
    service = make_service(tmp_path)
    quote, token = service.prepare(QUERY)
    with pytest.raises(AtlasError) as e:
        service.status(quote.quote_id, None)
    assert e.value.code == "UNAUTHORIZED"
    assert service.status(quote.quote_id, token)["state"] == "QUOTED"
    assert service.result(quote.quote_id, bearer=token)[1] == 402
    buy(service, quote)
    for kwargs in [{}, {"bearer": "wrong"}]:
        with pytest.raises(AtlasError) as e:
            service.result(quote.quote_id, **kwargs)
        assert e.value.code == "UNAUTHORIZED"


def test_expiry_applies_only_to_unstarted_orders(tmp_path):
    now = [utcnow()]
    service = make_service(tmp_path, clock=lambda: now[0])
    paid, token = service.prepare(QUERY)
    buy(service, paid)
    unpaid, _ = service.prepare(QUERY)
    now[0] += timedelta(minutes=6)
    assert service.result(paid.quote_id, bearer=token)[1] == 200
    with pytest.raises(AtlasError) as e:
        service.result(unpaid.quote_id)
    assert e.value.code == "QUOTE_EXPIRED"


def test_snapshot_cannot_change_after_sec_update(tmp_path):
    service = make_service(tmp_path)
    quote, token = service.prepare(QUERY)
    (first, _, _), _ = buy(service, quote)

    def source_now_unavailable(*args, **kwargs):
        raise RuntimeError("source changed")

    service.atlas.get_financials = source_now_unavailable
    assert service.result(quote.quote_id, bearer=token)[0] == first


def test_parallel_identical_proof_has_one_settlement(tmp_path):
    entered, resume = Event(), Event()

    class SlowAdapter(MockAdapter):
        def settle(self, quote, payment):
            entered.set()
            assert resume.wait(10)
            return super().settle(quote, payment)

    adapter = SlowAdapter()
    service = make_service(tmp_path, adapter)
    quote, token = service.prepare(QUERY)
    proof = adapter.sign(quote, ORIGIN)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.result, quote.quote_id, proof=proof)
        assert entered.wait(10)
        second = pool.submit(service.result, quote.quote_id, proof=proof).result()
        assert second[1] == 202
        assert service.status(quote.quote_id, token)["state"] == "SETTLEMENT_UNKNOWN"
        resume.set()
        assert first.result()[1] == 200
    assert adapter.settlements == 1


@pytest.mark.parametrize("reuse", ["payment_id", "nonce"])
def test_reuse_across_quotes_is_conflict(tmp_path, reuse):
    service = make_service(tmp_path)
    first, _ = service.prepare(QUERY)
    second, _ = service.prepare(QUERY)
    (_, _, _), proof = buy(service, first)
    new = json.loads(MockAdapter.sign(second, ORIGIN))
    new[reuse] = json.loads(proof)[reuse]
    with pytest.raises(AtlasError) as e:
        service.result(second.quote_id, proof=canonical(new).decode())
    assert e.value.code == "PAYMENT_REUSE_CONFLICT"
    assert service.adapter.settlements == 1


@pytest.mark.parametrize("state", ["FAILED", "SETTLEMENT_UNKNOWN"])
def test_verification_never_implies_settlement(tmp_path, state):
    class FailingAdapter(MockAdapter):
        def settle(self, quote, payment):
            self.settlements += 1
            return Settlement(state)

    service = make_service(tmp_path, FailingAdapter())
    quote, token = service.prepare(QUERY)
    if state == "FAILED":
        with pytest.raises(AtlasError) as e:
            buy(service, quote)
        assert e.value.code == "PAYMENT_FAILED"
    else:
        (result, status, _), proof = buy(service, quote)
        assert status == 202 and "data" not in result
        assert service.result(quote.quote_id, proof=proof)[1] == 202
    assert service.status(quote.quote_id, token)["state"] == state
    assert service.adapter.settlements == 1


def test_crash_after_transfer_before_commit_never_settles_again(tmp_path, monkeypatch):
    adapter = MockAdapter()
    service = make_service(tmp_path, adapter)
    quote, token = service.prepare(QUERY)
    proof = adapter.sign(quote, ORIGIN)

    def lost_database(*args, **kwargs):
        raise AtlasError("LEDGER_UNAVAILABLE", "Simulated commit failure")

    monkeypatch.setattr(service, "complete", lost_database)
    with pytest.raises(AtlasError):
        service.result(quote.quote_id, proof=proof)
    resumed = make_service(tmp_path, adapter)
    assert resumed.status(quote.quote_id, token)["state"] == "SETTLEMENT_UNKNOWN"
    assert resumed.result(quote.quote_id, bearer=token)[1] == 202
    assert adapter.settlements == 1


def test_unavailable_artifact_or_database_stops_payment(tmp_path):
    service = make_service(tmp_path)
    quote, _ = service.prepare(QUERY)
    proof = MockAdapter.sign(quote, ORIGIN)
    with service.db.connect(write=True) as conn:
        conn.execute("UPDATE result_snapshots SET body=?", (b"corrupt",))
    with pytest.raises(AtlasError) as e:
        service.result(quote.quote_id, proof=proof)
    assert e.value.code == "LEDGER_UNAVAILABLE" and service.adapter.settlements == 0
    service.db.path.unlink()
    with pytest.raises(AtlasError) as e:
        service.result(quote.quote_id, proof=proof)
    assert e.value.code == "LEDGER_UNAVAILABLE" and service.adapter.settlements == 0


def test_no_quote_or_payment_for_missing_coverage(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(AtlasError):
        service.prepare(QUERY.model_copy(update={"metrics": ["liabilities"]}))
    with service.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM quotes").fetchone()[0] == 0
    assert service.adapter.settlements == 0


def test_atomic_budget_reservations_and_global_daily_limit(tmp_path):
    service = make_service(tmp_path)
    quotes = [service.prepare(QUERY)[0] for _ in range(4)]
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    p = policy(per_day_atomic="10000", per_session_atomic="10000")

    def reserve(item):
        try:
            return ledger.reserve(item, p, ORIGIN, "fixture:agent", "session-" + item.quote_id)
        except AtlasError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, quotes))
    assert results.count(True) == 1 and results.count("BUDGET_EXCEEDED") == 3
    chosen = quotes[results.index(True)]
    with ledger.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE client_orders SET day='2020-01-01',state='PENDING' WHERE quote_id=?",
            (chosen.quote_id,),
        )
    # A change of task/session or UTC day cannot release an unresolved transfer.
    assert reserve(quotes[(results.index(True) + 1) % 4]) == "BUDGET_EXCEEDED"


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"network": "eip155:8453"},
        {"asset": "fake:USDC"},
        {"pay_to": "fixture:attacker"},
        {"allowed_origins": ["https://other.invalid"]},
        {"per_purchase_atomic": "9999"},
    ],
)
def test_policy_denials_precede_signing(tmp_path, changes):
    quote, _ = make_service(tmp_path).prepare(QUERY)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    with pytest.raises(AtlasError):
        ledger.reserve(quote, policy(**changes), ORIGIN, "fixture:agent", "default")
    assert ledger.get(quote.quote_id) is None


def test_max_cost_can_only_reduce_limit(tmp_path):
    service = make_service(tmp_path)
    quote, _ = service.prepare(QUERY)
    with pytest.raises(AtlasError):
        policy(per_purchase_atomic="9999").authorize(quote, ORIGIN, "999999")
    with pytest.raises(AtlasError):
        policy().authorize(quote, ORIGIN, "9999")


@pytest.mark.parametrize("mode", ["off", "mock"])
def test_http_sdk_purchase_and_restart(tmp_path, mode):
    app = create_app(provider="fixture", payments=mode, directory=tmp_path / "server")
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(canonical(policy()))
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(
            ORIGIN, directory=tmp_path / "client", client=http, policy_path=policy_path
        )
        quote = client.quote(QUERY)
        assert client.status(quote.quote_id)["state"] == "QUOTED"
        first = client.purchase(quote.quote_id)
        second = client.purchase(quote.quote_id)
        assert first == second
        assert first.receipt.simulated == (mode == "mock")
        saved = HostedClient(
            ORIGIN, directory=tmp_path / "client", client=http, policy_path=policy_path
        )
        assert saved.purchase(quote.quote_id) == first
        assert http.get(quote.resource_path).status_code == 401
        assert http.get(f"/v1/quotes/{quote.quote_id}/status").status_code == 401
        assert "recovery" not in json.dumps(quote.model_dump()).lower()


def test_client_timeout_retains_reservation_and_refuses_new_signature(tmp_path, monkeypatch):
    app = create_app(provider="fixture", payments="mock", directory=tmp_path / "server")
    policy_path = tmp_path / "policy.json"
    policy_path.write_bytes(canonical(policy()))
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(
            ORIGIN, directory=tmp_path / "client", client=http, policy_path=policy_path
        )
        quote = client.quote(QUERY)
        calls = []
        original_request = client.request

        def lost_response(method, path, **kwargs):
            calls.append(kwargs.get("headers", {}))
            if "Atlas-Mock-Payment" in kwargs.get("headers", {}):
                raise AtlasError("SOURCE_UNAVAILABLE", "Simulated timeout before response")
            return original_request(method, path, **kwargs)

        monkeypatch.setattr(client, "request", lost_response)
        with pytest.raises(AtlasError):
            client.purchase(quote.quote_id)
        with pytest.raises(AtlasError) as e:
            client.purchase(quote.quote_id)
        assert e.value.code == "SETTLEMENT_UNKNOWN"
        assert sum("Atlas-Mock-Payment" in c for c in calls) == 1
        assert client.budgets.get(quote.quote_id)["state"] == "PENDING"


def test_http_limits_unknown_fields_and_private_recovery_header(tmp_path):
    app = create_app(provider="fixture", payments="off", directory=tmp_path)
    with TestClient(app, base_url=ORIGIN) as http:
        assert http.post("/v1/quotes", json={"extra": True}).status_code == 400
        assert http.post("/v1/quotes", json={"padding": "x" * 17000}).status_code == 413
        response = http.post("/v1/quotes", json=QUERY.model_dump(mode="json"))
        assert response.headers["Cache-Control"] == "no-store"
        token = response.headers["Atlas-Recovery-Token"]
        assert token not in response.text
        quote = Quote.model_validate(response.json())
        assert http.get(quote.resource_path).status_code == 401
        assert token.encode() not in app.state.orders.db.path.read_bytes()


def test_testnet_config_and_mainnet_refusal(tmp_path, monkeypatch):
    with pytest.raises(AtlasError):
        create_app(provider="fixture", payments="testnet", directory=tmp_path)
    monkeypatch.setenv("ATLAS_TESTNET_NETWORK", "eip155:8453")
    with pytest.raises(AtlasError) as e:
        create_app(
            provider="fixture", payments="testnet", directory=tmp_path, pay_to="0x" + "1" * 40
        )
    assert e.value.code == "POLICY_DENIED"
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(AtlasError):
        create_app(provider="fixture", directory=tmp_path)
