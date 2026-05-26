# Intent draft — agent 2

## 1. System purpose

StockBot is an AI-driven stock trading bot that uses multi-agent orchestration (Google ADK) to evaluate market signals and make portfolio allocation decisions. The system pulls real-time market data (prices, fundamentals, news, technicals, smart money) through a set of specialist analyst agents, aggregates their verdicts, and feeds them to a strategist LLM that decides portfolio stances (buy/sell/update/hold per ticker). A deterministic risk gate applies hard constraints, the executor submits orders via a broker API, and memory and observability writers capture decisions and learning for replay and analysis. The bot runs both in live mode (via Cloud Run) and in backtest mode (historical simulation) against identical code paths.

## 2. Module intents

### 1. analysts — `src/agents/analysts/`

**Purpose:** Extract structured directional signals (bullish/bearish/neutral + confidence) from raw market data. Deterministic analysts (Technical, Social) use rule-based heuristics; LLM analysts (Fundamental, News) call the model. All emit per-ticker verdict dicts with confidence, magnitude, key factors, and (for LLM analysts) prose drivers.

**Inputs / Outputs:**
- **Input:** Raw market data (OHLCV bars, company ratios, news articles, social sentiment) fetched by fetch agents; analyst heuristics from config.
- **Output:** AnalystEvidence records per ticker per analyst, written to state keyed by analyst domain (`technical_verdicts`, `fundamental_verdicts`, `news_verdicts`, `social_verdicts`). Each contains a `AnalystVerdict` schema (lean, magnitude, confidence, report) plus extracted deterministic feature vectors.

**Key invariants:**
- Phase 9 per-ticker fan-out (News and Fundamental are `SequentialAgent[FetchAgent, ParallelAgent[PerTickerBranches], JoinerAgent]`) ensures one ticker's 429 backoff does not block others; IsolatedFailureWrapper catches branch failures.
- Every state write rides on `state_delta` events (contract Rule 1).
- Deterministic analysts (Technical, Social) are BaseAgent subclasses with distinct `output_key`s; LLM analysts are wrapped in RetryingAgentWrapper for schema/rate-limit/timeout retry (Rule 4, Rule 5).
- Raw data fetches use `temp:` prefix keys so they don't survive tick boundaries.
- No analyst reads from the broker or persistence layer directly; all data comes from Phase 2 hydration.

### 2. strategist — `src/agents/strategist/`

**Purpose:** Read per-ticker evidence, the thesis book, and market context; synthesise a portfolio-wide decision with per-ticker stances (buy/sell/update/no_action) and document the reasoning.

**Inputs / Outputs:**
- **Input:** TickerEvidence records built by the digest aggregator (one per ticker per tick); thesis book (`user:positions`); closed trades log; memory buffer; current portfolio state; analyst evidence.
- **Output:** StrategistDecision with per-ticker stances (TickerStance), target portfolio weights, decision reasoning, optional thesis update, and confidence. The enricher derives target_weights and sell/update reasons from the stances.

**Key invariants:**
- Structured as `SequentialAgent[StrategistContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]`.
- ContextShim hydrates `temp:held_positions_view`, `temp:ticker_evidence`, `temp:ticker_evidence_objects` via state_delta (Rule 1).
- LlmAgent emits narrow StrategistLLMDecision via `output_key="strategist_decision"`.
- Enricher reads narrow output, runs derivation (target_weights calculation, sell_reasons extraction), and yields full StrategistDecision via state_delta.
- Retry wrap is *inside* the sequential, not outside, so ContextShim runs unwrapped.
- Four-verb stance vocabulary: buy (entry/add with weight 0 < w ≤ 0.05), sell (reduce/close with optional weight), update (revise prose), no_action (explicit hold).
- No agent reads the broker or persistence mid-tick; all data comes from Phase 2.

### 3. executor — `src/agents/executor/`

