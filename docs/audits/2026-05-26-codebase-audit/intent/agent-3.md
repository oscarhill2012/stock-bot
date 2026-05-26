# Intent draft — agent 3

## 1. System purpose

StockBot is an AI-driven stock trading bot that runs on a cadence (hourly ticks) and uses multi-agent reasoning to make long/short decisions. The system ingests stock market data (price history, fundamentals, news sentiment, technical indicators, insider trades), routes it through four specialist analyst agents who emit verdicts, feeds those verdicts to a strategist LLM that decides which tickers to buy/sell/hold, enforces risk constraints via a deterministic RiskGate, executes the decisions via a broker (trading212, paper or live), and records outcomes and learned thesis for the next tick. It runs identically in two modes: (a) *Live* — one invocation per tick in a Cloud Run Job, cold-starting each time; (b) *Backtest* — one long-lived Python process replaying a historical window tick-by-tick. The contract-invariants.md document mandates that both lifecycles produce the same pipeline outputs by forbidding reliance on in-process state survival.

## 2. Module intents

### 1. Analysts — `src/agents/analysts/`

**Purpose:** Emit directional verdicts (bullish/bearish/neutral) for each ticker on the watchlist. Each analyst examines one signal domain (technicals, fundamentals, news sentiment, social sentiment, smart money) and yields per-ticker evidence. The pool runs these concurrently and produces four per-ticker verdict lists that the strategist then consumes.

**Inputs / Outputs:**
- **In:** state["tickers"] (watchlist), state["reference_prices"] (SPY + sector ETFs), state["portfolio"] (current holdings)
- **Out:** Four parallel verdict lists written to state via state_delta: state["technical_verdicts"], state["fundamental_verdicts"], state["news_verdicts"], state["social_verdicts"]; corresponding evidence lists: state["*_evidence"]
- **Fetch leaf:** External provider calls (yfinance, Finnhub, EDGAR, Tiingo, etc.) cached in backtest, live in production

**Key invariants:**
- Each analyst owns exactly one output_key so concurrent verdicts do not collide (Rule 4).
- Deterministic analysts (Technical, Social) emit verdicts via extract→derive pattern with no LLM; LLM analysts (News, Fundamental) fan out per-ticker so one ticker's 429 backoff does not block others.
- All verdicts include is_no_data flag; LLM analysts populate report block (prose drivers); deterministic analysts leave report as None.
- Verdicts are written via state_delta event (Rule 1), not direct dict mutation.
- Evidence rows (keyed by ticker) are written to separate *_evidence keys and consumed by the strategist's context shim.

### 2. Strategist — `src/agents/strategist/`

**Purpose:** Aggregate analyst verdicts and devise a weekly/daily thesis plus per-ticker stances (buy/sell/update/no_action). The strategist is the *sole agent responsible for position sizing and duration decisions*; it decides when to open, close, trim, or hold each position and maintains an ongoing thesis narrative that justifies the portfolio.

**Inputs / Outputs:**
- **In:** state["temp:held_positions_view"] (open positions from the thesis book), state["temp:ticker_evidence"] (consolidated per-ticker analyst views), state["strategist_decision"] (LLM output, narrow shape), reference positions and thesis from state["user:positions"]
- **Out:** state["strategist_decision"] (enriched, full shape) carrying stances list, target_weights dict, decision_tag, reasoning, thesis (optional update)
- **Internally:** SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]

**Key invariants:**
- One stance per ticker per tick (explicit no_action when holding); no silence-is-hold rule.
- Stances are four-verb: buy (enter or add), sell (reduce or close), update (revise prose, no trade), no_action (reviewed, no change).
- Strategist never reads or writes the broker directly; all position state flows through state["user:positions"] (the thesis book) which is persisted by the Executor's after_agent_callback (Rule 1 clarification).
- target_weights dict must cover every watchlist ticker (0 = no position).
- Retry wrapper is *inside* the Sequential so ContextShim's state_delta reaches the ADK runner before LlmAgent renders its instruction template (per-tick rebuild contract).
- Thesis is optional; when None, the prior thesis is carried forward unchanged (not cleared).

