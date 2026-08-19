"""Regenerate docs/assets/demo-vlm-{light,dark}.svg (VLM interpretation —
README Demo section, 4th hero).

Sibling of `gen_demo_asset.py`/`gen_demo_asset_groups.py`/
`gen_demo_asset_docx_chart.py`, but a different story from all three: those
show native OOXML chart-data extraction (no VLM at all) or a bare zero-loss
marker. This one shows what `--vlm`/`Config(use_vlm=True)` ADDS on top of
the zero-loss floor for a figure that is NOT a native chart at all — a raw
screenshot embedded as an image inside a composite group, whose numbers
exist nowhere in the file's own XML. Framing is a before/after WITHIN
refigure itself (bare marker -> rich VLM prose), not docx-to-markdown in
general — the same lesson `gen_demo_asset_groups.py`'s own docstring
recorded from live user feedback: a marker is a placeholder, not recovered
content, so don't visually imply otherwise. Here the rich text genuinely
IS the recovered content, so showing it in full is honest, not
overclaiming.

Source: `efsa-trichinella-dashboard-guide.docx` (vlm-activation spec §6) —
9 composite groups, all resolved by the real VLM call committed at
`tests/integration/fixtures/vlm-cache/efsa-trichinella-dashboard-guide.json`
(see that commit's message for cost/provenance). Group fa3bebd21344 chosen
as the richest of the 9 after reading all of them: a full 4-category
legend + 8 real transcribed numbers (2022/2023 x 4 housing conditions) +
one synthesized narrative sentence, from what is genuinely just a
dashboard-UI screenshot with a chart image inside it, not an OOXML chart
part.

Deliberately does NOT re-render the VLM's own mermaid fence as a diagram
(unlike the OTHER 3 demo scripts' "rendered" panel): this group's
mermaid is a 4-series ``xychart-beta`` bar chart, and mermaid's
xychart-beta has no grouped/side-by-side multi-series bar support — only
overlay (see `gen_demo_asset_docx_chart.py`'s own docstring for the live
confirmation of this exact limitation, which is why THAT demo picked a
pie chart instead). Rendering this fence would reproduce the same
occluded-bars artifact. The text itself (real numbers, real legend) is the
point here, not a picture of a chart — so both OUTPUT panels stay raw text,
consistent with the "as your LLM/RAG pipeline reads it" framing the other
3 demos already use for their own top panel.

What it does, in order:
1. Gets the INPUT crop via `refigure.vlm._render_docx_group` directly (the
   same private helper the real pipeline uses) — the EXACT crop the VLM
   call actually saw, not a separately hand-cropped screenshot.
2. Reads the real bare (no-VLM) marker text AND the real VLM-injected block
   text for this group from two live `refigure.docx.convert()` calls (one
   plain, one with `Config(use_vlm=True, vlm_cache=FileCacheBackend(...))`
   pointed at the committed cache — 100% cache-hit, zero network) — both
   asserted present, not hand-copied, so a future engine/prompt change that
   drifts this content is caught, not silently stale.
3. Composites INPUT (screenshot) + arrow + OUTPUT-A (bare marker, "without
   --vlm") + OUTPUT-B (VLM prose excerpt, "with --vlm") into one SVG per
   theme. Same design language as the other 3 heroes: dual OUTPUT panels,
   one signature arrow, verified-live content only.

Re-run whenever the demo's source fixture, cache, or palette tokens change.
Does NOT run in CI — manual documentation-asset tooling, same as its 3
siblings.
"""

from __future__ import annotations

import base64
import re
import textwrap
from io import BytesIO
from pathlib import Path

from PIL import Image

from refigure import vlm as vlm_module
from refigure.api import Config
from refigure.docx import convert as docx_convert
from refigure.vlm.cache import FileCacheBackend

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests/integration/fixtures/docx/efsa-trichinella-dashboard-guide.docx"
CACHE_PATH = REPO / "tests/integration/fixtures/vlm-cache/efsa-trichinella-dashboard-guide.json"
OUT_DIR = REPO / "docs/assets"

GROUP_ID = "fa3bebd21344"
_DISPLAY_W = 460

FONT_MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONT_SANS = "Inter, ui-sans-serif, -apple-system, Segoe UI, sans-serif"

THEMES = {
    "light": dict(
        bg="#FAFAF8",
        ink="#1A1A1A",
        muted="#63635A",
        teal="#1E7A6E",
        blue="#3B5BA5",
    ),
    "dark": dict(
        bg="#14150F",
        ink="#EDEDE6",
        muted="#9A988F",
        teal="#4FBBA8",
        blue="#7C9CD6",
    ),
}
# Fixed (not per-theme), same reasoning as the other 3 demo scripts'
# _INPUT_LABEL_COLOR: this label sits on the real screenshot's own
# (light-UI) background in both themes.
_INPUT_LABEL_COLOR = "#1E7A6E"


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(text, width=width)
    if len(lines) <= max_lines:
        return lines
    truncated = lines[:max_lines]
    truncated[-1] = truncated[-1].rstrip() + " …"
    return truncated


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def get_input_crop() -> bytes:
    data_uri = vlm_module._render_docx_group(FIXTURE, GROUP_ID, raw_name=FIXTURE.name)
    assert data_uri is not None, f"group {GROUP_ID} failed to render — soffice/fixture issue?"
    _header, b64data = data_uri.split(";base64,", 1)
    return base64.b64decode(b64data)


