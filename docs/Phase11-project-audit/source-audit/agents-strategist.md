# Source audit — src/agents/strategist

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 11 (`__init__.py`, `agent.py`, `context_shim.py`,
`decision_writer.py`, `derivation.py`, `enricher.py`, `evidence_view.py`,
`held_view.py`, `position_thesis.py`, `prompts.py`, `schema.py`,
`stance_schema.py`)
**Findings:** 0 P0 · 4 P1 · 6 P2 · 3 P3

## Summary

The Strategist subsystem composes the production strategist branch
(`StrategistContextShim → wrapped LlmAgent → StrategistEnricher`), defines
the per-ticker stance schema, runs validation + derivation, and persists
per-stance rows to the DB. Three themes dominate the findings: (a) two
distinct `PositionThesis` Pydantic classes coexist (`schema.py` legacy
vs `position_thesis.py` canonical) — `src/` reads only the latter but
tests still validate the former; (b) `evidence_view.py` is an entire
module of orphaned rendering code — production routes through
`contract.strategist_prompt.render_all_ticker_blocks` instead; (c) the
contract-invariants doc still describes the strategist's
`_strategist_validation_callback` as the canonical in-tick carve-out
"today", but production replaced that wiring with the
`StrategistEnricher` BaseAgent on 2026-05-25 and the callback is now a
test-only shim. Cross-subsystem flags for consolidation:
`docs/contract-invariants.md §C-Rule 1` (lines 244-251) needs an update
to point at the enricher, not the callback; `derivation.py` docstring
still references the deleted `agents.risk_gate.lifecycle` module; the
`{thesis}` bare-key bridge in `context_shim.py:218-232` introduces a
state key the §A schema does not list.

## Findings

### P1-01 · C2 parallel old/new branches · two `PositionThesis` Pydantic classes coexist

- **Location:** `src/agents/strategist/schema.py:36-68` (legacy) and `src/agents/strategist/position_thesis.py:37-148` (canonical). Production importers all point at `position_thesis.py`: `src/agents/strategist/held_view.py:30`, `src/agents/executor/agent.py:14`, `src/agents/executor/_verb_dispatch.py:19`. Tests still construct the legacy `schema.py` variant: `tests/integration/test_strategist_v2_smoke.py:137`, `tests/unit/test_strategist_schema.py:6`, `tests/unit/agents/strategist/test_position_thesis_opened_tick_id.py:6`, `tests/unit/agents/strategist/test_strategist_callbacks_v2.py:11`.
- **Confidence:** high
- **Description:**
  Two Pydantic models named `PositionThesis` exist with materially different shapes. `schema.py:PositionThesis` carries `opened_tag`, `opened_price: float | None`, `last_review_note`, and no `weight` / `last_reviewed_decision` / `last_reviewed_reason`. `position_thesis.py:PositionThesis` carries the inverse field set — `opened_price: float` (required), `weight`, `last_reviewed_at` / `last_reviewed_decision` / `last_reviewed_reason`, and no `opened_tag` / `last_review_note`. The persisted `state["user:positions"]` shape used by the executor and held-view renderer is the `position_thesis.py` variant; the legacy `schema.py` variant has no production caller in `src/`. Any test that deserialises a stored thesis through the legacy model is exercising a parallel data shape that no longer matches reality, which is precisely the C2 footgun. The two classes also import a different cap config (`schema.py` uses `_POS_THESIS = _cfg.position_thesis_caps`; the canonical one has no schema-level caps), so any operator change to `config/strategist.json` cap fields silently affects only one of the two shapes.
- **Suggested action:**
  Delete the `PositionThesis` class from `src/agents/strategist/schema.py` and migrate the four affected tests onto the canonical `agents.strategist.position_thesis.PositionThesis`. Keep `schema.py` as the home for `StrategistDecision` / `StrategistLLMDecision`. (Test migration is the test-audit workstream's call to make, but the legacy class deletion is in this subsystem's scope.)

### P1-02 · C1 dead code · `evidence_view.py` has no production caller

