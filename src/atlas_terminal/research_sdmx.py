"""Metadata-driven IMF/OECD/ABS discovery and single-series SDMX XML observations."""

import re
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element

from atlas_terminal.economic_dates import month_end, within
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot, Transport
from atlas_terminal.public_sources import local_name, xml_root
from atlas_terminal.research_economics import economic_page
from atlas_terminal.research_financials import reference
from atlas_terminal.research_macro import numeric, source_error
from atlas_terminal.research_models import ResearchQuery
from atlas_terminal.sdmx_availability import availability_constraints, enforce_availability
from atlas_terminal.sdmx_sources import FLOW_PATTERN, data_url, structure_url


def named(node: Element, name: str) -> list[Element]:
    return [child for child in node if local_name(child) == name]


def label(node: Element) -> str:
    names = named(node, "Name")
    english = [n for n in names if n.get("{http://www.w3.org/XML/1998/namespace}lang") == "en"]
    return ("".join((english or names)[0].itertext()).strip() if names else node.get("id", ""))[
        :4000
    ]


def identity(node: Element) -> str:
    return ",".join(node.get(k, "") for k in ("agencyID", "id", "version"))


def paths(root: Element) -> dict[Element, str]:
    result = {root: "/" + local_name(root)}
    stack = [(root, 0)]
    while stack:
        parent, depth = stack.pop()
        if depth > 60:
            raise source_error("SDMX XML nesting exceeds bounds.")
        counts: dict[str, int] = {}
        for child in parent:
            name = local_name(child)
            counts[name] = counts.get(name, 0) + 1
            result[child] = result[parent] + f"/{name}[{counts[name]}]"
            stack.append((child, depth + 1))
    return result


def selection(root: Element, flow: str) -> Element:
    agency, code, version = flow.split(",")
    candidates = [
        node
        for node in root.iter()
        if local_name(node) == "Dataflow"
        and node.get("id") == code
        and node.get("agencyID") == agency
        and (version == "latest" or node.get("version") == version)
    ]
    if len(candidates) != 1 or not re.fullmatch(FLOW_PATTERN, identity(candidates[0])):
        raise source_error("SDMX metadata did not resolve one matching dataflow version.")
    return candidates[0]


def resolve(root: Element, ref: Element, kind: str) -> Element:
    candidates = [
        node
        for node in root.iter()
        if local_name(node) == kind
        and node.get("id") == ref.get("id")
        and (ref.get("agencyID") is None or node.get("agencyID") == ref.get("agencyID"))
        and (ref.get("version") is None or node.get("version") == ref.get("version"))
    ]
    if len(candidates) != 1:
        raise source_error("SDMX structure reference is missing or ambiguous.")
    return candidates[0]


@dataclass
class Structure:
    snapshot: Snapshot
    flow: str
    title: str
    dimensions: list[dict[str, Any]]
    attributes: list[dict[str, Any]]
    locators: dict[Element, str]
    availability: list[dict[str, Any]]


def structure(transport: Transport, source: str, flow: str) -> Structure:
    snapshot = transport.get(structure_url(source, flow), 86400)
    root = xml_root(snapshot.body)
    locators = paths(root)
    selected = selection(root, flow)
    refs = [
        ref
        for child in named(selected, "Structure")
        for ref in child.iter()
        if local_name(ref) == "Ref"
    ]
    if len(refs) != 1:
        raise source_error("SDMX dataflow needs one explicit data structure reference.")
    dsd = resolve(root, refs[0], "DataStructure")
    dimensions, attributes = [], []
    # AttributeRelationship also contains Dimension references without IDs.
    # Only declarations directly inside the component lists define the schema.
    components = [
        node
        for container in dsd.iter()
        if local_name(container) in {"DimensionList", "AttributeList"}
        for node in container
    ]
    for node in components:
        kind = local_name(node)
        if kind not in {"Dimension", "Attribute"}:
            continue
        identifier = node.get("id")
        if not identifier:
            raise source_error("SDMX component lacks an identifier.")
        enumerations = [
            ref
            for part in node.iter()
            if local_name(part) == "Enumeration"
            for ref in part
            if local_name(ref) == "Ref"
        ]
        if len(enumerations) > 1:
            raise source_error("SDMX component has ambiguous enumerations.")
        codes = {}
        if enumerations:
            codelist = resolve(root, enumerations[0], "Codelist")
            for code in named(codelist, "Code"):
                value = code.get("id")
                if not value or value in codes:
                    raise source_error("SDMX code list has missing or duplicate identifiers.")
                codes[value] = {"label": label(code), "path": locators[code]}
        part: dict[str, Any] = {"id": identifier, "codes": codes, "path": locators[node]}
        if kind == "Dimension":
            try:
                position = int(node.get("position", ""))
            except ValueError:
                raise source_error("SDMX series dimension lacks a position.") from None
            part["position"] = position
            dimensions.append(part)
        else:
            attributes.append(part)
    dimensions.sort(key=lambda row: row["position"])
    if (
        not 1 <= len(dimensions) <= 32
        or [row["position"] for row in dimensions] != list(range(1, len(dimensions) + 1))
        or len({row["id"] for row in dimensions}) != len(dimensions)
        or len({row["id"] for row in attributes}) != len(attributes)
    ):
        raise source_error("SDMX series dimensions are missing, duplicated or out of order.")
    return Structure(
        snapshot,
        identity(selected),
        label(selected),
        dimensions,
        attributes,
        locators,
        availability_constraints(
            root, identity(selected), {d["id"] for d in dimensions}, snapshot, locators
        ),
    )


