"""Independent reference calculations and strict source-series selection contracts."""

import json
from copy import deepcopy
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from typer.testing import CliRunner

from atlas_terminal.errors import AtlasError
from atlas_terminal.research_statistics import analyze_research_series
from atlas_terminal.util import canonical, digest


def research_result(values=None):
    if values is None:
        values = [((i * 17) % 23) - 11 + (i % 5) * 2 for i in range(80)]
    source = {
        "raw_source_url": "https://fixtures.atlas.invalid/series.json",
        "raw_sha256": "a" * 64,
        "retrieved_at": "2026-10-30T00:00:00Z",
        "json_pointer": "/observations",
    }
    return {
        "schema_version": "0.2.0",
        "dataset": "economics",
        "query": {"dataset": "economics", "series": "cpi"},
        "source_id": "bls",
        "source_terms_url": "https://fixtures.atlas.invalid/terms",
        "upstream_data_cost": "0",
        "is_synthetic": True,
        "source_text_is_untrusted": True,
        "retrieved_at": "2026-10-30T00:00:00Z",
        "records": [
            {
                "date": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                "value": str(value) if value is not None else None,
                "unit": "index",
                "series": "fictional_sequence",
                "status": "reported" if value is not None else "unavailable",
                "source": source.copy(),
            }
            for i, value in enumerate(values)
        ],
        "sources": [source],
        "coverage": {"truncated": False, "point_in_time": False},
        "limitations": ["Fictional data. No historical publication-time availability is asserted."],
    }


def response_metadata(result):
    return {
        "view": "full",
        "fields": [],
        "snapshot_id": "b" * 64,
        "result_sha256": "c" * 64,
        "expires_at": "2026-10-31T00:00:00Z",
        "total_records": len(result["records"]),
        "returned_records": len(result["records"]),
        "offset": 0,
        "page_size": None,
        "next_cursor": None,
        "evidence_count": 1,
    }


def test_hand_calculated_mean_sample_variance_acf_and_ljung_box():
    report = analyze_research_series(research_result([-1, 0, 1]), max_lag=2)
    assert report["descriptive"] == {
        "observations": 3,
        "minimum": "-1",
        "maximum": "1",
        "mean": "0",
        "median": "0",
        "sample_variance": "1",
        "sample_standard_deviation": "1",
        "variance_denominator": 2,
        "annualized": False,
    }
    assert report["autocorrelation"][0]["autocorrelation"] == "0"
    assert report["autocorrelation"][1]["autocorrelation"] == "-0.5"
    assert report["autocorrelation"][1]["ljung_box_q"] == "3.75"
    assert report["adf"]["status"] == report["kpss"]["status"] == "unavailable"


# Independent statsmodels 0.14.4/NumPy 2.2.6/SciPy 1.15.3 values for the integer
# vector above: adfuller(v, regression='c'/'ct', maxlag=lag, autolag=None) and
# kpss(v, regression='c'/'ct', nlags=lag). No reference-library runtime dependency.
@pytest.mark.parametrize(
    "deterministic,lags,adf,kpss,critical_five",
    [
        ("constant", 0, "-10.13958751926938", "0.021961164858037403", "-2.898885703483903"),
        ("constant", 1, "-11.541216426649624", "0.02485399085628008", "-2.8993754262546574"),
        ("constant", 3, "-4.674700731173979", "0.07287886653414202", "-2.9003945086747343"),
        ("trend", 0, "-10.067475274397133", "0.016625903934352967", "-3.467604557671908"),
        ("trend", 1, "-11.464118944442504", "0.01880826785314343", "-3.468358000977764"),
        ("trend", 3, "-4.674747995733415", "0.05490220769933386", "-3.4699261887665838"),
    ],
)
def test_adf_kpss_match_independent_reference(deterministic, lags, adf, kpss, critical_five):
    report = analyze_research_series(
        research_result(), deterministic=deterministic, adf_lags=lags, kpss_lags=lags
    )
    assert abs(Decimal(report["adf"]["statistic"]) - Decimal(adf)) < Decimal("1e-11")
    assert abs(Decimal(report["kpss"]["statistic"]) - Decimal(kpss)) < Decimal("1e-13")
    assert abs(Decimal(report["adf"]["critical_values"]["5%"]) - Decimal(critical_five)) < Decimal(
        "1e-14"
    )
    assert report["adf"]["regression_observations"] == 79 - lags
    assert report["adf"]["reject_null"]["5%"] is True
    assert report["kpss"]["reject_null"]["5%"] is False
    assert report["adf"]["p_value"] is report["kpss"]["p_value"] is None


