# Japanese margin trading balances

`margin_balances` reads JPX's public all-issue daily margin report for one exact Japanese security. It returns credit buy and sell balances, their reported daily changes, general/negotiable and standardized margin components, reported JPY balances, and a share-based margin ratio. CLI, Python, MCP and HTTP use the same validated query and parser. No API key is required.

```sh
atlas research margin 7203.T
atlas research margin 6758.T --json
atlas research margin 7267.T --observation-date 2026-11-05 --json
```

```python
from atlas_terminal import Atlas

with Atlas("public") as atlas:
    result = atlas.get_margin_status("7203.T")
    row = result["records"][0]
    print(row["date"], row["publication_date"])
    print(row["shares"]["buy_balance"], row["shares"]["sell_balance"])
    print(row["margin_ratio"]["value"])
    print(result["coverage"]["available_application_dates"])
```

MCP `atlas_get_research` accepts this object under `query`; HTTP accepts the same object at `POST /v1/research`:

```json
{
  "dataset": "margin_balances",
  "identifier": "7203.T"
}
```

Add `"observation_date": "2026-11-05"` to select that exact application date, if still linked by JPX. Omitting the date selects the latest linked application date. Four-character `.T` identifiers map to the trailing-zero five-character source code; five-character identifiers select their exact source class. The source code, ISIN and raw issue label remain available. The adapter does not infer share class or historical membership from the code.

## Dates and available history

The [official all-issue directory](https://www.jpx.co.jp/markets/statistics-equities/margin/01.html) currently publishes daily PDFs. JPX changed from weekly to daily issue-margin reporting in late September 2026; its [daily data specification](https://jpx-jquants.com/ja/spec/mkt-margin-int) identifies observations from 2026-09-25. Atlas selects only canonical PDF links actually present in the current public directory. The paid J-Quants/TMI machine feeds are separate products and are not used.

`date` is the report's application date. `publication_date` is the separately printed publication date, validated against the PDF header on every page. `published_at` stays null: a directory's scheduled update time is not proof of the report's actual publication timestamp. Current source bytes do not establish historical point-in-time availability.

`coverage.available_application_dates` lists the exact currently linked dates. A requested date or security absent from that report raises `INCOMPLETE_COVERAGE`; another date or security is never substituted. The error for an unavailable date includes the available dates. No complete long-term archive, weekly-to-daily conversion, holiday interpolation or historical URL probing is supplied. The default JPX connectivity diagnostic still probes the listed-company workbook; it does not certify margin PDF availability.

The directory is cached for five minutes and original PDFs for one hour. Layout extraction runs in a separate bounded process. A large report can take over a minute on its first extraction; identical bytes reuse a bounded two-document memory cache within the running process. This is a daily source, with no intraday refresh claim.

## Values and calculations

| Output                               | Meaning                                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `shares.buy_balance`, `sell_balance` | Reported outstanding credit purchases and sales, in one-share units                              |
| `shares.buy_change`, `sell_change`   | Source daily changes; the triangle marker means a negative change                                |
| `general_*`, `standardized_*`        | Source general/negotiable and standardized components, with separate daily changes               |
| `*_listed_share_pct`                 | Source-rounded listed-share percentages, as decimal percentage strings                           |
| `amount_jpy`                         | Separately reported JPY balances and changes; no repricing from current quotes                   |
| `margin_ratio`                       | Derived `buy_balance_shares / sell_balance_shares`, with both exact reported inputs and evidence |

The PDF uses one share and one JPY. A `B` issue label indicating a 100-share trading unit does not multiply the reported balances. JPX treats one investment-trust unit as one share in this report; raw labels are retained. Each nonmissing reported total must equal its general and standardized components, separately for shares, JPY, balances and changes. Missing totals are not reconstructed from components. Missing markers remain null, and genuine zero balances remain zero.

The ratio uses exact integer/Decimal arithmetic, 18 decimal places and half-even rounding. A zero sell balance returns the balances with a null ratio and `zero_sell_balance`; it does not return infinity. Missing share inputs produce a null ratio with `missing_reported_share_balance`. The output never substitutes a JPY amount ratio. Ratios are descriptive observations, not trading recommendations.

`coverage.missing_fields` identifies missing numeric balances or changes and `partial` marks those gaps. Source percentages can be unavailable, including investment-trust conventions, without implying a zero percentage. Optional [quality requirements](agents.md) use application dates; they do not certify the economic interpretation of every field.

## Evidence and source conditions

Every reported numeric cell retains its original PDF URL, SHA-256, retrieval time, PDF page and one-based layout text line/column span, plus the exact reported text and extraction version. Rotation is normalized in memory before layout extraction; raw PDF bytes remain unchanged. Headers, units, numeric column order, issue identity and component arithmetic fail explicitly if they differ from the supported contract. Evidence also resolves through the existing compact view. The dedicated worker accepts at most 160 pages and 8 MiB; the independent analyst-report importer keeps its 100-page limit.

These are exchange margin trading balances. They do not represent the operator's account positions, collateral or maintenance margin, JSF financing/lending balances, or all institutional short interest. A large buy balance or margin ratio does not establish a future price direction. Broker-account integration and actual trading are not provided.

[JPX terms](https://www.jpx.co.jp/term-of-use/index.html) apply separately from Atlas's Apache-2.0 code license. Free access does not grant commercial collection, secondary-use or redistribution rights. Actual source PDFs and extracted observations must remain private where those permissions are absent. The bundled fixture is entirely fictional.

An explicit private source check can save a CSV, normalized JSON, raw source hashes and independent original-cell/ratio checks for selected issues and up to five currently linked reports:

```sh
uv run --offline python scripts/verify_margin_live.py --allow-network \
  --identifiers 7203.T,7267.T,6758.T --report-count 2 \
  --output build/margin-live
```

The script uses the ordinary public transport and existing private cache. It does not request a data subscription, run automatically, or verify the entire issuer universe. See [dated validation](verification.md#japanese-margin-balances) and [source conditions](../DATA_SOURCES.md).
