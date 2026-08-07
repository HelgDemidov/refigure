"""Input normalization for refigure.docx/refigure.xlsx.

zipfile.ZipFile and openpyxl.load_workbook already accept either a path or a
file-like object directly, so most of the conversion pipeline needs no
changes to support ``bytes``/``BinaryIO`` input. This module only bridges the
one gap: an arbitrary file-like object isn't itself something the rest of the
pipeline knows how to re-read multiple times, so it gets read into ``bytes``
once, up front.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

Source = Path | bytes | BinaryIO


class NotARegularFileError(OSError):
    """A ``Path`` source does not refer to a regular file (e.g. a directory,
    FIFO/named pipe, or device node). Mirrors the builtin
    ``IsADirectoryError``/``NotADirectoryError`` convention of subclassing
    ``OSError`` for this class of filesystem-shape mismatch."""


def normalize_source(source: Source) -> Path | bytes:
    """Path and bytes pass through unchanged; a file-like object is read fully.

    A ``Path`` is validated with ``is_file()`` — a non-blocking ``stat()``
    call, never opening the file — before being returned. Without this, a
    ``Path`` pointing at a FIFO/named pipe with no writer would pass
    through untouched and later hang forever inside
    ``zipfile.ZipFile(path)`` (via ``zipsafe.check_archive``), with no
    timeout anywhere in the call chain. The CLI already gates on
    ``is_file()`` before ever reaching a conversion call, so this only
    closes the gap for direct library callers (security-audit finding
    #17)."""
    if isinstance(source, Path):
        if not source.is_file():
            raise NotARegularFileError(f"not a regular file: {source}")
        return source
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return source.read()
