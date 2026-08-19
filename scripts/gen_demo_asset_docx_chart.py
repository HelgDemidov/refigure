"""Regenerate docs/assets/demo-docx-chart-{light,dark}.svg (docx native-chart
happy path — README Demo section).

Third hero graphic — sibling of `gen_demo_asset.py` (same "native chart-data
extraction, not OCR" claim), for the format `gen_demo_asset.py` DOESN'T cover:
the README's Demo section had an xlsx happy path and a docx FALLBACK
(composite figures, `gen_demo_asset_groups.py`), but no docx happy path —
this closes that gap. Same design language: dual-view OUTPUT (raw text +
rendered chart), one signature arrow, verified-live content only.

Source selection (spec §1, empirical, not by manifest.yaml chart-part counts
alone) — TWO rounds:

Round 1 ran `refigure.docx.convert()` on 4 candidates and compared
charts_found/rendered: `onehealth-ejp-d3.20.docx` (5/5 -> mermaid, max 2
series), `hackair-d7.7-pilot-evaluation.docx` (8 found, 6/8 -> mermaid, all
xychart-beta), `swd2021-396-platform-work-ia.docx` (8 found, 3/8 ->
mermaid: 2 xychart-beta + 1 pie), `ukri-user-behaviour-survey.docx` (44
found, **44/44** -> mermaid, richest single chart = a real 4-series grouped
Likert-scale bar). By raw count/rate alone, ukri won outright.

Round 2 actually rendered ukri's 4-series chart via `mermaidx` and looked at
it: mermaid's `xychart-beta` does NOT support grouped/side-by-side
multi-series bars (`refigure/core/chart_data.py`'s own comment: "xychart-beta
does NOT support stacking (issue #7392, overlay only)") — it OVERLAYS each
`bar [...]` series at full width, so 4 series render as bars occluding each
other, not the clean grouped comparison the source chart actually shows.
Confirmed live (rendered PNG, 2026-08-19): the "Always" category shows only
ONE visible bar (the tallest series painted last), not 4 — a viewer would
read this as broken, not impressive. This is an honest, structural mermaid
limitation, not a refigure bug, and not fixable by picking a "better"
multi-series bar chart — checked live: ukri has 44 charts, 0 of which are
`line`-type (which WOULD overlay cleanly, unlike filled bars), only bar-type
xychart-beta (30 single-series, 14 multi-series - all subject to the same
overlay issue).

Given that, swd2021's **pie chart** wins on round 2: pie is a fundamentally
different mermaid construct with no overlay ambiguity — renders as one clean
diagram regardless of slice count. It's also real and substantive (5 slices,
real EU labour-platform survey labels, "Figure E" in the source PDF) and
gives the README's 3 hero images genuine visual variety (bar/xychart for the
xlsx demo, marker+table for the docx-fallback demo, pie for this one) instead
of two near-identical xychart-beta bar charts. Traded away: swd2021's
file-wide rate (3/8, weaker than ukri's 44/44) — accepted, because this asset
showcases ONE real chart, not a file-wide completeness claim (that claim
lives in README's "Real examples" table + tests/integration/
test_corpus_totals.py, not in a hero SVG).

What it does, in order:
1. Renders `tests/integration/fixtures/docx/swd2021-396-platform-work-ia.docx`
   page 132 (of 396 pages) to a 300dpi PNG via `soffice --convert-to pdf` +
   `pdftoppm`, then crops the real "Figure E" pie chart — found live via
   `pdftotext` full-text search for the figure's own caption text, not
   guessed. The chart itself sits on the page AFTER the one carrying its own
   caption text (Word's float layout put "Figure E." on PDF page 131, the
   actual chart image on 132) — confirmed by rendering both and looking, not
   assumed from the caption's page alone.
2. Runs the real `refigure.docx.convert()` output's mermaid `pie` block
   through `mermaidx.render()` — mermaid's own default slice palette, no
   colour post-processing (unlike `gen_demo_asset.py`'s rank-fade, which
   exists specifically because GitHub's default xychart-beta bar colouring
   is flat/pale; mermaid's default pie palette has no equivalent problem).
3. Composites INPUT (screenshot) + arrow + OUTPUT (raw text view — the real
   mermaid `pie` fence AND the real single-column data table refigure emits
   right after it + rendered view) into one SVG per theme.

`_MERMAID_CODE`/`_RAW_EXCERPT_LINES` are literal strings (not generated at
run time — same choice `gen_demo_asset.py` makes, for a stable, reviewable
diff), but `main()` asserts the real live `convert()` output for this
fixture still contains this exact chart's labels/data — a source/logic
drift is caught, not silently stale.

Re-run whenever the demo's source fixture or palette tokens change. Does
NOT run in CI — manual documentation-asset tooling, same as its two
siblings.
"""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from pathlib import Path

