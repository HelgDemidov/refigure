---
description: Audit every checkable claim in CLAUDE.md and memory against live code
---

# /memory-sync — audit and synchronize project memory against the real repo state

Recurring, not a one-off: bring every checkable claim in `CLAUDE.md` and the memory directory in line with the live code and history. Run manually, periodically, or before a large batch of work that will lean on memory being correct. Run in a fresh session, this task alone.

This is an audit of **truth, not completeness** — do NOT add new facts (that's `/post-merge-sync`'s job), only fix stale/false claims and flag unverifiable ones. The evidence discipline below is mandatory because fact-checking by hand at this volume is impossible.

Paths: memory at the harness-provided project memory directory (the session states the resolved path — never hard-code one, it differs per machine/checkout). Audit working files at `.claude/memory-sync/` (worklist, last-run record, backup archive) — created on first run; its absence means no audit has ever happened. This lives under `.claude/`, not `docs/`, because `docs/` is tracked/public here (`CLAUDE.md` §Working language) and these are ephemeral run artifacts, not project documentation — `.gitignore`'s `.claude/*` rule keeps it untracked automatically.

## 1. Sources of truth (descending priority)

1. **Live code:** `refigure/`, `tests/`, build/tool config (`pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.python-version`), `.gitignore`, and `.claude/commands/*.md`.
2. **Version control:** `git log`, merge history, PR metadata (`gh`).
3. **Filesystem:** path existence, directory layout, generated artifacts.

