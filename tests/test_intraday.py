"""Fictional intraday boundaries, exact evidence and source access protections."""

import copy
import json
from datetime import UTC, datetime
from importlib.resources import files
from urllib.parse import parse_qs

import httpx
import pytest
from jsonschema import Draft202012Validator

from atlas_terminal.client import Atlas
from atlas_terminal.equity_intraday import (
    INTRADAY_INTERVALS,
    intraday_identity,
    intraday_url,
    validate_intraday_window,
)
from atlas_terminal.equity_sources import yahoo_identity
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicTransport
from atlas_terminal.research_intraday import (
    MAX_INTRADAY_ROWS,
    intraday_result,
    intraday_rows,
    yahoo_intraday,
)
from atlas_terminal.research_models import ResearchQuery


def document(symbol="AAPL", interval="5m"):
    return json.loads(
        files("atlas_terminal")
        .joinpath("resources", "research", f"yahoo_intraday_{symbol}_{interval}.json")
        .read_bytes()
    )


def snapshot(data, retrieved="2026-10-29T21:00:00Z"):
    return Snapshot(
        json.dumps(data).encode(),
        "https://fixtures.atlas.invalid/research/intraday.json",
        retrieved,
    )


def query(**changes):
    return ResearchQuery.model_validate(
        {
            "dataset": "intraday_prices",
            "identifier": "AAPL",
            "start_date": "2026-10-28",
            "end_date": "2026-10-28",
            "interval": "5m",
            "limit": 500,
        }
        | changes
    )


class Source:
    synthetic = True

    def __init__(self, data=None, retrieved="2026-10-29T21:00:00Z"):
        self.data = document() if data is None else data
        self.retrieved = retrieved
        self.calls = []

    def get(self, url, ttl):
        self.calls.append((url, ttl))
        return snapshot(self.data, self.retrieved)

    def close(self):
        pass


def parse(data, retrieved="2026-10-29T21:00:00Z", **changes):
    return yahoo_intraday(Source(data, retrieved), query(**changes))


@pytest.mark.parametrize("interval", INTRADAY_INTERVALS)
@pytest.mark.parametrize("symbol", ["AAPL", "7203.T", "^N225", "USDJPY=X", "BTC-USD", "CL=F"])
def test_canonical_urls_separate_daily_queries_and_reject_extra_parameters(symbol, interval):
    url = intraday_url(symbol, "2026-10-27", "2026-10-28", interval)
    assert intraday_identity(url) == (symbol, "2026-10-27", "2026-10-28", interval)
    assert yahoo_identity(url) is None
    PublicTransport.validate_url(url)
    for modified in [
        url + "&crumb=private",
        url + "&interval=1m",
        url.replace("query1", "query2"),
        url.replace("includePrePost=false", "includePrePost=true"),
        url.replace("includeAdjustedClose=false", "includeAdjustedClose=true"),
    ]:
        assert intraday_identity(modified) is None
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(modified)


def test_dst_midnights_use_named_zone_without_fixed_offset():
    url = intraday_url("AAPL", "2026-10-31", "2026-11-02", "60m")
    params = parse_qs(url.split("?", 1)[1])
    assert params["period1"] == [str(int(datetime(2026, 10, 31, 4, tzinfo=UTC).timestamp()))]
    assert params["period2"] == [str(int(datetime(2026, 11, 3, 5, tzinfo=UTC).timestamp()))]
    assert intraday_identity(url) == ("AAPL", "2026-10-31", "2026-11-02", "60m")


@pytest.mark.parametrize(
    "start,end,interval",
    [
        ("2026-10-01", "2026-10-08", "1m"),
        ("2026-09-01", "2026-10-02", "5m"),
        ("2026-10-29", "2026-10-28", "5m"),
        ("2026-02-30", "2026-03-01", "5m"),
        ("20261028", "2026-10-28", "5m"),
        ("2026-10-28", "2026-10-28", "2m"),
    ],
)
def test_static_windows_fail_before_network(start, end, interval):
    with pytest.raises(AtlasError) as error:
        intraday_url("AAPL", start, end, interval)
    assert error.value.code == "INVALID_INPUT"


