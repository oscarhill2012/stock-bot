# ops module vocabulary (2026-05-26)

Exhaustive list of names/terms defined by `src/observability/`,
`src/baselines/`, `src/deploy/`, `src/config/` and the JSON files they
load. One line each.

## Observability — classes and functions

- `TraceWriter` — `src/observability/trace.py:19` — collects labelled JSON sections per tick, finalised to disk.
- `TraceWriter.snapshot` — appends one `{label: payload}` section.
- `TraceWriter.llm_pair` — paired `{label}_in` / `{label}_out` LLM round-trip writer.
- `TraceWriter.finalise` — flushes all sections to a JSON file.
- `TraceWriter.__deepcopy__` / `__copy__` — identity passthrough so ADK session-state deep-copies share one writer.
- `_trace_maybe(state, label, payload, *, state_keys=None)` — `src/observability/trace.py:120` — no-op hook unless `state["temp:_trace"]` is set.
- `_extract_content_text(content)` — concatenate text parts of an ADK `Content`.
- `make_llm_trace_callbacks(section_name, *, model)` — paired before/after model callbacks for LLM trace capture.
- `TickBufferedSpanExporter` — `src/observability/exporters.py:33` — in-memory OTEL span buffer, drains to JSON.
- `TickBufferedSpanExporter.export` / `shutdown` / `force_flush` / `drain_to_file` — SpanExporter contract + drain.
- `_serialise_span(span)` — convert `ReadableSpan` to JSON-safe dict in OTEL GenAI shape.
- `TickBufferedMetricExporter` — `src/observability/exporters.py:170` — in-memory OTEL metric buffer.
- `TickBufferedMetricExporter.export` / `force_flush` / `shutdown` / `drain_to_file` — MetricExporter contract + drain.
- `_serialise_metric_data(metric)` — flatten histogram/sum/gauge data points to JSON-safe dicts.
- `TickBufferedLogHandler` — `src/observability/log_handler.py:27` — stdlib `logging.Handler` buffering records per tick.
- `TickBufferedLogHandler.emit` / `drain_to_file` — append record + flush to JSON.
- `_isoformat_ts(epoch_seconds)` — ISO-8601 UTC timestamp formatter.
- `_extract_extras(record)` — pull `logger.info(..., extra={...})` fields off a `LogRecord`.
- `_STDLIB_LOGRECORD_FIELDS` — frozenset of stdlib `LogRecord` attribute names.
- `AgentLifecycleLogger` — `src/observability/otel_setup.py:44` — `SpanProcessor` that emits one INFO log per closed `invoke_agent` span.
- `ObservabilityHandles` — `src/observability/otel_setup.py:89` — dataclass bundling span exporter, metric exporter, log handler, metric reader.
- `install_observability(*, service_name="stockbot")` — `src/observability/otel_setup.py:120` — idempotent OTEL setup; returns `ObservabilityHandles`.
- `get_handles()` — `src/observability/otel_setup.py:219` — return installed handles or `None`. **No callers — see F-ops-004.**
- `_reset_for_tests()` — `src/observability/otel_setup.py:231` — clear the singleton; test hook.
- `_HANDLES` — process-wide singleton; `ObservabilityHandles | None`.
- `_LIFECYCLE_LOG` — `logging.getLogger("stockbot.lifecycle")`.
- `drain_tick(handles, obs_dir, *, tick_slug, tick_id)` — `src/observability/drain.py:19` — per-tick flush of spans/metrics/logs.
- `_drain_logger` — `logging.getLogger("observability.drain")`.
- `HandleInjectorPlugin` — `src/observability/handle_injector_plugin.py:56` — `BasePlugin` injecting per-invocation observability handles via `before_run_callback`.
- `HandleInjectorPlugin.before_run_callback` — installs `state["temp:_trace"]` and `state["temp:_decision_logger"]`.
- `HandleInjectorPlugin._DEFAULT_NAME` — `"stockbot_handle_injector"`.
- `setup_terminal_logging(level=INFO, *, mode="minimal")` — `src/observability/terminal_log.py:107` — install stderr handler + silence ADK chatter.
- `_TICK_LOGGER` — `"stockbot.tick"` — verbatim-printed logger name.
- `_CALLS_LOGGER` — `"stockbot.tick.calls"` — per-call detail logger (DEBUG).
- `_ADK_NOISY_LOGGERS` — `("google_adk", "google.adk")`.
- `_TickFormatter` — verbatim for `stockbot.tick`, standard format otherwise.
- `format_tokens(n)` / `format_latency(seconds)` — fixed-width formatters.
- `make_observability_callbacks(*, analyst, ticker, ticker_index, ticker_count, model_name)` — `terminal_log.py:284` — paired before/after model callbacks writing `temp:_obs_<analyst>_call_<TICKER>`.
- `emit_analyst_summary(analyst_label, *, calls, ticker_count, retries=None)` — `terminal_log.py:486` — one summary row per analyst per tick.
- `emit_analyst_totals(...)` — `terminal_log.py:663` — legacy compat shim. **No callers — see F-ops-003.**
- `emit_analyst_header(analyst_label, model_name)` — `terminal_log.py:720` — section header line. **No callers — see F-ops-003.**

