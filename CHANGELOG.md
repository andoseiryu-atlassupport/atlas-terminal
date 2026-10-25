# Release notes

## Unreleased

- Add 81 registered official macro selections: twelve Japanese GDP/activity/price/labour/housing series, eight BLS price/labour series, eight BEA demand/consumption/saving series, 48 Eurostat European selections and five UK ONS series.
- Add strict Eurostat JSON-stat 2.0 and ONS time-series adapters, current ECOICOP-v2 HICP selection, quarterly Japanese Dashboard periods and explicit UK rolling unemployment windows, preserving exact values and original evidence.
- Add Japanese/US activity, euro-area/UK and global macro presets through the existing six research tools, plus searchable definitions, calendar panels, diagnostics and source probes. New live-source compatibility remains unverified; consensus and release vintages are unavailable.

- Add ECB AAA/all-rating euro area zero-coupon spot curves and RBA nominal government curves with strict series, unit, frequency and original CSV-cell evidence.
- Add 23 regional curve calculations, European/Australian presets and a four-region government-bond preset through the existing research interfaces; retain curve universe and compounding without cross-country conversions.

- Add Japan MOF 1–40-year government curves with original CSV row/column evidence, strict source parsing and explicit historical/current-month overlap handling.
- Add US/Japan government curve slopes, 2s5s10s butterflies, preceding-observation changes and US nominal/real par inflation-compensation proxies with exact basis-point arithmetic.
- Add US, Japan and combined government-bond presets through existing research interfaces, searchable series, dated panels and an opt-in MOF source probe. No security prices, duration/DV01, hedge costs or live-source validation are supplied by this change.

## 0.7.0

- Run `atlas showcase research` and `atlas showcase japan-risk` from an installed package through actual official MCP client sessions. Inspect complete JSON, resolved evidence, model metadata and holdout output; public research requires explicit network opt-in.
- Publish two reproducible walkthroughs with fictional execution reports, transcripts, captions and 60-second video replays. Add a structured first-run feedback form and align installation guides with the release.
- Add independent experimental fundamental equity risk models for US/USD, Japanese/JPY, Korean/KRW and British/GBP supplied observations: market/local-industry/style exposures, identified cross-sectional WLS, shrunk EWMA factor covariance and specific variances, portfolio risk contributions, tracking error and common-factor shocks.
- Support JPX33 and explicit KSIC 10/11 sections, native industry mappings, local-currency guards, market-specific session bounds and saved market/classification metadata. Local-market demos run through the shared native workflow; no country/FX factors or global covariance is supplied.
- Support explicit UK SIC 2007/2026 sections with revision timing/holdout checks and GBP/pence price normalization before return ratios and raw-price capitalization. Quotation labels are required for GBP prices; reported capitalization stays GBP and no foreign currency conversion is inferred.
- Add native observed-factor time-series OLS/EWMA-WLS with diagnostic intercepts, original-unit betas and explicit total/excess-return conventions. Both methods share portfolio analysis and subsequent holdout validation.
- Add operator-configured JSON, CSV and read-only SQLite inputs with explicit column/asset mappings, adjusted-price return normalization, input receipts and a single read/validate/fit/analyze request. Network provider extensions require explicit execution opt-in; preview reads no datasets.
- Store versioned content-addressed input chunks and immutable model artifacts in private state. Share CLI/SDK computations with opt-in MCP/HTTP; remote risk mode requires bearer authentication. Historical availability remains caller-asserted, with strict timing, coverage and rank checks.
- Add a complete fictional 60-stock workflow, subsequent-session variance comparison against an EWMA baseline and offline numerical/timing/interface tests. Equity-price feeds, verified PIT vintages and real-market forecast accuracy remain unavailable or unverified.

## 0.6.0

- Capture three reproducible research cases through one official MCP connection, resolve original evidence and preserve content-addressed snapshots. Public observation-page export requires completed non-synthetic results.
- Add controlled manual discovery observations with separate branded/unbranded rates, pending runs and unknown outcomes. Search probes and fixture checks never count as model citations.
- Unify public package descriptions, correct generated dataset counts, and prepare manual trusted PyPI publication and search ownership verification. Actual external registrations remain separate.

