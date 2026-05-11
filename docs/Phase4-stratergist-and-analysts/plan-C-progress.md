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
| Chunk 2 — Strategist rewrite | C7–C9 | `phase4/planC-strategist-rewrite` (off Chunk 1 tip) | ✅ audited; ready for Chunk 3 stack |
| Chunk 3 — Persistence + wiring | C10–C14 | `phase4/planC-persistence-and-wiring` (off Chunk 2 tip `c0136f7`) | ✅ audited; ready for Chunk 4 stack |
| Chunk 4 — Verify | C15–C16 | `phase4/planC-verify` (off Chunk 3 tip `54bbb65`) | in flight |

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

## Chunk 2 — `phase4/planC-strategist-rewrite`

Chunk 2 is the strategist rewrite that *uses* the Chunk 1 substrate (stance
schema, lifecycle helper, derivation, held-view, evidence-view). C7 extends
`StrategistDecision`; C8 rewrites the prompt template to consume the new
slots; C9 rewrites the agent and its callbacks to wire derivation +
rendering into the ADK pipeline.

**Pre-flight (Chunk 2):**
- Worktree: `.claude/worktrees/phase4-planC-chunk2`
- Branch: `phase4/planC-strategist-rewrite` off `phase4/planC-foundation @ ba4680a` (Chunk 1 tip)
- Venv: symlinked from main repo's `.venv`

**Tasks:**

- [x] C7 — Extend `StrategistDecision` with `stances` + `trim_reasons`. Plan §C7. — `4de5c74` (+`814a64c` polish)
- [x] C8 — Rewrite the strategist prompt template. Plan §C8. — `8fe0d66` (+`208270e` polish)
- [x] C9 — Rewrite the strategist agent (callbacks + wiring). Plan §C9. — `e6b395a` (+`8f03bc4` legacy-test cleanup, `033dd40` polish)
- [x] **Final review** — Opus cross-task audit of C7-C9. ✅ approved with 5 non-blocking follow-ups (see session log).

---

## Chunk 3 — `phase4/planC-persistence-and-wiring`

**Branch:** off Chunk 2 tip (`c0136f7`).
**Worktree:** `.claude/worktrees/phase4-planC-chunk3`
**Venv:** symlinked from main repo's `.venv`

**Scope:** Persists strategist v2 output and wires it into the pipeline. C10 adds a per-stance
ORM row + save helper; C11 adds tick FKs on the trade log; C12 adds a
`StrategistDecisionWriter` agent that uses C10/C11; C13 updates the executor to write
`PositionThesis` on BUY and populate the FKs; C14 wires the new writer into the pipeline
and addresses the Chunk 2 audit follow-up by seeding `state["portfolio"]` before the
strategist runs.

**Tasks:**

- [x] C10 — Add `TickerStanceRow` ORM + `save_ticker_stance`. Plan §C10. — `eb2d53a` (+`1993e4b` polish)
- [x] C11 — Add `TradeLogRow.opening_tick_id` / `closing_tick_id`. Plan §C11. — `da006fc`
- [x] C12 — Add `StrategistDecisionWriter` agent. Plan §C12. — `ca1c283`
- [x] C13 — Update executor (thesis on BUY, FKs on SELL). Plan §C13. — `1c2fffb`
- [x] C14 — Wire `StrategistDecisionWriter` into the pipeline + seed `state["portfolio"]`. Plan §C14. — `9c482d6` (+`5bd6567` polish)
- [x] **Final review** — Opus cross-task audit of C10-C14. ✅ approved with 8 non-blocking follow-ups (see session log).

---

## Chunk 4 — `phase4/planC-verify`

**Branch:** off Chunk 3 tip (`54bbb65`).
**Worktree:** `.claude/worktrees/phase4-planC-chunk4`
**Venv:** symlinked from main repo's `.venv`

