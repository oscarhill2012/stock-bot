# Codebase intent — synthesised 2026-05-26

This document is the agreed "what the codebase is supposed to do" reference for
the audit. It was synthesised from three independent intent drafts
(`intent/agent-{1,2,3}.md`). Sections marked **[AGREED]** reflect unanimous or
near-unanimous claims across drafts. Sections marked **[DISAGREEMENT]** capture
genuine differences requiring human adjudication. Sections marked
**[UNCERTAIN]** are open questions all drafters flagged.

Spot-checks against `docs/contract-invariants.md` and `src/` were performed for
contested claims; verification notes are inlined as `verified: <file:line>`.

---

## 1. System purpose

**[AGREED]**

StockBot is an AI-driven stock trading bot that runs on a tick cadence (NYSE
open/close phases) and uses a Google ADK multi-agent pipeline to make
buy/sell/update/hold decisions for a watchlist of equities. Each tick:

1. Pulls market data (price history, fundamentals, news, social sentiment, and
   — when reinstated — smart-money flow) through a pluggable provider layer.
2. Routes that data through four specialist analyst agents that emit per-ticker
   directional verdicts (lean + magnitude + confidence) plus evidence rows.
3. Feeds an aggregated per-ticker view (`TickerEvidence`) and the running
   thesis book to an LLM strategist that emits one stance verb per watchlist
   ticker (`buy` / `sell` / `update` / `no_action`) with a rationale.
4. Applies deterministic risk constraints via a `RiskGate` (concentration, cash
   floor, per-trade delta, turnover) and converts clamped weights into broker
   orders.
5. Submits orders via a broker abstraction (`Trading212Broker` live,
   `FakeBroker` for backtest), records fills, and updates the persistent
   thesis book (`user:positions`) and standing market thesis (`user:thesis`).

The same pipeline topology runs in two lifecycles:

- **Live** — one Cloud Run Job invocation per tick, cold-started.
- **Backtest** — one long-lived process iterating an NYSE-scheduled tick
  sequence against a golden SQLite cache of historical data.

`contract-invariants.md` is the binding contract: both lifecycles must produce
the same pipeline outputs, which is enforced by forbidding any reliance on
in-process state survival between ticks.

The bot is **pre-deployment** — neither live nor paper instance is running.

---

## 2. Module intents

### 2.1 analysts — `src/agents/analysts/`

**[AGREED] Purpose.** Emit per-ticker directional verdicts (bullish / bearish /
neutral + magnitude + confidence) for five signal domains: technical,
fundamental, news, social, and smart-money. Each analyst owns one output key
so concurrent writes do not collide (Rule 4).

**Inputs.** `state["tickers"]`, `state["reference_prices"]`,
`state["portfolio"]`, and per-domain raw data fetched into `temp:*_data` keys
by `before_agent_callback` hooks (or per-ticker fetch agents for the LLM
analysts).

**Outputs.** Two parallel families of state keys per domain:
- `{domain}_verdicts` — list/`VerdictBatch` of `AnalystVerdict` records
  (verified: `src/agents/analysts/technical/agent.py:153`,
  `src/agents/analysts/news/joiner.py:147`).
- `{domain}_evidence` — list of `AnalystEvidence` dumps (verdict + extracted
  feature vector + metadata), written by an after-callback evidence writer
  (verified: `src/agents/analysts/social/agent.py:17`,
  `src/agents/analysts/news/joiner.py:148`).

**Key invariants:**
- Deterministic analysts (`Technical`, `Social`) are `BaseAgent` subclasses
  using an extract → derive pattern with no LLM call.
- LLM analysts (`News`, `Fundamental`) use Phase 9 per-ticker fan-out:
  `SequentialAgent[FetchAgent, ParallelAgent[per-ticker branches], JoinerAgent]`
  so one ticker's 429 backoff does not block others.
- Every per-ticker LLM branch is wrapped by `IsolatedFailureWrapper` so a
  branch failure logs but does not abort the tick; the joiner synthesises a
  no-data verdict for missing keys.
- LLM analysts populate `AnalystReport` (summary + 2-4 `ReportDriver` rows);
  deterministic analysts leave `report=None`.
- All writes ride on `EventActions(state_delta=...)` (Rule 1).
- `smart_money` analyst is shelved (no PIT-correct providers); it remains
  wired to emit a canonical empty/no-data verdict so downstream consumers see
  a consistent shape. **[DISAGREEMENT — see §6.1]** on whether it is "wired
  but empty" or "unwired".

### 2.2 strategist — `src/agents/strategist/`

**[AGREED] Purpose.** Aggregate per-ticker analyst evidence with the running
thesis book and standing market thesis, then emit one `StrategistDecision` per
tick containing a stance per watchlist ticker. The strategist is the sole
agent responsible for position sizing and duration decisions.

**Inputs.** `temp:held_positions_view`, `temp:ticker_evidence`,
`temp:ticker_evidence_objects` (populated by `StrategistContextShim`),
`user:positions` (thesis book), `user:thesis` (standing thesis), per-domain
evidence keys, `reference_prices`. `memory_buffer` and `day_digest` are
contracted inputs but deferred (Spec C).

**Output.** `state["strategist_decision"]` — a `StrategistDecision` (full
shape) carrying `stances` list, `target_weights` dict (one entry per watchlist
ticker), `decision_tag`, prose reasoning, and optional `thesis_revision`.

**Key invariants:**
- Composition is `SequentialAgent[StrategistContextShim,
  RetryingAgentWrapper[LlmAgent], StrategistEnricher]` — the retry wrapper sits
  *inside* the sequential so the ContextShim's `state_delta` reaches the
  runner before the LLM renders its prompt template.
- LLM emits the narrow `StrategistLLMDecision` (stances + optional
  `thesis_revision`); the `StrategistEnricher` derives `target_weights`,
  `new_positions`, `close_reasons`, `trim_reasons` and writes the full
  `StrategistDecision`.
- Four-verb stance vocabulary: `buy` (entry/add, weight required, `0 < w ≤
  0.05`), `sell` (reduce/close, rationale required), `update` (revise prose
  only, no trade), `no_action` (reviewed-no-change). One stance per watchlist
  ticker per tick — no silence-is-hold.
- `buy` weight is hard-capped at `max_buy_delta_per_trade` (5%); `sell` and
  `update` are unconstrained prose verbs.
- Strategist never reads broker or persistence directly; thesis-book mutation
  is the Executor's job.
