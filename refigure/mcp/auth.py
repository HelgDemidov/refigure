"""Token-file loading + the static-token ``TokenVerifier`` implementation
behind ``refigure-mcp --transport http``. See
``docs/mcp-server/mcp-server-phase3-http-auth/
mcp-server-phase3-http-auth-2026-08-21.md`` §2 for the design this module
implements, and architecture doc §6 for the requirements it comes from.

This module is a config-loading/auth-verification concern, not a
conversion-tool concern — its own ``ValueError``s are handled at CLI
startup (``cli.py``, before ``build_server()`` is even called), never
routed through ``server.py``'s ``_TYPED_EXCEPTIONS``/``_call_and_wrap_errors``
(that's for exceptions a live tool call can raise, which this isn't).
"""

from __future__ import annotations

import hmac
from pathlib import Path

from mcp.server.auth.provider import AccessToken, TokenVerifier

from .state import _LOCAL_CALLER_ID


def _normalize(value: str) -> str:
    """The one normalization function used both when loading the token
    file and (indirectly, via the tuples ``_StaticTokenVerifier`` is built
    from) when verifying a request's bearer token — architecture doc §6:
    "одна функция нормализации ... на стартовую дедупликацию и
    рантайм-проверку". Case-sensitive, ``strip()`` only — no case-folding,
    a bearer token is opaque data, not a human-typed identifier."""
    return value.strip()


def load_token_file(path: Path) -> dict[str, str]:
    """Parse a ``--mcp-auth-token-file``: one ``token = caller_id`` pair
    per non-blank line (exactly one ``=``; no comment syntax — the
    architecture doc doesn't call for one, and this format doesn't need
    it). Returns ``{token: caller_id}``.

    Raises ``ValueError`` (line number included in the message) on: a
    line with no ``=`` or more than one; an empty token or ``caller_id``
    after normalization; ``caller_id == "__local__"`` (reserved for the
    stdio/in-memory sentinel, architecture doc §6 — accepting it here
    would let a configured HTTP caller collide with the unauthenticated
    local identity); the SAME token value mapped to two DIFFERENT
    ``caller_id``s (almost certainly a typo — silently letting the last
    line win would hide a real misconfiguration). The same token mapped
    to the SAME ``caller_id`` twice is a harmless idempotent duplicate,
    not an error. DIFFERENT tokens mapped to the same ``caller_id`` is
    the normal token-rotation case (architecture doc §6) and needs no
    special handling at all — it's just two distinct dict keys."""
    token_map: dict[str, str] = {}
    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.count("=") != 1:
            raise ValueError(
                f"{path}:{lineno}: expected exactly one 'token = caller_id' pair, got {raw_line!r}"
            )
        raw_token, raw_caller_id = line.split("=", 1)
        token = _normalize(raw_token)
        caller_id = _normalize(raw_caller_id)
        if not token or not caller_id:
            raise ValueError(f"{path}:{lineno}: token and caller_id must both be non-empty")
        if caller_id == _LOCAL_CALLER_ID:
            raise ValueError(
                f"{path}:{lineno}: caller_id {_LOCAL_CALLER_ID!r} is reserved "
                "for the stdio/unauthenticated identity, not usable in a token file"
            )
        existing = token_map.get(token)
        if existing is not None and existing != caller_id:
            raise ValueError(
                f"{path}:{lineno}: token already maps to caller_id {existing!r}, "
                f"got a second, different caller_id {caller_id!r} for the same token value"
            )
        token_map[token] = caller_id
    return token_map


class _StaticTokenVerifier(TokenVerifier):
    """``TokenVerifier`` (SDK protocol, ``mcp.server.auth.provider``) over a
    fixed ``{token: caller_id}`` map loaded once at server startup — no
    live-reload (phase-3 spec, out of scope; architecture doc §6 phrases
    it conditionally, "если появится").

    Verification is a linear scan with ``hmac.compare_digest`` per
    candidate, NOT a dict lookup by token value — architecture doc §6:
    "сравнение — constant-time". A dict lookup is faster (O(1) vs. O(n))
    but its timing depends on the incoming token's hash/equality path in
    a way ``compare_digest`` is specifically designed to avoid; the token
    list in a self-hosted deployment is small (single/low-double digits),
    so the O(n) scan costs nothing that matters here."""

    def __init__(self, token_map: dict[str, str]) -> None:
        self._entries: tuple[tuple[str, str], ...] = tuple(token_map.items())

    async def verify_token(self, token: str) -> AccessToken | None:
        for configured_token, caller_id in self._entries:
            if hmac.compare_digest(token, configured_token):
                return AccessToken(token=token, client_id=caller_id, scopes=[])
        return None
