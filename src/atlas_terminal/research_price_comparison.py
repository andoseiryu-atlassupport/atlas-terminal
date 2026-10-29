"""Comparable source-price histories with explicit exact-date alignment.

Stocks and ETFs must share currency and market timezone. All observed date
vectors must match; no intersection, carry, gap filling or cross-currency
conversion is performed. Correlations are descriptive, not forecasts.
"""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import TYPE_CHECKING, Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Transport
from atlas_terminal.research_price_analytics import CALCULATION_PRECISION, _formatted

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery

MAX_COMPARISON_OBSERVATIONS = 500
MAX_TOTAL_COMPARISON_OBSERVATIONS = 3000
PRICE_COMPARISON_LIMITATIONS = [
    "Only explicitly selected stocks/ETFs with one currency and market timezone are compared. "
    "Observed date vectors must match exactly; no intersection, interpolation, resampling, "
    "currency conversion or missing-price repair is performed.",
    "Simple adjusted-close returns are calculated between consecutive source observations. "
    "Volatility and covariance use the sample denominator n-1 and are not annualized. "
    "Correlation is unavailable when either observed return series has zero variance.",
    "Cumulative performance compounds observed adjusted-close ratios from the first selected "
    "price. Maximum drawdown is the most negative close-to-running-peak ratio in this window; "
    "intraday drawdowns, fees, taxes, trading frictions and original cash dividends are absent.",
    "Shared source dates do not verify exchange-calendar completeness or synchronous closes. "
    "Current revised source prices are not point-in-time vintages, a complete investment "
    "universe or evidence of future correlation or risk. Source data rights remain separate.",
]


def validate_price_comparison(query: "ResearchQuery") -> None:
    """Reject unsupported comparisons before any upstream source request."""
    from atlas_terminal.equity_sources import yahoo_profile, yahoo_url

    if not 2 <= len(query.identifiers) <= 10 or len(set(query.identifiers)) != len(
        query.identifiers
    ):
        raise AtlasError("INVALID_INPUT", "Choose 2–10 distinct stock/ETF identifiers.")
    if query.start_date is None or query.end_date is None:
        raise AtlasError("INVALID_INPUT", "Price comparisons require explicit start and end dates.")
    profiles = [yahoo_profile(identifier) for identifier in query.identifiers]
    if any(profile["asset_class"] != "security" for profile in profiles):
        raise AtlasError("INVALID_INPUT", "Price comparisons support stocks and ETFs only.")
    if len({(profile["currency"], profile["timezone"]) for profile in profiles}) != 1:
        raise AtlasError("INVALID_INPUT", "Price comparisons require one currency and market.")
    for identifier in query.identifiers:
        yahoo_url(identifier, query.start_date, query.end_date)


def _statistics(values: list[Decimal]) -> tuple[list[Decimal], Decimal, Decimal, Decimal]:
    returns = [right / left - 1 for left, right in zip(values[:-1], values[1:], strict=True)]
    mean = sum(returns, Decimal(0)) / len(returns)
    variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / (len(returns) - 1)
    maximum = values[0]
    drawdown = Decimal(0)
    for value in values:
        maximum = max(maximum, value)
        drawdown = min(drawdown, value / maximum - 1)
    return returns, mean, variance, drawdown


