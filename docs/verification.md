# Validation scope

Recorded on 2026-10-22 for Atlas Terminal 0.3.1 with Python 3.12.14 and Node.js 24.19.0 on Linux.

## Verified behavior

437 offline tests pass; two live tests are deselected. Default tests prohibit IP sockets and do not retrieve live sources or transfer funds.

| Area                         | Coverage                                                                                                                                                                                                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| All 21 research datasets     | Retrieval from fictional JSON/XML source bytes and validation against the published schema                                                                                                                                                                                       |
| Financial periods and units  | Annual/quarter/YTD distinctions, no synthesized Q4, EPS units, missing/conflicting values and amendments                                                                                                                                                                         |
| Ratios                       | Input provenance, matching periods, zero denominators and Decimal calculations                                                                                                                                                                                                   |
| Growth and cash flow         | Actual prior-year quarter/YTD matching, 52/53-week years, missing/nonpositive baselines, concept changes, FCF input periods/signs, exact subtraction and stable rounding                                                                                                         |
| Aligned comparison           | Common reported ends across independent fictional issuers, mismatched current/prior-year dates, missing common periods, and separate reported/derived coverage                                                                                                                   |
| Ownership                    | Form 3/4/5 precision, missing prices and footnotes; 13F namespaces, units and options                                                                                                                                                                                            |
| Economics, yields and debt   | BLS series identity and annual averages, null yields and exact debt values                                                                                                                                                                                                       |
| Reference FX and rates       | ECB quote direction, inverse/cross arithmetic, same-day inputs, missing rates/locators, malformed/duplicate records; EFFR/SOFR identity, percent units, effective dates, revisions and notices                                                                                   |
| Macro calculations           | Exact CPI calendar-month baselines, missing/zero/ambiguous inputs, M13 exclusion, payroll year boundaries and precision, same-day 10Y–2Y basis-point conversion, real-yield nulls and negative values                                                                            |
| HTTP transport               | Mock HTTP checks for endpoint restrictions, cache, etag, rate/daily limits, response bounds and finite retries                                                                                                                                                                   |
| CLI / SDK / MCP / HTTP       | Consistent results and official MCP client communication with a separate process                                                                                                                                                                                                 |
| Agent contracts              | Dataset-specific MCP query branches, actual metric/series enums, validated common output schemas, filtered/compact catalogs and sanitized validation hints                                                                                                                       |
| Research presentation        | Lossless evidence reconstruction for all 21 datasets, FX projection context, no-refetch pages across restarts, cursor binding, expiry, integrity, bounded-store rollback and fixture/public isolation                                                                            |
| Quality requirements         | Oldest source/input age, exact age boundaries, observation/filing/period distinctions, unknown/future timestamps, monthly/ownership dates, missing primary amounts, derivation/truncation, projection/page isolation and continuation aging                                      |
| Research batches             | Ordered independent results, malformed-envelope rejection before reads, per-item validation/quality/transient errors, no automatic retry, response bounds, safe errors, CLI exit codes and real MCP/HTTP contracts                                                               |
| Source-check instrumentation | Explicit network opt-in, mock HTTP capture, value-independent structure changes, observation regression, failure retention, baseline integrity, fixture/config NOT_RUN and revalidation/shared BLS daily quotas                                                                  |
| Recovery guidance            | Structured next actions, known retry delays and HTTP headers, BLS UTC reset hints, stable error IDs and purchase-transport guidance without renewed signing                                                                                                                      |
| Optional payments            | Offline x402 wire handling, local signatures, mock Facilitator, budget, authorization and failure recovery                                                                                                                                                                       |
| Finalized reconciliation     | Exact nonce/USDC transfer matching, canonical finalized blocks, bounded discovery and hash hints; expired-unused failure, inconsistent/unavailable RPC, legacy attempts, crashes, concurrent completion and atomic audit rollback                                                |
| Official client interop      | Python HTTPX and TypeScript fetch wrappers, plus the official Python x402 MCP client over real MCP messages; successful and unknown settlement, metadata transport and prevention of repeated signing                                                                            |
| Packages                     | Wheel installation in a new virtual environment and CLI/MCP/HTTP execution outside the repository                                                                                                                                                                                |
| CI                           | [Published baseline run](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39428101933): types, formatting, 158 tests, demos and builds. Separate from local verification of expanded analytics, x402, agent contracts, macro/FX, requirements and batches. |

