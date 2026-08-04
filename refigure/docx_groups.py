"""Composite groups in docx (spec convert-docx §2-ter): Word draws a complex
infographic as a GROUP of shapes (``mc:AlternateContent``/``mc:Choice``/
``wpg:wgp`` + VML ``mc:Fallback``) — mammoth walks such a group element by
element, breaking ONE diagram apart into a scatter of raster fragments plus
disconnected lines of text (a real-world case, see spec §2-ter.1: 3 out of 3
real infographics in the test excerpt fell apart exactly this way). This
module detects such groups BEFORE the document is handed to mammoth, cuts
them out whole (replacing them with a text sentinel that mammoth carries
through as ordinary text in the same spot), collects the ids of the nested
media (so the fallback pass ``converters._docx_image_markers`` doesn't
duplicate them), and collects the group's text captions (zero-loss in case
VLM is unavailable).

Detection: a top-level body block contains ``mc:AlternateContent`` whose
``mc:Choice`` carries a ``wpg:wgp`` (the modern DrawingML shape group) — a
signal that is both specific and reliable (2026-07-20 prototype: 3 out of 3
real diagrams, 0 false positives).

Second category (kind="chart", §2-ter ultimate test, 2026-07-20): a bare
``w:drawing`` with a ``c:chart`` anchor OUTSIDE ``mc:AlternateContent`` — a
classic native Word chart (data lives in embeddings/*.xlsx). It has no
Fallback image at all: mammoth simply doesn't see it — a "silent loss"
class of bug (real-world case: a CAPEX/OPEX bar chart in an actual report).
Handled by the same pipeline — sentinel -> marker -> soffice render: the
mini-docx inherits the ENTIRE zip of the original (see
``extract_group_docx``), so chart parts/rels/xlsx come along automatically.
Next-generation chartEx diagrams (sunburst etc.) are DELIBERATELY excluded
here: by construction, Word puts a ready-made PNG rendering for them in
``mc:Fallback`` — that gets picked up by the regular mammoth inline-image
path (position + VLM for free).
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from . import chart_render
from .chart_data import ChartData, parse_chart

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

SENTINEL_PREFIX = "DOCXGROUPSENTINEL"
# \**...\** absorbs the bold wrapping markdownify may add: the sentinel inherits
# the rPr of the run whose content it replaced (real case — a bold block in the
# fixture).
_SENTINEL_SCAN_RE = re.compile(r"\**" + SENTINEL_PREFIX + r"(?P<id>[0-9a-f]{12})\**")
_NUMERIC_JUNK_RE = re.compile(r"^-?\d+$")  # posOffset/extent coordinates in itertext()


def _q(prefix: str, local: str) -> str:
    return f"{{{_NS[prefix]}}}{local}"


@dataclass(frozen=True)
class DocxGroup:
    id12: str
    media_ids: frozenset[str]
    captions: tuple[str, ...]
    kind: str = "group"  # "group" (wpg shape group) | "chart" (native c:chart)
    # kind="chart" ONLY (spec chart-data-extraction §4.2): parsed chart data for
    # data-driven resolution (inject_group_markers). kind="group" is always
    # None — the group path is left unchanged, still handled by VLM.
    chart_data: ChartData | None = None


def _rel_targets(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """rId -> Target for ``part`` (e.g. ``word/document.xml``), from its sibling .rels."""
    rels_name = f"{posixpath.dirname(part)}/_rels/{posixpath.basename(part)}.rels"
    if rels_name not in z.namelist():
        return {}
    root = etree.fromstring(z.read(rels_name))
    return {rel.get("Id"): rel.get("Target") for rel in root if rel.get("Id")}


def _group_media_ids(
    ac: Any, rel_targets: dict[str, str], z: zipfile.ZipFile, names: set[str]
) -> frozenset[str]:
    ids: set[str] = set()
    for el in ac.iter():
        for attr in ("embed", "id", "link"):
            rid = el.get(_q("r", attr))
            if rid is None or rid not in rel_targets:
                continue
            media = posixpath.normpath(posixpath.join("word", rel_targets[rid]))
            if media.startswith("word/media/") and media in names:
                ids.add(hashlib.sha256(z.read(media)).hexdigest()[:12])
    return frozenset(ids)


def _filter_caption_texts(texts: Any) -> tuple[str, ...]:
    """Shared text filter for captions: ``itertext()`` also pulls in numeric
    coordinate junk (``wp:posOffset``/``a:ext`` carry their value as the
    element's text content, not an attribute) — we drop strings made up
    entirely of digits (real captions contain letters); dedup in order of
    appearance (proofErr sometimes splits a word across several runs — we
    don't stitch them back together, just pass them through honestly as-is)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        s = t.strip()
        if not s or _NUMERIC_JUNK_RE.match(s) or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return tuple(out)


def _group_captions(ac: Any) -> tuple[str, ...]:
    """Group text (captions under the marker, zero-loss without VLM)."""
    return _filter_caption_texts(ac.itertext())


def _chart_root(
    drawing: Any, rel_targets: dict[str, str], z: zipfile.ZipFile, names: set[str]
) -> Any | None:
    """The parsed chart part (``word/charts/chartN.xml``) referenced by the
    ``w:drawing`` anchor — shared resolution used for both captions
    (``_chart_captions``) AND data (``chart_data.parse_chart``, spec
    chart-data-extraction §4.2). None means the anchor/rel/part is
    unreachable (malformed OOXML, honestly skipped).

    KNOWN LATENT RISK (not yet confirmed in any real document, flagged
    2026-08-05 while root-causing a confirmed sibling bug in
    ``xlsx_charts._chart_anchors``): this uses a singular ``.find()``, so if
    a single bare ``w:drawing`` ever legitimately carried more than one
    ``c:chart`` descendant, every chart after the first would be silently
    dropped — same shape as the xlsx bug. Not fixed here: no real fixture in
    the current 15-document docx corpus triggers this (unlike xlsx's
    confirmed 3-file case), and unlike the xlsx fix (self-contained inside
    one function), fixing this would need ``_iter_chart_drawings``/its
    caller to potentially yield multiple entries per drawing — a real
    restructuring, not a minimal patch, for an unconfirmed case. Revisit if
    a real document ever surfaces this shape."""
    chart_ref = drawing.find(f".//{_q('c', 'chart')}")
    rid = chart_ref.get(_q("r", "id")) if chart_ref is not None else None
    if rid is None or rid not in rel_targets:
        return None
    part = posixpath.normpath(posixpath.join("word", rel_targets[rid]))
    if part not in names:
        return None
    return etree.fromstring(z.read(part))


def _chart_captions(chart_root: Any | None) -> tuple[str, ...]:
    """Chart title from its chart part (``c:title``): the ``w:drawing`` anchor
    itself carries no text (data and title live in
    ``word/charts/chartN.xml``). We take ONLY the title, not the whole
    part — otherwise captions would get flooded with axis/category/value
    labels."""
    if chart_root is None:
        return ()
    title = chart_root.find(f".//{_q('c', 'title')}")
    if title is None:
        return ()
    return _filter_caption_texts(title.itertext())


def _iter_group_acs(body: Any) -> list[tuple[Any, Any]]:
    """Top-level (block, ac) pairs for every composite group in ``body`` — the
    shared detection point for ``extract_and_strip_groups``/
    ``extract_group_docx`` (the sole criterion: ``mc:Choice`` carries
    ``wpg:wgp``, see the module docstring)."""
    pairs: list[tuple[Any, Any]] = []
    for block in list(body):
        for ac in block.findall(f".//{_q('mc', 'AlternateContent')}"):
            choice = ac.find(_q("mc", "Choice"))
            if choice is None or choice.find(f".//{_q('wpg', 'wgp')}") is None:
                continue
            pairs.append((block, ac))
    return pairs


def _iter_chart_drawings(body: Any) -> list[tuple[Any, Any]]:
    """Top-level (block, drawing) pairs for bare chart anchors: a ``w:drawing``
    with a ``c:chart`` OUTSIDE ``mc:AlternateContent``. Charts nested inside
    an AC (chartEx: Choice carries the cx-diagram, Fallback carries a
    ready-made PNG image from Word) are deliberately skipped — they're
    picked up by the mammoth inline-image path (see the module
    docstring)."""
    pairs: list[tuple[Any, Any]] = []
    for block in list(body):
        for drawing in block.findall(f".//{_q('w', 'drawing')}"):
            if drawing.find(f".//{_q('c', 'chart')}") is None:
                continue
            anc = drawing.getparent()
            inside_ac = False
            while anc is not None and anc is not block:
                if anc.tag == _q("mc", "AlternateContent"):
                    inside_ac = True
                    break
                anc = anc.getparent()
            if not inside_ac:
                pairs.append((block, drawing))
    return pairs


def _iter_objects(body: Any) -> list[tuple[Any, Any, str]]:
    """All objects in ``body`` that get cut out: (block, element, kind) — the
    single entry point shared by ``extract_and_strip_groups``/
    ``extract_group_docx``."""
    objects = [(b, el, "group") for b, el in _iter_group_acs(body)]
    objects += [(b, el, "chart") for b, el in _iter_chart_drawings(body)]
    return objects


def extract_and_strip_groups(raw: Path | bytes) -> tuple[bytes, list[DocxGroup]]:
    """Return (rewritten docx zip, groups found). Zero groups -> the bytes are
    BYTE-FOR-BYTE identical to ``raw.read_bytes()`` (a document with no
    composite groups — the majority of docx files — pays only for a single
    detection pass, with zero risk of accidentally corrupting the content).

    ``raw`` can be ``bytes`` (refigure accepts in-memory input, §2
    stage2-public-api-wrapper) — used as-is, without reading from disk.

    A malformed ``word/document.xml`` (not XML at all, or XML with no
    ``w:body``) gets the same honest pass-through as a missing part
    altogether (found by a Hypothesis test, stage2-public-api-wrapper: an
    empty ``document.xml`` raised ``etree.XMLSyntaxError``, XML with no
    ``w:body`` raised ``TypeError`` on ``list(None)`` in ``_iter_objects``)."""
    orig = raw.read_bytes() if isinstance(raw, Path) else raw
    with zipfile.ZipFile(io.BytesIO(orig)) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            return orig, []
        rel_targets = _rel_targets(z, "word/document.xml")
        try:
            tree = etree.fromstring(z.read("word/document.xml"))
        except etree.XMLSyntaxError:
            return orig, []
        body = tree.find(_q("w", "body"))
        if body is None:
            return orig, []
        groups: list[DocxGroup] = []
        for _block, el, kind in _iter_objects(body):
            media_ids = _group_media_ids(el, rel_targets, z, names)
            parsed_chart_data: ChartData | None = None
            if kind == "group":
                captions = _group_captions(el)
            else:
                chart_root = _chart_root(el, rel_targets, z, names)
                captions = _chart_captions(chart_root)
                if chart_root is not None:
                    parsed_chart_data = parse_chart(chart_root)
            id12 = hashlib.sha256(etree.tostring(el)).hexdigest()[:12]
            groups.append(
                DocxGroup(
                    id12=id12,
                    media_ids=media_ids,
                    captions=captions,
                    kind=kind,
                    chart_data=parsed_chart_data,
                )
            )

            run = el.getparent()
            sentinel = etree.Element(_q("w", "t"))
            sentinel.set(_XML_SPACE, "preserve")
            sentinel.text = f"{SENTINEL_PREFIX}{id12}"
            run.replace(el, sentinel)
        if not groups:
            return orig, []
        new_doc_xml = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zo:
            for n in z.namelist():
                zo.writestr(n, new_doc_xml if n == "word/document.xml" else z.read(n))
        return buf.getvalue(), groups


def extract_group_docx(raw: Path, id12: str) -> bytes | None:
    """Rebuild a mini-docx that contains ONLY the block with the given group
    (+ the original's ``sectPr`` for page geometry) — for an isolated
    render via soffice (``figures_vlm._render_docx_group``). 2026-07-20
    prototype: all 3 diagrams in the test excerpt rendered COMPLETELY this
    way. None means a group with this id12 wasn't found on re-detection
    (did ``raw`` change?)."""
    with zipfile.ZipFile(raw) as z:
        names = z.namelist()
        tree = etree.fromstring(z.read("word/document.xml"))
        body = tree.find(_q("w", "body"))
        blocks = list(body)
        target = next(
            (
                block
                for block, el, _kind in _iter_objects(body)
                if hashlib.sha256(etree.tostring(el)).hexdigest()[:12] == id12
            ),
            None,
        )
        if target is None:
            return None
        sect = blocks[-1] if etree.QName(blocks[-1]).localname == "sectPr" else None
        for block in blocks:
            body.remove(block)
        body.append(target)
        if sect is not None and sect is not target:
            body.append(sect)
        new_doc_xml = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zo:
            for n in names:
                zo.writestr(n, new_doc_xml if n == "word/document.xml" else z.read(n))
        return buf.getvalue()


def _render_group_marker(id12: str, captions: tuple[str, ...], kind: str = "group") -> str:
    # The literal text stays in English, like the rest of the marker grammar
    # (spec convert-knowledge-seam-hardening §1, item B17): the marker text ends
    # up in doc.md, which means it ends up in FTS tokens and the chunk vector;
    # the curator's language would be an impurity there — the corpus's machine
    # contract is monolingual. Russian is confined to logs/CLI.
    caption_line = "; ".join(captions) if captions else "(no captions)"
    noun = "composite" if kind == "group" else "chart"
    return (
        f"\n\n> [Figure, docx {kind} {id12} — {noun} content not analyzed]\n"
        f"> captions: {caption_line}\n\n"
    )


def inject_group_markers(text: str, groups: list[DocxGroup]) -> tuple[str, int]:
    """Replace the text sentinels (which survived mammoth+markdownify in the
    spot where the group was cut out, see ``extract_and_strip_groups``) with
    the final block.

    kind="group" — LEFT UNCHANGED, the honest VLM marker (spec
    chart-data-extraction §4.2/§2: the group path stays on VLM). kind="chart"
    — data-driven (spec §4.2): ``chart_render.render_chart(group.chart_data)``
    if the extraction is non-empty; an empty extraction (no numCache etc.)
    -> THE SAME honest marker as before (caption fallback, zero-loss without
    VLM). Position is preserved exactly — the sentinel is replaced IN-PLACE
    (spec §4.4: docx provenance IS the position in the stream itself, no
    separate line needed, unlike xlsx).

    Returns ``(text, rendered_count)`` — the second element is needed by the
    caller (``refigure.docx``) for ``ConversionResult.charts_rendered``, so
    it doesn't have to duplicate this same check (§3
    stage2-public-api-wrapper)."""
    if not groups:
        return text, 0
    by_id = {g.id12: g for g in groups}
    rendered_count = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal rendered_count
        group = by_id.get(m.group("id"))
        if group is None:  # practically impossible (id12 is a sha256), but don't crash
            return m.group(0)
        if group.kind == "chart" and group.chart_data is not None:
            rendered = chart_render.render_chart(group.chart_data)
            if rendered is not None:
                rendered_count += 1
                return f"\n\n{rendered}\n\n"
        return _render_group_marker(group.id12, group.captions, group.kind)

    new_text = _SENTINEL_SCAN_RE.sub(_replace, text)
    return new_text, rendered_count


def all_media_ids(groups: list[DocxGroup]) -> frozenset[str]:
    """Union of media_ids across all groups — the "absorbed" ids for the
    fallback pass (``converters._docx_image_markers(raw, placed=...)``):
    pieces of a group must not resurface again, neither inline nor under
    ``## Figures (position unknown)``."""
    if not groups:
        return frozenset()
    return frozenset().union(*(g.media_ids for g in groups))
