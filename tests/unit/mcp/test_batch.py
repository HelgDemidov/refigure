"""``convert_batch`` — phase-4 spec (``docs/mcp-server/
mcp-server-phase4-batch-progress/mcp-server-phase4-batch-progress-
2026-08-21.md``). In-memory ``Client(mcp_server)`` tests for the whole-call
admission/isolation/aggregation behavior, plus a few direct unit-level
calls to ``_run_convert_batch_tool`` (mirroring ``test_bridge.py``'s own
``_RecordingCtx`` pattern) for progress/heartbeat, which a Client round
trip can't observe directly.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import anyio
import openpyxl
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from refigure.mcp.server import (
    BatchItem,
    _register_convert_batch,
    _run_convert_batch_tool,
    _ServerContext,
    build_server,
)
from refigure.mcp.state import ServerState
from refigure.mcp.vlm_cache import BoundedLruVlmCache
from tests.unit.docx.test_docx import build_minimal_docx

pytestmark = pytest.mark.anyio


def _minimal_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _b64_docx(*paragraphs: str) -> str:
    return base64.b64encode(build_minimal_docx(list(paragraphs))).decode("ascii")


def _b64_xlsx() -> str:
    return base64.b64encode(_minimal_xlsx_bytes()).decode("ascii")


def _text(result: Any) -> str:
    return result.content[0].text if result.content else ""


@pytest.fixture
async def client():
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        yield c


# --- happy path + aggregates -------------------------------------------


async def test_mixed_docx_xlsx_batch_succeeds_with_correct_aggregates(client: Client) -> None:
    result = await client.call_tool(
        "convert_batch",
        {
            "items": [
                {"format": "docx", "content_base64": _b64_docx("hello docx")},
                {"format": "xlsx", "content_base64": _b64_xlsx()},
            ]
        },
    )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["total"] == 2
    assert sc["succeeded"] == 2
    assert sc["failed"] == 0
    assert "hello docx" in sc["items"][0]["markdown"]
    assert sc["items"][0]["status"] == "ok"
    assert "hello" in sc["items"][1]["markdown"]
    assert sc["items"][1]["status"] == "ok"


async def test_via_path(client: Client, tmp_path) -> None:
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(build_minimal_docx(["hello via path"]))

    result = await client.call_tool(
        "convert_batch", {"items": [{"format": "docx", "path": str(doc_path)}]}
    )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["succeeded"] == 1
    assert "hello via path" in sc["items"][0]["markdown"]


# --- per-item isolation ---------------------------------------------------


async def test_one_malformed_item_does_not_abort_the_others(client: Client) -> None:
    result = await client.call_tool(
        "convert_batch",
        {
            "items": [
                {"format": "docx", "content_base64": _b64_docx("still fine")},
                {"format": "docx", "content_base64": "not-valid-base64!!"},
            ]
        },
    )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["total"] == 2
    assert sc["succeeded"] == 1
    assert sc["failed"] == 1
    assert sc["items"][0]["status"] == "ok"
    assert "still fine" in sc["items"][0]["markdown"]
    assert sc["items"][1]["status"] == "error"
    assert (
        sc["items"][1]["error"] == "content_base64 is not valid base64: Only base64 data is allowed"
    )
    assert sc["items"][1]["markdown"] is None


async def test_xlsx_use_vlm_warns_per_item_without_a_real_vlm_path(client: Client) -> None:
    result = await client.call_tool(
        "convert_batch",
        {"items": [{"format": "xlsx", "content_base64": _b64_xlsx()}], "use_vlm": True},
    )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["items"][0]["status"] == "ok"
    assert sc["items"][0]["vlm_used"] is False
    assert any("no VLM path" in w for w in sc["items"][0]["warnings"])


# --- structural (whole-batch) admission -----------------------------------


async def test_empty_items_is_a_clean_error(client: Client) -> None:
    result = await client.call_tool("convert_batch", {"items": []})

    assert result.is_error is True
    assert "items must be non-empty" in _text(result)


async def test_batch_over_max_batch_size_is_rejected_not_silently_truncated() -> None:
    mcp_server = build_server(max_batch_size=2)
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.call_tool(
            "convert_batch",
            {"items": [{"format": "docx", "content_base64": _b64_docx("x")}] * 3},
        )

    assert result.is_error is True
    assert "exceeds the configured max_batch_size of 2" in _text(result)


async def test_neither_path_nor_content_base64_on_one_item_rejects_the_whole_batch(
    client: Client,
) -> None:
    result = await client.call_tool(
        "convert_batch",
        {
            "items": [
                {"format": "docx", "content_base64": _b64_docx("would have been fine")},
                {"format": "docx"},
            ]
        },
    )

    assert result.is_error is True
    assert "each item needs exactly one of path or content_base64" in _text(result)


async def test_both_path_and_content_base64_on_one_item_rejects_the_whole_batch(
    client: Client,
) -> None:
    result = await client.call_tool(
        "convert_batch",
        {
            "items": [
                {"format": "docx", "path": "irrelevant.docx", "content_base64": "eA=="},
            ]
        },
    )

    assert result.is_error is True
    assert "each item needs exactly one of path or content_base64" in _text(result)


async def test_path_on_one_item_rejects_the_whole_batch_over_http() -> None:
    mcp_server = build_server(transport="http")
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.call_tool(
            "convert_batch",
            {
                "items": [
                    {"format": "docx", "content_base64": _b64_docx("fine")},
                    {"format": "docx", "path": "/etc/passwd"},
                ]
            },
        )

    assert result.is_error is True
    assert "path is not accepted over HTTP" in _text(result)


async def test_path_still_works_in_a_batch_over_stdio(client: Client, tmp_path) -> None:
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(build_minimal_docx(["stdio path in a batch"]))

    result = await client.call_tool(
        "convert_batch", {"items": [{"format": "docx", "path": str(doc_path)}]}
    )

    assert result.is_error is False


# --- resource_uri branch ---------------------------------------------------


async def test_oversized_item_gets_resource_uri_readable_back() -> None:
    mcp_server = build_server(resource_inline_threshold_bytes=1)
    async with Client(mcp_server, raise_exceptions=True) as c:
        b64 = _b64_docx("hello world, definitely more than one byte of markdown")

        result = await c.call_tool(
            "convert_batch", {"items": [{"format": "docx", "content_base64": b64}]}
        )
        sc = result.structured_content
        assert sc["items"][0]["markdown"] is None
        resource_uri = sc["items"][0]["resource_uri"]
        assert resource_uri is not None and resource_uri.startswith("refigure://conversion/")

        read_back = await c.read_resource(resource_uri)

    assert "hello world" in read_back.contents[0].text


# --- atomic rate-limit admission --------------------------------------------


async def test_rate_limit_rejects_the_whole_batch_atomically() -> None:
    mcp_server = build_server(transport="http", rate_limit_count=3, max_batch_size=3)
    async with Client(mcp_server, raise_exceptions=True) as c:
        b64 = _b64_docx("x")

        # A batch of 3 exactly exhausts the window (no items run yet).
        r1 = await c.call_tool(
            "convert_batch", {"items": [{"format": "docx", "content_base64": b64}] * 3}
        )
        # A second call, even for just 1 item, must be rejected wholesale —
        # the window is already fully consumed by the first batch.
        r2 = await c.call_tool(
            "convert_batch", {"items": [{"format": "docx", "content_base64": b64}]}
        )

    assert r1.is_error is False
    assert r1.structured_content["succeeded"] == 3
    assert r2.is_error is True
    assert "RateLimitExceededError" in _text(r2)


async def test_rate_limit_is_never_applied_over_stdio_even_for_a_large_batch() -> None:
    mcp_server = build_server(rate_limit_count=1)  # transport defaults to "stdio"
    async with Client(mcp_server, raise_exceptions=True) as c:
        b64 = _b64_docx("x")

        result = await c.call_tool(
            "convert_batch", {"items": [{"format": "docx", "content_base64": b64}] * 5}
        )

    assert result.is_error is False
    assert result.structured_content["succeeded"] == 5


# --- build_server()'s own max_batch_size/rate_limit_count validation -------


def test_build_server_rejects_a_batch_ceiling_above_the_rate_window() -> None:
    with pytest.raises(ValueError, match="max_batch_size"):
        build_server(
            transport="http",
            token_map={"tok": "alice"},
            rate_limit_count=5,
            max_batch_size=10,
        )


def test_build_server_accepts_a_batch_ceiling_at_or_below_the_rate_window() -> None:
    server = build_server(
        transport="http", token_map={"tok": "alice"}, rate_limit_count=5, max_batch_size=5
    )
    assert server is not None


def test_max_batch_size_is_unvalidated_without_auth_configured() -> None:
    # token_map is None (stdio, or HTTP with no auth — which cli.py itself
    # never allows, but build_server() is still called directly here) —
    # rate-limit isn't enforced either way, so an oversized default ceiling
    # relative to a tiny rate_limit_count is not a real misconfiguration.
    server = build_server(rate_limit_count=1, max_batch_size=20)
    assert server is not None


# --- progress + heartbeat (direct call, mirrors test_bridge.py) -----------


class _RecordingCtx:
    def __init__(self) -> None:
        self.ticks: list[tuple[int, int | None, str | None]] = []

    async def report_progress(self, progress, total=None, message=None) -> None:
        self.ticks.append((progress, total, message))


def _build_ctx(**overrides: Any) -> _ServerContext:
    from refigure import docx as docx_module

    state = ServerState(
        max_entries=100,
        max_bytes=100_000_000,
        ttl_s=3600,
        rate_limit_count=1000,
        rate_limit_window_s=60,
    )
    defaults: dict[str, Any] = dict(
        limiter=anyio.CapacityLimiter(8),
        vlm_client=None,
        vlm_api_key=None,
        vlm_max_markers=None,
        max_input_b64_mb=100,
        timeout_s=5.0,
        state=state,
        vlm_cache=BoundedLruVlmCache(max_entries=100, max_bytes=1_000_000),
        resource_inline_threshold_bytes=256 * 1024,
        max_batch_size=20,
        batch_convert_fns={"docx": docx_module.convert},
        transport="stdio",
    )
    defaults.update(overrides)
    return _ServerContext(**defaults)


async def test_progress_ticks_once_per_completed_item() -> None:
    items = [BatchItem(format="docx", content_base64=_b64_docx(f"item {i}")) for i in range(3)]
    ctx = _build_ctx()
    rec = _RecordingCtx()

    result = await _run_convert_batch_tool(items, False, False, None, None, ctx, rec)  # type: ignore[arg-type]

    assert result.succeeded == 3
    completions = [t for t in rec.ticks if t[2] is not None and "elapsed" not in t[2]]
    assert [t[0] for t in completions] == [1, 2, 3]
    assert all(t[1] == 3 for t in completions)


async def test_heartbeat_ticks_while_a_slow_item_is_still_converting(monkeypatch) -> None:
    import refigure.mcp.server as server_module

    monkeypatch.setattr(server_module, "_HEARTBEAT_INTERVAL_S", 0.05)

    def _slow_convert(source, *, config):
        import time

        time.sleep(0.4)
        from refigure.api import ConversionResult

        return ConversionResult(markdown="slow but done")

    ctx = _build_ctx(batch_convert_fns={"docx": _slow_convert})
    items = [BatchItem(format="docx", content_base64=_b64_docx("x"))]
    rec = _RecordingCtx()

    result = await _run_convert_batch_tool(items, False, False, None, None, ctx, rec)  # type: ignore[arg-type]

    assert result.succeeded == 1
    heartbeat_ticks = [t for t in rec.ticks if t[2] is not None and "elapsed" in t[2]]
    assert len(heartbeat_ticks) >= 2, "batch-level heartbeat did not fire during a slow item"
    assert [t[0] for t in rec.ticks] == sorted(t[0] for t in rec.ticks), (
        "progress must strictly increase (or hold), per the MCP contract"
    )


# --- format unavailable per item (white-box, mirrors resource_uri test above) ---


async def test_format_with_no_registered_convert_fn_is_isolated_per_item() -> None:
    # batch_convert_fns deliberately omits "xlsx" — simulates a
    # refigure[mcp]+[docx]-only install without needing a real subprocess
    # (the extras-isolation matrix already covers that boundary).
    ctx = _build_ctx()
    items = [
        BatchItem(format="docx", content_base64=_b64_docx("fine")),
        BatchItem(format="xlsx", content_base64=_b64_xlsx()),
    ]
    rec = _RecordingCtx()

    result = await _run_convert_batch_tool(items, False, False, None, None, ctx, rec)  # type: ignore[arg-type]

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.items[1].status == "error"
    assert result.items[1].error is not None
    assert result.items[1].error.startswith("MissingOptionalDependencyError:")


# --- registration guard + unexpected-exception unwrap (coverage closers) ---


def test_register_convert_batch_registers_nothing_without_any_format() -> None:
    """Mirrors test_prompts.py's own direct has_docx=False/has_xlsx=False
    calls — a bare refigure[mcp]-only install (or a server built with
    neither [docx] nor [xlsx]) must not publish a tool that can never
    succeed for any item (phase-4 spec §4)."""
    ctx = _build_ctx(batch_convert_fns={})
    mcp = MCPServer("test")

    _register_convert_batch(mcp, ctx, "stdio", has_docx=False, has_xlsx=False)

    assert "convert_batch" not in mcp._tool_manager._tools  # type: ignore[attr-defined]


async def test_an_unexpected_exception_mid_batch_is_unwrapped_and_reraised() -> None:
    """The defensive except BaseException/_unwrap_task_group_exception
    branch in _run_convert_batch_tool — should never fire in practice
    (_run_batch_item catches everything itself), exercised here the same
    way test_bridge.py's own timeout test exercises _convert_with_bridge's
    identical branch: force a REAL exception to escape the task group,
    from report_progress itself (e.g. a broken session mid-batch), and
    confirm it surfaces as the real exception type, not a generic
    ExceptionGroup."""

    class _RaisingCtx:
        async def report_progress(self, progress, total=None, message=None) -> None:
            raise RuntimeError("session broke mid-batch")

    ctx = _build_ctx()
    items = [BatchItem(format="docx", content_base64=_b64_docx("x"))]

    with pytest.raises(RuntimeError, match="session broke mid-batch"):
        await _run_convert_batch_tool(items, False, False, None, None, ctx, _RaisingCtx())  # type: ignore[arg-type]
