"""Tests for chart_data.py (spec chart-data-extraction §3): ``parse_chart`` on
container-agnostic chart XML (identical DrawingML schema for xlsx/docx). Pure
in-memory XML, built BY HAND — ``openpyxl.chart`` doesn't work for fixtures:
empirically confirmed (2026-07-22) that ``chart.add_data()`` writes a chart
WITHOUT ``c:numCache``/``c:strCache`` at all (only a ``<c:f>`` reference), while
the v1 parser reads EXCLUSIVELY the cache. The XML shapes below were checked
against real charts from the govtech fixture (strLit/xVal-yVal/a series without
its own ``c:cat`` — all three live findings from that session; the fixture
itself has since been renamed/rebuilt twice, see
``tests/fixtures/local/README.md``)."""

from __future__ import annotations

from lxml import etree

from refigure.core.chart_data import ChartData, ChartSeries, _series_kind, parse_chart

_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _pts(values: list[str]) -> str:
    return "".join(
        f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>' for i, v in enumerate(values) if v is not None
    )


def _sparse_pts(indexed: dict[int, str]) -> str:
    return "".join(f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>' for i, v in indexed.items())


def _str_cat(values: list[str], count: int | None = None) -> str:
    n = count if count is not None else len(values)
    return (
        f"<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${n + 1}</c:f>"
        f'<c:strCache><c:ptCount val="{n}"/>{_pts(values)}</c:strCache></c:strRef></c:cat>'
    )


def _str_lit_cat(values: list[str]) -> str:
    return f'<c:cat><c:strLit><c:ptCount val="{len(values)}"/>{_pts(values)}</c:strLit></c:cat>'


def _num_val(values: list[str], fmt: str | None = None, count: int | None = None) -> str:
    n = count if count is not None else len(values)
    fmt_xml = f"<c:formatCode>{fmt}</c:formatCode>" if fmt else ""
    return (
        f"<c:val><c:numRef><c:f>Sheet1!$B$2:$B${n + 1}</c:f>"
        f'<c:numCache>{fmt_xml}<c:ptCount val="{n}"/>{_pts(values)}</c:numCache></c:numRef></c:val>'
    )


def _sparse_num_val(indexed: dict[int, str], count: int) -> str:
    return (
        f"<c:val><c:numRef><c:f>Sheet1!$B$2:$B${count + 1}</c:f>"
        f'<c:numCache><c:ptCount val="{count}"/>{_sparse_pts(indexed)}</c:numCache>'
        f"</c:numRef></c:val>"
    )


def _num_lit_val(values: list[str]) -> str:
    return f'<c:val><c:numLit><c:ptCount val="{len(values)}"/>{_pts(values)}</c:numLit></c:val>'


def _num_lit_cat(values: list[str]) -> str:
    return f'<c:cat><c:numLit><c:ptCount val="{len(values)}"/>{_pts(values)}</c:numLit></c:cat>'


def _xval(values: list[str]) -> str:
    return (
        f"<c:xVal><c:numRef><c:f>Sheet1!$A$2:$A${len(values) + 1}</c:f>"
        f'<c:numCache><c:ptCount val="{len(values)}"/>{_pts(values)}</c:numCache>'
        f"</c:numRef></c:xVal>"
    )


def _yval(values: list[str]) -> str:
    return (
        f"<c:yVal><c:numRef><c:f>Sheet1!$B$2:$B${len(values) + 1}</c:f>"
        f'<c:numCache><c:ptCount val="{len(values)}"/>{_pts(values)}</c:numCache>'
        f"</c:numRef></c:yVal>"
    )


def _tx(name: str | None) -> str:
    if name is None:
        return ""
    return (
        '<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache><c:ptCount val="1"/>'
        f'<c:pt idx="0"><c:v>{name}</c:v></c:pt></c:strCache></c:strRef></c:tx>'
    )


def _ser(idx: int, name: str | None, body: str) -> str:
    return f'<c:ser><c:idx val="{idx}"/><c:order val="{idx}"/>{_tx(name)}{body}</c:ser>'


def _bar_chart(sers: str, *, bar_dir: str = "col", grouping: str = "clustered") -> str:
    return (
        f'<c:barChart><c:barDir val="{bar_dir}"/><c:grouping val="{grouping}"/>{sers}</c:barChart>'
    )


def _plot_area(*chart_type_xmls: str, val_ax_title: str | None = None) -> str:
    val_ax = (
        f'<c:valAx><c:axId val="1"/><c:title><c:tx><c:rich><a:p><a:r>'
        f"<a:t>{val_ax_title}</a:t></a:r></a:p></c:rich></c:tx></c:title></c:valAx>"
        if val_ax_title
        else '<c:valAx><c:axId val="1"/></c:valAx>'
    )
    return f"<c:plotArea>{''.join(chart_type_xmls)}{val_ax}</c:plotArea>"


def _chart_root(plot_area_xml: str, *, title: str | None = None) -> etree._Element:
    title_xml = (
        f"<c:title><c:tx><c:rich><a:p><a:r><a:t>{title}</a:t></a:r></a:p></c:rich></c:tx></c:title>"
        if title
        else '<c:autoTitleDeleted val="1"/>'
    )
    xml = (
        f'<c:chartSpace xmlns:c="{_C}" xmlns:a="{_A}">'
        f"<c:chart>{title_xml}{plot_area_xml}</c:chart></c:chartSpace>"
    )
    return etree.fromstring(xml.encode())


def test_bar_chart_column_type_categories_and_series() -> None:
    sers = _ser(0, "Series A", _str_cat(["A", "B", "C"]) + _num_val(["1", "2", "3"]))
    root = _chart_root(_plot_area(_bar_chart(sers)), title="My Chart")
    data = parse_chart(root)
    assert data.chart_type == "column"
    assert data.title == "My Chart"
    assert data.categories == ("A", "B", "C")
    assert len(data.series) == 1
    assert data.series[0].name == "Series A"
    assert data.series[0].values == (1.0, 2.0, 3.0)
    assert data.series[0].kind == "column"


def test_bar_chart_bar_direction_gives_bar_type() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers, bar_dir="bar")))
    assert parse_chart(root).chart_type == "bar"


