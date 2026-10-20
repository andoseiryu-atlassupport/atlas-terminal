"""Real SDK and local cryptography; the facilitator and chain are simulated."""

import json

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient
from x402 import x402ClientSync
from x402.extensions.payment_identifier import (
    append_payment_identifier_to_extensions,
    generate_payment_id,
)
from x402.http import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_signature_header,
    encode_payment_required_header,
    encode_payment_signature_header,
)
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.mechanisms.evm.types import ExactEIP3009Payload
from x402.schemas import PaymentPayload, ResourceInfo

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.hosted import HostedClient
from atlas_terminal.models import Quote, parse_query
from atlas_terminal.payments.common import ASSET, NETWORK, make_pricing
from atlas_terminal.payments.orders import OrderService
from atlas_terminal.payments.policy import BudgetLedger, SpendingPolicy
from atlas_terminal.payments.x402_testnet import TestnetAdapter as OfficialAdapter
from atlas_terminal.payments.x402_testnet import TestnetSigner as OfficialSigner
from atlas_terminal.server import create_app
from atlas_terminal.util import canonical, digest

ORIGIN = "http://127.0.0.1:8000"
RECIPIENT = "0x" + "1" * 40  # Public synthetic recipient, used only by offline transports.
QUERY = parse_query({"identifier": "ATLASDEMO", "metrics": ["revenue", "net_income"], "count": 3})


class FacilitatorFixture:
    def __init__(self, *, unknown=False):
        self.calls = []
        self.unknown = unknown
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=False)
        self.client = HTTPFacilitatorClientSync(
            FacilitatorConfig(url="https://facilitator.atlas.invalid", http_client=self.http)
        )

    def handle(self, request):
        self.calls.append(request.url.path)
        if request.url.path == "/supported":
            return httpx.Response(
                200,
                json={
                    "kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}],
                    "extensions": [],
                    "signers": {},
                },
            )
        if request.url.path == "/verify":
            data = json.loads(request.content)
            payload = PaymentPayload.model_validate(data["paymentPayload"])
            auth = ExactEIP3009Payload.from_dict(payload.payload).authorization
            domain, types, primary, message = build_typed_data_for_signing(
                auth, 84532, ASSET, "USDC", "2"
            )
            recovered = Account.recover_message(
                encode_typed_data(
                    domain_data={
                        "name": domain.name,
                        "version": domain.version,
                        "chainId": domain.chain_id,
                        "verifyingContract": domain.verifying_contract,
                    },
                    message_types=types,
                    message_data=message,
                ),
                signature=payload.payload["signature"],
            )
            assert recovered.lower() == auth.from_address.lower()
            return httpx.Response(200, json={"isValid": True, "payer": recovered})
        if request.url.path == "/settle":
            if self.unknown:
                raise httpx.ReadTimeout("simulated lost settlement response")
            payload = PaymentPayload.model_validate(json.loads(request.content)["paymentPayload"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "payer": payload.payload["authorization"]["from"],
                    "transaction": "0x" + "a" * 64,
                    "network": NETWORK,
                    "amount": payload.accepted.amount,
                },
            )
        raise AssertionError("Unexpected SDK endpoint")


def policy():
    return SpendingPolicy(
        enabled=True,
        mode="testnet",
        allowed_origins=[ORIGIN],
        network=NETWORK,
        asset=ASSET,
        pay_to=RECIPIENT,
        per_purchase_atomic="10000",
        per_session_atomic="20000",
        per_day_atomic="20000",
    )


def official_service(tmp_path, fixture=None):
    fixture = fixture or FacilitatorFixture()
    adapter = OfficialAdapter("https://facilitator.atlas.invalid", facilitator=fixture.client)
    service = OrderService(
        Atlas("fixture"),
        tmp_path / "server.sqlite3",
        make_pricing("testnet", pay_to=RECIPIENT),
        base_origin=ORIGIN,
        adapter=adapter,
    )
    return fixture, adapter, service


def test_official_sdk_challenge_signature_and_facilitator_http_binding(tmp_path):
    fixture, adapter, service = official_service(tmp_path)
    quote, token = service.prepare(QUERY)
    challenge = service.result(quote.quote_id)[2]["PAYMENT-REQUIRED"]
    required = decode_payment_required_header(challenge)
    assert required.x402_version == 2 and required.accepts[0].network == NETWORK
    assert required.accepts[0].extra["name"] == "USDC"
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    # An ephemeral in-memory key signs locally; no chain call or transfer occurs.
    signer = OfficialSigner(Account.create().key.hex())
    assert ledger.reserve(quote, policy(), ORIGIN, signer.address, "test")
    proof = signer.sign(quote, challenge, policy(), ledger, ORIGIN)
    assert decode_payment_signature_header(proof).accepted == required.accepts[0]
    result, status, headers = service.result(quote.quote_id, proof=proof)
    assert status == 200 and "PAYMENT-RESPONSE" in headers
    assert not result.receipt.simulated and result.receipt.mode == "testnet"
    assert digest(canonical(result.data)) == quote.result_sha256
    assert service.result(quote.quote_id, bearer=token)[0] == result
    assert fixture.calls.count("/settle") == 1


