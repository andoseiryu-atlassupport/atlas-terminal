# Financial selection methodology

`latest_primary_filing_per_fact_v1` is Atlas's limited selection policy, not a complete accounting interpretation supplied by SEC.

1. Resolve the issuer using the current ticker directory. Preserve share classes with the same CIK, and require disambiguation between multiple issuers.
2. Retrieve recent submissions and at most 20 submission archives. Index filings by accession and validate archive and primary-document filenames.
3. Use facts joined to `10-K` / `10-K/A` metadata whose end date matches the filing's reportDate. Exclude comparative-only values and 10-Q YTD contexts.
4. Require annual duration facts to span 330–400 days between actual start/end dates. This accepts 52/53-week years and excludes short transition years with a warning. Keep instant and duration facts distinct.
5. Select the newest requested number of period ends anchored by at least one supported annual duration metric. Assets-only queries use the same period set.
6. Rank eligible facts by explicit concept priority, filing date and acceptance time where available. Conflicting same-day values with unknown ordering are ambiguous. Accession string order never selects between unequal values.
7. Apply amendments per metric. Do not fill other metrics with zero or derived values. Warn when metrics use different filings. Equal value/period duplicates use stable provenance.

## Annual snapshot metrics

| Metric              | us-gaap tag                                                                      | Kind     |
| ------------------- | -------------------------------------------------------------------------------- | -------- |
| revenue             | RevenueFromContractWithCustomerExcludingAssessedTax → Revenues → SalesRevenueNet | duration |
| net_income          | NetIncomeLoss                                                                    | duration |
| assets              | Assets                                                                           | instant  |
| liabilities         | Liabilities                                                                      | instant  |
| operating_cash_flow | NetCashProvidedByUsedInOperatingActivities                                       | duration |

Candidates use USD only. A revenue-concept change across periods produces a warning. Raw sources retain concept descriptions, but conceptual equivalence is not guaranteed. Annual snapshots do not calculate margins, growth, quarter differences, currency conversions or missing values such as Assets−Equity.

Each fact includes its decimal-string value, actual period, unit, filing date, acceptance time or null, accession, tag, original filing URL, raw-source URL, JSON pointer, raw-byte SHA-256 and retrieval time. Missing primary-document metadata falls back to the filing index, without inventing a document filename.

## Coverage and limitations

Results retain the selected period-by-metric matrix, with null values and reasons for missing or conflicting cells. Requested and available period counts remain distinct. At least one reported value is required. Incomplete coverage is an error by default; `allow_partial=true` preserves gaps in both quotes and results.

The policy does not track revisions appearing only as comparative values in later primary-year filings. Current companyfacts cannot reconstruct historical information availability, so `as_of`, PIT and published_before requests are rejected. Accounting equivalence and issuer-wide completeness are not guaranteed.

Raw hashes and JSON pointers trace source values. At quote preparation, the server fixes canonical FinancialResult bytes independently of future source updates. Canonical JSON uses UTF-8, sorted keys, `,` / `:` separators, no ASCII escaping and no non-finite numbers.

## Research financial contracts

The five-metric policy above applies to `financials` and annual snapshots. Research `statements` supports 37 concepts and annual, explicitly reported discrete-quarter and YTD periods. Research `ratios` supports six ratios with original inputs. See the [research guide](research.md) for periods, units, missing data and Q4 treatment. Raw-context `company_facts` observations are distinct from normalized statement values.