import mermaidx
from PIL import Image

from refigure.docx import convert as docx_convert

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests/integration/fixtures/docx/swd2021-396-platform-work-ia.docx"
OUT_DIR = REPO / "docs/assets"

# PDF page 132 (verified live 2026-08-19 via pdftotext full-text search for
# "Figure E." — the chart's own caption — then rendering pages 131/132 and
# visually confirming the actual pie image is on 132, one page after its
# caption text). Crop is the chart box + its own source attribution line
# only, generous margin (verified live against the full page render) —
# excludes the surrounding body paragraphs and the bold "Figure E." caption
# line (quoted verbatim in the raw-excerpt panel instead, not duplicated).
_PDF_PAGE = 132
_CROP_BOX = (330, 60, 2140, 1090)
_CROP_DPI = 300
_DISPLAY_W = 380

_MERMAID_CODE = (
    "pie\n"
    '    "Never – all my clients are based in country of residence" : 31.44\n'
    '    "Sometimes, but most of my clients are based in country of residence" : 35.54\n'
    '    "Often – most of my clients are based outside country of residence" : 19.08\n'
    '    "Always – all of my clients are based outsidecountry of residence" : 4.854\n'
    '    "Dont know/ not applicable" : 9.08'
)
_LABELS = [
    "Never – all my clients are based in [country of residence]",
    "Sometimes, but most of my clients are based in [country of residence]",
    "Often – most of my clients are based outside [country of residence]",
]

_RAW_EXCERPT_LINES = [
    "Figure E. 2021 survey: When working via online",
    "platforms, how often have you worked for clients",
    "based in countries other than [country of residence]",
    "",
    "```mermaid",
    "pie",
    '    "Never – all my clients are based in',
    '     country of residence" : 31.44',
    '    "Sometimes, but most of my clients are',
    '     based in country of residence" : 35.54',
    '    "Often – most of my clients are based',
    '     outside country of residence" : 19.08',
    "    …",
    "```",
    "",
    "| Category | Series 1 |",
    "| --- | --- |",
    "| Never – all my clients are… | 31.4 |",
    "| Sometimes, but most of my… | 35.5 |",
]

FONT_MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONT_SANS = "Inter, ui-sans-serif, -apple-system, Segoe UI, sans-serif"

