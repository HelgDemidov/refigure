"""Regenerate docs/assets/demo-{light,dark}.svg (stage 7 README + demo).

One-off, reproducible generator for the README hero graphic — NOT part of
the `refigure` package, not imported by anything under `refigure/`, no
runtime dependency added. Requires the system `soffice` (LibreOffice) +
`pdftoppm` (poppler-utils) binaries, plus the `mermaidx`/`Pillow` packages
(already dev/optional dependencies via `refigure[docx,xlsx]`).

What it does, in order:
1. Renders `tests/integration/fixtures/xlsx/foodrus-dashboard.xlsx`'s
   "Dashboard" sheet to a 300dpi PNG via `soffice --convert-to pdf` +
   `pdftoppm`, then crops the real "TOP 10 Communication" chart out of it —
   an authentic screenshot, not a redrawn recreation. The crop is
   deliberately generous (see `_CROP_BOX`) so the full circle badge is
   captured uncut; the circle's OWN clipping of ~3 of its 10 category
   labels at its edge is a genuine limit of the source file's chart design
   (verified 2026-08-06 — see the caption this script writes into the
   asset), not something to "fix".
2. Runs the real `refigure.xlsx.convert()` output through `mermaidx.render()`
   with a custom theme (contrast fix — mermaid's default xychart-beta bar
   fill is too pale on a white background) and a rank-based sequential
   colour fade across the 10 bars, adapted from `scopus_search_code`'s
   `chartColors.ts` (`getRankedBarColor`/`mixHex`): linear RGB interpolation
   from the base teal toward the theme's ink/paper token, capped at 88% so
   the last bar never fully merges into the background. This is REAL
   `mermaidx` output post-processed for legibility/brand consistency, not a
   hand-drawn substitute — but it does mean the rendered panel is captioned
   "same file, rendered" rather than "as GitHub renders it automatically":
   GitHub's native mermaid rendering would show flat colour bars (mermaid's
   `plotColorPalette` cycles per dataset, not per bar, within one series —
   confirmed empirically, no per-bar theming hook exists), not this fade.
3. Composites INPUT (screenshot) + arrow + OUTPUT (raw text view + rendered
   view) into one SVG per theme, embeds the screenshot as a base64 PNG and
   the mermaid render as an inline nested <svg>, and writes both files to
   `docs/assets/`.

Re-run whenever the demo's source fixture, chosen chart, or palette tokens
change. Does NOT run in CI — this is documentation-asset tooling, exercised
manually (see the "Ручная проверка" note in the stage 7 spec's test-coverage
section for the light/dark GitHub-rendering QA step this doesn't automate).
"""

from __future__ import annotations

import base64
import re
import subprocess
import tempfile
from pathlib import Path

import mermaidx
from PIL import Image

from refigure.xlsx import convert as xlsx_convert

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests/integration/fixtures/xlsx/foodrus-dashboard.xlsx"
OUT_DIR = REPO / "docs/assets"

# Generous crop of the 300dpi page-1 render (the "Dashboard" sheet prints
# first) — chosen 2026-08-06 by visual inspection to include the FULL
# circle badge uncut, with margin. Wider than strictly necessary on
# purpose: the circle's own label-clipping (RFID..LoRa, ~3 of 10 cut at the
# edge) is genuine to the source and must survive untouched; only OUR
# cropping must not add a second, artificial cut on top of it.
_CROP_BOX = (150, 150, 1650, 1650)
_CROP_DPI = 300
_DISPLAY_W = 372

_MERMAID_CODE = (
    "xychart-beta\n"
    'x-axis ["RFID", "WSN", "WiFi", "GPRS", "Bluetooth", "Unknown", '
    '"3G", "Internet", "Raspberry Pi", "LoRa"]\n'
    'y-axis "Value" 0 --> 246\n'
    "bar [246, 132, 85, 59, 57, 36, 30, 28, 25, 25]"
)
_N_BARS = 10
_FADE_MAX_T = 0.88  # same cap as scopus_search_code's chartColors.ts

