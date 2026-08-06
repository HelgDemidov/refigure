"""Anti-drift guard for ``examples/*.md`` (stage 7, README + demo spec §4).

Each ``examples/*.md`` file is a real, unmodified ``convert()`` output
(prefixed with an HTML-comment attribution header) committed so README.md
can link to it as a full real example, marker-style. Without this test,
that committed file could silently drift from what ``convert()`` actually
produces today (a chart-parsing fix changes a table's formatting, a new
warning gets added, etc.) and nobody would notice — the whole point of
linking a REAL example is that it stays real.

Each entry's ``(fmt, filename)`` is the same fixture cited in the example
file's own header comment — kept here as plain data, not re-parsed from the
header, so a header edit can't accidentally desync the test from what it
checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refigure.docx import convert as docx_convert
from refigure.xlsx import convert as xlsx_convert

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

_HEADER_END_MARKER = "-->\n\n"

# (example filename, source fmt, source fixture filename) — mirrors the
# table in docs/readme/readme-and-demo/readme-and-demo-2026-08-06.md §4.
_EXAMPLES = [
    ("hackair-native-charts.md", "docx", "hackair-d7.7-pilot-evaluation.docx"),
    ("swd2018-combo.md", "docx", "swd2018-254-marine-litter-ia-annex.docx"),
    ("govtech-xlsx-charts.md", "xlsx", "govtech-2025-charts.xlsx"),
]


def _split_header(example_text: str, example_filename: str) -> str:
    """Strip the leading HTML-comment attribution header, return the body."""
    marker_idx = example_text.find(_HEADER_END_MARKER)
    assert marker_idx != -1, (
        f"{example_filename}: no {_HEADER_END_MARKER!r} header-end marker found — "
        "attribution header format changed?"
    )
    return example_text[marker_idx + len(_HEADER_END_MARKER) :]


@pytest.mark.parametrize("example_filename,fmt,fixture_filename", _EXAMPLES)
def test_example_matches_live_convert(
    example_filename: str, fmt: str, fixture_filename: str
) -> None:
    fixture_path = _FIXTURES_DIR / fmt / fixture_filename
    if not fixture_path.exists():
        pytest.skip(f"fixture not present on disk: {fixture_path}")

    example_path = _EXAMPLES_DIR / example_filename
    committed_body = _split_header(example_path.read_text(encoding="utf-8"), example_filename)

    convert = docx_convert if fmt == "docx" else xlsx_convert
    live_markdown = convert(fixture_path).markdown

    assert committed_body == live_markdown, (
        f"examples/{example_filename} has drifted from a live convert() call "
        f"on {fixture_filename} — regenerate it (see the file's own header "
        "for provenance) and re-check README.md's example table/claims."
    )
