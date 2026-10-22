# Retrieve company financials, macro data and FX through one connection

Atlas Terminal provides three presets for common research tasks. Discover their accepted fields in `atlas_research_catalog`, preview the exact plan, then inspect every item and its evidence. These examples use the packaged fictional fixture provider. They do not show current observations or establish live source availability.

## Company financials and free cash flow

For a complete workflow spanning company financials, CPI and USD/JPY in one batch, use the [runnable MCP example](company-macro-fx.md). It includes exact fictional output, saved queries, evidence resolution and an explicit public-source mode.

```sh
uv run --offline atlas research preset company_overview --identifier ATLASDEMO --period quarterly --count 1 --provider fixture --json
```

This retrieves issuer profile, reported statements, financial analytics and filing-discovery metadata. The financial inputs must meet the actual-period rules; missing values are not inferred. The synthetic issuer ATLASDEMO is not a real company. For public SEC data use a real identifier, `--provider public`, and your privately configured SEC contact setting.

Equivalent MCP arguments for `atlas_get_research_preset`:

```json
{
  "query": {
    "preset": "company_overview",
    "identifier": "ATLASDEMO",
    "period": "quarterly",
    "count": 1
  }
}
```

Use ATLASDEMO only when the server itself is configured for fixtures. [Financial methodology](methodology.md).

## US inflation, employment and rates

```sh
uv run --offline atlas research preset us_macro_overview --include cpi,cpi_yoy,unemployment,sofr --provider fixture --json
```

The named item IDs select CPI level and year-over-year calculation, unemployment and overnight SOFR. No SEC configuration is needed for these sources. A monthly observation, an overnight rate's effective date and a source retrieval timestamp describe different events.

MCP arguments:

```json
{
  "query": {
    "preset": "us_macro_overview",
    "include": ["cpi", "cpi_yoy", "unemployment", "sofr"]
  }
}
```

## USD/JPY reference FX and US rate context

```sh
uv run --offline atlas research preset fx_context --base-currency USD --quote-currency JPY --include fx,us_sofr --provider fixture --json
```

MCP arguments:

```json
{
  "query": {
    "preset": "fx_context",
    "base_currency": "USD",
    "quote_currency": "JPY",
    "include": ["fx", "us_sofr"]
  }
}
```

An abbreviated fixture outcome is shown below. It deliberately omits source records; retrieve the full result or compact evidence before citation.

```json
{
  "mode": "executed",
  "status": "ok",
  "is_synthetic": true,
  "requires_wallet": false,
  "atomic_snapshot": false,
  "batch": {
    "summary": {
      "requested": 2,
      "successful": 2,
      "failed": 0,
      "retryable_read_ids": []
    }
  }
}
```

Read `overview` for usable/missing records, observation dates, known/unknown publication times and source cadence. USD/JPY is a derived same-day ECB reference cross, not a real-time tradeable quote. The context items describe the US, not both currency economies.

## Reuse the same workflow in Python

```python
from atlas_terminal.client import Atlas

with Atlas("fixture") as atlas:
    result = atlas.get_research_preset(
        {
            "preset": "fx_context",
            "base_currency": "USD",
            "quote_currency": "JPY",
            "include": ["fx", "us_sofr"],
            "view": "full",
        }
    )
    print(result["overview"])
    print(result["batch"]["summary"])
```

[Saved JSON queries](../examples/research_presets/fx_context.json) · [Setup](codex.md) · [Preset contracts](presets.md) · [Sources](../DATA_SOURCES.md)
