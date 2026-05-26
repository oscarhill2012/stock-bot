# Vocabulary — `src/backtest/`

Audit date: 2026-05-26. Scope: every identifier, key, label, or term coined or owned by `src/backtest/` (including `cache/`, `providers/`, `audit/`) plus the four `scripts/backtest_*.py` CLIs and their config keys.

---

## 1. Classes (public)

| Name | Source | Role |
|---|---|---|
| `Driver` | `driver.py` | Tick-loop executor that wires the live pipeline against the cache. |
| `Runner` | `runner.py` | End-to-end orchestrator (window → run-dir → driver → report). |
| `RunResult` | `runner.py` | Dataclass returned from `Runner.run()` (run_id, run_dir, status). |
| `Tick` | `schedule.py` | `(as_of: datetime, phase: Literal["open","close"])` pair. |
| `Window` | `windows.py` | Pydantic model `(start: date, end: date)` with `_check_range` validator. |
| `BacktestSettings` | `settings.py` | Pydantic settings model — `extra="forbid"`. |
| `DecisionLogger` | `decision_logger.py` | Per-Fill snapshot writer registered as `state['temp:_decision_logger']`. |
| `Fetcher` | `cache/fetcher.py` | Idempotent backfill driver writing the golden cache. |
| `CachedDataStore` | `cache/store.py` | The SQLite golden-cache façade. |
| `AuditingStore` | `audit/auditing_store.py` | Decorator over `CachedDataStore` that captures every read. |

## 2. Classes (cache row schemas — `cache/schema.py`)

| Name | Notes |
|---|---|
| `OHLCBarRow` | OHLCV bar persistence row. |
| `NewsArticleRow` | News article persistence row. |
| `FilingRow` | SEC filing persistence row. |
| `InsiderTradeRow` | Form 4 trade persistence row. |
| `NotableHolderRow` | SC-13D/G/F holder persistence row. |
| `PoliticianTradeRow` | Congressional/politician trade persistence row. |
| `CompanyRatiosRow` | Per-as_of company ratios snapshot row. |
| `MetaRow` | Schema-meta singleton row (`SCHEMA_VERSION = 2`). |
| `CacheRunRow` | Per-fetch audit trail row (`status`, `started_at`, `live_provider`). |
| `SCHEMA_VERSION` | Module constant — currently `2`. |

## 3. State keys written / read by backtest code

| Key | Producer | Notes |
|---|---|---|
| `as_of` | Driver per-tick | ISO-string (timeguard boundary coerced). |
| `tick_phase` | Driver | `"open"` / `"close"`. |
| `tick_id` | Driver | `<run-id>-<as_of>-<phase>`. |
| `tickers` | Runner seed | Watchlist. |
| `watchlist` | Runner seed | Synonym used by some agents. |
| `portfolio` | Runner seed → broker refresh | Broker-of-truth working copy. |
| `positions` | Carried from updated_state | Bare key — see F-backtest-005. |
| `user:positions` | (Executor) | Source of truth; runner intentionally leaves empty. |
| `user:thesis` | (Strategist context shim) | Persisted thesis book. |
| `memory_buffer` | Runner seed `[]` | Carried tick→tick; consumed by `agents.memory.writer`. |
| `day_digest` | Runner seed `""` | Compressed daily summary. |
| `thesis` | (Strategist) | Bridged from `user:thesis` by context shim. |
| `reference_prices` | Driver `_seed_reference_prices` | SPY + SPDR ETFs OHLCV slice, JSON-dumped. |
| `last_snapshot` | Snapshotter | Drives `_enforce_completion` check. |
| `executions` | Executor | Consumed by `decision_logger.on_executions`. |
| `strategist_decision` | Strategist | `stances` list, `reasoning`, `thesis`, `decision_tag`, `confidence`, `sell_reasons`. |
| `clamps` | Risk gate | Consumed by `decision_logger._build_snapshot`. |
| `temp:_decision_logger` | Plugin install | Triggers `DecisionLogger.on_executions`. |
| `temp:_trace` | Plugin install | `TraceWriter` activation flag. |
| `temp:technical_data` | Technical analyst | Per-ticker raw data. |
| `temp:fundamental_data` | Fundamental analyst | Per-ticker raw data. |
| `temp:news_data` | News analyst | Per-ticker raw data. |
| `temp:social_data` | Social analyst | Per-ticker raw data (null in backtest). |
| `temp:ticker_evidence_objects` | Context shim | List of TickerEvidence dumps. |
| `smart_money_data` | Smart-money analyst | Not temp-prefixed — Pydantic raw, coerced via `_coerce`. |

