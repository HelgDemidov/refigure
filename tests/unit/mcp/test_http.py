"""Real Streamable HTTP transport tests — phase-3 spec §8. Unlike every
other MCP test in this suite (in-memory ``Client(mcp_server)``), these
drive the actual ASGI app ``mcp_server.streamable_http_app()`` builds,
over ``httpx.ASGITransport`` (in-process, no real socket/port) — the only
way to exercise real bearer-auth middleware, per-request Authorization
headers, and genuine HTTP status codes (401 on a missing/invalid token).

Three real mechanics this harness needed, none obvious from the SDK's own
docs, found live against ``mcp==2.0.0`` while writing this file (see the
phase-3 spec §0/commit history for the corrections this drove):

1. ``httpx.ASGITransport`` does NOT drive the ASGI lifespan protocol on
   its own — the Starlette app's own ``session_manager.run()`` (wired via
   ``lifespan=lambda app: session_manager.run()`` inside the SDK's
   ``streamable_http_app()``) never starts, and every request fails with
   "Task group is not initialized." ``app.router.lifespan_context(app)``
   must be driven explicitly around every request.
2. The DNS-rebinding protection ``streamable_http_app()`` auto-enables
   for a loopback host rejects a bare ``Host: 127.0.0.1`` — the allowed
   pattern is ``"127.0.0.1:*"``, so the test client's ``base_url`` needs
   an explicit port, not just a loopback hostname.
3. ``mcp.client.streamable_http.streamable_http_client()`` yields a
   2-tuple ``(read_stream, write_stream)``, not 3.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from typing import AsyncIterator

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from refigure.mcp.server import build_server
from tests.unit.docx.test_docx import build_minimal_docx

pytestmark = pytest.mark.anyio

_BASE_URL = "http://127.0.0.1:8000"


@contextlib.asynccontextmanager
async def _connected_session(app, *, token: str) -> AsyncIterator[ClientSession]:
    """A real ClientSession over the real ASGI app, authenticated with
    ``token`` — reused by every test below that needs to get past the
    ``initialize()`` handshake (the 401-only tests deal in raw httpx
    requests instead, see below)."""
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )
    async with http_client:
        async with streamable_http_client(url=f"{_BASE_URL}/mcp", http_client=http_client) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _build_http_server(**kwargs):
    kwargs.setdefault("token_map", {"tok-alice": "alice", "tok-bob": "bob"})
    kwargs.setdefault("rate_limit_count", 1000)
    return build_server(transport="http", **kwargs)


async def test_server_reports_its_own_version_over_http_too() -> None:
    """Same regression as test_tools.py's stdio-side version, exercised
    against the OTHER build_server() construction branch (token_map is
    not None -> the token_verifier/AuthSettings path) — both branches
    pass version=__version__ as a separate literal, line coverage alone
    doesn't prove this one's value is right."""
    import refigure

    mcp_server = _build_http_server()
    app = mcp_server.streamable_http_app()

    async with app.router.lifespan_context(app):
        http_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            headers={"Authorization": "Bearer tok-alice"},
        )
        async with http_client:
            async with streamable_http_client(url=f"{_BASE_URL}/mcp", http_client=http_client) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()

    assert init.server_info.version == refigure.__version__
    assert init.server_info.version != ""


async def test_valid_bearer_token_converts_successfully() -> None:
    mcp_server = _build_http_server()
    app = mcp_server.streamable_http_app()
    b64 = base64.b64encode(build_minimal_docx(["hello over real http"])).decode("ascii")

    async with app.router.lifespan_context(app):
        async with _connected_session(app, token="tok-alice") as session:
            result = await session.call_tool("convert_docx", {"content_base64": b64})

    assert result.is_error is False
    assert "hello over real http" in result.structured_content["markdown"]


async def test_missing_authorization_header_gets_401() -> None:
    mcp_server = _build_http_server()
    app = mcp_server.streamable_http_app()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_BASE_URL
        ) as raw:
            response = await raw.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )

    assert response.status_code == 401


async def test_wrong_bearer_token_gets_401() -> None:
    mcp_server = _build_http_server()
    app = mcp_server.streamable_http_app()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=_BASE_URL,
            headers={"Authorization": "Bearer not-a-configured-token"},
        ) as raw:
            response = await raw.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )

    assert response.status_code == 401


async def test_two_tokens_get_isolated_resource_stores() -> None:
    """Regression on phase 2's caller_id-scoped ServerState.get() (§4 of
    the phase-2 spec), now exercised through two REAL, differently
    authenticated bearer tokens instead of a mocked get_access_token()."""
    mcp_server = _build_http_server(resource_inline_threshold_bytes=1)
    app = mcp_server.streamable_http_app()
    big_doc = base64.b64encode(
        build_minimal_docx(["hello world, definitely more than one byte of markdown"])
    ).decode("ascii")

    async with app.router.lifespan_context(app):
        async with _connected_session(app, token="tok-alice") as alice:
            result = await alice.call_tool("convert_docx", {"content_base64": big_doc})
            resource_uri = result.structured_content["resource_uri"]
            assert resource_uri is not None

            read_back = await alice.read_resource(resource_uri)
            assert "hello world" in read_back.contents[0].text

        async with _connected_session(app, token="tok-bob") as bob:
            with pytest.raises(Exception, match="not found"):
                await bob.read_resource(resource_uri)


async def test_rate_limit_rejects_over_a_real_http_round_trip() -> None:
    # max_batch_size must not exceed rate_limit_count (build_server()'s own
    # phase-4 validation) — explicit here since the default max_batch_size
    # (20) would otherwise fail this deliberately tiny rate_limit_count.
    mcp_server = _build_http_server(rate_limit_count=1, max_batch_size=1)
    app = mcp_server.streamable_http_app()
    b64 = base64.b64encode(build_minimal_docx(["one"])).decode("ascii")

    async with app.router.lifespan_context(app):
        async with _connected_session(app, token="tok-alice") as session:
            r1 = await session.call_tool("convert_docx", {"content_base64": b64})
            r2 = await session.call_tool("convert_docx", {"content_base64": b64})

    assert r1.is_error is False
    assert r2.is_error is True
    assert "RateLimitExceededError" in (r2.content[0].text if r2.content else "")


async def test_concurrent_calls_over_one_session_all_complete() -> None:
    """The moment architecture doc §12 п.3 calls out explicitly: ServerState's
    lock actually exercised under real concurrent HTTP traffic, not just a
    unit-level call to check_and_consume_rate_limit()."""
    mcp_server = _build_http_server(max_concurrent=8)
    app = mcp_server.streamable_http_app()
    b64 = base64.b64encode(build_minimal_docx(["concurrent"])).decode("ascii")

    async with app.router.lifespan_context(app):
        async with _connected_session(app, token="tok-alice") as session:
            results = await asyncio.gather(
                *(session.call_tool("convert_docx", {"content_base64": b64}) for _ in range(8))
            )

    assert len(results) == 8
    assert all(r.is_error is False for r in results)
