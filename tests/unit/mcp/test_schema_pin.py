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


def _without_descriptions(node: object) -> object:
    """Strip every ``"description"`` key, recursively — needed only for
    ``convert_batch`` (phase 4): unlike ``ConvertOutput`` (returned
    directly, no ``description`` in its own top-level schema — confirmed
    live, which is why the pin above compares full dicts with no
    stripping), ``BatchItem``/``BatchItemOutput`` are referenced via
    ``$ref`` inside ``$defs`` — the SDK's schema derivation DOES include
    each nested dataclass's own docstring there as ``"description"``.
    Pinning that verbatim would make this test a docstring-wording pin,
    not a structural one — this file's own module docstring says the
    goal is catching "a field added/removed/retyped", not prose
    changes."""
    if isinstance(node, dict):
        return {k: _without_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_without_descriptions(v) for v in node]
    return node


_EXPECTED_BATCH_INPUT_SCHEMA = {
    "type": "object",
    "title": "convert_batchArguments",
    "$defs": {
        "BatchItem": {
            "type": "object",
            "title": "BatchItem",
            "required": ["format"],
            "properties": {
                "format": {"type": "string", "enum": ["docx", "xlsx"], "title": "Format"},
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
            },
        }
    },
    "properties": {
        "items": {"type": "array", "items": {"$ref": "#/$defs/BatchItem"}, "title": "Items"},
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
    "required": ["items"],
}

_EXPECTED_BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "title": "BatchOutput",
    "$defs": {
        "BatchItemOutput": {
            "type": "object",
            "title": "BatchItemOutput",
            "required": ["status"],
            "properties": {
                "status": {"type": "string", "enum": ["ok", "error"], "title": "Status"},
                "markdown": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Markdown",
                },
                "resource_uri": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Resource Uri",
                },
                "warnings": {"type": "array", "items": {"type": "string"}, "title": "Warnings"},
                "charts_found": {"type": "integer", "default": 0, "title": "Charts Found"},
                "charts_rendered": {"type": "integer", "default": 0, "title": "Charts Rendered"},
                "groups_found": {"type": "integer", "default": 0, "title": "Groups Found"},
                "vlm_used": {"type": "boolean", "default": False, "title": "Vlm Used"},
                "error": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Error",
                },
            },
        }
    },
    "properties": {
        "items": {
            "type": "array",
            "items": {"$ref": "#/$defs/BatchItemOutput"},
            "title": "Items",
        },
        "total": {"type": "integer", "title": "Total"},
        "succeeded": {"type": "integer", "title": "Succeeded"},
        "failed": {"type": "integer", "title": "Failed"},
    },
    "required": ["items", "total", "succeeded", "failed"],
}


async def test_convert_batch_schema_pin() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    tool = tools["convert_batch"]
    assert _without_descriptions(tool.input_schema) == _EXPECTED_BATCH_INPUT_SCHEMA, (
        "convert_batch's inputSchema drifted from the pinned shape"
    )
    assert _without_descriptions(tool.output_schema) == _EXPECTED_BATCH_OUTPUT_SCHEMA, (
        "BatchOutput's output schema drifted from the pinned shape"
    )
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
