"""Explicit, private checks of key-free order books and report-directory fields."""

import argparse
import re
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
from verify_g20_live import private_write

from atlas_terminal.analyst_reports import FISCO_URL, ReportTransport
from atlas_terminal.client import Atlas
from atlas_terminal.order_books import PAIRS, BookTransport, book_url
from atlas_terminal.util import canonical, digest, stamp, state_dir


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


class SourceCapture:
    def __init__(self, *, raw_directory, **kwargs):
        super().__init__(**kwargs)
        self.raw_directory, self.diagnostics = raw_directory, {}

    def validate_body(self, url, body):
        # Keep bounded original bytes even when production format validation fails.
        private_write(self.raw_directory / (digest(body) + ".raw"), body)
        super().validate_body(url, body)

    @contextmanager
    def request(self, url, headers):
        diagnostic = {"http_status": None}
        self.diagnostics[url] = diagnostic
        try:
            with super().request(url, headers) as response:
                diagnostic["http_status"] = response.status_code
                yield response
        except httpx.HTTPError as error:
            diagnostic["network_exception"] = type(error).__name__
            raise


class BookCapture(SourceCapture, BookTransport):
    pass


class ReportCapture(SourceCapture, ReportTransport):
    pass


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items, self.active = [], None
        self.item_text = None

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.item_text = ""
        if tag == "a":
            self.active = {
                "href": dict(attrs).get("href", ""),
                "text": "",
                "before": self.item_text,
            }
            self.items.append(self.active)

    def handle_data(self, text):
        if self.item_text is not None:
            self.item_text += text
        if self.active is not None:
            self.active["text"] += text

    def handle_endtag(self, tag):
        if tag == "a":
            self.active = None
        if tag == "li":
            self.item_text = None


def verify_book(result, snapshot, symbol, depth=200):
    """Recalculate with rational arithmetic independently of production Decimal code."""
    data = snapshot.data["data"]
    assert result["sources"][0]["raw_sha256"] == digest(snapshot.body)
    expected_levels = {side: len(data[side]) for side in ("bids", "asks")}
    assert result["coverage"]["source_levels"] == expected_levels
    assert result["coverage"]["truncated"] == any(n > depth for n in expected_levels.values())
    assert len(result["records"]) == sum(min(n, depth) for n in expected_levels.values())
    expected_time = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=data["timestamp"])
    future = expected_time > datetime.fromisoformat(snapshot.retrieved_at)
    assert result["snapshot"]["future_observation"] == future
    sequence = data.get("sequenceId")
    assert result["snapshot"]["sequence_id"] == (str(sequence) if sequence is not None else None)
    for key, value in result["snapshot"]["overflow_quantities"].items():
        assert value == data.get(key)
    totals, expected_rows = {}, []
    for side in ("bids", "asks"):
        prices = [Fraction(pair[0]) for pair in data[side]]
        assert len(prices) == len(set(prices))
        assert prices == sorted(prices, reverse=side == "bids")
        quantity, notional = Fraction(0), Fraction(0)
        for index, (price, qty) in enumerate(data[side][:depth]):
            assert Fraction(price) > 0 and Fraction(qty) > 0
            quantity += Fraction(qty)
            notional += Fraction(price) * Fraction(qty)
            expected_rows.append((side, index, price, qty, quantity, notional))
        totals[side] = quantity
    for row, (side, index, price, qty, cumulative_qty, notional) in zip(
        result["records"], expected_rows, strict=True
    ):
        assert (row["side"], row["level"], row["price"], row["quantity"]) == (
            side,
            index + 1,
            price,
            qty,
        )
        assert row["source"]["json_pointer"] == f"/data/{side}/{index}"
        assert row["source"]["raw_sha256"] == snapshot.sha256
        assert row["source"]["raw_source_url"] == snapshot.url
        assert datetime.fromisoformat(row["observed_at"]) == expected_time
        assert row["future_observation"] == future
        assert (row["venue"], row["asset_class"], row["base_asset"], row["quote_currency"]) == (
            "bitbank",
            "crypto",
            PAIRS[symbol],
            "JPY",
        )
        assert row["quantity_unit"] == "base_asset" and row["unit"] == "JPY_per_base_asset"
        assert Fraction(row["cumulative_quantity"]) == cumulative_qty
        assert Fraction(row["cumulative_notional"]) == notional
    best_bid = Fraction(data["bids"][0][0]) if data["bids"] else None
    best_ask = Fraction(data["asks"][0][0]) if data["asks"] else None
    valid = best_bid is not None and best_ask is not None and best_bid < best_ask and not future
    expected = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_quantity": totals["bids"],
        "ask_quantity": totals["asks"],
        "spread": None,
        "midpoint": None,
        "spread_bps": None,
        "quantity_imbalance": None,
    }
    if valid:
        spread, midpoint = best_ask - best_bid, (best_ask + best_bid) / 2
        expected.update(
            spread=spread,
            midpoint=midpoint,
            spread_bps=Fraction(round(spread / midpoint * 10000 * 10**18), 10**18),
            quantity_imbalance=Fraction(
                round((totals["bids"] - totals["asks"]) / sum(totals.values()) * 10**18), 10**18
            ),
        )
    stats = result["book_analytics"]
    assert stats["status"] == ("derived" if valid else "unavailable")
    assert stats["depth_per_side"] == depth
    assert stats["quantity_unit"] == "base_asset" and stats["quote_currency"] == "JPY"
    for key, value in expected.items():
        assert (Fraction(stats[key]) if stats[key] is not None else None) == value
    return {
        "original_field_matches": 3 * len(expected_rows),
        "calculation_matches": 2 * len(expected_rows) + len(expected),
        "source_levels": expected_levels,
        "future_observation": future,
    }


