"""``BoundedLruVlmCache`` — the server-wide default ``VlmCacheBackend``
(``refigure/api.py``), replacing today's per-call ``InMemoryCacheBackend()``
(``Config.vlm_cache=None``'s own fallback) with ONE bounded-LRU instance
shared across every ``convert_docx`` call for the server's lifetime —
architecture doc §7-bis measured the per-call default at "0% reuse" (2
paid ``send()`` calls instead of 1 on a repeat of the same document).

Also ``acquire_vlm_cache_file_lock()``, the guard against two
``refigure-mcp`` processes (or two ``build_server()`` calls in one
process — verified live: ``flock`` locks are per open-file-description,
so a second ``os.open()``+``flock()`` on the same path fails even within
one process) pointed at the same ``--mcp-vlm-cache <path>``, which the
architecture review verified loses writes (last-writer-wins on
``FileCacheBackend``'s own whole-file rewrite).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ._lru import BoundedLru

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows only, see acquire_vlm_cache_file_lock
    fcntl = None  # type: ignore[assignment]

# Keeps every acquired lock's file descriptor open (and therefore the
# flock held) for the lifetime of the process — flock releases
# automatically when its fd closes, so an fd allowed to be
# garbage-collected would silently release the lock early.
_held_lock_fds: list[int] = []


def acquire_vlm_cache_file_lock(path: Path) -> None:
    """Best-effort guard against two instances sharing one
    ``--mcp-vlm-cache`` path (architecture doc §7-bis). POSIX-only
    (``fcntl``): a non-blocking exclusive ``flock`` on a sibling
    ``<path>.lock`` file. On Windows (no ``fcntl``) this degrades to a
    logged warning, not a hard block — this project's own tooling
    (``soffice`` resolution via ``shutil.which``, its CI) is already
    POSIX-oriented and untested on Windows, so a partial guard there is
    consistent with the existing posture, not a new gap.

    Raises ``ValueError`` immediately if the lock is already held — by
    another process, or by an earlier call in THIS process (the fd is
    never released until the process exits or this module is torn down)."""
    if fcntl is None:
        logger.warning(
            "refigure-mcp: cannot verify exclusive access to %s (fcntl unavailable, "
            "not a POSIX platform) — two instances pointed at the same --mcp-vlm-cache "
            "path can silently lose cache writes to each other",
            path,
        )
        return
    lock_path = path.parent / f"{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise ValueError(
            f"another refigure-mcp instance already has {path} open via "
            f"--mcp-vlm-cache (lock file: {lock_path})"
        ) from exc
    _held_lock_fds.append(fd)


class BoundedLruVlmCache:
    """``VlmCacheBackend`` (``refigure/api.py``'s Protocol) implementation
    with copy-on-get AND copy-on-set — not just a thin ``BoundedLru``
    wrapper, because core genuinely aliases the dict it gets back:
    ``refigure/vlm/__init__.py``'s judge-backfill path
    (``entry["judge_verdict"] = ...`` around lines 1194-1197/1266-1275)
    mutates the SAME dict object returned by ``_cache_get_safely`` in
    place, then passes that same object to ``_cache_set_safely``. Under
    two concurrent conversions of the same document (identical
    ``marker_id``s — a real MCP-server scenario, not hypothetical), a
    cache that hands out live references would let one conversion's
    in-progress mutation leak into the other's read, or a caller's later
    unrelated mutation silently corrupt an already-``set()`` entry. A
    shallow ``dict(...)`` copy on both ends closes this: ``judge_verdict``
    is always reassigned wholesale (``entry["judge_verdict"] = ...``),
    never mutated in place as a list, so a shallow copy is sufficient —
    no deep-copy needed for this specific value shape."""

    def __init__(self, *, max_entries: int, max_bytes: int) -> None:
        self._lru: BoundedLru[dict[str, object]] = BoundedLru(
            max_entries=max_entries, max_bytes=max_bytes
        )

    def get(self, key: str) -> dict[str, object] | None:
        entry = self._lru.get(key)
        return dict(entry) if entry is not None else None

    def set(self, key: str, value: dict[str, object]) -> None:
        size_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        self._lru.insert(key, dict(value), size_bytes)
