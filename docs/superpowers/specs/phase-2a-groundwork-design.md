# Phase 2a — Groundwork

**Status:** Approved (brainstorming complete; ready for implementation plan)
**Scope:** Three small, unrelated pieces of plumbing required before Phase 2b (dashboard) and 2c (pluralised deliberation): lifecycle scripts (`initialise` / `hard_reset`), simplified bot-vs-SPY baseline, and a recorded decision on execution timing.

---

## 1. Goals & non-goals

**Goals**
- A single `initialise` command that boots the bot (pre-flight checks, anchor snapshot, scheduler resume) and a symmetric `hard_reset` command that tears it down (scheduler pause, archive, truncate).
- A bot-vs-SPY equity curve, computed by a shared library that the future dashboard (2b) will reuse.
- A static matplotlib plot for the first paper-trading runs, before the dashboard exists.
- An explicit, recorded decision on same-tick execution.

**Non-goals**
- Live local scheduling (no PID files, no Windows Task Scheduler, no run-loop process). The bot only runs in the cloud; local dev paths stay one-shot.
- MLP baseline (deferred to Phase 3 — breadcrumb retained in `phase1.5-remaining.md`).
- Any visualisation beyond the static PNG (the dashboard is 2b).
- Any change to the multi-agent pipeline shape (deliberation changes are 2c).
- Automated Trading 212 cash reset (T212's API doesn't expose it; stays manual via their UI).

---

## 2. Module layout

```
src/
├── lifecycle/
│   ├── __init__.py
│   ├── initialise.py         # boot the bot
│   └── hard_reset.py         # tear it down + archive
├── baselines/
│   ├── spy.py                # SPY metrics (cum. return, Sharpe, drawdown, ...)
│   └── equity_curve.py       # compute_equity_curve() — shared with future dashboard
scripts/
├── initialise.py             # thin CLI wrapper over src/lifecycle/initialise.py
├── hard_reset.py             # thin CLI wrapper over src/lifecycle/hard_reset.py
└── plot_equity.py            # thin matplotlib CLI over src/baselines/equity_curve.py
```

Library code lives in `src/`; CLI wrappers under `scripts/` are skinny and contain no logic. This keeps lifecycle code testable and the equity curve reusable from the dashboard later.

No changes to existing pipeline modules (`agents/`, `orchestrator/`, `broker/`, `data/`).

---

## 3. Lifecycle scripts

### 3.1 Topology

`initialise` and `hard_reset` are **lifecycle scripts you run locally that reach into the cloud**. They manage Cloud Scheduler and Cloud SQL together. No long-running local processes.

```
┌─────────────────────────┐         ┌──────────────────────┐
│ Your laptop             │         │ GCP                  │
│ python -m scripts.      │ ─────▶  │ Cloud Scheduler      │
│   initialise            │ resume  │ Cloud SQL (anchor)   │
│ python -m scripts.      │ ─────▶  │ Cloud Scheduler      │
│   hard_reset            │ pause   │ Cloud SQL (archive)  │
└─────────────────────────┘         └──────────────────────┘
```

Local dev paths (`smoke_run.py`, `replay_backtest.py`, ad-hoc `tick.py`) are unaffected. They don't go through `initialise`. If `db_url` points at SQLite, the scheduler step in both scripts is a no-op (logged).

GCP credentials (`gcloud auth application-default login`) are required for the scheduler step. That's already a prerequisite for deploying the bot, so no new operator burden.

### 3.2 `initialise`

```python
# src/lifecycle/initialise.py
def initialise(
    db_url: str,
    *,
    starting_capital: float,
    broker_mode: Literal["paper", "live"],
    watchlist: list[str],
    scheduler_job: str | None = None,   # None for SQLite / dev
) -> InitResult: ...
```

Steps, in order:

1. **Pre-flight.** Cloud SQL reachable; required env vars set (`TRADING212_API_KEY`, `FINNHUB_API_KEY`, `QUIVER_QUANT_API_KEY` optional); broker reachable; T212 cash matches `starting_capital` within $1 tolerance.
2. **Refuse on non-empty live tables.** If any StockBot-owned table has rows, exit 1 with `run hard_reset first`. Prevents accidental double-init that would corrupt the equity-curve anchor.
3. **Schema seed.** Run table creation (the §K2 logic) if tables don't exist yet. Idempotent.
4. **Anchor snapshot.** Write a synthetic `PortfolioSnapshot` row with `tick_id="init"`, `bot_total_value = starting_capital`, `bot_cash = starting_capital`, `bot_positions_value = 0`, `bot_position_count = 0`, `spy_price = <current SPY close via yfinance>`, `spy_value_if_held = starting_capital`, all return-pct fields = 0.0. This is the equity-curve anchor.
5. **Resume scheduler.** `gcloud scheduler jobs resume <scheduler_job>` (skip if SQLite).
6. **Print summary.** Next firing time, watchlist length, broker mode, capital, archive path of last reset (if any).

### 3.3 `hard_reset`

```python
# src/lifecycle/hard_reset.py
def hard_reset(
    db_url: str,
    *,
    archive_dir: Path,
    scheduler_job: str | None = None,
) -> ResetResult: ...
```