**Purpose:** Convert risk-gated orders into broker submissions, record fills, assemble and persist the position thesis book, and apply position lifecycle verbs (buy opens/adds, sell reduces/closes, update revises prose).

**Inputs / Outputs:**
- **Input:** RiskGate's `final_orders`; strategist's decision and stances; current portfolio; thesis book (`state["user:positions"]` via bare-key bridge).
- **Output:** Executions list (filled/rejected/partial status); updated `user:positions` thesis book; `user:thesis` standing market thesis. Both are persisted via after_agent_callback (Rule 1 Spec B: auto-yielded delta events).

**Key invariants:**
- Submits orders to broker via `submit_market` call; records fills and slippage.
- Idempotency guard: skips re-execution if `last_executed_tick_id` matches current tick.
- For every buy stance, reads the fill price and calls `apply_stance_to_thesis` to assemble the PositionThesis (opened_at, opened_price, weight, rationale).
- For every sell stance, trims or removes the thesis row; trade is logged to trade_log table.
- Updates thesis rows on update stances (rationale only).
- Thesis book writes happen in after_agent_callback so they ride the auto-yielded delta-tracked event (Rule 1 clarification).
- Bare-key `state["positions"]` bridge carries the cross-tick value for in-tick reads (legacy Band 4 pattern).

### 4. risk_gate — `src/agents/risk_gate/`

**Purpose:** Apply deterministic hard constraints (concentration, cash floor, max delta per ticker, turnover cap, buy-delta per-trade) to the strategist's proposed weights and synthesise executable broker orders.

**Inputs / Outputs:**
- **Input:** Strategist's target weights and stances; current portfolio and prices.
- **Output:** Clamped target weights; FinalOrders list (ticker, action BUY/SELL, quantity, est_price); ClampRecords telemetry.

**Key invariants:**
- Pure Python, fully deterministic, no LLM calls, no I/O.
- Applies clamps in sequence: buy-delta per-trade → max_position → max_delta → cash_floor → max_turnover.
- Rejects update and legacy hold stances from the weight clamping (no order generated).
- Validates that every sell or partial trim has rationale (position lifecycle contract).
- Converts final weights to concrete orders via `weights_to_orders` helper.
- Clamp records are merged and written to state for audit.

### 5. agents-misc — `src/agents/{attribution,memory,snapshot,isolated_failure.py,llm_retry.py}`

**Purpose:** Cross-cutting infrastructure for agent composition and observability.

**Key components:**
- **IsolatedFailureWrapper:** Catches exceptions from a child agent without propagating; logs structured failure with analyst/ticker/kind; yields no events on failure. Used by per-ticker fan-out analysts.
- **MemoryWriter (BaseAgent):** Appends a decision record (BufferEntry) to the experiential memory buffer after every tick; applies semantic dedup and eviction when buffer reaches BUFFER_EVICT_AT.
- **RetryingAgentWrapper:** Wraps a single LlmAgent with three-class retry (rate-limit 429, timeout, schema-validation). Buffers events and only yields on success; per-class attempt budgets configurable.
- **TraceWriter:** Singleton JSON snapshot collector for one tick; injected into state as `temp:_trace` so agents can append labelled boundary snapshots for offline debugging.
- **Snapshot agent:** Final agent in the pipeline; records `last_snapshot` state key for tick-completion validation.

**Key invariants:**
- All retry logic respects ADK's event-buffering semantics (Rule 5 on LoopAgent; here applied via custom wrapper).
- IsolatedFailureWrapper forwards every event up to failure point; joiner then synthesises no-data verdict for missing state key.
- Memory writer reads strategist decision, builds a BufferEntry with decision_tag, reasoning summary, smart-money flag, repeat detection, and optional embeddings; appends via `append_with_eviction`.

### 6. contract — `src/contract/` + `src/agents/contract/`

**Purpose:** Define the contract boundary schemas — the Pydantic models that encode analyst verdicts, evidence records, digest aggregates, and the strategist's stance vocabulary.

