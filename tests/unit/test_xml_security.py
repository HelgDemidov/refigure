"""XML parsing security regression tests (July 2026 threat model: OWASP
XXE guidance, defusedxml's own threat model, CVE-2026-32630-class OOXML
zip/XML resource-exhaustion).

These lock in behavior that's currently safe by default (lxml's bundled
libxml2 enforces an entity-amplification ceiling and a nesting-depth cap
out of the box) — proven live before writing these tests, not assumed from
documentation. The point of the tests is to catch a *regression*: a future
lxml/Python version, or an accidental `XMLParser(resolve_entities=True)`/
`huge_tree=True` somewhere, silently reopening one of these.

Also covers the one real gap this research found: refigure/docx.py used to
parse untrusted .rels content with stdlib xml.etree.ElementTree, which has
no nesting-depth protection at all (fixed to use lxml — see that commit).
"""

from __future__ import annotations

import io
import time
import zipfile

from lxml import etree

from refigure.docx import _docx_referenced_media_ids, convert

# 6 levels of 20x expansion: a 900-byte payload that would naively expand to
# 3 * 20**6 = 192,000,000 bytes if entities were resolved unbounded. Bounded
# deliberately — this must fail FAST, not actually allocate 192MB in CI.
_BILLION_LAUGHS_LEVELS = 6
_BILLION_LAUGHS_FACTOR = 20


def _billion_laughs_payload() -> bytes:
    entities = '<!ENTITY lol0 "lol">'
    for i in range(1, _BILLION_LAUGHS_LEVELS + 1):
        refs = "".join(f"&lol{i - 1};" for _ in range(_BILLION_LAUGHS_FACTOR))
        entities += f'<!ENTITY lol{i} "{refs}">'
    xml = (
        f'<?xml version="1.0"?><!DOCTYPE lolz [{entities}]>'
        f"<lolz>&lol{_BILLION_LAUGHS_LEVELS};</lolz>"
    )
    return xml.encode()


def test_lxml_rejects_billion_laughs_entity_expansion() -> None:
    try:
        etree.fromstring(_billion_laughs_payload())
        raise AssertionError("lxml parsed a billion-laughs payload instead of rejecting it")
    except etree.XMLSyntaxError:
        pass  # expected: libxml2's amplification ceiling rejects this


def test_lxml_rejects_xxe_external_entity(tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret-marker-content")
    payload = f"""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file://{secret}"> ]>
<foo>&xxe;</foo>""".encode()
    try:
        root = etree.fromstring(payload)
        assert root.text is None or "top-secret" not in root.text, (
            "XXE succeeded — local file content leaked into the parsed tree"
        )
    except etree.XMLSyntaxError:
        pass  # expected: entity is undefined, lxml doesn't resolve external SYSTEM entities


def test_lxml_rejects_excessive_nesting_depth() -> None:
    depth = 5000  # well over libxml2's default depth cap (256)
    payload = b"<a>" * depth + b"x" + b"</a>" * depth
    try:
        etree.fromstring(payload)
        raise AssertionError("lxml parsed excessively deep XML instead of rejecting it")
    except etree.XMLSyntaxError:
        pass


def test_docx_referenced_media_ids_rejects_deep_rels_fast() -> None:
    """Regression test for the fix: this exact payload shape previously cost
    547MB RSS / 2.15s via stdlib ElementTree at 2M levels; at a much smaller
    depth it must now be rejected near-instantly via lxml."""
    depth = 10_000
    rels_payload = b"<a>" * depth + b"x" + b"</a>" * depth
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/_rels/document.xml.rels", rels_payload)
        z.writestr("word/document.xml", "<doc/>")

    t0 = time.time()
    result = _docx_referenced_media_ids(buf.getvalue())
    elapsed = time.time() - t0

    assert result == frozenset()  # malformed .rels is skipped, not crashed on
    assert elapsed < 2.0, f"expected near-instant rejection, took {elapsed:.2f}s"


def test_docx_convert_survives_malicious_embedded_rels() -> None:
    """End-to-end: a malicious .rels doesn't have to be the main
    document.xml.rels — _docx_referenced_media_ids scans every word/*.xml
    part's own .rels. Full convert() must still complete fast, not just the
    isolated helper."""
    depth = 10_000
    malicious_rels = b"<a>" * depth + b"x" + b"</a>" * depth
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        # Extra part with nothing to do with the real document structure —
        # _docx_referenced_media_ids scans it purely because it matches
        # word/*.xml, independent of whether anything actually links to it.
        z.writestr("word/sneaky.xml", "<sneaky/>")
        z.writestr("word/_rels/sneaky.xml.rels", malicious_rels)

    t0 = time.time()
    result = convert(buf.getvalue())
    elapsed = time.time() - t0

    assert "Hello" in result.markdown
    assert elapsed < 3.0, f"expected fast completion, took {elapsed:.2f}s"
