# strategist — module audit findings

Source under audit: `src/agents/strategist/` (recurse).
Tests under audit: `tests/unit/agents/strategist/*`, `tests/integration/test_strategist_*`,
`tests/integration/backtest/test_end_to_end_smoke.py`,
`tests/integration/backtest/test_fresh_run_starts_clean.py`,
`tests/unit/contract/test_invariants_doc_carveout.py`,
`tests/unit/agents/strategist/test_evidence_view*.py`,
`tests/unit/agents/strategist/test_validation_callback.py`,
`tests/unit/agents/strategist/test_strategist_callbacks_v2.py`.

All findings derived from authoritative §7 resolutions in
`docs/audits/2026-05-26-codebase-audit/intent.md`.

---

## F-strategist-001
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/strategist/agent.py:54-90`
- **Evidence:** `_strategist_validation_callback` is a thin delegate to
  `validate_and_enrich`. Production wiring (`build_strategist`,
  `agent.py:309-356`) does NOT attach it — the `LlmAgent` is built without
  any `after_agent_callback`, and `StrategistEnricher` runs the enrichment
  instead. The only `src/` import is at `agent.py:46`
  (`from agents.strategist.enricher import validate_and_enrich`) which is
  consumed solely by the dead callback itself. Caller search:
  `rg -n "_strategist_validation_callback" src/` returns only the
  definition site and its own docstring references.
- **Intent violated:** §7.2 (AUTHORITATIVE) — "dead in production…
  survives only as a delegate for legacy integration tests."
- **Suggested action:** delete (along with the
  `CallbackContext`/`genai_types` imports it forces at module top and the
  `validate_and_enrich` import that becomes unused once the shim is gone).
- **Notes:** Per §7.2 also requires P2 doc-fix to
  `docs/contract-invariants.md` §C-Rule 1 carve-out clause (see
  F-strategist-010 below).

## F-strategist-002
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/integration/test_strategist_minimal_schema_no_retry.py`
  (whole file), `tests/integration/backtest/test_end_to_end_smoke.py:390-408`,
  `tests/integration/backtest/test_fresh_run_starts_clean.py:161-190,261`,
  `tests/unit/agents/strategist/test_validation_callback.py`,
  `tests/unit/agents/strategist/test_strategist_callbacks_v2.py`.
- **Evidence:** Each file imports `_strategist_validation_callback` and
  either calls it directly via a hand-rolled `_Ctx` shim or hand-builds
  a parallel `SequentialAgent` that wires it as `after_agent_callback`.
  `grep -l "_strategist_validation_callback" tests/` returns exactly these
  five files. None of them exercise the production
  `build_strategist()` branch composition.
- **Intent violated:** §7.2 — "legacy integration tests that exercise the
  callback" are explicitly flagged as P1 dead-test.
- **Suggested action:** delete tests whose only purpose is the dead path
  (e.g. `test_validation_callback.py`,
  `test_strategist_minimal_schema_no_retry.py`); rewrite or port the
  remaining tests' subject onto `StrategistEnricher` /
  `validate_and_enrich` directly (the latter is already the shared
  implementation core).
- **Notes:** `test_strategist_callbacks_v2.py` covers off-watchlist and
  bad-rationale paths that `test_enricher.py` already exercises against
  the production path — likely fully redundant after the port.

## F-strategist-003
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/strategist/context_shim.py:55-72` and
  `src/agents/strategist/enricher.py:73-89`.
- **Evidence:** Two byte-identical `_coerce_portfolio` helpers (same
  signature, same three-branch body — `Portfolio` instance → return,
  `None` → `Portfolio(cash=0.0)`, otherwise → `Portfolio.model_validate`).
  Comment on `context_shim.py:58` literally reads "Mirrors the helper in
  `agents.strategist.agent`" — the previous mirror site (the old
  validation callback) has migrated to `enricher.py`. Two mirror sites,
  one shape.
- **Intent violated:** n/a (no §-rule violation; pure dedupe).
- **Suggested action:** consolidate — promote to a single private helper
  in (e.g.) `agents/strategist/_portfolio_util.py` or onto
  `broker.portfolio.Portfolio` as a classmethod, then import from both
  sites.

## F-strategist-004
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/strategist/evidence_view.py` (whole module).
- **Evidence:** `rg -n "evidence_view" src/` returns only
  `evidence_view.py` itself plus one comment-only mention in
  `src/backtest/decision_logger.py:325`. The only `src/` import sites are
  the test files. The actual prompt-facing renderer used by the live
  pipeline is `render_all_ticker_blocks` from
  `src/contract/strategist_prompt.py:679`, invoked at
  `context_shim.py:290`. `evidence_view.render_ticker_evidence` is never
  called outside its own tests.
- **Intent violated:** n/a (no §-rule violation; the rendering function
  was superseded).
