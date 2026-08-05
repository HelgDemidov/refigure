# Integration test fixtures

Real DOCX/XLSX documents `tests/integration/` (stage 5) will convert against,
complementing the synthetic minimal fixtures built in-line in `tests/unit/`.

## Why these aren't committed to git

Mirrors the source pipeline's own pattern — its equivalent fixture directory
(`pipeline/scripts/tests/fixtures/local/`) is gitignored there too, even in
a private repo. ~81MB of binary office documents committed to git bloats the
repository permanently; git doesn't shrink history back down without a
rewrite. `manifest.yaml` is the tracked source of truth instead.

## Layout

- `docx/` — Word documents
- `xlsx/` — Excel workbooks
- `manifest.yaml` — provenance, license, attribution text, and a sha256
  checksum for every fixture

## Setting this up locally

Fixtures are not downloaded automatically.

1. For every `manifest.yaml` entry that has a `source_url`, download it into
   the matching `docx/`/`xlsx/` subdirectory under its `filename`.
2. Verify integrity: `sha256sum <file>` should match the manifest entry
   (except `eia-steo-chart-gallery.xlsx` — its source URL always serves the
   current month's edition, so its checksum will legitimately drift; see its
   manifest note).
3. The one `source: own` entry (`docx/iot-report-2022-national-strategies-excerpt.docx`)
   has no public URL — it isn't independently obtainable, only present if you
   are the repo owner with local access to it.

Integration tests are expected to skip individual fixture-dependent cases
gracefully when the corresponding file isn't present on disk (stage 5), so a
partial or empty local setup narrows coverage rather than breaking the suite.
