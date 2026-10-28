"""Bounded in-memory archive reads; no extraction or external resource loading."""

import io
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath

from atlas_terminal.research_macro import source_error


@contextmanager
def bounded_zip(body: bytes) -> Iterator[zipfile.ZipFile]:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (
                len(entries) > 2000
                or len(set(names)) != len(names)
                or sum(item.file_size for item in entries) > 128 * 1024 * 1024
            ):
                raise ValueError("Archive bounds or duplicate names")
            for item in entries:
                path = PurePosixPath(item.filename)
                mode = (item.external_attr >> 16) & 0o170000
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or item.filename != item.orig_filename
                    or item.flag_bits & 1
                    or mode not in {0, 0o100000, 0o040000}
                    or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    raise ValueError("Unsafe archive member")
            yield archive
    except (
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ):
        raise source_error("Source ZIP is malformed, unsafe or outside supported bounds.") from None


def member_bytes(archive: zipfile.ZipFile, name: str) -> bytes:
    if archive.getinfo(name).file_size > 32 * 1024 * 1024:
        raise ValueError("Archive member exceeds bounds")
    with archive.open(name) as stream:
        body = stream.read(32 * 1024 * 1024 + 1)
    if len(body) > 32 * 1024 * 1024:
        raise ValueError("Expanded archive member exceeds bounds")
    return body


def reject_archive_token(archive: zipfile.ZipFile, token: bytes) -> None:
    """Reject credential echoes in any compressed member before retaining the ZIP."""
    expanded = 0
    for item in archive.infolist():
        if item.is_dir():
            continue
        overlap = b""
        with archive.open(item) as stream:
            while chunk := stream.read(64 * 1024):
                expanded += len(chunk)
                if expanded > 128 * 1024 * 1024:
                    raise ValueError("Archive expansion limit")
                combined = overlap + chunk
                if token in combined:
                    raise ValueError("Source echoed a private credential")
                overlap = combined[-len(token) :]
