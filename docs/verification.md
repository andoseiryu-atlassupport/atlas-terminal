# Validation scope

Recorded on 2026-10-23 for Atlas Terminal 0.6.0, with Python 3.12.14 and Node.js 24.19.0 on Linux. Local verification, GitHub Actions, public deployment, official-source access and model observations are distinct evidence.

## Current local checks

| Check                                                     | Observed result                                             |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| `uv run --offline ruff check .`                           | PASS                                                        |
| `uv run --offline ruff format --check .`                  | PASS                                                        |
| `uv run --offline mypy`                                   | PASS; 46 source files                                       |
| `uv run --offline pytest`                                 | PASS; 645 tests, 2 live tests deselected                    |
| `uv run --offline python -m build --no-isolation`         | PASS; wheel and source distribution                         |
| `uv run --offline python scripts/verify_distribution.py`  | PASS; metadata, required contents and forbidden paths       |
| `uv run --offline python scripts/build_docs.py`           | PASS; 40 HTML pages and internal file/section links         |
| `uv run --offline python scripts/prepare_distribution.py` | PREPARED; versioned plugin ZIP and Registry draft           |
| `npm run check`                                           | PASS; Prettier and whitespace                               |
| `python scripts/scan_public_files.py`                     | PASS; heuristic working-file and reachable-history patterns |

Default tests prohibit IP sockets and use fictional sources or mocked transports. They do not retrieve live official data or transfer funds. Archive validation rejects private/runtime paths, dependency trees, unsafe paths and links; source archives contain project source and verification material rather than installed dependencies. The credential-pattern scan is a heuristic, not a guarantee against every secret format.

## Installed distributions and MCP execution

The 0.6.0 wheel was installed offline into a fresh virtual environment from locked runtime requirements. Run outside the repository, `scripts/verify_installed.py` passed installed CLI, SDK, actual stdio MCP and loopback HTTP checks. Covered behavior includes SDMX discovery/international observations, macro panels, company/macro/FX research, compact evidence and saved pagination, quality gates, presets, configuration diagnosis and source-check network opt-in. Payment checks used mock quotes, preflight/budget/task replay and restart recovery without real transfers.

The installed `scripts/verify_remote_installed.py` also passed against an actual loopback Streamable HTTP process using the official MCP client. It checks six research-only tools, preset execution, evidence resolution after restart, authentication and invalid Host/Origin handling. This is a local protocol check with fictional sources, not a public endpoint deployment.

`scripts/run_research_workflow.py` completes seven contract checks through one official stdio MCP session: tool discovery, configuration, four independent company/CPI/FX reads, original evidence resolution, explicit quality rejection and unsupported-dataset rejection. It makes no LLM requests.

`scripts/capture_research_examples.py` completes three cases in one actual research-only stdio MCP session and retains compact results and every resolved evidence reference. The executed captures are fictional. Nineteen publication/measurement tests cover content-addressed bytes, overwrite rejection, synthetic/failed/incomplete/evidence-mismatched export rejection, complete public JSON links, search ownership tags, controlled prompts, unknown rates, reviewed/unknown outcomes and invalid/duplicate/credential-bearing observation rejection. Mock non-synthetic metadata exists only inside an offline publication-format test and is not live-source evidence.

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
