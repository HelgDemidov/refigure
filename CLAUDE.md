# refigure — Claude Code memory

## Project
PDF/DOCX/XLSX→Markdown converters extracted from a private pipeline,
scoped to one shared PyPI package for **DOCX+XLSX only** — PDF excluded from the
project entirely (crowded market, not a differentiator; see
`docs/project-meta/converter-viability-assessment/converter-viability-assessment-2026-08-04.md`). Core differentiator: native
OOXML chart-data extraction (numCache/strCache, no rasterize/OCR/VLM) + positioned
zero-loss markers for composite figures — market research found this genuinely
absent even among well-funded incumbents (Docling issue #1287, >1yr open, unfixed).

## Status (2026-08-06)
Design phase complete. Stages 1-2 (PRs #1-#2: `docx.convert()`/`xlsx.convert()`
callable, `ConversionResult`/3 typed exceptions/`Path|bytes|BinaryIO`), CI +
40-test robustness suite (PRs #3-#5: 4 bugs fixed, CI triggers fixed), stage 4
(PR #6: English translation) all merged. Stage 3 (corpus-fixture licensing)
done ad hoc, not a numbered PR: 27 real-document fixtures (15 docx, 12 xlsx),
provenance/license/sha256 in `tests/integration/fixtures/manifest.yaml`,
binaries gitignored (~81MB) — see `fixtures/README.md`. Stage 5 (PR #7):
ported 8 test files from the source pipeline + a new parametrized corpus-test layer
(`test_docx_corpus.py`/`test_xlsx_corpus.py`) exercising all 27 fixtures —
found and fixed 2 real bugs (chart-parsing crash on `#N/A` cached values,
silent chart loss on grouped xlsx anchors) via
`superpowers:systematic-debugging`, see `project_stage5_chart_parsing_bugs`
memory. 191 tests total. Stage 6 (PR #8): extras-isolation CI matrix (fresh
isolated venv per `bare`/`docx`/`xlsx`/`both` combo) — found and fixed a 3rd
real bug the same day (`xlsx.py`'s openpyxl guard ran after an unguarded
transitive import in `xlsx_charts.py`), see `project_extras_isolation_bug`
memory. Stage 6b (PR #9, `refigure` console command): single-file
(stdin/stdout/`-o`), batch mode (directory walk, keep-going + summary,
`--fail-fast`), typed exit codes, `--json`/`--strict`/`-v`/`-q` — design
synthesized a "triple gap" against MarkItDown/Docling/marker (no
competitor combines stdio-first + native batch + typed exit codes), see
`docs/cli/cli-wrapper/cli-wrapper-2026-08-05.md`. Its CI run found a 4th real production
bug the same day: `xlsx.convert()` wasn't safe to call concurrently from
multiple threads — openpyxl's own `xml/functions.py` reuses ONE
module-level `lxml.etree.XMLParser()` across every thread, causing a rare
segfault (first fix attempt, reducing test concurrency, only lowered the
odds — a later local repro loop caught the SAME root cause as a silent
wrong result instead of a crash). Fixed for real with a
`threading.Lock()` around `openpyxl.load_workbook()` in `xlsx.py`, not
just a test change — see `project_openpyxl_concurrent_parser_fragility`
memory. Stage 4b (PRs #10-#11, VLM — gated behind `Config.use_vlm` +
`[vlm]` extra, not announced in v1) now merged: PR #10 ported the VLM
layer (`VlmClient`/`VlmCacheBackend` protocols, `OpenRouterClient`, the
free witness gate — `token_recall`/`numeric_counter`/
`chart_render.mermaid_renders()`) and picked `vlm_model`'s default
(`google/gemini-3-flash-preview`) via 2-round A/B calibration, see
`docs/vlm/vlm-model-calibration/vlm-model-calibration-2026-08-05.md`. PR
#11 (witness-gate-redesign) found the free gate is language-sensitive on
non-English source docs and added an opt-in paid path
(`Config.vlm_verify`), then live-validated it before merging (per its own
spec's mandatory gate) — found the *original design itself* broken
(same-model self-judge: 30%/12% hallucination/mermaid-fit recall against
24 manually-labeled real responses, false positives, non-deterministic
verdicts on repeat calls) and pivoted mid-PR to an independent-judge
architecture (`Config.vlm_judge_mode` solo/panel, default panel = 2 fixed
models unioned, 80%/88% recall, 100% of confirmed defects caught on at
least one dimension) — see `docs/vlm/vlm-model-calibration/
judge-defects-validation-2026-08-06.md`. Same PR also carried the
`refigure/`+`tests/` package reorg (flat modules → `core`/`docx`/`xlsx`/
`vlm` subpackages mirroring the extras) and found a 5th real production
bug via the extras-isolation CI matrix: nesting `docx_groups.py` under
`refigure/docx/` made `import refigure.vlm` transitively require
`refigure[docx]`'s mammoth (importing any submodule always runs its
package's `__init__.py` first) — invisible to the regular test suite
(every extra installed there), caught only by the one CI leg built for
exactly this. Fixed by keeping `docx_groups.py` a flat module, same
lesson as `project_extras_isolation_bug` memory. Stage 4b insertion (PR
#12, `vlm-direct-clients`, 2026-08-06) added 2 more `VlmClient`
implementations to `refigure/vlm/client.py`: `OpenAIClient` (openai SDK,
`base_url`-configurable — covers direct OpenAI AND any OpenAI-compatible
local server, Ollama/vLLM/LM Studio, one client not two) and
`AnthropicClient` (anthropic SDK, injectable `client=` — accepts
`anthropic.AnthropicBedrock`/`AnthropicVertex`/`AnthropicFoundry` as-is,
since all 3 share the same `.messages.create()` interface, so Claude via
Bedrock/Vertex/Foundry works with zero extra code in this class).
LiteLLM was researched and rejected (PyPI supply-chain compromise March
2026 + 2 CVEs on CISA KEV); `aisuite` also rejected (9 months stale, no
confirmed vision support) — see `docs/vlm/vlm-direct-clients/
vlm-direct-clients-2026-08-06.md`. Found a 6th real production bug the
same day — 3rd occurrence of the `project_extras_isolation_bug` class,
a new trigger this time (neither import-order nor package-nesting):
`refigure/vlm/client.py` is a submodule of `refigure.vlm`, so importing
it always runs `refigure/vlm/__init__.py`'s pdfplumber guard first —
`pip install refigure[vlm-direct]` alone broke without `[vlm]` too.
Fixed with a self-referential extra (`vlm-direct = ["refigure[vlm]",
...]`), not a module move — the coupling is real, not incidental
(`Config.vlm_client`'s only call site always renders via pdfplumber
first, regardless of which client sends the result). 293 unit tests
total. Not yet done: stage 7 (README+demo), stage 8 (release gate).

## Dev environment
`pyproject.toml` (extras `[docx]`/`[xlsx]`/`[vlm]`/`[vlm-direct]`,
`refigure` console script via
`[project.scripts]`, ruff/mypy/pytest config) +
`requirements.txt`/`requirements-dev.txt`, managed with `uv`. CI
(`.github/workflows/ci.yml`): 4 parallel jobs — `quality` (ruff+mypy+
pip-audit), `test-unit` (pytest `tests/unit` + coverage, no threshold gate
yet), `test-integration` (pytest `tests/integration` — real corpus-fixture
tests, stage 5; 0 collected in CI without the gitignored local fixture
setup, graceful not a failure), `test-extras` (stage 6, now 7-leg matrix,
`bare`/`docx`/`xlsx`/`both`/`vlm`/`docx+vlm`/`vlm-direct`, each a FRESH
isolated venv — not requirements-dev.txt like the other 3 jobs — the only
way to actually catch a broken extras boundary). Custom Claude Code
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
- CLI wrapper (stage 6b, shipped PR #9, 2026-08-05): `refigure` console
  command, thin argparse layer over `convert()` — single-file, stdin, and
  batch modes, typed exit codes — market check found CLI is the category
  baseline, not an optional extra (MarkItDown/Docling/marker all ship one
  as first-class). Details: `docs/cli/cli-wrapper/cli-wrapper-2026-08-05.md`.
- Full phase/effort breakdown: `docs/project-meta/execution-sequence/execution-sequence-2026-08-04.md` (10
  numbered stages + 4b/6b insertions, dependency graph, %-effort per stage,
  mermaid-verified before publish).

## Package architecture
- One PyPI package, extras by format+capability: `[docx]`, `[xlsx]`,
  `[vlm]`, `[vlm-direct]`.
- Directory layout mirrors this exactly (reorganized 2026-08-05, same
  category/slug principle as `docs/`, scoped to what the package's own
  extras boundaries already imply — see `docs/project-meta/` for the
  general convention): `refigure/core/` (`chart_data.py`/`chart_render.py`/
  `zipsafe.py` — always installed, no format-specific dependency),
  `refigure/docx/` (`__init__.py` — the public `refigure.docx.convert()`
  entry point, only submodule), `refigure/xlsx/` (`__init__.py` — public
  `refigure.xlsx.convert()` — + `charts.py`), `refigure/vlm/` (`__init__.py`
  + `client.py` — `OpenRouterClient`/`OpenAIClient`/`AnthropicClient` —
  + `cache.py`). `api.py`/`_io.py`/`cli.py`/`__main__.py`
  stay at the package root — `api.py` deliberately not moved into `core/`
  despite being core-only in spirit: its exception qualnames
  (`refigure.api.MissingOptionalDependencyError`) are asserted by string in
  tests, moving it would be a gratuitous public-surface break for no
  functional gain. Public import paths are unaffected by any of this:
  `refigure.docx.convert`/`refigure.xlsx.convert`/`refigure.vlm.
  enhance_docx_markdown` resolve exactly as before (a subpackage's
  `__init__.py` re-exports the same names a flat module used to).
  `docx_groups.py` (group/composite-figure detection, `chart_render.py`'s
  DOCX-side counterpart) deliberately stays a FLAT top-level module
  (`refigure/docx_groups.py`), not nested under `refigure/docx/` — the
  reorg briefly moved it to `refigure/docx/groups.py`, which broke `[vlm]`-
  only installs (importing any submodule of a package always runs that
  package's `__init__.py` first, and `docx/__init__.py`'s own module-level
  `mammoth` guard fired even though `refigure.vlm` needs only `[vlm]`) —
  caught by the extras-isolation CI matrix on the PR implementing stage 4b,
  not by the regular test suite (every extra installed there, structurally
  can't see this class of bug). `xlsx/charts.py` has the identical nesting
  and is fine, because nothing outside `xlsx/` imports it — `docx_groups.py`
  is the one case with a cross-package consumer.
- `refigure/vlm/client.py`'s `OpenAIClient`/`AnthropicClient` (stage 4b
  insertion, PR #12, 2026-08-06) hit the SAME extras-isolation bug class a
  3rd time, a new trigger again (not import-order, not package-nesting):
  `client.py` is a submodule of `refigure.vlm`, so importing it — even just
  to reach `OpenAIClient`, which needs no `pdfplumber` at all — always runs
  `refigure/vlm/__init__.py`'s pdfplumber guard first. Unlike the prior two
  occurrences this ISN'T a bug to route around (`Config.vlm_client`'s only
  real call site, `enhance_docx_markdown`, always renders via `pdfplumber`
  regardless of which client sends the result) — fixed with a
  self-referential extra instead (`vlm-direct = ["refigure[vlm]", "openai
  ...", "anthropic..."]`), declaring the real dependency rather than
  flattening the module out of its package. See `project_extras_isolation_bug`
  memory for all 3 occurrences.
- Same file's `OpenAIClient`/`AnthropicClient` also use a CLASS-level
  (not module-level) variant of the optional-dependency guard below — a
  deliberate adaptation, not a new mechanism: `client.py` hosts multiple
  independent capabilities (`OpenRouterClient` has zero third-party deps
  and must keep working without `openai`/`anthropic` installed), so each
  class does its own `try/except ImportError` inside `__init__` instead
  of one guard at the top of the file.
- Independent per-format submodules (`refigure/docx/__init__.py` imports
  only mammoth+markdownify; `refigure/xlsx/__init__.py` imports only
  openpyxl) — avoids per-dependency try/except gymnastics. The guard must
  run BEFORE any same-package import, not just exist somewhere in the
  file: `xlsx_charts.py` (now `refigure/xlsx/charts.py`) also touches
  openpyxl directly (`get_column_letter`) with no guard of its own — a
  real bug (PR #8) when `xlsx.py` imported it before its own try/except
  ran. Same discipline holds for `chart_render.py`/`docx_groups.py`
  (`refigure/core/chart_render.py`/`refigure/docx_groups.py`) — re-checked
  after the 2026-08-05 package reorg, this time actually verified via the
  extras-isolation CI matrix, not just the regular dev-venv test suite
  (which has every extra installed and cannot catch this class of bug —
  see `project_extras_isolation_bug` memory).
- `refigure/core/chart_data.py`/`chart_render.py` — always installed,
  lxml-only + `mermaidx` optional inside itself.
- Optional-dependency pattern (proven, implemented+tested in the source
  pipeline first): module-level `try/except ImportError` + capability flag +
  `functools.lru_cache`-based warn-once via `logger.warning`. Reuse for
  mammoth/openpyxl/vlm-client — don't invent a new mechanism per dependency.
  `refigure/cli.py` extends the same discipline one step further: its
  per-format dispatch imports `docx`/`xlsx` lazily (inside the call, not
  at module level) so `refigure --help` works in a bare install.
- `refigure/xlsx/__init__.py` serializes `openpyxl.load_workbook()` behind
  a module-level `threading.Lock()` (`_OPENPYXL_LOAD_LOCK`) — openpyxl
  reuses ONE shared `lxml.etree.XMLParser()` across every thread
  internally, a real concurrency bug (not refigure's own code) found via
  PR #9's CI, see `project_openpyxl_concurrent_parser_fragility` memory.
  `refigure/docx/__init__.py` has no such constraint (refigure's own lxml
  usage never shares a parser instance across threads).

## Public API
Rich, not a bare string — full rationale in
`docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md` §3:
- `ConversionResult` dataclass: markdown + warnings + charts_found/rendered +
  groups_found + vlm_used.
- Typed exceptions: `UnsupportedFormatError`, `CorruptArchiveError`,
  `MissingOptionalDependencyError`.
- `strict: bool` — raise vs. degrade-with-warning on a missing capability.
- Input: path or bytes/file-like, not path-only.
- Config/client object, not a kwarg pile.
- Sync by default (the source pipeline's own VLM HTTP client confirmed fully
  sync, not assumed).
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
`docs/` is organized `<category>/<slug>/<slug>-<date>.md` (two-level,
same principle as scopus-search's own `docs/`, adopted 2026-08-05 —
categories so far: `project-meta`, `package-foundation`, `cli`, `vlm`),
not flat. `/tech-spec`/`/feature-workflow` both know this convention.
Foundational, non-stage-specific docs (listed below) live in
`project-meta`; per-stage specs live under their own category.
- `docs/project-meta/converter-viability-assessment/converter-viability-assessment-2026-08-04.md` — market research +
  code-review verdict on whether extraction is worth it.
- `docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md` — scope/architecture/API
  decisions.
- `docs/project-meta/execution-sequence/execution-sequence-2026-08-04.md` — phased plan, dependency graph,
  effort % per stage.
