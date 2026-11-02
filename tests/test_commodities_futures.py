"""Fictional commodity metadata, dated contracts and exact same-date spreads."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from jsonschema import Draft202012Validator, ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.commodity_catalog import COMMODITIES, PCPS_FLOW
from atlas_terminal.equity_sources import yahoo_identity, yahoo_url
from atlas_terminal.errors import AtlasError
from atlas_terminal.futures_catalog import FUTURES, MONTH_CODES, future_context, future_identity
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.research_sdmx import component_representation, period_end, structure


class MutatingSource(PublicFixtures):
    def __init__(self, mutate=lambda url, body: body):
        self.mutate, self.calls = mutate, []

    def get(self, url, ttl):
        self.calls.append(url)
        snap = super().get(url, ttl)
        return Snapshot(self.mutate(url, snap.body), snap.url, snap.retrieved_at)


def app(mutate=lambda url, body: body):
    atlas = Atlas("fixture")
    atlas._research.public = MutatingSource(mutate)
    return atlas


COMMODITY = {
    "dataset": "commodity_prices",
    "series": "gold",
    "start_date": "2026-01-01",
    "end_date": "2026-03-31",
}
CURVE = {
    "dataset": "futures_curve",
    "identifiers": ["GCJ27.CMX", "GCZ26.CMX", "GCG27.CMX"],
    "observation_date": "2026-10-20",
}


@pytest.mark.parametrize("series", COMMODITIES)
def test_commodity_physical_units_exact_values_zero_missing_and_dual_provenance(series):
    with app() as atlas:
        result = atlas.get_research(COMMODITY | {"series": series})
        assert len(atlas._research.public.calls) == 2  # One metadata read, one data read.
    rows = {r["period"]: r for r in result["records"]}
    assert rows["2026-M01"]["value"] == "123.12345678901234567890"
    assert rows["2026-M02"]["value"] == "0"
    assert rows["2026-M03"]["value"] is None
    row = rows["2026-M01"]
    code, label, unit = COMMODITIES[series]
    assert row["source_series"] == code and row["source_unit_label"] == label
    assert row["unit"] == unit and row["unit_multiplier"] == 0
    assert row["quote_unit_scale"] == ("0.01" if unit.startswith("US_cent_") else "1")
    assert row["date"] == "2026-01-31" and row["published_at"] is None
    assert row["source"]["xml_local_path"].endswith("/Series[1]/Obs[1]")
    assert row["source"]["raw_sha256"] != row["metadata_source"]["raw_sha256"]
    assert result["is_synthetic"] and not result["coverage"]["point_in_time"]


def test_commodity_period_boundary_and_panel_keep_source_units_and_nulls():
    with app() as atlas:
        bounded = atlas.get_research(COMMODITY | {"end_date": "2026-02-27"})
        assert [r["period"] for r in bounded["records"]] == ["2026-M01"]
        panel = atlas.get_research(
            {
                "dataset": "macro_panel",
                "items": [
                    {
                        "id": "gold",
                        "aggregation": "last",
                        "query": {"dataset": "commodity_prices", "series": "gold"},
                    }
                ],
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
                "allow_partial": True,
            }
        )
    assert panel["records"] and panel["coverage"]["missing_cells"] == 1
    assert panel["comparability"]["currency_conversion"] is False


@pytest.mark.parametrize(
    "old,new",
    [
        (b'position="0"', b'position="1"'),
        (b'position="2"', b'position="4"'),
        (b"Gold, US dollars", b"Gold, US cents"),
        (b">World<", b">United States<"),
        (b">US dollars<", b">Index<"),
        (b'maintainableParentVersion="1.0.0"', b'maintainableParentVersion="2.0.0"'),
        (b' SCALE="0"', b' SCALE="3"'),
        (b' SCALE="0"', b""),
    ],
)
def test_changed_commodity_dimension_identity_unit_or_scale_fails_closed(old, new):
    with app(lambda url, body: body.replace(old, new)) as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(COMMODITY)
    assert exc.value.code == "SOURCE_UNAVAILABLE"


def test_zero_based_imf_dimensions_preserve_source_order():
    spec = structure(PublicFixtures(), "imf", PCPS_FLOW)
    assert [d["position"] for d in spec.dimensions] == [0, 1, 2, 3]
    assert [d["id"] for d in spec.dimensions] == [
        "COUNTRY",
        "INDICATOR",
        "DATA_TRANSFORMATION",
        "FREQUENCY",
    ]


def test_core_representation_is_scoped_and_local_override_takes_precedence():
    root = ET.fromstring(
        '<Root><ConceptScheme id="S" agencyID="A" version="1">'
        '<Concept id="C"><CoreRepresentation><Enumeration><Ref id="RIGHT"/>'
        "</Enumeration></CoreRepresentation></Concept></ConceptScheme>"
        '<ConceptScheme id="S" agencyID="B" version="1">'
        '<Concept id="C"><CoreRepresentation/></Concept></ConceptScheme>'
        '<Dimension><ConceptIdentity><Ref id="C" agencyID="A" '
        'maintainableParentID="S" maintainableParentVersion="1"/></ConceptIdentity>'
        "</Dimension></Root>"
    )
    dimension = root.find("Dimension")
    assert component_representation(root, dimension)[0].find("Enumeration/Ref").get("id") == "RIGHT"
    root.append(deepcopy(root[0]))
    with pytest.raises(AtlasError):
        component_representation(root, dimension)
    local = ET.SubElement(dimension, "LocalRepresentation")
    assert component_representation(root, dimension) == [local]


@pytest.mark.parametrize("period", ["2024-02", "2024-M02"])
def test_monthly_source_spelling_supports_leap_years(period):
    assert period_end(period, "M") == "2024-02-29"


@pytest.mark.parametrize("period", ["2026-M00", "2026-M13", "2026-M1", "2026-2", "2026-MM02"])
def test_invalid_monthly_spelling_is_not_reinterpreted(period):
    with pytest.raises(AtlasError):
        period_end(period, "M")


@pytest.mark.parametrize("root", FUTURES)
def test_future_families_and_dated_identifiers_validate_source_market_and_keep_evidence(root):
    spec = FUTURES[root]
    symbol = root + "=F"
    with app() as atlas:
        result = atlas.get_research(
            {
                "dataset": "futures_prices",
                "identifier": symbol,
                "start_date": "2026-10-19",
                "end_date": "2026-10-23",
            }
        )
    assert result["instrument"]["source_instrument_type"] == "FUTURE"
    assert result["instrument"]["contract_role"] == "rolling_front_reference"
    assert result["records"][0]["underlying_root"] == root
    assert result["records"][0]["unit"] == "source_quote_units"
    quoted = "USX" if spec.quote_measure_unit.startswith("US_cent_") else "USD"
    assert result["instrument"]["currency"] == quoted
    assert result["records"][0]["quote_unit_scale"] == ("0.01" if quoted == "USX" else "1")
    assert result["records"][0]["denomination_currency"] == "USD"
    for code, month in MONTH_CODES.items():
        dated = f"{root}{code}27.{spec.exchange}"
        assert future_identity(dated) == (root, f"2027-{month:02d}")
        url = yahoo_url(dated, "2026-10-19", "2026-10-23")
        assert yahoo_identity(url) == (dated, "2026-10-19", "2026-10-23")
        context = future_context(dated)
        assert context["contract_multiplier"] is None and context["expiry_date"] is None
        assert not context["source_quote_convention_verified"]


@pytest.mark.parametrize(
    "symbol", ["CT=F", "DX=F", "GCZ19.CMX", "GCZ50.CMX", "GCA27.CMX", "GCZ26.NYM"]
)
def test_unregistered_or_ambiguous_future_is_not_guessed(symbol):
    with pytest.raises(AtlasError):
        ResearchQuery.model_validate({"dataset": "futures_prices", "identifier": symbol})


def test_nonpositive_futures_values_and_zero_volume_are_retained():
    with app() as atlas:
        result = atlas.get_research({"dataset": "futures_prices", "identifier": "RB=F"})
    by_date = {r["date"]: r for r in result["records"]}
    assert by_date["2026-10-20"]["close"] == "0"
    assert by_date["2026-10-20"]["volume"] == "0"
    assert by_date["2026-10-21"]["close"] == "-5"


def test_same_date_curve_orders_contract_months_and_preserves_exact_spread_inputs():
    with app() as atlas:
        result = atlas.get_research(CURVE)
    quotes = result["records"][:3]
    spreads = result["records"][3:]
    assert [r["contract_month"] for r in quotes] == ["2026-12", "2027-02", "2027-04"]
    assert [r["value"] for r in spreads] == ["10", "-5"]
    for row in spreads:
        near, far = row["inputs"]
        assert Decimal(row["value"]) == Decimal(far["value"]) - Decimal(near["value"])
        assert near["source"]["json_pointer"].endswith("/close/1")
        assert far["source"]["raw_sha256"]
        assert near["date"] == far["date"] == CURVE["observation_date"]
        assert row["expiry_date"] is None and not row["settlement_price_verified"]

    def precise_quotes(url, body):
        data = json.loads(body)
        quote = data["chart"]["result"][0]["indicators"]["quote"][0]
        quote["close"][1] = (
            "100.123456789012345678901234567890"
            if "GCZ26" in url
            else "110.123456789012345678901234567891"
        )
        quote["high"][1], quote["low"][1] = "999", "0"
        return json.dumps(data).encode()

    with app(precise_quotes) as atlas:
        precise = atlas.get_research(CURVE | {"identifiers": ["GCZ26.CMX", "GCG27.CMX"]})
    assert precise["records"][2]["value"] == "10.000000000000000000000000000001"


@pytest.mark.parametrize(
    "identifiers", [["GC=F", "GCG27.CMX"], ["GCZ26.CMX", "CLZ26.NYM"], ["GCZ26.CMX"]]
)
def test_curve_runtime_and_schema_reject_front_references_mixed_underlyings_and_singletons(
    identifiers,
):
    q = CURVE | {"identifiers": identifiers}
    with pytest.raises(AtlasError):
        ResearchQuery.model_validate(q)
    with pytest.raises(ValidationError):
        Draft202012Validator(ResearchQuery.model_json_schema()).validate(q)


def test_curve_never_falls_back_to_a_previous_session_even_with_partial_enabled():
    with app() as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(CURVE | {"observation_date": "2026-10-24", "allow_partial": True})
    assert exc.value.code == "INCOMPLETE_COVERAGE"


def test_null_curve_quotes_require_partial_and_never_enter_spread_arithmetic():
    def mutate(url, body):
        if "GCG27.CMX" in url:
            data = json.loads(body)
            data["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
            return json.dumps(data).encode()
        return body

    with app(mutate) as atlas:
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(CURVE)
        assert exc.value.code == "INCOMPLETE_COVERAGE"
        result = atlas.get_research(CURVE | {"allow_partial": True})
    assert result["coverage"]["partial"]
    assert all(r["value"] is None for r in result["records"][3:])


def test_cent_quotes_remain_unconverted_and_curve_rejects_mixed_denominations():
    query = {
        "dataset": "futures_curve",
        "identifiers": ["ZCZ26.CBT", "ZCH27.CBT"],
        "observation_date": "2026-10-20",
    }
    with app() as atlas:
        result = atlas.get_research(query)
        assert all(
            r["currency"] == "USX" and r["quote_unit_scale"] == "0.01" for r in result["records"]
        )

    def mutate(url, body):
        return body.replace(b'"currency":"USX"', b'"currency":"USD"') if "ZCH27" in url else body

    with app(mutate) as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(query)
    assert exc.value.code == "INCOMPLETE_COVERAGE"


def test_source_type_alias_or_wrong_currency_does_not_become_a_future():
    for field, value in [("instrumentType", "ALTSYMBOL"), ("currency", "EUR")]:

        def mutate(url, body, field=field, value=value):
            data = json.loads(body)
            data["chart"]["result"][0]["meta"][field] = value
            return json.dumps(data).encode()

        with app(mutate) as atlas, pytest.raises(AtlasError) as exc:
            atlas.get_research({"dataset": "futures_prices", "identifier": "GC=F"})
        assert exc.value.code == "SOURCE_UNAVAILABLE"


def test_invalid_future_ohlc_fails_even_when_partial_curve_is_requested():
    def mutate(url, body):
        if "GCG27.CMX" in url:
            data = json.loads(body)
            data["chart"]["result"][0]["indicators"]["quote"][0]["high"][1] = -100
            return json.dumps(data).encode()
        return body

    with app(mutate) as atlas:
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(CURVE)
        assert exc.value.code == "SOURCE_UNAVAILABLE"
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(CURVE | {"allow_partial": True})
        assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("dataset", ["futures_prices", "futures_curve"])
def test_optional_future_gate_precedes_source_and_cursor_reads(monkeypatch, dataset):
    monkeypatch.delenv("ATLAS_ENABLE_YAHOO_FINANCE", raising=False)
    query = CURVE if dataset == "futures_curve" else {"dataset": dataset, "identifier": "GC=F"}
    with Atlas("public") as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(query)
    assert exc.value.code == "CONFIG_REQUIRED"
    assert atlas._research.public is None


def test_curve_current_session_is_not_reported_as_a_completed_daily_close():
    today = datetime.now(UTC).date().isoformat()
    with app() as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(CURVE | {"observation_date": today})
    assert exc.value.code == "INCOMPLETE_COVERAGE"
