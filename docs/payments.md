# Payments and recovery

## Modes

- `off`: Zero price, no wallet and no x402 calls. A recovery capability retrieves the same quoted result.
- `mock`: A fully offline simulation using separate `Atlas-Mock-Required` / `Atlas-Mock-Payment` headers. It is not real x402 settlement. Receipts have `simulated=true` and `transaction=null`.
- `testnet`: Official x402 Python 2.25.0, V2/exact/EIP-3009, and Base Sepolia USDC on `eip155:84532` only. The official SDK handles `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE` and `PAYMENT-RESPONSE`. Mainnet is rejected even if configuration changes.

The example price of 10000 atomic units = 0.01 USDC is a test value, not a commercial price. Purchases deliver an immutable annual financial snapshot. Research, company resolution, filings, catalogs and quotes do not require payment.

## Offline verification

```sh
npm ci
uv run --offline python examples/mock_purchase.py
uv run --offline pytest tests/test_payments.py tests/test_official_x402.py tests/test_reconciliation.py tests/test_x402_interop.py
```

The SDK tests include local signatures with ephemeral keys, but Facilitator HTTP is mocked. They make no external requests, verify no live chain inclusion and transfer no funds. Passing testnet-mode code tests is not a successful real testnet transfer.

For separate processes, start `atlas serve --provider fixture --payments mock`. Configure the client's origin and private state directory. Copy `examples/budget.mock.json` to a private policy file and set `ATLAS_POLICY_FILE` to that path. Use the CLI quote, purchase and purchase-status commands.

## Signing and budgets

Atlas validates the operator-configured origin, scheme, network, asset, recipient, decimals and per-purchase/session/UTC-day limits. `--max-cost` accepts an atomic-unit string and can only lower the limit. Display symbols do not identify assets. The same signer's daily budget is shared across tasks and sessions, and unknown reservations survive day changes.

A SQLite write transaction checks total reservations and makes the `RESERVED → SIGNING` transition once before signing. The EIP-712 domain, chain, contract, recipient, amount, types and validity window are also checked before invoking the official signer. Ledger failures stop payment. Signatures and recovery tokens are never returned to the model.

## Retries and unknown settlement

The server stores the verified payer, nonce, payment identifier and quote/query/resource/price fingerprint, with uniqueness constraints for nonces and orders. It commits `SETTLEMENT_SUBMITTED` before settlement, allowing only one competing request to proceed. Only confirmed success publishes a receipt and data.

A Facilitator timeout, inconclusive failure or crash after transfer but before commit leaves `SETTLEMENT_UNKNOWN`. The server returns 202; the client does not sign again and retains the budget reservation. Recovery requires a bearer or the same stored payment proof. Changes to SEC sources do not change a purchased FinancialResult's canonical hash.

The selected SDK's `ResourceServer.settle_payment` can retry pending settlements internally. Atlas instead calls the official low-level `HTTPFacilitatorClientSync.settle` once after a durable claim. It does not invent a Facilitator status API. On-demand reconciliation can recover unknown orders from finalized chain evidence. External settlement and a database commit are not atomic, so unconditional exactly-once settlement is not guaranteed.

## Finalized-chain reconciliation

Configure `ATLAS_TESTNET_RPC_URL` privately on the reference HTTP server. This trusted RPC is separate from the Facilitator. It must support Base Sepolia, `finalized` blocks, EIP-1898 block-hash `eth_call`, receipt reads and `eth_getLogs`. HTTPS or explicit loopback HTTP is required; redirects, query strings and fragments are rejected. Provider keys may appear in the configured path, which is never returned or logged.

Use the existing hosted origin and private client state:

```sh
uv run atlas purchase-status QUOTE_ID --json
uv run atlas purchase-reconcile QUOTE_ID --json
```

The SDK exposes `Atlas("hosted").reconcile_purchase(quote_id, transaction=None)`. HTTP clients call `POST /v1/quotes/{quote_id}/reconcile` with `{}` and their saved recovery bearer. The optional `transaction` field (CLI `--transaction`) is a public transaction-hash hint, not proof of payment. Callers cannot supply URLs, networks, token addresses, signatures or success/failure overrides.

