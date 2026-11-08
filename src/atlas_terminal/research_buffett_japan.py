"""EDINET/JPX adapter for the existing fixed-weight score engine."""

from datetime import timedelta
from pathlib import Path
from typing import Any

from atlas_terminal.buffett_models import METRICS
from atlas_terminal.edinet_history import EdinetHistory, japan_identifier, today_japan
from atlas_terminal.edinet_mappings import JAPAN_MODEL_VERSION
from atlas_terminal.edinet_normalization import MAPPING_VERSION, normalize_filing
from atlas_terminal.edinet_selection import select_annual_filings
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_document_url, edinet_list_url
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.research_buffett import cell_id
from atlas_terminal.research_financials import reference
from atlas_terminal.research_japan_companies import jpx_rows
from atlas_terminal.risk_markets import JPX33
from atlas_terminal.source_deadline import remaining_timeout, source_deadline

LIMITS = {
    "recent_calendar_days": 7,
    "daily_list_reads": 21,
    "filing_reads": 11,
    "issuer_documents": 256,
    "history_calendar_days": 12 * 366,
    "elapsed_seconds": 90,
    "in_flight_phase_seconds": 20,
    "recent_index_seconds": 10,
    "selected_index_seconds": 20,
}


class Reads:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.days: dict[str, Snapshot] = {}
        self.filings = 0
        self.list_attempts = 0
        self.failures: list[dict[str, Any]] = []
        self.stage = "recent_index"

    def get(self, url: str, ttl: int, *, day: str | None = None) -> Snapshot:
        remaining_timeout()
        if day in self.days:
            return self.days[day]
        if day is not None:
            if self.list_attempts >= LIMITS["daily_list_reads"]:
                raise AtlasError("INCOMPLETE_COVERAGE", "Daily-list read limit reached.")
            self.list_attempts += 1
        elif url != JPX_LIST:
            if self.filings >= LIMITS["filing_reads"]:
                raise AtlasError("INCOMPLETE_COVERAGE", "Filing read limit reached.")
            self.filings += 1
        snapshot = self.transport.get(url, ttl)
        remaining_timeout()
        if day is not None:
            self.days[day] = snapshot
        return snapshot

    def failure(
        self, error: AtlasError, *, day: str | None = None, document: str | None = None
    ) -> None:
        if error.code == "CONFIG_REQUIRED":
            raise error
        self.failures.append(
            {
                "date": day,
                "document_id": document,
                "code": error.code,
                "reason": error.details.get("reason", "source_read_failed"),
                "stage": self.stage,
            }
        )


