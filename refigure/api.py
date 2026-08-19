"""Public result/config/exception types shared by refigure.docx and refigure.xlsx."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


class VlmClient(Protocol):
    """Structural contract for a VLM HTTP client (stage 4b).

    refigure is LLM provider-agnostic by design, not structurally tied to
    OpenRouter: ``vlm_client.OpenRouterClient`` is the one bundled
    implementation, not the only possible one. A caller with confidentiality
    requirements can supply any other ``VlmClient`` — e.g. a local
    Ollama/vLLM-backed client — without any change to ``vlm.py``/``docx.py``,
    which only ever depend on this Protocol, never on a concrete provider.

    Defined here (core, ``api.py``), not in ``vlm/client.py``: core must not
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

    ``vlm/cache.py`` provides two concrete implementations
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
    chart engine and ``docx/groups.py`` otherwise leave as an honest
    "content not analyzed" marker. **Data egress**: turning this on sends
    network requests to ``vlm_client``'s backing service — OpenRouter by
    default, or whichever service the configured ``vlm_client`` actually
    talks to (e.g. direct OpenAI/Ollama/vLLM/LM Studio via ``OpenAIClient``,
    direct Anthropic or Claude via Bedrock/Vertex/Foundry via
    ``AnthropicClient`` — see ``refigure/vlm/client.py``) — but ONLY the
    cropped image of the specific figure/group being interpreted, never the
    surrounding text or the rest of the document. This is a guarantee of
    the technique itself (the object is cropped before it's ever sent, by
    construction — see ``vlm._render_via_soffice``/``vlm._docx_media_uri``),
    not a policy layered on top of a less constrained call, and holds
    regardless of which ``vlm_client`` is configured."""

    vlm_verify: bool = False
    """Enable additional ``VlmClient.send()`` call(s) per resolved marker
    (image or group) that ask 3 fixed discriminative yes/no/n-a questions
    about the description already produced — hallucination, mermaid-diagram
    fit, and English-output compliance — instead of regenerating anything.
    See ``vlm.judge_defects``. Motivated by the Generative-Discriminative
    Gap (a VLM answers a concrete yes/no question about a piece of output
    more reliably than it generates accurate text from scratch) — NOT by
    asking a model to judge its own output: live validation (2026-08-06,
    24 labeled responses) found that self-judge (same model judges its own
    description, this field's original design) catches only 30%/12%
    (hallucination/mermaid-fit) of manually-confirmed real defects, with
    false positives on clean responses and non-deterministic verdicts on
    repeat calls. ``vlm_judge_mode``/``vlm_judge_model``/``vlm_judge_panel``
    below control WHICH model(s) judge instead — never the generating
    model alone. Full data lives in the project's internal research notes,
    not this public repo.

    How many calls this adds depends on ``vlm_judge_mode``: 1 in ``"solo"``
    mode, 2 in ``"panel"`` mode (the default) — see below. Off by default:
    this is a second (or third) network round-trip on top of ``use_vlm``
    alone, its own cost/latency commitment. Ignored entirely when
    ``use_vlm=False``, and never triggers a call when a marker's cached
    entry already carries a verdict from a previous ``vlm_verify`` run
    (cache-hit stays offline either way). Full rationale — including the
    language-blindness gap in the free ``vlm_witness_min_recall`` path
    below that this complements — lives in the project's internal
    research notes, not this public repo."""

    vlm_judge_mode: Literal["solo", "panel"] = "panel"
    """Which of ``vlm_judge_model``/``vlm_judge_panel`` below decides who
    judges, when ``vlm_verify=True``. ``"solo"``: one model, one call —
    cheaper (~$0.0045/marker at 2026-08-06 pricing), 70%/88%
    (hallucination/mermaid-fit) recall in live validation. ``"panel"``
    (default): 2 models, one call each, results unioned (flagged if EITHER
    judge flags it) — ~$0.0125/marker, 80%/88% recall, 100% of
    manually-confirmed defects caught on AT LEAST one dimension by at least
    one judge (vs. 27% for self-judge). ``vlm_verify`` is already an
    explicit opt-in; the default here favors the best-recall configuration
    of the two, not the cheapest — pass ``"solo"`` explicitly to trade
    recall for half the judge-call cost. Only ``"panel"``+UNION is
    supported, not an intersection/agreement policy: this gate's whole
    design is signal-not-failure (``ConversionResult.warnings``, never a
    hard fail) — a false positive costs one extra warning line, a missed
    real defect is unrecoverable once the document ships, so the gate
    optimizes for recall, not for silence. Full comparison table lives in
    the project's internal research notes, not this public repo."""

    vlm_judge_model: str = "anthropic/claude-haiku-4.5"
    """Judge model used when ``vlm_judge_mode="solo"``. Any OpenRouter model
    slug — no whitelist. Default is the best-performing SOLO judge found in
    live validation (2026-08-06): 70%/88% (hallucination/mermaid-fit)
    recall, notably better than ``vlm_model``'s own default
    (``google/gemini-3-flash-preview``) used as a judge (50%/50%) — being a
    strong generator and being a strong critic turned out to be different
    skills, not correlated in the same direction. Ignored in ``"panel"``
    mode. **Format is coupled to ``vlm_client``**: this default is an
    OpenRouter slug, meaningless if ``vlm_client`` is an ``AnthropicClient``
    (bare dated ID, e.g. ``"claude-haiku-4-5-20251001"``) or an
    ``OpenAIClient`` pointed at a non-OpenRouter endpoint — refigure does
    NOT canonicalize model IDs across clients."""

    vlm_judge_panel: tuple[str, str] = (
        "google/gemini-3-flash-preview",
        "anthropic/claude-haiku-4.5",
    )
    """Exactly 2 judge models used when ``vlm_judge_mode="panel"`` (the
    default) — the pair validated live (2026-08-06), not an arbitrary
    combination. Freely replaceable with any other 2 OpenRouter model
    slugs. Panel size is fixed at 2, not N — no researched precedent (this
    stage checked promptfoo/DeepEval/live MCP tool schemas, not just
    general literature) bakes an arbitrary-size ensemble into a single
    config surface; this stays a deliberately small, tested fork, not a
    general N-judge mechanism. Ignored in ``"solo"`` mode. Changing this
    field does not invalidate an already-cached ``judge_verdict`` computed
    under a different panel — same caveat ``vlm_model`` already has (the
    cache is keyed by marker id, not by which config produced the cached
    entry); clear the cache backend to force a re-check under new
    settings. Same ``vlm_client``-coupled model-ID format caveat as
    ``vlm_judge_model`` above applies to both entries of this tuple."""

    vlm_model: str = "google/gemini-3-flash-preview"
    """OpenRouter model slug used by the default ``OpenRouterClient``.
    Ignored when ``vlm_client`` is set to a custom implementation, which may
    have its own model-selection mechanism — and, if so, its own model-ID
    format: an ``OpenAIClient`` pointed at Ollama expects a bare model tag
    (e.g. ``"llava"``), an ``AnthropicClient`` expects Anthropic's bare
    dated ID (or a Bedrock/Vertex/Foundry-specific variant if ``client=``
    injects one of those — see ``refigure/vlm/client.py``'s
    ``AnthropicClient`` docstring), never this field's OpenRouter-slug
    shape. Confirmed (not just inherited as an untested placeholder) by
    this stage's own two-round A/B calibration against refigure's real
    corpus, 2026-08-05 — round 1 (3 simple English-caption crops) found a
    quality tie with a pricier competitor (``anthropic/claude-haiku-4.5``);
    round 2 (5 complex, multi-lingual crops added after a review found
    round 1 too thin) found ZERO manually-confirmed factual errors for this
    model against 2 for Claude Haiku and 5 for ``openai/gpt-4o-mini``
    (which fabricates an inappropriate mermaid diagram in 100% of responses
    regardless of structural fit — see the doc). Cheapest of the 3
    candidates in both rounds. One caveat, not a factual error: on
    non-English source documents this model tends to leave transcribed
    labels untranslated despite the prompt's "Output in English"
    instruction — a prompt-engineering fix, not a reason to switch models.
    Full comparison lives in the project's internal research notes, not
    this public repo."""

    vlm_api_key: str | None = field(default=None, repr=False)
    """API key for the default ``OpenRouterClient``. Falls back to the
    ``OPENROUTER_API_KEY`` environment variable when unset (explicit
    parameter overrides the environment, the usual SDK convention).
    Resolved lazily — only when a real (non-cached) VLM call is actually
    about to happen, so a fully cache-hit conversion needs no key at all.
    Ignored when ``vlm_client`` is set. ``repr=False``: the raw key must
    never appear in ``repr(config)``/``str(config)`` — a common debugging
    footgun (``logger.debug(config)``), live-verified leaking the plaintext
    key before this fix (security audit 2026-08-07, finding #7)."""

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
    independent reasons (full write-up in the project's internal research
    notes, not this public repo): (1) on English-caption content (the
    entire committed corpus) recall is a trivial 1.00 regardless of
    response quality — no separation signal exists; (2) on multi-lingual
    content (round 2, added after a review found round 1 too thin), recall
    DOES vary, but the variation tracks which models translate transcribed
    labels into English, not which models are factually accurate —
    calibrating against that signal would tune the gate to penalize
    language choice, not real errors. 0.80 is kept as a plausible default,
    not imported from unrelated literature thresholds either (own
    research, not this public repo). The manually-confirmed real defect
    classes (inappropriate mermaid fabrication, minor semantic drift) are
    NOT caught by this recall-only mechanism at all — a known, documented
    blind spot, not something this threshold value can fix.

    The real mitigation for both gaps is ``vlm_verify`` (``vlm.
    judge_defects``): its ``language``/``hallucination``/``mermaid_fit``
    questions check the description against the IMAGE itself, not a
    caption witness, so none of them inherit this field's
    language-sensitivity — see ``vlm_verify``'s own docstring and
    ``witness_defects``'s."""


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
    """Input is not a valid/safe zip archive (see ``refigure.core.zipsafe``)."""


class MissingOptionalDependencyError(Exception):
    """A required optional dependency (extra) is not installed."""
