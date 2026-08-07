"""Decompression ceiling for OOXML archives (spec convert-knowledge-seam-hardening §8).

docx/xlsx are zip archives, and the whole pipeline reads their parts entirely into
memory (``ZipFile.read``), never checking against the declared uncompressed size.
Live audit measurement: a 199 KB archive expands to 438 MB peak RAM on a SINGLE
``z.read`` — on a machine with 8 GB RAM that's an OOM for the whole run, not a
failure of a single document. The vector isn't hypothetical: docx files also
arrive from batch discovery channels, i.e. from third parties.

Two layers, not one — ``check_archive()``'s declared-size pass alone is NOT
real enforcement: ``ZipInfo.file_size`` is header metadata the attacker
controls directly, not a fact about the real decompressed content. Live
security-audit PoC (2026-08-07): a 204 KB archive with a spoofed declared
size of 100 bytes on a member whose real compressed payload inflates to
~200 MB passes ``check_archive()`` cleanly — nothing about an understated
declared size "breaks the decompression with a controlled exception" (an
earlier version of this docstring claimed exactly that; it's false, this PoC
refutes it). A plain ``z.read()`` on that member then fully decompresses the
real payload before anything fires — a 400+ MB RSS spike, not a caught
exception. The two layers:
- ``check_archive()`` — a cheap upfront pass over ``infolist()`` BEFORE any
  part is read. Catches the honest case (a member, or the archive as a
  whole, that legitimately declares an oversized total), for free, before
  any real work starts. It is an upfront reject, not the real defense
  against a spoofed declared size.
- ``safe_read()`` — the real enforcement, checked against ACTUAL decompressed
  bytes during the read itself, not declared metadata: reads a member via
  ``z.open()`` in bounded chunks, counts real bytes as they come out, and
  raises ``ArchiveBombSuspected`` the moment that count exceeds the ceiling
  — never buffering the full oversized payload first. Every ``z.read()``
  call site in the codebase goes through this instead of the zipfile method
  directly.

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

_SAFE_READ_CHUNK_BYTES = 1024 * 1024  # 1 MB — bounds how far a single check can overshoot max_bytes


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
    """Cheap upfront reject over declared archive metadata, BEFORE reading any
    part. Returns None silently if everything is within limits; otherwise
    raises ``ArchiveBombSuspected`` naming the culprit.

    This is the honest/declared-oversized layer only — it trusts
    ``ZipInfo.file_size``, which is attacker-controlled header metadata, not a
    fact about the real decompressed content. It does NOT protect against a
    spoofed (understated) declared size; that's ``safe_read()``'s job, checked
    against actual bytes during the real read (see the module docstring for
    the full two-layer rationale and a live PoC).

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


def safe_read(
    z: zipfile.ZipFile,
    name_or_info: str | zipfile.ZipInfo,
    *,
    max_bytes: int = MAX_MEMBER_BYTES,
) -> bytes:
    """Read one archive member, enforcing ``max_bytes`` against ACTUAL
    decompressed bytes as they come out — not ``ZipInfo.file_size`` (declared,
    attacker-controlled metadata that ``check_archive()`` alone cannot verify;
    see the module docstring for the live PoC this closes).

    Reads via ``z.open(name_or_info)`` in bounded chunks
    (``_SAFE_READ_CHUNK_BYTES``), accumulating only what's been verified safe
    so far. Raises ``ArchiveBombSuspected`` the moment the running total
    exceeds ``max_bytes`` — the oversized chunk itself is never appended, so
    the full oversized payload is never buffered, only up to one chunk's
    worth past the ceiling is ever held in memory.

    This is the drop-in replacement for every ``z.read(...)`` call site in the
    codebase — same return type, same two accepted argument forms (a member
    name or a ``ZipInfo``)."""
    name = name_or_info.filename if isinstance(name_or_info, zipfile.ZipInfo) else name_or_info
    chunks: list[bytes] = []
    total = 0
    with z.open(name_or_info) as member:
        while True:
            chunk = member.read(_SAFE_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ArchiveBombSuspected(
                    f"{name}: decompressed past {max_bytes} bytes during read "
                    f"— archive member not fully read"
                )
            chunks.append(chunk)
    return b"".join(chunks)
