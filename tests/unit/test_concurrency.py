"""Concurrency stress test — relevant to the planned v2 MCP server, which
may serve multiple conversion requests concurrently (threads or asyncio's
default thread-pool executor for sync code). Neither docx.py nor xlsx.py
hold module-level mutable state that a call writes to (placed_ids/warnings/
etc. are all function-local); the one shared piece of state is
chart_render's lru_cache'd _warn_missing_mermaidx, which is read-only from
convert()'s perspective and documented thread-safe by lru_cache itself —
this test verifies that holds in practice, not just in theory.

Root-caused 2026-08-05 (PR #9 CI hit a ``Fatal Python error: Segmentation
fault`` under ``pytest --cov``; at lower concurrency, the SAME root cause
also produced a silent WRONG result — one call returning another call's
outcome, not a crash): openpyxl's own ``xml/functions.py`` constructs ONE
module-level ``lxml.etree.XMLParser()`` (``safe_parser``) at import time
and reuses it via ``functools.partial`` for every internal XML parse call
(rels/workbook/worksheet), from every thread — confirmed by reading
openpyxl's source, and the CI crash's own traceback pinpoints the exact
line (``relationship.py``'s ``get_dependents``, the ``fromstring(src)``
call through that shared parser). refigure's OWN lxml usage
(``docx.py``/``docx_groups.py``/``xlsx_charts.py``) is unaffected — it
never passes an explicit ``parser=``, so lxml replicates its default
parser per-thread automatically (lxml's own documented safe pattern, see
its FAQ on thread safety).

This is a real correctness gap reachable by any concurrent caller of
``refigure.xlsx.convert()`` today, not just a future v2 MCP server — so
it's fixed at the source, not papered over here: ``xlsx.py`` now
serializes ``openpyxl.load_workbook()`` behind a module-level
``threading.Lock``. Worker counts below are back at their original,
deliberately aggressive values ON PURPOSE — proving the lock holds under
real stress is the whole point, not avoiding the stress. Full writeup:
``project_openpyxl_concurrent_parser_fragility`` memory.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import openpyxl
import pytest

import refigure.docx as docx
import refigure.xlsx as xlsx
from refigure.api import CorruptArchiveError, UnsupportedFormatError

from .test_docx import build_minimal_docx


def _make_xlsx(cell_value: str) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append([cell_value])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_concurrent_docx_calls_all_return_correct_independent_results() -> None:
    # Each input is distinct so a cross-call mix-up (thread A's result
    # leaking into thread B's) would show up as a content mismatch, not
    # just a crash.
    inputs = [build_minimal_docx([f"Document number {i}"]) for i in range(30)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(docx.convert, inputs))

    for i, result in enumerate(results):
        assert f"Document number {i}" in result.markdown
        assert result.warnings == []


def test_concurrent_xlsx_calls_all_return_correct_independent_results() -> None:
    inputs = [_make_xlsx(f"cell-value-{i}") for i in range(30)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(xlsx.convert, inputs))

    for i, result in enumerate(results):
        assert f"cell-value-{i}" in result.markdown
        assert result.warnings == []


def test_concurrent_mixed_valid_and_invalid_calls_do_not_interfere() -> None:
    """The adversarial version of the above: valid and invalid inputs,
    interleaved across threads, mixed formats — a shared-state bug would
    most likely show up as an exception on the wrong input, or a success
    where a failure was expected (or vice versa)."""
    valid_docx = build_minimal_docx(["stable"])
    valid_xlsx = _make_xlsx("stable")
    empty = b""
    truncated = valid_docx[: len(valid_docx) // 2]

    def call_docx(source: bytes) -> str:
        try:
            result = docx.convert(source)
            return f"ok:{result.markdown.strip()}"
        except (UnsupportedFormatError, CorruptArchiveError) as exc:
            return f"typed-error:{type(exc).__name__}"

    def call_xlsx(source: bytes) -> str:
        try:
            result = xlsx.convert(source)
            return f"ok:{result.markdown.strip()}"
        except (UnsupportedFormatError, CorruptArchiveError) as exc:
            return f"typed-error:{type(exc).__name__}"

    tasks = []
    for _ in range(15):
        tasks.append((call_docx, valid_docx, "ok:stable"))
        tasks.append((call_docx, valid_xlsx, "typed-error:UnsupportedFormatError"))
        tasks.append((call_docx, empty, "typed-error:CorruptArchiveError"))
        tasks.append((call_docx, truncated, "typed-error:CorruptArchiveError"))
        tasks.append((call_xlsx, valid_xlsx, "ok:## Sheet\n\n| stable |\n| --- |"))
        tasks.append((call_xlsx, valid_docx, "typed-error:UnsupportedFormatError"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fn, data) for fn, data, _expected in tasks]
        actual = [f.result() for f in futures]

    for (_, _, expected), got in zip(tasks, actual, strict=True):
        assert got == expected, f"expected {expected!r}, got {got!r}"


@pytest.mark.parametrize("run", range(3))
def test_concurrent_stress_is_stable_across_repeated_runs(run: int) -> None:
    """Repeat the mixed stress test a few times (pytest-repeat-style) — a
    race condition can pass on one run and fail on the next."""
    valid = build_minimal_docx([f"run {run}"])
    invalid = b"not a zip"

    def call(source: bytes) -> bool:
        try:
            docx.convert(source)
            return True
        except (UnsupportedFormatError, CorruptArchiveError):
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(call, [valid, invalid] * 20))

    assert results == [True, False] * 20
