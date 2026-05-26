# strategist — vocabulary inventory

Exhaustive list of identifiers, keys and fields the strategist module
reads, writes, or defines. Source files audited: `src/agents/strategist/`
(recurse). One line per item.

---

## State keys — read

- `state["as_of"]` — historical clock (backtest); may arrive as ISO str.
- `state["recorded_at"]` — fallback timestamp (live).
- `state["portfolio"]` — Portfolio dump (broker working copy).
- `state["tickers"]` — watchlist for this tick.
- `state["tick_id"]` — deterministic per-tick identifier.
- `state["user:positions"]` — thesis book (canonical).
- `state["positions"]` — bare-key fallback (executor-internal; per §7.3 should be dropped).
- `state["user:thesis"]` — standing market thesis (canonical).
- `state["user:active_stances_initialised"]` — first-tick flag source.
- `state["user:current_tick_index"]` — staleness reference index.
- `state["user:closed_trades_log"]` — round-trip log rendered into prompt.
- `state["technical_evidence"]` — per-ticker analyst evidence rows.
- `state["fundamental_evidence"]` — per-ticker analyst evidence rows.
- `state["news_evidence"]` — per-ticker analyst evidence rows.
- `state["smart_money_evidence"]` — per-ticker analyst evidence rows.
- `state["strategist_decision"]` — narrow LlmAgent output (enricher reads).
- `state["temp:_obs_strategist_call_decision"]` — observability call record.
- `state["temp:_obs_strategist_retries"]` — retry counter.
- `state["temp:_trace"]` — trace writer handle (via `_trace_maybe`).

## State keys — write (via state_delta)

- `state["strategist_decision"]` — overwritten by `StrategistEnricher` with full dump.
- `state["user:active_stances_initialised"]` — set True by enricher after first success.
- `state["temp:strategist_mode"]` — cold-start vs incremental prompt text.
- `state["temp:held_positions_view"]` — rendered thesis book.
- `state["temp:first_tick_flag"]` — `"True"`/`"False"` prompt slot.
- `state["temp:ticker_evidence"]` — rendered per-ticker evidence text.
- `state["temp:ticker_evidence_objects"]` — JSON-dumped TickerEvidence list.
- `state["temp:recent_trades_view"]` — rendered closed-trade log.
- `state["thesis"]` — bridge slot for `{thesis}` prompt placeholder (legacy bare-key).
- `state["temp:_last_schema_error"]` — empty default for retry feedback slot.

## Schema fields — Pydantic

### `TickerStance` (stance_schema.py)
- `ticker: str`
- `intent: Literal["buy","sell","update","no_action"]`
- `weight: float | None`
- `rationale: str | None`
- (validator `_require_intent_fields` — verb-conditional)
- `model_config = ConfigDict(extra="forbid")`

### `StrategistLLMDecision` (schema.py)
- `stances: list[TickerStance]`
- `decision_tag: str`
- `reasoning: str`
- `thesis: str | None`
- `confidence: float [0,1]`

### `StrategistDecision` (schema.py)
- `stances: list[TickerStance]`
- `target_weights: dict[str, float]`
- `decision_tag: str`
- `reasoning: str` (capped)
- `thesis: str | None` (capped)
- `confidence: float [0,1]`
- `sell_reasons: dict[str, str]`
- `update_reasons: dict[str, str]`

### `PositionThesis` (position_thesis.py)
- `ticker: str`
- `opened_at: datetime | None`
- `opened_tick_id: str | None`
- `opened_price: float | None`
- `weight: float | None`
- `rationale: str`
- `last_reviewed_at: datetime`
- `last_reviewed_decision: Literal["buy","sell","update","no_action"]`
- `last_reviewed_reason: str`
- `thesis_last_updated_tick: int`
- `model_config = ConfigDict(extra="forbid")`

### `TickContext` (derivation.py dataclass)
- `current_weights: dict[str, float]`
- `watchlist: list[str]`
- `held_tickers: set[str] | None`
- `tick_id: str | None`
- `decision_tag: str | None`
- `now: datetime | None`

### `DerivedFields` (derivation.py dataclass)
- `target_weights: dict[str, float]`
- `sell_reasons: dict[str, str]`
- `update_reasons: dict[str, str]`
- `decision_tags: dict[str, str]` (computed but unused downstream — see F-strategist-006)

## Config keys

