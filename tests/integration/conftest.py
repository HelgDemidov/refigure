"""Shared corpus-fixture loading for tests/integration/ (stage 5).

``tests/integration/fixtures/manifest.yaml`` is the tracked source of truth
for the real DOCX/XLSX corpus (provenance/license/sha256) — the binaries
themselves live under ``fixtures/docx/`` and ``fixtures/xlsx/`` but are
gitignored (~81MB of office documents; see ``fixtures/README.md``). A fresh
clone or a CI run without the local fixture setup has an empty or partial
``fixtures/{docx,xlsx}/`` directory, so every manifest entry must resolve to
a gracefully-skipped test case (not a failure) when its file isn't present
on disk — mirrors the source pipeline's own
``pytest.mark.skipif(not _FIXTURE.exists(), ...)`` pattern for the same
reason.

``load_manifest_fixtures``/``fixture_params`` are format-agnostic (take a
``fmt`` of ``"docx"`` or ``"xlsx"``) so both ``test_docx_corpus.py`` and
``test_xlsx_corpus.py`` parametrize off the same helper instead of each
re-implementing manifest parsing.

Why a hand-rolled parser instead of PyYAML: verified 2026-08-05 that PyYAML
is not a dependency anywhere in this project — absent from
``requirements.txt``/``requirements-dev.txt``/``pyproject.toml`` and not
importable in the project's own ``.venv``. Adding it would mean introducing
a new dependency from a test-only file, which is out of scope here (and
this task is scoped to conftest.py + test_docx_corpus.py only, not
touching dependency manifests) — so this uses the pathlib + manual parsing
fallback. ``_parse_manifest`` is deliberately NOT a general YAML parser: it
handles exactly the subset ``manifest.yaml`` uses (two top-level list
sections, flat scalar keys per entry, ``key: >`` folded multi-line block
scalars) and nothing else.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_MANIFEST_PATH = _FIXTURES_DIR / "manifest.yaml"
_FOLD_INDENT = "      "  # 6 spaces: continuation-line indent for `key: >` block scalars


def _parse_manifest(path: Path) -> dict[str, list[dict[str, str | None]]]:
    """Parse manifest.yaml's exact YAML subset into {section: [entry, ...]}.

    Section = top-level ``docx:``/``xlsx:`` key. Entry = one ``  - filename:
    ...`` list item, flattened to a dict of its scalar keys (``key: >``
    folded block scalars are joined into a single space-separated string,
    matching YAML's own folding semantics closely enough for this file's
    purposes — the parsed value is never diffed against manifest text by
    any test, only read informationally).
    """
    sections: dict[str, list[dict[str, str | None]]] = {}
    current_section: str | None = None
    current_entry: dict[str, str | None] | None = None
    fold_key: str | None = None
    fold_lines: list[str] = []

    def flush_fold() -> None:
        nonlocal fold_key, fold_lines
        if current_entry is not None and fold_key is not None:
            current_entry[fold_key] = " ".join(fold_lines).strip()
        fold_key = None
        fold_lines = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("#"):
            continue
        if not raw_line.strip():
            flush_fold()
            continue
        if fold_key is not None and raw_line.startswith(_FOLD_INDENT):
            fold_lines.append(raw_line.strip())
            continue
        flush_fold()

        # Top-level section header, e.g. "docx:" (zero indent, no list marker).
        if not raw_line.startswith(" ") and raw_line.rstrip().endswith(":"):
            current_section = raw_line.rstrip()[:-1].strip()
            sections[current_section] = []
            current_entry = None
            continue

        stripped = raw_line.strip()
        if raw_line.startswith("  - "):
            if current_section is None:
                continue  # malformed/unexpected shape — skip defensively
            current_entry = {}
            sections[current_section].append(current_entry)
            stripped = stripped[2:]  # drop the "- " list marker
        elif not raw_line.startswith("    "):
            continue  # neither a new entry nor a continuation of one — skip

        if current_entry is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value == ">":
            fold_key = key
            fold_lines = []
        elif value in ("", "null"):
            current_entry[key] = None
        else:
            current_entry[key] = value.strip('"')

    flush_fold()
    return sections


@dataclasses.dataclass(frozen=True)
class FixtureInfo:
    """One manifest.yaml entry resolved against the local filesystem."""

    filename: str
    path: Path
    format: str  # "docx" or "xlsx"
    license: str | None
    sha256: str | None
    exists: bool


def load_manifest_fixtures(fmt: str) -> list[FixtureInfo]:
    """All manifest entries for ``fmt`` ("docx" or "xlsx"), each resolved to
    an absolute path under ``fixtures/<fmt>/`` and checked for on-disk
    presence. Never raises on a missing section/entry/file — a partial or
    empty local fixture setup is the expected common case, not an error."""
    sections = _parse_manifest(_MANIFEST_PATH)
    fixtures: list[FixtureInfo] = []
    for entry in sections.get(fmt, []):
        filename = entry.get("filename")
        if not filename:
            continue
        path = _FIXTURES_DIR / fmt / filename
        fixtures.append(
            FixtureInfo(
                filename=filename,
                path=path,
                format=fmt,
                license=entry.get("license"),
                sha256=entry.get("sha256"),
                exists=path.exists(),
            )
        )
    return fixtures


def fixture_params(fmt: str) -> list[Any]:
    """``pytest.param(...)`` entries for every manifest fixture of ``fmt``,
    ready to hand straight to ``@pytest.mark.parametrize("fx",
    fixture_params("docx"))``. Each param carries a ``FixtureInfo`` and is
    individually ``skipif``-marked when its file isn't present on disk, so
    a partial local setup shows up as a visible "skipped" row per fixture
    instead of silently shrinking the parametrization list."""
    params = []
    for fx in load_manifest_fixtures(fmt):
        params.append(
            pytest.param(
                fx,
                id=fx.filename,
                marks=pytest.mark.skipif(
                    not fx.exists,
                    reason=(
                        f"fixture not present on disk: {fx.path} "
                        "(gitignored binary — see fixtures/README.md to set up locally)"
                    ),
                ),
            )
        )
    return params