**Key schemas:**
- **AnalystVerdict:** lean (bullish/bearish/neutral), magnitude [0,1], confidence [0,1], key_factors list, report (prose drivers + summary) for LLM analysts, is_no_data flag.
- **AnalystEvidence:** One row per analyst per ticker per tick; contains verdict plus deterministic feature vector extracted by analyst-specific extractor.
- **TickerEvidence:** Deterministic aggregate per ticker per tick; contains all four analysts' evidence, AggregateVerdict (cross-analyst consensus lean, magnitude, confidence, disagreement, summary), analyst weights.
- **TickerStance:** Four-verb vocabulary — buy/sell/update/no_action per ticker with conditional field rules enforced by Pydantic validators.
- **PositionThesis:** One row of the thesis book; keyed by ticker under `state["user:positions"]`; tracks opened_at/price/weight (or None if watched not owned), rationale, last-reviewed trail.
- **StrategistLLMDecision / StrategistDecision:** Narrow shape emitted by LLM vs full shape with derived weights/reasons consumed downstream.
- **EvidenceWriter BaseAgent:** Reads LLM-emitted verdicts from state, runs extractors, synthesises no-data fills, writes complete AnalystEvidence list to state.
- **Digest aggregator:** Deterministic function that collapses per-analyst evidence into per-ticker evidence with signed-confidence-weighted consensus.

**Key invariants:**
- Every schema field with free-text caps is resolved from config at import time (strategist.json, analysts.json) so operators can tune via JSON.
- Pydantic validators enforce contract rules at parse time (e.g. buy stance must have weight, sell must have rationale).
- No circular imports; contract.evidence is a pure data layer.
- Evidence and verdict instances round-trip through JSON seamlessly for persistence.

### 7. data — `src/data/`

**Purpose:** Provide a single unified API for fetching market data (prices, fundamentals, news, social, insider trades, filings) with automatic rate-limiting, caching (in backtest), and point-in-time handling.

**Inputs / Outputs:**
- **Input:** Ticker symbols, date ranges, as_of timestamps.
- **Output:** PriceHistory (OHLCV bars), CompanyRatios (scalar fundamentals), NewsArticle lists, SocialSentiment snapshots, InsiderTrade lists, NotableHolder lists, Filing lists.

**Key invariants:**
- Rate-limit bucket per provider (Finnhub 60/min, yfinance 60/min, EDGAR 10/sec).
- All callers use dispatch registry (`data.dispatch`) rather than importing providers directly; orchestrator can wire live or cache providers per environment.
- Leaf I/O functions (e.g. `_fetch_company_news`) are the stub seams for testing.
- as_of parameter enables PIT-correct backtest replay; defaults to wall-clock in live mode.
- No agent reads providers directly; all fetch happens before tick pipeline starts (Phase 2).

### 8. backtest — `src/backtest/`

**Purpose:** Provide historical simulation harness: cache of pre-fetched data, tick-schedule generator, driver loop, reporting.

**Key components:**
- **Driver:** Loop over scheduled ticks, inject TraceWriter, run pipeline via ADK Runner, catch failures, flush observability.
- **Runner:** Orchestrate multi-tick backtest, instantiate cache providers, build per-window settings, accumulate manifest.
- **Schedule:** Generate NYSE tick schedule (open/close phases) per window.
- **Cache store:** SQLite schema and façade for golden-cache OHLCV, fundamentals, news, social, insider, filings.
- **Cache providers:** Implement data.providers interface; fetch from cache SQLite instead of live APIs.
- **Decision logger:** Append JSON snapshot per trade (ticker, action, reason, fill price, pnl).
- **Reporting:** Equity curve chart, metrics.md (Sharpe, max drawdown, calmar), forward-return backfill.
- **Audit subsystem:** Tripwires for data schema mismatches, upstream verifier for consistency checks.

