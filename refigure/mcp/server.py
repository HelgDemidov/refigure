"""MCP server assembly: tool registration, the typed result schema, the
error-wrapper, and the sync-core async bridge + heartbeat. See
``docs/mcp-server/mcp-server-architecture/mcp-server-architecture.md``
§3/§6/§7/§7-bis for the full design this module implements, and
``docs/mcp-server/mcp-server-phase1-skeleton/
mcp-server-phase1-skeleton-2026-08-21.md`` for the phase-1 scope.

Format isolation extends here too, same discipline as ``refigure/cli.py``'s
own module docstring: this module never imports ``refigure.docx``/
``refigure.xlsx`` at module level — only lazily, inside each tool's own
registration function, so a ``refigure[mcp]``-only install (no ``[docx]``/
``[xlsx]``) still builds a server with 0 tools registered instead of
failing to import.
"""

from __future__ import annotations

import base64
import binascii
import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal, TypeVar

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations

from ..api import (
    Config,
    ConversionResult,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
    VlmClient,
    VlmMarkerLimitExceededError,
)
from ..cli import _VLM_XLSX_WARNING

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_HEARTBEAT_INTERVAL_S = 15

# The 4 refigure exception types a conversion can legitimately raise —
# reported to the caller as isError=True with a stable, parseable
# "ClassName: message" prefix, never a bare str(exc) — see
# _call_and_wrap_errors below.
_TYPED_EXCEPTIONS: tuple[type[Exception], ...] = (
    UnsupportedFormatError,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    VlmMarkerLimitExceededError,
)


@dataclass
class ConvertOutput:
    """Tool result schema for ``convert_docx``/``convert_xlsx`` — mirrors
    ``ConversionResult``'s richness, not a bare string. ``resource_uri`` is
    always ``None`` in this phase (the ``refigure://conversion/{id}``
    resource store lands in phase 2, chartered §4) — ``markdown`` is
    always the inline channel for now. Exactly one of the two is non-None
    once phase 2 ships; the SDK-derived schema cannot express that XOR
    itself (both fields are ``Optional``) — it's a runtime invariant,
    pinned by ``tests/unit/mcp/test_schema_pin.py``'s golden test, not by
    the schema."""

    markdown: str | None
    resource_uri: str | None
    warnings: list[str] = field(default_factory=list)
    charts_found: int = 0
    charts_rendered: int = 0
    groups_found: int = 0
    vlm_used: bool = False


@dataclass
class _ServerContext:
    """Per-process settings shared across every tool call — NOT the
    chartered ``ServerState`` (architecture doc §4: LRU resource-store +
    rate/soft-cap counters under one lock, phase 2). Phase 1 has none of
    that; this is just a plain settings container."""

    limiter: anyio.CapacityLimiter
    vlm_client: VlmClient | None
    vlm_api_key: str | None
    vlm_max_markers: int | None
    max_input_b64_mb: int
    timeout_s: float


