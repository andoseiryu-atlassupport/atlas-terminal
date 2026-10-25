"""Fictional SDMX bytes, source identity, discovery and transport boundaries."""

from copy import deepcopy
from importlib.resources import files
from xml.etree import ElementTree as ET

import httpx
import pytest
from jsonschema import Draft202012Validator, ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.sdmx_sources import data_url, structure_url
from atlas_terminal.util import digest

RESOURCE = files("atlas_terminal").joinpath("resources", "research")


def query(source="imf"):
    return {
        "dataset": "international_economics",
        "source_id": source,
        "dataflow": source.upper() + ".DEMO,ATLAS_MACRO,1.0",
        "dimensions": {"FREQUENCY": "M", "COUNTRY": "USA", "INDICATOR": "CPI"}
        if source == "imf"
        else {"FREQ": "M", "REF_AREA": "JPN", "MEASURE": "CPI"},
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
    }


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


class Sources(PublicFixtures):
    def __init__(self, data=None, metadata=None):
        self.data, self.metadata = data, metadata
        self.calls = []

    def get(self, url, ttl):
        self.calls.append(url)
        original = super().get(url, ttl)
        body = self.metadata if "/2.1/dataflow/" in url or "/rest/dataflow/" in url else self.data
        return Snapshot(body, original.url, original.retrieved_at) if body is not None else original


def application(data=None, metadata=None):
    atlas = Atlas("fixture")
    atlas._research.public = Sources(data, metadata)
    return atlas


@pytest.mark.parametrize("source", ["imf", "oecd"])
def test_discovery_resolves_ordered_dimensions_labels_and_exact_decimal_provenance(source):
    q = query(source)
    with application() as atlas:
        catalog = atlas.get_research(
            {"dataset": "sdmx_catalog", "source_id": source, "search": "macro"}
        )
        detail = atlas.get_research(catalog["records"][0]["query"])
        result = atlas.get_research(q)
        filtered = atlas.get_research(
            {
                **detail["query"],
                "dimension": "COUNTRY" if source == "imf" else "REF_AREA",
                "search": "Japan",
            }
        )
    assert [part["position"] for part in detail["dimensions"]] == [1, 2, 3]
    assert filtered["records"][0]["code"] == "JPN"
    row = result["records"][0]
    assert row["value"] == ("112.3000" if source == "imf" else "103.0000")
    assert row["date"] == "2026-03-31" and row["frequency"] == "monthly"
    assert row["unit"] == "IX" and row["unit_multiplier"] == 0 and row["base_period"] == "2020"
    assert row["seasonally_adjusted"] is False and row["published_at"] is None
    assert row["source"]["raw_sha256"] == digest(
        RESOURCE.joinpath(f"sdmx_{source}_data.xml").read_bytes()
    )
    assert row["source"]["xml_local_path"].endswith("/Obs[3]")
    assert row["metadata_source"]["raw_sha256"] != row["source"]["raw_sha256"]
    if source == "imf":
        assert row["observation_status_label"] == "Forecast"
    assert result["is_synthetic"] and not result["coverage"]["truncated"]


def test_sdmx_date_filter_truncation_missing_and_unknown_metadata_are_explicit():
    raw = RESOURCE.joinpath("sdmx_imf_data.xml").read_bytes()
    raw = raw.replace(b'OBS_VALUE="111.2000"', b'OBS_VALUE=""').replace(b' UNIT_MULT="0"', b"")
    with application(data=raw) as atlas:
        result = atlas.get_research(query() | {"start_date": "2026-02-01"})
        assert len(result["records"]) == 2 and result["records"][1]["value"] is None
        assert result["records"][0]["unit_multiplier"] is None
        small = atlas.get_research(query() | {"limit": 1})
        assert small["coverage"]["truncated"] and small["coverage"]["observed_records"] == 3
        with pytest.raises(AtlasError, match="requirements"):
            atlas.get_research(query() | {"requirements": {"allow_missing": False}})


