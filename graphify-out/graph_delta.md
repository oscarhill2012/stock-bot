# graph_delta.md

_No changes since last `/graphify . --update`._

## 2026-05-11 — B5: smart_money feature extractor

Added the smart-money deterministic extractor under the Phase 4 Plan B work.
Introduces the `is_no_data` sparseness flag so the digest aggregator can skip
this analyst's verdict when no congressional filing data is available for a ticker.

- New/changed nodes: `src/contract/extractors/smart_money.py` → exports `extract_smart_money_features`, `_zero_features`, `_amount`
- New/changed edges: `tests/unit/contract/extractors/test_smart_money.py` → imports `extract_smart_money_features`
- Removed: nothing

## 2026-05-11 — B6: wire dual-emit callback into technical analyst

Replaced `make_exhaustive_validator` with `make_dual_emit_callback` in the
technical analyst agent. The module-level `_after` callback now handles
exhaustiveness checking AND writes `AnalystEvidence` records to
`state["technical_evidence"]` after each run. Legacy `state["technical_signals"]`
is left untouched for existing downstream consumers.

- New/changed nodes: `src/agents/analysts/technical/agent.py` — `_after` (module-level callback), updated `technical_analyst` and `_build_technical_analyst`
- New/changed edges: `agent.py` now imports `make_dual_emit_callback` (from `agents.analysts._common`) and `extract_technical_features` (from `contract.extractors.technical`); removed import of `make_exhaustive_validator`
- Removed: direct `make_exhaustive_validator` call in `agent.py`

## 2026-05-11 — Phase 4 Plan B: per-analyst extractors + dual-emit (B1–B9 wrap-up)

Completes Plan B across all four analysts. Each analyst agent now writes BOTH
the legacy `<Analyst>Signal` (`state["{analyst}_signals"]`) and the new
`AnalystEvidence` (`state["{analyst}_evidence"]`). Strategist still consumes
only the legacy signals — Plan C flips that.

The smart_money branch required a small additional rework: its before-gate is
allowed to short-circuit the LLM entirely, so the dual-emit helper gained a
`sparse=True` flag that disables the exhaustive re-prompt. `SmartMoneySignal`
was widened to extend `AnalystSignal` (gaining `direction` / `confidence` /
`key_factors`) so the dual-emit callback can translate it uniformly with the
other three analysts. The prompt was rewritten to emit one record per
watchlist ticker (neutral + confidence=0.0 when no activity).

- New nodes:
  - `src/contract/extractors/__init__.py`
  - `src/contract/extractors/technical.py` → `extract_technical_features`
  - `src/contract/extractors/fundamental.py` → `extract_fundamental_features`
  - `src/contract/extractors/sentiment.py` → `extract_sentiment_features`
  - `src/contract/extractors/smart_money.py` → `extract_smart_money_features` (see prior B5 entry)
- Changed nodes:
  - `src/agents/analysts/_common.py` — adds `make_dual_emit_callback(..., sparse=False)` helper
  - `src/agents/analysts/{technical,fundamental,sentiment,smart_money}/agent.py`
    — `after_agent_callback` is now `make_dual_emit_callback(...)` instead of
    `make_exhaustive_validator(...)`; smart_money passes `sparse=True`
  - `src/agents/analysts/smart_money/schema.py` — `SmartMoneySignal` now
    extends `AnalystSignal`; `conviction` is `Optional`
  - `src/agents/analysts/smart_money/prompts.py` — always-emit-per-ticker prompt
- New edges:
  - `agents.analysts._common.make_dual_emit_callback` ──uses──> `AnalystEvidence` + `AnalystVerdict` (`contract.evidence`)
  - each analyst's `_after` ──uses──> the matching `extract_{analyst}_features`
  - smart_money `SmartMoneySignal` ──extends──> `AnalystSignal`
