# Validation scope

Recorded on 2026-10-25 (UTC) for the source checkout following Atlas Terminal 0.7.0, with Python 3.12.14 and Node.js 24.19.0 on Linux. Local verification, GitHub Actions, public deployment, official-source access and model observations are distinct evidence.

Version 0.7.0 includes the experimental native factor risk extension and installed walkthroughs described below. The prior 0.6.0 release did not include risk estimation. Local wheel checks remain separate evidence from a published GitHub release.

## Current local checks

| Check                                                     | Observed result                                             |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| `uv run --offline ruff check .`                           | PASS                                                        |
| `uv run --offline ruff format --check .`                  | PASS                                                        |
| `uv run --offline mypy`                                   | PASS; 58 source files                                       |
| `uv run --offline pytest`                                 | PASS; 926 tests, 2 live tests deselected                    |
| `uv run --offline python -m build --no-isolation`         | PASS; wheel and source distribution                         |
| `uv run --offline python scripts/verify_distribution.py`  | PASS; metadata, required contents and forbidden paths       |
| `uv run --offline python scripts/build_docs.py`           | PASS; 46 HTML pages and internal file/section links         |
| `uv run --offline python scripts/verify_showcases.py`     | PASS; both recorded versions, data modes and checksums      |
| `uv run --offline python scripts/prepare_distribution.py` | PREPARED; versioned plugin ZIP and Registry draft           |
| `npm run check`                                           | PASS; Prettier and whitespace                               |
| `python scripts/scan_public_files.py`                     | PASS; heuristic working-file and reachable-history patterns |

Default tests prohibit IP sockets and use fictional sources or mocked transports. They do not retrieve live official data or transfer funds. Archive validation rejects private/runtime paths, dependency trees, unsafe paths and links; source archives contain project source and verification material rather than installed dependencies. The credential-pattern scan is a heuristic, not a guarantee against every secret format.

Test cases use isolated private state directories so repeated verification does not consume an operator's persistent cache quota. The numerical and protocol assertions remain unchanged.

## Euro area and Australian government bonds

On 2026-10-25 (UTC), 926 offline tests passed, with two live cases deselected. The source catalog has 29 datasets, 14 source families, 50 government-curve calculation series and eleven presets. The 54 regional cases cover 23 hand-calculated slopes/butterflies/changes; ECB AAA/all-rating identity, dimensions, unit/multiplier and status checks; negative rates, nulls and duplicate rejection; RBA daily nominal-ID/frequency/unit checks with indexed bonds excluded; original CSV-cell hashes; correct-year evidence for missing multi-year tenors; compact context preservation; quality gates; calendar panels; CLI/SDK/HTTP and a real official stdio MCP session using one connection. Existing per-dataset compact reconstruction and source-check tests include both new datasets and ECB/RBA probes.

Ruff formatting/style and mypy passed for 58 source files. Wheel/source archive validation passed with 117 wheel files and 295 source files. The rebuilt 0.7.0 source-checkout wheel was installed offline with locked runtime dependencies; imports resolved inside that installation outside the repository, and European, Australian and four-region bond presets executed with fictional data. Documentation/internal links passed for 46 pages, and both prior showcase recording checksums remained unchanged. This is an unreleased source extension, not a replacement of existing 0.7.0 release assets.

