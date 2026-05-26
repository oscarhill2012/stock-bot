# Source audit — src/orchestrator/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 6 (`__init__.py`, `tick.py`, `pipeline.py`, `state.py`, `persistence.py`, `stock_picker.py`)
**Findings:** 3 P0 · 4 P1 · 3 P2 · 2 P3

## Summary

The orchestrator owns Phase 1 wiring (`make_session_service`, `build_pipeline`), Phase 2 hydration on the live path (`tick.run_once` → `_build_initial_state`), and the durable SQLAlchemy schema underneath the ADK session layer. The dominant themes are (1) the live tick entrypoint diverges materially from the backtest driver's Phase 2 conventions — it ships a raw `datetime` into `create_session`, never injects the observability `temp:` handles the agents read in backtest, and never coerces seeds the same way; (2) cross-tick fields `memory_buffer` / `day_digest` are seeded with empty values in violation of §B Phase 2 / §E; (3) the §C-Rule 7 lifecycle/pipeline split is partially observed — pipeline agents (`EvidenceWriter`, `StrategistDecisionWriter`, `SnapshotterAgent`, `ExecutorAgent`) import `orchestrator.persistence` directly to write durable rows mid-pipeline. Cross-subsystem dependency for the consolidator: the persistence module is consumed by `agents/`, `backtest/`, `lifecycle/`, `baselines/` and `scripts/` — anything proposing to enforce Rule 7 by relocation will touch all five.

## Findings

### P0-01 · C4 contract violation · `memory_buffer` / `day_digest` seeded empty at Phase 2 instead of hydrated from persistence

- **Location:** `src/orchestrator/tick.py:151-152` (live path), mirrored in `src/backtest/driver.py` Phase 2 builder.
- **Confidence:** high
- **Description:**
  `_build_initial_state` writes `"memory_buffer": []` and `"day_digest": ""` directly into the fresh tick state. Per `contract-invariants.md` §A both fields are **cross-tick** (Lifetime column says "cross-tick", Source of truth says "Persistence layer (see §E)"), and §B Phase 2 explicitly names this exact failure mode: "A common failure mode is treating a cross-tick field as tick-scoped — seeding it with an empty value at Phase 2 instead of reading from persistence. This is a Phase 2 violation." Spec E status for both fields is "Deferred" (Spec C), but the contract is still target-state — the empty-seed hard-codes loss of cross-tick learning. Today's `MemoryWriter` reads `state.get("memory_buffer", [])`, accumulates within a tick, and writes back; the empty seed on every tick erases everything the previous tick learned. The user memory entry `feedback_silent_failures_loud_tests` and the snapshotter's own comment at `src/agents/snapshot/agent.py:137-142` already flag this as a known issue tracked in `docs/todo-fixes.md` Group 2.5.
- **Suggested action:**
  Once Spec C lands the chosen `memory_buffer` / `day_digest` persistence layer, remove the empty-seed lines so the values are populated from persistence at Phase 2. Until then, mark these as known-violating in `docs/todo-fixes.md` (already done) and have tests assert they survive across ticks (currently they cannot).

### P0-02 · C5 silent-failure attractor · `except (AttributeError, BaseException)` swallows every exception including `KeyboardInterrupt` / `SystemExit`

