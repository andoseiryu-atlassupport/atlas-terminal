# BLS seller API

The optional seller API delivers an immutable, prepared BLS series through x402. The code remains Apache-2.0. Original BLS information is freely available; the configured fee covers Atlas normalization and delivery. The seller is a separate application from free research and the annual-financial snapshot API.

`bls-series-v1` is one product type, with one curated BLS series per result. Operators choose which of the 22 existing BLS series to prepare. The catalog lists only prepared series, availability, capture time, source-access mode and price. Catalog entries and quotes also disclose the selected source query, observation-date bounds, unit, seasonal adjustment, period kinds and reported/missing record counts before payment. They do not contain observation values. Source units, seasonal adjustment, actual periods, annual averages, missing values, raw hashes and JSON pointers use the existing research normalization. No new score or imputed observation is introduced.

Mainnet receiving code and offline interoperability are implemented. **Actual Base mainnet transfers, live facilitator/RPC compatibility and public seller hosting remain unverified. No official public Atlas seller endpoint is operated.** A mock receipt is not a real payment, and replaying a saved real source is not a fresh BLS request.

## Run the fictional example

Use a new private state directory and separate server/client state in different processes:

```sh
ATLAS_STATE_DIR="$PWD/build/seller-demo/server" uv run --offline atlas seller refresh cpi --fixture
ATLAS_STATE_DIR="$PWD/build/seller-demo/server" uv run --offline atlas seller serve --payments mock
```

In another terminal:

```sh
export ATLAS_STATE_DIR="$PWD/build/seller-demo/client"
export ATLAS_SELLER_ORIGIN=http://127.0.0.1:8000
uv run --offline atlas seller catalog
uv run --offline atlas seller quote cpi monthly-review-001
uv run --offline atlas seller preflight QUOTE_ID
```

`examples/seller_mock.py` exercises a complete in-memory purchase and recovery with fictional observations and mock settlement. It makes no network request and needs no wallet. `atlas seller recover QUOTE_ID` retrieves an already purchased result; it does not sign or buy.

## Prepare source data separately

The HTTP application cannot refresh, import or retrieve arbitrary source data. Buyer requests read `seller-products.sqlite3`; the quote freezes the full result in the separate `seller-orders.sqlite3` database. Updating a published series cannot change an already quoted or purchased artifact.

An operator can explicitly refresh one series through the existing BLS transport and shared cache/quota store:

```sh
uv run atlas seller refresh cpi --allow-network
```

This is an actual source operation. Do not run it when preserving a source quota or when network access is not authorized. Default refresh uses the BLS latest-three-year request, a six-hour cache TTL and the existing persistent 25-attempt-per-UTC-day limit. The upstream shared-IP limit remains separate. No automatic schedule is installed. Repeated cache use retains the original retrieval time.

Alternatively, import an existing private source body without requesting BLS:

```sh
uv run --offline atlas seller import-capture build/capture/metadata.json build/capture/body.json
```

The metadata must contain `series`, `url`, `retrieved_at` (timezone-aware), and `sha256`. Dated captures also require `start_date` and `end_date`; the official URL must match the series and selected years. One capture covers at most ten calendar years. Hash, source identity, periods and response bounds are checked before publication. Imported provenance is labeled `retained_capture`: its origin and retrieval time are operator-supplied, not newly authenticated by Atlas. `source_response` means the operator's refresh used the source transport, including its existing cache. `fictional_fixture` can be delivered only in loopback `off`/`mock` modes.

All-missing or empty series cannot be offered. Partial missing observations remain null. The default maximum source-capture age is **172800 seconds (48 hours)**, independently of the observation dates. Expired series are unavailable for new quotes rather than silently refreshed or presented as current. An operator may explicitly configure 60 seconds through 366 days; the catalog exposes that limit. This age check does not prove a complete latest release. Existing purchases remain recoverable for their separate retention period.

Each raw and normalized capture is limited to 2 MiB. The publication store is limited to the curated series and 64 MiB of logical raw/normalized content. Orders retain the existing 128-quote/64-MiB limits, five-minute quote expiry and at least 24 hours of purchased-artifact recovery. Unknown settlements and intent bindings are not silently discarded. A full store stops new publication/orders; protect and monitor the persistent volume.

## Payment modes and configuration