async def _convert_with_bridge(
    convert_fn: Callable[..., ConversionResult],
    source: Path | bytes,
    config: Config,
    *,
    mcp_ctx: Context,
    limiter: anyio.CapacityLimiter,
    timeout_s: float,
) -> ConversionResult:
    """Sync-core -> async bridge: ``anyio.to_thread.run_sync`` bounded by
    ``limiter`` (``--mcp-max-concurrent-conversions``), plus an
    elapsed-time heartbeat (``ctx.report_progress``) in the SAME task
    group as the bridge call — not a bare fire-and-forget, which would
    race a heartbeat tick against the final response (architecture doc
    §7). ``abandon_on_cancel=True`` is required explicitly: the SDK
    default (``False``) makes a timeout a no-op — the calling coroutine
    would not regain control until the sync thread finishes on its own,
    confirmed experimentally against this exact ``mcp`` version during
    architecture review. Even abandoned, the orphaned thread keeps
    running in the background (also confirmed) — this hard timeout
    protects caller responsiveness and pool availability, never spend;
    spend is bounded only by ``Config.vlm_max_markers`` (see
    ``_run_convert_tool``)."""

    async def _heartbeat() -> None:
        elapsed = 0
        while True:
            await anyio.sleep(_HEARTBEAT_INTERVAL_S)
            elapsed += _HEARTBEAT_INTERVAL_S
            await mcp_ctx.report_progress(elapsed, message="still converting")

    try:
        with anyio.fail_after(timeout_s):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_heartbeat)
                result = await anyio.to_thread.run_sync(
                    functools.partial(convert_fn, source, config=config),
                    abandon_on_cancel=True,
                    limiter=limiter,
                )
                # Cancel the heartbeat BEFORE returning — the task group's
                # own __aexit__ (triggered by this return unwinding through
                # it) would otherwise wait forever for the heartbeat's
                # `while True` to finish on its own, which it never does.
                tg.cancel_scope.cancel()
                return result
    except BaseException as exc:  # noqa: BLE001 - re-raises the unwrapped cause, see _unwrap_task_group_exception
        raise _unwrap_task_group_exception(exc) from exc
    # Unreachable: the block above always either returns or raises. mypy's
    # control-flow analysis doesn't treat a `return` inside `async with
    # TaskGroup` as a guaranteed exit (reproduced in isolation against this
    # anyio version) — this satisfies it without a bare `assert` (ruff
    # S101 forbids that outside tests/).
    raise AssertionError("unreachable — _convert_with_bridge's with-block always returns or raises")


def _unwrap_task_group_exception(exc: BaseException) -> BaseException:
    """``anyio.create_task_group()`` wraps ANY exception exiting its
    ``async with`` block — host code or a child task — in an
    ``ExceptionGroup``/``BaseExceptionGroup``, even when there's exactly
    one real failure (confirmed live: a plain ``CorruptArchiveError``
    from the ``to_thread`` call arrives here wrapped). Unwrapped so
    ``_call_and_wrap_errors``'s typed-exception dispatch still sees the
    real refigure exception, not a generic group. Duck-typed via
    ``.exceptions`` (the public attribute every such group exposes)
    rather than an explicit ``ExceptionGroup`` import: the concrete class
    differs between the Python 3.11+ builtin and the ``exceptiongroup``
    backport ``anyio`` pulls in on 3.10 (this package's own floor,
    ``pyproject.toml``'s ``requires-python``), but both expose this
    attribute identically. Recurses for a nested single-exception group;
    a group with more than one exception (should not happen here — this
    task group only ever runs the heartbeat, which never raises anything
    but ``Cancelled``, alongside the one bridge call) is returned as-is,
    reported as an unexpected/internal error rather than guessed at."""
    exceptions = getattr(exc, "exceptions", None)
    if exceptions is not None and len(exceptions) == 1:
        return _unwrap_task_group_exception(exceptions[0])
    return exc


