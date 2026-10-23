# Retrieve CPI, SOFR and USD/JPY through one MCP connection

Atlas Terminal retrieves US consumer prices from BLS, SOFR from the New York Fed and USD/JPY reference FX from ECB inputs through one MCP connection. These are independent observations with different dates and units. ECB reference FX describes a daily reference rate; it is not an executable live market quote.

## Execute the case

Follow the [reproduction guide](research-examples.md), then run:

```sh
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case inflation-rates-fx
```

The [saved queries](../examples/citation_cases.json) select the latest returned CPI, SOFR and USD/JPY observation. This case needs no SEC contact setting or data account. Supported installations are documented in [Codex setup](codex.md).

## Interpret the result

| Item    | Meaning                                        | Interpretation rule                                           |
| ------- | ---------------------------------------------- | ------------------------------------------------------------- |
| CPI     | BLS all-items US consumer price index          | Preserve the base index, seasonal adjustment and actual month |
| SOFR    | New York Fed secured overnight financing rate  | Preserve the percent-per-annum unit and effective date        |
| USD/JPY | JPY per USD from same-day ECB reference inputs | Preserve the formula and both original EUR-based inputs       |

The batch does not establish a synchronized market snapshot. Monthly CPI cannot describe the same observation interval as an overnight rate. Retrieval time does not establish publication time. Missing publication metadata stays unknown.

## Cite the result

Cite the returned BLS, New York Fed and ECB sources for numerical observations. Retain each raw-source hash and JSON/XML locator in the captured evidence. Link [Atlas macro methodology](macro.md) when explaining normalization or the cross-rate calculation.

No live observation snapshot has been published for this case yet. The capture command produces a dated result only after actual retrieval succeeds; the offline fixture example contains fictional values.

[Complete capture format](research-examples.md) · [Source terms](../DATA_SOURCES.md)
