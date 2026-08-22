"""Regenerate docs/assets/demo-vlm-dark.svg (VLM interpretation — README
Demo section, 1st hero as of 2026-08-20, replacing the retired
trichinella-dashboard/bare-marker framing).

Sibling of `gen_demo_asset.py`/`gen_demo_asset_groups.py`/
`gen_demo_asset_docx_chart.py`. Unlike those three (native OOXML chart-data
extraction, zero VLM involved) or this script's own PREVIOUS version
(trichinella dashboard — a before/after of recovered NUMBERS, no rendered
diagram at all, because that group's mermaid type — 4-series xychart-beta —
has no grouped-bar support), this one shows VLM recovering both real
transcribed content AND a genuinely renderable STRUCTURE: a dense wireless-
technology sunburst chart (`iot-report-2022-national-strategies-excerpt.docx`,
standalone image `007cf0f198bc`) that has no native chart data at all (a
screenshot, not an OOXML chart part) and, before the mermaid-type-expansion
work (spec `docs/vlm/mermaid-type-expansion/mermaid-type-expansion-2026-08-20.md`),
had no matching mermaid construct either — VLM could only describe it as
`graph TD`, which laid the whole thing out as an unreadable 17:1-aspect-ratio
strip. `mindmap` (one of that spec's 22 new types) renders it radially
instead — this hero is the concrete "was broken, now works" payoff of that
work, not a synthetic example.

Light+dark, like the other 3 siblings, as of 2026-08-22 — but only the
composite's own canvas (bg/ink/muted/blue) switches per theme; the nested
mindmap panel itself stays permanently dark (`_MINDMAP_PANEL`, a fixed,
non-per-theme dict), because the mindmap's categorical palette is tuned
specifically against a dark surface (see "Palette" below) and mermaid's
`mindmap` diagram type has no reachable per-theme override for it (see
"Direct SVG post-processing" below) — deriving a second, independently
validated light-mode categorical palette just for this one sub-panel was
explicitly rejected in favor of keeping the mindmap panel a fixed dark
card embedded in an otherwise theme-matched composite (explicit user
decision 2026-08-22, superseding this script's earlier dark-only-with-no-
light-variant-at-all design from 2026-08-20). README embeds this via a
`<picture>`/`<source media="prefers-color-scheme">` pair, matching the
other 3 heroes exactly.

**Direct SVG post-processing, not themeVariables — confirmed live, not
assumed:**
- `mindmap`'s per-branch/root fill and text color have NO reachable
  themeVariable path. `cScale0`..`cScaleN` are silently ignored for the
  root node specifically (`.section-root` is unconditionally hardcoded to
  `hsl(240,100%,46%)`, a fixed deep blue, regardless of any themeVariable);
  branch fills DO respond to `cScale1..cScaleN` (confirmed: `cScaleN` ->
  the Nth top-level branch in source order, 1-indexed, root consumes no
  slot), but branch TEXT color does not — dark theme hardcodes `lightgrey`
  for every section's text regardless of the branch's own fill lightness,
  which independently measured as failing WCAG AA against every fill in
  this palette (worst case 1.93:1, `lightgrey` on `#9A988F`, an earlier
  draft's muted-gray choice — see the git history of this file's palette
  comments below for the full contrast-driven iteration). Both root's fill
  and every section's text are therefore overridden after the fact by
  regexing mermaidx's own generated `<style>` rules for the literal
  `lightgrey`/`hsl(240,...)` values it emits — no other override surface
  exists for either.
- Mindmap does NOT need quotes around multi-word/Cyrillic labels (confirmed
  live: `A\\n  B C` parses identically to `A\\n  "B C"`) — the source VLM
  response quoted most labels anyway (habit carried over from this
  prompt's flowchart guidance, where quoting IS required), and mindmap
  renders the quote characters LITERALLY as part of the visible label —
  stripped here before rendering, not fixed in `FIG_PROMPT` (a demo-
  asset-local concern, not a prompt-wide one).
- A real, previously undocumented mindmap parser bug was found producing
  this exact asset: ANY parenthesis pair inside a quoted label breaks
  mermaidx's parser (even a minimal `"Item (note)"` fails) — the cached
  VLM response below was regenerated after fixing `FIG_PROMPT` to forbid
  this (commit `a24f094`, direct-to-main) and rephrase with a dash/colon
  instead; the committed cache is the POST-fix response, not the original
  broken one.

**Palette**: built with the `dataviz` skill (`scripts/validate_palette.js`,
6-check validator) under `--pairs all` — mindmap's radial layout doesn't
guarantee only-physical-neighbours the way a pie's wedge order does, so any
2 of the 5 branches can end up visually adjacent; the stricter assumption.
Exhaustively confirmed (all C(7,4)=35 four-hue extensions of the reference
palette's mandatory "blue" slot tested against `--pairs all`, dark mode):
NO 5-hue subset of this design system's reference categorical palette fully
clears the CVD-separation/normal-vision floors at 5 simultaneous slots —
this is a hard mathematical ceiling of the palette at this surface, not a
search failure. Accepted per explicit user direction (real category count —
matching the source figure's actual 5 top-level branches — takes priority
over strict CVD compliance for this asset); every node always carries a
visible text label regardless, which is the skill's own required secondary-
encoding mitigation for a CVD separation WARN. Root is a 6th, neutral color
distinct from all 5 branches (not reusing one of them) — a real design
requirement from live user review, not incidental.

Iterated live against 3 rounds of real user feedback on the rendered
output, each a genuine correction, not a style preference:
1. A `cScale`-index off-by-one bug scrambled the entire branch->color
   mapping (caught by the user comparing the render against the intended
   assignment, not by any check this script runs).
2. LAN/PAN's and Спутниковая связь's blues were too close to reliably
   tell apart at a glance — Спутниковая связь moved to light pink.
3. Root was left identical to LAN/PAN's blue (an earlier live fix for a
   *different* complaint — root not responding to color changes at all)
   — re-separated to its own neutral pale blue per explicit request, with
   the "Wireless Technologies" label split to 2 lines (`<br/>`) once room
   allowed it to stay centered inside the wider root circle.

**Input crop background — a real bug fixed here**: the source PNG is RGBA
(genuine transparency, confirmed live) — the previous version of this
script (and its own inherited label-chip workaround, added to keep the
label legible over what a naive `.convert("RGB")` turns into a solid BLACK
background) never composited it onto white first. Fixed at the source
(`compose()` pastes the RGBA image onto a white canvas via its own alpha
mask before JPEG-encoding), which makes the crop's corners genuinely empty
white space — same as the other 3 siblings' plain screenshot crops — so
the label sits directly on it with no chip needed at all, not just a
differently-colored chip.

What it does, in order:
1. Gets the INPUT crop via `refigure.vlm._docx_media_uri` directly (the
   same private helper the real pipeline uses for a standalone image
   marker) — the EXACT crop the VLM call actually saw.
2. Reads the real cached VLM response for marker `007cf0f198bc` and splices
   it through the real pipeline's own `_render_injected_docx_image` (so
   `sanitize_vlm_markdown`/injection-block formatting is exercised live,
   not hand-copied) — scoped to this ONE marker via the cache dict
   directly, not a full `docx.convert()` call: the source document has 5
   OTHER VLM-eligible markers with no cached entry, which would otherwise
   force an unbudgeted real API call per marker on any cache miss.
3. Renders the response's own mermaid `mindmap` fence via `mermaidx`, then
   applies the palette above via direct SVG post-processing (see above).
4. Composites INPUT (screenshot) + arrow + OUTPUT-A (raw text — prose
   excerpt + mermaid fence excerpt, "as your LLM/RAG pipeline reads it") +
   OUTPUT-B (the colored, rendered mindmap, embedded as a nested `<svg>`
   the same way `gen_demo_asset_docx_chart.py` embeds its pie) into one SVG.

Re-run whenever the demo's source fixture, cache, or palette tokens change.
Does NOT run in CI — manual documentation-asset tooling, same as its 3
siblings.
"""