### 3. Executor — `src/agents/executor/`

**Purpose:** Translate risk-gated orders into broker market calls, record fills plus slippage, and maintain the cross-tick thesis book (state["user:positions"]) that tracks why each position was entered and what would invalidate it.

**Inputs / Outputs:**
- **In:** state["final_orders"] (from RiskGate), state["portfolio"] (current holdings), state["positions"] (bare-key thesis book, cross-tick value)
- **Out:** state["executions"] (fill records), writes state["user:positions"] via after_agent_callback (Rule 1 auto-yield), writes trade_log DB rows on close
- **Broker calls:** submit_market (BUY/SELL), position_size, get_portfolio

**Key invariants:**
- Executor applies buy/sell verbs to the thesis book via apply_stance_to_thesis helper; buy stances create PositionThesis rows, sell stances close or trim them.
- Idempotency guard: skips re-execution if state["last_executed_tick_id"] == current tick_id.
- Cross-tick write is via after_agent_callback (auto-yielded by ADK as state_delta event); this is the writer-of-record for user:positions and user:thesis.
- Fills are recorded with slippage (requested qty vs actual); broker rejection exceptions are logged but do not crash the tick.
- Trade-log rows are written to the DB immediately on position close (full close only, not trims).

### 4. RiskGate — `src/agents/risk_gate/`

**Purpose:** Pure-Python deterministic constraint applier. Clamp the strategist's target weights to obey hard portfolio rules (concentration caps, cash floor, per-ticker buy delta, turnover limits), validate position lifecycle (every sell has a reason), and emit concrete broker Orders for the Executor.

**Inputs / Outputs:**
- **In:** state["strategist_decision"] (full shape with target_weights and stances)
- **Out:** state["final_orders"] (Order list keyed by action/ticker/price), defensive write to state["last_risk_gate_decision"] (in-tick handshake)
- **No LLM calls.**

**Key invariants:**
- Clamping is idempotent; applying the clamp twice produces the same result as clamping once.
- Buys that exceed max_buy_delta_per_trade are clamped at the stance level (defence-in-depth, re-check even if schema validator passed).
- Update and no_action stances are stripped from the proposed weights before clamping (they carry no trade, so their tickers should not appear in the weight dict).
- Sell stances must have a reason (validated via contract).
- Orders are emitted only for tickers whose weight changed; tickers at their current weight are silent (no churn).

### 5. Agents-misc — `src/agents/{attribution,memory,snapshot,isolated_failure.py,llm_retry.py}`

**Purpose:** Auxiliary agent patterns and support layers.

**Components:**
- **MemoryWriter** — rolling buffer of (decision_tag, reasoning, smart_money_flag, repeat_detection, embedding) entries; evicts oldest to day_digest via compress when buffer reaches 25 entries; deferred persistence (Spec C) so these rows currently rebuild from empty each tick.
- **Snapshotter** — records bot portfolio value plus SPY price after every tick for equity-curve reporting; anchors starting capital and SPY price on first tick, then computes returns relative to those anchors.
- **IsolatedFailureWrapper** — wraps per-ticker analyst branches; catches and logs exceptions without propagating them so one ticker's failure does not abort the tick. Yields zero events on exception (joiner synthesises no-data verdict for the missing key).
- **RetryingAgentWrapper** — wraps LLM agents; retries on three failure classes (HTTP 429, wall-clock timeout, pydantic.ValidationError) with independent budgets and exponential backoff. Buffers events until completion so retries are transparent to the ADK Runner.
- **Attribution** (placeholder) — intended for trade attribution plus p&l analysis per signal source; currently shelved.

**Key invariants:**
- Memory buffer's dedupe check looks for repeat decision_tag to avoid learning the same signal twice in one window.
- IsolatedFailureWrapper is only used for per-ticker LLM analysts (News, Fundamental); deterministic analysts (Technical, Social) do not need it.
- RetryingAgentWrapper must only wrap single LLM agents, never composites, because event buffering breaks inter-child state propagation.
- Snapshotter's call to get_price_history uses the same as_of as the tick so backtest replays do not leak wall-clock SPY prices into historical snapshots.