- **Location:** `src/orchestrator/tick.py:260-270`
- **Confidence:** high
- **Description:**
  The exception clause around `async for _ in events:` is written as `except (AttributeError, BaseException) as exc:`. `BaseException` is the root of the exception hierarchy, so the `AttributeError` member is redundant *and* the clause catches `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, and every `Exception` subclass — including programming errors that signal the pipeline genuinely failed. The comment narrates a specific ADK 1.32 teardown bug (`AttributeError('NoneType'.partial)` + `BaseExceptionGroup` from parallel-agent finalisers), but the actual catch is wider than that bug. After the swallow the function reads `session_service.get_session(...)` regardless and returns whatever state happens to be there. Per `test-policy.md` §A.7 and the `feedback_silent_failures_loud_tests` memory, this is the canonical silent-failure attractor: the pipeline can crash for real and the live caller still gets a "successful" return. There is no log severity escalation, no re-raise after a `state["executions"]` sanity check, and no path that distinguishes "teardown bug after success" from "pipeline blew up mid-run".
- **Suggested action:**
  Narrow the clause to the two specific known ADK shapes (`AttributeError` whose message matches the teardown bug fingerprint, and `BaseExceptionGroup` whose constituents are `GeneratorExit`), and re-raise everything else. Alternatively, after swallowing, assert positively that the session reached `last_snapshot` (mirroring the backtest driver's per-tick assertion in `src/backtest/driver.py:393-401`) and re-raise if it did not — Rule 8 observability/handshake key already exists for exactly this.

### P0-03 · C4 contract violation · live path writes a `datetime` into `create_session(state=...)` that `DatabaseSessionService` cannot JSON-serialise

- **Location:** `src/orchestrator/tick.py:148` (writer), `src/orchestrator/tick.py:242-247` (consumer call).
- **Confidence:** medium
- **Description:**
  `_build_initial_state` returns `"as_of": datetime.now(tz=UTC)` — a real `datetime` instance. `run_once` passes the dict straight into `session_service.create_session(state=initial_state)`. The session service is now `DatabaseSessionService` (see `persistence.make_session_service`, which now raises rather than fall back to in-memory). The backtest driver — which has been hardened against this exact failure — ISO-coerces every `datetime` before seeding at `src/backtest/driver.py:494-499`, with a comment explaining "DatabaseSessionService serialises state via json.dumps, which cannot handle native datetime objects." The live path skipped that fix. The user memory entry `feedback_as_of_boundary_coercion` is unambiguous: "every datetime write to state ISO-stringifies first; backtest DatabaseSessionService can't hold datetime". Because the live entrypoint has not been exercised against the real `DatabaseSessionService` yet (pre-deployment per memory), this is a *latent* P0 that will fire on first live tick. Confidence is medium because there might be ADK serialisation behaviour I have not verified end-to-end, but the cited backtest comment is direct evidence the same code shape fails.
- **Suggested action:**
  Apply the same ISO-coercion to `_build_initial_state` or to the `create_session` call site in `run_once`, mirroring `driver.py:494-499`. Better: extract the coercion into a helper in `orchestrator/` (e.g. `_seed_state_for_adk(state)`) and use it from both lifecycles so the symmetric Phase 2 invariant is enforced in one place.

### P1-01 · C4 contract violation · pipeline agents read `orchestrator.persistence` directly mid-tick

- **Location:** `src/orchestrator/pipeline.py:157-169` (composition), with the actual persistence touches at `src/agents/contract/evidence_writer.py:71`, `src/agents/strategist/decision_writer.py:62`, `src/agents/snapshot/agent.py:121`, `src/agents/executor/agent.py:204`.
- **Confidence:** medium
- **Description:**
  `contract-invariants.md` §C-Rule 7 says the pipeline "reads from and writes to **state**. It does not read from or write to the persistence layer (§E), the broker, or any provider for cross-tick data." Four agents that are sub-agents of the `HourlyTick` `SequentialAgent` *do* import `orchestrator.persistence` and write SQLAlchemy rows mid-pipeline (`save_analyst_evidence`, `save_ticker_evidence`, `save_ticker_stance`, `save_portfolio_snapshot`, `save_trade_log_entry`). The persistence-bearing tables here are not the §A cross-tick fields (those go through ADK `user_state` via Rule 1's auto-yielded callback path — Spec B), but they *are* durable rows the lifecycle layer is supposed to own. The Spec B clarification added to Rule 7 carves out `user:`-prefixed keys via ADK; it does not carve out direct SQLAlchemy writes. This isn't a runtime bug today — backtest and live are both wired and the writes work — but it is the architectural seam consolidation should look at. Pipeline composition (`pipeline.py:127-169`) is the right place to call this out because that is where the seam is decided. Filed as P1 (not P0) because the writes are tail-of-pipeline side-effects, not data the pipeline reads back, and the rule itself is more nuanced when "lifecycle" means "the §E persistence subsystem that lives behind the ADK layer".
- **Suggested action:**
  Either (a) explicitly carve these four writers out in `contract-invariants.md` §C-Rule 7 as additive lifecycle helpers analogous to the Spec B Executor `after_agent_callback`, or (b) lift them above the pipeline into a Phase-4 tick wrapper that drains `state` after `runner.run_async` completes. Option (a) is closer to the existing code, but option (b) is closer to what Rule 7 actually says.

### P1-02 · C4 contract violation · live `_fetch_reference_prices` bypasses the provider registry

- **Location:** `src/orchestrator/tick.py:100-102`
- **Confidence:** high
- **Description:**
  `_fetch_reference_prices` imports `data.providers.stats.yfinance._bulk_download` directly. The user memory entry `feedback_provider_switching_must_be_one_line` is explicit: "every registered data provider shares one signature; swaps are config/data.json edits, never code changes; keep fallback 'shell' providers registered". Reaching into a specific provider's leaf function makes the live reference-price fetch unswappable without editing `tick.py`. The backtest driver uses cache-backed providers and never reaches into yfinance directly. The fix is structural but small — call the registered stats provider's bulk-history surface rather than the yfinance-specific symbol. Also borderline §B Phase 1 ("provider implementations wired") — the wiring exists, the live path just doesn't use it.
- **Suggested action:**
  Replace the direct `_bulk_download` import with a call through the stats-provider registry (e.g. `data.providers.registry.get_stats_provider().bulk_download(...)` or equivalent). Keep the function signature so the test surface does not change.

### P1-03 · C7 doc/code drift · `TickState` is referenced by exactly one test and not used in `src/` at all

- **Location:** `src/orchestrator/state.py:60-101` (class definition), only reference outside the file is `tests/unit/test_tick_state.py`.
- **Confidence:** high
- **Description:**
  `TickState` is a Pydantic model documenting "complete shared state schema" — but no code in `src/` constructs, validates, or reads it. Grep `TickState` across `src/`, `scripts/`, `tests/` returns one definition + one self-contained test that only asserts default values. The actual pipeline state is a plain `dict` everywhere (built by `_build_initial_state` in `tick.py`, mutated by ADK). Worse, the class's `# Persistent across ticks (loaded from and saved to the ADK session store)` comment block (lines 82-91) still groups `memory_buffer`, `day_digest`, `last_executed_tick_id` together — but `last_executed_tick_id` is now tick-scoped per §A and `memory_buffer` / `day_digest` are Spec C-deferred. The class is a documentation artefact masquerading as a schema. Filed as C7 doc/code drift rather than C1 dead code because the *contract* this class is supposed to express is real — the implementation just no longer matches.
