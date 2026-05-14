# Backtest Harness — Design Spec

## Purpose

Build an end-to-end backtester that drives the **existing live pipeline** unchanged against historical 30-day windows, producing per-tick traces and per-trade decision snapshots that double as the seed corpus for future knowledge-base / RAG work.

The harness is the on-ramp for two distinct goals running in parallel:

1. **P&L validation** — measure bot-vs-SPY performance over historical regimes (banking stress, fed pivots, election cycles, etc.) so we can iterate the analysts / strategist with confidence before flipping the paper-to-live gate.
2. **Decision-corpus generation** — every executed buy/sell is captured as a self-contained JSON case study, ready to be ingested by a future retrieval substrate that lets the bot learn from its own history.

The defining constraint: **live and backtest run the same code path**. There is no separate "backtest pipeline" that could drift from live behaviour. The backtester is a thin driver that loops over historical tick timestamps, swaps the active data upstream from live APIs to a local cache, and calls the unchanged `HourlyTick SequentialAgent` pipeline.

## Non-goals

The following are explicitly **out of scope** for this spec; each is a separate concern with its own plan or backlog entry:

- **LLM determinism / response caching** — handled in a parallel plan the user is currently authoring.
- **Historical social-sentiment ingestion** — the social analyst will receive `None` in backtest. Strategist already tolerates this. A separate backlog entry covers building a Pushshift-successor scraper.
- **Multi-window or sliding-grid backtest scheduling** — v1 ships with a single configured window. Adding more windows is a config edit, not a code change.
- **Resumability of interrupted runs** — v1 treats interruption as terminal; user starts a fresh `run_id`. v2 nice-to-have.
- **A live-trading mode change.** Adopting the new 2-ticks/day cadence in production is an intentional consequence of the "same code path" constraint, but the cron change should be a separate, reviewable deployment.

## Architecture overview

Three architectural constraints drive every decision below:

1. **Same code path, live and backtest.** The orchestrator, analyst pool, evidence writer, strategist, strategist-decision writer, risk gate, executor, portfolio-snapshot writer, and trace writer all run unchanged. Backtest mode differs from live mode in exactly two places: (a) which upstream each provider domain is wired to (`yfinance` / `finnhub` / `edgar` / etc. in live, `cache` in backtest), and (b) what value `as_of` carries when fetch callbacks fire (`datetime.utcnow()` in live, the historical tick timestamp in backtest).
2. **Provider shell is the seam.** The existing `@register / dispatch` shell already supports per-domain upstream selection. Cache providers register themselves as a new upstream named `cache`. No analyst code changes; analysts call the same `provider_shell.dispatch(domain, ticker, as_of=...)` they always did.
3. **`as_of` becomes an explicit kwarg on every fetch.** Today, fetch callbacks implicitly use `datetime.utcnow()`. The migration: every `fetch(...)` signature gains `as_of: datetime = datetime.utcnow()`. Live behaviour is unchanged by the default; backtest passes the historical timestamp. This loud, explicit contract beats any clock-bus or session-state-monkey-patch alternative.
4. **Wall-clock leakage outside the fetch path must also be closed.** Determinism only holds if every code path whose output depends on "now" honours `as_of`, not just network fetches. Time-delta feature extractors, evidence `recorded_at` stamps, and tick-identity derivation all read wall-clock in the live pipeline today; each must accept `as_of` (or read it from session state) for backtest replay to be reproducible. Concrete enumeration lives in the plan.

The harness adds a thin driver layer on top of the live pipeline:

