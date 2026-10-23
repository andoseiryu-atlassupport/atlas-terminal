"""Native asset-by-asset time-series factor estimation, alongside the fundamental model."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_math import ONE, ZERO, covariance, dot, ewma_weights, text, wls
from atlas_terminal.risk_models import (
    MAX_RISK_SESSIONS,
    TIME_SERIES_METHODOLOGY,
    RiskFitQuery,
    TimeSeriesManifest,
    TimeSeriesSession,
    utc_key,
)

TIME_SERIES_LIMITATIONS = [
    "Historical time-series betas use supplied observed factor returns, not fundamental exposures.",
    "Input rights, adjustment convention and historical availability are caller assertions.",
    "All assets/factors must share explicit daily periods and a declared return unit/currency.",
    "Betas stay fixed for forecasts; alpha is diagnostic, not an expected-return forecast.",
    "Specific covariance is diagonal; residual correlations and changing exposures are omitted.",
    "Annualization assumes 252 sessions, fixed betas and no serial correlation.",
    "No downloaded vendor factors or price feed; real-market forecast accuracy is unverified.",
]


@dataclass
class TimeSeriesPanel:
    manifest: TimeSeriesManifest
    sessions: list[TimeSeriesSession]

    @cached_property
    def asset_ids(self) -> list[str]:
        return sorted(row.asset_id for row in self.manifest.universe)

    def validate(self) -> None:
        assets = set(self.asset_ids)
        sources = {s.source_id for s in self.manifest.sources}
        factors = set(self.manifest.factors)
        if not 1 <= len(self.sessions) <= MAX_RISK_SESSIONS:
            raise AtlasError("INVALID_INPUT", "Time-series panel exceeds session bounds.")
        self.sessions.sort(key=lambda row: row.end_at)
        for i, row in enumerate(self.sessions):
            if {r.asset_id for r in row.observations} != assets or set(
                row.factor_returns
            ) != factors:
                raise AtlasError(
                    "RISK_INPUT_INCOMPLETE",
                    "Every session needs the exact asset and factor universe.",
                )
            if row.source_id not in sources or row.factor_source_id not in sources:
                raise AtlasError("INVALID_INPUT", "Session references an unknown source ID.")
            needs_rf = self.manifest.factor_convention == "excess_return"
            if needs_rf != (row.risk_free_return is not None):
                raise AtlasError(
                    "INVALID_INPUT",
                    "Excess factors require a risk-free return; total factors must omit it.",
                )
            if i and (
                row.start_at != self.sessions[i - 1].end_at
                or row.available_at < self.sessions[i - 1].available_at
            ):
                raise AtlasError(
                    "INVALID_INPUT", "Sessions must be contiguous with nondecreasing availability."
                )

    def known_sessions(self, cutoff: datetime) -> list[TimeSeriesSession]:
        return [s for s in self.sessions if s.end_at <= cutoff and s.available_at <= cutoff]

    def modeled_returns(self, session: TimeSeriesSession) -> list[Decimal]:
        rf = Decimal(session.risk_free_return or "0")
        values = {r.asset_id: Decimal(r.total_return) - rf for r in session.observations}
        return [values[a] for a in self.asset_ids]


def fit_time_series(panel: TimeSeriesPanel, query: RiskFitQuery) -> dict[str, Any]:
    if query.method != "time_series":
        raise AtlasError("INVALID_INPUT", "Select time_series for observed-factor panel inputs.")
    known = panel.known_sessions(query.as_of)
    if len(known) < query.lookback:
        raise AtlasError("RISK_INPUT_INCOMPLETE", "Supply enough available time-series sessions.")
    if query.as_of - known[-1].end_at > timedelta(days=7):
        raise AtlasError("RISK_INPUT_INCOMPLETE", "Time-series observations are older than 7 days.")
    selected = known[-query.lookback :]
    factors = sorted(panel.manifest.factors)
    factor_returns = [[Decimal(s.factor_returns[f]) for f in factors] for s in selected]
    returns = [panel.modeled_returns(s) for s in selected]
    time_weights = ewma_weights(len(selected), Decimal(query.decay))
    regression_weights = (
        time_weights
        if query.regression_weights == "ewma"
        else [ONE / len(selected)] * len(selected)
    )
    # Scaling is numerical only. Stored betas are restored to original factor-return units.
    means = [
        dot([row[i] for row in factor_returns], regression_weights) for i in range(len(factors))
    ]
    scales = []
    for i, mean in enumerate(means):
        variance = dot([(row[i] - mean) ** 2 for row in factor_returns], regression_weights)
        if variance <= Decimal("1e-24"):
            raise AtlasError("RISK_RANK_DEFICIENT", "A supplied time-series factor is constant.")
        scales.append(variance.sqrt())
    design = [
        [ONE] + [(v - m) / s for v, m, s in zip(row, means, scales, strict=True)]
        for row in factor_returns
    ]
    parameter_count = len(factors) + 1
    if len(selected) < 3 * parameter_count:
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "Use three sessions per free regression parameter."
        )
    exposures: list[list[str]] = []
    alphas: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    specific: list[Decimal] = []
    for i, asset in enumerate(panel.asset_ids):
        values = [row[i] for row in returns]
        coefficients, residuals, pivot = wls(design, values, regression_weights)
        betas = [c / scale for c, scale in zip(coefficients[1:], scales, strict=True)]
        alpha = coefficients[0] - dot(betas, means)
        mean_return = dot(values, regression_weights)
        total = dot([(v - mean_return) ** 2 for v in values], regression_weights)
        residual_variance = covariance([[r] for r in residuals], time_weights)[0][0]
        residual_variance *= Decimal(len(selected)) / (len(selected) - parameter_count)
        specific.append(residual_variance)
        exposures.append([text(b) for b in betas])
        alphas.append(text(alpha))
        diagnostics.append(
            {
                "asset_id": asset,
                "alpha": text(alpha),
                "weighted_r_squared": text(
                    ONE - dot([r * r for r in residuals], regression_weights) / total
                )
                if total
                else None,
                "minimum_relative_pivot": text(pivot),
                "free_parameters": parameter_count,
                "residual_degrees_of_freedom": len(selected) - parameter_count,
            }
        )
    target = sum(specific, ZERO) / len(specific)
    shrinkage = Decimal(query.specific_shrinkage)
    specific = [
        max(Decimal("0.00000001"), (ONE - shrinkage) * value + shrinkage * target)
        for value in specific
    ]
    common_covariance = covariance(
        factor_returns, time_weights, Decimal(query.covariance_shrinkage)
    )
    summary = {
        "methodology_version": TIME_SERIES_METHODOLOGY,
        "method": "time_series",
        "input_head_id": query.head_id,
        "as_of": utc_key(query.as_of),
        "is_synthetic": panel.manifest.is_synthetic,
        "historical_availability": "caller_asserted_unverified",
        "currency": panel.manifest.currency,
        "frequency": "daily",
        "return_convention": panel.manifest.return_convention,
        "modeled_return": panel.manifest.factor_convention,
        "factor_return_origin": "supplied_observed_factor_returns",
        "assets": len(panel.asset_ids),
        "estimation_sessions": len(selected),
        "training_start": utc_key(selected[0].start_at),
        "training_end": utc_key(selected[-1].end_at),
        "factor_order": factors,
        "annualization_sessions": 252,
        "estimation_options": query.model_dump(mode="json", exclude={"action", "head_id", "as_of"}),
        "weighting": query.regression_weights or "equal",
        "effective_covariance_observations": text(ONE / dot(time_weights, time_weights)),
        "minimum_relative_pivot": text(
            min(Decimal(d["minimum_relative_pivot"]) for d in diagnostics)
        ),
        "specific_variance_floor": "0.00000001",
        "sources": [s.model_dump(mode="json") for s in panel.manifest.sources],
        "limitations": TIME_SERIES_LIMITATIONS,
    }
    return {
        "summary": summary,
        "asset_ids": panel.asset_ids,
        "exposures": exposures,
        "alphas": alphas,
        "regression_diagnostics": diagnostics,
        "factor_covariance": [[text(v) for v in row] for row in common_covariance],
        "specific_variances": [text(v) for v in specific],
        "transformations": {
            f: {"time_center": text(m), "time_scale": text(s)}
            for f, m, s in zip(factors, means, scales, strict=True)
        },
        "vintages": [
            {
                "asset_id": asset,
                "source_id": selected[-1].source_id,
                "locator": selected[-1].locator,
                "factor_source_id": selected[-1].factor_source_id,
                "factor_locator": selected[-1].factor_locator,
                "last_observation": utc_key(selected[-1].end_at),
                "available_at": utc_key(selected[-1].available_at),
            }
            for asset in panel.asset_ids
        ],
        "history": [
            {
                "start_at": utc_key(s.start_at),
                "end_at": utc_key(s.end_at),
                "available_at": utc_key(s.available_at),
                "factor_returns": [text(v) for v in f],
                "factor_return_origin": "supplied_observed_factor_returns",
            }
            for s, f in zip(selected, factor_returns, strict=True)
        ],
        "training_returns": [[text(v) for v in row] for row in returns],
        "fit_query": query.model_dump(mode="json"),
    }