Reconciliation pins authorization state to an exact canonical finalized block and never signs, settles, cancels a nonce or broadcasts:

- **Settled:** A successful finalized receipt must contain a matching USDC `AuthorizationUsed` event for the saved payer/nonce and a transfer to the quoted recipient for the exact atomic amount. Canonical hashes are checked. Multiple authorizations or outgoing transfers by that payer in one receipt are ambiguous and remain pending.
- **Failed:** The recorded `validBefore` must have elapsed in finalized chain time and the nonce must still be unused. A reverted transaction, HTTP timeout or missing receipt alone is insufficient.
- **Pending:** Missing, unfinalized, inconsistent, wrong-chain or unavailable evidence holds the reservation. A consumed/cancelled nonce without a matching transfer is not treated as failure.

Known transactions are checked directly. Otherwise nonce discovery scans at most 10,000 finalized blocks; older transfers may need the hash hint. Legacy attempts without a recorded deadline can recover as successful but cannot automatically fail as expired-unused. Crashes before the server records an attempt remain unresolved; reconciliation does not resend their proof or release their reservations.

A successful reconciliation commits the receipt, attempt state and hashed private audit record atomically. Terminal order states and original receipt retention are immutable; concurrent completion wins over an older pending observation. The response contains `quote_id`, `state`, `outcome`, `reason`, `checked_at`, `evidence_sha256` and an optional receipt. The private audit retains the finalized-block/evidence summary without raw RPC responses, provider URLs, bearers or signatures.

The hosted SDK validates the recovered immutable artifact before marking its budget spent. Definitively failed orders release reservations; pending/not-started responses do not. Concurrent pending updates cannot demote terminal budget states. There is no background reconciliation job.

## Standard HTTP and native MCP clients

Official Python `x402_httpx_transport` and TypeScript `wrapFetchWithPayment` clients can buy a prepared quote's HTTP result resource. Atlas requires the standard `payment-identifier` extension, so configure the official client hook to attach an identifier before creating payment. Save the quote and recovery bearer privately for status/reconciliation; do not create a new authorization for an unknown existing order.

With `atlas mcp serve --provider hosted`, two additional tools extend the eight default tools:

- `atlas_purchase_x402(quote_id)` returns V2 `PaymentRequired` in both structured content and JSON text. The wallet retries with `_meta["x402/payment"]`; successful settlement is returned in `_meta["x402/payment-response"]`.
- `atlas_purchase_reconcile(quote_id, transaction=None)` performs the same capability-authenticated reconciliation without signing or paying.

Prepare a quote with `atlas_quote`. Payment payloads belong only in transport metadata. The native tool never creates a local signature: the external wallet enforces its own delegation and budgets. `atlas_purchase` remains the operator-policy/local-wallet path. Durable per-quote client claims prevent switching between these paths or requesting another external signature after an ambiguous transport failure.

The pinned x402 2.25.0 Python MCP wrapper expects camelCase result attributes and `timedelta` timeouts; MCP 2.3.0 uses snake_case and numeric seconds. Atlas supplies a transport-only adapter while the official SDK handles signing/payment creation:

```python
from atlas_terminal.payments.mcp_client import create_x402_mcp_session

# session is an initialized MCP ClientSession. payment_client is an official
# x402Client configured with the operator's spending controls and ID hook.
paid_session = create_x402_mcp_session(session, payment_client, auto_payment=True)
result = await paid_session.call_tool("atlas_purchase_x402", {"quote_id": quote_id})
```

Automatic payment defaults to false in this helper. Offline tests run the actual Python/TypeScript HTTP wrappers and native MCP messages over in-memory streams. Local keys are ephemeral; Facilitator/RPC transports are mocked. Node's HTTP bridge uses private pipes and makes no network request. Successful offline integration is not a real testnet transfer.

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
