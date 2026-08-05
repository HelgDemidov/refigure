"""VLM interpretation of composite DOCX figures (stage 4b), gated behind
``Config.use_vlm`` — not active/announced in v1
(``docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md`` §1/§5).

Scope is hard-limited to DOCX: XLSX has no VLM path at all (its native
charts resolve data-driven, at conversion time — an unreadable chart stays
an honest static marker forever, no escalation), and PDF is out of the
project's scope entirely.

Architectural redesign from the source pipeline's ``figures_vlm.py`` (a
separate pass over an already-written ``doc.md`` on disk, cache keyed by
``raw.parent / ".figures.yaml"``): refigure has neither a file on disk (a
single in-memory ``convert()`` call, input can be ``bytes`` with no parent
directory at all) nor that on-disk cache convention. ``enhance_docx_markdown``
below scans a markdown STRING instead of a file, and both the VLM HTTP
client (``VlmClient``, ``vlm/client.py``) and the response cache
(``VlmCacheBackend``, ``vlm/cache.py``) are pluggable Protocols defined in
``api.py`` — not a hardcoded OpenRouter call or a hardcoded sidecar file.

Guard is the first same-package-import-adjacent statement in this file
(before ``from ..core import chart_render``/``from ..docx import groups``
below): a module-level
``try/except ImportError`` guard is only effective if it runs before any
OTHER same-package import that could itself transitively raise an
unguarded ``ImportError`` for the same dependency — the exact bug class PR
#8 found in ``xlsx.py`` (guard ran after ``xlsx_charts.py``'s own
unguarded ``openpyxl`` import), see the ``project_extras_isolation_bug``
memory. ``chart_render``/``docx_groups`` are safe today (neither transitively
imports ``pdfplumber``), but that safety is circumstantial, not contractual
— the ordering discipline holds regardless.
"""

from __future__ import annotations

from ..api import MissingOptionalDependencyError

try:
    import pdfplumber
except ImportError as exc:
    raise MissingOptionalDependencyError(
        "refigure[vlm] is required to use Config(use_vlm=True)"
    ) from exc

import base64
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..api import Config, VlmCacheBackend, VlmClient
from ..core import chart_render, zipsafe
from ..docx import groups as docx_groups
from .cache import InMemoryCacheBackend
from .client import OpenRouterClient

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

# --- marker grammar: docx-only (mirrors docx.py's/docx/groups.py's own marker
# text exactly — verified 2026-08-05 by running both against a live fixture,
# see docs/vlm/vlm-layer-port/vlm-layer-port-2026-08-05.md §2) -----------------------------------

_DOCX_IMAGE_MARKER_RE = re.compile(
    r"^> \[Image, docx media (?P<id>[0-9a-f]{12}) — raster content not analyzed\]$",
    re.MULTILINE,
)
# Group-only (kind="group"), by construction: a native c:chart (kind="chart")
# resolves data-driven in docx_groups.inject_group_markers, before this stage
# ever runs — an empty extraction leaves the SAME marker text, but this regex
# only matches the literal "docx group" noun, never "docx chart". A chart
# with no numCache stays an honest static marker forever — see the module
# docstring and docx/groups.py's own docstring for the full rationale.
_DOCX_GROUP_MARKER_RE = re.compile(
    r"^> \[Figure, docx group (?P<id>[0-9a-f]{12}) — composite content not analyzed\]\n"
    r"> captions: (?P<witness>.*)$",
    re.MULTILINE,
)

# Raster mime-types word/media/* actually carries (spec §2-bis: the image is
# already its own file — no render needed, only a content-type for the
# data-URI). Legacy vector OLE previews (wmf/emf) and svg are NOT raster —
# VLM as a vision input won't accept them; such a marker is honestly skipped.
_DOCX_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

