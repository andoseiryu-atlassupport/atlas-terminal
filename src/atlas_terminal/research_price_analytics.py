"""Trailing price indicators with bounded, source-addressable input ranges.

Calculations use only the selected source observations up to each output date.
The input table is shared so recursive indicators do not duplicate their entire
history in every row. No additional pre-window observations are fetched.
"""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import TYPE_CHECKING, Any

from atlas_terminal.economic_dates import window_metadata
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Transport

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery

DECIMAL_PLACES = 18
CALCULATION_PRECISION = 256
QUANTUM = Decimal("1e-18")


@dataclass(frozen=True)
class PriceIndicator:
    formula: str
    minimum_observations: int
    unit: str = "ratio"
    window_observations: int | None = None
    recursive: bool = False


PRICE_METRICS: dict[str, PriceIndicator] = {
    "return_1d": PriceIndicator("P[t] / P[t-1] - 1", 2, window_observations=2),
    "cumulative_return": PriceIndicator("P[t] / P[first_selected] - 1", 1),
    "sma_20": PriceIndicator("sum(P[t-19:t]) / 20", 20, "price", 20),
    "sma_50": PriceIndicator("sum(P[t-49:t]) / 50", 50, "price", 50),
    "ema_12": PriceIndicator(
        "seed = mean(first 12 consecutive P); EMA[t] = (2*P[t] + 11*EMA[t-1]) / 13",
        12,
        "price",
        recursive=True,
    ),
    "ema_26": PriceIndicator(
        "seed = mean(first 26 consecutive P); EMA[t] = (2*P[t] + 25*EMA[t-1]) / 27",
        26,
        "price",
        recursive=True,
    ),
    "rsi_14": PriceIndicator(
        "d=P[t]-P[t-1]; seed gains/losses=mean(first 14 positive/negative d); "
        "G[t]=(13*G[t-1]+max(d,0))/14; L[t]=(13*L[t-1]+max(-d,0))/14; "
        "RSI=100*G/(G+L); G=L=0 gives 50",
        15,
        "index_0_100",
        recursive=True,
    ),
    "volatility_20": PriceIndicator(
        "r=P[t]/P[t-1]-1; sqrt(sum((r-mean(last 20 r))^2)/19); not annualized",
        21,
        "ratio_per_observation",
        21,
    ),
    "drawdown": PriceIndicator("P[t] / max(P[first_selected:t]) - 1", 1),
    "macd": PriceIndicator("EMA12[t] - EMA26[t]", 26, "price", recursive=True),
    "macd_signal": PriceIndicator(
        "seed = mean(first 9 MACD); signal[t] = (2*MACD[t] + 8*signal[t-1]) / 10",
        34,
        "price",
        recursive=True,
    ),
    "macd_histogram": PriceIndicator("MACD[t] - MACD_signal[t]", 34, "price", recursive=True),
    "bollinger_upper_20": PriceIndicator(
        "mean(last 20 P) + 2*sqrt(sum((P-mean(last 20 P))^2)/20)", 20, "price", 20
    ),
    "bollinger_lower_20": PriceIndicator(
        "mean(last 20 P) - 2*sqrt(sum((P-mean(last 20 P))^2)/20)", 20, "price", 20
    ),
}
DEFAULT_PRICE_METRICS = ["return_1d", "cumulative_return", "sma_20", "rsi_14", "drawdown"]
PRICE_ANALYTICS_LIMITATIONS = [
    "Optional unofficial international Yahoo access is for personal use; no data redistribution "
    "or commercial license is granted. Source availability is not guaranteed.",
    "Indicators use the source adjusted-close field only. They do not substitute close, "
    "reconstruct total returns, repair prices or establish an investable futures roll strategy.",
    "Windows count consecutive returned observations, not verified trading sessions. Missing "
    "calendar dates are not inserted; return_1d means the preceding reported observation.",
    "Null, nonpositive or source-ambiguous adjusted prices invalidate affected calculations. "
    "Rolling and "
    "recursive indicators restart after such an observation; cumulative return and drawdown "
    "remain unavailable after a gap because their scope starts at the first selected row.",
    "EMA uses a simple-average seed; Wilder RSI uses 14 price changes and returns 50 for a "
    "flat seed/history. Bollinger bands use population standard deviation and multiplier 2. "
    "Volatility uses sample standard deviation of 20 simple returns and is not annualized.",
    "Only the selected window is used, with no hidden warm-up request. Recursive values depend "
    "on that window and the last reset. Dates are observation dates, not publication times; "
    "current revised source prices do not establish point-in-time availability.",
]


