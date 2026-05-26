# Vocab — orchestrator + lifecycle

## Pipeline factory functions
- `build_pipeline(broker, db_session=None, *, tickers)` — `src/orchestrator/pipeline.py:127`. Composes the `SequentialAgent("HourlyTick", ...)`.
- `_build_analyst_pool(tickers)` — `src/orchestrator/pipeline.py:7`. Returns `ParallelAgent("AnalystPool", [Parallel[Tech,Social], FundamentalBranch, NewsBranch])`.
- `_build_strategist()` — `src/orchestrator/pipeline.py:96`. Thin delegate to `agents.strategist.agent.build_strategist`; preserved as a stable mock seam for `mock.patch("orchestrator.pipeline._build_strategist", ...)`.
- `_build_memory_writer()` — `src/orchestrator/pipeline.py:121`. Returns `MemoryWriter()`.

## Tick entrypoint and helpers
- `run_once(broker, session=None, *, tick_label=None)` — `src/orchestrator/tick.py:164`. Live one-shot tick entry.
- `_build_initial_state(broker, tick_id, tickers)` — `src/orchestrator/tick.py:105`. Live Phase 2 seeder.
- `_fetch_reference_prices(symbols, *, as_of, period, interval)` — `src/orchestrator/tick.py:69`. Bulk yfinance pull for SPY + 11 SPDR sector ETFs.
- `_dispatch_app_name(broker_mode)` — `src/orchestrator/tick.py:25`. Returns `"StockBot-live"` or `"StockBot-paper"`.
- `BrokerMode` enum — `src/orchestrator/tick.py:13`. Values `LIVE = "live"`, `PAPER = "paper"`.
- `main()` — `src/orchestrator/tick.py:289`. CLI entry for one tick against the real Trading 212 broker.
- `_REFERENCE_SYMBOLS` — `src/orchestrator/tick.py:62`. Tuple of 12 symbols (SPY + 11 SPDR sectors).

## Watchlist
- `get_watchlist()` — `src/orchestrator/stock_picker.py:11`. Reads `config/watchlist.json`.

## State keys written by orchestrator (live Phase 2 seed)
- `tick_id` — deterministic per-tick id (`tick-<UTC-iso>-<8-hex>`).
- `tickers` — list[str] watchlist.
- `as_of` — `datetime` (timezone-aware UTC). NOTE: live writes a raw `datetime`; backtest coerces to ISO string before `create_session`.
- `tick_phase` — literal `"live"`.
- `portfolio` — `Portfolio.model_dump(mode="json")` dict.
- `memory_buffer` — `[]` (seeded empty; cross-tick persistence deferred to Spec C).
- `day_digest` — `""` (seeded empty; Spec C).
- `reference_prices` — `{symbol: PriceHistory.model_dump(mode="json")}`.

## State keys NOT seeded by orchestrator (intentional)
- `state["user:positions"]` — hydrated by ADK `DatabaseSessionService.user_state` merge on `create_session`.
- `state["user:thesis"]` — same.
- `state["positions"]` (bare key) — executor-internal, established at executor `_run_async_impl`.

## Persistence ORM models — `src/orchestrator/persistence.py`
- `BufferEntryRow` → table `buffer_entries`.
- `TradeLogRow` → table `trade_log`. Includes `opening_tick_id`, `closing_tick_id` FK-style strings.
- `TickerStanceRow` → table `ticker_stances`. UniqueConstraint `(tick_id, ticker)`.
- `PortfolioSnapshotRow` → table `portfolio_snapshots`.
- `AnalystEvidenceRow` → table `analyst_evidence`. Composite index `ix_analyst_evidence_lookup` on `(analyst, ticker, recorded_at)`.
- `TickerEvidenceRow` → table `ticker_evidence`.

