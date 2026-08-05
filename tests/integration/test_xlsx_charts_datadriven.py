"""Local deterministic check (spec chart-data-extraction §Test coverage +
§Final acceptance): the data-driven chart path against a REAL, not
synthetic, third-party workbook (``tests/integration/fixtures/xlsx/
govtech-2025-charts.xlsx``, World Bank GovTech Maturity Index Dataset, CC BY
4.0, gitignored -> skipif on a fresh clone). ALL 55 embedded charts are
preserved byte-identical (``xl/charts/*``/``xl/drawings/*`` untouched) —
only redundant sheet rows/columns and bloated per-cell ``<hyperlinks>`` were
trimmed (see the source pipeline's ``make_govtech_charts_fixture.py``, which
regenerates the fixture from the full workbook deterministically). This replaced the
original FULL workbook (5.4 MB, 2 sheets with 700k+ live cells) on
2026-07-22 by user decision: the full workbook was too heavy for a local
full-gate run (``openpyxl.load_workbook`` alone took 16.5s, driven by the
volume of live data entirely unrelated to the charts — ``parse_chart`` reads
ONLY the cached ``<c:numCache>``/``<c:strCache>`` inside the chart part,
never sheet cells). After trimming, the same coverage holds (55/55 charts
reachable, 0 crashes, 33/55 mermaid) at ~5s/run for the equivalent of
``_convert_xlsx`` in the source pipeline (``refigure.xlsx.convert()`` here)
— down from ~22s. This differs from a prior, more aggressively trimmed
Stats-only fixture (24/55 charts reachable, the rest orphaned by surgically
deleting 8 entire SHEETS) in method: here only rows/columns INSIDE each of
the 9 sheets were trimmed, no sheet/drawing/chart part was removed — orphan
charts cannot arise in principle. Unlike the removed
``test_xlsx_charts_live.py`` (spec convert-xlsx §3, required a system
``soffice`` install to render an image) — this check has NO external system
dependencies: parse_chart/render_chart are pure functions, and
``refigure.xlsx.convert()`` is openpyxl+lxml only, no soffice/network.
Lives in ``tests/integration/`` (not ``unit/``) solely because of its
dependency on a non-hermetic external file, not a system resource.

Ported from the source private pipeline
(``pipeline/scripts/tests/integration/test_xlsx_charts_datadriven.py``) —
the fixture file here is byte-identical (sha256-verified) to the one at the
source path this test originally pointed at
(``tests/fixtures/local/govtech-2025-charts.xlsx``). The private pipeline's
``convert.converters._convert_xlsx(raw, out_path, lang)`` wrote markdown to
a file and took a language code; refigure's public
``refigure.xlsx.convert(source) -> ConversionResult`` instead returns
markdown in-memory via ``ConversionResult.markdown`` (no output file, no
language parameter — refigure's output is English-only) plus structured
counters (``charts_found``/``charts_rendered``) that ``_convert_xlsx`` never
exposed directly — the assertions below use those counters where they carry
the same information the original test derived from re-parsing the file
output."""

from __future__ import annotations

from pathlib import Path

import pytest

import refigure.core.chart_data as chart_data
import refigure.core.chart_render as chart_render
import refigure.xlsx.charts as xlsx_charts
from refigure.xlsx import convert

_FIXTURE = Path(__file__).parent / "fixtures" / "xlsx" / "govtech-2025-charts.xlsx"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.exists(),
    reason="test fixture fixtures/xlsx/govtech-2025-charts.xlsx is absent (gitignored)",
)

# All 55 physical xl/charts/*.xml parts are reachable (3 sheets — Stats/
# Regions/Trends carry drawing anchors) — trimming sheets doesn't touch any
# chart/drawing part, so there are no orphan fragments (extract_charts == a
# bare zip-glob count, for once they coincide, but the code must still go
# through the former, not a glob).
_EXPECTED_REACHABLE_CHARTS = 55
_KNOWN_DOUGHNUT_ID = "81e3f64eb12d"  # "Institutional Responsibility for GovTech" (Stats!BO37)
_KNOWN_RADAR_ID = "96a22b948568"  # "GovTech Maturity Index Components" (Stats!AP25)