```
                ┌─────────────────────────────────────────────────────┐
                │  Live pipeline (unchanged)                          │
                │   HourlyTick SequentialAgent                        │
                │   ├─ AnalystPool (Technical, Fundamental, News,     │
                │   │    Social, SmartMoney)                          │
                │   ├─ EvidenceWriter                                 │
                │   ├─ Strategist                                     │
                │   ├─ StrategistDecisionWriter                       │
                │   ├─ RiskGate                                       │
                │   ├─ Executor   ──► DecisionLogger (per Fill)       │
                │   └─ PortfolioSnapshotWriter                        │
                └─────────────────────────────────────────────────────┘
                                       ▲
                                       │ pipeline.run_once(state)
                                       │
   ┌───────────────────────────────────┴─────────────────────────────┐
   │   Backtest Driver                                               │
   │     for each tick (open + close, NYSE business days):           │
   │       set as_of = tick_time                                     │
   │       state["watchlist"]  = filtered watchlist                  │
   │       broker              = FakeBroker                          │
   │       provider_shell.set_active_upstream({...: "cache"})        │
   │       pipeline.run_once(state)                                  │
   │       trace_writer.flush() ──► traces/<as_of>.json              │
   └─────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                   ┌──────────────────────────────────┐
                   │  Cache Providers (upstream=cache)│
                   │   market_cache, filings_cache,   │
                   │   news_cache, insider_cache, ... │
                   └──────────────────────────────────┘
                                       │
                                       ▼
                       ┌────────────────────────────┐
                       │   CachedDataStore           │
                       │   backtests/cache/store.db  │
                       │   (point-in-time filtered) │
                       └────────────────────────────┘
                                       ▲
                                       │ one-time fill
                                       │
                       ┌────────────────────────────┐
                       │  Fetcher                   │
                       │  scripts/backtest_fetch.py │
                       │  → live providers (rate-   │
                       │     limit-respecting)      │
                       └────────────────────────────┘
```

### Per-run database, from scratch

Each run materialises its own SQLite at `<runs_root>/<run_id>/db.sqlite` (sibling of `manifest.json`, `traces/`, `decisions/`). On startup the runner calls `create_all(engine)` against the same `Base` metadata the live writers use; from that point the writer surface (`EvidenceWriter`, `StrategistDecisionWriter`, `PortfolioSnapshotWriter`, buffer / trade-log writers) is byte-identical to live.

Implications:

- **No carry-over.** Every run begins with empty buffers, no open positions, no prior ticker stances. The driver's initial-state builder is the only seeder. A backtest simulates a live run *from scratch*; it never warm-starts from another run's artefacts.
- **Deterministic `tick_id` is safe.** Because the DB is fresh per run, `tick_id` can be derived deterministically from `(run_id, tick.as_of, tick.phase)` without risk of `UniqueConstraint` collision. Reruns of the same window with a fresh `run_id` are independently comparable.
- **Live DB is never touched.** Backtest pipelines never open `data/stockbot.db`. Cache providers read `backtests/cache/store.db`; writers write the per-run DB. There is no path by which a backtest can mutate live state.
- **Schema evolution is free.** Because every run starts with `create_all`, schema changes to the live persistence layer require no migration for backtest. (Persistence-layer refresh is tracked separately in the backlog.)

## Module layout

```
src/
├── data/providers/             # (unchanged) live providers
└── backtest/                   # NEW — all backtest-specific code, isolated subtree
    ├── __init__.py
    ├── windows.py              # era-window config loader; reads config/backtest_windows.json
    ├── schedule.py             # tick-schedule generator (open + close, NYSE business days)
    ├── clock.py                # historical-clock helper exposed to fetch callbacks
    ├── decision_logger.py      # post-Fill observer; writes per-trade decision snapshots
    ├── cache/
    │   ├── schema.py           # SQLite DDL for the golden store
    │   ├── store.py            # CachedDataStore — read/write keyed by (ticker, as_of, domain)
    │   └── fetcher.py          # one-time cache fill from live providers
    ├── providers/              # cache-reading providers — register as upstream="cache"
    │   ├── market_cache.py
    │   ├── filings_cache.py
    │   ├── news_cache.py
    │   ├── insider_cache.py
    │   ├── politician_cache.py
    │   └── holders_cache.py
    ├── driver.py               # tick loop: inject as_of → call pipeline → flush trace
    ├── runner.py               # one full run: window + watchlist → driver → reporting
    └── reporting.py            # equity_curve.png + metrics.md + forward-return backfill

scripts/
├── backtest_fetch.py           # PYTHONPATH=src python -m scripts.backtest_fetch --window <key>
├── backtest_run.py             # PYTHONPATH=src python -m scripts.backtest_run --window <key>
└── backtest_report.py          # PYTHONPATH=src python -m scripts.backtest_report --run-id <id>

config/
├── backtest_windows.json       # era keys → {start, end, notes}
└── backtest_settings.json      # cache path, output root, ticks per day, default lookback periods
```

