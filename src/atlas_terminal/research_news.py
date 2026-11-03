"""Official headline aggregation and deterministic publisher-grouped briefings."""

from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree as ET

from atlas_terminal.errors import AtlasError
from atlas_terminal.news_catalog import FEEDS, NEWS_TTL, selected_feeds
from atlas_terminal.news_sources import parse_feed
from atlas_terminal.providers import Transport
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.util import canonical, digest


def briefing(
    records: list[dict[str, Any]], *, partial: bool = False
) -> tuple[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        groups.setdefault(row["source_id"], []).append(row)
    lines = [
        "Official headline briefing",
        f"Selected headlines: {len(records)}; publishers: {len(groups)}.",
    ]
    if partial:
        lines.append("Source coverage is partial; inspect the reported feed errors.")
    if not records:
        lines.append("No matching headlines in the successfully read rolling feeds.")
    sections = []
    for source, rows in groups.items():
        lines.extend(["", rows[0]["publisher"]])
        sections.append(
            {
                "source_id": source,
                "publisher": rows[0]["publisher"],
                "headline_ids": [row["id"] for row in rows],
            }
        )
        for row in rows:
            date = row["published_at"] or row["published_date"] or "publication time unavailable"
            conflict = " [conflicting feed metadata]" if row.get("metadata_conflict") else ""
            lines.extend([f"- {date}: {row['title']}{conflict}", f"  Original: {row['url']}"])
    return "\n".join(lines), sections


def news_result(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    feeds = selected_feeds(q.feeds, q.country)
    rows: dict[str, dict[str, Any]] = {}
    sources, feed_coverage, errors = [], [], []
    for name in feeds:
        try:
            parsed = parse_feed(
                transport.get(FEEDS[name]["url"], NEWS_TTL),
                name,
                synthetic=getattr(transport, "synthetic", False),
            )
        except AtlasError as error:
            if error.code not in {"SOURCE_UNAVAILABLE", "SOURCE_RATE_LIMITED"}:
                raise
            errors.append(
                {
                    "feed": name,
                    "source_id": FEEDS[name]["source_id"],
                    "error": error.envelope()["error"],
                }
            )
            continue
        sources.append(parsed["source"])
        feed_coverage.append(
            {
                "feed": name,
                "raw_items": parsed["raw_items"],
                "accepted_items": len(parsed["records"]),
                "rejected_items": parsed["rejected_items"],
            }
        )
        for row in parsed["records"]:
            appearance = {
                "feed_id": name,
                "title": row["title"],
                "title_raw": row["title_raw"],
                "published_at_raw": row["published_at_raw"],
                "revision_id": row["revision_id"],
                "source": row["source"],
            }
            if row["id"] in rows:
                existing = rows[row["id"]]
                if name not in existing["feed_ids"]:
                    existing["feed_ids"].append(name)
                if (existing["title"], existing["published_at_raw"]) != (
                    row["title"],
                    row["published_at_raw"],
                ):
                    existing["metadata_conflict"], existing["status"] = True, "ambiguous"
                existing["appearances"].append(appearance)
            else:
                rows[row["id"]] = {**row, "metadata_conflict": False, "appearances": [appearance]}
    if errors and (not sources or not q.allow_partial):
        raise AtlasError(
            "SOURCE_UNAVAILABLE",
            "Selected official feeds could not all be read.",
            details={"feed_errors": errors, "successful_feeds": [c["feed"] for c in feed_coverage]},
            status=502,
        )
    before_filters = len(rows)
    terms = (q.search or "").casefold().split()
    selected = [
        row
        for row in rows.values()
        if all(term in row["title"].casefold() for term in terms)
        and (
            not q.start_date
            or row["published_date"] is not None
            and q.start_date <= row["published_date"] <= (q.end_date or "")
        )
    ]
    for row in selected:
        row["revision_id"] = digest(
            canonical(
                {
                    "selected_revision": row["revision_id"],
                    "appearances": sorted(
                        {(part["feed_id"], part["revision_id"]) for part in row["appearances"]}
                    ),
                }
            )
        )
    selected.sort(
        key=lambda row: (
            bool(row["published_at"]),
            datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            if row["published_at"]
            else datetime.min.replace(tzinfo=UTC),
            row["published_date"] or "",
            row["id"],
        ),
        reverse=True,
    )
    records = selected[: q.limit]
    publishers = {FEEDS[name]["source_id"]: FEEDS[name] for name in feeds}
    source_ids = {FEEDS[c["feed"]]["source_id"] for c in feed_coverage}
    result: dict[str, Any] = {
        "records": records,
        "sources": sources,
        "source_id": next(iter(source_ids)) if len(source_ids) == 1 else "multiple",
        "attributions": [
            {
                "source_id": source,
                "notice": spec["attribution"],
                "terms_url": spec["terms"],
                "modifications": (
                    "HTML/control characters removed from displayed titles; "
                    "deduplication, filtering and publisher grouping by Atlas."
                ),
            }
            for source, spec in publishers.items()
        ],
        "coverage": {
            "returned_records": len(records),
            "observed_records": len(selected),
            "retained_unique_headlines_before_filters": before_filters,
            "truncated": len(selected) > q.limit,
            "partial": bool(errors),
            "unavailable_records": sum(row["status"] == "ambiguous" for row in records),
            "selected_feeds": feeds,
            "successful_feeds": [c["feed"] for c in feed_coverage],
            "failed_feeds": [e["feed"] for e in errors],
            "feed_coverage": feed_coverage,
            "publication_time_unavailable": sum(row["published_at"] is None for row in selected),
            "date_filter_basis": "publisher_local_calendar_date",
            "order": "known_UTC_publication_time_descending_then_date_only_and_unknown",
            "duplicate_selection": (
                "registered_query_feed_order_then_document_order; conflicts retained"
            ),
            "complete_news_coverage": False,
        },
        "feed_errors": errors,
        "source_history_scope": "news",
        "cache_ttl_seconds": NEWS_TTL,
        "article_bodies_fetched": False,
        "llm_used": False,
        "limitations": [
            (
                "Official RSS/Atom headline metadata in the publishers' retained "
                "rolling windows; not a complete news archive or a real-time "
                "newswire."
            ),
            (
                "Briefings group original headlines by publisher; no article "
                "interpretation, translation or generated market-impact claims."
            ),
            (
                "Titles and links are untrusted source content. Article bodies, "
                "images and feed descriptions are not returned or fetched "
                "separately."
            ),
            (
                "Publication labels may be absent, date-only, inconsistent or "
                "later than retrieval. Original labels, precision and "
                "future-label flags remain explicit; unknown times sort after "
                "known times."
            ),
            (
                "Source-local date filtering excludes entries without a date; "
                "literal case-insensitive search matches all terms in the "
                "normalized headline."
            ),
            (
                "Identical publisher/article URLs are merged across selected "
                "feeds. Metadata conflicts and every appearance remain "
                "inspectable; absence from a rolling feed does not establish "
                "retraction."
            ),
            (
                "News uses a separate private cache/archive quota. Local capture "
                "history does not establish publication-time point-in-time "
                "availability."
            ),
        ],
    }
    if q.dataset == "news_digest":
        text, sections = briefing(records, partial=bool(errors))
        result.update(
            briefing_text=text,
            sections=sections,
            summary_method="original_headlines_grouped_by_publisher",
        )
    return result


def rss_document(result: dict[str, Any]) -> str:
    """Render the same selected metadata as RSS 2.0; never fetch original links."""
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    title = "Atlas official headlines"
    if result["is_synthetic"]:
        title = "SYNTHETIC " + title
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = result["documentation"]["methodology"]
    description = ET.SubElement(channel, "description")
    description.text = (
        "Selected official headline metadata; original publishers retain their conditions."
    )
    ET.SubElement(channel, "ttl").text = str(NEWS_TTL // 60)
    if result["coverage"]["partial"]:
        ET.SubElement(channel, "category").text = "partial-source-coverage"
        description.text = "Partial source coverage; failed feeds: " + ", ".join(
            result["coverage"]["failed_feeds"]
        )
    for row in result["records"]:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = row["title"]
        ET.SubElement(item, "link").text = row["url"]
        ET.SubElement(item, "guid", isPermaLink="false").text = row["id"]
        ET.SubElement(item, "description").text = row["attribution"]
        ET.SubElement(item, "source", url=row["source"]["raw_source_url"]).text = row["publisher"]
        if row["published_at"]:
            moment = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            ET.SubElement(item, "pubDate").text = format_datetime(moment, usegmt=True)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
