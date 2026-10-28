"""Independently resolve retained macro source locators and compare reported cells."""

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree as ET

from atlas_terminal.util import digest


class SourceInspector:
    def __init__(self, snapshots):
        self.bodies = {s.sha256: s.body for s in snapshots.values()}
        self.parsed = {}
        self.matches = 0
        self.locators = 0

    def cell(self, source):
        sha = source["raw_sha256"]
        body = self.bodies[sha]
        assert digest(body) == sha
        if "json_pointer" in source:
            if sha not in self.parsed:
                self.parsed[sha] = json.loads(body, parse_float=Decimal)
            value = self.parsed[sha]
            for part in source["json_pointer"].split("/")[1:]:
                part = part.replace("~1", "/").replace("~0", "~")
                value = value[int(part)] if isinstance(value, list) else value[part]
        elif "xml_local_path" in source:
            if sha not in self.parsed:
                root = ET.fromstring(body)
                for node in root.iter():
                    node.tag = node.tag.rsplit("}", 1)[-1]
                self.parsed[sha] = root
            root = self.parsed[sha]
            path = source["xml_local_path"]
            path, _, attribute = path.partition("/@")
            path = path.removeprefix("/" + root.tag).lstrip("/")
            node = root.find(path) if path else root
            assert node is not None
            if attribute:
                value = node.attrib[attribute]
            elif node.tag == "Obs":
                value = node.find("ObsValue").get("value")
            else:
                value = node.text
        elif "csv_row" in source:
            if sha not in self.parsed:
                try:
                    text = body.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = body.decode("cp932")
                self.parsed[sha] = list(csv.reader(io.StringIO(text, newline="")))
            value = self.parsed[sha][source["csv_row"] - 1][source["csv_column"] - 1]
        elif "text_line" in source:
            lines = body.decode(source.get("text_encoding", "utf-8")).splitlines()
            value = lines[source["text_line"] - 1]
            if "text_column_start" in source:
                value = value[source["text_column_start"] - 1 : source["text_column_end"]]
        else:
            # Workbook cells have a dedicated independent live verifier.
            return None
        self.locators += 1
        return value

    def record(self, row):
        for child in row.get("inputs", []):
            self.record(child)
        source = row.get("source")
        if not source:
            return
        value = self.cell(source)
        if row.get("value") is None or row.get("status") != "reported":
            return
        expected = row.get("source_value", row["value"])
        if isinstance(value, dict):
            # Fiscal Data locates the complete original debt record.
            value = value["tot_pub_debt_out_amt"]
        try:
            original = str(value).strip().replace(",", "")
            assert Decimal(original) == Decimal(str(expected))
        except InvalidOperation:
            raise AssertionError(
                "Reported value does not match its raw numeric source cell"
            ) from None
        self.matches += 1


def verify_source_cells(result: dict[str, Any], snapshots) -> dict[str, int]:
    inspector = SourceInspector(snapshots)
    for source in result["sources"]:
        assert source["raw_sha256"] in inspector.bodies
    for row in result["records"]:
        inspector.record(row)
    return {
        "resolved_source_locators": inspector.locators,
        "matched_reported_cells": inspector.matches,
    }