### 6. Contract — `src/contract/` + `src/agents/contract/`

**Purpose:** Pydantic schemas for analyst verdicts, evidence, strategist input/output, and the strategist's per-ticker prompt render. The contract defines the shape of the signal surface the strategist sees and the shape of outputs all downstream agents consume.

**Key invariants:**
- ReportDriver (inside AnalystReport) has name, direction (bull/bear/neutral), weight, body; at least 2, at most 4 drivers per report.
- AnalystVerdict validator enforces: if is_no_data=False, report must be populated (LLM analysts only; deterministic leave report as None).
- TickerStance forbids extra kwargs (catches drift from deleted fields like target_price, stop_price, horizon, etc.) with extra="forbid".
- Strategist prompt renderer (strategist_prompt.py) is purely presentational; it formats TickerEvidence features into human-readable blocks with graceful degradation (missing features render as "(no data)").

### 7. Data — `src/data/`

**Purpose:** Provider abstraction layer. Agents request data via domain wrappers (get_price_history, get_company_ratios, get_stock_news, etc.); the orchestrator swaps implementations (live providers vs cache-backed) without agent code changes.

**Domains (8):** price_history (yfinance), company_ratios (yfinance scalars), news (Finnhub), social_sentiment (Tiingo), insider_trades (edgartools), notable_holders (edgartools 13F), filings (edgartools XBRL), politician_trades (quiver + SEC form 345).

**Key invariants:**
- Agents never import provider modules directly; they call domain wrappers (get_price_history(ticker, as_of=...)).
- Rate-limit budgets are enforced per source via AsyncRateLimiter (token bucket); slowest source (Quiver at 30/min = 2s floor) dictates the decision interval.
- as_of parameter is mandatory for backtest calls (ensures PIT correctness) and optional for live (defaults to wall-clock).
- Cache providers read from golden SQLite under backtests_root/window/store.sqlite; test fixtures build temporary caches in tmp_path.
- Config/data.json maps each domain to active provider; a mismatch between the JSON and a provider's @register decorator raises RuntimeError at import time.

### 8. Backtest — `src/backtest/`

**Purpose:** Historical-window replay harness for validation and strategy tuning. Reads a configuration window (date range, watchlist, settings), materialises the golden cache, constructs a FakeBroker with starting capital, runs the live pipeline once per scheduled tick, and produces an equity curve plus metrics report.

**Key invariants:**
- Test backtest runs use tick_limit=1 on the baseline window (2025-09) to cap LLM cost; replay marker (-m replay) flags long historical runs.
- Reference symbols (SPY plus 11 SPDR sector ETFs) are fetched once per tick from cache and stored in state["reference_prices"].
- Driver pre-flights the watchlist: tickers with no OHLCV bars in the window are dropped before tick iteration begins.
- Each tick's session uses a fresh InMemorySessionService (backtest only; live uses DatabaseSessionService) so cross-tick state requires explicit persistence reads/writes.
- Backtest uses the same pipeline topology as live; the only differences are: FakeBroker instead of Trading212Broker, cache providers instead of live, and optional observability sinks (trace to files, decision_log to JSON).

### 9. Orchestrator + Lifecycle — `src/orchestrator/`, `src/lifecycle/`

**Purpose:** Orchestrator assembles the pipeline and manages tick-boundary state transitions (Phase 2 hydration, Phase 4 persistence). Lifecycle handles initialisation (preflight checks, anchor snapshot, scheduler resume) and provides entry points for live and backtest ticks.

**Key invariants:**
- Phase 2 must populate all §A row fields from their Source of Truth; cross-tick fields cannot be seeded empty (contract violation).
- Phase 4 must persist all state_delta events written during the tick; state-dict-only writes are not durable.
- Live and backtest are lifecycle-agnostic to the pipeline; they differ only in broker/provider/session-service concrete implementations.
- Orchestrator never mutates state["portfolio"], state["reference_prices"], state["tickers"], or state["tick_id"]; the lifecycle wrapper owns those.