FIG_PROMPT = """Describe this figure/diagram, cropped from a document page.
Output in English, in two parts:

1. Prose description (ALWAYS include this): full sentences describing what the
   figure shows. Transcribe every text label exactly as printed (verbatim, do
   not translate or paraphrase). Describe spatial and logical relationships
   between elements (what connects to what, what contains what, ordering).

   CHART + DATA TABLE RULE: if a data table is visible in the same crop as a
   chart, the chart is the ONLY subject of your description — never the
   table. Read the table silently to get exact values right, then leave it
   out of the output entirely: do not name its columns, do not state its row
   count, do not restate its rows. Every number you write must be attached to
   a chart element (a bar, a segment, a point, an axis tick) — never written
   as a standalone transcription of a table cell. If a sentence you're about
   to write only reports "the table says X" with no chart element attached,
   drop that sentence.

   CROSS-CHECK: when a chart element's own printed value (a data label, an
   axis-anchored point) and a table cell clearly represent the SAME number
   (not just a related statistic — e.g. a table of percentages next to a
   chart of raw counts is related, not the same number), read both and use
   whichever rendering is printed more clearly and unambiguously (table text
   is usually cleaner-printed than small/rotated/overlapping in-chart data
   labels, but not always — judge per number). This cross-check happens
   entirely inside your own reasoning — never mention the comparison, never
   flag a discrepancy, never say which source you used; only its effect
   (the correct digits ending up in your chart description) should be
   visible.

2. Mermaid diagram — include a ```mermaid fence whenever the figure fits one
   of the categories below (omit entirely otherwise — e.g. scatter plots,
   matrices, grids, photos, or anything with no clean structural fit). Use
   the chart's own printed/labeled values (axis labels, data labels, or a
   silently-read source table) for every number — never a value you only
   visually estimated from bar height or segment angle.

   - Flowchart / sequence / hierarchy — ``flowchart``/``graph``. Include ONLY
     edges that are visually present (arrows/connectors you can actually
     see) — never infer or guess a connection that is not drawn. Wrap every
     node label in double quotes, e.g. A["Label"] (unquoted labels containing
     punctuation break the mermaid parser). If fill color visually groups
     nodes into categories (a shared background marking a functional
     grouping, not just decoration), preserve that grouping via
     `classDef`/`class` — never per-node `style` for a repeated category,
     that's for a rare one-off only:
     - At most 4-5 classes, one per distinct color you actually see — never
       a unique hex per node, and never force a preset vocabulary like
       danger/success/warning onto colors that don't represent that. Name
       each class after what the group actually IS (its shared role/topic),
       not a generic severity word.
     - Always set `color` (text) together with `fill` in the same classDef,
       e.g. `classDef components fill:#f4b8cf,color:#000` — never `fill`
       alone; the node's text must stay readable regardless of viewer theme.
     - Omit classDef entirely if color is purely decorative or uniform
       across all nodes — do not invent grouping that isn't visually there.
     - If you also style a `subgraph`, give it an explicit id separate from
       its title (`subgraph sg1["Title"]`, then `style sg1 fill:...`) —
       styling a bare multi-word subgraph title directly breaks the parser.
   - Pie / donut chart — ``pie``: `pie title "..."` then one `"Label" :
     value` line per slice.
   - Bar chart, line chart, or bar+line combo (including grouped bars or a
     constant reference/average line drawn across categories) —
     ``xychart-beta``: first line exactly `xychart-beta`, then `x-axis
     [cat1, cat2, ...]`, `y-axis "label" min --> max`, and one `bar [...]`
     and/or `line [...]` array per series (same length as x-axis; a constant
     reference line repeats one value across the array).
   - Radar / spider chart — ``radar-beta``: `axis id1["Label1"],
     id2["Label2"], ...` then one `curve id["Series"]{value1, value2, ...}`
     line per series, values in the same order as the axes. The `id` before
     each bracketed label is REQUIRED and must be a single bare word with no
     spaces or quotes (invent a short slug for multi-word series, e.g. series
     "Regional Avg" -> id `reg`) — `curve reg["Regional Avg"]{0.49, 0.51}` is
     correct, `curve "Regional Avg"{0.49, 0.51}` (label alone, no id) is
     invalid syntax and will fail to render.

Output ONLY the prose description, optionally followed by a ```mermaid code
fence — no other commentary."""

# --- response sanitization (applied on every injection, including a cache
# hit: the cache stores the RAW model response, sanitization lives on the
# output side, so an already-paid-for response gets it too without a new
# call) ------------------------------------------------------------------

_VLM_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_MERMAID_FENCE_RE = re.compile(
    r"^```mermaid[ \t]*\n(?P<code>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL
)


