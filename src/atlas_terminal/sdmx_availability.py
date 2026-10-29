"""Conservative SDMX Actual constraint checks with original metadata evidence."""

from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element

from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.public_sources import local_name
from atlas_terminal.research_financials import reference


def _children(node: Element, name: str) -> list[Element]:
    return [child for child in node if local_name(child) == name]


def _instant(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("An explicit timezone is required")
    return instant


def availability_constraints(
    root: Element,
    flow: str,
    dimension_ids: set[str],
    snapshot: Snapshot,
    locators: dict[Element, str],
) -> list[dict[str, Any]]:
    """Inspect only constraints attached exactly to this resolved dataflow.

    Codes in one include-only cube can disprove availability, but cannot prove
    that their Cartesian product exists. Unknown shapes remain nonassertive.
    Constraint validity is evaluated at metadata capture, never as publisher PIT.
    """
    agency, identifier, version = flow.split(",")
    result: list[dict[str, Any]] = []
    for node in root.iter():
        if local_name(node) != "ContentConstraint" or node.get("type") != "Actual":
            continue
        attachments = _children(node, "ConstraintAttachment")
        relevant = [
            ref
            for attachment in attachments
            for ref in attachment.iter()
            if local_name(ref) == "Ref"
            and ref.get("id") == identifier
            and ref.get("agencyID") in {None, agency}
            and ref.get("version") in {None, version}
        ]
        if not relevant:
            continue
        if len(result) >= 32:
            return [{"supported": False, "reason": "Too many matching Actual constraints."}]
        row: dict[str, Any] = {
            "constraint_id": node.get("id"),
            "type": "Actual",
            "valid_from": node.get("validFrom"),
            "valid_to": node.get("validTo"),
            "evaluated_at": snapshot.retrieved_at,
            "validity_basis": "metadata_capture_time",
            "active_at_capture": None,
            "supported": False,
            "source": reference(snapshot, locators[node], xml=True),
        }
        result.append(row)
        attached = list(attachments[0]) if len(attachments) == 1 else []
        refs = _children(attached[0], "Ref") if len(attached) == 1 else []
        if (
            len(refs) != 1
            or local_name(attached[0]) != "Dataflow"
            or any(
                refs[0].get(key) != expected
                for key, expected in (
                    ("agencyID", agency),
                    ("id", identifier),
                    ("version", version),
                )
            )
        ):
            row["reason"] = "Constraint attachment is not one exact dataflow version."
            continue
        try:
            captured = _instant(snapshot.retrieved_at)
            low = _instant(node.attrib["validFrom"]) if "validFrom" in node.attrib else None
            high = _instant(node.attrib["validTo"]) if "validTo" in node.attrib else None
            if low is not None and high is not None and low > high:
                raise ValueError("Reversed validity bounds")
            row["active_at_capture"] = (low is None or low <= captured) and (
                high is None or captured <= high
            )
        except ValueError:
            row["reason"] = "Constraint validity cannot be evaluated unambiguously."
            continue
        cubes = _children(node, "CubeRegion")
        if (
            len(cubes) != 1
            or cubes[0].get("include") not in {"true", "1"}
            or set(cubes[0].attrib) != {"include"}
            or _children(node, "DataKeySet")
        ):
            row["reason"] = "Only one include-only CubeRegion is supported."
            continue
        codes: dict[str, list[str]] = {}
        supported = True
        for key in cubes[0]:
            name = key.get("id", "")
            if local_name(key) != "KeyValue" or name in codes or set(key.attrib) != {"id"}:
                supported = False
                break
            if name == "TIME_PERIOD":
                # Time bounds are not used to infer observation completeness or
                # to rewrite requested dates; only series code absence can reject.
                continue
            values = [v.text or "" for v in key if local_name(v) == "Value"]
            if (
                name not in dimension_ids
                or not values
                or len(values) != len(key)
                or any(v.attrib or list(v) for v in key)
                or any(not value or len(value) > 100 for value in values)
                or len(set(values)) != len(values)
                or sum(map(len, codes.values())) + len(values) > 10000
            ):
                supported = False
                break
            codes[name] = values
        if supported:
            row.update(supported=True, allowed_dimension_codes=codes)
        else:
            row["reason"] = "Constraint components require additional support or exceed bounds."
    return result


def enforce_availability(constraints: list[dict[str, Any]], dimensions: dict[str, str]) -> None:
    """Reject only an explicitly absent code under one unambiguous active constraint."""
    current = [row for row in constraints if row.get("active_at_capture") is not False]
    if len(current) != 1 or not current[0].get("supported"):
        return
    if current[0].get("active_at_capture") is not True:
        return
    codes = current[0]["allowed_dimension_codes"]
    absent = [
        name for name, value in dimensions.items() if name in codes and value not in codes[name]
    ]
    if absent:
        raise AtlasError(
            "INCOMPLETE_COVERAGE",
            "Source Actual availability metadata excludes the selected code for "
            + ", ".join(sorted(absent))
            + ". Inspect sdmx_catalog for this dataflow; no different definition is substituted.",
            fields=["dimensions"],
        )