## Observability — state keys and conventions

- `state["temp:_trace"]` — per-tick `TraceWriter` handle, installed by `HandleInjectorPlugin`.
- `state["temp:_decision_logger"]` — per-tick `DecisionLogger` handle, installed by `HandleInjectorPlugin`.
- `state["temp:_llm_start_<analyst>_<ticker>"]` — per-branch high-resolution start timestamp.
- `state["temp:_obs_<analyst>_call_<TICKER>"]` — per-branch single record dict for summary aggregation.
- `state["temp:_obs_<analyst>_retries"]` — per-tick retry-class counter dict (read by `emit_analyst_summary`).
- (legacy) `state["_trace"]` — bare-key form used only by `scripts/trace_tick.py`. **See F-ops-001.**

## Observability — output artefacts

- `runs/<id>/obs/logs/<tick>.json` — buffered log records flushed by `TickBufferedLogHandler.drain_to_file`.
- `runs/<id>/obs/traces/<tick>.json` — buffered OTEL spans flushed by `TickBufferedSpanExporter.drain_to_file`.
- `runs/<id>/obs/metrics/<tick>.json` — buffered OTEL metric snapshots flushed by `TickBufferedMetricExporter.drain_to_file`.
- `runs/<id>/traces/<tick>.json` — `TraceWriter.finalise` output (per-tick boundary snapshots).
- `runs/<id>/decisions/<tick>.json` — `DecisionLogger` output (owned by `src/backtest/`, mentioned for completeness).

## OTEL terms

- `invoke_agent` — ADK GenAI span name; `AgentLifecycleLogger` triggers on this name only.
- `generate_content` / `execute_tool` / `invoke_workflow` — other ADK GenAI spans (captured, not specially logged).
- `gen_ai.agent.invocation.duration` — ADK histogram.
- `gen_ai.tool.execution.duration` — ADK histogram.
- `gen_ai.agent.request.size` / `gen_ai.agent.response.size` — ADK histograms.
- `gen_ai.agent.workflow.steps` — ADK histogram.
- `gen_ai.agent.name` — span attribute used by `AgentLifecycleLogger`.
- `service.name` — OTEL resource attribute, defaults to `"stockbot"`.
- `SimpleSpanProcessor` — synchronous span processor (chosen over `BatchSpanProcessor` for tick-boundary determinism).
- `PeriodicExportingMetricReader` — periodic metric push, 1-hour interval (safety net; primary path is `force_flush` at drain).
- `Resource` — OTEL resource carrying `service.name`.
- `TracerProvider` / `MeterProvider` — global OTEL providers installed once per process.

## Baselines

- `SPYMetrics` — `src/baselines/spy.py:17` — frozen dataclass: `cumulative_return`, `annualised_return`, `sharpe`, `max_drawdown`, `calmar`. **Only test callers — see F-ops-005.**
- `_metrics_from_series(close)` — `src/baselines/spy.py:25` — compute SPYMetrics from a daily close series. **Only test callers — see F-ops-005.**
- `EquityCurve` — `src/baselines/equity_curve.py:15` — frozen dataclass holding parallel `timestamps`, `bot_pct`, `spy_pct`, `excess_pct` lists and anchor metadata.
- `compute_equity_curve(db_url)` — `src/baselines/equity_curve.py:25` — read `portfolio_snapshots` table, anchor on first row, return `EquityCurve`.
- (used only by `scripts/plot_equity.py`.)

## Config loaders — functions

- `load_analysts_config(*, path=None)` — `src/config/analysts.py:210`.
- `get_analysts_config()` — `src/config/analysts.py:238` — `lru_cache(maxsize=1)`.
- `load_strategist_config(*, path=None)` — `src/config/strategist.py:177`.
- `get_strategist_config()` — `src/config/strategist.py:205` — `lru_cache(maxsize=1)`.
- `load_risk_gate_config(*, path=None)` — `src/config/risk_gate.py:85`.
- `get_risk_gate_config()` — `src/config/risk_gate.py:113` — `lru_cache(maxsize=1)`. (No `_reset_cache` helper — mild inconsistency.)
- `load_retry_429_policy(*, path=None)` — `src/config/retry_429.py:73`.
- `get_retry_429_policy()` — `src/config/retry_429.py:121` — `lru_cache(maxsize=1)`.
- `_reset_cache()` — `src/config/retry_429.py:139` — test hook.
- `load_schedule_config(*, path=None)` — `src/config/schedule.py:117`.
- `get_schedule_config()` — `src/config/schedule.py:145` — `lru_cache(maxsize=1)`.
- `load_models_config(*, path=None)` — `src/config/models.py:83`.
- `get_models_config()` — `src/config/models.py:121` — `lru_cache(maxsize=1)`.
- `_reset_cache()` — `src/config/models.py:141` — test hook.