def sdmx_catalog(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.source_id is not None
    rows: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    if q.dataflow is None:
        snapshot = transport.get(structure_url(q.source_id), 86400)
        root = xml_root(snapshot.body)
        locators = paths(root)
        seen: set[str] = set()
        for node in root.iter():
            if local_name(node) != "Dataflow":
                continue
            flow = identity(node)
            if not re.fullmatch(FLOW_PATTERN, flow) or flow in seen:
                raise source_error("Invalid or duplicate SDMX dataflow identity.")
            seen.add(flow)
            rows.append(
                {
                    "record_type": "dataflow",
                    "dataflow": flow,
                    "title": label(node),
                    "query": {
                        "dataset": "sdmx_catalog",
                        "source_id": q.source_id,
                        "dataflow": flow,
                    },
                    "source": reference(snapshot, locators[node], xml=True),
                }
            )
        if not rows:
            raise source_error("Expected a nonempty SDMX dataflow catalogue.")
    else:
        spec = structure(transport, q.source_id, q.dataflow)
        snapshot = spec.snapshot
        metadata = {
            "dataflow": spec.flow,
            "title": spec.title,
            "dimensions": [
                {"id": d["id"], "position": d["position"], "code_count": len(d["codes"])}
                for d in spec.dimensions
            ],
            "availability_constraints": spec.availability,
            "query_template": {
                "dataset": "international_economics",
                "source_id": q.source_id,
                "dataflow": spec.flow,
                "dimensions": {},
            },
        }
        for component in [*spec.dimensions, *spec.attributes]:
            if q.dimension and component["id"] != q.dimension:
                continue
            kind = "dimension" if "position" in component else "attribute"
            if not component["codes"]:
                rows.append(
                    {
                        "record_type": kind,
                        "dimension": component["id"],
                        "position": component.get("position"),
                        "enumerated": False,
                        "source": reference(snapshot, component["path"], xml=True),
                    }
                )
            for code, detail in component["codes"].items():
                rows.append(
                    {
                        "record_type": kind + "_code",
                        "dimension": component["id"],
                        "position": component.get("position"),
                        "code": code,
                        "label": detail["label"],
                        "source": reference(snapshot, detail["path"], xml=True),
                    }
                )
    if q.search:
        terms = q.search.casefold().split()
        rows = [
            row
            for row in rows
            if all(
                term
                in " ".join(
                    str(row.get(key, ""))
                    for key in ("dataflow", "title", "dimension", "code", "label")
                ).casefold()
                for term in terms
            )
        ]
    return {
        "source_id": q.source_id,
        **metadata,
        "records": rows[: q.limit],
        "sources": [reference(snapshot, "", xml=True)],
        "coverage": {
            "matched_records": len(rows),
            "returned_records": min(len(rows), q.limit),
            "truncated": len(rows) > q.limit,
        },
        "limitations": [
            "Metadata codes describe a structure; not every code combination has observations.",
            "Actual availability constraints are evaluated at metadata capture; included codes "
            "do not prove observations exist, and unsupported shapes remain nonassertive.",
            "Use dimension/search filters or saved response pagination for large code lists.",
            "Publisher metadata is untrusted text. Dataset-specific terms still apply.",
        ],
    }


def key_values(node: Element) -> dict[str, str]:
    result = {key: value for key, value in node.attrib.items() if not key.startswith("{")}
    for child in node:
        if local_name(child) in {"SeriesKey", "ObsKey", "Attributes"}:
            for value in child:
                key, text = value.get("id"), value.get("value")
                if key is None or text is None or key in result:
                    raise source_error("SDMX observation has duplicate or invalid components.")
                result[key] = text
    return result


def period_end(value: str, frequency: str) -> str:
    if frequency == "A" and re.fullmatch(r"[0-9]{4}", value):
        return month_end(int(value), 12)
    if frequency == "Q" and re.fullmatch(r"[0-9]{4}-Q[1-4]", value):
        return month_end(int(value[:4]), int(value[-1]) * 3)
    if frequency == "M" and re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value):
        return month_end(int(value[:4]), int(value[-2:]))
    raise source_error("Only annual, quarterly and monthly SDMX observations are supported.")