- Search registered macro/FX query templates by text, country, frequency, source and unit through existing catalog interfaces.
- Discover IMF/OECD dataflows and classification codes, validate exact series dimensions and retrieve monthly, quarterly or annual SDMX observations with original XML evidence.
- Align up to ten macro/FX queries into calendar panels with explicit last/mean aggregation, source inputs, missing cells and independent error handling. Add bounded saved-query CLI execution and persistent OECD hourly attempt limits.
- Extend offline parser, aggregation, HTTP/SDK/CLI and actual MCP contract checks. Live upstream compatibility remains unverified.

## 0.5.0

- Add eight BEA national-account series and ten Japanese macro series through CLI, SDK, MCP and HTTP. BEA uses a private free registered key; BOJ and Statistical Dashboard require no registration.
- Add paired observation-date filters, bounded BLS history POSTs, exact prior-period macro inputs and the full ECB reference-rate history with 31 currencies. Preserve source units, scales, periods and original byte evidence.
- Add Japan and US/Japan presets, configuration diagnostics and fixed probes for the three new source families. Current revised data remain distinct from historical release vintages.
- Include fictional source fixtures and offline wire-contract/parser/interface tests. Live new-source connectivity remains unverified.

## 0.4.0

- Inspect local delegated spending, unresolved reservations and remaining session/UTC-day budgets without loading a signing key. Purchase preflight combines authenticated order readiness with local policy checks without signing or reserving funds; actual purchases still enforce limits atomically.
- Bind a stable task intent to an immutable annual snapshot quote using a private replay capability. Recover the same order after response loss or restart, publish only one quote under concurrent preparation, reject changed queries and retain tombstones after quote cleanup.
- Expose budget/preflight tools and task IDs through SDK, CLI and full MCP, and capability-authenticated order preflight/replay through HTTP. MCP purchases accept a cost ceiling that can only lower the operator limit. Research-only MCP continues to expose six non-payment tools.
- Testnet budget inspection uses an operator-configured public payer address; a configured address must match the actual signer. Payments remain off by default and real Base Sepolia transfers remain unverified.

## 0.3.3

- Use extensionless documentation URLs for the active static host so canonical URLs, sitemap entries and internal links match the served pages. Retain an explicit `.html` mode for hosts that require file suffixes.
- Expose bounded, escaped public-access failure codes directly in GitHub check annotations while retaining the complete JSON artifact.

## 0.3.2

- Run a reproducible company-financials, CPI and USD/JPY workflow through one official MCP connection, resolve all evidence IDs and retain explicit quality/unsupported-dataset checks. Fixture contracts and real-source opt-in remain separate from model evaluations.
- Inspect published documentation HTTP status, crawler directives, canonicals and same-origin sitemap pages with bounded responses and safe reports. A separate weekly/manual workflow retains public checks and optionally verifies a configured actual remote MCP endpoint.
- Prepare single-host systemd and Caddy deployment files with loopback ingress, persistent private state and explicit bearer/anonymous access. Templates do not provision or deploy a public service.
- Publish expanded documentation and a complete multi-source example at the active documentation origin. Include deployment/evaluation materials in source distributions.

## 0.3.1

- Limit source distributions to project source, fixtures, documentation and build configuration; exclude installed Node dependencies and generated/private state independently of VCS ignore behavior.
- Validate built source/wheel archives before release publication, including required content, package version and forbidden dependency/state paths.
- Keep MCP Registry ownership comments out of HTML page summaries while preserving the raw package README marker.

## 0.3.0

- Retrieve 21 free primary-source datasets across company financials, disclosures, ownership, US macro indicators and reference FX through shared CLI, Python, MCP and HTTP services.
- Use company, US macro and FX-context presets, exact-query batches, quality conditions, compact evidence, saved queries and configuration/source diagnostics.
- Connect compatible agents through stdio MCP or the research-only Streamable HTTP service with explicit authentication/anonymous mode, host/origin checks and bounded ingress.
- Read public HTML and Markdown documentation, usage examples, source/citation guidance and Codex setup; research responses link the relevant documentation while retaining original source attribution.
- Package reusable research skills and prepare versioned plugin/MCP Registry metadata. No official public remote endpoint, PyPI publication or OpenAI plugin listing is claimed.
- Recover optional Base Sepolia x402 snapshot purchases with durable settlement claims and finalized chain evidence. Payments remain off by default, fixture/mock results are fictional and real transfers remain unverified.

See [capabilities](IMPLEMENTATION_STATUS.md), [coverage](DATA_SOURCES.md) and [verification](docs/verification.md) for implementation and validation limits.