- New state keys (write-only this plan): `technical_evidence`, `fundamental_evidence`, `sentiment_evidence`, `smart_money_evidence`. Legacy `{analyst}_signals` keys still populated for `attribution_writer` / `memory_writer`.
- New fixtures: `tests/fixtures/contract/{technical_aapl,fundamental_aapl,sentiment_aapl,smart_money_aapl,smart_money_no_data}.json`.
- Removed: none (legacy signal path intact).

## 2026-05-11 — C1: TickerStance schema

Introduces `TickerStance`, the per-ticker decision substrate the strategist
will emit on each tick (Phase 4 Plan C foundation). Nothing imports this module
yet — downstream tasks C4, C7, C9, C10 will wire it in.

- New nodes:
  - `src/agents/strategist/stance_schema.py` → exports `TickerStance`
- New edges:
  - `tests/unit/agents/strategist/test_stance_schema.py` → imports `TickerStance`
- New test package: `tests/unit/agents/strategist/__init__.py`
- Removed: nothing

## 2026-05-11 — C2: lifecycle derive_lifecycle_action

Introduces `lifecycle.py` with `derive_lifecycle_action`, `OPEN_EPSILON`, and
`SIZE_CHANGE_EPSILON`. Classifies a per-ticker weight delta into one of five
lifecycle actions: open, close, trim, add, hold. Nothing imports this module
yet — downstream tasks C4 (`derive_legacy_fields`) and C9 (strategist
after-callback) will consume it.

- New nodes:
  - `src/agents/strategist/lifecycle.py` → exports `derive_lifecycle_action`, `OPEN_EPSILON`, `SIZE_CHANGE_EPSILON`, `LifecycleAction`
- New edges:
  - `tests/unit/agents/strategist/test_lifecycle.py` → imports `derive_lifecycle_action`, `OPEN_EPSILON`, `SIZE_CHANGE_EPSILON`
- Removed: nothing

## 2026-05-11 — C5: held_view render_held_positions_view

Adds `held_view.py` with `render_held_positions_view`. Formats all held
positions from `state["positions"]` into a structured multi-line text block
for prompt injection. Accepts either `PositionThesis` instances or their
`model_dump(mode="json")` dict form; likewise accepts `Portfolio` instances or
dicts. Total function — corrupt thesis entries are silently skipped at the
rendering boundary. Will be wired into the strategist's
`before_agent_callback` in C9.

- New nodes:
  - `src/agents/strategist/held_view.py` → exports `render_held_positions_view`, `_coerce_thesis`, `_coerce_portfolio`, `_format_one`
- New edges:
  - `held_view.py` → imports `PositionThesis` (from `agents.strategist.schema`), `Portfolio` (from `broker.portfolio`)
  - `tests/unit/agents/strategist/test_held_view.py` → imports `render_held_positions_view`, `PositionThesis`, `Portfolio`, `Position`
- Removed: nothing

## 2026-05-11 — C12: StrategistDecisionWriter agent

Adds `StrategistDecisionWriter`, a `BaseAgent` subclass that reads
`state["strategist_decision"]` and `state["portfolio"]` from the invocation
context, derives the lifecycle action for each ticker via
`derive_lifecycle_action`, and persists one `TickerStanceRow` per stance via
`save_ticker_stance`. Returns early if `db_session` is `None` or if no decision
is present in state. Factory function `build_strategist_decision_writer` mirrors
the `AttributionWriter` pattern.

- New nodes:
  - `src/agents/strategist/decision_writer.py` → exports `StrategistDecisionWriter`, `build_strategist_decision_writer`
- New edges:
  - `decision_writer.py` → imports `derive_lifecycle_action` (from `agents.strategist.lifecycle`), `StrategistDecision` (from `agents.strategist.schema`), `Portfolio` (from `broker.portfolio`), `save_ticker_stance` (from `orchestrator.persistence`)
  - `tests/unit/agents/strategist/test_decision_writer.py` → imports `StrategistDecisionWriter`, `build_strategist_decision_writer`
