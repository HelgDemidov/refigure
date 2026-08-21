"""Golden-file schema pin (architecture doc §3): a single diff covers ANY
drift in either tool's inputSchema OR ConvertOutput's output schema — a
field added/removed/retyped anywhere fails this test, not silently ships.
Schemas are SDK-derived (from each tool's own parameter list and its
``-> ConvertOutput`` return annotation, per ``mcp==2.0.0``'s documented
behavior — confirmed live, not assumed), never hand-maintained separately.
"""

from __future__ import annotations

import pytest
from mcp import Client

from refigure.mcp.server import build_server

pytestmark = pytest.mark.anyio


def _expected_input_schema(tool_name: str) -> dict:
    return {
        "type": "object",
        "title": f"{tool_name}Arguments",
        "properties": {
            "path": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Path",
            },
            "content_base64": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Content Base64",
            },
            "use_vlm": {"type": "boolean", "default": False, "title": "Use Vlm"},
            "vlm_verify": {"type": "boolean", "default": False, "title": "Vlm Verify"},
            "vlm_judge_mode": {
                "anyOf": [{"type": "string", "enum": ["solo", "panel"]}, {"type": "null"}],
                "default": None,
                "title": "Vlm Judge Mode",
            },
            "vlm_model": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Vlm Model",
            },
        },
    }


_EXPECTED_OUTPUT_SCHEMA = {
    "type": "object",
    "title": "ConvertOutput",
    "required": ["markdown", "resource_uri", "warnings"],
    "properties": {
        "markdown": {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Markdown"},
        "resource_uri": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "title": "Resource Uri",
        },
        "warnings": {"type": "array", "items": {"type": "string"}, "title": "Warnings"},
        "charts_found": {"type": "integer", "default": 0, "title": "Charts Found"},
        "charts_rendered": {"type": "integer", "default": 0, "title": "Charts Rendered"},
        "groups_found": {"type": "integer", "default": 0, "title": "Groups Found"},
        "vlm_used": {"type": "boolean", "default": False, "title": "Vlm Used"},
    },
}


@pytest.mark.parametrize("tool_name", ["convert_docx", "convert_xlsx"])
async def test_tool_schema_pin(tool_name: str) -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    tool = tools[tool_name]
    assert tool.input_schema == _expected_input_schema(tool_name), (
        "inputSchema drifted from the pinned shape — a field/type/default changed; "
        "update this pin deliberately if the change is intended, don't just re-run it"
    )
    assert tool.output_schema == _EXPECTED_OUTPUT_SCHEMA, (
        "ConvertOutput's output schema drifted from the pinned shape"
    )
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
