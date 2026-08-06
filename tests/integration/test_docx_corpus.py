"""Real-corpus behavioral tests for ``refigure.docx.convert()`` (stage 5).

Runs against the actual DOCX fixtures cataloged in
``tests/integration/fixtures/manifest.yaml``. Fixture binaries are
gitignored (see ``fixtures/README.md``) — any entry whose file isn't
present on disk is skipped via ``conftest.fixture_params`` (fresh clone /
CI without local fixture setup), not failed.

Each fixture gets ONE test running two assertion tiers against a single
``convert()`` call (some fixtures take 10-20s — running convert() twice per
fixture for separately-named tiers would needlessly double total suite
time):

* Tier A — invariants that hold identically for every fixture, regardless
  of its specific content (no fixture-specific numbers).
* Tier B — one pinned golden value per fixture, capturing the ACTUAL
  ``charts_found``/``charts_rendered``/``groups_found`` observed by running
  ``convert()`` against the real file on 2026-08-05 (git branch
  ``test/stage5-port-and-corpus-tests``) — NOT values inferred from
  ``manifest.yaml``'s raw-XML provenance notes. ``docx_groups.py``'s
  group/chart detection is intentionally narrower than "every chart part /
  every mc:AlternateContent block in the file" (it only flags composite
  figures its own logic recognizes as needing extraction ahead of
  mammoth), so the manifest's raw-XML counts and refigure's reported counts
  can legitimately diverge. See ``_PINNED_DOCX_VALUES`` below for the one
  fixture where they do, and why.
"""

from __future__ import annotations

import time

import pytest

from refigure.docx import convert

from .conftest import FixtureInfo, fixture_params

# Generous wall-clock ceiling, not a tight regression guard — a prior
# robustness-test round in this project hit real flakiness from tight timing
# budgets under parallel test-suite load (see feedback_untrusted_input_handling
# memory). 120s comfortably covers even the 53MB marcobolo fixture (observed
# ~1.5s) and the slowest observed fixture (onehealth-ejp-d3.20.docx, ~23s).
_CONVERT_TIMEOUT_S = 120.0

# Exact warning strings refigure/docx/__init__.py's convert() can append to
# ConversionResult.warnings, as of 2026-08-05 (read directly from source, not
# guessed/copied from an older memory of it — keep this set in sync if
# docx.py's warning text changes; it's not exposed as named constants there).
_KNOWN_DOCX_WARNINGS = frozenset(
    {
        "no extractable content",
        "mermaidx not installed — chart diagrams disabled, tables only "
        "(install refigure[docx] with mermaidx to enable rendering)",
    }
)

