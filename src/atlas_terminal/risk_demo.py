"""Deterministic fictional observations for exercising the complete risk workflow."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from typing import Any

from atlas_terminal.client import Atlas
from atlas_terminal.risk_connections import NativeRiskConnector, RiskConnectionSpec
from atlas_terminal.risk_engine import RiskPanel, exposures
from atlas_terminal.risk_math import ZERO, dot, text
from atlas_terminal.risk_models import RiskMethod, RiskPanelInput
from atlas_terminal.util import canonical, digest, stamp


def fictional_panel(sessions: int = 85, assets: int = 60) -> dict[str, Any]:
    """Simulate observations, never a market-data fixture or forecast-quality benchmark."""
    rng = Random(402)
    ids = [f"USDEMO{i:03d}" for i in range(assets)]
    start = datetime(2024, 1, 2, 21, tzinfo=UTC)
    caps = {asset: Decimal(rng.randrange(200, 2000)) * 1000000 for asset in ids}
    fundamentals = []
    for i, asset in enumerate(ids):
        total_assets = Decimal(rng.randrange(100, 3000)) * 1000000
        fundamentals.append(
            {
                "asset_id": asset,
                "period_end": "2023-12-01T21:00:00Z",
                "available_at": "2024-01-01T21:00:00Z",
                "source_id": "fictional",
                "locator": f"fundamentals/{asset}",
                "sic": ("1311", "3571", "6021", "7372")[i % 4],
                "book_equity": text(caps[asset] * Decimal(rng.randrange(10, 180)) / 100),
                "total_assets": text(total_assets),
                "net_income_ttm": text(total_assets * Decimal(rng.randrange(-5, 30)) / 100),
                "total_debt": text(total_assets * Decimal(rng.randrange(2, 80)) / 100),
            }
        )
    data: dict[str, Any] = {
        "manifest": {
            "kind": "manifest",
            "is_synthetic": True,
            "return_convention": "split_and_dividend_adjusted_simple_total_return",
            "universe": [{"asset_id": asset} for asset in ids],
            "sources": [
                {
                    "source_id": "fictional",
                    "uri": "https://example.invalid/fictional-risk-observations",
                    "raw_sha256": digest(
                        canonical({"generator": "atlas-risk-demo-v1", "seed": 402})
                    ),
                    "retrieved_at": "2024-01-01T21:00:00Z",
                    "usage_basis": "operator_supplied_rights_cleared",
                }
            ],
        },
        "fundamentals": fundamentals,
        "sessions": [],
    }
    design = None
    with localcontext() as context:
        context.prec = 42
        for t in range(sessions):
            end = start + timedelta(days=1)
            while end.weekday() >= 5:
                end += timedelta(days=1)
            if design is None:
                values = {asset: "0.001" for asset in ids}
            else:
                factors = [Decimal(rng.randrange(-8000, 8001)) / 1000000]
                groups = sorted(set(design.industries))
                group_caps = {
                    g: sum(
                        (
                            c
                            for c, group in zip(design.caps, design.industries, strict=True)
                            if g == group
                        ),
                        ZERO,
                    )
                    for g in groups
                }
                group_returns = {
                    g: Decimal(rng.randrange(-3000, 3001)) / 1000000 for g in groups[:-1]
                }
                group_returns[groups[-1]] = (
                    -sum((group_caps[g] * group_returns[g] for g in groups[:-1]), ZERO)
                    / group_caps[groups[-1]]
                )
                factors += [group_returns[g] for g in groups]
                factors += [Decimal(rng.randrange(-2000, 2001)) / 1000000 for _ in range(4)]
                values = {
                    asset: format(
                        dot(row, factors) + Decimal(rng.randrange(-1500, 1501)) / 1000000, ".12f"
                    )
                    for asset, row in zip(ids, design.rows, strict=True)
                }
            data["sessions"].append(
                {
                    "start_at": stamp(start),
                    "end_at": stamp(end),
                    "available_at": stamp(end),
                    "source_id": "fictional",
                    "locator": f"sessions/{t}",
                    "observations": [
                        {
                            "asset_id": asset,
                            "total_return": values[asset],
                            "market_cap": text(caps[asset]),
                        }
                        for asset in ids
                    ],
                }
            )
            if design is None:
                parsed = RiskPanelInput.model_validate_json(canonical(data))
                panel = RiskPanel(parsed.manifest, parsed.fundamentals, parsed.sessions)
                design = exposures(panel, end, ["size", "value", "profitability", "leverage"])
            start = end
    return data


def fictional_time_series_panel(sessions: int = 85) -> dict[str, Any]:
    rng = Random(614)
    start = datetime(2024, 1, 2, 21, tzinfo=UTC)
    assets = ["TSDEMO0", "TSDEMO1", "TSDEMO2"]
    factors = ["market", "quality", "value"]
    betas = [
        [Decimal(v) for v in row]
        for row in (("1.2", "-0.4", "0.7"), ("0.8", "0.5", "-0.2"), ("1", "0", "0"))
    ]
    source = {
        "source_id": "fictional",
        "uri": "https://example.invalid/fictional-observed-factors",
        "raw_sha256": digest(canonical({"generator": "atlas-time-series-demo-v1", "seed": 614})),
        "retrieved_at": "2024-01-01T21:00:00Z",
        "usage_basis": "operator_supplied_rights_cleared",
    }
    data: dict[str, Any] = {
        "manifest": {
            "kind": "time_series_manifest",
            "is_synthetic": True,
            "universe": [
                {"asset_id": a, "currency": "USD", "security_type": "etf"} for a in assets
            ],
            "factors": factors,
            "sources": [source],
            "currency": "USD",
            "return_convention": "split_and_dividend_adjusted_simple_total_return",
            "factor_convention": "total_return",
            "return_unit": "decimal_return",
        },
        "sessions": [],
    }
    with localcontext() as context:
        context.prec = 42
        for i in range(sessions):
            end = start + timedelta(days=1)
            while end.weekday() >= 5:
                end += timedelta(days=1)
            values = [Decimal(rng.randrange(-10000, 10001)) / 1000000 for _ in factors]
            data["sessions"].append(
                {
                    "start_at": stamp(start),
                    "end_at": stamp(end),
                    "available_at": stamp(end),
                    "source_id": "fictional",
                    "locator": f"assets/{i}",
                    "factor_source_id": "fictional",
                    "factor_locator": f"factors/{i}",
                    "factor_returns": dict(zip(factors, map(text, values), strict=True)),
                    "observations": [
                        {
                            "asset_id": asset,
                            "total_return": format(
                                Decimal("0.0001")
                                + dot(beta, values)
                                + Decimal(rng.randrange(-250, 251)) / 1000000,
                                ".12f",
                            ),
                        }
                        for asset, beta in zip(assets, betas, strict=True)
                    ],
                }
            )
            start = end
    return data


def run_demo(atlas: Atlas, method: RiskMethod = "cross_sectional") -> dict[str, Any]:
    data = fictional_panel() if method == "cross_sectional" else fictional_time_series_panel()
    if method == "cross_sectional":
        positions = [
            {"asset_id": a["asset_id"], "weight": "0.05"} for a in data["manifest"]["universe"][:20]
        ]
        benchmark = [
            {"asset_id": a["asset_id"], "weight": "0.10"}
            for a in data["manifest"]["universe"][20:30]
        ]
    else:
        positions = [
            {"asset_id": "TSDEMO0", "weight": "0.6"},
            {"asset_id": "TSDEMO1", "weight": "0.4"},
        ]
        benchmark = [{"asset_id": "TSDEMO2", "weight": "1"}]
    with TemporaryDirectory(prefix="atlas-risk-demo-") as temporary:
        path = Path(temporary) / "panel.json"
        path.write_bytes(canonical(data))
        connection_id = "fictional_" + method
        atlas.register_risk_connection(
            connection_id,
            NativeRiskConnector(
                RiskConnectionSpec(
                    connection_id=connection_id,
                    kind="json",
                    method=method,
                    path=str(path),
                )
            ),
        )
        executed = atlas.risk(
            {
                "action": "run",
                "connection_id": connection_id,
                "fit": {"method": method, "as_of": data["sessions"][64]["end_at"]},
                "positions": positions,
                "benchmark": benchmark,
                "shocks": [{"factor": "market", "return_shock": "-0.05"}],
            }
        )
    fitted = executed | {"action": "fit", "data": executed["data"]["model"]}
    analyzed = executed | {"action": "analyze", "data": executed["data"]["analysis"]}
    model_id = fitted["data"]["model_id"]
    return {
        "is_synthetic": True,
        "model": fitted,
        "analysis": analyzed,
        "connection_execution": {
            "plan": executed["data"]["plan"],
            "input_evidence": executed["data"]["input_evidence"],
        },
        "validation": atlas.risk(
            {
                "action": "validate",
                "model_id": model_id,
                "head_id": fitted["data"]["input_head_id"],
                "positions": positions,
            }
        ),
    }
