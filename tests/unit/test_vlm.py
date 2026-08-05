"""Unit tests for refigure.vlm (stage 4b).

Everything here that is not scenario-labelled "cache miss" runs fully
offline: no real HTTP client and no real ``soffice`` invocation is mocked
in — the whole point of the offline-only assertions below is that an
unmocked network call would hang/error, which is itself the proof that the
code path never reaches one. The one scenario that WOULD reach a real
render (a genuine cache miss) monkeypatches ``soffice`` availability to
``False`` so it degrades before ever touching the network, rather than
mocking the network directly.

``build_minimal_docx`` is reused from ``test_docx.py`` (hand-built via
``zipfile``, no python-docx dependency) rather than reinvented here.
"""

from __future__ import annotations

import pytest

from refigure import vlm
from refigure.api import Config
from refigure.vlm_cache import InMemoryCacheBackend

from .test_docx import build_minimal_docx

# --- shared fixture data ----------------------------------------------------

_IMAGE_ID = "0123456789ab"
_GROUP_ID = "abcdef012345"


# =============================================================================
# 1. Marker regex scanning
# =============================================================================


def test_image_marker_regex_matches_well_formed_marker_and_extracts_id() -> None:
    text = f"> [Image, docx media {_IMAGE_ID} — raster content not analyzed]"
    m = vlm._DOCX_IMAGE_MARKER_RE.search(text)
    assert m is not None
    assert m.group("id") == _IMAGE_ID


def test_image_marker_regex_does_not_match_group_marker_text() -> None:
    text = f"> [Figure, docx group {_GROUP_ID} — composite content not analyzed]"
    assert vlm._DOCX_IMAGE_MARKER_RE.search(text) is None


def test_group_marker_regex_matches_well_formed_marker_and_extracts_id_and_witness() -> None:
    text = (
        f"> [Figure, docx group {_GROUP_ID} — composite content not analyzed]\n"
        "> captions: Quarterly revenue by region"
    )
    m = vlm._DOCX_GROUP_MARKER_RE.search(text)
    assert m is not None
    assert m.group("id") == _GROUP_ID
    assert m.group("witness") == "Quarterly revenue by region"


def test_group_marker_regex_requires_the_captions_line_immediately_after() -> None:
    # The marker line alone, with no "> captions: ..." line following it,
    # must not match — the grammar is a two-line unit, not just the first line.
    text = f"> [Figure, docx group {_GROUP_ID} — composite content not analyzed]"
    assert vlm._DOCX_GROUP_MARKER_RE.search(text) is None


def test_group_marker_regex_requires_marker_line_before_a_bare_captions_line() -> None:
    # A "> captions: ..." line with no preceding marker line must not match
    # either — both halves of the two-line unit are required.
    text = "> captions: Quarterly revenue by region"
    assert vlm._DOCX_GROUP_MARKER_RE.search(text) is None


# =============================================================================
# 2. sanitize_vlm_markdown
# =============================================================================


def test_heading_is_demoted_to_bold_text() -> None:
    out = vlm.sanitize_vlm_markdown("## Regional Sales\n\nSome prose.")
    assert "## Regional Sales" not in out
    assert "**Regional Sales**" in out


def test_heading_looking_line_inside_code_fence_is_left_untouched() -> None:
    md = "```\n# not a heading, a shell comment\n```"
    out = vlm.sanitize_vlm_markdown(md)
    assert out == md


@pytest.mark.mermaid  # real mermaidx render via chart_render.mermaid_renders
def test_valid_mermaid_fence_survives_unchanged() -> None:
    md = '```mermaid\nflowchart TD\nA["x"] --> B["y"]\n```'
    out = vlm.sanitize_vlm_markdown(md)
    assert out == md
    assert "```mermaid" in out


@pytest.mark.mermaid  # real mermaidx render via chart_render.mermaid_renders
def test_invalid_mermaid_fence_degrades_to_text_fence_preserving_content() -> None:
    garbage = "this is not mermaid at all !!! ###"
    md = f"```mermaid\n{garbage}\n```"
    out = vlm.sanitize_vlm_markdown(md)
    assert "```mermaid" not in out
    assert "```text" in out
    assert garbage in out


