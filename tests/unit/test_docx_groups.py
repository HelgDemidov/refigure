"""Tests for docx_groups.py (spec convert-docx §2-ter): composite-group
detection (mc:AlternateContent/wpg:wgp), sentinel replacement, media_ids/
captions extraction, post-injection marker. No network, no soffice — pure
XML in-memory."""

from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree

from refigure.docx_groups import (
    SENTINEL_PREFIX,
    DocxGroup,
    _chart_captions,
    _chart_root,
    all_media_ids,
    extract_and_strip_groups,
    extract_group_docx,
    inject_group_markers,
)
from tests.support import (
    build_docx_with_inline_chart,
    build_docx_with_inline_chart_data,
    build_docx_with_shape_group,
    build_minimal_docx,
)


def test_no_groups_returns_original_bytes_unchanged(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    orig = build_minimal_docx(["Just plain text, no groups."])
    raw.write_bytes(orig)
    rewritten, groups = extract_and_strip_groups(raw)
    assert groups == []
    assert rewritten == orig


def test_detects_single_group_with_media_and_captions(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    img = b"x" * 100
    raw.write_bytes(
        build_docx_with_shape_group(
            ["Before."], ["Caption A", "Caption B"], {"a.png": img}, ["After."]
        )
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    assert len(groups) == 1
    group = groups[0]
    assert group.media_ids == frozenset({hashlib.sha256(img).hexdigest()[:12]})
    assert group.captions == ("Caption A", "Caption B")


def test_numeric_only_captions_filtered_as_position_junk(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group(
            ["Before."], ["-635", "1231900", "Real caption"], {}, ["After."]
        )
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    assert groups[0].captions == ("Real caption",)


def test_duplicate_captions_deduplicated_in_order(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group(["Before."], ["Repeat", "Other", "Repeat"], {}, ["After."])
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    assert groups[0].captions == ("Repeat", "Other")


def test_group_with_no_images_has_empty_media_ids(tmp_path: Path) -> None:
    """Live case §2-ter.1: EU Data Act flow — all icons are below the
    raster threshold/vector, group_media_ids contains NOTHING, but the group
    itself is still detected and gets a marker (its captions aren't lost)."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group(["Before."], ["Only text, no raster"], {}, ["After."])
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    assert len(groups) == 1
    assert groups[0].media_ids == frozenset()
    assert groups[0].captions == ("Only text, no raster",)


def test_sentinel_replaces_group_in_rewritten_document_xml(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_shape_group(["Before."], ["Cap"], {}, ["After."]))
    rewritten, groups = extract_and_strip_groups(raw)
    with zipfile.ZipFile(BytesIO(rewritten)) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "mc:AlternateContent" not in doc_xml
    assert f"{SENTINEL_PREFIX}{groups[0].id12}" in doc_xml


def test_id12_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group(["Before."], ["Cap"], {"a.png": b"y" * 50}, ["After."])
    )
    _r1, groups1 = extract_and_strip_groups(raw)
    _r2, groups2 = extract_and_strip_groups(raw)
    assert groups1[0].id12 == groups2[0].id12


def test_multiple_groups_get_distinct_ids_and_own_media(tmp_path: Path) -> None:
    """The builder targets one group per document — we check across TWO
    separate single-group documents that id12 really depends on content
    (different images/captions -> different ids, different media_ids), which
    is what's needed for correct behavior on a document with multiple groups
    (live case — a report excerpt with 3 groups)."""
    img_a, img_b = b"a" * 100, b"b" * 100
    raw_a = tmp_path / "a.docx"
    raw_a.write_bytes(build_docx_with_shape_group([], ["Cap A"], {"a.png": img_a}, []))
    raw_b = tmp_path / "b.docx"
    raw_b.write_bytes(build_docx_with_shape_group([], ["Cap B"], {"b.png": img_b}, []))
    _rewritten_a, groups_a = extract_and_strip_groups(raw_a)
    _rewritten_b, groups_b = extract_and_strip_groups(raw_b)
    assert groups_a[0].id12 != groups_b[0].id12
    assert groups_a[0].media_ids != groups_b[0].media_ids


def test_inject_group_markers_replaces_bare_sentinel() -> None:
    from refigure.docx_groups import DocxGroup

    group = DocxGroup(id12="abc123def456", media_ids=frozenset(), captions=("Foo", "Bar"))
    text = f"Before.\n\n{SENTINEL_PREFIX}abc123def456\n\nAfter."
    result, rendered_count = inject_group_markers(text, [group])
    assert "> [Figure, docx group abc123def456 — composite content not analyzed]" in result
    assert "> captions: Foo; Bar" in result
    assert SENTINEL_PREFIX not in result
    assert result.index("Before.") < result.index("abc123def456") < result.index("After.")
    assert rendered_count == 0


def test_inject_group_markers_consumes_bold_wrapping() -> None:
    """markdownify sometimes wraps the sentinel in ** (inherited from the
    rPr of the run the sentinel replaced — live case: a bold block in the
    fixture) — the regex must absorb the wrapping whole, leaving no dangling
    asterisks."""
    from refigure.docx_groups import DocxGroup

    group = DocxGroup(id12="abc123def456", media_ids=frozenset(), captions=())
    text = f"Before. **{SENTINEL_PREFIX}abc123def456** After."
    result, _rendered_count = inject_group_markers(text, [group])
    assert "**" not in result
    assert "> [Figure, docx group abc123def456" in result


def test_inject_group_markers_empty_captions_says_no_text() -> None:
    from refigure.docx_groups import DocxGroup

    group = DocxGroup(id12="abc123def456", media_ids=frozenset(), captions=())
    result, _rendered_count = inject_group_markers(f"{SENTINEL_PREFIX}abc123def456", [group])
    assert "> captions: (no captions)" in result


def test_inject_group_markers_no_groups_is_noop() -> None:
    text = "Nothing to replace here."
    result, rendered_count = inject_group_markers(text, [])
    assert result == text
    assert rendered_count == 0


def test_detects_native_chart_with_title_captions(tmp_path: Path) -> None:
    """A bare c:chart (kind="chart", §2-ter ultimate test): an anchor with no
    Fallback, the title comes from the chart part (c:title), not from
    document.xml."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_inline_chart(["Before."], ["Costs of LTE", "and 5G"], ["After."])
    )
    rewritten, groups = extract_and_strip_groups(raw)
    assert len(groups) == 1
    chart = groups[0]
    assert chart.kind == "chart"
    assert chart.media_ids == frozenset()
    assert chart.captions == ("Costs of LTE", "and 5G")
    with zipfile.ZipFile(BytesIO(rewritten)) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "c:chart" not in doc_xml
    assert f"{SENTINEL_PREFIX}{chart.id12}" in doc_xml


def test_native_chart_without_numcache_has_empty_chart_data(tmp_path: Path) -> None:
    """``build_docx_with_inline_chart`` is a captions-only fixture (only
    ``c:title``, no ``c:ser`` at all): ``chart_data`` is NOT None (the chart
    part is reachable), but the extraction is honestly empty —
    ``chart_type="other"``, zero series (see ``chart_data.parse_chart``)."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_inline_chart(["Before."], ["Title"], ["After."]))
    _rewritten, groups = extract_and_strip_groups(raw)
    chart = groups[0]
    assert chart.chart_data is not None
    assert chart.chart_data.series == ()


def test_native_chart_with_numcache_gets_parsed_chart_data(tmp_path: Path) -> None:
    """Live fact (chart-data-extraction spec §4.2): a chart part with a real
    numCache produces non-empty ``ChartData`` already at the cut-out stage —
    resolution (``inject_group_markers``) is no longer needed to tell
    whether data is present."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_inline_chart_data(
            ["Before."],
            ["After."],
            title="Regional Scores",
            categories=["A", "B"],
            values=["0.42", "0.87"],
            value_format="0.0%",
        )
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    chart = groups[0]
    assert chart.kind == "chart"
    data = chart.chart_data
    assert data is not None
    assert data.chart_type == "column"
    assert data.title == "Regional Scores"
    assert data.categories == ("A", "B")
    assert len(data.series) == 1
    assert data.series[0].values == (0.42, 0.87)


def test_inject_group_markers_chart_kind_renders_data_driven_table_and_mermaid(
    tmp_path: Path,
) -> None:
    """``inject_group_markers`` (chart-data-extraction §4.2): a chart-kind
    with a non-empty extraction -> ``render_chart`` output IN-PLACE of the
    sentinel, NOT the honest marker — the position in the stream (Before./
    After.) is preserved exactly (§4.4: docx provenance IS the position
    itself, no separate line needed)."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_inline_chart_data(
            ["Before."],
            ["After."],
            title="Regional Scores",
            categories=["A", "B"],
            values=["0.42", "0.87"],
            value_format="0.0%",
        )
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    chart = groups[0]
    text = f"Before.\n\n{SENTINEL_PREFIX}{chart.id12}\n\nAfter."
    result, rendered_count = inject_group_markers(text, groups)
    assert "chart content not analyzed" not in result
    assert "```mermaid\nxychart-beta" in result
    assert "| Category | Series 1 |" in result
    assert "| A | 42.0% |" in result
    assert result.index("Before.") < result.index("Regional Scores") < result.index("After.")
    assert rendered_count == 1


def test_inject_group_markers_chart_kind_empty_extraction_falls_back_to_marker() -> None:
    """A chart-kind with an empty extraction (no numCache) -> the SAME
    honest marker as before data-driven resolution existed (caption
    fallback, not a crash/empty output)."""
    from refigure.core.chart_data import ChartData
    from refigure.docx_groups import DocxGroup

    empty = ChartData(
        chart_type="other",
        title=None,
        value_axis_title=None,
        value_format=None,
        stacked=False,
        categories=(),
        series=(),
    )
    chart = DocxGroup(
        id12="abc123def456",
        media_ids=frozenset(),
        captions=("Title",),
        kind="chart",
        chart_data=empty,
    )
    result, rendered_count = inject_group_markers(f"{SENTINEL_PREFIX}abc123def456", [chart])
    assert "> [Figure, docx chart abc123def456 — chart content not analyzed]" in result
    assert "> captions: Title" in result
    assert rendered_count == 0


def test_chart_inside_alternate_content_not_detected(tmp_path: Path) -> None:
    """A chart wrapped in mc:AlternateContent (chartEx class: Choice carries
    the diagram, Fallback carries a ready-made PNG from Word) is
    DELIBERATELY skipped by the detector — it's picked up by the mammoth
    inline-image path via Fallback instead (see the module docstring). The
    ``build_docx_with_choice_only_images`` builder puts a drawing right
    inside an AC — we augment it with a chart anchor by hand."""
    from tests.support import _OOXML_MC, _docx_chart_drawing, _docx_para, _docx_zip

    body = _docx_para("Before.")
    body += (
        f'<w:p><w:r><mc:AlternateContent xmlns:mc="{_OOXML_MC}">'
        f'<mc:Choice Requires="cx1">{_docx_chart_drawing("rId300")}</mc:Choice>'
        f"<mc:Fallback/></mc:AlternateContent></w:r></w:p>"
    )
    raw = tmp_path / "raw.docx"
    raw.write_bytes(_docx_zip(body, {}))
    rewritten, groups = extract_and_strip_groups(raw)
    assert groups == []
    assert rewritten == raw.read_bytes()


def test_inject_chart_marker_uses_chart_noun() -> None:
    from refigure.docx_groups import DocxGroup

    chart = DocxGroup(id12="abc123def456", media_ids=frozenset(), captions=("Title",), kind="chart")
    result, rendered_count = inject_group_markers(f"{SENTINEL_PREFIX}abc123def456", [chart])
    assert "> [Figure, docx chart abc123def456 — chart content not analyzed]" in result
    assert "> captions: Title" in result
    assert rendered_count == 0


def test_extract_group_docx_finds_chart_and_keeps_chart_part(tmp_path: Path) -> None:
    """A mini-docx for a chart: the body is squeezed down to one block, the
    chart part (and its rels) come along automatically — extract_group_docx
    copies the WHOLE zip."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_inline_chart(["Before."], ["Title"], ["After."]))
    _rewritten, groups = extract_and_strip_groups(raw)
    mini = extract_group_docx(raw, groups[0].id12)
    assert mini is not None
    with zipfile.ZipFile(BytesIO(mini)) as z:
        assert "word/charts/chart1.xml" in z.namelist()
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "c:chart" in doc_xml or "chart" in doc_xml
    assert "Before." not in doc_xml
    assert "After." not in doc_xml


def test_all_media_ids_unions_across_groups() -> None:
    from refigure.docx_groups import DocxGroup

    g1 = DocxGroup(
        id12="a" * 12, media_ids=frozenset({"111111111111", "222222222222"}), captions=()
    )
    g2 = DocxGroup(
        id12="b" * 12, media_ids=frozenset({"222222222222", "333333333333"}), captions=()
    )
    assert all_media_ids([g1, g2]) == frozenset({"111111111111", "222222222222", "333333333333"})


def test_all_media_ids_empty_for_no_groups() -> None:
    assert all_media_ids([]) == frozenset()


# --- defensive/malformed-input branches (coverage-hardening spec,
# docs/testing/coverage-hardening/coverage-hardening-2026-08-06.md §2) ---

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def test_chart_root_returns_none_when_rid_not_in_rel_targets() -> None:
    drawing = etree.fromstring(
        f'<w:drawing xmlns:w="{_W_NS}">'
        f'<c:chart xmlns:c="{_C_NS}" xmlns:r="{_R_NS}" r:id="rIdMissing"/>'
        f"</w:drawing>"
    )
    assert _chart_root(drawing, {}, None, set()) is None  # rId not in an empty rel_targets


def test_chart_root_returns_none_when_resolved_part_missing_from_archive() -> None:
    drawing = etree.fromstring(
        f'<w:drawing xmlns:w="{_W_NS}">'
        f'<c:chart xmlns:c="{_C_NS}" xmlns:r="{_R_NS}" r:id="rId1"/>'
        f"</w:drawing>"
    )
    rel_targets = {"rId1": "charts/chart1.xml"}
    assert _chart_root(drawing, rel_targets, None, set()) is None  # names is empty


def test_chart_captions_returns_empty_tuple_for_none_chart_root() -> None:
    assert _chart_captions(None) == ()


def test_chart_captions_returns_empty_tuple_when_chart_has_no_title_element() -> None:
    chart_root = etree.fromstring(f'<c:chartSpace xmlns:c="{_C_NS}"><c:chart/></c:chartSpace>')
    assert _chart_captions(chart_root) == ()


def _minimal_docx_zip(document_xml: str) -> bytes:
    """A bare 3-member docx around a caller-supplied word/document.xml —
    for malformed/edge-case documents none of tests/support.py's builders
    produce (no w:body, a trailing w:sectPr, ...)."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_extract_and_strip_groups_returns_original_when_body_element_missing(
    tmp_path: Path,
) -> None:
    """A ``w:document`` that parses fine as XML but carries no ``w:body`` at
    all — the malformed-input case a Hypothesis test found during
    stage2-public-api-wrapper (``TypeError`` on ``list(None)`` in
    ``_iter_objects``, see ``extract_and_strip_groups``'s own docstring).
    Same honest pass-through as a missing part entirely."""
    raw = tmp_path / "raw.docx"
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W_NS}"/>'
    )
    orig = _minimal_docx_zip(document)
    raw.write_bytes(orig)

    rewritten, groups = extract_and_strip_groups(raw)

    assert groups == []
    assert rewritten == orig


def test_extract_group_docx_returns_none_when_id_not_found(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_shape_group(["Before."], ["Cap"], {}, ["After."]))
    assert extract_group_docx(raw, "000000000000") is None


def test_extract_group_docx_preserves_trailing_sect_pr_for_page_geometry(tmp_path: Path) -> None:
    """extract_group_docx keeps the original's trailing w:sectPr (page
    geometry, needed for a faithful soffice render) in the rebuilt
    mini-docx — none of tests/support.py's builders include one (a real
    Word document always does), so this constructs the body by hand."""
    from tests.support import _docx_group_ac

    group_ac = _docx_group_ac(["Cap"], 0)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>'
        f"<w:p><w:r>{group_ac}</w:r></w:p>"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        "</w:body></w:document>"
    )
    raw = tmp_path / "raw.docx"
    raw.write_bytes(_minimal_docx_zip(document))

    _rewritten, groups = extract_and_strip_groups(raw)
    mini = extract_group_docx(raw, groups[0].id12)

    assert mini is not None
    with zipfile.ZipFile(BytesIO(mini)) as z:
        mini_doc = z.read("word/document.xml").decode("utf-8")
    assert "sectPr" in mini_doc


def test_inject_group_markers_unrecognized_id_leaves_sentinel_unchanged() -> None:
    # "practically impossible" per the function's own comment (id12 is a
    # sha256) — but the fallback exists in the code, so it gets a test.
    known = DocxGroup(id12="abc123def456", media_ids=frozenset(), captions=("Foo",))
    text = f"Before.\n\n{SENTINEL_PREFIX}999999999999\n\nAfter."

    result, rendered_count = inject_group_markers(text, [known])

    assert result == text
    assert rendered_count == 0
