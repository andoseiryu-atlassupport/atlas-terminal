"""Bounded, isolated JPX layout extraction; unrelated report import limits stay intact."""

import gc
import io
import json
import logging
import sys
from typing import Any

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_PAGES = 160
MAX_TEXT_CHARS = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 12 * 1024 * 1024


def main() -> None:
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader, PdfWriter

    body = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if len(body) > MAX_DOCUMENT_BYTES or not body.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF")
    reader = PdfReader(io.BytesIO(body), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("Encrypted or oversized PDF")
    pages: list[dict[str, Any]] = []
    total = 0
    for index, original in enumerate(reader.pages):
        # A whole-document writer retains parsed content operations for every page.
        # Copy one page at a time so rotation normalization stays within the memory cap.
        writer = PdfWriter()
        page = writer.add_page(original)
        # pypdf 6.21.4 writer pages have no .pdf context until serialization.
        # Layout font discovery requires this public document context in memory.
        page.pdf = writer
        page.transfer_rotation_to_content()
        text = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False) or ""
        total += len(text)
        if total > MAX_TEXT_CHARS:
            raise ValueError("Text bound exceeded")
        pages.append({"page": index + 1, "text": text})
        del page, writer
        gc.collect()
    if not all(page["text"].strip() for page in pages):
        raise ValueError("Missing embedded text; OCR is not supplied")
    output = json.dumps(pages, ensure_ascii=False).encode("utf-8")
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("Output bound exceeded")
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
