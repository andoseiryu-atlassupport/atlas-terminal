"""Offline Decimal diagnostics for one complete, source-backed observation series."""

import json
import re
from calendar import monthrange
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Any

from pydantic import ValidationError

from atlas_terminal.errors import AtlasError
from atlas_terminal.research_views import ResearchResult
from atlas_terminal.util import canonical, digest

MAX_STATISTICS_BYTES = 4 * 1024 * 1024
MAX_STATISTICS_ROWS = 5000
MAX_STATISTICS_OUTPUT_BYTES = 5 * 1024 * 1024
PRECISION = 256
QUANTUM = Decimal("1e-18")
ZERO = Decimal(0)
ONE = Decimal(1)

# MacKinnon (2010), N=1, response-surface coefficients a0+a1/n+a2/n²+a3/n³.
# Values cross-checked with statsmodels 0.14.4 tsa/adfvalues.py; no fitted p-values.
ADF_COEFFICIENTS = {
    "constant": {
        "1%": ("-3.43035", "-6.5393", "-16.786", "-79.433"),
        "5%": ("-2.86154", "-2.8903", "-4.234", "-40.040"),
        "10%": ("-2.56677", "-1.5384", "-2.809", "0"),
    },
    "trend": {
        "1%": ("-3.95877", "-9.0531", "-28.428", "-134.155"),
        "5%": ("-3.41049", "-4.3904", "-9.036", "-45.374"),
        "10%": ("-3.12705", "-2.5856", "-3.925", "-22.380"),
    },
}
# Kwiatkowski, Phillips, Schmidt and Shin (1992), Table 1.
KPSS_CRITICAL = {
    "constant": {"10%": "0.347", "5%": "0.463", "2.5%": "0.574", "1%": "0.739"},
    "trend": {"10%": "0.119", "5%": "0.146", "2.5%": "0.176", "1%": "0.216"},
}
METHODOLOGY = [
    {
        "method": "ADF critical values",
        "reference": "MacKinnon (2010), Critical Values for Cointegration Tests, N=1",
        "url": "https://www.econ.queensu.ca/sites/econ.queensu.ca/files/qed_wp_1227.pdf",
        "cross_check": "https://github.com/statsmodels/statsmodels/blob/v0.14.4/"
        "statsmodels/tsa/adfvalues.py",
    },
    {
        "method": "KPSS statistic and critical values",
        "reference": "Kwiatkowski, Phillips, Schmidt and Shin (1992), "
        "Journal of Econometrics 54, 159–178, equations 10–11 and Table 1",
        "url": "https://doi.org/10.1016/0304-4076(92)90104-Y",
        "cross_check": "https://github.com/statsmodels/statsmodels/blob/v0.14.4/"
        "statsmodels/tsa/stattools.py",
    },
]


def _invalid(message: str) -> AtlasError:
    return AtlasError("INVALID_INPUT", message)


def _text(value: Decimal) -> str:
    rounded = value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    result = format(rounded, "f").rstrip("0").rstrip(".")
    return result if rounded else "0"


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or len(str(value)) > 100:
        raise _invalid("Statistics need exact decimal strings or integers, without missing values.")
    try:
        number = Decimal(value)
        parts = number.as_tuple()
        if (
            not number.is_finite()
            or not isinstance(parts.exponent, int)
            or not -40 <= parts.exponent <= 40
            or len(parts.digits) > 80
            or abs(number) > Decimal("1e40")
        ):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise _invalid("Statistics values must be finite bounded decimals.") from None
    return number


def _unit(row: dict[str, Any], field: str) -> str:
    value = row.get("volume_unit") if field == "volume" else row.get("unit")
    if value is None and field in {"open", "high", "low", "close", "adjusted_close"}:
        value = row.get("currency")
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise _invalid("Every selected statistics record needs an explicit unit.")
    return value


def _validate_spacing(dates: list[date], frequency: str) -> None:
    if frequency == "observations":
        return
    step = {"monthly": 1, "quarterly": 3, "annual": 12}.get(frequency)
    for left, right in zip(dates[:-1], dates[1:], strict=True):
        if frequency in {"daily", "weekly"}:
            valid = (right - left).days == (1 if frequency == "daily" else 7)
        else:
            month_step = (right.year - left.year) * 12 + right.month - left.month
            month_end = (
                left.day == monthrange(left.year, left.month)[1]
                and right.day == monthrange(right.year, right.month)[1]
            )
            valid = month_step == step and (left.day == right.day or month_end)
        if not valid:
            raise _invalid(
                "Selected dates do not have the requested regular spacing; "
                "missing periods are never filled."
            )