def test_retention_boundaries_are_local_inclusive_and_not_source_promises():
    now = datetime(2026, 10, 29, 1, tzinfo=UTC)  # still October 28 in New York
    validate_intraday_window("AAPL", "2026-10-22", "2026-10-28", "1m", now=now)
    validate_intraday_window("AAPL", "2026-08-30", "2026-09-01", "60m", now=now)
    for start, end, interval in [
        ("2026-10-21", "2026-10-22", "1m"),
        ("2026-08-29", "2026-08-30", "60m"),
        ("2026-10-29", "2026-10-29", "5m"),
    ]:
        with pytest.raises(AtlasError):
            validate_intraday_window("AAPL", start, end, interval, now=now)


def test_exact_original_cells_and_timestamps_not_adjusted_close():
    data = document()
    quote = data["chart"]["result"][0]["indicators"]["quote"][0]
    quote["close"][0] = "100.123456789012345678"
    result = parse(data)
    row = result["records"][-1]
    assert row["close"] == "100.123456789012345678" and row["volume"] == "0"
    assert row["observation_at"] == "2026-10-28T13:30:00Z"
    assert row["observation_local"] == "2026-10-28T09:30:00-04:00"
    assert row["bar_end_at"] == "2026-10-28T13:35:00Z"
    assert row["bar_duration_seconds"] == 300 and row["bar_status"] == "elapsed_interval"
    assert row["bar_finality_verified"] is False
    assert row["price_adjustment_verified"] is False and "adjusted_close" not in row
    assert row["price_basis"] == "yahoo_reported_intraday"
    for field in ("open", "high", "low", "close", "volume"):
        assert row["field_sources"][field]["json_pointer"].endswith(f"/quote/0/{field}/0")
        assert row["field_sources"][field]["raw_sha256"] == snapshot(data).sha256
    assert row["field_sources"]["observation_at"]["json_pointer"].endswith("/timestamp/0")
    assert row["field_sources"]["session"]["json_pointer"].endswith("/tradingPeriods/0/0")


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "MSFT"),
        ("currency", "JPY"),
        ("exchangeName", "TOR"),
        ("exchangeTimezoneName", "Asia/Tokyo"),
        ("instrumentType", "MUTUALFUND"),
        ("dataGranularity", "1d"),
        ("dataGranularity", "15m"),
    ],
)
def test_metadata_identity_and_exact_interval_are_required(field, value):
    data = document()
    data["chart"]["result"][0]["meta"][field] = value
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("bad", ["duplicate", "unordered", "float", "boolean", "oversize"])
def test_invalid_timestamp_arrays_fail_closed(bad):
    data = document()
    result = data["chart"]["result"][0]
    if bad == "duplicate":
        result["timestamp"][1] = result["timestamp"][0]
    elif bad == "unordered":
        result["timestamp"].reverse()
    elif bad == "float":
        result["timestamp"][0] = float(result["timestamp"][0])
    elif bad == "boolean":
        result["timestamp"][0] = True
    else:
        result["timestamp"] *= MAX_INTRADAY_ROWS // 3 + 1
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_null_cells_preserved_and_strict_quality_rejects(field, tmp_path):
    data = document()
    data["chart"]["result"][0]["indicators"]["quote"][0][field][1] = None
    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas._research.public = Source(data)
        result = atlas.get_research(query().model_dump(exclude_unset=True))
        assert result["records"][1][field] is None
        assert result["records"][1]["status"] == "unavailable"
        assert result["coverage"]["unavailable_records"] == 1
        with pytest.raises(AtlasError) as error:
            atlas.get_research(
                query().model_dump(exclude_unset=True) | {"requirements": {"allow_missing": False}}
            )
        assert error.value.code == "QUALITY_NOT_MET"


def test_current_interval_excluded_until_capture_time_boundary():
    data = document()
    result = parse(data, "2026-10-28T13:39:59Z")
    assert len(result["records"]) == 1
    assert result["coverage"]["excluded_unelapsed_interval_rows"] == 2
    assert result["coverage"]["partial"]
    result = parse(data, "2026-10-28T13:40:00Z")
    assert len(result["records"]) == 2
    assert result["records"][0]["observation_at"] == "2026-10-28T13:35:00Z"
    with pytest.raises(AtlasError) as error:
        parse(data, "2026-10-28T13:29:59Z")
    assert error.value.code == "INCOMPLETE_COVERAGE"