**Output tree** (gitignored; local-only artefacts, same posture as `graphify-out/`):

```
backtests/
├── cache/store.sqlite          # one shared golden store across all runs
└── runs/<run-id>/              # run-id = <window-key>-<git-sha7>
    ├── manifest.json           # window, watchlist, git sha, config snapshot, skipped tickers,
    │                           # failed ticks, started_at, finished_at, status
    ├── db.sqlite               # full live-schema DB scoped to this run (reuses make_engine)
    ├── traces/<tick-ts>.json   # one TraceWriter dump per tick — bedrock debug log
    ├── decisions/              # one file per executed Fill — RAG-seed corpus
    │   └── <tick-ts>__<TICKER>__<side>.json
    └── report/
        ├── equity_curve.png
        ├── metrics.md          # Sharpe, total return, max DD, vs-SPY delta, win rate
        └── (decisions/ is the corpus; no flattened JSONL needed)
```

## Data flow — one tick, end to end

**Run setup (once per backtest)**

1. `Runner.run(window_key, watchlist)` resolves the window key to a `(start, end)` date pair from `config/backtest_windows.json`.
2. Materialises `backtests/runs/<run-id>/` with an empty `manifest.json`, a fresh `db.sqlite` (via `make_engine` + `create_all`), an empty `traces/` and `decisions/`.
3. Builds the live pipeline via the existing `build_pipeline()` — **no changes to that function**.
4. Calls `provider_shell.set_active_upstream({"market": "cache", "filings": "cache", "news": "cache", "insider": "cache", "politicians": "cache", "holders": "cache", "social": None})`. The `None` for social tells the analyst pool to skip social-sentiment fetch entirely; the strategist already tolerates `social=None` evidence.
5. Wires the broker to `FakeBroker` (already deterministic, already used throughout the test suite).
6. Configures `TraceWriter.output_dir = backtests/runs/<run-id>/traces/`.
7. Configures `DecisionLogger.output_dir = backtests/runs/<run-id>/decisions/`.
8. Pre-flight watchlist check: drop any ticker that has zero OHLCV bars in the window range from `state["watchlist"]` and record the drops in `manifest.skipped_tickers`.
9. Generates the tick schedule from `schedule.py`: a list of `(date, "open" | "close")` pairs over NYSE business days in the window (using `pandas_market_calendars`).

**Per-tick loop (in `driver.py`)**

For each scheduled tick:

1. Compute `as_of` from the (date, phase) pair: `09:30 ET` for `open`, `16:00 ET` for `close`. Store on the session state as a fallback (`state["as_of"]`).
2. `pipeline.run_once(state)` executes. Internally:
   - Each analyst's `fetch_callback` calls `provider_shell.dispatch(domain, ticker, as_of=as_of)`.
   - Dispatch routes to the registered `cache` upstream for that domain.
   - The cache provider asks `CachedDataStore` for rows where the relevant time column is `<= as_of`. For OHLCV bars specifically, the provider also returns the day's `open` price for an open tick and the day's `close` price for a close tick — both fields are in the same daily bar row, so this needs no separate intraday data.
   - Analysts run, emit `AnalystEvidence` / `TickerEvidence` / verdicts.
   - `EvidenceWriter` persists to the run's `db.sqlite`.
   - Strategist emits per-ticker `TickerStance`. `StrategistDecisionWriter` persists.
   - `RiskGate` clamps. `Executor` submits Orders to `FakeBroker`, records `Fill` rows.
   - `DecisionLogger` (registered as an after-run hook on the Executor) fires **once per Fill**, writes one file under `decisions/`.
   - `PortfolioSnapshotWriter` records the post-tick snapshot to `db.sqlite`.
3. `TraceWriter.flush()` writes the full tick trace to `traces/<as_of>.json`.
4. Advance to next tick.

**End of window (in `reporting.py`)**