from __future__ import annotations

import base64
import json
import re
import textwrap
from pathlib import Path

import mermaidx

from refigure import vlm as vlm_module

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests/integration/fixtures/docx/iot-report-2022-national-strategies-excerpt.docx"
CACHE_PATH = (
    REPO / "tests/integration/fixtures/vlm-cache/iot-report-2022-national-strategies-excerpt.json"
)
OUT_DIR = REPO / "docs/assets"

MARKER_ID = "007cf0f198bc"
_DISPLAY_W = 380

FONT_MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONT_SANS = "Inter, ui-sans-serif, -apple-system, Segoe UI, sans-serif"

# Canvas tokens, same shape/values as the 3 siblings' per-theme dicts —
# only the composite's own bg/ink/muted/blue switch per theme now.
THEMES: dict[str, dict[str, str]] = {
    "light": dict(bg="#FAFAF8", ink="#1A1A1A", muted="#63635A", blue="#3B5BA5"),
    "dark": dict(bg="#14150F", ink="#EDEDE6", muted="#9A988F", blue="#7C9CD6"),
}
# Fixed (not per-theme, see module docstring): the mindmap sub-panel stays
# a permanently dark card regardless of the surrounding canvas theme —
# these are exactly THEMES["dark"]'s own former values, kept as the
# mindmap panel's own frozen identity now that the canvas around it moves.
_MINDMAP_PANEL = dict(bg="#14150F", muted="#9A988F", blue="#7C9CD6")
# Fixed (not derived from THEMES), same reasoning as the other 3 demo
# scripts' _INPUT_LABEL_COLOR: this label sits on the real screenshot's
# own light UI background, not the page's dark surface.
_INPUT_LABEL_COLOR = "#1E7A6E"

