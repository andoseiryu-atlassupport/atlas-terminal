"""G20 geography, WDI definitions, source-cell evidence and shared interface contracts."""

import json
import subprocess
import sys
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.global_macro_sources import (
    global_wdi_identity,
    wdi_country_url,
    wdi_data_url,
    wdi_metadata_url,
)
from atlas_terminal.providers import Snapshot, decode_json
from atlas_terminal.public_sources import PublicFixtures, PublicTransport
from atlas_terminal.research_models import ResearchQuery, parse_research
from atlas_terminal.research_presets import ResearchPresetQuery, parse_preset
from atlas_terminal.server import create_app

DATES = {"start_date": "2023-01-01", "end_date": "2025-12-31"}


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))


def query(country="IN", series="gdp_growth", **extra):
    return {"dataset": "global_economics", "country": country, "series": series, **DATES, **extra}


class Changed(PublicFixtures):
    def __init__(self, kind, change):
        self.kind, self.change = kind, change

    def get(self, url, ttl):
        snapshot = super().get(url, ttl)
        if global_wdi_identity(url)[0] == self.kind:
            raw = decode_json(snapshot.body)
            self.change(raw)
            return Snapshot(
                json.dumps(raw, default=str).encode(), snapshot.url, snapshot.retrieved_at
            )
        return snapshot


@pytest.mark.parametrize(
    "country,iso3",
    [
        ("AR", "ARG"),
        ("AU", "AUS"),
        ("BR", "BRA"),
        ("CA", "CAN"),
        ("CN", "CHN"),
        ("DE", "DEU"),
        ("FR", "FRA"),
        ("GB", "GBR"),
        ("ID", "IDN"),
        ("IN", "IND"),
        ("IT", "ITA"),
        ("JP", "JPN"),
        ("KR", "KOR"),
        ("MX", "MEX"),
        ("RU", "RUS"),
        ("SA", "SAU"),
        ("TR", "TUR"),
        ("US", "USA"),
        ("ZA", "ZAF"),
        ("EU", "EUU"),
    ],
)
def test_country_reads_keep_both_source_codes_and_distinguish_the_eu_aggregate(country, iso3):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(country))
    row = result["records"][0]
    assert (row["country"], row["country_iso3"]) == (country, iso3)
    assert row["reference_area_kind"] == ("regional_aggregate" if country == "EU" else "country")
    assert result["is_synthetic"] and result["source_id"] == "world_bank"
    assert row["value"] == "123.12345678901234567890"
    assert [r["value"] for r in result["records"]] == [row["value"], "0", None]
    assert result["attributions"][0]["terms_url"].startswith("https://datacatalog.worldbank.org/")


@pytest.mark.parametrize(
    "series,unit,definition",
    [
        ("gdp_nominal", "USD", "Current-price"),
        ("gdp_real", "constant_2015_USD", "2015"),
        ("gdp_ppp", "current_international_dollar", "PPP"),
        ("unemployment", "percent", "Modeled ILO"),
        ("central_government_debt", "percent_of_GDP", "Central-government"),
        ("official_exchange_rate", "LCU_per_USD", "period-average"),
        ("population", "persons", "not imply a year-end census"),
    ],
)
def test_incompatible_price_bases_estimates_debt_scopes_and_fx_conventions_stay_explicit(
    series, unit, definition
):
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(series=series))
    row = result["records"][0]
    assert row["unit"] == unit and definition in row["definition"]
    assert row["source_unit"] == "" and row["unit_multiplier"] == 0
    assert row["value"] == "123.12345678901234567890"  # Display decimals never round source cells.
    assert row["frequency"] == "annual" and not row["annualized"]
    assert row["date_basis"] == "source_year_label_mapped_to_december_31"
    assert row["published_at"] is None
    if series == "official_exchange_rate":
        assert row.get("currency") is None  # Current ISO codes cannot label historic denominations.