Note: `.claude/commands/*.md` IS tracked in this repo (`.gitignore` allow-lists it, unlike some sibling repos where it's local-only) — still a source of truth either way.

NOT sources of truth — they're the patients, or derivatives: `CLAUDE.md`, memory files, and `docs/` (has its own sync via `/post-merge-sync`). Do not edit those as evidence. Exception: a memory claim ABOUT a document ("the spec lives at path X") — then that file IS the evidence.

⚠ **Context contamination:** your context already contains `CLAUDE.md` and `MEMORY.md` at session start. Before audit, treat both as unverified — only fresh command output counts as evidence.

Sync direction is one-way: **code → memory.** Never "fix" code or tests to match memory.

## 2. Evidence discipline

- Every fix needs TWO pieces of evidence: disproof of the old claim + confirmation of the new one (path+line, commit hash, or command output). Re-verify the new text after fixing — don't just verify the defect.
- Verification means RUNNING a command, not recalling one.
- **Absence claims** ("no such consumer exists") need 2 independent search phrasings before confirming. One empty search isn't proof.
- If code confirms neither old nor new phrasing, don't invent one — mark unresolved, add a dated "not confirmed by code" note, move on.
- Don't trust "most recently modified file wins" when two memory entries conflict — a live check overrides the timestamp heuristic even against it.
- Keep evidence terse and in-flight: a short parenthetical in the worklist, full pairs in the final summary. No second persisted findings file.

## 3. Claim taxonomy and edit rights

| Class | Example | Verification | Right |
|---|---|---|---|
| C1 existence | "class `ConversionResult` in `refigure/docx.py`" | search | edit freely |
| C2 value | "ruff line-length is 100", "requires-python is >=3.10" | search for the exact value | edit freely |
| C3 status | "merged in PR #12", "chart_data.py ported in commit abc123" | `git log`, `gh pr` | edit freely |
| C4 history | "extraction estimated at ~1400-1500 lines before porting" | code can't disprove it; only check internal consistency | do NOT rewrite; flag only on direct contradiction with a later dated entry |
| C5 norm/rule | "docx.py never imports openpyxl" | code illustrates, doesn't disprove | do NOT rewrite; on conflict the later DATED entry wins; ambiguous dating → unresolved |

## 4. Edit rights per memory type

- **`user`** — do not touch the body.
- **`feedback`** — the lesson/Why is untouchable; audit only anchors (paths, functions, flags, commands). Dead anchor → update it or append "(mechanism retired; principle still holds)". Deleting/weakening a feedback entry is forbidden.
- **Stale run measurements** (durations, "measured Xs") that are illustrative colour, not the point — delete on sight, even inside a `feedback` body (the one exception to the untouchable-body rule). Keep a measurement only when the value itself is the evidence for a decision/threshold. When in doubt, keep.
- **`project`** — full C1–C3 audit, conservative on C4–C5. Rationale/rejected alternatives are C4.
- **`reference`** — out of scope; verifying external resources is a separate task.
- Autonomously deleting a memory file is **forbidden** — flag as a deletion/compression candidate, user decides.
- **Anti-bloat:** post-edit file size must not exceed the original by more than ~10%. If an honest fix needs more, flag unresolved instead of bloating. Copying prose between `CLAUDE.md` and memory (either direction) is forbidden. Don't hedge a claim that was just confirmed by code.
- **Replace in place, never append a correction** — memory has no natural pressure to shrink, so every accepted append compounds silently until the next audit catches it. Rewrite stale C1–C3 sentences to state the current truth directly, never beside the old wording. If the surrounding paragraph is built around a now-stale premise (not just one wrong value), that's a **compression candidate** — flag it, don't patch a correction around it, don't execute the compression yourself.
- **Pointer over copy:** content fully derivable from code or `CLAUDE.md` → flag as a compress-to-pointer candidate, don't compress it yourself.
- Leave metadata untouched except the one-line description, and only if body edits made it inaccurate.

## 5. Execution order (checkpoint after each file)

**Phase 0 — safety net.** Runtime state for one run only — don't let it accumulate across runs.
1. **Backup, one-generation rotation — exactly 2 archives on disk, never more/fewer.** One compressed archive of the memory directory, never a loose directory. To inspect: extract to a temp dir, do the work, re-archive — never leave it extracted between operations. New run: delete the older archive, demote the current one, snapshot fresh. Resuming an interrupted run of this same session: leave it untouched. No dates in the filename.
   *Why:* memory lives outside version control — without a backup, edits are irreversible, and one copy means a second bad run in a row erases the only safety net.
2. **Worklist.** Prior run fully complete → overwrite from scratch. Pending items remain → this is a resume, continue from the first pending item.
3. Update the worklist after every file — must survive context compaction or a dropped connection. Re-passing over a done file is safe (idempotent).

**Phase A — `CLAUDE.md`** (heaviest, go first). Section by section, verify every C1–C3 claim. Historical asides are fine as long as their bottom line about the present is correct. Do NOT restructure or restyle — targeted factual fixes only.

**Phase B — `project` memory.** Build the file list from `MEMORY.md` AND a directory listing — an index/directory mismatch is itself a Phase D finding.

**Phase C — `feedback`, `user`, `reference`** — anchor audit per §4.

**Phase C2 (optional, budget permitting) — `.claude/commands/*.md`.** Anchor audit only, same rule as `feedback`: never rewrite a command's protocol body, only fix dead paths/anchors.

**Phase D — cross-consistency:** (1) every `[[cross-link]]` resolves to an existing entry — a broken link is a flag, not a deletion; (2) `MEMORY.md` matches the actual file contents; (3) entry contradictions resolved by rule C5.

**Phase E — wrap-up:** (1) compose the summary directly in the reply — N checked, M fixed with evidence pairs, plus unresolved/warning/compression-candidate lists; (2) show the user that summary and the `CLAUDE.md` diff; (3) on confirmation, commit ONLY `CLAUDE.md` — memory is never committed (it lives outside the repo); (4) write the last-run record (date + current `main` commit); (5) do not delete the backup — rotation in Phase 0 is the only size control, it stays until the next run. Leave the worklist as-is for the next run to overwrite. The final reply IS the durable record — no on-disk duplicate.

## 6. Environment constraints

- Read-only work: no full test suite, no expensive computations, no network calls beyond `gh`/GitHub API metadata. Cheap validators and collection-only test runs are fine.
- Subagents only with explicit user approval (`CLAUDE.md` §Do NOT).

## 7. Out of scope

Adding new facts; restructuring `CLAUDE.md`; verifying external references; editing code, tests, or `docs/`; de-duplicating the backlog against `CLAUDE.md` (a separate task depending on this audit's outcome).

## 8. Definition of done

Every worklist item closed or explicitly flagged with an inline reason; `CLAUDE.md` committed after confirmation; the last-run record written; the reply carries the full summary with evidence pairs and the flagged list. The backup stays on disk per the rotation rule.
