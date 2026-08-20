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


@pytest.mark.parametrize("example_filename,fmt,fixture_filename,config_factory", _EXAMPLES)
def test_example_matches_live_convert(
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

    convert = docx_convert if fmt == "docx" else xlsx_convert
    config = config_factory() if config_factory is not None else None
    live_markdown = convert(fixture_path, config=config).markdown

    assert committed_body == live_markdown, (
        f"examples/{example_filename} has drifted from a live convert() call "
        f"on {fixture_filename} — regenerate it (see the file's own header "
        "for provenance) and re-check README.md's example table/claims."
    )