Preset tests cover all three recipes, exact preview expansion, rejection before reads, selected-item execution, common/per-item requirements, original batch/provenance preservation, explicit missingness, UTC publication times and distinct observation/retrieval times. SDK, HTTP, CLI and real stdio MCP exercise the same contracts. Every local OpenAPI reference resolves in the exported document; shared query schemas validate requests using component references. Diagnostic tests cover configuration-only operation without source clients/stores, private-contact redaction, fixture isolation, invalid network flags and actual probe logic against mock HTTP. Mock HTTP probe success is not live verification.

Research endpoints remain free of payment requirements in every payment mode. Mock-purchase checks cover authenticated recovery after an API restart. Wheel and sdist packages contain source fixtures and the Apache-2.0 LICENSE, without private runtime databases.

Source distributions explicitly select project paths and exclude installed dependencies, generated output and private state independently of VCS ignore behavior. `scripts/verify_distribution.py` checks source/wheel contents, package version, license and the Registry ownership marker before CI/release publication. A source archive rebuilt outside Git with a fictional `node_modules` directory passes the same archive checks, confirming the development dependency is excluded. The current source archive contains 172 entries; the wheel contains 68 files.

## Local validation results

| Check                                                                        | Result                                  |
| ---------------------------------------------------------------------------- | --------------------------------------- |
| `uv run --offline ruff check .` and `uv run --offline ruff format --check .` | PASS                                    |
| `uv run --offline mypy`                                                      | PASS; 37 source files                   |
| `uv run --offline pytest`                                                    | PASS; 437 passed, 2 deselected          |
| `uv run --offline python -m build --no-isolation`                            | PASS; wheel and sdist                   |
| `npm run check`                                                              | PASS; Prettier and whitespace checks    |
| `python scripts/scan_public_files.py`                                        | PASS; heuristic credential-pattern scan |

The free-research and mock-purchase examples run successfully. The built wheel was installed into a fresh virtual environment using the exported locked runtime requirements. `scripts/verify_installed.py` passed outside the repository for CLI, official stdio MCP, loopback HTTP, compact research/evidence, page continuation, mock purchase and server restart recovery. All four new macro/FX datasets returned identical synthetic results across the installed CLI, SDK, MCP and HTTP. Additional earlier installed-wheel CLI/SDK checks verified evidence and page recovery across separate processes; earlier checks also covered reconciliation, hosted MCP tools, growth, FCF and aligned comparison. Runtime checks made no external requests or real transfers. Local results do not establish a new GitHub Actions run or live-source validation.

The installed-wheel verifier also checks requirement-gated batches and independent invalid-query errors through CLI, SDK, real stdio MCP and loopback HTTP, plus the source-check command's network opt-in guard. The packaged four-query batch example succeeds offline. Source-check fingerprints and persistence are exercised only with mock HTTP; no mocked PASS is recorded as live verification.

The fresh installed wheel also passes named FX-context preset execution through CLI, SDK, real stdio MCP and loopback HTTP, including original values and missingness/publication-time reporting. SDK preview returns no batch; configuration diagnostics work through all four interfaces without live source access. All three saved preset examples execute against fictional packaged sources. The MCP server exposes twelve default tools. No external network or real transfers are used by these checks.

Research-only Streamable HTTP is exercised with the pinned official MCP client using an offline ASGI transport. Tests cover initialize/list/call, six research tools without payment handlers, preset execution, compact evidence and state-volume recovery, unknown input fields, authentication, exact Host/Origin checks, body-size and body-read timeout limits, ingress quotas, default-port normalization and multiple-worker rejection. These tests use fictional data and do not establish a public deployment or live source availability.

