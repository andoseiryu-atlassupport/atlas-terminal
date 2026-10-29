"""Fictional metadata-led FRED series and exact outbound credential contracts."""

import json
from copy import deepcopy
from importlib.resources import files
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from atlas_terminal.activity_sources import fred_url
from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.fred_sources import (
    FRED_MAX_OBSERVATIONS,
    fred_series_id,
    fred_series_identity,
    fred_series_url,
    fred_window,
)
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_fred import fred_metadata, fred_series
from atlas_terminal.research_models import ResearchQuery

DATES = ("2026-01-01", "2026-12-31")
RESOURCE = files("atlas_terminal").joinpath("resources", "research")


def payload(kind):
    return json.loads(RESOURCE.joinpath(f"fred_DEMO_INDEX_{kind}.json").read_bytes())


def snapshot(value, url):
    return Snapshot(json.dumps(value).encode(), url, "2026-10-29T01:00:00Z")


class FictionalFred:
    synthetic = True

    def __init__(self, *, metadata=None, data=None):
        self.metadata = payload("metadata") if metadata is None else metadata
        self.data = payload("data") if data is None else data
        self.urls = []

    def get(self, url, ttl):
        self.urls.append(url)
        identity = fred_series_identity(url)
        assert identity is not None
        return snapshot(self.metadata if identity[1] == "metadata" else self.data, url)


def query(**changes):
    return cast(
        ResearchQuery,
        SimpleNamespace(
            **dict(series="DEMO_INDEX", start_date=DATES[0], end_date=DATES[1], limit=100) | changes
        ),
    )


def test_native_dates_missing_zero_and_metadata_have_exact_cell_evidence():
    source = FictionalFred()
    result = fred_series(source, query())
    assert [row["value"] for row in result["records"]] == [None, "0", "100.001"]
    assert [row["date"] for row in result["records"]] == [
        "2026-03-01",
        "2026-02-01",
        "2026-01-01",
    ]
    for i, row in enumerate(reversed(result["records"])):
        assert row["source"]["json_pointer"] == f"/observations/{i}/value"
        assert row["metadata_source"]["json_pointer"] == "/seriess/0"
        assert row["observation_source"]["json_pointer"] == f"/observations/{i}"
        assert row["date_basis"] == "fred_native_observation_label"
        assert row["realtime_start"] == "2026-10-29"
        assert row["unit"] == "Index 2025=100"
        assert row["frequency_code"] == "M"
        assert row["seasonal_adjustment_code"] == "NSA"
        assert row["published_at"] is None
    assert "Fictional" in result["series_metadata"]["notes"]
    assert result["coverage"]["upstream_complete"] is True
    assert result["coverage"]["point_in_time"] is False
    assert result["coverage"]["date_selection"] == "inclusive_fred_native_observation_label"
    assert source.urls == [fred_series_url("DEMO_INDEX"), fred_series_url("DEMO_INDEX", *DATES)]


def test_return_limit_is_explicit_and_does_not_truncate_source_validation():
    result = fred_series(FictionalFred(), query(limit=1))
    assert len(result["records"]) == 1
    assert result["coverage"]["observed_records"] == 3
    assert result["coverage"]["truncated"] is True
    broken = payload("data")
    broken["observations"][0]["value"] = "NaN"
    with pytest.raises(AtlasError, match="numeric"):
        fred_series(FictionalFred(data=broken), query(limit=1))


def test_packaged_fixture_supports_one_explicit_fictional_selector_and_keeps_curated_claims():
    result = fred_series(PublicFixtures(), query())
    assert result["records"][-1]["value"] == "100.001"
    assert result["sources"][0]["raw_source_url"].startswith("https://fixtures.atlas.invalid/")
    assert (
        PublicFixtures().get(fred_url("us_initial_claims"), 100).data["seriess"][0]["id"] == "ICSA"
    )
    with pytest.raises(AtlasError):
        PublicFixtures().get(fred_series_url("OTHER"), 100)


