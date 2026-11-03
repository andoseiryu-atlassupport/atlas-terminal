"""Explicit local polling: bounded in-memory deduplication, no hosted notifications."""

import time
from collections.abc import Callable, Iterator
from typing import Any

from atlas_terminal.errors import AtlasError
from atlas_terminal.news_catalog import NEWS_DATASETS, NEWS_TTL
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_news import briefing
from atlas_terminal.util import stamp

MAX_SEEN_HEADLINES = 10000


def retry_wait(errors: list[dict[str, Any]], interval: int) -> int:
    delays = [entry.get("error", entry).get("retry_after_seconds") for entry in errors]
    return max(
        [
            interval,
            *[value for value in delays if isinstance(value, int) and not isinstance(value, bool)],
        ]
    )


def watch_news(
    get: Callable[[ResearchQuery | dict[str, Any]], dict[str, Any]],
    query: dict[str, Any],
    *,
    interval_seconds: int = NEWS_TTL,
    max_polls: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[dict[str, Any]]:
    q = parse_research(query)
    if (
        q.dataset not in NEWS_DATASETS
        or q.response.view != "full"
        or q.response.fields
        or q.response.page_size
    ):
        raise AtlasError("INVALID_INPUT", "News polling needs a full unpaged news query.")
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or not NEWS_TTL <= interval_seconds <= 86400
    ):
        raise AtlasError("INVALID_INPUT", "News polling intervals must be 900–86400 seconds.")
    if max_polls is not None and (
        isinstance(max_polls, bool) or not isinstance(max_polls, int) or not 1 <= max_polls <= 10000
    ):
        raise AtlasError(
            "INVALID_INPUT", "Choose 1–10000 polls or stop the foreground watch manually."
        )
    seen: dict[str, str] = {}
    poll = 0
    while max_polls is None or poll < max_polls:
        poll += 1
        wait = interval_seconds
        try:
            result = get(q)
        except AtlasError as error:
            if error.code not in {"SOURCE_UNAVAILABLE", "SOURCE_RATE_LIMITED"}:
                raise
            wait = retry_wait(
                [error.envelope()["error"], *error.details.get("feed_errors", [])], interval_seconds
            )
            yield {
                "event": "news_poll_error",
                "poll": poll,
                "observed_at": stamp(),
                "error": error.envelope()["error"],
                "continuity_guaranteed": False,
                "next_poll_after_seconds": wait,
            }
        else:
            wait = retry_wait(result["feed_errors"], interval_seconds)
            records = result["records"]
            if len(seen.keys() | {r["id"] for r in records}) > MAX_SEEN_HEADLINES:
                raise AtlasError(
                    "CACHE_FULL",
                    "Foreground news deduplication reached 10000 headlines; restart the watch.",
                )
            added = [row for row in records if row["id"] not in seen]
            updated = [
                row
                for row in records
                if row["id"] in seen and seen[row["id"]] != row["revision_id"]
            ]
            seen.update((row["id"], row["revision_id"]) for row in records)
            event = {
                "event": "news_update",
                "poll": poll,
                "observed_at": stamp(),
                "is_synthetic": result["is_synthetic"],
                "added": added,
                "updated": updated,
                "coverage": result["coverage"],
                "feed_errors": result["feed_errors"],
                "sources": result["sources"],
                "attributions": result["attributions"],
                "delivery_semantics": "best_effort_rolling_feed_polling",
                "continuity_guaranteed": False,
                "source_text_is_untrusted": True,
                "next_poll_after_seconds": wait,
            }
            if q.dataset == "news_digest":
                text, sections = briefing([*added, *updated], partial=result["coverage"]["partial"])
                event.update(
                    briefing_text=text,
                    sections=sections,
                    summary_method="original_headlines_grouped_by_publisher",
                )
            yield event
        if max_polls is None or poll < max_polls:
            sleep(wait)