THEMES: dict[str, dict[str, str | list[str]]] = {
    "light": dict(
        bg="#FAFAF8",
        ink="#1A1A1A",
        muted="#63635A",
        teal="#1E7A6E",
        blue="#3B5BA5",
        mermaid_text="#333333",
        # 5-hue categorical pie palette, built + validated per the dataviz
        # skill's method (references/color-formula.md, six-check
        # validator) rather than eyeballed. Slot 1 (blue) reuses this
        # demo family's exact anchor hex unchanged — it already passed the
        # checks standalone. Slot 3 (teal) does NOT reuse the other two
        # demos' exact #1E7A6E: validated live 2026-08-19, that hex FAILS
        # the chroma floor (0.085 < 0.10, "reads gray") once it has to
        # hold its own next to 4 other simultaneous categorical hues,
        # unlike the other 2 demos where teal/blue each lead ALONE (a
        # rank-fade base or a single accent, never competing against 4
        # peers at once) — the muddiness the user flagged live, confirmed
        # by the validator, not just by eye. #0E9C86 is the same hue
        # family, brighter/more saturated, chosen from a set of candidates
        # that all cleared the floor. Slots 2/4/5 (orange/gold/violet) are
        # new, taken from dataviz's own documented reference palette
        # (references/palette.md) for slots already vetted for CVD
        # separation, not invented here. Full command + all 5 checks
        # (adjacent pairlist — physically valid for a pie: only
        # neighbouring wedges share a border, same reasoning as
        # stacks/bars/lines, not scatter/map's any-two-can-be-neighbours
        # case): `node scripts/validate_palette.js
        # "#3B5BA5,#eb6834,#0E9C86,#eda100,#4a3aa7" --mode light --surface
        # "#FAFAF8"` — all 5 PASS except a contrast WARN on gold (2.07:1),
        # mitigated by this chart's own always-visible slice-percentage +
        # legend text labels (the "relief channel" the skill requires for
        # a contrast WARN, already present here regardless).
        pie_palette=["#3B5BA5", "#eb6834", "#0E9C86", "#eda100", "#4a3aa7"],
    ),
    "dark": dict(
        bg="#14150F",
        ink="#EDEDE6",
        muted="#9A988F",
        teal="#4FBBA8",
        blue="#7C9CD6",
        # Brighter than this composition's general dark-theme ink (#EDEDE6)
        # deliberately — the pie's own slice/legend text needs to read as
        # unambiguously bright white against the dark surface, not just
        # "light enough"; reuses the light theme's own bg token value
        # (#FAFAF8, a warm near-white) rather than an unrelated new hex.
        mermaid_text="#FAFAF8",
        # Dark-mode slots 1/3 (blue/teal) diverge further from the other 2
        # demos' exact dark hex than the light palette does: validated live
        # 2026-08-19, the demo family's own #7C9CD6/#4FBBA8 both fail the
        # dark lightness band (L 0.69/0.72, band is 0.48-0.67 — both were
        # tuned as light-on-dark accent/text tones, not saturated dark-mode
        # chart fills) and #7C9CD6 additionally fails the chroma floor
        # (0.093). #618AD1/#20A98C are the same two hue families, restepped
        # into the band — same "harmonize, don't clone" latitude as the
        # light teal fix above, same command pattern with `--mode dark
        # --surface "#14150F"`: all 5 checks PASS, no WARN.
        pie_palette=["#618AD1", "#d95926", "#20A98C", "#c98500", "#9085e9"],
    ),
}
# Fixed (not per-theme), same reasoning as the other 2 demo scripts'
# _INPUT_LABEL_COLOR: this label sits on the real screenshot's own white
# background in both themes.
_INPUT_LABEL_COLOR = "#3B5BA5"


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


def render_mermaid_svg(theme: dict[str, str | list[str]]) -> str:
    # Slice-label/legend text sized via the pie-specific
    # pieSectionTextSize/pieLegendTextSize variables (default 17px each,
    # confirmed via the SVG's own generated CSS — NOT the generic
    # `fontSize` variable, which also touches pieTitleText/other diagram
    # types unnecessarily). Bumped modestly to 19px per live user feedback
    # 2026-08-19 (too small at the default). Verified live: like the
    # generic fontSize case, this pie layout's viewBox stays a FIXED
    # 1065.9x450 regardless of either variable's value (headless layout
    # doesn't remeasure glyph widths from it) — a small bump is safe
    # (checked visually, no overlap/clipping), a large one risks the
    # legend text overflowing the width the fixed layout reserved for it.
    palette = theme["pie_palette"]
    assert isinstance(palette, list)
    mermaid_text = theme["mermaid_text"]
    assert isinstance(mermaid_text, str)
    theme_vars: dict[str, str] = {f"pie{i + 1}": color for i, color in enumerate(palette)}
    theme_vars.update(
        {
            "pieOuterStrokeWidth": "0px",
            "pieSectionTextColor": mermaid_text,
            "pieLegendTextColor": mermaid_text,
            "pieSectionTextSize": "19px",
            "pieLegendTextSize": "19px",
        }
    )
    config = {"theme": "base", "themeVariables": theme_vars}
    svg: str = mermaidx.render(_MERMAID_CODE, theme="base", config=config).svg()
    return _widen_legend_row_spacing(svg)


