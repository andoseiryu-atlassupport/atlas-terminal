"""Fictional source constraints distinguish codelist membership from availability."""

from copy import deepcopy
from importlib.resources import files
from xml.etree import ElementTree as ET

import pytest

from atlas_terminal.client import Atlas
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import PublicFixtures
from atlas_terminal.research_sdmx import paths
from atlas_terminal.sdmx_availability import availability_constraints, enforce_availability
from atlas_terminal.util import digest

FLOW = "IMF.DEMO,ATLAS_MACRO,1.0"
CAPTURE = "2026-02-01T00:00:00Z"
DIMENSIONS = {"FREQUENCY": "M", "COUNTRY": "USA", "INDICATOR": "CPI"}
RESOURCE = files("atlas_terminal").joinpath("resources", "research")


def constraint(**attributes):
    node = ET.Element(
        "ContentConstraint",
        {"id": "SYNTHETIC_CURRENT", "type": "Actual", **attributes},
    )
    attached = ET.SubElement(ET.SubElement(node, "ConstraintAttachment"), "Dataflow")
    ET.SubElement(attached, "Ref", agencyID="IMF.DEMO", id="ATLAS_MACRO", version="1.0")
    key = ET.SubElement(ET.SubElement(node, "CubeRegion", include="true"), "KeyValue", id="COUNTRY")
    ET.SubElement(key, "Value").text = "JPN"
    return node


def parsed(*nodes, capture=CAPTURE):
    root = ET.Element("Structure")
    root.extend(nodes)
    raw = ET.tostring(root)
    snapshot = Snapshot(raw, "https://fixtures.atlas.invalid/constraints.xml", capture)
    return availability_constraints(root, FLOW, set(DIMENSIONS), snapshot, paths(root))


def test_exact_active_constraint_rejects_only_absent_codes_with_source_evidence():
    rows = parsed(constraint(validFrom=CAPTURE))
    row = rows[0]
    assert row["supported"] and row["active_at_capture"]
    assert row["allowed_dimension_codes"] == {"COUNTRY": ["JPN"]}
    assert row["source"]["xml_local_path"] == "/Structure/ContentConstraint[1]"
    assert row["evaluated_at"] == CAPTURE and row["validity_basis"] == "metadata_capture_time"
    with pytest.raises(AtlasError, match="COUNTRY") as error:
        enforce_availability(rows, DIMENSIONS)
    assert error.value.code == "INCOMPLETE_COVERAGE"
    enforce_availability(rows, DIMENSIONS | {"COUNTRY": "JPN"})


@pytest.mark.parametrize(
    "attributes,active",
    [
        ({"validFrom": CAPTURE}, True),
        ({"validTo": CAPTURE}, True),
        ({"validFrom": "2026-02-01T00:00:01Z"}, False),
        ({"validTo": "2026-01-31T23:59:59Z"}, False),
        ({"validFrom": "2026-02-01T09:00:00+09:00"}, True),
        ({"validFrom": "2026-02-01T00:00:00"}, None),
        ({"validFrom": "not-a-date"}, None),
        ({"validFrom": CAPTURE, "validTo": "2025-01-01T00:00:00Z"}, None),
    ],
)
def test_constraint_validity_uses_metadata_capture_and_explicit_timezone(attributes, active):
    rows = parsed(constraint(**attributes))
    assert rows[0]["active_at_capture"] is active
    if active is not True:
        enforce_availability(rows, DIMENSIONS)


@pytest.mark.parametrize(
    "shape",
    [
        "exclude_cube",
        "multiple_cubes",
        "key_set",
        "hierarchy",
        "excluded_key",
        "unknown_dimension",
        "duplicate_values",
        "mixed_attachment",
        "inexact_attachment",
        "complex_value",
        "empty_key",
    ],
)
def test_unsupported_constraint_shapes_never_assert_code_unavailability(shape):
    node = constraint()
    cube = node.find("CubeRegion")
    key = cube.find("KeyValue")
    if shape == "exclude_cube":
        cube.set("include", "false")
    elif shape == "multiple_cubes":
        node.append(deepcopy(cube))
    elif shape == "key_set":
        ET.SubElement(node, "DataKeySet", isIncluded="true")
    elif shape == "hierarchy":
        key[0].set("cascadeValues", "true")
    elif shape == "excluded_key":
        key.set("include", "false")
    elif shape == "unknown_dimension":
        key.set("id", "OTHER")
    elif shape == "duplicate_values":
        key.append(deepcopy(key[0]))
    elif shape == "mixed_attachment":
        node.find("ConstraintAttachment").append(ET.Element("DataProvider"))
    elif shape == "inexact_attachment":
        node.find("ConstraintAttachment/Dataflow/Ref").attrib.pop("version")
    elif shape == "complex_value":
        ET.SubElement(key[0], "Unexpected")
    else:
        key.remove(key[0])
    rows = parsed(node)
    assert rows and not rows[0]["supported"] and rows[0]["reason"]
    enforce_availability(rows, DIMENSIONS)


