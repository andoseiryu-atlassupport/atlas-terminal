"""Mainnet-shaped signatures, official SDK messages and simulated settlement. No transfers."""

import json

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from test_seller_products import simulated_capture
from x402 import x402ClientSync
from x402.extensions.payment_identifier import (
    append_payment_identifier_to_extensions,
    generate_payment_id,
)
from x402.http import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.utils import decode_payment_required_header, encode_payment_signature_header
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.mechanisms.evm.types import ExactEIP3009Payload
from x402.schemas import PaymentPayload

from atlas_terminal.errors import AtlasError
from atlas_terminal.payments.common import MAINNET_ASSET, MAINNET_NETWORK, seller_pricing
from atlas_terminal.payments.x402_mainnet import BaseMainnetReconciler, MainnetAdapter
from atlas_terminal.seller import SellerOrders
from atlas_terminal.seller_library import ProductLibrary
from atlas_terminal.seller_models import ProductQuery
from atlas_terminal.server import create_app

ORIGIN = "https://seller.atlas.invalid"
RECIPIENT = "0x" + "1" * 40


class SellerFacilitator:
    def __init__(self, *, unknown=False, wrong_network=False):
        self.calls = []
        self.unknown, self.wrong_network = unknown, wrong_network
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.client = HTTPFacilitatorClientSync(
            FacilitatorConfig(url="https://facilitator.atlas.invalid", http_client=self.http)
        )

    def handle(self, request):
        self.calls.append(request.url.path)
        if request.url.path == "/supported":
            return httpx.Response(
                200,
                json={
                    "kinds": [{"x402Version": 2, "scheme": "exact", "network": MAINNET_NETWORK}],
                    "extensions": [],
                    "signers": {},
                },
            )
        payload = PaymentPayload.model_validate(json.loads(request.content)["paymentPayload"])
        if request.url.path == "/verify":
            auth = ExactEIP3009Payload.from_dict(payload.payload).authorization
            domain, types, _, message = build_typed_data_for_signing(
                auth, 8453, MAINNET_ASSET, "USD Coin", "2"
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
        assert request.url.path == "/settle"
        if self.unknown:
            raise httpx.ReadTimeout("Fictional transport interruption")
        return httpx.Response(
            200,
            json={
                "success": True,
                "payer": payload.payload["authorization"]["from"],
                "transaction": "0x" + "a" * 64,
                "network": "eip155:84532" if self.wrong_network else MAINNET_NETWORK,
                "amount": payload.accepted.amount,
            },
        )


def mainnet_orders(tmp_path, **flags):
    fixture = SellerFacilitator(**flags)
    library = ProductLibrary(tmp_path)
    library.publish("cpi", simulated_capture(), source_access="retained_capture")
    adapter = MainnetAdapter(facilitator=fixture.client)
    service = SellerOrders(
        library, seller_pricing("mainnet", "10000", RECIPIENT), base_origin=ORIGIN, adapter=adapter
    )
    return fixture, service


def make_payload(required):
    client = x402ClientSync()
    client.register(MAINNET_NETWORK, ExactEvmClientScheme(EthAccountSigner(Account.create())))
    return client.create_payment_payload(
        required,
        extensions=append_payment_identifier_to_extensions(
            required.extensions, generate_payment_id()
        ),
    )


def test_mainnet_seller_uses_exact_base_usdc_and_one_durable_settlement(tmp_path):
    fixture, service = mainnet_orders(tmp_path)
    quote, token = service.prepare_product(ProductQuery(series="cpi"))
    required = decode_payment_required_header(service.result(quote.quote_id)[2]["PAYMENT-REQUIRED"])
    assert required.accepts[0].network == MAINNET_NETWORK
    assert required.accepts[0].asset == MAINNET_ASSET
    assert required.accepts[0].extra == {
        "name": "USD Coin",
        "version": "2",
        "assetTransferMethod": "eip3009",
    }
    proof = encode_payment_signature_header(make_payload(required))
    data, status, headers = service.result(quote.quote_id, proof=proof)
    assert status == 200 and data.receipt.mode == "mainnet" and "PAYMENT-RESPONSE" in headers
    assert service.result(quote.quote_id, bearer=token)[0] == data
    assert service.result(quote.quote_id, proof=proof)[0] == data
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize(
    "mutation", ["asset", "network", "pay_to", "amount", "resource", "decimals", "mode"]
)
def test_price_or_resource_mismatch_never_reaches_facilitator(tmp_path, mutation):
    fixture, service = mainnet_orders(tmp_path)
    quote, _ = service.prepare_product(ProductQuery(series="cpi"))
    if mutation in {"mode", "decimals"}:
        altered = quote.model_copy(deep=True)
        setattr(altered.pricing, mutation, "testnet" if mutation == "mode" else 18)
        with pytest.raises(AtlasError):
            service.adapter.requirements(altered)
    else:
        required = decode_payment_required_header(
            service.result(quote.quote_id)[2]["PAYMENT-REQUIRED"]
        )
        payload = make_payload(required)
        if mutation == "resource":
            payload.resource.url = "https://other.invalid/result"
        else:
            setattr(
                payload.accepted,
                mutation,
                {
                    "asset": "0x" + "2" * 40,
                    "network": "eip155:84532",
                    "pay_to": "0x" + "2" * 40,
                    "amount": "1",
                }[mutation],
            )
        with pytest.raises(AtlasError):
            service.result(quote.quote_id, proof=encode_payment_signature_header(payload))
    assert "/verify" not in fixture.calls and "/settle" not in fixture.calls


@pytest.mark.parametrize("flags", [{"unknown": True}, {"wrong_network": True}])
def test_unknown_or_wrong_chain_settlement_never_retries(tmp_path, flags):
    fixture, service = mainnet_orders(tmp_path, **flags)
    quote, token = service.prepare_product(ProductQuery(series="cpi"))
    required = decode_payment_required_header(service.result(quote.quote_id)[2]["PAYMENT-REQUIRED"])
    proof = encode_payment_signature_header(make_payload(required))
    assert service.result(quote.quote_id, proof=proof)[1] == 202
    assert service.result(quote.quote_id, proof=proof)[1] == 202
    assert service.status(quote.quote_id, token)["state"] == "SETTLEMENT_UNKNOWN"
    assert fixture.calls.count("/settle") == 1


@pytest.mark.parametrize("wrong_chain", [False, True])
def test_mainnet_reconciliation_requires_finalized_correct_chain_and_asset(
    tmp_path, monkeypatch, wrong_chain
):
    import test_reconciliation as helpers

    fixture, service = mainnet_orders(tmp_path, unknown=True)
    quote, token = service.prepare_product(ProductQuery(series="cpi"))
    required = decode_payment_required_header(service.result(quote.quote_id)[2]["PAYMENT-REQUIRED"])
    service.result(quote.quote_id, proof=encode_payment_signature_header(make_payload(required)))
    payment = helpers.attempt(service, quote)
    monkeypatch.setattr(helpers, "ASSET", MAINNET_ASSET)
    rpc = helpers.RpcFixture(quote, payment)
    rpc.chain = hex(84532 if wrong_chain else 8453)
    service.reconciler = BaseMainnetReconciler(
        "https://rpc.atlas.invalid/private",
        client=httpx.Client(transport=httpx.MockTransport(rpc.handle)),
    )
    result = service.reconcile(quote.quote_id, token)
    assert result.outcome == ("pending" if wrong_chain else "settled")
    if not wrong_chain:
        assert result.receipt.network == MAINNET_NETWORK
        assert result.receipt.asset == MAINNET_ASSET
        assert service.result(quote.quote_id, bearer=token)[1] == 200
    assert fixture.calls.count("/settle") == 1


def test_existing_reference_server_still_rejects_mainnet(tmp_path):
    with pytest.raises(AtlasError):
        create_app(directory=tmp_path, payments="mainnet")


def test_cdp_auth_is_endpoint_bound_and_never_contains_wallet_credentials(monkeypatch):
    pytest.importorskip("cdp")
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from atlas_terminal.payments.x402_mainnet import cdp_headers

    key = ec.generate_private_key(ec.SECP256R1())
    secret = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("CDP_API_KEY_ID", "ephemeral-offline-test")
    monkeypatch.setenv("CDP_API_KEY_SECRET", secret)
    headers = cdp_headers()
    for endpoint, values in headers.items():
        claims = jwt.decode(
            values["Authorization"][7:],
            key.public_key(),
            algorithms=["ES256"],
            options={"verify_aud": False},
        )
        method = "GET" if endpoint == "supported" else "POST"
        assert claims["uris"] == [f"{method} api.cdp.coinbase.com/platform/v2/x402/{endpoint}"]
        assert claims["exp"] - claims["nbf"] == 120
        assert secret not in str(values)
    monkeypatch.delenv("CDP_API_KEY_SECRET")
    with pytest.raises(AtlasError) as exc:
        cdp_headers()
    assert secret not in str(exc.value)