def test_shared_query_and_compact_view_retain_unit_native_dates_and_realtime_context(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    request = {
        "dataset": "fred_series",
        "series": "DEMO_INDEX",
        "start_date": DATES[0],
        "end_date": DATES[1],
    }
    with Atlas("fixture") as atlas:
        full = atlas.get_research(request)
        compact = atlas.get_research(
            {**request, "response": {"view": "compact", "fields": ["value"]}}
        )
        assert full["dataset"] == "fred_series" and full["is_synthetic"] is True
        assert len(full["records"]) == 3
        for original, selected in zip(full["records"], compact["records"], strict=True):
            assert selected["value"] == original["value"]
            assert selected["unit"] == original["unit"]
            assert selected["date_basis"] == "fred_native_observation_label"
            assert selected["realtime_start"] == original["realtime_start"]
            assert selected["seasonal_adjustment_code"] == "NSA"
            evidence = atlas.get_evidence(selected["source"]["evidence_id"])["evidence"]
            assert evidence == original["source"]


@pytest.mark.parametrize("value", ["", "icSa", "A/B", "A?x=1", "A&api_key=x", "A" * 81, " A "])
def test_series_identifier_is_a_single_bounded_exact_selector(value):
    with pytest.raises(AtlasError) as error:
        fred_series_id(value)
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize(
    "start,end",
    [
        (None, "2026-01-01"),
        ("2026-01-01", None),
        ("2026-02-30", "2026-12-31"),
        ("20260101", "2026-12-31"),
        ("2026-12-31", "2026-01-01"),
        ("1899-01-01", "1900-01-01"),
        ("1996-01-01", "2026-01-01"),
    ],
)
def test_fred_windows_are_bounded_real_dates(start, end):
    with pytest.raises(AtlasError):
        fred_window(start, end)


def test_canonical_source_urls_allow_no_query_or_origin_mutations():
    meta = fred_series_url("DEMO_INDEX")
    data = fred_series_url("DEMO_INDEX", *DATES)
    assert fred_series_identity(meta) == ("DEMO_INDEX", "metadata")
    assert fred_series_identity(data) == ("DEMO_INDEX", "data")
    for url in (
        meta + "&api_key=private",
        meta + "&series_id=OTHER",
        meta + "#fragment",
        meta.replace("https://", "http://"),
        meta.replace("api.stlouisfed.org", "api.stlouisfed.org.evil.invalid"),
        meta.replace("api.stlouisfed.org", "private@api.stlouisfed.org"),
        meta.replace("api.stlouisfed.org", "api.stlouisfed.org:443"),
        meta.replace("series?", "series/search?"),
        data.replace("units=lin", "units=pc1"),
        data.replace("offset=0", "offset=1"),
        data.replace("sort_order=asc", "sort_order=desc"),
        data.replace("limit=12000", "limit=100000"),
    ):
        assert fred_series_identity(url) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "OTHER"),
        ("frequency_short", "H"),
        ("units", ""),
        ("seasonal_adjustment", None),
        ("notes", []),
        ("observation_start", "2027-01-01"),
        ("realtime_start", "2027-01-01"),
    ],
)
def test_metadata_identity_units_frequency_and_dates_are_not_invented(field, value):
    metadata = payload("metadata")
    metadata["seriess"][0][field] = value
    with pytest.raises(AtlasError) as error:
        fred_metadata(snapshot(metadata, fred_series_url("DEMO_INDEX")), "DEMO_INDEX")
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("count", [2, 4, FRED_MAX_OBSERVATIONS + 1])
def test_incomplete_source_pages_fail_instead_of_becoming_complete_local_results(count):
    data = payload("data")
    data["count"] = count
    with pytest.raises(AtlasError) as error:
        fred_series(FictionalFred(data=data), query())
    assert error.value.code == "INCOMPLETE_COVERAGE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("offset", 1),
        ("offset", False),
        ("count", True),
        ("units", "pc1"),
        ("output_type", 2),
        ("output_type", True),
        ("sort_order", "desc"),
        ("observation_start", "2025-01-01"),
    ],
)
def test_source_transformations_partial_offsets_and_window_drift_fail(field, value):
    data = payload("data")
    data[field] = value
    with pytest.raises(AtlasError) as error:
        fred_series(FictionalFred(data=data), query())
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("value", ["NaN", "Inf", "", None, 1.5, True, "1e999"])
def test_only_documented_missing_marker_and_finite_decimal_strings_are_accepted(value):
    data = payload("data")
    data["observations"][1]["value"] = value
    with pytest.raises(AtlasError):
        fred_series(FictionalFred(data=data), query())