_RAW_EXCERPT_LINES = [
    "> sheet Dashboard, anchor B8",
    "",
    "```mermaid",
    "xychart-beta",
    'x-axis ["RFID", "WSN", "WiFi", …]',
    'y-axis "Value" 0 --> 246',
    "bar [246, 132, 85, 59, 57, 36, …]",
    "```",
    "",
    "| Category | Total |",
    "| --- | --- |",
    "| RFID | 246 |",
    "| WSN | 132 |",
    "| … | … |",
]

FONT_MONO = "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONT_SANS = "Inter, ui-sans-serif, -apple-system, Segoe UI, sans-serif"

THEMES = {
    # `muted` picked for >=4.5:1 contrast against `bg` (WCAG AA, normal text)
    # — verified 2026-08-06: #8A8A82/#FAFAF8 was only 3.33:1 (fails), #63635A
    # is 5.8:1. Dark theme's #9A988F/#14150F was already 6.35:1, unchanged.
    "light": dict(
        bg="#FAFAF8",
        ink="#1A1A1A",
        muted="#63635A",
        teal="#1E7A6E",
        blue="#3B5BA5",
        mermaid_text="#333333",
        fade_target="#1A1A1A",
    ),
    "dark": dict(
        bg="#14150F",
        ink="#EDEDE6",
        muted="#9A988F",
        teal="#4FBBA8",
        blue="#7C9CD6",
        mermaid_text="#e8e6df",
        fade_target="#FAFAF8",
    ),
}
_TEAL_BASE = "#1E7A6E"
# Fixed (not per-theme) — the INPUT label always sits on the real
# screenshot's own white top margin, in both themes; 6.5:1 contrast on
# white, verified 2026-08-06 (the dark theme's own `blue` token is 2.8:1
# on white — tuned for a dark page background this label never touches).
_INPUT_LABEL_COLOR = "#3B5BA5"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(round(r), round(g), round(b))


