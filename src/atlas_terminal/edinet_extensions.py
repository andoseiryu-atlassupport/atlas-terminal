"""Issuer-scoped exact label mappings, resolved through original XSD/linkbases.

Toyota reports gross purchases excluding rental assets and rental-asset purchases
separately. Both operands are required; sales, intangibles and noncash lease
additions are never candidates. Labels are exact reviewed definitions, not fuzzy
concept-name matches. Monetary duration declarations and all links are retained.
"""

import posixpath
import unicodedata
from typing import Any

from atlas_terminal.edinet_mappings import toyota_extension
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot
from atlas_terminal.research_financials import reference
from atlas_terminal.research_japan_companies import expanded_qname, scoped_xml
from atlas_terminal.source_archives import bounded_zip, member_bytes
from atlas_terminal.source_deadline import remaining_timeout
from atlas_terminal.util import digest

XSD = "{http://www.w3.org/2001/XMLSchema}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
LABELS = {
    "有形固定資産の購入<賃貸資産を除く>": "ppe_excluding_rental_assets",
    "賃貸資産の購入": "rental_assets",
}


def exact_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def capex_extensions(
    snapshot: Snapshot, filing: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    if filing["edinet_code"] != "E02144":
        return {}
    definitions: dict[tuple[str, str], dict[str, Any]] = {}
    mapped: dict[tuple[str, str], dict[str, Any]] = {}
    with bounded_zip(snapshot.body) as archive:
        members = sorted(name for name in archive.namelist() if name.startswith("XBRL/PublicDoc/"))
        if sum(name.endswith((".xsd", "_lab.xml")) for name in members) > 64:
            raise AtlasError("SOURCE_UNAVAILABLE", "Issuer mapping metadata exceeds bounds.")
        for member in members:
            if not member.endswith(".xsd"):
                continue
            remaining_timeout()
            body = member_bytes(archive, member)
            root, scopes = scoped_xml(body)
            namespace = root.get("targetNamespace", "")
            if not toyota_extension(namespace, filing):
                continue
            for index, child in enumerate(root, 1):
                if (
                    child.tag != XSD + "element"
                    or not child.get("id")
                    or not child.get("name")
                    or child.get(XBRLI + "periodType") != "duration"
                    or expanded_qname(child.get("type"), scopes[child])
                    != XBRLI + "monetaryItemType"
                ):
                    continue
                identity = (member, child.get("id", ""))
                if identity in definitions:
                    raise AtlasError("SOURCE_UNAVAILABLE", "Duplicate issuer schema concept ID.")
                definitions[identity] = {
                    "namespace": namespace,
                    "concept": child.get("name"),
                    "source": reference(snapshot, f"/schema/*[{index}]", xml=True)
                    | {
                        "archive_member": member,
                        "member_sha256": digest(body),
                    },
                }
        for member in members:
            if not member.endswith("_lab.xml"):
                continue
            remaining_timeout()
            body = member_bytes(archive, member)
            root, _ = scoped_xml(body)
            for link_index, link in enumerate(root, 1):
                if link.tag != LINK + "labelLink":
                    continue
                locators = {
                    child.get(XLINK + "label"): child.get(XLINK + "href", "")
                    for child in link
                    if child.tag == LINK + "loc"
                }
                labels = {
                    child.get(XLINK + "label"): (index, child)
                    for index, child in enumerate(link, 1)
                    if child.tag == LINK + "label"
                }
                if len(locators) != sum(child.tag == LINK + "loc" for child in link) or len(
                    labels
                ) != sum(child.tag == LINK + "label" for child in link):
                    raise AtlasError("SOURCE_UNAVAILABLE", "Ambiguous issuer label link IDs.")
                for arc in link:
                    if arc.tag != LINK + "labelArc":
                        continue
                    label = labels.get(arc.get(XLINK + "to"))
                    href = locators.get(arc.get(XLINK + "from"), "")
                    if not label or "#" not in href:
                        continue
                    path, fragment = href.split("#", 1)
                    definition = definitions.get(
                        (
                            posixpath.normpath(posixpath.join(posixpath.dirname(member), path)),
                            fragment,
                        )
                    )
                    index, element = label
                    if (
                        definition is None
                        or element.get("{http://www.w3.org/XML/1998/namespace}lang")
                        not in {"ja", "ja-JP"}
                        or element.get(XLINK + "role") != "http://www.xbrl.org/2003/role/label"
                    ):
                        continue
                    operand = LABELS.get(exact_text("".join(element.itertext())))
                    if operand is None:
                        continue
                    key = (definition["namespace"], definition["concept"])
                    evidence = reference(
                        snapshot, f"/linkbase/*[{link_index}]/*[{index}]", xml=True
                    ) | {
                        "archive_member": member,
                        "member_sha256": digest(body),
                        "label": "".join(element.itertext()),
                        "schema": definition["source"],
                        "locator_href": href,
                    }
                    if key in mapped and mapped[key]["operand"] != operand:
                        mapped[key]["operand"] = "ambiguous"
                    else:
                        mapped.setdefault(key, {"operand": operand, "evidence": []})
                    mapped[key]["evidence"].append(evidence)
    return mapped
