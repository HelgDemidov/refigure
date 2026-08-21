"""MCP-server-local exception types — deliberately NOT in ``refigure/api.py``
alongside ``VlmMarkerLimitExceededError`` et al.: everything in this module
is a transport/admission concept of ``refigure-mcp`` itself, never
something ``docx.convert()``/``xlsx.convert()`` raise or the plain
``refigure`` CLI needs to catch. See
``docs/mcp-server/mcp-server-phase3-http-auth/
mcp-server-phase3-http-auth-2026-08-21.md`` §3.
"""

from __future__ import annotations


class RateLimitExceededError(Exception):
    """A ``caller_id`` exceeded its per-window conversion quota
    (``ServerState.check_and_consume_rate_limit`` — architecture doc §6
    п.3, HTTP-always, including a deployment with only one configured
    token). Raised at admission time, before the sync-core bridge runs —
    no partial processing, same discipline as
    ``VlmMarkerLimitExceededError``."""
