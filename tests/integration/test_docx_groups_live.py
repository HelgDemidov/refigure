"""Live soffice-render test for composite DOCX groups — ported from the
source pipeline, deferred at stage 5 (needed ``vlm.py``, not yet ported
then; see the ``project_deferred_docx_groups_live_test`` memory) and
completed here at stage 4b.

Targets ``efsa-echinococcus-guide.docx`` (CC BY 4.0, see
``../../../ATTRIBUTION.md``) — 10 composite groups, the highest group
density in the corpus (pinned in ``test_docx_corpus.py``'s
``_PINNED_DOCX_VALUES`` as ``groups_found=10``). The originally-planned
target, ``iot-report-2022-national-strategies-excerpt.docx``, stays
gitignored (authorship risk, not a license grant) and so isn't reliably
available in CI — replaced 2026-08-05, see
``docs/vlm/vlm-layer-port/vlm-layer-port-2026-08-05.md`` §4.

Exercises the REAL ``soffice`` binary — gated via
``skipif(not shutil.which("soffice"))`` as a defensive local-dev
fallback, but CI installs ``libreoffice-writer`` (``test-unit`` job,
``docs/vlm/vlm-layer-port/vlm-layer-port-2026-08-05.md`` §3) specifically so this path is NOT
expected to skip there: an untested CI path for this code was explicitly
rejected.

All 10 groups in this fixture have empty captions (verified live,
2026-08-05) — this test exercises the render mechanism itself (mini-docx
-> soffice -> PDF -> content-bbox crop -> JPEG), NOT the witness gate
(``vlm.witness_defects``), which needs non-empty captions and is covered
separately by a synthetic fixture in ``tests/unit/test_vlm.py``.
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from refigure import vlm
from refigure.docx import groups as docx_groups

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "docx" / "efsa-echinococcus-guide.docx"

# Pre-verified live 2026-08-05 (docx_groups.extract_and_strip_groups against
# the real, now-committed fixture) — see docs/vlm/vlm-layer-port/vlm-layer-port-2026-08-05.md
# §4. id12 = sha256 of the group's own XML element, deterministic.
_KNOWN_GROUP_IDS = (
    "a106c326d0e9",
    "23da97fa9020",
    "179dfcb933b4",
    "fde6ff90fd0c",
    "1d84dbf972e7",
    "4e3bc9ef73f2",
    "559538feeabf",
    "961722518e1d",
    "41e0f316a735",
    "32aee94aaaeb",
)

pytestmark = pytest.mark.skipif(
    not _FIXTURE_PATH.exists(),
    reason=f"fixture not present on disk: {_FIXTURE_PATH}",
)


def test_fixture_group_ids_match_pinned_baseline() -> None:
    """Re-detection is deterministic — a regression here means either the
    fixture changed or docx_groups.py's detection logic changed; either way
    this is the canary the rest of this file's parametrization depends on."""
    _, groups = docx_groups.extract_and_strip_groups(_FIXTURE_PATH)
    group_only = [g for g in groups if g.kind == "group"]
    observed_ids = frozenset(g.id12 for g in group_only)
    assert observed_ids == frozenset(_KNOWN_GROUP_IDS)
    assert all(g.captions == () for g in group_only), (
        "this test targets the render mechanism, not the witness gate — a "
        "fixture with non-empty captions would need a different test, see "
        "tests/unit/test_vlm.py for that coverage"
    )


@pytest.mark.skipif(
    shutil.which("soffice") is None,
    reason=(
        "soffice (LibreOffice) not installed — defensive local-dev fallback; "
        "CI installs libreoffice-writer specifically so this is not expected "
        "to skip there, see docs/vlm/vlm-layer-port/vlm-layer-port-2026-08-05.md §3"
    ),
)
@pytest.mark.parametrize("id12", _KNOWN_GROUP_IDS)
def test_render_docx_group_produces_a_real_jpeg(id12: str) -> None:
    data_uri = vlm._render_docx_group(_FIXTURE_PATH, id12, raw_name=_FIXTURE_PATH.name)

    assert data_uri is not None, f"group {id12} failed to render — see warnings in the test log"
    assert data_uri.startswith("data:image/jpeg;base64,")

    raw = base64.b64decode(data_uri.removeprefix("data:image/jpeg;base64,"))
    assert raw[:2] == b"\xff\xd8", "decoded payload is not a valid JPEG (bad SOI marker)"
    assert len(raw) > 1000, "suspiciously small render output"
