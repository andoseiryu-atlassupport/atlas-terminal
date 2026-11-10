# Project overview

Atlas Terminal is an Apache-2.0 financial research toolkit for AI agents and applications. It combines data retrieval, research calculations and inspectable source evidence through MCP, a Python SDK, a CLI and an HTTP API. The current source checkout has **69 research datasets, 28 upstream source families and 34 presets**; the existing `v0.7.0` release does not contain every later source addition.

## Research capabilities

- **Company research:** 37 US-GAAP financial concepts; annual, reported discrete quarterly and YTD statements; TTM flows, ratios, growth, free cash flow, issuer comparisons, filing discovery, original document text/tables, scoped XBRL facts and selected ownership/fund disclosures. Actual filing periods and original locations remain available. [Research](research.md) · [Original filings](filing-research.md).
- **Company scoring:** a versioned Buffett-inspired rubric for annual business-quality proxies and an explicit valuation scenario, with fixed weights, SEC US financials, indexed EDINET Japan GAAP/IFRS consolidated JPY inputs, supplied-data support, exact operands and missing-point bounds. Japanese automation needs a private key and history index; live financial retrieval remains unverified. Qualitative judgment remains manual; no endorsement or return forecast is implied. [Scoring](buffett-scoring.md) · [Japanese scoring](japan-buffett.md).
- **Macro, rates and FX:** selected US, Japanese and international indicators; government curves, policy rates and spreads; EFFR/SOFR historical observation windows; ECB reference FX; G20 annual WDI data; metadata-selected IMF/OECD observations and explicit calendar panels. Dated EFFR checks reach 2000-07-03. Frequencies, units, country gaps and source definitions remain separate. [Economic indicators](economic-indicators.md) · [Rates and FX](macro.md) · [Government bonds](fixed-income.md) · [International discovery](international-macro.md).
- **Market history:** optional daily/intraday prices and security actions for US, Tokyo, Korean and registered European instruments, bounded annual reads for multi-year stock exports, daily futures observations and same-date contract spreads. IMF monthly commodity reference prices are a distinct dataset. [Stock history](yahoo-finance.md) · [European/Korean coverage](europe-korea-equities.md) · [Commodities](commodities-futures.md).
- **Technical and statistical analysis:** 23 trailing price indicators, including SMA, EMA, Bollinger bands, Wilder RSI and MACD; same-market price comparison; offline history calculations, series statistics, local charts and a research workbench. Original price basis, dates, formula inputs, warm-up gaps and source adjustments remain explicit. [Technicals](technical-indicators.md) · [Statistics](time-series-statistics.md) · [Workbench](research-workbench.md).
- **Japanese market context:** JPX daily credit buy/sell balances, changes and margin ratios, listed-company references, and EDINET disclosure/XBRL readers. Application/publication dates and source cells are retained. Own broker positions and JSF financing/lending balances are outside scope. [Margin balances](margin-trading.md) · [Japanese companies](japan-companies.md).
- **Prediction markets:** read-only Polymarket Gamma keyword/exact-slug event queries, outcome-aligned prices and percentages, lifecycle flags, contract details and original evidence. Current probabilities are unavailable for closed/inactive markets; quote timestamps remain unverified. [Polymarket](prediction-markets.md).
- **Additional evidence:** five official news feeds and publisher briefings, one-venue bitbank crypto depth, permitted local books, FISCO report-link discovery, private PDF/text imports, licensed local credit-rating observations and private source history. [News](news.md) · [Books and reports](order-books-reports.md) · [Credit ratings](credit-ratings.md) · [Source history](source-history.md).

Optional native factor risk models support cross-sectional estimation and observed-factor time-series OLS/EWMA-WLS for supplied US, Japanese, Korean and British stock universes in separate local currencies. JSON, CSV and read-only SQLite connections preserve declared inputs. Risk attribution, active risk, shocks and holdout calculations share saved model artifacts. The models require a suitable estimation universe and do not include a verified historical fundamental database or proven real-market forecast accuracy. [Risk models](risk-model.md) · [Connections](data-connections.md).

## Shared interfaces and evidence

Research datasets use one validated query, catalog and service across CLI/SDK/MCP/HTTP. Research-only MCP has six tools; the separate optional risk extension requires explicit enablement. Presets and bounded batches retain independent results and errors, while preview and configuration-only diagnostics make no source requests.

Original values retain units, actual periods, raw-source hashes, retrieval times and JSON/XML/CSV/document locations. Derived results retain their formulas and exact inputs. Missing or conflicting values are explicit; unreported quarters, absent prices and prediction-market complements are not synthesized. Compact evidence references, field selection, pagination and optional quality requirements support bounded agent responses.

Source retrieval time, observation dates, publication times and contract deadlines have different meanings. Current/revised observations and private captures do not establish publisher point-in-time history. Calendar alignment does not establish statistical comparability. [Agent contracts](agents.md) · [Methodology](methodology.md) · [Citation guidance](citations.md).

## Sources and verification

Public primary sources are the default, including SEC, BLS, Treasury, Federal Reserve, New York Fed, ECB, BEA, Japanese official sources, JPX, IMF, OECD, World Bank and other national statistical publishers. Optional international Yahoo market observations are a secondary-source exception, disabled by default for explicitly enabled personal use. Local imports require the operator's applicable rights. Public API access and the code license do not grant source-data redistribution rights.

SEC requires private operator contact configuration. BEA, Census, FRED and EDINET require separate free registered API keys. Research has no built-in LLM, wallet or paid data-provider dependency. Source availability, network permissions and reuse terms remain specific to each source.

[Verification](verification.md) distinguishes fictional offline tests, dated actual-source reads, independent source/calculation audits, installed-package checks and CI. Selected stock histories, rates, daily futures, JPX margin reports and Polymarket outcomes have dated live evidence. These records do not certify continuous source availability, every supported series or risk/score forecast accuracy. Live SEC/EDINET access and some country-series selections retain explicit limits.

## Runtime and distribution

Python 3.12+, uv, Pydantic, httpx, Typer, FastAPI, the official MCP SDK and SQLite provide the research runtime. Node.js 24 and Prettier support documentation checks. Local history exports and analysis outputs remain private; generating documentation or distribution metadata does not publish a service.

Research-only stdio and operator-hosted Streamable HTTP share the research contracts. Remote use requires explicit authentication/access configuration and operator-managed HTTPS with persistent private state. No official public remote endpoint or public ChatGPT plugin is operated. PyPI publication and an MCP Registry listing remain external publication steps.

The separate annual-snapshot API supports optional x402 V2 quote/purchase/recovery with payments off by default. Its local signer and annual endpoint remain restricted to Base Sepolia USDC. The isolated [BLS seller](seller.md) adds a prepared-series product with private publication, immutable purchased results and an optional Base mainnet receiving adapter. The seller requires explicit CDP/recipient/RPC configuration and an external payer wallet. It does not expose free research or upstream refresh routes. Mainnet settlement and public seller hosting remain unverified; production custody is unsupported. Ordinary research queries never purchase or sign.

## Scope

Consolidated real-time equity quotes/order books, options, analyst consensus, complete news/transcript coverage, complete corporate-action/security-master data, historical release vintages and trading are unavailable. Company scores and experimental risk models do not establish investment performance.

[Canonical README](../README.md) · [Complete capabilities](../IMPLEMENTATION_STATUS.md) · [Sources and terms](../DATA_SOURCES.md) · [Validation scope](verification.md)
