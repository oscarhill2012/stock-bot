# Multi-Agent System Design

**Date:** 2026-05-06
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Scope:** Phase 1 of StockBot — the multi-agent pipeline that consumes the six canonical data functions in `docs/data-sources.md`, produces hourly trade decisions, and executes against Trading 212's paper account.

---

## 1. Goals & non-goals

**Goals**
- Hourly trading decisions during US market hours (≈ 7 ticks/day) using Google ADK.
- Four specialised analyst agents whose data sources never overlap.
- A single strategist that fuses analyst signals into target portfolio weights.
- Deterministic risk and execution layers — every safety guarantee is testable.
- Persistent memory (in-session + cross-session) bounded for cost; tiered for retention.
- Cloud-hosted from day one (Cloud Run Jobs + Cloud Scheduler).
- Apples-to-apples equity-curve comparison against SPY persisted every tick.
- Phase 1 paper-trades only; bot is gated from live trading by ≥30 days of beating both baselines on Sharpe + cumulative return.

**Non-goals (Phase 1)**
- Tool-using analyst agents that pick their own data slices (Phase 2 — see §13).
- Agent-driven stock picking (Phase 2 — interface preserved via `stock_picker.get_watchlist()`).
- Cross-session `MemoryService` recall (Phase 2).
- Custom web dashboard (Trading 212 app + nightly markdown report cover Phase 1).
- Live trading. Phase 1 is paper only.
- A self-critique `LoopAgent` around the strategist (Phase 2 if observation justifies it).

---

## 2. Top-level pipeline

One ADK `SequentialAgent` runs once per hour:

```
HourlyTick (SequentialAgent)
├── 1. AnalystPool (ParallelAgent)
│      ├── TechnicalAnalyst       (LlmAgent, before_callback fetches yfinance)
│      ├── FundamentalAnalyst     (LlmAgent, before_callback fetches edgartools filings)
│      ├── SentimentAnalyst       (LlmAgent, before_callback fetches finnhub news + social)
│      └── SmartMoneyAnalyst      (LlmAgent + has-signal gate; insider + politicians)
├── 2. Strategist                 (LlmAgent — Gemini Pro)
├── 3. RiskGate                   (BaseAgent — deterministic, no LLM)
├── 4. Executor                   (BaseAgent — calls Trading 212)
├── 5. MemoryWriter               (BaseAgent — rolling buffer + dedup)
└── 6. Snapshotter                (BaseAgent — equity curve persistence)
```

Each box owns a single, well-bounded responsibility. Boundaries are typed Pydantic structures in shared session state. The strategist sees nothing the analysts haven't typed; the executor sees nothing the risk gate hasn't approved.

---

## 3. Module layout

```
StockBot/
├── data/                                 # already specified in docs/data-sources.md
│   ├── providers/                        # 6 functions, tenacity, shared token bucket
│   └── models/                           # Pydantic models for raw data
│
├── agents/
│   ├── analysts/
│   │   ├── technical/    {agent.py, fetch.py, schema.py}
│   │   ├── fundamental/  {agent.py, fetch.py, schema.py}
│   │   ├── sentiment/    {agent.py, fetch.py, schema.py}
│   │   └── smart_money/  {agent.py, fetch.py, schema.py}
│   ├── strategist/       {agent.py, schema.py, prompts.py}
│   ├── risk_gate/        {agent.py}
│   ├── executor/         {agent.py}
│   ├── memory/           {writer.py, embeddings.py, nightly_ingest.py [Phase 2]}
│   └── snapshot/         {agent.py}
│
├── orchestrator/
│   ├── pipeline.py                       # builds the SequentialAgent
│   ├── tick.py                           # one-shot entrypoint for Cloud Run Jobs
│   └── stock_picker.py                   # static JSON → list[str]; agent-replaceable
│
├── broker/
│   ├── trading212.py                     # paper / live mode flag
│   ├── fake.py                           # deterministic test broker
│   └── portfolio.py                      # current holdings + cash
│
├── baselines/                            # already specified in docs/baselines.md
├── deploy/
│   ├── Dockerfile                        # python:3.11-slim, runs orchestrator/tick.py
│   ├── cloudbuild.yaml                   # build → push → deploy job
│   └── scheduler.yaml                    # Cloud Scheduler cron config
│
├── config/
│   └── watchlist.json                    # {"tickers": ["AAPL", ...]} — Phase 1 universe
│
├── scripts/
│   └── plot_equity.py                    # nightly bot vs SPY equity curve PNG
│
├── tests/
│   ├── analysts/                         # one file per analyst
│   ├── test_risk_gate.py                 # snapshot tests of every clamp path
│   ├── test_strategist.py                # stub signals → strategist
│   ├── test_pipeline_e2e.py              # mocked providers + LLM
│   └── replay/                           # historical-data backtest harness
│
└── docs/
    ├── superpowers/specs/                # this document
    └── performance/                      # nightly equity reports
```