The ECB and RBA CSV contracts were compared with the pinned public format references in [data sources](../DATA_SOURCES.md#ecb-euro-area-curves-and-rba-government-yields). Direct live `data-api.ecb.europa.eu` and `www.rba.gov.au` access and current source-terms pages remain unverified under outbound host restrictions. Mock/parser success does not establish actual source availability, redistribution rights, synchronized observations or forecast accuracy. ECB euro area composite spot curves are not individual European sovereigns or UK gilts, and no cross-country compounding/FX normalization was validated.

## Government-bond research expansion

On 2026-10-25 (UTC), local checks passed for 27 datasets, 13 source families, 27 government-curve calculation series and eight presets. The 53 new fixed-income cases cover hand-calculated US/Japan spreads and butterflies, nominal/real par gaps, preceding-observation changes, null/date matching, weekend gaps, caller decimal precision/traps, MOF CSV overlap precedence, original-cell locators, malformed inputs, encodings, fixed endpoint validation, compact evidence/pagination, quality gates, dated panels, CLI/SDK/HTTP and an actual official stdio MCP session. Existing Streamable HTTP and opt-in equity-risk checks also passed after the catalog expansion.

A newly built 0.7.0 source-checkout wheel was installed offline into a fresh virtual environment using locked runtime dependencies. Outside the repository, imports resolved to the installed wheel and all three bond presets executed with fictional data. This is not a new published release or a replacement for the existing 0.7.0 release assets. Wheel/source archive checks, 46-page documentation/internal links and both existing recording checksums passed; the prior recorded source bytes were unchanged.

The MOF English CSV contract was compared with public format references, including [the pinned FinanceToolkit source](https://github.com/JerBouma/FinanceToolkit/blob/fd2e313e8c287ac907900ff20af586330ccd9564/financetoolkit/economics/mof_model.py). Direct official Treasury/MOF retrieval and the current MOF terms page could not be checked under this environment's outbound host restrictions. Mock HTTP/parser success does not establish actual source compatibility, redistribution permission, market freshness or bond-return accuracy. No security pricing, duration/DV01, auction, repo or FX hedge-cost validation was performed.

## Installed distributions and MCP execution

The 0.7.0 wheel was installed offline into a fresh virtual environment from locked runtime dependencies. Both `showcase` commands passed outside the checkout with isolated Python imports from that installed package. Each used an actual official stdio MCP session. The installed `verify_installed.py` and `verify_remote_installed.py` checks also passed for CLI/SDK/MCP/loopback HTTP, restart, evidence and mock recovery without external source requests or real transfers.

The 0.6.0 wheel was installed offline into a fresh virtual environment from locked runtime requirements. Run outside the repository, `scripts/verify_installed.py` passed installed CLI, SDK, actual stdio MCP and loopback HTTP checks. Covered behavior includes SDMX discovery/international observations, macro panels, company/macro/FX research, compact evidence and saved pagination, quality gates, presets, configuration diagnosis and source-check network opt-in. Payment checks used mock quotes, preflight/budget/task replay and restart recovery without real transfers.

The installed `scripts/verify_remote_installed.py` also passed against an actual loopback Streamable HTTP process using the official MCP client. It checks six research-only tools, preset execution, evidence resolution after restart, authentication and invalid Host/Origin handling. This is a local protocol check with fictional sources, not a public endpoint deployment.

`scripts/run_research_workflow.py` completes seven contract checks through one official stdio MCP session: tool discovery, configuration, four independent company/CPI/FX reads, original evidence resolution, explicit quality rejection and unsupported-dataset rejection. It makes no LLM requests.

`scripts/capture_research_examples.py` completes three cases in one actual research-only stdio MCP session and retains compact results and every resolved evidence reference. The executed captures are fictional. Twenty-three publication/measurement tests cover content-addressed bytes, overwrite rejection, synthetic/failed/incomplete/evidence-mismatched export rejection, complete public JSON links, search ownership tags, controlled prompts, unknown rates, reviewed/unknown outcomes, invalid/duplicate/credential-bearing observation rejection, captioned media delivery and recording integrity. Mock non-synthetic metadata exists only inside an offline publication-format test and is not live-source evidence.

Seven walkthrough tests execute both scenarios through actual MCP, independently recalculate the same-day FX cross, check units and resolved evidence, verify Japanese universe/holdings and variance decomposition, reject public requests before process creation without opt-in, and suppress transport exception details. The committed [research](agent-research-walkthrough.md) and [Japanese risk](japan-risk-walkthrough.md) recordings retain complete JSON, readable transcripts, MP4 and VTT files. `ffprobe` reported exactly 60 seconds for each replay; selected frames were inspected for readable output. Video duration is a presentation choice, not measured execution speed. No browser rendering or actual-source availability is established by these checks.

## Experimental native factor risk extension

`uv run --offline pytest tests/test_risk_model.py` passes 54 fictional-data tests. They cover independently specified factor recovery, constrained industry identification, covariance symmetry/positive semidefiniteness, Euler contribution additivity, benchmark-relative zero risk, factor shocks, insufficient history/universe/rank, availability cutoffs, delayed closes, later-vintage/future-return exclusion, missing/duplicate/invalid input rejection, content hashes, private permissions, deterministic restart and caller-context isolation, atomic quota failure and holdout baseline calculations. No input is a real-market observation.

`tests/test_factor_workflow.py` contains 103 offline tests. Independent linear data recover known time-series alphas/betas under OLS and EWMA-WLS, for both total and excess factors; holdouts verify risk-free subtraction. Cases also cover JPY return labeling, caller Decimal traps, future-data isolation, missing/collinear factors, invalid units, native JSON/CSV/SQLite numerical agreement, adjusted-price pipelines, raw-capitalization/adjustment separation, explicit aliases/columns, rejection of SQLite binary floats, saved ingestion receipts, restart reproducibility, unchanged source databases, network-extension opt-in and no-read preview/configuration bounds.

Local-market cases cover Japanese/JPY, Korean/KRW and British/GBP execution/restart from all three native formats; monetary-unit scaling and industry-permutation invariance of portfolio risk; US legacy defaults; wrong/missing currency, mixed markets, invalid classifications and pre-effective KSIC 11/UK SIC 2026 availability; leading-zero JPX codes, nullable columns and explicit source-industry mapping; holdout market/classification mismatch; and bounded extended Japanese/Korean closures with seven-day US/British limits. The actual stdio MCP workflow executes all four fundamental markets plus time-series estimation in one session. The Korean mapped-CSV workflow also executes through the HTTP route. All observations remain fictional.

British cases distinguish UK SIC 2007/2026 and their different section sets, including the new V section. Pence normalization tests cover both GBp/GBX labels, quote-unit changes, raw-close/share capitalization versus unchanged reported GBP capitalization, low caller precision/traps, and rejection of missing/foreign/lowercase quote labels. Actual HTTP requests from configured CSV/SQLite prices produce identical numerical portfolio outputs for pound and mixed pound/pence quotations. These cases test declared unit handling, not the truth of upstream quotation metadata or exchange connectivity.

The actual official stdio MCP client imports a complete panel, fits a model and analyzes a portfolio through one connection. Additional actual stdio calls run each model in one request from configured CSV inputs, without uploading observations. HTTP tests execute a configured SQLite input and reject caller path/SQL/URL arguments. Other checks cover the CLI, SDK and official Streamable HTTP client with an in-memory ASGI transport. Remote anonymous risk mode is rejected. Existing default tool sets retain their six research-only tools; enabling risk adds one state-writing tool. IP sockets remain disabled in the default suite.

`atlas risk demo --json` completes a 60-stock/four-style/60-estimation-session example with risk attribution, tracking error, a common-factor shock and 20 subsequent sessions versus a historical EWMA portfolio baseline. `--method time_series` completes the same native-connection workflow with three fictional assets and observed factors. The source-tree wheel was installed offline into a fresh virtual environment from locked runtime requirements; both demos passed outside the repository with isolated imports from installed package files. New configuration/request examples were also validated against the actual installed models. This wheel is not a new published release. Its synthetic numbers do not establish forecast accuracy.

The latest source-tree wheel was reinstalled into that isolated runtime. All five demos (US, Japan, Korea, Britain and time-series) passed outside the checkout using installed package files. Japanese/JPY/JPX33, Korean/KRW/KSIC 11 and British/GBP/UK SIC 2007 metadata survived forecast and holdout output. The British request and connection templates validated against the installed models. Classification documentation was inspected from official references; this does not establish live issuer/price data access, historical assignment correctness or local-market forecast calibration. See [market conventions and source references](equity-markets.md).

Neither model includes an equity-price feed, independently verified PIT vintages, corporate-action/security-master reconciliation, changing-universe engine or actual-data calibration study. No external equity API provider is bundled or live-tested; the trusted Python extension contract and network guard are tested offline. Input rights and availability timestamps remain caller assertions. The balanced supplied universe, coarse SIC divisions for fundamental estimation, approximate residual degrees-of-freedom adjustments, diagonal specific covariance and fixed-exposure annualization are material methodological limits. See [risk methodology and usage](risk-model.md).

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
