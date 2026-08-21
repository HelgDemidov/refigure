"""``convert_batch`` under REAL concurrent MCP load — the one test phase 4
explicitly charters (``docs/mcp-server/mcp-server-phase4-batch-progress/
mcp-server-phase4-batch-progress-2026-08-21.md`` §6, architecture doc §12
п.4): both of the core's own known concurrency hazards
(``_OPENPYXL_LOAD_LOCK``, soffice profile isolation + the shared VLM
cache), exercised through the real ``convert_batch`` tool over a real
``Client``, not an isolated ``threading``-level unit test.

The docx+VLM test below found a REAL, previously-unknown bug live: 2+
concurrent ``pdfplumber.open()`` calls (one per conversion thread,
rendering its OWN separate PDF) crashed the whole Python interpreter with
SIGTRAP inside ``libpdfium.so`` (pdfplumber's native rendering backend,
``pypdfium2`` — not thread-safe across Python threads in one process,
confirmed via a real coredump backtrace: ``CPDF_Color::~CPDF_Color()``).
Same root-cause SHAPE as ``_OPENPYXL_LOAD_LOCK``
(``project_openpyxl_concurrent_parser_fragility``) — a third-party
library sharing unsynchronized native state across threads — but a more
severe failure mode (a hard process crash, not a wrong-result race).
Fixed in ``refigure/vlm/__init__.py`` with ``_PDFIUM_RENDER_LOCK``, the
identical pattern; re-verified live afterward at ``max_concurrent=4``
before this test's assertions were finalized.

Real corpus fixtures, gated the same way ``test_docx_groups_live.py``
already gates its own soffice-dependent test — ``skipif`` on the
fixture's presence on disk (fixtures are optional/gitignored-adjacent,
see ``tests/integration/fixtures/README.md``); CI installs
``libreoffice-writer`` for THIS job (``test-integration``, per the
2026-08-19 pre-release audit noted in CLAUDE.md), so the docx+VLM test
below is not expected to skip there.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from mcp import Client

from refigure.mcp.server import build_server

_XLSX_FIXTURE = Path(__file__).parent / "fixtures" / "xlsx" / "daisy-trd2-radar-scoring.xlsx"
_DOCX_GROUPS_FIXTURE = Path(__file__).parent / "fixtures" / "docx" / "efsa-echinococcus-guide.docx"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _StubVlmClient:
    """Never touches the network — every call returns the same short,
    valid figure description. Enough to exercise the real soffice-render
    + VLM-cache-write path end to end without any real cost, same
    "no real network" discipline as ``tests/unit/vlm/test_vlm.py``'s own
    ``_ScriptedVlmClient``."""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        self.calls += 1
        return "A composite figure."


@pytest.mark.skipif(
    not _XLSX_FIXTURE.exists(), reason=f"fixture not present on disk: {_XLSX_FIXTURE}"
)
async def test_concurrent_xlsx_batch_exercises_the_openpyxl_load_lock() -> None:
    """``_OPENPYXL_LOAD_LOCK`` (``refigure/xlsx/__init__.py``) already has
    dedicated direct-threading coverage elsewhere
    (``project_openpyxl_concurrent_parser_fragility``) — this is the first
    test that exercises it through the REAL MCP concurrent path
    (``convert_batch``'s own task-group fan-out over
    ``anyio.to_thread.run_sync``, bounded by ``max_concurrent``), not a
    bare ``threading.Thread`` loop."""
    b64 = base64.b64encode(_XLSX_FIXTURE.read_bytes()).decode("ascii")
    mcp_server = build_server(max_concurrent=8, max_batch_size=8)

    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.call_tool(
            "convert_batch",
            {"items": [{"format": "xlsx", "content_base64": b64}] * 8},
        )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["total"] == 8
    assert sc["succeeded"] == 8, sc["items"]
    # Every item converted the SAME source — their markdown must agree
    # (a corrupted/interleaved parser read under contention would show up
    # as items disagreeing with each other, not just as a crash).
    markdowns = {item["markdown"] for item in sc["items"]}
    assert len(markdowns) == 1


@pytest.mark.skipif(
    not _DOCX_GROUPS_FIXTURE.exists(), reason=f"fixture not present on disk: {_DOCX_GROUPS_FIXTURE}"
)
async def test_concurrent_docx_vlm_batch_with_composite_groups() -> None:
    """``efsa-echinococcus-guide.docx`` — 10 composite groups, the same
    fixture ``test_docx_groups_live.py`` uses for its own real-soffice
    coverage. N copies converted concurrently in ONE ``convert_batch``
    call with ``use_vlm=True`` exercises the hazards architecture doc
    §7-bis inventories, plus one it didn't (found live writing this test
    — see the module docstring): (1) per-conversion soffice profile
    isolation (PR #27's prerequisite fix, measured live at a ~1/3 failure
    rate WITHOUT it) — a render failure surfaces as a
    ``"vlm-render-failed: ..."`` entry in that item's ``warnings``
    (``refigure/vlm/__init__.py``), so success here means genuinely zero
    render failures under concurrent MCP-batch load, not just "no crash";
    (2) the shared ``BoundedLruVlmCache`` written from multiple concurrent
    ``to_thread`` workers at once — success here means no crash/corruption
    under that access pattern, not a specific hit-rate (this fixture's 10
    groups are read in file order every time, cache reuse across the N
    copies is a real but incidental bonus, not the property under test);
    (3) ``_PDFIUM_RENDER_LOCK`` — without it, this exact test crashed the
    whole interpreter (SIGTRAP) even at just 2 concurrent conversions, not
    a graceful degradation the ``vlm-render-failed`` check above could
    ever catch."""
    b64 = base64.b64encode(_DOCX_GROUPS_FIXTURE.read_bytes()).decode("ascii")
    stub = _StubVlmClient()
    mcp_server = build_server(max_concurrent=4, max_batch_size=4, vlm_client=stub)

    async with Client(mcp_server, raise_exceptions=True) as c:
        result = await c.call_tool(
            "convert_batch",
            {
                "items": [{"format": "docx", "content_base64": b64}] * 4,
                "use_vlm": True,
            },
        )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["total"] == 4
    assert sc["succeeded"] == 4, sc["items"]
    for item in sc["items"]:
        assert item["groups_found"] == 10
        assert not any(w.startswith("vlm-render-failed:") for w in item["warnings"]), item[
            "warnings"
        ]
    assert stub.calls > 0, "the stub VLM client was never actually reached"
