"""Extras-isolation verification (stage 6, CI-only).

Confirms the extras architecture's core promise — asserted repeatedly in
CLAUDE.md/design docs ("refigure/docx.py imports only mammoth+markdownify",
"a bare pip install refigure never pulls in openpyxl") but never actually
CI-checked before this file: ``pip install refigure[docx]`` doesn't pull in
openpyxl, ``[xlsx]`` doesn't pull in mammoth, and importing the submodule
for a format whose extra isn't installed raises
``MissingOptionalDependencyError`` with an actionable message — not a bare
``ModuleNotFoundError``. Also covers the ``refigure`` console command
(stage 6b): it must work (``--help``/``--version``) in every leg including
``bare`` — argparse is stdlib, no extra needed for the entry point itself —
and per-format conversion through it must match the leg's extras exactly,
same contract as the submodule tests below but through the real installed
CLI entry point end-to-end.

Only meaningful under CI's ``test-extras`` matrix, which installs
``refigure`` into a FRESH, isolated venv per leg (no shared
``requirements-dev.txt``, unlike the other 3 CI jobs) with exactly one
extras combination and sets ``REFIGURE_EXTRAS_UNDER_TEST`` accordingly. Not
meant to run against a normal dev venv, which has every extra installed at
once — the whole module skips there instead of asserting something false.
"""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_EXTRAS = None
try:
    import os

    _EXTRAS = os.environ["REFIGURE_EXTRAS_UNDER_TEST"]
except KeyError:
    pass

if _EXTRAS is None:
    pytest.skip(
        "REFIGURE_EXTRAS_UNDER_TEST not set — this suite only makes sense "
        "under CI's test-extras matrix (isolated per-leg installs), not a "
        "normal dev venv with every extra installed at once",
        allow_module_level=True,
    )

_HAS_DOCX = _EXTRAS in ("docx", "both")
_HAS_XLSX = _EXTRAS in ("xlsx", "both")


def test_core_types_always_importable() -> None:
    """Config/ConversionResult/exceptions are core-tier (lxml-only) —
    importable regardless of which extras, if any, are installed."""
    import refigure

    assert refigure.Config is not None
    assert refigure.ConversionResult is not None
    assert refigure.UnsupportedFormatError is not None
    assert refigure.CorruptArchiveError is not None
    assert refigure.MissingOptionalDependencyError is not None


def test_docx_submodule_matches_extras() -> None:
    from refigure import MissingOptionalDependencyError

    if _HAS_DOCX:
        import refigure.docx  # noqa: F401 — import succeeding is the assertion
    else:
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[docx\]"):
            import refigure.docx  # noqa: F401


def test_xlsx_submodule_matches_extras() -> None:
    from refigure import MissingOptionalDependencyError

    if _HAS_XLSX:
        import refigure.xlsx  # noqa: F401
    else:
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[xlsx\]"):
            import refigure.xlsx  # noqa: F401


def test_mammoth_only_importable_when_docx_extra_present() -> None:
    try:
        import mammoth  # noqa: F401

        importable = True
    except ModuleNotFoundError:
        importable = False
    assert importable == _HAS_DOCX, (
        f"mammoth importable={importable}, expected={_HAS_DOCX} for extras={_EXTRAS!r} "
        "— a leaked/missing transitive dependency in the [docx] extra"
    )


def test_openpyxl_only_importable_when_xlsx_extra_present() -> None:
    try:
        import openpyxl  # noqa: F401

        importable = True
    except ModuleNotFoundError:
        importable = False
    assert importable == _HAS_XLSX, (
        f"openpyxl importable={importable}, expected={_HAS_XLSX} for extras={_EXTRAS!r} "
        "— a leaked/missing transitive dependency in the [xlsx] extra"
    )


# --- CLI (stage 6b) --------------------------------------------------------
# The console_script entry point (refigure/cli.py) has no dependency of its
# own — argparse is stdlib — and must work in every leg, including bare.
# Actual format conversion must match the leg's extras exactly, same
# contract as the submodule-import tests above but exercised through the
# real installed entry point end-to-end, not just `import refigure.xlsx`.

_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _build_minimal_docx(text: str) -> bytes:
    """Hand-rolled OOXML via ``zipfile`` only — building one needs no
    mammoth (only reading it does), so this works even in legs where
    ``[docx]`` is absent."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{w}"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "refigure", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_help_and_version_work_regardless_of_extras() -> None:
    for flag in ("--help", "--version"):
        result = _run_cli(flag)
        assert result.returncode == 0, (
            f"refigure {flag} failed under extras={_EXTRAS!r}: {result.stderr}"
        )


def test_cli_docx_conversion_matches_extras(tmp_path: Path) -> None:
    doc_path = tmp_path / "doc.docx"
    if _HAS_DOCX:
        doc_path.write_bytes(_build_minimal_docx("hello from the extras matrix"))
        result = _run_cli(str(doc_path))
        assert result.returncode == 0, result.stderr
        assert "hello from the extras matrix" in result.stdout
    else:
        # Content is irrelevant — the guard fires before any parsing.
        doc_path.write_bytes(b"placeholder, never parsed")
        result = _run_cli(str(doc_path))
        assert result.returncode == 5
        assert "refigure[docx]" in result.stderr


def test_cli_xlsx_conversion_matches_extras(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "doc.xlsx"
    if _HAS_XLSX:
        import openpyxl  # only importable in this leg — that's the point

        wb = openpyxl.Workbook()
        assert wb.active is not None
        wb.active.append(["cell value from the extras matrix"])
        wb.save(xlsx_path)
        result = _run_cli(str(xlsx_path))
        assert result.returncode == 0, result.stderr
        assert "cell value from the extras matrix" in result.stdout
    else:
        xlsx_path.write_bytes(b"placeholder, never parsed")
        result = _run_cli(str(xlsx_path))
        assert result.returncode == 5
        assert "refigure[xlsx]" in result.stderr
