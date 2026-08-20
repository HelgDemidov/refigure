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

import base64
import hashlib
import io
import subprocess
import zipfile

import mermaidx
import pytest

from refigure import vlm
from refigure.api import Config
from refigure.core import chart_render
from refigure.vlm.cache import InMemoryCacheBackend

from ..docx.test_docx import build_minimal_docx

# --- shared fixture data ----------------------------------------------------

_IMAGE_ID = "0123456789ab"
_GROUP_ID = "abcdef012345"

_CLEAN_VERDICT = "hallucination: no\nmermaid_fit: n/a\nlanguage: yes"


class _ScriptedVlmClient:
    """Minimal VlmClient stand-in for judge_defects/enhance_docx_markdown
    tests: returns ``verdict`` for a judge-shaped prompt (identified by the
    literal "hallucination:" instruction line JUDGE_PROMPT_TEMPLATE always
    contains, which a real figure description never does) and
    ``description`` for anything else — lets one fake client serve both the
    generation call and the judge call in the same test. Records every
    prompt it receives so tests can assert exact call counts."""

    def __init__(self, description: str = "A chart.", verdict: str = _CLEAN_VERDICT) -> None:
        self.description = description
        self.verdict = verdict
        self.prompts: list[str] = []

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        self.prompts.append(prompt)
        return self.verdict if "hallucination:" in prompt else self.description


class _RaisingVlmClient:
    """VlmClient stand-in that always fails — for judge_defects's
    never-abort-the-conversion degradation path."""

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        raise RuntimeError("network exploded")


class _NonConformingVlmClient:
    """VlmClient stand-in that returns something other than a proper
    non-empty ``str`` WITHOUT raising — security audit 2026-08-07, finding
    #9: a real client (OpenRouterClient itself, on an API response with
    ``content: null``) can legitimately do this. The Protocol's type hint
    is not enforced at runtime for an external implementation."""

    def __init__(self, value: object) -> None:
        self.value = value

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        return self.value  # type: ignore[return-value]


class _PerModelJudgeClient:
    """VlmClient stand-in whose JUDGE verdict depends on WHICH model is
    asked — _ScriptedVlmClient's single fixed verdict can't exercise true
    panel UNION behavior (two judges genuinely disagreeing). Generation-
    shaped prompts (no "hallucination:" line) always get the same fixed
    description, regardless of model. Records (model, prompt) per call."""

    def __init__(self, description: str, verdicts_by_model: dict[str, str]) -> None:
        self.description = description
        self.verdicts_by_model = verdicts_by_model
        self.calls: list[tuple[str, str]] = []

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        self.calls.append((model, prompt))
        if "hallucination:" in prompt:
            return self.verdicts_by_model[model]
        return self.description


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


# --- security audit 2026-08-07, finding #14: unterminated fence -----------


def test_unterminated_generic_fence_is_closed() -> None:
    # Realistically triggered by ordinary max_tokens truncation mid-fence,
    # not just malice. Plain, non-mermaid fence — no real render involved.
    md = "Some prose.\n\n```\nsome truncated code that never closed"
    out = vlm.sanitize_vlm_markdown(md)
    assert out.count("```") % 2 == 0
    assert out.endswith("```")


@pytest.mark.mermaid  # real mermaidx render via chart_render.mermaid_renders
def test_unterminated_mermaid_fence_is_closed_then_gated_by_real_render() -> None:
    # _balance_mermaid_fences must run BEFORE _gate_mermaid_fences: a
    # truncated-but-invalid mermaid block should still get a real-render
    # verdict (degrading to ```text) rather than being skipped entirely
    # for lacking a close fence.
    md = "```mermaid\nthis is not valid mermaid at all !!! ###"
    out = vlm.sanitize_vlm_markdown(md)
    assert "```mermaid" not in out
    assert "```text" in out


def test_unterminated_fence_balancing_is_idempotent() -> None:
    md = "```\nunterminated"
    once = vlm.sanitize_vlm_markdown(md)
    twice = vlm.sanitize_vlm_markdown(once)
    assert once == twice


# --- security audit 2026-08-07, finding #12: marker-grammar lookalikes ----


def test_bare_image_marker_lookalike_is_neutralized() -> None:
    lookalike = f"> [Image, docx media {_IMAGE_ID} — raster content not analyzed]"
    out = vlm.sanitize_vlm_markdown(lookalike)
    assert vlm._DOCX_IMAGE_MARKER_RE.search(out) is None
    # invisible in rendered markdown — only the zero-width space differs
    assert out.replace("​", "") == lookalike


def test_injection_terminator_lookalike_is_neutralized() -> None:
    lookalike = f"> [/VLM interpretation docx media {_IMAGE_ID}]"
    out = vlm.sanitize_vlm_markdown(lookalike)
    assert not out.startswith(vlm._INJECTION_END_PREFIX)
    assert vlm._INJECTION_END_PREFIX not in out


def test_marker_lookalike_neutralization_is_idempotent() -> None:
    md = f"> [Image, docx media {_IMAGE_ID} — raster content not analyzed]"
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


# --- vlm-activation spec §1: Config.strict + soffice-missing boundary -----


def test_enhance_docx_markdown_strict_true_soffice_unavailable_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONE case ``strict=True`` actually changes: a composite group
    that needs ``soffice`` to render, and ``soffice`` isn't installed."""
    monkeypatch.setattr(vlm, "_soffice_available", lambda: False)

    docx_bytes = build_minimal_docx(["A paragraph of unrelated document text."])
    markdown = _group_only_markdown(_GROUP_ID, "quarterly revenue")
    config = Config(use_vlm=True, strict=True, vlm_cache=InMemoryCacheBackend())

    with pytest.raises(vlm.MissingOptionalDependencyError, match="soffice"):
        vlm.enhance_docx_markdown(markdown, docx_bytes, config=config)


