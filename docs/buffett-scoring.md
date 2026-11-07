# Buffett-inspired company scoring

`buffett_score` evaluates annual business fundamentals with a transparent, versioned 100-point rubric. Business-quality proxies carry 80 points; an optional price-versus-value scenario carries 20. These are Atlas editorial thresholds inspired by public Berkshire letters, not Buffett's own scoring system, an endorsement, or a return prediction.

```sh
atlas research buffett AAPL --years 5 --json
```

Automatic reads use the existing SEC primary-filing selector: supported US-GAAP USD facts, matched accession/report dates and explicit annual durations. They require the private `SEC_USER_AGENT` configuration. Unsupported taxonomies are not converted. No Yahoo, paid financial feed or LLM is required.

For any supported declared currency, including JPY, supply your own annual financials and valuation assumptions. Attribution URLs are recorded without being fetched. The files below are entirely fictional:

```sh
atlas research buffett BUFFETTDEMO --provider fixture \
  --input examples/buffett/financials.synthetic.json \
  --valuation-input examples/buffett/valuation.synthetic.json --json
```

```python
import json
from pathlib import Path
from atlas_terminal.client import Atlas

financials = json.loads(Path("examples/buffett/financials.synthetic.json").read_text())
valuation = json.loads(Path("examples/buffett/valuation.synthetic.json").read_text())
with Atlas("fixture") as atlas:
    result = atlas.get_buffett_score("BUFFETTDEMO", financial_input=financials, valuation=valuation)
    print(result["records"][0]["score"])
```

MCP `atlas_get_research` and HTTP `POST /v1/research` accept the same object under their existing research contract:

```json
{
  "dataset": "buffett_score",
  "identifier": "AAPL",
  "count": 5
}
```

Optional `financial_input` and `valuation` contain the corresponding JSON objects rather than server-side filenames. CLI files are bounded to 1 MiB; typed annual input accepts at most eleven periods. Use the existing research batch to evaluate several named companies independently. Scores with different periods, currencies, classifications or missing components do not establish a comparable ranking.

## Rubric and missing data

| Component                  | Weight | Zero-point endpoint                                               | Full-point endpoint             |
| -------------------------- | -----: | ----------------------------------------------------------------- | ------------------------------- |
| Sustained ROE              |     15 | Median annual net income / average opening and closing equity ≤5% | ≥20%                            |
| Operating margin           |     10 | Median annual operating income / revenue ≤5%                      | ≥20%                            |
| Earnings consistency       |     10 | No selected years with positive net income                        | All selected years positive     |
| Free-cash-flow consistency |     10 | No selected years with positive CFO minus PP&E capex              | All selected years positive     |
| Cash conversion            |     10 | Sum CFO / sum net income ≤0.5                                     | ≥1.2                            |
| Balance sheet              |     10 | Latest liabilities / assets ≥0.75                                 | ≤0.35                           |
| Interest coverage          |      5 | Latest operating income / interest expense ≤1                     | ≥8                              |
| Stock compensation         |      5 | Sum stock compensation / sum CFO ≥0.25                            | ≤0.05                           |
| Capital intensity          |      5 | Sum PP&E capex / sum CFO ≥0.8                                     | ≤0.2                            |
| Margin of safety scenario  |     20 | Scenario value does not exceed supplied market cap                | Discount to scenario value ≥30% |

Between endpoints, points interpolate linearly and are clipped to the component's fixed weight. All amounts and calculations use decimal strings, 100-digit Decimal precision and half-even output rounding to 18 places. Sum ratios use complete matching annual windows; ROE uses actual opening equity, not a closing-equity substitution. Liabilities are not labeled total debt. Negative equity cannot produce a favorable ROE by division; nonpositive aggregate earnings/CFO fail the affected rules. A reported zero interest expense receives full coverage points only when operating income is positive; infinity is never emitted. Invalid negative capex, liabilities or expense inputs remain unavailable.

The default window is five annual periods; three through ten are supported. Longitudinal components require consecutive, nonoverlapping reported annual periods. ROE also needs closing equity immediately before the first selected period. The full fictional example therefore supplies six annual rows. Missing years, changed annual duration definitions, inconsistent source concepts or missing cells are not filled. Latest balance-sheet and interest criteria can still be evaluated when longer history is unavailable.

`score` stays null until all 100 points of input coverage are available. `quality_score` stays null until the complete 80-point quality rubric is evaluable. `known_points`, `available_weight` and `score_bounds` show what is known: with 80 achieved quality points and no valuation, bounds are 80–100 and total is null. The bounds describe missing rubric points, not a statistical confidence interval. Missing weights are never redistributed and zero is preserved as an observed value or an evaluated failure. Optional strict quality requirements can reject incomplete output.