def resolve_issuer(rows: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    field, expected = japan_identifier(identifier)
    issuers = {row["edinet_code"] for row in rows}
    securities = {row["security_code"] for row in rows}
    if not rows:
        return {"reason": "issuer_not_in_index", "business_type": "unspecified"}
    if len(issuers) != 1 or len(securities) != 1:
        return {"reason": "ambiguous_issuer_security_mapping", "business_type": "unspecified"}
    edinet, security = next(iter(issuers)), next(iter(securities))
    try:
        if not edinet or japan_identifier(edinet)[0] != "edinet_code":
            raise ValueError
        if not security or japan_identifier(security) != ("security_code", security):
            raise ValueError
        if any(row[field] != expected for row in rows):
            raise ValueError
    except (ValueError, AtlasError):
        return {"reason": "unsupported_issuer_security_mapping", "business_type": "unspecified"}
    return {
        "edinet_code": edinet,
        "edinet_security_code": security,
        "identifier": security[:4] + ".T",
        "business_type": "unspecified",
        "reason": None,
        "mapping_sources": [row["source"] for row in rows[:1]],
    }


def japan_financials(
    transport: Transport,
    identifier: str,
    years: int,
    directory: Path,
    *,
    synthetic: bool = False,
) -> dict[str, Any]:
    history = EdinetHistory(directory, synthetic=synthetic)
    if synthetic:
        from atlas_terminal.edinet_scoring_fixtures import EdinetScoringFixtures, seed_history

        if isinstance(transport, EdinetScoringFixtures):
            seed_history(history)
    reads = Reads(transport)
    acquired: list[str] = []
    reused: list[str] = []
    needed_dates: list[str] = []
    data: dict[str, Any] = {
        "identifier": identifier,
        "currency": "JPY",
        "business_type": "unspecified",
        "annual": [],
        "inputs": {},
        "sources": [],
        "source_id": "multiple",
        "input_origin": "edinet_primary_annual_facts",
        "history_truncated": True,
        "issuer": {},
        "filing_selection": [],
        "normalization_version": MAPPING_VERSION,
        "model_version": JAPAN_MODEL_VERSION,
    }
    with source_deadline(LIMITS["elapsed_seconds"]):
        today = today_japan()
        if not synthetic:
            with source_deadline(LIMITS["recent_index_seconds"]):
                for offset in range(LIMITS["recent_calendar_days"]):
                    day = (today - timedelta(days=offset)).isoformat()
                    try:
                        history.ingest(day, reads.get(edinet_list_url(day), 21600, day=day))
                    except AtlasError as error:
                        reads.failure(error, day=day)
                        break
        rows, truncated = history.rows(identifier)
        issuer = resolve_issuer(rows, identifier)
        data["issuer"] = issuer
        if issuer["reason"] is None:
            reads.stage = "classification"
            # Check both directions. An EDINET code must not quietly combine changing codes.
            all_rows, more = history.rows(issuer["edinet_code"])
            if resolve_issuer(all_rows, issuer["edinet_code"])["reason"] is not None:
                issuer.update(reason="ambiguous_issuer_security_mapping")
            truncated = truncated or more
        if issuer["reason"] is None:
            try:
                snapshot = reads.get(JPX_LIST, 21600)
                data["sources"].append(reference(snapshot, ""))
                matches = [
                    row for row in jpx_rows(snapshot) if row["identifier"] == issuer["identifier"]
                ]
                if len(matches) != 1 or matches[0]["industry_code"] not in JPX33:
                    issuer["reason"] = "jpx_industry_not_confirmed"
                else:
                    issuer["classification"] = matches[0]
                    if matches[0]["industry_code"] in {"7050", "7100", "7150", "7200"}:
                        issuer.update(
                            business_type="financial", reason="financial_business_not_applicable"
                        )
                    elif not matches[0]["market_product"].endswith("（内国株式）"):
                        issuer["reason"] = "domestic_common_equity_not_confirmed"
                    else:
                        issuer["business_type"] = "nonfinancial"
            except AtlasError as error:
                reads.failure(error)
                issuer["reason"] = "jpx_classification_unavailable"
        data["business_type"] = issuer["business_type"]
        if issuer["business_type"] == "nonfinancial":
            choices = select_annual_filings(rows, years)
            # Re-read selected report and ancestor dates through the bounded source cache.
            days = sorted(
                {
                    candidate["filing_date"]
                    for choice in choices
                    for candidate in choice["candidates"]
                },
                reverse=True,
            )
            failed_days = set()
            needed_dates = days
            reads.stage = "selected_index"
            if not synthetic:
                with source_deadline(LIMITS["selected_index_seconds"]):
                    for day in days:
                        try:
                            history.ingest(day, reads.get(edinet_list_url(day), 21600, day=day))
                        except AtlasError as error:
                            reads.failure(error, day=day)
                            failed_days.add(day)
            rows, more = history.rows(identifier)
            truncated = truncated or more
            refreshed_issuer = resolve_issuer(rows, identifier)
            mapping_changed = (
                refreshed_issuer.get("edinet_code") != issuer.get("edinet_code")
                or refreshed_issuer.get("identifier") != issuer.get("identifier")
                or refreshed_issuer["reason"] is not None
            )
            if mapping_changed:
                issuer.update(
                    reason="issuer_mapping_changed_on_refresh", business_type="unspecified"
                )
                data["business_type"] = "unspecified"
            choices = select_annual_filings(rows, years)
            # Resume incomplete filings first. The returned selection stays in
            # report-period order; source/document order never selects a fact.
            scheduled = sorted(
                choices,
                key=lambda choice: bool(
                    choice.get("filing") and history.normalized(choice["filing"], MAPPING_VERSION)
                ),
            )
            for choice in scheduled:
                if "period" not in choice:
                    continue
                period = choice["period"]
                filing = choice["filing"]
                problem = choice["reason"] if filing is None else None
                if any(row["filing_date"] in failed_days for row in choice["candidates"]):
                    problem = "selected_filing_date_refresh_failed"
                if truncated:
                    problem = "issuer_document_limit_reached"
                if mapping_changed:
                    problem = "issuer_mapping_changed_on_refresh"
                normalized = None
                if problem is None:
                    try:
                        reads.stage = "filing_download"
                        snapshot = reads.get(edinet_document_url(filing["document_id"]), 21600)
                        reads.stage = "filing_normalization"
                        normalized = history.normalized(filing, MAPPING_VERSION, snapshot)
                        if normalized is None:
                            normalized = normalize_filing(snapshot, filing)
                            history.save_normalized(snapshot, filing, MAPPING_VERSION, normalized)
                        else:
                            reused.append(period["end"])
                        acquired.append(period["end"])
                        data["sources"].append(reference(snapshot, ""))
                        choice["normalization"] = {
                            key: value
                            for key, value in normalized.items()
                            if key not in {"cells", "opening_equity"}
                        }
                    except AtlasError as error:
                        reads.failure(error, document=filing["document_id"])
                        problem = "selected_filing_read_failed"
                choice["evaluation_reason"] = problem or (normalized and normalized["reason"])
                choice["retrieval_stage"] = "normalized" if normalized else reads.stage
                available = max(
                    (row["filed_at"][:10] for row in choice["candidates"] if row["filed_at"]),
                    default=period["end"],
                )
                if filing is not None:
                    available = filing["filed_at"][:10]
                annual = {**period, "available_on": available}
                if filing is not None:
                    annual["available_at"] = filing["filed_at"]
                data["annual"].append(annual)
                for metric in METRICS:
                    data["inputs"][cell_id(period["end"], metric)] = (
                        normalized["cells"][metric]
                        if normalized
                        else {
                            "metric": metric,
                            "value": None,
                            "unit": "JPY",
                            "status": "unavailable",
                            "period": period,
                            "reason": problem,
                            "source": None,
                        }
                    )
                if normalized:
                    opening_id = cell_id(period["end"], "opening_stockholders_equity")
                    annual["opening_equity_input_id"] = opening_id
                    data["inputs"][opening_id] = normalized["opening_equity"]
            data["filing_selection"] = choices
        data["sources"].extend(row["source"] for row in rows)
    data["annual"].sort(key=lambda row: row["end"], reverse=True)
    data["history_truncated"] = truncated or len(data["annual"]) < years
    missing = [row["end"] for row in data["annual"] if row["end"] not in acquired]
    pending = [
        choice
        for choice in data["filing_selection"]
        if choice.get("period", {}).get("end") in missing
    ]
    data["source_coverage"] = {
        "limits": LIMITS.copy(),
        "daily_list_reads": reads.list_attempts,
        "filing_reads": reads.filings,
        "failures": reads.failures,
        "history": history.coverage(),
        "issuer_document_limit_reached": truncated,
        "required_opening_equity": "day before each selected annual start, in the same filing",
        "progress": {
            "required_submission_dates": needed_dates,
            "acquired_periods": sorted(acquired),
            "reused_normalized_periods": sorted(reused),
            "missing_periods": sorted(missing),
            "unlocated_annual_periods": max(0, years - len(data["annual"])),
            "failed_stages": sorted({failure["stage"] for failure in reads.failures}),
            "resume": {
                "action": "repeat_same_query"
                if pending
                else "sync_missing_index_dates"
                if len(data["annual"]) < years
                else "none",
                "document_id": pending[0].get("selected_document_id") if pending else None,
                "period": pending[0].get("period") if pending else None,
                "submission_dates": sorted(
                    {failure["date"] for failure in reads.failures if failure["date"]}
                ),
            },
            "financial_inputs_complete": bool(data["annual"])
            and len(data["annual"]) == years
            and all(cell.get("value") is not None for cell in data["inputs"].values()),
            "amendment_search_complete": False,
        },
    }
    return data
