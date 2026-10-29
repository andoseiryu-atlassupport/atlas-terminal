# Offline time-series statistics

Analyze one saved Atlas research result without another source request:

```sh
uv run --offline atlas research statistics --input prices.json --output statistics.json --field adjusted_close --transform return --max-lag 10 --adf-lags 1 --kpss-lags 3
uv run --offline atlas research statistics --input monthly.json --output monthly-statistics.json --field value --frequency monthly --deterministic trend --adf-lags 2 --kpss-lags 3
```

The CLI creates a new private JSON file and refuses to overwrite an existing file. It does not load source credentials, fetch observations, write into the research cache or publish a report. Keep the original source conditions when sharing any saved result.

Python callers use the same calculation:

```python
from atlas_terminal.research_statistics import analyze_research_series

report = analyze_research_series(
    saved_result,
    field="adjusted_close",
    transform="return",
    frequency="observations",
    max_lag=10,
    adf_lags=1,
    kpss_lags=3,
    deterministic="constant",
)
```

## Inputs and selection

The input must be a full, unprojected, untruncated research result with embedded original-source evidence. Compact references and partial pages are rejected. The input is limited to 4 MiB, 5,000 records, nesting depth 64 and 100,000 nested values. The report is limited to 5 MiB.

Select one top-level numeric field, optionally restricted to one exact metric. The selected rows must have unique ISO observation dates, a consistent series identity, price basis and unit, and finite decimal strings or integers. Ambiguous, partial, unavailable or null selected observations fail without removing rows. Values are bounded to magnitude 1e40, at most 80 digits and decimal exponents from -40 to 40. Binary floating-point values are rejected.

Rows are sorted by observation date. The report retains the original input unchanged, its canonical SHA-256, the indices of the selected original records, transformed values, source attribution, units and coverage. It does not re-download or independently authenticate the source bytes behind a supplied hash.

Input coverage may report a broader partial result, for example an unavailable warm-up period in another metric. A complete selected metric can still be analyzed; the report retains that original coverage, sets `input_reports_partial_coverage`, and always sets `upstream_completeness_verified=false`. Passing selected-row validation does not certify the provider's entire history.

## Transform and spacing

| Option       | Meaning                                                                                                  |
| ------------ | -------------------------------------------------------------------------------------------------------- |
| `level`      | Use the selected values directly.                                                                        |
| `difference` | Subtract each immediately preceding supplied value.                                                      |
| `return`     | Divide by the preceding supplied value and subtract one; every original value must be strictly positive. |

Differences retain the input unit. Simple returns use `ratio_per_observation`. A transform removes only its mathematically required first output; the initial source value remains in the evidence. There is no automatic logarithm, annualization, seasonal adjustment or source aggregation.

The default `frequency=observations` treats successive selected rows as the intended equally spaced process. It does not verify trading sessions or detect an omitted observation. Choose this only when that sampling assumption is appropriate. With `daily` or `weekly`, dates must be exactly one or seven calendar days apart. With `monthly`, `quarterly` or `annual`, consecutive labels must be one, three or twelve months apart, using a fixed day of the month or consistent month ends. For example, month-end February labels can differ from January and March. A requested calendar frequency with a missing period fails; Atlas never inserts a row. Intraday timestamps are outside this date-based analyzer's scope.

## Descriptive and autocorrelation output

At least three transformed observations are required. Summary output includes the count, minimum, maximum, mean, median, sample variance and sample standard deviation. Sample variance uses denominator `n-1`.

For each requested lag `k`, autocorrelation is:

```text
sum((y[t] - mean) * (y[t-k] - mean)) / sum((y[t] - mean)^2)
```

The cumulative Ljung–Box statistic through lag `h` is:

```text
Q[h] = n * (n+2) * sum(acf[k]^2 / (n-k), k=1..h)
```

No Ljung–Box p-value or significance decision is supplied. These are raw-series diagnostics without a fitted-model degrees-of-freedom correction. A constant series retains descriptive statistics but has unavailable autocorrelation.

## ADF and KPSS

These diagnostics test different null hypotheses. Failure to reject either null does not prove it. Neither test supplies an economic forecast, causality claim or automatic investment conclusion. Lag counts are fixed by the caller from 0 to 24; they must be smaller than the sample. There is no automatic lag selection or multiple-testing adjustment.

`deterministic=constant` includes an intercept. `deterministic=trend` includes an intercept and a linear observation-index trend. The same choice applies to both tests.

