# Free public-source US equity research

`atlas research`, `Atlas.get_research`, MCP `atlas_get_research` and HTTP `POST /v1/research` use the same service. Sources are free government primary sources. No API key, data subscription, model or wallet is required. SEC alone requires a private `SEC_USER_AGENT` with the operator's actual contact details.

Results include missing-data status, periods, coverage and source terms. Discover the API with `atlas research catalog --json` or `GET /v1/research/catalog`.

## Datasets

| ID                         | Content                                                        | Query fields                                                | Output                             |
| -------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------- |
| `companies`                | Search the current SEC issuer/ticker directory                 | Optional `identifier`, `limit`                              | Current reference data             |
| `profile`                  | Name, SIC, exchanges and fiscal year end                       | `identifier`                                                | Submission metadata                |
| `statements`               | 37 financial concepts                                          | `identifier`, `metrics`, `period`, `count`, `allow_partial` | Normalized reported facts          |
| `ratios`                   | Six ratios with formulas and inputs                            | `identifier`, `period`, `count`, `allow_partial`            | Derived values                     |
| `company_facts`            | Concept inventory or raw XBRL observations                     | `identifier`, `taxonomy`, `concept`, `unit`, `limit`        | Original accounting contexts       |
| `filings`                  | All-form filing discovery                                      | `identifier`, `form`, `limit`                               | Filing references                  |
| `insider_transactions`     | Form 3/4/5 transactions and positions                          | `identifier`, `accession`, `limit`                          | XML records from one filing        |
| `institutional_holdings`   | A manager's 13F information table                              | `identifier`, `accession`, `limit`                          | XML records from one filing        |
| `ownership_filings`        | 13D/13G beneficial ownership reports                           | `identifier`, `limit`                                       | Filing references                  |
| `fund_filings`             | N-PORT, N-PX, N-CSR and related reports                        | `identifier`, `limit`                                       | Filing references                  |
| `corporate_action_filings` | Current reports, proxies, offerings and tender-related filings | `identifier`, `limit`                                       | Filing references                  |
| `economics`                | Eight BLS inflation/employment series                          | `series`, `limit`                                           | Economic observations              |
| `treasury_yields`          | Daily Treasury par yields                                      | `year`, `limit`                                             | Observations by date and maturity  |
| `federal_debt`             | Debt to the Penny                                              | `limit`                                                     | Daily debt observations            |
| `policy_news`              | Federal Reserve releases                                       | `limit`                                                     | Headlines, dates and source links  |
| `compare`                  | Latest reported period for up to ten specified issuers         | `identifiers`, `metrics`, `period`, `allow_partial`         | Per-issuer financials and coverage |

`identifier` accepts a ticker, CIK or company name. For 13F, use the **reporting manager's CIK**. An issuer CIK does not reverse-search every manager holding that issuer. Insider reports are likewise limited to the resolved filing history. Omitting `accession` selects the latest matching filing. Original filings and amendments are not merged automatically.

`limit` ranges from 1–500; financial `count` ranges from 1–20; comparisons accept at most ten identifiers. Unknown fields, fields unrelated to the selected dataset and booleans used as counts are rejected. The filing `form` filter is an exact match. Original form names, including `SCHEDULE 13D` and legacy `SC 13D`, are preserved.

## Examples

These examples use fictional data without network access. For public sources, use `--provider public` and replace `ATLASDEMO` with a real ticker or CIK.

```sh
uv run --offline atlas research get profile --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period quarterly --metrics revenue,eps_basic --count 3 --provider fixture --json
uv run --offline atlas research get statements --identifier ATLASDEMO --period ytd --metrics operating_cash_flow,capital_expenditure --count 2 --provider fixture --json
uv run --offline atlas research get ratios --identifier ATLASDEMO --count 1 --provider fixture --json
uv run --offline atlas research get company_facts --identifier ATLASDEMO --concept EarningsPerShareBasic --unit USD/shares --limit 5 --provider fixture --json
uv run --offline atlas research get filings --identifier ATLASDEMO --form 8-K --provider fixture --json
uv run --offline atlas research get insider_transactions --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get institutional_holdings --identifier ATLASDEMO --provider fixture --json
uv run --offline atlas research get economics --series unemployment --provider fixture --json
uv run --offline atlas research get treasury_yields --year 2026 --provider fixture --json
uv run --offline atlas research get federal_debt --provider fixture --json
uv run --offline atlas research get policy_news --provider fixture --json
```

Python:

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    data = atlas.get_research(
        {
            "dataset": "statements",
            "identifier": "ATLASDEMO",
            "period": "quarterly",
            "metrics": ["revenue", "eps_basic"],
            "count": 3,
        }
    )
    print(data["records"])
