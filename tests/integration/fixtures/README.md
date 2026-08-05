# Integration test fixtures

Real DOCX/XLSX documents `tests/integration/` converts against, complementing
the synthetic minimal fixtures built in-line in `tests/unit/`.

## Committed vs. gitignored (2026-08-05)

26 of 27 fixtures (~133MB) are committed directly — clean, well-documented
redistribution licenses (CC BY 4.0/CC BY 3.0 IGO/EU reuse right/US public
domain), see `../../../ATTRIBUTION.md` and `manifest.yaml`'s
`attribution:`/`license:` fields. This makes CI's `test-integration` and
`test-unit` (stage 4b's soffice-render path) exercise the real corpus, not
0 collected tests against an empty fixture directory.

One exception stays gitignored: `docx/iot-report-2022-national-strategies-excerpt.docx`
— authorship risk, not a license grant (`source: own`, no independent proof
of authorship beyond file possession), explicitly accepted by the repo owner
2026-08-04, see its `manifest.yaml` note. Not independently obtainable —
only present if you are the repo owner with local access to it.

## Layout

- `docx/` — Word documents
- `xlsx/` — Excel workbooks
- `manifest.yaml` — provenance, license, attribution text, and a sha256
  checksum for every fixture

## Re-verifying integrity

`sha256sum <file>` should match the manifest entry for every committed
fixture, except `eia-steo-chart-gallery.xlsx` — its source URL always serves
the current month's edition, so a fresh re-download will legitimately drift
from the checksum recorded here; see its manifest note.

Integration tests are expected to skip individual fixture-dependent cases
gracefully when the corresponding file isn't present on disk (currently only
the one gitignored exception), so a checkout missing that one file narrows
coverage by exactly one test case rather than breaking the suite.