`scripts/verify_remote_installed.py` also starts the installed wheel's actual loopback HTTP process, connects the official Streamable HTTP client, executes a fictional FX/SOFR preset, resolves its evidence, and verifies evidence lookup after a process restart. Authentication and invalid Host/Origin requests are checked against the real loopback process. The original installed-wheel CLI/SDK/stdio/HTTP and mock-recovery verifier also passes. No external source requests or transfers are made.

`scripts/build_docs.py` produces 30 HTML documentation pages with canonical metadata, Markdown alternatives, a sitemap, supplemental `llms.txt` and a noindex 404 page. All generated internal file and section links pass offline verification. `scripts/prepare_distribution.py` produces a portable research-only stdio plugin ZIP and versioned MCP Registry metadata; anonymous HTTPS remote metadata generation is also checked with a fictional `.invalid` endpoint, without contacting it. Generated metadata is not publication or platform approval.

Public tool/package documentation links use the GitHub source guides. The [documentation website](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site) is deployed successfully through Sites with public access. Its generated canonical, sitemap, Markdown and crawler files use that origin, and the deployment's rendered screenshot was inspected. The separate search-fetch tool could not retrieve the new domain, so external crawler access, response headers, search indexing and citation behavior remain unverified. PyPI publication, official MCP Registry listing and OpenAI plugin review are not completed. The optional Pages workflow requires configured Pages and `PAGES_ENABLED=true`. Local verification remains separate from GitHub Actions results and does not establish model task success.

The interoperability tests use the pinned official x402 Python and JavaScript clients. JavaScript dependencies require `npm ci`; the fetch bridge uses private pipes rather than IP sockets. Native MCP uses in-memory client/server streams and the transport adapter for the pinned MCP 2.x API. Facilitator and chain RPC responses are fixtures, not live evidence.

## Unverified behavior

Live SEC, BLS, Treasury, Federal Reserve, ECB and New York Fed responses and runtime availability; real Base Sepolia transfers and reconciliation against a live RPC; and execution on Windows are unverified. ECB reference-rate documentation/conditions, New York Fed reference-rate terms and Treasury XML documentation were inspected for the macro/FX expansion. The NY Fed API documentation page could not be read fully; current terms for other sources and final dataset-specific redistribution checks remain unverified. Offline tests do not establish source availability, completeness, delivery latency or successful real settlement.

During this verification the current enforced outbound policy excluded all official data hosts, and no private SEC contact setting was configured. Live source checks remain NOT_RUN in this environment. The separate manual source-check workflow was added but not executed; it does not establish continuous monitoring or a new CI result. [Operator instructions and required hosts](source-checks.md).

No model-driven benchmark establishes task completion, tool-call reduction or token savings. Compact response tests compare fictional payload bytes and verify lossless provenance; they are not LLM evaluations.

## Validation commands

```sh
uv sync --locked --all-extras --dev
npm ci
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline atlas demo --json
uv run --offline python examples/free_research.py
uv run --offline atlas research batch --input examples/research_batch.json --provider fixture --json
uv run --offline atlas research preset --input examples/research_presets/fx_context.json --provider fixture --json
uv run --offline atlas research doctor --provider fixture --json
uv run --offline python examples/mock_purchase.py
uv run --offline python -m build --no-isolation
uv run --offline python scripts/verify_distribution.py
uv run --offline python scripts/build_docs.py
uv run --offline python scripts/prepare_distribution.py
npm run check
python scripts/scan_public_files.py
```

`scripts/verify_installed.py` uses the installed package to check CLI, MCP, loopback HTTP, mock purchases and restart recovery without external requests or real transfers.

Run `scripts/verify_remote_installed.py` with the fresh installed wheel's Python interpreter to check the research-only Streamable HTTP process, official client and restart/evidence behavior on loopback. It uses packaged fixtures and creates a temporary private test token; it does not deploy a service or retrieve live observations.

Live checks in `scripts/research_smoke.py` and `scripts/smoke.py` require `--allow-network`. Real testnet transfers also require dedicated configuration and a transfer opt-in flag. Default CI does not run these checks.

[Capabilities](../IMPLEMENTATION_STATUS.md) · [Sources and terms](../DATA_SOURCES.md) · [Contributing](../CONTRIBUTING.md)
