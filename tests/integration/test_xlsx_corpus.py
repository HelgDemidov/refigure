"""Real-corpus behavioral tests for ``refigure.xlsx.convert()`` (stage 5).

Runs against the actual XLSX fixtures cataloged in
``tests/integration/fixtures/manifest.yaml``. Fixture binaries are
gitignored (see ``fixtures/README.md``) — any entry whose file isn't
present on disk is skipped via ``conftest.fixture_params`` (fresh clone /
CI without local fixture setup), not failed.

Each fixture gets ONE test running two assertion tiers against a single
``convert()`` call (mirrors ``test_docx_corpus.py``'s reasoning — some
fixtures take several seconds, no need to double total suite time by
running ``convert()`` twice per fixture for separately-named tiers):

* Tier A — invariants that hold identically for every fixture, regardless
  of its specific content (no fixture-specific numbers).
* Tier B — one pinned golden value per fixture, capturing the ACTUAL
  ``charts_found``/``charts_rendered``/``groups_found`` observed by running
  ``convert()`` against the real file on 2026-08-05 (git branch
  ``test/stage5-port-and-corpus-tests``) — NOT values inferred from
  ``manifest.yaml``'s raw-XML provenance notes. ``groups_found`` is
  hardcoded to ``0`` in ``refigure/xlsx.py`` itself (xlsx has no
  composite-group concept — see its docstring/§ ``ConversionResult(...,
  groups_found=0, ...)``), so that part of the tuple is trivially always 0
  by construction, not something that could regress independently.

  For ``charts_found``/``charts_rendered`` specifically: unlike docx (whose
  ``docx_groups.py`` narrows "every chart part" down to "only charts inside
  a detected composite figure group"), xlsx chart discovery in
  ``xlsx_charts.py`` is a much more direct "walk every sheet's drawing,
  every anchor, every ``c:chart`` reference" operation — so raw-XML part
  counts and refigure's reported counts were expected (per this task's
  brief) to match more often here than they did for docx.

  UPDATE 2026-08-05, same day as the initial corpus-test pass: this first
  run found 4 fixtures diverging from their manifest expectations, traced
  (via ``superpowers:systematic-debugging``) to two genuine root-cause bugs
  in ``refigure`` itself, both now FIXED — the values below are the
  POST-FIX observations, re-captured after the fix, not the original buggy
  ones this test suite caught:

  * ``chart_data.py``'s ``_materialize_num``/``_pt_count`` did unguarded
    ``float()``/``int()`` on cached OOXML text, crashing with an uncaught
    ``ValueError`` whenever a chart cached an Excel error placeholder
    (``#N/A``, confirmed real: eia-steo-chart-gallery.xlsx, 19/67 chart
    parts) or a malformed ``idx``/``ptCount`` attribute. Fixed via
    ``_safe_int``/``_safe_float`` helpers routed through every numeric
    coercion in that file, not just the one crashing call site — closes the
    whole bug CLASS, not one instance (2 further crash vectors in the same
    file were confirmed reproducible before the fix, via synthetic XML, and
    are now covered by unit tests in ``test_chart_data.py``).
  * ``xlsx_charts.py``'s ``_chart_anchors`` used a singular ``.find()`` for
    ``c:chart`` inside each drawing anchor, silently dropping every chart
    after the first whenever an anchor was an ``xdr:grpSp`` (Excel's
    "group these charts" feature) wrapping multiple charts at one shared
    position — a real, confirmed structure (eia-aeo-2026-figures.xlsx,
    eia-ieo-2023-figures.xlsx, waste-statistics.xlsx), not a corruption.
    Silent data loss with zero trace in ``ConversionResult`` — the opposite
    of this project's zero-loss positioning. Fixed via ``.findall()``,
    yielding every chart per anchor; covered by a unit test in
    ``test_xlsx_charts.py``.
"""

from __future__ import annotations

import time

import pytest

from refigure.xlsx import convert

from .conftest import FixtureInfo, fixture_params

# Generous wall-clock ceiling, not a tight regression guard — same value and
# reasoning as test_docx_corpus.py's _CONVERT_TIMEOUT_S (a prior
# robustness-test round in this project hit real flakiness from tight timing
# budgets under parallel test-suite load, see feedback_untrusted_input_handling
# memory). Comfortably covers every observed fixture here, including
# mermaidx's one-time QuickJS cold-start cost on whichever fixture runs
# first in a session (observed ~10s on even the smallest, 51KB fixture,
# purely from that warmup, not file size/content).
_CONVERT_TIMEOUT_S = 120.0