## 4. Config / settings keys (`config/backtest_settings.json`)

| Key | Type | Notes |
|---|---|---|
| `backtests_root` | str | Root for `<window>/store.sqlite` + `runs/`. |
| `ticks_per_day` | list[str] | E.g. `["open","close"]`. |
| `failed_tick_abort_ratio` | float [0,1] | Driver abort threshold. |
| `fake_broker_starting_cash` | float | Initial cash for FakeBroker. |
| `forward_return_horizons_days` | list[int] | Backfilled by `_backfill_forward_returns`. |
| `ohlcv_warmup_days` | int (=30) | Pre-window OHLCV padding for rolling indicators. |

## 5. Settings module functions / helpers

| Symbol | Source | Notes |
|---|---|---|
| `load_backtest_settings_from(path)` | settings.py | Test-injectable loader. |
| `get_backtest_settings()` | settings.py | LRU-cached default-path loader. |
| `cache_path_for_window(settings, window)` | settings.py | `<root>/<window>/store.sqlite`. |
| `runs_root_for_window(settings, window)` | settings.py | `<root>/<window>/runs/`. |
| `window_from_run_id(run_id)` | settings.py | Strips trailing `-<sha7>` from run-id. |
| `_reset_cache()` | settings.py | Test-only singleton reset. |
| `_DEFAULT_PATH` | settings.py | `Path("config/backtest_settings.json")`. |

## 6. CLI scripts and their flags

### `scripts/backtest_fetch.py`
- `--window` (required) — window key.
- `--watchlist` (default `config/watchlist.json`) — tickers source.
- `--refetch-domain` (repeatable) — force overwrite per domain.

### `scripts/backtest_run.py`
- `--window` (required).
- `--limit N` — tick cap (sanity runs).
- `--run-id NAME` — override default `<window>-<sha7>`.
- `--fresh` — delete `session.sqlite` before start.
- `--log-level {minimal,info,debug}`.

### `scripts/backtest_report.py`
- `--run-id` (required) — regenerate report only.

### `scripts/backtest_audit_tick.py`
- `--run-id`, `--window`, `--tick` (ISO), `--phase {open,close}`.

## 7. Environment variables

| Var | Notes |
|---|---|
| `STOCKBOT_STRICT_AS_OF=1` | Mandatory for backtests — set by `backtest_run.py` and `backtest_audit_tick.py`. |
| `STOCKBOT_TERMINAL_LOG=1` | Per-LLM-call observability — set by `backtest_run.py`. |

## 8. Audit-subsystem vocabulary (`backtest/audit/`)

| Term | Source | Meaning |
|---|---|---|
| `build_telemetry_record(...)` | telemetry.py | Layer-1 per-tick record assembler. |
| `build_telemetry_record_from_logs(...)` | telemetry.py | Dead alternate (see F-backtest-006). |
| `write_telemetry_record(audit_dir, record)` | telemetry.py | Writes `<tick-slug>.tick.json`. |
| `per_domain_from_store_reads(...)` | telemetry.py | Captured-rows → `per_domain` shape. |
| `compute_tripwires(...)` | tripwires.py | Five-flag rollup. |
| `ACTIONABLE_TRIPWIRES` | tripwires.py | Frozenset excluding `*_advisory` flags. |
| `build_deep_rows(...)` | deep_dump.py | Layer-2 per-row evidence builder. |
| `write_deep_dump(...)` | deep_dump.py | Writes `<tick-slug>.full.jsonl` + `.summary.md`. |
| `verify_row(...)` | upstream_verifier.py | Per-row PIT-evidence dict assembler. |
| `_verify_filing(row)` | upstream_verifier.py | EDGAR re-fetch placeholder (always-green; see F-backtest-010). |
| `_verify_news(row)` | upstream_verifier.py | Tiingo re-fetch placeholder (always-green). |
| `_AGREEMENT_TOLERANCE` | upstream_verifier.py | `timedelta(seconds=60)`. |
| `_is_midnight_utc(value)` | upstream_verifier.py | Helper. |
| `_same_day(value, tick_as_of)` | upstream_verifier.py | Helper. |
| `_filter_key(domain, row)` | upstream_verifier.py | Per-domain PIT-key extractor. |

### Tripwire flag names (`tripwires.py`)
- **Actionable:** `wall_clock_fallback_fired`, `any_filter_key_after_as_of`, `missing_timestamp_rows_seen`.
- **Advisory (excluded from counts):** `open_tick_sameday_bar_advisory`, `midnight_utc_timestamps_seen_advisory`.

