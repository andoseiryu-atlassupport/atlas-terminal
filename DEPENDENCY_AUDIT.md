# Dependency and specification audit

Recorded on 2026-10-21 with Python 3.12.14, uv 0.12.23 and Node.js 24.19.0. Python versions were checked against PyPI metadata, the resolver and installed distributions, then locked in `pyproject.toml` and `uv.lock`. TypeScript interoperability dependencies were checked against npm metadata and installed exports, and locked in `package-lock.json`. Package-name availability and publication eligibility have not been checked.

## Direct dependencies

| Distribution    | Version | License      | Verified integration                                                    |
| --------------- | ------- | ------------ | ----------------------------------------------------------------------- |
| fastapi         | 0.151.0 | MIT          | create_app / TestClient / OpenAPI                                       |
| httpx           | 0.28.1  | BSD-3-Clause | Bounded streams / MockTransport / no redirects                          |
| mcp             | 2.3.0   | MIT          | MCPServer / ClientSession / stdio_client / structured content           |
| pydantic        | 2.14.1  | MIT          | Strict queries / JSON / exact financial strings                         |
| typer           | 0.27.3  | MIT          | CLI and TyperException input-error handling                             |
| uvicorn         | 0.54.0  | BSD-3-Clause | Single-worker loopback HTTP checks                                      |
| x402[evm,httpx] | 2.25.0  | MIT          | Official V2 models / payment identifiers / exact EVM / Facilitator HTTP |

Development dependencies are build 1.6.1, hatchling 1.32.4, jsonschema 4.26.0, mypy 2.4.0, pytest 9.1.1, pytest-asyncio 1.4.0, pytest-socket 0.8.1 and ruff 0.17.1. Documentation uses Prettier 3.9.10. Transitive dependencies are locked. [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) records declared license metadata for 89 distributions; this is not a vulnerability-database audit.

## SEC REST

Direct httpx calls centralize ticker, submissions/archive and companyfacts retrieval with Atlas's period-selection rules. [EdgarTools](https://github.com/dgunning/edgartools) was evaluated through its PyPI metadata (5.62.0, MIT, 41 requires_dist entries including extras), but is not installed or required. Atlas manages rates, caching and financial selection without its broader filing/table dependency set.

Live SEC access and current official pages are unverified. Mock HTTP tests cover two requests/second, at most two concurrent requests, 20 archives, response bounds and conditional caching.

## Official MCP SDK

The [official README](https://github.com/modelcontextprotocol/python-sdk) and installed 2.3.0 source were checked. The removed `mcp.server.fastmcp.FastMCP` API is not used; the server imports `MCPServer` from `mcp.server`.

Verified imports include `mcp.server.MCPServer`, `mcp.client.session.ClientSession`, `mcp.client.stdio.StdioServerParameters/stdio_client` and `mcp.types.CallToolResult/ToolAnnotations`. Python fields use snake_case and official SDK aliases define wire names. Middleware validates raw financial/research requests before coercion, rejecting unknown fields, PIT requests and boolean counts. The official client verifies initialize/list/call against a separate server process.

MCP 2.3.0 also supports `Annotated[CallToolResult, OutputModel]`, used to publish and validate research/evidence output schemas while preserving structured content and error results. The actual official stdio client exercises typed dataset queries, compact pages, evidence retrieval and sanitized errors. These features add no runtime dependencies.

## Official x402 SDK

The [official Python source](https://github.com/x402-foundation/x402/tree/main/python/x402), README, PyPI 2.25.0 and installed source were checked. Verified imports include:

```python
from x402 import x402ClientSync, x402ResourceServerSync
from x402.http import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.http.utils import (
    encode_payment_required_header,
    decode_payment_required_header,
    encode_payment_signature_header,
    decode_payment_signature_header,
    encode_payment_response_header,
)
from x402.mechanisms.evm.exact import ExactEvmClientScheme, ExactEvmServerScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.extensions.payment_identifier import (
    declare_payment_identifier_extension,
    append_payment_identifier_to_extensions,
    extract_payment_identifier,
    generate_payment_id,
)
```

A `payment_flows` annotation mismatch between `ExactEvmServerScheme` and the SDK protocol uses an explicit boundary cast, with real binding covered by offline tests.

`HTTPFacilitatorClientSync` follows redirects by default. Atlas passes a no-redirect httpx client through `FacilitatorConfig`. It bypasses `ResourceServer.settle_payment` pending retries and makes one official low-level settle call after a durable claim. No challenge is issued until the Facilitator's `/supported` response confirms V2 exact Base Sepolia support.

The selected SDK's `mechanisms/evm/default_assets.py` defines USDC network `eip155:84532`, contract `0x036CbD53842c5426634e7929541eC2318f3dCF7e`, decimals 6, and EIP-712 name `USDC` / version `2`. The current [Circle reference](https://developers.circle.com/stablecoins/usdc-contract-addresses) has not been rechecked; final verification remains necessary before real transfers.

Offline tests cover official 402 models, local EIP-3009 signing, eth-account signature recovery, Facilitator `/supported` / `/verify` / `/settle` wire handling and a generic official x402 client's purchase. They do not establish live Facilitator availability, authentication, chain inclusion or balances.

The official Python `x402_httpx_transport` and TypeScript `wrapFetchWithPayment` wrappers are exercised through complete offline HTTP exchanges. Native MCP uses `x402/payment` and `x402/payment-response` metadata. The pinned x402 MCP client expects camelCase result attributes and timedelta timeouts; MCP 2.3.0 exposes snake_case and numeric seconds. `payments/mcp_client.py` adapts those transport details without replacing official payment creation or signing.

Development-only npm packages `@x402/core`, `@x402/evm`, `@x402/extensions` and `@x402/fetch` are pinned to 2.25.0; `viem` is pinned to 2.48.11. These packages are not Python runtime dependencies. Their installed exports and licenses, plus transitive packages, are recorded in the notices inventory. The test bridge uses private pipes, no network fetches and ephemeral wallet keys.

Reconciliation uses bounded read-only Ethereum JSON-RPC against a separately configured trusted Base Sepolia RPC. It verifies finalized canonical blocks, EIP-1898 authorization state and USDC transfer/authorization events. RPC compatibility and finality are verified with mock transports; a live RPC and real token transfer remain unverified. No Facilitator status endpoint is assumed.

Service-specific AuthProvider integration, live connectivity, real Base Sepolia transfers and public transaction evidence remain unverified. Configuration failures do not fall back to mock mode. Explicit smoke opt-ins distinguish missing settings from actual execution.

## Licensing and CI

The code uses Apache-2.0. The full license text is included in [LICENSE](LICENSE), sourced from the [official Apache GitHub repository](https://github.com/apache/.github/blob/main/LICENSE). README and Python/npm metadata use the same SPDX identifier. Source-data and dependency terms are separate.

`actions/setup-python` v6 is pinned to verified SHA `ece7cb06caefa5fff74198d8649806c4678c61a1`. Checkout and setup-node are also SHA-pinned. [Validation scope](docs/verification.md) records CI and local verification evidence.

## Free research connectors

Research uses existing httpx, Pydantic, FastAPI and MCP dependencies plus standard-library `xml.etree.ElementTree`. Shared transport implements HTTP caching, rate limits and retries for SEC and other public sources. Parsing rejects DTD/entity declarations, UTF-16 and oversized XML. BLS, Treasury and Federal Reserve live access and current terms are unverified; connector evidence is limited to fixtures and mock HTTP. See [sources](DATA_SOURCES.md) and the [research guide](docs/research.md).
