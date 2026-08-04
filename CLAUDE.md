# refigure — Claude Code memory

## Project
PDF/DOCX/XLSX→Markdown converters extracted from a private pipeline (G2AI_ME),
scoped to one shared PyPI package for **DOCX+XLSX only** — PDF excluded from the
project entirely (crowded market, not a differentiator; see
`docs/converter-viability-assessment-2026-08-04.md`). Core differentiator: native
OOXML chart-data extraction (numCache/strCache, no rasterize/OCR/VLM) + positioned
zero-loss markers for composite figures — market research found this genuinely
absent even among well-funded incumbents (Docling issue #1287, >1yr open, unfixed).

## Status (2026-08-05)
Design phase complete. Stages 1-2 (PRs #1-#2: `docx.convert()`/`xlsx.convert()`
callable, `ConversionResult`/3 typed exceptions/`Path|bytes|BinaryIO`), CI +
40-test robustness suite (PRs #3-#5: 4 bugs fixed, CI triggers fixed), stage 4
(PR #6: English translation) all merged. Stage 3 (corpus-fixture licensing)
done ad hoc, not a numbered PR: 27 real-document fixtures (15 docx, 12 xlsx),
provenance/license/sha256 in `tests/integration/fixtures/manifest.yaml`,
binaries gitignored (~81MB) — see `fixtures/README.md`. Stage 5 (PR #7):
ported 8 G2AI_ME test files + a new parametrized corpus-test layer
(`test_docx_corpus.py`/`test_xlsx_corpus.py`) exercising all 27 fixtures —
found and fixed 2 real bugs (chart-parsing crash on `#N/A` cached values,
silent chart loss on grouped xlsx anchors) via
`superpowers:systematic-debugging`, see `project_stage5_chart_parsing_bugs`
memory. 191 tests total. Stage 6 (PR #8): extras-isolation CI matrix (fresh
isolated venv per `bare`/`docx`/`xlsx`/`both` combo) — found and fixed a 3rd
real bug the same day (`xlsx.py`'s openpyxl guard ran after an unguarded
transitive import in `xlsx_charts.py`), see `project_extras_isolation_bug`
memory. Not yet done: stage 4b (VLM, deferred — will need extending, not
redoing, the stage-6 matrix), stage 6b (CLI wrapper, added to the roadmap
2026-08-05, not yet built), stage 7 (README+demo), stage 8 (release gate).

## Dev environment
`pyproject.toml` (extras `[docx]`/`[xlsx]`, ruff/mypy/pytest config) +
`requirements.txt`/`requirements-dev.txt`, managed with `uv`. CI
(`.github/workflows/ci.yml`): 4 parallel jobs — `quality` (ruff+mypy+
pip-audit), `test-unit` (pytest `tests/unit` + coverage, no threshold gate
yet), `test-integration` (pytest `tests/integration` — real corpus-fixture
tests, stage 5; 0 collected in CI without the gitignored local fixture
setup, graceful not a failure), `test-extras` (stage 6: 4-leg matrix,
`bare`/`docx`/`xlsx`/`both`, each a FRESH isolated venv — not
requirements-dev.txt like the other 3 jobs — the only way to actually
catch a broken extras boundary). Custom Claude Code
commands in `.claude/commands/`: `/tech-spec` (draft a spec under `docs/`),
`/feature-workflow` (implement one end-to-end), `/post-merge-sync` (this
command), `/memory-sync` (audit memory against live code).

## Git workflow
Routine changes (≤1-2 commits, no `/tech-spec`+`/feature-workflow` needed) —
direct commit to `main`, no PR. Substantial changes — `/tech-spec` →
`/feature-workflow` → PR. No branch protection on `main` yet, deliberately
(solo pre-release velocity) — revisit at the v1 release gate (stage 8):
no-force-push/no-deletion/required-status-checks, per OpenSSF Scorecard's
Branch-Protection/Code-Review checks (matter once public-facing, not mid
solo iteration). Full PR+review only if/when external contributors appear.
Not purely a deferred choice: the GitHub repo is currently **private**, and
`branches/main/protection` 403s with "Upgrade to GitHub Pro or make this
repository public" — branch protection is structurally unavailable on a
private repo under GitHub Free, not just switched off. Resolves itself once
the repo goes public for the PyPI release (already the stage-8 plan), but
don't assume it's a settings toggle we simply haven't flipped before then.
Side effect: with no required-status-checks rule, a merged PR's CI results
don't show the usual prominent "required checks" gate banner — easy to
misread as "CI didn't run." Verify with `gh pr checks <n>` or the Checks API
against the PR's head SHA, not by eyeballing the merge box.

`delete_branch_on_merge: true` (2026-08-05) — unlike branch protection,
this IS available on a private repo under GitHub Free (a plain repo
setting, not gated); merged PR branches now auto-delete on GitHub. Local
tracking branches still need manual `git branch -d` (or `-D` after a squash
merge, which `git` can't always detect as "fully merged") — `git fetch
--prune` clears stale `remotes/origin/*` refs but not local branches.

## Scope v1
- DOCX + XLSX, one package, not two.
- VLM composite-figure interpretation (LibreOffice+cloud): ported in parallel,
  gated behind `[vlm]` extra + runtime `use_vlm` toggle — **not active/announced
  in v1**.
- MCP server: **not v1** — v2's primary goal (replaces PDF as the v2 target).
- CLI wrapper (stage 6b, added 2026-08-05, not yet built): thin argparse layer
  over `convert()`, MarkItDown-parity scope only (single file in, markdown on
  stdout/`-o`) — market check found CLI is the category baseline, not an
  optional extra (MarkItDown/Docling/marker all ship one as first-class).
- Full phase/effort breakdown: `docs/execution-sequence-2026-08-04.md` (10
  numbered stages + 4b/6b insertions, dependency graph, %-effort per stage,
  mermaid-verified before publish).

## Package architecture
- One PyPI package, extras by format+capability: `[docx]`, `[xlsx]`, `[vlm]`.
- Independent per-format submodules (`refigure/docx.py` imports only
  mammoth+markdownify; `refigure/xlsx.py` imports only openpyxl) — avoids
  per-dependency try/except gymnastics. The guard must run BEFORE any
  same-package import, not just exist somewhere in the file: `xlsx_charts.py`
  also touches openpyxl directly (`get_column_letter`) with no guard of its
  own — a real bug (PR #8) when `xlsx.py` imported it before its own
  try/except ran. Same discipline will need re-checking when stage 4b's VLM
  code touches `chart_render.py`/`docx_groups.py`.
- `chart_data.py`/`chart_render.py` — core, always installed, lxml-only +
  `mermaidx` optional inside itself.
- Optional-dependency pattern (proven, implemented+tested in G2AI_ME commit
  `25ef657`): module-level `try/except ImportError` + capability flag +
  `functools.lru_cache`-based warn-once via `logger.warning`. Reuse for
  mammoth/openpyxl/vlm-client — don't invent a new mechanism per dependency.

## Public API
Rich, not a bare string — full rationale in
`docs/v1-scope-and-api-design-2026-08-04.md` §3:
- `ConversionResult` dataclass: markdown + warnings + charts_found/rendered +
  groups_found + vlm_used.
- Typed exceptions: `UnsupportedFormatError`, `CorruptArchiveError`,
  `MissingOptionalDependencyError`.
- `strict: bool` — raise vs. degrade-with-warning on a missing capability.
- Input: path or bytes/file-like, not path-only.
- Config/client object, not a kwarg pile.
- Sync by default (G2AI_ME's `openrouter.py` confirmed fully sync, not assumed).
  Async is NOT for the whole library — only a later, narrow VLM-batch entry
  point, decided when VLM ships, doesn't block v1.

## Working language
English: code, comments/docstrings, commits, PRs, this file. Russian: dialogue
with the user, and `docs/` (project documentation/specs) only — `docs/` is
tracked in git here (unlike some sibling projects), a deliberate choice, not
gitignored scratch space.

## Memory/doc update convention
Replace or delete stale content — never append a "superseded" note next to old
info left standing. No tombstones except where genuinely necessary for
traceability.

## Memory style
Applies to this file and any future Claude memory file in this repo: English,
dry, compressed, high information density — no filler, no restating what's
obvious from the code itself.

## Do NOT
- Launch the Agent tool (subagents/worktrees) without the user's explicit
  permission first.
- Rewrite already-pushed commit history to fix past convention violations
  (e.g. early Russian-language commits) — apply rules going forward only.
- Parse untrusted XML with stdlib `xml.etree.ElementTree` — no nesting-depth
  or entity-expansion protection, unlike `lxml`. Real bug, fixed in PR #4 —
  see `feedback_untrusted_input_handling` memory.
- Claim "battle-tested"/"production-ready" in README/package description
  without verifiable evidence — decision 2026-08-04, research-backed
  (`producingoss.com`'s marketing-claims chapter; CNCF's own graduation bar
  requires ≥3 independent production adopters even at its lowest tier).
  Neither is testable pre-release with zero external users, regardless of
  fixture-corpus size — corpus size supports a *different*, honest claim
  ("validated against N real documents, M native charts, K composite
  groups — see `tests/integration/fixtures/manifest.yaml`"), not that one.
  Revisit "battle-tested"-tier language only once real external adopters
  exist to cite (natural fit: stage 7 README, or post-release).

## Source docs (Russian, tracked in git — project documentation, not code)
- `docs/converter-viability-assessment-2026-08-04.md` — market research +
  code-review verdict on whether extraction is worth it.
- `docs/v1-scope-and-api-design-2026-08-04.md` — scope/architecture/API
  decisions.
- `docs/execution-sequence-2026-08-04.md` — phased plan, dependency graph,
  effort % per stage.