@pytest.mark.parametrize(
    "kind,change",
    [
        ("country", lambda r: r[1][0].update(id="IDN")),
        ("country", lambda r: r[1][0].update(iso2Code="ID")),
        ("country", lambda r: r[1][0]["region"].update(id="NA")),
        ("country", lambda r: r[0].update(total=2)),
        ("metadata", lambda r: r[1][0].update(id="NY.GDP.MKTP.CD")),
        ("metadata", lambda r: r[1][0]["source"].update(id="11")),
        ("metadata", lambda r: r[1][0].update(unit="percent")),
        ("metadata", lambda r: r[1][0].update(name="GDP growth (quarterly %)")),
        ("metadata", lambda r: r[1][0].update(sourceNote="")),
        ("metadata", lambda r: r[1][0].update(sourceOrganization=None)),
        ("metadata", lambda r: r[0].update(pages=2)),
        ("data", lambda r: r[0].update(page=True)),
        ("data", lambda r: r[0].update(pages=2)),
        ("data", lambda r: r[0].update(total=4)),
        ("data", lambda r: r[0].update(sourceid="11")),
        ("data", lambda r: r[0].update(lastupdated="2026-02-30")),
        ("data", lambda r: r[1][0].update(countryiso3code="IDN")),
        ("data", lambda r: r[1][0]["country"].update(id="ID")),
        ("data", lambda r: r[1][0]["indicator"].update(id="FP.CPI.TOTL.ZG")),
        ("data", lambda r: r[1][0].update(unit="percent")),
        ("data", lambda r: r[1][0].update(date="2025-Q4")),
        ("data", lambda r: r[1][0].update(date="0000")),
        ("data", lambda r: r[1][0].update(date="2024")),
        ("data", lambda r: r[1][0].pop("obs_status")),
        ("data", lambda r: r[1][0].update(decimal=False)),
        ("data", lambda r: r[1][0].update(value="NaN")),
        ("data", lambda r: r[1][0].pop("value")),
    ],
)
def test_source_changes_fail_closed_without_mixing_geographies_or_silently_rebasing(kind, change):
    with Atlas("fixture") as atlas:
        atlas._research.public = Changed(kind, change)
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query())
    assert error.value.code == "SOURCE_UNAVAILABLE"


def test_missing_years_nil_negative_growth_and_flags_preserve_the_original_cells():
    def changes(raw):
        raw[1][0].update(value="-2.125", obs_status="F")
        raw[1][1].update(value="-1.5")

    with Atlas("fixture") as atlas:
        atlas._research.public = Changed("data", changes)
        result = atlas.get_research(query(start_date="2020-01-01"))
    first, second, third = result["records"]
    assert first["source_value"] == "-2.125" and first["value"] is None
    assert first["observation_status"] == "F" and first["status"] == "unavailable"
    assert second["value"] == "-1.5" and third["value"] is None
    assert result["coverage"]["missing_years"] == ["2020", "2021", "2022"]
    assert len(result["records"]) == 3  # Absent years never become fabricated observations.


def test_year_labels_use_inclusive_bounds_without_claiming_actual_fiscal_period_ends():
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query(start_date="2024-12-31", end_date="2025-12-30"))
    assert [r["date"] for r in result["records"]] == ["2024-12-31"]
    assert result["records"][0]["reference_period_kind"] == "publisher_reference_year"
    assert not result["coverage"]["point_in_time"]


def test_future_reference_year_values_are_retained_as_unavailable(monkeypatch):
    class Earlier(date):
        @classmethod
        def today(cls):
            return cls(2024, 11, 1)

    monkeypatch.setattr("atlas_terminal.research_global_macro.date", Earlier)
    with Atlas("fixture") as atlas:
        result = atlas.get_research(query())
    row = result["records"][0]
    assert row["source_value"] == "123.12345678901234567890" and row["value"] is None
    assert "Future" in row["reason"]


def test_empty_publisher_response_fails_explicitly_without_fabricating_observations():
    def empty(raw):
        raw[0].update(total=0, pages=0)
        raw[1] = None

    with Atlas("fixture") as atlas:
        atlas._research.public = Changed("data", empty)
        with pytest.raises(AtlasError) as error:
            atlas.get_research(query())
    assert error.value.code == "INCOMPLETE_COVERAGE"


def test_catalog_schemas_preset_preview_and_runtime_require_an_explicit_supported_country():
    schema = Draft202012Validator(ResearchQuery.model_json_schema())
    preset_schema = Draft202012Validator(ResearchPresetQuery.model_json_schema())
    for bad in [
        query(country="XX"),
        query(country=None),
        query(series="policy_rate"),
        {"dataset": "global_economics", "country": "IN"},
        query(identifier="AAPL"),
    ]:
        with pytest.raises(AtlasError):
            parse_research(bad)
        with pytest.raises(ValidationError):
            schema.validate(bad)
    for bad in [
        {"preset": "g20_country_overview"},
        {"preset": "g20_country_overview", "country": "SG"},
    ]:
        with pytest.raises(AtlasError):
            parse_preset(bad)
        with pytest.raises(ValidationError):
            preset_schema.validate(bad)
    with Atlas("fixture") as atlas:
        catalog = atlas.research_catalog(dataset="global_economics", country="IN")
        assert catalog["series_search"]["matched"] == 14
        assert all(r["series"] == r["query"]["series"] for r in catalog["series_search"]["records"])
        dataset = catalog["datasets"][0]
        assert dataset["unsupported_regional_members"] == ["African Union"]
        assert dataset["countries"]["AU"]["iso3"] == "AUS"
        assert len(dataset["countries"]) == 24
        preview = atlas.get_research_preset(
            {"preset": "g20_country_overview", "country": "IN", "preview": True}
        )
    assert preview["mode"] == "preview" and len(preview["plan"]["items"]) == 10
    assert all(item["query"]["country"] == "IN" for item in preview["plan"]["items"])


