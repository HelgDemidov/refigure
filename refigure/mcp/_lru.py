"""Generic bounded LRU + byte-budget store, shared by ``ServerState``
(``state.py``) and ``BoundedLruVlmCache`` (``vlm_cache.py``) — architecture
doc §7-bis: "the same LRU utility as the resource store", not two
independent implementations of the same eviction policy.

Byte-budget eviction is O(1) per operation: every entry carries its OWN
precomputed size (this class never (re)computes it — the caller does, and
is expected to do so OUTSIDE any lock it holds), and a running total is
adjusted by plain addition/subtraction on insert/evict. Recomputing every
remaining entry's size on each eviction (e.g. re-encoding/re-hashing the
whole store under the lock) would reproduce the exact stall the
architecture review measured for that approach: 34-60ms of event-loop
blocking vs. <1ms for this incremental counter.

Thread-safe via a plain ``threading.Lock`` — correct for both of this
class's callers even though they run in different execution contexts:
``ServerState`` is only ever touched from the event loop (architecture
doc §4's "mutations only from the event loop" invariant), while
``BoundedLruVlmCache`` is written from worker threads (core's
``enhance_docx_markdown`` calls ``VlmCacheBackend.set()`` synchronously,
inside the ``anyio.to_thread`` bridge). A plain lock protects either
caller identically — no async-specific primitive needed here.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Generic, TypeVar

# Not PEP 695 generics (`class BoundedLru[T]`) — this repo's floor is
# Python 3.10 (`pyproject.toml`'s `requires-python = ">=3.10"`), that
# syntax is 3.12+.
_T = TypeVar("_T")


class BoundedLru(Generic[_T]):
    """A dict-like LRU store bounded by BOTH entry count and total byte
    size — whichever limit is hit first evicts the oldest entries until
    neither is exceeded. Has no notion of time itself; a TTL (if the
    caller wants one, as ``ServerState`` does) is layered on top by
    storing a timestamp inside the value and checking it after ``get()``,
    not something this class enforces.

    A single entry larger than ``max_bytes`` on its own is kept, not
    evicted-on-arrival: eviction never drops the store to zero entries
    just because the one remaining entry is large — a caller calling
    ``get()`` right after ``insert()`` must always find what it just
    inserted. This only matters for a pathologically oversized single
    entry; every real caller in this codebase keeps individual entries
    well under their respective budgets (see ``state.py``/
    ``vlm_cache.py``)."""

    def __init__(self, *, max_entries: int, max_bytes: int) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[_T, int]] = OrderedDict()
        self._total_bytes = 0

    def insert(self, key: str, value: _T, size_bytes: int) -> None:
        """Insert (or replace) ``key``, then evict oldest entries while
        over either limit. ``size_bytes`` must be precomputed by the
        caller BEFORE calling this — see the module docstring; computing
        it here (e.g. from ``value``) would put arbitrary caller-defined
        serialization cost back under the lock, exactly what this class
        exists to avoid."""
        with self._lock:
            if key in self._data:
                _, old_size = self._data.pop(key)
                self._total_bytes -= old_size
            self._data[key] = (value, size_bytes)
            self._total_bytes += size_bytes
            while len(self._data) > 1 and (
                len(self._data) > self._max_entries or self._total_bytes > self._max_bytes
            ):
                _, (_, evicted_size) = self._data.popitem(last=False)
                self._total_bytes -= evicted_size

    def get(self, key: str) -> _T | None:
        """The value for ``key``, or ``None`` on a miss. A hit touches the
        entry as most-recently-used (moves it to the end) — a genuine LRU
        read, not just a lookup."""
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            value, _ = self._data[key]
            return value

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes
