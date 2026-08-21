"""MCP server assembly: tool registration, the typed result schema, the
error-wrapper, and the sync-core async bridge + heartbeat. See
``docs/mcp-server/mcp-server-architecture/mcp-server-architecture.md``
§3/§4/§5/§6/§7/§7-bis for the full design this module implements, and
``docs/mcp-server/mcp-server-phase1-skeleton/
mcp-server-phase1-skeleton-2026-08-21.md`` (phase 1) /
``docs/mcp-server/mcp-server-phase2-resources-prompts/
mcp-server-phase2-resources-prompts-2026-08-21.md`` (phase 2 — resources,
prompts, the shared VLM cache) for the phase scope this module actually
implements.

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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal, TypeVar, cast

import anyio
from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.types import (
    CallToolResult,
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceLink,
    ResourceTemplateReference,
    TextContent,
    ToolAnnotations,
)
from pydantic import AnyHttpUrl

from ..api import (
    Config,
    ConversionResult,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
    VlmCacheBackend,
    VlmClient,
    VlmMarkerLimitExceededError,
)
from ..cli import _VLM_XLSX_WARNING
from .auth import _StaticTokenVerifier
from .exceptions import RateLimitExceededError

# NOT `from ..vlm.cache import FileCacheBackend` at module level: refigure.vlm's
# own __init__.py guard requires [vlm] (pdfplumber), and importing ANY submodule
# of a guarded package always runs its __init__.py first — the exact
# extras-isolation bug class this project has hit 3 times before (package
# NESTING, PR #11's docx_groups.py incident). FileCacheBackend itself needs no
# heavy dependency, but its container package does, so this module (which must
# keep working with only [mcp] installed, no [docx]/[xlsx]/[vlm]) imports it
# lazily, only inside build_server(), only when --mcp-vlm-cache is actually
# passed.
from .state import ServerState, resolve_caller_id
from .vlm_cache import BoundedLruVlmCache, acquire_vlm_cache_file_lock

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_HEARTBEAT_INTERVAL_S = 15

# The refigure/refigure-mcp exception types a conversion can legitimately
# raise — reported to the caller as isError=True with a stable, parseable
# "ClassName: message" prefix, never a bare str(exc) — see
# _call_and_wrap_errors below. RateLimitExceededError (phase 3) is the
# first MCP-LOCAL member of this tuple — everything else comes from
# refigure.api, this one from refigure.mcp.exceptions (see that module's
# docstring for why it isn't in api.py) — the dispatch in
# _call_and_wrap_errors doesn't care which package a type comes from, only
# that it's listed here.
_TYPED_EXCEPTIONS: tuple[type[Exception], ...] = (
    UnsupportedFormatError,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    VlmMarkerLimitExceededError,
    RateLimitExceededError,
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
    """Per-process settings shared across every tool call. ``state`` IS
    the chartered ``ServerState`` (architecture doc §4: LRU resource-store
    + eventually rate/soft-cap counters under one lock — phase 2 only
    adds the LRU-store half, soft-cap/rate-limit stay phase 3, needing a
    token file and caller_id diversity that don't exist yet). ``vlm_cache``
    is the ONE ``VlmCacheBackend`` instance shared by every ``convert_docx``
    call for the server's lifetime (architecture doc §7-bis) — deliberately
    outside ``state``, since it's written from worker threads, not the
    event loop (see ``state.py``'s own module docstring)."""

    limiter: anyio.CapacityLimiter
    vlm_client: VlmClient | None
    vlm_api_key: str | None
    vlm_max_markers: int | None
    max_input_b64_mb: int
    timeout_s: float
    state: ServerState
    vlm_cache: VlmCacheBackend
    resource_inline_threshold_bytes: int
    transport: Literal["stdio", "http"]
    """Same value ``build_server(transport=...)`` was called with — a
    second copy of what ``_register_convert_docx``/``_register_convert_xlsx``
    already receive as their own function parameter (used there for
    ``_call_and_wrap_errors``'s redaction level), both sourced from the
    one ``transport`` argument to ``build_server()`` (no drift risk).
    ``_run_convert_tool`` needs its own copy for admission checks
    (phase-3 spec §5) that must run BEFORE ``_call_and_wrap_errors`` ever
    sees the call — a bare parameter, not a second constructor argument,
    since every other transport-dependent decision already flows through
    this dataclass."""


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
    raise AssertionError(  # pragma: no cover - provably unreachable, see the comment above
        "unreachable — _convert_with_bridge's with-block always returns or raises"
    )


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
) -> ConvertOutput | CallToolResult:
    """Shared body for both convert_* tools — a plain function, not a
    decorator wrapping convert_docx/convert_xlsx: each tool stays a thin,
    directly-signatured wrapper the SDK introspects for inputSchema
    without any risk of a decorator's ``functools.wraps`` not fully
    preserving that introspection (architecture doc §5/§6).

    Returns a real ``CallToolResult`` (not ``ConvertOutput``) when the
    markdown is too large to inline (``resource_uri`` branch, architecture
    doc §3/§4) — this function's OWN signature is honest about that; the
    outer ``convert_docx``/``convert_xlsx`` (annotated ``-> ConvertOutput``
    for the SDK's schema derivation) each ``cast()`` this at their single
    ``return`` site, not here — see their docstrings for why a WIDENED
    Union annotation there is impossible, not just undesirable."""
    if (path is None) == (content_base64 is None):
        raise ValueError("exactly one of path or content_base64 is required")
    if server_ctx.transport == "http" and path is not None:
        # Considered-and-rejected, not deferred (phase-3 spec §5): a
        # realpath+commonpath check against an allowed-root would preserve
        # remote filesystem access but adds permanent surface (a new flag,
        # symlink/traversal edge cases, dedicated tests) for convenience
        # the self-hosted deployment model doesn't need — content_base64
        # already covers remote input. Same principle as
        # core.zipsafe.safe_read(): remove the vulnerability class outright
        # rather than mitigate it.
        raise ValueError(
            "path is not accepted over HTTP — use content_base64, or run over "
            "stdio for local filesystem access"
        )

    if server_ctx.transport == "http":
        # Rate-limit is HTTP-always (architecture doc §6 п.3), including a
        # deployment with only one configured token — unlike soft-cap, it
        # needs no caller_id diversity to matter (it protects the
        # operator's own resources/spend from a runaway or leaked token,
        # not inter-caller fairness). Checked here, before any input is
        # even decoded, so a rejected call never reaches the (expensive)
        # bridge — admission, not a post-hoc check. stdio skips this
        # block entirely, not just harmlessly always-True: resolve_caller_id()
        # is never called for it either.
        caller_id = resolve_caller_id()
        if not server_ctx.state.check_and_consume_rate_limit(caller_id):
            raise RateLimitExceededError(
                f"caller_id {caller_id!r} exceeded its conversion rate limit"
            )

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

    kwargs: dict[str, Any] = {"vlm_cache": server_ctx.vlm_cache}
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

    markdown_bytes = len(result.markdown.encode("utf-8"))
    if markdown_bytes <= server_ctx.resource_inline_threshold_bytes:
        return ConvertOutput(
            markdown=result.markdown,
            resource_uri=None,
            warnings=[*warnings, *result.warnings],
            charts_found=result.charts_found,
            charts_rendered=result.charts_rendered,
            groups_found=result.groups_found,
            vlm_used=result.vlm_used,
        )

    # Too large to inline: store the markdown in ServerState, return a
    # ResourceLink instead — architecture doc §3/§4. caller_id resolved
    # HERE, after the async bridge already returned (on the event loop,
    # never inside the to_thread worker) — see state.py's own docstring
    # for why this ordering is safe (get_access_token() is stable across
    # an internal await within one request coroutine, confirmed live).
    caller_id = resolve_caller_id()
    conversion_id = server_ctx.state.insert(caller_id, result.markdown)
    resource_uri = f"refigure://conversion/{conversion_id}"
    output = ConvertOutput(
        markdown=None,
        resource_uri=resource_uri,
        warnings=[*warnings, *result.warnings],
        charts_found=result.charts_found,
        charts_rendered=result.charts_rendered,
        groups_found=result.groups_found,
        vlm_used=result.vlm_used,
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Markdown is {markdown_bytes} bytes, over the "
                    f"{server_ctx.resource_inline_threshold_bytes} byte inline cap — "
                    "see resource_uri."
                ),
            ),
            ResourceLink(
                name=f"conversion-{conversion_id}",
                uri=resource_uri,
                description="Full converted markdown",
                mime_type="text/markdown",
            ),
        ],
        structured_content=asdict(output),
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
    except MissingOptionalDependencyError:  # pragma: no cover - see extras-isolation mcp leg
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
) -> bool:
    """Registers convert_docx and returns True, or returns False without
    registering anything if [docx] isn't installed — build_server() needs
    to know which tools actually registered to make _register_prompts
    (below) capability-aware, not just silently skip a tool."""
    try:
        from .. import docx as docx_module
    except MissingOptionalDependencyError:  # pragma: no cover - see extras-isolation mcp leg
        return False

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
        via a cloud VLM call (prose + mermaid), off by default. A result
        too large to inline returns resource_uri instead of markdown —
        read it via the refigure://conversion/{id} resource."""
        # cast: _run_convert_tool's real return type is ConvertOutput |
        # CallToolResult (see its docstring) — the SDK's own ToolManager
        # special-cases an actual CallToolResult regardless of a tool
        # function's declared annotation (verified live), but REJECTS a
        # widened `-> ConvertOutput | CallToolResult` signature outright
        # at registration time ("CallToolResult cannot be used in Union
        # or Optional types") — this stays `-> ConvertOutput` and the
        # cast is the one place that honestly acknowledges the mismatch.
        return cast(
            ConvertOutput,
            await _call_and_wrap_errors(
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
            ),
        )

    return True