def test_url_allowlist_rejects_country_substitution_unselected_codes_and_extra_parameters():
    urls = [
        wdi_country_url("IN"),
        wdi_metadata_url("gdp_real"),
        wdi_data_url("IN", "gdp_growth", 1996, 2025),
    ]
    for url in urls:
        assert global_wdi_identity(url)
        PublicTransport.validate_url(url)
        for bad in [
            url + "&api_key=secret",
            url + "#fragment",
            url.replace("https:", "http:"),
            url.replace("api.worldbank.org/", "api.worldbank.org.evil.invalid/"),
            url.replace("api.worldbank.org/", "user:secret@api.worldbank.org/"),
        ]:
            assert global_wdi_identity(bad) is None
            with pytest.raises(AtlasError):
                PublicTransport.validate_url(bad)
    data = wdi_data_url("IN", "gdp_growth", 1996, 2025)
    for bad in [
        data.replace("IND/", "KAZ/"),
        data.replace("source=2", "source=11"),
        data.replace("1996%3A2025", "1995%3A2025"),
        data + "&date=2020:2025",
    ]:
        assert global_wdi_identity(bad) is None


def test_public_transport_cache_and_local_history_keep_canonical_sources_without_secrets(tmp_path):
    source = PublicFixtures()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert "api_key" not in request.url.query.decode()
        return httpx.Response(200, content=source.get(str(request.url), 0).body)

    with Atlas("fixture", directory=tmp_path) as atlas:
        atlas._research.public = PublicTransport(
            cache_path=tmp_path / "public-cache.sqlite3",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            interval=0,
        )
        result = atlas.get_research(query())
        again = atlas.get_research(query())
        assert result["records"] == again["records"] and len(calls) == 3
        url = wdi_data_url("IN", "gdp_growth", 2023, 2025)
        history = atlas.get_research({"dataset": "source_history", "source_url": url})
        assert history["records"]
        compact = atlas.get_research(query(response={"view": "compact", "fields": ["value"]}))
        assert compact["records"][0]["country_iso3"] == "IND"
        assert compact["records"][0]["definition"] == result["records"][0]["definition"]
        evidence = atlas.get_evidence(compact["records"][0]["source"]["evidence_id"])["evidence"]
        assert evidence == result["records"][0]["source"]
        assert evidence["json_pointer"] == "/1/0/value"


def test_annual_panel_and_country_preset_share_the_research_service():
    with Atlas("fixture") as atlas:
        preset = atlas.get_research_preset(
            {"preset": "g20_country_overview", "country": "IN", **DATES, "view": "full"}
        )
        assert preset["status"] == "ok" and len(preset["batch"]["items"]) == 10
        panel = atlas.get_research(
            {
                "dataset": "macro_panel",
                "frequency": "annual",
                "allow_partial": True,
                **DATES,
                "items": [
                    {
                        "id": country,
                        "aggregation": "last",
                        "query": {
                            "dataset": "global_economics",
                            "country": country,
                            "series": "gdp_growth",
                        },
                    }
                    for country in ("IN", "BR")
                ],
            }
        )
    assert len(panel["records"]) == 3
    assert panel["coverage"]["missing_cells"] == 2


def test_cli_http_and_sdk_return_the_same_g20_records():
    with Atlas("fixture") as atlas:
        expected = atlas.get_research(query())
    with TestClient(create_app(provider="fixture")) as client:
        response = client.post("/v1/research", json=query())
        assert response.status_code == 200 and response.json()["records"] == expected["records"]
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "atlas_terminal",
            "research",
            "get",
            "global_economics",
            "--country",
            "IN",
            "--series",
            "gdp_growth",
            "--start-date",
            "2023-01-01",
            "--end-date",
            "2025-12-31",
            "--provider",
            "fixture",
            "--json",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(run.stdout)["records"] == expected["records"]


@pytest.mark.asyncio
async def test_actual_stdio_mcp_accepts_country_queries_and_resolves_original_evidence():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "atlas_terminal", "mcp", "serve", "--provider", "fixture", "--research-only"],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "atlas_get_research",
            {"query": query(response={"view": "compact", "fields": ["value"]})},
        )
        assert not result.is_error
        body = result.structured_content
        row = body["records"][0]
        evidence = await session.call_tool(
            "atlas_get_evidence", {"evidence_id": row["source"]["evidence_id"]}
        )
        assert not evidence.is_error and "evidence" in evidence.structured_content
        assert Decimal(row["value"]) == Decimal("123.12345678901234567890")
