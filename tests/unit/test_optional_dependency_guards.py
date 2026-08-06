"""Behavioral regression guard for the exact bug class found by stage 6's
extras-isolation CI matrix (PR #8, see the ``project_extras_isolation_bug``
memory): a module-level ``try/except ImportError -> raise
MissingOptionalDependencyError`` guard is only effective if it runs BEFORE
any other same-package import that could itself transitively raise an
unguarded ``ImportError`` for the same dependency. ``xlsx.py``'s guard was
individually correct but ran too late — ``xlsx_charts.py``, imported one
line earlier, has its own unguarded ``openpyxl`` import.

Unlike ``tests/extras/test_extras_isolation.py`` (only meaningful under
CI's 4 isolated-venv matrix legs), this file runs in the FAST, always-run
unit suite, with the normal dev venv's openpyxl/mammoth genuinely
installed — it "poisons" the target module in a subprocess's
``sys.modules`` before the import, rather than needing a real venv without
the dependency. Catches an import-order regression immediately on any
future edit, in the same test run as everything else, not just in the
special CI job that's easy to overlook when touching an unrelated module.

Extend ``_POISON_CASES`` below when stage 4b's VLM module lands — same
pattern, new (module_name, blocked_dependency) pair.

``test_cli_lazy_import_isolates_formats`` below extends the same
discipline to ``refigure/cli.py`` (stage 6b): its per-format dispatch must
import ``refigure.docx``/``refigure.xlsx`` lazily, not at module level, so
that converting one format via the CLI still works when the other
format's dependency is absent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

from refigure.api import MissingOptionalDependencyError
from tests.support import REPO_ROOT

from .docx.test_docx import build_minimal_docx

_EXPECTED_MISSING_DEP_QUALNAME = (
    f"{MissingOptionalDependencyError.__module__}.{MissingOptionalDependencyError.__qualname__}"
)

# (refigure module to import, PyPI dependency to simulate as absent)
_POISON_CASES = [
    ("refigure.docx", "mammoth"),
    ("refigure.xlsx", "openpyxl"),
    ("refigure.vlm", "pdfplumber"),
]


@pytest.mark.parametrize("target_module,blocked_dependency", _POISON_CASES)
def test_missing_dependency_raises_typed_error_not_bare_import_error(
    target_module: str, blocked_dependency: str
) -> None:
    script = (
        f"import sys\n"
        f"sys.modules[{blocked_dependency!r}] = None\n"
        f"try:\n"
        f"    import {target_module}\n"
        f"except Exception as e:\n"
        f"    print(type(e).__module__ + '.' + type(e).__qualname__)\n"
        f"else:\n"
        f"    print('NO_EXCEPTION_RAISED')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    observed = result.stdout.strip()
    assert observed == _EXPECTED_MISSING_DEP_QUALNAME, (
        f"importing {target_module} with {blocked_dependency} blocked raised "
        f"{observed!r}, expected {_EXPECTED_MISSING_DEP_QUALNAME} "
        f"(stderr: {result.stderr})"
    )


# (refigure.vlm.client class name, PyPI dependency to simulate as absent)
_CLASS_LEVEL_POISON_CASES = [
    ("OpenAIClient", "openai"),
    ("AnthropicClient", "anthropic"),
]


@pytest.mark.parametrize("class_name,blocked_dependency", _CLASS_LEVEL_POISON_CASES)
def test_vlm_client_guard_is_class_level_not_module_level(
    class_name: str, blocked_dependency: str
) -> None:
    """OpenAIClient/AnthropicClient guard their SDK dependency inside
    __init__, not at refigure/vlm/client.py's module level like every
    other case above — OpenRouterClient has zero third-party deps and must
    keep working with openai/anthropic both absent (see
    docs/vlm/vlm-direct-clients/vlm-direct-clients-2026-08-06.md §4).
    Proves both halves: the module imports cleanly with the dependency
    poisoned, AND constructing the affected class still raises the typed
    error, not a bare ImportError."""
    script = (
        f"import sys\n"
        f"sys.modules[{blocked_dependency!r}] = None\n"
        f"from refigure.vlm.client import OpenRouterClient, {class_name}\n"
        f"print('MODULE_IMPORT_OK')\n"
        f"try:\n"
        f"    {class_name}(api_key='x')\n"
        f"except Exception as e:\n"
        f"    print(type(e).__module__ + '.' + type(e).__qualname__)\n"
        f"else:\n"
        f"    print('NO_EXCEPTION_RAISED')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[:1] == ["MODULE_IMPORT_OK"], (
        f"import refigure.vlm.client failed with {blocked_dependency!r} poisoned "
        f"(should succeed — the guard is class-level, not module-level): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert lines[1:2] == [_EXPECTED_MISSING_DEP_QUALNAME], (
        f"constructing {class_name} with {blocked_dependency!r} blocked raised "
        f"{lines[1:2]!r}, expected [{_EXPECTED_MISSING_DEP_QUALNAME!r}] "
        f"(stderr: {result.stderr})"
    )


def test_docx_convert_lazy_imports_vlm_only_when_use_vlm_true(tmp_path: Path) -> None:
    """docx.convert() with the default use_vlm=False must not import
    refigure.vlm — and therefore not pdfplumber — at all. Proves the lazy
    import inside docx.py's use_vlm-gated block; a bare `import
    refigure.vlm` succeeding/failing alone (the parametrized case above)
    wouldn't catch a regression to a module-level `from . import vlm` in
    docx.py, since that path is never exercised by importing docx.py with
    use_vlm never mentioned."""
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(build_minimal_docx(["distinctive docx paragraph"]))

    script = (
        "import sys\n"
        "sys.modules['pdfplumber'] = None\n"
        "from pathlib import Path\n"
        "from refigure.docx import convert\n"
        "from refigure.api import Config\n"
        f"result = convert(Path({str(doc_path)!r}), config=Config(use_vlm=False))\n"
        "assert 'refigure.vlm' not in sys.modules, "
        "'refigure.vlm was imported despite use_vlm=False'\n"
        "assert 'distinctive docx paragraph' in result.markdown\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"convert(use_vlm=False) with pdfplumber poisoned failed unexpectedly: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout.strip() == "OK"


def _write_minimal_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    assert wb.active is not None
    wb.active.append(["distinctive-xlsx-cell-value"])
    wb.save(path)


# (dependency to poison, format the CLI must still successfully convert)
_CLI_CROSS_FORMAT_CASES = [
    ("mammoth", "xlsx"),  # docx unusable -> refigure CLI converting .xlsx must still work
    ("openpyxl", "docx"),  # xlsx unusable -> refigure CLI converting .docx must still work
]


@pytest.mark.parametrize("poisoned_dependency,target_format", _CLI_CROSS_FORMAT_CASES)
def test_cli_lazy_import_isolates_formats(
    tmp_path: Path, poisoned_dependency: str, target_format: str
) -> None:
    """``refigure.cli``'s per-format dispatch (``_convert_fn``) imports
    ``refigure.docx``/``refigure.xlsx`` lazily — proves the CLI invoked for
    one format still works when the OTHER format's dependency is poisoned,
    not just that ``import refigure.cli`` itself doesn't crash (that alone
    wouldn't catch a regression to a top-level ``from . import docx, xlsx``
    in ``cli.py``, since neither format is actually exercised at import
    time). Closes the same guard-ordering risk class at the CLI boundary
    that ``xlsx.py``'s own guard closed at the library boundary (see module
    docstring)."""
    if target_format == "xlsx":
        target_path = tmp_path / "doc.xlsx"
        _write_minimal_xlsx(target_path)
        expect = "distinctive-xlsx-cell-value"
    else:
        target_path = tmp_path / "doc.docx"
        target_path.write_bytes(build_minimal_docx(["distinctive docx paragraph"]))
        expect = "distinctive docx paragraph"

    script = (
        f"import sys\n"
        f"sys.modules[{poisoned_dependency!r}] = None\n"
        f"from refigure.cli import main\n"
        f"sys.exit(main([{str(target_path)!r}]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"CLI conversion of a .{target_format} file failed with "
        f"{poisoned_dependency!r} poisoned (unrelated to {target_format}): "
        f"exit {result.returncode}, stderr: {result.stderr}"
    )
    assert expect in result.stdout
