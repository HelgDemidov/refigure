#!/usr/bin/env python3
"""MCPB entry point — thin shim over refigure-mcp's own CLI.

manifest.json declares server.type "uv": the host (e.g. Claude Desktop)
resolves dependencies from pyproject.toml at run time via `uv run`, no
vendored deps/venv inside the bundle. No arguments are passed to
main() — the CLI's own default transport (stdio) is exactly what a
locally-launched MCP server needs.
"""

from refigure.mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