@pytest.mark.parametrize(
    "fault", ["series", "duplicate", "nonfinite", "scale", "frequency", "group", "date"]
)
def test_wrong_or_ambiguous_sdmx_data_never_becomes_an_observation(fault):
    root = ET.fromstring(RESOURCE.joinpath("sdmx_imf_data.xml").read_bytes())
    dataset = root.find("{*}DataSet")
    series = dataset.find("Series")
    if fault == "series":
        series.set("COUNTRY", "JPN")
    elif fault == "duplicate":
        series.append(deepcopy(series[0]))
    elif fault == "nonfinite":
        series[0].set("OBS_VALUE", "NaN")
    elif fault == "scale":
        series.set("UNIT_MULT", "99999")
    elif fault == "frequency":
        series.set("FREQUENCY", "D")
    elif fault == "group":
        ET.SubElement(dataset, "Group", UNIT_MEASURE="BAD")
    else:
        series[0].set("TIME_PERIOD", "2026-13")
    with application(data=ET.tostring(root)) as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(query())
    assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("fault", ["missing_dimension", "unknown_code", "extra_dimension"])
def test_dimension_validation_stops_before_data_request(fault):
    q = query()
    if fault == "missing_dimension":
        q["dimensions"].pop("COUNTRY")
    elif fault == "unknown_code":
        q["dimensions"]["COUNTRY"] = "UNKNOWN"
    else:
        q["dimensions"]["UNREQUESTED"] = "BAD"
    with application() as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(q)
    assert exc.value.code == "INVALID_INPUT"
    assert len(atlas._research.public.calls) == 1


@pytest.mark.parametrize("fault", ["version", "missing_dsd", "duplicate_code", "position"])
def test_metadata_identity_and_dimension_order_are_verified(fault):
    raw = RESOURCE.joinpath("sdmx_imf_structure.xml").read_bytes()
    if fault == "version":
        raw = raw.replace(b'version="1.0"', b'version="2.0"')
    elif fault == "missing_dsd":
        raw = raw.replace(b'class="DataStructure"', b'class="DataStructure" missing="true"')
        raw = raw.replace(
            b'<Ref agencyID="IMF.DEMO" id="DSD_MACRO"', b'<Ref agencyID="IMF.DEMO" id="NO_DSD"'
        )
    elif fault == "duplicate_code":
        raw = raw.replace(b'id="JPN"', b'id="USA"')
    else:
        raw = raw.replace(b'position="2"', b'position="1"')
    with application(metadata=raw) as atlas, pytest.raises(AtlasError):
        atlas.get_research(query())
    assert len(atlas._research.public.calls) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"source_id": "other"},
        {"dataflow": "../../secrets"},
        {"dimensions": {}},
        {"dimensions": {"FREQ": "*"}},
        {"dimensions": {"COUNTRY": "USA+JPN"}},
        {"dimensions": {"COUNTRY": "https://evil.invalid"}},
        {"start_date": None},
        {"end_date": "2025-01-01"},
        {"start_date": "1900-01-01"},
        {"as_of": "2020-01-01"},
    ],
)
def test_invalid_international_queries_are_rejected_without_fetch(patch):
    with application() as atlas, pytest.raises(AtlasError):
        atlas.get_research(query() | patch)
    assert not atlas._research.public.calls


def test_required_new_query_fields_are_not_nullable_in_agent_schema():
    validator = Draft202012Validator(ResearchQuery.model_json_schema())
    validator.validate(query())
    for name in ("source_id", "dataflow", "start_date", "end_date", "dimensions"):
        with pytest.raises(ValidationError):
            validator.validate(query() | {name: None})
    with pytest.raises(ValidationError):
        validator.validate(query() | {"dimensions": {}})


def test_new_series_codes_are_resolved_from_metadata_without_a_code_change():
    data = (
        RESOURCE.joinpath("sdmx_imf_data.xml")
        .read_bytes()
        .replace(b'INDICATOR="CPI"', b'INDICATOR="NEW_INDICATOR"')
    )
    metadata = (
        RESOURCE.joinpath("sdmx_imf_structure.xml")
        .read_bytes()
        .replace(b'id="CPI"', b'id="NEW_INDICATOR"')
    )
    q = query()
    q["dimensions"]["INDICATOR"] = "NEW_INDICATOR"
    with application(data=data, metadata=metadata) as atlas:
        result = atlas.get_research(q)
    assert result["records"][0]["dimensions"]["INDICATOR"] == "NEW_INDICATOR"
    assert result["records"][0]["value"] == "112.3000"