**Key invariants:**
- Cache reads are deterministic; no API calls during backtest.
- Per-window cache (e.g. `backtests/baseline-2025-09/store.sqlite`) is the golden reference; tests build temporary caches in tmp_path.
- Schedule respects NYSE hours (13:30 UTC open in EDT, 14:30 in EST).
- Driver applies same Phase 2/3/4 contract as live; both lifecycles see identical state dict shape at identical phases.
- `last_snapshot` validation confirms pipeline reached the Snapshotter.

### 9. orchestrator+lifecycle — `src/orchestrator/`, `src/lifecycle/`

**Purpose:** Tie the pipeline, persistence, and the tick lifecycle together. Orchestrator composes agents; lifecycle handles initialization, scheduling, and state hydration at tick boundaries.

**Key components:**
- **pipeline.py:** Factory functions for analyst pool, strategist, memory writer, full pipeline. Constructs the SequentialAgent pipeline topology.
- **state.py:** Shared state schemas (TickState, Order, Execution, ClampRecord) and risk constants from config.
- **persistence.py:** SQLAlchemy ORM for trade_log, buffer_entries, ticker_stances, portfolio_snapshots tables; session service factory.
- **tick.py:** Live tick entrypoint — hydrate state at Phase 2, call pipeline via ADK Runner, persist at Phase 4.
- **initialise.py:** Pre-flight checks (env vars, broker cash, heuristics) and anchor snapshot.
- **hard_reset.py:** Clear all tables and reset broker cash.
- **scheduler.py:** cron or manual tick invocation.

**Key invariants:**
- Pipeline topology is identical in live and backtest.
- Both lifecycles read cross-tick fields from persistence at Phase 2, write at Phase 4.
- State dict seeded fresh each tick with tick_id, tickers, reference_prices, portfolio; cross-tick fields read from persistence.
- Observability handles (TraceWriter, DecisionLogger) injected as `temp:_trace`, `temp:_decision_logger` so they don't persist.
- Tick-scoped fields (analyst data, verdicts, orders, executions) discarded at tick boundary.

### 10. broker — `src/broker/`

**Purpose:** Abstract the brokerage API so the pipeline works against any broker implementation (live Trading212, fake for backtest/tests).

**Key abstractions:**
- **Broker protocol:** `submit_market(ticker, action, quantity) → Fill`, `get_portfolio() → Portfolio`, `position_size(ticker) → float`.
- **Trading212Broker:** Live implementation using Trading212 REST API.
- **FakeBroker:** In-memory mock for backtests and tests; tracks positions and cash; applies realistic slippage.
- **Portfolio:** Dict-like view of holdings (ticker → Position with quantity, last_price) plus cash balance.

**Key invariants:**
- Execution is synchronous (async in code but deterministic in backtest).
- Portfolio state is the source of truth for current holdings (not the thesis book, which is strategist intent).
- No agent reads broker state directly during the tick; portfolio is refreshed into state at Phase 2.

### 11. ops — `src/{observability,baselines,deploy,config}/`

**Purpose:** Cross-cutting infrastructure for configuration, observability, and deployment scaffolding.

**Key components:**
- **observability:** TraceWriter (JSON boundary snapshots), DecisionLogger (per-trade snapshots), OTEL setup (noop in pre-deployment state), drain utilities for batch logging.
- **baselines:** SPY buy-and-hold metrics (Sharpe, max drawdown, calmar) for performance comparison.
- **config:** Centralized JSON config loaders (data.json, strategist.json, analysts.json, risk_gate.json, backtest_settings.json, watchlist.json). Single-source-of-truth pattern.
- **deploy:** Empty directory (deployment scaffolding deferred).

**Key invariants:**
- All free-text caps (LLM output char limits) are sourced from config at import time.
- TraceWriter is a shared singleton across all session-state copies for one tick (via __deepcopy__ pass-through).
- DecisionLogger appends JSON records that the backtest driver collects for per-run artefacts.
- Config loaders validate schema at import time; env vars are read at Phase 1.

## 3. Cross-cutting concepts (CRITICAL)

