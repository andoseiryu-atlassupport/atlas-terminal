# Trace free cash flow to reported SEC inputs

Atlas Terminal calculates free cash flow as reported operating cash flow minus nonnegative payments to acquire property, plant and equipment for the same actual financial period. It returns the formula and both original inputs so an AI agent can inspect the calculation and cite the SEC disclosure.

## Execute the case

Set your private SEC contact setting as described in [setup](quickstart.md), then run from the source checkout:

```sh
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case reported-free-cash-flow
```

The [saved case](../examples/citation_cases.json) requests AAPL's latest selected annual operating cash flow, capital expenditure and derived free cash flow. Read each result's actual period; separate source requests are not an atomic filing snapshot. The fixture mode substitutes the fictional ATLASDEMO issuer.

## Verify the calculation

1. Check the financial period's actual start and end, rather than inferring it from a fiscal-year label.
2. Inspect the cash-flow inputs preserved inside the derived record. Separate reported rows must refer to the same period before comparison.
3. Subtract nonnegative PP&E acquisition payments from reported operating cash flow using exact decimal arithmetic.
4. Resolve the returned evidence and inspect the filing accession, taxonomy/tag, unit and JSON pointer.
5. Keep missing or conflicting inputs unavailable. No missing quarter is synthesized from annual minus YTD values.

Free cash flow is an Atlas calculation with a stated definition. A company's own adjusted free cash flow can use different exclusions. See [financial methodology](methodology.md) and [calculation rules](research.md#growth-and-cash-flow-analytics).

## Cite the result

Cite the returned SEC filing for reported numbers and link the Atlas methodology for the formula and selection rules. A retrieval hash records the bytes Atlas observed; it does not establish a historical point-in-time data service.

No actual SEC observation snapshot has been published for this case yet. Offline results are fictional; source connectivity and the selected issuer's available facts must be verified on the execution host.

[Complete capture format](research-examples.md) · [Citation rules](citations.md)
