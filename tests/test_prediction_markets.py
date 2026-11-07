"""Fictional Gamma payloads verify event discovery, outcome alignment and exact evidence."""

import json
import subprocess
import sys
from copy import deepcopy
from decimal import localcontext
from importlib.resources import files

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.prediction_sources import (
    GAMMA,
    event_url,
    prediction_identity,
    search_term,
    search_url,
)
from atlas_terminal.providers import Snapshot, decode_json
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_predictions import parse_event
from atlas_terminal.research_views import SourceEvidence
from atlas_terminal.server import create_app
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources/research")
SLUG = "fictional-anthropic-event"
QUERY = {
    "dataset": "prediction_markets",
    "search": "Anthropic",
    "limit": 3,
    "include_closed": False,
}


def event_data():
    return json.loads(RESOURCE.joinpath("prediction_event.json").read_bytes())


def search_data():
    return json.loads(RESOURCE.joinpath("prediction_search.json").read_bytes())


def snapshot(data):
    return Snapshot(
        json.dumps(data).encode(),
        "https://fixtures.atlas.invalid/prediction-event.json",
        "2026-11-07T12:00:00Z",
    )


def parse(data=None, closed=False):
    return parse_event(
        snapshot(event_data() if data is None else data), SLUG, "fictional-event-1", closed
    )


class Captured:
    synthetic = True

    def __init__(self, event=None, search=None):
        self.event = event_data() if event is None else event
        self.search = search_data() if search is None else search
        self.requests = []

    def get(self, url, ttl):
        self.requests.append(url)
        selected = prediction_identity(url)
        assert selected is not None
        return snapshot(self.search if selected[0] == "search" else self.event)

    def close(self):
        pass


def test_discovery_fetches_exact_event_details_and_preserves_search_evidence():
    capture = Captured()
    with Atlas("fixture") as atlas:
        atlas._research.public = capture
        result = atlas.get_research(QUERY)
    assert capture.requests == [search_url("Anthropic", 3), event_url(SLUG)]
    assert len(result["sources"]) == 2 and result["sources"][0]["json_pointer"] == "/events"
    assert result["coverage"]["markets"] == 2 and result["coverage"]["outcomes"] == 4
    assert not result["coverage"]["partial"]
    assert result["records"][0]["filtered_closed_markets"] == 1


def test_exact_prices_percentages_and_reversed_outcome_order():
    event = parse()
    first, second = event["markets"]
    assert first["outcomes"][0]["outcome"] == "Yes"
    assert first["outcomes"][0]["reported_price"] == "0.625"
    assert first["outcomes"][0]["probability_percent"] == "62.500"
    assert second["outcomes"][0]["outcome"] == "No"
    assert second["outcomes"][0]["probability_percent"] == "70.0"
    assert second["outcomes"][1]["probability_percent"] == "30.0"
    assert first["outcomes"][0]["token_id"] == "1001"


def test_closed_reference_prices_are_not_current_forecasts_or_resolution_proof():
    event = parse(closed=True)
    market = event["markets"][-1]
    assert market["lifecycle"] == "closed"
    assert [row["reported_price"] for row in market["outcomes"]] == ["0", "1"]
    assert all(row["market_implied_probability"] is None for row in market["outcomes"])
    assert "resolved" not in market


@pytest.mark.parametrize(
    "field,value,state",
    [
        ("active", False, "inactive"),
        ("closed", True, "closed"),
        ("archived", True, "archived"),
        ("acceptingOrders", False, "inactive"),
        ("acceptingOrders", None, "unknown"),
    ],
)
def test_market_lifecycle_gates_current_probability(field, value, state):
    data = event_data()
    data["markets"][0][field] = value
    market = parse(data, closed=True)["markets"][0]
    assert market["lifecycle"] == state
    assert market["outcomes"][0]["reported_price"] == "0.625"
    assert market["outcomes"][0]["probability_percent"] is None


def test_missing_prices_are_not_zero_or_complements():
    data = event_data()
    data["markets"][0]["outcomePrices"] = '["0.6",null]'
    event = parse(data)
    yes, no = event["markets"][0]["outcomes"]
    assert yes["probability_percent"] == "60.0"
    assert no["reported_price"] is None and no["probability_percent"] is None
    assert event["markets"][0]["reported_price_sum"] is None
    with Atlas("fixture") as atlas:
        atlas._research.public = Captured(event=data)
        result = atlas.get_research(QUERY)
    assert result["coverage"]["partial"] and result["coverage"]["missing_prices"] == 1


@pytest.mark.parametrize("value", [None, [], "[]"])
def test_unpriced_market_retains_outcome_identity(value):
    data = event_data()
    data["markets"][0]["outcomePrices"] = value
    outcomes = parse(data)["markets"][0]["outcomes"]
    assert [row["outcome"] for row in outcomes] == ["Yes", "No"]
    assert all(row["reported_price"] is None for row in outcomes)


