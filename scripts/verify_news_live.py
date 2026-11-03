"""Opt-in official feeds with private original-field, briefing and archive checks."""

import argparse
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from verify_g20_live import private_write
from verify_source_cells import SourceInspector

from atlas_terminal.client import Atlas
from atlas_terminal.news_catalog import FEEDS
from atlas_terminal.news_sources import NewsTransport
from atlas_terminal.research_news import rss_document
from atlas_terminal.util import canonical, stamp, state_dir


class Capture(NewsTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.snapshots, self.diagnostics = {}, {}

    @contextmanager
    def request(self, url, headers):
        with super().request(url, headers) as response:
            self.diagnostics[url] = {"http_status": response.status_code}
            yield response

    def get(self, url, ttl, **kwargs):
        snapshot = super().get(url, ttl, **kwargs)
        self.snapshots[url] = snapshot
        return snapshot


def verify(result, transport, directory):
    assert not result["is_synthetic"] and not result["coverage"]["partial"]
    assert not result["coverage"]["truncated"] and not result["article_bodies_fetched"]
    inspector = SourceInspector(transport.snapshots)
    checked = 0
    for source in result["sources"]:
        snapshot = transport.snapshots[source["raw_source_url"]]
        assert snapshot.sha256 == source["raw_sha256"]
        private_write(directory / (snapshot.sha256 + ".xml"), snapshot.body)
    for row in result["records"]:
        for field, raw in [
            ("title", row["title_raw"]),
            ("url", row["url_raw"]),
            ("published_at", row["published_at_raw"]),
        ]:
            if raw is not None:
                assert str(inspector.cell(row["field_sources"][field])).strip() == raw
                checked += 1
        raw_date = row["published_at_raw"]
        if raw_date:
            try:
                parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(raw_date)
            if parsed.tzinfo is not None:
                assert row["published_at"] == parsed.astimezone(UTC).isoformat().replace(
                    "+00:00", "Z"
                )
                assert (
                    row["published_date"]
                    == parsed.astimezone(ZoneInfo(row["publisher_timezone"])).date().isoformat()
                )
        for appearance in row["appearances"]:
            source = {
                **appearance["source"],
                "xml_local_path": appearance["source"]["xml_local_path"] + "/title",
            }
            assert str(inspector.cell(source)).strip() == appearance["title_raw"]
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/news-live"))
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parents[1] / "build"):
        parser.error("Actual headlines must stay in the ignored build directory.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    raw = out / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    raw.chmod(0o700)
    checks = []
    report = {"started_at": stamp(), "checks": checks}
    with Atlas("public") as atlas:
        capture = Capture(cache_path=state_dir() / "news-cache.sqlite3")
        atlas._research.news = capture
        for feed in FEEDS:
            entry = {"feed": feed, "status": "failed"}
            try:
                result = atlas.get_news(feeds=[feed], limit=500, allow_partial=False)
                count = verify(result, capture, raw)
                private_write(out / (feed + ".json"), canonical(result))
                history = atlas.get_research(
                    {
                        "dataset": "source_history",
                        "source_url": FEEDS[feed]["url"],
                        "history_scope": "news",
                    }
                )
                assert history["records"] and history["history"]["archive_scope"] == "news"
                entry.update(
                    status="verified",
                    rows=len(result["records"]),
                    original_fields_checked=count,
                    coverage=result["coverage"]["feed_coverage"],
                    latest_publication=result["records"][0]["published_at"]
                    if result["records"]
                    else None,
                )
            except Exception as error:
                entry.update(error_type=type(error).__name__, error=str(error))
            checks.append(entry)
            print(json.dumps(entry), flush=True)
            private_write(out / "report.json", canonical(report))
        try:
            merged = atlas.get_news(limit=500, digest=True, allow_partial=False)
            count = verify(merged, capture, raw)
            assert all(
                row["title"] in merged["briefing_text"] and row["url"] in merged["briefing_text"]
                for row in merged["records"]
            )
            private_write(out / "briefing.json", canonical(merged))
            private_write(out / "headlines.rss", rss_document(merged).encode())
            report["aggregate"] = {
                "status": "verified",
                "unique_headlines": len(merged["records"]),
                "original_fields_checked": count,
                "sections": len(merged["sections"]),
            }
        except Exception as error:
            report["aggregate"] = {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        assert set(capture.diagnostics) <= {spec["url"] for spec in FEEDS.values()}
        report["requests"] = capture.diagnostics
    report.update(
        completed_at=stamp(),
        status="PASS"
        if all(c["status"] == "verified" for c in checks)
        and report["aggregate"]["status"] == "verified"
        else "INCOMPLETE",
    )
    private_write(out / "report.json", canonical(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