| Term | Canonical meaning | Notes |
|------|-------------------|-------|
| **tick** | One invocation of the pipeline (analysts → strategist → executor), scheduled hourly on NYSE open/close. Atomic unit of work. Tick-scoped state is rebuilt fresh; cross-tick state is persisted and re-hydrated. | Tick ID is deterministic per schedule slot (backtest) or wall-clock (live). |
| **Phase 1, 2, 3, 4** | Tick lifecycle stages: Phase 1 = run-start (once per process), Phase 2 = tick-start (hydrate from persistence), Phase 3 = pipeline execution, Phase 4 = tick-end (persist, flush observability). | Defined in contract-invariants.md §B. Both lifecycles follow identical contract. |
| **state dict / session state** | ADK session object (`adk_session.state`) holding all agent-readable/writable working data for one tick. Mutated by agents via `state_delta` events. Persisted at Phase 4. | Tick-scoped fields (analyst data, verdicts) discarded at tick boundary. Cross-tick fields (`user:positions`, `memory_buffer`, `day_digest`, `user:thesis`) read from persistence at Phase 2. |
| **state_delta** | EventActions field carrying state mutations that ADK persists via SessionService. Required channel for all durable writes (Rule 1). | Direct `state[key] = value` is in-memory only and does not survive tick boundary on serialising backends. |
| **verdict** | Per-ticker directional call from an analyst: lean (bullish/bearish/neutral), magnitude, confidence. Part of AnalystVerdict schema. | Deterministic analysts (Technical, Social) extract features → magnitude. LLM analysts emit lean + magnitude + prose drivers + confidence. |
| **evidence** | Per-analyst per-ticker per-tick complete record: verdict + deterministic feature vector + analyst metadata. Part of AnalystEvidence schema. | Built by evidence-writer callback; persisted for KB learning loop (backlog). |
| **TickerEvidence** | Per-ticker per-tick aggregate of all four analysts: per_analyst dict + AggregateVerdict (cross-analyst consensus) + analyst weights. | Deterministically built by digest aggregator from analyst evidence list. Strategist reads TickerEvidence records, not raw per-analyst verdicts. |
| **stance** | Per-ticker decision by the strategist: a TickerStance with intent (buy/sell/update/no_action), ticker, optional weight, optional rationale. | Four-verb vocabulary. Intent determines required/forbidden fields. One stance per ticker per tick (explicit even for no_action). |
| **StrategistDecision** | Full strategist output: stances list + derived target_weights + reasoning + thesis + confidence + sell_reasons + update_reasons. | LLM emits narrow StrategistLLMDecision; enricher derives full StrategistDecision. |
| **target_weights** | Dict mapping every watchlist ticker → portfolio weight [0, 1]. Sum is typically < 1.0 (cash reserve). Derived by enricher from stances. | Consumed by risk gate and executor. One entry per ticker (0 = no position). |
| **PositionThesis / thesis book** | Strategist's persistent record of its reasoning per held/watched ticker. Dict keyed by ticker under `state["user:positions"]`. | Row fields: opened_at/price/weight (or None if watched), rationale (mutable on buy/update), review trail. Persisted via ADK `user_state` table. Executor applies stance verbs to mutate rows. |
| **rationale** | Prose justification for a stance or thesis entry. Mutable on buy (entry or add) and update stances. Immutable on sell (captured in last_reviewed_reason for audit). | Single field on TickerStance (replaces old split rationale/reason/catalyst). Every change of view must be justified. |
| **reason / sell_reason / update_reason** | Prose documenting why a position is being trimmed/closed (sell) or thesis revised (update). Captured in strategist's sell_reasons and update_reasons dicts. | Not the same as TickerStance.rationale (which is per-stance prose); these are aggregated decision-level reasons. |
| **order** | Executable trade instruction from risk gate: ticker, action (BUY/SELL), quantity, est_price. Submitted by executor to broker. | Created by `weights_to_orders` helper after risk gate finishes clamping. |
| **execution** | Result of submitting an order to the broker: filled/rejected/partial status, actual_price, actual_quantity, slippage_bps, broker_order_id, error. | Recorded by executor; used for PnL and slippage analysis. |
| **memory_buffer / day_digest** | Cross-tick experiential memory: rolling buffer of decision records + compressed daily digest. Persisted to buffer_entries table. | Deferred to Spec C (design TBD). Buffer appends BufferEntry records; day_digest is a summary string. |
| **analyst evidence / raw data** | Raw market data fetched by analyst before-callbacks: OHLCV bars, company ratios, news articles, social sentiment, insider trades. Stored in `temp:{analyst}_data` keys. | Fetch agent runs first; per-ticker LLM agent reads from temp key; evidence callback runs extractor on raw data to build feature vector. |
| **feature / feature_vector** | Deterministic numerical features extracted by analyst extractor from raw data (e.g. P/E ratio, RSI, sentiment score). Part of AnalystEvidence.features dict. | Extractors are analyst-specific; technical extracts from OHLCV (RSI, MA), social extracts from sentiment snapshot. |
| **no_data flag / is_no_data** | Boolean on AnalystVerdict indicating the analyst had no data to work with (provider returned empty, data fetch failed, etc.). Verdict is then neutral with magnitude 0.0. | Aggregate verdict excludes no-data rows from confidence/disagreement calculations. Strategist prompt indicates absence. |
| **LllmAgent / LlmAgent** | ADK agent wrapper that calls a Gemini model with a prompt template and expects a Pydantic-schema-validated JSON response. | Strategist, NewsAnalyst, FundamentalAnalyst are LlmAgents. Wrapped in RetryingAgentWrapper for retry. Technical and Social are BaseAgent subclasses (no model call). |
| **ParallelAgent / SequentialAgent** | ADK agent composition primitives. Parallel runs sub-agents concurrently, merges state_deltas. Sequential runs sub-agents in order, threading state through. | Analyst pool uses both: Parallel outer layer, Sequential per-ticker branches. Strategist uses Sequential (ContextShim → LlmAgent → Enricher). |
| **retry / RetryingAgentWrapper** | Custom wrapper that catches and classifies agent failures into three classes: rate-limit (429), timeout, schema-validation. Each has its own attempt budget. Buffers events, only yields on success. | Not ADK's LoopAgent; this is a thin wrapper specific to LLM call resilience. Applied to LLM analysts only. |
| **isolated failure / IsolatedFailureWrapper** | Custom wrapper that catches exceptions from a child agent and logs a structured warning without propagating. Yields no events on failure. | Applied to per-ticker branches (Fan-out design). Allows one ticker's failure (429 backoff) not to block others. |
| **joiner** | BaseAgent that reads per-ticker working state keys and synthesises a canonical verdict key. Used by Fundamental and News per-ticker fan-out. | Merges disparate per-ticker state dicts into consolidated `fundamental_verdicts` / `news_verdicts` for digest aggregator. |
| **as_of** | Historical clock timestamp (backtest) or wall-clock now (live). Injected into state at Phase 2 so agents see deterministic PIT clock. | Data providers accept as_of parameter for PIT-correct fetches. Executor stamps opened_at/opened_tick_id with as_of so position thesis is deterministic. |
| **portfolio / positions** | Current broker holdings (state["portfolio"], source of truth from broker). Distinct from user:positions (strategist intent thesis book). | Portfolio.current_weights() computes weight dict for risk gate. Executor reads positions via broker on every tick. |
| **portfolio weight / weight** | Fraction of portfolio value allocated to a ticker [0, 1]. Sum of all weights + cash reserve = 1.0. | Used by risk gate and strategist. Order quantity is derived from weight change. |
| **risk gate / constraint** | Deterministic rules clamping strategist weights: max position per ticker, max delta per trade, cash floor, total turnover cap, no shorting. | Applied sequentially; clamp records document each application for audit. Not an agent contract violation (soft guard). |
| **clamp** | Reduction of a weight due to a risk constraint. Recorded as ClampRecord with rule name, ticker, before/after weights. | One weight may be clamped by multiple rules in sequence. |