def test_source_session_end_shortens_bar_and_excludes_terminal_quote():
    data = document("7203.T", "60m")
    result = data["chart"]["result"][0]
    result["meta"]["dataGranularity"] = "1h"
    result["timestamp"].append(result["meta"]["currentTradingPeriod"]["regular"]["end"])
    for values in result["indicators"]["quote"][0].values():
        values.append(values[-1])
    got = parse(data, identifier="7203.T", interval="60m")
    assert got["instrument"]["source_interval"] == "1h"
    row = got["records"][0]
    assert row["bar_duration_seconds"] == 1800
    assert row["bar_end_at"] == "2026-10-28T06:30:00Z"
    assert row["quality_flags"] == ["shortened_session_interval"]
    assert got["coverage"]["excluded_outside_regular_session_rows"] == 1
    assert got["coverage"]["shortened_session_intervals"] == 1


def test_increasing_source_timestamps_cannot_describe_overlapping_intervals():
    data = document()
    result = data["chart"]["result"][0]
    result["timestamp"][1] = result["timestamp"][0] + 60
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "SOURCE_UNAVAILABLE"
    assert "overlap" in str(error.value)


def test_shortened_session_bar_can_precede_immediate_next_source_session():
    data = document("7203.T", "60m")
    result = data["chart"]["result"][0]
    prior = result["meta"]["tradingPeriods"][0][0]
    next_session = {"start": prior["end"], "end": prior["end"] + 3600}
    result["meta"]["tradingPeriods"].append([next_session])
    result["meta"]["currentTradingPeriod"]["regular"] = copy.deepcopy(next_session)
    result["timestamp"].append(prior["end"])
    for values in result["indicators"]["quote"][0].values():
        values.append(values[-1])
    got = parse(data, identifier="7203.T", interval="60m")
    assert len(got["records"]) == 5
    assert got["records"][1]["bar_duration_seconds"] == 1800
    assert got["records"][0]["observation_at"] == got["records"][1]["bar_end_at"]
    assert got["records"][0]["bar_duration_seconds"] == 3600


def test_absent_session_calendar_has_explicit_unverified_state_and_no_imputed_gaps():
    data = document()
    result = data["chart"]["result"][0]
    result["meta"].pop("tradingPeriods")
    result["meta"].pop("currentTradingPeriod")
    result["timestamp"][2] += 300
    got = parse(data)
    assert got["coverage"]["session_unverified_records"] == 3
    assert got["coverage"]["timestamp_gaps"] == 1
    assert got["records"][0]["timestamp_gap_seconds"] == 600
    assert all(row["session_verified"] is False for row in got["records"])
    assert len(got["records"]) == 3


def test_conflicting_ohlc_retained_without_repair_and_rejected_by_quality(tmp_path):
    data = document()
    data["chart"]["result"][0]["indicators"]["quote"][0]["high"][0] = "1"
    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas._research.public = Source(data)
        got = atlas.get_research(query().model_dump(exclude_unset=True))
        assert got["records"][-1]["high"] == "1"
        assert got["records"][-1]["status"] == "ambiguous"
        assert got["records"][-1]["quality_flags"] == ["ohlc_bounds_conflict"]
        with pytest.raises(AtlasError):
            atlas.get_research(
                query().model_dump(exclude_unset=True) | {"requirements": {"allow_missing": False}}
            )


@pytest.mark.parametrize(
    "bad", ["array_length", "negative_volume", "fractional_volume", "zero_price"]
)
def test_malformed_values_or_columns_fail_closed(bad):
    data = document()
    quote = data["chart"]["result"][0]["indicators"]["quote"][0]
    if bad == "array_length":
        quote["close"].pop()
    elif bad == "negative_volume":
        quote["volume"][0] = -1
    elif bad == "fractional_volume":
        quote["volume"][0] = "1.5"
    else:
        quote["close"][0] = 0
    with pytest.raises(AtlasError):
        parse(data)


