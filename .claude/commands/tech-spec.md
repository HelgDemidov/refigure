---
description: Turn a feature/refactor/porting task into a structured spec (ТЗ) under docs/
---

Turn a feature/refactor/porting-task request into a structured spec (ТЗ), following this repo's `docs/<category>/<slug>/<slug>-<date>.md` convention (category = thematic grouping, e.g. `vlm`, `cli`, `project-meta`, `package-foundation` — see Step 6).

Take the task description from the user's message (and any command args) as the primary input. Everything below is a DEFAULT — an explicit instruction in that message (different length, different process, different location) overrides it.

## Cross-cutting principle — refigure's architectural invariants

The spec is built against current industry best practices — code robustness, cleanliness, secure development — AND against the architecture decisions already locked in `CLAUDE.md` and `docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md`. This isn't a separate step but a criterion applied at every stage — draft (Step 2), self-critique (Step 3), finalization (Step 4). Concretely, any spec touching converter code must respect:
- **Format isolation**: `refigure/docx/__init__.py` imports only mammoth+markdownify; `refigure/xlsx/__init__.py` imports only openpyxl. Neither imports the other's heavy dependency.
- **Core stays light**: `refigure/core/chart_data.py`/`refigure/core/chart_render.py` import only `lxml` (+ optional `mermaidx` inside `chart_render.py`) — never mammoth/openpyxl.
- **Optional-dependency pattern**: module-level `try/except ImportError` + capability flag + `functools.lru_cache` warn-once via `logger.warning` — reuse the existing pattern, don't invent a new one per dependency.
- **Sync core**: `chart_data`/`chart_render`/`docx`/`xlsx` stay synchronous; async is reserved for a future narrow VLM-batch entry point only, not the whole library.
- **Rich public API**: `ConversionResult` dataclass + typed exceptions + `strict: bool`, not a bare string or raised-kwarg pile.

## Step 1 — Ground the spec in reality

Read the relevant existing code (`refigure/`), related `docs/*.md` (for tone/structure/prior decisions), `CLAUDE.md`, and relevant project memory (`MEMORY.md` index + linked files). Every concrete claim that ends up in the spec — file path, function name, module boundary — must be verified against the actual repo, not assumed.

## Step 2 — Draft (round 1)

Write an initial spec: problem/goal, technical approach, affected files/modules, test coverage needed, commit/PR breakdown. Base it on the user's requirements plus Step 1 research.

## Step 3 — Adversarial self-critique (round 2)

Re-read the round-1 draft as a skeptical senior reviewer, not its author. Look for: wrong assumptions about existing code, missing edge cases, unstated scope boundaries, violations of the invariants above (a stray `import openpyxl` in `docx.py`, a new dependency added without the try/except+capability-flag pattern, an accidental `async def` in core), gaps in the test plan, an unrealistic commit breakdown. List concrete objections, then revise to address each one.

## Step 4 — Verify (round 3, when warranted)

Run a third pass only if round 2 raised material issues or the task is unusually complex. Re-check every file/function reference against the repo (grep/read, not memory), confirm no contradictions between sections, finalize wording.

Only the synthesized final spec goes in the document — never the round-by-round working.

## Step 5 — Assemble the document

Target **≤100 lines**. Exceed only for genuinely high-complexity tasks whose analysis/plan doesn't fit — and say so in one line at the top of the doc if you do.

```
# Спецификация: <feature name>

**Статус:** черновик v1 · <date>
**Ветка:** `<feat|refactor|fix>/<slug>`

## 0. Что и зачем
<problem, motivation, one-line scope boundary>

## 1..N. <technical sections as needed>
<concrete, file/function-referenced, no filler>

## Тестовое покрытие
<new tests this spec requires — bullets>

## План коммитов/PR (10–20 lines)
<numbered roadmap, one line per commit, conventional-commit prefixed>

## Чек-лист реализации
<unchecked boxes mirroring the commit plan — /feature-workflow checks these off>

## Вне скоупа
<explicit exclusions, optional section>
```

Match the tone and terseness of existing `docs/*.md` files — concise, technical, file-referenced, no marketing language. Content in Russian, per `docs/` convention (`CLAUDE.md` §Working language). Do NOT add a "Статус выполнения" section — `/post-merge-sync` appends that after the branch merges.

## Step 6 — Place the file

`docs/<category>/<slug>/<slug>-<date>.md` — two-level structure (category, then a slug subdirectory holding the dated file), the same principle as scopus-search's own `docs/` layout, adopted 2026-08-05. Existing categories: `project-meta` (scope/roadmap/market docs, not stage-specific — e.g. `v1-scope-and-api-design`, `execution-sequence`, `converter-viability-assessment`), `package-foundation` (core skeleton/API extraction — stages 1-2), `cli`, `vlm`. Reuse an existing category when the work fits one; create a new top-level category directory only when it genuinely doesn't (e.g. a future `mcp-server` category for stage 10) — don't force an awkward fit, and don't nest a spec's slug directory under an unrelated category just to avoid creating a new one. Derive `<slug>` from the feature itself, not a copy-paste of the user's raw phrasing — the same slug names both the subdirectory and the file (only the filename gets the `-<date>` suffix, the directory doesn't).

`docs/` is tracked in this repo (deliberate choice, not gitignored scratch space — `CLAUDE.md` §Working language) — the spec gets committed, not left as a local-only file.

## Step 7 — Branch + commit

Skip this step only if the work is trivial enough for a direct commit to `main` — rare, since this command exists for substantial tasks.

1. `git fetch origin && git checkout -b <feat|refactor|fix>/<slug> origin/main` — prefix matches the dominant conventional-commit type of the work.
2. `mkdir -p docs/<category>/<slug>`, then write the spec file there (Step 6).
3. `git add docs/<category>/<slug>/<slug>-<date>.md && git commit -m "docs: draft spec for <slug>"` — English commit message (`CLAUDE.md` §Working language: code/commits/PRs in English, only dialogue and `docs/` content in Russian).
4. `git push -u origin <branch-name>`.

## Step 8 — Report

State the spec path, branch name, and that it's pushed. Do not start implementation — that's `/feature-workflow`.
