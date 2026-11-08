"""Narrow Toyota disclosure rules; never infer amounts from prose with an LLM.

Only reviewed consolidated annual wording/rows for March 2024–2026 are eligible.
Unrecognized wording, periods, units, spans or conflicting repeats stay unavailable.
The original text block, physical table coordinates or normalized text span, and
the explicit million-yen conversion remain separate from numeric XBRL evidence.
"""

import re
from decimal import Decimal
from typing import Any

from atlas_terminal.edinet_extensions import LABELS, exact_text
from atlas_terminal.edinet_mappings import taxonomy_family
from atlas_terminal.filing_html import FilingHTML
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_financials import reference
from atlas_terminal.research_japan_companies import NIL, scoped_xml, xbrl_member
from atlas_terminal.util import canonical, digest

AMOUNT = r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)(?:\.[0-9]+)?"
SBC = re.compile(
    r"株式報酬に係る費用は、前連結会計年度が(?P<previous>" + AMOUNT + r")百万円、"
    r"当連結会計年度が(?P<current>" + AMOUNT + r")百万円であり、連結損益計算書の"
    r"「販売費及び一般管理費」に含まれています。"
)


def toyota_notes(
    snapshot: Snapshot, filing: dict[str, Any], eligible_contexts: set[str]
) -> dict[str, list[dict[str, Any]]]:
    if (
        filing["edinet_code"] != "E02144"
        or filing["security_code"] != "72030"
        or filing["report_period_end"] not in {"2024-03-31", "2025-03-31", "2026-03-31"}
    ):
        return {}
    year = int(filing["report_period_end"][:4])
    interest = re.compile(
        rf"{year - 1}年3月31日および{year}年3月31日に終了した各1年間における支払利息は、"
        r"それぞれ(?P<previous>" + AMOUNT + r")百万円および(?P<current>" + AMOUNT + r")百万円です。"
        r"なお、金融事業に関連する支払利息は、連結損益計算書の「金融事業に係る金融費用」に"
        r"含めて計上しています。"
    )
    member, body = xbrl_member(snapshot.body)
    root, _ = scoped_xml(body)
    result: dict[str, list[dict[str, Any]]] = {}
    for index, child in enumerate(root, 1):
        namespace, _, tag = child.tag.rpartition("}")
        if (
            not tag.endswith("TextBlock")
            or child.get("unitRef") is not None
            or child.get("contextRef") not in eligible_contexts
            or child.get(NIL) in {"true", "1"}
            or taxonomy_family(namespace.removeprefix("{")) not in {"jpcrp", "jpigp"}
        ):
            continue
        html = child.text or ""
        # Text blocks are escaped XHTML. Child XML structures are not guessed.
        if len(child) or len(html) > 8_000_000:
            continue
        parser = FilingHTML(html)
        parser.feed(html)
        parser.close()
        text = exact_text("".join(part[0] for part in parser.parts))
        source = reference(snapshot, f"/xbrl/*[{index}]", xml=True) | {
            "archive_member": member,
            "member_sha256": digest(body),
            "expanded_element": child.tag,
            "context_id": child.get("contextRef"),
            "text_block_sha256": digest(html.encode()),
        }
        for metric, pattern in (("stock_based_compensation", SBC), ("interest_expense", interest)):
            for match in pattern.finditer(text):
                raw = match["current"]
                result.setdefault(metric, []).append(
                    {
                        "value": format(Decimal(raw.replace(",", "")) * 1_000_000, "f"),
                        "raw_value": raw,
                        "currency": "JPY",
                        "display_unit": "million JPY",
                        "period": {
                            "start": filing["report_period_start"],
                            "end": filing["report_period_end"],
                        },
                        "formula": "reported_note_amount * 1000000",
                        "evidence_type": "xbrl_note_text",
                        "measurement_basis": "profit_loss_expense",
                        "source": source
                        | {
                            "normalized_text_start": match.start(),
                            "normalized_text_end": match.end(),
                            "text_normalization": "NFKC; remove whitespace",
                            "quote": match[0],
                            "period_binding": (
                                "annual consolidated context; explicit current/prior wording"
                            ),
                        },
                    }
                )
        # A table route is useful where the issuer does not tag the two cash
        # purchases individually. Physical cells with spans are not expanded.
        if (
            tag != "ConsolidatedStatementOfCashFlowsIFRSTextBlock"
            or "連結キャッシュ・フロー計算書" not in text
            or "金額:百万円" not in text
            or f"3月31日に終了した1年間{year - 1}年{year}年" not in text
        ):
            continue
        for table in parser.tables:
            table_text = exact_text("".join(cell["text"] for cell in table.get("cells", [])))
            if f"3月31日に終了した1年間{year - 1}年{year}年" not in table_text:
                continue
            rows: dict[int, list[dict[str, Any]]] = {}
            for cell in table.get("cells", []):
                rows.setdefault(cell["row_index"], []).append(cell)
            operands: dict[str, list[dict[str, Any]]] = {}
            for cells in rows.values():
                cells.sort(key=lambda item: item["cell_index"])
                if len(cells) not in {3, 4} or any(
                    c["rowspan"] != 1 or c["colspan"] != 1 for c in cells
                ):
                    continue
                label = exact_text(cells[0]["text"])
                if label not in LABELS or (len(cells) == 4 and cells[1]["text"].strip()):
                    continue
                raw = exact_text(cells[-1]["text"])
                prior = exact_text(cells[-2]["text"])
                if not all(re.fullmatch("△?" + AMOUNT, value) for value in (prior, raw)):
                    continue
                amount = Decimal(raw.removeprefix("△").replace(",", ""))
                if not raw.startswith("△") and amount != 0:
                    continue
                operands.setdefault(LABELS[label], []).append(
                    {
                        "value": format(amount * 1_000_000, "f"),
                        "raw_value": cells[-1]["text"],
                        "row_label": cells[0]["text"],
                        "source": source
                        | {
                            k: cells[-1][k]
                            for k in (
                                "table_index",
                                "row_index",
                                "cell_index",
                                "html_start_char",
                                "html_end_char",
                            )
                        },
                    }
                )
            if set(operands) != set(LABELS.values()) or any(
                len(items) != 1 for items in operands.values()
            ):
                continue
            selected = [items[0] for _, items in sorted(operands.items())]
            result.setdefault("capital_expenditure", []).append(
                {
                    "value": format(sum(Decimal(item["value"]) for item in selected), "f"),
                    "currency": "JPY",
                    "display_unit": "million JPY",
                    "operands": selected,
                    "period": {
                        "start": filing["report_period_start"],
                        "end": filing["report_period_end"],
                    },
                    "formula": "-(ppe_excluding_rental_assets + rental_assets) * 1000000",
                    "evidence_type": "xbrl_note_table",
                    "measurement_basis": "gross_cash_outflow",
                    "source": source
                    | {
                        "table_index": table["table_index"],
                        "header_binding": f"3月31日に終了した1年間{year - 1}年{year}年",
                    },
                }
            )
    for observations in result.values():
        observations.sort(key=lambda item: canonical(item["source"]))
    return result