---

## 4. Components

### 4.1 The four analysts

All four share the same shape:
1. `before_agent_callback` (deterministic Python) fetches data into `state.<analyst>_data`.
2. `LlmAgent` reads `{<analyst>_data}` via state-template interpolation and emits a typed signal via `output_schema`.
3. Output written to `state.<analyst>_signals`.

**Model selection:** Gemini Flash for analysts (cheap, fast, narrow reasoning); Gemini Pro for the strategist.

| Analyst | Data sources | Lib(s) | Signal density |
|---|---|---|---|
| Technical | OHLCV history (3mo daily) | `yfinance` | Dense (every ticker, every tick) |
| Fundamental | 10-K / 10-Q / 8-K filings (last 3) | `edgartools` | Dense (cached; filings rarely change) |
| Sentiment | Company news + aggregated social score | `finnhub-python` | Dense |
| Smart Money | Insider Form 4s + politician trades | `edgartools` (insiders) + `requests` (Quiver) | Sparse — early-return gate |

**Smart Money gate:** the `before_agent_callback` runs before the LLM. It checks every watchlist ticker for *either* a Form 4 ≥ $100k in the last 14 days *or* a Quiver politician disclosure in the last 30 days. If no ticker has either, the callback writes `state.smart_money_signals = []` and skips the LLM call entirely. Saves cost and gracefully degrades when the API is down.

**Dense-vs-sparse rule:**
- Dense analysts MUST emit one signal per watchlist ticker (`neutral` allowed; confidence still required). Enforced by an `after_agent_callback` validator that retries once on missing tickers.
- Sparse analyst (Smart Money) emits only for tickers with detected activity. Absence ≠ neutral; absence = no signal.

### 4.2 Strategist

Single `LlmAgent`, Gemini Pro. Reads:
- All four signal lists.
- `state.portfolio` — current holdings + cash from broker.
- `state.memory_buffer` — rolling 24-tick decision history.
- `state.day_digest` — compressed older history.
- `state.thesis` — current ≤500-char outlook (mutable working memory).
- `state.positions` — active position book (each with rationale, horizon, catalyst).

Emits `StrategistDecision` (Pydantic, schema-enforced):

```python
class StrategistDecision(BaseModel):
    target_weights: dict[str, float]                  # ticker → weight in [0, 1]
    decision_tag: str                                 # snake_case, e.g. "trim_aapl_on_weak_fundamentals"
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    new_positions: dict[str, PositionThesis] = {}     # required when opening
    close_reasons: dict[str, str] = {}                # required when closing
```

**Smart-money bias:** the strategist's instruction explicitly says: *"If `smart_money_signals` is non-empty AND `conviction='high'`, let it dominate the directional call for those tickers — weight 2-3× the dense signals."* Smart Money is a bias channel, not a co-equal vote.

**Exhaustive weights:** strategist must emit a weight for every watchlist ticker (including 0). Prevents silent omission.

