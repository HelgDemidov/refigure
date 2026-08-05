"""Characterization tests (spec chart-data-extraction, hardening §2 —
"characterization tests FIRST, before any deletion"): a docx document with
BOTH kinds of cut-out objects — a composite group (kind="group", still left
to VLM) and a native c:chart (kind="chart", becomes data-driven) —
simultaneously. The existing tests (``test_docx_groups.py``) cover each
kind SEPARATELY; this file is a regression guard that switching chart-kind
resolution to data-driven (later commits) does NOT affect the group path
(detect/cut-out/sentinel/resolution), which shares code with it
(``docx_groups._iter_objects``/``extract_and_strip_groups``).

The ``inject_group_markers`` assembly for kind="chart" in this file pins the
CURRENT (pre-refactor) behavior — a marker, not a data-driven render; this
ONE assertion is meant to change once chart-kind resolution becomes
data-driven (the rest — the group path and detect/cut-out — must stay green
unchanged throughout the whole refactor)."""

from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from pathlib import Path

from refigure.docx.groups import (
    DocxGroup,
    all_media_ids,
    extract_and_strip_groups,
    extract_group_docx,
    inject_group_markers,
)
from tests.support import build_docx_with_shape_group_and_inline_chart


def test_group_and_chart_coexist_both_detected_with_distinct_kinds(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    img = b"x" * 100
    raw.write_bytes(
        build_docx_with_shape_group_and_inline_chart(
            ["Group caption"], {"a.png": img}, ["Chart Title"]
        )
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    assert len(groups) == 2
    kinds = {g.kind for g in groups}
    assert kinds == {"group", "chart"}
    group = next(g for g in groups if g.kind == "group")
    chart = next(g for g in groups if g.kind == "chart")
    assert group.media_ids == frozenset({hashlib.sha256(img).hexdigest()[:12]})
    assert group.captions == ("Group caption",)
    assert chart.media_ids == frozenset()
    assert chart.captions == ("Chart Title",)


def test_group_and_chart_coexist_both_stripped_to_sentinels(tmp_path: Path) -> None:
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group_and_inline_chart(["Cap"], {"a.png": b"y" * 50}, ["Title"])
    )
    rewritten, groups = extract_and_strip_groups(raw)
    with zipfile.ZipFile(BytesIO(rewritten)) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "mc:AlternateContent" not in doc_xml
    assert "c:chart" not in doc_xml
    for group in groups:
        assert group.id12 in doc_xml


def test_group_and_chart_coexist_chart_part_survives_in_rewritten_zip(tmp_path: Path) -> None:
    """The sentinel replaces ONLY the anchor in document.xml — the chart part
    (``word/charts/chart1.xml``) is left untouched in the archive (needed by
    resolution — currently the VLM render via ``extract_group_docx``, later
    the data-driven parser ``chart_data.parse_chart``)."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_shape_group_and_inline_chart(["Cap"], {}, ["Title"]))
    rewritten, _groups = extract_and_strip_groups(raw)
    with zipfile.ZipFile(BytesIO(rewritten)) as z:
        assert "word/charts/chart1.xml" in z.namelist()


def test_group_and_chart_coexist_injection_gives_each_its_own_marker_kind(tmp_path: Path) -> None:
    """Post-refactor (chart-data-extraction §4.2, ``inject_group_markers``
    now resolves kind="chart" data-driven): here BOTH kinds still produce a
    marker, not a data-driven output — not because the resolution didn't
    change (it did), but because ``_docx_chart_part`` (the test builder)
    carries ONLY a ``c:title``, with no ``c:ser``/numCache at all — the
    extraction is honestly empty, ``render_chart`` returns ``None``, and the
    caption fallback matches the pre-refactor text verbatim. The data-driven
    path with a real numCache is checked separately
    (``test_docx_groups.py``/``test_converters.py``)."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(build_docx_with_shape_group_and_inline_chart(["Group cap"], {}, ["Chart cap"]))
    rewritten, groups = extract_and_strip_groups(raw)
    with zipfile.ZipFile(BytesIO(rewritten)) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    # The sentinels don't flow through mammoth in this test (there's no real
    # text stream) — we inject the marker directly into raw sentinel
    # strings, the way converters._convert_docx does after markdownify.
    group = next(g for g in groups if g.kind == "group")
    chart = next(g for g in groups if g.kind == "chart")
    text = f"before {doc_xml.count('DOCXGROUPSENTINEL')} " + "".join(
        f"DOCXGROUPSENTINEL{g.id12}" for g in groups
    )
    result, _rendered_count = inject_group_markers(text, groups)
    assert f"> [Figure, docx group {group.id12} — composite content not analyzed]" in result
    assert "> captions: Group cap" in result
    assert f"> [Figure, docx chart {chart.id12} — chart content not analyzed]" in result
    assert "> captions: Chart cap" in result


def test_group_path_render_extraction_unaffected_by_coexisting_chart(tmp_path: Path) -> None:
    """``extract_group_docx`` for kind="group" (feeds
    ``_render_docx_group`` -> soffice -> VLM, spec §2 "group path
    UNCHANGED") keeps finding and isolating EXACTLY the group, not picking
    up the neighboring chart, when both are present in the same document."""
    raw = tmp_path / "raw.docx"
    raw.write_bytes(
        build_docx_with_shape_group_and_inline_chart(["Cap"], {"a.png": b"z" * 60}, ["Title"])
    )
    _rewritten, groups = extract_and_strip_groups(raw)
    group = next(g for g in groups if g.kind == "group")
    mini = extract_group_docx(raw, group.id12)
    assert mini is not None
    with zipfile.ZipFile(BytesIO(mini)) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert "wpg:wgp" in doc_xml
    assert "c:chart" not in doc_xml


def test_all_media_ids_includes_both_kinds_media() -> None:
    g1 = DocxGroup(id12="a" * 12, media_ids=frozenset({"111111111111"}), captions=(), kind="group")
    g2 = DocxGroup(id12="b" * 12, media_ids=frozenset(), captions=(), kind="chart")
    assert all_media_ids([g1, g2]) == frozenset({"111111111111"})
