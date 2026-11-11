"""Decimal CPI effects and explicit bounds for rounded published inputs."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from atlas_terminal.cpi_catalog import METHOD_URL, months_between, shift_month


@dataclass(frozen=True)
class Interval:
    low: Decimal
    high: Decimal

    def __add__(self, other: "Interval") -> "Interval":
        return Interval(self.low + other.low, self.high + other.high)

    def __sub__(self, other: "Interval") -> "Interval":
        return Interval(self.low - other.high, self.high - other.low)

    def __mul__(self, other: "Interval") -> "Interval":
        parts = [a * b for a in (self.low, self.high) for b in (other.low, other.high)]
        return Interval(min(parts), max(parts))

    def __truediv__(self, other: "Interval") -> "Interval":
        if other.low <= 0:
            raise ValueError("Nonpositive denominator interval")
        return self * Interval(1 / other.high, 1 / other.low)

    def overlaps(self, other: "Interval") -> bool:
        return self.low <= other.high and other.low <= self.high

    def json(self) -> dict[str, str]:
        return {"lower": decimal_string(self.low), "upper": decimal_string(self.high)}


def decimal_string(value: Decimal) -> str:
    return format(value, "f")


def rounded(value: str) -> Interval:
    amount = Decimal(value)
    half = Decimal(1).scaleb(amount.as_tuple().exponent) / 2  # type: ignore[arg-type]
    return Interval(amount - half, amount + half)


ONE = Interval(Decimal(1), Decimal(1))
HUNDRED = Interval(Decimal(100), Decimal(100))
ZERO = Interval(Decimal(0), Decimal(0))


def reconciliation(
    values: list[str | None],
    target: str | None,
    *,
    complete: bool,
    bounds: list[Interval] | None = None,
) -> dict[str, Any]:
    if not complete or target is None or not values or any(v is None for v in values):
        return {
            "status": "not_evaluable",
            "sum_effect_pt": None,
            "residual_pt": None,
            "reason": "incomplete_decomposition_or_missing_values",
            "adjusted_to_match": False,
        }
    with localcontext() as context:
        context.prec = 50
        available = [v for v in values if v is not None]
        total = sum((Decimal(v) for v in available), Decimal(0))
        interval = ZERO
        for band in bounds or [rounded(v) for v in available]:
            interval = interval + band
        benchmark = rounded(target)
        return {
            "status": "within_input_rounding_bounds"
            if interval.overlaps(benchmark)
            else "difference_exceeds_input_rounding_bounds",
            "sum_effect_pt": decimal_string(total),
            "target_rate_pct": target,
            "residual_pt": decimal_string(Decimal(target) - total),
            "sum_bounds_pt": interval.json(),
            "target_bounds_pct": benchmark.json(),
            "rounding_basis": "Half a unit of each last printed decimal; independent bounds.",
            "reason": "Published rounding and seasonal aggregation can leave a residual.",
            "adjusted_to_match": False,
        }


def core_effect(
    month: str,
    change: str,
    category: str,
    releases: dict[str, dict[str, Any]],
    weights: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Chain monthly NSA effects, verifying both core constituents at every step.

    RI is a share of all items. Dividing item RI by core RI supplies the core
    expenditure share for that month, not a relabelled all-items table effect.
    January uses the new December-pivot vintage, independently checked against
    the annual RI table. Published rounded indexes/RI imply calculated estimates.
    """
    with localcontext() as context:
        context.prec = 50
        return _core_effect(month, change, category, releases, weights)