# Branch order confirmed from the real cached response's own top-level
# indentation, not assumed: RFID, LAN/PAN, LPWAN, Сотовая связь,
# Спутниковая связь. cScaleN -> the Nth branch in that order (1-indexed;
# root consumes no cScale slot at all, confirmed live — see module
# docstring).
_BRANCH_FILLS = {
    1: "#9085e9",  # RFID -> violet
    2: "#1E4A8C",  # LAN/PAN -> dark navy blue
    3: "#199e70",  # LPWAN -> cool mint
    4: "#c98500",  # Сотовая связь -> gold
    5: "#F4A6C8",  # Спутниковая связь -> light pink
}
# section-(N-1) text color, matched per fill above for real WCAG AA
# contrast (computed, not eyeballed — every value clears 4.5:1 against its
# own fill): violet/mint/gold/pink all pair with black; the dark navy pairs
# with white instead (black-on-navy measured at 2.4:1, a hard fail).
_BRANCH_TEXT = {
    0: "#000000",  # RFID
    1: "#FFFFFF",  # LAN/PAN
    2: "#000000",  # LPWAN
    3: "#000000",  # Сотовая связь
    4: "#000000",  # Спутниковая связь
}
_ROOT_FILL = "#B8D4F0"  # neutral pale blue, distinct from all 5 branches
_ROOT_TEXT = "#000000"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(text, width=width)
    if len(lines) <= max_lines:
        return lines
    truncated = lines[:max_lines]
    truncated[-1] = truncated[-1].rstrip() + " …"
    return truncated


def get_input_crop() -> bytes:
    data_uri = vlm_module._docx_media_uri(FIXTURE.read_bytes(), MARKER_ID, raw_name=FIXTURE.name)
    assert data_uri is not None, f"marker {MARKER_ID} failed to render — fixture/format issue?"
    _header, b64data = data_uri.split(";base64,", 1)
    return base64.b64decode(b64data)


def get_live_text() -> tuple[str, str]:
    """(prose excerpt, mermaid code) — both sliced from the REAL cached
    response after passing it through the real pipeline's own
    `_render_injected_docx_image` (sanitize_vlm_markdown + injection-block
    formatting exercised live, not hand-copied). Scoped to this one
    marker's cache entry directly rather than a full `docx.convert()` call
    — see module docstring for why."""
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    entry = cache[MARKER_ID]
    injected = vlm_module._render_injected_docx_image(MARKER_ID, entry["model"], entry["markdown"])
    fence_match = re.search(r"```mermaid\n(.*?)\n```", injected, re.DOTALL)
    assert fence_match is not None, "no mermaid fence in the cached VLM response — cache stale?"
    prose = injected.split("```mermaid", 1)[0]
    # drop the injection-open marker line itself, keep only the prose body
    prose = prose.split("]\n\n", 1)[-1].strip()
    return prose, fence_match.group(1)