def test_out_of_window_rows_are_counted_even_if_source_returns_adjacent_dates():
    data = document()
    result = data["chart"]["result"][0]
    result["timestamp"][-1] += 86400
    got = parse(data)
    assert len(got["records"]) == 2
    assert got["coverage"]["excluded_outside_date_rows"] == 1


def test_empty_nontrading_window_and_overlapping_session_calendar_fail_explicitly():
    data = document()
    result = data["chart"]["result"][0]
    result["timestamp"] = []
    for field in result["indicators"]["quote"][0]:
        result["indicators"]["quote"][0][field] = []
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "INCOMPLETE_COVERAGE"
    data = document()
    period = data["chart"]["result"][0]["meta"]["tradingPeriods"][0][0]
    data["chart"]["result"][0]["meta"]["tradingPeriods"].append([copy.deepcopy(period)])
    with pytest.raises(AtlasError) as error:
        parse(data)
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_enablement_checked_before_cache_and_credentials_never_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "Private Contact private@example.invalid")
    monkeypatch.setenv("FRED_API_KEY", "private-fred-test")
    monkeypatch.setenv("EDINET_API_KEY", "private-edinet-test")
    calls = []

    def request(req):
        calls.append(req)
        return httpx.Response(200, content=snapshot(document()).body)

    client = httpx.Client(transport=httpx.MockTransport(request))
    transport = PublicTransport(cache_path=tmp_path / "public.sqlite", client=client)
    url = intraday_url("AAPL", "2026-10-28", "2026-10-28", "5m")
    try:
        monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
        with pytest.raises(AtlasError) as error:
            transport.get(url, 900)
        assert error.value.code == "CONFIG_REQUIRED" and not calls
        monkeypatch.setenv("ATLAS_ENABLE_YAHOO_FINANCE", "1")
        transport.get(url, 900)
        assert len(calls) == 1 and str(calls[0].url) == url
        assert "private" not in str(calls[0].headers).lower()
        monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE")
        with pytest.raises(AtlasError) as error:
            transport.get(url, 900)
        assert error.value.code == "CONFIG_REQUIRED" and len(calls) == 1
    finally:
        transport.close()


def test_live_retention_rejected_before_transport_access():
    source = Source()
    source.synthetic = False
    with pytest.raises(AtlasError) as error:
        yahoo_intraday(source, query(start_date="2000-01-01", end_date="2000-01-01"))
    assert error.value.code == "INVALID_INPUT" and not source.calls


def test_full_and_compact_results_keep_intraday_interpretation_and_evidence(tmp_path):
    with Atlas("fixture", directory=tmp_path) as atlas:
        q = query().model_dump(exclude_unset=True)
        Draft202012Validator(ResearchQuery.model_json_schema()).validate(q)
        full = atlas.get_research(q)
        compact = atlas.get_research(q | {"response": {"view": "compact", "fields": ["close"]}})
        assert full["is_synthetic"] and len(full["records"]) == 3
        row = compact["records"][0]
        for field in ["interval", "observation_at", "bar_end_at", "bar_status", "price_basis"]:
            assert row[field] == full["records"][0][field]
        assert row["source"]["evidence_id"]


def test_hour_alias_and_dst_ambiguous_local_time_keep_two_distinct_utc_instants():
    data = document()
    result = data["chart"]["result"][0]
    result["meta"].update(dataGranularity="60m")
    result["meta"].pop("tradingPeriods")
    result["meta"].pop("currentTradingPeriod")
    result["timestamp"] = [
        int(datetime(2026, 11, 1, 5 + offset, 30, tzinfo=UTC).timestamp()) for offset in range(3)
    ]
    snap = snapshot(data, "2026-11-02T21:00:00Z")
    validated, _ = intraday_result(snap, "AAPL", "60m")
    rows, _ = intraday_rows(
        validated, snap, symbol="AAPL", interval="60m", start="2026-11-01", end="2026-11-01"
    )
    assert rows[0]["observation_local"] == "2026-11-01T01:30:00-04:00"
    assert rows[1]["observation_local"] == "2026-11-01T01:30:00-05:00"
    assert len(rows) == 3
    assert datetime.fromisoformat(rows[1]["bar_end_at"]) <= datetime.fromisoformat(
        snap.retrieved_at
    )
