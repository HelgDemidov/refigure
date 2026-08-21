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

Phase 3 (``docs/mcp-server/mcp-server-phase3-http-auth/
mcp-server-phase3-http-auth-2026-08-21.md`` §1) adds two additive members —
existing callers that don't pass ``on_evict``/don't call ``remove()`` see
no behavior change: an optional ``on_evict`` callback, invoked for entries
evicted by ``insert()``'s own count/byte-budget loop (``ServerState``'s
per-caller soft-cap accounting needs to know which OTHER caller's entry a
given ``insert()`` just evicted — this class has no other way to tell it),
and ``remove()``, an explicit point-eviction the caller already knows
about (deliberately NOT routed through ``on_evict`` — the caller doing the
removing already updates its own bookkeeping at the call site, invoking
the callback too would double-count).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

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

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        on_evict: Callable[[str, _T, int], None] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._on_evict = on_evict
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[_T, int]] = OrderedDict()
        self._total_bytes = 0

    def insert(self, key: str, value: _T, size_bytes: int) -> None:
        """Insert (or replace) ``key``, then evict oldest entries while
        over either limit. ``size_bytes`` must be precomputed by the
        caller BEFORE calling this — see the module docstring; computing
        it here (e.g. from ``value``) would put arbitrary caller-defined
        serialization cost back under the lock, exactly what this class
        exists to avoid.

        ``on_evict`` (if set) is called once per entry evicted by THIS
        call's own count/byte-budget loop — AFTER ``self._lock`` has
        already been released (never while held: a lock this class
        doesn't own must never be acquired while this one is, and
        ``ServerState``'s own callback does exactly that with its own
        ``self._lock`` — see the inline comment below for the full
        lock-ordering rationale). A replacement of ``key`` itself
        (already present, same key re-inserted) is NOT reported via
        ``on_evict`` — that's a value update, not an eviction, and the
        caller performing the ``insert()`` already knows about it
        directly."""
        evicted: list[tuple[str, _T, int]] = []
        with self._lock:
            if key in self._data:
                _, old_size = self._data.pop(key)
                self._total_bytes -= old_size
            self._data[key] = (value, size_bytes)
            self._total_bytes += size_bytes
            while len(self._data) > 1 and (
                len(self._data) > self._max_entries or self._total_bytes > self._max_bytes
            ):
                evicted_key, (evicted_value, evicted_size) = self._data.popitem(last=False)
                self._total_bytes -= evicted_size
                if self._on_evict is not None:
                    evicted.append((evicted_key, evicted_value, evicted_size))
        # Callbacks fire AFTER releasing the lock (not each iteration inside
        # the loop above): a lock this class doesn't own must never be
        # acquired while this one is held, and ServerState's own callback
        # (§4 of the phase-3 spec) does exactly that (its own `self._lock`).
        # Reporting collected evictions post-unlock avoids any risk of a
        # lock-ordering cycle, at the cost of a caller theoretically
        # observing `on_evict` fire slightly after `insert()`'s own state
        # change is externally visible — irrelevant here, every real
        # `on_evict` caller is on the same single-threaded event loop.
        if self._on_evict is not None:
            for evicted_key, evicted_value, evicted_size in evicted:
                self._on_evict(evicted_key, evicted_value, evicted_size)

    def remove(self, key: str) -> _T | None:
        """Explicit point-eviction of ``key`` — the value it held, or
        ``None`` if ``key`` wasn't present. Deliberately does NOT invoke
        ``on_evict`` (see the module/class docstrings): the caller
        removing a specific key already knows it's doing so and updates
        its own bookkeeping at the call site — ``ServerState``'s soft-cap
        self-eviction (phase-3 spec §4) is the one real caller."""
        with self._lock:
            entry = self._data.pop(key, None)
            if entry is None:
                return None
            value, size_bytes = entry
            self._total_bytes -= size_bytes
            return value

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