def _demote_headings(md: str) -> str:
    """``#``-headings in the VLM response -> bold: document structure belongs
    to the converter, not the model. A heading inside the injected block
    would otherwise re-parent every section after it in any downstream
    heading-based chunker. Lines inside a code fence are left untouched
    (``# comment`` is not a heading there)."""
    out: list[str] = []
    in_fence = False
    for line in md.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else _VLM_HEADING_RE.match(stripped)
        out.append(f"**{m.group(1)}**" if m is not None else line)
    return "\n".join(out)


def _gate_mermaid_fences(md: str) -> str:
    """Every ```mermaid fence in the response goes through a REAL render
    (``chart_render.mermaid_renders``); one that fails to render degrades to
    ```text — the prose is kept, the false promise "this is a valid
    diagram" is dropped. Same discipline already applied to data-driven
    charts (``chart_render.py``): syntactic validity is not the same
    guarantee as "the real renderer accepts this" — a VLM response has
    less reason to be trusted here, not more."""

    def _replace(m: re.Match[str]) -> str:
        if chart_render.mermaid_renders(m.group("code").rstrip("\n")):
            return m.group(0)
        return m.group(0).replace("```mermaid", "```text", 1)

    return _MERMAID_FENCE_RE.sub(_replace, md)


def sanitize_vlm_markdown(md: str) -> str:
    """Sanitize a model response before injecting it into the markdown
    output. Idempotent: headings are already demoted and a degraded fence is
    no longer ```mermaid, so running this twice is a no-op."""
    return _gate_mermaid_fences(_demote_headings(md))


# --- witness gate: cross-check a VLM description against the document's OWN
# independently-extracted captions (docx/groups.py's `captions`) ------------


def token_recall(reference: str, candidate: str) -> float:
    """Fraction of UNIQUE alphabetic tokens in ``reference`` also found
    anywhere in ``candidate`` (case-insensitive; Unicode letters — accents —
    count as ordinary letters). A ``reference`` with no alphabetic token at
    all -> 1.0 (nothing to lose, recall is trivially complete)."""
    word_re = re.compile(r"[^\W\d_]+", re.UNICODE)
    reference_words = {w.lower() for w in word_re.findall(reference)}
    if not reference_words:
        return 1.0
    candidate_words = {w.lower() for w in word_re.findall(candidate)}
    return len(reference_words & candidate_words) / len(reference_words)


def numeric_counter(text: str) -> Counter[str]:
    """Multiset of numeric tokens (``\\d+``) in ``text``."""
    return Counter(re.findall(r"\d+", text))


_NUMERIC_DIVERGENCE_TOKEN_CAP = 10  # tokens per side shown in a defect string


def format_missing_side(nums: Counter[str], other: Counter[str]) -> str:
    """The distinct numbers present in ``nums`` but absent from ``other``,
    sorted numerically for determinism, capped at
    ``_NUMERIC_DIVERGENCE_TOKEN_CAP``."""
    missing = sorted((nums - other).keys(), key=int)
    if not missing:
        return "none"
    shown = missing[:_NUMERIC_DIVERGENCE_TOKEN_CAP]
    rest = len(missing) - len(shown)
    return ",".join(shown) + (f"…+{rest}" if rest > 0 else "")


def witness_defects(witness: str, markdown: str, obj_id: str, *, min_recall: float) -> list[str]:
    """Cross-check a VLM figure description against an INDEPENDENT witness —
    the group's own captions, deterministically extracted by
    ``docx/groups.py`` itself (zero-loss fallback text, not model output).

    Applies ONLY to composite groups, never to standalone images: a
    standalone ``> [Image, docx media ...]`` marker carries no captions at
    all (``_DOCX_IMAGE_MARKER_RE`` has no witness group) — this mirrors the
    source pipeline exactly (its ``docx_image_matches`` loop never calls
    ``witness_defects`` either), easy to miss when porting.

    Signal, not a hard failure: a mismatch flags "look at this one," a human
    remains the final arbiter. The numeric check is ONE-SIDED: witness
    numbers missing from the description are suspicious; the reverse is
    legitimate (the VLM reads values off the chart itself that the caption
    never mentioned). An empty witness (standalone images never reach this
    function, but a group with genuinely empty captions can) -> gate does
    not apply."""
    if not witness.strip():
        return []
    defects: list[str] = []
    recall = token_recall(witness, markdown)
    if recall < min_recall:
        defects.append(f"figure-witness-recall: {obj_id} {recall:.2f}")
    witness_nums = numeric_counter(witness)
    figure_nums = numeric_counter(markdown)
    if witness_nums - figure_nums:
        defects.append(
            f"figure-witness-numeric: {obj_id} "
            f"witness_only=[{format_missing_side(witness_nums, figure_nums)}]"
        )
    return defects