| Mode      | Scope                               | Payment                                        |
| --------- | ----------------------------------- | ---------------------------------------------- |
| `off`     | Loopback development                | Free, no signature                             |
| `mock`    | Loopback development                | Fictional headers and simulated receipts       |
| `testnet` | Prepared real source captures       | Base Sepolia USDC, external wallet             |
| `mainnet` | Explicit public HTTPS seller origin | Base USDC, external wallet and CDP facilitator |

The annual API and local `TestnetSigner` still reject mainnet. Selecting the seller service is a separate action; environment changes cannot convert the annual endpoint into a mainnet endpoint. The seller never requires the **receiving wallet's private key**. The buyer's wallet signs; the facilitator verifies and submits the payment to the configured recipient.

Install the optional server authentication dependency with `uv sync --locked --extra seller`. It pins `cdp-sdk==1.52.0`; the existing x402 SDK remains `2.25.0`. Ordinary research, mock mode and buyer discovery do not require CDP credentials.

| Server setting                         | Meaning                                                                                                               |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ATLAS_STATE_DIR`                      | Dedicated private persistent volume, never shared with an unrelated service                                           |
| `ATLAS_PUBLIC_ORIGIN`                  | Exact operator-controlled HTTPS origin, without a path                                                                |
| `ATLAS_SELLER_PAYMENTS`                | Explicit mode; default `off`                                                                                          |
| `ATLAS_PRICE_ATOMIC`                   | Integer USDC base units; `10000` is an example **0.01 USDC per immutable result**, not an announced commercial tariff |
| `ATLAS_PAY_TO`                         | Public Base USDC receiving address                                                                                    |
| `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET` | Private server credentials for the configured CDP facilitator                                                         |
| `ATLAS_MAINNET_RPC_URL`                | Private trusted HTTPS Base RPC supporting finalized blocks, EIP-1898 calls, receipts and logs                         |
| `ATLAS_SELLER_MAX_SOURCE_AGE_SECONDS`  | Source-capture age limit; default 172800                                                                              |

The mainnet profile pins `eip155:8453`, USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, six decimals, the `USD Coin`/`2` signing domain and exact/EIP-3009. CDP requests use `https://api.cdp.coinbase.com/platform/v2/x402`, short-lived endpoint-bound authentication generated by the official SDK, and no redirects. Facilitator/API credentials are distinct from a wallet signing key. Facilitator pricing, supported jurisdictions and account terms are the operator's responsibility; [official seller documentation](https://docs.cdp.coinbase.com/x402/seller/quickstart) describes its current service.

Testnet uses explicit `ATLAS_FACILITATOR_URL` and optional `ATLAS_TESTNET_RPC_URL`. Both real-payment modes reject fictional source products. Mainnet requires a configured reconciliation RPC at startup. `atlas seller doctor` reports missing configuration as booleans without loading a payer key or making requests. A configuration-only `ready` result does not prove successful payment, hosting, source completeness or a current release.

## HTTP and SDK

| Method | Path                                     | Behavior                                                     |
| ------ | ---------------------------------------- | ------------------------------------------------------------ |
| GET    | `/v1/seller/catalog`                     | Inspect available products, age policy and prices            |
| POST   | `/v1/seller/quotes`                      | Freeze an existing prepared result and return a quote        |
| GET    | `/v1/seller/quotes/{quote_id}/result`    | x402 challenge, paid delivery or authenticated recovery      |
| GET    | `/v1/seller/quotes/{quote_id}/preflight` | Read advisory order state without signing                    |
| GET    | `/v1/seller/quotes/{quote_id}/status`    | Recover an existing order's status                           |
| POST   | `/v1/seller/quotes/{quote_id}/reconcile` | Read finalized-chain evidence and update an unresolved order |

The quote body is `{"product_id":"bls-series-v1","series":"cpi"}`. Query fields cannot set a price, recipient, origin, source URL, data-refresh policy or network. `POST` may include a private random `Atlas-Intent-Key` to replay the same purchase intent. The response's `Atlas-Recovery-Token` belongs in private client storage, never logs, tool output or URLs. A token retrieves status and paid data; it cannot pay for an unpaid result. A quote ID alone grants no recovery access.

```python
from atlas_terminal.seller_client import SellerClient

with SellerClient("https://api.example.com") as seller:
    quote = seller.quote({"series": "cpi"}, intent_id="monthly-review-001")
    print(quote.pricing.display_amount, quote.captured_at)
    print(seller.preflight(quote.quote_id).decision)
    # For a real-network quote, inspect the bound challenge without signing:
    terms, settlement, payment_required = seller.external_purchase(quote.quote_id)
    # A separately authorized external wallet can sign those terms with its own limits.
    # Pass its payload to external_purchase(quote.quote_id, payment=payload).
```

