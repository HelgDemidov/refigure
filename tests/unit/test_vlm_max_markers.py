"""Config.vlm_max_markers pre-flight ceiling (mcp-server-phase1-skeleton
spec §2): counts markers requiring a PAID call — cache-misses, plus (when
vlm_verify=True) cache-hits whose judge_verdict is still unset — before
any paid call, raising VlmMarkerLimitExceededError instead of partial
processing.
"""

from __future__ import annotations

import pytest

from refigure.api import Config, VlmMarkerLimitExceededError
from refigure.vlm import enhance_docx_markdown
from refigure.vlm.cache import InMemoryCacheBackend

from .docx.test_docx import build_minimal_docx


class _CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        self.calls += 1
        return "hallucination: no\nmermaid_fit: n/a\nlanguage: yes"


def _marker_id(i: int) -> str:
    # Exactly 12 lowercase hex chars, required by _DOCX_IMAGE_MARKER_RE —
    # a naive "a"*11+str(i) breaks past i=9 (two-digit i overflows the
    # fixed-width id and silently stops matching the marker regex at all).
    return f"{i:012x}"


def _image_markers_markdown(n: int) -> str:
    return "\n".join(
        f"> [Image, docx media {_marker_id(i)} — raster content not analyzed]" for i in range(n)
    )


def test_cache_hit_only_document_under_the_ceiling_makes_no_calls() -> None:
    cache = InMemoryCacheBackend()
    for i in range(3):
        cache.set(_marker_id(i), {"model": "m", "markdown": "cached", "judge_verdict": None})
    client = _CountingClient()
    config = Config(use_vlm=True, vlm_max_markers=5, vlm_cache=cache, vlm_client=client)
    docx_bytes = build_minimal_docx(["irrelevant"])

    markdown, vlm_used, warnings = enhance_docx_markdown(
        _image_markers_markdown(3), docx_bytes, config=config
    )

    assert vlm_used is True
    assert client.calls == 0
    assert "cached" in markdown


def test_cache_miss_above_ceiling_raises_before_any_paid_call() -> None:
    client = _CountingClient()
    config = Config(
        use_vlm=True, vlm_max_markers=2, vlm_cache=InMemoryCacheBackend(), vlm_client=client
    )
    docx_bytes = build_minimal_docx(["irrelevant"])

    with pytest.raises(VlmMarkerLimitExceededError, match=r"3 marker\(s\).*vlm_max_markers=2"):
        enhance_docx_markdown(_image_markers_markdown(3), docx_bytes, config=config)

    assert client.calls == 0


def test_judge_backfill_on_a_cache_hit_counts_toward_the_ceiling() -> None:
    """Regression guard for the exact gap an adversarial review caught in
    the first design of this mechanism: a fully-cached document under
    vlm_verify=True still triggers paid judge calls (the backfill pass)
    — those must be counted too, or the ceiling is silently bypassable by
    a fully-cached-but-unjudged document."""
    cache = InMemoryCacheBackend()
    cache.set(_marker_id(0), {"model": "m", "markdown": "already described", "judge_verdict": None})
    client = _CountingClient()
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_max_markers=0,
        vlm_cache=cache,
        vlm_client=client,
    )
    docx_bytes = build_minimal_docx(["irrelevant"])

    with pytest.raises(VlmMarkerLimitExceededError, match=r"1 marker\(s\)"):
        enhance_docx_markdown(_image_markers_markdown(1), docx_bytes, config=config)

    assert client.calls == 0


def test_judged_cache_hit_does_not_count_toward_the_ceiling() -> None:
    """The counterpart to the regression guard above: a cache hit that
    ALREADY carries a judge_verdict needs no further paid call, so it must
    NOT count against the ceiling."""
    cache = InMemoryCacheBackend()
    cache.set(
        _marker_id(0),
        {"model": "m", "markdown": "already described", "judge_verdict": []},
    )
    client = _CountingClient()
    config = Config(
        use_vlm=True,
        vlm_verify=True,
        vlm_max_markers=0,
        vlm_cache=cache,
        vlm_client=client,
    )
    docx_bytes = build_minimal_docx(["irrelevant"])

    markdown, vlm_used, warnings = enhance_docx_markdown(
        _image_markers_markdown(1), docx_bytes, config=config
    )

    assert vlm_used is True
    assert client.calls == 0


def _group_markers_markdown(n: int) -> str:
    return "\n".join(
        f"> [Figure, docx group {_marker_id(i)} — composite content not analyzed]\n"
        f"> captions: caption {i}"
        for i in range(n)
    )


def test_group_marker_cache_miss_above_ceiling_raises_before_any_paid_call() -> None:
    """Same pre-flight ceiling, but exercised through the group-marker loop
    (composite-figure ``docx group`` markers) rather than the image-marker
    loop every other test above uses — both loops feed the same paid_count/
    _get_entry machinery, but only the image-marker one was covered until
    this test. Never reaches soffice: the ceiling raises before any
    rendering is attempted."""
    client = _CountingClient()
    config = Config(
        use_vlm=True, vlm_max_markers=1, vlm_cache=InMemoryCacheBackend(), vlm_client=client
    )
    docx_bytes = build_minimal_docx(["irrelevant"])

    with pytest.raises(VlmMarkerLimitExceededError, match=r"2 marker\(s\).*vlm_max_markers=1"):
        enhance_docx_markdown(_group_markers_markdown(2), docx_bytes, config=config)

    assert client.calls == 0


def test_no_ceiling_by_default_is_unchanged_behavior() -> None:
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend(), vlm_client=_CountingClient())
    docx_bytes = build_minimal_docx(["irrelevant"])

    # Many markers, no vlm_max_markers set -> no VlmMarkerLimitExceededError,
    # same as before this field existed.
    enhance_docx_markdown(_image_markers_markdown(50), docx_bytes, config=config)