**Position lifecycle contract** (`MIN_HELD_WEIGHT = 0.001`, the threshold below which a position is considered "not held"):
- Opening (weight rises from below `MIN_HELD_WEIGHT` to above): MUST add a `PositionThesis` to `new_positions`.
- Closing (weight drops from above `MIN_HELD_WEIGHT` to below): MUST add a `close_reason` to `close_reasons`.

Violation → risk gate rejects → one re-invocation with the error in the prompt → fail-loud and skip tick on second failure.

### 4.3 RiskGate (deterministic)

Pure Python. Algorithm in fixed order:

1. **Structural validation** — strategist emitted weights for every watchlist ticker (no missing, no extras). Reject if not.
2. **No shorts** — clamp negative weights to 0.
3. **Max position 20%** — clamp each weight ≤ 0.20.
4. **Cash floor 10%** — if Σ weights > 0.90, scale all proportionally.
5. **Max delta 1%/ticker** — clamp `|target - current|` per ticker to ≤ 0.01.
6. **Max turnover 30%** — if Σ |delta| > 0.30, scale all deltas proportionally.
7. **Position-lifecycle contracts** — verify `new_positions` / `close_reasons` accompany state changes.
8. **Translate weights → orders** — produce `Order` records (BUY/SELL, ticker, qty, est_price).

Every clamp emits a `ClampRecord(rule, ticker, before, after)` for telemetry. A strategist consistently clamped on `max_position` is a sign the prompt or cap needs tuning.

### 4.4 Executor

Pure Python. Calls broker via the `Broker` Protocol (Trading 212 paper / Trading 212 live / Fake for tests).

- Idempotent on `tick_id`: re-running the same tick returns existing `Execution`s.
- Sequential order submission. 5-10 orders/tick max — parallelism not worth the complexity.
- Market orders only in Phase 1.
- Single rejection logs and continues; doesn't fail the tick.
- Auth/connectivity failure → tick exits non-zero → page.
- On position close (size goes to 0), pops `PositionThesis` from `state.positions` and appends to durable `trade_log` table with `pnl_pct`, `holding_period_hours`, `catalyst_realised`, etc.

### 4.5 MemoryWriter

Three layers, all in `session.state`:

| Layer | Field | Bound | Mutation |
|---|---|---|---|
| Working | `thesis: str` | ≤ 500 chars | Strategist rewrites every tick |
| Rolling | `memory_buffer: list[BufferEntry]` | ≤ 24 entries | Append; oldest evicted to digest |
| Compressed | `day_digest: str` | ≤ 2000 chars | LLM-compressed when buffer evicts |

**Semantic dedup:** when a new entry's `decision_tag` matches any of the last 4 entries, embed the `reasoning_summary` and check cosine ≥ 0.85 against the matching entries. If yes, set `is_repeat=True`. The strategist next tick sees thrashing as data ("hold_aapl_low_conf appeared 7 of last 24 ticks") and can act accordingly.

Embedding model: Vertex AI `text-embedding-005`. Called only on tag-collision (cheap path: skip).

**Strategist's prompt projection** (≤ 1.2k tokens):
- `thesis` (≤500 chars).
- `day_digest` (≤2000 chars).
- Last 8 buffer entries: tag + 1-line reasoning + flags.
- Tag-frequency table for last 24 ticks (≥3 occurrences shown).

The full 24-entry buffer (and all evicted entries via the digest pipeline) is persisted in DB so per-analyst attribution analysis can join signals against subsequent returns. The full buffer is NOT injected into the prompt verbatim — only the projection above.

### 4.6 Snapshotter

Tiny `BaseAgent`. After every tick (including ticks that skipped trading):
- Read portfolio total value + holdings from broker.
- Fetch SPY's current close (yfinance).
- Compute bot return %, SPY return %, excess return % vs starting capital.
- Append `PortfolioSnapshot` row to durable `portfolio_snapshots` table.

This is the source of truth for the equity-curve plot.

### 4.7 Stock picker (Phase 1 — static)

```python
# orchestrator/stock_picker.py
def get_watchlist() -> list[str]:
    with open("config/watchlist.json") as f:
        return json.load(f)["tickers"]
```

