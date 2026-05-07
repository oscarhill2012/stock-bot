# Phase 1.5 — Remaining Tasks

> **Status (2026-05-07):** Phases K, L, O, P implemented as part of the combined
> Phase 1.5 + Phase 2a plan. M and N1 are now provided by the Phase 2a baseline
> stack (`src/baselines/spy.py`, `src/baselines/equity_curve.py`,
> `src/scripts/plot_equity.py`). N2 (MLP) is deferred to Phase 3. N3 is
> simplified to a 2-way bot-vs-SPY comparison (the plotter covers it).

What's left after the initial agent/broker/orchestrator implementation. Phases K–P from `docs/phase1-agents.md`, deferred to continue tomorrow.

**Status:** Phases A–J implemented and passing (110 tests). All source code lives under `src/`.

---

## Phase K — Persistence extensions

### K1: SessionService factory
- Add `make_session_service()` to `src/orchestrator/persistence.py`
- Dev → SQLite-backed `DatabaseSessionService`; prod → Postgres via `DATABASE_URL` env var
- Test: dev/prod mode switching by `STOCKBOT_ENV`
- Commit: `feat(persistence): session-service factory by env`

### K2: DB init script
- Add `scripts/init_db.py` — one-shot `python -m scripts.init_db` runs `create_all()`
- Commit: `feat(persistence): init_db.py creates all tables`
- **Note:** scripts need `PYTHONPATH=src` — e.g. `PYTHONPATH=src python -m scripts.init_db`

### K3: AttributionSignals SQL table
- Add `AttributionSignalsRow` to `src/orchestrator/persistence.py` (discriminator column `analyst`)
- Serialise all four signal types into one table per tick
- Hook into pipeline (MemoryWriter or a post-pipeline callback) to write each tick
- Test: round-trip each signal type
- Commit: `feat(persistence): attribution_signals table + per-tick write`

---

## Phase L — Local end-to-end validation

### L1: Smoke run script
- Write `scripts/smoke_run.py`
- Instantiates `FakeBroker($10k)`, runs 3 consecutive ticks with real LLMs + real data providers
- Prints executions + final portfolio
- Run: `PYTHONPATH=src python -m scripts.smoke_run`
- Cost: ~$0.20/run (Gemini Flash + Pro)
- Commit: `feat: smoke_run script for local end-to-end validation`

### L2: Replay backtest harness
- Write `scripts/replay_backtest.py` — 30-day walk-forward using cached yfinance data
- `--fixture-dir` flag swaps real providers for fixture loaders via `unittest.mock`
- Write `tests/replay/test_replay_30days.py` (marked `@pytest.mark.replay`)
- Commit: `feat: replay_backtest harness for Tier 4 evaluation`

---

## Phase M — Equity plotter

> **SUPERSEDED by Phase 2a:** This work is now covered by `src/baselines/spy.py`, `src/baselines/equity_curve.py`, and `src/scripts/plot_equity.py`.

### M1: plot_equity.py
- Write `scripts/plot_equity.py`
- Reads `portfolio_snapshots` table from SQLite/Postgres
- Three-panel plot: bot equity, SPY-if-held, excess return bar chart
- Output: `docs/performance/<date>.png`
- Test: write to temp file, assert non-empty
- Commit: `feat(scripts): plot_equity.py — bot vs SPY equity curve`

---

## Phase N — Baselines

> **SUPERSEDED by Phase 2a:** This work is now covered by `src/baselines/spy.py`, `src/baselines/equity_curve.py`, and `src/scripts/plot_equity.py`.

### N1: SPY buy-and-hold baseline
- Write `src/baselines/spy.py`
- Pull SPY OHLCV via yfinance, compute: cumulative return, annualised return, Sharpe, max drawdown, Calmar
- Reference: `docs/baselines.md`
- Commit: `feat(baselines): SPY buy-and-hold baseline`

### N2: PyTorch MLP baseline (DEFERRED to Phase 3)

Originally part of the live-trading gate per `docs/baselines.md`. Deferred:
the simpler bot-vs-SPY comparison covers Phase 1's "is the bot beating
buy-and-hold?" question. Reintroduce when Phase 3 model training begins.

> **SUPERSEDED by Phase 2a:** This work is now covered by `src/baselines/spy.py`, `src/baselines/equity_curve.py`, and `src/scripts/plot_equity.py`.

### N3: 3-way evaluation harness
- Write `src/baselines/evaluate.py`
- Runs StockBot replay + SPY + MLP over same window
- Writes comparison table to `docs/performance/<date>.md` with pass/fail line
- Commit: `feat(baselines): evaluate.py runs 3-way comparison`

---

## Phase O — Cloud deployment

### O1: Dockerfile
```dockerfile
FROM python:3.12-slim   # updated from 3.11-slim to match dev Python
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY config/ ./config/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 STOCKBOT_ENV=prod
ENTRYPOINT ["python", "-m", "orchestrator.tick"]
```
- Test: `docker build -t stockbot-tick:dev -f deploy/Dockerfile . && docker run --rm stockbot-tick:dev --help`
- Commit: `feat(deploy): Dockerfile`

### O2: cloudbuild.yaml
- Build → push → deploy Cloud Run Job on each commit to main
- See template in `docs/phase1-agents.md §Phase O Task O2`
- Commit: `feat(deploy): cloudbuild.yaml`

### O3: scheduler.yaml + GCP setup runbook
- Cron: `30 9-15 * * 1-5` America/New_York → Cloud Run Job execute endpoint
- `deploy/README.md`: one-time GCP setup runbook (enable APIs, service account, Cloud SQL, Secret Manager, scheduler)
- Commit: `feat(deploy): scheduler config + GCP setup runbook`

---

## Phase P — Final acceptance

### P1: Paper-trading kickoff checklist
Add to `deploy/README.md`:
1. `PYTHONPATH=src python -m scripts.smoke_run` — confirm clean output
2. `PYTHONPATH=src python -m scripts.replay_backtest --window 30d` — verify sane decisions
3. `PYTHONPATH=src python -m baselines.evaluate` — confirm comparison report

### P2: Live-trading gate
- Bot must beat both SPY and MLP on **Sharpe + cumulative return** over ≥30 days of paper trading
- Flip `broker_mode=live` only after this gate passes
- Gate is manual/observational — no automated promotion

---

## Notes for next session

> **Note (2026-05-07):** Work complete. These notes are historical.

- **`PYTHONPATH=src`** required for running scripts directly (pytest handles this automatically via `pytest.ini`)
- **ADK 1.32**: `analyst_pool`, `strategist_agent`, `memory_writer` are factory functions (not module-level singletons) due to ADK's single-parent enforcement — see `src/orchestrator/pipeline.py`
- **Quiver Quant**: `get_public_figure_trades` soft-fails to `[]` until `QUIVER_QUANT_API_KEY` is set; Smart Money gate works on insiders + notable holders in the interim
- **google-adk 1.32** installed (plan was written for 0.2.x — API surface is compatible, `LlmAgent`/`BaseAgent`/`ParallelAgent`/`SequentialAgent` all verified working)