- `config/models.json :: strategist` — model ID.
- `config/strategist.json :: llm.max_output_tokens`
- `config/strategist.json :: llm.timeout_seconds`
- `config/strategist.json :: llm.timeout_retries`
- `config/strategist.json :: llm.schema_retries`
- `config/strategist.json :: decision_caps.reasoning_max_chars`
- `config/strategist.json :: decision_caps.thesis_max_chars`
- `config/strategist.json :: stance_caps.rationale_max_chars`
- `config/strategist.json :: schema_cap()` (slack_percent helper)
- `config/risk_gate.json :: max_position_weight`
- `config/risk_gate.json :: max_delta_per_ticker` (used as buy-delta cap)
- `config/risk_gate.json :: cash_floor_weight`
- env `STOCKBOT_TERMINAL_LOG`
- env `STOCKBOT_TRACE`
- env `STRATEGIST_PROBE_DIR`

## Internal verbs / function names

- `build_strategist()` — factory in `agent.py`.
- `_strategist_validation_callback()` — legacy callback shim (DEAD, F-001).
- `validate_and_enrich(state) -> dict | None` — pure enrichment core (enricher.py).
- `StrategistEnricher` — BaseAgent (sole live enrichment path).
- `StrategistEnricher._run_async_impl()`.
- `build_strategist_enricher()` — unused factory (DEAD, F-008).
- `StrategistContextShim` — BaseAgent.
- `StrategistContextShim.render(state) -> dict` — pure helper for unit tests.
- `StrategistContextShim._run_async_impl()`.
- `_render_positions_shim(positions, *, current_tick_index, portfolio)` — thesis-book renderer.
- `_render_recent_trades(closed_log)` — round-trip log renderer.
- `_coerce_portfolio(value) -> Portfolio` — duplicated in context_shim.py and enricher.py (F-003).
- `_index_evidence(state, key) -> dict[str, AnalystEvidence]`.
- `_log_offending_decision(tick_id, decision, violation)`.
- `_fmt_opened_at(raw_val)` (nested in `_render_positions_shim`).
- `StrategistDecisionWriter` — BaseAgent persisting per-ticker stances.
- `build_strategist_decision_writer(db_session)`.
- `derive_decision_fields(stances, ctx) -> DerivedFields`.
- `derive_decision_tag(*, prior, new) -> str` — per-ticker intent tag helper.
- `StrategistContractViolation(RuntimeError)`.
- `render_ticker_evidence(items)` — in `evidence_view.py` (DEAD module, F-004).
- `_format_per_analyst(te)` — in `evidence_view.py` (dead).
- `_format_features(features)` — in `evidence_view.py` (dead).
- `STRATEGIST_INSTRUCTION` (prompts.py) — final prompt template.
- `COLD_START_MODE_TEMPLATE` / `INCREMENTAL_MODE_TEMPLATE` (prompts.py).
- `_RAW_INSTRUCTION` (prompts.py) — pre-substitution template.

## Stance verbs (canonical)

- `buy` — entry/add; requires weight + rationale; weight ≤ 0.05.
- `sell` — reduce/close; rationale required; weight optional (absent = full close).
- `update` — prose-only; rationale required; no weight; no trade.
- `no_action` — explicit "considered, no change"; ticker + intent only.

## Decision tags (derive_decision_tag output)

- `entry`, `ramp`, `trim`, `exit`, `hold_flat`, `hold`.

## Prose-field cluster (synonym candidates — intent §3.2)

- `TickerStance.rationale` — per-stance prose (source of truth).
- `PositionThesis.rationale` — per-position prose (mutated on buy/update).
- `PositionThesis.last_reviewed_reason` — set verbatim from `stance.rationale` by executor (F-011).
- `StrategistDecision.sell_reasons` — derived: ticker → `stance.rationale` for sell stances.
- `StrategistDecision.update_reasons` — derived: ticker → `stance.rationale` for update stances.
- `StrategistDecision.reasoning` — tick-level narrative (LLM-emitted).
- `StrategistDecision.thesis` — standing thesis (LLM-emitted).
- `StrategistLLMDecision.thesis` — same field, narrow shape.

## Cross-module touch points

- Imports `contract.digest.build_ticker_evidence`.
- Imports `contract.digest_defaults.DEFAULT_ANALYST_WEIGHTS`.
- Imports `contract.evidence.AnalystEvidence`.
- Imports `contract.strategist_prompt.render_all_ticker_blocks` (live renderer).
- Imports `contract.ticker_evidence.TickerEvidence`.
- Imports `broker.portfolio.{Portfolio, Position}`.
- Imports `orchestrator.state.ORDER_EPSILON`.
- Imports `orchestrator.persistence.save_ticker_stance`.
- Imports `data.timeguard.resolve_as_of`.
- Imports `observability.trace._trace_maybe` and `_extract_content_text`.
- Imports `observability.terminal_log.{make_observability_callbacks, emit_analyst_summary}`.
- Imports `agents.analysts._common.{_chain_after, _chain_before}`.
- Imports `agents.llm_retry.{RetryingAgentWrapper, build_retry_policies}`.
