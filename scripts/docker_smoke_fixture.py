"""Writes a minimal hand-rolled .docx to a given path — for ci.yml's
docker-build job (real-conversion smoke test against the built image, not
just --version checks). Building one needs no mammoth (only reading it
does), same technique as
tests/extras/test_extras_isolation.py's _build_minimal_docx — kept as a
standalone script rather than inlined in the workflow YAML, since a
multi-line Python heredoc inside a `run:` block is fragile to edit/review.

Usage: python3 scripts/docker_smoke_fixture.py <output-path> <text>
"""

from __future__ import annotations

import sys
import zipfile

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


def build(path: str, text: str) -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{w}"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
