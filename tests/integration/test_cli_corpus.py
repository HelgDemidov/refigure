"""Integration smoke test: ``refigure``'s CLI batch mode against the FULL
real corpus (``tests/integration/fixtures/{docx,xlsx}/``), not 1-2
hand-picked files — an amendment to the cli-wrapper spec's test plan
(2026-08-05, user decision).

Proves two things ``tests/unit/test_cli.py`` (synthetic fixtures)
structurally cannot: (1) the CLI is a pure pass-through over ``convert()``
— every batch output byte-for-byte matches a direct ``convert()`` call on
the same real file, not just "doesn't crash"; (2) directory-walk batch
mode holds up at real corpus volume (27 real office documents), not just
a couple of files.

Split into one test per format (not a single combined batch call) purely
to sidestep the theoretical basename-collision case if a future corpus
addition ever shares a stem across docx/xlsx — see
``_detect_collisions``/Docling issue #3811 in ``refigure/cli.py``. Both
formats' fixtures happen to convert in one process either way; this is
about output-path safety, not runtime.

Gracefully skips (not fails) when the local fixture corpus isn't present
on disk — same convention as ``test_docx_corpus.py``/``test_xlsx_corpus.py``
(fixture binaries are gitignored, see ``fixtures/README.md``). Runs the
full real corpus twice per fixture (once via the CLI, once via a direct
``convert()`` call, to prove they match) — noticeably slower than the
single-pass corpus tests; that cost buys the pass-through guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from refigure.api import ConversionResult
from refigure.cli import EXIT_OK, main
from refigure.docx import convert as convert_docx
from refigure.xlsx import convert as convert_xlsx

from .conftest import FixtureInfo, load_manifest_fixtures

_DOCX_FIXTURES = [fx for fx in load_manifest_fixtures("docx") if fx.exists]
_XLSX_FIXTURES = [fx for fx in load_manifest_fixtures("xlsx") if fx.exists]

_SKIP_REASON = (
    "no corpus fixtures present on disk for this format (gitignored "
    "binaries — see fixtures/README.md to set up locally)"
)


def _assert_batch_matches_direct(
    fixtures: list[FixtureInfo],
    out_dir: Path,
    convert_fn: Callable[[Path], ConversionResult],
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources = [str(fx.path) for fx in fixtures]

    code = main([*sources, "-o", str(out_dir), "--json"])

    n = len(fixtures)
    assert code == EXIT_OK, f"expected all {n} fixtures to convert cleanly via CLI batch mode"
    assert f"{n}/{n} converted, 0 failed" in capsys.readouterr().err

    for fx in fixtures:
        out_path = out_dir / Path(fx.filename).with_suffix(".json")
        assert out_path.exists(), f"missing CLI batch output for {fx.filename}"
        cli_payload = json.loads(out_path.read_text(encoding="utf-8"))

        direct = convert_fn(fx.path)

        assert cli_payload["markdown"] == direct.markdown, (
            f"{fx.filename}: CLI batch markdown diverges from direct convert() "
            "— the CLI is supposed to be a pure pass-through, not a reimplementation"
        )
        assert cli_payload["charts_found"] == direct.charts_found
        assert cli_payload["charts_rendered"] == direct.charts_rendered
        assert cli_payload["groups_found"] == direct.groups_found
        assert cli_payload["warnings"] == direct.warnings


@pytest.mark.skipif(not _DOCX_FIXTURES, reason=_SKIP_REASON)
def test_docx_corpus_batch_matches_direct_convert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _assert_batch_matches_direct(_DOCX_FIXTURES, tmp_path / "out", convert_docx, capsys)


@pytest.mark.skipif(not _XLSX_FIXTURES, reason=_SKIP_REASON)
def test_xlsx_corpus_batch_matches_direct_convert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _assert_batch_matches_direct(_XLSX_FIXTURES, tmp_path / "out", convert_xlsx, capsys)