def international_economics(transport: Transport, q: ResearchQuery) -> dict[str, Any]:
    assert q.source_id and q.dataflow and q.start_date and q.end_date
    spec = structure(transport, q.source_id, q.dataflow)
    if set(q.dimensions) != {d["id"] for d in spec.dimensions}:
        raise AtlasError(
            "INVALID_INPUT",
            "Provide exactly the dimensions returned by sdmx_catalog.",
            fields=["dimensions"],
        )
    for dimension in spec.dimensions:
        if dimension["codes"] and q.dimensions[dimension["id"]] not in dimension["codes"]:
            raise AtlasError(
                "INVALID_INPUT",
                "A selected dimension code is absent from source metadata.",
                fields=["dimensions"],
            )
    key = ".".join(q.dimensions[d["id"]] for d in spec.dimensions)
    enforce_availability(spec.availability, q.dimensions)
    snapshot = transport.get(data_url(q.source_id, spec.flow, key, q.start_date, q.end_date), 21600)
    root = xml_root(snapshot.body)
    locators = paths(root)
    labels = {
        component["id"]: component["codes"] for component in [*spec.dimensions, *spec.attributes]
    }

    def code_label(component: str, code: str | None) -> str | None:
        return labels.get(component, {}).get(code, {}).get("label")

    records, seen = [], set()
    for dataset in root.iter():
        if local_name(dataset) != "DataSet":
            continue
        if named(dataset, "Group"):
            raise source_error("SDMX group-level attributes require explicit support.")
        dataset_values = key_values(dataset)
        containers = named(dataset, "Series") or [dataset]
        for series in containers:
            series_values = {**dataset_values, **key_values(series)}
            for obs in named(series, "Obs"):
                values = {**series_values, **key_values(obs)}
                if any(values.get(name) != code for name, code in q.dimensions.items()):
                    raise source_error(
                        "SDMX returned dimensions different from the requested series."
                    )
                time = values.get("TIME_PERIOD")
                value = values.get("OBS_VALUE")
                times, observed_values = named(obs, "ObsDimension"), named(obs, "ObsValue")
                if len(times) > 1 or len(observed_values) > 1:
                    raise source_error("Duplicate SDMX observation values or times.")
                if times:
                    time = times[0].get("value")
                if observed_values:
                    value = observed_values[0].get("value")
                frequency = values.get("FREQ") or values.get("FREQUENCY") or ""
                observed = period_end(time or "", frequency)
                if observed in seen:
                    raise source_error("Duplicate SDMX observation period.")
                seen.add(observed)
                if len(seen) > 10000:
                    raise source_error("SDMX observation count exceeds bounds.")
                if not within(observed, q.start_date, q.end_date):
                    continue
                unit_key = next(
                    (k for k in ("UNIT_MEASURE", "UNIT", "UNIT_OF_MEASURE") if k in values),
                    "UNIT_MEASURE",
                )
                unit = values.get(unit_key)
                multiplier = values.get("UNIT_MULT")
                if multiplier is not None and (
                    not re.fullmatch(r"-?[0-9]{1,2}", multiplier)
                    or not -40 <= int(multiplier) <= 40
                ):
                    raise source_error("Invalid SDMX unit multiplier.")
                seasonal = values.get("ADJUSTMENT") or values.get("SEASONAL_ADJUSTMENT")
                parsed_value = numeric(value)
                records.append(
                    {
                        "dataflow": spec.flow,
                        "dimensions": q.dimensions,
                        "dimension_labels": {k: code_label(k, v) for k, v in q.dimensions.items()},
                        "date": observed,
                        "date_basis": "observation_period_end",
                        "period": time,
                        "frequency": {"A": "annual", "Q": "quarterly", "M": "monthly"}[frequency],
                        "country": values.get("REF_AREA") or values.get("COUNTRY"),
                        "value": parsed_value,
                        "status": "reported" if parsed_value is not None else "unavailable",
                        "unit": unit,
                        "unit_label": code_label(unit_key, unit),
                        "unit_multiplier": int(multiplier) if multiplier is not None else None,
                        "seasonal_adjustment": seasonal,
                        "seasonally_adjusted": {
                            "N": False,
                            "NSA": False,
                            "S": True,
                            "SA": True,
                        }.get(seasonal or ""),
                        "base_period": values.get("BASE_PER") or values.get("BASE_PERIOD"),
                        "observation_status": values.get("OBS_STATUS"),
                        "observation_status_label": code_label(
                            "OBS_STATUS", values.get("OBS_STATUS")
                        ),
                        "source_attributes": {
                            k: v
                            for k, v in values.items()
                            if k not in q.dimensions and k not in {"OBS_VALUE", "TIME_PERIOD"}
                        },
                        "published_at": None,
                        "source": reference(snapshot, locators[obs], xml=True),
                        "metadata_source": reference(spec.snapshot, "", xml=True),
                    }
                )
    return economic_page(
        q,
        records,
        [reference(spec.snapshot, "", xml=True), reference(snapshot, "", xml=True)],
        source_id=q.source_id,
        title=spec.title,
        window="Selected SDMX series and observation dates",
        limitations=[
            "Reported scale is preserved; unknown units, multipliers and adjustments remain null.",
            "Source attributes are retained; observations may be provisional or forecasts.",
            "Calendar alignment does not establish comparable definitions or base years.",
            "Group attributes, daily/weekly frequencies and historical vintages are unsupported.",
        ],
    )