def render_mindmap_svg(mermaid_code: str) -> str:
    """The real VLM-generated mermaid code, colored + made legible via
    direct SVG post-processing — see module docstring for why themeVariables
    can't reach this diagram type's per-section color at all."""
    lines = mermaid_code.splitlines()
    unquoted = []
    for line in lines:
        m = re.match(r'^(\s*)"(.*)"\s*$', line)
        unquoted.append((m.group(1) + m.group(2)) if m else line)
    code = "\n".join(unquoted)
    code = code.replace("root((Wireless Technologies))", "root((Wireless<br/>Technologies))")

    theme_vars = ", ".join(f'"cScale{k}": "{v}"' for k, v in _BRANCH_FILLS.items())
    init = f'%%{{init: {{"theme": "dark", "themeVariables": {{{theme_vars}}}}}}}%%'
    svg = mermaidx.render(f"{init}\n{code}", theme="dark").svg()

    for i, tcolor in _BRANCH_TEXT.items():
        svg = re.sub(
            rf"(\.section-{i} (?:rect|path|circle|polygon|text)[^{{]*\{{fill:)lightgrey(;)",
            r"\1" + tcolor + r"\2",
            svg,
        )
        svg = re.sub(rf"(\.section-{i} text\{{fill:)lightgrey(;)", r"\1" + tcolor + r"\2", svg)
        svg = re.sub(
            rf"(\.node-icon-{i}\{{font-size:40px;color:)lightgrey(;)",
            r"\1" + tcolor + r"\2",
            svg,
        )
        svg = re.sub(rf"(\.section-{i} span\{{color:)lightgrey(;)", r"\1" + tcolor + r"\2", svg)
        svg = re.sub(
            rf'(\[data-look="neo"\]\.mindmap-node\.section-{i} \.text-inner-tspan\{{fill:)'
            r"lightgrey(;)",
            r"\1" + tcolor + r"\2",
            svg,
        )
    svg = re.sub(r"(\.section-root [^{]*\{fill:)hsl\([^)]+\)(;)", rf"\g<1>{_ROOT_FILL}\g<2>", svg)
    svg = re.sub(r"(\.section-root text\{fill:)[^;]+(;)", rf"\g<1>{_ROOT_TEXT}\g<2>", svg)
    return svg


def _mermaid_viewbox(svg: str) -> tuple[float, float]:
    match = re.search(r'viewBox="[\d.eE+-]+ [\d.eE+-]+ ([\d.]+) ([\d.]+)"', svg)
    assert match, f"no viewBox found in mermaid svg output: {svg[:200]!r}"
    return float(match.group(1)), float(match.group(2))


def compose(
    theme_name: str,
    theme: dict[str, str],
    input_bytes: bytes,
    prose: str,
    mermaid_code: str,
    mindmap_svg: str,
    out_path: Path,
) -> None:
    W, H = 1400, 900
    pad = 40
    in_x, in_y, in_w = pad, 96, _DISPLAY_W

    from io import BytesIO

    from PIL import Image, ImageDraw

    # This source PNG is RGBA (real transparency, confirmed live) — unlike
    # the other 3 demo scripts' plain screenshot crops, a bare .convert("RGB")
    # here would flatten the transparent background to BLACK (Pillow's
    # default), not the white the source document's own page actually
    # shows around this figure. A real bug found live: composite onto white
    # FIRST, matching what a real page render would look like.
    im = Image.open(BytesIO(input_bytes))
    if im.mode == "RGBA":
        flat = Image.new("RGB", im.size, "#FFFFFF")
        flat.paste(im, mask=im.split()[3])
        im = flat
    else:
        im = im.convert("RGB")
    # The source PNG's own top strip is the source document's chart title
    # ("Спектр основных технологий..."), which this SVG's own INPUT label
    # needs to occupy instead — painted over with white (title text
    # confirmed live to span rows 11-24; the sunburst circle itself doesn't
    # start until row 50, so row 0-45 stays inside that real empty margin,
    # not into the circle) rather than cropped out, so the label sits
    # INSIDE the image's own canvas exactly like the other 3 demo scripts'
    # crops, not outside/above it.
    ImageDraw.Draw(im).rectangle([0, 0, im.width, 45], fill="#FFFFFF")
    in_h = round(im.height * in_w / im.width)
    resized = im.resize((in_w, in_h), Image.Resampling.LANCZOS)
    buf = BytesIO()
    resized.save(buf, format="JPEG", quality=90, optimize=True)
    input_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    arrow_gap = 90
    arrow_cx = in_x + in_w + arrow_gap
    arrow_y = in_y + in_h / 2
    out_x = arrow_cx + arrow_gap
    out_w = W - pad - out_x

    boxA_y, boxA_h = 96, 300
    boxB_y = boxA_y + boxA_h + 26
    boxB_h = H - pad - boxB_y
    mono_size, line_h, text_pad_x, text_pad_top = 10, 12.8, 18, 46

    prose_lines = _wrap(prose, width=68, max_lines=8)
    fence_preview = ["```mermaid", *mermaid_code.splitlines()[:6], "    …", "```"]
    text_lines = [*prose_lines, "", *fence_preview]
    text_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(text_lines)
    )

    native_w, native_h = _mermaid_viewbox(mindmap_svg)
    avail_w = out_w - 2 * text_pad_x
    avail_h = boxB_h - 56
    scale = min(avail_w / native_w, avail_h / native_h)
    chart_w, chart_h = native_w * scale, native_h * scale

    bg, ink, muted, blue = theme["bg"], theme["ink"], theme["muted"], theme["blue"]

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="refigure VLM interpretation demo: a real dense wireless-technology sunburst chart with no native chart data, converted by refigure.docx.convert(use_vlm=True) into a rich text description and a real rendered mermaid mindmap diagram">
<rect width="{W}" height="{H}" fill="{bg}"/>

