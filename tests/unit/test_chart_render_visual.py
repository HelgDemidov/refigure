"""Render-validation of chart_render.py's mermaid output by the real
mermaid.js (spec chart-data-extraction, post-review fix 2026-07-22): our own
heuristics (label sanitization, verify+fallback-to-table) and syntax
validators (``mermaid-parser-bundle``/``mermaid.parse()``, checked
separately, never added as a permanent dependency) catch GRAMMATICAL
violations but not SEMANTIC/visual ones — a live example found on a real
render: ``pie title "T"`` is syntactically valid (quotes inside a flat
string aren't a grammar error), but mermaid.js renders the quotes LITERALLY
(a pie chart's title — pie title — is a plain string, not
quote-delimited, unlike data labels). Only an actual render catches this.

``mermaidx`` (dev dependency, requirements-dev.txt) — Python-native, no
Node/npm/browser: runs real mermaid.js through an embedded QuickJS. Not a
runtime dependency of the pipeline (user decision 2026-07-22) — test-only;
``refigure.docx``/``refigure.xlsx`` keep emitting mermaid WITHOUT a render
check on every conversion."""

from __future__ import annotations

import re

import mermaidx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from refigure.chart_data import ChartData, ChartSeries
from refigure.chart_render import render_chart

_QUOTE_RE = re.compile(r'"')


def _mermaid_fence(rendered: str) -> str:
    assert "```mermaid" in rendered
    return rendered.split("```mermaid", 1)[1].split("```", 1)[0].strip()


def _render_ok(code: str) -> str:
    """A real render through mermaidx -> SVG string. Raises if mermaid.js
    doesn't accept the diagram — the test functions check this via the
    absence of an exception (``pytest.raises`` isn't used, a test crash IS
    the signal)."""
    return mermaidx.render(code).svg()  # type: ignore[no-any-return]


pytestmark = pytest.mark.mermaid  # a real mermaidx render on every test — seconds, not ms


def test_pie_with_title_renders_without_literal_quotes_in_title() -> None:
    """Regression (found on a real render of the govtech fixture,
    2026-07-22): a pie chart's title is a flat string, not
    quote-delimited. The live shape of the defect: mermaid.js renders the
    title into the SVG as
    ``<text class="pieTitleText">&quot;Institutional Responsibility&quot;</text>``
    — the quotes are HTML-encoded as ``&quot;``, not left as a literal ``"``
    (confirmed by manual inspection of the SVG on the BROKEN version before
    the fix — a naive check for a literal ``"`` would have missed this
    shape)."""
    data = ChartData(
        chart_type="pie",
        title="Institutional Responsibility",
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=("A", "B"),
        series=(ChartSeries(name="S", values=(1.0, 2.0), kind="pie"),),
    )
    rendered = render_chart(data)
    assert rendered is not None
    svg = _render_ok(_mermaid_fence(rendered))
    title_match = re.search(r'class="pieTitleText"[^>]*>(.*?)</text>', svg)
    assert title_match is not None, "pieTitleText not found in SVG"
    title_text = title_match.group(1)
    assert not title_text.startswith("&quot;")
    assert not title_text.endswith("&quot;")
    assert title_text == "Institutional Responsibility"


def test_doughnut_with_many_categories_renders() -> None:
    data = ChartData(
        chart_type="doughnut",
        title="Institutional Responsibility for GovTech",
        value_axis_title=None,
        value_format="General",
        stacked=False,
        categories=("Unknown", "Autonomous Entity", "Ministry of ICT", "Other"),
        series=(ChartSeries(name="Economies", values=(15.0, 14.0, 93.0, 16.0), kind="doughnut"),),
    )
    rendered = render_chart(data)
    assert rendered is not None
    _render_ok(_mermaid_fence(rendered))


