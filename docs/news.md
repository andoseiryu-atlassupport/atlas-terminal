# Official headlines and free briefings

Read official announcements through the same CLI, SDK, MCP and HTTP research service. Five fixed feeds cover the Federal Reserve Board, ECB, Reserve Bank of Australia and Japan Ministry of Finance. Access needs no API key, wallet or paid news/AI provider.

```sh
atlas research news
atlas research news --country JP
atlas research news --digest
atlas research news --list
```

`news_feed` returns original headline metadata. `news_digest` adds a plain-text briefing that groups those headlines by publisher, preserving original links. Its `summary_method` is `original_headlines_grouped_by_publisher`: an extractive headline briefing rather than an article summary, translation or market-impact analysis. Both datasets explicitly report `llm_used=false` and `article_bodies_fetched=false`. A connected agent can separately explain or translate the returned headlines while retaining their source links and treating source text as untrusted.

## Available feeds

| Name             | Publisher / region             | Scope                                       |
| ---------------- | ------------------------------ | ------------------------------------------- |
| `fed_press`      | Federal Reserve Board / US     | Official press releases                     |
| `ecb_press`      | European Central Bank / EA     | The ECB's general press/communications feed |
| `ecb_statistics` | European Central Bank / EA     | Statistical press releases                  |
| `rba_media`      | Reserve Bank of Australia / AU | Media releases retained in the RBA RSS feed |
| `mof_news`       | Ministry of Finance Japan / JP | Japanese-language ministry updates          |

These are official announcement feeds, with source-specific update schedules and rolling retention. They do not constitute a broad company-news corpus, a newswire or guaranteed real-time delivery. Feed freshness can differ substantially between publishers. `--list` shows current URLs, source conditions, countries and languages without constructing a source client.

```sh
atlas research news --feeds fed_press,ecb_statistics --limit 30 --json
atlas research news --country JP --start-date 2026-10-01 --end-date 2026-10-31 --limit 100 --json
atlas research news --search "policy decision" --digest
```

The default selects all five feeds and at most **20 headlines globally**, with a maximum of 500. `feeds` selects registered names, never arbitrary URLs. `country` narrows the selection before requesting sources. Literal case-insensitive search requires every term to appear in the normalized headline. Paired inclusive dates refer to the publisher's local calendar date, not the UTC date or a historical availability cutoff. A source without retained matches returns an explicit empty selection; there is no archive backfill.

## Dates, duplicates and evidence

Known publication instants normalize to UTC. The original label, publisher-local date, timezone and precision remain available. A missing timezone or date-only label does not become an invented timestamp, and an Atom `updated` label never replaces a missing `published` label. Entries with unknown instants sort after known instants; date-only entries then use their date. Date filters omit undated entries. A source publication label after local retrieval is flagged and cannot satisfy a freshness requirement.

Each headline retains the feed URL, original byte hash, retrieval time, item path and field-level XML locators. RSS 1.0, RSS 2.0 and Atom parsers share size/item limits and DTD/entity rejection. HTML/control characters are removed from displayed titles; original source text and raw archives remain inspectable. Feed descriptions, article bodies, linked PDFs, images and remote scripts are not used in the normalized news response. Original article links are display-only and are never followed by the reader.

IDs are based on publisher plus article URL, with URL fragments excluded from identity. Matching URLs within one publisher can merge across selected feeds; identical titles at different publishers stay separate. Every appearance remains available. Conflicting title/date metadata is marked `ambiguous`, with the first query-feed/document appearance selected explicitly. Revision IDs describe selected headline metadata rather than unrelated changes elsewhere in the feed. Absence from a later rolling feed does not establish retraction.

Partial reads are enabled by default. Errors identify failed feeds alongside successful headlines; `--strict` or `allow_partial=false` rejects incomplete source reads. All-source failure fails explicitly. Storage-capacity and configuration errors stop reads. Compact evidence, pagination and explicit freshness/coverage requirements use the existing research contracts.

## Foreground updates

```sh
atlas research news --watch --feeds fed_press,mof_news
atlas research news --watch --digest --feeds ecb_press,ecb_statistics
```

The watch writes JSON Lines to stdout. Each poll reports added or updated headline records, source evidence, coverage and errors, including a heartbeat when no selected metadata changed. The default/minimum interval is **900 seconds**; `--interval-seconds` permits up to 86400. A longer reported `Retry-After` also delays the next poll. Use `--max-polls 1` for one poll, or stop the foreground process manually.

Deduplication is in memory and bounded to 10,000 headline IDs; restarting replays the initial selection. Delivery is best effort over rolling feeds, with `continuity_guaranteed=false`. A truncated selection or an upstream outage can hide intervening announcements. This is not a durable notification queue or a lossless event stream. No hosted worker, email/Slack subscription or service deployment is created by these commands.

When saving actual headlines locally, use a private directory and file permissions, for example `umask 077` before redirecting stdout. Actual data and raw captures stay outside source control and distribution artifacts.

## RSS delivery and shared queries

```sh
atlas research news --feeds fed_press,mof_news --rss
atlas serve --provider public --payments off
```

The reference server exposes `GET /v1/news/rss?feeds=fed_press,mof_news&limit=20` on its configured loopback origin. It renders the same selected metadata as RSS 2.0 and identifies partial coverage. `source` elements retain original feed attribution; the RSS TTL is 15 minutes. Starting the reference server is an operator action, and this feature does not deploy or publish a public endpoint.

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    headlines = atlas.get_news(country="JP", limit=20)
    briefing = atlas.get_news(feeds=["fed_press", "ecb_press"], digest=True)
    for update in atlas.watch_news({"dataset": "news_feed", "feeds": ["mof_news"]}, max_polls=1):
        print(update)
```

```json
{ "dataset": "news_digest", "feeds": ["fed_press", "mof_news"], "limit": 20 }
```

Use this query with `Atlas.get_research`, `atlas_get_research` (inside its `query` argument), `POST /v1/research`, or a saved research query. The same queries work in batches. To try fictional feeds offline, add `--provider fixture`; every displayed announcement is marked synthetic.

## Separate private news history

News HTTP captures use `ATLAS_STATE_DIR/news-cache.sqlite3`, separately from public macro-data counters/cache and SEC data. Conditional caching uses a 15-minute TTL. The news archive has its own limits: 1,024 distinct URL/hash versions, 256 MiB of bodies and 100,000 validation events. It does not evict versions or reset counters automatically; a full archive stops new captures explicitly.

```json
{
  "dataset": "source_history",
  "history_scope": "news",
  "source_url": "https://www.mof.go.jp/news.rss",
  "history_action": "list"
}
```

The same scope supports `get`, `diff` and local-observation `as_of`. The default `history_scope=public` continues to address the existing public archive. Legacy `policy_news` remains Federal Reserve-only and uses that original public cache; the same Fed URL may therefore have independent capture histories in the two scopes. Raw XML editions may include descriptions that the normalized reader discards. Local capture times do not prove original publication availability or reconstruct missing historical news. [History contract](source-history.md).

Keep publisher attribution and current [source conditions](../DATA_SOURCES.md#official-headline-feeds). Code licensing does not grant a news-data redistribution license. [Verification scope](verification.md) distinguishes fictional regressions, retained live captures and ongoing delivery.
