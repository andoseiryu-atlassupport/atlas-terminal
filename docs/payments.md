# Payments and recovery

## Modes

- `off`: Zero price, no wallet and no x402 calls. A recovery capability retrieves the same quoted result.
- `mock`: A fully offline simulation using separate `Atlas-Mock-Required` / `Atlas-Mock-Payment` headers. It is not real x402 settlement. Receipts have `simulated=true` and `transaction=null`.
- `testnet`: Official x402 Python 2.25.0, V2/exact/EIP-3009, and Base Sepolia USDC on `eip155:84532` only. The official SDK handles `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE` and `PAYMENT-RESPONSE`. Mainnet is rejected even if configuration changes.

The example price of 10000 atomic units = 0.01 USDC is a test value, not a commercial price. Purchases deliver an immutable annual financial snapshot. Research, company resolution, filings, catalogs and quotes do not require payment.

## Offline verification

```sh
uv run --offline python examples/mock_purchase.py
uv run --offline pytest tests/test_payments.py tests/test_official_x402.py
```

The SDK tests include local signatures with ephemeral keys, but Facilitator HTTP is mocked. They make no external requests, verify no live chain inclusion and transfer no funds. Passing testnet-mode code tests is not a successful real testnet transfer.

For separate processes, start `atlas serve --provider fixture --payments mock`. Configure the client's origin and private state directory. Copy `examples/budget.mock.json` to a private policy file and set `ATLAS_POLICY_FILE` to that path. Use the CLI quote, purchase and purchase-status commands.

## Signing and budgets

Atlas validates the operator-configured origin, scheme, network, asset, recipient, decimals and per-purchase/session/UTC-day limits. `--max-cost` accepts an atomic-unit string and can only lower the limit. Display symbols do not identify assets. The same signer's daily budget is shared across tasks and sessions, and unknown reservations survive day changes.

A SQLite write transaction checks total reservations and makes the `RESERVED → SIGNING` transition once before signing. The EIP-712 domain, chain, contract, recipient, amount, types and validity window are also checked before invoking the official signer. Ledger failures stop payment. Signatures and recovery tokens are never returned to the model.

## Retries and unknown settlement

The server stores the verified payer, nonce, payment identifier and quote/query/resource/price fingerprint, with uniqueness constraints for nonces and orders. It commits `SETTLEMENT_SUBMITTED` before settlement, allowing only one competing request to proceed. Only confirmed success publishes a receipt and data.

A Facilitator timeout, inconclusive failure or crash after transfer but before commit leaves `SETTLEMENT_UNKNOWN`. The server returns 202; the client does not sign again and retains the budget reservation. Recovery requires a bearer or the same stored payment proof. Changes to SEC sources do not change a purchased FinancialResult's canonical hash.

The selected SDK's `ResourceServer.settle_payment` can retry pending settlements internally. Atlas instead calls the official low-level `HTTPFacilitatorClientSync.settle` once after a durable claim. It does not invent a Facilitator status API. Automatic onchain reconciliation and reservation release are unsupported. Unknown states remain pending until the operator checks private ledger records against Facilitator/chain evidence. External settlement and a database commit are not atomic, so unconditional exactly-once settlement is not guaranteed.

## Explicit real testnet execution (unverified)

Run these steps only in an authorized testnet verification environment.

1. Prepare a dedicated wallet with a small testnet balance, Base Sepolia USDC test tokens, an approved Facilitator URL and any required authentication. Do not use a wallet containing existing assets or a mainnet network.
2. Set `ATLAS_PAY_TO`, `ATLAS_FACILITATOR_URL` and, if needed, `ATLAS_PUBLIC_ORIGIN` privately on the server. Start `uv run atlas serve --provider fixture --payments testnet`. The Facilitator URL is fixed configuration and redirects are rejected. Public origins require HTTPS and separate operational controls.
3. Set `ATLAS_HOSTED_ORIGIN`, `ATLAS_STATE_DIR`, `ATLAS_POLICY_FILE` and `ATLAS_TESTNET_PRIVATE_KEY` privately on the client. Never put real keys in command arguments, documentation or chat.
4. Copy `examples/budget.testnet.template.json` to a private file. Verify the origin and actual testnet recipient, set the intended limits and explicitly set enabled to true. The unmodified template does not authorize signing.
5. Run the command below and record the timestamp, SDK version, network, asset, transaction, result hash and matching recovery result.

```sh
uv run python scripts/smoke.py testnet --allow-network --allow-testnet-transfer
```

Live Facilitator availability, supported schemes, final token/domain checks, authentication and balances are unverified. The standard adapter uses an approved unauthenticated Facilitator or an injected official Facilitator client. Service-specific authentication requires a separately configured official AuthProvider in the private deployment.

Live SEC retrieval has a separate command:

```sh
uv run python scripts/smoke.py sec --allow-network
```

Pytest also requires explicit opt-ins. Do not run these checks in CI for external contributions.

```sh
ATLAS_RUN_LIVE_SEC=1 uv run pytest -m live_sec
ATLAS_RUN_LIVE_X402=1 uv run pytest -m live_x402
```

These environment-variable assignments use POSIX shell syntax. On Windows, set process environment variables before running the same Python entrypoint. Missing funds, connectivity or configuration leave checks NOT RUN.