def test_xychart_bar_line_combo_renders() -> None:
    data = ChartData(
        chart_type="combo",
        title=None,
        value_axis_title="Average GTMI score",
        value_format="#,##0.000",
        stacked=False,
        categories=("A", "B", "C", "D"),
        series=(
            ChartSeries(name="Avg GTMI", values=(0.86, 0.61, 0.37, 0.14), kind="column"),
            ChartSeries(name="Reg Avg", values=(0.59, 0.59, 0.59, 0.59), kind="line"),
        ),
    )
    rendered = render_chart(data)
    assert rendered is not None
    _render_ok(_mermaid_fence(rendered))


def test_radar_multi_series_renders() -> None:
    data = ChartData(
        chart_type="radar",
        title="GovTech Maturity Index Components",
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=("CGSI", "PSDI", "DCEI", "GTEI"),
        series=(
            ChartSeries(name="Regional Avg", values=(0.49, 0.51, 0.25, 0.46), kind="radar"),
            ChartSeries(name="Global Avg", values=(0.62, 0.66, 0.47, 0.61), kind="radar"),
        ),
    )
    rendered = render_chart(data)
    assert rendered is not None
    _render_ok(_mermaid_fence(rendered))


def test_pie_title_with_special_characters_still_renders() -> None:
    """Sanitization (``_sanitize_label``) strips ``[]{}()`` and replaces
    double quotes with single quotes BEFORE the text reaches mermaid — check
    that after our cleanup the real mermaid.js still accepts the result."""
    data = ChartData(
        chart_type="pie",
        title='Value (A) [1] "quoted"',
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=("X", "Y"),
        series=(ChartSeries(name="S", values=(3.0, 4.0), kind="pie"),),
    )
    rendered = render_chart(data)
    assert rendered is not None
    _render_ok(_mermaid_fence(rendered))


# --- Hypothesis: narrow but real render check (not just our own heuristics) ---
# max_examples is small (unlike the purely-heuristic property test in
# test_chart_render.py, 200 examples) — every mermaidx.render() call costs
# real time (tens of ms on a warmed-up engine), full scale is unnecessary
# for this particular check (it catches a class of defect, not specific
# values).

# no []{}()"' — already covered by test_pie_title_with_special_characters_still_renders
_LABEL_ALPHABET = "AaBbCc 0123456789"
_labels = st.text(alphabet=_LABEL_ALPHABET, min_size=1, max_size=12)
_chart_types = st.sampled_from(["column", "bar", "line", "area", "pie", "doughnut", "radar"])


@st.composite
def _renderable_chart_data(draw: st.DrawFn) -> ChartData:
    chart_type = draw(_chart_types)
    n_cats = draw(st.integers(min_value=2, max_value=5))
    categories = tuple(draw(_labels) for _ in range(n_cats))
    values = tuple(
        draw(st.floats(min_value=0.1, max_value=1000, allow_nan=False, allow_infinity=False))
        for _ in range(n_cats)
    )
    n_series = (
        1 if chart_type in ("pie", "doughnut") else draw(st.integers(min_value=1, max_value=3))
    )
    series = tuple(
        ChartSeries(name=draw(_labels), values=values, kind=chart_type) for _ in range(n_series)
    )
    return ChartData(
        chart_type=chart_type,
        title=draw(st.one_of(st.none(), _labels)),
        value_axis_title=draw(st.one_of(st.none(), _labels)),
        value_format=None,
        stacked=False,
        categories=categories,
        series=series,
    )


@given(data=_renderable_chart_data())
@settings(max_examples=30, deadline=None)
def test_render_chart_mermaid_output_accepted_by_real_mermaid_js(data: ChartData) -> None:
    rendered = render_chart(data)
    if rendered is None or "```mermaid" not in rendered:
        return
    try:
        _render_ok(_mermaid_fence(rendered))
    except Exception as exc:  # noqa: BLE001 — any real-render failure is the finding
        pytest.fail(f"mermaidx refused to render the generated mermaid: {exc}\n\n{rendered}")