def test_true_zero_price_is_preserved_and_prices_are_never_normalized():
    data = event_data()
    data["markets"][0]["outcomePrices"] = '["0","0.9"]'
    market = parse(data)["markets"][0]
    assert market["outcomes"][0]["market_implied_probability"] == "0"
    assert market["outcomes"][1]["market_implied_probability"] == "0.9"
    assert market["reported_price_sum"] == "0.9" and not market["price_sum_is_one"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcomePrices", '["0.2"]'),
        ("outcomePrices", '["1.1","0"]'),
        ("outcomePrices", '["NaN","0"]'),
        ("outcomePrices", "true"),
        ("outcomePrices", '[true,"0"]'),
        ("outcomes", '["Yes","Yes"]'),
        ("clobTokenIds", '["1001"]'),
        ("clobTokenIds", '["1001","1001"]'),
        ("clobTokenIds", '["secret-token","1002"]'),
        ("active", "false"),
        ("updatedAt", "2026-11-07T12:00:00"),
        ("slug", "../orders"),
    ],
)
def test_malformed_or_misaligned_fields_fail(field, value):
    data = event_data()
    data["markets"][0][field] = value
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_end_date_and_metadata_time_are_not_price_observation_timestamps():
    event = parse()
    assert event["end_date"] == "2026-12-31T23:59:59Z"
    assert event["metadata_updated_at"] == "2026-11-07T11:00:00Z"
    assert event["price_observed_at"] is None
    assert all(market["price_observed_at"] is None for market in event["markets"])
    with Atlas("fixture") as atlas, pytest.raises(AtlasError) as error:
        atlas.get_research(QUERY | {"requirements": {"min_observation_date": "2026-01-01"}})
    assert error.value.code == "QUALITY_NOT_MET"


def test_excluded_old_closed_markets_need_not_have_complete_pricing_fields():
    data = event_data()
    data["markets"][-1].pop("outcomes")
    assert len(parse(data)["markets"]) == 2
    with pytest.raises(AtlasError):
        parse(data, closed=True)


def test_duplicate_event_market_identity_and_wrong_selected_event_fail():
    data = event_data()
    data["markets"].append(deepcopy(data["markets"][0]))
    with pytest.raises(AtlasError):
        parse(data)
    with pytest.raises(AtlasError):
        parse_event(snapshot(event_data()), SLUG, "different-event", False)
    search = search_data()
    search["events"].append(deepcopy(search["events"][0]))
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas._research.public = Captured(search=search)
        atlas.get_research(QUERY)


def test_embedded_and_native_array_evidence_resolves_to_original_values():
    for native in (False, True):
        data = event_data()
        if native:
            for market in data["markets"]:
                for field in ("outcomes", "outcomePrices", "clobTokenIds"):
                    market[field] = json.loads(market[field])
        source = snapshot(data)
        event = parse_event(source, SLUG, None, False)
        for market in event["markets"]:
            for row in market["outcomes"]:
                for field, locator in row["field_sources"].items():
                    SourceEvidence.model_validate(locator)
                    assert locator["raw_sha256"] == digest(source.body)
                    value = source.data
                    for token in locator["json_pointer"].split("/")[1:]:
                        value = value[int(token)] if isinstance(value, list) else value[token]
                    if "embedded_json_pointer" in locator:
                        value = decode_json(value.encode())[
                            int(locator["embedded_json_pointer"][1:])
                        ]
                    assert value == row[field]


def test_metadata_and_price_evidence_survive_compact_projection():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(QUERY | {"response": {"view": "compact", "fields": ["title"]}})
        event = result["records"][0]
        assert event["markets"] and event["event_slug"] and event["price_observed_at"] is None
        price = event["markets"][0]["outcomes"][0]["field_sources"]["reported_price"]
        reference = atlas.get_evidence(price["evidence_id"])["evidence"]
        assert reference["json_pointer"] == "/markets/0/outcomePrices"
        assert reference["embedded_json_pointer"] == "/0"
        with pytest.raises(AtlasError) as error:
            atlas.get_research(QUERY | {"requirements": {"allow_derived": False}})
        assert error.value.code == "QUALITY_NOT_MET"


def test_search_aliases_canonical_urls_and_exact_slug_mode():
    assert search_term("アンソロピック") == "Anthropic"
    assert search_url("アンソロピック", 3) == search_url("Anthropic", 3)
    assert prediction_identity(event_url(SLUG)) == ("event", SLUG)
    capture = Captured()
    with Atlas("fixture") as atlas:
        atlas._research.public = capture
        result = atlas.get_prediction_markets(event_slug=SLUG)
    assert capture.requests == [event_url(SLUG)] and len(result["sources"]) == 1