def comparison_records(
    inputs: dict[str, list[dict[str, Any]]], currency: str
) -> list[dict[str, Any]]:
    """Build summaries and upper-triangle pairs over already aligned positive prices."""
    counts = {len(rows) for rows in inputs.values()}
    if not 2 <= len(inputs) <= 10 or len(counts) != 1:
        raise AtlasError("INCOMPLETE_COVERAGE", "Price comparison histories do not align.")
    count = next(iter(counts))
    if not 3 <= count <= MAX_COMPARISON_OBSERVATIONS:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Price comparisons require 3–500 observations per asset."
        )
    if count * len(inputs) > MAX_TOTAL_COMPARISON_OBSERVATIONS:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Price comparisons accept at most 3000 total price inputs."
        )
    dates = [row["date"] for row in next(iter(inputs.values()))]
    if any(left >= right for left, right in zip(dates, dates[1:], strict=False)):
        raise AtlasError("SOURCE_UNAVAILABLE", "Price comparison dates must be unique and ordered.")
    if any([row["date"] for row in rows] != dates for rows in inputs.values()):
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Observed dates differ; no intersection is applied."
        )
    if len({(row["unit"], row["price_basis"]) for rows in inputs.values() for row in rows}) != 1:
        raise AtlasError("INCOMPLETE_COVERAGE", "Price units or adjustment bases differ.")
    parsed: dict[str, list[Decimal]] = {}
    for identifier, rows in inputs.items():
        values = []
        for row in rows:
            if row.get("source_status") == "ambiguous":
                raise AtlasError("INCOMPLETE_COVERAGE", "A selected source price is ambiguous.")
            if row["value"] is None:
                raise AtlasError("INCOMPLETE_COVERAGE", "A selected adjusted close is missing.")
            value = Decimal(row["value"])
            if not value.is_finite() or value <= 0:
                raise AtlasError(
                    "INCOMPLETE_COVERAGE", "Adjusted closes must be finite and positive."
                )
            values.append(value)
        parsed[identifier] = values
    with localcontext(Context(prec=CALCULATION_PRECISION, rounding=ROUND_HALF_EVEN)):
        return _comparison_records(inputs, parsed, dates, currency)


def _comparison_records(
    inputs: dict[str, list[dict[str, Any]]],
    parsed: dict[str, list[Decimal]],
    dates: list[str],
    currency: str,
) -> list[dict[str, Any]]:
    statistics = {identifier: _statistics(values) for identifier, values in parsed.items()}
    input_ranges = {
        identifier: {
            "table": "price_inputs",
            "identifier": identifier,
            "first": 0,
            "last": len(dates) - 1,
        }
        for identifier in inputs
    }
    common = {
        "date": dates[-1],
        "period": {"kind": "duration", "start": dates[0], "end": dates[-1]},
        "currency": currency,
        "price_basis": next(iter(inputs.values()))[0]["price_basis"],
        "observation_count": len(dates),
        "return_count": len(dates) - 1,
        "annualization_factor": None,
        "derived": True,
        "rounding": "half_even_18_decimal_places",
        "decimal_precision": CALCULATION_PRECISION,
    }
    records = []
    for identifier, values in parsed.items():
        _, mean, variance, drawdown = statistics[identifier]
        results = (
            ("cumulative_return", values[-1] / values[0] - 1, "ratio", "P[last] / P[first] - 1"),
            ("maximum_drawdown", drawdown, "ratio", "min(P[t] / max(P[first:t]) - 1)"),
            ("mean_return", mean, "ratio_per_observation", "sum(r) / n; r=P[t]/P[t-1]-1"),
            (
                "volatility",
                variance.sqrt(),
                "ratio_per_observation",
                "sqrt(sum((r-mean(r))^2)/(n-1)); not annualized",
            ),
        )
        records.append(
            {
                "record_type": "asset_summary",
                "identifier": identifier,
                "records": [
                    {
                        **common,
                        "identifier": identifier,
                        "metric": metric,
                        "value": _formatted(value),
                        "unit": unit,
                        "formula": formula,
                        "input_range": input_ranges[identifier],
                        "status": "derived",
                    }
                    for metric, value, unit, formula in results
                ],
            }
        )
    identifiers = list(parsed)
    for index, left in enumerate(identifiers):
        left_returns, left_mean, left_variance, _ = statistics[left]
        for right in identifiers[index:]:
            right_returns, right_mean, right_variance, _ = statistics[right]
            covariance = sum(
                (
                    (a - left_mean) * (b - right_mean)
                    for a, b in zip(left_returns, right_returns, strict=True)
                ),
                Decimal(0),
            ) / (len(left_returns) - 1)
            correlation = (
                covariance / (left_variance * right_variance).sqrt()
                if left_variance > 0 and right_variance > 0
                else None
            )
            # Exact bounds tolerate only Decimal's final-rounding uncertainty.
            if correlation is not None:
                correlation = min(Decimal(1), max(Decimal(-1), correlation))
            pair_results = (
                (
                    "covariance",
                    covariance,
                    "ratio_squared_per_observation",
                    "sum((r_left-mean_left)*(r_right-mean_right))/(n-1); not annualized",
                ),
                (
                    "correlation",
                    correlation,
                    "ratio",
                    "sample_covariance / sqrt(sample_variance_left*sample_variance_right)",
                ),
            )
            records.append(
                {
                    "record_type": "return_pair",
                    "identifiers": [left, right],
                    "records": [
                        {
                            **common,
                            "identifiers": [left, right],
                            "metric": metric,
                            "value": _formatted(value),
                            "unit": unit,
                            "formula": formula,
                            "input_range": [input_ranges[left], input_ranges[right]],
                            "status": "derived" if value is not None else "unavailable",
                            "reason": None
                            if value is not None
                            else "Correlation is undefined for a zero-variance return series.",
                        }
                        for metric, value, unit, formula in pair_results
                    ],
                }
            )
    return records


