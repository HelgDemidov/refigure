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


@dataclass
class Config:
    """Runtime configuration for a conversion call.

    ``strict`` is currently a no-op: DOCX/XLSX conversion has no capability
    whose absence is worth hard-failing on — a missing ``mermaidx`` degrades
    to a table-only render, itself a valid zero-loss result, not a broken
    one. ``strict`` starts branching real behavior once VLM (``use_vlm``)
    ships.
    """

    strict: bool = False


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
