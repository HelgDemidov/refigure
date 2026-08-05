---
description: Pick up a spec drafted by /tech-spec and implement it end-to-end on its branch
---

Pick up a spec (ТЗ) drafted by `/tech-spec` that hasn't been implemented yet, and implement it end-to-end on its feature branch.

## Step 1 — Find the target spec

If invoked with an argument (slug, branch name, or path), use that spec directly.

Otherwise scan `docs/**/*.md` (recursive — specs live under a `docs/<category>/<slug>/` subdirectory, not flat, since 2026-08-05) for specs that are BOTH: missing a `## Статус выполнения` section, AND whose declared branch (the `**Ветка:**` line) still exists — local or `origin/` — and isn't merged into `main`. The `**Ветка:**` line is what distinguishes an actual spec from a general design doc (like `docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md`), which has none. This two-signal check avoids false positives on old completed specs whose status section was never backfilled.

- Zero matches → report nothing pending, stop.
- One match → proceed with it.
- Multiple matches → ask the user which one (AskUserQuestion) — don't guess.

## Step 2 — Enter the branch

`git fetch origin`, then check out the spec's branch locally (create a local tracking branch from `origin/<branch>` if none exists yet). Confirm it's pushed to `origin` — push if it somehow isn't (normally `/tech-spec` Step 7 already did this; this is just a safety net for resuming in a fresh session).

## Step 3 — Build the task list

Read the spec's "План коммитов/PR" section and turn it into a task list in this conversation, one task per planned commit.

If the spec has no commit breakdown (only a final-PR description), split the work into commits yourself — one coherent, independently buildable unit of change per commit (natural seams: a single module ported, its tests, the public-API wiring, docs).

The task list must include dedicated test-coverage tasks for the new functionality, per the spec's "Тестовое покрытие" section — tests are commits like any other, not an afterthought.

## Step 4 — Implement

Work through the task list commit by commit: implement, run the relevant checks (`ruff check`, `mypy refigure`, targeted `pytest`) for the files touched, commit with a conventional-commit message (English, per `CLAUDE.md` §Working language), check off the matching box in the spec's "Чек-лист реализации". Don't batch unrelated work into one commit.

When touching converter code, watch the architectural invariants from `CLAUDE.md`/`docs/project-meta/v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md`: `docx.py`/`xlsx.py` stay independent of each other's heavy dependency, `chart_data.py`/`chart_render.py` stay `lxml`-only, new optional dependencies go through the try/except+capability-flag+warn-once pattern (not ad hoc), core stays synchronous.

**Full-suite budget:** run the complete, unfiltered check (`ruff check .`, `ruff format --check .`, `mypy refigure`, full `pytest --cov`) **at most once per commit**, immediately before that commit — plus one extra full run right before opening the PR (Step 5). Everywhere else *within* a commit's work-in-progress cycle (after each edit, while iterating on a fix), run only targeted/selective checks scoped to the files actually touched — a single test file, `ruff check <path>` — not a full run each time. This keeps the iteration loop fast; the full sweep exists to catch cross-file regressions right before they get baked into a commit or a PR.

## Step 5 — Wrap up

Once all planned commits land: run the full-suite check one last time (the second and final use of the Step 4 budget for this feature), confirm everything passes, then push and open the PR (`gh pr create`) with a body summarizing the spec's goal/scope (§0) and a test-plan checklist — and a link to the spec's `docs/<category>/<slug>/<slug>-<date>.md` path, since `docs/` is tracked here and the spec will be in the diff (unlike a gitignored-docs setup). Report the PR URL.

Do not merge the PR yourself, and do not run `/post-merge-sync` — those happen after human review, as a separate step.
