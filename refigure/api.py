"""Public result/config/exception types shared by refigure.docx and refigure.xlsx."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class VlmClient(Protocol):
    """Structural contract for a VLM HTTP client (stage 4b).

    refigure is LLM provider-agnostic by design, not structurally tied to
    OpenRouter: ``vlm_client.OpenRouterClient`` is the one bundled
    implementation, not the only possible one. A caller with confidentiality
    requirements can supply any other ``VlmClient`` — e.g. a local
    Ollama/vLLM-backed client — without any change to ``vlm.py``/``docx.py``,
    which only ever depend on this Protocol, never on a concrete provider.

    Defined here (core, ``api.py``), not in ``vlm_client.py``: core must not
    depend on a per-capability peripheral module, only the reverse (same
    layering rule ``VlmCacheBackend`` below follows).
    """

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        """Send one prompt + one image (a data: URI) to the model, return its
        text response. Raises on an unrecoverable failure — callers are
        expected to catch broadly and degrade to the honest fallback marker
        (never let one figure's VLM failure abort the whole conversion)."""
        ...


class VlmCacheBackend(Protocol):
    """Structural contract for a VLM-response cache (stage 4b): a pluggable
    backend, not a hardcoded sidecar file — the source pipeline's
    ``.figures.yaml`` was a fixed on-disk convention tied to a persistent
    ``doc.md`` next to it; refigure has neither (a single in-memory
    ``convert()`` call, input can be bytes with no filesystem path at all).

    ``vlm_cache.py`` provides two concrete implementations
    (``InMemoryCacheBackend``/``FileCacheBackend``); a caller can supply any
    other backend via ``Config.vlm_cache`` (e.g. a shared Redis-backed one
    for a multi-process batch job) — same "core defines the contract,
    periphery implements it" layering as ``VlmClient`` above.
    """

    def get(self, key: str) -> dict[str, object] | None:
        """The cached entry for ``key``, or ``None`` on a cache miss."""
        ...

    def set(self, key: str, value: dict[str, object]) -> None:
        """Store ``value`` under ``key``, overwriting any prior entry."""
        ...


@dataclass
class Config:
    """Runtime configuration for a conversion call.

    ``strict`` is currently a no-op: DOCX/XLSX conversion has no capability
    whose absence is worth hard-failing on — a missing ``mermaidx`` degrades
    to a table-only render, itself a valid zero-loss result, not a broken
    one. ``strict`` starts branching real behavior once VLM (``use_vlm``)
    ships.

    VLM fields (stage 4b, DOCX-only — ``xlsx.convert()`` treats
    ``use_vlm=True`` as a silent no-op, XLSX has no VLM path at all, see
    ``vlm.py``'s module docstring): gated behind the ``[vlm]`` extra, not
    active/announced in v1.
    """

    strict: bool = False

    use_vlm: bool = False
    """Enable cloud VLM interpretation of composite DOCX figures that the
    chart engine and ``docx_groups.py`` otherwise leave as an honest
    "content not analyzed" marker. **Data egress**: turning this on sends
    network requests to ``vlm_client``'s backing service (OpenRouter by
    default) — but ONLY the cropped image of the specific figure/group
    being interpreted, never the surrounding text or the rest of the
    document. This is a guarantee of the technique itself (the object is
    cropped before it's ever sent, by construction — see
    ``vlm._render_via_soffice``/``vlm._docx_media_uri``), not a policy
    layered on top of a less constrained call."""

    vlm_model: str = "google/gemini-3-flash-preview"
    """OpenRouter model slug used by the default ``OpenRouterClient``.
    Ignored when ``vlm_client`` is set to a custom implementation, which may
    have its own model-selection mechanism. Confirmed (not just inherited
    as an untested placeholder) by this stage's own two-round A/B
    calibration against refigure's real corpus, 2026-08-05 — round 1 (3
    simple English-caption crops) found a quality tie with a pricier
    competitor (``anthropic/claude-haiku-4.5``); round 2 (5 complex,
    multi-lingual crops added after a review found round 1 too thin) found
    ZERO manually-confirmed factual errors for this model against 2 for
    Claude Haiku and 5 for ``openai/gpt-4o-mini`` (which fabricates an
    inappropriate mermaid diagram in 100% of responses regardless of
    structural fit — see the doc). Cheapest of the 3 candidates in both
    rounds. One caveat, not a factual error: on non-English source
    documents this model tends to leave transcribed labels untranslated
    despite the prompt's "Output in English" instruction — a prompt-
    engineering fix, not a reason to switch models. Full comparison:
    ``docs/vlm/vlm-model-calibration/vlm-model-calibration-2026-08-05.md``."""

    vlm_api_key: str | None = None
    """API key for the default ``OpenRouterClient``. Falls back to the
    ``OPENROUTER_API_KEY`` environment variable when unset (explicit
    parameter overrides the environment, the usual SDK convention).
    Resolved lazily — only when a real (non-cached) VLM call is actually
    about to happen, so a fully cache-hit conversion needs no key at all.
    Ignored when ``vlm_client`` is set."""

    vlm_client: VlmClient | None = None
    """Pluggable VLM HTTP client (see the ``VlmClient`` Protocol above).
    ``None`` falls back to ``OpenRouterClient(api_key=...)``. refigure is
    LLM provider-agnostic by design: supply any other implementation (e.g.
    a local Ollama/vLLM-backed client) here for confidentiality-sensitive
    documents, with no other code change needed."""

    vlm_cache: VlmCacheBackend | None = None
    """Pluggable VLM response cache (see the ``VlmCacheBackend`` Protocol
    above). ``None`` falls back to a fresh, empty ``InMemoryCacheBackend()``
    — no disk I/O by default. Pass a ``FileCacheBackend(path)`` (or any
    other implementation) to persist responses across ``convert()`` calls."""

    vlm_witness_min_recall: float = 0.80
    """Minimum token-recall (see ``vlm.token_recall``) of a composite
    group's own captions against its VLM description before
    ``vlm.witness_defects`` flags it. NOT empirically re-derived, across
    TWO rounds of this stage's A/B calibration, 2026-08-05, for two
    independent reasons documented in ``docs/vlm/vlm-model-calibration/
    vlm-model-calibration-2026-08-05.md``: (1) on English-caption content (the entire committed
    corpus) recall is a trivial 1.00 regardless of response quality — no
    separation signal exists; (2) on multi-lingual content (round 2, added
    after a review found round 1 too thin), recall DOES vary, but the
    variation tracks which models translate transcribed labels into
    English, not which models are factually accurate — calibrating against
    that signal would tune the gate to penalize language choice, not real
    errors. 0.80 is kept as a plausible default, not imported from
    unrelated literature thresholds either (see ``docs/vlm/vlm-layer-port/
    vlm-layer-port-2026-08-05.md`` §5's research). The manually-confirmed real defect
    classes (inappropriate mermaid fabrication, minor semantic drift) are
    NOT caught by this recall-only mechanism at all — a known, documented
    blind spot, not something this threshold value can fix."""


@dataclass
class ConversionResult:
    """Rich conversion output — never a bare string.

    ``warnings`` records structural findings the caller may want to act on
    (e.g. a chart with no numCache falling back to a caption-only marker),
    not exceptions: a warning means the conversion still succeeded.
    """

    markdown: str
    warnings: list[str] = field(default_factory=list)
    charts_found: int = 0
    charts_rendered: int = 0
    groups_found: int = 0
    vlm_used: bool = False


class UnsupportedFormatError(Exception):
    """Input is not structurally a valid document of the expected format."""


class CorruptArchiveError(Exception):
    """Input is not a valid/safe zip archive (see ``refigure.zipsafe``)."""


class MissingOptionalDependencyError(Exception):
    """A required optional dependency (extra) is not installed."""
