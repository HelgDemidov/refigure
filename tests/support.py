"""Synthetic docx fixture builders: hand-rolled zip + OOXML construction (no
external deps — no ``python-docx``, which isn't a dependency anywhere in this
project), used by the ported docx-groups / chart-group-coexistence test
suites.

Ported from G2AI_ME's ``pipeline/scripts/tests/support.py`` (stage 5),
pared down to only the builders/constants actually used by
``tests/unit/convert/test_docx_groups.py`` and
``tests/unit/convert/test_docx_chart_group_coexistence.py``. Dropped:
``build_pdf`` (PDF is out of scope for this project entirely), ``valid_record``
/``write_doc`` (corpus-pipeline record helpers, unrelated to docx/xlsx
conversion), and the image-only builders ``build_docx_with_inline_image``/
``build_docx_with_choice_only_images``/``build_docx_with_group_and_standalone_image``
(no ported test imports them).
"""

from __future__ import annotations

import io
import zipfile

_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def build_minimal_docx(paragraphs: list[str], *, media: dict[str, bytes] | None = None) -> bytes:
    """Minimal valid OOXML: exactly three zip members
    (``[Content_Types].xml``/``_rels/.rels``/``word/document.xml``) — none of
    the ``styles.xml``/``fontTable.xml``/``docProps`` etc. that Word writes
    but markitdown/mammoth don't require for reading (verified empirically: a
    minimal 3-member docx parses without error). No binary checked into git —
    the fixture is built fresh in each test.

    ``media`` — arbitrary ``{name: bytes}`` under ``word/media/`` — the
    marker code lists this folder DIRECTLY, without cross-checking
    relationships, so the synthetic bytes don't need to decode as a real
    image."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
        for name, data in (media or {}).items():
            z.writestr(f"word/media/{name}", data)
    return buf.getvalue()


_DOCX_CONTENT_TYPES_IMG = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

_OOXML_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_OOXML_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_OOXML_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_OOXML_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_OOXML_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_OOXML_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _docx_para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _docx_zip(body: str, images: dict[str, bytes]) -> bytes:
    """Assemble a docx where every file in ``images`` is WIRED UP via a
    relationship rId100+i (unlike ``build_minimal_docx(media=...)``, whose
    files are orphans by construction)."""
    rels_items = "".join(
        f'<Relationship Id="rId{100 + i}" Type="{_OOXML_R}/image" Target="media/{name}"/>'
        for i, name in enumerate(images)
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels_items}</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_OOXML_W}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES_IMG)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        for name, data in images.items():
            z.writestr(f"word/media/{name}", data)
    return buf.getvalue()


_OOXML_WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"


def _docx_group_ac(captions: list[str], n_images: int, *, rid_offset: int = 100) -> str:
    """mc:AlternateContent whose mc:Choice contains wpg:wgp (the docx_groups
    detector looks at EXACTLY this) — with free-floating text nodes
    (captions) and pic elements with r:embed=rId{rid_offset+i} (media_ids)
    inside; mc:Fallback is empty (the detector doesn't look there). Doesn't
    aim for fidelity to Word's real wpg markup — just the minimum
    ``extract_and_strip_groups`` needs."""
    pics = "".join(
        f'<pic:pic xmlns:pic="{_OOXML_PIC}"><pic:blipFill>'
        f'<a:blip r:embed="rId{rid_offset + i}" xmlns:r="{_OOXML_R}"/></pic:blipFill></pic:pic>'
        for i in range(n_images)
    )
    caption_nodes = "".join(f"<a:t>{c}</a:t>" for c in captions)
    return (
        f'<mc:AlternateContent xmlns:mc="{_OOXML_MC}"><mc:Choice Requires="wpg">'
        f'<w:drawing xmlns:wp="{_OOXML_WP}"><wp:inline><wp:docPr id="1" name="Group"/>'
        f'<a:graphic xmlns:a="{_OOXML_A}"><a:graphicData uri="{_OOXML_WPG}">'
        f'<wpg:wgp xmlns:wpg="{_OOXML_WPG}">{caption_nodes}{pics}</wpg:wgp>'
        f"</a:graphicData></a:graphic></wp:inline></w:drawing>"
        f"</mc:Choice><mc:Fallback/></mc:AlternateContent>"
    )


def build_docx_with_shape_group(
    before: list[str], captions: list[str], images: dict[str, bytes], after: list[str]
) -> bytes:
    """docx with ONE composite group — see ``_docx_group_ac``."""
    group_ac = _docx_group_ac(captions, len(images))
    body = "".join(_docx_para(p) for p in before)
    body += f"<w:p><w:r>{group_ac}</w:r></w:p>"
    body += "".join(_docx_para(p) for p in after)
    return _docx_zip(body, images)


_OOXML_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"


def _docx_chart_drawing(rid: str) -> str:
    """Bare w:drawing with a c:chart anchor (kind="chart" in docx_groups): a
    native Word chart WITHOUT AlternateContent/Fallback — the class "mammoth
    silently drops"."""
    return (
        f'<w:drawing xmlns:wp="{_OOXML_WP}"><wp:inline>'
        f'<wp:docPr id="2" name="Chart"/>'
        f'<a:graphic xmlns:a="{_OOXML_A}"><a:graphicData uri="{_OOXML_C}">'
        f'<c:chart xmlns:c="{_OOXML_C}" r:id="{rid}" xmlns:r="{_OOXML_R}"/>'
        f"</a:graphicData></a:graphic></wp:inline></w:drawing>"
    )


def _docx_chart_part(title_texts: list[str]) -> str:
    """Minimal chart part: c:title with rich text (source of the marker's
    captions) — plus an empty plotArea for structural plausibility."""
    runs = "".join(f'<a:r xmlns:a="{_OOXML_A}"><a:t>{t}</a:t></a:r>' for t in title_texts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{_OOXML_C}"><c:chart>'
        f'<c:title><c:tx><c:rich><a:p xmlns:a="{_OOXML_A}">{runs}</a:p></c:rich></c:tx></c:title>'
        f"<c:plotArea/></c:chart></c:chartSpace>"
    )


def _docx_chart_zip(before: list[str], after: list[str], chart_part_xml: str) -> bytes:
    """Shared zip assembly for a SINGLE native ``c:chart`` (kind="chart"):
    drawing anchor in the body + an arbitrary chart part + rels/
    [Content_Types] — used by both the minimal (``_docx_chart_part``, title
    only) and data-driven (``_docx_chart_part_with_series``, numCache)
    builders."""
    body = "".join(_docx_para(p) for p in before)
    body += f"<w:p><w:r>{_docx_chart_drawing('rId200')}</w:r></w:p>"
    body += "".join(_docx_para(p) for p in after)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_OOXML_W}"><w:body>{body}</w:body></w:document>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId200" Type="{_OOXML_R}/chart" Target="charts/chart1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/charts/chart1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/charts/chart1.xml", chart_part_xml)
    return buf.getvalue()