`SellerClient` persists private recovery and intent capabilities before making the quote request. It checks the returned query/resource/price tuple and purchased content hash. Once an external payment is submitted, a durable claim prevents a second signature or speculative resubmission after a timeout or restart. Use `status`, `reconcile` and `recover`. A transport failure before the server records the payment can remain unresolved; no exactly-once settlement guarantee or automatic refund mechanism is claimed.

Official x402 HTTP clients can use the prepared result resource and the required `payment-identifier` extension. Keep the original quote/capability and do not create a fresh authorization after an ambiguous response. Raw HTTP clients are responsible for the same durable retry behavior that `SellerClient` provides.

## MCP and Web clients

Set `ATLAS_SELLER_ORIGIN` and a private client `ATLAS_STATE_DIR`, then run `atlas seller mcp`. Seven tools expose catalog, quote, preflight, status, purchase, recovery and reconciliation. `atlas_seller_purchase` accepts only `quote_id` as a tool argument. A compatible external wallet submits the payload in `_meta["x402/payment"]`; the receipt uses `_meta["x402/payment-response"]`. The wallet must enforce its own delegated limits. No local mainnet signer is provided.

Web clients use the same HTTP responses rather than recalculating values or settlement state. Use [seller OpenAPI](../schemas/seller.openapi.json) and [product JSON Schema](../schemas/seller.schema.json). The product response is `{"data": ProductData, "receipt": Receipt}`; `data.research` retains the shared research envelope and full evidence. Display exact decimal strings, units, actual periods, nulls, capture age, `source_access`, `is_synthetic`, the quoted price and the receipt's independent `simulated` flag. A real source delivered through mock mode is still a simulated purchase. Treat source footnotes as untrusted text.

Return HTTP 202 as unresolved and offer status/reconciliation; do not turn it into another purchase. Keep CDP credentials, RPC provider keys and recovery capabilities out of browser bundles and model output. Browser wallet secrets remain with the buyer's wallet. The reference app accepts only its configured Origin; a separate Web origin should use a controlled server-side integration, not a wildcard proxy to free research.

## Prepare deployment

The single-process service binds to loopback behind operator-managed HTTPS. It has exact Host/Origin checks, eight concurrent requests, 120 requests/minute per process, 16-KiB bodies, a body deadline and persistent quote limits. It provides no tenant isolation, distributed limiter, uptime SLA, automated refresh, background reconciliation, tax/accounting integration or operator refund workflow.

```sh
uv run --offline python scripts/prepare_seller_deploy.py \
  --origin https://api.example.com --output build/seller-deploy
```

This only writes systemd, Caddy and private-environment templates. It does not register a domain, provision a host, create a wallet, configure a facilitator account, install a service or spend money. The environment template starts with payments off and blank credentials; incomplete production configuration fails closed. Use a dedicated host with a persistent volume, place the checked checkout at `/opt/atlas-terminal`, install locked seller dependencies, and keep `/etc/atlas-terminal/seller.env` root-owned with mode 0600. `StateDirectory=atlas-seller` belongs to the service's dynamic user; publish/import using that same service identity or correct private ownership before startup. Back up the whole SQLite state, including WAL/SHM files. Do not expose the annual/free-research application on the same paid origin.

Before public operation, configure a private security-reporting contact, source-refresh operation, monitoring/backups and hosted-service terms. Confirm the receiving address, facilitator account and RPC, complete a testnet check, and perform an explicitly authorized minimal mainnet verification with recovery. No such live transfer is executed by the default test suite or deployment-preparation script.

## Verification

`uv run --offline pytest tests/test_seller_products.py tests/test_seller_mainnet.py tests/test_seller_interfaces.py` uses fictional bytes, ephemeral signing keys, mock facilitator/RPC responses and actual in-memory MCP sessions. These exercise real SDK code but no blockchain transfers.

`scripts/verify_seller_captures.py --output build/seller-replay` independently matches previously retained real BLS values, units, periods, hashes and pointers, then exercises mock purchase/recovery. It uses no network, changes no BLS cache/counter and writes private ignored output. The old captures exceed the normal 48-hour policy; the audit deliberately raises its local replay age limit and records default rejection. [Dated results and remaining limits](verification.md).
