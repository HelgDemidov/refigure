"""``refigure-mcp`` console command — thin argparse layer over
``refigure.mcp.server.build_server``. Mirrors ``refigure/cli.py``'s own
module docstring in spirit: this file itself never imports ``refigure.docx``/
``refigure.xlsx`` (that stays inside ``server.py``'s per-tool lazy imports).

Reuses ``refigure.cli``'s VLM-provider-selection flags/logic literally
(``_resolve_vlm_client``/``_exit_code_for``) rather than a second copy that
could drift — same ``argparse.Namespace`` attribute names
(``vlm_provider``/``vlm_base_url``/``vlm_image_format``/``vlm_api_key_file``)
``_resolve_vlm_client`` already expects. Only stdio in this phase —
``--transport`` lands in phase 3 alongside HTTP (architecture doc §12).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..cli import EXIT_INTERNAL_ERROR, _exit_code_for, _resolve_vlm_client
from .server import build_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refigure-mcp",
        description="refigure MCP server — convert_docx/convert_xlsx tools over stdio.",
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
    except Exception as exc:
        code = _exit_code_for(exc)
        message = str(exc) if code != EXIT_INTERNAL_ERROR else f"internal error: {exc}"
        print(f"error: {message}", file=sys.stderr)
        return code

    kwargs: dict[str, Any] = {}
    if args.mcp_max_concurrent_conversions is not None:
        kwargs["max_concurrent"] = args.mcp_max_concurrent_conversions
    if args.mcp_max_input_mb is not None:
        kwargs["max_input_b64_mb"] = args.mcp_max_input_mb
    if args.mcp_conversion_timeout_s is not None:
        kwargs["timeout_s"] = args.mcp_conversion_timeout_s
    if args.vlm_max_markers is not None:
        kwargs["vlm_max_markers"] = args.vlm_max_markers

    mcp_server = build_server(
        transport="stdio", vlm_client=vlm_client, vlm_api_key=vlm_api_key, **kwargs
    )
    mcp_server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