## 4. Open questions & uncertainties

1. **memory_buffer and day_digest persistence (Spec C deferred).** The contract commits to cross-tick lifetime for both fields, but their schema, storage, and rebuild strategy are not yet designed. Current code rebuilds them fresh at Phase 2 (not persisted). When should these fields transition from tick-scoped to true cross-tick?

2. **Basis for analyst weight configuration.** config/analysts.json contains per-analyst weights used by the digest aggregator. Where do these weights come from (fixed heuristics, learned from performance)? Are they tuned per-window or globally?

3. **Smart money analyst status.** The module is shelved (2026-05-19) pending PIT-correct providers for notable_holders and politician_trades. Is this a blocking dependency for performance or a nice-to-have enhancement?

4. **Portfolio weight vs position weight semantics.** The thesis book records `weight` as a portfolio fraction; the executor's BUY handler applies it. Risk gate also reads/writes target_weights. Are these always synonymous or is there drift?

5. **Thesis book initialization on first tick.** When a new user starts, is the thesis book seeded empty, or does the strategist have guidance to assume a baseline stance (e.g. all-cash or benchmark-weighted)?

6. **Deterministic analyst feature extractors stability.** Technical, Social extract features from live data each tick. If live data goes missing (provider outage), does the feature vector degrade gracefully or does the analyst verdict emit is_no_data=True?

