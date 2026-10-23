"""An independent, constrained cross-sectional equity model on supplied observations."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.risk_math import (
    ONE,
    ZERO,
    covariance,
    dot,
    ewma_weights,
    matvec,
    standardize,
    text,
    wls,
)
from atlas_terminal.risk_models import (
    MAX_RISK_SESSIONS,
    METHODOLOGY,
    STYLES,
    RiskAnalyzeQuery,
    RiskFitQuery,
    RiskFundamental,
    RiskManifest,
    RiskPosition,
    RiskSession,
    utc_key,
)

LIMITATIONS = [
    "Independent experimental fundamental model; no MSCI/Barra or French numerical inputs.",
    "Input rights and historical availability are operator assertions, not independently verified.",
    "A balanced, supplied universe is required; survivorship/selection bias is not corrected.",
    "No consolidated price feed, security master, delisting engine, ETF look-through or FX risk.",
    "Diagonal specific risk omits residual correlations; no liquidity or trading-cost estimate.",
    "Annualization is daily variance times 252 with fixed exposures and no serial correlation.",
    "Synthetic verification does not establish forecast accuracy on real portfolios.",
]


def industry(sic: str) -> str:
    division = int(sic[:2])
    for low, high, label in (
        (1, 9, "agriculture"),
        (10, 14, "mining"),
        (15, 17, "construction"),
        (20, 39, "manufacturing"),
        (40, 49, "transport_utilities"),
        (50, 51, "wholesale"),
        (52, 59, "retail"),
        (60, 67, "finance"),
        (70, 89, "services"),
        (91, 99, "public_administration"),
    ):
        if low <= division <= high:
            return label
    raise AtlasError("RISK_INPUT_INCOMPLETE", "A supplied SIC has no supported division.")


@dataclass
class RiskPanel:
    manifest: RiskManifest
    fundamentals: list[RiskFundamental]
    sessions: list[RiskSession]

    def validate(self) -> None:
        assets = {a.asset_id for a in self.manifest.universe}
        sources = {s.source_id for s in self.manifest.sources}
        if not 1 <= len(self.sessions) <= MAX_RISK_SESSIONS or len(self.fundamentals) > 10000:
            raise AtlasError("INVALID_INPUT", "Risk panel exceeds observation bounds.")
        self.sessions.sort(key=lambda row: row.end_at)
        for i, session in enumerate(self.sessions):
            if {r.asset_id for r in session.observations} != assets:
                raise AtlasError(
                    "RISK_INPUT_INCOMPLETE",
                    "Every session must contain exactly the manifest universe; "
                    "no returns are filled.",
                )
            if session.source_id not in sources:
                raise AtlasError("INVALID_INPUT", "Session references an unknown source ID.")
            if i and (
                session.start_at != self.sessions[i - 1].end_at
                or session.available_at < self.sessions[i - 1].available_at
            ):
                raise AtlasError(
                    "INVALID_INPUT", "Sessions must be contiguous with nondecreasing availability."
                )
        seen = set()
        for row in self.fundamentals:
            key = row.asset_id, row.period_end, row.available_at
            if key in seen or row.asset_id not in assets or row.source_id not in sources:
                raise AtlasError("INVALID_INPUT", "Duplicate fundamental vintage or unknown ID.")
            seen.add(key)

    @cached_property
    def asset_ids(self) -> list[str]:
        return sorted(a.asset_id for a in self.manifest.universe)

    @cached_property
    def fundamental_index(self) -> dict[str, list[RiskFundamental]]:
        return {
            asset: [row for row in self.fundamentals if row.asset_id == asset]
            for asset in self.asset_ids
        }

    def known_sessions(self, cutoff: datetime) -> list[RiskSession]:
        return [s for s in self.sessions if s.end_at <= cutoff and s.available_at <= cutoff]


@dataclass
class Exposures:
    factors: list[str]
    rows: list[list[Decimal]]
    weights: list[Decimal]
    caps: list[Decimal]
    industries: list[str]
    transformations: dict[str, dict[str, str]]
    vintages: list[dict[str, str]]


def exposures(panel: RiskPanel, cutoff: datetime, styles: Sequence[str]) -> Exposures:
    known = panel.known_sessions(cutoff)
    if not known or cutoff - known[-1].end_at > timedelta(days=7):
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "No available market capitalization within 7 days."
        )
    caps_by_id = {r.asset_id: Decimal(r.market_cap) for r in known[-1].observations}
    assets = panel.asset_ids
    caps = [caps_by_id[a] for a in assets]
    weights = [cap.sqrt() for cap in caps]
    total = sum(weights, ZERO)
    weights = [weight / total for weight in weights]
    fundamentals = []
    for asset in assets:
        usable = [
            row
            for row in panel.fundamental_index[asset]
            if row.available_at <= cutoff and row.period_end <= cutoff
        ]
        if not usable:
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE", "A required fundamental vintage was unavailable at cutoff."
            )
        latest = max(usable, key=lambda row: (row.period_end, row.available_at))
        if cutoff - latest.period_end > timedelta(days=550):
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE", "A fundamental period is older than 550 days."
            )
        fundamentals.append(latest)
    industries = [industry(row.sic) for row in fundamentals]
    groups = sorted(set(industries))
    if len(groups) < 2 or any(industries.count(g) < 3 for g in groups):
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "Use at least two industries with three assets each."
        )
    raw: dict[str, list[Decimal]] = {}
    if "size" in styles:
        raw["size"] = [cap.ln() for cap in caps]
    for name, numerator in (
        ("value", "book_equity"),
        ("profitability", "net_income_ttm"),
        ("leverage", "total_debt"),
    ):
        if name in styles:
            raw[name] = [
                Decimal(getattr(row, numerator))
                / (cap if name == "value" else Decimal(row.total_assets))
                for row, cap in zip(fundamentals, caps, strict=True)
            ]
    if "momentum" in styles:
        if len(known) < 252:
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE", "Momentum requires 252 available prior sessions."
            )
        values = dict.fromkeys(assets, ONE)
        for session in known[-252:-21]:
            for row in session.observations:
                values[row.asset_id] *= ONE + Decimal(row.total_return)
        raw["momentum"] = [values[a] - ONE for a in assets]
    if "beta" in styles or "residual_volatility" in styles:
        if len(known) < 126:
            raise AtlasError("RISK_INPUT_INCOMPLETE", "Beta/volatility require 126 prior sessions.")
        prior = [
            {r.asset_id: Decimal(r.total_return) for r in s.observations} for s in known[-126:]
        ]
        # Equal-weight supplied-universe proxy; not an external index or claimed market portfolio.
        proxy = [sum(row.values(), ZERO) / len(assets) for row in prior]
        proxy_mean = sum(proxy, ZERO) / len(proxy)
        centered = [value - proxy_mean for value in proxy]
        denominator = dot(centered, centered)
        if denominator <= Decimal("1e-24"):
            raise AtlasError(
                "RISK_RANK_DEFICIENT", "Historical market proxy has no usable variance."
            )
        betas, volatilities = [], []
        for asset in assets:
            asset_returns = [row[asset] for row in prior]
            mean = sum(asset_returns, ZERO) / len(asset_returns)
            centered_asset = [v - mean for v in asset_returns]
            beta = dot(centered_asset, centered) / denominator
            residuals = [a - beta * b for a, b in zip(centered_asset, centered, strict=True)]
            betas.append(beta)
            volatilities.append((dot(residuals, residuals) / (len(asset_returns) - 2)).sqrt())
        if "beta" in styles:
            raw["beta"] = betas
        if "residual_volatility" in styles:
            raw["residual_volatility"] = volatilities
    transformations = {}
    standardized = {}
    for style in styles:
        standardized[style], transformations[style] = standardize(raw[style], weights)
    factors = ["market"] + ["industry:" + g for g in groups] + ["style:" + s for s in styles]
    rows = [
        [ONE] + [ONE if group == g else ZERO for g in groups] + [standardized[s][i] for s in styles]
        for i, group in enumerate(industries)
    ]
    return Exposures(
        factors,
        rows,
        weights,
        caps,
        industries,
        transformations,
        [
            {
                "asset_id": row.asset_id,
                "period_end": utc_key(row.period_end),
                "available_at": utc_key(row.available_at),
                "source_id": row.source_id,
                "locator": row.locator,
                "market_cap_source_id": known[-1].source_id,
                "market_cap_locator": known[-1].locator,
                "market_cap_end_at": utc_key(known[-1].end_at),
                "market_cap_available_at": utc_key(known[-1].available_at),
            }
            for row in fundamentals
        ],
    )


def regress(
    design: Exposures, returns: list[Decimal]
) -> tuple[list[Decimal], list[Decimal], dict[str, Any]]:
    groups = sorted(set(design.industries))
    cap_weights = {
        g: sum(
            (c for c, group in zip(design.caps, design.industries, strict=True) if group == g), ZERO
        )
        for g in groups
    }
    reference = max(groups, key=lambda g: (cap_weights[g], g))
    free = [g for g in groups if g != reference]
    ratios = {g: cap_weights[g] / cap_weights[reference] for g in free}
    rows = [
        [ONE]
        + [
            (ONE if group == g else ZERO) - (ratios[g] if group == reference else ZERO)
            for g in free
        ]
        + row[1 + len(groups) :]
        for row, group in zip(design.rows, design.industries, strict=True)
    ]
    rank = len(rows[0])
    if len(rows) < max(30, 3 * rank):
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "Use at least 30 assets and three per free parameter."
        )
    coefficients, residuals, pivot = wls(rows, returns, design.weights)
    industry_returns = dict(zip(free, coefficients[1 : len(groups)], strict=True))
    industry_returns[reference] = -sum((ratios[g] * industry_returns[g] for g in free), ZERO)
    full = coefficients[:1] + [industry_returns[g] for g in groups] + coefficients[len(groups) :]
    mean = dot(returns, design.weights)
    total = dot([(r - mean) ** 2 for r in returns], design.weights)
    error = dot([r * r for r in residuals], design.weights)
    return (
        full,
        residuals,
        {
            "free_parameters": rank,
            "residual_degrees_of_freedom": len(rows) - rank,
            "minimum_relative_pivot": text(pivot),
            "weighted_r_squared": None if not total else text(ONE - error / total),
            "industry_constraint": "sum(market_cap_by_industry * industry_return) = 0",
            "industry_capitalizations": {g: text(c) for g, c in cap_weights.items()},
        },
    )


def fit_model(panel: RiskPanel, query: RiskFitQuery) -> dict[str, Any]:
    styles = [s for s in STYLES if s in query.styles]
    known = panel.known_sessions(query.as_of)
    if len(known) < query.lookback + 1:
        raise AtlasError(
            "RISK_INPUT_INCOMPLETE", "Supply lookback sessions plus available warm-up data."
        )
    forecast = exposures(panel, query.as_of, styles)
    factor_history, residual_history, diagnostics, history = [], [], [], []
    for session in known[-query.lookback :]:
        design = exposures(panel, session.start_at, styles)
        if design.factors != forecast.factors or design.industries != forecast.industries:
            raise AtlasError(
                "RISK_INPUT_INCOMPLETE",
                "Industry membership must remain fixed in this model window.",
            )
        values = {r.asset_id: Decimal(r.total_return) for r in session.observations}
        factors, residuals, stats = regress(design, [values[a] for a in panel.asset_ids])
        factor_history.append(factors)
        df_adjustment = (Decimal(len(values)) / stats["residual_degrees_of_freedom"]).sqrt()
        residual_history.append([r * df_adjustment for r in residuals])
        diagnostics.append(stats)
        history.append(
            {
                "start_at": utc_key(session.start_at),
                "end_at": utc_key(session.end_at),
                "exposure_cutoff": utc_key(session.start_at),
                "factor_returns": [text(f) for f in factors],
                "diagnostics": stats,
            }
        )
    time_weights = ewma_weights(len(factor_history), Decimal(query.decay))
    factor_covariance = covariance(
        factor_history, time_weights, Decimal(query.covariance_shrinkage)
    )
    # Only the diagonal is needed; avoid an N x N residual covariance allocation.
    specific = [
        covariance([[row[i]] for row in residual_history], time_weights)[0][0]
        for i in range(len(panel.asset_ids))
    ]
    shrinkage = Decimal(query.specific_shrinkage)
    targets = {
        g: sum(
            (v for v, group in zip(specific, forecast.industries, strict=True) if group == g), ZERO
        )
        / forecast.industries.count(g)
        for g in set(forecast.industries)
    }
    specific = [
        max(Decimal("0.00000001"), (ONE - shrinkage) * v + shrinkage * targets[g])
        for v, g in zip(specific, forecast.industries, strict=True)
    ]
    summary = {
        "methodology_version": METHODOLOGY,
        "input_head_id": query.head_id,
        "as_of": utc_key(query.as_of),
        "is_synthetic": panel.manifest.is_synthetic,
        "historical_availability": "caller_asserted_unverified",
        "currency": "USD",
        "frequency": "daily",
        "return_convention": panel.manifest.return_convention,
        "assets": len(panel.asset_ids),
        "estimation_sessions": len(history),
        "training_start": history[0]["start_at"],
        "training_end": history[-1]["end_at"],
        "factor_order": forecast.factors,
        "styles": styles,
        "estimation_options": query.model_dump(
            mode="json", exclude={"action", "head_id", "as_of", "styles"}
        ),
        "annualization_sessions": 252,
        "weighting": "WLS objective weight proportional to square root of lagged market cap",
        "standardization": "5th/95th percentile clipping; WLS-weighted mean and population scale",
        "effective_covariance_observations": text(ONE / dot(time_weights, time_weights)),
        "minimum_relative_pivot": text(
            min(Decimal(s["minimum_relative_pivot"]) for s in diagnostics)
        ),
        "specific_variance_floor": "0.00000001",
        "sources": [s.model_dump(mode="json") for s in panel.manifest.sources],
        "limitations": LIMITATIONS,
    }
    return {
        "summary": summary,
        "asset_ids": panel.asset_ids,
        "exposures": [[text(x) for x in row] for row in forecast.rows],
        "factor_covariance": [[text(x) for x in row] for row in factor_covariance],
        "specific_variances": [text(x) for x in specific],
        "transformations": forecast.transformations,
        "vintages": forecast.vintages,
        "history": history,
        "training_returns": [
            [values[a] for a in panel.asset_ids]
            for s in known[-query.lookback :]
            for values in [{r.asset_id: r.total_return for r in s.observations}]
        ],
        "fit_query": query.model_dump(mode="json"),
    }


def portfolio_weights(artifact: dict[str, Any], positions: list[RiskPosition]) -> list[Decimal]:
    mapping = {p.asset_id: Decimal(p.weight) for p in positions}
    if set(mapping) - set(artifact["asset_ids"]):
        raise AtlasError("INVALID_INPUT", "Portfolio contains assets outside the fitted universe.")
    return [mapping.get(asset, ZERO) for asset in artifact["asset_ids"]]


def portfolio_risk(artifact: dict[str, Any], weights: list[Decimal]) -> dict[str, Any]:
    x = [[Decimal(v) for v in row] for row in artifact["exposures"]]
    f = [[Decimal(v) for v in row] for row in artifact["factor_covariance"]]
    d = [Decimal(v) for v in artifact["specific_variances"]]
    b = [dot([row[i] for row in x], weights) for i in range(len(f))]
    fb = matvec(f, b)
    common = dot(b, fb)
    specific = dot([w * w for w in weights], d)
    if common < -Decimal("1e-30"):
        raise AtlasError(
            "RISK_NUMERICAL_ERROR", "Saved common covariance produced negative variance."
        )
    common = max(ZERO, common)
    variance = common + specific
    volatility = variance.sqrt()
    annualization = Decimal(252).sqrt()
    contributions = []
    for i, (asset, weight) in enumerate(zip(artifact["asset_ids"], weights, strict=True)):
        if weight == 0:
            continue
        marginal_variance = dot(x[i], fb) + weight * d[i]
        contributions.append(
            {
                "asset_id": asset,
                "weight": text(weight),
                "daily_volatility_contribution": text(weight * marginal_variance / volatility)
                if volatility
                else "0",
                "daily_specific_volatility_contribution": text(weight * weight * d[i] / volatility)
                if volatility
                else "0",
            }
        )
    return {
        "daily_variance": text(variance),
        "daily_volatility": text(volatility),
        "annualized_volatility": text(volatility * annualization),
        "common_variance": text(common),
        "specific_variance": text(specific),
        "net_weight": text(sum(weights, ZERO)),
        "gross_weight": text(sum((abs(w) for w in weights), ZERO)),
        "factor_exposures": dict(
            zip(artifact["summary"]["factor_order"], map(text, b), strict=True)
        ),
        "common_factor_daily_volatility_contributions": {
            name: text(exposure * marginal / volatility) if volatility else "0"
            for name, exposure, marginal in zip(
                artifact["summary"]["factor_order"], b, fb, strict=True
            )
        },
        "asset_contributions": contributions,
    }


def analyze_model(artifact: dict[str, Any], query: RiskAnalyzeQuery) -> dict[str, Any]:
    weights = portfolio_weights(artifact, query.positions)
    result = {
        "model_id": query.model_id,
        "as_of": artifact["summary"]["as_of"],
        "currency": "USD",
        "portfolio": portfolio_risk(artifact, weights),
        "benchmark": None,
        "active": None,
        "scenario_common_return": None,
        "limitations": LIMITATIONS
        + [
            "Weights are fractions of NAV, never renormalized; "
            "unmodeled cash/financing has zero risk."
        ],
    }
    if query.benchmark:
        benchmark = portfolio_weights(artifact, query.benchmark)
        result["benchmark"] = portfolio_risk(artifact, benchmark)
        result["active"] = portfolio_risk(
            artifact, [w - b for w, b in zip(weights, benchmark, strict=True)]
        )
    if query.shocks:
        exposures = result["portfolio"]["factor_exposures"]
        if any(s.factor not in exposures for s in query.shocks):
            raise AtlasError("INVALID_INPUT", "Scenario references an unknown model factor.")
        result["scenario_common_return"] = text(
            sum(
                (Decimal(exposures[s.factor]) * Decimal(s.return_shock) for s in query.shocks), ZERO
            )
        )
    return result