**Scope:** Verify the full Plan C stack. C15 adds a gated LLM-touching smoke test for
the live strategist (skipped by default; runs only when `RUN_LLM_TESTS=1`). C16 runs
the final regression pass, cleans up any Plan-C-introduced ruff debt (including the
3 carry-overs on `persistence.py` per the Chunk 3 audit follow-up #1), and appends
the Plan C entry to `graphify-out/graph_delta.md`. After C16 lands, the whole
chunk-1 → chunk-2 → chunk-3 → chunk-4 stack merges to `main` as one PR.

**Tasks:**

- [x] C15 — Tier 2 LLM-touching smoke (gated by `RUN_LLM_TESTS=1`). Plan §C15. **Commits:** `7e4dce9` (initial) + `80b4cb1` (spec fix: add `@pytest.mark.integration`).
- [x] C16 — Final regression pass + ruff cleanup + graphify delta. Plan §C16. **Commits:** `4da2cbc` (ruff ×3 on `persistence.py`) + `b625c95` (graphify delta) + `b79f223` (`__import__` polish).
- [ ] **Final review** — Opus cross-task audit of C15-C16 (smoke gating + regression cleanliness).

---

## Session log

### 2026-05-11 — C16 complete (final regression + ruff cleanup + graphify delta)

Chunk 4's final implementer pass. Three commits, all narrowly scoped:

- **`4da2cbc chore(persistence): clean up 3 pre-existing ruff violations`** —
  resolves the Chunk 3 audit follow-up #1 carry-overs on
  `src/orchestrator/persistence.py`: dropped unused `JSON` import (F401 line 8),
  dropped unused top-level `BufferEntry` import (F401 line 50 — the function-local
  re-import inside `load_recent_buffer` is the real usage and remains), and
  replaced the local `from datetime import timezone` + `timezone.utc` inside
  `save_attribution_signal` with the module-level `UTC` alias (UP017).
- **`b625c95 docs(graph): record Plan C strategist v2 architectural delta`** —
  appended the dated section to `graphify-out/graph_delta.md` covering
  TickerStance / TickerEvidence / decision-writer / 8-stage pipeline /
  executor BUY+SELL changes. `graphify-out/` is gitignored so `git add -f`
  was required; this matches the precedent set by `05eb354` (provider-shell
  refactor delta). Total file size after append: 166 lines (under the
  200-line rebuild threshold from CLAUDE.md).
- **`b79f223 polish(persistence): replace stale __import__ pattern`** —
  raised by the code-quality reviewer: `save_portfolio_snapshot` had
  `__import__("datetime").datetime.now(__import__("datetime").timezone.utc)`
  as a default-factory expression. Pre-existing (predates Plan C) but
  inconsistent with the cleaned-up imports; replaced with plain
  `datetime.now(tz=UTC)`. Not a ruff violation either before or after — pure
  legibility fix while the file was open.

**Regression baseline:** `.venv/bin/python -m pytest tests/ -q` →
**329 passed, 1 skipped, 4 warnings in 228s** (skip is the C15 LLM smoke).
Identical to the pre-C16 baseline; no regressions introduced. Ruff on
`src/orchestrator/persistence.py` and the four chunk-4-touched paths: clean.

**Reviews:**
- **Spec compliance (Sonnet):** ✅ APPROVED. All five spec steps satisfied;
  test/ruff/delta/commit-hygiene all match.
- **Code quality (Sonnet):** ❌ initially — flagged the `__import__` pattern.
  Fixed in `b79f223`. No further issues raised.

### 2026-05-11 — C15 complete (LLM-touching smoke; gated by `RUN_LLM_TESTS`)

Added `tests/integration/test_strategist_v2_smoke.py` (222 lines) — one
`async def` test that seeds a two-ticker watchlist (AAPL held + MSFT new),
constructs per-analyst `AnalystEvidence` for both tickers across all four
dimensions, runs the real strategist via `Runner.run_async` over
`InMemorySessionService`, and validates the round-tripped
`strategist_decision` against `StrategistDecision` — asserting exhaustive
stances and target-weight coverage.

**Authorised deviations from plan reference code:**
- Async-rewritten Runner usage (`runner.run_async` + `await
  session_service.create_session(...)`) mirroring `tick.py`'s ADK 1.32 pattern.
- `from datetime import UTC` alias (project UP017 convention).

**Skip-behaviour proven:** `1 skipped` without `RUN_LLM_TESTS=1`. With the
env var set the test runs to assertion stage but the local environment has
no Gemini creds wired in, so it surfaces `AttributeError: 'BaseApiClient'
object has no attribute '_async_httpx_client'` — a credentials issue, not a
code defect. Documented in the module docstring.

**Reviews:**
- **Spec compliance (Sonnet):** flagged missing `@pytest.mark.integration`
  decorator (plan §C15 line 2691). Controller-applied one-line fix as
  `80b4cb1`. Re-verified: skip behaviour intact, ruff clean.
- **Code quality (Sonnet):** ✅ APPROVED. Three advisory observations
  (vacuous `except (AttributeError, BaseException)` tuple at line 186;
  cosmetic column-alignment in `initial_state` dict; missing param doc on
  inner helper) — all non-actionable.

**Regression check:** 314 unit tests pass; one integration test skipped.
No regressions.

### 2026-05-11 — Chunk 3 final Opus audit ✅ approved

Cross-task audit of C10-C14 together (Opus model). Empirical baseline:
**329/329 full project suite green**; ruff on the eight chunk-3-touched paths
(`src/agents/strategist/decision_writer.py`, `src/agents/executor/agent.py`,
`src/orchestrator/pipeline.py`, `src/orchestrator/tick.py`, plus four test
trees) **All checks passed**; the three known carry-over violations on
`src/orchestrator/persistence.py` (F401 line 8, F401 line 50, UP017 line 261)
remain unchanged. Diff footprint: **15 files, +871/-40**.

**No Critical, no Important findings.** The end-to-end story holds:

- Tick → `_build_initial_state` seeds `state["portfolio"]` via
  `broker.get_portfolio().model_dump(mode="json")` (`tick.py:36`).
- Strategist's `_composite_before_callback` calls `_coerce_portfolio`
  (`agent.py:23-38`), which round-trips the dict via
  `Portfolio.model_validate(value)`. `cash` (float) and `positions`
  (dict[str, Position]) are both natively round-trippable — verified.
- Strategist's `_strategist_validation_callback` (`agent.py:268-275`)
  derives `new_positions` via `derive_legacy_fields(decision.stances, ctx)`
  with `ctx.tick_id=state["tick_id"]`, populating
  `PositionThesis.opened_tick_id` (`derivation.py:142`); the validated
  decision is then re-dumped to `state["strategist_decision"]`.
- `StrategistDecisionWriter` (`decision_writer.py:87-97`) iterates
  `decision.stances`, calls `derive_lifecycle_action(curr, preferred_weight)`,
  and persists each via `save_ticker_stance(stance=stance.model_dump(mode="json"), ...)`.
  Dict-shape contract verified: `TickerStance` exposes `ticker`/`preferred_weight`/`conviction`/`rationale` (required) and `horizon`/`target_price`/`stop_price`/`catalyst`/`close_reason`/`trim_reason` (optional) — exactly what
  `save_ticker_stance` reads via `[...]` / `.get(...)`.
- Executor BUY (`agent.py:71-75`) copies `state["strategist_decision"]["new_positions"][ticker]` (a `PositionThesis` JSON dump that includes `opened_tick_id`) into `state["positions"][ticker]`.
- Executor SELL (`agent.py:78-128`) reads `thesis.get("opened_tick_id")` and
  passes `state["tick_id"]` as `closing_tick_id`. The 15-key dict matches
  `TradeLogRow` exactly, including the two new C11 columns. The
  `opened_at` string→`datetime` coercion at `agent.py:92-96` correctly
  guards the JSON-state path.
- Stage ordering at `pipeline.py:67-76`: `StrategistDecisionWriter` sits at
  index 3, between `Strategist` (index 2) and `RiskGate` (index 4). Intent
  is therefore persisted even if `RiskGate` raises a `StrategistContractViolation` — the desired audit-trail invariant.

**Cross-task contract sanity-checks all passed.** The chain from a real
`StrategistDecisionWriter`-persisted stance to a downstream `TradeLogRow` is
exercised piecewise — unit tests cover each seam — but no single
integration-level test stitches the full BUY→SELL round-trip across multiple
ticks with `opened_tick_id` flowing from the writer through the executor.
Acceptable for Chunk 3; flagged for Chunk 4.

**Non-blocking follow-ups for Chunk 4 / backlog:**

1. **Persistence.py ruff debt** (3 known carry-overs: `JSON` F401, `BufferEntry` F401, `UP017` on `timezone.utc`). All pre-Chunk-3; tidy in a standalone `chore(persistence)` commit before merge or fold into C16 regression pass.
2. **`validate_lifecycle_contract` orphan** in `src/agents/risk_gate/lifecycle.py` — still unused; carries forward from Chunk 2 audit follow-up #2.
3. **`tick_id` fallback to `recorded_at`** at `agent.py:200` — Chunk 2 follow-up #3, still present.
4. **Duplicate-ticker silent dedupe** in the "no extras" check — Chunk 2 follow-up #4, still present.
5. **Module-level `strategist_agent` singleton** in `agent.py:299-310` is now provably unused by the pipeline (C14 builds the `LlmAgent` inline). Decide in Chunk 4 whether to remove or keep as a public convenience.
6. **No full BUY→SELL integration round-trip test** that exercises the chain `StrategistDecisionWriter → executor.BUY → next tick → executor.SELL → TradeLogRow with opening_tick_id from a real prior write`. The seams are individually covered (`test_decision_writer.py`, `test_open_positions_state.py`, `test_trade_log_tick_id_fks.py`) but never stitched. Good candidate for C16.
7. **Generator-gate style drift:** `decision_writer.py:48,56,101` use `return; yield` for the no-event generator-protocol gate, whereas sibling writers under `attribution/` use `if False: yield`. Functionally identical; harmonise alongside the C12-noted UP035 sweep.
8. **Stale comment in `persistence.py:94`** mentions "executor when opening/closing" — accurate, but the FK is now also set by the strategist callback path (via `opened_tick_id` on `PositionThesis`). Minor wording polish.

**Chunk 3 is feature-complete and audit-approved.** Stays on its own branch
(`phase4/planC-persistence-and-wiring`, tip `eb1a94b`) per the stacked-branch
policy. Chunk 4 (C15-C16) will branch off this tip; no merge to main yet.

### 2026-05-11 — C14 landed (`9c482d6` + `5bd6567` polish) — Chunk 3 feature-complete
- **Pipeline wiring (Part 1):** `build_strategist_decision_writer(db_session)` inserted between `_build_strategist()` and `RiskGateAgent(broker=broker)` in `src/orchestrator/pipeline.py`. Pipeline is now 8 stages. `_build_strategist()` engages the v2 callbacks — adds `before_agent_callback=_composite_before_callback` alongside the pre-existing `after_agent_callback=_strategist_validation_callback`. **Model preserved at `gemini-2.5-pro`** (NOT plan's `gemini-2.0-pro-001` — C9 audit decision).
- **Portfolio seeding (Part 2 — Chunk 2 audit follow-up resolved):** `src/orchestrator/tick.py` extracted a module-scope async `_build_initial_state(broker, tick_id, tickers)` helper that reads `await broker.get_portfolio()` and dumps it under `state["portfolio"]`. `run_once` now uses this helper instead of the inline state literal. The strategist's `_held_view_before_callback` now sees real holdings on every tick — the Chunk 2 audit's most important follow-up is closed.
- Spec compliance: ✅ — 8 stages in the right order; both strategist callbacks wired; `gemini-2.5-pro` preserved; helper signature + body match the brief; 4 new tests pass (2 pipeline + 2 tick).
- **Authorised scope expansion to 5 paths:** the pre-existing integration tests `test_pipeline_has_seven_stages` / `test_pipeline_stage_names` in `tests/integration/test_pipeline_composition.py` were asserting the old 7-stage shape and would have regressed. Implementer updated them narrowly (count 7→8; renamed `test_pipeline_has_seven_stages` → `test_pipeline_has_eight_stages`; shifted index assertions to include `StrategistDecisionWriter` at position 3). No logic, no imports, no unrelated changes.
- Authorised deviations applied: `UTC` alias in tick.py (ruff UP017 auto-fixed); `FakeBroker(starting_cash=, prices={})` real signature in both new tests; test_tick_initial_state uses `broker._positions[...] = Position(...)` to seed test holdings (test-only convention, mirrors C13 precedent).
- Code quality: ⚠️ approved with one Important + 2 Nits. **Important — ruff I001 in `tests/integration/test_pipeline_composition.py`** (pre-existing import-block ordering issue in that file at chunk-2 tip `c0136f7`; not introduced by C14, but the file is now in C14's commit so worth tidying). Fixed in polish commit `5bd6567` — one blank line between `google.adk` and the first-party imports. **Nit — defensive `RiskGateAgent` fallback** in `test_pipeline_wiring_v2.py:16` is dead code (the agent's `name` is hard-coded to `"RiskGate"`). Left as-is; harmless defensive guard mirrors the plan's literal pattern. **Nit — long assertion-tuple line** in `test_tick_initial_state.py:27`; cosmetic, left as-is.
- Full suite at **329 passed** (325 from C13 + 4 from C14).

### 2026-05-11 — C13 landed (`1c2fffb`)
- Executor now writes the per-position thesis to `state["positions"][ticker]` on BUY (reading from `state["strategist_decision"]["new_positions"]`) and passes the new `opening_tick_id` / `closing_tick_id` FKs into `save_trade_log_entry` on SELL. Three new tests under `tests/unit/executor/`.
- Spec compliance: ✅ — exactly 3 paths in the commit (`src/agents/executor/agent.py`, `tests/unit/executor/__init__.py`, `tests/unit/executor/test_open_positions_state.py`); BUY-branch addition reads from the right state source with a `None` guard; SELL-side 13 existing trade-log keys preserved + 2 new keys added with correct source/coercion. All 3 new tests pass; existing executor tests unaffected; full suite at 325/325 (+3).
- Authorised deviations applied: `UTC` alias instead of `timezone.utc`; `AsyncGenerator` from `collections.abc`; test path `tests/unit/executor/` (matches repo per-package layout); FakeBroker constructor used as-is (real signature `starting_cash=`/`prices=`) — SELL tests pre-run a setup BUY to seed the position rather than introducing a non-existent `seed_positions=` kwarg, keeping `src/broker/fake.py` untouched.
- **Side-fix taken inline (intentional, not scope creep):** the previous executor passed `opened_at` as an ISO-string into `TradeLogRow(**entry)` — SQLAlchemy's `DateTime` column needs a Python `datetime`, so this was a latent bug that would have surfaced the first time a SELL closed a position whose thesis came via JSON state. The implementer normalised `opened_at_raw` → `opened_at_dt` once at the top of the SELL branch and reuses it for both `holding_hours` and the dict. Worth noting; no follow-up needed.
- File-level ruff sweep on the executor: `timezone` import dropped, `UTC` used throughout — eliminates one UP017 violation in the executor (separate from the 3 pre-existing on `persistence.py`, which remain).
- Code quality: ✅ approved. One purely-cosmetic nit (suggest a `# pre-seed broker so SELL has a position` comment in the test setup) — **not actioned**; the test reads fine without it.

### 2026-05-11 — C12 landed (`ca1c283`)
- `StrategistDecisionWriter` (BaseAgent subclass) + `build_strategist_decision_writer` factory in `src/agents/strategist/decision_writer.py`. Persists one `TickerStanceRow` per stance per tick using C10's `save_ticker_stance`; runs between strategist and risk_gate so intent is captured even when risk_gate later raises a contract violation.
- Spec compliance: ✅ — class invariants (BaseAgent, `name`, `db_session: Any = None`, `model_config={"arbitrary_types_allowed": True}`), all 3 short-circuits (no db_session / no decision / lifecycle derive per stance), portfolio coercion (Portfolio / None / dict), `derive_lifecycle_action` + `save_ticker_stance` call shape — all match plan. All 4 required tests pass; full suite at 322/322 (+4).
- Authorised deviations baked in (no rework): test path `tests/unit/agents/strategist/`, `UTC` alias instead of `timezone.utc`, fixture `session` with `s.close()`, `AsyncGenerator` from `collections.abc` (UP035), `class _S: pass` expanded for E701. All ruff-clean on the new files.
- Implementer also adopted a `yield`-after-`return` generator-gate pattern instead of the plan's `if False: yield`; functionally identical and arguably clearer at each exit. Sibling writers under `src/agents/attribution/` and `src/agents/memory/` still use `typing.AsyncGenerator` — backlog candidate to harmonise via UP035 sweep, out of C12 scope.
- Code quality: ✅ approved. 3 nits, **none actioned**: one is just an observation of sibling-file divergence; one notes the factory's docstring is *better* than the precedent (no fix); one claimed a redundant `session.commit()` in `test_writes_one_row_per_stance`, **incorrect** — `save_ticker_stance` only flushes (per C10), so the test's explicit commit is required. Left as-is.

### 2026-05-11 — C11 landed (`da006fc`)
- Two nullable+indexed columns added to `TradeLogRow`: `opening_tick_id` / `closing_tick_id` (both `Mapped[str | None]`, `String`, `index=True`, `nullable=True`) — with an inline comment explaining they're FK-style links back to the originating tick (NULL for pre-Plan-C rows).
- Spec compliance: ✅ — diff is exactly the 2 columns at the bottom of `TradeLogRow`; commit touches only `src/orchestrator/persistence.py` + the new test file; commit message matches the spec verbatim.
- Tests: 3 new in `tests/unit/orchestrator/test_trade_log_tick_id_fks.py` — `test_trade_log_accepts_tick_id_fks` (round-trip), `test_trade_log_join_to_ticker_stance` (SQL JOIN trade↔stance via `tick_id`), `test_tick_id_columns_nullable`. All pass; full suite at 318/318 (+3).
- Authorised deviations applied cleanly: `UTC` alias instead of `timezone.utc`, fixture named `session` with `s.close()` teardown (codebase precedent), and the extra `assert r.closing_tick_id is None` row. No fixture leak this time — C10's lesson was baked into the implementer prompt.
- Code quality: ✅ approved with 2 nits, both **not actioned**:
  - **Nit — `sessionmaker(bind=engine)`** uses the deprecated `bind=` keyword. Left as-is to stay consistent with the sibling persistence-test fixtures (`test_persistence_ticker_stance.py`, `test_attribution_persistence.py`). Cross-file rename is a separate change.
  - **Nit — `assert trade.ticker == "AAPL"` in the join test** is technically redundant given only one AAPL row exists; reviewer suggested `assert trade.opening_tick_id == stance.tick_id` instead. The current assertion is still true and reads as "the joined trade is the AAPL trade we inserted," which is acceptable. Left as-is.
- No new ruff findings; the 3 pre-existing violations on `persistence.py` (`F401` × 2, `UP017` × 1) are unchanged.

### 2026-05-11 — C10 landed (`eb2d53a` + `1993e4b` polish)
- First task of Chunk 3 (persistence + wiring).
- Spec compliance: ✅ — `TickerStanceRow` (15 columns + `id` PK) and `save_ticker_stance` helper added between `save_trade_log_entry` and `PortfolioSnapshotRow`; both required tests pass; full suite at 315/315 (+2 from C10).
- Authorised deviations applied: `datetime.UTC` instead of `timezone.utc` (UP017); new `tests/unit/orchestrator/__init__.py` to make the subdirectory a pytest package; `session: Session` type hint on the helper; expanded Args/Returns docstring on `save_ticker_stance` (project comment-the-code convention).
- Code quality: ⚠️ approved with 2 Important + 1 Nit, all actioned via controller Edit (`1993e4b`):
  - **Important — session leak in fixture:** the new `db` fixture `yield`ed the session but never called `.close()`; existing persistence tests (`test_attribution_persistence.py`, `test_attribution_writer.py`) always close. Fixed.
  - **Important — fixture name:** renamed `db` → `session` to match the precedent in `test_attribution_persistence.py`. Also tightened `test_nullable_lifecycle_fields` to assert *all six* claimed-null columns (was only asserting 3 — `horizon`, `target_price`, `lifecycle_action`).
  - **Nit — divider whitespace:** removed the extra blank line between the `# ── TickerStanceRow ──` divider and the class header to match the existing house style (`TradeLog`, `PortfolioSnapshot`, `AttributionSignals` dividers all use a single blank line).
- Two design observations from the review, not actioned (deliberate):
  - No `(tick_id, ticker)` UNIQUE constraint on `ticker_stances`. Acceptable — C12 controls the caller invariant (one stance per ticker per tick) and a DB constraint would be redundant noise.
  - The `=` column alignment in `TickerStanceRow` diverges from the unpadded style elsewhere in the file. Reviewer rated it readable; left as-is rather than reformatting.
- **Pre-existing ruff debt noted (out of C10 scope):** `src/orchestrator/persistence.py` carries 3 violations at chunk-2 tip `c0136f7` — `F401` on `JSON` (line 8) and `BufferEntry` (line 50), and `UP017` on `timezone.utc` in `save_attribution_signal` (line 257). All pre-date C10 (verified by `ruff check` on the file extracted at `c0136f7`). Backlog candidate: tidy in a standalone `chore(persistence)` commit before this branch lands, or carry it into Chunk 4's regression pass.

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

### 2026-05-11 — C8 landed (`8fe0d66` + `208270e`)
- Spec compliance: ✅ — prompt text byte-identical to plan snippet; all 8 required tests present and passing; only the two specified files changed; commit message matches plan literal.
- Code quality: ⚠️ approved with one Important + minor polish, both actioned via controller Edit (`208270e`):
  - Important — clarified `test_template_renders_with_all_required_slots` docstring to make explicit that the `.format(...)` call is the primary guard against missing slots (raises `KeyError`), and the two `assert` lines below are a lightweight sanity check.
  - Minor — added docstring to `test_template_has_state_slots` noting that `{tickers}` deliberately appears twice in the template (substring checks cannot distinguish one occurrence from two; the format-call test is the real guard).
- Three Minor findings declined: (a) "test path deviation" — already authorised across the chunk; (b) "British English check" — no issues found; (c) "loose substring matches" — reviewer concluded they were acceptable for a literal-text contract; no change needed.
- **Expected regressions:** 3 tests in `tests/unit/test_strategist_prompt_template.py` (legacy v1 prompt-contract tests) now fail. These are authorised — C9 owns replacing/deleting that test file as part of the agent rewrite. Full project suite: 308 passed / 3 failed (the three above).
- Verified that no non-test `src/` code calls `STRATEGIST_INSTRUCTION.format(...)`. Both `agent.py` and `pipeline.py` pass the template verbatim to ADK's `LlmAgent`, which does its own interpolation — so no production callsite was silently broken.

### 2026-05-11 — C9 landed (`e6b395a` + `8f03bc4` + `033dd40`)
- Spec compliance: ✅ — full `src/agents/strategist/agent.py` replacement matches plan template; all 9 callback tests present and passing; the four-pass validation (exhaustive → no-extras → lifecycle → derivation) is wired correctly; `_composite_before_callback` short-circuits on held-view non-None as specified.
- Authorised deviations correctly applied: test path under `tests/unit/agents/strategist/`; `datetime.UTC` instead of `timezone.utc` (UP017); **model preserved as `gemini-2.5-pro`** (NOT downgraded to the plan's `gemini-2.0-pro-001` — the plan was drafted before the upgrade); pytest noqa F401 with justifying comment; one-line ruff I001 fix to `src/agents/strategist/__init__.py` was required for clean lint.
- Code quality: ✅ approved with 4 Nits, all actioned in `033dd40` polish:
  - Typed `_coerce_portfolio` parameter as `Portfolio | dict | None`.
  - Dropped redundant `.replace("Z", "+00:00")` before `datetime.fromisoformat` — Python 3.11+ accepts trailing `Z` natively; updated the explanatory comment.
  - Removed the dead `_te` test helper plus its now-unused `AggregateVerdict` / `TickerEvidence` imports, and the unused `import pytest` noqa.
  - Removed the misleading `TickerStance` re-export from `agent.py` (the noqa comment claimed it was re-exported for callers, but no caller imported it from `agent`).
- Authorised legacy-test cleanup: deleted `tests/unit/test_strategist_validators.py` (`8f03bc4`) — its 3 tests probed the legacy `target_weights`-only contract that `test_strategist_callbacks_v2.py` now covers via stances. Same pattern as the earlier deletion of `tests/unit/test_strategist_prompt_template.py`.
- Full project suite: **313 passed**, all previously expected regressions resolved. Strategist suite at 70/70. Ruff clean.

### 2026-05-11 — Chunk 2 final Opus audit ✅ approved

Cross-task audit of C7-C9 together (Opus model). Empirical baseline: 70/70 strategist tests
green; 313/313 full project suite green; ruff clean; all three replaced modules parse.

**No Critical, no Important findings.** Five follow-ups for later chunks / backlog — none
blocking the chunk-2 merge into the stack:

1. **C14 must seed `state["portfolio"]` before the strategist runs.** The new before-callbacks
   read it but nothing in `src/orchestrator/tick.py` populates it today, and no upstream agent
   writes it either. Without seeding, `_held_view_before_callback` will see `None` and render
   the flat-portfolio sentinel even when positions exist, and the prompt's `{portfolio}` slot
   would interpolate from a missing key. Likely fix: seed `state["portfolio"]` in
   `orchestrator/tick.py` after `broker.get_portfolio()`, or add a tiny `PortfolioRefresh`
   stage at the head of the pipeline. **Track as a C14 prerequisite.**

2. **`validate_lifecycle_contract` is now orphaned in `src/agents/risk_gate/lifecycle.py`.**
   Imported by `src/agents/risk_gate/agent.py:11` but never invoked — the call site at lines
   72-79 does its own inline check using only the `StrategistContractViolation` exception
   class. Out of scope for chunk 2 (strict boundary), and tests still cover the helper directly.
   Worth a cleanup commit on whichever future chunk next touches `risk_gate/`.

3. **`tick_id` fallback to `recorded_at`** (agent.py — `_strategist_validation_callback`)
   silently sets `PositionThesis.opened_tick_id` to a timestamp string if `tick_id` is missing.
   In current production flow `tick_id` is always seeded by `orchestrator/tick.py`, so the
   fallback is defensive-only — but it masks misconfiguration rather than surfacing it.
   Consider tightening to a direct `state["tick_id"]` access (KeyError-loud) or asserting
   `"unknown" not in opened_tick_id`. Defer to a future polish pass.

4. **Duplicate-ticker stances silently dedupe.** The "no extras" check in
   `_strategist_validation_callback` uses a `set` comprehension over `decision.stances`, so
   two stances for `AAPL` would not be flagged. Not a realistic LLM-output failure mode, but
   a low-cost defensive re-prompt would harden the contract. Backlog candidate.

5. **Module-level `strategist_agent` singleton** in `agent.py` is currently re-exported by
   `__init__.py` but not used by the pipeline (which builds the `LlmAgent` inline). Once C14
   wires the new callbacks into the pipeline, decide whether the singleton stays as a public
   convenience handle or gets removed to avoid drift between two definitions of the same agent.

**Chunk 2 is now feature-complete and audit-approved.** Stays on its own branch
(`phase4/planC-strategist-rewrite`, tip `b431ad8`) per the stacked-branch policy. Chunk 3 will
branch off this tip when started; no merge to main yet.

### 2026-05-11 — C7 landed (`4de5c74` + `814a64c`)
- First task of Chunk 2 (strategist rewrite).
- Spec compliance: ✅ — `stances: list[TickerStance]` and `trim_reasons: dict[str, str]` added with `default_factory`; `target_weights` relaxed from required to defaulted; `StrategistDecision` docstring updated; `PositionThesis` byte-identical (every inline comment + C3 `opened_tick_id` preserved). All four required tests present. Strategist regression at 52/52 green; full project suite at 302/302 green.
- Code quality: ⚠️ approved with two Important issues, both actioned via controller Edit (`814a64c`):
  - **rST double-backticks** in the `StrategistDecision` docstring (` ``stances`` `) were inconsistent with every other docstring in the module — replaced with single backticks.
  - **Missing legacy-JSON test:** no case confirmed that `model_validate({...without "stances"...})` works. The `default_factory=list` makes this safe at the Pydantic level, but the safety was untested. Added `test_legacy_json_without_stances_parses`; test count now 5 (still ≤8 cap).
- Two Minor findings noted, not actioned: (a) the new file's per-test docstrings are denser than its Chunk-1 siblings — *better*, not worse, kept as-is; (b) no duplicate-ticker validator on `stances` — defer to C9, the after-callback is where business rules belong.
- `grep -rn "StrategistDecision(" src/` confirmed no non-test callsite constructs the model directly — all production code uses `model_validate(...)` on ADK state, so the `target_weights` relaxation cannot silently produce a broken instance in production.

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