1. `compute_equity_curve()` reads `PortfolioSnapshotRow` from the run's `db.sqlite` and renders `equity_curve.png` against the SPY baseline (existing `baselines/spy.py`).
2. Compute and write `metrics.md`: total return, annualised Sharpe ratio (252-day basis on the daily series), max drawdown, vs-SPY delta, win rate, total Fill count.
3. **Forward-return backfill**: walk `decisions/*.json`. For each decision, look up the +1d / +5d / +20d returns from the cache (using the entry price and the date offsets) and patch `forward_returns` into the JSON file in place. This is the supervision signal a future RAG retriever or self-improvement loop will want.
4. Update `manifest.json` with `finished_at` and `status`.

## Component details

### Cache schema (`src/backtest/cache/schema.py`)

One SQLite file at `backtests/cache/store.sqlite`. Tables mirror the live Pydantic models from `src/data/models/`. Every time-bearing column is indexed for fast point-in-time reads.

| Table | Columns | Read filter |
|---|---|---|
| `ohlcv_bars` | `ticker, date, open, high, low, close, volume, adj_close` (PK: ticker, date) | `WHERE ticker=? AND date<=?` |
| `market_meta` | `ticker, as_of_date, market_cap, trailing_pe, forward_pe, beta, dividend_yield, ma_50, ma_200, sector, long_name` (PK: ticker, as_of_date) | `WHERE ticker=? AND as_of_date<=?` (latest row before tick) |
| `filings` | `ticker, accession_no, form_type, filed_at, title, url, risk_factors_excerpt, mda_excerpt` (PK: accession_no) | `WHERE ticker=? AND filed_at<=?` |
| `news_articles` | `ticker, url, headline, summary, source, published_at, sentiment` (PK: ticker+url) | `WHERE ticker=? AND published_at<=?` |
| `insider_trades` | `ticker, accession_no, row_idx, insider_name, insider_title, side, shares, price_per_share, transaction_date, filed_at, form_type` (PK: accession_no+row_idx) | `WHERE ticker=? AND filed_at<=?` |
| `politician_trades` | `ticker, politician, chamber, party, side, transaction_date, disclosure_date, amount_min_usd, amount_max_usd` (PK: synthetic hash) | `WHERE ticker=? AND COALESCE(disclosure_date, transaction_date)<=?` |
| `notable_holders` | `ticker, accession_no, holder, form_type, intent, is_amendment, filed_at, url` (PK: accession_no) | `WHERE ticker=? AND filed_at<=?` |
| `cache_runs` | `run_id, started_at, finished_at, window_key, ticker, domain, source_provider, rows_written, status, error` | (audit log of fetch runs) |
| `meta` | `schema_version, created_at` | (single-row table) |

**Critical correctness rule:** point-in-time filters always use the *filing / publication* timestamp (`filed_at`, `published_at`), never the *transaction* date, because a Form 4 trade can predate its filing by days. Using `transaction_date` would leak future information into the analysts.

### Cache store (`src/backtest/cache/store.py`)

```python
class CachedDataStore:
    """Read/write façade over the golden SQLite store.

    Writers are called by the fetcher only. Readers honour the point-in-time
    filter — they will never return a row whose canonical timestamp is after
    the supplied ``as_of``.
    """

    def __init__(self, path: Path) -> None: ...

    # Writers
    def write_ohlcv(self, ticker: str, bars: list[OHLCBar]) -> None: ...
    def write_market_meta(self, ticker: str, snapshot: StockStats, as_of_date: date) -> None: ...
    def write_filings(self, ticker: str, filings: list[Filing]) -> None: ...
    def write_news(self, ticker: str, articles: list[NewsArticle]) -> None: ...
    def write_insider_trades(self, ticker: str, trades: list[InsiderTrade]) -> None: ...
    def write_politician_trades(self, ticker: str, trades: list[PoliticianTrade]) -> None: ...
    def write_notable_holders(self, ticker: str, holders: list[NotableHolder]) -> None: ...

    # Readers — every reader returns the same Pydantic model the live provider would
    def read_ohlcv(self, ticker: str, start: date, end: date) -> list[OHLCBar]: ...
    def read_market_meta(self, ticker: str, as_of: datetime) -> StockStats | None: ...
    def read_filings(self, ticker: str, as_of: datetime, lookback_days: int = 365) -> list[Filing]: ...
    def read_news(self, ticker: str, as_of: datetime, lookback_days: int = 30) -> list[NewsArticle]: ...
    def read_insider_trades(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[InsiderTrade]: ...
    def read_politician_trades(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[PoliticianTrade]: ...
    def read_notable_holders(self, ticker: str, as_of: datetime, lookback_days: int = 365) -> list[NotableHolder]: ...
```

