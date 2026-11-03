"""Official headlines, original evidence, failure isolation and explicit local delivery."""

import copy
import json
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from typer.testing import CliRunner

from atlas_terminal.cli import app
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.news_catalog import FEEDS
from atlas_terminal.news_sources import NewsFixtures, NewsTransport, parse_feed
from atlas_terminal.news_stream import watch_news
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_news import rss_document
from atlas_terminal.server import create_app
from atlas_terminal.source_history import source_history
from atlas_terminal.util import canonical, digest


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


class Feeds(NewsFixtures):
    def __init__(self, overrides=None, failures=()):
        self.overrides, self.failures, self.calls = overrides or {}, set(failures), []

    def get(self, url, ttl):
        self.calls.append((url, ttl))
        name = next(name for name, spec in FEEDS.items() if spec["url"] == url)
        if name in self.failures:
            raise AtlasError(
                "SOURCE_RATE_LIMITED",
                "Fictional feed throttle",
                retryable=True,
                retry_after_seconds=3600,
            )
        snapshot = super().get(url, ttl)
        return Snapshot(
            self.overrides.get(name, snapshot.body), snapshot.url, snapshot.retrieved_at
        )


def application(transport=None):
    atlas = Atlas("fixture")
    atlas._research.news = transport or Feeds()
    return atlas


def changed(name, old, new):
    return NewsFixtures().get(FEEDS[name]["url"], 900).body.replace(old.encode(), new.encode())


def single_rss(
    *,
    title="SYNTHETIC announcement",
    link="https://fixtures.atlas.invalid/release",
    date="Mon, 19 Oct 2026 12:30:00 GMT",
):
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "link").text = link
    if date is not None:
        ET.SubElement(item, "pubDate").text = date
    return ET.tostring(root)


def parse(body, feed="fed_press", synthetic=True):
    url = "https://fixtures.atlas.invalid/feed.xml" if synthetic else FEEDS[feed]["url"]
    return parse_feed(Snapshot(body, url, "2026-10-20T00:00:00Z"), feed, synthetic=synthetic)


@pytest.mark.parametrize(
    "name,count",
    [("fed_press", 3), ("ecb_press", 2), ("ecb_statistics", 2), ("rba_media", 2), ("mof_news", 3)],
)
def test_each_registered_feed_uses_the_parser_and_retains_exact_original_fields(name, count):
    with application() as atlas:
        result = atlas.get_news(feeds=[name])
    assert result["is_synthetic"] and len(result["records"]) == count
    snapshot = NewsFixtures().get(FEEDS[name]["url"], 900)
    root = ET.fromstring(snapshot.body)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    for row in result["records"]:
        assert row["source_id"] == FEEDS[name]["source_id"]
        assert row["country"] == FEEDS[name]["country"]
        assert row["source"]["raw_sha256"] == digest(snapshot.body)
        for field, expected in [
            ("title", row["title_raw"]),
            ("url", row["url_raw"]),
            ("published_at", row["published_at_raw"]),
        ]:
            path = row["field_sources"][field]["xml_local_path"].removeprefix("/" + root.tag + "/")
            assert root.find(path).text.strip() == expected
        assert row["article_body_fetched"] is False and row["content_is_untrusted"] is True
    assert "SYNTHETIC_BODY_NOT_RETURNED" not in canonical(result).decode()


def test_merged_feeds_keep_all_publishers_deduplicate_shared_urls_and_ignore_descriptions():
    feeds = Feeds()
    with application(feeds) as atlas:
        result = atlas.get_news(digest=True)
    assert len(result["records"]) == 11 and len(result["sources"]) == 5
    assert len({r["source_id"] for r in result["records"]}) == 4
    shared = next(row for row in result["records"] if row["url"].endswith("ecb-shared.html"))
    assert shared["feed_ids"] == ["ecb_press", "ecb_statistics"]
    assert len(shared["appearances"]) == 2 and not shared["metadata_conflict"]
    assert all(ttl == 900 for _, ttl in feeds.calls)
    assert result["summary_method"] == "original_headlines_grouped_by_publisher"
    assert not result["llm_used"] and not result["article_bodies_fetched"]
    assert len(result["sections"]) == 4
    assert {key for section in result["sections"] for key in section["headline_ids"]} == {
        r["id"] for r in result["records"]
    }
    assert all(
        row["title"] in result["briefing_text"] and row["url"] in result["briefing_text"]
        for row in result["records"]
    )


