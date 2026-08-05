"""API-level robustness tests: adversarial/edge-case input
through the public docx.convert()/xlsx.convert() surface, not the real
corpus-fixture behavioral tests (stage 5, gated on stage 3's licensing).

Covers: zip-bomb rejection through the public API (stage 1 only tested
zipsafe.py directly), truncated/corrupted zip, zero-byte input, non-ASCII
round-trip, and absence of shared state across repeated calls.
"""

from __future__ import annotations

import io
import time
import zipfile

import openpyxl
import pytest

import refigure.docx as docx
import refigure.xlsx as xlsx
from refigure.api import CorruptArchiveError, UnsupportedFormatError

from .docx.test_docx import build_minimal_docx


def _oversized_member_zip() -> bytes:
    """A zip with one member whose declared uncompressed size (129MB) is
    just over zipsafe's 128MB per-member limit — highly compressible, so
    building it is fast and the actual bytes on disk stay tiny (~128KB)."""
    size = 129 * 1024 * 1024
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"A" * size)
    return buf.getvalue()


def _corrupt_member_crc(data: bytes) -> bytes:
    """Flip a handful of bytes in the middle of a valid zip — corrupts a
    member's compressed data (bad CRC-32) without touching the central
    directory, so ZipFile() itself still opens fine."""
    mutable = bytearray(data)
    mid = len(mutable) // 2
    for i in range(mid, mid + 20):
        mutable[i] ^= 0xFF
    return bytes(mutable)


class TestZipBomb:
    def test_docx_convert_rejects_oversized_member(self) -> None:
        # Fixture construction (~1s, building the compressible 129MB member)
        # must not count against the rejection-speed budget below — time
        # only the convert() call itself.
        data = _oversized_member_zip()
        t0 = time.time()
        with pytest.raises(CorruptArchiveError):
            docx.convert(data)
        assert time.time() - t0 < 2.0  # rejected on declared size, never decompresses

    def test_xlsx_convert_rejects_oversized_member(self) -> None:
        data = _oversized_member_zip()
        t0 = time.time()
        with pytest.raises(CorruptArchiveError):
            xlsx.convert(data)
        assert time.time() - t0 < 2.0


class TestCorruptedZip:
    def test_docx_convert_rejects_truncated_zip(self) -> None:
        data = build_minimal_docx(["Hello"])
        truncated = data[: len(data) // 2]
        with pytest.raises(CorruptArchiveError):
            docx.convert(truncated)

    def test_xlsx_convert_rejects_truncated_zip(self) -> None:
        wb = openpyxl.Workbook()
        wb.active.append(["a"])
        buf = io.BytesIO()
        wb.save(buf)
        truncated = buf.getvalue()[: len(buf.getvalue()) // 2]
        with pytest.raises(CorruptArchiveError):
            xlsx.convert(truncated)

    def test_docx_convert_rejects_bad_crc(self) -> None:
        data = build_minimal_docx(["Hello"])
        with pytest.raises(CorruptArchiveError):
            docx.convert(_corrupt_member_crc(data))

    def test_xlsx_convert_rejects_bad_crc(self) -> None:
        wb = openpyxl.Workbook()
        wb.active.append(["a"])
        buf = io.BytesIO()
        wb.save(buf)
        with pytest.raises(CorruptArchiveError):
            xlsx.convert(_corrupt_member_crc(buf.getvalue()))


class TestZeroByteInput:
    def test_docx_convert_rejects_empty_bytes(self) -> None:
        with pytest.raises(CorruptArchiveError):
            docx.convert(b"")

    def test_xlsx_convert_rejects_empty_bytes(self) -> None:
        with pytest.raises(CorruptArchiveError):
            xlsx.convert(b"")


class TestNonAsciiRoundTrip:
    def test_docx_cyrillic_and_emoji_round_trip(self) -> None:
        text = "Привет, мир! 🎉 日本語"
        result = docx.convert(build_minimal_docx([text]))
        assert text in result.markdown

    def test_xlsx_cyrillic_and_emoji_round_trip(self) -> None:
        wb = openpyxl.Workbook()
        wb.active.append(["Привет", "🎉 日本語"])
        buf = io.BytesIO()
        wb.save(buf)
        result = xlsx.convert(buf.getvalue())
        assert "Привет" in result.markdown
        assert "🎉 日本語" in result.markdown


class TestNoSharedStateAcrossCalls:
    def test_repeated_docx_calls_are_independent(self) -> None:
        good = build_minimal_docx(["Stable content"])
        bad_format = _oversized_member_zip()

        # Interleave valid/invalid calls — a previous call's exception or
        # accumulated state must not leak into the next, unrelated call.
        for _ in range(5):
            result = docx.convert(good)
            assert result.markdown.strip().startswith("Stable content")
            assert result.warnings == []

            with pytest.raises(CorruptArchiveError):
                docx.convert(bad_format)

        # And once more, to confirm the good path still works cleanly after
        # N interleaved failures.
        final = docx.convert(good)
        assert final.markdown.strip().startswith("Stable content")
        assert final.warnings == []

    def test_repeated_xlsx_calls_are_independent(self) -> None:
        wb = openpyxl.Workbook()
        wb.active.append(["stable", "content"])
        buf = io.BytesIO()
        wb.save(buf)
        good = buf.getvalue()
        bad_format = _oversized_member_zip()

        for _ in range(5):
            result = xlsx.convert(good)
            assert "stable" in result.markdown
            assert result.warnings == []

            with pytest.raises(CorruptArchiveError):
                xlsx.convert(bad_format)

        final = xlsx.convert(good)
        assert "stable" in final.markdown
        assert final.warnings == []

    def test_docx_and_xlsx_calls_do_not_interfere_with_each_other(self) -> None:
        docx_data = build_minimal_docx(["From docx"])
        wb = openpyxl.Workbook()
        wb.active.append(["From xlsx"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_data = buf.getvalue()

        for _ in range(3):
            with pytest.raises(UnsupportedFormatError):
                docx.convert(xlsx_data)  # valid xlsx is not a valid docx
            with pytest.raises(UnsupportedFormatError):
                xlsx.convert(docx_data)  # and vice versa

        assert "From docx" in docx.convert(docx_data).markdown
        assert "From xlsx" in xlsx.convert(xlsx_data).markdown
