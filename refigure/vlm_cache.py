"""Concrete ``VlmCacheBackend`` implementations (``api.py``'s Protocol, see
its docstring).

``core/fsio.py`` (the source pipeline's atomic-write/exclusive-flock file
staging policy) is deliberately NOT ported here: it exists there to support
a persistent ``doc.md`` on disk with concurrent-process safety concerns
that don't apply to refigure's single in-memory ``convert()`` call.
``FileCacheBackend`` below does a plain (non-atomic) write — acceptable for
an opt-in, single-process convenience cache, not a production database.
"""

from __future__ import annotations

import json
from pathlib import Path


class InMemoryCacheBackend:
    """Default ``VlmCacheBackend`` (``Config.vlm_cache`` falls back to this):
    a plain dict, no disk I/O — safe for a library (no surprise files
    written to a caller's filesystem) and for concurrent/repeated calls
    within one process. Cache does not survive past the process, by
    design — that's what ``FileCacheBackend`` is for."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, object]] = {}

    def get(self, key: str) -> dict[str, object] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, object]) -> None:
        self._data[key] = value


class FileCacheBackend:
    """JSON-backed ``VlmCacheBackend``, for callers who want VLM responses to
    survive across ``convert()`` calls/processes (e.g. reconverting the same
    corpus repeatedly during development). JSON, not YAML like the source
    pipeline's ``.figures.yaml``: that choice was for human-readable git
    diffs of a tracked corpus cache, a scenario refigure doesn't have; JSON
    is stdlib, PyYAML isn't a dependency anywhere in this project.

    The whole cache is loaded into memory once at construction and the
    whole file is rewritten on every ``set()`` — simple and correct for the
    expected scale (a handful to a few hundred figures per document), not
    optimized for a huge shared cache."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, object]] = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> dict[str, object] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, object]) -> None:
        self._data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