- Removed: nothing

## 2026-05-11 — Phase 4 Plan C: strategist v2 against new contract

Strategist now emits per-ticker `TickerStance` and consumes the per-ticker
`TickerEvidence` built from Plan B's per-analyst evidence. Held-position context
rendered into the prompt. Per-ticker stances persisted to `TickerStanceRow`.
TradeLog gains `opening_tick_id` / `closing_tick_id` outcome attribution FKs.
Pipeline grows from 7 → 8 stages (new `StrategistDecisionWriter` between
strategist and risk-gate). Legacy `target_weights` / `new_positions` /
`close_reasons` / `trim_reasons` flat fields on `StrategistDecision` are now
derived server-side from `decision.stances`, not emitted directly by the LLM.

- New nodes:
  - `src/agents/strategist/schema.py` → `TickerStance`, `StrategistDecision` (v2 per-ticker structured output)
  - `src/agents/strategist/derivation.py` → `TickContext`, `derive_legacy_fields`
  - `src/agents/strategist/lifecycle.py` → `derive_lifecycle_action`
  - `src/agents/strategist/evidence_view.py` → `render_ticker_evidence`
  - `src/agents/strategist/held_view.py` → `render_held_positions_view`
  - `src/agents/strategist/decision_writer.py` → `StrategistDecisionWriter`, `build_strategist_decision_writer`
  - `src/contract/evidence.py` → `AnalystEvidence`
  - `src/contract/ticker_evidence.py` → `TickerEvidence`
  - `src/contract/digest.py` → `build_ticker_evidence`
  - `src/contract/digest_defaults.py` → `DEFAULT_ANALYST_WEIGHTS`
  - `src/orchestrator/persistence.py` → `TickerStanceRow` (SQLAlchemy ORM row), `save_ticker_stance`
  - `src/orchestrator/tick.py` → `_build_initial_state` (helper extracted from `run_once`)
  - `tests/integration/test_strategist_v2_smoke.py` (gated LLM smoke; skipped unless `RUN_LLM_TESTS=1`)
- New edges:
  - `strategist_agent --before--> _composite_before_callback --calls--> _held_view_before_callback + _evidence_view_before_callback`
  - `strategist_agent --after--> _strategist_validation_callback --calls--> derive_legacy_fields`
  - `_evidence_view_before_callback --calls--> contract.digest.build_ticker_evidence`
  - `StrategistDecisionWriter --persists--> TickerStanceRow`
  - `orchestrator.pipeline.build_pipeline --includes--> StrategistDecisionWriter` (stage 4 of 8)
  - `orchestrator.tick.run_once --calls--> _build_initial_state` (extracted helper that seeds `state["portfolio"]` from broker)
  - `executor.ExecutorAgent` BUY branch → writes `state["positions"][ticker]` from `state["strategist_decision"]["new_positions"]`
  - `executor.ExecutorAgent` SELL branch → populates `opening_tick_id` / `closing_tick_id` on `TradeLogRow`
  - Each per-analyst extractor (`agents/analysts/*/extractor.py`) → `contract.evidence.AnalystEvidence` (dual-emit pattern; analysts write both legacy state keys and `state["{dim}_evidence"]`)
- Modified:
  - `StrategistDecision` gains `stances` field (list[TickerStance])
  - `PositionThesis` gains `opened_tick_id`
  - `TradeLogRow` gains `opening_tick_id`, `closing_tick_id`
  - `STRATEGIST_INSTRUCTION` prompt template rewritten to consume `{ticker_evidence}` + `{held_positions_view}`
- State key changes: new `state["ticker_evidence"]` (rendered string) + `state["ticker_evidence_objects"]` (list[TickerEvidence dumps]); legacy `*_signals` keys still written by analysts and consumed by `attribution_writer` / `memory_writer` until Plan D.
- Removed: nothing (legacy dual-emit paths intact)
