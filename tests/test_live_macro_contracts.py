"""Offline fictional regressions for shapes encountered in official macro APIs."""

import json
from copy import deepcopy
from importlib.resources import files
from xml.etree import ElementTree as ET

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.regional_bond_sources import RBA_URL, parse_rba_curve
from atlas_terminal.regional_economic_catalog import EUROPE_SERIES
from atlas_terminal.source_checks import source_shapes

RESOURCE = files("atlas_terminal").joinpath("resources", "research")
DATES = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


@pytest.fixture(autouse=True)
def private(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path))


def changed(query, transform):
    class Transport(PublicFixtures):
        def get(self, url, ttl):
            original = super().get(url, ttl)
            return Snapshot(transform(url, original.body), original.url, original.retrieved_at)

    with Atlas("fixture") as atlas:
        atlas._research.public = Transport()
        return atlas.get_research(query)


@pytest.mark.parametrize("series", ["call_rate", "tankan_manufacturing", "monetary_base"])
def test_boj_resultset_integer_periods_and_future_null_placeholders(series):
    def transform(url, body):
        raw = json.loads(body)
        raw["RESULTSET"] = raw.pop("DATA")
        values = raw["RESULTSET"][0]["VALUES"]
        values["SURVEY_DATES"] = [int(x) for x in values["SURVEY_DATES"]]
        period = 20261231 if series == "call_rate" else 202604 if "tankan" in series else 202612
        values["SURVEY_DATES"].append(period)
        values["VALUES"].append(None)
        return json.dumps(raw).encode()

    result = changed({"dataset": "japan_economics", "series": series, **DATES}, transform)
    assert result["coverage"]["excluded_future_placeholder_rows"] == 1
    assert all(r["source"]["json_pointer"].startswith("/RESULTSET/0/") for r in result["records"])
    assert all(r["value"] is not None for r in result["records"])


@pytest.mark.parametrize("blank_unit", [False, True])
def test_dashboard_lowercase_cycle_absent_to_number_and_explicit_blank_unit(blank_unit):
    def transform(url, body):
        root = ET.fromstring(body)
        if "getIndicatorInfo" in url:
            for node in root.iter("Cycle"):
                node.tag = "cycle"
        else:
            counts = root.find("STATISTICAL_DATA/RESULT_INF")
            counts.remove(counts.find("TO_NUMBER"))
            if blank_unit:
                for group in root.findall("STATISTICAL_DATA/CLASS_INF/CLASS_OBJ"):
                    if group.get("id") == "unit":
                        for unit in group:
                            unit.set("name", "")
        return ET.tostring(root)

    result = changed({"dataset": "japan_economics", "series": "cpi", **DATES}, transform)
    assert result["records"]
    if blank_unit:
        assert result["records"][0]["unit"] == "source_unspecified"
        assert result["records"][0]["source_unit_label"] == ""


def test_dashboard_uses_one_newest_explicit_base_without_combining_series():
    class Transport(PublicFixtures):
        def get(self, url, ttl):
            name = "estat_cpi_metadata.xml" if "getIndicatorInfo" in url else "estat_cpi.xml"
            body = RESOURCE.joinpath(name).read_bytes()
            root = ET.fromstring(body)
            if "getIndicatorInfo" in url:
                parent = root.find("METADATA_INF/CLASS_INF")
                newer = deepcopy(parent[0])
                newer.set("name", newer.get("name").replace("2020", "2025"))
                newer.set("code", "9900000000000000099")
                parent.append(newer)
            else:
                assert "IndicatorCode=9900000000000000099" in url
                body = body.replace(b"9900000000000000001", b"9900000000000000099")
                body = body.replace(b"2020", b"2025")
                root = ET.fromstring(body)
            return Snapshot(
                ET.tostring(root),
                "https://fixtures.atlas.invalid/fictional.xml",
                "2026-10-27T02:00:00Z",
            )

    with Atlas("fixture") as atlas:
        atlas._research.public = Transport()
        result = atlas.get_research({"dataset": "japan_economics", "series": "cpi", **DATES})
    assert all(r["series_id"] == "9900000000000000099" for r in result["records"])
    assert all(r["base_period"] == "2025" for r in result["records"])


def test_sdmx_attribute_dimension_references_are_not_series_declarations():
    def transform(url, body):
        if "dataflow" not in url:
            return body
        root = ET.fromstring(body)
        ns = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"
        attr = root.find(".//" + ns + "Attribute")
        relation = ET.SubElement(attr, ns + "AttributeRelationship")
        dimension = ET.SubElement(relation, ns + "Dimension")
        ET.SubElement(dimension, "Ref", id="MEASURE")
        return ET.tostring(root)

    result = changed({"dataset": "activity_economics", "series": "au_gdp_real", **DATES}, transform)
    assert result["records"] and result["records"][0]["frequency"] == "quarterly"


def test_rba_all_missing_date_row_uses_original_date_locator_and_bounds_blank_trailer():
    body = RESOURCE.joinpath("rba_yields.csv").read_bytes()
    # A date-only holiday row and a long blank trailer are present in published F2.
    body += b"\n27-Oct-2026\n" + b"\n" * 31000
    rows, _ = parse_rba_curve(Snapshot(body, RBA_URL, "2026-10-28T02:00:00Z"))
    assert source_shapes([Snapshot(body, RBA_URL, "2026-10-28T02:00:00Z")])[0]["format"] == "csv"
    for value in rows["2026-10-27"].values():
        assert value["value"] is None and value["source_value"] is None
        assert value["source"]["csv_column"] == 1
        assert value["value_cell_present"] is False


def test_euro_area_activity_selectors_keep_fixed_21_country_definition_explicit():
    for series in ("ea_unemployment", "ea_industrial_production", "ea_retail_sales"):
        assert EUROPE_SERIES[series]["filters"]["geo"] == "EA21"
        assert "21 countries" in EUROPE_SERIES[series]["title"]
    assert EUROPE_SERIES["ea_gdp_real"]["filters"]["geo"] == "EA"