def _mix_hex(a: str, b: str, t: float) -> str:
    """Linear RGB interpolation — same formula as scopus_search_code's mixHex."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


def ranked_fade(base: str, target: str, n: int = _N_BARS) -> list[str]:
    return [_mix_hex(base, target, (i / (n - 1)) * _FADE_MAX_T) for i in range(n)]


def render_input_screenshot(tmp: Path) -> Path:
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(FIXTURE)],
        check=True,
        capture_output=True,
        timeout=90,
    )
    pdf_path = tmp / f"{FIXTURE.stem}.pdf"
    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r",
            str(_CROP_DPI),
            "-f",
            "1",
            "-l",
            "1",
            str(pdf_path),
            str(tmp / "page"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    page_png = next(tmp.glob("page-*.png"))
    im = Image.open(page_png)
    cropped = im.crop(_CROP_BOX)
    out = tmp / "input_crop.png"
    cropped.save(out)
    return out


def render_mermaid_svg(theme: dict[str, str], tmp: Path) -> str:
    config = {
        "theme": "base",
        "themeVariables": {
            # Default mermaid font-size is 16px — at this asset's ~0.49x
            # display scale (900x460 native, ~440x225 shown) that renders
            # near-illegible. 27px survives the scale-down at a readable
            # ~13px effective size — verified 2026-08-06 it does NOT
            # reintroduce the label-collision fixed earlier (mermaid
            # reflows its own layout for the larger font, still fits all
            # 10 categories unrotated at width=900).
            "fontSize": "27px",
            "xyChart": {
                "plotColorPalette": _TEAL_BASE,
                "backgroundColor": "transparent",
                "titleColor": theme["mermaid_text"],
                "xAxisLabelColor": theme["mermaid_text"],
                "xAxisLineColor": theme["mermaid_text"],
                "xAxisTitleColor": theme["mermaid_text"],
                "yAxisLabelColor": theme["mermaid_text"],
                "yAxisLineColor": theme["mermaid_text"],
                "yAxisTitleColor": theme["mermaid_text"],
            },
        },
        "xyChart": {"width": 900, "height": 460},
    }
    svg = mermaidx.render(_MERMAID_CODE, theme="base", config=config).svg()

    fade = ranked_fade(_TEAL_BASE, theme["fade_target"])
    counter = {"i": 0}

    def repl(m: re.Match[str]) -> str:
        i = counter["i"]
        counter["i"] += 1
        color = fade[i] if i < len(fade) else fade[-1]
        tag = m.group(0)
        return tag.replace(f'fill="{_TEAL_BASE}"', f'fill="{color}"').replace(
            f'stroke="{_TEAL_BASE}"', f'stroke="{color}"'
        )

    pattern = re.compile(
        rf'<rect[^>]*fill="{re.escape(_TEAL_BASE)}"[^>]*stroke="{re.escape(_TEAL_BASE)}"[^>]*></rect>'
    )
    svg, n = pattern.subn(repl, svg)
    assert n == _N_BARS, f"expected to recolor {_N_BARS} bars, recolored {n}"
    return svg


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def compose(
    theme_name: str, theme: dict[str, str], input_png: Path, mermaid_svg: str, out_path: Path
) -> None:
    W, H = 1200, 680
    pad = 40
    # in_y matches boxA_y (96) — INPUT panel's top edge now level with the
    # data panel's, per 2026-08-06 alignment fix (was 112 vs 96).
    # in_w widened +70 (was _DISPLAY_W=372) — reclaimed from the OUTPUT
    # column's unused width: the rendered chart panel is height-bound, not
    # width-bound (its native 900:460 aspect fits its height budget before
    # its width budget), so it had ~74px of slack doing nothing. 70px
    # keeps a 4px margin under that threshold — verified the chart's
    # display size is unchanged (still height-bound after the reclaim).
    in_x, in_y, in_w = pad, 96, _DISPLAY_W + 70

    im = Image.open(input_png).convert("RGB")
    in_h = round(im.height * in_w / im.width)
    resized = im.resize((in_w, in_h), Image.Resampling.LANCZOS)
    buf = input_png.with_name(f"input_resized_{in_w}.png")
    resized.save(buf, optimize=True)
    input_b64 = base64.b64encode(buf.read_bytes()).decode("ascii")
    # Symmetric gap on both sides of the centered arrow label (mono, 11px,
    # ~24 chars, half-width ~79px) — verified 2026-08-06 clears BOTH the
    # INPUT panel's right edge and box A's left edge with margin (a
    # lopsided gap=110/78 split cleared the left side but not the right).
    arrow_gap = 100
    arrow_cx = in_x + in_w + arrow_gap
    arrow_y = in_y + in_h / 2
    out_x = arrow_cx + arrow_gap
    out_w = W - pad - out_x
    # 46:54 raw-text:rendered-chart split (was ~58:42) — rebalanced
    # 2026-08-06 so the rendered mermaid chart gets more room for a larger
    # font; the raw-text panel's own font shrinks slightly to still fit.
    boxA_y, boxA_h = 96, 238
    boxB_y = boxA_y + boxA_h + 26
    boxB_h = H - pad - boxB_y
    mono_size, line_h, text_pad_x, text_pad_top = 10.5, 13.6, 18, 46

    raw_tspans = "".join(
        f'<tspan x="{out_x + text_pad_x}" dy="{0 if i == 0 else line_h}">{_esc(line)}</tspan>'
        for i, line in enumerate(_RAW_EXCERPT_LINES)
    )

    chart_w: float = out_w - 2 * text_pad_x
    chart_ratio = 460 / 900
    chart_h = chart_w * chart_ratio
    if boxB_h - 56 < chart_h:
        chart_h = boxB_h - 56
        chart_w = chart_h / chart_ratio

    bg, ink, muted, teal = theme["bg"], theme["ink"], theme["muted"], theme["teal"]

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="refigure before/after demo: a real xlsx chart converted to markdown with native numCache data, shown as both raw text and a rendered diagram">
<rect width="{W}" height="{H}" fill="{bg}"/>

<text x="{pad}" y="52" font-family="{FONT_SANS}" font-size="14" letter-spacing="0.04em" fill="{muted}">FOODRUS-DASHBOARD.XLSX &#8594; refigure.xlsx.convert() &#8594; MARKDOWN</text>

<rect x="{in_x - 1}" y="{in_y - 1}" width="{in_w + 2}" height="{in_h + 2}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<image x="{in_x}" y="{in_y}" width="{in_w}" height="{in_h}" href="data:image/png;base64,{input_b64}"/>
<!-- Label sits directly on the screenshot, same top/left inset as the
     OUTPUT panels' titles (text_pad_x, +26 baseline) — NO background chip
     behind it (2026-08-06: an opaque chip painted a visible dark patch
     over the real screenshot in the dark theme; the screenshot's own
     background must not be touched, only the label added). The
     screenshot's top margin is white in both themes (it's the same real
     capture either way) — _INPUT_LABEL_COLOR is a fixed on-white-safe
     blue (6.5:1), not the theme's `blue` token, which is deliberately
     LIGHTER for the dark theme's own dark page background and would fail
     contrast here (2.8:1) since this label never actually sits on that
     background. -->
<text x="{in_x + text_pad_x}" y="{in_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{_INPUT_LABEL_COLOR}">INPUT — a real chart</text>
<text x="{in_x}" y="{in_y + in_h + 24}" font-family="{FONT_SANS}" font-size="11.5" fill="{muted}">
<tspan x="{in_x}" dy="0">the source clips 3 of 10 categories at its own</tspan>
<tspan x="{in_x}" dy="15">circle edge — refigure reads cached values, not</tspan>
<tspan x="{in_x}" dy="15">pixels: all 10 survive in the output &#8594;</tspan>
</text>

<text x="{arrow_cx}" y="{arrow_y - 16}" font-family="{FONT_MONO}" font-size="11" fill="{ink}" text-anchor="middle">refigure.xlsx.convert()</text>
<text x="{arrow_cx}" y="{arrow_y + 7}" font-family="{FONT_MONO}" font-size="20" letter-spacing="-3px" fill="{muted}" text-anchor="middle">&gt;&gt;&gt;&gt;&gt;</text>
<text x="{arrow_cx}" y="{arrow_y + 28}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">native OOXML numCache</text>
<text x="{arrow_cx}" y="{arrow_y + 42}" font-family="{FONT_SANS}" font-size="11" fill="{muted}" text-anchor="middle">— not OCR</text>

<rect x="{out_x}" y="{boxA_y}" width="{out_w}" height="{boxA_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxA_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{teal}">OUTPUT — as your LLM/RAG pipeline reads it</text>
<text x="{out_x + text_pad_x}" y="{boxA_y + text_pad_top}" font-family="{FONT_MONO}" font-size="{mono_size}" fill="{ink}">{raw_tspans}</text>

<rect x="{out_x}" y="{boxB_y}" width="{out_w}" height="{boxB_h}" fill="none" stroke="{muted}" stroke-width="1" opacity="0.35"/>
<text x="{out_x + text_pad_x}" y="{boxB_y + 26}" font-family="{FONT_SANS}" font-size="15" font-weight="600" fill="{teal}">OUTPUT — same file, rendered</text>
<svg x="{out_x + (out_w - chart_w) / 2}" y="{boxB_y + 40}" width="{chart_w}" height="{chart_h}" viewBox="0 0 900 460">
{mermaid_svg}
</svg>

</svg>'''
    out_path.write_text(svg, encoding="utf-8")
    print(f"{theme_name}: {out_path} ({out_path.stat().st_size} bytes)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        input_png = render_input_screenshot(tmp)
        for theme_name, theme in THEMES.items():
            mermaid_svg = render_mermaid_svg(theme, tmp)
            compose(theme_name, theme, input_png, mermaid_svg, OUT_DIR / f"demo-{theme_name}.svg")

    # sanity check the extraction claim the asset itself makes
    result = xlsx_convert(FIXTURE)
    for label in [
        "RFID",
        "WSN",
        "WiFi",
        "GPRS",
        "Bluetooth",
        "Unknown",
        "3G",
        "Internet",
        "Raspberry Pi",
        "LoRa",
    ]:
        assert label in result.markdown, (
            f"{label!r} missing from convert() output — caption claim would be false"
        )
    print("verified: all 10 categories present in refigure.xlsx.convert() output")


if __name__ == "__main__":
    main()
