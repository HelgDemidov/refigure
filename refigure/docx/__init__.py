"""Public DOCX -> Markdown conversion entry point.

Imports mammoth + markdownify at module level — nothing else in this
package touches them, so ``pip install refigure`` (no extra) never pulls
them in. Importing this module without ``refigure[docx]`` installed raises
``MissingOptionalDependencyError`` immediately, with an actionable message,
instead of a bare ``ModuleNotFoundError``.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from lxml import etree

from .. import docx_groups
from .._io import normalize_source
from ..api import (
    Config,
    ConversionResult,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
)
from ..core import chart_render, zipsafe

try:
    import mammoth
    from mammoth import html as mammoth_html
    from markdownify import ATX, MarkdownConverter
except ImportError as exc:  # pragma: no cover - see tests/unit/test_optional_dependency_guards.py
    raise MissingOptionalDependencyError(
        "refigure[docx] is required to convert DOCX files"
    ) from exc

DOCX_IMAGE_MIN_BYTES = 5000
_DOCX_MARKER_SRC_PREFIX = "docx-marker:"


def _docx_referenced_media_ids(source: Path | bytes) -> frozenset[str]:
    """id12s of media files actually referenced by some XML part of the
    document (document.xml/headers/footers/notes/charts — any part with its
    own .rels). A file under word/media/ with no reference anywhere is an
    orphan Word never displays, not real document content.

    Uses lxml, not stdlib xml.etree.ElementTree, to parse untrusted .rels
    content — verified live (tests/unit/test_xml_security.py) that stdlib
    ElementTree has no nesting-depth protection (a 13MB crafted .rels caused
    547MB RSS growth), while lxml rejects excessive depth by default."""
    referenced: set[str] = set()
    z_source = source if isinstance(source, Path) else io.BytesIO(source)
    with zipfile.ZipFile(z_source) as z:
        names = set(z.namelist())
        for part in sorted(names):
            if not (part.startswith("word/") and part.endswith(".xml")) or "/_rels/" in part:
                continue
            rels_name = f"{posixpath.dirname(part)}/_rels/{posixpath.basename(part)}.rels"
            if rels_name not in names:
                continue
            try:
                rels_root = etree.fromstring(zipsafe.safe_read(z, rels_name))
            except etree.XMLSyntaxError:
                continue
            part_bytes = zipsafe.safe_read(z, part)
            for rel in rels_root:
                if not rel.get("Type", "").endswith("/image"):
                    continue
                rid = rel.get("Id")
                target = rel.get("Target", "")
                if not rid or f'"{rid}"'.encode() not in part_bytes:
                    continue
                media = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                if media.startswith("word/media/") and media in names:
                    referenced.add(hashlib.sha256(zipsafe.safe_read(z, media)).hexdigest()[:12])
    return frozenset(referenced)


def _docx_image_markers(source: Path | bytes, *, placed: frozenset[str] = frozenset()) -> str:
    """Fallback markers under "## Figures (position unknown)" for media that
    is (a) actually referenced by the document (orphan filter), (b) not
    smaller than DOCX_IMAGE_MIN_BYTES, (c) not already placed inline by the
    mammoth pass (``placed``). Deduplicated by id12. An empty result is
    valid and expected — on a clean document all real raster content lands
    inline."""
    referenced = _docx_referenced_media_ids(source)
    lines: list[str] = []
    seen: set[str] = set()
    z_source = source if isinstance(source, Path) else io.BytesIO(source)
    with zipfile.ZipFile(z_source) as z:
        for name in sorted(z.namelist()):
            if not name.startswith("word/media/"):
                continue
            data = zipsafe.safe_read(z, name)
            if len(data) < DOCX_IMAGE_MIN_BYTES:
                continue
            id12 = hashlib.sha256(data).hexdigest()[:12]
            if id12 in placed or id12 in seen or id12 not in referenced:
                continue
            seen.add(id12)
            lines.append(f"> [Image, docx media {id12} — raster content not analyzed]")
    if not lines:
        return ""
    return "\n## Figures (position unknown)\n\n" + "\n".join(lines) + "\n"


class _DocxMarkdownify(MarkdownConverter):
    def convert_img(self, el: Any, text: str, parent_tags: Any) -> str:
        # The only <img> source in this HTML comes from convert_image below,
        # so src always carries the marker-src prefix.
        id12 = (el.attrs.get("src") or "")[len(_DOCX_MARKER_SRC_PREFIX) :]
        return f"\n\n> [Image, docx media {id12} — raster content not analyzed]\n\n"


def convert(source: Path | bytes | BinaryIO, *, config: Config | None = None) -> ConversionResult:
    """Convert a DOCX file (path, bytes, or a file-like object) to Markdown."""
    config = config or Config()
    normalized = normalize_source(source)

    try:
        zipsafe.check_archive(normalized)

        rewritten, groups = docx_groups.extract_and_strip_groups(normalized)

        placed_ids: set[str] = set()

        def convert_image(image: Any) -> list[Any]:
            with image.open() as f:
                data = f.read()
            if len(data) < DOCX_IMAGE_MIN_BYTES:
                return []
            id12 = hashlib.sha256(data).hexdigest()[:12]
            placed_ids.add(id12)
            return [mammoth_html.element("img", {"src": f"{_DOCX_MARKER_SRC_PREFIX}{id12}"})]

        try:
            converted = mammoth.convert_to_html(io.BytesIO(rewritten), convert_image=convert_image)
        except zipfile.BadZipFile:
            raise  # corrupted member CRC — let the outer handler classify this
        except Exception as exc:
            # mammoth has no unified exception type for "not a valid docx" —
            # verified live across several malformation shapes: OSError
            # ("Could not find main document part..."), ValueError ("Could
            # not find the body element..."), xml.parsers.expat.ExpatError
            # (malformed inner XML). A blanket catch scoped to this one
            # narrow call is deliberate: anything mammoth raises parsing
            # untrusted input means "not a docx", never leak the internal
            # exception type.
            raise UnsupportedFormatError(str(exc)) from exc

        text = _DocxMarkdownify(heading_style=ATX).convert(converted.value).strip()

        warnings: list[str] = []
        charts_found = sum(1 for g in groups if g.kind == "chart")
        groups_found = sum(1 for g in groups if g.kind == "group")
        charts_rendered = 0

        if text:
            text, charts_rendered = docx_groups.inject_group_markers(text, groups)
        else:
            warnings.append("no extractable content")

        if charts_found and not chart_render.mermaidx_available():
            warnings.append(
                "mermaidx not installed — chart diagrams disabled, tables only "
                "(install refigure[docx] with mermaidx to enable rendering)"
            )

        fallback = _docx_image_markers(
            normalized, placed=frozenset(placed_ids) | docx_groups.all_media_ids(groups)
        )
    except (zipsafe.ArchiveBombSuspected, zipfile.BadZipFile) as exc:
        # BadZipFile here means a structurally valid zip with corrupted
        # member data (bad CRC-32) — can surface from any z.read() above
        # (extract_and_strip_groups, mammoth reading the rewritten bytes,
        # _docx_image_markers), not just zipsafe.check_archive itself.
        # Verified live: a byte-flipped-but-structurally-intact docx raises
        # this from extract_and_strip_groups, well before mammoth ever runs.
        raise CorruptArchiveError(str(exc)) from exc

    markdown = text + "\n" + fallback
    vlm_used = False
    if config.use_vlm:
        # Lazy import (stage 4b): refigure[docx] without [vlm] must not
        # require pdfplumber just to call convert() with the (default)
        # use_vlm=False — only actually using the feature pulls it in. Same
        # guard-ordering discipline as refigure/cli.py's per-format lazy
        # import (project_extras_isolation_bug memory).
        from .. import vlm

        markdown, vlm_used, vlm_warnings = vlm.enhance_docx_markdown(
            markdown, normalized, config=config
        )
        warnings.extend(vlm_warnings)

    return ConversionResult(
        markdown=markdown,
        warnings=warnings,
        charts_found=charts_found,
        charts_rendered=charts_rendered,
        groups_found=groups_found,
        vlm_used=vlm_used,
    )