def build_docx_with_inline_chart(
    before: list[str], title_texts: list[str], after: list[str]
) -> bytes:
    """docx with ONE native c:chart (kind="chart"): drawing anchor in the
    body + a chart part with a title (NO numCache — captions-only fixture;
    for data-driven resolution see ``build_docx_with_inline_chart_data``) +
    rels/[Content_Types]."""
    return _docx_chart_zip(before, after, _docx_chart_part(title_texts))


def _docx_chart_part_with_series(
    title: str, categories: list[str], values: list[str], value_format: str
) -> str:
    """Chart part with a REAL ``c:numCache``/``c:strCache`` — the same
    DrawingML ``c:chart`` schema as xlsx (``xl/charts/*.xml`` vs
    ``word/charts/*.xml``); a single bar-series chart, enough for an
    end-to-end check of data-driven chart-kind resolution."""
    cat_pts = "".join(f'<c:pt idx="{i}"><c:v>{c}</c:v></c:pt>' for i, c in enumerate(categories))
    val_pts = "".join(f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>' for i, v in enumerate(values))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<c:chartSpace xmlns:c="{_OOXML_C}" xmlns:a="{_OOXML_A}"><c:chart>'
        f"<c:title><c:tx><c:rich><a:p><a:r><a:t>{title}</a:t></a:r></a:p></c:rich></c:tx></c:title>"
        '<c:plotArea><c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        '<c:ser><c:idx val="0"/><c:order val="0"/>'
        f"<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${len(categories) + 1}</c:f>"
        f'<c:strCache><c:ptCount val="{len(categories)}"/>{cat_pts}</c:strCache></c:strRef></c:cat>'
        f"<c:val><c:numRef><c:f>Sheet1!$B$2:$B${len(values) + 1}</c:f>"
        f"<c:numCache><c:formatCode>{value_format}</c:formatCode>"
        f'<c:ptCount val="{len(values)}"/>{val_pts}</c:numCache></c:numRef></c:val>'
        "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
    )


def build_docx_with_inline_chart_data(
    before: list[str],
    after: list[str],
    *,
    title: str = "My Chart",
    categories: list[str] | None = None,
    values: list[str] | None = None,
    value_format: str = "0.0%",
) -> bytes:
    """Like ``build_docx_with_inline_chart``, but the chart part carries a
    REAL numCache — for data-driven resolution tests, not just the caption
    fallback."""
    cats, vals = categories or ["A", "B"], values or ["1", "2"]
    return _docx_chart_zip(
        before, after, _docx_chart_part_with_series(title, cats, vals, value_format)
    )


def build_docx_with_shape_group_and_inline_chart(
    group_captions: list[str], group_images: dict[str, bytes], chart_title_texts: list[str]
) -> bytes:
    """Composite: one composite group (kind="group") + one native c:chart
    (kind="chart") in a SINGLE document — a characterization fixture:
    detection/extraction/sentinel for both kinds share code
    (``docx_groups._iter_objects``), while chart-kind RESOLUTION is
    data-driven — this builder gives a regression guard that a change to
    chart resolution does NOT affect the group path (and vice versa)."""
    group_ac = _docx_group_ac(group_captions, len(group_images))
    body = f"<w:p><w:r>{group_ac}</w:r></w:p>"
    body += f"<w:p><w:r>{_docx_chart_drawing('rId200')}</w:r></w:p>"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_OOXML_W}"><w:body>{body}</w:body></w:document>'
    )
    image_rels = "".join(
        f'<Relationship Id="rId{100 + i}" Type="{_OOXML_R}/image" Target="media/{name}"/>'
        for i, name in enumerate(group_images)
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{image_rels}"
        f'<Relationship Id="rId200" Type="{_OOXML_R}/chart" '
        'Target="charts/chart1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/charts/chart1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/charts/chart1.xml", _docx_chart_part(chart_title_texts))
        for name, data in group_images.items():
            z.writestr(f"word/media/{name}", data)
    return buf.getvalue()