def price_analytics_catalog() -> dict[str, Any]:
    return {
        name: {**asdict(spec), "price_input": "adjusted_close", "annualization_factor": None}
        for name, spec in PRICE_METRICS.items()
    }


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def _deviation(values: list[Decimal], *, sample: bool = False) -> Decimal:
    mean = _mean(values)
    return (
        sum(((value - mean) ** 2 for value in values), Decimal(0)) / (len(values) - int(sample))
    ).sqrt()


def _formatted(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rounded = value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    return format(abs(rounded) if rounded == 0 else rounded, "f")


def _ema(values: list[Decimal], previous: Decimal | None, length: int) -> Decimal | None:
    if len(values) < length:
        return None
    if previous is None:
        return _mean(values[-length:])
    return (2 * values[-1] + (length - 1) * previous) / (length + 1)


def indicator_records(
    prices: list[dict[str, Any]], metrics: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute ascending rows; input indices address the returned price_inputs table.

    The reader has already validated source ordering, identity, bounds and closed
    sessions. Defend ordering here too for callers using this pure helper.
    """
    if not metrics or len(set(metrics)) != len(metrics) or set(metrics) - PRICE_METRICS.keys():
        raise AtlasError("INVALID_INPUT", "Choose unique supported price analytics metrics.")
    if len(prices) > 4000:
        raise AtlasError("INVALID_INPUT", "Price analytics accepts at most 4000 observations.")
    if any(a["date"] >= b["date"] for a, b in zip(prices, prices[1:], strict=False)):
        raise AtlasError("SOURCE_UNAVAILABLE", "Price analytics requires unique ascending dates.")
    if len({(row["unit"], row["adjusted_close_basis"]) for row in prices}) > 1:
        raise AtlasError("SOURCE_UNAVAILABLE", "Price units or adjustment bases changed.")
    with localcontext(Context(prec=CALCULATION_PRECISION, rounding=ROUND_HALF_EVEN)):
        return _indicator_records(prices, metrics)


def _indicator_records(
    prices: list[dict[str, Any]], metrics: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inputs, records = [], []
    segment: list[Decimal] = []
    segment_start = 0
    complete_history = True
    maximum: Decimal | None = None
    ema12 = ema26 = gain = loss = signal = None
    macds: list[Decimal] = []
    for index, row in enumerate(prices):
        raw = row["adjusted_close"]
        value = Decimal(raw) if raw is not None else None
        valid = (
            value is not None
            and value.is_finite()
            and value > 0
            and row.get("status") != "ambiguous"
        )
        inputs.append(
            {
                "index": index,
                "date": row["date"],
                "value": raw,
                "unit": row["unit"],
                "price_basis": row["adjusted_close_basis"],
                "status": "reported" if valid else "unavailable",
                "reason": None
                if valid
                else "A finite positive adjusted close without ambiguous source data is required.",
                "source_status": row.get("status"),
                "quality_flags": row.get("quality_flags", []),
                "ohlc_consistent": row.get("ohlc_consistent"),
                "source": row["field_sources"].get("adjusted_close"),
                "date_source": row["field_sources"]["date"],
            }
        )
        values: dict[str, Decimal | None] = dict.fromkeys(PRICE_METRICS)
        if not valid:
            segment = []
            segment_start = index + 1
            complete_history = False
            ema12 = ema26 = gain = loss = signal = None
            macds = []
        else:
            assert value is not None
            segment.append(value)
            if len(segment) >= 2:
                values["return_1d"] = value / segment[-2] - 1
            if complete_history:
                maximum = value if maximum is None else max(value, maximum)
                values["cumulative_return"] = value / segment[0] - 1
                values["drawdown"] = value / maximum - 1
            for length in (20, 50):
                if len(segment) >= length:
                    values[f"sma_{length}"] = _mean(segment[-length:])
            ema12, ema26 = _ema(segment, ema12, 12), _ema(segment, ema26, 26)
            values.update(ema_12=ema12, ema_26=ema26)
            if len(segment) >= 15:
                if gain is None or loss is None:
                    differences = [
                        b - a for a, b in zip(segment[-15:-1], segment[-14:], strict=True)
                    ]
                    gain = _mean([max(difference, Decimal(0)) for difference in differences])
                    loss = _mean([max(-difference, Decimal(0)) for difference in differences])
                else:
                    difference = segment[-1] - segment[-2]
                    gain = (13 * gain + max(difference, Decimal(0))) / 14
                    loss = (13 * loss + max(-difference, Decimal(0))) / 14
                values["rsi_14"] = 100 * gain / (gain + loss) if gain + loss else Decimal(50)
            if len(segment) >= 21:
                returns = [b / a - 1 for a, b in zip(segment[-21:-1], segment[-20:], strict=True)]
                values["volatility_20"] = _deviation(returns, sample=True)
            if ema12 is not None and ema26 is not None:
                macd = ema12 - ema26
                macds.append(macd)
                signal = _ema(macds, signal, 9)
                values.update(
                    macd=macd,
                    macd_signal=signal,
                    macd_histogram=macd - signal if signal is not None else None,
                )
            if len(segment) >= 20:
                average = _mean(segment[-20:])
                deviation = _deviation(segment[-20:])
                values["bollinger_upper_20"] = average + 2 * deviation
                values["bollinger_lower_20"] = average - 2 * deviation
        for metric in metrics:
            spec = PRICE_METRICS[metric]
            computed = values[metric]
            first = (
                max(0, index - spec.window_observations + 1)
                if spec.window_observations is not None
                else segment_start
                if spec.recursive and valid
                else 0
            )
            reason = None
            if computed is None:
                if not valid:
                    reason = (
                        "The current adjusted close is missing, nonpositive or source-ambiguous."
                    )
                elif metric in {"cumulative_return", "drawdown"} and not complete_history:
                    reason = (
                        "A selected earlier adjusted close is missing, nonpositive "
                        "or source-ambiguous."
                    )
                else:
                    reason = (
                        f"At least {spec.minimum_observations} consecutive positive adjusted "
                        "closes are required after the window start or last missing value."
                    )
            records.append(
                {
                    "identifier": row["identifier"],
                    "date": row["date"],
                    "date_basis": row["date_basis"],
                    "metric": metric,
                    "value": _formatted(computed),
                    "unit": row["unit"] if spec.unit == "price" else spec.unit,
                    "status": "derived" if computed is not None else "unavailable",
                    "derived": True,
                    "reason": reason,
                    "formula": spec.formula,
                    "price_basis": row["adjusted_close_basis"],
                    "input_range": {"table": "price_inputs", "first": first, "last": index},
                    "rounding": "half_even_18_decimal_places",
                    "decimal_precision": CALCULATION_PRECISION,
                    "annualization_factor": None,
                }
            )
    return inputs, records


def price_analytics(transport: Transport, query: "ResearchQuery") -> dict[str, Any]:
    # Local imports avoid a query-schema/catalog dependency cycle.
    from atlas_terminal.equity_sources import yahoo_url
    from atlas_terminal.research_equities import chart_result, price_rows
    from atlas_terminal.research_financials import reference

    assert query.identifier is not None
    metrics = query.metrics or DEFAULT_PRICE_METRICS
    snapshot = transport.get(yahoo_url(query.identifier, query.start_date, query.end_date), 900)
    result, instrument = chart_result(snapshot, query.identifier)
    prices, excluded = price_rows(result, snapshot, query)
    if not prices:
        raise AtlasError("INCOMPLETE_COVERAGE", "No completed daily prices in the selected window.")
    inputs, records = indicator_records(prices, metrics)
    # A stable sort retains caller metric order within each descending date.
    records.sort(key=lambda row: row["date"], reverse=True)
    selected = records[: query.limit]
    unavailable = sum(row["value"] is None for row in records)
    return {
        "instrument": instrument,
        "records": selected,
        "price_inputs": inputs,
        "sources": [reference(snapshot, "")],
        "calculation": {
            "metrics": {metric: price_analytics_catalog()[metric] for metric in metrics},
            "price_input": "adjusted_close",
            "input_range": "inclusive zero-based indices in price_inputs, ordered oldest first",
            "input_count": len(inputs),
            "hidden_warmup_requests": False,
            "annualization_factor": None,
            "decimal_precision": CALCULATION_PRECISION,
            "rounding": "half_even_18_decimal_places",
            "missing_price_policy": "reset_recursive_and_rolling; invalidate_whole_window_metrics",
        },
        "coverage": {
            **window_metadata(prices, query.start_date, query.end_date),
            "date_selection": "inclusive_exchange_local_date",
            "observed_price_records": len(prices),
            "observed_records": len(records),
            "returned_records": len(selected),
            "truncated": len(records) > query.limit,
            "unavailable_records": unavailable,
            "partial": unavailable > 0,
            "upstream_window_limited": True,
            "upstream_window": "explicit selected observation dates"
            if query.start_date
            else "Yahoo latest month",
            "excluded_unconfirmed_session_rows": excluded,
            "exchange_calendar_verified": False,
            "point_in_time": False,
        },
        "limitations": list(PRICE_ANALYTICS_LIMITATIONS),
    }
