# Plan C — Execution Progress

This file is the resumable hand-off log for executing `plan-C-strategist-v2.md` via
`superpowers:subagent-driven-development`. It travels with the branch, so any future
session can `git checkout` the branch, read this file alongside the plan, and continue
exactly where the previous session stopped.

**How to read this file:**
- Tasks are listed in plan order (C1 → C16).
- `[ ]` means not started. `[x]` means landed (with the commit SHA).
- "In flight" means a subagent was dispatched but did not finish — re-dispatch fresh.
- The **Chunks** table groups tasks by branch / risk seam. Each chunk is its own branch
  off main and its own short-lived worktree under `.claude/worktrees/`.

---

## Chunks

| Chunk | Tasks | Branch | Status |
|---|---|---|---|
| **Chunk 1 — Strategist-internal foundation** | C1–C6 | `phase4/planC-foundation` | in flight |
| Chunk 2 — Strategist rewrite | C7–C9 | (not started) | — |
| Chunk 3 — Persistence + wiring | C10–C14 | (not started) | — |
| Chunk 4 — Verify | C15–C16 | (not started) | — |

Rationale for the split is in the session notes / conversation; in short, Plan C is
described in the spec as "high risk" because it touches the strategist prompt + agent
+ pipeline + executor + ORM. Splitting along the natural integration seams gives clean
stop-points to review, push, and pause between sessions.

---

## Chunk 1 — `phase4/planC-foundation`

All six tasks here are purely additive: new modules and tests under
`src/agents/strategist/`, plus one field added to `PositionThesis`. Nothing yet imports
the new modules, so the bot still runs identically to the post-Plan-B merge.

**Execution model:** subagent-driven-development. Each task gets one Sonnet implementer
subagent + one Sonnet spec-compliance reviewer + one Sonnet code-quality reviewer. After
all six tasks land, one Opus reviewer audits the chunk as a whole before this branch is
proposed for merge into main.

**Pre-flight:**
- Worktree: `.claude/worktrees/phase4-planC-chunk1` (this directory)
- Branch: `phase4/planC-foundation` off `main @ 19a12b7`
- Venv: symlinked from the main repo's `.venv` (Python 3.14, pydantic 2.13.4)

**Tasks:**

- [ ] **C1** — Add `stance_schema.py` (`TickerStance` model). Plan §C1.
- [ ] **C2** — Add `lifecycle.py` (`derive_lifecycle_action`). Plan §C2.
- [ ] **C3** — Add `PositionThesis.opened_tick_id` field. Plan §C3.
- [ ] **C4** — Add `derivation.py` (`derive_legacy_fields`). Plan §C4.
- [ ] **C5** — Add `held_view.py` (`render_held_positions_view`). Plan §C5.
- [ ] **C6** — Add `evidence_view.py` (render `TickerEvidence`). Plan §C6.
- [ ] **Final review** — Opus audit of all six tasks together.

Each task is committed individually with a Conventional-Commits message; this file is
updated to mark `[x] Cn — <sha>` before the next task is dispatched.

---

## Future chunks (placeholders — do not start until chunk 1 is merged)

### Chunk 2 — `phase4/planC-strategist-rewrite`
- [ ] C7 — Extend `StrategistDecision` with `stances` + `trim_reasons`. Plan §C7.
- [ ] C8 — Rewrite the strategist prompt template. Plan §C8.
- [ ] C9 — Rewrite the strategist agent (callbacks + wiring). Plan §C9.

### Chunk 3 — `phase4/planC-persistence-and-wiring`
- [ ] C10 — Add `TickerStanceRow` ORM + `save_ticker_stance`. Plan §C10.
- [ ] C11 — Add `TradeLogRow.opening_tick_id` / `closing_tick_id`. Plan §C11.
- [ ] C12 — Add `StrategistDecisionWriter` agent. Plan §C12.
- [ ] C13 — Update executor (thesis on BUY, FKs on SELL). Plan §C13.
- [ ] C14 — Wire `StrategistDecisionWriter` into the pipeline. Plan §C14.

### Chunk 4 — `phase4/planC-verify`
- [ ] C15 — Tier 2 LLM-touching smoke (gated by `RUN_LLM_TESTS=1`). Plan §C15.
- [ ] C16 — Final regression pass + graphify delta. Plan §C16.

---

## Session log

A short, append-only log of what happened in each session. New sessions append a dated
entry; do not rewrite history.

### 2026-05-11 — chunk 1 dispatched
- Created branch `phase4/planC-foundation` off `main @ 19a12b7`.
- Created this progress file as the first commit on the branch.
- Dispatching C1 implementer (Sonnet).