def verify_reports(result, snapshot):
    anchors = Anchors()
    anchors.feed(snapshot.body.decode("utf-8-sig"))
    anchors.close()
    languages = Counter()
    for row in result["records"]:
        source = row["source"]
        assert source["raw_sha256"] == digest(snapshot.body)
        assert source["raw_source_url"] == snapshot.url
        index = int(source["xml_local_path"].split("[")[1][:-1]) - 1
        anchor = anchors.items[index]
        assert anchor["text"] == row["title_raw"]
        assert urljoin(FISCO_URL, anchor["href"]) == row["original_source_url"]
        # Only this list item's prefix is eligible; never reuse an adjacent date.
        assert anchor["before"] is not None
        dates = re.findall(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", anchor["before"])
        assert len(dates) == 1
        assert row["published_date"] == date(*map(int, dates[0])).isoformat()
        assert source["publication_label_context"].endswith(anchor["before"])
        codes = re.findall(r"[<＜〈]([0-9]{3}[0-9A-Z])[>＞〉]", anchor["text"])
        assert len(codes) <= 1
        assert row["identifier"] == (codes[0] if codes else None)
        expected_language = "ja"
        for marker, language in (("英語版", "en"), ("中国語版", "zh")):
            if marker in anchor["text"]:
                expected_language = language
        assert row["language"] == expected_language
        assert not row["language_verified_for_document"]
        languages[expected_language] += 1
    return {
        "original_field_matches": 2 * len(result["records"]),
        "directory_metadata_matches": 3 * len(result["records"]),
        "language_hints": dict(languages),
        "unknown_security_codes": sum(r["identifier"] is None for r in result["records"]),
        "matching_directory_records": result["coverage"]["matching_records"],
        "selection_truncated": result["coverage"]["truncated"],
    }


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
    raw.chmod(0o700)
    checks = []
    with Atlas("public") as atlas:
        books = Captured(
            BookCapture(raw_directory=raw, cache_path=state_dir() / "order-books-cache.sqlite3")
        )
        reports = Captured(
            ReportCapture(raw_directory=raw, cache_path=state_dir() / "reports-cache.sqlite3")
        )
        atlas._research.books, atlas._research.reports = books, reports
        queries = [
            (symbol, {"dataset": "order_book", "identifier": symbol, "depth": 200}, books)
            for symbol in PAIRS
        ]
        queries.append(("fisco", {"dataset": "analyst_reports", "limit": 500}, reports))
        for name, query, capture in queries:
            check = {"source": name, "status": "FAIL"}
            url = book_url(name) if name in PAIRS else FISCO_URL
            try:
                result = atlas.get_research(query)
                assert not result["is_synthetic"] and result["records"]
                for snapshot in capture.snapshots.values():
                    private_write(raw / (snapshot.sha256 + ".raw"), snapshot.body)
                if name in PAIRS:
                    checked = verify_book(result, capture.snapshots[url], name)
                else:
                    checked = verify_reports(result, capture.snapshots[url])
                private_write(out / (name + ".json"), canonical(result))
                check.update(
                    status="PASS",
                    records=len(result["records"]),
                    **checked,
                    **capture.inner.diagnostics.get(url, {"http_status": "cached"}),
                )
            except Exception as error:
                diagnostic = capture.inner.diagnostics.get(url, {})
                stage = (
                    "network"
                    if diagnostic.get("network_exception")
                    else "http"
                    if diagnostic.get("http_status") not in (None, 200, 304)
                    else "field_comparison"
                    if isinstance(error, AssertionError)
                    else "source_parsing_or_validation"
                )
                check.update(
                    error_type=type(error).__name__,
                    error_code=getattr(error, "code", None),
                    message=str(error)[:250],
                    failure_stage=stage,
                    **diagnostic,
                )
            checks.append(check)
            print(canonical(check).decode(), flush=True)
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
