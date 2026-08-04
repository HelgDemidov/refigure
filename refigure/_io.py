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


def normalize_source(source: Source) -> Path | bytes:
    """Path and bytes pass through unchanged; a file-like object is read fully."""
    if isinstance(source, Path):
        return source
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return source.read()