def test_generic_official_x402_client_offline_without_atlas_client(tmp_path):
    fixture = FacilitatorFixture()
    adapter = OfficialAdapter("https://facilitator.atlas.invalid", facilitator=fixture.client)
    app = create_app(
        provider="fixture",
        payments="testnet",
        directory=tmp_path,
        adapter=adapter,
        pay_to=RECIPIENT,
    )
    with TestClient(app, base_url=ORIGIN) as http:
        quote_response = http.post("/v1/quotes", json=QUERY.model_dump(mode="json"))
        quote = Quote.model_validate(quote_response.json())
        response = http.get(quote.resource_path)
        assert response.status_code == 402 and "data" not in response.json()
        required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
        client = x402ClientSync()
        client.register(NETWORK, ExactEvmClientScheme(EthAccountSigner(Account.create())))
        extensions = append_payment_identifier_to_extensions(
            required.extensions, generate_payment_id()
        )
        payload = client.create_payment_payload(required, extensions=extensions)
        paid = http.get(
            quote.resource_path,
            headers={"PAYMENT-SIGNATURE": encode_payment_signature_header(payload)},
        )
        assert paid.status_code == 200 and paid.json()["receipt"]["mode"] == "testnet"
        assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize(
    "mutation", ["amount", "asset", "network", "pay_to", "domain", "resource", "permit2"]
)
def test_tampered_challenge_cannot_reach_signer(tmp_path, mutation):
    fixture, adapter, service = official_service(tmp_path)
    quote, _ = service.prepare(QUERY)
    required = decode_payment_required_header(adapter.challenge(quote, ORIGIN)["PAYMENT-REQUIRED"])
    req = required.accepts[0]
    if mutation in {"amount", "asset", "network", "pay_to"}:
        setattr(
            req,
            mutation,
            {
                "amount": "10001",
                "asset": "0x" + "2" * 40,
                "network": "eip155:8453",
                "pay_to": "0x" + "2" * 40,
            }[mutation],
        )
    elif mutation == "domain":
        req.extra["name"] = "attacker"
    elif mutation == "permit2":
        req.extra["assetTransferMethod"] = "permit2"
    else:
        required.resource = ResourceInfo(url="https://other.invalid/payment")
    signer = OfficialSigner(Account.create().key.hex())
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    ledger.reserve(quote, policy(), ORIGIN, signer.address, "test")
    with pytest.raises(AtlasError) as e:
        signer.sign(quote, encode_payment_required_header(required), policy(), ledger, ORIGIN)
    assert e.value.code == "QUOTE_MISMATCH"
    assert ledger.get(quote.quote_id)["proof"] is None
    assert "/verify" not in fixture.calls and "/settle" not in fixture.calls


def test_official_facilitator_timeout_is_unknown_and_not_retried(tmp_path):
    fixture = FacilitatorFixture(unknown=True)
    fixture, adapter, service = official_service(tmp_path, fixture)
    quote, token = service.prepare(QUERY)
    challenge = adapter.challenge(quote, ORIGIN)["PAYMENT-REQUIRED"]
    signer = OfficialSigner(Account.create().key.hex())
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    ledger.reserve(quote, policy(), ORIGIN, signer.address, "test")
    proof = signer.sign(quote, challenge, policy(), ledger, ORIGIN)
    assert service.result(quote.quote_id, proof=proof)[1] == 202
    assert service.result(quote.quote_id, bearer=token)[1] == 202
    assert fixture.calls.count("/settle") == 1


def test_full_atlas_hosted_client_with_real_local_sdk_signing(tmp_path, monkeypatch):
    fixture = FacilitatorFixture()
    adapter = OfficialAdapter("https://facilitator.atlas.invalid", facilitator=fixture.client)
    app = create_app(
        provider="fixture",
        payments="testnet",
        directory=tmp_path / "server",
        adapter=adapter,
        pay_to=RECIPIENT,
    )
    p = tmp_path / "policy.json"
    p.write_bytes(canonical(policy()))
    monkeypatch.setenv("ATLAS_TESTNET_PRIVATE_KEY", Account.create().key.hex())
    with TestClient(app, base_url=ORIGIN) as http:
        client = HostedClient(ORIGIN, directory=tmp_path / "client", client=http, policy_path=p)
        quote = client.quote(QUERY)
        result = client.purchase(quote.quote_id)
        assert client.purchase(quote.quote_id) == result
        assert client.budgets.get(quote.quote_id)["state"] == "SPENT"
        assert fixture.calls.count("/settle") == 1