General-company rules are not applied to financial businesses. Automatic SEC classification uses a reported four-digit SIC and excludes 6000–6999; missing classification remains unspecified. Supplied classification is a declaration. Banks, insurers, REITs and other financial businesses require a separate model. Even among nonfinancial companies, sector economics, financial subsidiaries, acquisitions and restatements need review.

## Valuation assumptions

There is no automatic market-cap lookup. Supply a positive whole-equity market capitalization, its currency, observation date and timezone-aware capture time. Currency must equal the financial input currency; the quote date cannot precede the selected financials' stated availability. These date checks do not establish historical PIT availability. Synthetic input flags are required and propagate to the result.

The scenario starts with the selected-window mean of **CFO minus total PP&E capex minus stock-based compensation**. This conservative cash-flow proxy is not reported owner earnings or FCFE: maintenance capex, normalized working capital, financing changes and true future requirements remain unknown. It is discounted directly as a rough equity-value scenario; cash is not added and debt is not subtracted again.

Defaults are 3% annual growth, 10% discount rate, 2% terminal growth and ten projection years. These are visible assumptions, not Buffett quotations or forecasts. The schema bounds growth to −10%…10%, discount to 1%…30%, terminal growth to −5%…5%, and the horizon to 1…20 years, with discount strictly above terminal growth. The result includes base, lower and higher growth/discount scenarios; the score uses only the base case. A nonpositive cash-flow base earns zero valuation points without inventing a positive value.

`margin_of_safety = (scenario_equity_value - supplied_market_cap) / scenario_equity_value`. Inspect its sensitivity before using it. The 20 points do not certify a bargain, future returns, or a trade recommendation.

## Evidence and supplied inputs

Every component retains its formula, endpoints, fixed weight, points, unavailable reason and exact `input_ids`. `reported_inputs` contains the selected values, units, periods and original source locators. Compact evidence and field projection preserve calculation context. SEC facts retain original bytes/hash/pointers and filing metadata through the existing private source cache.

Supplied financials declare an issuer identifier, three-letter currency, business type, capture time, original attribution URL, synthetic flag and annual rows (`start`, `end`, `available_on`, `values`). Each numeric value is a decimal string or null. No currency scaling or taxonomy harmonization is inferred. To use Japanese data, set the identifier and JPY amounts explicitly; do not paste thousands/millions as whole-currency values without converting them yourself. Accounting definitions must be consistent: IFRS can classify interest payments outside operating cash flow, which affects this proxy. The supplied-input mode does not normalize those differences or establish cross-country comparability.

`supplied_documents` retains the exact canonical, validated JSON documents whose hashes and pointers appear in the result. Their `https://operator-input.atlas.invalid/` locations identify local request documents, not publisher downloads. `input_origin=operator_supplied_unverified` makes the distinction explicit. Attribution, currency, accounting consistency, classification and availability declarations are not independently verified against the original publisher. Do not include credentials or private URLs in attribution fields.

## Philosophy and OSS review

The [1977 Berkshire letter](https://www.berkshirehathaway.com/letters/1977.html) discusses returns on equity, understandable businesses, long-term prospects, management and price. The [1986 letter](https://www.berkshirehathaway.com/letters/1986.html) explains owner earnings and the difficulty of estimating maintenance investment. The [1992 letter](https://www.berkshirehathaway.com/letters/1992.html) discusses business value and purchase price. These motivate the dimensions; they do not supply this numerical rubric. Circle of competence, management integrity and durable competitive advantage remain explicit manual-review items, with no invented score.

Two MIT-licensed projects were reviewed on 2026-11-08 JST:

- [AI Hedge Fund's current Buffett agent](https://github.com/virattt/ai-hedge-fund/blob/main/hedge_fund/signals/buffett.py), reviewed blob `d6753190ce500f141d52c960708dc0ae237a2ac8`, expresses a philosophy checklist through an LLM prompt. Atlas implements reproducible arithmetic and leaves qualitative judgment unscored. Its [MIT license](https://github.com/virattt/ai-hedge-fund/blob/main/LICENSE) was inspected.
- [FinanceToolkit's Piotroski model](https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/models/piotroski_model.py) exposes separate financial criteria and a transparent aggregate. This informed presentation of components and inputs; this score is not a Piotroski F-Score. Its [MIT license](https://github.com/JerBouma/FinanceToolkit/blob/main/LICENSE.txt) was inspected.

No source code was copied and neither package was added as a dependency. These projects' source/API conditions do not grant rights to underlying financial data. See [validation scope](verification.md#buffett-inspired-scoring) and [source conditions](../DATA_SOURCES.md).
