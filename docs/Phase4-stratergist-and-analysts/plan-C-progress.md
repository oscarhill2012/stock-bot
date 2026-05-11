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
| **Chunk 1 — Strategist-internal foundation** | C1–C6 | `phase4/planC-foundation` | ✅ approved by final Opus audit; staged for stacked merge |
| Chunk 2 — Strategist rewrite | C7–C9 | `phase4/planC-strategist-rewrite` (off Chunk 1 tip) | in flight |
| Chunk 3 — Persistence + wiring | C10–C14 | (not started — branches off Chunk 2 tip) | — |
| Chunk 4 — Verify | C15–C16 | (not started — branches off Chunk 3 tip) | — |

**Stacked-branch policy:** Plan C is one coherent rewrite — Chunk 1 alone is dead
code until C9 wires it in. The four chunk branches form a stack (each branches off
the previous chunk's tip, not off main), so `main` never carries unused modules
mid-rewrite. The whole stack merges to `main` as one PR at the end of Chunk 4.

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

- [x] **C1** — Add `stance_schema.py` (`TickerStance` model). Plan §C1. — `a09d614`
- [x] **C2** — Add `lifecycle.py` (`derive_lifecycle_action`). Plan §C2. — `55966c8` (+`e6ac789` docstring fix)
- [x] **C3** — Add `PositionThesis.opened_tick_id` field. Plan §C3. — `79a15ac`
- [x] **C4** — Add `derivation.py` (`derive_legacy_fields`). Plan §C4. — `ef319b3` (+`cd84aa9` docstring clarification)
- [x] **C5** — Add `held_view.py` (`render_held_positions_view`). Plan §C5. — `4d427ba` (+`f82f26d` polish)
- [x] **C6** — Add `evidence_view.py` (render `TickerEvidence`). Plan §C6. — `0c8cc68` (+`de2dd22` polish)
- [x] **Final review** — Opus audit of all six tasks together. ✅ **Approved.** No Critical or Important issues; six Minor (none blocking) and six seam-notes for Chunk 2 recorded in the session log below.

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

### 2026-05-11 — C1 landed (`a09d614`)
- Spec compliance: ✅ — schema fields, constraints, and 9 required tests match spec exactly. Test path `tests/unit/agents/strategist/` chosen over the plan's `tests/unit/strategist/` to match the repo's existing `tests/unit/agents/analysts/` convention; authorised deviation.
- Code quality: ⚠️ approved with minor issues. One Important finding (ticker field unvalidated — accepts `""` or whitespace) **deferred**: the plan and Plan A's `AnalystEvidence`/`TickerEvidence` schemas all spec `ticker: str` bare. Tightening it here without doing so across the family creates a one-off inconsistency. **Backlog candidate**: introduce a shared `Ticker` type alias (e.g. `Annotated[str, Field(min_length=1, pattern=...)]`) and apply across `contract/` and `strategist/` in one pass — out of scope for chunk 1.
- Three Minor cosmetic findings noted and not actioned (test assertion completeness, unrealistic `catalyst="Q3"` value, module-docstring brevity). All would be trivial follow-ups if the file ever opens for another reason.

### 2026-05-11 — C2 landed (`55966c8` + `e6ac789`)
- Spec compliance: ✅ — five-branch lifecycle math implemented exactly as specified; all 10 required tests present and passing.
- Code quality: ⚠️ approved with one Important docstring defect (test_close_at_exact_epsilon_boundary docstring described the case as "close" while asserting "hold"). The wording came from the plan's literal Python snippet — a plan-level wording defect rather than an implementation oversight. Controller applied the reviewer's verbatim suggested fix directly (`e6ac789`) rather than spinning up another implementer + 2 reviewers for a 1-line docstring edit. Tests still pass (22/22 across C1+C2).
- One Minor style note noted, not actioned: the inner `if held and wants_held:` guard is technically redundant given the preceding early returns, but the inline comment explains it and the structure aids readability. Leaving as-is.

### 2026-05-11 — C3 landed (`79a15ac`)
- Spec compliance: ✅ — one-line additive field on `PositionThesis` with `str = ""` default; 2 tests assert default and JSON round-trip. Strategist test suite at 24 green.
- Code quality: ✅ approved (no issues). Field placement, inline comment scope, and `datetime.UTC`/UP017 usage all clean.
- Authorised deviation noted: implementer used `datetime.UTC` (Python 3.11+ shortcut) instead of the plan's `timezone.utc` for ruff UP017 compliance. Functionally identical.

### 2026-05-11 — Chunk 1 final Opus audit ✅ approved

Cross-task audit of the six new modules + the `opened_tick_id` schema field.
Empirical baseline: 48/48 strategist tests green; 298/298 full suite green;
ruff clean; main repo working tree clean (no stray graphify writes anywhere).

**No Critical, no Important findings.** Six Minor items — none blocking:

1. **Docstring style is split** between Google `Args:`/`Returns:` (C1, C5) and NumPy
   `Parameters\n----------` (C2, C4, C6). Pick one in a follow-up; both work.
2. `held_view.py:154` silently swallows corrupt-thesis exceptions. Add a
   `logging.warning` when central logging lands in C9.
3. `derivation.py:136` defaults `horizon` to a magic literal `"swing"`. Promote
   to a shared `DEFAULT_HORIZON: Final` when C9 introduces one.
4. `stance_schema.py:32` docstring mentions a "risk-gate clamp" alongside the
   pydantic `[0.0, 1.0]` bound — readers in isolation may think there are two
   clamps; clarify wording when next opening the file.
5. `evidence_view.py:57` hard-codes the four-analyst tuple. Replace with
   `typing.get_args(AnalystName)` when the catalogue next grows.
6. British/US spelling check passed cleanly — no regressions.

**Six seam-notes for Chunk 2 (C7-C9) wiring:**

- `derive_legacy_fields(stances, ctx)` requires `ctx.current_weights` from
  `portfolio.current_weights()`, NOT from `state["positions"].keys()` (the
  pre-Plan-C strategist agent uses stubs).
- The C9 after-callback MUST reject `open` stances with `horizon is None`
  *before* calling `derive_legacy_fields`, or the `"swing"` fallback silently
  applies. Add a callback test.
- `held_view.py:99` price-unavailable check looks for `pos is None` or
  `pos.last_price <= 0`. Confirm the executor (C13) never emits a sentinel
  like `-1` for missing-tick prices.
- No test currently covers the `derivation → held_view` seam (open a position
  on tick N, see it render on tick N+1). Add one integration test in C9.
- `_format_features` uses `:.3g`, which renders `5.0` as `5`. Confirm
  acceptable to the spec author; likely fine.
- `PositionThesis.opened_tick_id` has two writers in the plan: `derivation.py:142`
  (C9 path) and the C13 executor (`schema.py:25` comment). Reconcile when C13
  is written — one writer, not both.

**Verdict:** Chunk 1 is ready. Plan C is one integrated rewrite, so the four
chunk branches stack rather than merging to `main` independently — Chunk 2
branches off the tip of `phase4/planC-foundation`. The cumulative stack merges
to `main` as one PR after Chunk 4 verifies the whole thing.

### 2026-05-11 — C6 landed (`0c8cc68` + `de2dd22`)
- Spec compliance: ✅ — `render_ticker_evidence` + two private helpers exactly as specified; the six required tests all present and pass. Strategist regression at 48/48 green. Three pre-authorised ruff deviations applied (UP035 `from collections.abc import Iterable`, UP017 `from datetime import UTC`, F401 dropped unused `import pytest`). Spec reviewer noted that the implementer replaced the plan's filter-comprehension idiom for the optional summary line with a clean `if agg.summary: block.append(...)` — semantically identical and arguably more readable; not flagged as a deviation.
- Code quality: ⚠️ approved with issues; two Important and two Minor actioned via controller Edit (`de2dd22`):
  - Important #1 — silent rationale truncation: `rationale[:60]` quietly dropped up to 100 chars; the renderer now appends `…` whenever it shortens the text so neither the LLM nor a human reader is fooled into treating a clipped sentence as complete. Plan said `[:60]` literally; this is a fourth authorised deviation (compactness intent preserved; only the cut signal is new).
  - Important #2 — `(missing)` branch had no test coverage; added `test_missing_analyst_renders_placeholder`. Also added `test_long_rationale_is_truncated_with_ellipsis` to cover the new ellipsis behaviour. Test count now 8 (still ≤8 cap).
  - Minor — tightened `test_empty_evidence_renders_placeholder` to assert exact equality on the stable sentinel string, and tightened `test_disagreement_rendered` to assert on the numeric value rather than the always-present `disagreement` label (the latter was tautological as written).
  - Two Minor findings declined: `__all__` declaration (not a convention used elsewhere in `src/agents/strategist/*.py`) and a fixture-docstring wording tweak (taste).
- Implementer report was clean this time — no spurious `graphify-out/` writes mentioned. The hallucination pattern from C2–C5 did not recur.

### 2026-05-11 — C5 landed (`4d427ba` + `f82f26d`)
- Spec compliance: ✅ — `render_held_positions_view(positions, portfolio)` accepts both `PositionThesis` instances and `model_dump(mode="json")` dicts; renders the multi-line Ticker / Opened / Why / Aim / Horizon / Catalyst / Now block specified in §C5; total (never raises); empty/flat → sentinel string; corrupt entries silently skipped. All 9 required tests present; strategist regression at 40/40 green.
- Code quality: ⚠️ approved with three Minor issues. Two actioned via controller Edit (`f82f26d`): (1) added a clarifying comment on the `Opened:` line explaining why `curr_weight` is also rendered there; (2) the `_thesis()` test fixture's `opened_tag` now derives from the ticker parameter (`f"open_{ticker.lower()}"`) so the MSFT case no longer carries `"open_aapl"`. Third Minor declined (`"+5" in out` → `"+5.00"`) — matter of taste.
- Pattern recurrence: implementer's status report again claimed to write to `graphify-out/graph_delta.md` in the main repo; verified main repo working tree clean, no actual writes. Same hallucinated side effect as C2/C3/C4 — committed work remains clean.

### 2026-05-11 — C4 landed (`ef319b3` + `cd84aa9`)
- Spec compliance: ✅ — `TickContext`/`DerivedFields` frozen dataclasses + pure `derive_legacy_fields` function exactly as specified. All six required tests present, plus one implementer-added test for the `add` lifecycle branch (test count = 7, within ≤8 cap). Strategist regression at 31/31 green.
- Code quality: ⚠️ approved with two Minor issues. One actioned (`cd84aa9` adds a Note to `DerivedFields` docstring explaining that `frozen=True` doesn't deep-freeze dict contents — read-by-convention). One declined (multi-stance test could assert PositionThesis fields, not just membership — covered elsewhere; opportunistic).
- Authorised deviations: `from collections.abc import Iterable` (UP035), `datetime.UTC` (UP017), removed unused `DerivedFields` import from test file (F401).