Phase 2: replace body with an LlmAgent that picks tickers each morning. Same signature, same call sites — no other module changes.

---

## 5. State schema

`session.state` is the only thing agents share. Exhaustive list of keys:

```python
class TickState(BaseModel):
    # ── seeded at tick start ──────────────────────────────
    tick_id: str
    tickers: list[str]
    portfolio: Portfolio

    # ── written by analyst before_callbacks ───────────────
    technical_data: dict[str, StockStats]
    fundamental_data: dict[str, list[Filing]]
    sentiment_data: dict[str, SentimentBundle]
    smart_money_data: SmartMoneyBundle | None         # None when gate fired

    # ── written by analyst LLMs ──────────────────────────
    technical_signals: list[TechnicalSignal]          # exhaustive
    fundamental_signals: list[FundamentalSignal]     # exhaustive
    sentiment_signals: list[SentimentSignal]          # exhaustive
    smart_money_signals: list[SmartMoneySignal]       # sparse; [] when no activity

    # ── persistent across ticks (DB-backed session) ──────
    memory_buffer: list[BufferEntry]                  # rolling 24
    day_digest: str                                   # ≤2000 chars
    thesis: str                                       # ≤500 chars
    positions: dict[str, PositionThesis]              # active positions
    last_executed_tick_id: str | None                 # for idempotency

    # ── written by strategist ────────────────────────────
    strategist_decision: StrategistDecision

    # ── written by risk gate ─────────────────────────────
    final_orders: list[Order]
    risk_clamps_applied: list[ClampRecord]

    # ── written by executor ──────────────────────────────
    executions: list[Execution]
```

`*_data` fields are transient — discarded at tick end, not persisted. Only structured signals + decisions + executions + position book persist.

---

## 6. Pydantic models

```python
class AnalystSignal(BaseModel):
    ticker: str
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(max_length=3)        # bullets ≤80 chars

class TechnicalSignal(AnalystSignal): ...
class FundamentalSignal(AnalystSignal): ...
class SentimentSignal(AnalystSignal):
    top_headlines: list[str] = Field(max_length=2)
    social_score_delta: float                            # vs 7-day baseline

class SmartMoneySignal(BaseModel):
    ticker: str
    direction: Literal["bullish", "bearish"]             # neutral excluded — sparse
    conviction: Literal["low", "high"]
    insiders: list[str]
    politicians: list[str]
    total_dollar_value: float

class PositionThesis(BaseModel):
    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str
    rationale: str = Field(max_length=400)
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None
    stop_price: float | None
    catalyst: str | None = Field(max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(max_length=200)

class Order(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float                                       # fractional shares ok
    est_price: float                                      # for slippage budgeting

class Execution(BaseModel):
    order: Order
    status: Literal["filled", "rejected", "partial"]
    actual_price: float | None
    actual_quantity: float | None
    slippage_bps: float | None
    broker_order_id: str | None
    error: str | None

class ClampRecord(BaseModel):
    rule: Literal["max_position", "max_delta", "cash_floor", "max_turnover", "no_short"]
    ticker: str | None
    before: float
    after: float

class BufferEntry(BaseModel):
    timestamp: datetime
    decision_tag: str
    reasoning_summary: str = Field(max_length=120)
    smart_money_seen: bool
    is_repeat: bool = False
    executions_count: int
    embedding: list[float] | None = None                  # populated only on tag-collision

class TradeLogEntry(BaseModel):
    ticker: str
    opened_at: datetime
    closed_at: datetime
    opened_price: float
    closed_price: float
    pnl_dollar: float
    pnl_pct: float
    holding_period_hours: int
    horizon_intent: Literal["intraday", "swing", "long_term"]
    opened_tag: str
    closed_tag: str
    opened_rationale: str
    close_reason: str
    catalyst_realised: bool

class PortfolioSnapshot(BaseModel):
    tick_id: str
    bot_total_value: float
    bot_cash: float
    bot_positions_value: float
    bot_position_count: int
    spy_price: float
    spy_value_if_held: float
    bot_return_pct: float
    spy_return_pct: float
    excess_return_pct: float
    holdings_breakdown: dict[str, float]
```

