"""Corpus-wide aggregate totals (stage 7, README + demo).

Sums the two regression-baseline tables ``test_docx_corpus.py`` and
``test_xlsx_corpus.py`` already maintain (``_PINNED_DOCX_VALUES``/
``_PINNED_XLSX_VALUES``) — NOT a new computation, no new risk of drift from
what those tests already pin. This file's only job is to turn "the corpus
grew/shrank or a pinned value changed" into an explicit, named regression a
human reads and reacts to, because ``README.md``'s status section (stage 7
spec, §5) states these totals as prose ("tested against 27 real documents,
407 native charts, 35 composite groups") that pytest cannot otherwise cross-
check against.

A failure here means: update ``README.md``'s claim to match the new totals
printed by the assertion message, then update the pinned constants below —
not silently ignore it. Deliberately does NOT parse ``README.md`` itself
(regex-scraping markdown prose for numbers is brittle and not idiomatic
here) — this is the single source of truth the prose is written from.
"""

from __future__ import annotations

from .test_docx_corpus import _PINNED_DOCX_VALUES
from .test_xlsx_corpus import _PINNED_XLSX_VALUES

# Captured 2026-08-06 by summing both pinned tables. Update
# these four numbers (and README.md's status section) together whenever a
# fixture is added/removed or a pinned per-fixture tuple changes.
_EXPECTED_FIXTURE_COUNT = 27
_EXPECTED_CHARTS_FOUND = 407
_EXPECTED_CHARTS_RENDERED = 400
_EXPECTED_GROUPS_FOUND = 35


def test_corpus_totals_match_readme_claim() -> None:
    all_values = list(_PINNED_DOCX_VALUES.values()) + list(_PINNED_XLSX_VALUES.values())

    fixture_count = len(all_values)
    charts_found = sum(v[0] for v in all_values)
    charts_rendered = sum(v[1] for v in all_values)
    groups_found = sum(v[2] for v in all_values)

    assert (fixture_count, charts_found, charts_rendered, groups_found) == (
        _EXPECTED_FIXTURE_COUNT,
        _EXPECTED_CHARTS_FOUND,
        _EXPECTED_CHARTS_RENDERED,
        _EXPECTED_GROUPS_FOUND,
    ), (
        f"corpus totals drifted from README.md's claim: observed "
        f"{fixture_count} fixtures / {charts_found} charts_found / "
        f"{charts_rendered} charts_rendered / {groups_found} groups_found "
        f"vs. pinned {_EXPECTED_FIXTURE_COUNT}/{_EXPECTED_CHARTS_FOUND}/"
        f"{_EXPECTED_CHARTS_RENDERED}/{_EXPECTED_GROUPS_FOUND} — update "
        f"README.md's status section AND the constants above together"
    )


def test_docx_xlsx_split_matches_manifest() -> None:
    # Cheap sanity check on the split itself (15 docx + 12 xlsx, per
    # CLAUDE.md/manifest.yaml) — catches a table getting entries added to
    # the wrong file, not just the wrong total.
    assert len(_PINNED_DOCX_VALUES) == 15
    assert len(_PINNED_XLSX_VALUES) == 12