Readers return the same Pydantic models the live providers return — analysts cannot distinguish a cache read from a live fetch.

### Fetcher (`src/backtest/cache/fetcher.py`, CLI: `scripts/backtest_fetch.py`)

Invoked once per (window, watchlist) combination. Steps:

1. Resolve the window key to a date range. Resolve the watchlist (default: `config/watchlist.json`).
2. For each domain × ticker, call the live provider with the window's `start` / `end`, respecting the same `AsyncRateLimiter` infrastructure used in production fetches.
3. **Idempotent.** Re-running the fetcher skips (ticker, domain, window) combinations already marked `status='ok'` in `cache_runs`. Failed runs (`status='error'`) are retried; partial runs (no `cache_runs` row at all) are restarted.
4. Logs progress to console; writes one `cache_runs` row per (window, ticker, domain).

**Rate-limit budget** for a 30-day window × 20 tickers, all free tier:

- yfinance OHLCV + meta: 20 calls × ~1s = ~20s.
- EDGAR filings (10-K, 10-Q, 8-K, SC 13D/G, Form 4): 5 form types × 20 tickers @ ≤10 req/s = ~10s.
- Finnhub news (60/min cap): 20 calls = ~20s.
- FMP politician trades (250/day budget): 20 calls = trivial.

Total: minutes per window, not hours. Multi-window expansion is comfortably overnight.

### Cache providers (`src/backtest/providers/*.py`)

One file per domain. Each registers itself with the existing provider shell as the `cache` upstream:

```python
# src/backtest/providers/market_cache.py

from data.providers.shell import register
from backtest.cache.store import CachedDataStore
from datetime import datetime

# Module-level singleton; runner sets the store path at boot.
_store: CachedDataStore | None = None


def configure(store: CachedDataStore) -> None:
    """Wire the cache store at run start. Called once by the runner."""
    global _store
    _store = store


@register(domain="market", name="cache")
async def fetch(ticker: str, *, as_of: datetime, **kwargs) -> StockStats:
    """Return market-data snapshot as of ``as_of`` from the local cache."""
    assert _store is not None, "Cache store not configured. Call configure() first."
    return _store.read_market_meta(ticker, as_of=as_of)
```

Live mode does `set_active_upstream({"market": "yfinance", ...})`; backtest mode does `set_active_upstream({"market": "cache", ...})`. No analyst code changes.

### Driver / Runner (`src/backtest/driver.py`, `src/backtest/runner.py`)

`Runner.run(window_key: str, watchlist: list[str] | None = None) -> RunResult`:

1. Materialise the run directory and DB.
2. Build the live pipeline via the existing `build_pipeline()`.
3. Configure cache providers (`market_cache.configure(store)`, etc.) and `set_active_upstream({...: "cache", "social": None})`.
4. Pre-flight watchlist check; record `skipped_tickers`.
5. Generate the tick schedule from `schedule.py`.
6. Hand off to `Driver.run(state, schedule)`.

`Driver.run(state, schedule)`:

For each `(tick_date, phase)` in `schedule`:

1. Compute `as_of`; write into `state["as_of"]` as a defensive fallback for any consumer that does not have it on its fetch signature.
2. `pipeline.run_once(state)` inside a `try / except` (see Error Handling below).
3. `TraceWriter.flush()` to `traces/<as_of>.json`.

Decision-snapshot files are written inside `pipeline.run_once` by the `DecisionLogger` post-Fill hook — the driver does not do it directly.

### Era window config (`config/backtest_windows.json`)

```json
{
  "svb-stress-2023-03": {
    "start": "2023-03-06",
    "end":   "2023-04-07",
    "notes": "SVB / Signature collapse, regional banking stress. First v1 window."
  }
}
```