def _register_convert_xlsx(
    mcp: MCPServer, server_ctx: _ServerContext, transport: Literal["stdio", "http"]
) -> bool:
    """See _register_convert_docx's docstring — same reasoning, xlsx side."""
    try:
        from .. import xlsx as xlsx_module
    except MissingOptionalDependencyError:  # pragma: no cover - see extras-isolation mcp leg
        return False

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
        warning is returned instead of a silent no-op. A result too large
        to inline returns resource_uri instead of markdown — read it via
        the refigure://conversion/{id} resource."""
        # cast: see convert_docx's identical comment above.
        return cast(
            ConvertOutput,
            await _call_and_wrap_errors(
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
            ),
        )

    return True


def _register_conversion_resource(mcp: MCPServer, server_ctx: _ServerContext) -> None:
    """``refigure://conversion/{id}`` — the resource behind the
    ``resource_uri`` branch above. Registered unconditionally (unlike
    ``convert_docx``/``convert_xlsx``): it has no format-specific
    dependency, and a bare ``refigure[mcp]`` install still benefits from
    being able to read back anything a caller inserted (there just won't
    be a convert tool to have produced an entry in the first place).

    ``async def``, not sync ``def``: keeps the O(1) LRU lookup directly
    on the event loop, matching architecture doc §4's "mutations only
    from the event loop" invariant, without an unneeded ``to_thread`` hop
    for what's fast, pure, in-memory work.

    A template resource (``{id}`` in the URI) is NEVER listed by
    ``resources/list`` — confirmed live against ``mcp==2.0.0``: a
    registered ``{id}``-template produces an empty ``resources/list``
    regardless of how many entries ``ServerState`` holds. "Unlisted" per
    architecture doc §4 falls out of this mechanism for free, not a
    separate filter this function has to implement.

    ``ResourceNotFoundError`` (not a bespoke exception): the SDK's own
    resource-template handler collapses ANY other exception — a bare
    ``ValueError``, a custom class — into a generic ``ResourceError``
    with no real message reaching the client (confirmed live: a custom
    exception's own text never made it past
    ``mcp/server/mcpserver/resources/templates.py``'s
    ``except Exception`` branch). ``ResourceNotFoundError`` is the one
    type that branch passes through untouched, with the SEP-2164
    ``-32602`` code and the real message intact — exactly what
    architecture doc §4's "an explicit not-found error, not a generic
    exception" asks for. No separate try/except for a genuinely
    unexpected failure here: ``ServerState.get()`` is pure in-memory
    work with no I/O, realistically incapable of raising anything else,
    and the SDK already degrades a truly unexpected exception safely on
    its own (the same collapsing behavior just described)."""

    @mcp.resource("refigure://conversion/{id}")
    async def read_conversion(id: str) -> str:
        caller_id = resolve_caller_id()
        markdown = server_ctx.state.get(id, caller_id)
        if markdown is None:
            raise ResourceNotFoundError(f"conversion result not found: {id}")
        return markdown


