"""MCP-server subpackage (v2, stage 10) — a thin protocol wrapper over
``refigure.docx.convert()``/``refigure.xlsx.convert()``, not new
conversion logic. See ``docs/mcp-server/mcp-server-architecture/
mcp-server-architecture.md`` for the full design.

Guard is the first same-package-import-adjacent statement in this file —
same discipline as ``refigure/vlm/__init__.py``'s own guard docstring
explains: a module-level ``try/except ImportError`` is only effective if
it runs before any OTHER same-package import that could itself
transitively raise an unguarded ``ImportError``. This subpackage currently
has no such transitive risk (``server.py``/``cli.py`` only import
``..api`` at module level, itself ``lxml``-only), but the ordering
discipline holds regardless of today's safety being circumstantial.

No self-referential coupling to ``[docx]``/``[xlsx]``/``[vlm]`` (unlike
``vlm-direct``, see ``pyproject.toml``'s comment there) — a bare
``refigure[mcp]`` install is valid, it just registers 0 tools (``docx``/
``xlsx`` are imported lazily, per-tool, inside ``server.py``).
"""

from __future__ import annotations

from ..api import MissingOptionalDependencyError

try:
    import mcp as _mcp_sdk  # noqa: F401 - import-for-guard, see module docstring
except ImportError as exc:  # pragma: no cover - see tests/unit/test_optional_dependency_guards.py
    raise MissingOptionalDependencyError(
        "refigure[mcp] is required to use the refigure-mcp server"
    ) from exc