### Deep-dump summary counter keys (`deep_dump._build_summary`)
- `fabricated_timestamp`, `midnight_utc`, `same_day_as_as_of`, `missing_timestamp`, `upstream_disagreement`.

### Cache-store audit hooks (`cache/store.py`)
- `_audit_capture_enabled()`, `_audit_record(domain, ticker, rows)`, `_audit_enable_capture()`, `_audit_drain_reads()`, `_audit_reads` (instance attr).

## 9. Driver / Runner internal vocabulary

### Driver (`driver.py`)
- `_failed: list[dict]` — per-tick failure record.
- `_total: int`, `_ratio: float` — abort-threshold inputs.
- `_run_id`, `_run_dir`, `_window_key` — run identity.
- `_audit_dir`, `_traces_dir`, `_obs_dir` — artefact subdirs.
- `_enforce_completion: bool` — flag.
- `_log_exception_chain(exc, tick_id)` — module fn.
- `_run_one_tick(state, traceWriter)` — per-tick body.
- `_refresh_broker_prices(tickers, tick)` — FakeBroker price refresh.
- `_drain_logs_cache_hits()` — report_cache_hit log scanner.
- `_enforce_completion` check — `state['last_snapshot'].tick_id == state['tick_id']`.
- `_write_manifest_status(status)` — writes `manifest.json`.

### Runner (`runner.py`)
- `_seed_reference_prices(*, store, window_start, window_end, as_of)` — SPY/SPDR PIT slicer.
- `_seed_initial_prices(*, store, tickers, window_start, window_end)` — FakeBroker bootstrap (see F-backtest-004).
- `_run_async(...)` — inner async body.
- `_runs_root_from_config(window_key)` — classmethod.
- `_REFERENCE_SYMBOLS` — also defined in `scripts/backtest_fetch.py`.
- `_git_sha7()`, `_git_sha_full()` — git helpers (see F-backtest-007).
- `RunResult.status` — `"completed" | "completed_with_failures" | "aborted"`.

### Reporting (`reporting.py`)
- `report(run_dir, settings, window)` — top-level entry.
- `report_progress(run_dir, settings)` — interim entry.
- `_spy_benchmark_series(...)`, `_matched_exposure_series(...)` — series builders.
- `_compute_vs_spy_delta(...)`, `_annualised_sharpe(...)`, `_avg_exposure_pct(...)` — metrics.
- `_backfill_forward_returns(run_dir, settings)` — DecisionLogger snapshot post-processor.
- `_aggregate_obs_artefacts(run_dir)`, `_format_obs_section(...)` — observability rollup.
- `_parse_date(s)` — helper.
- N/A signalling sentinels (see F-backtest-008): `"N/A — SPY not in cache"`, `"N/A — SPY series too short"`, `"N/A — matched series too short"`, `"N/A — matched series start is zero"`, `"_N/A_"`, `"N/A (no closed trades)"`.

## 10. Cache fetcher (`cache/fetcher.py`) vocabulary

- `_WRITER_BY_DOMAIN: dict[str, Callable]` — domain → CachedDataStore writer.
- `_already_ok(store, ticker, domain)` — idempotency check against `cache_runs`.
- `fill_audit.json` — per-run shrinkage report filename.
- `Fetcher.run()` — main loop.
- `refetch_domains: set[str]` — force-overwrite list from CLI.
- `live_providers_for_domain: dict[str, str]` — provider-name attribution for `cache_runs.live_provider`.

## 11. Provider domain keys

Both the registered cache providers (`src/backtest/providers/*_cache.py`) and the fetcher's `_WRITER_BY_DOMAIN` map use these strings:

`price_history`, `company_ratios`, `news`, `filings`, `insider_trades`, `notable_holders`, `politician_trades`, `social_sentiment`.

### Cache provider modules
- `price_history_cache.py` — declares `_PERIOD_DAYS: dict[str, int]` (yfinance period semantics shadow).
- `company_ratios_cache.py`.
- `news_cache.py`.
- `filings_cache.py`.
- `insider_trades_cache.py` — wraps result in `Form4Bundle(derivatives=[])` for shape parity.
- `notable_holders_cache.py` — fetcher disabled (see F-backtest-012).
- `politician_trades_cache.py` — fetcher disabled (see F-backtest-011).
- `social_sentiment_cache.py` — returns empty model (placeholder for backlog B19).
- `_store_handle.py` — module-level `_store` singleton + `set_store`, `get_store`, `clear_store`.