def _register_prompts(mcp: MCPServer, *, has_docx: bool, has_xlsx: bool) -> None:
    """Two prompts (architecture doc §5), both capability-aware: neither
    recommends a tool/argument this particular server didn't actually
    register (``has_docx``/``has_xlsx`` come straight from
    ``_register_convert_docx``/``_register_convert_xlsx``'s own return
    value, not a second guess at what's installed).

    Prompt arguments are always plain strings on the wire — the MCP
    protocol's own ``PromptArgument`` carries no type beyond
    name/title/description/required (checked directly against
    ``mcp.types.PromptArgument.model_fields``) — so both functions parse
    their own ``bool``-shaped inputs from ``str`` rather than relying on
    any implicit coercion."""

    @mcp.prompt()
    def ingest_for_rag(
        document_format: str, use_vlm: str = "false", vlm_judge_mode: str = ""
    ) -> str:
        """Which convert_* tool and VLM settings fit a RAG-ingestion goal,
        given a document format and whether its figures should be
        VLM-interpreted. use_vlm/vlm_judge_mode mirror convert_docx's own
        tool arguments by name (architecture doc §5) — vlm_judge_mode is
        completable, dependent on use_vlm (see _register_completion)."""
        fmt = document_format.strip().lower()
        if fmt not in ("docx", "xlsx"):
            return f"Unrecognized document_format {document_format!r} — expected 'docx' or 'xlsx'."
        if fmt == "docx" and not has_docx:
            return "This server has no [docx] extra installed — convert_docx is not available here."
        if fmt == "xlsx" and not has_xlsx:
            return "This server has no [xlsx] extra installed — convert_xlsx is not available here."
        if fmt == "xlsx":
            return (
                "Use convert_xlsx(path=...) — native chart data becomes mermaid diagrams "
                "automatically. use_vlm has no effect on xlsx (no VLM path exists for it)."
            )
        wants_vlm = use_vlm.strip().lower() == "true"
        if not wants_vlm:
            return (
                "Use convert_docx(path=...) — native chart data becomes mermaid diagrams "
                "automatically; composite figures stay as zero-loss 'not analyzed' markers "
                "unless you also pass use_vlm=True."
            )
        judge_mode = vlm_judge_mode.strip().lower()
        judge_clause = f", vlm_judge_mode={judge_mode!r}" if judge_mode in ("solo", "panel") else ""
        return (
            f"Use convert_docx(path=..., use_vlm=True, vlm_verify=True{judge_clause}) — "
            "native chart data becomes mermaid diagrams automatically, and composite "
            "figures/groups additionally get a cloud VLM interpretation, judged for "
            "defects before you rely on it (panel mode is the higher-recall default "
            "if vlm_judge_mode is left unset)."
        )

    @mcp.prompt()
    def explain_conversion_warnings(warnings: str) -> str:
        """Ask the model to explain a ConversionResult.warnings list (one
        warning per line) in plain, non-technical language."""
        items = [w for w in warnings.split("\n") if w.strip()]
        if not items:
            return "No warnings to explain — the conversion reported none."
        bullet_list = "\n".join(f"- {w}" for w in items)
        return (
            "Explain each of the following refigure conversion warnings in plain, "
            f"non-technical language:\n{bullet_list}"
        )


