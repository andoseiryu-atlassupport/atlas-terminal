"""One bounded CPI effects application service for SDK, CLI, MCP and HTTP."""

from decimal import Decimal, localcontext
from typing import Any

from atlas_terminal.cpi_calculations import Interval, core_effect, decimal_string, reconciliation
from atlas_terminal.cpi_catalog import (
    ARCHIVE_URL,
    CATEGORIES,
    COMBINATIONS,
    DEADLINE_SECONDS,
    MAX_DOCUMENTS,
    SETS,
    VERSION,
    months_between,
    period_end,
    shift_month,
    stacking,
)
from atlas_terminal.cpi_sources import annual_weights, archive_releases, reference, release_tables
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.source_deadline import remaining_timeout, source_deadline
from atlas_terminal.util import canonical, digest

LIMITATIONS = [
    "Dated HTML as retrieved; original publication-time fidelity is unverified.",
    "SA history can differ from revised BLS API indexes; no cross-vintage replacement.",
    "Effects are percentage points; BLS contribution shares are percentages of aggregate change.",
    "Published relative importance always refers to all-items CPI, even for a core-target query.",
    "Core NSA estimates use printed indexes and monthly RI, not unrounded production inputs.",
    "January vintage checks and component reconciliation must pass before returning core effects.",
    "Missing or unsupported observations are not zero or an 'other' component.",
    "Archive links do not guarantee that Tables 1, 6 and 7 exist in every edition.",
    "Only dated HTML is parsed; supplemental XLSX/ZIP and PDF-only editions are unsupported.",
]


class CPIReader:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.snapshots: dict[str, Snapshot] = {}
        self.failures: list[dict[str, Any]] = []
        self.attempts = 0

    def read(self, url: str, stage: str, month: str | None = None) -> Snapshot | None:
        if url in self.snapshots:
            return self.snapshots[url]
        reason = None
        if self.attempts >= MAX_DOCUMENTS:
            reason = "document_limit_reached"
        elif len(self.failures) >= 3:
            reason = "source_failure_budget_reached"
        try:
            if reason:
                raise AtlasError("SOURCE_UNAVAILABLE", reason, details={"reason": reason})
            remaining_timeout()
            self.attempts += 1
            snapshot = self.transport.get(url, 21600 if stage == "archive" else 86400)
            self.snapshots[url] = snapshot
            return snapshot
        except AtlasError as exc:
            self.failures.append(
                {
                    "stage": stage,
                    "month": month,
                    "source_url": url,
                    "code": exc.code,
                    "reason": exc.details.get("reason", exc.code),
                }
            )
            return None

    def parse_failure(self, exc: AtlasError, url: str, stage: str, month: str | None) -> None:
        self.failures.append(
            {
                "stage": stage,
                "month": month,
                "source_url": url,
                "code": exc.code,
                "reason": exc.details.get("reason", exc.code),
            }
        )


def _missing(reason: str) -> dict[str, Any]:
    return {"value": None, "raw_value": None, "source": None, "reason": reason}


