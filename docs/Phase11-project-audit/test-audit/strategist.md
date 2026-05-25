# Test audit — src/agents/strategist

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` (primary); `docs/contract-invariants.md` §C-Rule 1; `docs/test-policy.md` §A.5 / §A.7 / §E.
**Test files in scope:** 33 (full list below)
**Tests collected from those files:** 198 (via `pytest <paths> --collect-only -q`).
**Findings:** 4 P0 · 7 P1 · 6 P2 · 2 P3

## Files in scope

Grouped by location. "Mixed" means the file touches strategist symbols but its primary subject is elsewhere — included for completeness; recommendations stay narrow.

### `tests/unit/agents/strategist/` (21 files — the canonical subtree)

- `test_after_model_unwired.py`
- `test_build_strategist.py`
- `test_context_shim.py`
- `test_context_shim_mode.py`
- `test_decision_schema_v2.py`
- `test_decision_tag_derivation.py`
- `test_decision_writer.py`
- `test_derivation.py`
- `test_derivation_intent_path.py`
- `test_derivation_stance_required.py`
- `test_enricher.py`
- `test_evidence_view.py`
- `test_evidence_view_drops_dead_social.py`
- `test_evidence_view_missing_report.py`
- `test_held_view.py`
- `test_held_view_evolution.py`
- `test_position_thesis.py`
- `test_position_thesis_opened_tick_id.py`
- `test_prompts_v2.py`
- `test_stance_schema.py`
- `test_strategist_callbacks_v2.py`
- `test_ticker_stance_validation.py`
- `test_validation_callback.py`

### `tests/unit/` (root-level — layout oddity, see P2-04)

- `test_strategist_schema.py`
- `test_strategist_prompt_risk_substitutions.py`
- `test_strategist_prompt_worked_examples_ticker.py`

### `tests/unit/config/`

- `test_strategist_config.py`

### `tests/unit/contract/`

- `test_invariants_doc_carveout.py`
- `test_strategist_prompt_layout.py`

### `tests/unit/orchestrator/`

- `test_pipeline_wiring_v2.py` (mixed)
- `test_persistence_ticker_stance.py` (mixed)

### `tests/integration/`

- `test_strategist_v2_smoke.py` (LLM-touching, gated)
- `test_strategist_minimal_schema_no_retry.py`
- `test_thesis_persistence_round_trip.py`
- `test_pipeline_composition.py` (mixed)
- `test_multi_tick_backtest_produces_diverse_rationale.py`
- `test_state_delta_user_prefix_end_to_end.py` (mixed)

### `tests/integration/backtest/`

- `test_end_to_end_smoke.py` (mixed — references the strategist branch heavily)
- `test_fresh_run_starts_clean.py` (mixed — same)

## Summary

The Strategist test suite has good per-feature unit coverage of the stance schema, derivation, and the new `StrategistEnricher` BaseAgent — but is held back by three structural problems mapped directly to the source-audit. (1) Four tests still construct the legacy `agents.strategist.schema.PositionThesis` class (source P1-01), validating fields the canonical persisted shape no longer carries. (2) Three test files anchor the entire orphaned `evidence_view.py` module in place (source P1-02) — these are the only callers in `src/` or `tests/`. (3) The `_strategist_validation_callback` shim (source P2-04) has become a major test-only API: five integration / unit tests drive it directly, and two backtest smoke tests construct their own LlmAgent wired with it as `after_agent_callback`, which is *not* the production wiring (production uses the `StrategistEnricher` BaseAgent). The biggest **gap** is T4: the `tick_id` fallback to literal `"unknown"` (source P1-04) and the `decision_writer` silent-no-op (source P2-05) have no surfacing tests — both are textbook silent-failure attractors per `test-policy §A.7`. Layout-wise, three strategist tests sit at `tests/unit/test_strategist_*.py` root rather than `tests/unit/agents/strategist/` — minor T8 finding.

## Findings

### P0-01 · T4 missing surfacing test · `tick_id="unknown"` fallback in enricher + decision_writer

- **Location(s):** New test needed. Recommended home: `tests/unit/agents/strategist/test_enricher.py` (extend) and `tests/unit/agents/strategist/test_decision_writer.py` (extend).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-04.
- **Confidence:** high
- **Description:**
  The enricher (`enricher.py:176`) and decision_writer (`decision_writer.py:90`) silently fall back to the literal string `"unknown"` when `state["tick_id"]` is missing. The source audit explicitly calls this out as the canonical §A.7 silent-failure shape — a contract violation degrades into a defensible-looking constant rather than aborting. No existing test exercises either fallback. `test_enricher.py` and `test_decision_writer.py` both feed `tick_id="t-test"` / `"tick_X"` into their `_state(...)` builders unconditionally, so the fallback branch is unreached. When source P1-04 lands (replacing the fallbacks with raises), there needs to be an assertion-pair: happy path doesn't raise, missing-tick_id state raises `StrategistContractViolation` (or `KeyError`). Without those, the source fix could be reverted silently.
- **Suggested action:**
  Add two tests per agent: `test_enricher_raises_when_tick_id_missing` and `test_decision_writer_raises_when_tick_id_missing`. Assert `pytest.raises(...)` rather than checking the persisted row carries `"unknown"`. These must be written as part of the source P1-04 fix PR.

### P0-02 · T4 missing surfacing test · `decision_writer` silent no-op when `strategist_decision` missing

- **Location(s):** New test needed. Recommended home: `tests/unit/agents/strategist/test_decision_writer.py`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P2-05.
- **Confidence:** high
- **Description:**
  `tests/unit/agents/strategist/test_decision_writer.py::test_no_op_without_decision` (lines 88-94) currently *encourages* the silent-no-op behaviour the source audit flags as a P2 silent-failure attractor — it asserts `session.query(TickerStanceRow).count() == 0` when `state["strategist_decision"]` is `None`. The strategist branch always runs in production (it is `pipeline.sub_agents[3]`), so absence at the decision_writer stage indicates the enricher emitted `None`, which itself is a degradation. Pairing the source fix (raise instead of silent return) with a test inversion is necessary; the current test would actively block the fix.
- **Suggested action:**
  When source P2-05 lands, **invert** `test_no_op_without_decision`: it should assert `pytest.raises(KeyError)` (or `StrategistContractViolation`). Keep one test that exercises the legitimate-no-decision path *iff* the source audit decides such a path is real (e.g. cold-start tick before any analysts emit); otherwise delete. Until the source fix lands, file this finding as P0 because the test is masking the bug.

### P0-03 · T3 weak assertion · `test_strategist_v2_smoke.py` doesn't assert stance content or non-degraded path

- **Location(s):** `tests/integration/test_strategist_v2_smoke.py:243-264`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-04 (silent-failure attractor pattern); test-policy §A.7 / §E ("It didn't raise, therefore it works").
- **Confidence:** high
- **Description:**
  The lone LLM-touching smoke test verifies (a) `state["strategist_decision"]` is not None, (b) the dict validates as `StrategistDecision`, (c) every watchlist ticker appears in `decision.stances` and `decision.target_weights`. It does **not** assert any positive content on the stances — no `intent` check, no `weight` value, no `rationale` non-empty, no `is_no_data` / `branch_failed` filter. Per §A.7 a degraded LLM response carrying two `hold` stances with `weight=0.0` would still pass this test, despite that being the exact "stuck on tick 1" pathology Spec B was written to defeat. The strategist's `tick_id="unknown"` fallback (source P1-04) would also pass — the test never reads back the persisted `TickerStanceRow.tick_id`. Caplog is not used to assert against `branch_failed` warnings either.
- **Suggested action:**
  Add positive content assertions: at least one stance has `intent in {"open","hold","trim","close","add","update"}`, the AAPL held-position stance is **not** `intent="open"` (cannot re-open a held), and `rationale` / `reason` is non-empty on every stance. Pair with `caplog.set_level("WARNING")` and assert no `branch_failed` records. Strict but cheap.

### P0-04 · T5 mock at the wrong level · backtest smokes patch `_build_strategist` and substitute a hand-built LlmAgent with the **legacy** callback wiring

- **Location(s):** `tests/integration/backtest/test_end_to_end_smoke.py:379-405` (the `_patched_build_strategist` closure) and `tests/integration/backtest/test_fresh_run_starts_clean.py:161-187` (same pattern).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-03 (contract-invariants drift), P2-04 (legacy callback shim retained for tests only).
- **Confidence:** high
- **Description:**
  Both backtest smokes replace `orchestrator.pipeline._build_strategist` wholesale with a hand-built `LlmAgent` carrying `after_agent_callback=_strategist_validation_callback` and a `before_model_callback` that synthesises an `LlmResponse`. This is **not** the production wiring after the 2026-05-25 enricher refactor — production uses `SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]`, where the enricher writes via a `state_delta` event. The smokes therefore exercise a parallel old code path that no live tick uses. Two consequences: (1) any regression in the enricher's state-delta emission goes undetected by these smokes; (2) the smokes will keep "passing" through the source P2-04 fix (deletion of `_strategist_validation_callback`), giving false confidence the deletion is safe. The right stub point is `LlmAgent.before_model_callback` or the leaf `LlmAgent.run_async` — *not* `_build_strategist` itself.
- **Suggested action:**
  Reshape both smokes to keep the real `_build_strategist` and patch the inner LLM at the `before_model_callback` shim only (which the existing helper `_make_strategist_llm_response` already produces). Alternatively, switch to wrapping `StrategistEnricher` with a known-good payload. Either way the wrapping must mirror the production composition; if it does not, the smoke is decorative.

---

### P1-01 · T2 parallel old/new branches · 4 tests construct legacy `PositionThesis` from `agents.strategist.schema`

- **Location(s):** `tests/integration/test_strategist_v2_smoke.py:137`; `tests/unit/test_strategist_schema.py:6`; `tests/unit/agents/strategist/test_position_thesis_opened_tick_id.py:6`; `tests/unit/agents/strategist/test_strategist_callbacks_v2.py:11`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-01.
- **Confidence:** high
- **Description:**
  Two `PositionThesis` Pydantic classes coexist with materially different shapes: `schema.py:PositionThesis` (legacy — `opened_tag`, `last_review_note`, optional `opened_price`) and `position_thesis.py:PositionThesis` (canonical — required `opened_price`, `weight`, `last_reviewed_decision`/`reason`). Production importers all point at the canonical one (`held_view.py`, `executor.agent`, `executor._verb_dispatch`); the four tests above still construct the legacy variant, validating fields the persisted `state["user:positions"]` shape no longer carries. `test_position_thesis_opened_tick_id.py` is the worst offender because its only purpose is to assert on the `opened_tick_id` round-trip — a field both classes share, but tested against the legacy class. The canonical-class version of these tests (`test_position_thesis.py` + `test_held_view.py` + `test_held_view_evolution.py`) already exists in the same subtree, so the legacy-class tests are duplicative as well as stale.
- **Suggested action:**
  When source P1-01's deletion of the legacy `schema.py:PositionThesis` lands, migrate all four files onto `agents.strategist.position_thesis.PositionThesis`. Field deltas: `opened_tag` → `opened_tick_id`, `last_review_note` → `last_reviewed_decision`/`last_reviewed_reason`, `opened_price` becomes required. After migration, `test_position_thesis_opened_tick_id.py` is redundant with the canonical `test_position_thesis.py` round-trip; recommend delete-after-migrate.

### P1-02 · T1 dead tests · 3 tests anchor the orphaned `evidence_view.py` module

- **Location(s):** `tests/unit/agents/strategist/test_evidence_view.py` (10 tests); `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py` (2 tests); `tests/unit/agents/strategist/test_evidence_view_missing_report.py` (1 test).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-02.
- **Confidence:** high
- **Description:**
  `src/agents/strategist/evidence_view.py` has zero production callers — `grep -rn "agents.strategist.evidence_view" src/` returns only the module itself. Production rendering goes through `contract.strategist_prompt.render_all_ticker_blocks` (which is exercised by `tests/unit/contract/test_strategist_prompt_layout.py`). The three test files above are the only consumers of `evidence_view._format_per_analyst` and `render_ticker_evidence`; they hold the module alive. The two "is_no_data social" and "missing report placeholder" defence-in-depth tests cover behaviours that *also* live in (and need testing in) the production `contract.strategist_prompt` renderer.
- **Suggested action:**
  Conditional on source P1-02's deletion of `evidence_view.py`: delete `test_evidence_view.py` and `test_evidence_view_drops_dead_social.py` outright. For `test_evidence_view_missing_report.py`, migrate the `(is_no_data=False, report=None) → placeholder` assertion onto `tests/unit/contract/test_strategist_prompt_layout.py` (the production renderer) — that defence-in-depth assertion *is* worth keeping; it just needs to fire against the live code path.

### P1-03 · T2 parallel old/new branches · 5 tests drive `_strategist_validation_callback` directly

- **Location(s):** `tests/unit/agents/strategist/test_strategist_callbacks_v2.py` (6 tests, lines 53–315); `tests/unit/agents/strategist/test_validation_callback.py:113`; `tests/integration/test_strategist_minimal_schema_no_retry.py:287`; `tests/integration/backtest/test_end_to_end_smoke.py:406`; `tests/integration/backtest/test_fresh_run_starts_clean.py:187`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P2-04 (and P1-03 — contract-invariants doc drift).
- **Confidence:** high
- **Description:**
  The legacy `_strategist_validation_callback` is no longer wired into the production pipeline — its docstring explicitly admits this (`agent.py:57-75`). Production uses the `StrategistEnricher` BaseAgent. Five tests still drive the callback directly; their existence is the only thing keeping the shim alive in `src/`. The five tests duplicate logic that `tests/unit/agents/strategist/test_enricher.py` already covers via the production path (`test_enricher_raises_on_off_watchlist_ticker`, `test_enricher_transforms_narrow_llm_output_into_full_decision`), so the loss is one of test-shape (direct callback call vs `_run_async_impl` yielding an Event), not test-coverage. Per the rubric §2-T2, file as P1 contingent on the source-audit P2-04 fix landing.
- **Suggested action:**
  When source P2-04 lands (callback deletion), migrate the five tests onto the `StrategistEnricher` path (instantiate the enricher, drain its `_run_async_impl`, read the state_delta). The `test_strategist_callbacks_v2.py` tests are the highest-value migration — they cover off-watchlist, held-coverage, close-without-reason, trim-without-reason, and target_weights derivation. `test_validation_callback.py` is narrower (retry-counter passing) and can be folded into `test_enricher.py`. The two backtest smokes need their `_patched_build_strategist` rewritten regardless (see P0-04).

### P1-04 · T7 hard-rule violation (§A.4) · `test_strategist_v2_smoke.py` LLM gating is module-level but missing `@pytest.mark.integration`

- **Location(s):** `tests/integration/test_strategist_v2_smoke.py:37-40`.
- **Source-audit cross-ref:** test-policy §A.4 and §C.
- **Confidence:** medium
- **Description:**
  The file is correctly gated on `RUN_LLM_TESTS=1` at module level (line 37) and the single test carries `@pytest.mark.integration` (line 116). However, the module-level `pytestmark = pytest.mark.skipif(...)` should also include `pytest.mark.integration` so the test inherits the marker even at collection time. Today, the marker only applies to the function, which is fine for skipping but means `-m integration` selection skips other discovery surfaces (e.g. `--collect-only -m integration` may behave inconsistently with how the rest of the suite tags LLM tests). Minor §A.4 hygiene — the test does honour the §A.1 (no real keys without opt-in) and §A.4 (LLM-opt-in) intent, so this is P1 not P0.
- **Suggested action:**
  Promote the marker to module-level by combining: `pytestmark = [pytest.mark.integration, pytest.mark.skipif(...)]`. Two-line change.

### P1-05 · T3 weak assertion · `test_multi_tick_backtest_produces_diverse_rationale.py` doesn't drive the LLM agent or strategist branch

- **Location(s):** `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py:119-231` (`_PromptRecorder` substring substitution).
- **Source-audit cross-ref:** Not source-audit driven, but test-policy §A.7 and §E ("the test verifies the input differs, not that the LLM is unstuck").
- **Confidence:** medium
- **Description:**
  The test name claims "produces diverse rationale" but the test only exercises `StrategistContextShim._run_async_impl` for 5 ticks and asserts that the **input** prompt string differs across ticks. The strategist's actual rationale-emission path (the LlmAgent + enricher) is not driven. The "stuck on tick 1" pathology the docstring references is about LLM output, not input. The test as written is a useful prompt-diversity unit but its name overstates what it proves — a future regression that makes the LLM stuck-on-tick-1 despite varied prompts would still pass.
- **Suggested action:**
  Rename to `test_multi_tick_prompt_input_differs_across_ticks` (clearer scope) and demote from `tests/integration/` to `tests/unit/agents/strategist/` — it does not wire two modules together, it exercises one BaseAgent's output. Alternatively, leave at integration and add a stubbed LlmAgent (mirroring the pattern in `test_strategist_minimal_schema_no_retry.py`) that records the prompt the agent **sees** and asserts diversity on output rationale text.

### P1-06 · T8 layout · 3 strategist tests live at `tests/unit/test_strategist_*.py` instead of `tests/unit/agents/strategist/`

- **Location(s):** `tests/unit/test_strategist_schema.py`, `tests/unit/test_strategist_prompt_risk_substitutions.py`, `tests/unit/test_strategist_prompt_worked_examples_ticker.py`.
- **Source-audit cross-ref:** test-policy §B (mirror-source-tree convention).
- **Confidence:** high
- **Description:**
  Per test-policy §B "Unit tests live under `tests/unit/` mirroring the source tree (e.g. `src/agents/news/fetch.py` → `tests/unit/agents/news/test_fetch.py`)". These three files target `src/agents/strategist/{schema.py, prompts.py}` but sit at the unit root rather than under `tests/unit/agents/strategist/`. The naming is also inconsistent — the subtree uses `test_strategist_callbacks_v2.py`, `test_decision_schema_v2.py` (no `strategist_` prefix on schema), while the root has `test_strategist_schema.py`. The discoverability cost is real: a developer running `pytest tests/unit/agents/strategist/` misses three relevant tests.
- **Suggested action:**
  Move all three into `tests/unit/agents/strategist/` and drop the `strategist_` prefix where the subdirectory makes it redundant: `test_schema.py` (or merge with `test_decision_schema_v2.py`), `test_prompts_risk_substitutions.py`, `test_prompts_worked_examples.py`. Pure-mechanical move, no assertion changes.

### P1-07 · T2 parallel old/new branches · `test_invariants_doc_carveout.py` will fail when contract-invariants doc updates per source P1-03

- **Location(s):** `tests/unit/contract/test_invariants_doc_carveout.py:19-34`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-03 (contract-invariants doc drift); `docs/contract-invariants.md` §C-Rule 1.
- **Confidence:** medium
- **Description:**
  The test asserts that `docs/contract-invariants.md` contains the substring "In-tick callback carve-out" and that `docs/Phase8-contract-audit-fixes/contract-audit.md` contains "in-tick carve-out". Source-audit P1-03 recommends updating §C-Rule 1's "canonical instance today" paragraph to describe the `StrategistEnricher` BaseAgent + `state_delta` Event mechanism — the carve-out clause may stay but its framing changes. A doc-substring presence test like this one is brittle and creates a coupling between source-fix PRs and unrelated doc-prose edits. Filed P1 because the test will block the doc-fix PR otherwise.
- **Suggested action:**
  Either widen the substring to something more semantically stable (e.g. "Rule 1" + "callback carve-out" as two separate substring checks), or delete the test entirely and rely on the doc being human-reviewed. The audit-row substring test (line 28-34) is a similar trap. Recommend deletion of both unless someone is actively defending a specific clause — substring-presence on a Markdown doc is the wrong granularity.

---

### P2-01 · T3 weak assertion · `test_decision_writer.py::test_no_op_without_db_session` asserts only that the call doesn't raise

- **Location(s):** `tests/unit/agents/strategist/test_decision_writer.py:97-108`.
- **Source-audit cross-ref:** test-policy §A.7 / §E ("It didn't raise"); source-audit P2-05 (related silent-failure attractor).
- **Confidence:** high
- **Description:**
  The test sets `db_session=None`, runs `_run_async_impl(...)`, and asserts nothing — the comment says "must not raise" but there is no positive assertion. If a future refactor makes the writer silently swallow a real failure (e.g. `try/except` around the missing-session branch), the test still passes. The writer's `if self.db_session is None: return` is conformant per current contract, but the test should at minimum assert that **no event** was yielded (mirroring the structural assertion pattern used in `test_enricher.py::test_enricher_no_op_when_no_decision_in_state`).
- **Suggested action:**
  Assert `events == []` (where `events = _run(...)`). One-line change; brings the test into line with the §A.7 surfacing-assertion contract.

### P2-02 · T3 weak assertion · `test_evidence_view.py` placeholder-string substring matches are too loose

- **Location(s):** `tests/unit/agents/strategist/test_evidence_view.py:102-105` (`assert "no data" in out.lower() or "no_data" in out.lower() or "n/a" in out.lower()`), :116-120 (RSI features), :84-88 (per-analyst presence).
- **Source-audit cross-ref:** test-policy §E ("Asserting only on counts, never on content"); related to source-audit P1-02 anyway (this whole module is dead).
- **Confidence:** medium
- **Description:**
  Multiple assertions use the `assert X in out or Y in out or Z in out` shape that admits any one of three loose substrings. Per `test-policy §E`, "Asserting only on counts, never on content" — these are the qualitative analogue. If the renderer regresses to emit a different placeholder, the test passes silently because the assertion accepts three forms. This is mild, mitigated by the file being slated for deletion (see P1-02), but called out so the migration of `test_evidence_view_missing_report.py` to `test_strategist_prompt_layout.py` (per P1-02 disposition) doesn't import the same looseness.
- **Suggested action:**
  N/A if P1-02 disposition deletes the file. If kept (migration only of missing-report test), pin the exact placeholder string in the migrated assertion.

### P2-03 · T6 wide-scope monkeypatch · `test_validation_callback.py` mutates module-level state via `importlib.reload` is not present, but the test does monkeypatch a function on `agents.strategist.enricher`

- **Location(s):** `tests/unit/agents/strategist/test_validation_callback.py:94-97`.
- **Confidence:** low
- **Description:**
  The test does `monkeypatch.setattr("agents.strategist.enricher.emit_analyst_summary", _fake_emit)`. This is the leaf-seam pattern recommended by §A.5 — `emit_analyst_summary` is the boundary call — but the comment at lines 91-93 admits a parallel patching concern ("Patch at the enricher site so both the new BaseAgent path and the shim-based test wiring see the fake"). The dual-target framing hints that the production path and the test path see the symbol via different import chains; if `_strategist_validation_callback` happens to call `emit_analyst_summary` via a different qualified path in the future, the patch will silently no-op. Mild — the current patch is at the right level.
- **Suggested action:**
  Once source P2-04 lands and the legacy callback is deleted, this test should be re-homed in `test_enricher.py`. At that point the patch concern goes away.

### P2-04 · T6 wide-scope patch · `test_after_model_unwired.py` uses `patch.dict(os.environ, {}, clear=False)` and `os.environ.pop(...)`

- **Location(s):** `tests/unit/agents/strategist/test_after_model_unwired.py:33-35`.
- **Confidence:** medium
- **Description:**
  The test uses `with patch.dict(os.environ, {}, clear=False): os.environ.pop("STOCKBOT_TRACE", None)` inside the context manager. `patch.dict(clear=False)` does not actually unset keys — the manual `pop` is what removes `STOCKBOT_TRACE`, and the context manager only restores the original `os.environ` snapshot on exit. This works in practice but is brittle: the snapshot semantics for `patch.dict` don't automatically restore a deletion you do inside the block (the snapshot captures the *state at entry*, so the pop is correctly reversed). However, the safer idiom is `monkeypatch.delenv("STOCKBOT_TRACE", raising=False)` — pytest's built-in tracker restores deletions explicitly and the test file is not pytest-fixture-shy elsewhere.
- **Suggested action:**
  Switch to `monkeypatch.delenv("STOCKBOT_TRACE", raising=False)` and drop the `patch.dict + pop` dance. One-line change.

### P2-05 · T8 redundancy · `test_decision_schema_v2.py` and `test_strategist_schema.py` overlap

- **Location(s):** `tests/unit/agents/strategist/test_decision_schema_v2.py` (4 tests) and `tests/unit/test_strategist_schema.py` (4 tests, 2 covering `StrategistDecision`, 2 covering `PositionThesis`).
- **Source-audit cross-ref:** P1-01 (legacy PositionThesis migration).
- **Confidence:** medium
- **Description:**
  Both files cover `StrategistDecision` schema behaviour. `test_decision_schema_v2.py` is the canonical v2 file (named for the contract version). `test_strategist_schema.py` mixes two `StrategistDecision` tests (reasoning length cap, confidence range) with two legacy `PositionThesis` tests. After the P1-01 migration, the `PositionThesis` portion goes away, and the remaining two `StrategistDecision` tests should fold into `test_decision_schema_v2.py`.
- **Suggested action:**
  Conditional on P1-01: fold the two `StrategistDecision` tests from `test_strategist_schema.py` into `test_decision_schema_v2.py` and delete the file. Layout finding paired with P1-06.

### P2-06 · T8 missing markers · backtest smokes and the strategist smoke lack consistent marker treatment

- **Location(s):** `tests/integration/backtest/test_end_to_end_smoke.py`, `tests/integration/backtest/test_fresh_run_starts_clean.py`, `tests/integration/test_strategist_v2_smoke.py`, `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`.
- **Source-audit cross-ref:** test-policy §C (markers).
- **Confidence:** medium
- **Description:**
  Per test-policy §C, "Backtest smoke tests almost always need `slow + integration`". The four files above all tick the strategist branch (real or stubbed) and use ADK runner internals, but only `test_strategist_v2_smoke.py` carries `@pytest.mark.integration`. The two backtest smokes have no markers visible from the strategist refs I inspected; verify on full read. Without `slow`, they run on every commit; without `integration`, they don't gate cleanly on `-m integration`.
- **Suggested action:**
  Audit marker coverage on the four files; add `pytestmark = [pytest.mark.slow, pytest.mark.integration]` at module level where missing. Sub-task for the layout-fix PR.

---

### P3-01 · T8 docstring drift · `test_invariants_doc_carveout.py` cites `agent.py:383` which is no longer the function's line

- **Location(s):** `tests/unit/contract/test_invariants_doc_carveout.py:28-34`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-strategist.md` P1-03 (the function moved to line 54-90).
- **Confidence:** high
- **Description:**
  The test docstring references "the strategist/agent.py:383 row" but the function lives at `agent.py:54-90` post-refactor. The test asserts on the audit doc's content, not on the line number, so the test itself still passes — but the docstring is stale. Catalogued here because the audit doc itself will likely change as part of the P1-03 fix.
- **Suggested action:**
  Update the docstring during the P1-07 disposition (either delete the test or rephrase the substring check). One-paragraph edit.

### P3-02 · T8 docstring drift · `test_strategist_v2_smoke.py` known-failure-modes block references an ADK version specifically

- **Location(s):** `tests/integration/test_strategist_v2_smoke.py:14-26` (module docstring "Known failure modes" block).
- **Confidence:** medium
- **Description:**
  The docstring references "ADK 1.32 runner-cleanup bug" by version number. If ADK has since moved on (the project may have upgraded), the docstring is misleading. Not a functional finding, just a maintenance smell.
- **Suggested action:**
  When P0-03 strengthens this test's assertions, refresh the failure-modes block: confirm whether the ADK 1.32 bug is still relevant, drop or update the version reference.