def price_comparison(transport: Transport, query: "ResearchQuery") -> dict[str, Any]:
    from atlas_terminal.equity_sources import yahoo_url
    from atlas_terminal.research_equities import chart_result, price_rows
    from atlas_terminal.research_financials import reference
    from atlas_terminal.research_models import ResearchQuery

    validate_price_comparison(query)
    inputs, instruments, sources, exclusions = {}, [], [], {}
    for identifier in query.identifiers:
        selected_query = ResearchQuery(
            dataset="equity_prices",
            identifier=identifier,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=500,
        )
        snapshot = transport.get(yahoo_url(identifier, query.start_date, query.end_date), 900)
        chart, instrument = chart_result(snapshot, identifier)
        if instrument["source_instrument_type"] not in {"EQUITY", "ETF"}:
            raise AtlasError(
                "SOURCE_UNAVAILABLE", "The selected source instrument is not a stock/ETF."
            )
        prices, excluded = price_rows(chart, snapshot, selected_query)
        if not 3 <= len(prices) <= MAX_COMPARISON_OBSERVATIONS:
            raise AtlasError(
                "INCOMPLETE_COVERAGE", "Select a window containing 3–500 observations per asset."
            )
        inputs[identifier] = [
            {
                "index": index,
                "date": row["date"],
                "value": row["adjusted_close"],
                "unit": row["unit"],
                "price_basis": row["adjusted_close_basis"],
                "source_status": row.get("status"),
                "source": row["field_sources"].get("adjusted_close"),
                "date_source": row["field_sources"]["date"],
            }
            for index, row in enumerate(prices)
        ]
        instruments.append(instrument)
        sources.append(reference(snapshot, ""))
        exclusions[identifier] = excluded
    if len({(item["currency"], item["exchange_timezone"]) for item in instruments}) != 1:
        raise AtlasError("INCOMPLETE_COVERAGE", "Source currencies or market timezones differ.")
    records = comparison_records(inputs, instruments[0]["currency"])
    missing = sum(
        leaf["status"] == "unavailable" for record in records for leaf in record["records"]
    )
    dates = [row["date"] for row in next(iter(inputs.values()))]
    return {
        "instruments": instruments,
        "records": records[: query.limit],
        "price_inputs": inputs,
        "sources": sources,
        "calculation": {
            "alignment": "identical_observed_date_vectors_without_intersection",
            "sample_denominator": "return_count_minus_one",
            "correlation_matrix": "upper_triangle_including_diagonal; symmetric",
            "input_range": "inclusive zero-based indices in price_inputs[identifier]",
            "price_input": "adjusted_close",
            "annualization_factor": None,
            "decimal_precision": CALCULATION_PRECISION,
            "rounding": "half_even_18_decimal_places",
        },
        "coverage": {
            "requested_start_date": query.start_date,
            "requested_end_date": query.end_date,
            "first_observation_date": dates[0],
            "last_observation_date": dates[-1],
            "observed_price_records_per_asset": len(dates),
            "return_observations_per_asset": len(dates) - 1,
            "observed_records": len(records),
            "returned_records": min(len(records), query.limit),
            "truncated": len(records) > query.limit,
            "unavailable_records": missing,
            "partial": missing > 0,
            "upstream_window_limited": True,
            "excluded_unconfirmed_session_rows": exclusions,
            "exchange_calendar_verified": False,
            "point_in_time": False,
        },
        "limitations": list(PRICE_COMPARISON_LIMITATIONS),
    }
