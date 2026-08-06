"""Regenerate docs/assets/demo-groups-{light,dark}.svg (stage 7 README + demo).

Second hero graphic — sibling of `gen_demo_asset.py` (native chart-data
extraction). This one demonstrates the OTHER differentiator: positioned
zero-loss markers for composite figures (`docx group` in `docx_groups.py`'s
marker text) — the capability CLAUDE.md and the README cite as absent even
in well-funded incumbents (Docling issue #1287). Native chart extraction,
while rare, is not unique to refigure; composite-group recovery is. Same
design language as the chart-extraction hero (dual-view OUTPUT, one
signature arrow, verified-live content only), colour roles swapped: teal
leads INPUT here, blue leads OUTPUT (was the reverse) — the two hero images
read as a matched pair, not near-duplicates.

Revision 2026-08-06 (source swap): the first draft used one of
`efsa-trichinella-dashboard-guide.docx`'s 9 groups — a UI screenshot of
dashboard tabs. User feedback: (1) that source doesn't read as "a figure"
at a glance, just app chrome; (2) the OUTPUT panel showed 9 undifferentiated
colour chips with no actual information in them — looked like it might be
a converter bug, not just a weak demo. Investigated: NOT a bug — the
marker mechanism is exactly as designed (docx_groups.py, matches the
regression-pinned baselines elsewhere in this repo) — but the demo's
VISUAL METAPHOR overclaimed what a marker actually is (a placeholder
saying "not analyzed", not "recovered content"). Fixed by switching to
`onehealth-ejp-d3.20.docx`'s one composite group: a circular phylogenetic
tree WITH REAL CAPTIONS ("Europe/USA cluster 1", etc.) — this is a much
stronger, unambiguous "figure", and its captions are genuine extracted
information (not a decorative fill), so the OUTPUT panel now shows the 4
real caption strings as labelled chips instead of blank rank-fade squares.

What it does, in order:
1. Renders `tests/integration/fixtures/docx/onehealth-ejp-d3.20.docx`
   (671 pages at LibreOffice's pagination) to a 300dpi PNG of page 380 via
   `soffice --convert-to pdf` + `pdftoppm`, then crops the real "Figure 2:
   Phylogenetic relationship..." composite group — a circular tree diagram
   + a 4-entry colour legend, grouped as one `wpg` object (found live via
   `pdftotext`, not guessed).
2. Reads the real `refigure.docx.convert()` output for the marker text AND
   the real caption string refigure extracted ("captions: Europe/USA
   cluster 1; Europe cluster 2; Europe cluster 3; Asia/Africa cluster 4"),
   parses it into 4 labels — the OUTPUT chips' text comes from this, not a
   hardcoded copy, so a source/logic change here is caught by main()'s
   assertions, not silently stale.
3. Composites INPUT (screenshot) + arrow + OUTPUT (raw text view + the 4
   real caption labels as rank-faded chips, same `mixHex` formula as the
   chart hero, blue-anchored per user direction 2026-08-06) into one SVG
   per theme.

Re-run whenever the demo's source fixture or palette tokens change. Does
NOT run in CI — manual documentation-asset tooling, same as
`gen_demo_asset.py`.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from refigure.docx import convert as docx_convert

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests/integration/fixtures/docx/onehealth-ejp-d3.20.docx"
OUT_DIR = REPO / "docs/assets"

# Page 380 of 671 (verified live 2026-08-06 via pdftotext full-text search
# for the group's real caption text, not guessed/assumed from a page
# estimate) carries "Figure 2: Phylogenetic relationship among C. parvum
# isolates...". Crop is the tree + its colour legend, excluding the
# surrounding body-text paragraph and the figure's own caption line below
# it (that text is quoted verbatim in the raw-excerpt panel instead, not
# duplicated as a second copy inside the image).
# Generous margin on all sides (verified 2026-08-06 against the full page
# render, not guessed): the tree's own circle needs clean whitespace above
# (for the INPUT label, which must not sit on top of real content) AND
# below (an earlier, tighter bottom bound clipped the circle's own bottom
# edge — a crop artifact, not something in the source, same class of
# mistake as the chart hero's first-draft circle crop).
_PDF_PAGE = 380
_CROP_BOX = (150, 1650, 2340, 2950)
_CROP_DPI = 300
_DISPLAY_W = 560

_FADE_MAX_T = 0.88

FONT_MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONT_SANS = "Inter, ui-sans-serif, -apple-system, Segoe UI, sans-serif"

THEMES = {
    "light": dict(
        bg="#FAFAF8",
        ink="#1A1A1A",
        muted="#63635A",
        teal="#1E7A6E",
        blue="#3B5BA5",
        fade_target="#1A1A1A",
    ),
    "dark": dict(
        bg="#14150F",
        ink="#EDEDE6",
        muted="#9A988F",
        teal="#4FBBA8",
        blue="#7C9CD6",
        fade_target="#FAFAF8",
    ),
}
_BLUE_BASE = "#3B5BA5"
# Fixed (not per-theme), same reasoning as gen_demo_asset.py's
# _INPUT_LABEL_COLOR: this label always sits on the real screenshot's own
# white background, in both themes — needs an on-white-safe colour, not
# the theme's own (dark-theme-tuned) teal token.
_INPUT_LABEL_COLOR = "#1E7A6E"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(round(r), round(g), round(b))


def _mix_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


def ranked_fade(base: str, target: str, n: int) -> list[str]:
    if n <= 1:
        return [base]
    return [_mix_hex(base, target, (i / (n - 1)) * _FADE_MAX_T) for i in range(n)]


def render_input_screenshot(tmp: Path) -> Path:
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(FIXTURE)],
        check=True,
        capture_output=True,
        timeout=280,
    )
    pdf_path = tmp / f"{FIXTURE.stem}.pdf"
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            str(_CROP_DPI),
            "-f",
            str(_PDF_PAGE),
            "-l",
            str(_PDF_PAGE),
            str(pdf_path),
            str(tmp / "page"),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    page_png = next(tmp.glob("page-*.png"))
    cropped = Image.open(page_png).crop(_CROP_BOX)
    out = tmp / "input_crop.png"
    cropped.save(out)
    return out


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def compose(
    theme_name: str,
    theme: dict[str, str],
    input_png: Path,
    raw_lines: list[str],
    caption_labels: list[str],
    out_path: Path,
) -> None:
    W, H = 1200, 680
    pad = 40
    in_x, in_y, in_w = pad, 96, _DISPLAY_W

    im = Image.open(input_png).convert("RGB")
    in_h = round(im.height * in_w / im.width)
    resized = im.resize((in_w, in_h), Image.Resampling.LANCZOS)
    buf = input_png.with_name(f"input_resized_{in_w}.png")
    resized.save(buf, optimize=True)
    input_b64 = base64.b64encode(buf.read_bytes()).decode("ascii")

    arrow_gap = 100
    arrow_cx = in_x + in_w + arrow_gap
    arrow_y = in_y + in_h / 2
    out_x = arrow_cx + arrow_gap
    out_w = W - pad - out_x

    boxA_y, boxA_h = 96, 238
    boxB_y = boxA_y + boxA_h + 26
    boxB_h = H - pad - boxB_y
    mono_size, line_h, text_pad_x, text_pad_top = 10.5, 13.6, 18, 46

    raw_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(raw_lines)
    )

    # OUTPUT-B: the 4 REAL caption labels refigure extracted, as chips —
    # not decorative shapes. Each gets a small colour swatch (rank-faded
    # from the blue base, per user direction 2026-08-06) + its actual text,
    # echoing the original diagram's own swatch+label legend layout.
    fade = ranked_fade(
        _BLUE_BASE if theme_name == "light" else theme["blue"],
        theme["fade_target"],
        len(caption_labels),
    )
    chip_h = 30
    chip_gap = 10
    chips_y0 = boxB_y + 56
    chip_rows = []
    for i, label in enumerate(caption_labels):
        y = chips_y0 + i * (chip_h + chip_gap)
        chip_rows.append(
            f'<rect x="{out_x + text_pad_x}" y="{y}" width="16" height="16" rx="3" fill="{fade[i]}"/>'
            f'<text x="{out_x + text_pad_x + 24}" y="{y + 13}" font-family="{FONT_SANS}" '
            f'font-size="13.5" fill="{theme["ink"]}">{_esc(label)}</text>'
        )
    output_chips = "".join(chip_rows)
    chips_bottom = chips_y0 + len(caption_labels) * (chip_h + chip_gap)

    bg, ink, muted, blue = theme["bg"], theme["ink"], theme["muted"], theme["blue"]

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="refigure before/after demo: a real docx composite figure (a circular phylogenetic tree diagram with a 4-entry colour legend, grouped as one object) converted to a positioned zero-loss marker that keeps the legend's 4 real caption labels as text">
<rect width="{W}" height="{H}" fill="{bg}"/>

<text x="{pad}" y="52" font-family="{FONT_SANS}" font-size="14" letter-spacing="0.04em" fill="{muted}">ONEHEALTH-EJP-D3.20.DOCX &#8594; refigure.docx.convert() &#8594; MARKDOWN</text>

<rect x="{in_x - 1}" y="{in_y - 1}" width="{in_w + 2}" height="{in_h + 2}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<image x="{in_x}" y="{in_y}" width="{in_w}" height="{in_h}" href="data:image/png;base64,{input_b64}"/>
<!-- Label sits on the diagram's own blank canvas corner (this crop is a
     circular tree with legend text to the right — top-left is empty
     white space in the real image, verified 2026-08-06), same "no
     background chip painted over real content" rule as the chart hero. -->
<text x="{in_x + 14}" y="{in_y + 22}" font-family="{FONT_SANS}" font-size="14" font-weight="600" fill="{_INPUT_LABEL_COLOR}">INPUT — a composite figure</text>

<text x="{in_x}" y="{in_y + in_h + 24}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">
<tspan x="{in_x}" dy="0">a circular phylogenetic tree + its colour legend,</tspan>
<tspan x="{in_x}" dy="15">grouped as one object — mammoth (and any</tspan>
<tspan x="{in_x}" dy="15">plain-text extractor) can't parse OOXML groups.</tspan>
</text>

<text x="{arrow_cx}" y="{arrow_y - 16}" font-family="{FONT_MONO}" font-size="11" fill="{ink}" text-anchor="middle">refigure.docx.convert()</text>
<text x="{arrow_cx}" y="{arrow_y + 7}" font-family="{FONT_MONO}" font-size="20" letter-spacing="-3px" fill="{muted}" text-anchor="middle">&gt;&gt;&gt;&gt;&gt;</text>
<text x="{arrow_cx}" y="{arrow_y + 28}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">positioned marker</text>
<text x="{arrow_cx}" y="{arrow_y + 42}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">— not silently dropped</text>

<rect x="{out_x}" y="{boxA_y}" width="{out_w}" height="{boxA_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxA_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — as your LLM/RAG pipeline reads it</text>
<text x="{out_x + text_pad_x}" y="{boxA_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{raw_tspans}</text>

<rect x="{out_x}" y="{boxB_y}" width="{out_w}" height="{boxB_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxB_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — the legend survives, as text</text>
{output_chips}
<text x="{out_x + text_pad_x}" y="{chips_bottom + 16}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">the diagram's own 4-entry legend — not the</text>
<text x="{out_x + text_pad_x}" y="{chips_bottom + 32}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">drawing, but its real labels, positioned, no OCR</text>
</svg>'''
    out_path.write_text(svg, encoding="utf-8")
    print(f"{theme_name}: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    result = docx_convert(FIXTURE)
    marker_idx = result.markdown.find("> [Figure")
    assert marker_idx != -1, "no group marker found in convert() output"
    cap_start = result.markdown.find("captions:", marker_idx)
    assert cap_start != -1, "no captions line found after the group marker"
    cap_end = result.markdown.find("\n", cap_start)
    caption_labels = [
        s.strip() for s in result.markdown[cap_start:cap_end].split(":", 1)[1].split(";")
    ]
    assert caption_labels == [
        "Europe/USA cluster 1",
        "Europe cluster 2",
        "Europe cluster 3",
        "Asia/Africa cluster 4",
    ], f"caption labels drifted from what this asset's copy expects: {caption_labels!r}"
    print(f"verified live: groups_found={result.groups_found}, captions={caption_labels}")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        input_png = render_input_screenshot(tmp)
        raw_lines = [
            "…the European isolates form three",
            "clusters (Figure 2), one of which…",
            "",
            "> [Figure, docx group f200d7cd2d94 —",
            "composite content not analyzed]",
            "> captions: Europe/USA cluster 1;",
            "Europe cluster 2; Europe cluster 3;",
            "Asia/Africa cluster 4",
            "",
            "*Figure 2. Phylogenetic relationship*",
        ]
        for theme_name, theme in THEMES.items():
            compose(
                theme_name,
                theme,
                input_png,
                raw_lines,
                caption_labels,
                OUT_DIR / f"demo-groups-{theme_name}.svg",
            )


if __name__ == "__main__":
    main()