<text x="{pad}" y="52" font-family="{FONT_SANS}" font-size="14" letter-spacing="0.04em" fill="{muted}">IOT-REPORT-2022-NATIONAL-STRATEGIES-EXCERPT.DOCX &#8594; refigure.docx.convert(use_vlm=True)</text>

<rect x="{in_x - 1}" y="{in_y - 1}" width="{in_w + 2}" height="{in_h + 2}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<image x="{in_x}" y="{in_y}" width="{in_w}" height="{in_h}" href="data:image/jpeg;base64,{input_b64}"/>
<text x="{in_x + 14}" y="{in_y + 22}" font-family="{FONT_SANS}" font-size="14" font-weight="600" fill="{_INPUT_LABEL_COLOR}">INPUT — a screenshot, not a chart</text>
<text x="{in_x}" y="{in_y + in_h + 24}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">
<tspan x="{in_x}" dy="0">a dense wireless-tech sunburst — no native chart</tspan>
<tspan x="{in_x}" dy="15">data at all, and (before this feature) no mermaid</tspan>
<tspan x="{in_x}" dy="15">construct could represent it radially either &#8594;</tspan>
</text>

<text x="{arrow_cx}" y="{arrow_y - 16}" font-family="{FONT_MONO}" font-size="11" fill="{ink}" text-anchor="middle">--vlm</text>
<text x="{arrow_cx}" y="{arrow_y + 7}" font-family="{FONT_MONO}" font-size="20" letter-spacing="-3px" fill="{muted}" text-anchor="middle">&gt;&gt;&gt;&gt;&gt;</text>
<text x="{arrow_cx}" y="{arrow_y + 28}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">cloud VLM</text>
<text x="{arrow_cx}" y="{arrow_y + 42}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">reads the pixels,</text>
<text x="{arrow_cx}" y="{arrow_y + 56}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">cached + reproducible</text>

<rect x="{out_x}" y="{boxA_y}" width="{out_w}" height="{boxA_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxA_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — as your LLM/RAG pipeline reads it</text>
<text x="{out_x + text_pad_x}" y="{boxA_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{text_tspans}</text>

<rect x="{out_x}" y="{boxB_y}" width="{out_w}" height="{boxB_h}" fill="{_MINDMAP_PANEL["bg"]}" stroke="{_MINDMAP_PANEL["muted"]}" stroke-width="1" stroke-opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxB_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{_MINDMAP_PANEL["blue"]}">OUTPUT — same response, rendered as a real diagram</text>
<svg x="{out_x + (out_w - chart_w) / 2}" y="{boxB_y + 40 + (avail_h - chart_h) / 2}" width="{chart_w}" height="{chart_h}" viewBox="0 0 {native_w} {native_h}" preserveAspectRatio="xMidYMid meet">
{mindmap_svg}
</svg>

</svg>'''
    out_path.write_text(svg, encoding="utf-8")
    print(f"{theme_name}: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    prose, mermaid_code = get_live_text()
    print(f"verified live: cached VLM response for marker {MARKER_ID} has a mermaid mindmap fence")

    # Rendered once, not per theme: the mindmap sub-panel is a fixed dark
    # card regardless of the surrounding canvas (see module docstring).
    mindmap_svg = render_mindmap_svg(mermaid_code)
    input_bytes = get_input_crop()

    for theme_name, theme in THEMES.items():
        compose(
            theme_name,
            theme,
            input_bytes,
            prose,
            mermaid_code,
            mindmap_svg,
            OUT_DIR / f"demo-vlm-{theme_name}.svg",
        )


if __name__ == "__main__":
    main()