## Persistence helpers
- `save_buffer_entry(session, entry_data, tick_id)` — `persistence.py:41`.
- `load_recent_buffer(session, tick_id, limit=24)` — `persistence.py:59`.
- `save_trade_log_entry(session, entry)` — `persistence.py:109`.
- `save_ticker_stance(session, *, tick_id, decision_tag, recorded_at, stance, lifecycle_action)` — `persistence.py:142`.
- `save_portfolio_snapshot(session, snap)` — `persistence.py:220`. Uses `resolve_as_of(...)` on `recorded_at`.
- `save_analyst_evidence(session, *, ...)` — `persistence.py:282`. Uses `resolve_as_of` on `recorded_at`.
- `save_ticker_evidence(session, *, ...)` — `persistence.py:359`. Uses `resolve_as_of` on `recorded_at`.

## Engine and session factories
- `make_engine(db_url="sqlite://")` — `persistence.py:411`.
- `make_session_factory(engine)` — `persistence.py:416`. Returns SQLAlchemy `sessionmaker`.
- `create_all(engine)` — `persistence.py:421`. Idempotent `Base.metadata.create_all`.
- `make_session_service(db_url=None)` — `persistence.py:429`. Returns ADK `DatabaseSessionService`; raises `RuntimeError` if neither `db_url` nor `DATABASE_URL` env var is set.
- `Base` — `persistence.py:23`. SQLAlchemy `DeclarativeBase`.

## TickState (Pydantic) — `src/orchestrator/state.py`
- `TickState` — `state.py:61`. Currently unused except by its own test; see F-orch-005.
- `Order`, `ClampRecord`, `Execution` — risk-gate / executor adjacent value classes.
- Risk-gate module-level constants imported from `config.risk_gate.get_risk_gate_config()`:
  - `MIN_HELD_WEIGHT`, `MAX_POSITION_WEIGHT`, `CASH_FLOOR_WEIGHT`, `MAX_DELTA_PER_TICKER`, `MAX_TOTAL_TURNOVER`, `ORDER_EPSILON`.

## Lifecycle phase verbs and entrypoints
- `initialise(*, db_url, starting_capital, broker_mode, watchlist, broker, scheduler_job)` — `src/lifecycle/initialise.py:126`. Phase 1 entry.
- `hard_reset(*, db_url, archive_dir, scheduler_job, meta_extra=None)` — `src/lifecycle/hard_reset.py:83`.
- `InitResult` dataclass — `initialise.py:36`.
- `ResetResult` dataclass — `hard_reset.py:20`.
- Preflight steps (in `initialise()` order): `_check_env` → `_check_heuristics` → `create_all` → `_check_live_tables_empty` → `_check_broker_cash` → `_fetch_spy_price` → `_write_anchor` → `scheduler.resume_job`.
- Preflight errors: `EnvVarMissingError`, `NonEmptyTablesError`, `BrokerCashMismatch`.

## Anchor terminology
- "anchor" — the seed row written by `initialise._write_anchor` to `portfolio_snapshots` with `tick_id="init"`. Captures starting capital + SPY price at deployment.
- `_fetch_spy_price()` — yfinance call pulled out for monkeypatch (`initialise.py:44`).

## Scheduler shim
- `scheduler.pause_job(name)` — `src/lifecycle/scheduler.py:7`. Shells out to `gcloud scheduler jobs pause`.
- `scheduler.resume_job(name)` — `scheduler.py:15`. Shells out to `gcloud scheduler jobs resume`.

## Hard-reset table list (currently stale per F-orch-004)
- `_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots")` — duplicated in `lifecycle/initialise.py:21` and `lifecycle/hard_reset.py:17`. Missing `ticker_stances`, `analyst_evidence`, `ticker_evidence`.

## Required env vars (live)
- `_REQUIRED_ENV = ("TRADING212_API_KEY", "FINNHUB_API_KEY")` — `initialise.py:20`.
- `DATABASE_URL` — read by `make_session_service` when no explicit `db_url`.
- `STOCKBOT_STRICT_AS_OF` — set to `"1"` by backtest runner; live must NOT set it (lets wall-clock fallback work).