Steps, in order:

1. **Pause scheduler first.** `gcloud scheduler jobs pause <scheduler_job>` (skip if SQLite). Done before any archiving so no tick can fire mid-wipe.
2. **Acquire advisory lock.** `SELECT pg_try_advisory_lock(...)` (Postgres) or `BEGIN EXCLUSIVE` (SQLite). Aborts if held — guards against a tick already in flight.
3. **Archive.** Backend-aware:
   - **SQLite:** `VACUUM INTO 'data/archives/<timestamp>.db'`, then truncate live tables.
   - **Postgres:** `CREATE SCHEMA stockbot_archive_<timestamp>`, then for each StockBot table `CREATE TABLE archive.X AS SELECT * FROM public.X`, then `TRUNCATE` the live tables in one transaction.
4. **Write `archive_meta.json`** alongside the archive: `{archived_at, watchlist, model_versions, broker_mode, git_sha, starting_capital_of_archived_run, row_counts_per_table}`.
5. **Remind operator** about T212: print "Reset Trading 212 practice account in the UI now (Settings → Practice account → Reset). Then run `python -m scripts.initialise --capital <amount>`."

Tables archived: ADK session state table(s), `decisions`, `executions`, `trade_log`, `portfolio_snapshots`, `attribution_signals`. ADK's own framework-internal tables are left alone (they recreate themselves).

### 3.4 CLI UX

`scripts/hard_reset.py`:

```
$ python -m scripts.hard_reset
This will pause the scheduler, archive all StockBot state, and wipe live tables.
Archive will be written to: data/archives/2026-05-07T14-30-00.db
Type 'RESET' to confirm: RESET

✓ Paused Cloud Scheduler job stockbot-tick
✓ Archived 6 tables, 1,247 rows → data/archives/2026-05-07T14-30-00.db
✓ Live tables truncated
✓ Wrote archive_meta.json

Next: reset Trading 212 practice account in the UI, then run:
  python -m scripts.initialise --capital 10000
```

`scripts/initialise.py`:

```
$ python -m scripts.initialise --capital 10000
✓ Cloud SQL reachable
✓ Live tables empty
✓ Required env vars set
✓ Trading 212 reachable, cash $10,000.00 matches expected
✓ Wrote anchor snapshot (SPY $478.23)
✓ Resumed Cloud Scheduler job stockbot-tick
  → next tick: 2026-05-07 14:30 America/New_York

Bot is live (paper mode). Watchlist: 15 tickers.
```