def _core_effect(
    month: str,
    change: str,
    category: str,
    releases: dict[str, dict[str, Any]],
    weights: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    baseline = shift_month(month, -1 if change == "mom" else -12)
    needed = months_between(shift_month(baseline, 1), month)
    result: dict[str, Any] = {
        "effect_pt": None,
        "status": "unavailable",
        "reason": None,
        "method": "monthly_relative_importance_chain",
        "method_url": METHOD_URL,
        "formula": (
            "sum_m[(RI_item,m-1 / RI_core,m-1) * (I_item,m / I_item,m-1 - 1) * 100"
            " * I_core,m-1 / I_core,start]; January RI uses the new expenditure vintage"
        ),
        "inputs": [],
        "monthly_reconciliation": [],
        "rounding_bounds_pt": None,
        "uses_unrounded_bls_production_inputs": False,
    }
    if needed[0] < "2023-01":
        result["reason"] = "computed_core_before_supported_annual_weight_schedule"
        return result
    total = Decimal(0)
    total_band = ZERO
    original_core: str | None = None
    original_indexes: dict[str, str] = {}
    previous_indexes: dict[str, str] = {}
    for current in needed:
        table = releases.get(current, {}).get("tables", {}).get(1)
        if not table:
            result["reason"] = "required_monthly_index_table_unavailable"
            result["missing_month"] = current
            return result
        previous = shift_month(current, -1)
        selected = list(
            dict.fromkeys(["core_cpi", "core_goods_cpi", "core_services_cpi", category])
        )
        inputs: dict[str, Any] = {"month": current, "weight_base_month": previous, "items": {}}
        for key in selected:
            row = table["rows"].get(key, {})
            cells = {
                "index_start": row.get("index:" + previous),
                "index_end": row.get("index:" + current),
                "relative_importance_pct": row.get("relative_importance_pct"),
            }
            if any(c is None or c["value"] is None for c in cells.values()):
                result["reason"] = "required_index_or_relative_importance_missing"
                result["missing_month"] = current
                result["missing_category"] = key
                return result
            if any(Decimal(c["value"]) <= 0 for c in cells.values()):
                result["reason"] = "nonpositive_index_or_weight"
                return result
            original_indexes.setdefault(key, cells["index_start"]["value"])
            if change == "yoy" and current == month:
                endpoint = row.get("index:" + baseline)
                if endpoint is None or endpoint["value"] is None:
                    result["reason"] = "annual_baseline_not_verified_in_final_release"
                    return result
                if Decimal(endpoint["value"]) != Decimal(original_indexes[key]):
                    result["reason"] = "annual_baseline_vintage_conflict"
                    result["conflict"] = {
                        "category": key,
                        "baseline_month": baseline,
                        "monthly_chain_baseline": original_indexes[key],
                        "final_release_baseline": endpoint,
                    }
                    return result
                cells["period_baseline_index"] = endpoint
            if key in previous_indexes and Decimal(previous_indexes[key]) != Decimal(
                cells["index_start"]["value"]
            ):
                result["reason"] = "index_vintage_conflict_across_releases"
                result["missing_month"] = current
                return result
            previous_indexes[key] = cells["index_end"]["value"]
            if current.endswith("-01"):
                annual = weights.get(int(current[:4]) - 1, {}).get(key)
                if (
                    annual is None
                    or annual["value"] is None
                    or not rounded(annual["value"]).overlaps(
                        rounded(cells["relative_importance_pct"]["value"])
                    )
                ):
                    result["reason"] = "new_january_weight_vintage_not_verified"
                    result["missing_month"] = current
                    return result
                cells["annual_new_vintage_check"] = annual
            inputs["items"][key] = cells
        core = inputs["items"]["core_cpi"]
        if original_core is None:
            original_core = core["index_start"]["value"]
        effects: dict[str, tuple[Decimal, Interval]] = {}
        for key in selected:
            item = inputs["items"][key]
            weight = item["relative_importance_pct"]["value"]
            core_weight = core["relative_importance_pct"]["value"]
            start, end = item["index_start"]["value"], item["index_end"]["value"]
            effect = (
                Decimal(weight) / Decimal(core_weight) * (Decimal(end) / Decimal(start) - 1) * 100
            )
            band = (
                rounded(weight)
                / rounded(core_weight)
                * (rounded(end) / rounded(start) - ONE)
                * HUNDRED
            )
            effects[key] = effect, band
        core_start, core_end = core["index_start"]["value"], core["index_end"]["value"]
        target_band = (rounded(core_end) / rounded(core_start) - ONE) * HUNDRED
        component_band = effects["core_goods_cpi"][1] + effects["core_services_cpi"][1]
        component_sum = effects["core_goods_cpi"][0] + effects["core_services_cpi"][0]
        target_change = (Decimal(core_end) / Decimal(core_start) - 1) * 100
        verified = target_band.overlaps(component_band)
        result["monthly_reconciliation"].append(
            {
                "month": current,
                "status": "within_input_rounding_bounds" if verified else "failed",
                "sum_effect_pt": decimal_string(component_sum),
                "target_index_change_pct": decimal_string(target_change),
                "residual_pt": decimal_string(target_change - component_sum),
                "sum_bounds_pt": component_band.json(),
                "target_bounds_pct": target_band.json(),
            }
        )
        result["inputs"].append(inputs)
        if not verified:
            result["reason"] = "core_components_do_not_reconcile_with_target_indexes"
            return result
        factor = Decimal(core_start) / Decimal(original_core)
        factor_band = rounded(core_start) / rounded(original_core)
        total += effects[category][0] * factor
        total_band = total_band + effects[category][1] * factor_band
    result.update(
        effect_pt=decimal_string(total),
        status="calculated",
        reason=None,
        rounding_bounds_pt=total_band.json(),
    )
    return result
