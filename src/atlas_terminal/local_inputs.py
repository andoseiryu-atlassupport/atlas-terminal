"""Bounded operator-selected files; shared services never accept filesystem paths."""

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from atlas_terminal.errors import AtlasError


def read_regular(path: Path, max_bytes: int) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path.expanduser(), flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Regular file required")
            body = stream.read(max_bytes + 1)
    except (OSError, ValueError, RuntimeError):
        raise AtlasError("CONFIG_REQUIRED", "Input must be a readable regular file.") from None
    if not body or len(body) > max_bytes:
        raise AtlasError("INVALID_INPUT", "Input is empty or exceeds the byte limit.")
    return body


def public_reference(value: str) -> str:
    """Display-only source link; never fetched, with no query credential leakage."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Use a public HTTPS reference without credentials, query or fragment")
    return value
