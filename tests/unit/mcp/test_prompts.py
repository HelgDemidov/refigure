"""``ingest_for_rag``/``explain_conversion_warnings`` prompts
(``server.py``'s ``_register_prompts``) and the ``@mcp.completion()``
dependent-argument handler (``_register_completion``)."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import PromptReference

from refigure.mcp.server import _register_completion, _register_prompts, build_server

pytestmark = pytest.mark.anyio


def _text_of(prompt_result) -> str:
    return prompt_result.messages[0].content.text


async def test_prompts_list_registers_both() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        prompts = await c.list_prompts()

    assert {p.name for p in prompts.prompts} == {"ingest_for_rag", "explain_conversion_warnings"}


async def test_ingest_for_rag_docx_without_vlm() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt("ingest_for_rag", {"document_format": "docx"})

    text = _text_of(result)
    assert "convert_docx" in text
    # The non-VLM branch legitimately still MENTIONS use_vlm=True as an
    # available opt-in ("...unless you also pass use_vlm=True") - the real
    # differentiator from the with-VLM branch below is that it doesn't
    # recommend calling WITH it as the primary suggested invocation.
    assert "use_vlm=True, vlm_verify=True" not in text


async def test_ingest_for_rag_docx_with_vlm() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt(
            "ingest_for_rag",
            {"document_format": "docx", "use_vlm": "true"},
        )

    text = _text_of(result)
    assert "convert_docx" in text
    assert "use_vlm=True" in text


async def test_ingest_for_rag_docx_with_vlm_and_an_explicit_judge_mode() -> None:
    """architecture doc §5's literal target: vlm_judge_mode surfaced as
    its own argument, not just mentioned in passing."""
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt(
            "ingest_for_rag",
            {"document_format": "docx", "use_vlm": "true", "vlm_judge_mode": "solo"},
        )

    assert "vlm_judge_mode='solo'" in _text_of(result)


async def test_ingest_for_rag_xlsx_never_mentions_use_vlm_true() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt(
            "ingest_for_rag",
            {"document_format": "xlsx", "use_vlm": "true"},
        )

    text = _text_of(result)
    assert "convert_xlsx" in text
    assert "use_vlm=True" not in text


async def test_ingest_for_rag_unrecognized_format() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt("ingest_for_rag", {"document_format": "pdf"})

    assert "Unrecognized" in _text_of(result)


async def test_ingest_for_rag_is_capability_aware_without_docx() -> None:
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=False, has_xlsx=True)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.get_prompt("ingest_for_rag", {"document_format": "docx"})

    assert "no [docx] extra installed" in _text_of(result)


async def test_ingest_for_rag_is_capability_aware_without_xlsx() -> None:
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=False)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.get_prompt("ingest_for_rag", {"document_format": "xlsx"})

    assert "no [xlsx] extra installed" in _text_of(result)


async def test_explain_conversion_warnings_lists_each_line() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt(
            "explain_conversion_warnings",
            {"warnings": "vlm-render-failed: abc\nchart-lost: def"},
        )

    text = _text_of(result)
    assert "vlm-render-failed: abc" in text
    assert "chart-lost: def" in text


async def test_explain_conversion_warnings_empty_string() -> None:
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.get_prompt("explain_conversion_warnings", {"warnings": ""})

    assert "No warnings to explain" in _text_of(result)


async def test_completion_offers_solo_and_panel_when_use_vlm_true() -> None:
    """architecture doc §5's literal target: vlm_judge_mode completion,
    dependent on use_vlm already being resolved to "true"."""
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=True)
    _register_completion(mcp)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.complete(
            ref=PromptReference(name="ingest_for_rag"),
            argument={"name": "vlm_judge_mode", "value": ""},
            context_arguments={"use_vlm": "true"},
        )

    assert result.completion.values == ["solo", "panel"]


async def test_completion_offers_nothing_when_use_vlm_false() -> None:
    """solo/panel is meaningless when VLM isn't even wanted — offering it
    anyway would be actively misleading, not just unhelpful."""
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=True)
    _register_completion(mcp)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.complete(
            ref=PromptReference(name="ingest_for_rag"),
            argument={"name": "vlm_judge_mode", "value": ""},
            context_arguments={"use_vlm": "false"},
        )

    assert result.completion.values == []


async def test_completion_offers_nothing_when_use_vlm_not_yet_resolved() -> None:
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=True)
    _register_completion(mcp)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.complete(
            ref=PromptReference(name="ingest_for_rag"),
            argument={"name": "vlm_judge_mode", "value": ""},
        )

    assert result.completion.values == []


async def test_completion_on_an_irrelevant_argument_offers_nothing() -> None:
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=True)
    _register_completion(mcp)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.complete(
            ref=PromptReference(name="ingest_for_rag"),
            argument={"name": "document_format", "value": ""},
        )

    assert result.completion.values == []


async def test_completion_on_an_irrelevant_prompt_offers_nothing() -> None:
    mcp = MCPServer("x")
    _register_prompts(mcp, has_docx=True, has_xlsx=True)
    _register_completion(mcp)
    async with Client(mcp, raise_exceptions=True) as c:
        result = await c.complete(
            ref=PromptReference(name="explain_conversion_warnings"),
            argument={"name": "warnings", "value": ""},
        )

    assert result.completion.values == []
