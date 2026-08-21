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
import time

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

    def __init__(self, *, max_entries: int, max_bytes: int, ttl_s: float) -> None:
        self._lru: BoundedLru[tuple[str, str, float]] = BoundedLru(
            max_entries=max_entries, max_bytes=max_bytes
        )
        self._ttl_s = ttl_s

    def insert(self, caller_id: str, markdown: str) -> str:
        """Store ``markdown`` under a fresh random id, tagged with
        ``caller_id``. 128 bits of randomness (``secrets.token_urlsafe``'s
        16 input bytes) — collisions inside a bounded, TTL'd, at-most-a-
        few-hundred-entry store are negligible; this is unguessability,
        not a long-lived secret."""
        conversion_id = secrets.token_urlsafe(16)
        size_bytes = len(markdown.encode("utf-8"))
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