def test_line_chart_type() -> None:
    sers = _ser(0, "S", _str_cat(["A", "B"]) + _num_val(["1", "2"]))
    root = _chart_root(_plot_area(f"<c:lineChart>{sers}</c:lineChart>"))
    data = parse_chart(root)
    assert data.chart_type == "line"
    assert data.series[0].kind == "line"


def test_pie_chart_type() -> None:
    sers = _ser(0, "S", _str_cat(["A", "B"]) + _num_val(["1", "2"]))
    root = _chart_root(_plot_area(f"<c:pieChart>{sers}</c:pieChart>"))
    assert parse_chart(root).chart_type == "pie"


def test_doughnut_chart_type() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(f"<c:doughnutChart>{sers}</c:doughnutChart>"))
    assert parse_chart(root).chart_type == "doughnut"


def test_radar_chart_type() -> None:
    sers = _ser(0, "S", _str_cat(["A", "B"]) + _num_val(["1", "2"]))
    root = _chart_root(_plot_area(f"<c:radarChart>{sers}</c:radarChart>"))
    data = parse_chart(root)
    assert data.chart_type == "radar"
    assert data.series[0].kind == "radar"


def test_area_chart_type() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(f"<c:areaChart>{sers}</c:areaChart>"))
    assert parse_chart(root).chart_type == "area"


def test_scatter_chart_uses_xval_yval_instead_of_cat_val() -> None:
    """Live fact from the govtech fixture: scatterChart carries data via
    ``c:xVal``/``c:yVal``, structurally a separate pair from ``c:cat``/
    ``c:val`` — without an explicit fallback, the chart would silently lose
    the ENTIRE table, not just the mermaid diagram."""
    sers = _ser(0, "Trend", _xval(["1984", "1985", "1986"]) + _yval(["10", "20", "30"]))
    root = _chart_root(_plot_area(f"<c:scatterChart>{sers}</c:scatterChart>"))
    data = parse_chart(root)
    assert data.chart_type == "scatter"
    assert data.categories == ("1984", "1985", "1986")
    assert data.series[0].values == (10.0, 20.0, 30.0)
    assert data.series[0].kind == "scatter"


