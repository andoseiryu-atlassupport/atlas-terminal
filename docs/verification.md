# Validation scope

Recorded on 2026-10-23 for Atlas Terminal 0.6.0, with Python 3.12.14 and Node.js 24.19.0 on Linux. Local verification, GitHub Actions, public deployment, official-source access and model observations are distinct evidence.

The current source tree additionally includes the unreleased experimental fundamental risk extension described below. The published 0.6.0 release does not include that extension; source-tree checks and a locally built wheel are separate from release publication.

## Current local checks

| Check                                                     | Observed result                                             |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| `uv run --offline ruff check .`                           | PASS                                                        |
| `uv run --offline ruff format --check .`                  | PASS                                                        |
| `uv run --offline mypy`                                   | PASS; 51 source files                                       |
| `uv run --offline pytest`                                 | PASS; 699 tests, 2 live tests deselected                    |
| `uv run --offline python -m build --no-isolation`         | PASS; wheel and source distribution                         |
| `uv run --offline python scripts/verify_distribution.py`  | PASS; metadata, required contents and forbidden paths       |
| `uv run --offline python scripts/build_docs.py`           | PASS; 41 HTML pages and internal file/section links         |
| `uv run --offline python scripts/prepare_distribution.py` | PREPARED; versioned plugin ZIP and Registry draft           |
| `npm run check`                                           | PASS; Prettier and whitespace                               |
| `python scripts/scan_public_files.py`                     | PASS; heuristic working-file and reachable-history patterns |

Default tests prohibit IP sockets and use fictional sources or mocked transports. They do not retrieve live official data or transfer funds. Archive validation rejects private/runtime paths, dependency trees, unsafe paths and links; source archives contain project source and verification material rather than installed dependencies. The credential-pattern scan is a heuristic, not a guarantee against every secret format.

## Installed distributions and MCP execution

The 0.6.0 wheel was installed offline into a fresh virtual environment from locked runtime requirements. Run outside the repository, `scripts/verify_installed.py` passed installed CLI, SDK, actual stdio MCP and loopback HTTP checks. Covered behavior includes SDMX discovery/international observations, macro panels, company/macro/FX research, compact evidence and saved pagination, quality gates, presets, configuration diagnosis and source-check network opt-in. Payment checks used mock quotes, preflight/budget/task replay and restart recovery without real transfers.

The installed `scripts/verify_remote_installed.py` also passed against an actual loopback Streamable HTTP process using the official MCP client. It checks six research-only tools, preset execution, evidence resolution after restart, authentication and invalid Host/Origin handling. This is a local protocol check with fictional sources, not a public endpoint deployment.

`scripts/run_research_workflow.py` completes seven contract checks through one official stdio MCP session: tool discovery, configuration, four independent company/CPI/FX reads, original evidence resolution, explicit quality rejection and unsupported-dataset rejection. It makes no LLM requests.

`scripts/capture_research_examples.py` completes three cases in one actual research-only stdio MCP session and retains compact results and every resolved evidence reference. The executed captures are fictional. Nineteen publication/measurement tests cover content-addressed bytes, overwrite rejection, synthetic/failed/incomplete/evidence-mismatched export rejection, complete public JSON links, search ownership tags, controlled prompts, unknown rates, reviewed/unknown outcomes and invalid/duplicate/credential-bearing observation rejection. Mock non-synthetic metadata exists only inside an offline publication-format test and is not live-source evidence.

## Experimental fundamental risk extension

`uv run --offline pytest tests/test_risk_model.py` passes 54 fictional-data tests. They cover independently specified factor recovery, constrained industry identification, covariance symmetry/positive semidefiniteness, Euler contribution additivity, benchmark-relative zero risk, factor shocks, insufficient history/universe/rank, availability cutoffs, delayed closes, later-vintage/future-return exclusion, missing/duplicate/invalid input rejection, content hashes, private permissions, deterministic restart and caller-context isolation, atomic quota failure and holdout baseline calculations. No input is a real-market observation.