---

## 7. Constraints summary

Hard rules (deterministic, enforced in RiskGate):

| Rule | Value |
|---|---|
| Max single position | 20% of portfolio |
| Cash floor | 10% always reserved |
| Shorting | Disabled (Phase 1) |
| Max delta per ticker per hour | 1% |
| Max total turnover per hour | 30% (Σ \|Δweight\|) |

Strategist contract (rejected if violated):

| Rule | Effect |
|---|---|
| Exhaustive weights (one per watchlist ticker) | Re-invoke with `you missed: [...]` hint |
| `new_positions` for every weight 0 → >MIN | Re-invoke with error |
| `close_reasons` for every weight >MIN → 0 | Re-invoke with error |
| Schema validation (Pydantic) | Re-invoke; second failure → skip tick |

---

## 8. Memory & retention tiering

```
HOT     Cloud SQL Postgres                last 30 days, fully queryable
        decisions, executions, attribution_signals, position_history,
        trade_log, portfolio_snapshots
        ~2 MB at 15 tickers, ~10 MB at 100 tickers — both trivial
        (~9 KB attribution + ~1 KB other per tick × 154 ticks/30d)

   ↓   nightly archive job

WARM    Cloud SQL "archive" partition     30-180 days, summary only
        decisions_summary, attribution_aggregates (per-ticker per-analyst monthly)

   ↓   monthly archive job

COLD    GCS Parquet                       >180 days
        gs://<project>-stockbot-archive/year=YYYY/month=MM/decisions.parquet
        $0.02/GB/month — pennies/year forever
```

Strategist (and Phase 2 `load_memory` tool) only ever queries HOT and WARM. Cold tier is for ad-hoc analysis and Phase 3 model training. The pipeline never reads from cold.

---

## 9. Cloud deployment

```
Cloud Scheduler (cron: "30 9-15 * * 1-5" America/New_York)
   │ triggers
   ▼
Cloud Run Job (one container, runs orchestrator/tick.py once)
   ├─ Image: Artifact Registry; built by Cloud Build on push to main
   ├─ Secrets from Secret Manager:
   │     TRADING212_API_KEY, FINNHUB_API_KEY, QUIVER_QUANT_API_KEY
   ├─ env: GOOGLE_CLOUD_PROJECT, GOOGLE_GENAI_USE_VERTEXAI=1, BROKER_MODE=paper
   └─ Service account: roles/aiplatform.user, roles/cloudsql.client,
                       roles/secretmanager.secretAccessor, roles/storage.objectUser
   │
   │ reads/writes
   ▼
Cloud SQL Postgres
   ├─ ADK DatabaseSessionService — one persistent session, indefinite
   ├─ session.state holds memory_buffer, day_digest, thesis, positions
   └─ append-only tables: decisions, executions, trade_log, portfolio_snapshots,
                          attribution_signals

Vertex AI Gemini Flash (analysts) + Gemini Pro (strategist)
   No separate API key — Cloud Run Job's service account authenticates.

Cloud Storage gs://<project>-stockbot-archive/
   Cold-tier parquet, written by monthly archive job.

Cloud Logging captures structured logs from ADK; nightly digest emails
performance summary + plot_equity.py PNG to operator.
```

`tick.py` constraints for Cloud Run Jobs:
- Run once and exit (no servers, no event loops).
- Idempotent (Cloud Scheduler retries on failure; replaying the same tick is safe).

Local dev path identical: `python -m orchestrator.tick --mode paper` against a SQLite session DB. No environment-branching code.

---

## 10. Failure handling

