# refigure — Claude Code memory

## Project
PDF/DOCX/XLSX→Markdown converters extracted from a private pipeline (G2AI_ME),
scoped to one shared PyPI package for **DOCX+XLSX only** — PDF excluded from the
project entirely (crowded market, not a differentiator; see
`docs/converter-viability-assessment-2026-08-04.md`). Core differentiator: native
OOXML chart-data extraction (numCache/strCache, no rasterize/OCR/VLM) + positioned
zero-loss markers for composite figures — market research found this genuinely
absent even among well-funded incumbents (Docling issue #1287, >1yr open, unfixed).

## Status (2026-08-04)
Design phase complete (scope, architecture, API, phased execution plan — all
decided, none implemented yet; no package code exists in this repo). Extraction
source: G2AI_ME, ~1400-1500 lines near-verbatim (`chart_data.py`/`chart_render.py`/
`xlsx_charts.py`/`docx_groups.py`/`zipsafe.py`).

## Scope v1
- DOCX + XLSX, one package, not two.
- VLM composite-figure interpretation (LibreOffice+cloud): ported in parallel,
  gated behind `[vlm]` extra + runtime `use_vlm` toggle — **not active/announced
  in v1**.
- MCP server: **not v1** — v2's primary goal (replaces PDF as the v2 target).
- Full phase/effort breakdown: `docs/execution-sequence-2026-08-04.md` (10 stages,
  dependency graph, %-effort per stage, mermaid-verified before publish).

## Package architecture
- One PyPI package, extras by format+capability: `[docx]`, `[xlsx]`, `[vlm]`.
- Independent per-format submodules (`refigure/docx.py` imports only
  mammoth+markdownify; `refigure/xlsx.py` imports only openpyxl) — avoids
  per-dependency try/except gymnastics.
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
  (e.g. early Russian-language commit messages) — apply rules going forward
  only, don't force-push to rewrite what's already public.

## Source docs (Russian, tracked in git — project documentation, not code)
- `docs/converter-viability-assessment-2026-08-04.md` — market research +
  code-review verdict on whether extraction is worth it.
- `docs/v1-scope-and-api-design-2026-08-04.md` — scope/architecture/API
  decisions.
- `docs/execution-sequence-2026-08-04.md` — phased plan, dependency graph,
  effort % per stage.