v1 ships with this one window. The CLI accepts a window key (`--window svb-stress-2023-03`) or an ad-hoc start date (`--start 2023-08-01 --days 30`). Additional eras are config edits, no code change.

### Decision logger (`src/backtest/decision_logger.py`)

A thin observer registered as an after-run callback on the Executor agent. **Lives outside the backtest-only path** — it is wired up in both live and backtest mode so the RAG corpus grows continuously once we deploy.

For each `Fill` recorded by the Executor:

1. Read the relevant context from `state`: per-analyst evidence + verdicts + rationale, `TickerEvidence` aggregate, `TickerStance` decision, `ClampRecord` list, the `Order` and `Fill` themselves.
2. Assemble a self-contained decision snapshot:

```json
{
  "decision_id": "2023-03-13T13-30-00Z__SIVB__sell",
  "tick":   { "as_of": "...", "phase": "open|close", "window_key": "svb-stress-2023-03" },
  "ticker": "SIVB",
  "side":   "sell",
  "execution":      { "order_qty": -120, "fill_price": 42.31, "fill_qty": -120, "status": "filled" },
  "analyst_inputs": { "technical": {...}, "fundamental": {...}, "news": {...}, "smart_money": {...}, "social": null },
  "analyst_outputs":{ "technical": { "verdict": {...}, "rationale": "...", "key_factors": [...] }, ... },
  "strategist_view":     { "ticker_evidence": {...}, "held_view_at_decision": {...} },
  "strategist_decision": { "stance": {...}, "close_reason": "...", "reasoning_excerpt": "..." },
  "risk_gate":           { "clamps": [...] },
  "forward_returns":     null
}
```

3. Write to `<output_dir>/<as_of>__<TICKER>__<side>.json`.

`forward_returns` is `null` until `reporting.py` back-fills it at end-of-window.

**This schema is an explicit iteration surface.** First-pass aims for "captures the obvious"; backtest runs will surface what diagnostics we wish we had. Expected near-term extensions: strategist's full prompt+response, raw LLM token usage, post-trade portfolio delta, intermediate ClampRecord reasoning.

## Error handling

**Cache miss / missing data (expected, common).** Providers return empty lists / `None`, never raise. Analysts soft-fail to `is_no_data=True` verdicts — the existing behaviour when live providers return empty. Logged in the trace; no special-case code in the driver.

**Ticker has no OHLCV in window.** Caught at runner pre-flight; dropped from the watchlist for the run; recorded in `manifest.skipped_tickers`.

**Mid-tick failure** (analyst throws, Pydantic validation fails, LLM times out). Driver wraps `pipeline.run_once` in `try / except`. On failure: append `{ as_of, exception_type, message }` to `manifest.failed_ticks` and continue to next tick. Threshold: if `failed_ticks / total_ticks > 0.10`, runner aborts with non-zero exit and `manifest.status = "aborted"`. Below threshold, run completes with `manifest.status = "completed_with_failures"`.

**Cache corruption / schema mismatch.** Cache-store readers validate Pydantic models on read. A row that fails validation is logged and skipped (same effect as missing data). Repeated validation failures for one (ticker, domain) raise `CacheCorruptedError` → runner aborts. The fetcher writes a schema-version row to `meta`; readers refuse to operate against a mismatched schema version.

**Run interrupted mid-flight** (Ctrl-C, OOM). Runner registers a SIGINT/SIGTERM handler that writes `manifest.status = "interrupted"` and the last-completed tick. Re-invoking with the same `run_id` is **not** auto-resumed in v1; user starts a fresh `run_id`. Resumability is a v2 nice-to-have.

**LLM determinism / caching.** Out of scope — handled in the parallel plan. Driver makes no special accommodation; whatever the live pipeline does is what the backtest does.

## Testing strategy

**Tier 1 — Unit (deterministic, no I/O, no LLM):**