@pytest.mark.parametrize("fault", ["duplicate", "unordered", "outside", "realtime"])
def test_observation_order_range_and_realtime_bounds_are_validated(fault):
    data = payload("data")
    if fault == "duplicate":
        data["observations"][1] = deepcopy(data["observations"][0])
    elif fault == "unordered":
        data["observations"].reverse()
    elif fault == "outside":
        data["observations"][0]["date"] = "2025-12-01"
    else:
        data["observations"][0]["realtime_end"] = "2020-01-01"
    with pytest.raises(AtlasError):
        fred_series(FictionalFred(data=data), query())


def test_credentials_are_outbound_only_and_cached_reads_still_require_configuration(
    tmp_path, monkeypatch
):
    key = "a" * 32
    monkeypatch.setenv("FRED_API_KEY", key)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.stlouisfed.org"
        assert parse_qs(urlsplit(str(request.url)).query)["api_key"] == [key]
        return httpx.Response(200, json=payload("metadata"))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    transport = PublicTransport(cache_path=tmp_path / "cache.sqlite3", client=client)
    url = fred_series_url("DEMO_INDEX")
    result = transport.get(url, 3600)
    assert result.url == url and key not in result.url
    assert len(requests) == 1
    transport.get(url, 3600)
    assert len(requests) == 1
    monkeypatch.delenv("FRED_API_KEY")
    with pytest.raises(AtlasError) as error:
        transport.get(url, 3600)
    assert error.value.code == "CONFIG_REQUIRED"
    with transport.db.connect() as conn:
        cache_dump = "\n".join(conn.iterdump())
    assert key not in cache_dump
    transport.close()


@pytest.mark.parametrize("curated", [False, True])
def test_credential_redirects_and_echoes_are_rejected_without_cache_or_secret_logs(
    tmp_path, monkeypatch, caplog, curated
):
    key = "b" * 32
    monkeypatch.setenv("FRED_API_KEY", key)
    url = fred_url("us_initial_claims", *DATES) if curated else fred_series_url("DEMO_INDEX")
    for fault in ("redirect", "body", "escaped_body", "header"):
        requests = []

        def handler(request, fault=fault, requests=requests):
            requests.append(request)
            if fault == "redirect":
                return httpx.Response(302, headers={"Location": "https://example.invalid/steal"})
            data = payload("metadata")
            if fault == "body":
                data["echo"] = key
            if fault == "escaped_body":
                body = json.dumps({**data, "echo": key}).replace(key, "\\u0062" * 32).encode()
                return httpx.Response(200, content=body)
            return httpx.Response(200, json=data, headers={"etag": key})

        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        transport = PublicTransport(cache_path=tmp_path / f"{fault}.sqlite3", client=client)
        if fault == "header":
            transport.get(url, 3600)
        else:
            with pytest.raises(AtlasError):
                transport.get(url, 3600)
        assert len(requests) == 1
        assert requests[0].url.host == "api.stlouisfed.org"
        with transport.db.connect() as conn:
            assert key not in "\n".join(conn.iterdump())
        transport.close()
    assert key not in caplog.text
