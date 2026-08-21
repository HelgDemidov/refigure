"""Concrete ``VlmCacheBackend`` implementations (``api.py``'s Protocol, see
its docstring).

``core/fsio.py`` (the source pipeline's atomic-write/exclusive-flock file
staging policy) is deliberately NOT ported here in full: it exists there to
support a persistent ``doc.md`` on disk with concurrent-PROCESS safety
concerns that don't apply to refigure's single in-memory ``convert()`` call.
``FileCacheBackend`` below DOES need its own, narrower concurrent-THREAD
safety — a single instance shared across concurrently-running conversions
(e.g. an MCP server's async bridge, PR mcp-server-phase1-skeleton §3) can
have multiple ``set()`` calls interleave on the same process. A
``threading.Lock`` around the read-modify-write plus an atomic
tempfile+``os.replace`` swap (not a torn ``write_text``) closes both the
lost-update race and the corrupted-file-on-crash window; still not a
production database (single JSON blob, no indexing, no cross-process
locking) — the opt-in, convenience-cache framing stays, "safe under
concurrent access from one process" is the bar this file now clears.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
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
    corpus repeatedly during development, or one long-lived server process
    serving many conversions). JSON, not YAML like the source pipeline's
    ``.figures.yaml``: that choice was for human-readable git diffs of a
    tracked corpus cache, a scenario refigure doesn't have; JSON is stdlib,
    PyYAML isn't a dependency anywhere in this project.

    The whole cache is loaded into memory once at construction and the
    whole file is rewritten on every ``set()`` — simple and correct for the
    expected scale (a handful to a few hundred figures per document), not
    optimized for a huge shared cache. ``set()`` is safe under concurrent
    calls from multiple threads of the SAME process (``threading.Lock`` +
    an atomic tempfile-then-``os.replace`` write — never a partially-written
    file, even if the process crashes mid-write); it is NOT safe shared
    across multiple PROCESSES (no cross-process file locking — a second
    process's writes and this one's can still race)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
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
        # No lock: a plain dict read against concurrent single-key writes
        # is safe under CPython's GIL (no torn read), and this is the ONLY
        # operation that doesn't also touch the filesystem — see the class
        # docstring. The real race this module closes lives entirely in
        # set()'s read-modify-write-to-disk sequence.
        return self._data.get(key)

    def set(self, key: str, value: dict[str, object]) -> None:
        with self._lock:
            self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True)
            # Atomic swap: write to a sibling tempfile (same directory, so
            # os.replace is a same-filesystem rename, not a cross-device
            # copy) then rename over the real path in one step — a reader
            # never observes a partially-written file, and a crash mid-write
            # leaves the ORIGINAL file intact, not a truncated one.
            fd, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp_name, self.path)
            except BaseException:
                os.unlink(tmp_name)
                raise
