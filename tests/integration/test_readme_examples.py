"""Anti-drift guard for ``examples/*.md`` (stage 7, README + demo spec §4;
excerpting introduced 2026-08-22 for the README overhaul).

Each ``examples/*.md`` file is a CURATED EXCERPT (≤200 lines, prefixed with
an HTML-comment attribution header) of a real ``convert()`` output — not
the full output verbatim anymore. Trimmed sections are marked inline with
a ``*(...something omitted for this excerpt...)*`` line; each span between
two markers is split further on blank lines into paragraph/table/code-fence
BLOCKS (never split mid-block — markdown blocks don't contain blank lines
internally), and every block is asserted to be a genuine, ORDER-PRESERVING
substring of a live ``convert()`` call. Block-level, not span-level: a real
run found that requiring a whole multi-paragraph span to match
contiguously false-failed on genuinely correct excerpts — small internal
edits below the resolution an explicit marker is worth adding for (a
tightened connector sentence) are real and expected. That's weaker than
the original "byte-for-byte identical" check this file used before
excerpting, but it still catches the real regression this guard exists
for: a chart-parsing change silently altering committed output (a table's
formatting shifts, a mermaid fence's data changes) without anyone
noticing — the whole point of showcasing REAL examples is that they stay
real, not that every line of the source document is present.

Each entry's ``(fmt, filename)`` is the same fixture cited in the example
file's own header comment — kept here as plain data, not re-parsed from the
header, so a header edit can't accidentally desync the test from what it
checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from refigure.api import Config
from refigure.docx import convert as docx_convert
from refigure.vlm.cache import FileCacheBackend
from refigure.xlsx import convert as xlsx_convert

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

_HEADER_END_MARKER = "-->\n\n"

# One full line, nothing else — matches every omission marker actually used
# across examples/*.md (verified live 2026-08-22, `grep` against all 5
# files before relying on this pattern).
_OMISSION_MARKER_RE = re.compile(r"^\*\(\.\.\..*omitted.*\.\.\.\)\*$", re.MULTILINE)

# A kept span between two markers can still contain its own small internal
# edits (a trimmed connector sentence, tightened spacing) below the
# resolution an explicit marker is worth adding for — real, found live
# 2026-08-22: requiring one multi-paragraph span to match CONTIGUOUSLY
# false-failed on genuinely correct excerpts. Splitting further on blank
# lines checks each paragraph/table/code-fence block on its own instead —
# markdown blocks never contain a blank line internally, so this can't
# fracture a table or a ```mermaid fence mid-block.
_BLOCK_SPLIT_RE = re.compile(r"\n{2,}")


def _trichinella_vlm_config() -> Config:
    """100%-cache-hit Config for the VLM-activation demo example (spec §6) —
    zero network calls: every one of this document's 27 VLM-eligible
    markers is already present in the committed FileCacheBackend fixture
    (see that fixture's own commit for the real call's cost/provenance)."""
    cache_path = _FIXTURES_DIR / "vlm-cache" / "efsa-trichinella-dashboard-guide.json"
    return Config(use_vlm=True, vlm_cache=FileCacheBackend(cache_path))


# (example filename, source fmt, source fixture filename, optional Config
# factory) — mirrors the table in the README's Demo section.
# swd2021-pie-chart.md added for the docx pie chart README Demo hero
# (2026-08-19). efsa-trichinella-vlm.md added for the VLM-activation demo
# hero — its Config factory is the only entry that isn't None, since every
# other example uses convert()'s own plain default.
_EXAMPLES: list[tuple[str, str, str, Callable[[], Config] | None]] = [
    ("hackair-native-charts.md", "docx", "hackair-d7.7-pilot-evaluation.docx", None),
    ("swd2018-combo.md", "docx", "swd2018-254-marine-litter-ia-annex.docx", None),
    ("govtech-xlsx-charts.md", "xlsx", "govtech-2025-charts.xlsx", None),
    ("swd2021-pie-chart.md", "docx", "swd2021-396-platform-work-ia.docx", None),
    (
        "efsa-trichinella-vlm.md",
        "docx",
        "efsa-trichinella-dashboard-guide.docx",
        _trichinella_vlm_config,
    ),
]


def _split_header(example_text: str, example_filename: str) -> str:
    """Strip the leading HTML-comment attribution header, return the body."""
    marker_idx = example_text.find(_HEADER_END_MARKER)
    assert marker_idx != -1, (
        f"{example_filename}: no {_HEADER_END_MARKER!r} header-end marker found — "
        "attribution header format changed?"
    )
    return example_text[marker_idx + len(_HEADER_END_MARKER) :]


def _kept_blocks(body: str) -> list[str]:
    """Split an excerpt's body on omission markers, dropping the marker
    lines themselves, then split each remaining span further on blank
    lines. Each returned block (a paragraph, a table, or a fenced code
    block) is real, kept content — checked below as an ordered substring
    of a live convert() call, never the marker text itself."""
    spans = [span.strip("\n") for span in _OMISSION_MARKER_RE.split(body)]
    blocks: list[str] = []
    for span in spans:
        blocks.extend(block for block in _BLOCK_SPLIT_RE.split(span) if block.strip())
    return blocks


@pytest.mark.parametrize("example_filename,fmt,fixture_filename,config_factory", _EXAMPLES)
def test_example_is_ordered_excerpt_of_live_convert(
    example_filename: str,
    fmt: str,
    fixture_filename: str,
    config_factory: Callable[[], Config] | None,
) -> None:
    fixture_path = _FIXTURES_DIR / fmt / fixture_filename
    if not fixture_path.exists():
        pytest.skip(f"fixture not present on disk: {fixture_path}")

    example_path = _EXAMPLES_DIR / example_filename
    committed_body = _split_header(example_path.read_text(encoding="utf-8"), example_filename)
    blocks = _kept_blocks(committed_body)
    assert blocks, f"examples/{example_filename}: no kept content found after splitting"

    convert = docx_convert if fmt == "docx" else xlsx_convert
    config = config_factory() if config_factory is not None else None
    live_markdown = convert(fixture_path, config=config).markdown

    cursor = 0
    for block in blocks:
        found_at = live_markdown.find(block, cursor)
        assert found_at != -1, (
            f"examples/{example_filename} has drifted from a live convert() call "
            f"on {fixture_filename} — a kept excerpt block is no longer a substring "
            "of the live output (in order), starting:\n"
            f"{block[:200]!r}\n"
            "Regenerate the excerpt (see the file's own header for provenance) "
            "and re-check README.md's example table/claims."
        )
        cursor = found_at + len(block)