def test_multiple_plot_area_types_give_combo_and_per_series_kind() -> None:
    bar = _bar_chart(_ser(0, "Bars", _str_cat(["A", "B"]) + _num_val(["1", "2"])))
    line_ser = _ser(1, "Line", _str_cat(["A", "B"]) + _num_val(["3", "4"]))
    line = f"<c:lineChart>{line_ser}</c:lineChart>"
    root = _chart_root(_plot_area(bar, line))
    data = parse_chart(root)
    assert data.chart_type == "combo"
    kinds = {s.name: s.kind for s in data.series}
    assert kinds == {"Bars": "column", "Line": "line"}


def test_unrecognized_plot_area_element_gives_other() -> None:
    root = _chart_root(_plot_area("<c:bubbleChart><c:ser/></c:bubbleChart>"))
    assert parse_chart(root).chart_type == "other"


def test_no_plot_area_gives_other() -> None:
    root = _chart_root("")
    assert parse_chart(root).chart_type == "other"


def test_sparse_pt_idx_gives_none_or_empty_string_gaps() -> None:
    cat_xml = _str_cat(["A"], count=3)  # ptCount=3, but only idx=0 is materialized below
    val_xml = _sparse_num_val({0: "1", 2: "3"}, count=3)
    root = _chart_root(_plot_area(_bar_chart(_ser(0, "S", cat_xml + val_xml))))
    data = parse_chart(root)
    assert data.categories == ("A", "", "")
    assert data.series[0].values == (1.0, None, 3.0)