def test_acf_and_ljung_box_match_independent_reference():
    report = analyze_research_series(research_result(), max_lag=3)
    expected = (
        ("-0.11639281654888531", "1.1249393367321139"),
        ("-0.48543753034165776", "20.94367457864498"),
        ("-0.07727068247291413", "21.452352174827894"),
    )
    for row, (correlation, q) in zip(report["autocorrelation"], expected, strict=True):
        assert abs(Decimal(row["autocorrelation"]) - Decimal(correlation)) < Decimal("1e-14")
        assert abs(Decimal(row["ljung_box_q"]) - Decimal(q)) < Decimal("1e-12")
        assert row["p_value"] is None


def test_original_input_hash_evidence_and_indices_survive_sorting_without_mutation():
    result = research_result()
    result["records"].reverse()
    before = deepcopy(result)
    report = analyze_research_series(result, frequency="daily")
    assert result == before and report["input"] == result and report["input"] is not result
    assert report["input_sha256"] == digest(canonical(result))
    assert report["sample"]["source_record_indices"] == list(reversed(range(80)))
    assert report["sample"]["start_date"] == "2025-01-01"
    assert report["sample"]["end_date"] == "2025-03-21"
    assert report["input"]["sources"][0]["raw_sha256"] == "a" * 64


def test_difference_and_return_transform_use_adjacent_original_values():
    result = research_result([100, 110, 99, 99])
    difference = analyze_research_series(result, transform="difference", max_lag=2)
    returns = analyze_research_series(result, transform="return", max_lag=2)
    assert difference["sample"]["transformed_values"] == ["10", "-11", "0"]
    assert difference["sample"]["unit"] == "index"
    assert returns["sample"]["transformed_values"] == ["0.1", "-0.1", "0"]
    assert returns["sample"]["unit"] == "ratio_per_observation"
    assert returns["sample"]["original_observations"] == 4
    assert returns["sample"]["observations"] == 3
    assert returns["sample"]["start_date"] == "2025-01-02"
    assert returns["sample"]["source_record_indices"] == [0, 1, 2, 3]
    assert returns["input"]["records"][0]["value"] == "100"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_simple_returns_reject_nonpositive_source_values(value):
    result = research_result()
    result["records"][1]["value"] = value
    with pytest.raises(AtlasError, match="strictly positive"):
        analyze_research_series(result, transform="return")


def test_flat_and_deterministic_trend_are_unavailable():
    flat = analyze_research_series(research_result([5] * 80))
    assert flat["adf"]["reason"] == flat["kpss"]["reason"] == "constant_series"
    assert flat["descriptive"]["sample_variance"] == "0"
    assert flat["autocorrelation"][0]["autocorrelation"] is None
    trend = analyze_research_series(research_result(range(80)), deterministic="trend")
    assert trend["adf"]["status"] == trend["kpss"]["status"] == "unavailable"
    assert "reject_null" not in trend["adf"] and "reject_null" not in trend["kpss"]


def test_nearly_deterministic_design_is_unavailable_without_regularization():
    with localcontext() as context:
        context.prec = 100
        values = [Decimal(i) + Decimal((-1) ** i) * Decimal("1e-30") for i in range(80)]
    report = analyze_research_series(research_result(values), deterministic="trend")
    assert report["adf"]["reason"] == "rank_deficient_or_ill_conditioned_design"
    assert report["kpss"]["status"] == "unavailable"


def test_translation_and_scale_invariance_and_ambient_context_independence():
    original = research_result()
    shifted = deepcopy(original)
    for row in shifted["records"]:
        row["value"] = str(Decimal(row["value"]) * Decimal("0.00000001") + Decimal("1000000"))
    expected = analyze_research_series(original, adf_lags=2, kpss_lags=2)
    actual = analyze_research_series(shifted, adf_lags=2, kpss_lags=2)
    assert actual["adf"]["statistic"] == expected["adf"]["statistic"]
    assert actual["kpss"]["statistic"] == expected["kpss"]["statistic"]
    assert actual["autocorrelation"] == expected["autocorrelation"]
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        actual = analyze_research_series(original, adf_lags=2, kpss_lags=2)
    assert actual == expected