All providers share the registration signature: `@register(domain, "cache", upstream="cache", rate_per_minute=1_000_000, burst=1_000)` and accept `**_unused` to absorb dispatcher kwargs.

## 12. Schedule (`schedule.py`)

- `generate_ticks(start, end, ticks_per_day)` — yields `Tick` objects.
- `_SUPPORTED_PHASES: frozenset[str]` — `{"open","close"}`.
- NYSE calendar hardcoded via `pandas_market_calendars.get_calendar("NYSE")`.

## 13. Decision-logger vocabulary (`decision_logger.py`)

- `_coerce(value)` — recursive Pydantic / datetime normaliser.
- `_strict_default(value)` — `json.dumps` default that **raises** (loud-failure pattern — good).
- `_serialise_snapshot(snapshot)` — public coerce + dump entry point.
- `_slug(as_of)` — filename-safe ISO slug.
- `DecisionLogger.on_executions(state)` — public per-tick hook.
- `DecisionLogger._build_snapshot(...)` — assembles one decision dict.

### Snapshot top-level keys
`decision_id`, `tick`, `ticker`, `side`, `execution`, `analyst_inputs`, `analyst_outputs`, `strategist_view`, `strategist_decision`, `risk_gate`, `forward_returns`.

### `tick` sub-keys
`as_of`, `phase`, `window_key`, `tick_id`.

### `execution` sub-keys
`order_qty`, `fill_price`, `fill_qty`, `status`, `broker_order_id`, `slippage_bps`.

### `analyst_inputs` sub-keys
`technical`, `fundamental`, `news`, `smart_money`, `social`.

### `strategist_view` sub-keys
`ticker_evidence`, `held_view_at_decision` (see F-backtest-005).

### `strategist_decision` sub-keys
`stance`, `sell_reason`, `reasoning`, `thesis`, `decision_tag`, `confidence`.

### `risk_gate` sub-keys
`clamps`.

## 14. Reference symbols (`backtest_fetch.py` + `runner.py`)

`_REFERENCE_SYMBOLS = ("SPY", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC")` — duplicated between `scripts/backtest_fetch.py:379` and (implicitly via `_seed_reference_prices`) `src/backtest/runner.py`. Mirrors `orchestrator.tick._REFERENCE_SYMBOLS`.

## 15. Artefact directory layout (per run)

```
<backtests_root>/<window>/
├── store.sqlite
└── runs/
    └── <run-id>/                       # <window>-<sha7>
        ├── manifest.json
        ├── session.sqlite              # ADK DatabaseSessionService
        ├── db.sqlite                   # additional store (Driver-owned)
        ├── traces/<tick-slug>.json
        ├── audit/
        │   ├── <tick-slug>.tick.json   # telemetry (Layer 1)
        │   ├── <tick-slug>.full.jsonl  # deep-dump (Layer 2, on-demand)
        │   └── <tick-slug>.summary.md
        ├── obs/logs/<tick-slug>.json
        ├── decisions/<slug>__<TICKER>__<side>.json
        └── report/                     # metrics.md + plots
```

## 16. Manifest status vocabulary

`completed`, `completed_with_failures`, `aborted`.

## 17. Test fixture / scope vocabulary (`tests/backtest/` + `tests/integration/backtest/`)

- `tmp_path` — pytest fixture used everywhere for cache writes (test-policy hard rule).
- `baseline-2025-09` — sole window key used in the end-to-end smoke (test-policy hard rule).
- `tick_limit=1` — smoke convention.
- Sub-packages: `tests/backtest/audit/`, `tests/backtest/leak_regressions/`.

### Leak-regression test names (all in `tests/backtest/leak_regressions/`)
`test_cache_skip_includes_source_provider`, `test_missing_timestamp_marks_row`, `test_open_tick_excludes_sameday_bar`, `test_open_tick_sameday_assertion`, `test_politician_same_day_disclosure_not_visible`, `test_report_cache_logs_originating_as_of`.

### Audit-subpackage test names
`test_auditing_store`, `test_audit_tick_smoke`, `test_telemetry_record_shape`, `test_tripwires`.

### Integration test names
`test_backfill_smoke`, `test_driver_failure_threshold`, `test_driver_one_tick`, `test_end_to_end_smoke`, `test_fetcher_idempotent`, `test_fresh_run_starts_clean`, `test_strict_mode_aborts_on_missing_as_of`.