def _select(
    result: dict[str, Any], field: str, metric: str | None, frequency: str
) -> tuple[list[int], list[date], list[Decimal], str]:
    if not 1 <= len(result["records"]) <= MAX_STATISTICS_ROWS:
        raise _invalid("Statistics input must contain 1–5000 records before metric selection.")
    selected: list[tuple[int, date, Decimal]] = []
    identity: bytes | None = None
    units: set[str] = set()
    for index, row in enumerate(result["records"]):
        if metric is not None and row.get("metric") != metric:
            continue
        observed = row.get("date")
        try:
            if not isinstance(observed, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed):
                raise ValueError
            day = date.fromisoformat(observed)
        except ValueError:
            raise _invalid("Statistics records need valid ISO observation dates.") from None
        if (
            row.get("status") not in (None, "reported", "derived")
            or row.get("quality_flags")
            or row.get("is_partial") is True
            or row.get("session_status") not in (None, "completed")
        ):
            raise AtlasError(
                "INCOMPLETE_COVERAGE",
                "Statistics require complete, unambiguous selected observations; "
                "no rows are dropped.",
            )
        if field not in row or row[field] is None:
            raise AtlasError(
                "INCOMPLETE_COVERAGE", "The selected statistics field has a missing observation."
            )
        units.add(_unit(row, field))
        current = canonical(
            {
                key: row.get(key)
                for key in (
                    "identifier",
                    "series",
                    "series_id",
                    "metric",
                    "currency",
                    "dimensions",
                    "country",
                    "region",
                    "tenor",
                    "maturity",
                    "frequency",
                    "date_basis",
                    "adjustment_basis",
                    "price_basis",
                    "adjusted_close_basis",
                    "instrument_type",
                    "taxonomy",
                    "concept",
                )
            }
        )
        if identity is None:
            identity = current
        elif current != identity:
            raise _invalid("Statistics records must describe one series with one consistent basis.")
        selected.append((index, day, _number(row[field])))
    if not selected:
        raise AtlasError("INCOMPLETE_COVERAGE", "No records match the statistics metric selector.")
    if len(units) != 1:
        raise _invalid("Selected statistics records have incompatible units.")
    selected.sort(key=lambda item: item[1])
    dates = [item[1] for item in selected]
    if len(set(dates)) != len(dates):
        raise _invalid("Statistics require unique dates; duplicate observations are ambiguous.")
    _validate_spacing(dates, frequency)
    return [item[0] for item in selected], dates, [item[2] for item in selected], units.pop()


def _dot(left: list[Decimal], right: list[Decimal]) -> Decimal:
    return sum((a * b for a, b in zip(left, right, strict=True)), ZERO)


def _has_evidence_reference(value: Any) -> bool:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 100_000:
            raise _invalid("Statistics input exceeds nested data bounds.")
        if isinstance(current, dict):
            if "evidence_id" in current:
                return True
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return False


def _ols(
    columns: list[list[Decimal]], response: list[Decimal]
) -> tuple[list[Decimal], list[Decimal], list[list[Decimal]]]:
    """Twice-orthogonalized QR; no ridge, pseudo-inverse or rank repair."""
    width = len(columns)
    q: list[list[Decimal]] = []
    r = [[ZERO] * width for _ in range(width)]
    for j, column in enumerate(columns):
        vector = column[:]
        original_norm = _dot(vector, vector).sqrt()
        for _ in range(2):
            for i, basis in enumerate(q):
                projection = _dot(basis, vector)
                r[i][j] += projection
                vector = [x - projection * y for x, y in zip(vector, basis, strict=True)]
        norm = _dot(vector, vector).sqrt()
        if not original_norm or norm / original_norm < Decimal("1e-24"):
            raise ArithmeticError("rank_deficient_or_ill_conditioned_design")
        r[j][j] = norm
        q.append([x / norm for x in vector])
    rhs = [_dot(column, response) for column in q]
    coefficients = [ZERO] * width
    for i in reversed(range(width)):
        coefficients[i] = (rhs[i] - _dot(r[i][i + 1 :], coefficients[i + 1 :])) / r[i][i]
    residuals = [
        value - sum((columns[j][i] * coefficients[j] for j in range(width)), ZERO)
        for i, value in enumerate(response)
    ]
    inverse = [[ZERO] * width for _ in range(width)]
    for j in range(width):
        for i in reversed(range(width)):
            rhs_value = ONE if i == j else ZERO
            inverse[i][j] = (
                rhs_value - sum((r[i][k] * inverse[k][j] for k in range(i + 1, width)), ZERO)
            ) / r[i][i]
    return coefficients, residuals, inverse


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "statistic": None, **extra}


