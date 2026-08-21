"""In-memory Client tests for convert_docx/convert_xlsx — architecture doc
§9's pattern (see conftest.py's anyio_backend fixture for why anyio, not
pytest-asyncio). Corpus fixtures are deliberately NOT used here (they're
optional/gitignored, and tests/integration/ already exercises real corpus
documents through the core convert() functions) — synthetic builders
(``build_minimal_docx``/a bare ``openpyxl.Workbook()``) keep this suite
fast and independent of local fixture setup, matching
``tests/unit/vlm/test_vlm.py``'s own convention.
"""

from __future__ import annotations

import base64
import io
import zipfile
from typing import Any
from unittest.mock import patch

import openpyxl
import pytest
from mcp import Client

from refigure.mcp.server import build_server
from tests.unit.docx.test_docx import build_minimal_docx

pytestmark = pytest.mark.anyio


def _minimal_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
async def client():
    mcp_server = build_server()
    async with Client(mcp_server, raise_exceptions=True) as c:
        yield c


def _text(result: Any) -> str:
    return result.content[0].text if result.content else ""


async def test_convert_docx_via_path_succeeds(client: Client, tmp_path) -> None:
    doc_path = tmp_path / "doc.docx"
    doc_path.write_bytes(build_minimal_docx(["hello world"]))

    result = await client.call_tool("convert_docx", {"path": str(doc_path)})

    assert result.is_error is False
    sc = result.structured_content
    assert "hello world" in sc["markdown"]
    assert sc["resource_uri"] is None
    assert sc["vlm_used"] is False


async def test_convert_xlsx_via_content_base64_succeeds(client: Client) -> None:
    b64 = base64.b64encode(_minimal_xlsx_bytes()).decode("ascii")

    result = await client.call_tool("convert_xlsx", {"content_base64": b64})

    assert result.is_error is False
    sc = result.structured_content
    assert "hello" in sc["markdown"]
    assert sc["warnings"] == []


async def test_convert_xlsx_use_vlm_warns_without_a_real_vlm_path(client: Client) -> None:
    b64 = base64.b64encode(_minimal_xlsx_bytes()).decode("ascii")

    with patch("refigure.vlm.client.OpenRouterClient.send") as send:
        result = await client.call_tool("convert_xlsx", {"content_base64": b64, "use_vlm": True})

    assert result.is_error is False
    sc = result.structured_content
    assert sc["vlm_used"] is False
    assert any("no VLM path" in w for w in sc["warnings"])
    send.assert_not_called()


async def test_convert_docx_neither_path_nor_content_base64_is_an_error(client: Client) -> None:
    result = await client.call_tool("convert_docx", {})

    assert result.is_error is True
    assert "exactly one of path or content_base64" in _text(result)


async def test_convert_docx_both_path_and_content_base64_is_an_error(client: Client) -> None:
    result = await client.call_tool(
        "convert_docx", {"path": "irrelevant.docx", "content_base64": "eA=="}
    )

    assert result.is_error is True
    assert "exactly one of path or content_base64" in _text(result)


async def test_content_base64_over_cap_rejected_before_decoding(client: Client) -> None:
    oversized = "A" * (200 * 1024 * 1024)  # server default cap is 100 MB

    with patch("refigure.mcp.server.base64.b64decode") as b64decode:
        result = await client.call_tool("convert_docx", {"content_base64": oversized})

    assert result.is_error is True
    assert "MB cap" in _text(result)
    b64decode.assert_not_called()


async def test_corrupt_archive_reports_the_typed_exception_class(client: Client) -> None:
    not_a_zip = base64.b64encode(b"this is not a zip file at all").decode("ascii")

    result = await client.call_tool("convert_docx", {"content_base64": not_a_zip})

    assert result.is_error is True
    text = _text(result)
    assert text.startswith("Error executing tool convert_docx: CorruptArchiveError:")
    # Never a bare traceback/repr leaking the internal zipfile message alone
    # without the classifying prefix _call_and_wrap_errors adds.
    assert "internal_error" not in text


async def test_convert_docx_use_vlm_is_inert_without_any_vlm_markers(client: Client) -> None:
    """use_vlm=True on a document with nothing for it to interpret (no
    image/group markers) must never fail just because no VLM
    provider/API key is configured — enhance_docx_markdown only resolves
    an API key lazily, on an actual marker."""
    doc_bytes = build_minimal_docx(["some text, no VLM markers though"])
    b64 = base64.b64encode(doc_bytes).decode("ascii")

    with patch.dict("os.environ", {}, clear=True):
        result = await client.call_tool("convert_docx", {"content_base64": b64, "use_vlm": True})

    assert result.is_error is False
    assert result.structured_content["vlm_used"] is False


async def test_list_tools_registers_both_convert_tools(client: Client) -> None:
    tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert names == {"convert_docx", "convert_xlsx"}
    for tool in tools.tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


async def test_corrupt_zip_bytes_are_rejected_as_corrupt_archive(client: Client) -> None:
    """A structurally-valid zip with no OOXML content still degrades to
    CorruptArchiveError, not something more exotic — belt-and-suspenders
    alongside test_corrupt_archive_reports_the_typed_exception_class'
    not-a-zip-at-all case above."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("unrelated.txt", "not a docx")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    result = await client.call_tool("convert_docx", {"content_base64": b64})

    assert result.is_error is True
    assert "CorruptArchiveError" in _text(result) or "UnsupportedFormatError" in _text(result)
