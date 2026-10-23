"""Deterministic fictional observations for exercising the complete risk workflow."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from random import Random
from typing import Any

from atlas_terminal.client import Atlas
from atlas_terminal.risk_engine import RiskPanel, exposures
from atlas_terminal.risk_math import ZERO, dot, text
from atlas_terminal.risk_models import RiskPanelInput
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


def run_demo(atlas: Atlas) -> dict[str, Any]:
    data = fictional_panel()
    receipt = atlas.import_risk_panel(data)
    fitted = atlas.risk(
        {"action": "fit", "head_id": receipt["head_id"], "as_of": data["sessions"][64]["end_at"]}
    )
    model_id = fitted["data"]["model_id"]
    positions = [
        {"asset_id": asset["asset_id"], "weight": "0.05"}
        for asset in data["manifest"]["universe"][:20]
    ]
    benchmark = [
        {"asset_id": asset["asset_id"], "weight": "0.10"}
        for asset in data["manifest"]["universe"][20:30]
    ]
    return {
        "is_synthetic": True,
        "model": fitted,
        "analysis": atlas.risk(
            {
                "action": "analyze",
                "model_id": model_id,
                "positions": positions,
                "benchmark": benchmark,
                "shocks": [{"factor": "market", "return_shock": "-0.05"}],
            }
        ),
        "validation": atlas.risk(
            {
                "action": "validate",
                "model_id": model_id,
                "head_id": receipt["head_id"],
                "positions": positions,
            }
        ),
    }