@pytest.mark.parametrize("fault", ["null", "absent", "ambiguous", "quality", "partial"])
def test_missing_ambiguous_or_partial_observations_are_never_dropped(fault):
    result = research_result()
    row = result["records"][20]
    if fault == "null":
        row["value"] = None
    elif fault == "absent":
        del row["value"]
    elif fault == "ambiguous":
        row["status"] = "ambiguous"
    elif fault == "quality":
        row["quality_flags"] = ["ohlc_bounds_conflict"]
    else:
        row["is_partial"] = True
    with pytest.raises(AtlasError) as error:
        analyze_research_series(result)
    assert error.value.code == "INCOMPLETE_COVERAGE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit", "percent"),
        ("series", "other"),
        ("date_basis", "publication_date"),
        ("adjusted_close_basis", "unadjusted"),
        ("price_basis", "unadjusted"),
        ("currency", "JPY"),
        ("dimensions", {"region": "other"}),
    ],
)
def test_mixed_series_units_or_bases_are_rejected(field, value):
    result = research_result()
    result["records"][20][field] = value
    with pytest.raises(AtlasError):
        analyze_research_series(result)


@pytest.mark.parametrize("value", [True, 1.1, "NaN", "Infinity", "1e9999", "1e41", "1e-41", ""])
def test_unbounded_inexact_and_nonfinite_values_fail_safely(value):
    result = research_result()
    result["records"][0]["value"] = value
    with pytest.raises(AtlasError) as error:
        analyze_research_series(result)
    assert error.value.code == "INVALID_INPUT"


def test_extreme_allowed_return_variance_serializes_without_decimal_errors():
    result = research_result(["1e-40", "1e40", "1e-40", "1e40"] * 10)
    report = analyze_research_series(result, transform="return", max_lag=2)
    assert Decimal(report["descriptive"]["sample_variance"]).is_finite()
    assert len(report["descriptive"]["sample_variance"]) > 150
    assert "NaN" not in canonical(report).decode()


@pytest.mark.parametrize(
    "fault",
    [
        "compact",
        "projection",
        "page",
        "offset",
        "count",
        "truncated",
        "nested_reference",
        "no_sources",
        "bad_source",
    ],
)
def test_full_embedded_untruncated_result_is_required(fault):
    result = research_result()
    result["response"] = response_metadata(result)
    if fault == "compact":
        result["response"]["view"] = "compact"
    elif fault == "projection":
        result["response"]["fields"] = ["value"]
    elif fault == "page":
        result["response"]["next_cursor"] = "r1_" + "d" * 64 + "_1"
    elif fault == "offset":
        result["response"]["offset"] = 1
    elif fault == "count":
        result["response"]["total_records"] += 1
    elif fault == "truncated":
        result["coverage"]["truncated"] = True
    elif fault == "nested_reference":
        result["records"][0]["source"] = {"evidence_id": "a" * 64}
    elif fault == "no_sources":
        result["sources"] = []
    else:
        del result["sources"][0]["raw_sha256"]
    with pytest.raises(AtlasError):
        analyze_research_series(result)


@pytest.mark.parametrize(
    "frequency,dates",
    [
        ("daily", ["2026-01-01", "2026-01-02", "2026-01-03"]),
        ("weekly", ["2026-01-01", "2026-01-08", "2026-01-15"]),
        ("monthly", ["2026-01-31", "2026-02-28", "2026-03-31"]),
        ("quarterly", ["2025-09-30", "2025-12-31", "2026-03-31"]),
        ("annual", ["2024-02-29", "2025-02-28", "2026-02-28"]),
    ],
)
def test_calendar_spacing_accepts_regular_labels_and_rejects_omitted_periods(frequency, dates):
    result = research_result([1, 2, 3])
    for row, day in zip(result["records"], dates, strict=True):
        row["date"] = day
    report = analyze_research_series(result, frequency=frequency, max_lag=2)
    assert report["sample"]["observations"] == 3
    result["records"][1]["date"] = (date.fromisoformat(dates[1]) + timedelta(days=1)).isoformat()
    with pytest.raises(AtlasError):
        analyze_research_series(result, frequency=frequency, max_lag=2)