@pytest.mark.parametrize(
    "url",
    [
        GAMMA + "/orders",
        GAMMA + "/events/slug/../orders",
        GAMMA + "/events/slug/" + SLUG + "?key=secret",
        GAMMA + "/events/slug/" + SLUG + "#fragment",
        GAMMA.replace("https", "http") + "/events/slug/" + SLUG,
        "https://name:secret@gamma-api.polymarket.com/events/slug/" + SLUG,
        "https://clob.polymarket.com/order",
        search_url("Anthropic", 3) + "&page=2",
    ],
)
def test_noncanonical_trading_or_credential_urls_are_not_allowed(url):
    assert prediction_identity(url) is None
    with pytest.raises(AtlasError):
        PublicTransport.validate_url(url)


def test_empty_search_is_explicit_and_never_substitutes_a_market():
    capture = Captured(search={"events": [], "pagination": {"hasMore": False, "totalResults": 0}})
    with Atlas("fixture") as atlas:
        atlas._research.public = capture
        result = atlas.get_research(QUERY)
    assert result["records"] == [] and len(capture.requests) == 1


def test_pagination_and_market_caps_are_reported():
    search = search_data()
    search["pagination"] = {"hasMore": True, "totalResults": 20}
    with Atlas("fixture") as atlas:
        atlas._research.public = Captured(search=search)
        result = atlas.get_research(QUERY)
    assert result["coverage"]["truncated"] and result["coverage"]["search_total_results"] == 20
    data = event_data()
    template = data["markets"][0]
    data["markets"] = [dict(template, id=f"market-{i}") for i in range(101)]
    event = parse(data)
    assert (
        event["available_markets"] == 101
        and len(event["markets"]) == 100
        and event["markets_truncated"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"search": None},
        {"event_slug": SLUG},
        {"search": " "},
        {"search": "bad\nquery"},
        {"limit": 6},
        {"identifier": "Anthropic"},
        {"start_date": "2026-01-01"},
        {"as_of": "2020-01-01"},
    ],
)
def test_runtime_query_rejects_ambiguity_history_and_irrelevant_fields(extra):
    with Atlas("fixture") as atlas, pytest.raises(AtlasError):
        atlas.get_research(QUERY | extra)


def test_published_schema_requires_exactly_one_discovery_or_detail_selector():
    validator = Draft202012Validator(Atlas.research_catalog()["query_schema"])
    validator.validate(QUERY)
    validator.validate({"dataset": "prediction_markets", "event_slug": SLUG})
    for query in (
        {"dataset": "prediction_markets"},
        QUERY | {"event_slug": SLUG},
        QUERY | {"limit": 6},
    ):
        with pytest.raises(ValidationError):
            validator.validate(query)


def test_redirect_does_not_follow_to_a_trading_host_or_other_provider(tmp_path):
    requested = []

    def respond(request):
        requested.append(request)
        return httpx.Response(302, headers={"location": "https://clob.polymarket.com/order"})

    transport = PublicTransport(
        cache_path=tmp_path / "cache.sqlite3",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with Atlas("public") as atlas, pytest.raises(AtlasError) as error:
        atlas._research.public = transport
        atlas.get_research(QUERY)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert len(requested) == 1 and requested[0].method == "GET"


def test_decimal_context_cannot_round_price_percentages():
    with localcontext() as context:
        context.prec = 3
        data = event_data()
        data["markets"][0]["outcomePrices"] = '["0.123456789123456789","0.876543210876543211"]'
        row = parse(data)["markets"][0]["outcomes"][0]
    assert row["probability_percent"] == "12.345678912345678900"


def test_cli_sdk_http_share_prediction_service(tmp_path):
    with Atlas("fixture") as atlas:
        expected = atlas.get_prediction_markets("Anthropic")
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "polymarket",
            "Anthropic",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(cli.stdout) == expected and not cli.stderr
    with TestClient(create_app(provider="fixture", directory=tmp_path)) as client:
        response = client.post("/v1/research", json=QUERY)
        assert response.status_code == 200 and response.json() == expected
    with Atlas("fixture") as atlas:
        expected_detail = atlas.get_research(
            {
                "dataset": "prediction_markets",
                "event_slug": SLUG,
                "include_closed": True,
            }
        )
    detail = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "prediction_markets",
            "--event-slug",
            SLUG,
            "--include-closed",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(detail.stdout) == expected_detail


async def test_official_stdio_mcp_uses_the_shared_service(tmp_path):
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(QUERY)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture"],
        env={"ATLAS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        result = await session.call_tool("atlas_get_research", {"query": QUERY})
        assert not result.is_error and result.structured_content == expected
