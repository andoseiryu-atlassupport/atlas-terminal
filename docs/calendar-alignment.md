# Aggregate macro series while preserving periods and missing data

Atlas Terminal can aggregate explicit macro queries into monthly, quarterly or annual calendar buckets. Each series selects `last` or `mean` explicitly. Results retain exact inputs, units, adjustment metadata and missing cells. Calendar alignment alone does not establish that two economic indicators are statistically comparable.

## Execute the case

From the source checkout, run:

```sh
uv run --offline python scripts/capture_research_examples.py --provider public --allow-network --case calendar-alignment
```

The [saved case](../examples/citation_cases.json) requests quarterly US CPI means and the last monthly unemployment observation in each quarter from January through September 2026. No SEC contact setting or data account is required. Review actual observation availability when executing it.

## Inspect the calendar rules

| Condition                                               | Result                                                    |
| ------------------------------------------------------- | --------------------------------------------------------- |
| Three usable monthly CPI inputs exist for a quarter     | Return the explicit arithmetic mean and all three inputs  |
| A required monthly input is absent or null              | Keep the aggregate unavailable; inspect its reason        |
| Units, base period, adjustment or frequency change      | Fail that series instead of combining incompatible inputs |
| A monthly series is requested at daily frequency        | Reject unsupported upsampling                             |
| A daily business calendar has unknown expected coverage | Report that coverage limitation                           |

`last` selects the last actual observation under the same complete-period rules. The actual input date is preserved separately from the calendar bucket end. There is no forward filling, interpolation or reconstruction of historical releases. The source supplies current or revised observations.

## Cite the result

Cite BLS for original observations. For an aggregate, preserve the aggregation method, exact inputs, original source hashes and periods in the capture. Link [Atlas calendar methodology](international-macro.md) for aggregation behavior.

No live observation snapshot has been published for this case yet. The fixture run checks behavior using fictional observations and includes explicit missing cells where fixture coverage is incomplete.

[Complete capture format](research-examples.md) · [Source terms](../DATA_SOURCES.md)