# --- rendering: standalone images (no render, already a raster file) and
# composite groups (isolated mini-docx -> soffice -> PDF -> content-bbox crop)
# --------------------------------------------------------------------------

FIGURE_RENDER_DPI = 144
FIGURE_JPEG_QUALITY = 90  # figures are color/fine-detail; per-document volume is small
SOFFICE_RENDER_TIMEOUT = 60  # one (~1-page) object, headless soffice — seconds


def _source_label(source: Path | bytes) -> str:
    """Human-readable name for log messages — ``source`` may be ``bytes``
    with no filename at all (refigure's in-memory input path)."""
    return source.name if isinstance(source, Path) else "<in-memory document>"


def _docx_media_uri(source: Path | bytes, marker_id: str, *, raw_name: str) -> str | None:
    """Find the ``word/media/*`` file whose sha256[:12] matches ``marker_id``
    and return it as a data-URI. No render/crop needed — it's already a
    standalone raster file (unlike a composite group, where a page region
    has to be rendered from a mini-document). A non-raster format (svg/wmf/
    emf — legacy vector OLE previews, not in ``_DOCX_IMAGE_MIME``) ->
    ``None`` + warning: VLM as a vision input won't accept it."""
    z_source = source if isinstance(source, Path) else io.BytesIO(source)
    with zipfile.ZipFile(z_source) as z:
        for name in z.namelist():
            if not name.startswith("word/media/"):
                continue
            data = z.read(name)
            if hashlib.sha256(data).hexdigest()[:12] != marker_id:
                continue
            ext = name.rsplit(".", 1)[-1].lower()
            mime = _DOCX_IMAGE_MIME.get(ext)
            if mime is None:
                logger.warning(
                    "%s: media %s — format .%s is not raster (VLM won't accept it), marker skipped",
                    raw_name,
                    marker_id,
                    ext,
                )
                return None
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    logger.warning(
        "%s: media %s not found in word/media/* on re-detection "
        "(did the source change?) — marker skipped",
        raw_name,
        marker_id,
    )
    return None


def _soffice_available() -> bool:
    return shutil.which("soffice") is not None


def _content_bbox(page: Any) -> BBox | None:
    """Dense bbox of a page's visible content (union of rects/curves/images/
    chars) — a rendered mini-docx page carries a lot of empty margin around
    the group itself, this crops down to just the content. ``None`` means an
    empty page (shouldn't happen for a non-empty group, but doesn't crash).

    Clamped to the page's own bbox: LibreOffice can lay an object out
    partially off-page (observed live: a phantom chart element with a
    negative top coordinate) — anything off-page isn't visible in the PDF
    either (the page itself clips it), and an unclamped bbox makes
    ``page.crop()`` raise; clamping loses nothing visible."""
    xs0: list[float] = []
    tops: list[float] = []
    xs1: list[float] = []
    bottoms: list[float] = []
    for collection in (page.rects, page.curves, page.images, page.chars):
        for el in collection:
            xs0.append(el["x0"])
            xs1.append(el["x1"])
            tops.append(el["top"])
            bottoms.append(el["bottom"])
    if not xs0:
        return None
    px0, ptop, px1, pbottom = page.bbox
    x0, top = max(min(xs0), px0), max(min(tops), ptop)
    x1, bottom = min(max(xs1), px1), min(max(bottoms), pbottom)
    if x0 >= x1 or top >= bottom:
        return None
    return (x0, top, x1, bottom)


