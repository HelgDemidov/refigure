"""Property-based robustness tests (Hypothesis) — per July 2026 best-practice
research, the dominant technique for answering "is this API safe against
inputs nobody thought to write by hand". Generalizes the exact class of bug
tests/unit/test_robustness.py already found twice by manual construction
(mammoth/openpyxl raising an unexpected exception type for a malformation
shape nobody had tried yet).

The invariant under test, everywhere: docx.convert()/xlsx.convert() either
succeed (return a ConversionResult) or raise exactly one of
{UnsupportedFormatError, CorruptArchiveError} — never hang, never raise
anything else. Deliberately excludes MissingOptionalDependencyError (an
import-time condition, not a per-call one) and doesn't assert anything
about the *content* of a successful result — that's stage 5's job.
"""

from __future__ import annotations

import io
import zipfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import refigure.docx as docx
import refigure.xlsx as xlsx
from refigure.api import ConversionResult, CorruptArchiveError, UnsupportedFormatError

_TYPED_ERRORS = (UnsupportedFormatError, CorruptArchiveError)

# Parsing untrusted bytes should be fast even when it succeeds — but some
# generated examples make it deep enough into mammoth/lxml/openpyxl that the
# default 200ms per-example deadline is too tight to be meaningful signal.
_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _assert_safe(fn, data: bytes) -> None:
    try:
        result = fn(data)
        assert isinstance(result, ConversionResult)
    except _TYPED_ERRORS:
        pass  # a typed, documented failure mode — not a bug


@_SETTINGS
@given(st.binary(max_size=4096))
def test_docx_convert_never_raises_unexpected_exception_on_arbitrary_bytes(data: bytes) -> None:
    _assert_safe(docx.convert, data)


@_SETTINGS
@given(st.binary(max_size=4096))
def test_xlsx_convert_never_raises_unexpected_exception_on_arbitrary_bytes(data: bytes) -> None:
    _assert_safe(xlsx.convert, data)


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _docx_shell_with_document_xml(document_xml: bytes) -> bytes:
    """A structurally valid docx zip (correct [Content_Types].xml, correct
    _rels/.rels) except word/document.xml is arbitrary — exercises deep
    parsing rather than just "is this a zip at all", which is where the
    real bugs in this codebase were actually found."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


@_SETTINGS
@given(st.one_of(st.binary(max_size=500), st.text(max_size=500).map(str.encode)))
def test_docx_convert_never_raises_unexpected_exception_with_malformed_document_xml(
    document_xml: bytes,
) -> None:
    _assert_safe(docx.convert, _docx_shell_with_document_xml(document_xml))


def _xlsx_shell_with_sheet_xml(sheet_xml: bytes) -> bytes:
    """Take a real, valid xlsx (built by openpyxl) and replace its one
    worksheet part with arbitrary content — same rationale as the docx
    variant: structurally valid container, malformed inner part."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    base = io.BytesIO()
    wb.save(base)

    buf = io.BytesIO()
    with zipfile.ZipFile(base) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            if name == "xl/worksheets/sheet1.xml":
                dst.writestr(name, sheet_xml)
            else:
                dst.writestr(name, src.read(name))
    return buf.getvalue()


@_SETTINGS
@given(st.one_of(st.binary(max_size=500), st.text(max_size=500).map(str.encode)))
def test_xlsx_convert_never_raises_unexpected_exception_with_malformed_sheet_xml(
    sheet_xml: bytes,
) -> None:
    _assert_safe(xlsx.convert, _xlsx_shell_with_sheet_xml(sheet_xml))
