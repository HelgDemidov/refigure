"""``refigure-mcp`` console command — thin argparse layer over
``refigure.mcp.server.build_server``. Mirrors ``refigure/cli.py``'s own
module docstring in spirit: this file itself never imports ``refigure.docx``/
``refigure.xlsx`` (that stays inside ``server.py``'s per-tool lazy imports).

Reuses ``refigure.cli``'s VLM-provider-selection flags/logic literally
(``_resolve_vlm_client``/``_exit_code_for``) rather than a second copy that
could drift — same ``argparse.Namespace`` attribute names
(``vlm_provider``/``vlm_base_url``/``vlm_image_format``/``vlm_api_key_file``)
``_resolve_vlm_client`` already expects.

``--transport http`` (phase 3, ``docs/mcp-server/mcp-server-phase3-http-auth/
mcp-server-phase3-http-auth-2026-08-21.md`` §7) — Streamable HTTP + bearer
auth via a token file. The SDK's own transport literal is
``"streamable-http"``, not ``"http"``; this file's ``--transport`` choices
stay ``stdio``/``http`` (matching the architecture doc's own
``refigure-mcp --transport stdio|http`` wording) and the mapping happens at
the single ``mcp_server.run()`` call site below, nowhere else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..cli import EXIT_INTERNAL_ERROR, EXIT_USAGE, _exit_code_for, _resolve_vlm_client
from .auth import load_token_file
from .server import DEFAULT_MAX_INPUT_B64_MB, build_server

# JSON-RPC envelope/tool-name/string-quoting overhead added on top of the
# tool's own content_base64 length cap when sizing --transport http's
# max_request_body_size (phase-3 spec §7) — content_base64 itself needs no
# escaping (its alphabet is already JSON-string-safe), this is purely the
# surrounding envelope. Generous, not tight: this bounds a legitimate
# maximal request from being rejected at the transport layer before the
# tool's own check ever runs, not a security boundary in itself.
_JSONRPC_ENVELOPE_OVERHEAD_BYTES = 4096

# The DNS-rebinding-protection host patterns build_server()'s underlying SDK
# call already special-cases (mcp.server.lowlevel.server.Server.
# streamable_http_app's own host in (...) check) — mirrored here only for
# the operator-facing non-loopback bind warning (architecture doc §6 п.1),
# not to duplicate any actual security logic.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refigure-mcp",
        description=(
            "refigure MCP server — convert_docx/convert_xlsx tools over stdio "
            "(default) or Streamable HTTP (--transport http)."
        ),
    )
    parser.add_argument(
        "--mcp-max-concurrent-conversions",
        metavar="N",
        type=int,
        help="Bounded pool size for concurrent conversions (default: 4).",
    )
    parser.add_argument(
        "--mcp-max-input-mb",
        metavar="N",
        type=int,
        help=(
            "content_base64 length cap in MB, checked before decoding "
            "(default: 100 — see the spec for the RAM-budget derivation)."
        ),
    )
    parser.add_argument(
        "--mcp-conversion-timeout-s",
        metavar="N",
        type=int,
        help=(
            "Hard per-call timeout in seconds (default: 3600). A caller is "
            "released on timeout even if the underlying conversion keeps "
            "running abandoned — this bounds responsiveness/pool "
            "availability, not spend (see --vlm-max-markers for that)."
        ),
    )
    parser.add_argument(
        "--vlm-max-markers",
        metavar="N",
        type=int,
        help=(
            "Server-wide default ceiling on VLM markers requiring a paid "
            "call per convert_docx invocation (default: 200). Same "
            "mechanism as refigure CLI's --vlm-max-markers, applied "
            "uniformly here since MCP tool calls have no per-call "
            "equivalent flag."
        ),
    )
    parser.add_argument(
        "--mcp-resource-inline-threshold-kb",
        metavar="N",
        type=int,
        help=(
            "Markdown at or under this size (KB) returns inline; larger "
            "returns resource_uri instead, readable via the "
            "refigure://conversion/{id} resource (default: 256)."
        ),
    )
    parser.add_argument(
        "--mcp-resource-max-entries",
        metavar="N",
        type=int,
        help="Max conversion-result entries kept in the resource store (default: 200).",
    )
    parser.add_argument(
        "--mcp-resource-max-mb",
        metavar="N",
        type=int,
        help=(
            "Total byte budget (MB) for the resource store, whichever "
            "limit hits first (default: 500)."
        ),
    )
    parser.add_argument(
        "--mcp-resource-ttl-s",
        metavar="N",
        type=int,
        help="Resource-store entry TTL in seconds (default: 3600).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help=(
            'Transport to serve over (default: stdio). "http" (Streamable '
            "HTTP) requires --mcp-auth-token-file — fails fast at startup "
            "otherwise, never serves HTTP without auth."
        ),
    )
    parser.add_argument(
        "--mcp-auth-token-file",
        metavar="PATH",
        help=(
            "Bearer-token file for --transport http: one 'token = caller_id' "
            "pair per line. Required with --transport http, rejected with "
            "--transport stdio (auth has no meaning there — stdio's only "
            "caller_id is the __local__ sentinel)."
        ),
    )
    parser.add_argument(
        "--mcp-http-host",
        metavar="HOST",
        default="127.0.0.1",
        help=(
            "Bind host for --transport http (default: 127.0.0.1 — loopback "
            "only). A non-loopback value is accepted but prints a startup "
            "warning: the HTTP endpoint becomes reachable beyond this "
            "machine."
        ),
    )
    parser.add_argument(
        "--mcp-http-port",
        metavar="N",
        type=int,
        default=8000,
        help="Bind port for --transport http (default: 8000).",
    )
    parser.add_argument(
        "--mcp-rate-limit-count",
        metavar="N",
        type=int,
        help=(
            "Per-caller_id conversion quota per window, --transport http "
            "only, applied even with a single configured token (default: "
            "30 — protects the operator's own resources/spend from a "
            "runaway or leaked token, not inter-caller fairness)."
        ),
    )
    parser.add_argument(
        "--mcp-rate-limit-window-s",
        metavar="N",
        type=int,
        help="Rate-limit window in seconds, --transport http only (default: 60).",
    )
    parser.add_argument(
        "--mcp-vlm-cache",
        metavar="PATH",
        help=(
            "Persist the shared VLM cache to this JSON file (requires "
            "refigure[vlm]) instead of the default in-memory bounded-LRU "
            "cache. Dev/small-corpus convenience, NOT memory-safe for a "
            "large corpus (whole cache held in RAM, rewritten on every "
            "write) — see FileCacheBackend's own docstring. Never point "
            "two refigure-mcp instances at the same path — guarded "
            "against with a file lock, fails fast with a clear error "
            "instead of silently losing writes."
        ),
    )
    vlm_group = parser.add_argument_group(
        "VLM provider (resolved once at server start, never per tool call)"
    )
    vlm_group.add_argument(
        "--vlm-provider",
        choices=["openrouter", "openai", "anthropic"],
        default="openrouter",
        help=(
            "VLM backend for any tool call with use_vlm=True (default: "
            "openrouter). openai/anthropic need refigure[vlm-direct]."
        ),
    )
    vlm_group.add_argument(
        "--vlm-base-url",
        metavar="URL",
        help="OpenAI-compatible endpoint (Ollama/vLLM/LM Studio). Requires --vlm-provider openai.",
    )
    vlm_group.add_argument(
        "--vlm-image-format",
        choices=["dict", "string"],
        default="dict",
        help=(
            "Image content shape for the openai provider (default: dict; "
            "Ollama needs 'string'). Requires --vlm-provider openai."
        ),
    )
    vlm_group.add_argument(
        "--vlm-api-key-file",
        metavar="PATH",
        help=(
            "Read the VLM API key from this file (openrouter: "
            "OPENROUTER_API_KEY-equivalent; ignored for openai/anthropic, "
            "which read their own SDK-standard env vars). Avoids a secret "
            "in argv/shell history."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Fail-fast (parser.error -> argparse's own exit code 2, same pattern
    # _resolve_vlm_client's --vlm-base-url check already uses): HTTP without
    # a token file would silently collapse every remote caller into one
    # anonymous __local__-equivalent identity (architecture doc §6) — an
    # explicit error, not a fail-open default. A token file WITH stdio is
    # rejected too, not silently ignored: it would otherwise look like auth
    # is active when it isn't (stdio has no notion of caller identity at
    # all).
    if args.transport == "http" and args.mcp_auth_token_file is None:
        parser.error("--transport http requires --mcp-auth-token-file")
    if args.transport == "stdio" and args.mcp_auth_token_file is not None:
        parser.error("--mcp-auth-token-file requires --transport http")

    # Token-file loading gets its OWN except clause, not the generic
    # _exit_code_for boundary below: load_token_file()'s ValueError is an
    # MCP-local config-loading concern refigure.cli's exit-code map knows
    # nothing about — routing it through _exit_code_for's fallback would
    # mislabel an ordinary token-file typo as EXIT_INTERNAL_ERROR ("internal
    # error: ..."), same class of mistake CLAUDE.md's "Do NOT" list already
    # flags for other external-construction boundaries. Also catches OSError
    # (FileNotFoundError/PermissionError/IsADirectoryError, all raised by
    # load_token_file's own Path.read_text() before it ever gets to parsing
    # — none are ValueError subclasses) — a missing/unreadable token-file
    # path is the same class of plain operator typo as a malformed line
    # inside it, not an internal error either (real gap found by ultrareview
    # on this PR: only ValueError was caught here, so a bad path crashed
    # with a raw traceback and exit code 1 instead of this clean path).
    token_map: dict[str, str] | None = None
    if args.mcp_auth_token_file is not None:
        try:
            token_map = load_token_file(Path(args.mcp_auth_token_file))
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if args.transport == "http" and args.mcp_http_host not in _LOOPBACK_HOSTS:
        print(
            f"warning: refigure-mcp: binding to non-loopback host {args.mcp_http_host!r} — "
            "the HTTP endpoint is reachable beyond this machine; make sure "
            "--mcp-auth-token-file is configured and its tokens are trusted",
            file=sys.stderr,
        )

    try:
        # Constructing a real OpenAIClient/AnthropicClient here can raise
        # (missing API key, missing refigure[vlm-direct]) — routed through
        # the same _exit_code_for mapping the main CLI's _build_config uses
        # for the identical eager-construction case (CLAUDE.md's "Do NOT"
        # list: never let an external-SDK construction escape this
        # boundary as a raw traceback).
        vlm_client = _resolve_vlm_client(args, parser)
        vlm_api_key = None
        if vlm_client is None and args.vlm_api_key_file is not None:
            vlm_api_key = Path(args.vlm_api_key_file).read_text().strip()

        kwargs: dict[str, Any] = {}
        if args.mcp_max_concurrent_conversions is not None:
            kwargs["max_concurrent"] = args.mcp_max_concurrent_conversions
        if args.mcp_max_input_mb is not None:
            kwargs["max_input_b64_mb"] = args.mcp_max_input_mb
        if args.mcp_conversion_timeout_s is not None:
            kwargs["timeout_s"] = args.mcp_conversion_timeout_s
        if args.vlm_max_markers is not None:
            kwargs["vlm_max_markers"] = args.vlm_max_markers
        if args.mcp_resource_inline_threshold_kb is not None:
            kwargs["resource_inline_threshold_bytes"] = args.mcp_resource_inline_threshold_kb * 1024
        if args.mcp_resource_max_entries is not None:
            kwargs["resource_max_entries"] = args.mcp_resource_max_entries
        if args.mcp_resource_max_mb is not None:
            kwargs["resource_max_bytes"] = args.mcp_resource_max_mb * 1024 * 1024
        if args.mcp_resource_ttl_s is not None:
            kwargs["resource_ttl_s"] = args.mcp_resource_ttl_s
        if args.mcp_vlm_cache is not None:
            kwargs["vlm_cache_path"] = Path(args.mcp_vlm_cache)
        if args.mcp_rate_limit_count is not None:
            kwargs["rate_limit_count"] = args.mcp_rate_limit_count
        if args.mcp_rate_limit_window_s is not None:
            kwargs["rate_limit_window_s"] = args.mcp_rate_limit_window_s
        if token_map is not None:
            kwargs["token_map"] = token_map

        # build_server() can also raise here — MissingOptionalDependencyError
        # (--mcp-vlm-cache without [vlm]) or ValueError (--mcp-vlm-cache path
        # already locked by another instance) — same boundary as
        # _resolve_vlm_client above, not a second, uncaught failure mode.
        mcp_server = build_server(
            transport=args.transport, vlm_client=vlm_client, vlm_api_key=vlm_api_key, **kwargs
        )
    except Exception as exc:
        code = _exit_code_for(exc)
        message = str(exc) if code != EXIT_INTERNAL_ERROR else f"internal error: {exc}"
        print(f"error: {message}", file=sys.stderr)
        return code

    # The single point where the CLI's own "stdio"/"http" choice maps onto
    # the SDK's real transport literal ("streamable-http", not "http" —
    # phase-3 spec §0/§7). max_request_body_size sized off the SAME
    # effective content_base64 cap build_server() itself just used (falling
    # back to DEFAULT_MAX_INPUT_B64_MB, never a second "100" literal here)
    # plus a fixed JSON-RPC envelope allowance — otherwise a legal maximal
    # tool call would be rejected at the transport layer before the tool's
    # own cap check ever runs.
    if args.transport == "http":
        effective_max_input_mb = (
            args.mcp_max_input_mb if args.mcp_max_input_mb is not None else DEFAULT_MAX_INPUT_B64_MB
        )
        mcp_server.run(
            transport="streamable-http",
            host=args.mcp_http_host,
            port=args.mcp_http_port,
            max_request_body_size=effective_max_input_mb * 1024 * 1024
            + _JSONRPC_ENVELOPE_OVERHEAD_BYTES,
        )
    else:
        mcp_server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