def test_enhance_docx_markdown_strict_false_soffice_unavailable_degrades_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``strict=False`` (the default) on the exact same input
    must behave EXACTLY as before this feature — degrade, not raise."""
    monkeypatch.setattr(vlm, "_soffice_available", lambda: False)

    docx_bytes = build_minimal_docx(["A paragraph of unrelated document text."])
    markdown = _group_only_markdown(_GROUP_ID, "quarterly revenue")
    config = Config(use_vlm=True, strict=False, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []


def test_enhance_docx_markdown_strict_true_other_vlm_failure_still_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``strict=True`` does NOT become a general "raise on any VLM failure"
    switch — a non-soffice failure (here: the VlmClient itself raising)
    stays a degrade, proving the boundary is exactly "soffice missing", not
    "any failure while strict"."""
    monkeypatch.setattr(vlm, "_render_docx_group", lambda *a, **k: "data:image/jpeg;base64,x")
    docx_bytes = build_minimal_docx(["text"])
    markdown = _group_only_markdown(_GROUP_ID, "quarterly revenue")
    config = Config(
        use_vlm=True,
        strict=True,
        vlm_cache=InMemoryCacheBackend(),
        vlm_client=_RaisingVlmClient(),
    )

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []


# =============================================================================
# 8. judge_defects
# =============================================================================


def test_judge_defects_clean_response_produces_no_defects() -> None:
    client = _ScriptedVlmClient(verdict=_CLEAN_VERDICT)
    assert vlm.judge_defects("uri", "A chart.", client=client, model="m") == []


def test_judge_defects_hallucination_yes_flags_it() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: yes\nmermaid_fit: n/a\nlanguage: yes")
    defects = vlm.judge_defects("uri", "A chart.", client=client, model="m")
    assert defects == ["vlm-judge-hallucination"]


def test_judge_defects_mermaid_fit_no_flags_it() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: no\nmermaid_fit: no\nlanguage: yes")
    defects = vlm.judge_defects("uri", "A chart.", client=client, model="m")
    assert defects == ["vlm-judge-mermaid"]


def test_judge_defects_mermaid_fit_yes_does_not_flag() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: no\nmermaid_fit: yes\nlanguage: yes")
    assert vlm.judge_defects("uri", "A chart.", client=client, model="m") == []


def test_judge_defects_language_no_flags_it() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: no\nmermaid_fit: n/a\nlanguage: no")
    defects = vlm.judge_defects("uri", "A chart.", client=client, model="m")
    assert defects == ["vlm-judge-language"]


def test_judge_defects_all_three_flagged_and_ordered_hallucination_mermaid_language() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: yes\nmermaid_fit: no\nlanguage: no")
    defects = vlm.judge_defects("uri", "A chart.", client=client, model="m")
    assert defects == ["vlm-judge-hallucination", "vlm-judge-mermaid", "vlm-judge-language"]


def test_judge_defects_parsing_is_case_insensitive_and_tolerates_surrounding_text() -> None:
    client = _ScriptedVlmClient(
        verdict="Sure, here goes:\nHallucination: YES\nMermaid_Fit: N/A\nLanguage: Yes\nThanks!"
    )
    defects = vlm.judge_defects("uri", "A chart.", client=client, model="m")
    assert defects == ["vlm-judge-hallucination"]


def test_judge_defects_unparseable_response_returns_empty_list() -> None:
    client = _ScriptedVlmClient(verdict="I refuse to answer in that format.")
    assert vlm.judge_defects("uri", "A chart.", client=client, model="m") == []


def test_judge_defects_missing_one_of_the_three_fields_returns_empty_list() -> None:
    client = _ScriptedVlmClient(verdict="hallucination: no\nlanguage: yes")  # mermaid_fit missing
    assert vlm.judge_defects("uri", "A chart.", client=client, model="m") == []


def test_judge_defects_client_exception_returns_empty_list_not_raised() -> None:
    assert vlm.judge_defects("uri", "A chart.", client=_RaisingVlmClient(), model="m") == []


@pytest.mark.parametrize("value", [None, 123, b"bytes-not-str", ""])
def test_judge_defects_non_string_response_returns_empty_list_not_raised(value: object) -> None:
    # Regression for finding #9: a None (or other non-str) send() return
    # used to reach re.finditer(verdict) unguarded -> TypeError, crashing
    # the whole conversion, not just this one judge call.
    client = _NonConformingVlmClient(value)
    assert vlm.judge_defects("uri", "A chart.", client=client, model="m") == []


def test_judge_defects_embeds_the_already_generated_response_in_the_prompt() -> None:
    client = _ScriptedVlmClient(verdict=_CLEAN_VERDICT)
    vlm.judge_defects("uri", "A UNIQUE description marker XYZ123.", client=client, model="m")
    assert "A UNIQUE description marker XYZ123." in client.prompts[0]


# =============================================================================
# 9. enhance_docx_markdown — vlm_verify wiring
# =============================================================================


def _image_only_markdown(image_id: str) -> str:
    return f"> [Image, docx media {image_id} — raster content not analyzed]"


def _group_only_markdown(group_id: str, witness: str) -> str:
    return (
        f"> [Figure, docx group {group_id} — composite content not analyzed]\n> captions: {witness}"
    )


def test_enhance_docx_markdown_vlm_verify_false_never_calls_judge_even_on_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _ScriptedVlmClient()
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True, vlm_verify=False, vlm_cache=InMemoryCacheBackend(), vlm_client=client
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    assert len(client.prompts) == 1  # only the generation call, no judge call
    assert not any(w.startswith("vlm-judge-") for w in warnings)