- **Suggested action:**
  Either (a) make the dict-shape live by validating Phase 2 output against `TickState` at the tick boundary (turning the model into a contract enforcement point), or (b) delete the class and its lone test, and move the comment-as-doc content into `contract-invariants.md` §A where it already half-lives. The orphan state at line 82-91 is misleading either way.

### P1-04 · C5 silent-failure attractor · `_dispatch_app_name` silently re-classifies unknown broker modes as PAPER

- **Location:** `src/orchestrator/tick.py:225-227`
- **Confidence:** medium
- **Description:**
  `_raw_mode = getattr(broker, "mode", "paper")` then the next line says: `_broker_mode = BrokerMode(_raw_mode) if _raw_mode in BrokerMode._value2member_map_ else BrokerMode.PAPER`. The fallback path silently demotes any unrecognised mode value to PAPER. The function docstring at `_dispatch_app_name` is the opposite — it raises `ValueError` on unknown enum members. So the function itself is loud; the call site swallows the loudness. A broker built with `mode="live"` but typo'd as `mode="livee"` would route to `StockBot-paper` and a live tick would silently land in the paper user_state namespace. Per `test-policy.md` §A.7, "Treat degradation paths as failures in happy-path tests" — this is a degradation path on the most consequential routing decision the bot makes. The intent here was a `FakeBroker` accommodation (the inline comment says "FakeBroker does not expose `.mode`; default to PAPER"), but the implementation accommodates much more than that.
