"""Extras-isolation verification (stage 6, CI-only).

Confirms the extras architecture's core promise — asserted repeatedly in
CLAUDE.md/design docs ("refigure/docx/__init__.py imports only mammoth+markdownify",
"a bare pip install refigure never pulls in openpyxl") but never actually
CI-checked before this file: ``pip install refigure[docx]`` doesn't pull in
openpyxl, ``[xlsx]`` doesn't pull in mammoth, ``[vlm]`` doesn't pull in
mammoth/openpyxl (stage 4b, 2026-08-05 — same import-order risk class PR
#8 found in ``xlsx.py``), and importing the submodule for a format/
capability whose extra isn't installed raises
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

_HAS_DOCX = _EXTRAS in ("docx", "both", "docx+vlm")
_HAS_XLSX = _EXTRAS in ("xlsx", "both")
# vlm-direct depends on [vlm] too (self-referential extra, see
# pyproject.toml's vlm-direct comment) — pdfplumber is present there too.
_HAS_VLM = _EXTRAS in ("vlm", "docx+vlm", "vlm-direct")
_HAS_VLM_DIRECT = _EXTRAS == "vlm-direct"
# [mcp] has no self-referential coupling to [docx]/[xlsx]/[vlm] (unlike
# vlm-direct) — a single "mcp" leg is enough, no combinatorial legs needed
# (mcp-server-phase1-skeleton spec §9).
_HAS_MCP = _EXTRAS == "mcp"


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


def test_vlm_submodule_matches_extras() -> None:
    from refigure import MissingOptionalDependencyError

    if _HAS_VLM:
        import refigure.vlm  # noqa: F401
    else:
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[vlm\]"):
            import refigure.vlm  # noqa: F401


def test_mcp_submodule_matches_extras() -> None:
    from refigure import MissingOptionalDependencyError

    if _HAS_MCP:
        import refigure.mcp  # noqa: F401
    else:
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[mcp\]"):
            import refigure.mcp  # noqa: F401


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


def test_pdfplumber_only_importable_when_vlm_extra_present() -> None:
    try:
        import pdfplumber  # noqa: F401

        importable = True
    except ModuleNotFoundError:
        importable = False
    assert importable == _HAS_VLM, (
        f"pdfplumber importable={importable}, expected={_HAS_VLM} for extras={_EXTRAS!r} "
        "— a leaked/missing transitive dependency in the [vlm] extra"
    )


def test_openai_and_anthropic_only_importable_when_vlm_direct_extra_present() -> None:
    for pkg in ("openai", "anthropic"):
        try:
            __import__(pkg)
            importable = True
        except ModuleNotFoundError:
            importable = False
        assert importable == _HAS_VLM_DIRECT, (
            f"{pkg} importable={importable}, expected={_HAS_VLM_DIRECT} for "
            f"extras={_EXTRAS!r} — a leaked/missing transitive dependency in "
            "the [vlm-direct] extra"
        )


def test_mcp_sdk_only_importable_when_mcp_extra_present() -> None:
    try:
        import mcp as _mcp_sdk  # noqa: F401

        importable = True
    except ModuleNotFoundError:
        importable = False
    assert importable == _HAS_MCP, (
        f"mcp importable={importable}, expected={_HAS_MCP} for extras={_EXTRAS!r} "
        "— a leaked/missing dependency in the [mcp] extra"
    )


def test_mcp_cli_help_and_version_work_on_the_mcp_leg() -> None:
    if not _HAS_MCP:
        pytest.skip("refigure-mcp isn't installed outside the mcp leg")
    for flag in ("--help", "--version"):
        result = subprocess.run(
            [sys.executable, "-m", "refigure.mcp.cli", flag],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"refigure-mcp {flag} failed under extras={_EXTRAS!r}: {result.stderr}"
        )


def test_mcp_build_server_registers_zero_tools_without_docx_or_xlsx() -> None:
    """The exact format-isolation guarantee refigure/mcp/server.py's own
    module docstring makes: a bare refigure[mcp] install (this leg has
    neither [docx] nor [xlsx]) still builds a working server, just with
    0 tools registered — never a crash from an eager top-level import of
    refigure.docx/refigure.xlsx."""
    if not _HAS_MCP:
        pytest.skip("only meaningful on the mcp leg")
    if _HAS_DOCX or _HAS_XLSX:  # pragma: no cover - not reachable on any current leg
        pytest.skip("this leg unexpectedly also has [docx]/[xlsx] — not this test's case")

    from refigure.mcp.server import build_server

    server = build_server()
    assert server._tool_manager.list_tools() == []


def test_vlm_client_direct_classes_match_extras() -> None:
    """3-tier boundary, found live 2026-08-05 while implementing vlm-direct:
    refigure.vlm.client is a submodule of refigure.vlm, so importing it
    ALWAYS runs refigure/vlm/__init__.py's pdfplumber guard first —
    OpenAIClient/AnthropicClient's own guard only gets a chance to fire if
    that first one already passed."""
    from refigure.api import MissingOptionalDependencyError

    if _HAS_VLM_DIRECT:
        from refigure.vlm.client import AnthropicClient, OpenAIClient

        OpenAIClient(api_key="x")
        AnthropicClient(api_key="x")
    elif _HAS_VLM:
        from refigure.vlm.client import AnthropicClient, OpenAIClient

        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[vlm-direct\]"):
            OpenAIClient(api_key="x")
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[vlm-direct\]"):
            AnthropicClient(api_key="x")
    else:
        with pytest.raises(MissingOptionalDependencyError, match=r"refigure\[vlm\]"):
            from refigure.vlm.client import OpenAIClient  # noqa: F401


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


# --- --vlm (vlm-activation spec §2/§3/Тестовое покрытие) -------------------
# No real network call is exercised anywhere below — same principle as
# test_anthropic_*_live.py: a live VLM call is opt-in-gated, never part of
# the regular/extras-matrix CI run.


def test_cli_vlm_flag_without_vlm_extra_is_exit_missing_dependency(tmp_path: Path) -> None:
    """``refigure --vlm x.docx`` exits cleanly with EXIT_MISSING_DEPENDENCY
    on every leg lacking ``[vlm]`` — ``bare``/``xlsx`` (``[docx]`` itself is
    unavailable, that guard fires first, before ``--vlm`` is ever reached)
    and the ``docx`` leg (docx parses fine, fails specifically on missing
    pdfplumber/``[vlm]`` when ``use_vlm=True`` tries to import ``refigure.vlm``).
    Legs that DO have ``[vlm]`` are covered by
    ``test_cli_vlm_flag_matches_extras`` instead."""
    if _HAS_VLM:
        pytest.skip("this leg has [vlm] — covered by test_cli_vlm_flag_matches_extras instead")
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(_build_minimal_docx("hello"))

    result = _run_cli(str(doc_path), "--vlm")

    assert result.returncode == 5, result.stderr
    expected = "refigure[vlm]" if _HAS_DOCX else "refigure[docx]"
    assert expected in result.stderr


def test_cli_vlm_flag_matches_extras(tmp_path: Path) -> None:
    """Complement of the test above: legs WITH ``[vlm]`` (``vlm``,
    ``docx+vlm``, ``vlm-direct``). Only ``docx+vlm`` also has ``[docx]`` —
    the sole leg in this matrix combining both — so only it can actually
    complete a conversion; the other two still fail, but now on the
    DIFFERENT, already-covered ``[docx]`` boundary (see
    ``test_cli_docx_conversion_matches_extras``), not the one this test
    targets."""
    if not _HAS_VLM:
        pytest.skip("this leg lacks [vlm] — covered by the test above instead")
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(_build_minimal_docx("hello from the extras matrix"))

    result = _run_cli(str(doc_path), "--vlm")

    if _HAS_DOCX:
        assert result.returncode == 0, result.stderr
        assert "hello from the extras matrix" in result.stdout
    else:
        assert result.returncode == 5
        assert "refigure[docx]" in result.stderr


def test_cli_vlm_provider_openai_without_vlm_direct_extra_is_exit_missing_dependency(
    tmp_path: Path,
) -> None:
    """``--vlm --vlm-provider openai`` fails cleanly with
    EXIT_MISSING_DEPENDENCY on every leg lacking ``[vlm-direct]``. Client
    construction happens in ``_build_config()``, before any source file is
    even read — the placeholder file's content/format is irrelevant to this
    outcome, unlike the plain ``--vlm`` test above (which needs a real
    ``.docx`` to reach the point where ``refigure.vlm`` gets imported at
    all)."""
    if _HAS_VLM_DIRECT:
        pytest.skip("this leg has [vlm-direct] — no negative case without live credentials")
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(b"placeholder, _build_config fails before this is ever read")

    result = _run_cli(str(doc_path), "--vlm", "--vlm-provider", "openai", "--vlm-model", "m")

    assert result.returncode == 5, result.stderr
    expected = "refigure[vlm-direct]" if _HAS_VLM else "refigure[vlm]"
    assert expected in result.stderr