The actual official stdio MCP client imports a complete panel, fits a model and analyzes a portfolio through one connection. Other checks cover the CLI, SDK, opt-in HTTP route and official Streamable HTTP client with an in-memory ASGI transport. Remote anonymous risk mode is rejected. Existing default tool sets retain their six research-only tools; enabling risk adds one state-writing tool. IP sockets remain disabled in the default suite.

`atlas risk demo --json` completes a 60-stock/four-style/60-estimation-session example with risk attribution, tracking error, a common-factor shock and 20 subsequent sessions versus a historical EWMA portfolio baseline. The wheel built from this source tree was installed offline into a new virtual environment from locked runtime requirements; the same demo passed outside the repository using installed package files. This source-tree wheel is not a new published release. Its synthetic numbers do not establish forecast accuracy.

The model has no equity-price feed, independently verified PIT vintages, corporate-action/security-master reconciliation, changing-universe engine or actual-data calibration study. Input rights and availability timestamps remain caller assertions. The balanced supplied universe, coarse SIC divisions, approximate residual degrees-of-freedom adjustment, diagonal specific covariance and fixed-exposure annualization are material methodological limits. See [risk methodology and usage](risk-model.md).

## Data and payment coverage

Offline cases cover all 26 datasets, exact financial periods and decimals, missing/conflicting inputs, financial formulas, source hashes and JSON/XML locators. Series discovery includes registered templates and IMF/OECD metadata. International parsers check dimensions, periods, status/units and unsafe XML/URL rejection. Panels check last/mean aggregation, missing months, actual input dates, changing units/base/adjustment, unsupported upsampling, bounded daily chunks and independent errors. OECD attempt quotas persist across clients.

Eight BEA and ten Japanese macro series, bounded BLS history and ECB history are covered by fictional bytes and parser/interface tests. Metadata-probe success does not establish successful observation retrieval. Source ages, observation periods, publication times and retrieval times remain distinct. Current revised source snapshots are not historical release vintages or PIT data.

Official x402 Python and JavaScript interoperability tests use mocked facilitator/RPC responses and private pipes or in-memory streams. Research-only MCP has no payment handlers. Real Base Sepolia settlement and reconciliation against a live RPC are unverified; mainnet is unsupported.

## Public documentation and discovery

The documentation website is deployed at [the configured public origin](https://atlas-terminal-docs.citrus-genet-7815.chatgpt.site). The static build includes canonical URLs, a sitemap, factual metadata, Markdown alternatives, root crawler intent and a noindex 404 page. The host uses extensionless HTML URLs. Site publication and the [public-access workflow](https://github.com/andoseiryu-atlassupport/atlas-terminal/actions/workflows/public-checks.yml) have their own observable outcomes; a local HTML build does not establish either.

Twenty connected web-search probes on 2026-10-23 returned no exact Atlas repository/documentation URL. The bounded result URLs are retained in [the probe record](../evals/search-probes-2026-10-23.json); one URL matching a credential pattern was omitted without publishing its value. These observations do not establish search-index exclusion. The manual answer template still has no model observations and an unknown citation rate. Synthetic workflow success is excluded from model task-success rates.

Simulated User-Agent checks do not establish access from official crawler IPs, search-console ownership, indexing or AI citations. The three research guides currently include no completed actual-source observation snapshots. PyPI publication, official MCP Registry registration, OpenAI plugin review and an official public remote MCP endpoint are not established. The manual trusted-publisher workflow, ownership-tag flags, plugin ZIP and Registry metadata are prepared only.

## Unverified behavior

Live SEC, BLS, Treasury, Federal Reserve, ECB, New York Fed, BEA, BOJ, Statistical Dashboard, IMF and OECD responses and runtime availability remain unverified. The execution environment permits package/GitHub hosts but not the selected official-data endpoints. SEC and BEA also require private operator configuration. Source documentation inspection and mock PASS results do not establish live availability, completeness, delivery latency or final dataset-specific redistribution rights.

Windows execution, actual public remote uptime, live RPC/facilitator recovery, model-driven research success and comparative latency/token benchmarks are unverified. Local command outcomes do not establish a new GitHub Actions result. See [publication setup](publication-setup.md), [source checks](source-checks.md), [research captures](research-examples.md) and [source terms](../DATA_SOURCES.md).