Single literal-string confirmation (`RESET`) on `hard_reset`. `initialise` runs without confirmation (it's already gated by the live-tables-empty check).

---

## 4. SPY baseline + equity curve

### 4.1 `src/baselines/spy.py`

Pure functions, no I/O beyond yfinance:

```python
@dataclass(frozen=True)
class SPYMetrics:
    cumulative_return: float
    annualised_return: float
    sharpe: float
    max_drawdown: float
    calmar: float

def spy_metrics(start: date, end: date) -> SPYMetrics: ...
```

Used standalone for the baseline summary in performance reports, and as a building block for the equity-curve lib.

### 4.2 `src/baselines/equity_curve.py`

The shared lib that both the static plotter and the future dashboard import:

```python
@dataclass(frozen=True)
class EquityCurve:
    timestamps: list[datetime]
    bot_pct: list[float]        # 0.0 = anchor, 0.05 = +5%
    spy_pct: list[float]
    excess_pct: list[float]     # bot_pct - spy_pct
    anchor_tick_id: str
    anchor_bot_value: float
    anchor_spy_price: float

def compute_equity_curve(db_url: str) -> EquityCurve: ...
```

Logic: read every `portfolio_snapshots` row from the live DB ordered by `tick_id`. Anchor = first row (this is the synthetic `init` row written by `initialise`). For each subsequent row:

- `bot_pct = (row.bot_total_value / anchor.bot_total_value) - 1`
- `spy_pct = (row.spy_price / anchor.spy_price) - 1`
- `excess_pct = bot_pct - spy_pct`

Archived rows live in a separate schema/file and are excluded by design — each run gets its own clean anchor.

Empty DB (no anchor yet) returns an `EquityCurve` with empty lists. Single-row DB (anchor only, no ticks yet) returns lists of length 1 with all percentages at 0.0. Caller decides how to render those edge cases.

### 4.3 `scripts/plot_equity.py`

Thin matplotlib wrapper:

```
$ python -m scripts.plot_equity --out docs/performance/2026-05-07.png
✓ 14 ticks since reset (anchor: 2026-05-06T13:30Z)
✓ Bot: +1.2%   SPY: +0.4%   Excess: +0.8%
✓ Wrote docs/performance/2026-05-07.png
```

Single chart with two overlaid lines (bot and SPY, both anchored at 0%) plus a thin excess-delta line on a secondary axis. Title shows date range and cumulative metrics. ~40 lines of matplotlib.

### 4.4 Phase 1.5 §N changes

- §N1 (SPY baseline) → kept; becomes part of `src/baselines/spy.py` in this phase.
- §N2 (MLP baseline) → removed from 1.5. Breadcrumb retained in `phase1.5-remaining.md` under "Deferred to Phase 3".
- §N3 (3-way evaluation harness) → simplified to 2-way bot-vs-SPY. The plotter covers it for now; no separate `evaluate.py` until the MLP returns.

---

## 5. Execution timing decision

**Decision:** Keep same-tick execution. Analysts → strategist → risk gate → executor all run synchronously within one Cloud Run Job invocation, as specified in the Phase 1 multi-agent design.

**Rationale:** Latency from data fetch to order submit is seconds, not minutes. Market orders fill near-instantly. At hourly cadence the "stale data" concern is not real. Splitting into two-tick decide-then-execute introduces a second persistent decision state and reconciliation logic for benefit that isn't measurable.

**Reconsider trigger:** If the dashboard (2b) shows recurring meaningful price drift between strategist's `est_price` and executor's `actual_price` (e.g., median slippage > 25 bps), revisit by adding a price-sanity check at the executor (re-fetch each ticker's price right before submitting; abort the order if it's moved >X% since `est_price`). This is a cheap add-on that doesn't require splitting the tick.

---

## 6. Testing strategy

| Component | Tier | Approach |
|---|---|---|
| `equity_curve.py` | 1 (unit) | Fixture rows in a temp SQLite DB → assert anchor math, empty case, single-row case |
| `spy.py` metrics | 1 (unit) | Hand-crafted price series → assert Sharpe / drawdown values to known reference |
| `plot_equity.py` | 1 (unit) | Render to a tempfile, assert non-empty PNG (existing pattern from §M1) |
| `hard_reset.py` | 1 (unit) | Seed temp SQLite, run reset, assert archive file exists with N rows + live tables empty + `archive_meta.json` present |
| `initialise.py` | 1 (unit) | Pre-flight refuses on non-empty tables; succeeds on empty; anchor snapshot written with correct fields |
| Lifecycle + Cloud Scheduler | Skipped | The `gcloud scheduler` calls are thin shell-outs — verify the command string, don't mock GCP |

No new Tier 2-5 tests; this is groundwork code, not pipeline code.

---

## 7. Failure handling

| Scenario | Behaviour |
|---|---|
| `initialise` against non-empty tables | Exit 1 with "run hard_reset first" |
| `initialise` and T212 cash mismatch | Exit 1 with "expected $X, found $Y; reset T212 cash and retry" |
| `initialise` and required env var missing | Exit 1 with explicit list of missing vars |
| `initialise` and Cloud Scheduler resume fails | Local state is already seeded; print actionable error and exit 1. Operator can re-run `gcloud scheduler jobs resume` directly. |
| `hard_reset` and Scheduler pause fails | Abort before archive — refuse to wipe state if a tick could still fire. |
| `hard_reset` and archive write fails | Abort before truncate — never wipe live state without a successful archive. |
| `hard_reset` and advisory lock held | Abort with "tick may be in flight; wait and retry" |
| `hard_reset` and archive path already exists | Abort (timestamp collision indicates clock or concurrent-run problem) |
| GCP auth missing | Both scripts exit 1 with `gcloud auth application-default login` instruction |

---

## 8. Decisions log

| # | Decision | Rationale |
|---|---|---|
| 1 | Same-tick execution kept | Hourly cadence makes deferral pointless; no measurable freshness gain |
| 2 | MLP baseline deferred to Phase 3 | Not load-bearing for "is the bot beating buy-and-hold"; breadcrumb retained |
| 3 | `equity_curve.py` is a shared lib, not a script-internal helper | Future dashboard (2b) will import the same function; render layer differs only |
| 4 | Lifecycle scripts run locally, manage cloud | No local long-running runner; cloud is the only execution environment |
| 5 | `hard_reset` archives, never just deletes | Wipe-and-archive is barely more code; never lose a run's data on reset |
| 6 | Scheduler pause is the first step of `hard_reset` | No tick may fire while archive/truncate is in progress |
| 7 | `initialise` writes a synthetic anchor snapshot | Equity curve needs a starting reference; without it the first real tick has nothing to compare against |
| 8 | T212 cash reset stays manual | T212 API doesn't expose it; one-click in their UI |
| 9 | `initialise` refuses on non-empty live tables | Prevents corrupting the equity-curve anchor on accidental double-init |
| 10 | Single-string confirmation (`RESET`) on `hard_reset` | Operator can't fat-finger past it; no `--yes` flag (lifecycle scripts don't run in tests) |

---

## 9. Open questions deferred to implementation plan

- Exact Cloud Scheduler job name (probably `stockbot-tick` per `phase1-agents.md §Phase O`).
- Tolerance for T212 cash mismatch check ($1? $0.01?).
- Whether `archive_dir` is configurable per-environment or fixed (`data/archives/` for SQLite, fixed schema-naming pattern for Postgres).
- Whether `archive_meta.json` records full env-var values (no — only their *presence*, never values) and which model versions are pinned at archive time.

---

**End of design document.** Ready for implementation plan via `superpowers:writing-plans`.
