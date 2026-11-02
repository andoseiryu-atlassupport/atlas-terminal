"""Fictional country coverage, source definitions, CPI bases and national rate limits."""

from itertools import product

import pytest
from jsonschema import Draft202012Validator, ValidationError

from atlas_terminal.client import Atlas
from atlas_terminal.country_macro_catalog import country_series_catalog
from atlas_terminal.errors import AtlasError
from atlas_terminal.global_macro_sources import (
    G20_AREAS,
    GLOBAL_AREAS,
    GLOBAL_INDICATORS,
    global_wdi_identity,
    wdi_data_url,
)
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_presets import ResearchPresetQuery, parse_preset

DATES = {"start_date": "2026-01-01", "end_date": "2026-09-30"}
CPI = {"dataset": "country_economics", "country": "NZ", "series": "cpi_index", **DATES}
YIELD = {
    "dataset": "country_bond_yields",
    "country": "CN",
    "series": "long_term_government_yield",
    **DATES,
}


class Mutating(PublicFixtures):
    def __init__(self, mutate=lambda url, body: body):
        self.mutate, self.calls = mutate, []

    def get(self, url, ttl):
        self.calls.append(url)
        snap = super().get(url, ttl)
        return Snapshot(self.mutate(url, snap.body), snap.url, snap.retrieved_at)


def application(mutate=lambda url, body: body):
    atlas = Atlas("fixture")
    atlas._research.public = Mutating(mutate)
    return atlas


@pytest.mark.parametrize(
    "country,series", list(product(("MY", "TH", "SG", "NZ"), GLOBAL_INDICATORS))
)
def test_additional_wdi_countries_preserve_source_units_and_do_not_become_g20_members(
    country, series
):
    q = {
        "dataset": "global_economics",
        "country": country,
        "series": series,
        "start_date": "2024-01-01",
        "end_date": "2025-12-31",
    }
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(q)
    with Atlas("fixture") as atlas:
        result = atlas.get_research(q)
        catalog = atlas.research_catalog(dataset="global_economics", country=country)
    assert result["records"] and result["is_synthetic"]
    for row in result["records"]:
        assert row["country_iso3"] == GLOBAL_AREAS[country]["iso3"]
        assert row["unit"] == GLOBAL_INDICATORS[series]["unit"]
        assert row["reference_area_kind"] == "country"
    assert all(
        not row["g20_member"] and row["membership_source"] is None
        for row in catalog["series_search"]["records"]
    )
    assert country not in G20_AREAS


@pytest.mark.parametrize(
    "spec", country_series_catalog(), ids=lambda s: s["country"] + "_" + s["series"]
)
def test_every_registered_country_series_keeps_precision_frequency_units_and_dual_provenance(spec):
    q = {"dataset": spec["dataset"], "country": spec["country"], "series": spec["series"], **DATES}
    Draft202012Validator(ResearchQuery.model_json_schema()).validate(q)
    with application() as atlas:
        result = atlas.get_research(q)
        assert len(atlas._research.public.calls) == 2
    rows = sorted(result["records"], key=lambda r: r["date"])
    assert [r["value"] for r in rows] == ["12.123456789012345678901234567890", "0", None]
    assert rows[0]["frequency"] == spec["frequency"]
    assert rows[0]["unit"] == spec["unit"] and rows[0]["unit_multiplier"] == 0
    assert rows[0]["source"]["xml_local_path"].endswith("/Obs[1]")
    assert rows[0]["source"]["raw_sha256"] != rows[0]["metadata_source"]["raw_sha256"]
    assert rows[0]["country_iso3"] == GLOBAL_AREAS[spec["country"]]["iso3"]
    assert rows[0]["benchmark_tenor_years"] == spec["benchmark_tenor_years"]
    assert rows[0]["published_at"] is None and not result["coverage"]["point_in_time"]
    assert result["source_id"] == spec["source_id"]


def test_new_zealand_keeps_native_quarters_and_original_index_base_with_date_boundaries():
    with application() as atlas:
        result = atlas.get_research(CPI | {"end_date": "2026-03-31"})
        assert [(r["period"], r["date"]) for r in result["records"]] == [("2026-Q1", "2026-03-31")]
        row = result["records"][0]
        assert row["base_period"] == row["common_reference_period"] == "2020A"
        assert row["frequency"] == "quarterly"
        small = atlas.get_research(CPI | {"limit": 1})
        assert small["coverage"]["truncated"]
        with pytest.raises(AtlasError):
            atlas.get_research(CPI | {"end_date": "2026-03-30"})
        panel = {
            "dataset": "macro_panel",
            "start_date": "2026-01-01",
            "end_date": "2026-09-30",
            "items": [
                {
                    "id": "nz_cpi",
                    "query": {
                        "dataset": "country_economics",
                        "country": "NZ",
                        "series": "cpi_index",
                    },
                    "aggregation": "last",
                }
            ],
        }
        with pytest.raises(AtlasError) as exc:
            atlas.get_research(panel | {"frequency": "monthly"})
        assert exc.value.code == "INVALID_INPUT"
        quarterly = atlas.get_research(panel | {"frequency": "quarterly", "allow_partial": True})
        assert quarterly["records"][0]["records"][0]["input_frequency"] == "quarterly"


@pytest.mark.parametrize(
    "old,new",
    [
        (b"Consumer price index (CPI)", b"Producer price index"),
        (b"All Items", b"Food only"),
        (b"Quarterly", b"Monthly"),
        (b' REFERENCE_PERIOD="2020A"', b' REFERENCE_PERIOD="2024A"'),
    ],
)
def test_cpi_definition_changes_fail_or_keep_the_new_publisher_base_without_rebasing(old, new):
    with application(lambda url, body: body.replace(old, new)) as atlas:
        if old.lstrip().startswith(b"REFERENCE"):
            row = atlas.get_research(CPI)["records"][-1]
            assert row["base_period"] == "2024A"
            assert row["common_reference_period"] == "2020A"
            assert row["value"] == "12.123456789012345678901234567890"
        else:
            with pytest.raises(AtlasError) as exc:
                atlas.get_research(CPI)
            assert exc.value.code == "SOURCE_UNAVAILABLE"
            assert len(atlas._research.public.calls) == 1


