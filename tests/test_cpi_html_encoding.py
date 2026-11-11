"""Fictional encoding regressions; never modify the captured source bytes."""

import pytest

from atlas_terminal.cpi_sources import CPIHTML, decode_html, reference
from atlas_terminal.errors import AtlasError
from atlas_terminal.providers import Snapshot


def test_non_table_invalid_byte_keeps_numeric_cell_offsets_and_original_hash():
    body = (
        b"<html><p>Comment \x9d</p><table><caption>Example</caption>"
        b"<tr><td>0.064</td></tr></table></html>"
    )
    snapshot = Snapshot(
        body, "https://www.bls.gov/news.release/archives/cpi_11102026.htm", "2026-11-11T00:00:00Z"
    )
    parsed = CPIHTML(body)
    cell = parsed.tables[0]["rows"][0][0]
    source = reference(snapshot, cell, table="Example")
    assert cell["text"] == "0.064"
    assert (
        decode_html(body)[source["html_start_char"] : source["html_end_char"]] == "<td>0.064</td>"
    )
    assert source["raw_sha256"] == snapshot.sha256
    assert snapshot.body is body
    assert "U+FFFD" in source["offset_encoding"]


@pytest.mark.parametrize(
    "part", [b"<td>0.\x9d064</td>", b"<caption>Food\x9d</caption>", b'<th title="\x9d">Value</th>']
)
def test_invalid_bytes_inside_tables_remain_unavailable(part):
    with pytest.raises(AtlasError) as error:
        CPIHTML(b"<table><tr>" + part + b"</tr></table>")
    assert error.value.details["reason"] == "html_encoding_in_table"


def test_valid_utf8_and_bom_keep_the_original_text():
    text = "<table><tr><td>\u22120.125</td></tr></table>"
    assert decode_html(b"\xef\xbb\xbf" + text.encode()) == text