def test_old_and_future_constraints_do_not_compete_with_one_active_release():
    old = constraint(validTo="2026-01-31T23:59:59Z")
    current = constraint(validFrom=CAPTURE)
    current.find("CubeRegion/KeyValue/Value").text = "USA"
    future = constraint(validFrom="2026-03-01T00:00:00Z")
    enforce_availability(parsed(old, current, future), DIMENSIONS)
    with pytest.raises(AtlasError):
        enforce_availability(parsed(old, current, future), DIMENSIONS | {"COUNTRY": "JPN"})


def test_multiple_current_constraints_remain_nonassertive_instead_of_assuming_set_logic():
    enforce_availability(parsed(constraint(), constraint()), DIMENSIONS)


def test_allowed_constraint_and_other_flow_are_not_actual_availability():
    allowed, other = constraint(type="Allowed"), constraint()
    other.find("ConstraintAttachment/Dataflow/Ref").set("id", "OTHER")
    assert parsed(allowed, other) == []


def test_time_range_does_not_infer_missing_periods_or_expand_query():
    node = constraint()
    node.find("CubeRegion/KeyValue/Value").text = "USA"
    time = ET.SubElement(node.find("CubeRegion"), "KeyValue", id="TIME_PERIOD")
    span = ET.SubElement(time, "TimeRange")
    ET.SubElement(span, "StartPeriod", isInclusive="true").text = "2026-01-01T00:00:00"
    rows = parsed(node)
    assert rows[0]["supported"]
    assert rows[0]["allowed_dimension_codes"] == {"COUNTRY": ["USA"]}
    enforce_availability(rows, DIMENSIONS)


def test_constraint_count_bound_prevents_large_catalogue_envelopes():
    rows = parsed(*(constraint(id=f"SYNTHETIC_{i}") for i in range(33)))
    assert len(rows) == 1 and not rows[0]["supported"]
    enforce_availability(rows, DIMENSIONS)


def test_absent_actual_code_stops_before_data_fetch_and_catalogue_exposes_reason(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    root = ET.fromstring(RESOURCE.joinpath("sdmx_imf_structure.xml").read_bytes())
    root.append(constraint())
    body = ET.tostring(root)

    class Constrained(PublicFixtures):
        def __init__(self):
            self.calls = []

        def get(self, url, ttl):
            self.calls.append(url)
            assert "/dataflow/" in url, "Rejected series must not reach the data endpoint"
            return Snapshot(body, "https://fixtures.atlas.invalid/constraints.xml", CAPTURE)

    with Atlas("fixture") as atlas:
        source = Constrained()
        atlas._research.public = source
        with pytest.raises(AtlasError) as error:
            atlas.get_research(
                {
                    "dataset": "international_economics",
                    "source_id": "imf",
                    "dataflow": FLOW,
                    "dimensions": DIMENSIONS,
                    "start_date": "2026-01-01",
                    "end_date": "2026-03-31",
                }
            )
        assert error.value.code == "INCOMPLETE_COVERAGE" and len(source.calls) == 1
        result = atlas.get_research(
            {
                "dataset": "sdmx_catalog",
                "source_id": "imf",
                "dataflow": FLOW,
                "dimension": "COUNTRY",
            }
        )
    assert {row["code"] for row in result["records"]} == {"USA", "JPN"}
    constraint_row = result["availability_constraints"][0]
    assert constraint_row["allowed_dimension_codes"] == {"COUNTRY": ["JPN"]}
    assert constraint_row["source"]["raw_sha256"] == digest(body)