```

For MCP, call `atlas_research_catalog`, then pass `{"query":{"dataset":"economics","series":"cpi"}}` to `atlas_get_research`. With `--provider public`, macro-only queries need no SEC contact setting. SEC configuration is checked on the first SEC request. The `hosted` provider does not proxy research.

HTTP:

```sh
uv run --offline atlas serve --provider fixture --payments off --port 8000
curl -sS http://127.0.0.1:8000/v1/research/catalog
curl -sS http://127.0.0.1:8000/v1/research -H 'Content-Type: application/json' -d '{"dataset":"economics","series":"cpi"}'
```

The reference API supports self-hosted, single-process loopback use. Research endpoints never require quotes or payments. Optional annual snapshot purchases use a separate contract.

## Financial concepts and periods

The catalog's `statement_metrics` lists metric names, tags and units. USD, shares and USD/share values remain distinct. Normalized statements do not support IFRS or custom extension concepts; `company_facts` can inspect an explicitly requested taxonomy when it exists in the source.

- `annual`: Primary-year 10-K / 10-K/A facts spanning 330–400 days.
- `quarterly`: Explicit 70–110-day durations, including independently reported quarters in 10-K filings. Atlas never calculates Q4 as annual minus nine-month YTD.
- `ytd`: The longest eligible reported 70–310-day duration in the selected 10-Q / 10-Q/A filing. Discrete quarters and cumulative values remain distinct.

Selection joins accession, form, filed date and submissions reportDate to actual start/end dates. It never infers periods from `fy` or `frame` alone. Balance-sheet instant values require a duration anchor at the same report date. Transition periods, short years and periods without duration anchors may be absent. Different metrics may use original and amended filings; each retains its own provenance.

Missing or equally ranked conflicting facts return `null` with a reason. Incomplete coverage produces `INCOMPLETE_COVERAGE` by default. `allow_partial=true` / `--allow-partial` permits partial results; no eligible reported periods remains an error.

Ratios cover net margin, gross margin, operating margin, current ratio, total liabilities / stockholders' equity, and operating cash flow / net income. Output units are `ratio`, with Decimal precision 28. Inputs must cover the same period; missing inputs, mismatched periods and zero denominators prevent calculation. Price/earnings, price/book and forecast yields are unavailable because prices and analyst estimates are not supplied.

## Ownership records

Form 3/4/5 preserves transaction codes, acquisition/disposition codes, direct/indirect ownership, derivatives, missing prices and footnote IDs. A reported transaction is not automatically interpreted as a purchase or sale. At most 200 footnotes are returned, each limited to 2,000 characters, with explicit truncation fields. Price currency is not inferred; its unit is `as_reported_in_filing`.

13F reports selected eligible quarter-end positions with a reporting lag. It does not establish all current holdings, shorts or trade flows. Because value conventions differ across form versions, Atlas preserves `reported_value_unit=see_filing_instructions` without guessing dollars versus thousands or multiplying values. It does not infer ticker mappings from CUSIPs or query a paid identifier database.

XML discovery uses filing history and safe directory filenames, examining at most six candidates. Unsupported XML forms do not count as successful extraction. The service does not redistribute whole documents, images or attachments.

## Economics and policy

BLS `series` accepts `cpi`, `cpi_sa`, `core_cpi`, `unemployment`, `participation`, `payrolls`, `hourly_earnings` and `job_openings`, or their registered series IDs. Unregistered V1 retrieval covers the latest three years. Units, seasonal adjustment, footnotes and M13 annual averages are preserved. Release-time vintages and year-over-year changes are not generated. A shared cache and private database enforce 25 HTTP attempts per UTC day. Other programs and users sharing an IP may consume the upstream allowance first.

Treasury yields use the selected year's nominal par curve. Each record is one date/maturity pair, so a record limit can truncate a day's curve; coverage identifies truncation. Fiscal Data returns one page of at most 500 recent records. Federal Reserve output contains retained official headlines and links, not general company news or article bodies.

## Provenance, validation and unavailable data

Raw bytes stay in a private cache. Values carry `raw_sha256`, `retrieved_at` and a JSON pointer or XML local path. XML local paths omit namespaces and use one-based positions. JSON pointers escape `/` as `~1` under RFC 6901. Source names, search results, footnotes and headlines are untrusted text, never instructions.

Retrieving an observation for an earlier year does not establish what was available then. `as_of` / `pit` requests are rejected. Issuers in a comparison may have different latest fiscal dates.

Consolidated prices, daily prices and adjustment factors, order books, options, consensus estimates, complete news/transcript coverage, a historical security master and PIT are unavailable. Private APIs or scraping with unclear terms do not fill those gaps. See [DATA_SOURCES](../DATA_SOURCES.md) for source conditions and [validation scope](verification.md) for test evidence. Catalog `live_verified=false` distinguishes offline validation from live access.