_N_LEGEND_ROWS = 5
# Mermaid's pie legend has no themeVariable for row spacing (verified live
# 2026-08-19 — pieSectionTextSize/pieLegendTextSize resize the text but
# leave each row's own hardcoded 22px translate-Y step untouched, which is
# why bumping text size alone made the rows read as cramped: same
# complaint, same live-verified cause as the `_mermaid_viewbox` /
# `render_mermaid_svg` fontSize notes above). Widened via direct SVG
# post-processing — same technique gen_demo_asset.py already uses for its
# bar rank-fade — to a 1.42x step, chosen the same way as this skill picks
# a font-size: a computed ratio (~1.4-1.5x font-size is the standard
# comfortable line-height band), not eyeballed, applied to the swatch+text
# row unit as a whole (dataviz skill, per user request 2026-08-19).
_LEGEND_ROW_STEP_SCALE = 27 / 22


def _widen_legend_row_spacing(svg: str) -> str:
    pattern = re.compile(r'(<g class="legend" transform="translate\(216,)(-?\d+)(\)")')
    counter = {"n": 0}

    def repl(m: re.Match[str]) -> str:
        counter["n"] += 1
        y = round(int(m.group(2)) * _LEGEND_ROW_STEP_SCALE)
        return f"{m.group(1)}{y}{m.group(3)}"

    svg, n = pattern.subn(repl, svg)
    assert n == _N_LEGEND_ROWS, f"expected to reflow {_N_LEGEND_ROWS} legend rows, reflowed {n}"
    return svg


def _mermaid_viewbox(svg: str) -> tuple[float, float]:
    """(width, height) of the rendered mermaid SVG's own viewBox — the pie
    diagram's native size varies with label length/slice count, unlike
    gen_demo_asset.py's fixed 900x460 xychart canvas, so this can't be a
    hardcoded constant here."""
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert match, f"no viewBox found in mermaid svg output: {svg[:200]!r}"
    return float(match.group(1)), float(match.group(2))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def compose(
    theme_name: str,
    theme: dict[str, str | list[str]],
    input_png: Path,
    mermaid_svg: str,
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

    arrow_gap = 90
    arrow_cx = in_x + in_w + arrow_gap
    arrow_y = in_y + in_h / 2
    out_x = arrow_cx + arrow_gap
    out_w = W - pad - out_x

    boxA_y, boxA_h = 96, 260
    boxB_y = boxA_y + boxA_h + 26
    boxB_h = H - pad - boxB_y
    mono_size, line_h, text_pad_x, text_pad_top = 10, 12.8, 18, 46

    raw_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(_RAW_EXCERPT_LINES)
    )

    # The rendered mermaid pie's native viewBox is a wide rectangle (slices
    # + its own legend to the right, ~2.37:1 for this fixture's long
    # labels) — fit it into boxB's budget preserving aspect ratio, take
    # whichever of width/height is the binding constraint (same pattern as
    # gen_demo_asset.py's fixed-900x460 case, just with a measured instead
    # of hardcoded native size, since pie layout width varies with label
    # length/slice count).
    native_w, native_h = _mermaid_viewbox(mermaid_svg)
    avail_w = out_w - 2 * text_pad_x
    avail_h = boxB_h - 56
    scale = min(avail_w / native_w, avail_h / native_h)
    chart_w, chart_h = native_w * scale, native_h * scale

    bg, ink, muted, blue = theme["bg"], theme["ink"], theme["muted"], theme["blue"]

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="refigure before/after demo: a real docx pie chart from an EU survey converted to markdown with native OOXML chart data, shown as both raw text with its data table and a rendered diagram">
<rect width="{W}" height="{H}" fill="{bg}"/>