7. **LLM prompt stability across model versions.** Strategist, News, Fundamental prompts are in src/agents/{strategist,news,fundamental}/prompts.py. When the underlying Gemini model is updated (e.g. gemini-2.5 → gemini-3.0), how are prompts validated to still produce valid stances?

8. **trade_log vs memory_buffer distinction.** trade_log captures closed positions (opened/closed timestamps, PnL). memory_buffer captures decision reasoning. How are these two used together (e.g. for post-trade analysis or backlog B2 KB learning)?

9. **As_of timezone handling.** Backtest uses historical NYSE time; live uses UTC. Are all timestamp fields expected to be in UTC and localized at the observability/reporting layer?

10. **Strategist first-tick behaviour.** On the first tick of a run, the thesis book is empty. Does the strategist emit a full baseline stance per watchlist ticker, or does it emit no_action / update to build the thesis incrementally?

## 5. Apparent contradictions with policy

**None found.** The source code faithfully implements the contract-invariants.md specifications:

- State mutations ride on state_delta events (Rule 1) — verified in strategist enricher, executor callback, evidence writer, memory writer.
- Cross-tick fields are read from persistence at Phase 2 and written via state_delta at Phase 4 — `user:positions` and `user:thesis` flow through ADK's DatabaseSessionService.
- Lifecycle symmetry is enforced: both live and backtest see the same state dict shape at the same phases.
- ParallelAgent branches have unique output keys (Rule 4) — technical_verdicts, fundamental_verdicts, news_verdicts, social_verdicts are distinct.
- LoopAgent usage respects Rule 5 (max_iterations and/or escalate-driven termination) — RetryingAgentWrapper implements bounded attempts per failure class.
- Observability writes are additive and do not change pipeline outputs (Rule 8) — TraceWriter, DecisionLogger, and Decision-Logger are injected as `temp:` keys.

Test policy (test-policy.md) is also honoured:

- Tests mock at the leaf I/O boundary (provider _fetch functions), not above (Rule 5).
- No real API keys are expected; stubbing is comprehensive (Rule 1).
- Backtest cache writes are confined to tmp_path for tests (Rule 2).
- LLM-touching tests are gated by RUN_LLM_TESTS=1 (Rule 4).
- Tests assert on positive output state, not just completion (Rule §A.7) — integration tests check verdict content, order counts, state delta shapes.

---

**Audit completed 2026-05-26.**