- **Suggested action:** delete `src/agents/strategist/evidence_view.py`
  in its entirety. Tests `test_evidence_view.py`,
  `test_evidence_view_drops_dead_social.py`,
  `test_evidence_view_missing_report.py` go with it (see F-strategist-005).
- **Notes:** The `_format_per_analyst` helper is also covered by
  contract-side tests around `render_all_ticker_blocks`
  (`tests/unit/contract/test_strategist_prompt_layout.py`) — the
  dead-social and missing-report assertions belong there if not already.

## F-strategist-005
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/unit/agents/strategist/test_evidence_view.py`,
  `tests/unit/agents/strategist/test_evidence_view_drops_dead_social.py`,
  `tests/unit/agents/strategist/test_evidence_view_missing_report.py`.
- **Evidence:** All three import directly from
  `agents.strategist.evidence_view`, which is dead (F-strategist-004).
  They exercise prose formatting that no longer reaches the LLM.
- **Suggested action:** delete with the evidence_view module; if any
  assertion (drop-dead-social, missing-report fallback) is genuinely
  load-bearing, port the assertion onto the corresponding
  `render_all_ticker_blocks` test in
  `tests/unit/contract/test_strategist_prompt_layout.py`.

## F-strategist-006
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/agents/strategist/derivation.py:150-183, 253-348`.
- **Evidence:** `DerivedFields.decision_tags: dict[str, str]` is computed
  per-ticker in `derive_decision_fields` (lines 309, 341) and returned as
  a frozen field of `DerivedFields`. But the only consumer
  (`enricher.validate_and_enrich`, lines 217-229) reads only
  `derived.target_weights`, `derived.sell_reasons`,
  `derived.update_reasons` and discards `decision_tags`. `rg -n
  "derived\.decision_tags|\.decision_tags\b" src/` returns zero hits
  outside `derivation.py`. The dataclass docstring at line 170 claims
  "Downstream Spec B / Spec C memory writers use this tag as the intent
  key" — that consumer does not exist.
- **Intent violated:** "Silent failures are the recurring bug class"
  (auto-memory). Computation that no consumer reads is dead, and the
  docstring claim is misleading.
- **Suggested action:** investigate — either (a) wire `decision_tags`
  into `StrategistDecision` and the MemoryWriter as the docstring
  promises, or (b) drop the field plus the per-ticker
  `derive_decision_tag` calls. The standalone `derive_decision_tag`
  function still has direct callers (its own tests); only the
  `DerivedFields.decision_tags` plumbing is dead.

## F-strategist-007
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/strategist/context_shim.py:153, 229`.
- **Evidence:** Two bare-key fallback chains:
  `state.get("user:positions") or state.get("positions") or {}`. Per
  intent §7.3 (AUTHORITATIVE), "ContextShim runs after the executor's
  after-callback, so it could read `user:positions` directly" — the bare
  key is executor-internal, and external readers (ContextShim,
  decision_logger) "do NOT need the bare key."
- **Intent violated:** §7.3 — explicitly named as the P2 consolidation
  candidate.
- **Suggested action:** consolidate — drop the `or state.get("positions")`
  fallback in both sites, read `state.get("user:positions") or {}`
  directly.
- **Notes:** Strict ordering inside one tick must hold for this to be
  safe — verify executor's `after_agent_callback` fires before
  StrategistContextShim runs on the *next* tick (per §7.3 this is the
  invariant the resolution rests on).

## F-strategist-008
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/strategist/enricher.py:357-362`.
- **Evidence:** `build_strategist_enricher()` factory — sole external
  reference is its own docstring. `rg -n "build_strategist_enricher"
  src/ tests/ scripts/` returns only the definition. `build_strategist`
  in `agent.py:355` constructs `StrategistEnricher()` directly.
- **Intent violated:** n/a.
- **Suggested action:** delete the factory.

