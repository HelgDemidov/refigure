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
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent

# (refigure module to import, PyPI dependency to simulate as absent)
_POISON_CASES = [
    ("refigure.docx", "mammoth"),
    ("refigure.xlsx", "openpyxl"),
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
        cwd=str(_REPO_ROOT),
        timeout=30,
    )
    observed = result.stdout.strip()
    assert observed == "refigure.api.MissingOptionalDependencyError", (
        f"importing {target_module} with {blocked_dependency} blocked raised "
        f"{observed!r}, expected refigure.api.MissingOptionalDependencyError "
        f"(stderr: {result.stderr})"
    )