@pytest.mark.mermaid  # real mermaidx render via chart_render.mermaid_renders
def test_sanitize_vlm_markdown_is_idempotent() -> None:
    md = (
        "## A heading\n\n"
        "Some prose.\n\n"
        "```mermaid\ngarbage that will not render !!!\n```\n\n"
        '```mermaid\nflowchart TD\nA["x"] --> B["y"]\n```'
    )
    once = vlm.sanitize_vlm_markdown(md)
    twice = vlm.sanitize_vlm_markdown(once)
    assert once == twice


# =============================================================================
# 3. witness_defects
# =============================================================================


def test_witness_defects_empty_witness_returns_empty_list_unconditionally() -> None:
    assert vlm.witness_defects("", "anything at all", "obj1", min_recall=0.80) == []


def test_witness_defects_whitespace_only_witness_returns_empty_list() -> None:
    assert vlm.witness_defects("   \n\t  ", "anything at all", "obj1", min_recall=0.80) == []


def test_witness_defects_full_recall_produces_no_recall_defect() -> None:
    witness = "sales by region"
    markdown = "A chart describing sales figures broken down by region."
    defects = vlm.witness_defects(witness, markdown, "obj1", min_recall=0.80)
    assert not any(d.startswith("figure-witness-recall") for d in defects)


def test_witness_defects_low_recall_produces_recall_defect_naming_the_id() -> None:
    witness = "elephant zebra giraffe"
    markdown = "A bar chart with three categories and no animals mentioned."
    defects = vlm.witness_defects(witness, markdown, "obj1", min_recall=0.80)
    recall_defects = [d for d in defects if d.startswith("figure-witness-recall")]
    assert len(recall_defects) == 1
    assert "obj1" in recall_defects[0]


def test_witness_defects_numeric_missing_from_markdown_triggers_defect() -> None:
    witness = "revenue grew to 42 percent"
    markdown = "A chart showing revenue growth over time."
    defects = vlm.witness_defects(witness, markdown, "obj1", min_recall=0.0)
    numeric_defects = [d for d in defects if d.startswith("figure-witness-numeric")]
    assert len(numeric_defects) == 1
    assert "42" in numeric_defects[0]
    assert "obj1" in numeric_defects[0]


def test_witness_defects_numeric_extra_in_markdown_only_does_not_trigger_defect() -> None:
    # One-sided by design: the VLM is allowed to read additional numbers off
    # the chart that the caption never mentioned.
    witness = "revenue over time"
    markdown = "A chart showing revenue growing from 10 to 42 percent."
    defects = vlm.witness_defects(witness, markdown, "obj1", min_recall=0.0)
    assert not any(d.startswith("figure-witness-numeric") for d in defects)


# =============================================================================
# 4. token_recall / numeric_counter / format_missing_side
# =============================================================================


def test_token_recall_reference_with_no_letters_returns_one() -> None:
    assert vlm.token_recall("42 100 !!!", "completely unrelated text") == 1.0


def test_token_recall_is_case_insensitive() -> None:
    assert vlm.token_recall("Revenue Growth", "the revenue growth chart") == 1.0


def test_token_recall_partial_overlap() -> None:
    assert vlm.token_recall("alpha beta", "only alpha appears here") == 0.5


def test_numeric_counter_counts_repeated_occurrences_as_a_multiset() -> None:
    counts = vlm.numeric_counter("10 apples, 10 oranges, and 20 pears")
    assert counts["10"] == 2
    assert counts["20"] == 1


def test_format_missing_side_sorted_numerically_not_lexicographically() -> None:
    nums = vlm.numeric_counter("100 20 3")
    other = vlm.numeric_counter("")
    assert vlm.format_missing_side(nums, other) == "3,20,100"


def test_format_missing_side_no_missing_numbers_returns_none_literal() -> None:
    nums = vlm.numeric_counter("10 20")
    other = vlm.numeric_counter("10 20 30")
    assert vlm.format_missing_side(nums, other) == "none"