def _standardized(values: list[Decimal]) -> list[Decimal] | None:
    mean = sum(values, ZERO) / len(values)
    residual = [value - mean for value in values]
    scale = max(abs(value) for value in residual)
    return [value / scale for value in residual] if scale else None


def _adf(values: list[Decimal], lags: int, deterministic: str) -> dict[str, Any]:
    nobs = len(values) - 1 - lags
    width = 2 + lags + int(deterministic == "trend")
    common: dict[str, Any] = {
        "null_hypothesis": "unit_root",
        "alternative": "trend_stationary" if deterministic == "trend" else "level_stationary",
        "lags": lags,
        "deterministic": deterministic,
        "regression_observations": nobs,
        "residual_degrees_of_freedom": nobs - width,
        "p_value": None,
        "critical_value_method": "MacKinnon 2010 N=1 finite-sample response surface",
        "formula": "Δy[t] = constant + gamma*y[t-1] + optional trend + "
        "sum(delta[j]*Δy[t-j], j=1..lags) + error[t]; statistic=gamma/SE(gamma)",
    }
    if nobs < 20 or nobs - width < 10:
        return _unavailable("insufficient_regression_observations", **common)
    normalized = _standardized(values)
    if normalized is None:
        return _unavailable("constant_series", **common)
    differences = [b - a for a, b in zip(normalized[:-1], normalized[1:], strict=True)]
    columns = [normalized[lags:-1], [ONE] * nobs]
    if deterministic == "trend":
        columns.append([Decimal(i + 1) / nobs for i in range(nobs)])
    columns.extend(differences[lags - lag : -lag] for lag in range(1, lags + 1))
    try:
        coefficients, residuals, inverse = _ols(columns, differences[lags:])
    except ArithmeticError as error:
        return _unavailable(str(error), **common)
    sse = _dot(residuals, residuals)
    # A deterministic fit cannot identify a stochastic standard error. This is
    # relative to the normalized response, so input scale does not decide it.
    if sse <= _dot(differences[lags:], differences[lags:]) * Decimal("1e-48"):
        return _unavailable("zero_or_numerically_negligible_residual_variance", **common)
    variance = sse / (nobs - width)
    se = (variance * _dot(inverse[0], inverse[0])).sqrt()
    statistic = coefficients[0] / se
    critical = {
        level: sum(
            (Decimal(coefficient) / Decimal(nobs) ** i for i, coefficient in enumerate(cs)), ZERO
        )
        for level, cs in ADF_COEFFICIENTS[deterministic].items()
    }
    return {
        "status": "available",
        **common,
        "statistic": _text(statistic),
        "lagged_level_coefficient": _text(coefficients[0]),
        "lagged_level_standard_error": _text(se),
        "critical_values": {level: _text(value) for level, value in critical.items()},
        "reject_null": {level: statistic < value for level, value in critical.items()},
        "comparison": "strictly_below_critical_value",
    }


def _kpss(values: list[Decimal], lags: int, deterministic: str) -> dict[str, Any]:
    n = len(values)
    common: dict[str, Any] = {
        "null_hypothesis": "trend_stationary" if deterministic == "trend" else "level_stationary",
        "alternative": "unit_root",
        "lags": lags,
        "deterministic": deterministic,
        "observations": n,
        "p_value": None,
        "kernel": "Bartlett",
        "critical_values": KPSS_CRITICAL[deterministic].copy(),
        "critical_value_method": "KPSS 1992 Table 1 asymptotic critical values",
        "formula": "eta=sum(cumsum(residual)^2)/n^2; "
        "long_run_variance=(sum(residual^2)+2*sum((1-j/(lags+1))*"
        "sum(residual[t]*residual[t-j]),j=1..lags))/n; statistic=eta/long_run_variance",
    }
    if n < 20:
        return _unavailable("insufficient_observations", **common)
    normalized = _standardized(values)
    if normalized is None:
        return _unavailable("constant_series", **common)
    residuals = normalized
    if deterministic == "trend":
        columns = [[ONE] * n, [Decimal(i + 1) / n for i in range(n)]]
        _, residuals, _ = _ols(columns, normalized)
    sum_squares = _dot(residuals, residuals)
    if sum_squares <= _dot(normalized, normalized) * Decimal("1e-48"):
        return _unavailable("zero_or_numerically_negligible_residual_variance", **common)
    long_run_sum = sum_squares + 2 * sum(
        (
            (ONE - Decimal(j) / (lags + 1)) * _dot(residuals[j:], residuals[:-j])
            for j in range(1, lags + 1)
        ),
        ZERO,
    )
    if long_run_sum <= sum_squares * Decimal("1e-48"):
        return _unavailable("nonpositive_or_negligible_long_run_variance", **common)
    cumulative = ZERO
    cumulative_squares = ZERO
    for residual in residuals:
        cumulative += residual
        cumulative_squares += cumulative * cumulative
    statistic = cumulative_squares / (n * long_run_sum)
    return {
        "status": "available",
        **common,
        "statistic": _text(statistic),
        "reject_null": {
            level: statistic > Decimal(value)
            for level, value in KPSS_CRITICAL[deterministic].items()
        },
        "comparison": "strictly_above_critical_value",
    }


