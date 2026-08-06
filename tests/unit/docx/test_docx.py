"""Synthetic smoke tests for refigure.docx.convert().

Fixtures are built by hand (zip + raw OOXML XML), same technique as
the source pipeline's tests/support.py::build_minimal_docx — not python-docx, which
isn't a dependency anywhere in this project. These are not the real
corpus-fixture behavioral tests (stage 5, gated on fixture licensing,
stage 3) — just enough to confirm the new wrapper (stage 2) actually works
end to end.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from refigure import CorruptArchiveError, UnsupportedFormatError
from refigure.api import Config
from refigure.core import chart_render
from refigure.docx import convert
from tests.support import build_docx_with_inline_chart_data, build_docx_with_shape_group

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def build_minimal_docx(paragraphs: list[str]) -> bytes:
    paras = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paras}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def test_convert_returns_markdown_for_simple_docx() -> None:
    result = convert(build_minimal_docx(["Hello world", "Second paragraph"]))
    assert "Hello world" in result.markdown
    assert "Second paragraph" in result.markdown
    assert result.warnings == []
    assert (result.charts_found, result.charts_rendered, result.groups_found) == (0, 0, 0)
    assert result.vlm_used is False


def test_convert_accepts_path_bytes_and_file_like_identically(tmp_path) -> None:
    data = build_minimal_docx(["Same content"])
    path = tmp_path / "doc.docx"
    path.write_bytes(data)

    from_path = convert(path)
    from_bytes = convert(data)
    from_stream = convert(io.BytesIO(data))

    assert from_path.markdown == from_bytes.markdown == from_stream.markdown


def test_empty_document_is_a_warning_not_an_exception() -> None:
    result = convert(build_minimal_docx([]))
    assert result.markdown.strip() == ""
    assert "no extractable content" in result.warnings


def test_valid_zip_but_not_docx_raises_unsupported_format() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(UnsupportedFormatError):
        convert(buf.getvalue())


def test_non_zip_raises_corrupt_archive() -> None:
    with pytest.raises(CorruptArchiveError):
        convert(b"not a zip at all")


def test_convert_warns_when_mermaidx_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same technique as test_chart_render_missing_mermaidx.py: monkeypatch
    # the module-level optional import rather than actually uninstalling
    # mermaidx — a real numCache-bearing chart still renders (table-only,
    # render_chart's own mermaidx handling degrades internally), only the
    # warning text is new here.
    monkeypatch.setattr(chart_render, "mermaidx", None)
    data = build_docx_with_inline_chart_data(["Before."], ["After."])

    result = convert(data)

    assert result.charts_found == 1
    assert any(w.startswith("mermaidx not installed") for w in result.warnings)


def test_convert_use_vlm_true_wires_through_to_enhance_docx_markdown() -> None:
    # Nothing else exercises Config(use_vlm=True) THROUGH docx.convert()
    # itself — tests/unit/vlm/test_vlm.py tests enhance_docx_markdown()
    # directly, in isolation, never through convert(). A cache HIT (same
    # offline pattern test_vlm.py uses throughout) needs neither soffice
    # nor a real VlmClient — id12 is deterministic (build_docx_with_shape_
    # group + extract_and_strip_groups, same fixture/technique
    # test_docx_groups.py already uses), so the cache can be pre-populated
    # before convert() ever runs.
    from refigure.docx_groups import extract_and_strip_groups
    from refigure.vlm.cache import InMemoryCacheBackend

    data = build_docx_with_shape_group(["Before."], ["Cap"], {}, ["After."])
    _rewritten, groups = extract_and_strip_groups(data)
    cache = InMemoryCacheBackend()
    cache.set(groups[0].id12, {"model": "test-model", "markdown": "A chart, described via VLM."})
    config = Config(use_vlm=True, vlm_cache=cache)

    result = convert(data, config=config)

    assert result.vlm_used is True
    assert "A chart, described via VLM." in result.markdown