# Exact warning strings refigure/xlsx.py's convert() can append to
# ConversionResult.warnings, as of 2026-08-05 (read directly from source, not
# guessed/copied from an older memory of it — keep this set in sync if
# xlsx.py's warning text changes; it's not exposed as named constants there).
_KNOWN_XLSX_WARNINGS = frozenset(
    {
        "no extractable content",
        "mermaidx not installed — chart diagrams disabled, tables only "
        "(install refigure[xlsx] with mermaidx to enable rendering)",
    }
)

# --- Two bugs found by this test file's first run, 2026-08-05 — now FIXED
# (root-caused via superpowers:systematic-debugging) ------------------------
#
# Bug #1 (crash): eia-steo-chart-gallery.xlsx made refigure.xlsx.convert()
# RAISE an uncaught ValueError instead of returning a ConversionResult:
# refigure/chart_data.py's _materialize_num did `float(v)` unconditionally
# on every cached point text, and 19 of this file's 67 native chart parts
# contain a literal "#N/A" (an Excel formula-error placeholder — common in
# EIA forecast-vs-actual charts where a future period has no projection
# under some scenario). This contradicted chart_data.py's own module
# docstring promise ("does not raise exceptions on a structurally
# incomplete or unusual chart") and refigure's public-API contract (only 3
# typed exceptions). FIXED: _safe_int/_safe_float helpers now guard every
# numeric coercion in chart_data.py, not just this one call site — two
# further crash vectors in the same file (malformed `idx`/`ptCount`
# attributes) were confirmed reproducible via synthetic XML before the fix
# and are now covered by unit tests in test_chart_data.py.
#
# Bug #2 (silent data loss): three fixtures showed charts_found LOWER than
# manifest.yaml's raw xl/charts/chartN.xml part count.
# refigure/xlsx_charts.py's _chart_anchors() used a singular lxml .find()
# to locate the c:chart reference inside one xdr:oneCellAnchor/
# xdr:twoCellAnchor — when a single anchor was an xdr:grpSp (Excel's "group
# these charts" feature) wrapping MULTIPLE charts at one shared position
# (real, confirmed structure — e.g. drawing25.xml's one anchor nesting 6
# charts, each in its own graphicFrame), every chart after the first was
# silently dropped: no warning, no error, no trace in ConversionResult at
# all. Reconciled arithmetic confirmed every missing chart in all three
# cases (eia-aeo-2026-figures.xlsx: 43-5=38; eia-ieo-2023-figures.xlsx:
# 93-48=45; waste-statistics.xlsx: 22-1=21). This was silent DATA LOSS —
# the opposite of this project's "zero-loss" chart-extraction positioning.
# FIXED: .find() -> .findall(), yielding every chart per anchor; covered by
# a unit test in test_xlsx_charts.py.
#
# Pinned baselines below are the POST-FIX values, re-captured 2026-08-05
# after both fixes landed — filename -> (charts_found, charts_rendered,
# groups_found), captured live by running convert() against every fixture
# and cross-checking the result against manifest.yaml's notes field.
_PINNED_XLSX_VALUES: dict[str, tuple[int, int, int]] = {
    # manifest: "1 native chart part, confirmed c:radarChart (4 c:numCache +
    # 8 c:strCache)". Matches: 1 chart, fully rendered (mermaid radar-beta).
    "daisy-trd2-radar-scoring.xlsx": (1, 1, 0),
    # manifest: "43 native chart parts (area=8, line=26, scatter=1,
    # bar=11)". Post-Bug-#2-fix: charts_found=43, matching exactly (was 38
    # pre-fix — see the Bug #2 comment above). All 43 render successfully.
    "eia-aeo-2026-figures.xlsx": (43, 43, 0),
    # manifest: "93 native chart parts (area=67, line=78, bar=15) — highest
    # chart count in the entire corpus". Post-Bug-#2-fix: charts_found=93,
    # matching exactly (was 45 pre-fix — see the Bug #2 comment above). All
    # 93 render successfully.
    "eia-ieo-2023-figures.xlsx": (93, 93, 0),
    # manifest: "5 native chart parts — 1 confirmed c:pieChart, 1 lineChart,
    # 3 barChart". Matches: 5 charts (the 3 "barChart" entries classify as
    # refigure's "column" subtype per c:barDir — a finer split than the
    # manifest's OOXML-tag-level note, not a mismatch), all rendered.
    "electricity-production-market-2023.xlsx": (5, 5, 0),
    # manifest: "5 native chart parts — 1 confirmed c:doughnutChart, 4
    # barChart". Matches: 5 charts, all rendered (the 4 "barChart" entries
    # classify as "column", same barDir-based split as above).
    "foodrus-dashboard.xlsx": (5, 5, 0),
    # manifest note for this fixture documents provenance (core.xml
    # byte-identity to the official WBG release) but makes no chart-count
    # claim to cross-check against — same situation as docx's
    # iot-report-2022-national-strategies-excerpt.docx "own" fixture.
    # Pinning the observed value as a plain regression baseline only.
    "govtech-2025-charts.xlsx": (55, 55, 0),
    # manifest: "9 native chart parts — 5 confirmed c:radarChart, 4
    # confirmed c:bubbleChart". Matches: 9 charts found. IMPORTANT: all 9
    # are ALSO reported as "rendered" (charts_rendered == charts_found),
    # including the 4 bubble charts — even though bubbleChart is not one of
    # chart_render.py's known mermaid-producing types (confirmed: bubble is
    # NOT in _PIE_LIKE/_XYCHART_LIKE/radar; also confirmed structurally that
    # xlsx_charts.py's _ALL_CHART_TAGS doesn't include "bubbleChart" at all,
    # so chart_data.parse_chart classifies these as chart_type="other" —
    # their series/category data still gets extracted via the same
    # xVal/yVal fallback used for scatter charts, since bubbleChart's c:ser
    # happens to carry those same element names). This is the key finding
    # for "does charts_rendered drop for unmapped chart types": it does
    # NOT. render_chart()/_render_xlsx_chart_block() in refigure/xlsx.py
    # count a chart as "rendered" whenever ANY markdown block (table with or
    # without a mermaid diagram) is produced — a mermaid diagram is only
    # ADDITIVE within that block, not a precondition for "rendered". Direct
    # per-chart inspection confirmed: all 4 bubble ("other"-typed) charts
    # here produce a table-only block (no mermaid fence), and still count
    # toward charts_rendered. charts_rendered < charts_found only happens
    # when a chart's numCache is genuinely EMPTY (see waste-statistics.xlsx
    # below for a real example of that case) — not merely "unmapped type".
    "radiant-metrics-dashboard.xlsx": (9, 9, 0),
    # manifest: "4 native chart parts, ALL 4 confirmed true combo charts...
    # Only true-combo fixture in the corpus". Chart count matches (4). Two
    # things confirmed by direct inspection, both worth recording:
    # (1) all 4 render as TABLE-ONLY, not mermaid, despite "combo" being one
    # of chart_render.py's _XYCHART_LIKE types — each combo chart's cached
    # values contain real gaps (None entries) in at least one series, which
    # trips _series_shape_ok's "no gaps allowed" check and correctly drops
    # to table-only per chart_render.py's own documented verify+fallback
    # design (not a bug — real-world data exercising the designed fallback
    # path, previously untested against an actual document per this
    # fixture's own manifest note).
    # (2) the manifest's "only true-combo fixture in the corpus" claim does
    # NOT hold: wb-gep-jan2022-ch3-annex.xlsx's 13 charts are ALSO genuine
    # area+line overlay combos (confirmed by direct XML inspection — see its
    # entry below), just described there with the looser "area/line"
    # wording rather than "combo". A manifest-note precision gap (like
    # docx's marcobolo case), not a refigure bug.
    "renewable-energy-stats.xlsx": (4, 4, 0),
    # manifest: "22 native chart parts (108 c:numCache + 128 c:strCache) —
    # 1 confirmed c:pieChart, 1 lineChart, 20 barChart". Post-Bug-#2-fix:
    # charts_found=22, matching exactly (was 21 pre-fix — see the Bug #2
    # comment above). Separately, charts_rendered=15 < charts_found=22 for a
    # DIFFERENT, non-buggy reason: confirmed by direct per-chart inspection
    # that exactly 7 of the 22 found charts have genuinely empty numCache
    # data (a single category, single series, all-None values — degenerate
    # placeholder charts in the source file), so render_chart() correctly
    # returns None for them and refigure falls back to the honest "chart
    # content not analyzed" caption marker instead of a table. This is the
    # real-world case the task brief anticipated for "charts_rendered <
    # charts_found", just triggered by empty data rather than an unmapped
    # chart type (see the radiant-metrics-dashboard.xlsx entry above for why
    # "unmapped type" alone does not, in fact, cause this).
    "waste-statistics.xlsx": (22, 15, 0),
    # manifest: "13 native chart parts (area/line), 102 c:numCache + 8
    # c:strCache". Chart count matches (13). chart_type breakdown is 100%
    # "combo" for all 13, not plain area or line — confirmed by direct XML
    # inspection: every one of these 13 chart parts genuinely contains BOTH
    # a c:areaChart AND a c:lineChart element in its plotArea, each with
    # real (non-empty) series — true overlay combo charts, not a
    # misclassification. The manifest's "area/line" wording described the
    # series content, not chart-part structure, so this isn't a manifest
    # error requiring correction (same class of gap as marcobolo's docx
    # note, milder) — but it does mean the renewable-energy-stats.xlsx
    # manifest note's "only true-combo fixture in the corpus" claim is
    # incorrect (see that entry above). All 13 render successfully.
    "wb-gep-jan2022-ch3-annex.xlsx": (13, 13, 0),
    # manifest: "10 native chart parts (bar/line), 62 c:numCache + 112
    # c:strCache". Matches: 10 charts, all rendered (a mix of "column"/
    # "line"/"combo" subtypes — consistent with the manifest's broad
    # "bar/line" description, which wasn't claiming exclusivity the way
    # renewable-energy-stats.xlsx's note did).
    "wb-gep-jan2022-ch4-annex.xlsx": (10, 10, 0),
    # manifest: "67 native chart parts (bar/line/area/scatter), 1240
    # c:numCache + 528 c:strCache". Post-Bug-#1-fix: charts_found=67,
    # matching exactly and no longer crashing (see the Bug #1 comment
    # above — 19 of these 67 parts cache a literal "#N/A" placeholder,
    # which _safe_float now turns into a graceful None data point instead
    # of an uncaught ValueError). All 67 render successfully — a chart with
    # some None-valued points among otherwise-real data still renders (the
    # existing gap-tolerant table/mermaid rendering path handles partial
    # data, this was never in question — only the crash was the bug).
    "eia-steo-chart-gallery.xlsx": (67, 67, 0),
}


