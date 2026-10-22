# Contributing

Use Python 3.12+, uv and Node.js 24.

```sh
uv sync --locked --all-extras --dev
npm ci
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline mypy
uv run --offline pytest
uv run --offline python -m build --no-isolation
uv run --offline python scripts/build_docs.py
uv run --offline python scripts/prepare_distribution.py
npm run check
```

Work on a branch and review the [project scope](docs/project-brief.md), [capabilities](IMPLEMENTATION_STATUS.md) and [validation scope](docs/verification.md). Describe the checks actually run and any unverified behavior in the pull request.

For financial data issues, include the CIK, accession, taxonomy/tag, actual period, expected value and original source reference. Do not include a User-Agent containing personal contact details or a real purchase database. Prioritize tests for period selection, missing data, signature reuse and crash recovery.

Fixtures are fictional, and source hashes depend on their exact bytes. Do not reformat raw sources or expected outputs excluded from Prettier. Keep public documentation and templates in English, with README.md as the canonical README.

After research/error contract or HTTP route changes, run `uv run --offline python scripts/export_schemas.py`, then format the three files under `schemas/` with Prettier. This exporter makes no source requests or transfers and does not rewrite schema examples. The [agent guide](docs/agents.md) describes typed MCP, presentation and recovery contracts.

Default tests make no external requests or transfers. Live checks require the explicit opt-ins described in [payments](docs/payments.md) and must not run in CI for external contributions. The code is licensed under [Apache-2.0](LICENSE).
