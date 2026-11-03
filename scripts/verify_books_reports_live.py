"""Explicit, private checks of key-free order books and report-directory fields."""

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from verify_g20_live import private_write

from atlas_terminal.analyst_reports import FISCO_URL, ReportTransport
from atlas_terminal.client import Atlas
from atlas_terminal.order_books import PAIRS, BookTransport
from atlas_terminal.util import canonical, stamp, state_dir


class Captured:
    def __init__(self, inner):
        self.inner = inner
        self.synthetic = False
        self.snapshots = {}

    def get(self, url, ttl):
        snapshot = self.inner.get(url, ttl)
        self.snapshots[url] = snapshot
        return snapshot

    def close(self):
        self.inner.close()


class BookCapture(BookTransport):
    @contextmanager
    def request(self, url, headers):
        with super().request(url, headers) as response:
            self.last_status = response.status_code
            yield response


class ReportCapture(ReportTransport):
    @contextmanager
    def request(self, url, headers):
        with super().request(url, headers) as response:
            self.last_status = response.status_code
            yield response


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.active = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.active = {"href": dict(attrs).get("href", ""), "text": ""}
            self.items.append(self.active)

    def handle_data(self, text):
        if self.active is not None:
            self.active["text"] += text

    def handle_endtag(self, tag):
        if tag == "a":
            self.active = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("build/books-reports-live"))
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(Path(__file__).resolve().parents[1] / "build"):
        parser.error("Actual source data must stay in ignored build output.")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    raw = out / "raw"
    raw.mkdir(exist_ok=True, mode=0o700)
    checks = []
    with Atlas("public") as atlas:
        books = Captured(BookCapture(cache_path=state_dir() / "order-books-cache.sqlite3"))
        reports = Captured(ReportCapture(cache_path=state_dir() / "reports-cache.sqlite3"))
        atlas._research.books, atlas._research.reports = books, reports
        queries = [
            (symbol, {"dataset": "order_book", "identifier": symbol, "depth": 200}, books)
            for symbol in PAIRS
        ]
        queries.append(("fisco", {"dataset": "analyst_reports", "limit": 500}, reports))
        for name, query, capture in queries:
            check = {"source": name, "status": "FAIL"}
            try:
                result = atlas.get_research(query)
                assert not result["is_synthetic"] and result["records"]
                fields = 0
                for snapshot in capture.snapshots.values():
                    private_write(raw / (snapshot.sha256 + ".raw"), snapshot.body)
                if name in PAIRS:
                    for row in result["records"]:
                        source = row["source"]
                        snapshot = capture.snapshots[source["raw_source_url"]]
                        actual = snapshot.data["data"][row["side"]][row["level"] - 1]
                        assert actual == [row["price"], row["quantity"]]
                        assert snapshot.sha256 == source["raw_sha256"]
                        elapsed = datetime.fromisoformat(row["observed_at"]).astimezone(
                            UTC
                        ) - datetime(1970, 1, 1, tzinfo=UTC)
                        assert (
                            elapsed // timedelta(milliseconds=1)
                            == snapshot.data["data"]["timestamp"]
                        )
                        fields += 3
                else:
                    snapshot = capture.snapshots[FISCO_URL]
                    anchors = Anchors()
                    anchors.feed(snapshot.body.decode("utf-8-sig"))
                    anchors.close()
                    for row in result["records"]:
                        index = int(row["source"]["xml_local_path"].split("[")[1][:-1]) - 1
                        anchor = anchors.items[index]
                        assert anchor["text"] == row["title_raw"]
                        assert urljoin(FISCO_URL, anchor["href"]) == row["original_source_url"]
                        fields += 2
                private_write(out / (name + ".json"), canonical(result))
                check.update(
                    status="PASS",
                    records=len(result["records"]),
                    original_field_matches=fields,
                    http_status=getattr(capture.inner, "last_status", "cached"),
                )
            except Exception as error:
                check.update(
                    error_type=type(error).__name__,
                    error_code=getattr(error, "code", None),
                    message=str(error)[:250],
                )
            checks.append(check)
            print(canonical(check).decode())
    report = {
        "checked_at": stamp(),
        "checks": checks,
        "passed": all(c["status"] == "PASS" for c in checks),
        "remote_pdf_downloaded": False,
        "broker_login_used": False,
    }
    private_write(out / "report.json", canonical(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
