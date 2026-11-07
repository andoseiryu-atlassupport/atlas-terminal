"""Reported Japanese margin balances, with share-based ratios and exact PDF cells."""

import json
import re
import subprocess
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from atlas_terminal.errors import AtlasError
from atlas_terminal.margin_pdf import MAX_OUTPUT_BYTES, MAX_PAGES, MAX_TEXT_CHARS
from atlas_terminal.margin_sources import (
    MARGIN_INDEX,
    MAX_PDF_BYTES,
    margin_identity,
    margin_reports,
)
from atlas_terminal.providers import Snapshot, Transport

if TYPE_CHECKING:
    from atlas_terminal.research_models import ResearchQuery

TOKYO = ZoneInfo("Asia/Tokyo")
DATE = r"(20[0-9]{2})/([0-9]{1,2})/([0-9]{1,2})"
ISSUE = re.compile(
    r"(?P<code>[0-9][0-9A-Z]{4})\s+(?P<isin>[A-Z]{2}[A-Z0-9]{9}[0-9])\s*"
    r"(?:(?:株数\s*)?(?P<shares>Shs\.)|(?:金\s*額\s*)?(?P<amount>Val\.))\s+"
)
FIELDS = [
    "sell_balance",
    "sell_change",
    "sell_listed_share_pct",
    "buy_balance",
    "buy_change",
    "buy_listed_share_pct",
    "general_sell_balance",
    "general_sell_change",
    "standardized_sell_balance",
    "standardized_sell_change",
    "general_buy_balance",
    "general_buy_change",
    "standardized_buy_balance",
    "standardized_buy_change",
]
COLUMN_HEADINGS = [
    "Outstanding Sales",
    "Daily change",
    "Ratio to",
    "Outstanding",
    "Daily change",
    "Ratio to",
    "Negotiable",
    "Daily change",
    "Standardized",
    "Daily change",
    "Negotiable",
    "Daily change",
    "Standardized",
    "Daily change",
]


def source_error(message: str) -> AtlasError:
    return AtlasError("SOURCE_UNAVAILABLE", message, status=502)


@lru_cache(maxsize=2)
def extract_margin_pages(body: bytes) -> list[dict[str, Any]]:
    if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF-"):
        raise source_error("JPX margin PDF exceeds the document contract.")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "atlas_terminal.margin_pdf"],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=195,
            check=True,
        )
        if len(result.stdout) > MAX_OUTPUT_BYTES:
            raise ValueError("Output bound exceeded")
        pages = json.loads(result.stdout)
        if (
            not isinstance(pages, list)
            or not 1 <= len(pages) <= MAX_PAGES
            or any(
                not isinstance(page, dict)
                or page.get("page") != index + 1
                or not isinstance(page.get("text"), str)
                for index, page in enumerate(pages)
            )
            or sum(len(page["text"]) for page in pages) > MAX_TEXT_CHARS
        ):
            raise ValueError("Invalid worker output")
        return pages
    except (OSError, subprocess.SubprocessError, ValueError):
        raise source_error("JPX margin PDF layout extraction failed its resource bounds.") from None


def evidence(snapshot: Snapshot, **locator: Any) -> dict[str, Any]:
    return {
        "raw_source_url": snapshot.url,
        "raw_sha256": snapshot.sha256,
        "retrieved_at": snapshot.retrieved_at,
        **locator,
    }


def header_dates(text: str, expected: str, retrieved_at: str) -> tuple[str, int]:
    lines = text.splitlines()[:18]
    header = "\n".join(lines)
    if not all(
        label in header
        for label in (
            "Outstanding margin trading by issue",
            "Unit: 1 share, 1 JPY",
            "Outstanding Sales",
            "Outstanding Purchases",
            "Negotiable",
            "Standardized",
        )
    ):
        raise source_error("JPX margin table title, units or column headings changed.")
    column_lines = [re.split(r" {2,}", line.strip()) for line in lines if "New Sec. Code" in line]
    if len(column_lines) != 1 or column_lines[0][-14:] != COLUMN_HEADINGS:
        raise source_error("JPX margin numeric column order differs from the supported contract.")
    applications = re.findall(r"As of " + DATE + r" application based", header)
    pairs = [(index, re.findall(DATE, line)) for index, line in enumerate(lines, 1)]
    pairs = [(index, values) for index, values in pairs if len(values) == 2]
    try:
        if len(applications) != 1 or len(pairs) != 1:
            raise ValueError("Ambiguous dates")
        line, values = pairs[0]
        application = date(*(int(part) for part in applications[0])).isoformat()
        observed = date(*(int(part) for part in values[0])).isoformat()
        published = date(*(int(part) for part in values[1])).isoformat()
        retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        if (
            application != expected
            or observed != expected
            or published < observed
            or retrieved.tzinfo is None
            or published > retrieved.astimezone(TOKYO).date().isoformat()
        ):
            raise ValueError("Inconsistent application/publication dates")
        return published, line
    except ValueError:
        raise source_error("JPX printed application/publication dates are inconsistent.") from None


