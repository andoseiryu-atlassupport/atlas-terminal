# Interest spreads in one command

Retrieve the latest observed government yield-curve spread with a short name. These free Treasury, Japan MOF, ECB and RBA sources need no API key or Yahoo enablement.

```sh
atlas research spread us_10y_2y
atlas research spread jp_10y_2y
atlas research spread --list
```

The default returns one observation with its source date and basis-point value. Use `--json` for the formula, original input yields, units, source URLs, hashes and cell locators. For a fictional offline example:

```sh
uv run --offline atlas research spread us_10y_2y --year 2025 --provider fixture
```

## Names and definitions

| Region / curve                 | Short names                                           |
| ------------------------------ | ----------------------------------------------------- |
| US nominal par                 | `us_10y_2y`, `us_10y_3m`, `us_30y_5y`                 |
| Japan constant maturity        | `jp_10y_2y`, `jp_20y_10y`, `jp_30y_10y`, `jp_40y_30y` |
| Euro area AAA spot             | `ea_aaa_10y_2y`, `ea_aaa_30y_5y`, `ea_aaa_30y_10y`    |
| Euro area all-rating spot      | `ea_all_10y_2y`, `ea_all_30y_5y`, `ea_all_30y_10y`    |
| Australia nominal interpolated | `au_10y_2y`, `au_10y_3y`                              |

Every definition is **long yield minus short yield**, using two observations on the same source date and curve. Both yields remain percent per annum; multiply their difference by 100 to obtain basis points. `100 bp = 1 percentage point`. Negative values mean an inverted yield curve. `percentage_points` provides the same difference without the factor of 100. Decimal strings preserve exact arithmetic and source precision.

ECB AAA and all-rating universes remain separate; their spot curves are continuously compounded. These are euro area aggregates rather than individual national sovereign curves. [Source conventions](fixed-income.md#euro-area-and-australia) remain in the response. This selection does not represent cross-country differentials, corporate credit/OAS spreads or cross-currency basis. FRED's generic reader can retrieve explicitly chosen permitted series when configured with a private key, but the short spread names do not silently select those series.

## Latest observation and history

Without dates, the search window runs from January 1 of the previous source-local year through today in that source's timezone. This includes prior-year observations when the current year has no published rows. It returns the latest **source observation**, rather than asserting a real-time quote or publication timestamp. The effective bounds are recorded in `selection`.

If the latest dated row has a missing tenor, its spread stays null and unavailable. Atlas does not choose an older complete row, mix dates, interpolate, or fill missing yields from another source. A source with no observations in the selected window fails explicitly. Use source-age/observation requirements when freshness matters; a historical archive is not a latency guarantee.

```sh
atlas research spread us_10y_2y --limit 30 --json
atlas research spread jp_10y_2y --start-date 2026-10-01 --end-date 2026-10-30 --limit 30 --json
```

`limit` defaults to 1 and permits up to 500 rows, newest first. Coverage states when a result is truncated. Select either `year` or both inclusive dates, for at most 30 calendar years. US nominal coverage starts in 1990, ECB in 2004 and the selected RBA series in 2013. Explicit dates are observation dates, not historical information-availability dates. Current/revised files do not establish publication-time point-in-time data.

## Shared SDK, MCP and HTTP

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    latest = atlas.get_spread("us_10y_2y")
    history = atlas.get_spread("jp_10y_2y", year=2026, limit=30)
```

The shared research query is:

```json
{ "dataset": "interest_spreads", "series": "us_10y_2y" }
```

Pass it to the MCP tool `atlas_get_research` as its `query` argument, to `POST /v1/research`, or to `Atlas.get_research`. Catalog search accepts the short names and spread-related terms. Compact evidence, saved pagination, explicit quality requirements, batches and macro panels use the existing research contracts. The original longer `macro_analytics` series names continue to work; this interface reuses their calculator and evidence rather than defining a second financial calculation.

The CLI's `--list` is offline and constructs no source client. Live retrieval follows the normal proxy, TLS verification and private cache/history policy. Source access and redistribution remain subject to [upstream conditions](../DATA_SOURCES.md). See [verification](verification.md) for actual retrieval checks and their limits.