@pytest.mark.parametrize(
    "query,old,new",
    [
        (CPI, b' SCALE="0"', b' SCALE="3"'),
        (CPI, b' SCALE="0"', b""),
        (CPI, b' SCALE="0"', b' SCALE="0" UNIT="BP"'),
        (CPI, b' SCALE="0"', b' SCALE="0" UNIT_MULT="2"'),
        (YIELD, b'id="UNIT_MULT" value="0"', b'id="UNIT_MULT" value="3"'),
        (YIELD, b"Percent per annum", b"Basis points"),
        (YIELD, b"Long-term interest rates", b"Short-term interest rates"),
        (YIELD, b'value="CHN"', b'value="NZL"'),
        (CPI, b'COUNTRY="NZL"', b'COUNTRY="MYS"'),
    ],
)
def test_wrong_scale_country_or_rate_definition_never_silently_changes_values(query, old, new):
    with (
        application(lambda url, body: body.replace(old, new)) as atlas,
        pytest.raises(AtlasError) as exc,
    ):
        atlas.get_research(query)
    assert exc.value.code == "SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("country", ["TR", "SG"])
@pytest.mark.parametrize(
    "series", ["government_bond_rate", "long_term_government_yield", "treasury_bill_yield"]
)
def test_unregistered_bond_coverage_fails_before_fetch_without_a_rate_or_country_fallback(
    country, series
):
    with application() as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(YIELD | {"country": country, "series": series})
    assert exc.value.code == "INCOMPLETE_COVERAGE"
    assert atlas._research.public.calls == []


@pytest.mark.parametrize("country", ["SG", "CN"])
def test_exchange_rate_or_money_market_policy_is_not_invented_as_a_policy_target(country):
    with application() as atlas, pytest.raises(AtlasError) as exc:
        atlas.get_research(CPI | {"country": country, "series": "monetary_policy_related_rate"})
    assert exc.value.code == "INCOMPLETE_COVERAGE"
    assert atlas._research.public.calls == []


def test_signed_rates_forecast_flags_and_future_reference_periods_remain_explicit():
    query = CPI | {"country": "TR", "series": "monetary_policy_related_rate"}

    def negative(url, body):
        return body.replace(b'OBS_VALUE="12.123456789012345678901234567890"', b'OBS_VALUE="-0.25"')

    with application(negative) as atlas:
        assert atlas.get_research(query)["records"][-1]["value"] == "-0.25"
    with application(
        lambda url, body: body.replace(b'DERIVATION_TYPE="O"', b'DERIVATION_TYPE="SP"')
    ) as atlas:
        result = atlas.get_research(query)
        assert all(r["value"] is None for r in result["records"])
        assert result["coverage"]["unavailable_records"] == 3
        assert result["records"][-1]["source_value"] == "12.123456789012345678901234567890"
    with application(lambda url, body: body.replace(b"2026-Q", b"2099-Q")) as atlas:
        result = atlas.get_research(CPI | {"start_date": "2099-01-01", "end_date": "2099-09-30"})
        assert result["coverage"]["unavailable_records"] == 3
        assert all(r["value"] is None and r["status"] == "unavailable" for r in result["records"])


def test_registered_country_preset_does_not_expand_g20_membership_and_preview_makes_no_fetch():
    q = {"preset": "country_macro_overview", "country": "SG", "preview": True}
    Draft202012Validator(ResearchPresetQuery.model_json_schema()).validate(q)
    with application() as atlas:
        result = atlas.get_research_preset(q)
        assert len(result["plan"]["items"]) == 10 and not atlas._research.public.calls
        assert all(item["query"]["country"] == "SG" for item in result["plan"]["items"])
    with pytest.raises(AtlasError):
        parse_preset(q | {"preset": "g20_country_overview"})


@pytest.mark.parametrize(
    "patch",
    [{"country": "XX"}, {"series": "gdp_growth"}, {"source_id": "imf"}, {"frequency": "monthly"}],
)
def test_named_country_query_rejects_unknown_country_series_and_source_overrides(patch):
    with pytest.raises(AtlasError):
        parse_research(CPI | patch)
    with pytest.raises(ValidationError):
        Draft202012Validator(ResearchQuery.model_json_schema()).validate(CPI | patch)


def test_compact_projection_retains_rate_basis_and_resolvable_source_evidence():
    with application() as atlas:
        result = atlas.get_research(YIELD | {"response": {"view": "compact", "fields": ["value"]}})
        row = result["records"][-1]
        assert row["benchmark_tenor_years"] == 10
        assert row["benchmark_tenor_basis"] == "OECD_indicator_definition_not_instrument_maturity"
        assert row["rate_period_basis"] == "monthly_average_OECD_definition"
        assert row["unit"] == "percent_per_annum" and row["frequency"] == "monthly"


def test_new_country_urls_are_canonical_and_do_not_admit_extra_parameters():
    for country in ("MY", "TH", "SG", "NZ"):
        url = wdi_data_url(country, "gdp_growth", 2020, 2025)
        assert global_wdi_identity(url) == ("data", country, "gdp_growth")
        PublicTransport.validate_url(url)
        assert global_wdi_identity(url + "&api_key=secret") is None
        with pytest.raises(AtlasError):
            PublicTransport.validate_url(url + "&api_key=secret")