def cell_value(text: str, field: str, *, amount: bool) -> str | None:
    if text in {"-", "*"}:
        return None
    if field.endswith("_pct"):
        if amount or not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})\.[0-9]%", text):
            raise source_error("Invalid reported listed-share percentage.")
        return text[:-1]
    if not re.fullmatch(r"(?:▲ ?|-)?(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*|[1-9][0-9]{0,17})", text):
        raise source_error("JPX margin cell is not a supported exact integer.")
    value = int(text.replace(",", "").replace("▲", "-").replace(" ", ""))
    if abs(value) > 10**18 or (field.endswith("balance") and value < 0):
        raise source_error("JPX margin balance exceeds its numeric contract.")
    return str(value)


def parse_issue(
    snapshot: Snapshot,
    identifier: str,
    application_date: str,
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    from atlas_terminal.japan_company_sources import jpx_identifier

    code = jpx_identifier(identifier).removesuffix(".T")
    source_code = code + "0" if len(code) == 4 else code
    selected: dict[str, dict[str, Any]] = {}
    publication_dates = set()
    for page in pages:
        publication_date, date_line = header_dates(
            page["text"], application_date, snapshot.retrieved_at
        )
        publication_dates.add(publication_date)
        for line_number, line in enumerate(page["text"].splitlines(), 1):
            matches = [match for match in ISSUE.finditer(line) if match["code"] == source_code]
            if not matches:
                continue
            if len(matches) != 1:
                raise source_error("Duplicate issuer identity in one JPX margin line.")
            match = matches[0]
            kind = "shares" if match["shares"] else "amount_jpy"
            if kind in selected:
                raise source_error("Duplicate selected issuer balance row.")
            # Preserve cell boundaries. Never join separated digits from plain text extraction.
            tail = line[match.end() :]
            cells = list(re.finditer(r"▲ ?\S+|\S+", tail))
            if len(cells) != len(FIELDS) or any(
                right.start() - left.end() < 2
                for left, right in zip(cells, cells[1:], strict=False)
            ):
                raise source_error("JPX margin row does not have fourteen aligned numeric cells.")
            values, sources = {}, {}
            for field, cell in zip(FIELDS, cells, strict=True):
                values[field] = cell_value(cell[0], field, amount=kind == "amount_jpy")
                sources[field] = evidence(
                    snapshot,
                    pdf_page=page["page"],
                    text_line=line_number,
                    text_column_start=match.end() + cell.start() + 1,
                    text_column_end=match.end() + cell.end(),
                    reported_text=cell[0],
                    extraction="pypdf.layout.rotation_normalized",
                    extraction_version="6.21.4",
                )
            for side in ("sell", "buy"):
                for measure in ("balance", "change"):
                    inputs = [
                        values[f"{prefix}{side}_{measure}"]
                        for prefix in (
                            "",
                            "general_",
                            "standardized_",
                        )
                    ]
                    known = [int(value) for value in inputs if value is not None]
                    if len(known) == 3 and known[0] != known[1] + known[2]:
                        raise source_error("JPX reported total differs from its margin components.")
            selected[kind] = {
                "unit": "share" if kind == "shares" else "JPY",
                **values,
                "isin": match["isin"],
                "source_label": line[: match.start()].strip(),
                "field_sources": sources,
                "date_source": evidence(snapshot, pdf_page=page["page"], text_line=date_line),
            }
    if len(publication_dates) != 1:
        raise source_error("JPX margin PDF mixes publication dates.")
    if not selected:
        raise AtlasError(
            "INCOMPLETE_COVERAGE", "Selected issue is absent from this exact JPX report."
        )
    if set(selected) != {"shares", "amount_jpy"} or (
        selected["shares"]["isin"] != selected["amount_jpy"]["isin"]
        or selected["shares"]["date_source"]["pdf_page"]
        != selected["amount_jpy"]["date_source"]["pdf_page"]
    ):
        raise source_error("Selected JPX share and JPY rows lack a consistent identity.")
    shares = selected["shares"]
    buy, sell = shares["buy_balance"], shares["sell_balance"]
    reason, ratio = None, None
    if buy is None or sell is None:
        reason = "missing_reported_share_balance"
    elif int(sell) == 0:
        reason = "zero_sell_balance"
    else:
        with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
            ratio = format((Decimal(buy) / Decimal(sell)).quantize(Decimal("1e-18")), "f")
    return {
        "identifier": identifier,
        "source_security_code": source_code,
        "isin": shares["isin"],
        "date": application_date,
        "publication_date": next(iter(publication_dates)),
        "published_at": None,
        "frequency": "daily",
        "shares": shares,
        "amount_jpy": selected["amount_jpy"],
        "margin_ratio": {
            "value": ratio,
            "unit": "ratio",
            "derived": True,
            "formula": "buy_balance_shares / sell_balance_shares",
            "inputs": {
                field: {
                    "value": shares[field],
                    "unit": "share",
                    "source": shares["field_sources"][field],
                }
                for field in ("buy_balance", "sell_balance")
            },
            "unavailable_reason": reason,
            "decimal_places": 18,
            "rounding": "ROUND_HALF_EVEN",
        },
        "source": shares["field_sources"]["buy_balance"],
    }, publication_date


def margin_balances(transport: Transport, q: "ResearchQuery") -> dict[str, Any]:
    directory = transport.get(MARGIN_INDEX, 300)
    reports = margin_reports(directory)
    selected_date = q.observation_date or str(reports[0]["date"])
    selected = [report for report in reports if report["date"] == selected_date]
    if len(selected) != 1:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Application date is not linked in the current JPX directory.",
            details={"available_application_dates": [item["date"] for item in reports]},
        )
    report = selected[0]
    snapshot = transport.get(str(report["url"]), 3600)
    if not transport.synthetic and (
        snapshot.url != report["url"] or margin_identity(snapshot.url) != selected_date
    ):
        raise source_error("Downloaded JPX report identity differs from the selected link.")
    pages = extract_margin_pages(snapshot.body)
    row, published = parse_issue(snapshot, q.identifier or "", selected_date, pages)
    missing = [
        f"{kind}.{field}"
        for kind in ("shares", "amount_jpy")
        for field in FIELDS
        if not field.endswith("_pct") and row[kind][field] is None
    ]
    return {
        "records": [row],
        "sources": [
            evidence(directory, text_line=report["text_line"]),
            row["shares"]["date_source"],
        ],
        "coverage": {
            "observed_records": 1,
            "returned_records": 1,
            "truncated": False,
            "point_in_time": False,
            "frequency": "daily",
            "application_date": selected_date,
            "publication_date": published,
            "available_application_dates": [item["date"] for item in reports],
            "upstream_window_limited": True,
            "upstream_window": "Currently linked JPX all-issue reports only",
            "pdf_pages": len(pages),
            "validated_issues": 1,
            "missing_fields": missing,
            "partial": bool(missing),
            "source_scope": "JPX outstanding margin trading by issue",
        },
        "limitations": [
            "JPX exchange margin balances are not JSF financing/lending balances, all short "
            "interest, or account positions/collateral.",
            "Daily reports use application dates; printed publication dates remain separate. "
            "No publication timestamp is inferred.",
            "Only currently linked daily reports are selected. No weekly-to-daily conversion, "
            "guessed historical URLs or PIT availability is supplied.",
            "PDF amounts are in one share and one JPY, including fund-unit conventions; "
            "lot-size labels are not multipliers.",
            "Listed-share percentages retain source rounding. Missing cells remain null; "
            "zero sell balances make the share-based ratio undefined.",
            "JPX terms apply; free public access does not grant commercial collection "
            "or redistribution rights.",
        ],
    }
