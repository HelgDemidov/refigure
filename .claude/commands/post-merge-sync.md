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

If the merge closes a feature branch with a spec at `docs/<category>/<slug>/<slug>-<date>.md` AND that file still exists in this local checkout (`docs/` is gitignored since 2026-08-19 — the spec was never committed/pushed by `/tech-spec`, so it only exists if you're running this on the same machine that drafted it; if it's missing, skip this step, there's nothing to update), append a concise §"Статус выполнения" section (10–15 lines) stating: merge date, PR number, commit hashes, what was done, what remains out of scope. Like Step 1/2, this edit is made in place on disk but is NEVER staged or committed — it's local-only bookkeeping now, not a shared repo artifact.

## Step 4 — Update README.md

Read the current `README.md`. Update ONLY the parts whose content is now factually outdated because of the merged changes (new extra, changed status line, scope change, etc.) — surgical and minimal, don't bloat the file. Preserve the file's existing style, structure, and tone. If the PR's changes touch nothing reflected in README, skip this step.

## Step 5 — Commit documentation changes

`CLAUDE.md` and the `docs/<category>/<slug>/<slug>-<date>.md` spec file (Steps 1 and 3) are gitignored — never stage or commit them, same rule as memory files (they also live outside git tracking, just for a different reason: memory is outside the repo entirely, `CLAUDE.md`/`docs/` are gitignored inside it). Their edits stay on this machine only and do NOT propagate to other clones/machines by themselves.

The only file from the steps above that can actually be committed is `README.md`, and only if Step 4 changed it. If it did: `git add README.md && git commit -m "docs: sync README after merging <branch-name>"`. If Step 4 was skipped (nothing to update), there is nothing to commit at all — that's a valid, expected outcome of this command, not an error.

Then report a short summary: what was updated (and where each edit lives — committed vs. local-only) and why.