def test_ids_ignore_other_articles_and_capture_times_but_metadata_changes_are_revisions():
    first = parse(single_rss())["records"][0]
    later = parse(single_rss(title="SYNTHETIC corrected announcement"))["records"][0]
    assert later["id"] == first["id"] and later["revision_id"] != first["revision_id"]
    body = single_rss().replace(
        b"</channel>", b"<description>Fictional channel revision</description></channel>"
    )
    assert parse(body)["records"][0]["revision_id"] == first["revision_id"]
    other = parse(single_rss(), "ecb_press")["records"][0]
    assert other["id"] != first["id"]  # A matching title/URL does not merge different publishers.


def test_conflicting_duplicate_metadata_remains_inspectable_and_fails_a_strict_quality_gate():
    body = changed(
        "ecb_statistics",
        "Fictional monetary statistics release",
        "Fictional corrected statistical release",
    )
    with application(Feeds({"ecb_statistics": body})) as atlas:
        result = atlas.get_news(feeds=["ecb_press", "ecb_statistics"])
        shared = next(r for r in result["records"] if len(r["appearances"]) == 2)
        assert shared["metadata_conflict"] and shared["status"] == "ambiguous"
        assert shared["title"] != shared["appearances"][1]["title"]
        with pytest.raises(AtlasError) as caught:
            atlas.get_news(
                feeds=["ecb_press", "ecb_statistics"], requirements={"allow_missing": False}
            )
    assert caught.value.code == "QUALITY_NOT_MET"


def test_country_selection_happens_before_fetch_and_uses_publisher_dates_at_midnight():
    feeds = Feeds()
    with application(feeds) as atlas:
        result = atlas.get_news(country="JP", start_date="2026-10-19", end_date="2026-10-19")
    assert feeds.calls == [(FEEDS["mof_news"]["url"], 900)]
    assert len(result["records"]) == 1
    row = result["records"][0]
    assert row["published_at"] == "2026-10-18T15:30:00Z"
    assert row["published_date"] == "2026-10-19" and row["publisher_timezone"] == "Asia/Tokyo"


def test_literal_search_limit_and_empty_filters_do_not_fetch_articles_or_infer_no_global_news():
    with application() as atlas:
        selected = atlas.get_news(search="FICTIONAL STATISTICS", limit=1)
        empty = atlas.get_news(search="no_such_fictional_headline", digest=True)
    assert len(selected["records"]) == 1 and selected["coverage"]["truncated"]
    assert selected["coverage"]["observed_records"] == 2
    assert empty["records"] == [] and "successfully read rolling feeds" in empty["briefing_text"]
    assert empty["coverage"]["complete_news_coverage"] is False


@pytest.mark.parametrize(
    "date,expected_date,precision",
    [
        (None, None, "unknown"),
        ("malformed", None, "unknown"),
        ("2026-10-19", "2026-10-19", "day"),
        ("2026-10-19T12:30:00", "2026-10-19", "day"),
    ],
)
def test_missing_and_timezone_less_labels_never_become_invented_publication_instants(
    date, expected_date, precision
):
    row = parse(single_rss(date=date))["records"][0]
    assert row["published_at"] is None and row["published_date"] == expected_date
    assert row["publication_time_precision"] == precision and row["published_at_raw"] == date


def test_future_publication_labels_are_flagged_without_making_capture_times_publication_times():
    row = parse(single_rss(date="2026-10-21T12:30:00Z"))["records"][0]
    assert row["publication_after_retrieval"] and row["published_at"] == "2026-10-21T12:30:00Z"
    assert row["source"]["retrieved_at"] == "2026-10-20T00:00:00Z"