- Optional `thesis_revision`: when `None`, the prior `user:thesis` is carried
  forward unchanged.
- **[DISAGREEMENT — see §6.2]** on the `StrategistEnricher`'s mechanism:
  Agent 1 says it is a standalone `BaseAgent` (carve-out from Rule 3's
  callback contract); Agent 3 and the contract reference a legacy
  `_strategist_validation_callback`. Verified: both exist
  (`src/agents/strategist/agent.py:383` for the callback;
  `StrategistEnricher` is a separate `BaseAgent` sub-agent). The relationship
  needs human clarification.

### 2.3 executor — `src/agents/executor/`

**[AGREED] Purpose.** Submit risk-gated orders to the broker, record fills and
slippage, mutate the thesis book by applying strategist stance verbs to the
prior `user:positions` dict, persist the new `user:positions` and
`user:thesis` via the ADK auto-yield mechanism, and write trade-log DB rows on
position close.

**Inputs.** `state["final_orders"]`, `state["portfolio"]`,
`state["strategist_decision"]`, `state["positions"]` (bare-key thesis-book
working copy), `state["tick_id"]`.

**Outputs.** `state["executions"]` (Execution list with status
filled/rejected/partial), `state["last_executed_tick_id"]` (idempotency
handshake), and cross-tick writes to `state["user:positions"]` and
`state["user:thesis"]` via `after_agent_callback`. Trade-log rows persisted on
close.

**Key invariants:**
- Idempotency guard: skips if `state["last_executed_tick_id"] == tick_id`.
- The `after_agent_callback` (`_executor_thesis_writer_callback`) is the
  writer-of-record for `user:positions` and `user:thesis` — writes go through
  ADK's delta-tracking `State` and are auto-yielded as a `state_delta` event
  (contract Rule 1, Spec B clarification).
- Every stance verb dispatches deterministically (`_verb_dispatch`); there is
  no second LLM pass.
- Broker rejections are logged and recorded as `status="rejected"` executions;
  they do not crash the tick.
- Trade-log rows are written on full close only (not on trims).
- The bare-key `state["positions"]` is mutated in-tick as an intra-tick BUY →
  SELL bridge (verified: `src/agents/executor/agent.py:320` —
  `state["positions"] = positions  # Band 4 bare-key BUY→SELL bridge`).
  **[DISAGREEMENT — see §6.3]** on whether this bare-key bridge is
  contractually documented or a legacy compat shim.

### 2.4 risk_gate — `src/agents/risk_gate/`

**[AGREED] Purpose.** Pure-Python deterministic clamp of the strategist's
`target_weights` to hard portfolio constraints, plus synthesis of concrete
broker orders. No LLM calls, no I/O.

**Inputs.** `state["strategist_decision"]`, `state["portfolio"]`.

**Outputs.** `state["final_orders"]` (list of `Order` dicts),
`state["risk_clamps_applied"]` / clamp telemetry, defensive write to
`state["last_risk_gate_decision"]`.

**Key invariants:**
- Clamps applied in order: buy-delta-per-trade (defence-in-depth) →
  concentration cap → cash floor → per-ticker delta → total turnover →
  no-short rule.
- `update` and `no_action` stances are stripped from the weight dict before
  clamping (they carry no weight change).
- Every `sell` (and trim) must carry a rationale (validated upstream by
  contract, re-checked here).
- Clamping is idempotent.
- Orders emitted only for tickers whose weight changes (no churn).
- Every clamp is recorded for audit.

### 2.5 agents-misc — `src/agents/{memory,snapshot,isolated_failure.py,llm_retry.py}` (and empty `attribution/`)

**[AGREED] Components:**
- **`isolated_failure.py` / `IsolatedFailureWrapper`** — wraps per-ticker LLM
  analyst branches; catches and structured-logs exceptions without
  propagating; yields zero events on failure. Joiner synthesises no-data
  verdict for the missing key.
- **`llm_retry.py` / `RetryingAgentWrapper`** — wraps a single `LlmAgent` with
  three-class retry (HTTP 429 / wall-clock timeout / `pydantic.ValidationError`)
  with independent per-class attempt budgets and backoff; buffers events,
  yields only on success. Must wrap bare `LlmAgent` units only — never
  composites (event buffering breaks inter-child state propagation).
- **`memory/writer.py` / `MemoryWriter`** — appends a `BufferEntry`
  (decision_tag, reasoning summary, smart-money flag, repeat-detection,
  optional embedding) to `memory_buffer`; FIFO eviction to `day_digest` at
  ~25 entries (`compress` stub). Cross-tick persistence deferred to Spec C;
  today rebuilds from empty each tick.
- **`snapshot/agent.py` / `Snapshotter`** — final pipeline agent; anchors
  starting capital and SPY price on first tick; records bot total value,
  position count, and SPY price each tick; writes `state["last_snapshot"]`
  handshake key (read by backtest driver as a pipeline-completion assertion).

**Note on `attribution/`** — both Agent 2 and Agent 3 list this as a module;
verified the directory exists but is empty (`ls src/agents/attribution/`
returns nothing). It is a placeholder for future trade-attribution work.

**Key invariants:**
- `IsolatedFailureWrapper` only wraps LLM per-ticker branches; deterministic
  analysts do not need it.
- `RetryingAgentWrapper` policies (429 backoff, timeout, schema cap) are
  loaded from `config/retry_429.json`, `config/analysts.json`,
  `config/strategist.json` at agent construction time.
- Snapshotter passes `as_of` to SPY price-fetch so backtest does not leak
  wall-clock prices into historical snapshots.

### 2.6 contract — `src/contract/` + `src/agents/contract/`

**[AGREED] Purpose.** Define canonical Pydantic schemas for verdicts,
evidence, ticker stances, position theses, orders, executions, the digest
aggregator that collapses four analyst signals into one `TickerEvidence` per
ticker, and the strategist's prompt renderer.

**Key schemas:**
- `AnalystVerdict` — lean (bullish/bearish/neutral), magnitude `[0,1]`,
  confidence `[0,1]`, `key_factors`, optional `report` (LLM-only), `is_no_data`.
  Validator: if `is_no_data=False` then `report` must be populated for LLM
  analysts.
- `AnalystEvidence` — one row per analyst per ticker per tick; verdict +
  deterministic feature vector + metadata.