## F-strategist-009
- **Category:** policy-mismatch / dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/strategist/schema.py:96-107` (StrategistDecision.thesis
  Field description) and `prompts.py:130` (`Thesis: {thesis}` prompt
  placeholder) and `context_shim.py:295-304` ("bridge user:thesis →
  {thesis}" comment).
- **Evidence:** The `StrategistDecision.thesis` Field carries an
  inline TODO at line 103: "TODO Band 4: migrate this write to
  Executor's after_agent_callback and rename state key from 'thesis' to
  'user:thesis'." Per intent §A and §C-Rule 7 Spec B clarification, the
  executor's after-callback IS already the writer-of-record for
  `user:thesis`; the migration is half-done. The ContextShim explicitly
  documents the bridge it carries to keep the prompt placeholder
  resolving against `{thesis}` instead of `{user:thesis}` and notes
  "Plan 2 will rename the placeholder…" — Plan 2 is a Phase doc
  reference and is forbidden reading; whatever it is, the bridge persists.
- **Intent violated:** §A clarifies `user:thesis` is the contract key;
  the bare `{thesis}` slot is undocumented working state.
- **Suggested action:** investigate / refactor — rename the prompt
  placeholder to `{user:thesis}` (ADK supports prefixed keys), drop the
  `"thesis"` state-delta write in `context_shim.py:331`, and remove the
  stale TODO from the schema docstring.

## F-strategist-010
- **Category:** policy-mismatch (doc-fix)
- **Severity:** P2
- **Location:** `docs/contract-invariants.md` §C-Rule 1 "In-tick callback
  carve-out" clause (lines 237-250) plus the supporting test
  `tests/unit/contract/test_invariants_doc_carveout.py`.
- **Evidence:** The contract carve-out names
  `_strategist_validation_callback` as "the canonical instance today" of
  the in-tick callback pattern. Per §7.2, that callback is dead in
  production. Once F-strategist-001 lands, the contract example
  evaporates and the carve-out clause needs revision (or the example
  needs to be swapped to whatever the next legitimate in-tick callback
  is — likely none in the strategist module).
  `test_invariants_doc_carveout.py:28-34` further asserts presence of a
  string in `docs/Phase8-contract-audit-fixes/contract-audit.md` — that
  is a Phase folder, on the forbidden reading list, and is presumably
  also stale.
- **Intent violated:** §7.2 (audit implication: P2 doc-fix).
- **Suggested action:** rewrite the carve-out clause after the callback
  is deleted; delete the `test_audit_marks_383_as_conformant_under_carveout`
  test (which references a deleted Phase doc).

## F-strategist-011
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/strategist/context_shim.py:343-488`
  (`_render_positions_shim`).
- **Evidence:** Per intent §3.2 cluster 1, the prose-field cluster
  (`rationale` / `last_reviewed_reason` / `sell_reasons` / `update_reasons`
  / `close_reasons` / `trim_reasons` / `report.summary` / `reasoning`)
  is a flagged synonym candidate. Concretely in this module:
  `_render_positions_shim` reads `data.get("rationale")` and renders
  only that (lines 435, 479); `last_reviewed_reason` is set by the
  *executor* from `stance.rationale` (`executor/_verb_dispatch.py:235,
  251, 291, 311, 323`) and is then *not rendered* into the next tick's
  prompt (per `position_thesis.py:74`: "persisted for the audit trail
  but NOT rendered into the next tick's prompt"). One field is computed
  from another at every site (`last_reviewed_reason = stance.rationale
  or ""`), with no transformation — it is an alias.
- **Intent violated:** §3.2 cluster 1.
- **Suggested action:** investigate — `last_reviewed_reason` looks like
  redundant storage of `stance.rationale` at the moment of close /
  update / buy. If the audit trail genuinely needs a frozen copy at
  review time, document why; if not, derive it on render and delete the
  schema field.

## F-strategist-012
- **Category:** test-gap
- **Severity:** P2
- **Location:** strategist tests overall.
- **Evidence:** `test_enricher.py:154-164` exercises the no-op
  short-circuit (no decision in state → zero events yielded). The
  invariant the enricher carries — that `target_weights` is exhaustive
  over the watchlist and that `update`/`no_action` stances do NOT
  introduce weight changes — is covered for happy-path inputs but not
  for the degraded path: there is no test that asserts the enricher
  raises when the strategist's LLM payload arrives partially-validated
  (e.g. `intent=None` slipping past schema). `test_derivation.py`
  covers `StrategistContractViolation` for off-watchlist / intent=None,
  but the BaseAgent wrapper (`StrategistEnricher._run_async_impl`) is
  only tested for off-watchlist (`test_enricher_raises_on_off_watchlist_ticker`).
  An `intent=None` happy-path BaseAgent test would lock in §7.2 + the
  "loud raise beats corrupted decision" comment at `enricher.py:306`.
- **Intent violated:** test-policy §A.7 + §G.7 (silent-failure attractors).
- **Suggested action:** add a `test_enricher_raises_on_intent_none`
  test analogous to the off-watchlist test, using
  `TickerStance.model_construct(intent=None, …)` to bypass schema and
  prove the BaseAgent wrapper propagates the violation.

## F-strategist-013
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/agents/strategist/decision_writer.py:39-99`.
- **Evidence:** The `StrategistDecisionWriter` BaseAgent runs only to
  call `save_ticker_stance` and commit. Its sole production caller is
  `orchestrator/pipeline.py:163` via `build_strategist_decision_writer`.
  The agent shape (with `yield` placeholders to satisfy the async
  generator type, lines 49, 57, 99) is heavyweight for a single-side-
  effect step that yields nothing. Comment line 87 admits the "fallback
  to 'update'" is unreachable in production. Not actionable on its own
  — flagged for triage.
- **Intent violated:** n/a.
- **Suggested action:** investigate — could collapse to a regular
  function called from `executor.after_agent_callback` or from an
  `after_agent_callback` on `StrategistEnricher` (with the latter
  blocked by Rule 1 cross-tick durability — would need to remain a
  BaseAgent). Likely keep as-is unless a wider refactor merges
  persistence sites.

