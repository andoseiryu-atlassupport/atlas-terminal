"""Isolated text extraction worker. No URLs, OCR, actions or external AI calls."""

import io
import json
import logging
import sys
from typing import Any

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_PAGES = 100
MAX_TEXT_CHARS = 500000


def main() -> None:
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader

    body = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
    if len(body) > MAX_DOCUMENT_BYTES or not body.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF")
    reader = PdfReader(io.BytesIO(body), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("Encrypted or oversized PDF")
    pages: list[dict[str, Any]] = []
    total = 0
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        total += len(text)
        if total > MAX_TEXT_CHARS:
            raise ValueError("Text bound exceeded")
        pages.append({"page": index + 1, "text": text})
    if not any(p["text"].strip() for p in pages):
        raise ValueError("No embedded text; OCR is not supplied")
    sys.stdout.write(json.dumps(pages, ensure_ascii=False))


if __name__ == "__main__":
    main()
