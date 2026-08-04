"""Extras-isolation verification (stage 6, CI-only).

Confirms the extras architecture's core promise — asserted repeatedly in
CLAUDE.md/design docs ("refigure/docx.py imports only mammoth+markdownify",
"a bare pip install refigure never pulls in openpyxl") but never actually
CI-checked before this file: ``pip install refigure[docx]`` doesn't pull in
openpyxl, ``[xlsx]`` doesn't pull in mammoth, and importing the submodule
for a format whose extra isn't installed raises
``MissingOptionalDependencyError`` with an actionable message — not a bare
``ModuleNotFoundError``.

Only meaningful under CI's ``test-extras`` matrix, which installs
``refigure`` into a FRESH, isolated venv per leg (no shared
``requirements-dev.txt``, unlike the other 3 CI jobs) with exactly one
extras combination and sets ``REFIGURE_EXTRAS_UNDER_TEST`` accordingly. Not
meant to run against a normal dev venv, which has every extra installed at
once — the whole module skips there instead of asserting something false.
"""

from __future__ import annotations

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