- `TickerEvidence` — deterministic per-ticker aggregate from all four
  analysts; contains `per_analyst` dict plus an `AggregateVerdict` (consensus
  lean/magnitude/confidence + `disagreement` = variance of signed
  confidences).
- `TickerStance` — four-verb vocabulary with conditional Pydantic validators
  (e.g. `buy` requires weight + rationale; `sell` requires rationale).
  `extra="forbid"` catches drift from deleted fields (`target_price`,
  `stop_price`, `horizon`).
- `PositionThesis` — one row of the thesis book, keyed by ticker under
  `state["user:positions"]`; holds entry rationale, current rationale, and a
  `last_reviewed_reason` audit trail field for exits.
- `StrategistLLMDecision` (narrow LLM emit) and `StrategistDecision` (full
  enricher output).
- `AnalystReport` — `summary` + 2-4 `ReportDriver` rows (name, direction,
  weight, body). `max_length` is intentionally NOT set on free-text fields —
  Vertex's constrained decoder treats `maxLength` as a pad target. The
  "two-tier convention" puts the prompt-facing cap in JSON config; the schema
  has headroom.
- Evidence writer (`BaseAgent`) reads verdicts, runs domain extractors,
  synthesises no-data fills, and writes complete `AnalystEvidence` lists.

**Key invariants:**
- Free-text caps resolved from config (`strategist.json`, `analysts.json`) at
  import time so operators tune via JSON.
- Pydantic validators enforce contract rules at parse time.
- Feature extractors are deterministic, time-aware (accept `as_of` for PIT
  clamping), and state-aware (the technical extractor reads
  `reference_prices` from state).
- `rationale` is the single prose field on `TickerStance` (the older split
  between `rationale`/`reason`/`catalyst` was collapsed — recent commits
  `742f38e`, `ba8555b`).

### 2.7 data — `src/data/`

**[AGREED] Purpose.** Pluggable provider registry and rate-limited async
dispatch to multiple data domains, with live providers for production and
cache-backed providers for backtest replay.

**Inputs.** Ticker, time period/interval, optional historical clock
(`as_of`), optional tick phase.

**Outputs.** Domain models: `PriceHistory` (OHLCV), `CompanyRatios`,
`NewsArticle[]`, `SocialSentiment`, `InsiderTrade[]`, `NotableHolder[]`,
`Filing[]`, `PoliticianTrade[]`.

**Key invariants:**
- Providers registered via `@register(domain, name, upstream,
  rate_per_minute, burst)`; active providers wired from `config/data.json` at
  runtime.
- Agents never import providers directly — they call domain wrappers
  (`get_price_history(ticker, as_of=...)`).
- Rate limiting is token-bucket per provider; a coroutine waits for a token
  (backpressure, not error). Slowest source (Quiver, 30/min) dictates the
  decision interval floor.
- `as_of` is mandatory for backtest calls (PIT correctness); optional/wall-
  clock in live.
- Cache providers read from golden SQLite under
  `backtests/<window>/store.sqlite`; tests build temporary caches in
  `tmp_path`.
- A mismatch between `config/data.json` and a provider's `@register`
  decorator raises `RuntimeError` at import time.
- `politician_trades` is registered but disabled in the fetcher (no free
  historical source); analyst degrades gracefully.
- **[DISAGREEMENT — see §6.4]** on the canonical domain count: Agent 1 says 5,
  Agent 3 says 8. Both are correct at different layers (5 analyst domains;
  ~8 underlying provider domains because `smart_money` fans out into
  `insider_trades`, `notable_holders`, `filings`, `politician_trades`).

### 2.8 backtest — `src/backtest/`

**[AGREED] Purpose.** Historical-window replay harness. Iterates a scheduled
tick sequence against a golden cache, runs the live pipeline once per tick
with a `FakeBroker`, and collects artefacts (traces, decision logs,
snapshots, equity curve, metrics report).

**Components.**
- `driver.py` — per-tick loop: injects `as_of`, `temp:_trace`,
  `temp:_decision_logger`; runs pipeline via ADK Runner; collects results.
- `runner.py` — end-to-end window orchestrator: builds schedule, wires cache
  + fake broker, instantiates driver, runs all ticks, generates final
  report.
- `schedule.py` — NYSE tick sequence (open/close phases), honours holidays.
- `windows.py` — loads window config from `config/backtest_windows.json`.
- `reporting.py` — equity curve, `metrics.md` (Sharpe / max drawdown /
  Calmar vs SPY baseline), forward-return backfill.
- `decision_logger.py` — per-trade JSON snapshot writer.
- `cache/` — SQLite golden-cache schema + store façade.
- `providers/` — cache-backed providers conforming to data dispatch.

**Key invariants:**
- Cache reads are deterministic; no API calls during backtest.
- Per-window cache (e.g. `backtests/baseline-2025-09/store.sqlite`) is the
  golden reference.
- Pipeline topology is identical to live; only the broker, providers, and
  session service differ.
- Each tick uses a fresh `InMemorySessionService` in backtest (live uses
  `DatabaseSessionService`).
- Watchlist pre-flight: tickers with no OHLCV in the window are dropped
  before tick iteration.
- Reference symbols (SPY + 11 SPDR sector ETFs) fetched once per tick and
  stored in `state["reference_prices"]`.
- `last_snapshot` is asserted at the end of every tick to confirm pipeline
  completion (verified per `driver.py:393-401` per Agent 3).
- Driver catches known ADK 1.32 Runner cleanup exceptions
  (`AttributeError`, `BaseExceptionGroup`) after pipeline finishes.

### 2.9 orchestrator + lifecycle — `src/orchestrator/`, `src/lifecycle/`