def test_observation_spacing_preserves_holes_without_certifying_calendar():
    result = research_result()
    del result["records"][20]
    report = analyze_research_series(result)
    assert report["sample"]["observations"] == 79
    assert any("cannot certify a trading calendar" in text for text in report["limitations"])
    with pytest.raises(AtlasError):
        analyze_research_series(result, frequency="daily")


def test_metric_selection_never_merges_unrelated_or_unavailable_series():
    result = research_result()
    other = deepcopy(result["records"])
    for row in result["records"]:
        row["metric"] = "first"
    for row in other:
        row.update(metric="second", value=None, status="unavailable")
    result["records"].extend(other)
    report = analyze_research_series(result, metric="first")
    assert report["sample"]["observations"] == 80 and len(report["input"]["records"]) == 160
    with pytest.raises(AtlasError):
        analyze_research_series(result)
    with pytest.raises(AtlasError):
        analyze_research_series(result, metric="missing")


@pytest.mark.parametrize(
    "changes",
    [
        {"max_lag": True},
        {"max_lag": 25},
        {"adf_lags": -1},
        {"kpss_lags": "3"},
        {"transform": "log_return"},
        {"frequency": "business_days"},
        {"deterministic": "none"},
        {"field": "source.value"},
        {"metric": ""},
        {"transform": []},
        {"frequency": []},
        {"deterministic": []},
    ],
)
def test_unsupported_options_fail_without_coercion(changes):
    with pytest.raises(AtlasError) as error:
        analyze_research_series(research_result(), **changes)
    assert error.value.code == "INVALID_INPUT"


def test_duplicate_invalid_dates_and_insufficient_transformed_sample_fail():
    for day in ("2025-01-01", "2025-02-30", "2025-01-02T00:00:00Z"):
        result = research_result()
        result["records"][1]["date"] = day
        with pytest.raises(AtlasError):
            analyze_research_series(result)
    with pytest.raises(AtlasError):
        analyze_research_series(research_result([1, 2, 3]), transform="difference", max_lag=1)
    with pytest.raises(AtlasError):
        analyze_research_series(research_result([1, 2, 3]), max_lag=3)


def test_volume_uses_volume_unit_not_price_currency():
    result = research_result()
    for row in result["records"]:
        row.update(volume=row["value"], volume_unit="shares", unit="USD")
    assert analyze_research_series(result, field="volume")["sample"]["unit"] == "shares"