def test_format_missing_side_is_capped_at_the_module_constant() -> None:
    cap = vlm._NUMERIC_DIVERGENCE_TOKEN_CAP
    values = list(range(cap + 5))
    nums = vlm.numeric_counter(" ".join(str(v) for v in values))
    other = vlm.numeric_counter("")
    out = vlm.format_missing_side(nums, other)
    shown = out.split("…")[0].split(",")
    assert len(shown) == cap
    assert out.endswith(f"…+{len(values) - cap}")


# =============================================================================
# 5. enhance_docx_markdown — offline cache-hit-only path
# =============================================================================


def _markdown_with_markers(image_id: str, group_id: str, witness: str) -> str:
    return (
        "Intro paragraph.\n\n"
        f"> [Image, docx media {image_id} — raster content not analyzed]\n\n"
        "Some more prose in between.\n\n"
        f"> [Figure, docx group {group_id} — composite content not analyzed]\n"
        f"> captions: {witness}\n\n"
        "Trailing paragraph."
    )


def test_enhance_docx_markdown_cache_hit_replaces_both_markers_offline() -> None:
    docx_bytes = build_minimal_docx(["A paragraph of unrelated document text."])
    witness = "sales region"  # both words present in the cached markdown below
    markdown = _markdown_with_markers(_IMAGE_ID, _GROUP_ID, witness)

    cache = InMemoryCacheBackend()
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A photo of a smiling person."})
    cache.set(
        _GROUP_ID,
        {"model": "test-model", "markdown": "A bar chart of sales by region."},
    )
    config = Config(use_vlm=True, vlm_cache=cache)

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is True
    # Bare markers are gone.
    assert vlm._DOCX_IMAGE_MARKER_RE.search(new_markdown) is None
    assert vlm._DOCX_GROUP_MARKER_RE.search(new_markdown) is None
    # Both injected blocks are present: open marker, sanitized body, close marker.
    expected_image_block = vlm._render_injected_docx_image(
        _IMAGE_ID, "test-model", "A photo of a smiling person."
    )
    expected_group_block = vlm._render_injected_docx_group(
        _GROUP_ID, "test-model", "A bar chart of sales by region."
    )
    assert expected_image_block in new_markdown
    assert expected_group_block in new_markdown
    # Witness matches well -> no recall warning for this group.
    assert not any(w.startswith("figure-witness-recall") for w in warnings)


def test_enhance_docx_markdown_cache_hit_mismatched_witness_produces_warning() -> None:
    docx_bytes = build_minimal_docx(["A paragraph of unrelated document text."])
    witness = "elephant zebra giraffe"  # none of these appear in the cached markdown
    markdown = _markdown_with_markers(_IMAGE_ID, _GROUP_ID, witness)

    cache = InMemoryCacheBackend()
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A photo of a smiling person."})
    cache.set(
        _GROUP_ID,
        {"model": "test-model", "markdown": "A bar chart of sales by region."},
    )
    config = Config(use_vlm=True, vlm_cache=cache)

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is True
    recall_warnings = [w for w in warnings if w.startswith("figure-witness-recall")]
    assert len(recall_warnings) == 1
    assert _GROUP_ID in recall_warnings[0]


# =============================================================================
# 6. enhance_docx_markdown — no markers present
# =============================================================================


def test_enhance_docx_markdown_without_markers_returns_input_unchanged() -> None:
    docx_bytes = build_minimal_docx(["Just some plain text, no figures at all."])
    markdown = "# Title\n\nJust some plain prose with no VLM markers whatsoever."
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    result = vlm.enhance_docx_markdown(markdown, docx_bytes, config=config)

    assert result == (markdown, False, [])
    assert result[0] is markdown


# =============================================================================
# 7. enhance_docx_markdown — cache miss + soffice unavailable (graceful degrade)
# =============================================================================


def test_enhance_docx_markdown_cache_miss_soffice_unavailable_leaves_marker_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_soffice_available", lambda: False)

    docx_bytes = build_minimal_docx(["A paragraph of unrelated document text."])
    witness = "quarterly revenue"
    markdown = (
        "Intro paragraph.\n\n"
        f"> [Figure, docx group {_GROUP_ID} — composite content not analyzed]\n"
        f"> captions: {witness}\n\n"
        "Trailing paragraph."
    )
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []
