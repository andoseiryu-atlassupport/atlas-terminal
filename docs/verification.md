# Validation scope

Recorded on 2026-10-20 for Atlas Terminal 0.2.0 with Python 3.12.14 and Node.js 24.19.0 on Linux.

## Verified behavior

342 offline tests pass; two live tests are deselected. Default tests prohibit IP sockets and do not retrieve live sources or transfer funds.

| Area                        | Coverage                                                                                                                                                                                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| All 21 research datasets    | Retrieval from fictional JSON/XML source bytes and validation against the published schema                                                                                                                                                                |
| Financial periods and units | Annual/quarter/YTD distinctions, no synthesized Q4, EPS units, missing/conflicting values and amendments                                                                                                                                                  |
| Ratios                      | Input provenance, matching periods, zero denominators and Decimal calculations                                                                                                                                                                            |
| Growth and cash flow        | Actual prior-year quarter/YTD matching, 52/53-week years, missing/nonpositive baselines, concept changes, FCF input periods/signs, exact subtraction and stable rounding                                                                                  |
| Aligned comparison          | Common reported ends across independent fictional issuers, mismatched current/prior-year dates, missing common periods, and separate reported/derived coverage                                                                                            |
| Ownership                   | Form 3/4/5 precision, missing prices and footnotes; 13F namespaces, units and options                                                                                                                                                                     |
| Economics, yields and debt  | BLS series identity and annual averages, null yields and exact debt values                                                                                                                                                                                |
| Reference FX and rates      | ECB quote direction, inverse/cross arithmetic, same-day inputs, missing rates/locators, malformed/duplicate records; EFFR/SOFR identity, percent units, effective dates, revisions and notices                                                            |
| Macro calculations          | Exact CPI calendar-month baselines, missing/zero/ambiguous inputs, M13 exclusion, payroll year boundaries and precision, same-day 10Y–2Y basis-point conversion, real-yield nulls and negative values                                                     |
| HTTP transport              | Mock HTTP checks for endpoint restrictions, cache, etag, rate/daily limits, response bounds and finite retries                                                                                                                                            |
| CLI / SDK / MCP / HTTP      | Consistent results and official MCP client communication with a separate process                                                                                                                                                                          |
| Agent contracts             | Dataset-specific MCP query branches, actual metric/series enums, validated common output schemas, filtered/compact catalogs and sanitized validation hints                                                                                                |
| Research presentation       | Lossless evidence reconstruction for all 21 datasets, FX projection context, no-refetch pages across restarts, cursor binding, expiry, integrity, bounded-store rollback and fixture/public isolation                                                     |
| Recovery guidance           | Structured next actions, known retry delays and HTTP headers, BLS UTC reset hints, stable error IDs and purchase-transport guidance without renewed signing                                                                                               |
| Optional payments           | Offline x402 wire handling, local signatures, mock Facilitator, budget, authorization and failure recovery                                                                                                                                                |
| Finalized reconciliation    | Exact nonce/USDC transfer matching, canonical finalized blocks, bounded discovery and hash hints; expired-unused failure, inconsistent/unavailable RPC, legacy attempts, crashes, concurrent completion and atomic audit rollback                         |
| Official client interop     | Python HTTPX and TypeScript fetch wrappers, plus the official Python x402 MCP client over real MCP messages; successful and unknown settlement, metadata transport and prevention of repeated signing                                                     |
| Packages                    | Wheel installation in a new virtual environment and CLI/MCP/HTTP execution outside the repository                                                                                                                                                         |
| CI                          | [Published baseline run](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39428101933): types, formatting, 158 tests, demos and builds. Separate from local verification of expanded analytics, x402, agent contracts and macro/FX. |

Research endpoints remain free of payment requirements in every payment mode. Mock-purchase checks cover authenticated recovery after an API restart. Wheel and sdist packages contain source fixtures and the Apache-2.0 LICENSE, without private runtime databases.

## Local validation results

| Check                                                                        | Result                                  |
| ---------------------------------------------------------------------------- | --------------------------------------- |
| `uv run --offline ruff check .` and `uv run --offline ruff format --check .` | PASS                                    |
| `uv run --offline mypy`                                                      | PASS; 30 source files                   |
| `uv run --offline pytest`                                                    | PASS; 342 passed, 2 deselected          |
| `uv run --offline python -m build --no-isolation`                            | PASS; wheel and sdist                   |
| `npm run check`                                                              | PASS; Prettier and whitespace checks    |
| `python scripts/scan_public_files.py`                                        | PASS; heuristic credential-pattern scan |

The free-research and mock-purchase examples run successfully. The built wheel was installed into a fresh virtual environment using the exported locked runtime requirements. `scripts/verify_installed.py` passed outside the repository for CLI, official stdio MCP, loopback HTTP, compact research/evidence, page continuation, mock purchase and server restart recovery. All four new macro/FX datasets returned identical synthetic results across the installed CLI, SDK, MCP and HTTP. Additional earlier installed-wheel CLI/SDK checks verified evidence and page recovery across separate processes; earlier checks also covered reconciliation, hosted MCP tools, growth, FCF and aligned comparison. Runtime checks made no external requests or real transfers. Local results do not establish a new GitHub Actions run or live-source validation.

The interoperability tests use the pinned official x402 Python and JavaScript clients. JavaScript dependencies require `npm ci`; the fetch bridge uses private pipes rather than IP sockets. Native MCP uses in-memory client/server streams and the transport adapter for the pinned MCP 2.x API. Facilitator and chain RPC responses are fixtures, not live evidence.

## Unverified behavior

Live SEC, BLS, Treasury, Federal Reserve, ECB and New York Fed responses and runtime availability; real Base Sepolia transfers and reconciliation against a live RPC; and execution on Windows are unverified. ECB reference-rate documentation/conditions, New York Fed reference-rate terms and Treasury XML documentation were inspected for the macro/FX expansion. The NY Fed API documentation page could not be read fully; current terms for other sources and final dataset-specific redistribution checks remain unverified. Offline tests do not establish source availability, completeness, delivery latency or successful real settlement.

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
uv run --offline python examples/mock_purchase.py
uv run --offline python -m build --no-isolation
npm run check
python scripts/scan_public_files.py
```

`scripts/verify_installed.py` uses the installed package to check CLI, MCP, loopback HTTP, mock purchases and restart recovery without external requests or real transfers.

Live checks in `scripts/research_smoke.py` and `scripts/smoke.py` require `--allow-network`. Real testnet transfers also require dedicated configuration and a transfer opt-in flag. Default CI does not run these checks.

[Capabilities](../IMPLEMENTATION_STATUS.md) · [Sources and terms](../DATA_SOURCES.md) · [Contributing](../CONTRIBUTING.md)
