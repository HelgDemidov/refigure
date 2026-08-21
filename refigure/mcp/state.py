"""``ServerState`` — the in-memory conversion-result store behind
``refigure://conversion/{id}`` (architecture doc §4), plus
``resolve_caller_id()``, the single caller-identity helper used by both
the tool path (``server.py``'s ``_run_convert_tool``) and the resource-read
path (``server.py``'s ``read_conversion``).

Only the converted markdown is stored here, not a full ``ConversionResult``
— every OTHER field (``warnings``/``charts_found``/etc.) is already
returned inline in the tool's own structured output even when the markdown
itself is too large to inline (see ``server.py``'s ``resource_uri``
branch); the resource only ever needs to serve the markdown body a caller
couldn't fit inline.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict

from mcp.server.auth.middleware.auth_context import get_access_token

from ._lru import BoundedLru

# Sentinel caller_id on stdio (and the in-memory test transport): the
# transport has no notion of an authenticated identity at all, unlike
# HTTP (phase 3), where get_access_token() returns a real AccessToken.
# Confirmed live against mcp==2.0.0: get_access_token() is a stable None
# on this transport, both before and after an internal `await` within the
# same request coroutine — safe to call after the async bridge returns,
# not just at the top of a handler.
_LOCAL_CALLER_ID = "__local__"


def resolve_caller_id() -> str:
    """The identity of the current request's caller — ``__local__`` on
    stdio/in-memory, the token's ``client_id`` on an authenticated HTTP
    request (phase 3). Works identically from a tool body or a resource
    handler: both run on the event loop, where the auth contextvar
    ``get_access_token()`` reads from is valid."""
    token = get_access_token()
    return token.client_id if token is not None else _LOCAL_CALLER_ID


class ServerState:
    """LRU store of converted markdown, addressable by a cryptographically
    random id, bounded by entry count OR total byte size (whichever hits
    first) and a TTL. Wraps ``BoundedLru`` (``_lru.py``) — the same
    eviction primitive ``vlm_cache.BoundedLruVlmCache`` uses, per
    architecture doc §7-bis.

    Every entry is tagged with the ``caller_id`` that produced it; ``get()``
    treats "not found", "expired", and "belongs to a different caller" as
    the SAME ``None`` result, deliberately — distinguishing them would let
    an unauthorized caller learn that <em>something</em> exists at a given
    id, which the "unlisted" design (architecture doc §4) is meant to
    avoid leaking in the first place."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        ttl_s: float,
        rate_limit_count: int,
        rate_limit_window_s: float,
        soft_cap_enabled: bool = False,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._lru: BoundedLru[tuple[str, str, float]] = BoundedLru(
            max_entries=max_entries, max_bytes=max_bytes, on_evict=self._on_evict
        )
        self._ttl_s = ttl_s
        self._rate_limit_count = rate_limit_count
        self._rate_limit_window_s = rate_limit_window_s
        # ServerState's OWN lock — separate from _lru's internal one
        # (architecture doc §4: "всё admission-time состояние под ОДНИМ
        # threading.Lock"). Same insurance-not-sole-defense role _lru.py's
        # own lock plays: every real caller only ever touches ServerState
        # from the event loop (get()/insert() are called from tool/resource
        # handlers, never a to_thread worker), so this is a documented
        # invariant backed by a lock, not the only thing preventing a race.
        self._lock = threading.Lock()
        self._rate_counters: dict[str, tuple[int, float]] = {}
        # Soft-cap (architecture doc §4, phase-3 spec §4) — active only
        # when build_server()/cli.py computed ≥2 distinct configured
        # caller_ids (a single-caller deployment gets zero protective value
        # from capping itself at a quarter of its own store, see the
        # architecture review's own self-defeat finding). Both dicts stay
        # empty and untouched when disabled — insert() below simply skips
        # the block that would populate them; _on_evict's lookups on them
        # are then always no-ops.
        self._soft_cap_enabled = soft_cap_enabled
        self._by_caller: dict[str, OrderedDict[str, None]] = {}
        self._caller_bytes: dict[str, int] = {}

    def _on_evict(
        self, evicted_id: str, evicted_value: tuple[str, str, float], evicted_size: int
    ) -> None:
        """``BoundedLru``'s eviction callback (``_lru.py`` §1) — fires
        synchronously inside ``self._lru.insert()``, itself called from
        inside ``insert()``'s own ``with self._lock:`` block below, so
        this method must NEVER acquire ``self._lock`` itself (already
        held by the caller two frames up). Keeps the soft-cap
        per-caller index honest when the GLOBAL count/byte-budget policy
        (not soft-cap's own self-eviction, which goes through
        ``self._lru.remove()`` directly and updates these same dicts at
        its own call site) evicts an entry — including, harmlessly, one
        belonging to the SAME caller that triggered the eviction."""
        evicted_caller_id, _, _ = evicted_value
        caller_ids = self._by_caller.get(evicted_caller_id)
        if caller_ids is not None:
            caller_ids.pop(evicted_id, None)
        if evicted_caller_id in self._caller_bytes:
            self._caller_bytes[evicted_caller_id] -= evicted_size

    def check_and_consume_rate_limit(self, caller_id: str, *, n: int = 1) -> bool:
        """Admit (and atomically consume) ``n`` units of ``caller_id``'s
        per-window conversion quota, or refuse without consuming anything.
        Fixed window (not a token bucket — architecture doc §6 п.3 asks
        for simple runaway/leaked-token protection, not strict fairness;
        a caller straddling a window boundary can burst up to ~2x the
        configured rate, an accepted tradeoff for this goal).

        ``n`` defaults to 1 — one file conversion (architecture doc §6
        п.3: "единица учёта — конверсия ОДНОГО файла"), which is all
        phase 3 ever passes. The parameter exists so phase 4's
        ``convert_batch`` can admit an entire batch atomically against
        the same counter without a second admission mechanism (phase-3
        spec §4/Вне скоупа) — not exercised with ``n != 1`` anywhere in
        this phase."""
        now = time.monotonic()
        with self._lock:
            count, window_start = self._rate_counters.get(caller_id, (0, now))
            if now - window_start >= self._rate_limit_window_s:
                count, window_start = 0, now
            if count + n > self._rate_limit_count:
                self._rate_counters[caller_id] = (count, window_start)
                return False
            self._rate_counters[caller_id] = (count + n, window_start)
            return True

    def insert(self, caller_id: str, markdown: str) -> str:
        """Store ``markdown`` under a fresh random id, tagged with
        ``caller_id``. 128 bits of randomness (``secrets.token_urlsafe``'s
        16 input bytes) — collisions inside a bounded, TTL'd, at-most-a-
        few-hundred-entry store are negligible; this is unguessability,
        not a long-lived secret.

        When soft-cap is enabled: BEFORE inserting, trims ``caller_id``'s
        OWN oldest entries (via ``self._lru.remove()``, not through the
        global count/byte-budget loop) until it's back under ~1/4 of the
        store's entry/byte budget (architecture doc §4). This is how "a
        cheap flooding caller doesn't evict someone else's entries" is
        actually achieved: each caller's own growth trims their own tail
        first, so the shared store's global LRU eviction — which picks
        the OLDEST entry regardless of whose it is — is never the first
        thing to kick in for a caller that's over their own quota."""
        conversion_id = secrets.token_urlsafe(16)
        size_bytes = len(markdown.encode("utf-8"))
        with self._lock:
            if self._soft_cap_enabled:
                quota_entries = max(1, self._max_entries // 4)
                quota_bytes = max(1, self._max_bytes // 4)
                caller_ids = self._by_caller.setdefault(caller_id, OrderedDict())
                while caller_ids and (
                    len(caller_ids) >= quota_entries
                    or self._caller_bytes.get(caller_id, 0) + size_bytes > quota_bytes
                ):
                    oldest_id, _ = caller_ids.popitem(last=False)
                    evicted = self._lru.remove(oldest_id)
                    if evicted is not None:
                        _, evicted_markdown, _ = evicted
                        self._caller_bytes[caller_id] = self._caller_bytes.get(caller_id, 0) - len(
                            evicted_markdown.encode("utf-8")
                        )
                caller_ids[conversion_id] = None
                self._caller_bytes[caller_id] = self._caller_bytes.get(caller_id, 0) + size_bytes
            self._lru.insert(conversion_id, (caller_id, markdown, time.monotonic()), size_bytes)
        return conversion_id

    def get(self, conversion_id: str, caller_id: str) -> str | None:
        """The stored markdown for ``conversion_id``, or ``None`` if it
        doesn't exist, was evicted, has expired its TTL, or belongs to a
        different ``caller_id`` — all four are indistinguishable to the
        caller by design (see the class docstring)."""
        entry = self._lru.get(conversion_id)
        if entry is None:
            return None
        entry_caller_id, markdown, inserted_at = entry
        if time.monotonic() - inserted_at > self._ttl_s:
            return None
        if entry_caller_id != caller_id:
            return None
        return markdown
