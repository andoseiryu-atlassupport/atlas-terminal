# Validation scope

Recorded on 2026-10-20 for Atlas Terminal 0.2.0 with Python 3.12.14 on Linux.

## Verified behavior

194 offline tests pass; two live tests are deselected. Default tests prohibit IP sockets and do not retrieve live sources or transfer funds.

| Area                        | Coverage                                                                                                                                                                                                                  |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| All 17 research datasets    | Retrieval from fictional JSON/XML source bytes and validation against the published schema                                                                                                                                |
| Financial periods and units | Annual/quarter/YTD distinctions, no synthesized Q4, EPS units, missing/conflicting values and amendments                                                                                                                  |
| Ratios                      | Input provenance, matching periods, zero denominators and Decimal calculations                                                                                                                                            |
| Growth and cash flow        | Actual prior-year quarter/YTD matching, 52/53-week years, missing/nonpositive baselines, concept changes, FCF input periods/signs, exact subtraction and stable rounding                                                  |
| Aligned comparison          | Common reported ends across independent fictional issuers, mismatched current/prior-year dates, missing common periods, and separate reported/derived coverage                                                            |
| Ownership                   | Form 3/4/5 precision, missing prices and footnotes; 13F namespaces, units and options                                                                                                                                     |
| Economics, yields and debt  | BLS series identity and annual averages, null yields and exact debt values                                                                                                                                                |
| HTTP transport              | Mock HTTP checks for endpoint restrictions, cache, etag, rate/daily limits, response bounds and finite retries                                                                                                            |
| CLI / SDK / MCP / HTTP      | Consistent results and official MCP client communication with a separate process                                                                                                                                          |
| Optional payments           | Offline x402 wire handling, local signatures, mock Facilitator, budget, authorization and failure recovery                                                                                                                |
| Packages                    | Wheel installation in a new virtual environment and CLI/MCP/HTTP execution outside the repository                                                                                                                         |
| CI                          | [Published baseline run](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/runs/39428101933): types, formatting, 158 tests, demos and builds. Separate from local verification of the expanded analytics. |

Research endpoints remain free of payment requirements in every payment mode. Mock-purchase checks cover authenticated recovery after an API restart. Wheel and sdist packages contain source fixtures and the Apache-2.0 LICENSE, without private runtime databases.

## Local validation results

| Check                                                                        | Result                                  |
| ---------------------------------------------------------------------------- | --------------------------------------- |
| `uv run --offline ruff check .` and `uv run --offline ruff format --check .` | PASS                                    |
| `uv run --offline mypy`                                                      | PASS; 25 source files                   |
| `uv run --offline pytest`                                                    | PASS; 194 passed, 2 deselected          |
| `uv run --offline python -m build --no-isolation`                            | PASS; wheel and sdist                   |
| `npm run check`                                                              | PASS; Prettier and whitespace checks    |
| `python scripts/scan_public_files.py`                                        | PASS; heuristic credential-pattern scan |

The free-research and mock-purchase examples run successfully. The built wheel was installed into a fresh virtual environment using the exported locked runtime requirements. `scripts/verify_installed.py` passed outside the repository for CLI, MCP, loopback HTTP, mock purchase and restart recovery. Additional installed-wheel SDK checks verified growth, FCF and aligned comparison. Runtime checks made no external requests or real transfers. Local results do not establish a new GitHub Actions run or live-source validation.

## Unverified behavior

Live SEC, BLS, Treasury and Federal Reserve access; the current state of official terms pages; real Base Sepolia transfers; and execution on Windows are unverified. Offline tests do not establish source availability, completeness, delivery latency or successful real settlement.

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