@pytest.mark.parametrize(
    "frequency,periods,expected",
    [
        ("Q", ["2026-Q1", "2026-Q2", "2026-Q3"], ["2026-09-30", "2026-06-30", "2026-03-31"]),
        ("A", ["2024", "2025", "2026"], ["2026-12-31", "2025-12-31", "2024-12-31"]),
    ],
)
def test_quarterly_and_annual_period_ends_are_preserved(frequency, periods, expected):
    root = ET.fromstring(RESOURCE.joinpath("sdmx_imf_data.xml").read_bytes())
    series = root.find("{*}DataSet/Series")
    series.set("FREQUENCY", frequency)
    for obs, period in zip(series, periods, strict=True):
        obs.set("TIME_PERIOD", period)
    q = query() | {"start_date": "2024-01-01", "end_date": "2026-12-31"}
    q["dimensions"]["FREQUENCY"] = frequency
    with application(data=ET.tostring(root)) as atlas:
        result = atlas.get_research(q)
    assert [row["date"] for row in result["records"]] == expected


@pytest.mark.parametrize("source", ["imf", "oecd"])
def test_sdmx_transport_negotiates_xml_keeps_cache_and_rejects_arbitrary_urls(tmp_path, source):
    requests = []

    def handle(request):
        requests.append(request)
        assert "sdmx.structure+xml" in request.headers["accept"]
        return httpx.Response(
            200, content=RESOURCE.joinpath(f"sdmx_{source}_structure.xml").read_bytes()
        )

    with_client = httpx.Client(transport=httpx.MockTransport(handle))
    transport = PublicTransport(
        client=with_client, cache_path=tmp_path / "cache.sqlite3", sleep=lambda _: None
    )
    url = structure_url(source)
    transport.get(url, 86400)
    transport.get(url, 86400)
    assert len(requests) == 1
    for invalid in [
        url + "&redirect=https://evil.invalid",
        url.replace("https://", "http://"),
        url.replace("/dataflow/", "/../"),
        url + "#fragment",
    ]:
        with pytest.raises(AtlasError):
            transport.get(invalid, 0)
    transport.close()


def test_exact_data_urls_are_allowed_but_wildcards_and_unbounded_windows_are_not():
    for source in ("imf", "oecd"):
        flow = query(source)["dataflow"]
        url = data_url(source, flow, "M.USA.CPI", "2026-01-01", "2026-03-31")
        PublicTransport.validate_url(url)
        for invalid in [
            url.replace("M.USA.CPI", "M..CPI"),
            url.replace("M.USA.CPI", "M.*.CPI"),
            url + "&lastNObservations=1",
            url.replace("2026-01-01", "1900-01-01"),
        ]:
            with pytest.raises(AtlasError):
                PublicTransport.validate_url(invalid)


def test_oecd_attempt_limit_is_rolling_persistent_and_retry_attempts_count(tmp_path):
    now = [10000.0]
    kwargs = {
        "cache_path": tmp_path / "cache.sqlite3",
        "clock": lambda: now[0],
        "sleep": lambda _: None,
    }
    url = structure_url("oecd")
    one = PublicTransport(**kwargs)
    for _ in range(60):
        one.before_request(url)
    two = PublicTransport(**kwargs)
    with pytest.raises(AtlasError) as limited:
        two.before_request(url)
    assert limited.value.code == "SOURCE_RATE_LIMITED" and limited.value.retry_after_seconds == 3600
    now[0] += 3599
    with pytest.raises(AtlasError) as limited:
        two.before_request(url)
    assert limited.value.retry_after_seconds == 1
    now[0] += 1
    two.before_request(url)
    one.close()
    two.close()


def test_registered_series_search_filters_return_executable_queries_and_stay_offline():
    result = Atlas.research_catalog(
        search="物価", country="JP", frequency="monthly", source_id="estat"
    )
    rows = result["series_search"]["records"]
    assert [row["query"] for row in rows] == [
        {"dataset": "japan_economics", "series": "cpi"},
        {"dataset": "japan_economics", "series": "core_cpi"},
    ]
    assert result["series_search"]["network_access"] is False
    for row in Atlas.research_catalog()["series_search"]["records"]:
        parse_research(row["query"])
    exact = Atlas.research_catalog(search="CUUR0000SA0")["series_search"]["records"]
    assert exact[0]["query"] == {"dataset": "economics", "series": "cpi"}
    assert Atlas.research_catalog(search="no-such-series")["series_search"]["matched"] == 0
    units = Atlas.research_catalog(country="US", unit="percent_per_annum")["series_search"][
        "records"
    ]
    assert {row["series"] for row in units} == {"effr", "sofr"}
    for filters in ({"source_id": "unknown"}, {"search": "a" * 101}, {"country": "japan"}):
        with pytest.raises(AtlasError):
            Atlas.research_catalog(**filters)