- **Location:** `src/agents/strategist/evidence_view.py:106` (the public `render_ticker_evidence` function) and the supporting private `_format_per_analyst` / `_format_features` helpers at lines 17, 37. The strategist's production rendering path is `contract.strategist_prompt.render_all_ticker_blocks`, called from `src/agents/strategist/context_shim.py:204`. `grep -rn "agents.strategist.evidence_view" src/` returns zero hits outside the module itself.
- **Confidence:** high
- **Description:**
  The entire `evidence_view.py` module is orphaned in `src/`. `render_ticker_evidence` is its only public symbol and has no `src/` caller; `_format_per_analyst` is exercised only by `tests/unit/agents/strategist/test_evidence_view_missing_report.py` and `test_evidence_view_drops_dead_social.py`, and `render_ticker_evidence` is exercised only by `tests/unit/agents/strategist/test_evidence_view.py`. Production routes through the parallel implementation in `contract/strategist_prompt.py:render_all_ticker_blocks`, which the Spec A surgical-correctness plan also targets for the D1.3 / M3 fixes. Two rendering implementations diverging silently is the C2 pattern; since the strategist-package copy has no production callers it is closer to C1 dead-code that the tests are anchoring in place.
- **Suggested action:**
  Either delete `evidence_view.py` and migrate the three orphaned tests onto `contract.strategist_prompt.render_all_ticker_blocks` (the easier resolution if the rendering logic in the two implementations has converged), or fold any unique-to-evidence_view formatting decisions into the production renderer and then delete. Drop the dead module from the strategist package either way.

### P1-03 · C7 doc/code drift · contract-invariants §C-Rule 1 carve-out cites the wrong file:line