def _descriptive(values: list[Decimal]) -> dict[str, Any]:
    n = len(values)
    mean = sum(values, ZERO) / n
    variance = sum(((value - mean) ** 2 for value in values), ZERO) / (n - 1)
    ordered = sorted(values)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "observations": n,
        "minimum": _text(ordered[0]),
        "maximum": _text(ordered[-1]),
        "mean": _text(mean),
        "median": _text(median),
        "sample_variance": _text(variance),
        "sample_standard_deviation": _text(variance.sqrt()),
        "variance_denominator": n - 1,
        "annualized": False,
    }


def _acf(values: list[Decimal], lags: int) -> list[dict[str, Any]]:
    n = len(values)
    centered = _standardized(values)
    denominator = _dot(centered, centered) if centered else ZERO
    total = ZERO
    output: list[dict[str, Any]] = []
    for lag in range(1, lags + 1):
        if not centered or not denominator:
            output.append(
                {
                    "lag": lag,
                    "autocorrelation": None,
                    "ljung_box_q": None,
                    "status": "unavailable",
                    "reason": "constant_series",
                }
            )
            continue
        correlation = _dot(centered[lag:], centered[:-lag]) / denominator
        total += correlation * correlation / (n - lag)
        output.append(
            {
                "lag": lag,
                "autocorrelation": _text(correlation),
                "ljung_box_q": _text(n * (n + 2) * total),
                "status": "available",
                "p_value": None,
            }
        )
    return output