<text x="{pad}" y="52" font-family="{FONT_SANS}" font-size="14" letter-spacing="0.04em" fill="{muted}">SWD2021-396-PLATFORM-WORK-IA.DOCX &#8594; refigure.docx.convert() &#8594; MARKDOWN</text>

<rect x="{in_x - 1}" y="{in_y - 1}" width="{in_w + 2}" height="{in_h + 2}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<image x="{in_x}" y="{in_y}" width="{in_w}" height="{in_h}" href="data:image/png;base64,{input_b64}"/>
<text x="{in_x + 14}" y="{in_y + 22}" font-family="{FONT_SANS}" font-size="14" font-weight="600" fill="{_INPUT_LABEL_COLOR}">INPUT — a real chart</text>
<text x="{in_x}" y="{in_y + in_h + 24}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">
<tspan x="{in_x}" dy="0">a real EU-survey pie chart — refigure reads the</tspan>
<tspan x="{in_x}" dy="15">native OOXML numCache data, not the pixels:</tspan>
<tspan x="{in_x}" dy="15">all 5 slices, with their real labels &#8594;</tspan>
</text>

<text x="{arrow_cx}" y="{arrow_y - 16}" font-family="{FONT_MONO}" font-size="11" fill="{ink}" text-anchor="middle">refigure.docx.convert()</text>
<text x="{arrow_cx}" y="{arrow_y + 7}" font-family="{FONT_MONO}" font-size="20" letter-spacing="-3px" fill="{muted}" text-anchor="middle">&gt;&gt;&gt;&gt;&gt;</text>
<text x="{arrow_cx}" y="{arrow_y + 28}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">native OOXML numCache</text>
<text x="{arrow_cx}" y="{arrow_y + 42}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">— not OCR</text>

<rect x="{out_x}" y="{boxA_y}" width="{out_w}" height="{boxA_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxA_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — as your LLM/RAG pipeline reads it</text>
<text x="{out_x + text_pad_x}" y="{boxA_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{raw_tspans}</text>

<rect x="{out_x}" y="{boxB_y}" width="{out_w}" height="{boxB_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxB_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{blue}">OUTPUT — same file, rendered</text>
<svg x="{out_x + (out_w - chart_w) / 2}" y="{boxB_y + 40 + (avail_h - chart_h) / 2}" width="{chart_w}" height="{chart_h}" viewBox="0 0 {native_w} {native_h}" preserveAspectRatio="xMidYMid meet">
{mermaid_svg}
</svg>

</svg>'''
    out_path.write_text(svg, encoding="utf-8")
    print(f"{theme_name}: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    result = docx_convert(FIXTURE)
    assert "```mermaid\n" + _MERMAID_CODE + "\n```" in result.markdown, (
        "this asset's hardcoded _MERMAID_CODE drifted from what "
        "refigure.docx.convert() actually produces for this fixture — regenerate"
    )
    for label in _LABELS:
        assert label in result.markdown, (
            f"{label!r} missing from convert() output — caption claim would be false"
        )
    print(
        f"verified live: charts_found={result.charts_found}, "
        f"charts_rendered={result.charts_rendered}"
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        input_png = render_input_screenshot(tmp)
        for theme_name, theme in THEMES.items():
            mermaid_svg = render_mermaid_svg(theme)
            compose(
                theme_name,
                theme,
                input_png,
                mermaid_svg,
                OUT_DIR / f"demo-docx-chart-{theme_name}.svg",
            )


if __name__ == "__main__":
    main()