- **Location:** `docs/contract-invariants.md:244-251` (the in-tick callback carve-out's "canonical instance today" paragraph), pointing at `src/agents/strategist/agent.py:383` for `_strategist_validation_callback`. The function actually lives at `src/agents/strategist/agent.py:54-90` post-2026-05-25 refactor (see `graphify-out/graph_delta.md:25-36` for the move), and the production strategist branch no longer wires it — `agent.py:309-324` instantiates the LlmAgent without an `after_agent_callback`, and `agent.py:343-350` sequences `StrategistEnricher` (which writes via a real `state_delta` event) as the third sub-agent of the branch.
- **Confidence:** high
- **Description:**
  Two things are stale: the file-line citation (off by ~329 lines), and the framing. The contract doc reads as if the strategist's direct-mutation `after_agent_callback` is the live in-tick carve-out — i.e. production today relies on the Rule 1 carve-out for the strategist branch. It does not: `StrategistEnricher` writes via a yielded `Event(state_delta=…)` (see `enricher.py:337-343`) and is the production writer-of-record for the enriched `strategist_decision`. The remaining `_strategist_validation_callback` is a thin shim retained only for legacy integration tests that build their own LlmAgent (`tests/integration/test_strategist_minimal_schema_no_retry.py`, `test_end_to_end_smoke.py:388-406`, `test_fresh_run_starts_clean.py:166-187`). The audit doc still names this stale carve-out as the canonical example, which sends a future reader exploring the wrong code path. This finding is filed against `docs/contract-invariants` per the RUBRIC routing rule (§2-C7 last paragraph) — do not edit the doc; consolidation will reconcile.
- **Suggested action:**
  Subsystem: `docs/contract-invariants`. Update §C-Rule 1's "canonical instance today" paragraph to describe the `StrategistEnricher` BaseAgent + `state_delta` Event mechanism as the production strategist write path, and either drop the `_strategist_validation_callback` example or relabel it as the legacy shim. If the carve-out still has a canonical instance elsewhere (e.g. risk_gate, executor), point at that one instead.

### P1-04 · C5 silent-failure attractor · `tick_id` falls back to literal string `"unknown"` on missing §A field

- **Location:** `src/agents/strategist/enricher.py:176` (`tick_id: str = state.get("tick_id") or state.get("recorded_at", "unknown")`) and `src/agents/strategist/decision_writer.py:90` (`tick_id=state.get("tick_id", "unknown")`).
- **Confidence:** high
- **Description:**
  `tick_id` is a §A contract field (row 1, owner: Tick bootstrap, refresh point Phase 2) — its absence at the strategist stage means Phase 2 hydration failed and the rest of the pipeline cannot produce deterministic output. Both call sites silently fall back to the literal string `"unknown"` instead of raising. In `enricher.py:176` the value is then used as the `TickContext.tick_id` field (which is itself unused — see P2-01) and as the error-log key in `_log_offending_decision`, so the only externally observable consequence is a confusing error log. In `decision_writer.py:90` the `"unknown"` value is written into the persisted `TickerStanceRow.tick_id` column, polluting the DB with rows that cannot be joined back to a tick. Per `test-policy §A.7` and the `feedback_silent_failures_loud_tests` memory, this is the canonical silent-failure shape: a contract violation degrades a downstream observable into a defensible-looking constant rather than aborting.
- **Suggested action:**
  Replace both fallbacks with a hard assertion (or a `KeyError` re-raised as `StrategistContractViolation`) — if `state["tick_id"]` is missing at Phase 3, the tick should abort loudly. The enricher's `recorded_at`-as-secondary lookup is also stale (§A names `tick_id` and `as_of` separately; `recorded_at` is not an §A row at all) — drop it.

### P2-01 · C1 dead code · `TickContext.tick_id` / `decision_tag` / `now` and `DerivedFields.decision_tags`

- **Location:** `src/agents/strategist/derivation.py:86-117` (`TickContext` dataclass), lines 113-115 declare `tick_id`, `decision_tag`, `now`; lines 121-153 (`DerivedFields` dataclass), line 147 declares `decision_tags: dict[str, str]`. Population sites at lines 237, 277, 342, 348. The only `ctx.*` reads inside `derive_decision_fields` are `ctx.current_weights` (line 275, 315) and `ctx.watchlist` (line 335) — no caller reads `ctx.tick_id`, `ctx.decision_tag`, or `ctx.now`.
- **Confidence:** high
- **Description:**
  `TickContext.tick_id`, `decision_tag`, and `now` are populated by the enricher (`enricher.py:210-216`) and by every test fixture, but `derive_decision_fields` never reads them — they are documentation that pretends to be machinery. `DerivedFields.decision_tags` is similar but worse: the field is populated with per-stance results from `derive_decision_tag()`, returned from `derive_decision_fields`, and then discarded by the only caller (`enricher.py:218-230` constructs a `StrategistDecision` without ever reading `derived.decision_tags`). `derive_decision_tag` itself IS exercised — `tests/unit/agents/strategist/test_decision_tag_derivation.py` asserts the six tag outcomes — but no production code consumes the dict. The docstring at `DerivedFields.decision_tags` (line 148-153) claims "Spec B / Spec C memory writers use this as a discriminating intent key (S6 — replaces the constant `catalyst_driven_entry` the LLM emitted)" — `grep -rn decision_tags src/agents/memory/` returns zero hits.
- **Suggested action:**
  Delete the three unused `TickContext` fields and the `decision_tags` accumulator from `derive_decision_fields`. Drop `DerivedFields.decision_tags`. If the memory writer is later wired to consume tags, restore the field as part of that PR — adding a dataclass field to satisfy a single consumer is trivial. Keep `derive_decision_tag()` (the test still asserts its semantics); deciding whether to inline it into the new caller can wait. Also drop the dataclass docstring claim that memory writers consume it.

### P2-02 · C1 dead code · `build_strategist_enricher()` factory has no callers

- **Location:** `src/agents/strategist/enricher.py:346-351`.
- **Confidence:** high
- **Description:**
  `build_strategist_enricher()` is a one-line factory that returns `StrategistEnricher()`. `grep -rn build_strategist_enricher src/ tests/ scripts/` returns only the definition. The production strategist branch instantiates `StrategistEnricher()` directly at `agent.py:348`; tests in `tests/unit/agents/strategist/test_enricher.py` likewise construct the class directly. The factory exists "for symmetry with the other strategist factories (`build_strategist_decision_writer`)" per its docstring — but the decision-writer factory exists because it takes a `db_session` argument that has to be threaded through the pipeline composition layer. The enricher takes no arguments; there is nothing for the factory to wire.
- **Suggested action:**
  Delete `build_strategist_enricher`. The docstring's symmetry argument is cosmetic; a one-line factory for a zero-arg constructor adds an indirection layer without any wiring benefit. If a future refactor adds enricher arguments, reintroduce the factory then.

### P2-03 · C6 config-convention violation · hardcoded LLM decoding parameters in `agent.py`

- **Location:** `src/agents/strategist/agent.py:317-323` — `generate_content_config = genai_types.GenerateContentConfig(max_output_tokens=llm_caps.max_output_tokens, temperature=0.3, frequency_penalty=0.5, presence_penalty=0.5, thinking_config=genai_types.ThinkingConfig(thinking_budget=128))`.
- **Confidence:** high
- **Description:**
  Four numeric decoding-control parameters are hardcoded inline: `temperature=0.3`, `frequency_penalty=0.5`, `presence_penalty=0.5`, `thinking_budget=128`. The surrounding comments label them "probe: ..." which suggests these are tuning levers the operator may want to adjust to chase repetition-attractor regressions. `config/strategist.json` already carries the `llm` block (`timeout_seconds`, `max_output_tokens`, `timeout_retries`, `schema_retries`) — these four belong in the same block. Per the project's "Configuration Convention" (`.claude/CLAUDE.md`), tuning knobs that an operator might re-tune without a code change must live in `config/` and be documented in `config/README.md`. The `STRATEGIST_PROBE_DIR` env var is also undocumented in `config/README.md` (the entire env-gated probe block at `agent.py:217-302` is internal diagnostic plumbing and is out of `config/` scope, but the env var itself should at least be mentioned somewhere discoverable — currently it is mentioned only in an inline comment).
- **Suggested action:**
  Extend the `strategist.llm` block in `config/strategist.json` with `temperature`, `frequency_penalty`, `presence_penalty`, and `thinking_budget` keys (default to the current literal values). Surface them through `config.strategist.get_strategist_config().llm` and consume from `agent.py:317-323`. Document the four new keys in `config/README.md`. `STRATEGIST_PROBE_DIR` can be mentioned alongside `STOCKBOT_TERMINAL_LOG` / `STOCKBOT_TRACE` wherever the rest of the strategist env vars are described, or stay an internal-only debug flag with a docstring comment — operator's call.

### P2-04 · C2 parallel old/new branches · `_strategist_validation_callback` shim retained for tests only

- **Location:** `src/agents/strategist/agent.py:54-90`. Test-only callers: `tests/integration/test_strategist_minimal_schema_no_retry.py:287`, `tests/integration/backtest/test_end_to_end_smoke.py:406`, `tests/integration/backtest/test_fresh_run_starts_clean.py:187`, `tests/unit/agents/strategist/test_validation_callback.py:113`, `tests/unit/agents/strategist/test_strategist_callbacks_v2.py:98+`.
- **Confidence:** medium
- **Description:**
  The legacy `after_agent_callback` shim delegates to `validate_and_enrich(state)` and writes the result back via direct mutation (`state["strategist_decision"] = enriched`). It is no longer wired into the production pipeline (the `StrategistEnricher` BaseAgent at `agent.py:348` is the production writer). The shim's `state["strategist_decision"] = enriched` write is conformant per Rule 1's in-tick callback carve-out — the key is tick-scoped, consumed downstream in the same tick by RiskGate. But this creates a parallel-implementation hazard: production writes via `state_delta` event (durable), tests write via direct mutation (in-memory only). The two paths share the same underlying `validate_and_enrich` function so the validation logic does not drift, but tests are asserting on a wiring shape (`after_agent_callback=_strategist_validation_callback`) that no live pipeline uses. The docstring at lines 57-75 is honest about this — it labels the function "no longer wired into the production pipeline" — which downgrades the severity but does not eliminate the trap. Filed as C2 medium because the consolidator may decide the right resolution is a tests-only change (move the legacy tests onto the `StrategistEnricher` path) rather than a `src/` change.
- **Suggested action:**
  Migrate the five legacy tests onto the `StrategistEnricher` path (instantiate the enricher and drive it through the same `validate_and_enrich` logic), then delete `_strategist_validation_callback`. Out of audit scope to do; flag for the fix-plan workstream.

### P2-05 · C5 silent-failure attractor · `decision_writer` silently skips DB write when no decision in state

- **Location:** `src/agents/strategist/decision_writer.py:53-57`.
- **Confidence:** medium
- **Description:**
  `if not raw_decision: return` short-circuits the writer when `state["strategist_decision"]` is missing or falsy. The docstring (line 32) frames this as a no-op for ticks where "the strategist did not run" — but in the live pipeline the strategist branch ALWAYS runs (it is `pipeline.sub_agents[3]`), so the only way `strategist_decision` is absent at this stage is if the enricher's `validate_and_enrich` returned `None` from its no-op short-circuit (`enricher.py:151-153`) or if a prior agent's failure left state in a degraded shape. The persisted `ticker_stances` table then silently misses a row for the tick — a degradation indistinguishable from "strategist correctly emitted no stances" because the strategist now always emits stances on every tick (active-stances model). Per the project's `feedback_silent_failures_loud_tests` policy this is the recurring shape: a defensible-looking guard hides a real "should have run, didn't" outcome.
- **Suggested action:**
  Replace the silent return with either a `logger.warning` (so the absent-write is discoverable in run logs) or a raise when `strategist_decision` is missing and the strategist agent was scheduled to run. The cleaner shape is probably to invert: assert presence and let `KeyError` propagate — the strategist branch always runs in production, so absence IS a contract violation. Pair with a test that exercises the missing-decision branch on the happy path and asserts it raises.

### P2-06 · C7 doc/code drift · `derivation.py` module docstring references deleted `agents.risk_gate.lifecycle`

- **Location:** `src/agents/strategist/derivation.py:16-20`.
- **Confidence:** high
- **Description:**
  The module docstring reads "`StrategistContractViolation` lives here (rather than `agents.risk_gate.lifecycle`) because it is raised by the strategist's own validation callback ... `agents.risk_gate.lifecycle` is deleted in Band 6; all importers now point here." The first half describes a historic move; the second half tells the reader the deletion has happened. `ls src/agents/risk_gate/` confirms the deletion (no `lifecycle.py`). The docstring is internally consistent but redundant — it describes the deletion as if recent, while the audit context is now "post-deletion, indefinitely". A future reader investigating `StrategistContractViolation`'s placement does not need the Band 6 history; they need to know where it lives today.
- **Suggested action:**
  Trim the docstring to one sentence: "`StrategistContractViolation` is raised by the strategist's validation callback and by `derive_decision_fields` itself; co-locating it with the derivation keeps both raise sites in one module." Drop the `risk_gate.lifecycle` cross-reference entirely.

### P3-01 · C7 doc/code drift · `_log_offending_decision` docstring overstates its call sites

- **Location:** `src/agents/strategist/enricher.py:92-117`.
- **Confidence:** high
- **Description:**
  The function docstring claims "Called immediately before every `StrategistContractViolation` raise so that the LLM's own reasoning + decision_tag survive in the run log." It is not — `_log_offending_decision` is invoked only at `enricher.py:188` (the off-watchlist case). The four other `StrategistContractViolation` raises live inside `derive_decision_fields` (`derivation.py:252`, `288`, `299`, `322`) and have no pre-raise log call. The result is asymmetric logging: a single contract violation path is logged with the full LLM context; the other four lose the `decision_tag` / `reasoning` / `thesis` context the docstring promises.
- **Suggested action:**
  Either fix the docstring to acknowledge the single call site, or — better — call `_log_offending_decision` (or its equivalent) before each raise inside `derive_decision_fields`. The latter requires plumbing the `StrategistLLMDecision` into the derivation function, which is a wider change; the docstring-only fix is cheap and consistent with the audit's read-only mandate.

### P3-02 · C3 overabstraction (very mild) · unreachable trailing `yield` in `decision_writer`

- **Location:** `src/agents/strategist/decision_writer.py:97-99` (`self.db_session.commit(); return; yield  # required to make this a generator function`).
- **Confidence:** high
- **Description:**
  The final `yield` is unreachable — the preceding `return` exits the coroutine — and the comment "required to make this a generator function" is misleading. The function already contains `yield` statements at lines 49 and 57 (inside the early no-op returns), each guarded by `# pragma: no cover — generator gate`, so the body is already a generator. The trailing `yield` is gold-plating; deleting it does not change the function's type. Minor; flagged so the next person editing this file does not pile on more defensive boilerplate.
- **Suggested action:**
  Delete lines 98-99. Land alongside any other change to this file.

### P3-03 · C7 doc/code drift · `__init__.py` history note describes a fix already landed

- **Location:** `src/agents/strategist/__init__.py:8-15`.
- **Confidence:** medium
- **Description:**
  The package docstring's "History note" describes the pre-2026-05-21 module-level singleton plus its shadow `_STRATEGIST_MODEL` literal and the model-swap silent no-op it caused. The fix landed; the singleton is deleted; the literal is gone. The note is now a cautionary tale that adds vertical space without active value. Some projects keep these intentionally to deter regressions ("once burned, twice shy"); this one's docstring style elsewhere does not. Flag for tidy-up — the consolidator may decide history notes are valuable as policy and leave it.
- **Suggested action:**
  Either trim to one sentence ("`build_strategist` is the single construction path; the historic module-level singleton was deleted on 2026-05-21.") or drop the note entirely. Operator's call.
