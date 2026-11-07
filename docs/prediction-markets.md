# Polymarket event prices

`prediction_markets` searches Polymarket's public Gamma API and reads exact event details. Each event contains its markets and each market's named outcomes, reported prices, price-derived probability indicators, lifecycle flags, contract dates and source evidence. CLI, SDK, MCP and HTTP share the same implementation. No API key, wallet, funded account or trading SDK is used.

```sh
atlas research polymarket Anthropic
atlas research polymarket アンソロピック --json
atlas research polymarket --event-slug anthropic-ipo-date --json
```

The slug example requires that exact event to exist and remain accessible. Search finds current publisher-ranked matches; it does not choose a particular Anthropic contract in advance. The exact Japanese keywords `アンソロピック` and `アンスロピック` map to `Anthropic`; other queries are sent as entered after whitespace/Unicode normalization. The actual search term is included in coverage.

```python
from atlas_terminal.client import Atlas

with Atlas("public") as atlas:
    result = atlas.get_prediction_markets("Anthropic", limit=3)
    for event in result["records"]:
        for market in event["markets"]:
            for outcome in market["outcomes"]:
                print(market["question"], outcome["outcome"], outcome["probability_percent"])
```

MCP `atlas_get_research` accepts the following under `query`; HTTP accepts the same object at `POST /v1/research`:

```json
{
  "dataset": "prediction_markets",
  "search": "Anthropic",
  "limit": 3,
  "include_closed": false
}
```

Supply exactly one of `search` and `event_slug`. `limit` counts events, defaults to three and accepts one through five. An exact slug skips search. Search reads one page, then separately fetches the selected event details so prices do not depend on search previews. There are at most six GET requests per query before cache reuse. Search profiles/tags, wallet endpoints and order endpoints are excluded.

## Prices and interpretation

`reported_price` is Gamma's original decimal outcome price, expressed as a ratio to one unit of winning payout. For a market reported open, `market_implied_probability` retains that value and `probability_percent` is exactly `100 * reported_price`. It is a market-implied indicator, not a calibrated forecast, guaranteed result or executable quote. The adapter does not assume a collateral currency or claim that Gamma prices are independently verified live CLOB midpoints.

Outcome labels, prices and optional public CLOB token IDs are aligned by their source array index. The code does not assume that Yes is first or infer No as `1 - Yes`. A missing price remains null, a genuine zero remains zero, and malformed/misaligned arrays fail explicitly. Gamma JSON-encoded arrays and native arrays are both supported.

`reported_price_sum` and `price_sum_is_one` expose each market's supplied price vector. Prices are not normalized to sum to one. Markets within an event can overlap, refer to different deadlines, or represent separate binary questions; their Yes prices must not be summed into an event-wide distribution. Each contract's question, description, group label and resolution-source text remain available as untrusted publisher text.

By default, known closed and archived events/markets are excluded. `--include-closed` retains their reported prices as references, while current probability fields stay null. An inactive market, a market not accepting orders, or unknown required lifecycle flags likewise has no current probability indicator. A `closed` flag does not prove final resolution or identify a winner. No winner is inferred from a zero/one price.

## Time, coverage and evidence

Original search/detail responses are cached for 60 seconds through the existing private public-source cache. `snapshot_retrieved_at`, event/market `metadata_updated_at` and contract `end_date` have different meanings. `price_observed_at` remains null: Gamma metadata update times are not certified quote timestamps. A source-age requirement checks retrieval age; it cannot certify underlying quote freshness. An observation-date requirement does not substitute a future contract deadline for a price observation.

Search reports `upstream_search_has_more`, the publisher's optional total and explicit truncation. The adapter accepts up to 500 raw markets and returns at most 100 per event, with `available_markets` and `markets_truncated`. Shared response-size limits still apply. Empty search results remain empty; no fallback market or another provider is substituted. This is a bounded snapshot reader, without historical prices, PIT vintages, CLOB depth, user positions or streaming.

Each source field retains its original URL, SHA-256, retrieval time and JSON pointer. An encoded array locator points to the original string field and includes an `embedded_json_pointer` into its decoded JSON. Native arrays use a direct JSON pointer. Derived percentages and sums retain formulas; compact evidence and field projection preserve market context. Actual responses, normalized observations and verification CSVs remain private ignored output. The bundled fixture is fictional:

```sh
atlas research polymarket Anthropic --provider fixture --json
atlas research polymarket --event-slug fictional-anthropic-event \
  --include-closed --provider fixture --json
```

## Access, terms and verification

The only runtime host is `gamma-api.polymarket.com`, with fixed `GET /public-search` and `GET /events/slug/{slug}` paths. URLs with credentials, extra query parameters, fragments or arbitrary paths are rejected. Source redirects are not followed. The operator's normal proxy/TLS configuration remains in effect, with no alternate-host, VPN, cookie or geographic-restriction bypass. In a restricted execution environment, this host must be permitted for actual reads.

[Polymarket terms](https://polymarket.com/tos) apply separately from Atlas's code license. They restrict access by Capital Market Clients and market-data distributors without a written agreement, and restrict commercial redistribution. Public API access is not an unrestricted data license. This reader provides informational content and no trading functionality. The optional source is excluded from default connectivity probes; select `polymarket` explicitly when using diagnostics with network enabled.

An opt-in private verification checks original event/market identities, outcome labels/prices/token IDs and exact percentages, and can save source bytes, normalized JSON and a CSV:

```sh
uv run --offline python scripts/verify_predictions_live.py --allow-network \
  --search Anthropic --limit 1 --output build/predictions-live
```

A blocked connection is recorded as a failure, not a live PASS. Empty/unpriced results do not establish successful probability verification. [Current observed results](verification.md#polymarket-event-prices) are separate from fictional interface tests and from the publisher's general availability.

Official references: [Gamma search](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles), [event details](https://docs.polymarket.com/api-reference/events/get-event-by-slug), [research data guide](https://institute.polymarket.com/data) and [price interpretation](https://help.polymarket.com/en/articles/13364488-how-are-prices-calculated).