def _render_via_soffice(
    doc_bytes: bytes, *, suffix: str, raw_name: str, obj_id: str, obj_kind: str
) -> str | None:
    """Render an isolated mini-document via headless LibreOffice -> PDF ->
    crop to visible content (``_content_bbox``) -> JPEG data-URI. Requires
    the system ``soffice`` binary — its absence/failure degrades to
    ``None`` + warning, the marker+captions stay as an honest fallback
    (zero-loss without VLM), never a hard failure."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        doc_path = tmp_dir / f"obj{suffix}"
        doc_path.write_bytes(doc_bytes)
        try:
            result = subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_dir),
                    str(doc_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=SOFFICE_RENDER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "%s: soffice did not finish within %ss on %s %s — marker skipped",
                raw_name,
                SOFFICE_RENDER_TIMEOUT,
                obj_kind,
                obj_id,
            )
            return None
        pdf_path = tmp_dir / "obj.pdf"
        if result.returncode != 0 or not pdf_path.exists():
            logger.warning(
                "%s: soffice failed to render %s %s (%s) — marker skipped",
                raw_name,
                obj_kind,
                obj_id,
                result.stderr[-300:],
            )
            return None
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page = pdf.pages[0]
                bbox = _content_bbox(page)
                cropped = page.crop(bbox) if bbox is not None else page
                img = cropped.to_image(resolution=FIGURE_RENDER_DPI).original.convert("RGB")
        except Exception as exc:  # noqa: BLE001 — rendering the PDF can also fail
            logger.warning(
                "%s: rendering PDF for %s %s failed (%s) — marker skipped",
                raw_name,
                obj_kind,
                obj_id,
                exc,
            )
            return None
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=FIGURE_JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _render_docx_group(source: Path | bytes, id12: str, *, raw_name: str) -> str | None:
    """Composite group -> isolated mini-docx (``docx_groups.extract_group_docx``)
    -> ``_render_via_soffice``."""
    if not _soffice_available():
        logger.warning(
            "%s: soffice not installed — group %s skipped (install LibreOffice, "
            "e.g. `apt install libreoffice-writer`)",
            raw_name,
            id12,
        )
        return None
    mini_docx = docx_groups.extract_group_docx(source, id12)
    if mini_docx is None:
        logger.warning(
            "%s: group %s not found on re-detection (did the source change?) — marker skipped",
            raw_name,
            id12,
        )
        return None
    return _render_via_soffice(
        mini_docx, suffix=".docx", raw_name=raw_name, obj_id=id12, obj_kind="group"
    )


# --- injection grammar: bounded block (open marker, sanitized body,
# terminator) — round-trips producer/consumer by construction, both sides
# live in this one module (folded in from the source pipeline's tiny,
# VLM-specific core/markers.py — too small for its own core module here) ---

_INJECTION_END_PREFIX = "> [/VLM interpretation "


def _injection_open(head: str, model: str) -> str:
    return f"> [{head} — VLM interpretation ({model}); reconstruction, verify against original]"


def _injection_end(address: str) -> str:
    return f"{_INJECTION_END_PREFIX}{address}]"


def _render_injected(head: str, address: str, model: str, markdown: str) -> str:
    return (
        f"{_injection_open(head, model)}\n\n"
        f"{sanitize_vlm_markdown(markdown)}\n\n"
        f"{_injection_end(address)}"
    )


def _render_injected_docx_image(marker_id: str, model: str, markdown: str) -> str:
    return _render_injected(
        f"Image, docx media {marker_id}", f"docx media {marker_id}", model, markdown
    )


def _render_injected_docx_group(id12: str, model: str, markdown: str) -> str:
    return _render_injected(f"Figure, docx group {id12}", f"docx group {id12}", model, markdown)


def _call_client(
    client: VlmClient, prompt: str, image_uri: str, *, model: str, raw_name: str, obj_id: str
) -> str | None:
    """Shared client-call + failure handling: one region's VLM failure must
    never abort the whole conversion — the marker stays an honest "not
    analyzed" fallback."""
    try:
        return client.send(prompt, image_uri, model=model)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("%s: VLM call for %s failed (%s) — marker left as-is", raw_name, obj_id, exc)
        return None


def _resolve_api_key(config: Config) -> str:
    key = config.vlm_api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "Config(use_vlm=True) needs a VLM API key: set Config.vlm_api_key, "
            "the OPENROUTER_API_KEY environment variable, or supply a custom Config.vlm_client"
        )
    return key