### 10. Broker — `src/broker/`

**Purpose:** Abstraction over brokerage execution. Single interface (Broker protocol) implemented by Trading212Broker (live/paper) and FakeBroker (backtest). Each provides submit_market (BUY/SELL), position_size, and get_portfolio.

**Key invariants:**
- Broker is the source of truth for portfolio state; state["portfolio"] is a working copy refreshed at tick boundaries.
- Executor does not call broker directly mid-tick; it reads portfolio at Phase 2 and executes at Phase 3-4.
- FakeBroker can be injected with _prices dict for tests (used when a ticker is not held).

### 11. Ops — `src/{observability,baselines,deploy,config}/`

**Purpose:** Cross-cutting infrastructure for observability, baseline metrics, configuration, and deployment readiness.

**Key invariants:**
- Observability writes never mutate contract-bearing state (Rule 8); they are read-only consumers of state both lifecycles produce.
- Config edits are not hot-reloadable; a process restart is required.
- Trace and decision logs go to artefact trees in backtest (runs/run_id/traces/, .../decisions/) and to GCS in live (carve-out D1).
- All timestamps are UTC by convention; backtest replays enforce PIT correctness via as_of parameters.

## 3. Cross-cutting concepts

**Verdict, Evidence, TickerEvidence:** Verdict is an analyst's directional output (lean, magnitude, confidence, report). Evidence wraps a verdict with analyst/ticker metadata. TickerEvidence is a consolidated per-ticker view from all four analysts, flattened for prompt rendering.

**Stance, TickerStance, Thesis:** Stance is a per-ticker decision (buy/sell/update/no_action) with intent, ticker, optional weight, optional rationale. Thesis is prose narrative at two levels: standing market thesis (state["user:thesis"], cross-tick) and per-position thesis (rows in state["user:positions"], cross-tick).

**Position, PositionThesis:** Position is a held quantity in the broker's portfolio. PositionThesis is a row in the thesis book (state["user:positions"]), holding entry details, current rationale, and reviewed metadata.

**Order, Fill, Portfolio:** Order is a broker instruction (BUY/SELL + ticker + qty + price). Fill is the broker's execution confirmation. Portfolio is a snapshot of cash + positions (source of truth is the broker, never persisted to state).

**Rationale, Reasoning:** Rationale is per-ticker stance prose (why buy/sell/update). Reasoning is tick-level strategist prose (overall narrative).

**Analyst, Analyst branch:** One signal domain (Technical, Fundamental, News, Social, Smart Money shelved). Deterministic run via extract/derive; LLM run per-ticker via ParallelAgent fan-out inside SequentialAgent.

**Report, ReportDriver:** LLM-only output. Report = summary + 2-4 ReportDriver entries (name, direction, weight, body). Deterministic analysts leave report as None.

**Evidence Writer:** BaseAgent consuming verdicts and writing evidence to state["*_evidence"] keys.

**Signal, Feature:** Quantitative or qualitative input to a verdict (e.g., momentum, earnings surprise, sentiment). Flattened into TickerEvidence for prompt.

**Decision, Decision tag:** Strategist's tick-level output. Tag is snake_case (e.g., "earnings_surprise_pivot"). Used for audit and memory dedup.

**Risk Gate, Constraint, Clamp:** RiskGate is deterministic. Constraint is a portfolio rule (concentration, cash floor, buy delta, turnover). Clamp reduces proposed weights to satisfy constraints (idempotent).

**State, session.state:** ADK session dict. Cross-tick keys (user:positions, user:thesis) persist. Tick-scoped rebuild. Temp: prefix is invocation-local (never persisted).

**Output key, state_delta:** ADK event mechanism. LlmAgent uses output_key="name" → state["name"]. BaseAgent uses state_delta={...} events (atomic merge). All mutations ride on events (Rule 1).