## Config — Pydantic schemas

- `LlmCaps` — `src/config/analysts.py:35` — `timeout_seconds`, `max_output_tokens`, `timeout_retries`, `schema_retries`.
- `NewsCaps` — `analysts.py:74` — `max_articles_per_ticker`, `max_summary_chars`, `llm: LlmCaps`.
- `FundamentalCaps` — `analysts.py:82` — `max_filing_mda_chars`, `max_filing_risk_chars`, `max_insider_footnotes`, `max_insider_footnote_chars`, `llm: LlmCaps`.
- `CacheSettings` — `analysts.py:92` — `enabled`, `directory`.
- `OutputCaps` — `analysts.py:99` — `verdict_rationale_max_chars`, `verdict_rationale_prompt_headroom_chars`, `report_summary_max_chars`, `report_driver_name_max_chars`, `report_driver_body_max_chars`. Property: `verdict_rationale_prompt_budget`.
- `AnalystsConfig` — `analysts.py:159` — `slack_percent`, `news`, `fundamental`, `output_caps`, `cache`. Method: `schema_cap(prompt_cap)`. **See F-ops-010 (dedupe with strategist).**
- `DecisionCaps` — `strategist.py:69` — `reasoning_max_chars`, `thesis_max_chars`.
- `StanceCaps` — `strategist.py:88` — `rationale_max_chars`.
- `PositionThesisCaps` — `strategist.py:103` — `rationale_max_chars`, `last_review_note_max_chars`.
- `StrategistConfig` — `strategist.py:121` — `slack_percent`, `decision_caps`, `stance_caps`, `position_thesis_caps`, `llm: LlmCaps`. Method: `schema_cap(prompt_cap)`.
- `RiskGateConfig` — `risk_gate.py:34` — `min_held_weight`, `max_position_weight`, `cash_floor_weight`, `max_delta_per_ticker`, `max_total_turnover`, `max_buy_delta_per_trade`.
- `Retry429Policy` — `retry_429.py:46` — `max_attempts`, `base_delay_seconds`, `max_delay_seconds`.
- `ScheduleConfig` — `schedule.py:32` — `ticks_per_day`, `tick_times_et: list[str]`, `comment`. Validators on time-string format and length match.
- `ModelsConfig` — `models.py:45` — `strategist`, `news_analyst`, `fundamental_analyst`, `memory_compressor`, `memory_embedding`.

## Config files (project root `config/`) — keys defined

- `config/analysts.json` — `slack_percent`, `news.{max_articles_per_ticker, max_summary_chars, llm.{timeout_seconds, max_output_tokens, timeout_retries, schema_retries}}`, `fundamental.{max_filing_mda_chars, max_filing_risk_chars, max_insider_footnotes, max_insider_footnote_chars, llm.*}`, `output_caps.{verdict_rationale_max_chars, verdict_rationale_prompt_headroom_chars, report_summary_max_chars, report_driver_name_max_chars, report_driver_body_max_chars}`, `cache.{enabled, directory}`.
- `config/strategist.json` — `slack_percent`, `decision_caps.{reasoning_max_chars, thesis_max_chars}`, `stance_caps.rationale_max_chars`, `position_thesis_caps.{rationale_max_chars, last_review_note_max_chars}`, `llm.*`.
- `config/risk_gate.json` — `min_held_weight`, `max_position_weight`, `cash_floor_weight`, `max_delta_per_ticker`, `max_total_turnover`, `max_buy_delta_per_trade`.
- `config/retry_429.json` — `_comment`, `max_attempts`, `base_delay_seconds`, `max_delay_seconds`.
- `config/schedule.json` — `ticks_per_day`, `tick_times_et`, `comment`.
- `config/models.json` — `_comment`, `strategist`, `news_analyst`, `fundamental_analyst`, `memory_compressor`, `memory_embedding`.
- `config/watchlist.json` — `tickers` (loader out of scope).
- `config/watchlist_smoke.json` — same shape, undocumented in `config/README.md` (**F-ops-011**).
- `config/data.json` / `config/analyst_heuristics.json` / `config/backtest_windows.json` / `config/backtest_settings.json` — loaders live outside `src/config/`; mentioned for completeness.

## Module paths and defaults

- `_DEFAULT_PATH` (per loader) — `Path("config/<file>.json")` relative to CWD (PYTHONPATH=src convention).

## Env vars / constants observed

- No env vars are read by `src/observability/` or `src/config/`. (No `os.getenv` calls.)
- Service name default: `"stockbot"` (`install_observability`).
- Metric export interval: `3_600_000` ms (1 hour).
- Log handler default level: `logging.DEBUG`.

## Deploy

- `src/deploy/` — empty. No symbols. **See F-ops-002.**
