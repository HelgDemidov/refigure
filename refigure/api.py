"""Public result/config/exception types shared by refigure.docx and refigure.xlsx."""

from __future__ import annotations

from dataclasses import dataclass, field


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
