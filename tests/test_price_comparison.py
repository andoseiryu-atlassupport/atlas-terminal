"""Exact observed-date comparisons with fictional source data only."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.equity_sources import yahoo_identity
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_price_comparison import (
    comparison_records,
    price_comparison,
    validate_price_comparison,
)
from atlas_terminal.util import canonical

BODY = (
    files("atlas_terminal")
    .joinpath("resources", "research", "yahoo_chart_7203.T.json")
    .read_bytes()
)


def inputs(values, *, symbol="7203.T"):
    return [
        {
            "index": index,
            "date": (date(2020, 1, 1) + timedelta(days=index)).isoformat(),
            "value": None if value is None else str(value),
            "unit": "JPY_per_share",
            "price_basis": "yahoo_split_and_dividend_adjusted",
            "source": {
                "raw_source_url": f"https://fixtures.atlas.invalid/prices/{symbol}.json",
                "raw_sha256": "a" * 64,
                "retrieved_at": "2026-10-29T00:00:00Z",
                "json_pointer": f"/prices/{index}",
            },
            "date_source": {
                "raw_source_url": f"https://fixtures.atlas.invalid/prices/{symbol}.json",
                "raw_sha256": "a" * 64,
                "retrieved_at": "2026-10-29T00:00:00Z",
                "json_pointer": f"/dates/{index}",
            },
        }
        for index, value in enumerate(values)
    ]


def metric(record, name):
    return next(row for row in record["records"] if row["metric"] == name)


def query(**extra):
    return ResearchQuery(
        dataset="price_comparison",
        **{
            "identifiers": ["7203.T", "6758.T"],
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            **extra,
        },
    )


class Prices:
    def __init__(self, values=None):
        self.values = values or {"7203.T": [100, 200, 100], "6758.T": [50, 100, 50]}
        self.calls = []

    def close(self):
        pass

    def get(self, url, ttl):
        self.calls.append((url, ttl))
        symbol, _, _ = yahoo_identity(url)
        document = json.loads(BODY)
        chart = document["chart"]["result"][0]
        chart["meta"]["symbol"] = symbol
        values = self.values[symbol]
        chart["timestamp"] = [
            int(datetime(2020, 1, 1, tzinfo=UTC).timestamp()) + index * 86400
            for index in range(len(values))
        ]
        chart["indicators"] = {
            "quote": [
                {
                    **{field: values for field in ("open", "high", "low", "close")},
                    "volume": [10] * len(values),
                }
            ],
            "adjclose": [{"adjclose": values}],
        }
        return Snapshot(json.dumps(document).encode(), url, "2026-10-29T00:00:00Z")


def test_asset_statistics_pair_diagonal_and_covariance_have_independent_values():
    records = comparison_records(
        {"7203.T": inputs([100, 200, 100]), "6758.T": inputs([50, 100, 50])}, "JPY"
    )
    first, second, diagonal, pair, last_diagonal = records
    assert metric(first, "cumulative_return")["value"] == "0.000000000000000000"
    assert metric(first, "maximum_drawdown")["value"] == "-0.500000000000000000"
    assert metric(first, "mean_return")["value"] == "0.250000000000000000"
    assert metric(second, "cumulative_return")["value"] == "0.000000000000000000"
    assert metric(pair, "covariance")["value"] == "1.125000000000000000"
    for row in [diagonal, pair, last_diagonal]:
        assert metric(row, "correlation")["value"] == "1.000000000000000000"
    with localcontext() as context:
        context.prec = 80
        assert abs(
            Decimal(metric(first, "volatility")["value"]) - Decimal("1.125").sqrt()
        ) < Decimal("5e-19")
    assert metric(pair, "correlation")["input_range"] == [
        {"table": "price_inputs", "identifier": "7203.T", "first": 0, "last": 2},
        {"table": "price_inputs", "identifier": "6758.T", "first": 0, "last": 2},
    ]


def test_negative_correlation_and_covariance_keep_signs_and_no_annualization():
    records = comparison_records({"a": inputs([100, 200, 100]), "b": inputs([100, 50, 100])}, "JPY")
    pair = records[3]
    assert metric(pair, "correlation")["value"] == "-1.000000000000000000"
    assert metric(pair, "covariance")["value"] == "-1.125000000000000000"
    assert all(row["annualization_factor"] is None for group in records for row in group["records"])


def test_zero_variance_correlation_is_unavailable_while_covariance_is_zero():
    records = comparison_records(
        {"a": inputs([100, 100, 100]), "b": inputs([100, 200, 100])}, "JPY"
    )
    for pair in records[2:4]:
        assert metric(pair, "covariance")["value"] == "0.000000000000000000"
        assert metric(pair, "covariance")["status"] == "derived"
        assert metric(pair, "correlation")["value"] is None
        assert metric(pair, "correlation")["status"] == "unavailable"
        assert "zero-variance" in metric(pair, "correlation")["reason"]


@pytest.mark.parametrize(
    "fault",
    [
        "null",
        "zero",
        "negative",
        "ambiguous",
        "date",
        "order",
        "duplicate",
        "length",
        "unit",
        "basis",
        "small",
        "long",
        "total_bound",
    ],
)
def test_gaps_conflicts_and_bounds_fail_without_dropping_or_repairing(fault):
    selected = {"a": inputs([100, 110, 120]), "b": inputs([100, 110, 120])}
    if fault in {"null", "zero", "negative"}:
        selected["b"][1]["value"] = {"null": None, "zero": "0", "negative": "-1"}[fault]
    elif fault == "ambiguous":
        selected["b"][1]["source_status"] = "ambiguous"
    elif fault == "date":
        selected["b"][1]["date"] = "2020-01-04"
    elif fault == "order":
        selected["a"].reverse()
    elif fault == "duplicate":
        selected["a"][1]["date"] = selected["a"][0]["date"]
    elif fault == "length":
        selected["b"].pop()
    elif fault == "unit":
        selected["b"][0]["unit"] = "USD_per_share"
    elif fault == "basis":
        selected["b"][0]["price_basis"] = "raw"
    elif fault == "small":
        selected = {"a": inputs([1, 2]), "b": inputs([1, 2])}
    elif fault == "long":
        selected = {"a": inputs(range(1, 502)), "b": inputs(range(1, 502))}
    else:
        selected = {str(index): inputs(range(1, 402)) for index in range(10)}
    with pytest.raises(AtlasError):
        comparison_records(selected, "JPY")


@pytest.mark.parametrize(
    "symbols",
    [
        ["AAPL", "7203.T"],
        ["AAPL", "^GSPC"],
        ["BTC-USD", "ETH-USD"],
        ["EURUSD=X", "USDJPY=X"],
        ["CL=F", "GC=F"],
    ],
)
def test_incomparable_markets_and_nonsecurities_reject_before_any_reads(symbols):
    source = Prices()
    with pytest.raises(AtlasError) as error:
        price_comparison(source, query(identifiers=symbols))
    assert error.value.code == "INVALID_INPUT"
    assert source.calls == []


def test_stock_and_etf_comparison_requires_exact_full_dates_and_retains_each_source():
    source = Prices()
    result = price_comparison(source, query())
    assert len(source.calls) == 2
    assert len(result["sources"]) == 2
    assert set(result["price_inputs"]) == {"7203.T", "6758.T"}
    assert result["coverage"]["observed_price_records_per_asset"] == 3
    assert (
        result["calculation"]["alignment"] == "identical_observed_date_vectors_without_intersection"
    )
    assert not result["coverage"]["point_in_time"]
    assert not result["coverage"]["partial"]
    assert result["records"][0]["identifier"] == "7203.T"
    for identifier in result["price_inputs"]:
        assert identifier in result["price_inputs"][identifier][0]["source"]["raw_source_url"]


def test_limit_only_truncates_results_and_keeps_every_comparison_input():
    result = price_comparison(Prices(), query(limit=1))
    assert len(result["records"]) == 1
    assert result["coverage"]["truncated"]
    assert len(result["price_inputs"]) == 2
    assert all(len(rows) == 3 for rows in result["price_inputs"].values())


def test_calculations_ignore_external_decimal_context_and_future_observations():
    selected = {"a": inputs([100, 200, 100]), "b": inputs([200, 400, 200])}
    expected = comparison_records(selected, "JPY")
    with localcontext() as context:
        context.prec = 5
        context.rounding = "ROUND_UP"
        assert comparison_records(selected, "JPY") == expected


def test_complete_bound_keeps_source_evidence_under_research_response_budget():
    selected = {str(index): inputs(range(1, 301), symbol=f"{index}000.T") for index in range(10)}
    records = comparison_records(selected, "JPY")
    assert len(records) == 65
    assert (
        len(canonical({"price_inputs": selected, "records": records})) < 4 * 1024 * 1024 - 200_000
    )


def test_shared_compact_service_preserves_pair_identity_statistics_and_evidence():
    with Atlas("fixture") as atlas:
        atlas._research.public = Prices()
        result = atlas.get_research(
            {
                **query().model_dump(exclude_defaults=True),
                "response": {"view": "compact", "fields": ["value"]},
            }
        )
        pair = result["records"][3]
        assert pair["identifiers"] == ["7203.T", "6758.T"]
        correlation = metric(pair, "correlation")
        assert correlation["identifiers"] == ["7203.T", "6758.T"]
        assert correlation["return_count"] == 2
        assert correlation["input_range"][0]["identifier"] == "7203.T"
        evidence = result["price_inputs"]["7203.T"][0]["source"]
        assert atlas.get_evidence(evidence["evidence_id"])["evidence"]["json_pointer"].endswith(
            "/adjclose/0/adjclose/0"
        )


def test_runtime_and_schema_require_explicit_dates_and_multiple_distinct_symbols():
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    valid = query().model_dump(exclude_defaults=True)
    validator.validate(valid)
    for bad in [
        {**valid, "identifiers": ["7203.T"]},
        {**valid, "identifiers": ["7203.T", "7203.T"]},
        {key: value for key, value in valid.items() if key != "start_date"},
    ]:
        with pytest.raises((ValidationError, ValueError)):
            validator.validate(bad)
        with pytest.raises((AtlasError, ValueError)):
            validate_price_comparison(ResearchQuery.model_validate(bad))


def test_public_comparison_requires_yahoo_operator_enablement(monkeypatch):
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research(query().model_dump(exclude_defaults=True))
    assert error.value.code == "CONFIG_REQUIRED"
