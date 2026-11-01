# Official central-bank policy rates

`policy_rates` returns four registered daily source series through the existing SDK, CLI, MCP and HTTP research interfaces. It uses no paid API or registration key.

| `series`               | Instrument                                 | Original source                |
| ---------------------- | ------------------------------------------ | ------------------------------ |
| `ecb_deposit_facility` | ECB deposit facility                       | `FM.D.U2.EUR.4F.KR.DFR.LEV`    |
| `ecb_main_refinancing` | ECB fixed-rate main refinancing operations | `FM.D.U2.EUR.4F.KR.MRR_FR.LEV` |
| `ecb_marginal_lending` | ECB marginal lending facility              | `FM.D.U2.EUR.4F.KR.MLFR.LEV`   |
| `rba_cash_target`      | RBA cash rate target                       | F1 `FIRMMCRTD` column          |

Pass `series`, optional paired `start_date` / `end_date` and `limit` (1–500). Defaults span January 1 three years before the current year through today. Results are newest first. The searchable catalog includes the four templates, and `macro_panel` accepts them with an explicit `last` or `mean` calendar aggregation.

```sh
atlas research get policy_rates --series ecb_deposit_facility --start-date 2026-01-01 --end-date 2026-10-31 --provider public --json
atlas research get policy_rates --series rba_cash_target --start-date 2026-10-01 --end-date 2026-10-31 --provider fixture --json
```

The ECB adapter verifies the exact series key, frequency, area, currency, instrument, provider, unit and unit multiplier. Values use percent per annum (`PCPA`, multiplier zero). The RBA adapter verifies the published series ID, target title, RBA producer, original series type, daily frequency and percent unit. It tolerates omitted trailing unselected columns without changing the selected column. Each result includes exact decimal strings, original source value, CSV row/column, byte hash and retrieval time.

Deposit, refinancing, marginal lending and cash-target instruments have distinct meanings. Interbank overnight cash, EFFR, SOFR and government-bond yields are not substituted for policy targets. Published nulls remain null. No missing date is inserted or forward-filled by Atlas. A date in the series is an observation/effective reference date; no announcement time or publication-time PIT guarantee is inferred. The RBA F1 table is a rolling source whose currently returned history begins on 2011-01-04; absent older years are not reconstructed.

## Reproduce original-cell verification

```sh
uv run --offline python scripts/verify_policy_rates.py --allow-network --start-date 2026-01-01 --end-date 2026-10-31 --output build/policy-live
```

The script uses the normal proxy/TLS client and persistent cache, checks every normalized numeric cell against original CSV bytes, and saves private captures and summaries under ignored `build/`. It does not reset BLS counters, publish source data, or run by default in CI. Initial normal-proxy checks on 2026-11-01 returned 304 observations for each ECB series and 212 selected RBA observations. [Verification evidence](verification.md).

Official references: [ECB key interest rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html), [ECB API](https://data.ecb.europa.eu/help/api/overview), [RBA F1 table](https://www.rba.gov.au/statistics/tables/), [RBA cash rate target](https://www.rba.gov.au/statistics/cash-rate/). Publisher-specific reuse terms remain applicable; see [sources](../DATA_SOURCES.md).
