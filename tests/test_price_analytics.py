"""Price indicator calculations and source receipts; all observations are fictional."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, decode_json
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_price_analytics import (
    PRICE_METRICS,
    indicator_records,
    price_analytics,
    price_analytics_catalog,
)
from atlas_terminal.util import digest

BODY = (
    files("atlas_terminal")
    .joinpath("resources", "research", "yahoo_chart_7203.T.json")
    .read_bytes()
)


def price_rows(values):
    return [
        {
            "date": (date(2020, 1, 1) + timedelta(days=index)).isoformat(),
            "identifier": "7203.T",
            "date_basis": "exchange_local_session_date",
            "unit": "JPY_per_share",
            "adjusted_close": None if value is None else str(value),
            "adjusted_close_basis": "yahoo_split_and_dividend_adjusted",
            "field_sources": {
                "adjusted_close": {
                    "raw_source_url": "https://fixtures.atlas.invalid/prices.json",
                    "raw_sha256": "a" * 64,
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "json_pointer": f"/prices/{index}",
                },
                "date": {
                    "raw_source_url": "https://fixtures.atlas.invalid/prices.json",
                    "raw_sha256": "a" * 64,
                    "retrieved_at": "2026-10-29T00:00:00Z",
                    "json_pointer": f"/dates/{index}",
                },
            },
        }
        for index, value in enumerate(values)
    ]


def by_metric(records, metric):
    return [row for row in records if row["metric"] == metric]


def value(records, metric, index=-1):
    return by_metric(records, metric)[index]["value"]


def result_for(values, metrics=None):
    return indicator_records(price_rows(values), metrics or list(PRICE_METRICS))[1]


def test_returns_cumulative_and_drawdown_match_exact_independent_fraction_inputs():
    records = result_for([100, 120, 90, 135], ["return_1d", "cumulative_return", "drawdown"])
    assert [r["value"] for r in by_metric(records, "return_1d")] == [
        None,
        "0.200000000000000000",
        "-0.250000000000000000",
        "0.500000000000000000",
    ]
    assert value(records, "cumulative_return") == "0.350000000000000000"
    assert value(records, "drawdown", 2) == "-0.250000000000000000"
    assert value(records, "drawdown") == "0.000000000000000000"
    assert value(records, "drawdown", 0) == "0.000000000000000000"


def test_fixed_rolling_and_seeded_recursive_indicators_on_a_linear_price_path():
    records = result_for(range(1, 71))
    assert value(records, "sma_20", 19) == "10.500000000000000000"
    assert value(records, "sma_50", 49) == "25.500000000000000000"
    assert value(records, "ema_12", 11) == "6.500000000000000000"
    assert value(records, "ema_12", 69) == "64.500000000000000000"
    assert value(records, "ema_26", 25) == "13.500000000000000000"
    assert value(records, "macd", 25) == "7.000000000000000000"
    assert value(records, "macd_signal", 33) == "7.000000000000000000"
    assert value(records, "macd_histogram", 69) == "0.000000000000000000"
    assert value(records, "rsi_14", 14) == "100.000000000000000000"


@pytest.mark.parametrize("metric", list(PRICE_METRICS))
def test_warmup_is_explicit_and_does_not_use_future_prices(metric):
    required = PRICE_METRICS[metric].minimum_observations
    records = result_for(range(1, 72), [metric])
    assert all(row["value"] is None for row in records[: required - 1])
    assert all(row["status"] == "unavailable" and row["reason"] for row in records[: required - 1])
    assert records[required - 1]["value"] is not None
    prefix = result_for(range(1, required + 1), [metric])
    assert prefix == records[:required]
    for index, row in enumerate(records):
        assert 0 <= row["input_range"]["first"] <= row["input_range"]["last"] == index


def test_wilder_rsi_matches_published_convention_and_successive_recursive_update():
    prices = [
        "44.34",
        "44.09",
        "44.15",
        "43.61",
        "44.33",
        "44.83",
        "45.10",
        "45.42",
        "45.84",
        "46.08",
        "45.89",
        "46.03",
        "45.61",
        "46.28",
        "46.28",
        "46.00",
    ]
    records = result_for(prices, ["rsi_14"])
    # Sum of the 14 gains is 3.34, losses 1.40. The next price loses 0.28.
    exact_seed = Fraction(334, 474) * 100
    next_rsi = (
        (Fraction(334, 1400) * 13)
        / (Fraction(334, 1400) * 13 + Fraction(140, 1400) * 13 + Fraction(28, 100))
        * 100
    )
    with localcontext() as context:
        context.prec = 80
        for row, expected in ((records[14], exact_seed), (records[15], next_rsi)):
            difference = abs(
                Decimal(row["value"]) - Decimal(expected.numerator) / expected.denominator
            )
            assert difference < Decimal("5e-19")


@pytest.mark.parametrize("prices,expected", [([100] * 20, "50"), (range(30, 10, -1), "0")])
def test_flat_and_only_loss_rsi_have_documented_finite_values(prices, expected):
    records = result_for(prices, ["rsi_14"])
    assert Decimal(value(records, "rsi_14")) == Decimal(expected)


def test_bollinger_uses_population_variance_and_volatility_sample_returns_without_annualizing():
    records = result_for(range(1, 22), ["bollinger_upper_20", "bollinger_lower_20"])
    with localcontext() as context:
        context.prec = 80
        # Population variance of the integers 2..21 is (20^2 - 1)/12 = 33.25.
        sigma = Decimal("33.25").sqrt()
        assert abs(
            Decimal(value(records, "bollinger_upper_20")) - (Decimal("11.5") + 2 * sigma)
        ) < Decimal("5e-19")
        assert abs(
            Decimal(value(records, "bollinger_lower_20")) - (Decimal("11.5") - 2 * sigma)
        ) < Decimal("5e-19")
    alternating = [100, 200] * 10 + [100]
    records = result_for(alternating, ["volatility_20"])
    with localcontext() as context:
        context.prec = 80
        # Ten returns +1 and ten -0.5: mean .25 and sum of squared deviations 11.25.
        expected = (Decimal("11.25") / 19).sqrt()
        assert abs(Decimal(value(records, "volatility_20")) - expected) < Decimal("5e-19")
    assert records[-1]["annualization_factor"] is None
    assert records[-1]["unit"] == "ratio_per_observation"


@pytest.mark.parametrize("missing", [None, "0", "-4"])
def test_missing_nonpositive_prices_reset_trailing_indicators_without_bridging(missing):
    records = result_for([100] * 40 + [missing] + [200] * 55)
    for metric in PRICE_METRICS:
        assert value(records, metric, 40) is None
    assert value(records, "return_1d", 41) is None
    assert value(records, "return_1d", 42) == "0.000000000000000000"
    assert value(records, "sma_20", 59) is None
    assert value(records, "sma_20", 60) == "200.000000000000000000"
    assert value(records, "ema_12", 51) is None
    assert value(records, "ema_12", 52) == "200.000000000000000000"
    assert by_metric(records, "ema_12")[52]["input_range"] == {
        "table": "price_inputs",
        "first": 41,
        "last": 52,
    }
    assert value(records, "macd_signal", 73) is None
    assert value(records, "macd_signal", 74) == "0.000000000000000000"
    assert all(row["value"] is None for row in by_metric(records, "cumulative_return")[40:])
    assert all(row["value"] is None for row in by_metric(records, "drawdown")[40:])


def test_rounding_is_half_even_and_independent_of_callers_decimal_context():
    rows = price_rows(["1", "1.0000000000000000005", "1.0000000000000000015"])
    with localcontext() as context:
        context.prec = 6
        context.rounding = "ROUND_UP"
        inputs, records = indicator_records(rows, ["cumulative_return"])
    assert records[1]["value"] == "0.000000000000000000"
    assert records[2]["value"] == "0.000000000000000002"
    assert inputs[1]["value"] == "1.0000000000000000005"
    assert all("-0.000000000000000000" != row["value"] for row in records)


def test_source_inputs_keep_nulls_and_exact_locators_in_one_shared_table():
    rows = price_rows([100, None, 110])
    rows[1]["field_sources"].pop("adjusted_close")
    inputs, records = indicator_records(rows, ["return_1d", "drawdown"])
    assert inputs[1]["value"] is None and inputs[1]["source"] is None
    assert inputs[0]["source"] == rows[0]["field_sources"]["adjusted_close"]
    assert inputs[0]["date_source"] == rows[0]["field_sources"]["date"]
    assert inputs[0]["price_basis"] == "yahoo_split_and_dividend_adjusted"
    assert records[-1]["input_range"] == {"table": "price_inputs", "first": 0, "last": 2}


@pytest.mark.parametrize("fault", ["duplicate", "order", "units", "basis", "oversize"])
def test_invalid_input_order_units_and_bounds_fail_closed(fault):
    rows = price_rows([10, 11, 12])
    if fault == "duplicate":
        rows[1]["date"] = rows[0]["date"]
    elif fault == "order":
        rows.reverse()
    elif fault == "units":
        rows[1]["unit"] = "USD_per_share"
    elif fault == "basis":
        rows[1]["adjusted_close_basis"] = "raw"
    else:
        rows = price_rows([1] * 4001)
    with pytest.raises(AtlasError):
        indicator_records(rows, ["return_1d"])


@pytest.mark.parametrize("metrics", [[], ["unknown"], ["return_1d", "return_1d"]])
def test_unknown_or_duplicate_metrics_fail_before_calculation(metrics):
    with pytest.raises(AtlasError) as error:
        indicator_records(price_rows([1, 2]), metrics)
    assert error.value.code == "INVALID_INPUT"


def test_source_reader_limit_does_not_truncate_warmup_inputs_and_no_extra_requests():
    class Source:
        def __init__(self):
            self.calls = []

        def get(self, url, ttl):
            self.calls.append((url, ttl))
            document = json.loads(BODY)
            chart = document["chart"]["result"][0]
            prices = list(range(1, 71))
            chart["timestamp"] = [
                int(datetime(2020, 1, 1, tzinfo=UTC).timestamp()) + i * 86400 for i in range(70)
            ]
            chart["indicators"] = {
                "quote": [
                    {
                        **{field: prices for field in ("open", "high", "low", "close")},
                        "volume": [None] * 70,
                    }
                ],
                "adjclose": [{"adjclose": prices}],
            }
            return Snapshot(json.dumps(document).encode(), url, "2026-10-29T00:00:00Z")

    source = Source()
    query = ResearchQuery(
        dataset="price_analytics",
        identifier="7203.T",
        metrics=["sma_50"],
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit=1,
    )
    result = price_analytics(source, query)
    assert len(source.calls) == 1
    assert len(result["price_inputs"]) == 70
    assert result["records"][0]["value"] == "45.500000000000000000"
    assert result["records"][0]["input_range"] == {"table": "price_inputs", "first": 20, "last": 69}
    assert result["calculation"]["hidden_warmup_requests"] is False
    assert result["coverage"]["truncated"]
    # Missing volume cannot invalidate an available adjusted-price calculation.
    assert all(row["status"] == "reported" for row in result["price_inputs"])


def test_selected_date_boundaries_and_unconfirmed_session_are_not_used_as_warmup():
    query = ResearchQuery(
        dataset="price_analytics",
        identifier="7203.T",
        metrics=["return_1d"],
        start_date="2026-10-23",
        end_date="2026-10-27",
    )
    result = price_analytics(PublicFixtures(), query)
    assert [row["date"] for row in result["price_inputs"]] == ["2026-10-23", "2026-10-26"]
    assert result["records"][1]["value"] is None
    assert result["coverage"]["excluded_unconfirmed_session_rows"] == 1
    assert result["records"][0]["value"] is not None
    assert not result["coverage"]["point_in_time"]


def test_catalog_exposes_all_formulas_and_annualization_choice():
    catalog = price_analytics_catalog()
    assert len(catalog) == 14
    assert all(spec["formula"] and spec["minimum_observations"] > 0 for spec in catalog.values())
    assert all(spec["annualization_factor"] is None for spec in catalog.values())


def test_schema_metric_contract_matches_runtime_and_blocks_unknown_metrics():
    query = {"dataset": "price_analytics", "identifier": "7203.T", "metrics": ["rsi_14"]}
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    validator.validate(query)
    for metric in ["rsi", "close", "forward_return"]:
        with pytest.raises(ValidationError):
            validator.validate({**query, "metrics": [metric]})
        with pytest.raises(AtlasError):
            ResearchQuery.model_validate({**query, "metrics": [metric]})


def test_shared_service_keeps_source_inputs_after_compact_field_projection():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(
            {
                "dataset": "price_analytics",
                "identifier": "7203.T",
                "metrics": ["return_1d"],
                "response": {"view": "compact", "fields": ["value"]},
            }
        )
        assert result["is_synthetic"]
        assert result["records"][0]["input_range"]["table"] == "price_inputs"
        assert result["records"][0]["formula"] == "P[t] / P[t-1] - 1"
        inputs = result["price_inputs"]
        source = atlas.get_evidence(inputs[0]["source"]["evidence_id"])["evidence"]
        assert source["raw_sha256"] == digest(BODY)
        document = decode_json(BODY)
        for part in source["json_pointer"].split("/")[1:]:
            document = document[int(part)] if isinstance(document, list) else document[part]
        assert Decimal(inputs[0]["value"]) == document


def test_public_price_analytics_requires_operator_yahoo_enablement(monkeypatch):
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research({"dataset": "price_analytics", "identifier": "7203.T"})
    assert error.value.code == "CONFIG_REQUIRED"


def test_ambiguous_ohlc_source_rows_are_preserved_but_never_used_as_clean_prices():
    rows = price_rows([100, 110, 120, 130])
    rows[1].update(
        status="ambiguous", ohlc_consistent=False, quality_flags=["ohlc_bounds_conflict"]
    )
    inputs, records = indicator_records(rows, ["return_1d", "cumulative_return"])
    assert inputs[1]["value"] == "110"
    assert inputs[1]["source_status"] == "ambiguous"
    assert inputs[1]["quality_flags"] == ["ohlc_bounds_conflict"]
    assert inputs[1]["ohlc_consistent"] is False
    assert value(records, "return_1d", 1) is None
    assert value(records, "return_1d", 2) is None
    assert value(records, "return_1d", 3) is not None
    assert value(records, "cumulative_return", 3) is None