def get_live_text() -> tuple[str, str]:
    """(bare marker text, VLM-injected prose) — both read from a REAL
    `docx.convert()` call, the second via a 100%-cache-hit
    `FileCacheBackend` (zero network)."""
    bare_result = docx_convert(FIXTURE)
    bare_match = re.search(
        rf"^> \[Figure, docx group {GROUP_ID} — composite content not analyzed\]\n"
        rf"^> captions:.*$",
        bare_result.markdown,
        re.MULTILINE,
    )
    assert bare_match is not None, f"bare marker for group {GROUP_ID} not found — fixture drifted?"

    config = Config(use_vlm=True, vlm_cache=FileCacheBackend(CACHE_PATH))
    vlm_result = docx_convert(FIXTURE, config=config)
    vlm_match = re.search(
        rf"^> \[Figure, docx group {GROUP_ID} — VLM interpretation.*?\]\n\n"
        rf"(?P<body>.*?)\n\n> \[/VLM interpretation docx group {GROUP_ID}\]",
        vlm_result.markdown,
        re.MULTILINE | re.DOTALL,
    )
    assert vlm_match is not None, (
        f"VLM-injected block for group {GROUP_ID} not found — cache stale/regenerated?"
    )
    return bare_match.group(0), vlm_match.group("body")


def compose(
    theme_name: str,
    theme: dict[str, str],
    input_bytes: bytes,
    bare_marker: str,
    vlm_prose: str,
    out_path: Path,
) -> None:
    W, H = 1200, 700
    pad = 40
    in_x, in_y, in_w = pad, 96, _DISPLAY_W

    im = Image.open(BytesIO(input_bytes)).convert("RGB")
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

    boxA_y, boxA_h = 96, 110
    boxB_y = boxA_y + boxA_h + 26
    boxB_h = H - pad - boxB_y
    mono_size, line_h, text_pad_x, text_pad_top = 10.5, 13.6, 18, 46

    bare_lines = _wrap(bare_marker, width=58, max_lines=3)
    vlm_lines = _wrap(vlm_prose, width=58, max_lines=17)

    bare_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(bare_lines)
    )
    vlm_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(vlm_lines)
    )

    bg, ink, muted, blue = theme["bg"], theme["ink"], theme["muted"], theme["blue"]

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="refigure VLM interpretation demo: a real dashboard-screenshot composite figure from a docx file, shown first as the bare zero-loss marker without --vlm, then as the rich VLM-generated description with real transcribed numbers with --vlm">
<rect width="{W}" height="{H}" fill="{bg}"/>

<text x="{pad}" y="52" font-family="{FONT_SANS}" font-size="14" letter-spacing="0.04em" fill="{muted}">EFSA-TRICHINELLA-DASHBOARD-GUIDE.DOCX &#8594; refigure.docx.convert(use_vlm=True)</text>

<rect x="{in_x - 1}" y="{in_y - 1}" width="{in_w + 2}" height="{in_h + 2}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<image x="{in_x}" y="{in_y}" width="{in_w}" height="{in_h}" href="data:image/jpeg;base64,{input_b64}"/>
<!-- Unlike the other 3 demo scripts' hand-picked crops (a genuinely empty
     corner, verified visually), this crop comes from the real pipeline's
     own automatic _render_docx_group — it can carry real content (a
     document header) right where a label would sit. A background chip
     keeps the label legible regardless of what's underneath, rather than
     assuming an empty corner exists. -->
<rect x="{in_x + 6}" y="{in_y + 6}" width="270" height="24" rx="4" fill="{bg}" opacity="0.88"/>
<text x="{in_x + 14}" y="{in_y + 22}" font-family="{FONT_SANS}" font-size="14" font-weight="600" fill="{_INPUT_LABEL_COLOR}">INPUT — a screenshot, not a chart part</text>
<text x="{in_x}" y="{in_y + in_h + 24}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">
<tspan x="{in_x}" dy="0">a dashboard UI screenshot grouped with callout</tspan>
<tspan x="{in_x}" dy="15">shapes — its numbers exist nowhere in the</tspan>
<tspan x="{in_x}" dy="15">file's own XML, unlike a native OOXML chart.</tspan>
</text>

<text x="{arrow_cx}" y="{arrow_y - 16}" font-family="{FONT_MONO}" font-size="11" fill="{ink}" text-anchor="middle">--vlm</text>
<text x="{arrow_cx}" y="{arrow_y + 7}" font-family="{FONT_MONO}" font-size="20" letter-spacing="-3px" fill="{muted}" text-anchor="middle">&gt;&gt;&gt;&gt;&gt;</text>
<text x="{arrow_cx}" y="{arrow_y + 28}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">cloud VLM reads the</text>
<text x="{arrow_cx}" y="{arrow_y + 42}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">pixels, cached + reproducible</text>

<rect x="{out_x}" y="{boxA_y}" width="{out_w}" height="{boxA_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxA_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — without --vlm (zero-loss floor)</text>
<text x="{out_x + text_pad_x}" y="{boxA_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{bare_tspans}</text>

<rect x="{out_x}" y="{boxB_y}" width="{out_w}" height="{boxB_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxB_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — with --vlm, real numbers recovered</text>
<text x="{out_x + text_pad_x}" y="{boxB_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{vlm_tspans}</text>

</svg>'''
    out_path.write_text(svg, encoding="utf-8")
    print(f"{theme_name}: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    bare_marker, vlm_prose = get_live_text()
    print(f"verified live: bare marker + VLM block both found for group {GROUP_ID}")

    input_bytes = get_input_crop()
    for theme_name, theme in THEMES.items():
        compose(
            theme_name,
            theme,
            input_bytes,
            bare_marker,
            vlm_prose,
            OUT_DIR / f"demo-vlm-{theme_name}.svg",
        )


if __name__ == "__main__":
    main()
