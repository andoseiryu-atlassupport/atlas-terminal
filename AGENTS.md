# Atlas Terminal: agent instructions

Read `README.md`, `docs/project-brief.md`, `CONTRIBUTING.md` and `IMPLEMENTATION_STATUS.md` before changes. Work in this existing repository; preserve unrelated changes. Use Japanese project documentation and explanations, with the requested English README and clear English code identifiers.

## Product and data

- Use free public primary sources only. SEC issuer, financial and ownership disclosures plus BLS, Treasury and Federal Reserve context are supported. No paid data, UI, trading or LLM dependency. Do not claim consolidated real-time prices, order books, consensus estimates or complete news coverage.
- Research datasets share a validated query, source catalog and service across CLI / SDK / MCP / HTTP. Distinguish normalized facts, raw reported facts and filing-discovery metadata. Preserve units and explicit coverage limits.
- CLI / SDK / MCP / HTTP share application services. Never return expected fixtures directly as implementation.
- Join accession and filing reportDate. Do not select a period from fy/frame alone or derive missing values. Keep source bytes/hash, JSON pointer or XML path, filing and retrieval time. Research supports reported annual, discrete quarterly and YTD periods; never synthesize Q4 from annual minus YTD. Derived ratios must keep their exact inputs and formula.
- Current snapshots are not PIT data. Reject historical requests. Use exact integer/Decimal arithmetic and decimal strings.

## Payments and secrets

- Default off. Mock is fictional and offline. Actual integration is official x402 V2 exact/EIP-3009 on Base Sepolia USDC only. Reject mainnet even if environment variables change.
- Policy checks and durable budget reservations precede signing. Tools cannot alter policy, wallet, recipient or origin. Signing consumes a reservation atomically once.
- Persist a settlement claim before calling the facilitator. Unknown/crashed attempts remain pending; never re-sign or settle again speculatively. Do not invent a facilitator status API.
- Quote ID is not authorization. Keep recovery capabilities, private keys, signatures, contact settings and purchase databases out of output, logs and source control.
- Local policy is not an OS security boundary against a same-user shell. Do not claim production custody or exactly-once settlement.

## Work and verification

- Python 3.12+, locked uv dependencies; Node.js 24 for document checks. Verify actual SDK APIs when updating dependencies.
- Run `uv run --offline ruff check .`, `uv run --offline ruff format --check .`, `uv run --offline mypy`, `uv run --offline pytest`, `uv run --offline python -m build --no-isolation`, `npm run check`.
- Default tests must remain offline. Live SEC and testnet require explicit opt-in flags and private settings. Never convert mock PASS into real testnet PASS.
- Source fixtures are byte-exact: do not reformat `resources/sec_like`, `tests/fixtures`, or schema examples without deliberately regenerating provenance expectations.
- Describe current product capabilities in IMPLEMENTATION_STATUS.md. Record actual commands, results and limitations in docs/verification.md. Local checks and CI runs are separate evidence.
- Keep actions pinned to verified commit SHAs, use `pull_request` for external contributions, and grant read-only CI permissions.
- Publication, deployment and real transfers require an explicit operator instruction. The project code is licensed under Apache-2.0; keep LICENSE, README and package metadata consistent. Source-data and dependency licenses remain separate.

## Confidentiality

Keep private motivations, business hypotheses, owner strategy, conversations and handoff history outside tracked files and commit messages. Public documentation should describe usage, technical behavior and verified limitations. Store internal working notes outside this repository.