@pytest.mark.parametrize("fx", fixture_params("xlsx"))
def test_xlsx_corpus_fixture(fx: FixtureInfo) -> None:
    start = time.monotonic()
    result = convert(fx.path)
    elapsed = time.monotonic() - start

    # --- Tier A: invariants, identical for every fixture ---
    assert result.markdown != "", "none of our fixtures are blank workbooks"
    assert result.charts_rendered <= result.charts_found
    assert result.charts_found >= 0
    # xlsx has no composite-group concept — refigure/xlsx.py hardcodes
    # groups_found=0 unconditionally (verified by reading the source, not
    # assumed), so this is a trivial-by-construction invariant, not an
    # empirical observation that could vary per fixture.
    assert result.groups_found == 0
    for warning in result.warnings:
        assert warning in _KNOWN_XLSX_WARNINGS, f"unexpected warning text: {warning!r}"
    assert elapsed < _CONVERT_TIMEOUT_S, (
        f"convert() took {elapsed:.1f}s for {fx.filename}, expected < {_CONVERT_TIMEOUT_S}s"
    )

    # --- Tier B: pinned golden value, one per fixture ---
    expected = _PINNED_XLSX_VALUES.get(fx.filename)
    if expected is None:
        pytest.fail(
            f"no pinned baseline for {fx.filename!r} in _PINNED_XLSX_VALUES — "
            "run convert() against the file, observe the real "
            "charts_found/charts_rendered/groups_found, and add a pinned entry "
            "(see this file's module docstring for the methodology)."
        )
    observed = (result.charts_found, result.charts_rendered, result.groups_found)
    assert observed == expected, (
        f"{fx.filename}: charts_found/charts_rendered/groups_found regressed "
        f"from pinned baseline {expected} to {observed}"
    )