- **Suggested action:**
  Split the two concerns: handle the missing-attribute case (FakeBroker) by name explicitly, and let `BrokerMode(raw_mode)` raise its native `ValueError` on any *present-but-invalid* value. Equivalently: `mode = getattr(broker, "mode", None); _broker_mode = BrokerMode.PAPER if mode is None else BrokerMode(mode)`.

### P2-01 · C1 dead code · `TickState` (already filed as P1-03 doc drift, but also dead)

- **Location:** `src/orchestrator/state.py:60-101`
- **Confidence:** high
- **Description:**
  Cross-filed because the class genuinely has no live `src/` callers. The findings overlap; consolidation can collapse them. See P1-03 for the substantive description and suggested action.
- **Suggested action:**
  Resolved by P1-03's action — pick (a) or (b) there.

### P2-02 · C3 overabstraction · `_build_strategist` and `_build_memory_writer` are zero-logic forwarders

- **Location:** `src/orchestrator/pipeline.py:96-124`
- **Confidence:** medium
- **Description:**
  Both are thin shims. `_build_strategist` delegates to `agents.strategist.agent.build_strategist()` and the docstring openly admits the only reason for its existence: "Kept as a stable module-level symbol in `orchestrator.pipeline` so that existing backtest smoke tests which do `mock.patch('orchestrator.pipeline._build_strategist', ...)` continue to work without churn". `_build_memory_writer` is even thinner: one-liner that returns `MemoryWriter()`. Neither buys flexibility — they are monkeypatch seams for tests. Per the rubric C3 exception text, "interfaces required by the contract for backtest ⇄ live symmetry" are exempt, but these are not contract seams, they are test seams. The `mock.patch` arrangement could equivalently patch `agents.strategist.agent.build_strategist` directly. Confidence is medium because the existing test surface is real — removing the shims requires updating the patch targets, and the consolidator may prefer to leave them rather than churn tests.
- **Suggested action:**
  Inline both into `build_pipeline` and update the offending test patch paths to point at the canonical builder locations. Low priority — the indirection is harmless, just unnecessary.

### P2-03 · C1 dead code · `BrokerMode._value2member_map_` is a private Enum internal exposed in pipeline code

- **Location:** `src/orchestrator/tick.py:226`
- **Confidence:** low
- **Description:**
  Not strictly dead, but `BrokerMode._value2member_map_` is the leading-underscore Enum internal — the supported pattern is `try: BrokerMode(x) except ValueError`. Using the private member as a "is this value valid" probe is a code smell on the same line as the P1-04 finding above. Filed P2 because it disappears entirely once P1-04 is fixed (the new shape uses `BrokerMode(x)` directly and catches the resulting `ValueError`).
- **Suggested action:**
  Absorbed into P1-04's fix.

### P3-01 · C7 doc/code drift · `_build_strategist` docstring claims a 2-sub_agent shape

- **Location:** `src/orchestrator/pipeline.py:108-114`
- **Confidence:** high
- **Description:**
  The docstring says "`build_strategist()` returns `SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent]]`". Per `graphify-out/graph_delta.md` (2026-05-25 entry), `build_strategist()` now returns a 3-sub_agent SequentialAgent: `[StrategistContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]`. The orchestrator's documentation has not caught up. Cosmetic.
- **Suggested action:**
  Update the docstring to reflect the 3-element shape and mention the enricher.

### P3-02 · C7 doc/code drift · `tick.py` docstring still says "hourly tick"

- **Location:** `src/orchestrator/tick.py:1, 164, 217-218` (multiple sites in module/function docstrings referring to "hourly tick" or the "HourlyTick" SequentialAgent).
- **Confidence:** low
- **Description:**
  The pipeline's name is still `HourlyTick` (`pipeline.py:158`) but the schedule config is per `config/schedule.json` and the live broker mode supports paper/live with whatever cadence is configured. The `HourlyTick` name is historical. Minor cosmetic — confidence low because the name is a public ADK agent identifier and renaming could surface in traces/decision logs.
- **Suggested action:**
  Defer until a broader naming pass. Note in `docs/todo-fixes.md` if not already there.
