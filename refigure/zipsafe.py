"""Decompression ceiling for OOXML archives (spec convert-knowledge-seam-hardening §8).

docx/xlsx are zip archives, and the whole pipeline reads their parts entirely into
memory (``ZipFile.read``), never checking against the declared uncompressed size.
Live audit measurement: a 199 KB archive expands to 438 MB peak RAM on a SINGLE
``z.read`` — on a machine with 8 GB RAM that's an OOM for the whole run, not a
failure of a single document. The vector isn't hypothetical: docx files also
arrive from batch discovery channels, i.e. from third parties.

The gate is a single pass over ``infolist()`` BEFORE the first part is read: the
declared ``file_size`` covers the main failure class (whole-file-in-memory
``read``), and an understated size on a deliberately corrupted archive breaks the
decompression itself with a controlled exception, isolated per-doc. There is
deliberately no byte-by-byte stream check (per the spec's Design rationale):
wrapping every read for the sake of an edge case that already fails loudly would
be complexity without payoff.

Industry precedent — Apache POI's ``ZipSecureFile`` (min inflate ratio + size
ceiling) as standard-issue protection for OOXML pipelines.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MAX_MEMBER_BYTES = 128 * 1024 * 1024  # one part (large media/sheet) — with a generous margin
MAX_TOTAL_BYTES = 512 * 1024 * 1024  # whole archive, decompressed
# Initial calibration: a legitimate government document is orders of magnitude
# smaller (the heaviest in the corpus is a few MB), and an 8 GB RAM machine can
# hold 512 MB decompressed without risk.
# Like all numeric thresholds in the project — subject to revision once live
# acceptance data comes in.


class ArchiveBombSuspected(RuntimeError):
    """The archive declares a decompressed size above the ceiling — we don't start
    reading it.

    Inherits from ``RuntimeError``, not a refigure-specific exception: the gate is
    called from more than one entry point — ``docx.py``'s ``convert()`` and (stage
    4b) ``vlm.py``'s ``enhance_docx_markdown()``, which re-enters the same archive
    to read ``word/media/*``/render a group — and importing either from here would
    risk a circular import. This doesn't affect routing — each caller classifies
    the exception itself; the dedicated type exists for readability and tests.
    """


def check_archive(
    path: Path | bytes,
    *,
    max_member: int = MAX_MEMBER_BYTES,
    max_total: int = MAX_TOTAL_BYTES,
) -> None:
    """Check the declared sizes of archive members BEFORE reading any part. Returns
    None silently if everything is within limits; otherwise raises
    ``ArchiveBombSuspected`` naming the culprit.

    ``path`` can be ``bytes`` (refigure accepts in-memory input, §2
    stage2-public-api-wrapper) — it's wrapped in ``io.BytesIO`` before
    ``zipfile.ZipFile``; in that case the diagnostic has no filename."""
    name = path.name if isinstance(path, Path) else "<in-memory>"
    source = path if isinstance(path, Path) else io.BytesIO(path)
    with zipfile.ZipFile(source) as z:
        total = 0
        for info in z.infolist():
            if info.file_size > max_member:
                raise ArchiveBombSuspected(
                    f"{name}: member {info.filename} declares {info.file_size} bytes "
                    f"decompressed (ceiling {max_member}) — archive not read"
                )
            total += info.file_size
            if total > max_total:
                raise ArchiveBombSuspected(
                    f"{name}: total decompressed size exceeded {max_total} bytes — archive not read"
                )
