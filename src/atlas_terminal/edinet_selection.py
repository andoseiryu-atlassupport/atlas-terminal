"""Annual-report and amendment lineage selection within a bounded observed index."""

import re
from datetime import date
from typing import Any


def select_annual_filings(rows: list[dict[str, Any]], years: int) -> list[dict[str, Any]]:
    by_id = {row["document_id"]: row for row in rows}
    if len(by_id) != len(rows):
        return [
            {
                "selected_document_id": None,
                "reason": "duplicate_document_identity",
                "candidates": rows,
            }
        ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    rejected: list[dict[str, Any]] = []

    def root_of(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        current = row
        seen: set[str] = set()
        while current["document_type"] == "130":
            if current["document_id"] in seen or len(seen) >= 32:
                return None, "cyclic_or_excessive_amendment_chain"
            seen.add(current["document_id"])
            parent = by_id.get(current["parent_document_id"])
            if parent is None:
                return None, "amendment_parent_not_indexed"
            if (
                parent["edinet_code"] != row["edinet_code"]
                or parent["security_code"] != row["security_code"]
                or not parent["filed_at"]
                or not current["filed_at"]
                or parent["filed_at"] >= current["filed_at"]
            ):
                return None, "amendment_parent_identity_or_time_mismatch"
            if parent["withdrawal_status"] != "0" or parent["disclosure_status"] != "0":
                return None, "annual_root_withdrawn_or_unavailable"
            current = parent
        return (
            (current, None)
            if current["document_type"] == "120"
            else (None, "parent_not_annual_report")
        )

    for row in rows:
        root, problem = root_of(row)
        if root is not None:
            start, end = root["report_period_start"], root["report_period_end"]
            if row["report_period_start"] not in {None, start} or row["report_period_end"] not in {
                None,
                end,
            }:
                problem = "amendment_period_mismatch"
        else:
            start, end = row["report_period_start"], row["report_period_end"]
        try:
            if not all(
                isinstance(v, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v)
                for v in (start, end)
            ):
                raise ValueError
            if date.fromisoformat(start) > date.fromisoformat(end):
                raise ValueError
        except (ValueError, TypeError):
            rejected.append(
                {
                    "selected_document_id": None,
                    "reason": problem or "missing_or_invalid_report_period",
                    "candidates": [row],
                }
            )
            continue
        candidate = row | {
            "root_document_id": root["document_id"] if root else None,
            "selection_problem": problem,
        }
        grouped.setdefault((start, end), []).append(candidate)
    selected: list[dict[str, Any]] = []
    end_counts = {end: sum(period[1] == end for period in grouped) for _, end in grouped}
    for (start, end), candidates in sorted(
        grouped.items(), key=lambda pair: pair[0][1], reverse=True
    )[:years]:
        active = [
            row
            for row in candidates
            if row["withdrawal_status"] == "0" and row["disclosure_status"] == "0"
        ]
        roots = {row["root_document_id"] for row in active}
        chosen = max(active, key=lambda row: row["filed_at"] or "", default=None)
        problem = None
        if any(row["selection_problem"] for row in active):
            problem = next(row["selection_problem"] for row in active if row["selection_problem"])
        elif len(roots) > 1:
            problem = "multiple_annual_report_roots"
        elif not chosen:
            problem = "withdrawn_or_undisclosed_reports_only"
        elif (
            not chosen["filed_at"]
            or sum(row["filed_at"] == chosen["filed_at"] for row in active) != 1
        ):
            problem = "ambiguous_submission_time"
        elif not chosen["xbrl_available"]:
            problem = "latest_report_has_no_xbrl"
        elif chosen["filed_at"][:10] < end:
            problem = "report_period_after_submission"
        elif (
            by_id[chosen["root_document_id"]]["withdrawal_status"] != "0"
            or by_id[chosen["root_document_id"]]["disclosure_status"] != "0"
        ):
            problem = "annual_root_withdrawn_or_unavailable"
        active_rejected = [
            item
            for item in rejected
            if any(
                row["withdrawal_status"] == "0" and row["disclosure_status"] == "0"
                for row in item["candidates"]
            )
        ]
        if active_rejected:
            problem = (
                "unresolved_amendment_without_period"
                if any(
                    item["reason"].startswith(("amendment_", "cyclic_")) for item in active_rejected
                )
                else "unresolved_annual_report_period"
            )
        if end_counts[end] > 1:
            problem = "conflicting_report_periods_for_same_end"
        selected.append(
            {
                "period": {"start": start, "end": end},
                "selected_document_id": chosen["document_id"] if chosen and not problem else None,
                "reason": problem
                or (
                    "latest_linked_amendment"
                    if chosen and chosen["document_type"] == "130"
                    else "annual_securities_report"
                ),
                "candidates": candidates,
                "filing": chosen | {"report_period_start": start, "report_period_end": end}
                if chosen and not problem
                else None,
                "amendment_values_overlay": False,
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for item in selected:
        end = item["period"]["end"]
        if end in unique:
            unique[end]["candidates"].extend(item["candidates"])
        else:
            unique[end] = item
    return list(unique.values()) + rejected
