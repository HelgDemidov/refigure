---
description: Sync CLAUDE.md, memory, spec status, and README after a PR merges into main
---

Sync project documentation and long-term memory after a successful PR merge into main.

Run `git log main --oneline -15` and `git diff HEAD~1 --stat` to understand what changed.
Then perform ALL of the following steps — in order, without skipping:

## Step 1 — Update CLAUDE.md (repo root)

Read the current `CLAUDE.md`. Update ONLY sections that are factually outdated based on the merged changes:
- New modules landing in `refigure/` (e.g. `docx.py`, `xlsx.py`, `chart_data.py` ported)
- New/changed extras in `pyproject.toml` (`[docx]`, `[xlsx]`, `[vlm]`)
- Changes to commands, dependency versions, or dev tooling
- New architectural constraints or "Do NOT" rules

Do NOT add micropatterns or implementation details — only architectural/infrastructure facts.
Keep the file under 100 lines.

## Step 2 — Update or create memory files

Read the current `MEMORY.md` index at the project memory path.
For each significant change in the merged PR, decide:

- **New subsystem or feature** → create `project_<feature>.md` (type: project)
- **Architectural decision with lasting consequences** → create or update `project_*.md`
- **Testing/tooling lesson learned** → create or update `feedback_*.md`
- **New external resource or reference** → create `reference_*.md`

Memory body structure: lead with the fact/rule, then **Why:** and **How to apply:** lines.
Update `MEMORY.md` index: add one line per new file, update description of changed files.

## Step 3 — Update spec status

If the merge closes a feature branch with a spec at `docs/<slug>-<date>.md`, append a concise §"Статус выполнения" section (10–15 lines) stating: merge date, PR number, commit hashes, what was done, what remains out of scope. `docs/` is tracked here (not gitignored) — this edit is part of Step 5's commit, same as any other doc change.

## Step 4 — Update README.md

Read the current `README.md`. Update ONLY the parts whose content is now factually outdated because of the merged changes (new extra, changed status line, scope change, etc.) — surgical and minimal, don't bloat the file. Preserve the file's existing style, structure, and tone. If the PR's changes touch nothing reflected in README, skip this step.

## Step 5 — Commit documentation changes

Stage the documentation files touched above: `CLAUDE.md`, `README.md`, and the spec file at `docs/<slug>-<date>.md` if Step 3 edited one. Do NOT stage memory files (they live outside the repo). Commit with message:
`docs: sync CLAUDE.md and docs after merging <branch-name>`

Then report a short summary: what was updated and why.