def _component(
    category: str,
    table: dict[str, Any] | None,
    month: str,
    q: ResearchQuery,
    releases: dict[str, dict[str, Any]],
    weights: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    row = (table or {}).get("rows", {}).get(category, {})
    missing_reason = (
        "category_not_in_supported_table" if table else "required_release_table_unavailable"
    )
    rate_key = "rate_pct" if q.target == "all_items" else q.change + "_rate"
    rate = row.get(rate_key, _missing(missing_reason))
    effect = row.get("effect_pt", _missing(missing_reason))
    weight = row.get("relative_importance_pct", _missing(missing_reason))
    rate_reason = rate["reason"]
    rate_value = rate["value"]
    if q.target == "all_items" and row.get("rate_adjustment") != q.seasonal_adjustment:
        rate_value = None
        rate_reason = "source_item_rate_is_not_seasonally_adjusted" if row else missing_reason
    calculation = None
    status = "reported" if effect["value"] is not None else "unavailable"
    effect_value, effect_reason = effect["value"], effect["reason"]
    if q.target == "core":
        if category not in {
            "core_cpi",
            *SETS["core_basic"],
            "shelter_cpi",
            "rent_cpi",
            "owners_equivalent_rent_cpi",
            "education_cpi",
        }:
            effect_value, effect_reason = None, "category_outside_target"
        else:
            calculation = core_effect(month, q.change, category, releases, weights)
            effect_value, effect_reason = calculation["effect_pt"], calculation["reason"]
            status = calculation["status"]
    spec = CATEGORIES[category]
    return {
        "month": month,
        "category": category,
        "title": spec["label"],
        "bls_item_code": spec["code"],
        "parent": spec["parent"],
        "nsa_series_id": "CUUR0000" + spec["code"],
        "target": q.target,
        "change": q.change,
        "seasonal_adjustment": q.seasonal_adjustment,
        "rate_pct": rate_value,
        "effect_pt": effect_value,
        "relative_importance_pct": weight["value"],
        "weight_base_month": (table or {}).get("weight_base_month"),
        "weight_target": "all_items",
        "weight_vintage_status": "reported_in_monthly_release",
        "effect_status": status,
        "rate_status": "reported" if rate_value is not None else "unavailable",
        "weight_status": "reported" if weight["value"] is not None else "unavailable",
        "value": effect_value,
        "unit": "percentage_points",
        "status": status,
        "reason": effect_reason,
        "missing_reasons": {
            "rate_pct": rate_reason,
            "effect_pt": effect_reason,
            "relative_importance_pct": weight["reason"],
        },
        "reported_inputs": {
            "rate": rate,
            "relative_importance": weight,
            **({"effect": effect} if q.target == "all_items" else {}),
        },
        "field_sources": {
            "rate_pct": rate["source"]
            if rate_reason != "source_item_rate_is_not_seasonally_adjusted"
            else None,
            "effect_pt": effect["source"] if q.target == "all_items" else None,
            "relative_importance_pct": weight["source"],
        },
        "calculation": calculation,
        "source_rate_adjustment": row.get("rate_adjustment"),
        "contribution_share_pct": None,
        "contribution_share_status": "unavailable",
    }


def cpi_effects(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    with source_deadline(DEADLINE_SECONDS), localcontext() as context:
        context.prec = 50
        return _cpi_effects(transport, q)


def _cpi_effects(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    categories = (
        q.categories
        or SETS[q.category_set or ("all_items_basic" if q.target == "all_items" else "core_basic")]
    )
    classification = stacking(categories, q.target)
    supported = any(
        all(combo[key] == getattr(q, key) for key in ("target", "change", "seasonal_adjustment"))
        for combo in COMBINATIONS
    )
    result: dict[str, Any] = {
        "source_id": "bls",
        "mapping_version": VERSION,
        "status": "unavailable",
        "records": [],
        "sources": [],
        "classification": classification,
        "stacking_sets": SETS,
        "calculation_inputs": {},
        "calculation_checks": {},
        "limitations": list(LIMITATIONS),
        "coverage": {
            "requested_start_month": q.start_month,
            "requested_end_month": q.end_month,
            "advertised_first_month": None,
            "advertised_last_month": None,
            "retrieved_first_month": None,
            "retrieved_last_month": None,
            "available_effect_first_month": None,
            "available_effect_last_month": None,
            "missing_effect_months": [],
            "missing_weight_months": [],
            "missing_rate_months": [],
            "unretrieved_months": [],
            "months_not_linked_as_html": [],
            "failures": [],
            "returned_records": 0,
            "truncated": False,
            "vintage_policy": "dated_archive_edition_as_retrieved",
            "limits": {"documents": MAX_DOCUMENTS, "seconds": DEADLINE_SECONDS},
        },
    }
    if not supported:
        result.update(
            status="unsupported", reason="unsupported_target_change_adjustment_combination"
        )
        return result
    reader = CPIReader(transport)
    coverage = result["coverage"]
    archive: dict[str, dict[str, Any]] = {}
    index = reader.read(ARCHIVE_URL, "archive")
    if index is not None:
        try:
            archive = archive_releases(index)
        except AtlasError as exc:
            reader.parse_failure(exc, ARCHIVE_URL, "parse_archive", None)
    if archive:
        coverage.update(advertised_first_month=min(archive), advertised_last_month=max(archive))
    requested = (
        months_between(q.start_month, q.end_month)
        if q.start_month and q.end_month
        else ([max(archive)] if archive else [])
    )
    needed = set(requested)
    if q.target == "core" and q.change == "yoy" and requested:
        needed.update(months_between(shift_month(requested[0], -11), requested[-1]))
    releases: dict[str, dict[str, Any]] = {}
    weights: dict[int, dict[str, Any]] = {}
    for month in sorted(needed):
        if month not in archive:
            coverage["months_not_linked_as_html" if archive else "unretrieved_months"].append(month)
            continue
        entry = archive[month]
        snapshot = reader.read(entry["url"], "release", month)
        if snapshot is not None:
            try:
                releases[month] = release_tables(snapshot, month, entry["published_at"])
            except AtlasError as exc:
                reader.parse_failure(exc, entry["url"], "parse_release", month)
        if month not in releases:
            coverage["unretrieved_months"].append(month)
    if q.target == "core":
        for year in sorted(
            {int(m[:4]) - 1 for m in needed if m.endswith("-01") and m >= "2023-01"}
        ):
            url = f"https://www.bls.gov/cpi/tables/relative-importance/{year}.htm"
            snapshot = reader.read(url, "annual_weights", f"{year}-12")
            if snapshot is not None:
                try:
                    weights[year] = annual_weights(snapshot, year)
                except AtlasError as exc:
                    reader.parse_failure(exc, url, "parse_annual_weights", f"{year}-12")
    table_number = 1 if q.target == "core" else (6 if q.change == "mom" else 7)
    for month in requested:
        table = releases.get(month, {}).get("tables", {}).get(table_number)
        rows = [_component(key, table, month, q, releases, weights) for key in categories]
        # Shared monthly operands and checks stay source-addressable without copying
        # twelve months of identical evidence into every component of every year.
        for row in rows:
            calculation = row["calculation"]
            if calculation is None:
                continue
            for step in calculation["inputs"]:
                for key, cells in step["items"].items():
                    identity = digest(canonical(cells))
                    result["calculation_inputs"][identity] = cells
                    step["items"][key] = {"input_id": identity}
            checks = []
            for check in calculation["monthly_reconciliation"]:
                identity = digest(canonical(check))
                result["calculation_checks"][identity] = check
                checks.append({"check_id": identity})
            calculation["monthly_reconciliation"] = checks
        root = "cpi" if q.target == "all_items" else "core_cpi"
        target = (
            (table or {})
            .get("rows", {})
            .get(root, {})
            .get(
                "rate_pct" if q.target == "all_items" else q.change + "_rate",
                _missing("target_rate_unavailable"),
            )
        )
        for row in rows:
            if row["effect_pt"] is not None and target["value"] is not None:
                if Decimal(target["value"]) == 0:
                    row["contribution_share_reason"] = "target_change_is_zero"
                else:
                    row["contribution_share_pct"] = decimal_string(
                        Decimal(row["effect_pt"]) / Decimal(target["value"]) * 100
                    )
                    row["contribution_share_status"] = "calculated_from_displayed_values"
        bands = None
        if q.target == "core" and all(row["effect_pt"] is not None for row in rows):
            bands = [
                Interval(
                    Decimal(row["calculation"]["rounding_bounds_pt"]["lower"]),
                    Decimal(row["calculation"]["rounding_bounds_pt"]["upper"]),
                )
                for row in rows
            ]
        check = reconciliation(
            [row["effect_pt"] for row in rows],
            target["value"],
            complete=classification["covers_target"],
            bounds=bands,
        )
        missing = [row["category"] for row in rows if row["effect_pt"] is None]
        if missing or target["value"] is None:
            coverage["missing_effect_months"].append(month)
        if any(row["relative_importance_pct"] is None for row in rows):
            coverage["missing_weight_months"].append(month)
        if target["value"] is None or any(row["rate_pct"] is None for row in rows):
            coverage["missing_rate_months"].append(month)
        result["records"].append(
            {
                "month": month,
                "date": period_end(month),
                "country": "US",
                "frequency": "monthly",
                "date_basis": "observation_period_end",
                "population": "CPI-U",
                "area": "U.S. city average",
                "target": q.target,
                "change": q.change,
                "seasonal_adjustment": q.seasonal_adjustment,
                "rate_pct": target["value"],
                "value": target["value"],
                "unit": "percent",
                "target_rate_status": "reported" if target["value"] is not None else "unavailable",
                "source": target["source"],
                "published_at": archive.get(month, {}).get("published_at"),
                "records": rows,
                "reconciliation": check,
                "stacking": {
                    **classification,
                    "missing_categories": missing,
                    "value_reasons": {r["category"]: r["reason"] for r in rows if r["reason"]},
                    "ready": classification["classification_stackable"] and not missing,
                    "complete": classification["covers_target"] and not missing,
                },
            }
        )
    result["sources"] = [reference(s, {}, table="document") for s in reader.snapshots.values()]
    if releases:
        coverage.update(retrieved_first_month=min(releases), retrieved_last_month=max(releases))
    available = [m for m in requested if m not in coverage["missing_effect_months"]]
    if available:
        coverage.update(
            available_effect_first_month=min(available), available_effect_last_month=max(available)
        )
    coverage.update(
        failures=reader.failures,
        document_requests=reader.attempts,
        returned_records=len(result["records"]),
        requested_months=requested,
        dependency_months=sorted(needed - set(requested)),
        retrieved_months=sorted(releases),
        unavailable_records=len(coverage["missing_effect_months"]),
    )
    any_effect = any(
        c["effect_pt"] is not None for row in result["records"] for c in row["records"]
    )
    result["status"] = (
        "available"
        if requested
        and len(available) == len(requested)
        and not coverage["missing_weight_months"]
        and not coverage["missing_rate_months"]
        else ("partial" if any_effect else "unavailable")
    )
    if not archive:
        result["reason"] = "archive_discovery_unavailable"
    return result