def test_enhance_docx_markdown_vlm_verify_true_cache_miss_calls_judge_once_per_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit solo mode: this test is about the cache-miss call-count
    # contract itself, not panel dispatch (covered separately, see the
    # "panel mode" section below) — pinning to solo keeps the call count
    # deterministic regardless of Config's own default.
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _ScriptedVlmClient(verdict="hallucination: yes\nmermaid_fit: n/a\nlanguage: yes")
    cache = InMemoryCacheBackend()
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_judge_mode="solo",
        vlm_judge_model="test-judge-model",
        vlm_cache=cache,
        vlm_client=client,
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    assert len(client.prompts) == 2  # generation + judge
    assert warnings == [f"vlm-judge-hallucination: {_IMAGE_ID}"]
    cached_entry = cache.get(_IMAGE_ID)
    assert cached_entry is not None
    assert cached_entry["judge_verdict"] == ["vlm-judge-hallucination"]


def test_enhance_docx_markdown_vlm_verify_true_full_cache_hit_needs_no_client_at_all() -> None:
    cache = InMemoryCacheBackend()
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A chart.", "judge_verdict": []})
    # No vlm_client, no vlm_api_key: a fully-verified cache hit must never
    # construct a real client — proves the offline guarantee holds even
    # with vlm_verify=True, not just vlm_verify=False.
    config = Config(use_vlm=True, vlm_verify=True, vlm_cache=cache)
    docx_bytes = build_minimal_docx(["text"])

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    assert warnings == []


def test_enhance_docx_markdown_vlm_verify_true_partial_cache_hit_computes_judge_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit solo mode — same rationale as the cache-miss test above.
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _ScriptedVlmClient(verdict="hallucination: no\nmermaid_fit: no\nlanguage: yes")
    cache = InMemoryCacheBackend()
    # Pre-existing entry with no judge_verdict key at all — a cache written
    # before vlm_verify ever existed.
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A chart."})
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_judge_mode="solo",
        vlm_judge_model="test-judge-model",
        vlm_cache=cache,
        vlm_client=client,
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    assert len(client.prompts) == 1  # ONLY the judge call — no re-generation
    assert "A chart." in client.prompts[0]  # judges the CACHED description, not a new one
    assert warnings == [f"vlm-judge-mermaid: {_IMAGE_ID}"]
    updated_entry = cache.get(_IMAGE_ID)
    assert updated_entry is not None
    assert updated_entry["judge_verdict"] == ["vlm-judge-mermaid"]


def test_enhance_docx_markdown_vlm_verify_true_group_marker_uses_judge_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """judge_defects applies to GROUP markers too — unlike witness_defects,
    which is group-only by construction, judge_defects has no such
    restriction (see its own docstring)."""
    monkeypatch.setattr(vlm, "_render_docx_group", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _ScriptedVlmClient(
        description="A bar chart of sales by region.", verdict=_CLEAN_VERDICT
    )
    docx_bytes = build_minimal_docx(["text"])
    cache = InMemoryCacheBackend()
    config = Config(use_vlm=True, vlm_verify=True, vlm_cache=cache, vlm_client=client)

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _group_only_markdown(_GROUP_ID, "sales region"), docx_bytes, config=config
    )

    assert vlm_used is True
    assert warnings == []  # clean witness match + clean judge verdict
    cached_entry = cache.get(_GROUP_ID)
    assert cached_entry is not None
    assert cached_entry["judge_verdict"] == []


def test_enhance_docx_markdown_vlm_verify_true_partial_cache_hit_image_unavailable_skips_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: None)  # source changed/media gone
    client = _ScriptedVlmClient()
    cache = InMemoryCacheBackend()
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A chart."})
    docx_bytes = build_minimal_docx(["text"])
    config = Config(use_vlm=True, vlm_verify=True, vlm_cache=cache, vlm_client=client)

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True  # description still injected from cache
    assert warnings == []  # judge upgrade silently skipped, no crash
    assert len(client.prompts) == 0
    updated_entry = cache.get(_IMAGE_ID)
    assert updated_entry is not None
    assert "judge_verdict" not in updated_entry  # left untouched, not force-set


