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

# build_server()'s own canonical default for max_input_b64_mb — a module
# constant, not just an inline literal in the signature below, because
# cli.py (phase-3 spec §7) needs the EFFECTIVE value (flag-provided or
# this default) to size --transport http's max_request_body_size, and
# duplicating "100" as a second literal there would be exactly the
# default-drift this module's own docstring already warns against for
# Config/build_server's kwargs-dict discipline.
DEFAULT_MAX_INPUT_B64_MB = 100

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
class BatchItem:
    """One element of a ``convert_batch`` call (phase-4 spec §1). Exactly
    one of ``path``/``content_base64`` is required — validated up front
    for the WHOLE batch, atomically, before any item runs (see
    ``_run_convert_batch_tool``), the same "reject the whole call on a
    structural shape problem" treatment ``_run_convert_tool``'s own XOR
    check already gives a single-item call. ``path`` is rejected outright
    over HTTP for the same reason it is on ``convert_docx``/
    ``convert_xlsx`` (architecture doc §6.2)."""

    format: Literal["docx", "xlsx"]
    path: str | None = None
    content_base64: str | None = None


@dataclass
class BatchItemOutput:
    """One ``convert_batch`` result element — carries the FULL
    ``ConvertOutput`` shape (not just markdown) on ``status="ok"``, or a
    single ``error`` string (the identical ``"ClassName: message"``/
    ``"internal_error: message"`` text ``_classify_exception`` produces
    for the single-item tools) on ``status="error"``. Per-item isolation
    (phase-4 spec §3): one bad element never prevents the others from
    reporting a normal ``status="ok"`` result in the same call."""

    status: Literal["ok", "error"]
    markdown: str | None = None
    resource_uri: str | None = None
    warnings: list[str] = field(default_factory=list)
    charts_found: int = 0
    charts_rendered: int = 0
    groups_found: int = 0
    vlm_used: bool = False
    error: str | None = None


@dataclass
class BatchOutput:
    """``convert_batch``'s own result: every item's outcome plus
    aggregate counts, mirroring ``refigure`` CLI's own batch-mode summary
    line (``cli.py``'s ``_run_batch``: "N/M converted, K failed")."""

    items: list[BatchItemOutput]
    total: int
    succeeded: int
    failed: int


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
    max_batch_size: int
    batch_convert_fns: dict[str, Callable[..., ConversionResult]]
    """Populated by ``_register_convert_batch`` from its own guarded
    per-format imports (phase-4 spec §4) — ``{"docx": docx.convert}``
    and/or ``{"xlsx": xlsx.convert}``, whichever extras are actually
    installed. ``_run_batch_item`` looks a ``BatchItem.format`` up here
    rather than importing ``refigure.docx``/``refigure.xlsx`` itself —
    same lazy-import discipline as ``_register_convert_docx``/
    ``_register_convert_xlsx``, centralized once per server instead of
    re-guarded per item."""
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


async def _bridge_only(
    convert_fn: Callable[..., ConversionResult],
    source: Path | bytes,
    config: Config,
    *,
    limiter: anyio.CapacityLimiter,
    timeout_s: float,
) -> ConversionResult:
    """The raw sync-core -> async bridge: a hard per-call timeout
    (``anyio.fail_after``) around ``anyio.to_thread.run_sync`` (bounded by
    ``limiter``, ``abandon_on_cancel=True`` for the reason
    ``_convert_with_bridge``'s own docstring below explains). NO task
    group and NO heartbeat here, unlike ``_convert_with_bridge`` — exists
    for phase 4's ``convert_batch`` (``docs/mcp-server/
    mcp-server-phase4-batch-progress/mcp-server-phase4-batch-progress-
    2026-08-21.md`` §2/§3): N of these run as independent tasks inside
    ONE shared outer task group (``_run_batch_item``/``convert_batch``'s
    own registration), each needing to raise its OWN plain exception
    (``CorruptArchiveError``, ``TimeoutError``, ...) for per-item
    classification — an ``ExceptionGroup`` wrapper only ever comes from a
    task group's own ``async with`` block, which this function
    deliberately has none of, so no unwrap is needed at its call sites.
    ``_convert_with_bridge`` is this function plus a heartbeat task group,
    for the single-item tools that still want one."""
    with anyio.fail_after(timeout_s):
        return await anyio.to_thread.run_sync(
            functools.partial(convert_fn, source, config=config),
            abandon_on_cancel=True,
            limiter=limiter,
        )


async def _convert_with_bridge(
    convert_fn: Callable[..., ConversionResult],
    source: Path | bytes,
    config: Config,
    *,
    mcp_ctx: Context,
    limiter: anyio.CapacityLimiter,
    timeout_s: float,
) -> ConversionResult:
    """Sync-core -> async bridge: ``_bridge_only`` (above) inside a task
    group that also runs an elapsed-time heartbeat (``ctx.report_progress``)
    — not a bare fire-and-forget, which would race a heartbeat tick
    against the final response (architecture doc §7). ``abandon_on_cancel=
    True`` is required explicitly: the SDK default (``False``) makes a
    timeout a no-op — the calling coroutine would not regain control
    until the sync thread finishes on its own, confirmed experimentally
    against this exact ``mcp`` version during architecture review. Even
    abandoned, the orphaned thread keeps running in the background (also
    confirmed) — this hard timeout protects caller responsiveness and
    pool availability, never spend; spend is bounded only by
    ``Config.vlm_max_markers`` (see ``_run_convert_tool``).

    A timeout or a conversion failure raised by ``_bridge_only`` propagates
    as a plain exception out of this function's ``async with`` body (host
    code raising inside a task group's block, not a child task) — anyio's
    own ``__aexit__`` reacts exactly as it would to a child task's
    failure: it cancels the sibling heartbeat task, THEN wraps the single
    exception in an ``ExceptionGroup``, which ``_unwrap_task_group_exception``
    below undoes. This is mechanically different from the pre-phase-4
    version (where ``anyio.fail_after`` wrapped the whole task group
    directly and cancelled it itself on timeout) but produces the
    identical observable outcome — verified unchanged by this file's own
    ``test_bridge.py`` timeout/heartbeat tests after this refactor, not
    just reasoned about."""

    async def _heartbeat() -> None:
        elapsed = 0
        while True:
            await anyio.sleep(_HEARTBEAT_INTERVAL_S)
            elapsed += _HEARTBEAT_INTERVAL_S
            await mcp_ctx.report_progress(elapsed, message="still converting")

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_heartbeat)
            result = await _bridge_only(
                convert_fn, source, config, limiter=limiter, timeout_s=timeout_s
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


def _decode_source(
    path: str | None, content_base64: str | None, server_ctx: _ServerContext
) -> Path | bytes:
    """Turn a (caller-validated-exactly-one-of) ``path``/``content_base64``
    pair into the core's own ``Path | bytes`` union. Assumes the caller
    already confirmed exactly one of the two is set and that ``path``
    isn't being used over HTTP — ``_run_convert_tool``'s own XOR check, or
    ``_run_convert_batch_tool``'s upfront per-item shape validation
    (phase-4 spec §4) — this function only handles the DATA-quality
    failure that's still possible even for a well-formed item:
    ``content_base64`` present but not actually valid base64, or over the
    configured size cap. Raises ``ValueError`` for either — the
    per-item-isolatable class of failure for ``convert_batch`` (phase-4
    spec §3), unlike the two structural checks above it."""
    if path is not None:
        return Path(path)
    if content_base64 is None:  # pragma: no cover - callers' own XOR check already excludes this
        raise ValueError("exactly one of path or content_base64 is required")
    max_chars = server_ctx.max_input_b64_mb * 1024 * 1024
    if len(content_base64) > max_chars:
        raise ValueError(
            f"content_base64 is {len(content_base64)} chars, exceeding the "
            f"{server_ctx.max_input_b64_mb} MB cap — checked before decoding"
        )
    try:
        return base64.b64decode(content_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"content_base64 is not valid base64: {exc}") from exc


def _build_config(
    use_vlm: bool,
    vlm_verify: bool,
    vlm_judge_mode: Literal["solo", "panel"] | None,
    vlm_model: str | None,
    server_ctx: _ServerContext,
) -> Config:
    """Build a per-call ``Config`` from a tool call's VLM-parameter
    arguments plus the server-wide fixed settings (provider/key/cache/
    ceiling — architecture doc §3: "never per-call"). Shared by
    ``_run_convert_tool`` (single-item tools) and ``_run_batch_item``
    (phase 4) — ``convert_batch`` applies ONE policy per item within a
    batch call (phase-4 spec §1: a single shared ``use_vlm``/
    ``vlm_verify``/``vlm_judge_mode``/``vlm_model`` for the whole batch,
    not a per-item schema), so this function is called once per item with
    the same four arguments the whole ``convert_batch`` call received."""
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
    return Config(**kwargs)


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

    source = _decode_source(path, content_base64, server_ctx)
    config = _build_config(use_vlm, vlm_verify, vlm_judge_mode, vlm_model, server_ctx)

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


def _classify_exception(exc: Exception, *, transport: Literal["stdio", "http"]) -> str:
    """The one place an exception becomes a stable, transport-redacted
    string — shared by ``_call_and_wrap_errors`` (below, which wraps it in
    ``RuntimeError`` for a single-item tool call) and phase 4's
    ``_run_batch_item`` (which stores it directly as
    ``BatchItemOutput.error``, never raising it at all — per-item
    isolation, ``docs/mcp-server/mcp-server-phase4-batch-progress/
    mcp-server-phase4-batch-progress-2026-08-21.md`` §2/§3). A bare
    ``ValueError`` (input-validation failures) is NOT routed through this
    function by ``_call_and_wrap_errors`` — its own ``str(exc)`` is
    already a clean, stable message needing no ``ClassName`` prefix, so
    that caller still special-cases it before ever reaching here.
    ``_run_batch_item`` DOES route a per-item ``ValueError`` here (e.g. a
    malformed ``content_base64`` on just one batch element) — this
    function's own ``isinstance(exc, ValueError)`` branch reproduces the
    identical bare-message treatment for that caller, so both paths agree
    on what a ``ValueError``'s text looks like even though only one of
    them special-cases it at its own call site."""
    if isinstance(exc, _TYPED_EXCEPTIONS):
        message = _redact(str(exc)) if transport == "stdio" else "conversion failed"
        return f"{type(exc).__name__}: {message}"
    if isinstance(exc, ValueError):
        return str(exc)
    logger.error("refigure-mcp: unexpected error in a tool call", exc_info=True)
    message = _redact(str(exc)) if transport == "stdio" else "internal error"
    return f"internal_error: {message}"


async def _call_and_wrap_errors(
    coro: Coroutine[Any, Any, _T], *, transport: Literal["stdio", "http"]
) -> _T:
    """The one place every single-item tool call's exceptions are
    classified and formatted — not a decorator (see
    ``_run_convert_tool``'s docstring). Refigure's typed exceptions become
    ``RuntimeError("ClassName: message")`` so a calling agent can branch
    on the class name, mirroring ``cli.py``'s ``_exit_code_for`` —
    ``MCPServer``'s own ``@mcp.tool()`` wrapper catches a plain exception
    raised here and reports it as ``isError=True`` with the exception text
    as content (confirmed against this ``mcp`` version's docs), so raising
    is sufficient, no manual ``CallToolResult`` construction needed.
    ``transport`` is threaded through already, unused on the only
    transport phase 1 had (``"stdio"``) — phase 3 extended this for HTTP,
    phase 4 extends it a third time for ``convert_batch`` (via
    ``_classify_exception``, shared below), never rewriting it, per
    architecture doc §8."""
    try:
        return await coro
    except ValueError:
        # Input-validation failures from _run_convert_tool itself — already
        # a clean, stable message (see _classify_exception's own
        # docstring for why this case is excluded from it here), nothing
        # further to classify.
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected exceptions are the real-bug signal, see _classify_exception
        raise RuntimeError(_classify_exception(exc, transport=transport)) from exc


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


async def _run_batch_item(
    item: BatchItem,
    use_vlm: bool,
    vlm_verify: bool,
    vlm_judge_mode: Literal["solo", "panel"] | None,
    vlm_model: str | None,
    server_ctx: _ServerContext,
) -> BatchItemOutput:
    """One element of a ``convert_batch`` call — fully isolated: ANY
    failure here becomes a ``status="error"`` ``BatchItemOutput``, never
    an exception escaping to the caller (phase-4 spec §3). Assumes
    ``_run_convert_batch_tool`` already validated the WHOLE batch's shape
    upfront (every item has exactly one of ``path``/``content_base64``, no
    item uses ``path`` over HTTP — phase-4 spec §4) — this function only
    handles per-item DATA failures: an unavailable format extra, a
    malformed/oversized ``content_base64``, or a conversion failure from
    the core itself. Uses ``_bridge_only`` (no task group of its own), not
    ``_convert_with_bridge`` — no per-item heartbeat; ``convert_batch``'s
    own registration runs ONE batch-level heartbeat instead (phase-4 spec
    §4), avoiding N per-item heartbeats racing each other on the same
    progress stream."""
    try:
        convert_fn = server_ctx.batch_convert_fns.get(item.format)
        if convert_fn is None:
            raise MissingOptionalDependencyError(
                f"format {item.format!r} requires refigure[{item.format}]"
            )

        warnings: list[str] = []
        if item.format == "xlsx" and use_vlm:
            warnings.append(_VLM_XLSX_WARNING)

        source = _decode_source(item.path, item.content_base64, server_ctx)
        config = _build_config(use_vlm, vlm_verify, vlm_judge_mode, vlm_model, server_ctx)
        result = await _bridge_only(
            convert_fn, source, config, limiter=server_ctx.limiter, timeout_s=server_ctx.timeout_s
        )

        markdown_bytes = len(result.markdown.encode("utf-8"))
        if markdown_bytes <= server_ctx.resource_inline_threshold_bytes:
            return BatchItemOutput(
                status="ok",
                markdown=result.markdown,
                resource_uri=None,
                warnings=[*warnings, *result.warnings],
                charts_found=result.charts_found,
                charts_rendered=result.charts_rendered,
                groups_found=result.groups_found,
                vlm_used=result.vlm_used,
            )

        # Too large to inline — same resource-store branch _run_convert_tool
        # takes (architecture doc §3/§4); caller_id resolved HERE, after the
        # bridge already returned (on the event loop, never inside the
        # to_thread worker), same ordering _run_convert_tool already relies
        # on being safe.
        caller_id = resolve_caller_id()
        conversion_id = server_ctx.state.insert(caller_id, result.markdown)
        return BatchItemOutput(
            status="ok",
            markdown=None,
            resource_uri=f"refigure://conversion/{conversion_id}",
            warnings=[*warnings, *result.warnings],
            charts_found=result.charts_found,
            charts_rendered=result.charts_rendered,
            groups_found=result.groups_found,
            vlm_used=result.vlm_used,
        )
    except Exception as exc:  # noqa: BLE001 - per-item isolation, see _classify_exception
        return BatchItemOutput(
            status="error", error=_classify_exception(exc, transport=server_ctx.transport)
        )


async def _run_convert_batch_tool(
    items: list[BatchItem],
    use_vlm: bool,
    vlm_verify: bool,
    vlm_judge_mode: Literal["solo", "panel"] | None,
    vlm_model: str | None,
    server_ctx: _ServerContext,
    mcp_ctx: Context,
) -> BatchOutput:
    """``convert_batch``'s body — admission (structural shape check, batch-
    size ceiling, atomic rate-limit) THEN per-item concurrent execution
    with isolation (phase-4 spec §4). Raises for anything that rejects the
    WHOLE batch before any item runs — mirroring ``_run_convert_tool``'s
    own XOR/path-over-HTTP/rate-limit checks, just applied across every
    item at once instead of one; a returned ``BatchOutput`` with per-item
    ``status="error"`` entries covers everything isolatable per item
    instead (see ``_run_batch_item``)."""
    if not items:
        raise ValueError("items must be non-empty")
    if len(items) > server_ctx.max_batch_size:
        raise ValueError(
            f"batch of {len(items)} items exceeds the configured max_batch_size "
            f"of {server_ctx.max_batch_size}"
        )
    for item in items:
        if (item.path is None) == (item.content_base64 is None):
            raise ValueError("each item needs exactly one of path or content_base64")
        if server_ctx.transport == "http" and item.path is not None:
            # Same considered-and-rejected policy as _run_convert_tool's own
            # path-over-HTTP check (architecture doc §6.2) — a structural
            # request-shape problem, so it rejects the WHOLE batch before
            # anything runs, not just this one item.
            raise ValueError(
                "path is not accepted over HTTP — use content_base64, or run over "
                "stdio for local filesystem access"
            )

    if server_ctx.transport == "http":
        # Atomic whole-batch admission against the SAME per-caller counter
        # _run_convert_tool's own single-file admission uses (architecture
        # doc §6 п.3: "admit or reject целиком, no-refund при отказе
        # файла") — check_and_consume_rate_limit's n parameter has existed
        # unused since phase 3, built for exactly this call.
        caller_id = resolve_caller_id()
        if not server_ctx.state.check_and_consume_rate_limit(caller_id, n=len(items)):
            raise RateLimitExceededError(
                f"caller_id {caller_id!r}: batch of {len(items)} exceeds its "
                "remaining conversion rate limit for this window"
            )

    results: list[BatchItemOutput | None] = [None] * len(items)
    done = 0

    async def _report(*, message: str) -> None:
        await mcp_ctx.report_progress(done, total=len(items), message=message)

    async def _heartbeat() -> None:
        elapsed = 0
        while True:
            await anyio.sleep(_HEARTBEAT_INTERVAL_S)
            elapsed += _HEARTBEAT_INTERVAL_S
            # Batch-level heartbeat — a deliberate addition beyond the
            # architecture doc's literal "per-file progress" wording
            # (phase-4 spec §4): without it, a batch where every item is
            # simultaneously slow could stay fully silent for up to
            # timeout_s (default 1h) before the first per-file tick,
            # reintroducing the exact silence problem the single-item
            # heartbeat (§7) exists to avoid. Reuses the SAME (progress,
            # total) pair as the completion tick below — only `message`
            # differs — so the two triggers never disagree about what
            # "done" means on one progress stream.
            await _report(message=f"{done}/{len(items)} converted, {elapsed}s elapsed")

    async def _worker(i: int, item: BatchItem) -> None:
        nonlocal done
        results[i] = await _run_batch_item(
            item, use_vlm, vlm_verify, vlm_judge_mode, vlm_model, server_ctx
        )
        done += 1
        await _report(message=f"{done}/{len(items)} converted")

    try:
        async with anyio.create_task_group() as outer_tg:
            outer_tg.start_soon(_heartbeat)
            # Inner group's __aexit__ blocks until every _worker finishes —
            # the "await/gather, not unsupervised create_task" point
            # architecture doc §7 asks for: each outcome always lands in
            # `results`, the batch is never abandoned mid-flight. Only once
            # ALL items are done does the outer heartbeat get cancelled —
            # same tg.cancel_scope.cancel()-after-the-awaited-work-completes
            # pattern _convert_with_bridge already uses for a single call.
            async with anyio.create_task_group() as inner_tg:
                for i, item in enumerate(items):
                    inner_tg.start_soon(_worker, i, item)
            outer_tg.cancel_scope.cancel()
    except BaseException as exc:  # noqa: BLE001 - re-raises the unwrapped cause, see _unwrap_task_group_exception
        raise _unwrap_task_group_exception(exc) from exc

    final_results = cast(list[BatchItemOutput], results)
    succeeded = sum(1 for r in final_results if r.status == "ok")
    return BatchOutput(
        items=final_results,
        total=len(final_results),
        succeeded=succeeded,
        failed=len(final_results) - succeeded,
    )


def _register_convert_batch(
    mcp: MCPServer,
    server_ctx: _ServerContext,
    transport: Literal["stdio", "http"],
    *,
    has_docx: bool,
    has_xlsx: bool,
) -> None:
    """Registers convert_batch, only if at least one format is actually
    available — same "don't publish a tool that can never succeed"
    philosophy as ``_register_convert_docx``/``_register_convert_xlsx``
    (phase-4 spec §4). Builds ``server_ctx.batch_convert_fns`` from its
    OWN guarded imports (mirroring the exact pattern the other two
    register functions already use) rather than threading the already-
    imported modules through ``build_server()`` — the import is cheap
    (Python caches it) and this keeps each register function's
    format-guard local to itself, not centralized in ``build_server``."""
    if not (has_docx or has_xlsx):
        return
    if has_docx:
        from .. import docx as docx_module

        server_ctx.batch_convert_fns["docx"] = docx_module.convert
    if has_xlsx:
        from .. import xlsx as xlsx_module

        server_ctx.batch_convert_fns["xlsx"] = xlsx_module.convert

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
    async def convert_batch(
        mcp_ctx: Context,
        items: list[BatchItem],
        use_vlm: bool = False,
        vlm_verify: bool = False,
        vlm_judge_mode: Literal["solo", "panel"] | None = None,
        vlm_model: str | None = None,
    ) -> BatchOutput:
        """Convert multiple DOCX/XLSX files in one call. Each item needs
        exactly one of path/content_base64 (path is rejected outright over
        HTTP, same as convert_docx/convert_xlsx). use_vlm/vlm_verify/
        vlm_judge_mode/vlm_model apply as ONE shared policy across every
        item, not per item. One item failing never aborts the batch — its
        entry reports status="error" with a stable "ClassName: message"
        while every other item still reports its own real outcome; check
        the aggregate succeeded/failed counts, not just the overall call's
        own success."""
        return await _call_and_wrap_errors(
            _run_convert_batch_tool(
                items, use_vlm, vlm_verify, vlm_judge_mode, vlm_model, server_ctx, mcp_ctx
            ),
            transport=transport,
        )


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
    max_input_b64_mb: int = DEFAULT_MAX_INPUT_B64_MB,
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
    max_batch_size: int = 20,
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

    ``max_batch_size`` (phase-4 spec §4): ceiling on ``len(items)`` for a
    single ``convert_batch`` call — 20 by default, safely under the
    default ``rate_limit_count`` (30), since the whole batch is admitted
    atomically against that SAME per-window counter (``ServerState.
    check_and_consume_rate_limit``'s ``n`` parameter, unused before this
    phase). Validated HERE, not in ``cli.py``: when ``token_map is not
    None`` (a reliable proxy for "rate-limit is actually enforced" — it's
    ``None`` on stdio, where ``cli.py`` already fail-fasts on the
    token-file+stdio combination, so this condition is equivalent to
    "HTTP with real auth") and ``max_batch_size > rate_limit_count``, a
    full-size batch could NEVER be admitted — raises ``ValueError`` at
    startup rather than shipping a config that silently locks every
    caller out of their own configured batch ceiling.

    Can raise ``MissingOptionalDependencyError`` (``vlm_cache_path`` set
    without ``[vlm]`` installed — ``FileCacheBackend`` lives inside the
    ``refigure.vlm`` package, guarded the same as everything else in it)
    or ``ValueError`` (``vlm_cache_path`` already locked by another
    instance, or the ``max_batch_size``/``rate_limit_count`` mismatch
    above) — callers constructing a real server from user-supplied config
    (``cli.py``) must route this through the same exception-to-exit-code
    boundary every other optional-dependency/external-resource
    construction in this codebase already uses (CLAUDE.md's "Do NOT"
    list)."""
    if token_map is not None and max_batch_size > rate_limit_count:
        raise ValueError(
            f"max_batch_size ({max_batch_size}) must not exceed rate_limit_count "
            f"({rate_limit_count}) — otherwise a full-size batch could never be "
            "admitted over HTTP"
        )
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
        max_batch_size=max_batch_size,
        batch_convert_fns={},
        transport=transport,
    )
    has_docx = _register_convert_docx(mcp, server_ctx, transport)
    has_xlsx = _register_convert_xlsx(mcp, server_ctx, transport)
    _register_conversion_resource(mcp, server_ctx)
    _register_convert_batch(mcp, server_ctx, transport, has_docx=has_docx, has_xlsx=has_xlsx)
    _register_prompts(mcp, has_docx=has_docx, has_xlsx=has_xlsx)
    _register_completion(mcp)
    return mcp