def test_all_reachable_charts_parse_and_render_without_crash() -> None:
    charts = xlsx_charts.extract_charts(_FIXTURE)
    roots = xlsx_charts.extract_chart_roots(_FIXTURE)
    assert len(charts) == _EXPECTED_REACHABLE_CHARTS

    crashes: list[tuple[str, str]] = []
    mermaid_count = 0
    for chart in charts:
        try:
            data = chart_data.parse_chart(roots[chart.id12])
            rendered = chart_render.render_chart(data)
        except Exception as exc:  # noqa: BLE001 — a safety net: catch ANY crash on real data
            crashes.append((chart.id12, repr(exc)))
            continue
        assert rendered is not None, f"{chart.id12}: empty extraction on a real chart with numCache"
        if "```mermaid" in rendered:
            mermaid_count += 1
    assert crashes == []
    # A lower bound, not an exact count — catches a gross type-mapping
    # regression, not overfit to the current exact count (live measurement
    # from the final acceptance run, 2026-07-22: 33/55 — column/bar/combo/
    # radar/doughnut get mermaid, stacked-bar/scatter honestly get a table
    # only).
    assert mermaid_count >= 25


def test_known_doughnut_chart_type_and_categories() -> None:
    roots = xlsx_charts.extract_chart_roots(_FIXTURE)
    data = chart_data.parse_chart(roots[_KNOWN_DOUGHNUT_ID])
    assert data.chart_type == "doughnut"
    assert data.title == "Institutional Responsibility for GovTech"
    assert "Ministry of ICT" in data.categories
    assert len(data.series) == 1
    assert all(v is not None for v in data.series[0].values)


def test_known_radar_chart_type_and_series_count() -> None:
    roots = xlsx_charts.extract_chart_roots(_FIXTURE)
    data = chart_data.parse_chart(roots[_KNOWN_RADAR_ID])
    assert data.chart_type == "radar"
    assert data.title == "GovTech Maturity Index Components"
    assert len(data.categories) == 4  # CGSI/PSDI/DCEI/GTEI
    assert len(data.series) == 3  # Regional Avg / Global Avg / Mozambique (a live fixture fact)


def test_convert_xlsx_full_fixture_produces_stable_output_with_provenance() -> None:
    result = convert(_FIXTURE)
    text = result.markdown
    assert "## Stats" in text
    assert "> sheet Stats, anchor" in text
    assert "Institutional Responsibility for GovTech" in text
    assert "GovTech Maturity Index Components" in text
    assert "```mermaid" in text
    # All 55 reachable charts extract non-empty content (see
    # test_all_reachable_charts_parse_and_render_without_crash above) -> none
    # should fall back to the honest caption-only marker in a full convert() run.
    assert "chart content not analyzed" not in text
    # ConversionResult's structured counters (refigure.xlsx.convert() has no
    # file-output/language-code arguments, unlike the source pipeline's
    # _convert_xlsx — see the module docstring): every one of the 55
    # reachable charts is found AND renders non-None content, so both
    # counters equal the full reachable count.
    assert result.charts_found == _EXPECTED_REACHABLE_CHARTS
    assert result.charts_rendered == _EXPECTED_REACHABLE_CHARTS


def test_convert_xlsx_full_fixture_deterministic_across_runs() -> None:
    """Golden-safety (spec §5): a reconversion -> output identical to the
    first run, something that could never be guaranteed for the VLM path.
    ``raw`` is NOT modified (see the ``xlsx_charts`` module docstring) —
    running convert() twice against the same file is safe."""
    result1 = convert(_FIXTURE)
    result2 = convert(_FIXTURE)
    assert result1.markdown == result2.markdown


def test_all_mermaid_blocks_accepted_by_real_mermaid_js() -> None:
    """A standing analog of the 2026-07-22 final acceptance run (spec
    chart-data-extraction §Final acceptance): not just our heuristics
    (``test_chart_render_visual.py``, synthetic ``ChartData``), but ALL real
    mermaid blocks that ``render_chart`` produces from this specific
    workbook — through real mermaid.js (``mermaidx``, a runtime dependency
    — ``render_chart`` already gates every block through it itself; this
    test duplicates the check explicitly as a standing regression check).
    Live measurement: 33/55 charts produce mermaid, ~116ms/diagram on a
    warmed-up engine."""
    import mermaidx

    charts = xlsx_charts.extract_charts(_FIXTURE)
    roots = xlsx_charts.extract_chart_roots(_FIXTURE)
    failures: list[tuple[str, str]] = []
    mermaid_count = 0
    for chart in charts:
        data = chart_data.parse_chart(roots[chart.id12])
        rendered = chart_render.render_chart(data)
        if rendered is None or "```mermaid" not in rendered:
            continue
        mermaid_count += 1
        fence = rendered.split("```mermaid\n", 1)[1].split("```", 1)[0]
        try:
            mermaidx.render(fence).svg()
        except Exception as exc:  # noqa: BLE001 — any real-render failure is a finding of this test
            failures.append((chart.id12, repr(exc)[:200]))
    assert mermaid_count >= 25
    assert failures == []