async def _run_convert_tool(
    convert_fn: Callable[..., ConversionResult],
    fmt: Literal["docx", "xlsx"],
    path: str | None,
    content_base64: str | None,
    use_vlm: bool,
    vlm_verify: bool,
    vlm_judge_mode: Literal["solo", "panel"] | None,
    vlm_model: str | None,
    server_ctx: _ServerContext,
    mcp_ctx: Context,
) -> ConvertOutput:
    """Shared body for both convert_* tools — a plain function, not a
    decorator wrapping convert_docx/convert_xlsx: each tool stays a thin,
    directly-signatured wrapper the SDK introspects for inputSchema
    without any risk of a decorator's ``functools.wraps`` not fully
    preserving that introspection (architecture doc §5/§6)."""
    if (path is None) == (content_base64 is None):
        raise ValueError("exactly one of path or content_base64 is required")

    warnings: list[str] = []
    if fmt == "xlsx" and use_vlm:
        warnings.append(_VLM_XLSX_WARNING)

    source: Path | bytes
    if path is not None:
        source = Path(path)
    elif content_base64 is not None:
        max_chars = server_ctx.max_input_b64_mb * 1024 * 1024
        if len(content_base64) > max_chars:
            raise ValueError(
                f"content_base64 is {len(content_base64)} chars, exceeding the "
                f"{server_ctx.max_input_b64_mb} MB cap — checked before decoding"
            )
        try:
            source = base64.b64decode(content_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
    else:  # pragma: no cover - unreachable, the XOR check above already excludes this
        raise ValueError("exactly one of path or content_base64 is required")

    kwargs: dict[str, Any] = {}
    if use_vlm:
        kwargs["use_vlm"] = True
    if vlm_verify:
        kwargs["vlm_verify"] = True
    if vlm_judge_mode is not None:
        kwargs["vlm_judge_mode"] = vlm_judge_mode
    if vlm_model is not None:
        kwargs["vlm_model"] = vlm_model
    if server_ctx.vlm_max_markers is not None:
        kwargs["vlm_max_markers"] = server_ctx.vlm_max_markers
    if server_ctx.vlm_client is not None:
        kwargs["vlm_client"] = server_ctx.vlm_client
    elif server_ctx.vlm_api_key is not None:
        # Only meaningful for the default OpenRouterClient path (vlm_client
        # unset) — a custom vlm_client (openai/anthropic direct) already
        # carries its own resolved credentials, see cli.py's
        # _resolve_vlm_client.
        kwargs["vlm_api_key"] = server_ctx.vlm_api_key
    config = Config(**kwargs)

    result = await _convert_with_bridge(
        convert_fn,
        source,
        config,
        mcp_ctx=mcp_ctx,
        limiter=server_ctx.limiter,
        timeout_s=server_ctx.timeout_s,
    )

    return ConvertOutput(
        markdown=result.markdown,
        resource_uri=None,
        warnings=[*warnings, *result.warnings],
        charts_found=result.charts_found,
        charts_rendered=result.charts_rendered,
        groups_found=result.groups_found,
        vlm_used=result.vlm_used,
    )


def _redact(text: str) -> str:
    """Best-effort secret redaction, reusing the existing
    ``vlm._redact_secrets`` regex rather than duplicating it (architecture
    doc §8). Lazy AND guarded: ``refigure.vlm`` requires ``[vlm]``
    (``pdfplumber``), which ``[mcp]`` alone does not pull in — a
    non-VLM failure on a ``refigure[mcp]``-only install must not itself
    crash while trying to redact a message that was never VLM-shaped to
    begin with."""
    try:
        from ..vlm import _redact_secrets
    except MissingOptionalDependencyError:
        return text
    return _redact_secrets(text)


async def _call_and_wrap_errors(
    coro: Coroutine[Any, Any, _T], *, transport: Literal["stdio", "http"]
) -> _T:
    """The one place every tool call's exceptions are classified and
    formatted — not a decorator (see ``_run_convert_tool``'s docstring).
    Refigure's typed exceptions become ``RuntimeError("ClassName:
    message")`` so a calling agent can branch on the class name, mirroring
    ``cli.py``'s ``_exit_code_for`` — ``MCPServer``'s own ``@mcp.tool()``
    wrapper catches a plain exception raised here and reports it as
    ``isError=True`` with the exception text as content (confirmed against
    this ``mcp`` version's docs), so raising is sufficient, no manual
    ``CallToolResult`` construction needed. ``transport`` is threaded
    through already, unused on the only transport this phase has
    (``"stdio"``) — phase 3 extends this, not rewrites it, per
    architecture doc §8."""
    try:
        return await coro
    except _TYPED_EXCEPTIONS as exc:
        message = _redact(str(exc)) if transport == "stdio" else "conversion failed"
        raise RuntimeError(f"{type(exc).__name__}: {message}") from exc
    except ValueError:
        # Input-validation failures from _run_convert_tool itself — already
        # a clean, stable message, nothing further to classify.
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected exceptions are the real-bug signal below
        logger.error("refigure-mcp: unexpected error in a tool call", exc_info=True)
        message = _redact(str(exc)) if transport == "stdio" else "internal error"
        raise RuntimeError(f"internal_error: {message}") from exc


def _register_convert_docx(
    mcp: MCPServer, server_ctx: _ServerContext, transport: Literal["stdio", "http"]
) -> None:
    try:
        from .. import docx as docx_module
    except MissingOptionalDependencyError:
        return

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def convert_docx(
        mcp_ctx: Context,
        path: str | None = None,
        content_base64: str | None = None,
        use_vlm: bool = False,
        vlm_verify: bool = False,
        vlm_judge_mode: Literal["solo", "panel"] | None = None,
        vlm_model: str | None = None,
    ) -> ConvertOutput:
        """Convert a DOCX file to Markdown — native OOXML chart data
        becomes mermaid diagrams, composite figures/groups become
        positioned zero-loss markers. Exactly one of path/content_base64
        is required. use_vlm additionally interprets composite figures
        via a cloud VLM call (prose + mermaid), off by default."""
        return await _call_and_wrap_errors(
            _run_convert_tool(
                docx_module.convert,
                "docx",
                path,
                content_base64,
                use_vlm,
                vlm_verify,
                vlm_judge_mode,
                vlm_model,
                server_ctx,
                mcp_ctx,
            ),
            transport=transport,
        )


def _register_convert_xlsx(
    mcp: MCPServer, server_ctx: _ServerContext, transport: Literal["stdio", "http"]
) -> None:
    try:
        from .. import xlsx as xlsx_module
    except MissingOptionalDependencyError:
        return

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def convert_xlsx(
        mcp_ctx: Context,
        path: str | None = None,
        content_base64: str | None = None,
        use_vlm: bool = False,
        vlm_verify: bool = False,
        vlm_judge_mode: Literal["solo", "panel"] | None = None,
        vlm_model: str | None = None,
    ) -> ConvertOutput:
        """Convert an XLSX file to Markdown — native chart data becomes
        mermaid diagrams. Exactly one of path/content_base64 is required.
        use_vlm has no effect on XLSX (no VLM path exists for it) — a
        warning is returned instead of a silent no-op."""
        return await _call_and_wrap_errors(
            _run_convert_tool(
                xlsx_module.convert,
                "xlsx",
                path,
                content_base64,
                use_vlm,
                vlm_verify,
                vlm_judge_mode,
                vlm_model,
                server_ctx,
                mcp_ctx,
            ),
            transport=transport,
        )


def build_server(
    *,
    transport: Literal["stdio", "http"] = "stdio",
    max_concurrent: int = 4,
    max_input_b64_mb: int = 100,
    vlm_max_markers: int | None = 200,
    timeout_s: float = 3600,
    vlm_client: VlmClient | None = None,
    vlm_api_key: str | None = None,
) -> MCPServer:
    """Assemble the ``refigure-mcp`` server: register ``convert_docx``/
    ``convert_xlsx`` (each conditionally, see ``_register_convert_docx``/
    ``_register_convert_xlsx``). Defaults here are the CANONICAL ones
    (architecture doc §8's reasoning for each number) — ``cli.py`` leaves
    its matching flags unset by default and only forwards a value when the
    operator actually passed one, the same optional-kwargs-dict discipline
    ``refigure.cli``'s own ``_build_config`` already uses for ``Config``,
    to avoid the two layers' defaults silently drifting apart."""
    mcp = MCPServer("refigure")
    server_ctx = _ServerContext(
        limiter=anyio.CapacityLimiter(max_concurrent),
        vlm_client=vlm_client,
        vlm_api_key=vlm_api_key,
        vlm_max_markers=vlm_max_markers,
        max_input_b64_mb=max_input_b64_mb,
        timeout_s=timeout_s,
    )
    _register_convert_docx(mcp, server_ctx, transport)
    _register_convert_xlsx(mcp, server_ctx, transport)
    return mcp