def analyze_research_series(
    result: dict[str, Any],
    *,
    field: str = "value",
    metric: str | None = None,
    transform: str = "level",
    frequency: str = "observations",
    max_lag: int = 10,
    adf_lags: int = 0,
    kpss_lags: int = 0,
    deterministic: str = "constant",
) -> dict[str, Any]:
    """Analyze supplied observations; never fetch, fill, select lag lengths or forecast."""
    try:
        body = canonical(result)
    except (TypeError, ValueError, RecursionError):
        raise _invalid("Statistics input must be a bounded JSON research result.") from None
    if len(body) > MAX_STATISTICS_BYTES:
        raise _invalid("Statistics input exceeds the 4 MiB result budget.")
    try:
        ResearchResult.model_validate(result, strict=True)
    except (ValidationError, TypeError, ValueError):
        raise _invalid("Statistics input must be a full Atlas research result envelope.") from None
    if result["dataset"] == "intraday_prices":
        raise _invalid("Date-based statistics require a daily or lower-frequency source result.")
    if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", field):
        raise _invalid("Choose one top-level numeric statistics field.")
    if metric is not None and (not isinstance(metric, str) or not 1 <= len(metric) <= 100):
        raise _invalid("Statistics metric selector exceeds bounds.")
    if not isinstance(transform, str) or transform not in {"level", "difference", "return"}:
        raise _invalid("Statistics transform must be level, difference or return.")
    if not isinstance(frequency, str) or frequency not in {
        "observations",
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "annual",
    }:
        raise _invalid("Choose observations or an explicit supported calendar frequency.")
    if not isinstance(deterministic, str) or deterministic not in {"constant", "trend"}:
        raise _invalid("Statistics deterministic terms must be constant or trend.")
    if any(type(lag) is not int or not 0 <= lag <= 24 for lag in (max_lag, adf_lags, kpss_lags)):
        raise _invalid("Statistics lag counts must be explicit integers from 0 to 24.")
    response = result.get("response") or {}
    if (
        response.get("view") == "compact"
        or response.get("fields")
        or _has_evidence_reference(result)
        or not result["sources"]
        or any("raw_source_url" not in source for source in result["sources"])
        or result["coverage"].get("truncated") is True
        or response.get("next_cursor") is not None
        or response.get("offset", 0) != 0
        or response.get("total_records", len(result["records"])) != len(result["records"])
    ):
        raise _invalid("Statistics need a full, untruncated result with embedded source evidence.")
    indices, dates, source_values, unit = _select(result, field, metric, frequency)
    with localcontext(Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)):
        values = source_values
        if transform == "difference":
            values = [b - a for a, b in zip(source_values[:-1], source_values[1:], strict=True)]
        elif transform == "return":
            if any(value <= 0 for value in source_values):
                raise _invalid("Return transforms require strictly positive original observations.")
            values = [
                b / a - ONE for a, b in zip(source_values[:-1], source_values[1:], strict=True)
            ]
        if len(values) < 3:
            raise _invalid(
                "Statistics need at least three observations after the selected transform."
            )
        if max(max_lag, kpss_lags, adf_lags) >= len(values):
            raise _invalid(
                "Every statistics lag count must be smaller than the transformed sample."
            )
        output = {
            "schema_version": "0.1.0",
            "analysis": "series_statistics",
            "input_sha256": digest(body),
            "is_synthetic": result["is_synthetic"],
            "source_text_is_untrusted": True,
            "selection": {
                "field": field,
                "metric": metric,
                "transform": transform,
                "frequency": frequency,
                "deterministic": deterministic,
                "max_lag": max_lag,
                "adf_lags": adf_lags,
                "kpss_lags": kpss_lags,
            },
            "sample": {
                "original_observations": len(source_values),
                "observations": len(values),
                "start_date": dates[int(transform != "level")].isoformat(),
                "end_date": dates[-1].isoformat(),
                "input_unit": unit,
                "unit": "ratio_per_observation" if transform == "return" else unit,
                "source_record_indices": indices,
                "transformed_values": [_text(value) for value in values],
                "input_reports_partial_coverage": result["coverage"].get("partial") is True,
                "upstream_completeness_verified": False,
                "calendar_spacing_checked": frequency != "observations",
            },
            "descriptive": _descriptive(values),
            "autocorrelation": _acf(values, max_lag),
            "adf": _adf(values, adf_lags, deterministic),
            "kpss": _kpss(values, kpss_lags, deterministic),
            "calculation": {
                "arithmetic": "Decimal",
                "precision": PRECISION,
                "output_decimal_places": 18,
                "rounding": "ROUND_HALF_EVEN",
                "acf_formula": "sum((y[t]-mean)*(y[t-k]-mean))/sum((y[t]-mean)^2)",
                "ljung_box_formula": "n*(n+2)*sum(acf[k]^2/(n-k),k=1..lag)",
                "lag_selection": "explicit_fixed_lags; no automatic selection",
                "adf_critical_coefficients": {
                    level: list(values) for level, values in ADF_COEFFICIENTS[deterministic].items()
                },
            },
            "methodology": [reference.copy() for reference in METHODOLOGY],
            "limitations": [
                "Diagnostics describe the supplied current-revision snapshot, not publication-time "
                "availability, economic causality or a forecast.",
                "No missing observations are removed, filled or interpolated. Calendar spacing is "
                "checked only when a calendar frequency is selected; observations mode assumes "
                "successive rows form the intended equally spaced process. It cannot certify a "
                "trading calendar or detect absent sessions.",
                "ADF and KPSS have different null hypotheses. Failure to reject is not proof of "
                "the null. Critical-value comparisons are approximate, depend on the selected "
                "deterministic terms and fixed lags, and do not correct for repeated tests.",
                "No p-values, lag optimization, seasonal adjustment, structural-break model, "
                "cointegration or GARCH estimate is supplied. "
                "Ljung-Box values are statistics only.",
                "Results use 256-digit Decimal calculations and round final numbers half-even to "
                "18 fractional digits; square roots and division are finite-precision operations. "
                "Degenerate designs and negligible stochastic residuals are unavailable.",
                "Complete selected observations do not establish complete upstream coverage. "
                "Original source conditions and coverage remain in the embedded input.",
            ],
            "input": json.loads(body),
        }
    if len(canonical(output)) > MAX_STATISTICS_OUTPUT_BYTES:
        raise _invalid("Statistics output exceeds the 5 MiB report budget.")
    return output