**[AGREED] Purpose.**
- **orchestrator/** wires the live pipeline (`AnalystPool → Strategist →
  RiskGate → Executor → MemoryWriter → Snapshotter` as a `SequentialAgent`),
  constructs the per-tick state dict at Phase 2, and provides persistence /
  session-management glue.
- **lifecycle/** runs pre-flight environment checks, broker anchor snapshot,
  scheduler integration, and a hard-reset path.

**Components.** `pipeline.py` (agent factories), `state.py` (shared schemas
and risk constants), `persistence.py` (SQLAlchemy ORM for `trade_log`,
`buffer_entries`, `ticker_stances`, `portfolio_snapshots`), `tick.py` (live
tick entrypoint), `initialise.py` (preflight + anchor), `hard_reset.py`,
`scheduler.py`.

**Key invariants:**
- Phase 2 populates all §A row fields from their Source of Truth; cross-tick
  fields hydrated from persistence (never seeded empty).
- Phase 4 persists every cross-tick `state_delta` event.
- Both lifecycles see identical state-dict shape at identical phases.
- Pipeline never reads broker, persistence, or providers directly during a
  tick (Rule 7).
- Observability handles (`TraceWriter`, `DecisionLogger`) injected as
  `temp:_trace` / `temp:_decision_logger` by direct `adk_session.state`
  mutation *after* `create_session(...)` returns (because
  `extract_state_delta` strips `temp:` keys passed to `create_session`).

### 2.10 broker — `src/broker/`

**[AGREED] Purpose.** Unified `Broker` protocol satisfied by both
`Trading212Broker` (live/paper) and `FakeBroker` (backtest), plus a
`Portfolio` model snapshotting holdings + cash.

**Interface.** `submit_market(ticker, action, quantity) → Fill`,
`get_portfolio() → Portfolio`, `position_size(ticker) → float`.

**Key invariants:**
- Broker is source of truth for portfolio; `state["portfolio"]` is a working
  copy refreshed at Phase 2.
- Executor does not call the broker mid-tick except at execute time.
- `FakeBroker` simulates instant fills at reference prices with optional
  slippage; can be injected with a `_prices` dict for tests.
- Brokers respect the same interface so executor and risk gate are
  implementation-agnostic.

### 2.11 ops — `src/{observability,baselines,deploy,config}/`

**[AGREED] Purpose.**
- **observability/** — `TraceWriter` (JSON boundary snapshots),
  `DecisionLogger` (per-trade snapshots), OTEL scaffolding (no-op
  pre-deployment), drain utilities.
- **baselines/** — SPY buy-and-hold metrics for backtest comparison.
- **config/** — Centralised JSON config loaders (`data.json`,
  `strategist.json`, `analysts.json`, `risk_gate.json`,
  `backtest_settings.json`, `watchlist.json`, `retry_429.json`). Single
  source of truth for all tuning knobs.
- **deploy/** — Empty (deployment scaffolding deferred).

**Key invariants:**
- Observability writes are additive and never mutate contract-bearing state
  (Rule 8).
- Trace and decision logs go to `runs/<run-id>/...` in backtest; to GCS in
  live (carve-out D1).
- Config loaders use `@lru_cache(maxsize=1)`; tests override via
  `load_*_config(path=...)` hooks.
- Config edits are not hot-reloadable; process restart required.
- All timestamps are UTC by convention.

---

## 3. Cross-cutting concepts (CRITICAL — the dedupe pass relies on this)

### 3.1 Canonical glossary

| Term | Canonical meaning |
|---|---|
| **rationale** | Single prose justification field on `TickerStance` and `PositionThesis`. Required on `buy`, `sell`, `update`; optional on `no_action`. Earlier split (`rationale` / `reason` / `catalyst`) was collapsed; the field is now unified (commits `742f38e`, `ba8555b`). |
| **stance** | Per-ticker strategist decision: a `TickerStance` with `intent` (buy/sell/update/no_action), `ticker`, optional `weight`, optional `rationale`. One per watchlist ticker per tick. |
| **thesis** | Prose narrative at two levels: standing market thesis (`state["user:thesis"]`, cross-tick, scalar prose) and per-position thesis (rows of `state["user:positions"]`, cross-tick dict keyed by ticker). |
| **PositionThesis / thesis book** | The dict at `state["user:positions"]` — one `PositionThesis` row per held or watched ticker, recording entry rationale, current rationale, weight, opened_at / opened_price (or `None` if watched-not-owned), and `last_reviewed_reason` audit trail. |
| **verdict** | Per-ticker per-analyst directional call: lean (bullish/bearish/neutral) + magnitude + confidence + `key_factors` + optional `report` + `is_no_data` flag. Schema is `AnalystVerdict`. |
| **evidence** | Per-ticker per-analyst per-tick record wrapping a verdict with feature-vector + metadata. Schema is `AnalystEvidence`. Written to `{domain}_evidence` keys; consumed by the digest aggregator for the strategist. |
| **TickerEvidence** | Deterministic per-ticker aggregate of all four analysts: `per_analyst` dict + `AggregateVerdict` (cross-analyst consensus) + analyst weights. Built by the digest aggregator. |
| **AggregateVerdict** | Weighted cross-analyst consensus: lean, magnitude, confidence, `disagreement` (variance of signed confidences), summary. |
| **StrategistDecision** | Full strategist output: stances list + derived `target_weights` + `decision_tag` + reasoning + optional `thesis_revision` + confidence + `sell_reasons` / `update_reasons` / `new_positions` / `close_reasons` / `trim_reasons` (derived by enricher). |
| **StrategistLLMDecision** | Narrow shape emitted by the LLM (stances + optional `thesis_revision`); enricher inflates to `StrategistDecision`. |
| **target_weights** | Dict mapping every watchlist ticker → `[0, 1]` portfolio fraction (0 = no position). Sum < 1.0 (cash reserve). Derived by enricher from stances. |
| **order** | Risk-gate output: ticker, action (BUY/SELL), quantity, est_price. One per ticker whose weight changes. |
| **execution / Execution** | Broker result: status (filled/rejected/partial), `actual_price`, `actual_quantity`, `slippage_bps`, `broker_order_id`, optional `error`. |
| **tick** | One pipeline invocation. Atomic unit of work. Tick-scoped state rebuilt fresh; cross-tick state hydrated from persistence at Phase 2. |
| **tick_id** | Deterministic per-tick identifier (backtest: `<window>-<date>-<phase>`; live: typically a UUID or wall-clock-derived id). |
| **tick_phase** | Literal string: live sets `"live"`; backtest sets the schedule's `"open"` or `"close"`. Decorative for pipeline; used by observability. |
| **Phase 1 / 2 / 3 / 4** | Lifecycle stages — Phase 1 run-start (once per process), Phase 2 tick-start (hydrate), Phase 3 pipeline execution, Phase 4 tick-end (persist + flush). Both lifecycles follow identically per `contract-invariants.md §B`. |
| **state / session.state** | The ADK session dict — all agent-readable/writable working data for one tick. Mutated via `state_delta` events. |
| **state_delta** | `EventActions(state_delta={...})` payload — the ADK persistence channel. Required for all durable writes (Rule 1). |
| **temp:** | ADK prefix for invocation-scoped state keys (not persisted). Examples: `temp:held_positions_view`, `temp:ticker_evidence`, `temp:{domain}_data`, `temp:_trace`, `temp:_decision_logger`, per-ticker working keys like `temp:news_verdict_<ticker>`. |
| **user:** | ADK prefix for user-scoped cross-tick keys. Today: `user:positions`, `user:thesis`. Persisted via ADK `DatabaseSessionService` `user_state` table. Per Rule 7 Spec B clarification, `user:`-prefixed keys ARE the persistence layer the pipeline interacts with. |
| **as_of** | Historical clock timestamp for the tick. Mandatory PIT boundary for backtest provider calls; wall-clock in live. |
| **no_data / is_no_data** | Flag on `AnalystVerdict` indicating the analyst had no signal (provider returned empty, branch failed). Aggregate verdict excludes no-data rows from confidence/disagreement calculations. |
| **feature / feature_vector** | Deterministic numerical features extracted from raw analyst data (e.g. RSI, P/E, sentiment score). Stored on `AnalystEvidence.features`. |
| **AnalystReport / ReportDriver** | LLM-only output. `Report` = `summary` + 2-4 `ReportDriver` rows (name, direction, weight, body). Deterministic analysts leave it `None`. |
| **decision_tag** | snake_case label on `StrategistDecision` (e.g. `"earnings_surprise_pivot"`). Used for audit and memory dedup. |
| **clamp / ClampRecord** | Risk-gate weight reduction recorded as `ClampRecord(rule_name, ticker, before, after)`. One weight may be clamped by multiple rules in sequence. Clamping is idempotent. |
| **idempotency guard** | Executor check: skip re-execution if `state["last_executed_tick_id"] == tick_id`. |
| **handshake key** | In-tick boundary marker: `last_executed_tick_id` (Executor wrote it), `last_snapshot` (Snapshotter wrote it). Read by the backtest driver for completion assertion. Tick-scoped. |
| **window** | A backtest configuration (named date range, e.g. `baseline-2025-09`) from `config/backtest_windows.json`. |
| **joiner** | `BaseAgent` (e.g. `NewsJoinerAgent`, `FundamentalJoinerAgent`) consolidating per-ticker working keys into the canonical `{domain}_verdicts` / `{domain}_evidence` keys after a per-ticker `ParallelAgent` fan-out. |
| **IsolatedFailureWrapper** | Custom wrapper for per-ticker LLM branches; catches exceptions, logs structurally, yields zero events on failure. |
| **RetryingAgentWrapper** | Custom wrapper for single `LlmAgent` units; retries on 429 / timeout / `ValidationError` with independent budgets and backoff. Must NOT wrap composite agents. |
| **ContextShim / StrategistContextShim** | `BaseAgent` that pre-populates `temp:held_positions_view`, `temp:ticker_evidence`, `temp:ticker_evidence_objects` so the strategist's LLM instruction template resolves them. |
| **digest aggregator** | Deterministic function that collapses four analyst evidence lists into one `TickerEvidence` per ticker via signed-confidence-weighted consensus. |
| **bare-key "positions" bridge** | `state["positions"]` (no prefix) — a working copy of `user:positions` populated at Phase 2 and mutated in-tick by the Executor so BUY-stance thesis rows are visible to SELL handling later in the same tick. Verified at `src/agents/executor/agent.py:99,320`; `src/agents/strategist/context_shim.py:153,229`. **See §6.3.** |

### 3.2 Synonym candidates (priority inputs for the dedupe pass)

The following clusters may indicate dedupe opportunities — same concept at
different layers, or accumulated parallel naming:

1. **`rationale` / `last_reviewed_reason` / `sell_reasons` / `update_reasons`
   / `close_reasons` / `trim_reasons` / `report.summary` / `reasoning`** —
   all prose justifications, but at different scopes:
   - `rationale` — per-stance and per-position-thesis-row prose (mutable on
     buy/update).
   - `last_reviewed_reason` — `PositionThesis` audit field, captures the
     final review prose on exit.
   - `sell_reasons` / `update_reasons` / `close_reasons` / `trim_reasons` —
     derived enricher dicts on `StrategistDecision`, aggregating per-ticker
     prose by category.
   - `report.summary` — analyst-level prose (LLM analysts only).
   - `reasoning` — tick-level strategist narrative on `StrategistDecision`.

   These are likely all distinct in intent but the proliferation is a
   strong dedupe candidate.

2. **`verdicts` vs `evidence` (`{domain}_verdicts` vs `{domain}_evidence`)**
   — both written per analyst per tick. Verdicts are the lean+confidence
   call; evidence wraps verdict + feature vector + metadata. Agent 3 flagged
   this as a possible redundancy (open question §4.7); verified both are
   written by every analyst (e.g. `social/agent.py:13,17`).

3. **`state["positions"]` (bare key) vs `state["user:positions"]`** — bare
   key is the in-tick working copy; `user:`-prefixed is the persistence-
   bearing canonical. See §6.3.

4. **`state["portfolio"]` vs `state["user:positions"]`** — `portfolio` is
   broker truth (current holdings); `user:positions` is strategist intent
   (the thesis book). Distinct, but easy to conflate.

5. **`StrategistLLMDecision` vs `StrategistDecision`** — narrow LLM emit
   vs full enricher output. Two-shape pattern is intentional; flag if
   downstream consumers ever consume the narrow shape directly.

6. **`memory_buffer` vs `day_digest`** — both cross-tick experiential
   memory; relationship deferred to Spec C. Open question whether these are
   two stores or one store with two views (`contract-invariants.md §E`).

7. **`MemoryWriter` decision-tag / `decision_tag` / `is_repeat` / dedupe**
   — multiple dedup-style fields in the memory layer; check whether they
   overlap.

8. **`AnalystReport` / `ReportDriver` `body` vs `AnalystVerdict.key_factors`
   vs `AnalystEvidence.rationale`** — multiple short-prose fields on
   adjacent records.

9. **`tick_id` vs `tick_phase` vs `as_of`** — three time-keyed handles on
   the same tick. Distinct (id, phase string, clock), but related.

10. **`_strategist_validation_callback` (legacy) vs `StrategistEnricher`
    (BaseAgent)** — possibly redundant enrichment paths. See §6.2.

11. **`last_executed_tick_id` direct-write vs `state_delta` write** —
    contract notes the paired direct write is "defensive belt-and-braces
    (out of A1 scope)" — flag for cleanup.

---

## 4. Open questions (UNCERTAIN)

Merged and de-duplicated from all three drafts; grouped thematically.

### Memory / persistence
1. **`memory_buffer` and `day_digest` schema and persistence (Spec C
   deferred).** All three drafts flag this. Today rebuilds empty each tick.
   Open: storage format, bounded-retention policy, whether they are two
   stores or one with two views, migration/rebuild story.
2. **Thesis book initialisation on first tick.** Empty seed vs baseline
   stance (all-cash, benchmark-weighted)? (Agent 2.)
3. **Strategist first-tick behaviour.** Full baseline stance per watchlist,
   or incremental build via `update` / `no_action`? (Agent 2.)

### Smart money
4. **Smart-money analyst status.** Shelved (2026-05-19) pending PIT-correct
   `notable_holders` / `politician_trades`. Blocking dependency or nice-to-
   have? ETA? Graceful degradation strategy? (All three drafts.)

### Observability and ADK quirks
5. **Observability handle injection (`temp:_trace`, `temp:_decision_logger`)
   under `DatabaseSessionService`.** Direct mutation of `adk_session.state`
   after `create_session()` returns works with `InMemorySessionService` but
   is unverified with `DatabaseSessionService` (Spec C / Spec B follow-on).
   (Agents 1, 3.)
6. **ADK 1.32 Runner cleanup exceptions** caught by the driver
   (`AttributeError`, `BaseExceptionGroup`). Does the bug persist in ADK
   1.34+? Workaround beyond logging? (Agent 1.)
7. **`temp:` prefix survival across deep-copy** — `TraceWriter` /
   `DecisionLogger` use `__deepcopy__` passthrough. Do bare dicts survive
   the same path? (Agent 3.)

### Analyst behaviour
8. **Signal-validation guarantee on per-ticker fan-out** — is the joiner
   synthesis of no-data verdicts guaranteed for every ticker, or can a
   ticker be silently omitted if its branch dies before the joiner runs?
   (Agent 1.)
9. **Deterministic analyst feature-extractor stability** under provider
   outage — graceful degradation vs `is_no_data=True`? (Agent 2.)
10. **Analyst weight configuration basis** — where do the per-analyst
    weights in `config/analysts.json` come from? Fixed heuristics, tuned
    per-window, learned from performance? (Agent 2.)

### Operational
11. **Broker cash-mismatch detection in live.** Anchor checked at
    deployment; no ongoing mid-deployment detection for manual
    deposits/withdrawals. (Agent 1.)
12. **`trade_log` vs `memory_buffer` interaction.** `trade_log` =
    closed-position PnL; `memory_buffer` = decision reasoning. How are
    they used together (post-trade analysis, KB learning loop)? (Agent 2.)
13. **`as_of` timezone handling.** Backtest uses NYSE time, live uses
    UTC. Are all timestamp fields normalised to UTC and localised at
    observability/reporting? (Agent 2.)
14. **LLM prompt stability across Gemini model upgrades.** Validation
    strategy when underlying model changes? (Agent 2.)

### Contract / schema
15. **Evidence vs verdict duplication** — both written per analyst per
    tick. Audit projection or redundant? (Agent 3, partly answered: they
    are distinct shapes, but the proliferation deserves review.)
16. **`AnalystReport` `max_length` deliberately unset** — Vertex pad-target
    rationale. Working as intended or risk? (Agent 3.)
17. **`MemoryWriter.compress` stub status** — placeholder or real logic?
    (Agent 3.)

---

## 5. Apparent policy contradictions

### 5.1 Bare-key `state["positions"]` and §A documentation
- **Source:** `src/agents/executor/agent.py:99,320`;
  `src/agents/strategist/context_shim.py:153,229`. The bare key is read and
  written by Executor and read by ContextShim as a working copy of
  `user:positions`.
- **Policy:** `contract-invariants.md §A` lists `state["user:positions"]`
  but not the bare-key `state["positions"]`. §A scope statement says
  "pipeline-internal working state is allowed to exist; it is
  implementation, not contract."
- **Synthesiser verdict:** Neither code nor policy is wrong, but the bare
  key is doing more than implementation — it is the BUY → SELL intra-tick
  channel and is explicitly labelled `# Band 4 bare-key BUY→SELL bridge`.
  The contract should document the bare key as a tick-scoped working copy
  seeded from `user:positions` at Phase 2 to remove the ambiguity. **See
  §6.3.**

### 5.2 `StrategistEnricher` vs `_strategist_validation_callback`
- **Source:** `src/agents/strategist/agent.py:383` (the callback);
  `StrategistEnricher` is a separate sub-agent in the strategist's
  `SequentialAgent`.
- **Policy:** `contract-invariants.md` Rule 1 in-tick callback carve-out
  (added 2026-05-20) specifically names
  `_strategist_validation_callback` as the canonical instance of direct
  state mutation conformant for in-tick consumers.
- **Synthesiser verdict:** Two enrichment paths exist. Agent 1 says the
  enricher replaced the callback because the callback "misfires under
  schema-retry wrapping"; Agent 3 documents both as concurrent. The code
  has both; one is likely vestigial. Both paths cannot be authoritative
  — human adjudication required. **See §6.2.**

### 5.3 `last_executed_tick_id` paired direct write
- **Source:** `contract-invariants.md §A` notes "a paired direct write is
  currently retained as defensive belt-and-braces (out of A1 scope — see
  todo-fixes 2.5.x)."
- **Synthesiser verdict:** Self-documented technical debt. Acknowledge,
  schedule cleanup; not a contradiction so much as a known
  belt-and-braces.

### 5.4 Snapshotter `last_snapshot` paired direct write
- **Source:** `contract-invariants.md §A` notes the same defensive direct
  write for `last_snapshot`.
- **Synthesiser verdict:** Same as 5.3 — known and self-documented.

### 5.5 `attribution/` module referenced but empty
- **Source:** `ls src/agents/attribution/` returns nothing; Agents 2 and 3
  list it as a module path.
- **Synthesiser verdict:** Documentation/intent drift, not policy
  contradiction. The placeholder should either be deleted or have an
  intent doc stating it is reserved for future work.

### 5.6 No systemic contradictions
Agent 2 explicitly stated "no contradictions found" and Agent 3 concluded
"no systemic policy drift; violations are localised". The synthesiser
concurs: items 5.1-5.5 are localised documentation gaps and self-noted
debt, not deep contradictions between code and contract.

---

## 6. Disagreements requiring human adjudication

### 6.1 Smart-money analyst — "wired but empty" vs "unwired"
- **Drafts:** Agent 1 says smart_money is "shelved pending PIT-correct
  provider implementations" — evidence key remains but always empty.
  Agents 2 and 3 say it is "shelved (2026-05-19)" / "not wired" — module
  exists but not in the pipeline.
- **Why it matters:** Determines whether downstream consumers can assume
  a `smart_money_evidence` key always exists with a no-data shape, or
  whether they must defensively handle its absence.
- **Verification:** `src/agents/analysts/smart_money/agent.py` exists and
  references the same pattern as the other analysts; need to confirm
  whether it is composed into the `AnalystPool` in
  `src/orchestrator/pipeline.py`.
- **Recommendation:** Human should confirm one of:
  (a) module exists, registered in the pool, emits canonical no-data
  verdicts each tick;
  (b) module exists but is NOT registered — pool composition skips it,
  and downstream consumers must defend against absence.

### 6.2 Strategist enrichment mechanism — `StrategistEnricher` BaseAgent vs `_strategist_validation_callback`
- **Drafts:** Agent 1 says the enricher is a standalone `BaseAgent`
  inside the strategist's `SequentialAgent` *because* the callback
  pattern misfires under retry wrapping. Agent 3 documents both an
  `_strategist_validation_callback` (in-tick carve-out per Rule 1) and a
  `StrategistEnricher` (yields `state_delta`); flags as "redundant or
  separation-of-concerns?".
- **Verified:** Both exist. `agent.py:383` is the callback;
  `StrategistEnricher` is a separate sub-agent.
- **Why it matters:** Two enrichment paths is exactly the kind of legacy
  shim accumulation the audit is targeting. If the callback is dead code
  superseded by the enricher, it should be deleted (and the Rule 1
  carve-out documentation revised). If both are live and doing different
  work, that needs naming and documenting.
- **Recommendation:** Human should map exactly which fields each path
  derives, and whether the callback is still invoked end-to-end. If the
  enricher fully supersedes the callback, retire the callback.

### 6.3 Bare-key `state["positions"]` — contractual or compat shim?
- **Drafts:** Agent 1 calls it "the bare-key bridge" — a tick-scoped
  working copy of `user:positions`, persisted via the Executor's
  after-callback. Agent 2 describes it as "the bare-key bridge for
  in-tick reads (legacy Band 4 pattern)". Agent 3 lists it as a §A row,
  comments "executor comments say it's cross-tick and carries BUY → SELL
  in-tick channel. Is this legitimate per §A or temporary compat shim?".
- **Verified:** Code labels itself `# Band 4 bare-key BUY→SELL bridge`
  (`executor/agent.py:320`); ContextShim also reads it with fallback
  (`context_shim.py:153,229`).
- **Why it matters:** Whether the bare key is contractual or transitional
  determines whether the audit should propose removing it (once
  `user:positions` reads are sufficient) or formalising it (adding a §A
  row).
- **Recommendation:** Treat as transitional unless the human declares
  otherwise — the "Band 4" label and ContextShim's fallback (`...or {}`)
  both suggest a compat shim mid-migration. Document the intended end
  state.

### 6.4 Data-domain count — 5 vs 8
- **Drafts:** Agent 1 names 5 domains (price_history, company_ratios,
  news, insider_trades, social_sentiment). Agent 3 names 8 (price_history,
  company_ratios, news, social_sentiment, insider_trades, notable_holders,
  filings, politician_trades).
- **Why it matters:** Coverage map for the data layer; if the audit
  enumerates providers, the count must be right.
- **Recommendation:** Both are correct at different layers — there are
  ~5 analyst-domain-facing data wrappers but ~8 underlying provider
  registrations (smart_money fans out into 4 sources). The intent
  document should distinguish "analyst domains" from "provider domains"
  to avoid future confusion.

### 6.5 Whether the four cross-tick rows are all "persisted today"
- **Drafts:** Agent 1 treats `user:positions` / `user:thesis` as
  persisted (Spec B) and `memory_buffer` / `day_digest` as deferred
  (Spec C). Agent 2 says the same. Agent 3 says "currently rebuild from
  empty each tick" for memory_buffer/day_digest.
- **Why it matters:** Whether downstream agents reading these fields can
  assume historical content is present, or only current-tick content.
- **Recommendation:** All three are consistent — `user:positions` /
  `user:thesis` are persisted; `memory_buffer` / `day_digest` are NOT
  persisted today and rebuild empty. Treat as agreed. No human action
  needed beyond noting the Spec C dependency.

### 6.6 Whether the Executor writes `user:thesis` itself
- **Drafts:** Agent 1 says Executor's `after_agent_callback` writes BOTH
  `user:positions` AND `user:thesis`. Agent 3 agrees. Agent 2 says the
  executor writes both. `contract-invariants.md §A` confirms: Executor's
  `after_agent_callback` is the writer-of-record for both, with
  `user:thesis` being a "passthrough of Strategist's optional
  `thesis_revision`, else carry-forward of the prior value".
- **Synthesiser verdict:** Not really a disagreement — all three drafts
  align with the contract. Flagged here only because the mechanism
  (Executor as passthrough writer of a Strategist field) is non-obvious
  and warrants a confirmation glance.

### 6.7 Whether `RetryingAgentWrapper` wraps the strategist's whole sequential or just the LlmAgent
- **Drafts:** All three say the wrapper is *inside* the Sequential,
  wrapping only the `LlmAgent` (so ContextShim and Enricher run
  unwrapped). Agent 1 and Agent 2 explicitly note the reason
  (event-buffering breaks inter-child state propagation).
- **Synthesiser verdict:** Agreed across all drafts; not a disagreement.
  Listed here to confirm the audit can treat this as ground truth.

---

## 7. Human resolutions (2026-05-26)

The §6 disagreements have been adjudicated by the human. The following is
authoritative — downstream module agents must treat these as ground truth
in preference to anything in §1–§6 that contradicts them.

### 7.1 Smart-money analyst (resolves §6.1)
**Status:** Registered in `AnalystPool` and runs every tick. Emits a
canonical no-data shape (the underlying providers are shelved, so the
verdict carries `is_no_data=True`, but the `smart_money_evidence` key is
always present).
**Audit implication:** Downstream consumers may assume the key exists.
Any defensive code that handles `smart_money_evidence` absence is dead.

### 7.2 Strategist enrichment paths (resolves §6.2)
**Status:** `_strategist_validation_callback` is dead in production
(verified — not wired into the live `SequentialAgent`). It survives only
as a delegate for legacy integration tests that manually attach it to an
`LlmAgent`. `StrategistEnricher` (BaseAgent) is the sole live path; both
share `validate_and_enrich`, so the callback adds no unique logic.
**Audit implications (flagged for later report):**
- P1 dead-code: `_strategist_validation_callback` and its delegate path.
- P1 dead-test: the legacy integration tests that exercise the callback.
- P2 doc-fix: `docs/contract-invariants.md` Rule 1 callback carve-out
  documentation should be revised once the callback is removed.

### 7.3 Bare-key `state["positions"]` bridge (resolves §6.3)
**Status:** Load-bearing inside `executor._run_async_impl` for the
in-tick BUY → SELL ordering — `user:positions` is not written until the
after-callback fires, which is after the loop completes. The bare key is
the only channel through which an in-tick BUY's thesis reaches the SELL
handler in the same tick. Verified against the executor source and the
band4/band5/band6 commit history.

External readers, however, do NOT need the bare key:
- `src/agents/strategist/context_shim.py:153,229` — fallback chain
  `state.get("user:positions") or state.get("positions")` is unnecessary;
  by the time ContextShim runs, the callback has fired.
- `src/backtest/decision_logger.py:339` — same; runs after executor
  completes.

**Audit implications:**
- Keep the bare key inside the executor (contractual, executor-internal).
- P2 consolidation: ContextShim and decision_logger should read
  `user:positions` directly; remove the bare-key fallback.
- P2 doc-fix: `docs/contract-invariants.md §A` should add a row
  documenting `state["positions"]` as an executor-internal tick-scoped
  working copy of `user:positions`.

### 7.4 Data-domain count layering (resolves §6.4)
**Status:** Both numbers are correct at different layers.
- **Analyst-facing data domains (5):** `price_history`, `company_ratios`,
  `news`, `social_sentiment`, `insider_trades` (the wrappers analysts
  call via `data.dispatch`).
- **Underlying provider registrations (8):** the five above plus
  `notable_holders`, `filings`, `politician_trades` — the extra three
  are sub-providers that the smart_money analyst fans out into.

Module agents auditing the data layer should distinguish "analyst-domain
wrappers" from "provider registrations". This is a doc-clarity item, not
a code finding.

---

## 8. Post-audit gate resolutions (2026-05-26)

These resolve gates surfaced by the Phase-2/3 audit (see `FINDINGS.md`).
Authoritative — override anything earlier in this document that conflicts.

### 8.1 smart_money is shelved (revises §7.1, resolves A-021)
**Status:** Smart_money is **shelved**, not running. `pipeline.py:88` has
`_build_smart_money_analyst(...)` commented out, deliberately, pending
PIT-correct providers for `notable_holders` and `politician_trades`.

§7.1's claim that smart_money "runs every tick" was wrong. The source is
correct.

**Implications:**
- Keep `src/agents/analysts/smart_money/`, `fetch.py`, and the smart_money
  test files as dormant scaffolding for reactivation.
- Defensive consumer code that checks for smart_money presence is **not
  dead** — it is correct for the shelved state.
- A-022 (smart_money state-write style) is moot until reactivation.
- A-033 (smart_money fan-out test cluster) — keep; they pin the
  reactivation contract.

### 8.2 Deterministic analysts leave `report=None` (resolves A-016)
**Status:** The validator `_report_required_when_data_present` in
`src/contract/evidence.py:137` is **wrong** — it contradicts the field
comment ("LLM analysts populate this; deterministic analysts leave it
None") and intent §2.1/§2.6.

**Fix shape:**
- Relax the validator so deterministic verdicts may have `report=None`
  alongside `is_no_data=False`.
- Delete the synthetic-prose paths in
  `src/contract/extractors/{technical,social,smart_money}.py` (the
  `report = AnalystReport(summary=summary, drivers=drivers[:4])` lines
  at technical:695, social:319, smart_money:503).
- Pairs with A-013 (rationale dedupe) and A-049 — deterministic verdicts
  expose features structurally; the one-line `rationale` field is enough.

### 8.3 `BufferEntryRow` shell deleted (resolves A-030)
**Status:** `BufferEntryRow` + `save_buffer_entry` + `load_recent_buffer`
+ `tests/unit/test_buffer_persistence.py` are unused Spec C scaffolding.
Delete now; re-add when Spec C actually wires buffer persistence.

**Fix shape:**
- Delete `src/orchestrator/persistence.py:27-79` (BufferEntryRow + two
  CRUD helpers).
- Delete `tests/unit/test_buffer_persistence.py`.
- Remove `buffer_entries` from `_STOCKBOT_TABLES` in
  `src/lifecycle/initialise.py:21` and `src/lifecycle/hard_reset.py:17`.
- A-042 (`MemoryProjection`) — same fate; delete in the same pass.

### 8.4 `scripts/trace_tick.py` deleted (resolves A-012)
**Status:** Not used. The graphify-out artefacts, decision-logger and
TraceWriter cover its surface-trace role. Delete outright; do not migrate
to `HandleInjectorPlugin`.

### 8.5 Cloud Scheduler is the deployment plan (resolves A-090)
**Status:** Cloud Scheduler remains the planned scheduler. Keep
`src/lifecycle/scheduler.py` and the conditional pause/resume call sites
in `lifecycle/initialise.py:159` and `lifecycle/hard_reset.py:97` as-is.
Pre-deployment scaffolding; no-op under tests.

### 8.6 Risk-gate clamp order (revises §2.4, resolves A-056)
**Status:** Source wins. Actual order applied each tick:

1. `apply_buy_delta_clamp` (in `agent.py`, called before
   `apply_constraints`)
2. `_clamp_negatives` (no-short)
3. `_clamp_max_position` (concentration cap)
4. `_clamp_cash_floor`
5. `_clamp_max_delta` (per-ticker delta)
6. `_clamp_max_turnover` (total turnover)

§2.4's "no-short LAST" wording was wrong. No-short running first is the
sound choice — later clamps then operate on non-negative weights, and
none of them can introduce negatives. Update §2.4 to match source. Zero
code change.

---

*End of synthesised intent. §7 and §8 resolutions are authoritative.*
