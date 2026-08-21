"""``refigure://conversion/{id}`` — the resource behind the
``resource_uri`` branch (``server.py``'s ``_register_conversion_resource``).

The tool-call -> resource-uri -> resource-read happy path is already
covered in ``test_tools.py`` (``test_resource_uri_is_readable_back_through_
the_conversion_resource``); this file focuses on the resource's OWN
edge cases — ownership, expiry, listing — most easily reached by
building a ``ServerState``/server directly, not always through a real
conversion.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

from refigure.mcp.server import build_server
from tests.unit.docx.test_docx import build_minimal_docx

pytestmark = pytest.mark.anyio


async def _insert_via_real_conversion(threshold_bytes: int = 1) -> tuple[object, str]:
    """Build a server with a tiny inline threshold, run one real
    conversion through it, return (server, resource_uri)."""
    mcp_server = build_server(resource_inline_threshold_bytes=threshold_bytes)
    async with Client(mcp_server, raise_exceptions=True) as c:
        b64 = base64.b64encode(
            build_minimal_docx(["hello world, definitely more than 1 byte"])
        ).decode("ascii")
        result = await c.call_tool("convert_docx", {"content_base64": b64})
    return mcp_server, result.structured_content["resource_uri"]


async def test_reading_an_unknown_id_raises_the_sdks_resource_not_found_error() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        with pytest.raises(Exception) as exc_info:
            await c.read_resource("refigure://conversion/doesnotexist")

    # The real message, not the SDK's generic "Error creating resource
    # from template" wrapper — a regression guard for the live finding
    # that motivated using ResourceNotFoundError specifically (any OTHER
    # exception type loses its own text entirely).
    assert "conversion result not found" in str(exc_info.value)


async def test_a_different_callers_entry_is_not_found_not_forbidden() -> None:
    mcp_server, resource_uri = await _insert_via_real_conversion()

    def _fake_token():
        class _T:
            client_id = "someone-else"

        return _T()

    with patch("refigure.mcp.state.get_access_token", _fake_token):
        async with Client(mcp_server, raise_exceptions=True) as c:
            with pytest.raises(Exception) as exc_info:
                await c.read_resource(resource_uri)

    assert "conversion result not found" in str(exc_info.value)


async def test_the_owning_caller_can_still_read_it_back() -> None:
    mcp_server, resource_uri = await _insert_via_real_conversion()

    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.read_resource(resource_uri)

    assert "hello world" in result.contents[0].text


async def test_resources_list_stays_empty_even_with_real_entries() -> None:
    mcp_server, _resource_uri = await _insert_via_real_conversion()

    async with Client(mcp_server, raise_exceptions=True) as c:
        listed = await c.list_resources()

    assert listed.resources == []


async def test_read_conversion_directly_raises_the_sdks_own_exception_class() -> None:
    """Direct unit-level check on the handler itself (not just its effect
    through a Client round trip, covered above): confirms read_conversion
    genuinely raises ResourceNotFoundError, not a look-alike class the
    SDK's collapsing behavior (§0(3) of the spec) would still swallow.
    Calls the registered function directly (via the resource manager's own
    template registry), bypassing the SDK's create_resource() wrapping —
    that wrapping is what the Client-based tests above already exercise."""
    from mcp.server.mcpserver import MCPServer

    from refigure.mcp.server import _register_conversion_resource
    from refigure.mcp.state import ServerState

    class _FakeServerCtx:
        def __init__(self) -> None:
            self.state = ServerState(
                max_entries=10,
                max_bytes=10_000,
                ttl_s=3600,
                rate_limit_count=30,
                rate_limit_window_s=60,
            )

    mcp = MCPServer("x")
    _register_conversion_resource(mcp, _FakeServerCtx())  # type: ignore[arg-type]
    handler = mcp._resource_manager._templates["refigure://conversion/{id}"].fn

    with pytest.raises(ResourceNotFoundError):
        await handler(id="doesnotexist")