def test_input_output_and_record_budgets_are_enforced(monkeypatch):
    import atlas_terminal.research_statistics as statistics

    result = research_result()
    monkeypatch.setattr(statistics, "MAX_STATISTICS_BYTES", 10)
    with pytest.raises(AtlasError, match="4 MiB"):
        analyze_research_series(result)
    monkeypatch.setattr(statistics, "MAX_STATISTICS_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(statistics, "MAX_STATISTICS_OUTPUT_BYTES", 10)
    with pytest.raises(AtlasError, match="5 MiB"):
        analyze_research_series(result)
    result = research_result([1] * 5001)
    with pytest.raises(AtlasError, match="5000"):
        analyze_research_series(result)


def test_nonstationary_reference_has_correct_opposite_null_hypotheses():
    state, level, values = 7, 100, []
    for _ in range(160):
        state = (1664525 * state + 1013904223) % 2**32
        level += (state >> 16) % 21 - 10
        values.append(level)
    report = analyze_research_series(research_result(values), adf_lags=3, kpss_lags=3)
    assert abs(Decimal(report["adf"]["statistic"]) - Decimal("-1.254592788062238")) < Decimal(
        "1e-12"
    )
    assert abs(Decimal(report["kpss"]["statistic"]) - Decimal("3.2124647273050235")) < Decimal(
        "1e-12"
    )
    assert report["adf"]["reject_null"]["5%"] is False
    assert report["kpss"]["reject_null"]["5%"] is True
    assert report["adf"]["null_hypothesis"] == "unit_root"
    assert report["kpss"]["null_hypothesis"] == "level_stationary"


def test_mutating_report_cannot_poison_later_calculations_or_original_input():
    result = research_result()
    expected = analyze_research_series(result)
    altered = analyze_research_series(result)
    altered["calculation"]["adf_critical_coefficients"]["constant"] = ["999"]
    altered["calculation"]["adf_critical_coefficients"]["5%"][0] = "999"
    altered["methodology"][0]["url"] = "https://malicious.invalid"
    altered["input"]["records"][0]["value"] = "999"
    assert analyze_research_series(result) == expected


def test_deep_or_excessively_branching_metadata_fail_with_bounded_error():
    result = research_result()
    annotation = {}
    for _ in range(500):
        annotation = {"nested": annotation}
    result["records"][0]["annotation"] = annotation
    with pytest.raises(AtlasError):
        analyze_research_series(result)
    result = research_result()
    result["records"][0]["annotation"] = [None] * 100_001
    with pytest.raises(AtlasError, match="nested data bounds"):
        analyze_research_series(result)


def test_partial_input_metadata_is_retained_without_inventing_upstream_completeness():
    result = research_result()
    result["coverage"]["partial"] = True
    report = analyze_research_series(result)
    assert report["sample"]["input_reports_partial_coverage"] is True
    assert report["sample"]["upstream_completeness_verified"] is False
    assert report["sample"]["calendar_spacing_checked"] is False
    assert report["input"]["coverage"]["partial"] is True


def test_statistics_cli_is_offline_private_and_refuses_overwrite(tmp_path, monkeypatch):
    import atlas_terminal.cli as cli

    def forbidden_client(*args, **kwargs):
        raise AssertionError("Offline analysis must not construct a source client")

    monkeypatch.setattr(cli, "Atlas", forbidden_client)
    source, target = tmp_path / "source.json", tmp_path / "statistics.json"
    source.write_bytes(canonical(research_result()))
    before = source.read_bytes()
    arguments = [
        "research",
        "statistics",
        "--input",
        str(source),
        "--output",
        str(target),
        "--adf-lags",
        "1",
        "--kpss-lags",
        "3",
    ]
    runner = CliRunner()
    result = runner.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["network_used"] is False
    assert json.loads(result.output)["published"] is False
    assert target.stat().st_mode & 0o777 == 0o600
    report = json.loads(target.read_text())
    assert report["adf"]["lags"] == 1 and report["kpss"]["lags"] == 3
    retained = target.read_bytes()
    assert runner.invoke(cli.app, arguments).exit_code != 0
    assert target.read_bytes() == retained and source.read_bytes() == before


@pytest.mark.parametrize("fault", ["missing", "json", "scalar", "partial", "option", "symlink"])
def test_statistics_cli_rejects_invalid_input_and_output_without_side_effects(tmp_path, fault):
    from atlas_terminal.cli import app

    source, target = tmp_path / "source.json", tmp_path / "statistics.json"
    result = research_result()
    if fault == "partial":
        result["records"][10]["value"] = None
    if fault != "missing":
        source.write_bytes(canonical(result))
    if fault in {"json", "scalar"}:
        source.write_text("{" if fault == "json" else "[]")
    arguments = ["research", "statistics", "--input", str(source), "--output", str(target)]
    if fault == "option":
        arguments += ["--transform", "auto"]
    if fault == "symlink":
        target.symlink_to(source)
    before = source.read_bytes() if source.exists() else None
    assert CliRunner().invoke(app, arguments).exit_code != 0
    assert not target.exists() or target.is_symlink()
    assert (source.read_bytes() if source.exists() else None) == before


@pytest.mark.parametrize(
    "field,value", [("status", "provisional_unknown"), ("session_status", "in_progress")]
)
def test_unknown_completion_state_cannot_be_certified_as_complete(field, value):
    result = research_result()
    result["records"][0][field] = value
    with pytest.raises(AtlasError):
        analyze_research_series(result)


def test_intraday_results_are_not_silently_treated_as_daily_samples():
    result = research_result()
    result["dataset"] = "intraday_prices"
    with pytest.raises(AtlasError, match="lower-frequency"):
        analyze_research_series(result)