def test_numcache_error_placeholder_gives_none_not_crash() -> None:
    """Live bug (stage 5 corpus testing, 2026-08-05, root-caused via
    superpowers:systematic-debugging): real charts can cache an Excel
    formula-error placeholder directly inside c:numCache when the source
    formula errored for that data point — confirmed real:
    eia-steo-chart-gallery.xlsx, 19 of 67 chart parts contain a literal
    "#N/A". _materialize_num used to do float(v) unconditionally, raising
    an uncaught ValueError — must degrade to None (the same value already
    used for a missing <c:v>), not crash. Fixed via _safe_float."""
    sers = _ser(0, "S", _str_cat(["A", "B", "C"]) + _num_val(["1", "#N/A", "3"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (1.0, None, 3.0)


def test_malformed_ptcount_val_falls_back_to_idx_inference() -> None:
    """Live bug, same investigation as above: a ptCount/@val that isn't a
    valid integer used to crash _pt_count's unconditional int() — must fall
    back to inferring the count from the highest pt/@idx instead, same as
    when ptCount is absent entirely."""
    xml = (
        '<c:val><c:numRef><c:f>X</c:f><c:numCache><c:ptCount val="not-a-number"/>'
        '<c:pt idx="0"><c:v>1</c:v></c:pt><c:pt idx="1"><c:v>2</c:v></c:pt>'
        "</c:numCache></c:numRef></c:val>"
    )
    sers = _ser(0, "S", _str_cat(["A", "B"]) + xml)
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (1.0, 2.0)


def test_malformed_pt_idx_is_skipped_not_crashed_and_does_not_clobber() -> None:
    """Live bug, same investigation as above: a pt/@idx that isn't a valid
    integer used to crash the unconditional int() in _materialize_num —
    must skip that point rather than crash. Specifically does NOT default
    the malformed idx to 0 (an earlier draft of the fix did, and this exact
    test caught it): that would silently overwrite the real idx=0 point
    below with the malformed point's value instead of just dropping it."""
    xml = (
        '<c:val><c:numRef><c:f>X</c:f><c:numCache><c:ptCount val="2"/>'
        '<c:pt idx="0"><c:v>1</c:v></c:pt><c:pt idx="garbage"><c:v>99</c:v></c:pt>'
        "</c:numCache></c:numRef></c:val>"
    )
    sers = _ser(0, "S", _str_cat(["A", "B"]) + xml)
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (1.0, None)


def test_pt_missing_idx_attribute_entirely_is_skipped_when_inferring_count() -> None:
    """A <c:pt> with NO idx attribute at all (different from
    test_malformed_pt_idx's "garbage" string) — the text-is-None branch of
    _safe_int, only reachable via _pt_count's idx-inference fallback
    (ptCount element absent entirely)."""
    xml = (
        "<c:val><c:numRef><c:f>X</c:f><c:numCache>"
        '<c:pt idx="0"><c:v>1</c:v></c:pt><c:pt><c:v>99</c:v></c:pt>'
        "</c:numCache></c:numRef></c:val>"
    )
    sers = _ser(0, "S", _str_cat(["A"]) + xml)
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (1.0,)


def test_pt_with_no_v_element_gives_none_value_not_crash() -> None:
    """<c:pt idx="0"/> with no <c:v> child at all — different from an
    unparseable value like #N/A (which _pt_value DOES return, as text) —
    _safe_float's text-is-None branch specifically, via _pt_value itself
    returning None."""
    xml = (
        '<c:val><c:numRef><c:f>X</c:f><c:numCache><c:ptCount val="1"/>'
        '<c:pt idx="0"/>'
        "</c:numCache></c:numRef></c:val>"
    )
    sers = _ser(0, "S", _str_cat(["A"]) + xml)
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (None,)


def test_categories_returns_empty_tuple_when_cat_element_has_no_known_source_variant() -> None:
    """c:cat present but empty (none of strRef/strLit/numRef/numLit) — the
    final fallback after all 4 known CT_AxDataSource variants are checked
    and none match."""
    sers = _ser(0, "S", "<c:cat/>" + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).categories == ()


def test_empty_numcache_gives_empty_series() -> None:
    empty_val = "<c:val><c:numRef><c:f>X</c:f><c:numCache/></c:numRef></c:val>"
    sers = _ser(0, "S", _str_cat(["A", "B"]) + empty_val)
    root = _chart_root(_plot_area(_bar_chart(sers)))
    data = parse_chart(root)
    assert data.series[0].values == ()


def test_missing_val_element_gives_empty_series() -> None:
    sers = _ser(0, "S", _str_cat(["A"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == ()


def test_multi_series_chart() -> None:
    s1 = _ser(0, "First", _str_cat(["A", "B"]) + _num_val(["1", "2"]))
    s2 = _ser(1, "Second", _num_val(["3", "4"]))  # no own <c:cat> — shares the first series's
    root = _chart_root(_plot_area(_bar_chart(s1 + s2)))
    data = parse_chart(root)
    assert len(data.series) == 2
    assert [s.name for s in data.series] == ["First", "Second"]
    assert data.categories == ("A", "B")


def test_chart_without_title_gives_none() -> None:
    root = _chart_root(_plot_area(_bar_chart(_ser(0, "S", _str_cat(["A"]) + _num_val(["1"])))))
    assert parse_chart(root).title is None


def test_series_without_own_cat_falls_back_to_first_series_that_has_one() -> None:
    """Live fact from the govtech fixture's chart1.xml: the series that comes
    FIRST in the document doesn't carry a <c:cat> at all (Excel writes
    categories on only one series, not necessarily the first) — categories
    must be found on the SECOND one instead."""
    s1 = _ser(0, "NoCat", _num_val(["1", "2"]))
    s2 = _ser(1, "HasCat", _str_cat(["X", "Y"]) + _num_val(["3", "4"]))
    root = _chart_root(_plot_area(_bar_chart(s1 + s2)))
    data = parse_chart(root)
    assert data.categories == ("X", "Y")


def test_str_lit_categories_without_cell_reference() -> None:
    """Live fact from the govtech chart6.xml: categories are literal
    (c:strLit), with no <c:f> cell reference at all — structurally a separate
    branch from strRef."""
    sers = _ser(0, "S", _str_lit_cat(["A", "B", "C"]) + _num_val(["1", "2", "3"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).categories == ("A", "B", "C")


def test_num_lit_values_without_cell_reference() -> None:
    sers = _ser(0, "S", _str_cat(["A", "B"]) + _num_lit_val(["10", "20"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).series[0].values == (10.0, 20.0)


def test_num_lit_categories_without_cell_reference() -> None:
    """Categories as literal numbers (c:cat>c:numLit), no <c:f> cell
    reference — the numeric-category twin of
    test_str_lit_categories_without_cell_reference. One unparseable point
    (#N/A) also exercises _format_plain_num's None -> "" branch, not just
    the happy path."""
    sers = _ser(0, "S", _num_lit_cat(["2020", "#N/A", "2022"]) + _num_val(["1", "2", "3"]))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).categories == ("2020", "", "2022")


def test_series_name_literal_value_without_cell_reference() -> None:
    """c:tx>c:v (rare, but valid per the schema): the series name is given
    directly, no <c:f> cell reference at all — the literal twin of the
    ordinary strRef>strCache case _tx() always builds."""
    body = f"<c:tx><c:v>Literal Name</c:v></c:tx>{_str_cat(['A'])}{_num_val(['1'])}"
    ser = f'<c:ser><c:idx val="0"/><c:order val="0"/>{body}</c:ser>'
    root = _chart_root(_plot_area(_bar_chart(ser)))
    assert parse_chart(root).series[0].name == "Literal Name"


def test_series_kind_returns_other_when_ser_element_has_no_parent() -> None:
    # Defensive-only: getparent() is None on a genuinely detached element,
    # which the real parse path never produces (a c:ser is never the XML
    # root) — constructed directly to exercise the guard.
    ser_el = etree.fromstring(f'<c:ser xmlns:c="{_C}"/>')
    assert _series_kind(ser_el) == "other"


def test_parse_chart_returns_empty_chart_data_when_no_chart_element() -> None:
    root = etree.fromstring(f'<c:chartSpace xmlns:c="{_C}"/>')
    assert parse_chart(root) == ChartData(
        chart_type="other",
        title=None,
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=(),
        series=(),
    )


def test_value_axis_title_captured() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers), val_ax_title="Average GTMI score"))
    assert parse_chart(root).value_axis_title == "Average GTMI score"


def test_value_format_captured_from_numcache() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["0.589"], fmt="#,##0.000"))
    root = _chart_root(_plot_area(_bar_chart(sers)))
    assert parse_chart(root).value_format == "#,##0.000"


def test_stacked_grouping_detected() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers, grouping="stacked")))
    assert parse_chart(root).stacked is True


def test_percent_stacked_grouping_detected() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers, grouping="percentStacked")))
    assert parse_chart(root).stacked is True