def test_future_labels_fail_freshness_and_archive_capacity_errors_stop_reads():
    body = single_rss(date="2026-10-21T12:30:00Z")
    with application(Feeds({"fed_press": body})) as atlas:
        with pytest.raises(AtlasError) as caught:
            atlas.get_news(feeds=["fed_press"], requirements={"min_observation_date": "2026-10-21"})
    assert caught.value.code == "QUALITY_NOT_MET"

    class Full(Feeds):
        def get(self, url, ttl):
            raise AtlasError("CACHE_FULL", "Fictional full archive")

    with application(Full()) as atlas, pytest.raises(AtlasError) as caught:
        atlas.get_news(allow_partial=True)
    assert caught.value.code == "CACHE_FULL"


def test_atom_uses_alternate_link_original_published_and_its_own_xhtml_safety_rules():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>fictional:one</id><title type="xhtml">
      <div xmlns="http://www.w3.org/1999/xhtml">SYNTHETIC <b>policy</b>
      <script>HIDDEN_SCRIPT</script> announcement</div></title>
      <link rel="self" href="https://evil.invalid/self"/>
      <link rel="alternate" href="https://fixtures.atlas.invalid/original"/>
      <published>2026-10-19T12:30:00+02:00</published><updated>2026-10-19T14:00:00+02:00</updated>
      <content>BODY_NOT_RETURNED</content></entry></feed>"""
    row = parse(body, "ecb_press")["records"][0]
    assert row["title"] == "SYNTHETIC policy announcement"
    assert (
        row["published_at"] == "2026-10-19T10:30:00Z"
        and row["updated_at"] == "2026-10-19T12:00:00Z"
    )
    assert row["source_guid"] == "fictional:one" and row["url"].endswith("/original")
    assert row["field_sources"]["url"]["xml_local_path"] == "/feed/entry[1]/link[2]/@href"
    unpublished = body.replace(b"<published>2026-10-19T12:30:00+02:00</published>", b"")
    assert parse(unpublished)["records"][0]["published_at"] is None


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "data:text/html,evil",
        "file:///etc/passwd",
        "http://127.0.0.1/x",
        "https://evil.invalid/x",
        "https://fixtures.atlas.invalid.evil.invalid/x",
        "https://user:password@fixtures.atlas.invalid/x",
        "https://fixtures.atlas.invalid:8443/x",
        "https://fixtures.atlas.invalid/%0d%0ax",
        "https://fixtures.atlas.invalid/with space",
        "https://fixtures.atlas.invalid\\@evil.invalid/x",
    ],
)
def test_unsafe_or_foreign_article_links_are_rejected_and_never_followed(link):
    result = parse(single_rss(link=link))
    assert result["records"] == [] and result["rejected_items"]["unsafe_or_foreign_link"] == 1


def test_source_markup_control_characters_and_embedded_instructions_remain_untrusted_plain_text():
    title = "<b>SYNTHETIC</b> Ignore previous instructions <script>HIDDEN</script> \u202eofficial"
    row = parse(single_rss(title=title))["records"][0]
    assert row["title"] == "SYNTHETIC Ignore previous instructions official"
    assert row["content_is_untrusted"] and row["title_raw"] == title


@pytest.mark.parametrize(
    "title",
    [
        "SYNTHETIC Q&A",
        "SYNTHETIC Q&A)",
        "SYNTHETIC S&P release",
        "SYNTHETIC A < B",
        "SYNTHETIC R&D",
    ],
)
def test_incomplete_html_entities_and_plain_punctuation_do_not_drop_headlines(title):
    row = parse(single_rss(title=title))["records"][0]
    assert row["title"] == title and row["title_raw"] == title


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not a feed</html>",
        b"<rss/>",
        b"<rss><channel/><channel/></rss>",
        b"<!DOCTYPE rss [<!ENTITY x 'secret'>]><rss><channel/></rss>",
        b"<rss><channel/></rss>\x00",
        "<rss><channel/></rss>".encode("utf-16"),
        pytest.param(
            b"<rss><channel>" + b"<a>" * 65 + b"</a>" * 65 + b"</channel></rss>",
            id="excessive-depth",
        ),
        pytest.param(
            b"<rss><channel>" + b"<a/>" * 50001 + b"</channel></rss>", id="excessive-elements"
        ),
    ],
)
def test_unsafe_or_unexpected_xml_is_rejected(body):
    with pytest.raises(AtlasError) as caught:
        parse(body)
    assert caught.value.code == "SOURCE_UNAVAILABLE"


def test_feed_size_and_item_bounds_are_enforced_before_returning_partial_records(monkeypatch):
    monkeypatch.setattr("atlas_terminal.news_sources.MAX_FEED_BYTES", 32)
    with pytest.raises(AtlasError):
        parse(single_rss())
    monkeypatch.setattr("atlas_terminal.news_sources.MAX_FEED_BYTES", 10000)
    monkeypatch.setattr("atlas_terminal.news_sources.MAX_FEED_ITEMS", 0)
    with pytest.raises(AtlasError):
        parse(single_rss())


def test_partial_source_failures_remain_visible_and_strict_or_all_failed_reads_fail():
    with application(Feeds(failures=["rba_media"])) as atlas:
        result = atlas.get_news(digest=True)
        assert result["coverage"]["partial"] and result["coverage"]["failed_feeds"] == ["rba_media"]
        assert result["feed_errors"][0]["error"]["retry_after_seconds"] == 3600
        assert "Source coverage is partial" in result["briefing_text"]
        with pytest.raises(AtlasError):
            atlas.get_news(allow_partial=False)
        with pytest.raises(AtlasError) as caught:
            atlas.get_news(requirements={"allow_missing": False})
        assert caught.value.code == "QUALITY_NOT_MET"
    with application(Feeds(failures=FEEDS)) as atlas, pytest.raises(AtlasError) as caught:
        atlas.get_news()
    assert caught.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "bad",
    [
        {"feeds": ["unknown"]},
        {"feeds": ["fed_press", "fed_press"]},
        {"feeds": [FEEDS["fed_press"]["url"]]},
        {"country": "ZZ"},
        {"feeds": ["fed_press"], "country": "JP"},
        {"search": "   "},
        {"identifier": "AAPL"},
        {"as_of": "2026-10-19"},
        {"history_scope": "news"},
        {"start_date": "2026-10-19"},
        {"limit": True},
        {"limit": 0},
        {"limit": 501},
    ],
)
def test_invalid_news_queries_fail_before_any_source_access(bad):
    feeds = Feeds()
    with application(feeds) as atlas, pytest.raises(AtlasError):
        atlas.get_research({"dataset": "news_feed", **bad})
    assert not feeds.calls


def test_schema_defaults_catalog_and_compact_evidence_match_runtime():
    schema = ResearchQuery.model_json_schema()
    variants = {v["properties"]["dataset"]["const"]: v for v in schema["oneOf"]}
    for dataset in ("news_feed", "news_digest"):
        q = parse_research({"dataset": dataset})
        assert q.limit == 20 and q.allow_partial is True
        assert variants[dataset]["properties"]["limit"]["default"] == 20
        assert variants[dataset]["properties"]["allow_partial"]["default"] is True
        assert set(variants[dataset]["properties"]["feeds"]["items"]["enum"]) == set(FEEDS)
        Draft202012Validator(schema).validate({"dataset": dataset, "feeds": ["fed_press"]})
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({"dataset": "news_feed", "feeds": ["unknown"]})
    assert set(Atlas.research_catalog("news_feed")["news"]["feeds"]) == set(FEEDS)
    with application() as atlas:
        data = {
            "dataset": "news_feed",
            "feeds": ["fed_press"],
            "response": {"view": "compact", "page_size": 1, "fields": ["title"]},
        }
        page = atlas.get_research(data)
        row = page["records"][0]
        assert row["revision_id"] and row["publisher"] and row["published_date"]
        evidence = atlas.get_evidence(row["field_sources"]["title"]["evidence_id"])
        assert evidence["evidence"]["xml_local_path"].endswith("/title")
        next_page = atlas.get_research(
            data | {"response": data["response"] | {"cursor": page["response"]["next_cursor"]}}
        )
        assert next_page["records"][0]["id"] != row["id"]


@pytest.mark.parametrize(
    "suffix", ["?key=private", "#fragment", "/", "?redirect=https://evil.invalid"]
)
def test_news_transport_allows_only_exact_registered_feed_urls(tmp_path, suffix):
    with pytest.raises(AtlasError):
        NewsTransport.validate_url(FEEDS["fed_press"]["url"] + suffix)


def test_transport_keeps_redirects_closed_and_sends_no_operator_credentials(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/collect"})

    monkeypatch.setenv("SEC_USER_AGENT", "Private operator private@example.invalid")
    monkeypatch.setenv("FRED_API_KEY", "SYNTHETIC_PRIVATE_KEY")
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        transport = NewsTransport(
            cache_path=tmp_path / "news.sqlite3", client=client, sleep=lambda _: None
        )
        with pytest.raises(AtlasError):
            transport.get(FEEDS["fed_press"]["url"], 900)
    assert len(requests) == 1
    assert str(requests[0].url) == FEEDS["fed_press"]["url"]
    assert not any(
        key in requests[0].headers for key in ("Authorization", "Cookie", "Subscription-Key")
    )
    assert "private" not in str(requests[0].headers).casefold()


def test_news_archive_versions_are_separate_and_available_through_the_explicit_scope(tmp_path):
    url = FEEDS["fed_press"]["url"]
    bodies = [
        single_rss(link="https://www.federalreserve.gov/fictional", title=title)
        for title in ("SYNTHETIC first", "SYNTHETIC corrected")
    ]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200, content=bodies[len(calls) - 1], headers={"ETag": str(len(calls))}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        transport = NewsTransport(
            cache_path=tmp_path / "news-cache.sqlite3", client=client, sleep=lambda _: None
        )
        transport.get(url, 900)
        transport.get(url, 900)  # A cache hit creates no second request or new source edition.
        transport.get(url, 900, force_refresh=True)
    assert len(calls) == 2 and not (tmp_path / "public-cache.sqlite3").exists()
    history = source_history({"scope": "news", "url": url}, tmp_path)
    assert history["archive_scope"] == "news" and len(history["versions"]) == 2
    data = source_history(
        {"scope": "news", "url": url, "action": "get", "sha256": digest(bodies[0])}, tmp_path
    )
    assert data["body_utf8"] == bodies[0].decode()
    assert source_history({"url": url}, tmp_path)["versions"] == []


def test_watch_emits_new_revised_and_heartbeat_events_and_restarts_replay():
    with application() as atlas:
        first = atlas.get_news(feeds=["fed_press"], limit=1)
    revised = copy.deepcopy(first)
    revised["records"][0]["title"] = "SYNTHETIC correction"
    revised["records"][0]["revision_id"] = "b" * 64
    snapshots, sleeps = iter([first, first, revised, first]), []
    events = list(
        watch_news(
            lambda _: next(snapshots), {"dataset": "news_feed"}, max_polls=4, sleep=sleeps.append
        )
    )
    assert [len(e["added"]) for e in events] == [1, 0, 0, 0]
    assert [len(e["updated"]) for e in events] == [0, 0, 1, 1]
    assert sleeps == [900, 900, 900] and not any(e["continuity_guaranteed"] for e in events)
    restarted = list(watch_news(lambda _: first, {"dataset": "news_feed"}, max_polls=1))
    assert len(restarted[0]["added"]) == 1


def test_watch_honors_retry_after_for_partial_or_failed_polls_and_stops_at_archive_capacity(
    monkeypatch,
):
    with application(Feeds(failures=["rba_media"])) as atlas:
        partial = atlas.get_news()
    sleeps = []
    events = list(
        watch_news(lambda _: partial, {"dataset": "news_digest"}, max_polls=2, sleep=sleeps.append)
    )
    assert sleeps == [3600] and events[0]["next_poll_after_seconds"] == 3600
    assert "Original:" in events[0]["briefing_text"]

    def failure(_):
        raise AtlasError("SOURCE_RATE_LIMITED", "Fictional wait", retry_after_seconds=7200)

    sleeps.clear()
    errors = list(watch_news(failure, {"dataset": "news_feed"}, max_polls=2, sleep=sleeps.append))
    assert sleeps == [7200] and all(e["event"] == "news_poll_error" for e in errors)

    def full(_):
        raise AtlasError("CACHE_FULL", "Fictional quota")

    with pytest.raises(AtlasError):
        list(watch_news(full, {"dataset": "news_feed"}, max_polls=1))
    monkeypatch.setattr("atlas_terminal.news_stream.MAX_SEEN_HEADLINES", 0)
    with pytest.raises(AtlasError):
        list(watch_news(lambda _: partial, {"dataset": "news_feed"}, max_polls=1))


@pytest.mark.parametrize(
    "options",
    [
        {"interval_seconds": 0},
        {"interval_seconds": 899},
        {"interval_seconds": True},
        {"interval_seconds": 86401},
        {"max_polls": 0},
        {"max_polls": True},
        {"max_polls": 10001},
    ],
)
def test_polling_cannot_reduce_the_minimum_interval_or_start_with_invalid_bounds(options):
    with pytest.raises(AtlasError):
        list(
            watch_news(
                lambda _: pytest.fail("Invalid watch fetched sources"),
                {"dataset": "news_feed"},
                **options,
            )
        )


def test_offline_list_cli_briefing_json_rss_watch_and_http_share_services(tmp_path, monkeypatch):
    runner = CliRunner()
    with monkeypatch.context() as patch:
        patch.setattr(
            "atlas_terminal.cli.Atlas", lambda *args: pytest.fail("List constructed a client")
        )
        listed = runner.invoke(app, ["research", "news", "--list"])
    assert listed.exit_code == 0 and set(json.loads(listed.stdout)["feeds"]) == set(FEEDS)
    args = ["research", "news", "--feeds", "mof_news", "--provider", "fixture"]
    with application() as atlas:
        expected = atlas.get_news(feeds=["mof_news"])
    encoded = runner.invoke(app, [*args, "--json"])
    assert encoded.exit_code == 0 and json.loads(encoded.stdout) == expected
    brief = runner.invoke(app, [*args, "--digest"])
    assert brief.exit_code == 0 and "SYNTHETIC" in brief.stdout and "Original:" in brief.stdout
    watched = runner.invoke(app, [*args, "--watch", "--max-polls", "1"])
    assert watched.exit_code == 0 and len(json.loads(watched.stdout)["added"]) == 3
    rss = runner.invoke(app, [*args, "--rss"])
    assert rss.exit_code == 0 and rss.stdout.strip() == rss_document(expected)
    root = ET.fromstring(rss.stdout)
    assert (
        "SYNTHETIC" in root.findtext("./channel/title") and len(root.findall("./channel/item")) == 3
    )
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        result = client.post(
            "/v1/research",
            json={
                "dataset": "news_feed",
                "feeds": ["mof_news"],
                "limit": 20,
                "allow_partial": True,
            },
        )
        assert result.status_code == 200 and result.json() == expected
        feed = client.get("/v1/news/rss", params={"feeds": "mof_news"})
        assert feed.status_code == 200 and feed.text == rss_document(expected)
        assert feed.headers["content-type"].startswith("application/rss+xml")
        assert (
            client.get("/v1/news/rss", params={"feeds": "https://evil.invalid"}).status_code == 400
        )
