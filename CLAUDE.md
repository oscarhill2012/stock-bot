# CLAUDE.md

Guidance for Claude Code when working in this repository.
See also the user-global `~/.claude/CLAUDE.md` for style conventions (British
English, comment-heavy code, function docstrings, whitespace for legibility).

## Shell Conventions

Do **not** prepend `cd "/home/oscarhill2012/Documents/Repository/StockBot-phase6" && ...`
to Bash commands.  The Bash tool already runs in the project root.  Compound
`cd && ...` invocations break the permission allowlist and force manual
approval on every call.  Run commands directly:

```bash
git status
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window svb-stress-2023-03
```

## Project Goal

Build an AI-driven stock trading bot using Python data APIs, Google ADK, and
the Trading 212 API (practice account first, then live).  Full goal in the
main repo's `.claude/CLAUDE.md`.

**Current state:** Pre-deployment — no paper or live instance is running.

## Architecture

```
src/
├── agents/         # Google ADK agents (analysts, strategist, executor, etc.)
├── backtest/       # Backtesting harness (Phase 6)
│   ├── cache/      # SQLite golden-cache schema + store façade
│   ├── providers/  # Cache-backed data providers (one per domain)
│   ├── driver.py   # Tick-loop driver — runs the live pipeline per tick
│   ├── runner.py   # End-to-end run orchestrator (window → artefact tree)
│   ├── schedule.py # NYSE tick schedule generator
│   ├── windows.py  # Window config loader
│   ├── reporting.py # Equity curve + metrics.md + forward-return backfill
│   └── decision_logger.py  # Per-trade JSON snapshot writer
├── broker/         # FakeBroker + Trading212Broker
├── contract/       # Shared Pydantic schemas (evidence, verdicts)
├── data/           # Data ingestion layer (providers, models, registry)
├── orchestrator/   # Pipeline wiring + persistence + tick entrypoint
└── observability/  # TraceWriter
scripts/            # CLI entrypoints (backtest_fill, backtest_run, backtest_report)
config/             # JSON config files (one concern per file)
tests/              # pytest suite
```

## Backtest Harness

The backtesting layer in `src/backtest/` replays the **unmodified live
pipeline** against a frozen SQLite golden cache.  No live APIs or LLM calls
are needed during a replay.

### CLI entrypoints

All scripts are invoked as `PYTHONPATH=src python -m scripts.<name>`:

| Script | Purpose |
|---|---|
| `scripts.backtest_fill` | One-time cache fill — downloads and freezes market data for a date window |
| `scripts.backtest_run` | Execute a backtest window; writes `<runs_root>/<run-id>/` |
| `scripts.backtest_report` | Regenerate the report for an existing run directory |

### Config files

- `config/backtest_settings.json` — `cache_path`, `runs_root`,
  `fake_broker_starting_cash`, `forward_return_horizons_days`, lookback
  defaults, etc.
- `config/backtest_windows.json` — named era windows
  (e.g. `svb-stress-2023-03`).

### Run artefact layout

```
runs/<run-id>/
├── manifest.json          # run metadata, status, skipped tickers
├── db.sqlite              # portfolio snapshots + ticker stances (SQLAlchemy)
├── traces/                # one JSON trace file per tick
├── decisions/             # one JSON snapshot per executed trade (forward-returns backfilled)
└── report/
    ├── equity_curve.png
    └── metrics.md
```

### End-to-end smoke test

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```

Marked `@pytest.mark.slow` — excluded from the default `pytest` run.  LLM
agents (Strategist, Fundamental, News) are short-circuited via
`before_model_callback` shims that return synthetic `LlmResponse` objects;
yfinance is monkeypatched.  No Gemini credentials required.

### Important implementation notes

- **Initial state seeding**: `runner.py` must seed `portfolio`, `positions`,
  `memory_buffer`, `day_digest`, and `thesis` in addition to `tickers` and
  `watchlist`.  ADK's instruction-variable resolver raises `KeyError:
  'Context variable not found: portfolio'` if `portfolio` is absent.  This
  mirrors what `orchestrator/tick.py:_build_initial_state` does on live runs.
- **`OHLCBar` shape**: uses `timestamp: datetime` (not `date`); no `ticker`
  or `adj_close` field.  The store's `write_ohlcv` takes `ticker` separately.
- **`CompanyRatios`** replaces the retired `StockStats` model.  Use
  `write_company_ratios` / `read_company_ratios` (not `write_market_meta`).

## Key Commands

```bash
# Run the full test suite (fast — excludes slow + LLM integration)
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q

# Run only the end-to-end smoke test
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow

# Lint
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/

# Fill the backtest cache for a window
PYTHONPATH=src python -m scripts.backtest_fill --window svb-stress-2023-03

# Run a backtest
PYTHONPATH=src python -m scripts.backtest_run --window svb-stress-2023-03
```

## Configuration Convention

All configuration settings live in JSON files under `config/`.  A `README.md`
in `config/` describes every file and its settings.  Never hardcode config
values in source — add them to the appropriate JSON file and update
`config/README.md`.

## Docs Convention

Each experiment or milestone gets a dated file in `/docs`.  Architecture
decisions go in `docs/decisions/`.  Phase plans live in
`docs/Phase<N>-<name>/plans/`.