| Layer | Failure mode | Strategy | Outcome |
|---|---|---|---|
| Data | Provider 5xx (transient) | tenacity retry 3× + token bucket | tick proceeds |
| Data | Provider down (persistent) | analyst sees empty data | dense analyst → all neutral; sparse → empty |
| Data | All providers down | fetch sentinel raises | skip tick, log, next tick fresh |
| Analyst LLM | 5xx / timeout | retry once, then degrade | missing analyst's signals = neutral for all |
| Analyst LLM | Schema validation fail | after_callback retry with hint | fail-loud after 2; skip analyst |
| Strategist | Schema fail / contract violation | retry once with error in prompt | fail-loud after 2; SKIP TICK ENTIRELY |
| Risk gate | Bug in clamp logic | unit tests catch in CI | never deploys |
| Executor | Single order rejected | log Execution.rejected, continue | other orders proceed |
| Executor | Broker auth failed | tick exits non-zero | page |
| Executor | Broker timeout | order assumed pending; next tick reconciles | idempotent diff handles it |
| Memory | Embedding API fail | skip dedup this tick | `is_repeat=False` (acceptable false negative) |
| Memory | Cloud SQL transient | retry transaction 3× | proceed |
| Memory | Cloud SQL down | tick fails | page |

**Principles:**
1. Degrade rather than crash on data gaps — three working analysts > zero.
2. Skip the tick rather than execute a malformed decision — a bad strategist output is far worse than an idle hour.
3. Idempotency at every layer — retried Cloud Run Jobs are safe.
4. Page only on auth/db/broker. Provider 429s are noise; auth-401s are signals.

---

## 11. Alerting

Cloud Logging → Cloud Monitoring:

| Alert | Threshold | Severity |
|---|---|---|
| Cloud Run Job exit ≠ 0 | Any | Email |
| Broker auth failed | 1 occurrence | Email |
| Cloud SQL connection failed | 3/hour | Email |
| Strategist contract violation | >2/day | Email digest |
| Analyst schema retry rate | >20%/24h | Email digest |
| Tick took >5 minutes | Any | Email |
| Provider 429s | >10/hour any provider | Email digest |

Phase 1: email is sufficient (hourly trading cadence).
Phase 2: PagerDuty / Slack as the bot accumulates real value at risk.

---

## 12. Testing strategy

| Tier | Scope | Tooling | Cost / cadence |
|---|---|---|---|
| 1. Unit | Pure functions (RiskGate clamps, MemoryWriter eviction + dedup, provider parsers) | pytest, parametrised | Free / every PR |
| 2. Per-analyst | Snapshotted data → real LLM → schema-conformant signal | pytest + InMemoryRunner | ~$0.01 / merge to main |
| 3. Pipeline e2e | Full pipeline with FakeBroker + canned LLM responses | pytest-asyncio | Free / every PR |
| 4. Replay backtests | 30 days historical data through full pipeline; vs SPY + MLP baselines | `baselines/evaluate.py` | Real LLM calls / weekly + on prompt changes |
| 5. Live paper | Cloud Run Job vs Trading 212 demo | The actual deployment | Phase 1 entirety |

**Live → live trading gate:** Tier 5 must beat both baselines on Sharpe AND cumulative return for ≥30 consecutive days before any live capital is enabled (per `docs/baselines.md`).

**Explicitly NOT tested:**
- Raw LLM output quality — non-deterministic, fragile to model updates. We test schema contracts and outcomes (replay returns), not "did the LLM say the right words."
- Trading 212 broker correctness — its demo is the SUT for the broker layer.
- Prompt stability across model versions — model is pinned (e.g. `gemini-2.0-flash-001`); changes are intentional and Tier 4 is the regression check.

---

## 13. Phase 2 roadmap (out of Phase 1 scope)

