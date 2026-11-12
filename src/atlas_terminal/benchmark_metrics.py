"""Portfolio-only statistics are independent of the selected benchmark's date coverage."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from atlas_terminal.benchmark_models import BenchmarkPortfolio

PRECISION = 256


def formatted(value: Decimal) -> str:
    return format(value.quantize(Decimal("1e-18"), rounding=ROUND_HALF_EVEN), "f")


def metric(value: Decimal | None, formula: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "value": formatted(value) if value is not None else None,
        "status": "derived" if value is not None else "unavailable",
        "reason": reason,
        "formula": formula,
        "unit": "ratio",
    }


def portfolio_comparison(
    portfolio: BenchmarkPortfolio, benchmark: list[dict[str, Any]]
) -> dict[str, Any]:
    with localcontext(Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)):
        return _comparison(portfolio, benchmark)


def _comparison(p: BenchmarkPortfolio, benchmark: list[dict[str, Any]]) -> dict[str, Any]:
    observed = p.observations
    days = (date.fromisoformat(observed[-1].date) - date.fromisoformat(observed[0].date)).days
    values = [Decimal(row.value) if row.value is not None else None for row in observed]
    complete = all(value is not None for value in values)
    returns: list[Decimal] = []
    intervals: list[dict[str, Any]] = []
    by_date = {row["date"]: row for row in benchmark}
    paired_p, paired_b = [], []
    used_benchmarks: dict[str, dict[str, Any]] = {}
    for index, (left, right) in enumerate(zip(observed, observed[1:], strict=False)):
        first, last = values[index], values[index + 1]
        r = last / first - 1 if first is not None and last is not None else None
        if r is not None:
            returns.append(r)
        bleft, bright = by_date.get(left.date), by_date.get(right.date)
        usable = all(row is not None and row["value"] is not None for row in (bleft, bright))
        br = None
        if usable:
            assert bleft is not None and bright is not None
            br = Decimal(bright["value"]) / Decimal(bleft["value"]) - 1
            used_benchmarks.update({left.date: bleft, right.date: bright})
        if r is not None and br is not None:
            paired_p.append(r)
            paired_b.append(br)
        intervals.append(
            {
                "start": left.date,
                "end": right.date,
                "portfolio_input_indices": [index, index + 1],
                "portfolio_return": formatted(r) if r is not None else None,
                "benchmark_return": formatted(br) if br is not None else None,
                "excess_return": formatted(r - br) if r is not None and br is not None else None,
                "formula": "R=P[end]/P[start]-1; excess_return=portfolio_return-benchmark_return",
                "reason": "missing_portfolio_endpoint"
                if r is None
                else "missing_benchmark_or_fx_endpoint"
                if br is None
                else None,
            }
        )
    cagr = cumulative = None
    cagr_reason = "missing_portfolio_values" if not complete else None
    if complete:
        assert values[0] is not None and values[-1] is not None
        ratio = values[-1] / values[0]
        cumulative = ratio - 1
        exponent = ratio.ln() * Decimal("365.2425") / days
        if abs(exponent) <= 60:
            cagr = exponent.exp() - 1
        else:
            cagr_reason = "annualized_result_outside_numeric_bounds"
    sharpe = None
    sharpe_reason: str | None = "risk_free_rate_not_supplied"
    rf_session = None
    if p.risk_free_rate_annual is not None:
        rf_session = (1 + Decimal(p.risk_free_rate_annual)) ** (
            Decimal(1) / p.sessions_per_year
        ) - 1
        if not complete:
            sharpe_reason = "missing_portfolio_values"
        elif len(returns) < 2:
            sharpe_reason = "fewer_than_two_portfolio_returns"
        else:
            mean = sum(returns, Decimal(0)) / len(returns)
            variance = sum(((x - mean) ** 2 for x in returns), Decimal(0)) / (len(returns) - 1)
            if variance:
                sharpe = (mean - rf_session) / variance.sqrt() * Decimal(p.sessions_per_year).sqrt()
                sharpe_reason = None
            else:
                sharpe_reason = "zero_portfolio_return_variance"
    beta = None
    beta_reason: str | None = "fewer_than_two_matched_return_intervals"
    if len(paired_b) >= 2:
        mp = sum(paired_p, Decimal(0)) / len(paired_p)
        mb = sum(paired_b, Decimal(0)) / len(paired_b)
        denominator = sum(((x - mb) ** 2 for x in paired_b), Decimal(0))
        if denominator:
            beta = (
                sum(
                    ((x - mp) * (y - mb) for x, y in zip(paired_p, paired_b, strict=True)),
                    Decimal(0),
                )
                / denominator
            )
            beta_reason = None
        else:
            beta_reason = "zero_benchmark_return_variance"
    excess = None
    full_pairs = len(paired_b) == len(intervals)
    if full_pairs and cumulative is not None:
        bfirst, blast = by_date[observed[0].date], by_date[observed[-1].date]
        excess = cumulative - (Decimal(blast["value"]) / Decimal(bfirst["value"]) - 1)
    paired = [row for row in intervals if row["excess_return"] is not None]
    return {
        "model_version": "benchmark-comparison-v1",
        "portfolio_inputs": p.model_dump(),
        "input_origin": "user_supplied_unverified",
        "decimal_precision": PRECISION,
        "rounding": "ROUND_HALF_EVEN_18_places",
        "portfolio_period": {"start": observed[0].date, "end": observed[-1].date},
        "comparison_period": {
            "start": paired[0]["start"] if paired else None,
            "end": paired[-1]["end"] if paired else None,
            "matched_intervals": len(paired),
            "requested_intervals": len(intervals),
            "complete": full_pairs,
            "note": "Bounding dates may contain omitted intervals; inspect interval reasons.",
        },
        "portfolio_metrics": {
            "cumulative_return": metric(
                cumulative,
                "NAV[last]/NAV[first]-1",
                "missing_portfolio_values" if not complete else None,
            ),
            "cagr": metric(
                cagr, "(NAV[last]/NAV[first])^(365.2425/elapsed_calendar_days)-1", cagr_reason
            ),
            "sharpe_ratio": metric(
                sharpe,
                "sqrt(sessions_per_year)*(mean(r)-rf_session)/sample_stddev(r)",
                sharpe_reason,
            ),
            "risk_free_return_per_session": formatted(rf_session)
            if rf_session is not None
            else None,
            "risk_free_formula": "(1+user_annual_effective_risk_free_rate)^(1/sessions_per_year)-1",
            "return_count": len(returns),
            "elapsed_calendar_days": days,
            "benchmark_independent": True,
        },
        "relative_metrics": {
            "beta": metric(
                beta,
                "sample_cov(portfolio_returns,benchmark_returns)/sample_var(benchmark_returns)",
                beta_reason,
            ),
            "excess_cumulative_return": metric(
                excess,
                "portfolio_cumulative_return-benchmark_cumulative_return",
                None if full_pairs else "incomplete_matched_intervals",
            ),
        },
        "intervals": intervals,
        "benchmark_inputs": [used_benchmarks[key] for key in sorted(used_benchmarks)],
        "limitations": [
            "NAV, dividend basis, removed external flows and observation completeness are "
            "caller assertions.",
            "Sharpe uses every supplied consecutive portfolio session and explicit session "
            "annualization; an omitted session cannot be inferred. Monthly/sparse NAV is not "
            "daily NAV.",
            "Beta uses exactly paired endpoint intervals, without bridging an excluded "
            "portfolio interval. Partial beta retains its sample count; full-period excess "
            "requires all intervals.",
            "Excess return means portfolio minus selected benchmark, not risk-free excess or "
            "regression alpha.",
        ],
    }