def test_enhance_docx_markdown_default_judge_mode_is_panel_calls_both_default_judges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config()'s own default (vlm_judge_mode="panel", vlm_judge_panel =
    (gemini, claude-haiku)) is exercised WITHOUT the caller setting
    anything explicitly — proves the actual default, not just that panel
    mode works when asked for."""
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _PerModelJudgeClient(
        description="A chart.",
        verdicts_by_model={
            "google/gemini-3-flash-preview": _CLEAN_VERDICT,
            "anthropic/claude-haiku-4.5": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes",
        },
    )
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True, vlm_verify=True, vlm_cache=InMemoryCacheBackend(), vlm_client=client
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    judge_models = [m for m, p in client.calls if "hallucination:" in p]
    assert judge_models == ["google/gemini-3-flash-preview", "anthropic/claude-haiku-4.5"]
    assert warnings == [f"vlm-judge-hallucination: {_IMAGE_ID}"]


def test_enhance_docx_markdown_panel_mode_partial_cache_hit_unions_both_judges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _PerModelJudgeClient(
        description="ignored — cache already has a description",
        verdicts_by_model={
            "judge-a": "hallucination: no\nmermaid_fit: no\nlanguage: yes",
            "judge-b": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes",
        },
    )
    cache = InMemoryCacheBackend()
    cache.set(_IMAGE_ID, {"model": "test-model", "markdown": "A chart."})  # pre-vlm_verify entry
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_judge_mode="panel",
        vlm_judge_panel=("judge-a", "judge-b"),
        vlm_cache=cache,
        vlm_client=client,
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    # judge-a alone would only flag mermaid, judge-b alone would only flag
    # hallucination — UNION means both end up in the final warnings.
    assert set(warnings) == {
        f"vlm-judge-hallucination: {_IMAGE_ID}",
        f"vlm-judge-mermaid: {_IMAGE_ID}",
    }
    updated_entry = cache.get(_IMAGE_ID)
    assert updated_entry is not None
    judge_verdict = updated_entry["judge_verdict"]
    assert isinstance(judge_verdict, list)
    assert set(judge_verdict) == {"vlm-judge-hallucination", "vlm-judge-mermaid"}


# =============================================================================
# 10. _judge_with_config — solo/panel dispatch (direct, no marker scanning)
# =============================================================================


def test_judge_with_config_solo_mode_calls_only_vlm_judge_model() -> None:
    client = _PerModelJudgeClient(
        description="d",
        verdicts_by_model={"judge-a": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes"},
    )
    config = Config(vlm_judge_mode="solo", vlm_judge_model="judge-a")

    defects = vlm._judge_with_config("uri", "d", config, lambda: client)

    assert defects == ["vlm-judge-hallucination"]
    assert [m for m, _ in client.calls] == ["judge-a"]


def test_judge_with_config_panel_mode_calls_both_models_in_declared_order() -> None:
    client = _PerModelJudgeClient(
        description="d", verdicts_by_model={"judge-a": _CLEAN_VERDICT, "judge-b": _CLEAN_VERDICT}
    )
    config = Config(vlm_judge_mode="panel", vlm_judge_panel=("judge-a", "judge-b"))

    vlm._judge_with_config("uri", "d", config, lambda: client)

    assert [m for m, _ in client.calls] == ["judge-a", "judge-b"]


def test_judge_with_config_panel_unions_distinct_defects_from_each_judge() -> None:
    client = _PerModelJudgeClient(
        description="d",
        verdicts_by_model={
            "judge-a": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes",
            "judge-b": "hallucination: no\nmermaid_fit: no\nlanguage: yes",
        },
    )
    config = Config(vlm_judge_mode="panel", vlm_judge_panel=("judge-a", "judge-b"))

    defects = vlm._judge_with_config("uri", "d", config, lambda: client)

    assert defects == ["vlm-judge-hallucination", "vlm-judge-mermaid"]


def test_judge_with_config_panel_dedups_the_same_defect_flagged_by_both_judges() -> None:
    client = _PerModelJudgeClient(
        description="d",
        verdicts_by_model={
            "judge-a": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes",
            "judge-b": "hallucination: yes\nmermaid_fit: n/a\nlanguage: yes",
        },
    )
    config = Config(vlm_judge_mode="panel", vlm_judge_panel=("judge-a", "judge-b"))

    defects = vlm._judge_with_config("uri", "d", config, lambda: client)

    assert defects == ["vlm-judge-hallucination"]  # not duplicated


def test_judge_with_config_panel_both_clean_produces_no_defects() -> None:
    client = _PerModelJudgeClient(
        description="d", verdicts_by_model={"judge-a": _CLEAN_VERDICT, "judge-b": _CLEAN_VERDICT}
    )
    config = Config(vlm_judge_mode="panel", vlm_judge_panel=("judge-a", "judge-b"))

    assert vlm._judge_with_config("uri", "d", config, lambda: client) == []


# =============================================================================
# 11. Direct helper coverage — error/edge branches (coverage-hardening
# spec). None of these need a real network/soffice call — the module's
# own pluggable
# VlmClient/VlmCacheBackend Protocols plus subprocess/pdfplumber mocking are
# enough, same offline-only discipline as every test above.
# =============================================================================


def _docx_with_media(name: str, data: bytes = b"fake-bytes") -> bytes:
    """A minimal zip carrying an unrelated top-level part PLUS a single
    word/media/* entry — the unrelated part exercises _docx_media_uri's
    "not under word/media/" skip, the media entry is what's actually
    matched against marker_id."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", b"<irrelevant/>")
        z.writestr(f"word/media/{name}", data)
    return buf.getvalue()


def test_docx_media_uri_success_returns_base64_data_uri() -> None:
    data = b"png-bytes"
    docx_bytes = _docx_with_media("image1.png", data)
    marker_id = hashlib.sha256(data).hexdigest()[:12]

    result = vlm._docx_media_uri(docx_bytes, marker_id, raw_name="doc.docx")

    assert result == f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


def test_docx_media_uri_non_raster_format_returns_none_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = b"<svg/>"
    docx_bytes = _docx_with_media("image1.svg", data)
    marker_id = hashlib.sha256(data).hexdigest()[:12]

    with caplog.at_level("WARNING"):
        result = vlm._docx_media_uri(docx_bytes, marker_id, raw_name="doc.docx")

    assert result is None
    assert "not raster" in caplog.text


def test_docx_media_uri_marker_not_found_on_redetection_returns_none_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    docx_bytes = _docx_with_media("image1.png", b"png-bytes")

    with caplog.at_level("WARNING"):
        result = vlm._docx_media_uri(docx_bytes, "000000000000", raw_name="doc.docx")

    assert result is None
    assert "not found" in caplog.text


# --- security audit 2026-08-07, final-review finding: _docx_media_uri/
# _render_docx_group can raise on a re-read failure (corrupted/spoofed
# member) with no caller-side guard, unlike every other external-boundary
# call in enhance_docx_markdown -- contradicts that function's own "never
# raise, always degrade" contract for callers bypassing docx.convert(). ---


@pytest.mark.parametrize(
    "exc", [vlm.zipsafe.ArchiveBombSuspected("boom"), zipfile.BadZipFile("bad crc")]
)
def test_docx_media_uri_safely_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, exc: Exception
) -> None:
    def _raise(*a: object, **k: object) -> str | None:
        raise exc

    monkeypatch.setattr(vlm, "_docx_media_uri", _raise)

    with caplog.at_level("WARNING"):
        result = vlm._docx_media_uri_safely(b"docbytes", "id1", raw_name="doc.docx")

    assert result is None
    assert "failed to re-read" in caplog.text


@pytest.mark.parametrize(
    "exc", [vlm.zipsafe.ArchiveBombSuspected("boom"), zipfile.BadZipFile("bad crc")]
)
def test_render_docx_group_safely_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, exc: Exception
) -> None:
    def _raise(*a: object, **k: object) -> str | None:
        raise exc

    monkeypatch.setattr(vlm, "_render_docx_group", _raise)

    with caplog.at_level("WARNING"):
        result = vlm._render_docx_group_safely(b"docbytes", "id1", raw_name="doc.docx")

    assert result is None
    assert "failed to re-read" in caplog.text


def test_enhance_docx_markdown_corrupted_media_degrades_not_raises() -> None:
    # End-to-end reproduction of the live PoC the final review used: a
    # structurally-valid-but-corrupted docx passed directly to
    # enhance_docx_markdown() (bypassing docx.convert()) must never raise,
    # regardless of exactly where in the pipeline the corruption surfaces.
    data = (b"real media bytes, padded so the corrupted byte range below stays inside it") * 3
    docx_bytes = bytearray(_docx_with_media("image1.png", data))
    marker_id = hashlib.sha256(data).hexdigest()[:12]
    mid = len(docx_bytes) // 2
    for i in range(mid, min(mid + 20, len(docx_bytes))):
        docx_bytes[i] ^= 0xFF
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(marker_id), bytes(docx_bytes), config=config
    )

    assert vlm_used is False
    assert markdown == _image_only_markdown(marker_id)


def test_soffice_available_true_when_shutil_which_finds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm.shutil, "which", lambda name: "/usr/bin/soffice")
    assert vlm._soffice_available() is True


def test_soffice_available_false_when_shutil_which_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm.shutil, "which", lambda name: None)
    assert vlm._soffice_available() is False


class _FakePage:
    """pdfplumber.Page stand-in for _content_bbox — only .rects/.curves/
    .images/.chars/.bbox are read."""

    def __init__(
        self, elements: list[dict[str, float]] | None = None, bbox: vlm.BBox = (0, 0, 100, 100)
    ) -> None:
        self.rects = elements or []
        self.curves: list[dict[str, float]] = []
        self.images: list[dict[str, float]] = []
        self.chars: list[dict[str, float]] = []
        self.bbox = bbox


def test_content_bbox_empty_page_returns_none() -> None:
    assert vlm._content_bbox(_FakePage()) is None


def test_content_bbox_degenerate_zero_width_element_returns_none() -> None:
    # A single point-like element (x0 == x1) collapses to a zero-width bbox.
    page = _FakePage([{"x0": 5, "x1": 5, "top": 5, "bottom": 5}])
    assert vlm._content_bbox(page) is None


def test_content_bbox_valid_elements_returns_dense_bbox() -> None:
    page = _FakePage([{"x0": 10, "x1": 20, "top": 5, "bottom": 15}], bbox=(0, 0, 100, 100))
    assert vlm._content_bbox(page) == (10, 5, 20, 15)


def test_render_via_soffice_timeout_returns_none_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=vlm.SOFFICE_RENDER_TIMEOUT)

    monkeypatch.setattr(vlm.subprocess, "run", _raise_timeout)

    with caplog.at_level("WARNING"):
        result = vlm._render_via_soffice(
            b"docbytes", suffix=".docx", raw_name="doc.docx", obj_id="id1", obj_kind="group"
        )

    assert result is None
    assert "did not finish" in caplog.text


def test_render_via_soffice_nonzero_exit_returns_none_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(vlm.subprocess, "run", _fake_run)

    with caplog.at_level("WARNING"):
        result = vlm._render_via_soffice(
            b"docbytes", suffix=".docx", raw_name="doc.docx", obj_id="id1", obj_kind="group"
        )

    assert result is None
    assert "failed to render" in caplog.text


def test_render_via_soffice_pdfplumber_exception_returns_none_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        outdir_index = cmd.index("--outdir") + 1
        pdf_path = vlm.Path(cmd[outdir_index]) / "obj.pdf"
        pdf_path.write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    def _raise_pdfplumber_open(path: object) -> None:
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(vlm.subprocess, "run", _fake_run)
    monkeypatch.setattr(vlm.pdfplumber, "open", _raise_pdfplumber_open)

    with caplog.at_level("WARNING"):
        result = vlm._render_via_soffice(
            b"docbytes", suffix=".docx", raw_name="doc.docx", obj_id="id1", obj_kind="group"
        )

    assert result is None
    assert "rendering PDF" in caplog.text


class _FakeRenderedImage:
    def save(self, buf: io.BytesIO, format: str, quality: int) -> None:
        buf.write(b"fake-jpeg-bytes")


class _FakeOriginal:
    def convert(self, mode: str) -> _FakeRenderedImage:
        return _FakeRenderedImage()


class _FakeToImageResult:
    original = _FakeOriginal()


class _FakePdfPage:
    """pdfplumber page stand-in for the FULL _render_via_soffice success
    path — empty rects/curves/images/chars so _content_bbox degrades to
    None (the uncropped branch), .crop() is never actually reached, but is
    provided for interface completeness."""

    def __init__(self) -> None:
        self.rects: list[dict[str, float]] = []
        self.curves: list[dict[str, float]] = []
        self.images: list[dict[str, float]] = []
        self.chars: list[dict[str, float]] = []
        self.bbox: vlm.BBox = (0, 0, 100, 100)

    def crop(self, bbox: vlm.BBox) -> _FakePdfPage:
        return self

    def to_image(self, resolution: int) -> _FakeToImageResult:
        return _FakeToImageResult()


class _FakePdfDocument:
    def __init__(self) -> None:
        self.pages = [_FakePdfPage()]

    def __enter__(self) -> _FakePdfDocument:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_render_via_soffice_success_returns_jpeg_data_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        outdir_index = cmd.index("--outdir") + 1
        pdf_path = vlm.Path(cmd[outdir_index]) / "obj.pdf"
        pdf_path.write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vlm.subprocess, "run", _fake_run)
    monkeypatch.setattr(vlm.pdfplumber, "open", lambda path: _FakePdfDocument())

    result = vlm._render_via_soffice(
        b"docbytes", suffix=".docx", raw_name="doc.docx", obj_id="id1", obj_kind="group"
    )

    assert result is not None
    assert result.startswith("data:image/jpeg;base64,")


def test_render_docx_group_not_found_on_redetection_returns_none_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(vlm, "_soffice_available", lambda: True)
    monkeypatch.setattr(vlm.docx_groups, "extract_group_docx", lambda *a, **k: None)

    with caplog.at_level("WARNING"):
        result = vlm._render_docx_group(b"docbytes", "abcdef012345", raw_name="doc.docx")

    assert result is None
    assert "not found on re-detection" in caplog.text


def test_render_docx_group_success_delegates_to_render_via_soffice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_soffice_available", lambda: True)
    monkeypatch.setattr(vlm.docx_groups, "extract_group_docx", lambda *a, **k: b"mini-docx-bytes")
    monkeypatch.setattr(vlm, "_render_via_soffice", lambda *a, **k: "data:image/jpeg;base64,xyz")

    result = vlm._render_docx_group(b"docbytes", "abcdef012345", raw_name="doc.docx")

    assert result == "data:image/jpeg;base64,xyz"


def test_call_client_exception_returns_none_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        result = vlm._call_client(
            _RaisingVlmClient(), "prompt", "uri", model="m", raw_name="doc.docx", obj_id="id1"
        )

    assert result is None
    assert "id1: VLM call failed" in caplog.text


def test_resolve_api_key_missing_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = Config(use_vlm=True, vlm_api_key=None)

    with pytest.raises(RuntimeError, match="needs a VLM API key"):
        vlm._resolve_api_key(config)


def test_resolve_api_key_uses_config_value_when_set() -> None:
    config = Config(use_vlm=True, vlm_api_key="from-config")
    assert vlm._resolve_api_key(config) == "from-config"


def test_resolve_api_key_falls_back_to_openrouter_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    config = Config(use_vlm=True, vlm_api_key=None)
    assert vlm._resolve_api_key(config) == "from-env"


def test_enhance_docx_markdown_cache_miss_lazily_constructs_default_client_when_none_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither Config.vlm_client nor a cache hit supplies a client,
    _get_client() must construct the default OpenRouterClient itself —
    stub the CLASS (not a real HTTP call) to prove that construction path
    runs, offline."""

    class _FakeOpenRouterClient:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

        def send(self, prompt: str, image_uri: str, *, model: str) -> str:
            return "A chart via the lazily-constructed default client."

    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    monkeypatch.setattr(vlm, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    docx_bytes = build_minimal_docx(["text"])
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())  # no vlm_client, no vlm_api_key

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _image_only_markdown(_IMAGE_ID), docx_bytes, config=config
    )

    assert vlm_used is True
    assert warnings == []


def test_enhance_docx_markdown_image_cache_miss_media_unavailable_leaves_marker_unchanged() -> None:
    # No word/media/* entries at all — _docx_media_uri returns None for real.
    docx_bytes = build_minimal_docx(["text"])
    markdown = _image_only_markdown(_IMAGE_ID)
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []


def test_enhance_docx_markdown_image_cache_miss_client_failure_leaves_marker_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_docx_media_uri", lambda *a, **k: "data:image/jpeg;base64,x")
    docx_bytes = build_minimal_docx(["text"])
    markdown = _image_only_markdown(_IMAGE_ID)
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend(), vlm_client=_RaisingVlmClient())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []


def test_enhance_docx_markdown_group_cache_miss_client_failure_leaves_marker_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_render_docx_group", lambda *a, **k: "data:image/jpeg;base64,x")
    docx_bytes = build_minimal_docx(["text"])
    markdown = _group_only_markdown(_GROUP_ID, "sales region")
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend(), vlm_client=_RaisingVlmClient())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        markdown, docx_bytes, config=config
    )

    assert vlm_used is False
    assert new_markdown == markdown
    assert warnings == []


def test_enhance_docx_markdown_vlm_verify_true_partial_cache_hit_group_computes_judge_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group-side twin of
    ``..._partial_cache_hit_computes_judge_only`` (image side, §9 above)."""
    monkeypatch.setattr(vlm, "_render_docx_group", lambda *a, **k: "data:image/jpeg;base64,x")
    client = _ScriptedVlmClient(verdict="hallucination: no\nmermaid_fit: no\nlanguage: yes")
    cache = InMemoryCacheBackend()
    cache.set(
        _GROUP_ID, {"model": "test-model", "markdown": "A bar chart."}
    )  # no judge_verdict key
    docx_bytes = build_minimal_docx(["text"])
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_judge_mode="solo",
        vlm_judge_model="test-judge-model",
        vlm_cache=cache,
        vlm_client=client,
    )

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _group_only_markdown(_GROUP_ID, "bar chart"), docx_bytes, config=config
    )

    assert vlm_used is True
    assert len(client.prompts) == 1  # ONLY the judge call — no re-generation
    assert warnings == [f"vlm-judge-mermaid: {_GROUP_ID}"]
    updated_entry = cache.get(_GROUP_ID)
    assert updated_entry is not None
    assert updated_entry["judge_verdict"] == ["vlm-judge-mermaid"]


def test_enhance_docx_markdown_archive_recheck_failure_skips_with_warning() -> None:
    not_a_zip = b"this is not a zip archive at all"
    markdown = _image_only_markdown(_IMAGE_ID)
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(markdown, not_a_zip, config=config)

    assert vlm_used is False
    assert new_markdown == markdown
    assert len(warnings) == 1
    assert warnings[0].startswith("vlm enhancement skipped")


def test_enhance_docx_markdown_vlm_verify_true_partial_cache_hit_group_unavailable_skips_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group-side twin of the image-marker
    ``..._partial_cache_hit_image_unavailable_skips_gracefully`` test above —
    ``_render_docx_group`` returning ``None`` on re-detection (not
    ``_docx_media_uri``) must degrade the same way for a group marker."""
    monkeypatch.setattr(vlm, "_render_docx_group", lambda *a, **k: None)
    client = _ScriptedVlmClient()
    cache = InMemoryCacheBackend()
    cache.set(_GROUP_ID, {"model": "test-model", "markdown": "A bar chart."})
    docx_bytes = build_minimal_docx(["text"])
    config = Config(use_vlm=True, vlm_verify=True, vlm_cache=cache, vlm_client=client)

    _, vlm_used, warnings = vlm.enhance_docx_markdown(
        _group_only_markdown(_GROUP_ID, "bar chart"), docx_bytes, config=config
    )

    assert vlm_used is True  # description still injected from cache
    assert warnings == []  # judge upgrade silently skipped, no crash
    assert len(client.prompts) == 0
    updated_entry = cache.get(_GROUP_ID)
    assert updated_entry is not None
    assert "judge_verdict" not in updated_entry  # left untouched, not force-set


# =============================================================================
# 12. Security audit 2026-08-07 remediation — _send_safely/_cache_*_safely/
# Config.vlm_api_key, not already covered incidentally by sections above.
# =============================================================================


def test_send_safely_truncates_oversized_response_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    oversized = "x" * (vlm._MAX_VLM_RESPONSE_CHARS + 100)
    client = _NonConformingVlmClient(oversized)

    with caplog.at_level("WARNING"):
        result = vlm._send_safely(client, "prompt", "uri", model="m", context="ctx")

    assert result is not None
    assert len(result) == vlm._MAX_VLM_RESPONSE_CHARS
    assert "truncated" in caplog.text


def test_send_safely_redacts_secret_like_text_in_exception_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _LeakyVlmClient:
        def send(self, prompt: str, image_uri: str, *, model: str) -> str:
            raise RuntimeError("upstream rejected Bearer sk-abcdefghijklmnopqrstuvwxyz123456")

    with caplog.at_level("WARNING"):
        result = vlm._send_safely(_LeakyVlmClient(), "prompt", "uri", model="m", context="ctx")

    assert result is None
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in caplog.text
    assert "***REDACTED***" in caplog.text


def test_cache_get_safely_malformed_entry_treated_as_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _MalformedCache:
        def get(self, key: str) -> dict[str, object] | None:
            return {"unexpected": "shape"}  # missing "model"/"markdown"

        def set(self, key: str, value: dict[str, object]) -> None:
            raise AssertionError("not exercised")

    with caplog.at_level("WARNING"):
        result = vlm._cache_get_safely(_MalformedCache(), "key", context="ctx")

    assert result is None
    assert "malformed cache entry" in caplog.text


def test_cache_get_safely_backend_exception_treated_as_miss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _RaisingGetCache:
        def get(self, key: str) -> dict[str, object] | None:
            raise ConnectionError("backend unreachable")

        def set(self, key: str, value: dict[str, object]) -> None:
            raise AssertionError("not exercised")

    with caplog.at_level("WARNING"):
        result = vlm._cache_get_safely(_RaisingGetCache(), "key", context="ctx")

    assert result is None
    assert "cache read failed" in caplog.text


def test_cache_set_safely_backend_exception_is_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _RaisingSetCache:
        def get(self, key: str) -> dict[str, object] | None:
            raise AssertionError("not exercised")

        def set(self, key: str, value: dict[str, object]) -> None:
            raise ConnectionError("backend unreachable")

    with caplog.at_level("WARNING"):
        vlm._cache_set_safely(
            _RaisingSetCache(), "key", {"model": "m", "markdown": "x"}, context="ctx"
        )

    assert "cache write failed" in caplog.text


def test_config_vlm_api_key_excluded_from_repr() -> None:
    config = Config(vlm_api_key="sk-super-secret-value")
    assert "sk-super-secret-value" not in repr(config)


def test_judge_prompt_template_frames_response_as_data_not_instructions() -> None:
    # Finding #4: static-content guard against silently regressing the
    # prompt-injection mitigation wording.
    assert "not further instructions" in vlm.JUDGE_PROMPT_TEMPLATE


# --- mermaid-type-expansion (spec mermaid-type-expansion-2026-08-20): 22 new
# types added to FIG_PROMPT beyond the original 4 (flowchart/pie/
# xychart-beta/radar-beta). Each canonical example below is the exact
# syntax FIG_PROMPT's own guide teaches for that type — it must survive a
# REAL mermaidx render, not just look plausible (the same discipline that
# caught a wrong cynefin-beta syntax guess during implementation, before it
# ever reached this file). -------------------------------------------------

_NEW_MERMAID_EXAMPLES: dict[str, str] = {
    "mindmap": (
        "mindmap\n  root((Strategy))\n    Branch A\n      Leaf 1\n    Branch B\n      Leaf 2"
    ),
    "venn-beta": ('venn-beta\n  set A["5G"]\n  set B["IMT-2020"]\n  union A, B["5G/IMT-2020"]'),
    "quadrantChart": (
        "quadrantChart\n  x-axis Low --> High\n  y-axis Low --> High\n"
        "  quadrant-1 Leaders\n  quadrant-2 Challengers\n"
        "  quadrant-3 Niche\n  quadrant-4 Visionaries\n"
        "  Item A: [0.7, 0.8]\n  Item B: [0.3, 0.4]"
    ),
    "timeline": "timeline\n  title Roadmap\n  2020 : Launch\n  2021 : Expansion",
    "sankey-beta": "sankey-beta\nBudget,Marketing,100\nBudget,R&D,200\nMarketing,Ads,60",
    "treemap-beta": ('treemap-beta\n"Budget"\n  "Marketing": 100\n  "R&D": 200'),
    "gantt": ("gantt\n  dateFormat YYYY-MM-DD\n  section Phase 1\n  Task A : t1, 2024-01-01, 10d"),
    "sequenceDiagram": "sequenceDiagram\n  Alice->>Bob: Request\n  Bob->>Alice: Response",
    "classDiagram": "classDiagram\n  class Animal\n  Animal : +name\n  Animal <|-- Dog",
    "stateDiagram-v2": ("stateDiagram-v2\n  [*] --> Idle\n  Idle --> Running\n  Running --> [*]"),
    "erDiagram": "erDiagram\n  CUSTOMER ||--o{ ORDER : places",
    "journey": ("journey\n  section Shopping\n  Browse: 3: Customer\n  Checkout: 5: Customer"),
    "gitGraph": (
        "gitGraph\n  commit\n  branch develop\n  checkout develop\n"
        "  commit\n  checkout main\n  merge develop"
    ),
    "packet-beta": 'packet-beta\n0-7: "Source Port"\n8-15: "Dest Port"',
    "C4Context": (
        'C4Context\n  Person(user, "User", "A user")\n'
        '  System(sys, "System", "The system")\n  Rel(user, sys, "Uses")'
    ),
    "kanban": "kanban\n  Todo\n    task1[Design]\n  Done\n    task2[Ship]",
    "requirementDiagram": (
        "requirementDiagram\n  requirement req1 {\n    id: 1\n"
        "    text: the system shall respond\n    risk: high\n"
        "    verifymethod: test\n  }"
    ),
    "block-beta": ('block-beta\n  columns 2\n  a["Frontend"]\n  b["Backend"]\n  a --> b'),
    "architecture-beta": (
        "architecture-beta\n  group api(cloud)[API]\n"
        "  service db(database)[Database] in api\n"
        "  service srv(server)[Server] in api\n  srv:R -- L:db"
    ),
    "wardley-beta": (
        "wardley-beta\n  title Map\n  component Customer [0.9, 0.9]\n  component Product [0.7, 0.5]"
    ),
    "cynefin-beta": (
        'cynefin-beta\n  complex "Best practice"\n'
        '  complicated "Expert analysis"\n  clear "Known procedure"\n'
        '  chaotic "Crisis response"'
    ),
    "ishikawa-beta": ("ishikawa-beta\nProblem\n  People\n    Training\n  Process\n    Workflow"),
}


@pytest.mark.mermaid  # real mermaidx render, one call per new type
@pytest.mark.parametrize("mermaid_type", sorted(_NEW_MERMAID_EXAMPLES))
def test_fig_prompt_new_mermaid_type_canonical_example_renders(mermaid_type: str) -> None:
    assert chart_render.mermaid_renders(_NEW_MERMAID_EXAMPLES[mermaid_type]) is True


def test_fig_prompt_documents_all_22_new_types_by_keyword() -> None:
    for mermaid_type in _NEW_MERMAID_EXAMPLES:
        assert f"``{mermaid_type}``" in vlm.FIG_PROMPT, mermaid_type


def test_fig_prompt_recommends_venn_beta_union_bracket_label_syntax() -> None:
    # `union A, B["Label"]` is the only WORKING intersection-label syntax
    # (confirmed live against a real mermaidx render) — FIG_PROMPT's venn-beta
    # bullet must instruct the model to use it.
    assert 'union A, B["Label"]' in vlm.FIG_PROMPT


def test_fig_prompt_warns_against_both_broken_venn_beta_label_syntaxes() -> None:
    # `text [...]` breaks the mermaid parser outright; bare `text "..."`
    # (no brackets) parses but silently produces no visible label — both
    # confirmed live against a real mermaidx render. FIG_PROMPT must name
    # both explicitly as forbidden (not just recommend the working syntax
    # and hope the model never reaches for either broken one).
    assert "`text [...]` line (breaks the parser)" in vlm.FIG_PROMPT
    assert 'bare `text "..."` (renders' in vlm.FIG_PROMPT


@pytest.mark.mermaid  # real mermaidx render + SVG content inspection
def test_venn_beta_working_syntax_renders_with_visible_intersection_label() -> None:
    svg = mermaidx.render(_NEW_MERMAID_EXAMPLES["venn-beta"]).svg()
    assert "5G/IMT-2020" in svg


def test_fig_prompt_documents_wardley_beta_coordinate_order_is_not_xy() -> None:
    assert "[visibility, evolution]" in vlm.FIG_PROMPT
    assert "NOT `[x, y]`" in vlm.FIG_PROMPT


def test_fig_prompt_documents_cynefin_beta_fixed_domain_keywords_only() -> None:
    for keyword in ("clear", "complicated", "complex", "chaotic", "confusion"):
        assert keyword in vlm.FIG_PROMPT