# Pinned baselines: filename -> (charts_found, charts_rendered, groups_found),
# captured live 2026-08-05 by running convert() against every fixture and
# cross-checking the result qualitatively against manifest.yaml's notes
# field. Every entry below matched the manifest's qualitative expectation
# (charts/groups present vs. absent) EXCEPT marcobolo-3rd-workshop-report.docx
# — see its comment, which is a confirmed false-positive in the manifest's
# own grep-based verification method, not a refigure detection gap.
_PINNED_DOCX_VALUES: dict[str, tuple[int, int, int]] = {
    # manifest: "18 embedded images, 8 mc:AlternateContent blocks — all
    # Requires=\"wps\" ..., zero wpg occurrences, zero native chart parts."
    # Matches: no groups, no charts.
    "ipbes-ias-assessment-spm.docx": (0, 0, 0),
    # manifest: "8 native word/charts/chartN.xml parts, containing real
    # c:numCache/c:strCache data". Matches: 8 charts, 0 groups.
    "hackair-d7.7-pilot-evaluation.docx": (8, 8, 0),
    # manifest: "exactly one confirmed grouped shape (mc:Choice
    # Requires=\"wpg\"), zero native chart parts." Observed groups_found=0,
    # NOT 1 — investigated directly (unzipped and inspected
    # word/document.xml): the file's one Requires="wpg" mc:AlternateContent
    # block Choice contains a single background/watermark wps:wsp shape
    # (graphicData uri=".../wordprocessingShape"), not an actual
    # <wpg:wgp> group element anywhere in its subtree. docx_groups.py's
    # detection specifically requires choice.find(".//wpg:wgp") (see its
    # module docstring/_iter_group_acs) — Requires="wpg" alone (the
    # manifest's grep-based signal) is a weaker/looser match than "an
    # actual wpg:wgp group is present". This is a false positive in how
    # the manifest note was produced, not a refigure detection gap:
    # confirmed correct behavior, not silently pinning over a bug.
    "marcobolo-3rd-workshop-report.docx": (0, 0, 0),
    # manifest: "9 confirmed grouped shapes (mc:Choice Requires=\"wpg\") —
    # ... zero native chart parts." Matches: 9 groups, 0 charts.
    "efsa-trichinella-dashboard-guide.docx": (0, 0, 9),
    # manifest: "6 native chart parts with real c:numCache/c:strCache data,
    # ..., zero grouped shapes." Matches: 6 charts, 0 groups.
    "swd2018-254-marine-litter-ia-main.docx": (6, 6, 0),
    # manifest: "1 native chart part (real c:numCache/c:strCache), 2
    # confirmed grouped shapes (mc:Choice Requires=\"wpg\")". Matches:
    # 1 chart, 2 groups.
    "swd2018-254-marine-litter-ia-annex.docx": (1, 1, 2),
    # manifest: "44 native chart parts (172 c:numCache + 284 c:strCache
    # occurrences) ... Zero grouped shapes." Matches: 44 charts, 0 groups —
    # highest chart density in the corpus.
    "ukri-user-behaviour-survey.docx": (44, 44, 0),
    # manifest: "10 confirmed grouped shapes (mc:Choice Requires=\"wpg\") —
    # highest group density in the corpus ..., zero native chart parts."
    # Matches: 10 groups, 0 charts.
    "efsa-echinococcus-guide.docx": (0, 0, 10),
    # manifest: "8 confirmed grouped shapes (mc:Choice Requires=\"wpg\"),
    # ..., zero native chart parts." Matches: 8 groups, 0 charts.
    "efsa-rabies-guide.docx": (0, 0, 8),
    # manifest: "8 native chart parts (78 c:numCache + 54 c:strCache), 4
    # mc:AlternateContent blocks (no confirmed wpg group among them)".
    # Matches: 8 charts, 0 groups.
    "swd2021-396-platform-work-ia.docx": (8, 8, 0),
    # manifest: "5 native chart parts (42 c:numCache + 14 c:strCache), 1
    # confirmed grouped shape, ...". Matches: 5 charts, 1 group.
    "onehealth-ejp-d3.20.docx": (5, 5, 1),
    # manifest: "3 native chart parts (20 c:numCache + 36 c:strCache), ...,
    # zero grouped shapes." Matches: 3 charts, 0 groups.
    "kth-onsset-ecuador.docx": (3, 3, 0),
    # manifest: "2 native chart parts (24 c:numCache + 24 c:strCache), ...,
    # zero grouped shapes." Matches: 2 charts, 0 groups.
    "swd2020-335-batteries-ia-part2.docx": (2, 2, 0),
    # manifest: "2 native chart parts (20 c:numCache + 10 c:strCache), ...,
    # zero grouped shapes." Matches: 2 charts, 0 groups.
    "swd2020-335-batteries-ia-part3.docx": (2, 2, 0),
    # "own" fixture (repo owner's draft) — manifest note does not report a
    # verified chart/group XML count for this one (unlike every other entry,
    # which was explicitly XML-inspected), so there is no manifest claim to
    # cross-check against; pinning the observed value as a plain regression
    # baseline only.
    "iot-report-2022-national-strategies-excerpt.docx": (1, 1, 5),
}


@pytest.mark.parametrize("fx", fixture_params("docx"))
def test_docx_corpus_fixture(fx: FixtureInfo) -> None:
    start = time.monotonic()
    result = convert(fx.path)
    elapsed = time.monotonic() - start

    # --- Tier A: invariants, identical for every fixture ---
    assert result.markdown != "", "none of our fixtures are blank documents"
    assert result.charts_rendered <= result.charts_found
    assert result.groups_found >= 0
    assert result.charts_found >= 0
    for warning in result.warnings:
        assert warning in _KNOWN_DOCX_WARNINGS, f"unexpected warning text: {warning!r}"
    assert elapsed < _CONVERT_TIMEOUT_S, (
        f"convert() took {elapsed:.1f}s for {fx.filename}, expected < {_CONVERT_TIMEOUT_S}s"
    )

    # --- Tier B: pinned golden value, one per fixture ---
    expected = _PINNED_DOCX_VALUES.get(fx.filename)
    if expected is None:
        pytest.fail(
            f"no pinned baseline for {fx.filename!r} in _PINNED_DOCX_VALUES — "
            "run convert() against the file, observe the real "
            "charts_found/charts_rendered/groups_found, and add a pinned entry "
            "(see this file's module docstring for the methodology)."
        )
    observed = (result.charts_found, result.charts_rendered, result.groups_found)
    assert observed == expected, (
        f"{fx.filename}: charts_found/charts_rendered/groups_found regressed "
        f"from pinned baseline {expected} to {observed}"
    )