1. **`load_memory` tool for the strategist.** Cross-session semantic recall via `VertexAiMemoryBankService`. Nightly ingest job. Strategist gets a sparingly-used `FunctionTool`.
2. **Tool-using analysts.** Each analyst becomes a `SequentialAgent(fetcher_with_tools, structurer_with_output_schema)`. Same module path, same Signal schema, same call sites — drop-in.
3. **Agent-driven stock picker.** Replace `stock_picker.get_watchlist()` body with an LLM that picks the daily watchlist. Same signature.
4. **Strategist self-critique LoopAgent.** Wrap strategist + critic in `LoopAgent(max_iterations=2)` only if Phase 1 observation shows incoherent decisions the deterministic risk gate misses.
5. **Static HTML dashboard.** Nightly-generated Plotly charts to a public GCS bucket — only when there's ≥30 days of curve to look at.
6. **PagerDuty / Slack alerting.** When real capital is at risk.
7. **Live trading.** Gated by §12 Tier 5.

---

## 14. Decisions log (for traceability)

| # | Decision | Rationale |
|---|---|---|
| 1 | Hourly ticks (~7/day market hours) | Fits Finnhub 60/min budget; max signal/cost density |
| 2 | Static watchlist via `stock_picker.get_watchlist()` | Decouple "what to trade" from "how to trade"; agent-replaceable later |
| 3 | Strategist emits target weights for full watchlist | Single contract; executor mechanically diffs |
| 4 | Four analysts: Technical, Fundamental, Sentiment, Smart Money | Specialisation along data-source boundaries; minimal correlation |
| 5 | Smart Money is sparse + bias channel | Insider/politician signals are rare but strong; treating as bias preserves semantics |
| 6 | `edgartools` instead of `sec-api` | No API key, no 100/day cap, free indefinitely |
| 7 | Per-analyst data via `before_agent_callback` (not tools) | Phase 1 has no fetch decision worth making; preserves `output_schema` for typed signals |
| 8 | Dense analysts exhaustive; sparse analyst sparse | Attribution analysis requires per-ticker rows; sparse analyst's absence is meaningful |
| 9 | Risk gate is deterministic; never an LLM | Safety boundary must be testable; constraints are math, not judgement |
| 10 | Max trade delta 1% per ticker per hour | Conservative pacing; full position takes ~3 weeks → forces long-term planning |
| 11 | Max position 20%; cash floor 10%; max turnover 30%; no shorts | Phase 1 defaults; tighten/loosen based on observation |
| 12 | DB-backed persistent session | Cloud Run Jobs come and go; session row in Postgres is the durable home |
| 13 | Memory: thesis (500ch) + 24-entry buffer + 2k-char digest | Bounded prompt budget; full buffer in DB for attribution |
| 14 | Semantic dedup: tag-match → embedding cosine ≥0.85 | Cheap path skips embedding most ticks |
| 15 | First-class `PositionThesis` per active position | Bot can never silently hold a position it can't justify |
| 16 | Closed positions → `trade_log` with `catalyst_realised` flag | Long-term evaluation: did the strategist's catalyst reasoning hold up? |
| 17 | Per-tick `PortfolioSnapshot` with bot + SPY values | Equity curve persisted; clean SPY comparison without retroactive reconstruction |
| 18 | Hot / Warm / Cold storage tiering | Unbounded retention at bounded cost (~55 MB at 100 tickers × 6 months) |
| 19 | Cloud Run Jobs + Cloud Scheduler (not Vertex AI Agent Engine) | Right tool for hourly batch; cheaper than always-warm service |
| 20 | Gemini Flash for analysts, Pro for strategist | Cost: ~5× cheaper analysts; Pro reserved for the heaviest reasoning |
| 21 | Live trading gated by 30 days beating both baselines | `docs/baselines.md` pass/fail line — no exceptions |

---

## 15. Open questions deferred to implementation plan

These are NOT design questions — they're tactical choices that the writing-plans step will work through:
- Specific Cloud SQL instance tier (probably `db-f1-micro` for Phase 1, ~$10/month).
- Initial watchlist contents (`config/watchlist.json`).
- Token bucket rates per provider (start: Finnhub 50/min to leave 10/min headroom).
- Embedding cache vs re-compute on tag-match.
- Exact Gemini model versions to pin.
- Cost budgets per tick / per day; alert thresholds when exceeded.

---

**End of design document.** Ready for implementation plan via `superpowers:writing-plans`.