def enhance_docx_markdown(
    markdown: str, source: Path | bytes, *, config: Config
) -> tuple[str, bool, list[str]]:
    """Scan ``markdown`` (a STRING, not a file — refigure has no ``doc.md``
    on disk) for the bare VLM-eligible markers ``docx.py``'s ``convert()``
    already produces, and inject a VLM interpretation for each — cache-hit
    entries are re-injected fully offline, a cache-miss triggers a real
    render + VLM call (and, only then, lazily resolves an API key/client —
    see ``_resolve_api_key``, a fully cache-hit conversion needs neither).

    Returns ``(markdown, vlm_used, warnings)``. ``vlm_used`` is ``True`` iff
    at least one marker was actually resolved (cache-hit or a fresh call);
    a document with bare markers but where every resolution attempt failed
    (soffice missing, VLM call failing, ...) returns ``False`` — the
    markers are left exactly as ``docx.py`` produced them, an honest
    zero-loss result either way.

    Re-runs ``zipsafe.check_archive`` on ``source`` even though
    ``docx.py``'s ``convert()`` already checked it once: unlike the source
    pipeline's ``apply_figures_pass`` (a genuinely separate process/stage,
    where a second check was the ONLY check for its own re-entrant read of
    ``raw``), this function is also a public entry point on its own — a
    caller invoking it directly, bypassing ``convert()``, must get the same
    protection ``convert()``'s own callers get. A failure here degrades to
    "VLM enhancement skipped" (a warning), not a raised exception: any
    markdown already produced by the caller remains valid on its own."""
    image_matches = list(_DOCX_IMAGE_MARKER_RE.finditer(markdown))
    group_matches = list(_DOCX_GROUP_MARKER_RE.finditer(markdown))
    if not image_matches and not group_matches:
        return markdown, False, []

    try:
        zipsafe.check_archive(source)
    except (zipsafe.ArchiveBombSuspected, zipfile.BadZipFile) as exc:
        logger.warning("VLM enhancement skipped — archive re-check failed (%s)", exc)
        return markdown, False, [f"vlm enhancement skipped: {exc}"]

    raw_name = _source_label(source)
    cache: VlmCacheBackend = config.vlm_cache or InMemoryCacheBackend()
    model = config.vlm_model
    resolved_client: VlmClient | None = config.vlm_client

    def _get_client() -> VlmClient:
        nonlocal resolved_client
        if resolved_client is None:
            resolved_client = OpenRouterClient(api_key=_resolve_api_key(config))
        return resolved_client

    warnings: list[str] = []
    vlm_used = False
    replacements: list[tuple[int, int, str]] = []

    for m in image_matches:
        marker_id = m.group("id")
        entry = cache.get(marker_id)
        if entry is None:
            data_uri = _docx_media_uri(source, marker_id, raw_name=raw_name)
            if data_uri is None:
                continue
            text = _call_client(
                _get_client(),
                FIG_PROMPT,
                data_uri,
                model=model,
                raw_name=raw_name,
                obj_id=marker_id,
            )
            if text is None:
                continue
            entry = {"model": model, "markdown": text}
            cache.set(marker_id, entry)
        vlm_used = True
        replacements.append(
            (
                m.start(),
                m.end(),
                _render_injected_docx_image(marker_id, str(entry["model"]), str(entry["markdown"])),
            )
        )

    for m in group_matches:
        gid = m.group("id")
        witness = m.group("witness")
        entry = cache.get(gid)
        if entry is None:
            data_uri = _render_docx_group(source, gid, raw_name=raw_name)
            if data_uri is None:
                continue
            text = _call_client(
                _get_client(), FIG_PROMPT, data_uri, model=model, raw_name=raw_name, obj_id=gid
            )
            if text is None:
                continue
            entry = {"model": model, "markdown": text}
            cache.set(gid, entry)
        vlm_used = True
        entry_markdown = str(entry["markdown"])
        warnings.extend(
            witness_defects(witness, entry_markdown, gid, min_recall=config.vlm_witness_min_recall)
        )
        replacements.append(
            (
                m.start(),
                m.end(),
                _render_injected_docx_group(gid, str(entry["model"]), entry_markdown),
            )
        )

    if not replacements:
        return markdown, False, warnings

    new_text = markdown
    for start, end, replacement in sorted(replacements, key=lambda t: t[0], reverse=True):
        new_text = new_text[:start] + replacement + new_text[end:]
    return new_text, vlm_used, warnings