**Augmented Dickey–Fuller (ADF)** fits:

```text
Δy[t] = constant + gamma*y[t-1] + optional trend
        + sum(delta[j]*Δy[t-j], j=1..adf_lags) + error[t]
statistic = gamma / standard_error(gamma)
```

The null is a unit root. The alternative is level stationarity with a constant, or trend stationarity with a linear trend. The test uses ordinary least squares with a twice-orthogonalized Decimal QR decomposition. Centering and scaling improve numerical conditioning without changing the test statistic. No regularization or pseudo-inverse repairs a deficient design.

With `N` transformed values and `p` fixed lagged differences, the regression has `N-1-p` observations and `p+2` coefficients, plus one if a trend is included. At least 20 regression observations and 10 residual degrees of freedom are required. Critical values at 1%, 5% and 10% use the N=1 MacKinnon (2010) finite-sample response surface:

```text
critical_value = a0 + a1/n + a2/n^2 + a3/n^3
```

Here `n` is the regression observation count. Coefficients, sample size, statistic and standard error are returned. Rejection requires the statistic to be strictly below the corresponding critical value. ADF is not compared against ordinary Student-t critical values.

**Kwiatkowski–Phillips–Schmidt–Shin (KPSS)** has level or trend stationarity as its null, with a unit-root alternative. It first removes the selected deterministic terms, then divides the squared cumulative-residual sum by a Bartlett-kernel estimate of the long-run variance. The fixed `kpss_lags` determines the Bartlett bandwidth:

```text
eta = sum(cumsum(residual)^2) / n^2
long_run_variance =
  (sum(residual^2)
   + 2*sum((1-j/(lags+1))*sum(residual[t]*residual[t-j]), j=1..lags)) / n
statistic = eta / long_run_variance
```

At least 20 observations are required. Asymptotic critical values from Table 1 of KPSS (1992) are returned at 10%, 5%, 2.5% and 1%. Rejection requires the statistic to be strictly above the corresponding critical value. No interpolated or clipped p-value is reported for either test.

## Numerical limits and interpretation

Calculations use a private 256-digit Decimal context; final numbers are rounded half-even to 18 fractional digits. Division and square roots use finite precision. The original exact input values remain embedded, so rounded analysis values do not replace the source. Critical-value decisions use the unrounded calculation.

Constant series, rank-deficient or ill-conditioned ADF designs, and negligible stochastic residuals produce an explicit `unavailable` result without a rejection decision. Relative QR residual norms below 1e-24 indicate deficient conditioning. Residual or long-run variance below the documented relative 1e-48 threshold is considered numerically negligible.

The tests are approximate and sensitive to sample size, lag choice, deterministic terms, structural breaks and the sampling process. The minimum sample rules are computation bounds, not a guarantee of statistical power. Cointegration, seasonal-unit-root tests, GARCH, regime changes, forecasts and historical publication availability are not supplied.

## References and verification

- James G. MacKinnon (2010), [Critical Values for Cointegration Tests, working paper 1227](https://www.econ.queensu.ca/sites/econ.queensu.ca/files/qed_wp_1227.pdf). Only the univariate N=1 ADF response-surface coefficients for a constant or constant plus trend are used.
- Denis Kwiatkowski, Peter C. B. Phillips, Peter Schmidt and Yongcheol Shin (1992), [Testing the null hypothesis of stationarity against the alternative of a unit root](<https://doi.org/10.1016/0304-4076(92)90104-Y>), _Journal of Econometrics_ 54, 159–178. KPSS equations 10–11 and Table 1.
- Published coefficients and definitions were cross-checked against [statsmodels 0.14.4 ADF values](https://github.com/statsmodels/statsmodels/blob/v0.14.4/statsmodels/tsa/adfvalues.py) and [ADF/KPSS implementations](https://github.com/statsmodels/statsmodels/blob/v0.14.4/statsmodels/tsa/stattools.py). Atlas uses its own Decimal implementation and has no statsmodels dependency.

Offline regressions compare hand-calculated descriptive/ACF/Ljung–Box values and fixed stationary/nonstationary vectors against independently calculated statsmodels 0.14.4 results. These checks verify numerical implementation for those cases; they do not establish forecast accuracy or statistical conclusions for real markets.

[Market research](market-research.md) · [Research workbench](research-workbench.md) · [Verification](verification.md)