- `tests/unit/backtest/test_cache_store.py` — write known rows, read with various `as_of` values, assert point-in-time filter excludes future rows. Most important correctness property: no lookahead bias.
- `tests/unit/backtest/test_schedule.py` — schedule generator produces correct (date, phase) pairs for a known business-day range; handles weekends and NYSE holidays via `pandas_market_calendars`.
- `tests/unit/backtest/test_windows.py` — Pydantic config loader rejects malformed entries, accepts canonical fixture.
- `tests/unit/backtest/test_cache_providers.py` — each cache provider, given a stubbed `CachedDataStore`, returns Pydantic models matching the live provider's shape (round-trip equivalence to the live provider contract).
- `tests/unit/backtest/test_decision_logger.py` — given a synthetic post-tick session state with one Fill, writes a decision JSON whose schema validates and contains every required field.

**Tier 2 — Component integration (real SQLite, fake providers, no network):**

- `tests/integration/backtest/test_fetcher_idempotent.py` — fetcher invoked twice against fake live providers; second run is a no-op; `cache_runs` rows are not duplicated.
- `tests/integration/backtest/test_driver_one_tick.py` — drive the full live pipeline through one synthetic tick against a hand-populated cache; assert: (a) one tick file in `traces/`, (b) if any Fill was produced, one matching file in `decisions/`, (c) a `PortfolioSnapshotRow` exists in the run's `db.sqlite`.
- `tests/integration/backtest/test_driver_failure_threshold.py` — inject a deliberately broken analyst; run a 10-tick mini-window; assert the run aborts at the configured failure ratio.

**Tier 3 — End-to-end smoke (real cache, real pipeline, FakeBroker; LLM mocked or short-prompted):**

- `tests/integration/backtest/test_end_to_end_smoke.py` — one fixture run over a 3-day micro-window in a fixture cache. Asserts the run produces a complete manifest, an equity curve PNG, a `metrics.md` with non-NaN values, and ≥1 decision snapshot file. Marked `@pytest.mark.slow` so it runs in nightly CI only.

**Not tested in v1:**

- Live-provider correctness (already covered by `tests/data/providers/`).
- LLM behaviour itself (handled by your parallel plan).
- Backtest performance / Sharpe — those are strategy properties, not harness properties.

## Iteration surface / known unknowns

We expect the following to evolve as we run real backtests; none of them block v1:

- **Decision-snapshot schema** — first pass aims for "obvious". Expected extensions: strategist prompt+response, token usage, post-trade portfolio delta, intermediate ClampRecord reasoning.
- **Failed-tick threshold (10%)** — initial best guess; tune once we see real failure distributions.
- **Forward-return horizons (+1d, +5d, +20d)** — straw-man; real RAG-supervision work may want different lookahead.
- **Era-window catalogue** — v1 ships one window; we will add more as we learn what's diagnostically useful.
- **`as_of` migration** — first pass uses `as_of: datetime = datetime.utcnow()` as a default for backwards compatibility. Once every callsite is updated, the default can be removed for an even louder contract.

## Backlog (deferred ideas to add to `docs/superpowers/backlog.md`)

Tier 2 entry: **Historical social-sentiment ingestion.** Build a Pushshift-successor (`pullpush.io` / `arctic_shift`) scraper that backfills Reddit/WSB social-sentiment posts with timestamps for arbitrary date ranges. Compute sentiment locally (VADER or FinBERT) so we are not dependent on Finnhub's paid endpoint. Wires into the existing social-sentiment provider shell as a new upstream. **Why this is one-future-brainstorming-session-sized:** it has its own data-quality questions (which subreddits, which sentiment model, how to handle deletions), its own scraper-maintenance cost, and its own deployment posture (rate-limit-respecting, retry-able). Not bundled into the backtest harness.

Tier 3 entry: **Backtest resumability.** Allow a run interrupted mid-flight to be resumed against the same `run_id` instead of starting over. Requires checkpointing the last-completed tick and validating that the cache + config + git sha have not changed since interruption. Skip in v1.

Tier 3 entry: **Multi-window orchestration + cross-window dashboards.** Once v1 is stable, build a simple driver that runs all configured era windows in sequence and produces a single comparison report (regime-by-regime Sharpe, vs-SPY by era, decision count by era). One file in `reporting.py`, no schema changes.

Tier 3 entry: **Forward-return supervision tuning.** Beyond the straw-man +1d/+5d/+20d, explore what lookahead horizons are most informative for the RAG retriever. Likely depends on holding-period statistics from real backtest runs.