**Tick, Invocation:** One pipeline execution (live: one Cloud Run Job; backtest: one schedule iteration). Phase 2 hydration → Phase 3 pipeline → Phase 4 persistence.

**Window, Backtest window:** Historical date range for replay (e.g., "baseline-2025-09"). Runner executes all ticks and produces equity curve/metrics.

**Cache, Golden cache:** SQLite under backtests/window/store.sqlite (populated by backtest_fetch script). Providers query it in backtest instead of live APIs.

**Isolated failure, Branch isolation:** IsolatedFailureWrapper wraps per-ticker analyst branches so one ticker's exception does not propagate. Logged, zero events yielded, joiner synthesises no-data.

**Retry, Retrying wrapper:** RetryingAgentWrapper wraps LLM agents; handles 429, timeout, ValidationError with independent budgets + backoff. Events buffered until completion (transparent to ADK).

**Memory buffer, Day digest:** Experiential memory (Spec C deferred). Buffer = rolling list of (decision_tag, reasoning, smart_money, is_repeat, executions, embedding). Digest = summarised narrative. Both cross-tick; currently rebuild empty each tick.

**Handshake, Completion marker:** In-tick keys for boundary validation. last_executed_tick_id (Executor wrote it). last_snapshot (Snapshotter wrote it). Read by backtest driver to assert completion.

**Context shim, Evidence view:** StrategistContextShim BaseAgent hydrates temp:held_positions_view, temp:ticker_evidence, temp:ticker_evidence_objects by reading verdicts. Runs before Strategist LlmAgent.

**As_of, Point-in-time:** Historical clock supplied to data providers in backtest (prevents lookahead). Live omits as_of (defaults to wall-clock).

**Idempotency guard:** Check preventing tick re-run. Executor checks state["last_executed_tick_id"] == current tick_id and skips if true.

## 4. Open questions & uncertainties

1. **Smart Money analyst shelving** — Module exists but not wired (notable_holders / politician_trades PIT-correctness issue). Design or implementation blocker?

2. **Memory buffer + day digest persistence** — Spec C deferred. Currently rebuild from empty each tick. Design doc exists, or TBD?

3. **Bare-key "positions" bridge** — Executor comments say it's cross-tick and carries BUY→SELL in-tick channel. Is this legitimate per §A or temporary compat shim?

4. **Thesis revision carve-out** — Contract 2026-05-20 in-tick callback carve-out (direct state mutation for same-tick consumers). Intended to stay or retire?

5. **MemoryWriter's compress stub** — agents/memory/compress.py implementation status? No-op or real logic in PR?

6. **Temp: prefix lifecycle across deep-copy** — How do TraceWriter/DecisionLogger survive InMemorySessionService deep-copies? (__deepcopy__ passthrough works, but do bare dicts?)

7. **Evidence vs verdict duplication** — Why write both? Evidence is separate projection for audit, or redundant?

8. **AnalystReport padding risk** — max_length intentionally NOT set; Vertex's constrained decoder treats schema maxLength as pad target. Is this working as intended or risk?

## 5. Apparent contradictions with policy

1. **Rule 1 callback carve-out vs auto-yield** — Legacy _strategist_validation_callback does direct state mutation (conformant for in-tick, per policy). But adds mental load; retire intent?

2. **Strategist decision writer vs enricher dual responsibility** — Enricher validates+enriches, yields state_delta. DecisionWriter reads it back and writes to DB. Redundant or separation-of-concerns?

3. **Phase 2 reference_prices refresh** — Backtest driver calls _seed_reference_prices per tick with as_of. Runner's Phase 1 call uses as_of=None as "safety net". Does Phase 2 overwrite Phase 1? Should Phase 1 skip it?

4. **Snapshot vs last_snapshot naming** — §A uses "last_snapshot" (state key). Agent is "SnapshotterAgent". Consistent; no issue.

**Conclusion:** Contract-invariants.md and test-policy.md are well-aligned with source. No systemic policy drift; violations are localised (e.g., temp: key injection semantics).
