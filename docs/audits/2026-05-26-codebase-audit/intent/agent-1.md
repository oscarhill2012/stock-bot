# Intent Draft — Agent 1

## 1. System purpose

StockBot is an AI-driven stock trading bot that continuously monitors a watchlist of equities, analyses multi-dimensional signals (technical, fundamental, news, social sentiment, smart-money flow), synthesises those signals into a market thesis via an LLM-powered strategist agent, and executes buy/sell orders through a brokerage API whilst respecting hard risk constraints. The bot operates in two distinct lifecycles: live (single ticker-per-Cloud-Run-Job invocation via ADK's EventActions persistence) and backtest (multi-tick iteration from a golden SQLite cache), both served by the same pipeline topology and state contract. The core output is a stream of trade executions and a running portfolio of position theses grounded in explicit market reasoning.

## 2. Module intents

### 1. analysts — `src/agents/analysts/`

**Purpose:** Emit per-ticker directional signals (bullish/bearish/neutral + magnitude + confidence) for five independent data domains. Each analyst is deterministic or LLM-assisted, reads domain-specific provider outputs, and produces a canonic `AnalystEvidence` record that the strategist aggregates.

**Inputs / Outputs:**
- **In:** State keys like `temp:technical_data`, `temp:fundamental_data`, `temp:news_data`, `temp:social_data` (populated by `before_agent_callback` fetches); watchlist (`tickers`); reference prices for relative-strength feature extraction.
- **Out:** Per-domain evidence keys (`technical_evidence`, `fundamental_evidence`, `news_evidence`, `social_evidence`, `smart_money_evidence`), each a JSON list of `AnalystEvidence` dicts.

**Key invariants:**
- One `AnalystEvidence` record per ticker per analyst per tick, even if the upstream LLM or provider fails (no-data verdicts synthesised for absent outputs).
- `technical` and `social` analysts are pure BaseAgent subclasses (no LLM) that derive verdicts deterministically from heuristics; `fundamental` and `news` are LLM-powered fan-out branches (Phase 9 per-ticker isolation); `smart_money` is shelved pending PIT-correct provider implementations.
- Evidence is written via `EventActions(state_delta=…)` (contract Rule 1) only in the `after_agent_callback` path (`make_evidence_callback`).
- Feature extractors receive the raw per-ticker data dict, the ticker symbol, and the full state dict (so technical features can compute relative-strength vs SPY from `reference_prices`).

### 2. strategist — `src/agents/strategist/`

**Purpose:** Consume the four analyst evidence lists plus the running position thesis book, apply portfolio-composition rules, and emit one `StrategistDecision` per tick containing per-ticker stance verbs (buy/sell/update/no_action) with weight targets and prose rationales.

**Inputs / Outputs:**
- **In:** `technical_evidence`, `fundamental_evidence`, `news_evidence`, `social_evidence`, `reference_prices`, `user:positions` (cross-tick thesis book), `user:thesis` (standing market view), `memory_buffer` (deferred to Spec C), `day_digest` (deferred to Spec C).
- **Out:** `strategist_decision` (via `output_key`), a JSON dict containing narrowly-typed `StrategistLLMDecision` (stances + optional thesis_revision) that flows through `StrategistEnricher` to yield the full `StrategistDecision` with derived fields (`target_weights`, `new_positions`, `close_reasons`, `trim_reasons`).

**Key invariants:**
- A SequentialAgent of three sub-agents: `StrategistContextShim` → `RetryingAgentWrapper[LlmAgent]` → `StrategistEnricher`. The enricher is a BaseAgent (not an `after_agent_callback`) because the callback path misfires under schema-retry wrapping.
- The `ContextShim` pre-populates `temp:held_positions_view`, `temp:ticker_evidence`, `temp:ticker_evidence_objects` so the LLM prompt's instruction-template substitution resolves them.
- The strategist must emit exactly one `TickerStance` per watchlist ticker per tick (no silence). Every verb except `no_action` carries rationale prose.
- `buy` weight is hard-capped at 5% (`max_buy_delta_per_trade`); `sell` and `update` are unconstrained prose verbs. Position theses are mutable; the agent is accountable via the audit trail.

### 3. executor — `src/agents/executor/`

**Purpose:** Translate risk-gated orders into broker API calls, record fills and slippage, persist the updated thesis book (`user:positions`) and standing thesis (`user:thesis`), and log closed trades to the DB.

**Inputs / Outputs:**
- **In:** `final_orders` (list of `Order` dicts from RiskGate), `positions` (bare-key cross-tick position dict), `portfolio` (broker snapshot), `strategist_decision` (for stance verbs that update thesis prose), `tick_id` (idempotency guard).
- **Out:** `executions` (list of `Execution` dicts with fills/rejections/partial fills), updates to `user:positions` and `user:thesis` via `after_agent_callback` that ADK auto-yields as state-delta events, trade-log rows written to the DB.

**Key invariants:**
- Idempotency guard: skips execution if `tick_id` matches `last_executed_tick_id` (double-execution guard on retry).
- The `after_agent_callback` (`_executor_thesis_writer_callback`) is the writer-of-record for cross-tick `user:positions` and `user:thesis` keys (writes via delta-tracked ADK state mutation that auto-yields a state-delta event).
- Every stance verb (buy/sell/update/no_action) triggers a `_verb_dispatch` call that mutates the thesis book deterministically; no second LLM pass.
- Broker rejections are logged but do not crash the tick; execution completes with a `status="rejected"` `Execution` record.

### 4. risk_gate — `src/agents/risk_gate/`

**Purpose:** Sit between the strategist and executor as a pure-Python deterministic gatekeeper that applies hard portfolio constraints (concentration, cash floor, per-ticker delta, total turnover) and converts the strategist's clamped weights into concrete broker orders.

**Inputs / Outputs:**
- **In:** `strategist_decision` (target weights dict), `portfolio` (broker snapshot for current weights and prices).
- **Out:** `final_orders` (list of `Order` dicts), `risk_clamps_applied` (telemetry list of `ClampRecord` dicts).

**Key invariants:**
- Constraints are applied in order: buy-delta clamp (defence-in-depth), concentration cap, cash floor, per-ticker delta, total turnover, no-short rule.
- No LLM calls; fully deterministic and fast.
- `update` and `no_action` stances are stripped from the weight dict before clamping (they carry no weight change, so clamping against stale/zero weights is semantically wrong).
- Every clamp decision is logged in `risk_clamps_applied` for post-hoc analysis.

### 5. agents-misc — `src/agents/{memory,snapshot,isolated_failure.py,llm_retry.py}`

**Purpose:**
- **isolated_failure.py:** Wrap a per-ticker analyst branch and suppress exceptions (logging but not propagating), so one ticker's persistent LLM failure does not abort the tick. The wrapped events are forwarded up to the point of failure; downstream joiners synthesise no-data verdicts for absent state keys.
- **llm_retry.py:** Wrap any LLM agent with three-class retry (rate-limit 429 / timeout wall-clock / schema-validation) plus independent per-class attempt budgets. Only wraps bare `LlmAgent` units; never wraps composite agents like `SequentialAgent` (buffering breaks inter-child state propagation).
- **memory/writer.py:** Append tick decisions to a rolling buffer with FIFO eviction, compress evicted entries into a day-digest summary, and yield state-delta events to persist both (cross-tick fields deferred to Spec C until `memory_buffer` and `day_digest` schema is finalised).
- **snapshot/agent.py:** Record equity-curve snapshots (bot total value, position count, SPY price, relative performance) after every tick so the backtest reporting layer can compute returns and Sharpe ratio without additional data fetches.

**Inputs / Outputs:**
- **isolated_failure:** In: any child agent's output. Out: same events, or nothing on exception.
- **llm_retry:** In: any LLM agent's output. Out: same success output; or exception on exhausted retries.
- **memory/writer:** In: analyst evidence, execution count. Out: updated `memory_buffer`, updated `day_digest` (both via state_delta).
- **snapshot/agent:** In: `portfolio`, `as_of`, `tick_id`. Out: `last_snapshot` (tick_id handshake) and DB snapshot row.

**Key invariants:**
- `isolated_failure` and `llm_retry` are BaseAgent wrappers; both respect contract Rule 1 (state writes via EventActions).
- Retry policies (429 backoff, timeout, schema cap) are read from `config/retry_429.json`, `config/analysts.json`, `config/strategist.json` at agent construction time.
- Memory writer's FIFO buffer is sized at 24 entries with eviction at 25; day_digest compression is deferred (Spec C).
- Snapshotter initialises `starting_capital` and initial SPY price on the first tick and reuses them for all subsequent relative-return calculations.

### 6. contract — `src/contract/` + `src/agents/contract/`

**Purpose:** Define canonic Pydantic schemas for evidence, verdicts, orders, executions, ticker stances, position theses, and the digest aggregator that collapses four analyst signals into one TickerEvidence per ticker.

**Inputs / Outputs:**
- **In:** Domain-specific analyst outputs (verdicts, raw data), analyst evidence (features + verdict), strategist stances.
- **Out:** Canonic Pydantic models (`AnalystEvidence`, `AnalystVerdict`, `AnalystReport`, `TickerEvidence`, `AggregateVerdict`, `TickerStance`, `PositionThesis`, `Order`, `Execution`, `ClampRecord`), extractors for feature computation, digest builder for signal aggregation.

**Key invariants:**
- No field on `AnalystVerdict`, `TickerStance`, `PositionThesis` may be added or removed without a default value (backward-compatibility gate at test-fixture level).
- `AnalystReport` (prose drivers, summary) is emitted only by LLM analysts (Fundamental, News); deterministic analysts (Technical, Social) leave it `None`.
- `AggregateVerdict` includes `disagreement` (variance of signed confidences) so the strategist can weight uncertainty independently of magnitude.
- Four-verb stance schema (`buy`, `sell`, `update`, `no_action`) with single `rationale` field (no reason/horizon/target_price hallucination patterns).
- Feature extractors are deterministic, time-aware (accept `as_of` for PIT clamping), and state-aware (technical extractor reads `reference_prices` from state).

### 7. data — `src/data/`

**Purpose:** Provide a pluggable provider registry and rate-limited async dispatch to five data domains (price history, company ratios, news, insider trades, social sentiment), with live providers for production and cache-backed providers for backtest replay.

**Inputs / Outputs:**
- **In:** Ticker symbol, time period/interval, optional historical clock (`as_of`), optional tick phase (open/close).
- **Out:** Domain models (`PriceHistory` with OHLCV bars, `CompanyRatios`, `NewsArticle` list, `InsiderTrade` / `NotableHolder` / `PoliticianTrade` bundles, `SocialSentiment`).

**Key invariants:**
- Providers are registered via `@register(domain, name, upstream, rate_per_minute, burst)` decorator; active providers are wired from `config/data.json` at runtime.
- Rate limiting is token-bucket per domain (Finnhub 60/min, yfinance 60/min, EDGAR 600/min, Quiver 30/min); a coroutine waits for a token (backpressure, not error).
- Live providers call real APIs (yfinance, Finnhub, etc.); backtest providers query a golden SQLite cache keyed by (ticker, domain, as_of).
- PIT (Point-In-Time) clamping: the `as_of` parameter is a hard boundary; data after `as_of` is filtered out before the return.

### 8. backtest — `src/backtest/`

**Purpose:** Iterate a historical tick schedule (NYSE open/close), inject cache-backed providers in place of live ones, and orchestrate end-to-end pipeline runs per tick with artefact collection (traces, decision logs, snapshots, metrics).

**Inputs / Outputs:**
- **In:** Window config (date range), backtest settings (cache root, tick schedule, starting cash, failure tolerance), broker implementation (FakeBroker).
- **Out:** Run artefact tree (`runs/<run-id>/`) with traces, decision logs, snapshot SQLite, manifest (success/failure/abort status per tick), final equity curve and metrics.

**Key invariants:**
- `driver.py` loops over scheduled ticks (NYSE phases: open/close), injects `as_of` (historical clock) and observability handles (`TraceWriter`, `DecisionLogger`) into state, calls the pipeline via ADK Runner, and collects results.
- `runner.py` is the end-to-end orchestrator: constructs the window schedule, builds the cache, wires the fake broker, instantiates the driver, runs all ticks, and generates final reports.
- `schedule.py` generates NYSE tick sequences (open/close) from a date range, respecting market holidays.
- `windows.py` loads window config (date range, name) from `config/backtest_windows.json`.
- `reporting.py` computes equity-curve metrics (Sharpe, max drawdown, Calmar) vs SPY baseline and renders `metrics.md` + `equity_curve.png`.
- `decision_logger.py` writes per-trade JSON snapshots (timestamp, ticker, action, fill price) for post-hoc decision audit.

### 9. orchestrator+lifecycle — `src/orchestrator/`, `src/lifecycle/`

**Purpose:**
- **orchestrator/:** Wire the live pipeline (AnalystPool → Strategist → RiskGate → Executor → Snapshotter as a SequentialAgent), construct state at tick-start (Phase 2), and provide persistence / session management glue.
- **lifecycle/:** Pre-flight environment checks (env vars, broker cash anchor, table state), scheduler integration, hard-reset path for re-initialising a deployment.

**Inputs / Outputs:**
- **orchestrator:** In: broker, session factory, provider config, watchlist. Out: complete SequentialAgent pipeline ready for ADK Runner, tick state dict, session service for persistence.
- **lifecycle:** In: environment, broker config, DB URL. Out: anchor snapshot (capital, SPY price, starting tick_id), scheduler job handle (or None if already running).

**Key invariants:**
- Phase 2 hydration (tick-start): populate fresh tick-scoped fields (tickers, reference_prices, portfolio) from source-of-truth; load cross-tick fields (user:positions, user:thesis, memory_buffer, day_digest) from ADK `user_state` table.
- Phase 4 persistence (tick-end): ADK's `DatabaseSessionService` persists every state_delta event that any agent yields (Rule 1).
- Bare-key bridge: `state["positions"]` is a working copy of `user:positions` (written at tick-start from persistence, read by Executor in-tick before the after-callback persists the new value).
- `initialise()` runs once at deployment; `scheduler.py` coordinates tick dispatch via Cloud Scheduler (live) or direct loop (backtest).

### 10. broker — `src/broker/`

**Purpose:** Provide a unified protocol (`Broker`) satisfied by both `FakeBroker` (backtest) and `Trading212Broker` (live/paper), plus a `Portfolio` model that snapshots the current position book and cash balance.

**Inputs / Outputs:**
- **In:** Market orders (ticker, action BUY/SELL, quantity); idempotency checks.
- **Out:** `Fill` records (broker order ID, actual quantity, execution price) or `BrokerRejection` exceptions (logged, not fatal).

**Key invariants:**
- `FakeBroker` simulates fills instantly at reference prices (from state or injected `_prices` map) with optional slippage.
- `Trading212Broker` issues real API calls to the Trading 212 REST API with error handling and retry on transient failures.
- `Portfolio` model includes per-ticker positions (shares, entry price, current price, market value) and total cash.
- Both brokers respect the same interface so the Executor and RiskGate are agnostic to the underlying implementation.

### 11. ops — `src/{observability,baselines,deploy,config}/`

**Purpose:**
- **observability/:** Emit structured trace snapshots (JSON boundary records), decision logs (trade metadata), and OTEL-compatible telemetry for production observability.
- **baselines/:** Compute SPY buy-and-hold performance metrics (Sharpe, max drawdown, Calmar) for backtest comparison.
- **deploy/:** Reserved for deployment-specific orchestration (currently empty).
- **config/:** Centralised JSON config loaders for models, analysts, strategist, risk gates, retry policies, schedules — the single source of truth for tuning without code edits.

**Inputs / Outputs:**
- **observability/trace:** In: agent outputs, state snapshots, LLM in/out. Out: JSON trace files (per-tick), decision logs (per-trade).
- **baselines/spy:** In: SPY close series. Out: `SPYMetrics` (cumulative return, Sharpe, drawdown, Calmar).
- **config/:** In: JSON files. Out: Pydantic-validated config dicts (models, analyst caps, retry policy, etc.).

**Key invariants:**
- `TraceWriter` is a non-serialisable handle injected into `state["temp:_trace"]` (ADK strips it at boundaries).
- `DecisionLogger` similarly injected as `state["temp:_decision_logger"]` for trade snapshots.
- Config loaders use `@lru_cache(maxsize=1)` so JSON is read once per process; tests override via `load_*_config(path=...)` hooks.
- The "two-tier convention" on output caps: prompt-facing value (smaller) sits in JSON; schema enforces a larger value with headroom to prevent Vertex pad-target pathology.

## 3. Cross-cutting concepts (CRITICAL)

- **rationale** — prose justification emitted by Strategist for every TickerStance verb and persisted in PositionThesis. Distinct from `last_reviewed_reason` (exit-only audit field) and `report.summary` (analyst prose drivers).
- **stance** — Strategist's per-ticker decision: one of four verbs (buy/sell/update/no_action) with weight and rationale. Emitted fresh every tick (no silence).
- **thesis** — Running market view and per-position entry/exit basis. Two forms: `user:thesis` (cross-tick standing thesis) and `PositionThesis` rows (mutable via stance verbs). Accountability anchor.
- **verdict** — Analyst's directional signal (lean, magnitude, confidence, key_factors). Two sources: deterministic or LLM. Always present (synthesised as no-data if omitted).
- **evidence** — Complete analyst signal record: verdict + features + metadata. Written via state_delta.
- **aggregate** — Weighted cross-analyst summary (confidence, magnitude, disagreement, prose). Computed deterministically.
- **TickerEvidence** — Per-ticker per-tick record for Strategist, containing per_analyst dict and aggregate verdict.
- **tick** — One pipeline invocation. Independent; state built fresh at Phase 2.
- **tick_id** — Deterministic per-tick identifier (backtest: `<window_key>-<date>-<phase>`; live: UUID).
- **position** — Open stock holding in portfolio and thesis book.
- **intent** — Executor's atomic state verb (buy/sell/update/no_action).
- **state_delta** — ADK EventActions payload for incremental state mutations. Every write must ride on state_delta (Rule 1).
- **as_of** — Historical clock for backtest ticks (PIT boundary).
- **temp:** — ADK prefix for invocation-scoped state keys (not persisted).
- **user:** — ADK prefix for user-scoped cross-tick keys (`user:positions`, `user:thesis`).
- **no_data** — Flag on AnalystVerdict indicating analyst had no signal this tick.
- **feature** — Deterministic numerical value extracted from raw analyst data.

## 4. Open questions & uncertainties

1. **memory_buffer and day_digest persistence schema (Spec C).** Contract commits to cross-tick storage for experiential memory, but schema is deferred. Current code reconstructs fresh each tick. Unclear: storage format, bounded-retention policy, relationship between the two fields, and migration story for schema changes.

2. **Smart-money analyst provider status.** Shelved pending PIT-correct implementations. Evidence key `smart_money_evidence` remains but always empty. Unclear: ETA on provider work and graceful degradation strategy.

3. **Observability handle injection timing (Spec B clarification).** Driver injects `temp:_trace` and `temp:_decision_logger` via direct state mutation after `create_session()` returns. Works with `InMemorySessionService` but unverified with `DatabaseSessionService` (scheduled for Spec C).

4. **Broker cash-mismatch detection in live.** `lifecycle/initialise.py` checks anchor at deployment. No ongoing cash-mismatch detection code. Unclear: live ops strategy for manual deposits/withdrawals mid-deployment.

5. **ADK Runner cleanup exceptions (known ADK 1.32 bug).** Driver catches `AttributeError` and `BaseExceptionGroup` after pipeline finishes. Unclear: whether bug persists in ADK 1.34+ or if workaround beyond logging is needed.

6. **Signal-validation guarantee on per-ticker analyst fan-out.** Phase 9 per-ticker branches with joiners synthesising no-data verdicts for absent tickers. Unclear: whether joiner synthesis is guaranteed or whether a ticker can be silently omitted if its branch dies before the joiner runs.

## 5. Apparent contradictions with policy

1. **Bare-key bridge persistence (contract §A).** Contract states `state["portfolio"]` is tick-scoped read-only. Code reads `state.get("positions")` (bare-key) as cross-tick working copy of thesis book. **Verdict:** Contract incomplete; documentation gap, not code bug. Contract §A should document bare-key bridge as tick-scoped working copy seeded from `user:positions` at Phase 2.

2. **StrategistEnricher as BaseAgent instead of after_agent_callback (contract §C Rule 3).** Contract states callbacks return None (pass) or final response (replace, no retry). Strategist used callback but misfired under schema-retry. Production sequences enricher as standalone BaseAgent. **Verdict:** Rule 3 carve-out justified by incident analysis in `src/agents/strategist/agent.py:17-28`. Code correct; contract correct.

3. **Rule 7 clarification on `user:`-prefixed keys (added 2026-05-23, Spec B).** Contract originally: "pipeline never reads/writes persistence directly." Spec B clarifies `user:` keys *are* persistence layer. No pipeline agent reads DB directly; it reads `user:*` from state and writes via state_delta. **Verdict:** No contradiction; clarification in place. Code implements pattern correctly.