def _register_completion(mcp: MCPServer) -> None:
    """A single, server-wide ``@mcp.completion()`` dispatcher — NOT one
    hook per prompt argument. ``completable()``, the mechanism the
    architecture doc originally assumed (a per-argument annotation
    wrapper), does not exist anywhere in ``mcp==2.0.0`` — confirmed live
    during the tech-spec pass; the real API is this single handler,
    receiving ``(ref, argument, context)`` and expected to branch
    internally on which prompt/resource-template and which argument is
    being completed.

    Only handles ``ingest_for_rag``'s ``vlm_judge_mode`` argument,
    dependent on ``use_vlm`` already being resolved — architecture doc
    §5's literal target (not the ``document_format``/
    ``needs_figure_interpretation`` pair an earlier draft of this phase
    used instead, a real deviation from the charter caught on review:
    ``vlm_judge_mode`` is meaningless when ``use_vlm`` isn't even
    ``"true"``, so completion offers nothing in that case rather than a
    misleading solo/panel choice). Every other ``ref``/``argument``
    combination returns ``None`` — confirmed against the SDK's own
    docstring: the client falls back to offering no completions, not an
    error."""

    # MCPServer.completion() itself carries no type annotations at all in
    # mcp==2.0.0 (unlike .tool()/.resource()/.prompt()) — a genuine SDK stub
    # gap, not something fixable here.
    @mcp.completion()  # type: ignore[no-untyped-call, untyped-decorator]
    async def handle_completion(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion | None:
        if (
            not isinstance(ref, PromptReference)
            or ref.name != "ingest_for_rag"
            or argument.name != "vlm_judge_mode"
        ):
            return None
        prior = (context.arguments if context is not None else None) or {}
        if prior.get("use_vlm", "").strip().lower() == "true":
            return Completion(values=["solo", "panel"])
        return Completion(values=[])


def build_server(
    *,
    transport: Literal["stdio", "http"] = "stdio",
    max_concurrent: int = 4,
    max_input_b64_mb: int = 100,
    vlm_max_markers: int | None = 200,
    timeout_s: float = 3600,
    vlm_client: VlmClient | None = None,
    vlm_api_key: str | None = None,
    resource_inline_threshold_bytes: int = 256 * 1024,
    resource_max_entries: int = 200,
    resource_max_bytes: int = 500 * 1024 * 1024,
    resource_ttl_s: float = 3600,
    vlm_cache_path: Path | None = None,
    rate_limit_count: int = 30,
    rate_limit_window_s: float = 60.0,
    token_map: dict[str, str] | None = None,
) -> MCPServer:
    """Assemble the ``refigure-mcp`` server: register ``convert_docx``/
    ``convert_xlsx`` (each conditionally, see ``_register_convert_docx``/
    ``_register_convert_xlsx``). Defaults here are the CANONICAL ones
    (architecture doc §8's reasoning for each number) — ``cli.py`` leaves
    its matching flags unset by default and only forwards a value when the
    operator actually passed one, the same optional-kwargs-dict discipline
    ``refigure.cli``'s own ``_build_config`` already uses for ``Config``,
    to avoid the two layers' defaults silently drifting apart.

    ``vlm_cache_path is None`` (default): the shared cache is a
    ``BoundedLruVlmCache`` (2000 entries or 100 MB, whichever hits first —
    ``vlm/__init__.py``'s own ``_MAX_VLM_RESPONSE_CHARS=50_000`` bounds a
    single entry to roughly 50 KB, so 2000 entries stays well inside
    100 MB even at that ceiling). Passing a path switches to
    ``FileCacheBackend`` as-is (dev/small-corpus persistence, NOT a
    memory-safe alternative — it keeps its whole cache in RAM and
    rewrites the entire file on every ``set()``, see its own docstring)
    and guards it with ``acquire_vlm_cache_file_lock`` against a second
    instance sharing the same path, which loses writes.

    ``rate_limit_count``/``rate_limit_window_s`` (phase-3 spec §4): fixed
    per-``caller_id`` window, HTTP-always regardless of how many callers
    are configured (architecture doc §6 п.3 — a single-token deployment
    still gets this, unlike soft-cap below, which needs ≥2). Defaults (30
    per 60s) are a reasoned choice for a single-operator self-hosted
    deployment, not a chartered number — same status as
    ``resource_inline_threshold_bytes``'s 256 KB default (phase 2).
    ``_run_convert_tool`` only ever checks this when ``transport=="http"``
    (stdio skips the check entirely, not just never hits the limit).

    ``token_map`` (``{token: caller_id}``, from ``auth.load_token_file()``
    — phase-3 spec §2/§6): ``None`` (default, stdio's only valid value —
    ``cli.py`` fail-fasts before ever calling this with a non-``None``
    map on stdio) builds a plain ``MCPServer("refigure")`` exactly as
    phases 1-2 did. A real map builds
    ``MCPServer("refigure", token_verifier=_StaticTokenVerifier(token_map),
    auth=AuthSettings(issuer_url=..., resource_server_url=...))`` — both
    URLs are fixed, meaningless placeholders (``http://localhost/``), not
    a real authorization server: live-verified against ``mcp==2.0.0``
    (this spec's own §0) that ``auth_server_provider=None`` (never set
    here) means the SDK never mounts the OAuth grant-flow endpoints that
    would make those URLs matter — they exist purely to satisfy
    ``AuthSettings``' own required-field validation for a
    resource-server-only (bearer-verification-only) setup. Soft-cap
    (``ServerState.__init__``'s ``soft_cap_enabled``) is derived HERE from
    the same map — ``len(set(token_map.values())) >= 2`` — the one and
    only place that count is computed (architecture doc §4: active only
    at ≥2 distinct configured ``caller_id``s).

    Can raise ``MissingOptionalDependencyError`` (``vlm_cache_path`` set
    without ``[vlm]`` installed — ``FileCacheBackend`` lives inside the
    ``refigure.vlm`` package, guarded the same as everything else in it)
    or ``ValueError`` (``vlm_cache_path`` already locked by another
    instance) — callers constructing a real server from user-supplied
    config (``cli.py``) must route this through the same
    exception-to-exit-code boundary every other optional-dependency/
    external-resource construction in this codebase already uses
    (CLAUDE.md's "Do NOT" list)."""
    vlm_cache: VlmCacheBackend
    if vlm_cache_path is not None:
        from ..vlm.cache import FileCacheBackend  # lazy — see this module's import block

        acquire_vlm_cache_file_lock(vlm_cache_path)
        vlm_cache = FileCacheBackend(vlm_cache_path)
    else:
        vlm_cache = BoundedLruVlmCache(max_entries=2000, max_bytes=100 * 1024 * 1024)

    soft_cap_enabled = token_map is not None and len(set(token_map.values())) >= 2
    state = ServerState(
        max_entries=resource_max_entries,
        max_bytes=resource_max_bytes,
        ttl_s=resource_ttl_s,
        rate_limit_count=rate_limit_count,
        rate_limit_window_s=rate_limit_window_s,
        soft_cap_enabled=soft_cap_enabled,
    )

    if token_map is not None:
        mcp = MCPServer(
            "refigure",
            token_verifier=_StaticTokenVerifier(token_map),
            auth=AuthSettings(
                issuer_url=AnyHttpUrl("http://localhost/"),
                resource_server_url=AnyHttpUrl("http://localhost/"),
            ),
        )
    else:
        mcp = MCPServer("refigure")
    server_ctx = _ServerContext(
        limiter=anyio.CapacityLimiter(max_concurrent),
        vlm_client=vlm_client,
        vlm_api_key=vlm_api_key,
        vlm_max_markers=vlm_max_markers,
        max_input_b64_mb=max_input_b64_mb,
        timeout_s=timeout_s,
        state=state,
        vlm_cache=vlm_cache,
        resource_inline_threshold_bytes=resource_inline_threshold_bytes,
        transport=transport,
    )
    has_docx = _register_convert_docx(mcp, server_ctx, transport)
    has_xlsx = _register_convert_xlsx(mcp, server_ctx, transport)
    _register_conversion_resource(mcp, server_ctx)
    _register_prompts(mcp, has_docx=has_docx, has_xlsx=has_xlsx)
    _register_completion(mcp)
    return mcp