def test_clustered_grouping_is_not_stacked() -> None:
    sers = _ser(0, "S", _str_cat(["A"]) + _num_val(["1"]))
    root = _chart_root(_plot_area(_bar_chart(sers, grouping="clustered")))
    assert parse_chart(root).stacked is False


def test_worked_example_bar_chart_xml_to_chartdata_verbatim() -> None:
    """Golden-target, half 1 (spec §Test coverage — XML -> ChartData): chart
    XML (2 categories x 2 series, percent format, axis title) -> exact
    ``ChartData``, verbatim. Half 2 (ChartData -> exact markdown) —
    ``test_chart_render.py::test_worked_example_chartdata_to_markdown_verbatim``,
    the same ``ChartData`` is built there directly (without XML) —
    separately, so that ``chart_data.py`` and ``chart_render.py`` stay
    independently committable and testable."""
    s1 = _ser(
        0,
        "2024",
        _str_cat(["Montenegro", "Estonia"]) + _num_val(["0.42", "0.87"], fmt="0.0%"),
    )
    s2 = _ser(1, "2025", _num_val(["0.55", "0.91"], fmt="0.0%"))
    root = _chart_root(
        _plot_area(_bar_chart(s1 + s2), val_ax_title="Score"), title="Regional Comparison"
    )
    assert parse_chart(root) == ChartData(
        chart_type="column",
        title="Regional Comparison",
        value_axis_title="Score",
        value_format="0.0%",
        stacked=False,
        categories=("Montenegro", "Estonia"),
        series=(
            ChartSeries(name="2024", values=(0.42, 0.87), kind="column"),
            ChartSeries(name="2025", values=(0.55, 0.91), kind="column"),
        ),
    )


def test_chart_data_and_series_are_frozen_dataclasses() -> None:
    series = ChartSeries(name="S", values=(1.0,), kind="column")
    data = ChartData(
        chart_type="column",
        title=None,
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=("A",),
        series=(series,),
    )
    assert data.series[0].name == "S"
