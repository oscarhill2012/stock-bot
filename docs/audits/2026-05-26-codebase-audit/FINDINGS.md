# StockBot codebase audit — final findings

_2026-05-26 — assembled from 11 module reports + cross-module dedupe + test-strategy review._

## Index by severity

- **P0 (15)**: A-001 risk_gate silent-skip; A-002 risk_gate cannot price unheld BUY; A-003 T212 `await resp.json()` bug; A-004 T212 silently drops unknown instruments; A-005 FakeBroker `_prices` leak into prod; A-006 Snapshotter SPY-fetch silent zero; A-007 Finnhub social-sentiment soft-fail; A-008 Executor after-callback `print`-swallows AssertionError; A-009 Live `as_of` raw datetime; A-010 Live `run_once` no `HandleInjectorPlugin`; A-011 `_STOCKBOT_TABLES` only 3 of 6; A-012 `scripts/trace_tick.py` bare `_trace`; A-013 prose-field proliferation; A-014 bare `state["positions"]` external readers; A-015 three no-data verdict synthesis sites with drift.
- **P1 (32)**: A-016 deterministic extractors synthesise AnalystReport; A-017 `_NO_RISK_GATE_INTENTS` stale-verb; A-018 §A.7 violations cluster; A-019 no `is_no_data=False` happy-path asserts; A-020 tests cement bugs; A-021 smart_money pipeline contradiction; A-022 smart_money Rule 1 violation; A-023 `_strategist_validation_callback` dead; A-024 strategist legacy-callback test cluster; A-025 `evidence_view.py` dead; A-026 evidence_view test cluster; A-027 `build_strategist_enricher` dead; A-028 empty `attribution/`; A-029 memory DI setters dead; A-030 BufferEntryRow shell; A-031 snapshot test patches wrong target; A-032 `_is_schema_error` ImportError silent downgrade; A-033 smart_money fan-out tests; A-034 `_close_tickers` post-clamp restoration; A-035 `_REFERENCE_SYMBOLS` triple-defined; A-036 4 unused provider modules + ~975 LoC tests; A-037 `news.alpha_vantage` dead; A-038 `company_ratios.yfinance` duplicate registration; A-039 reversed-window silent `[]`; A-040 missing API key silent `[]`; A-041 `set_active_provider` accepts unregistered; A-042 `MemoryProjection` dead; A-043 `cache_capture` vs `AuditingStore` dual; A-044 driver `except RuntimeError: pass` quartet; A-045 redundant `(AttributeError, Exception)`; A-046 `_seed_initial_prices` 0.0 default; A-047 `HandleInjectorPlugin` lifecycle parity.
- **P2 (35)**: A-048 `headline_polarity_mean_7d` alias; A-049 verdict.rationale/report.summary overlap; A-050 `digest._fill_missing` silent neutral-fill; A-051 TickerVerdict/LlmTickerVerdict two-shape; A-052 invariants-doc carve-out test; A-053 `feature_warnings` unpopulated; A-054 insider legacy vs flat-list; A-055 `last_price` `None` vs `0.0` sentinels; A-056 clamp-order disagreement; A-057 `risk_gate_agent` singleton dead; A-058 `apply_buy_delta_clamp` over-abstraction; A-059 risk_gate test four-file dup; A-060 risk_gate fixtures use deleted fields; A-061 stale three-verb comment; A-062 executor `intent="open"` test; A-063 legacy thesis fixture keys; A-064 `resolve_broker_call` dead; A-065 idempotency-guard after-callback gap; A-066 rejection test asserts only status; A-067 `tests/executor/` schism; A-068 fill_prices redundant lookups; A-069 executor paired direct write; A-070 §A doc fix for bare positions; A-071 two `_coerce_portfolio` copies; A-072 three mid-tick `get_portfolio` calls; A-073 triple direct-write + state_delta; A-074 deterministic analyst singletons; A-075 `DOMAINS` frozenset duplicated; A-076 `schema_cap` duplicated; A-077 `_audit_capture_enabled` single-caller; A-078 verifiers hard-code `agreement_with_cache=True`; A-079 `test_end_to_end_smoke.py` mega-file; A-080 `last_snapshot` vs `last_executed_tick_id` parallel; A-081 live `BaseException` swallow; A-082 7 dormant data schemas.
- **P3 (15)**: A-083 `headline_polarity_mean` alias nit; A-084 `_git_sha7`/`_git_sha_full`; A-085 `build_telemetry_record_from_logs` orphan; A-086 `state["thesis"]`/`user:thesis` residue; A-087 `TickState` unused; A-088 `_dispatch_app_name` over-abstraction; A-089 `BrokerMode._value2member_map_` private access; A-090 `lifecycle/scheduler.py` Cloud Scheduler shells; A-091 `_check_live_tables_empty` Postgres `public.`; A-092 `BUFFER_MAX` unused; A-093 triple structured-log pattern; A-094 `_has_real_smart_money` over-abstraction; A-095 `log_cache_hit_to_state` no-op; A-096 `report_cache.py` importlib gymnastics; A-097 misc minor nits (~25 collected).

**Total: 97 findings (15 P0 / 32 P1 / 35 P2 / 15 P3).**

## How to read this

Each finding lists `Origin:` underlying report IDs. Open those files under `docs/audits/2026-05-26-codebase-audit/` for full evidence / greps / snippets.

## Findings

### A-001 — risk_gate silent-skip on missing strategist_decision
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/agents/risk_gate/agent.py:49-51`
- **Evidence:** Bare `return` when `strategist_decision` missing/falsy — no event, no log, no `final_orders` key.
- **Intent violated:** §2.4; test-policy §A.7.
- **Suggested action:** investigate (raise or yield empty final_orders + warning).
- **Origin:** F-risk_gate-001, F-risk_gate-011 (gap).

### A-002 — risk_gate cannot price an unheld BUY in live
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/agents/risk_gate/orders.py:31-32`; `src/agents/risk_gate/agent.py:100-104`
- **Evidence:** Prices built only from currently-held positions plus `hasattr(broker, "_prices")` FakeBroker-only fallback. `state["reference_prices"]` never read. First live BUY of an unheld watchlist ticker crashes the tick.
- **Intent violated:** §2.4; §A reference_prices row.
- **Suggested action:** refactor — fall back to `state["reference_prices"]`.
- **Origin:** F-risk_gate-002, F-broker-001.

### A-003 — Trading212 `await resp.json()` runtime bug
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/broker/trading212.py:58,77,92,100`
- **Evidence:** `httpx.Response.json()` is sync; awaiting a `dict` raises `TypeError`. Tests pass because `AsyncMock.json` is async — cementing the bug.
- **Suggested action:** refactor (drop `await`; rewrite test with `MagicMock`).
- **Origin:** F-broker-003, F-broker-008.

### A-004 — Trading212 `get_portfolio` silently drops unknown instruments
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/broker/trading212.py:104-113`
- **Evidence:** Positions not in `instrument_map` silently `continue`d. Concentration clamps + BUY→SELL bridge see smaller portfolio than reality.
- **Intent violated:** §A portfolio row.
- **Suggested action:** refactor (log per drop, or raise).
- **Origin:** F-broker-002, F-broker-009 (gap).

### A-005 — FakeBroker `_prices` private channel leaks into production
- **Severity:** P0 · **Category:** policy-mismatch
- **Locations:** `src/agents/risk_gate/agent.py:101-104`; `src/broker/fake.py:21`
- **Evidence:** Production risk_gate reads `self.broker._prices` via hasattr — a private test injection point with no T212 equivalent.
- **Intent violated:** §D3, §2.10.
- **Suggested action:** refactor (read from `state["reference_prices"]`).
- **Origin:** F-broker-001, F-broker-006.

### A-006 — Snapshotter SPY-fetch silently substitutes `spy_price=0.0`
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/agents/snapshot/agent.py:60-74`
- **Evidence:** Bare `except Exception: spy_price = 0.0` no log/warn. First-tick anchor at 0.0 permanently breaks every return calc.
- **Suggested action:** refactor (log WARNING `exc_info`; consider raising on anchor).
- **Origin:** F-agents-misc-006, F-agents-misc-007.

### A-007 — Finnhub social-sentiment soft-fails to empty
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/data/providers/social_sentiment/finnhub.py:79-85`
- **Evidence:** Every `FinnhubAPIException` (auth/429/server) returns `SocialSentiment(snapshots=[], aggregate_score=0.0)`, indistinguishable from "no mentions" downstream.
- **Suggested action:** refactor (raise on 4xx except documented premium-gate).
- **Origin:** F-data-004, F-data-013.

### A-008 — Executor after-callback `print`-swallows AssertionError
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/agents/executor/agent.py:510-526`
- **Evidence:** `_executor_thesis_writer_callback` uses `print(file=sys.stderr)` (not `logger`), bypassing structured log and `caplog`. BUY with `fill_price=None` silently drops thesis update.
- **Suggested action:** refactor (`logger.error(..., exc_info=True)` + caplog test).
- **Origin:** F-executor-001, F-executor-006.

### A-009 — Live `as_of` written as raw `datetime` (lifecycle divergence)
- **Severity:** P0 · **Category:** cross-lifecycle
- **Locations:** `src/orchestrator/tick.py:148`; cf `src/backtest/driver.py:545-550`
- **Evidence:** Live `_build_initial_state` passes a `datetime` to `DatabaseSessionService`; backtest ISO-strings first. Memory policy: every datetime write to state must ISO-stringify. Test `test_tick_as_of_phase.py:48-50` cements broken behaviour.
- **Suggested action:** refactor (ISO-coerce in `_build_initial_state`; rewrite cementing test).
- **Origin:** F-orch-001, D-022, F-orch-009, T-005, T-008.

### A-010 — Live `run_once` never installs `HandleInjectorPlugin`
- **Severity:** P0 · **Category:** cross-lifecycle
- **Locations:** `src/orchestrator/tick.py:229-247` vs `src/backtest/driver.py:517-527`
- **Evidence:** Backtest installs the plugin (because direct mutation after `create_session` is silently discarded); live does neither. Intent §2.9's "direct mutation" description is stale.
- **Suggested action:** refactor (shared runner-builder helper).
- **Origin:** F-orch-002, D-023.

### A-011 — `_STOCKBOT_TABLES` lists only 3 of 6 ORM tables
- **Severity:** P0 · **Category:** silent-failure
- **Locations:** `src/lifecycle/initialise.py:21`; `src/lifecycle/hard_reset.py:17`
- **Evidence:** Tuple covers `buffer_entries`, `trade_log`, `portfolio_snapshots`; ORM declares six. Preflight passes when stale rows exist; hard_reset leaves them on Postgres. `tests/unit/test_init_db_script.py` cements stale set.
- **Suggested action:** refactor (derive from `Base.metadata.tables.keys()`; rewrite test).
- **Origin:** F-orch-004, D-021, F-orch-010, T-005.

### A-012 — `scripts/trace_tick.py` uses bare-key `_trace`
- **Severity:** P0 · **Category:** silent-failure · **Human gate**
- **Locations:** `scripts/trace_tick.py:118-160`
- **Evidence:** Bare `"_trace"` (not `temp:_trace`) handle install after `create_session` — exact pattern `HandleInjectorPlugin` was created to replace. Works under `InMemorySessionService` only; silently empties under `DatabaseSessionService`.
- **Suggested action:** investigate (migrate to plugin or delete — human-gated).
- **Origin:** F-ops-001.

### A-013 — Prose-field proliferation (rationale cluster)
- **Severity:** P0 · **Category:** vocabulary-collision
- **Locations:** `src/contract/{schemas.py,stance_schema.py,position_thesis.py}`; `src/agents/strategist/{schema,derivation}.py`; `src/agents/executor/_verb_dispatch.py:235,251,291,311,323`
- **Evidence:** Seven schema fields + one prompt slot carry "why": `AnalystVerdict.rationale`, `AnalystReport.summary`, `TickerStance.rationale`, `PositionThesis.{rationale,last_reviewed_reason}`, `StrategistDecision.{sell_reasons,update_reasons}`, prompt `{reasoning}`. `last_reviewed_reason` derived byte-identically from `stance.rationale`; reasons dicts derived from per-ticker stances.
- **Intent violated:** §3.2 cluster 1.
- **Suggested action:** investigate (single rationale, derive everywhere else).
- **Origin:** D-001, F-strategist-011, F-contract-004.

### A-014 — Bare `state["positions"]` read by external consumers
- **Severity:** P0 · **Category:** vocabulary-collision
- **Locations:** `src/agents/strategist/context_shim.py:153,229`; `src/backtest/decision_logger.py:336-340`; `src/backtest/runner.py`
- **Evidence:** §7.3 makes bare-key positions executor-internal-only. External readers retain `state.get("user:positions") or state.get("positions")` fallback; `decision_logger.py:339` reads bare key exclusively. Remove bridge → `held_view_at_decision` silently null.
- **Suggested action:** consolidate (drop bare-key fallback; read `user:positions`).
- **Origin:** D-002, F-executor-004, F-strategist-007, F-backtest-005.

### A-015 — Three sites synthesise no-data verdicts with drift
- **Severity:** P0 · **Category:** dedupe / silent-failure
- **Locations:** `src/agents/analysts/_common.py:148-158`; news/fundamental joiners; `src/contract/extractors.py`; `src/agents/strategist/derivation.py`
- **Evidence:** Three sites independently synthesise; each picks own wording/confidence (0.0 vs None vs 0.5)/direction. Same tick presents three different prose strings.
- **Suggested action:** consolidate — single `build_no_data_verdict(ticker, *, reason)`.
- **Origin:** D-014, F-analysts-015, F-contract-005, F-strategist-006.

### A-016 — Deterministic extractors synthesise `AnalystReport` · **Human gate**
- **Severity:** P1 · **Category:** policy-mismatch
- **Locations:** `src/contract/extractors/{social,technical,smart_money}.py`
- **Evidence:** `_report_required_when_data_present` forces every non-no-data verdict to carry a report; deterministic extractors fabricate prose, contradicting intent §2.1/§2.6.
- **Suggested action:** investigate (exempt deterministic analysts, or update intent + renderer).
- **Origin:** F-contract-001.

### A-017 — `_NO_RISK_GATE_INTENTS` stale-verb set lets `no_action` slip
- **Severity:** P1 · **Category:** vocabulary-collision
- **Locations:** `src/agents/risk_gate/agent.py:21,75-86`
- **Evidence:** Set is `{"hold","update"}`; canonical four-verb set is `{buy,sell,update,no_action}`. `no_action` stances on held tickers survive into clamping → surprise SELL. Unit test pins stale set.
- **Suggested action:** refactor (swap to `{update,no_action}`; rewrite test).
- **Origin:** D-007, F-risk_gate-003, F-risk_gate-009, F-risk_gate-013, T-005.

### A-018 — Test-policy §A.7 widely violated ("did it raise?" tests)
- **Severity:** P1 · **Category:** test-gap
- **Locations:** 14+ files incl. `tests/unit/test_tick_entrypoint.py`, `test_memory_writer_agent.py`, `test_tick_state.py`, `tests/integration/test_executor_with_fake_broker.py::test_executor_rejection_continues`, `test_snapshotter.py`, `tests/unit/contract/test_evidence.py`.
- **Suggested action:** refactor (add content assertions or delete).
- **Origin:** T-001 (subsumes F-orch-007, F-orch-015, F-agents-misc-010, F-executor-010, F-agents-misc-007, F-contract-008, F-contract-009, F-analysts-013).

### A-019 — No happy-path `is_no_data=False` assertions
- **Severity:** P1 · **Category:** test-gap
- **Locations:** every joiner test, every digest test, every pipeline smoke.
- **Evidence:** Grep finds zero `assert not v.is_no_data` invariants on happy paths.
- **Suggested action:** refactor (shared `assert_no_silent_degradation(state)` fixture).
- **Origin:** T-002.

### A-020 — Tests encode buggy behaviour
- **Severity:** P1 · **Category:** test-gap
- **Locations:** `test_trading212_request_construction.py`; `test_news_tiingo.py` (+ politician variants); `test_tick_as_of_phase.py:48-50`; `test_init_db_script.py`; `test_risk_gate.py::test_no_risk_gate_intents_constant_contains_hold_and_update`.
- **Suggested action:** refactor (rewrite in the same patch as each bug fix).
- **Origin:** T-005.

### A-021 — Smart-money pipeline-wiring contradiction · **Human gate**
- **Severity:** P1 · **Category:** policy-mismatch
- **Locations:** `src/orchestrator/pipeline.py:82-93`; `src/agents/analysts/smart_money/`
- **Evidence:** Intent §7.1 (authoritative) says smart_money is registered and runs every tick. Source has line commented out. Worst-of-both (live module + defensive consumers + dead pipeline slot).
- **Suggested action:** investigate (re-enable, or revise §7.1 + delete module/tests/consumers).
- **Origin:** F-analysts-001.

### A-022 — Smart-money analyst writes verdicts without `state_delta`
- **Severity:** P1 · **Category:** policy-mismatch
- **Locations:** `src/agents/analysts/smart_money/agent.py:153`
- **Evidence:** Direct `state["smart_money_verdicts"] = ...` no event. Also writes bare `smart_money_data` key (no `temp:` prefix).
- **Suggested action:** refactor — conditional on A-021.
- **Origin:** F-analysts-005, F-analysts-006, D-005.

### A-023 — `_strategist_validation_callback` dead in production
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/strategist/agent.py:54-90`
- **Evidence:** §7.2 (authoritative) — callback survives only as legacy-test delegate. `StrategistEnricher` is sole live path.
- **Suggested action:** delete.
- **Origin:** F-strategist-001.

### A-024 — Strategist legacy-callback test cluster (5 files)
- **Severity:** P1 · **Category:** dead-test
- **Locations:** `tests/integration/test_strategist_minimal_schema_no_retry.py`; `tests/integration/backtest/test_end_to_end_smoke.py:390-408`; `test_fresh_run_starts_clean.py:161-190,261`; `tests/unit/agents/strategist/test_validation_callback.py`; `test_strategist_callbacks_v2.py`.
- **Suggested action:** delete-or-port off-watchlist/bad-rationale to `test_enricher.py`.
- **Origin:** F-strategist-002, T-103.

### A-025 — `evidence_view.py` dead module
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/strategist/evidence_view.py`
- **Evidence:** Only test-side imports; live renderer is `render_all_ticker_blocks`.
- **Suggested action:** delete.
- **Origin:** F-strategist-004.

### A-026 — `evidence_view` test cluster (3 files)
- **Severity:** P1 · **Category:** dead-test
- **Locations:** `tests/unit/agents/strategist/test_evidence_view*.py` (3 files).
- **Suggested action:** delete (port any load-bearing assertion).
- **Origin:** F-strategist-005.

### A-027 — `build_strategist_enricher` dead factory
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/strategist/enricher.py:357-362`
- **Suggested action:** delete.
- **Origin:** F-strategist-008.

### A-028 — Empty `src/agents/attribution/` directory
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/attribution/`; stale refs in `strategist/decision_writer.py:60` and `contract/evidence_writer.py:68`.
- **Suggested action:** delete directory; correct docstring refs.
- **Origin:** F-agents-misc-001.

### A-029 — Memory DI setters never called
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/memory/embeddings.py:6-13`; `src/agents/memory/compress.py:9-15`
- **Evidence:** Zero callers; tests use monkeypatch directly.
- **Suggested action:** delete.
- **Origin:** F-agents-misc-004.

### A-030 — Unwired `BufferEntryRow` persistence shell · **Human gate**
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/orchestrator/persistence.py:27-79`
- **Evidence:** `BufferEntryRow`/`save_buffer_entry`/`load_recent_buffer` only test consumer; Spec C deferred.
- **Suggested action:** investigate (keep as Spec C scaffolding or remove).
- **Origin:** F-agents-misc-005, F-agents-misc-009.

### A-031 — Snapshotter integration test patches wrong target
- **Severity:** P1 · **Category:** test-gap
- **Locations:** `tests/integration/test_snapshotter.py:26-44`
- **Evidence:** Patches `yfinance.Ticker`; production calls `data.get_price_history`. Patch is no-op; masks A-006.
- **Suggested action:** refactor (repoint to `data.get_price_history`; assert `spy_price > 0`).
- **Origin:** F-agents-misc-007, T-004.

### A-032 — `_is_schema_error` ImportError silent downgrade
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/agents/llm_retry.py:170-175,206-211,246-272`
- **Evidence:** `except ImportError: return False` on `pydantic` — a hard dep. Silently downgrades every schema retry to "not retryable".
- **Suggested action:** refactor (drop guard).
- **Origin:** F-agents-misc-015.

### A-033 — Smart-money fan-out test cluster (5 files)
- **Severity:** P1 · **Category:** dead-test (conditional on A-021)
- **Locations:** `tests/analysts/test_smart_money.py`; `test_smart_money_fetch.py`; `test_smart_money_gate.py`; `test_derive_smart_money_verdict.py`; `tests/agents/memory/test_writer_smart_money_seen.py`.
- **Suggested action:** delete if A-021 → shelved.
- **Origin:** F-analysts-003, T-104.

### A-034 — risk_gate `_close_tickers` post-clamp restoration distorts telemetry
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/agents/risk_gate/agent.py:119-135`
- **Evidence:** `proposed[_t] = 0.0` overwritten after clamps computed; telemetry shows clamps that didn't constrain output. No re-run of `apply_constraints` after override.
- **Suggested action:** investigate.
- **Origin:** F-risk_gate-005.

### A-035 — `_REFERENCE_SYMBOLS` defined in three files
- **Severity:** P1 · **Category:** dedupe
- **Locations:** `scripts/backtest_fetch.py:379`; `src/backtest/runner.py`; `src/orchestrator/tick.py:62`.
- **Suggested action:** consolidate (single `src/data/_reference_symbols.py`).
- **Origin:** D-016.

### A-036 — Phase-3 unused providers (4 modules, ~975 LoC tests)
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/data/providers/{earnings/finnhub.py,analyst_consensus/yfinance.py,short_interest/finra.py,options/yfinance.py}` + models + tests.
- **Evidence:** Zero consumers; registered + listed in `config/data.json` but produce nothing downstream. Not in §7.4's authoritative 8-domain count.
- **Suggested action:** delete (modules, models, registry, config, tests).
- **Origin:** F-data-001, F-data-016, T-105, D-026.

### A-037 — `news.alpha_vantage` dead provider
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/data/providers/news/alpha_vantage.py` (757 LoC); `tests/unit/data/providers/test_news_alpha_vantage_as_of.py`.
- **Evidence:** `news.finnhub` active; tiingo is legitimate fallback. AV's `sentiment` field intentionally unused per memory. Makes `NewsArticle.sentiment`/`.relevance` write-never-read-never.
- **Suggested action:** delete.
- **Origin:** F-data-003, F-data-015, F-data-020.

### A-038 — `company_ratios.yfinance` duplicate registration
- **Severity:** P1 · **Category:** dedupe
- **Locations:** `src/data/providers/stats/yfinance.py:527-572`.
- **Evidence:** Documented "unsuitable for backtests" but registered as alt to `pit_composite`. No swap call site.
- **Suggested action:** investigate (delete or doc as live-degraded fallback).
- **Origin:** F-data-002.

### A-039 — News providers silently `return []` on reversed window
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/data/providers/news/finnhub.py:344-359`; `news/alpha_vantage.py:340-344`.
- **Suggested action:** refactor (raise `ValueError`).
- **Origin:** F-data-006.

### A-040 — Providers `return []` on missing API key
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `news/tiingo.py:147-150`; `politician_trades/{quiver.py:153-158,fmp.py:251-258}`. (`news/alpha_vantage.py:316` uses `require_key()` correctly.)
- **Suggested action:** refactor (raise `SecretMissingError`; rewrite cementing tests).
- **Origin:** F-data-005, F-data-014.

### A-041 — `set_active_provider` accepts unregistered names
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/data/registry.py:196-235`.
- **Evidence:** Runtime swap accepts any string; only catches typo at next dispatch. Violates §2.7.
- **Suggested action:** refactor (assert membership).
- **Origin:** F-data-007.

### A-042 — `MemoryProjection` dead class
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/agents/memory/schema.py:22-38`.
- **Suggested action:** investigate (delete or acknowledge as Spec C scaffolding alongside A-030).
- **Origin:** F-agents-misc-003, F-agents-misc-008.

### A-043 — `cache_capture` vs `AuditingStore` dual mechanisms
- **Severity:** P1 · **Category:** dedupe / over-abstraction
- **Locations:** `src/backtest/cache/store.py:858-902` vs `src/backtest/audit/auditing_store.py:16-210`.
- **Evidence:** Two independent row-capture mechanisms; same shape, same call sites, same data.
- **Suggested action:** investigate (pick one).
- **Origin:** F-backtest-001, D-017.

### A-044 — Driver `except RuntimeError: pass` quartet
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/backtest/driver.py:203-208,315-319,350-355,690-694`.
- **Evidence:** Four "store not wired" guards exist for unit tests; in prod silently skip reference-prices seed/audit drain/broker refresh.
- **Suggested action:** refactor (log on guard fire, or explicit "no store" mode).
- **Origin:** F-backtest-002.

### A-045 — Driver `(AttributeError, Exception)` redundant tuple
- **Severity:** P1 · **Category:** dead-code
- **Locations:** `src/backtest/driver.py:599`.
- **Suggested action:** refactor (delete `AttributeError,`).
- **Origin:** F-backtest-003.

### A-046 — `_seed_initial_prices` defaults to 0.0 for missing OHLCV
- **Severity:** P1 · **Category:** silent-failure
- **Locations:** `src/backtest/runner.py:149-186`.
- **Evidence:** Watchlist ticker with no bars silently → 0.0; FakeBroker accepts zero-priced BUYs.
- **Suggested action:** refactor (raise on missing bars).
- **Origin:** F-backtest-004.

### A-047 — `HandleInjectorPlugin` lifecycle parity
- **Severity:** P1 · **Category:** cross-lifecycle
- **Locations:** `src/orchestrator/tick.py` (absent) vs `src/backtest/runner.py`.
- **Suggested action:** consolidate (shared runner-builder helper).
- **Origin:** D-023 (overlaps A-010).

### A-048 — `headline_polarity_mean_7d` alias duplicates value
- **Severity:** P2 · **Category:** dedupe
- **Locations:** `src/contract/extractors/news.py:29-30`.
- **Evidence:** Only `_7d` suffix has downstream reader (`strategist_prompt.py:377`).
- **Suggested action:** delete non-suffixed alias.
- **Origin:** F-contract-003, D-018.

### A-049 — `AnalystVerdict.rationale` vs `report.summary` overlap
- **Severity:** P2 · **Category:** dedupe / silent-failure
- **Locations:** `src/contract/evidence.py:109-156` + deterministic extractors.
- **Suggested action:** consolidate (drop one; pair with A-013).
- **Origin:** F-contract-004.

### A-050 — `digest._fill_missing` silent neutral-fill
- **Severity:** P2 · **Category:** silent-failure
- **Locations:** `src/contract/digest.py:69-90`.
- **Evidence:** Missing analyst slot → `is_no_data=True, report=None` legally; with A-016 synthetic reports observers can't distinguish. Per §7.1 missing slot = pipeline bug.
- **Suggested action:** refactor (structured warning; possibly raise).
- **Origin:** F-contract-005.

### A-051 — `TickerVerdict`/`LlmTickerVerdict` two-shape pattern
- **Severity:** P2 · **Category:** schema-duplication
- **Locations:** `src/contract/evidence.py:158-266`; `src/agents/strategist/schema.py`.
- **Suggested action:** investigate (LooseToStrict mixin).
- **Origin:** D-006, F-contract-006, F-strategist-009.

### A-052 — Invariants-doc carve-out test pins doc text
- **Severity:** P2 · **Category:** dead-test
- **Locations:** `tests/unit/contract/test_invariants_doc_carveout.py:14-34`.
- **Evidence:** Asserts presence of `_strategist_validation_callback` carve-out + refs `docs/Phase8-contract-audit-fixes/contract-audit.md` (forbidden). Will fail on §7.2 doc-fix.
- **Suggested action:** delete (after A-023).
- **Origin:** F-contract-007, F-strategist-010.

### A-053 — `feature_warnings` declared but never populated
- **Severity:** P2 · **Category:** dead-code
- **Locations:** `src/contract/evidence.py:303`.
- **Suggested action:** investigate (wire emission or delete field + column).
- **Origin:** F-contract-010, F-contract-009.

### A-054 — Insider extractor legacy vs flat-list paths
- **Severity:** P2 · **Category:** dead-code
- **Locations:** `src/contract/extractors/fundamental.py:344-405,481-577`.
- **Suggested action:** investigate (retire legacy if no live producer).
- **Origin:** F-contract-011.

### A-055 — `TickerEvidence.last_price` None vs 0.0 sentinels
- **Severity:** P2 · **Category:** policy-mismatch / silent-failure
- **Locations:** `src/contract/ticker_evidence.py:50-63`.
- **Suggested action:** refactor (`PositiveFloat | None`).
- **Origin:** F-contract-013.

### A-056 — risk_gate clamp-order disagreement · **Human gate**
- **Severity:** P2 · **Category:** policy-mismatch
- **Locations:** `src/agents/risk_gate/constraints.py:170-176`.
- **Evidence:** Source order differs from intent §2.4. No behavioural impact today.
- **Suggested action:** investigate (reconcile authoritative ordering).
- **Origin:** F-risk_gate-004.

### A-057 — `risk_gate_agent` module-level singleton dead
- **Severity:** P2 · **Category:** dead-code
- **Locations:** `src/agents/risk_gate/agent.py:186`.
- **Suggested action:** delete.
- **Origin:** F-risk_gate-006.

### A-058 — `apply_buy_delta_clamp` two-call structure
- **Severity:** P2 · **Category:** over-abstraction
- **Locations:** `src/agents/risk_gate/constraints.py:32-80`.
- **Suggested action:** investigate (fold into `apply_constraints`).
- **Origin:** F-risk_gate-008.

### A-059 — risk_gate four-file test duplication
- **Severity:** P2 · **Category:** test-consolidation
- **Locations:** `tests/unit/orchestrator/test_risk_gate.py` + `tests/unit/agents/risk_gate/test_agent.py` + `tests/integration/test_risk_gate_agent.py` + `tests/integration/test_risk_gate_state_delta.py`.
- **Suggested action:** consolidate.
- **Origin:** F-risk_gate-010, T-108.

### A-060 — risk_gate fixtures use deleted `thesis`/`close_reasons`
- **Severity:** P2 · **Category:** dead-test
- **Locations:** `tests/integration/test_risk_gate_agent.py:24-32`; `test_risk_gate_state_delta.py:55-65`.
- **Suggested action:** refactor (use `sell_reasons`/`update_reasons`).
- **Origin:** F-risk_gate-012.

### A-061 — Stale "three-verb schema" comment
- **Severity:** P2 · **Category:** dead-code (doc)
- **Locations:** `src/agents/risk_gate/agent.py:21`.
- **Suggested action:** refactor (comment refresh).
- **Origin:** F-risk_gate-013.

### A-062 — Executor decision-hook test uses `intent="open"`
- **Severity:** P2 · **Category:** dead-test
- **Locations:** `tests/unit/agents/test_executor_decision_hook.py:78-92`.
- **Evidence:** Invalid verb under four-verb schema with `extra="forbid"`; passes via BUY broker path without validation.
- **Suggested action:** delete-or-rewrite.
- **Origin:** F-executor-002.

### A-063 — Legacy thesis fixture keys
- **Severity:** P2 · **Category:** dead-test
- **Locations:** `tests/executor/test_executor_bookkeeping.py:40-52`; `tests/unit/executor/test_open_positions_state.py:161-170,205-212`; `tests/unit/agents/test_executor_decision_hook.py:163-170`.
- **Evidence:** Fixtures carry `horizon`/`target_price`/`stop_price`/`last_review_note` — `PositionThesis` forbids them.
- **Suggested action:** refactor.
- **Origin:** F-executor-003.

### A-064 — `resolve_broker_call` zero callers
- **Severity:** P2 · **Category:** dead-code
- **Locations:** `src/agents/executor/_verb_dispatch.py:84-141`.
- **Suggested action:** delete (with four tests).
- **Origin:** F-executor-007.

### A-065 — Executor idempotency-guard misses after-callback
- **Severity:** P2 · **Category:** test-gap
- **Locations:** `tests/integration/test_executor_with_fake_broker.py::test_executor_idempotent`.
- **Suggested action:** investigate (extend guard; Runner-driven double-invocation test).
- **Origin:** F-executor-008, T-207.

### A-066 — Executor rejection test asserts only `status`
- **Severity:** P2 · **Category:** test-gap
- **Locations:** `tests/integration/test_executor_with_fake_broker.py:138-153`.
- **Suggested action:** refactor (augment).
- **Origin:** F-executor-010.

### A-067 — `tests/executor/` vs `tests/unit/executor/` schism
- **Severity:** P2 · **Category:** test-consolidation
- **Suggested action:** consolidate per test-policy §B taxonomy.
- **Origin:** F-executor-011, T-101.

### A-068 — Executor `fill_prices` redundant lookups
- **Severity:** P2 · **Category:** silent-failure
- **Locations:** `src/agents/executor/agent.py:447-457`.
- **Evidence:** `row["stance"]["ticker"]` never written; `row.get("fill_price") or row.get("actual_price")` accepts both spellings; rejected rows → `fill_prices[ticker] = None` triggering A-008.
- **Suggested action:** refactor.
- **Origin:** F-executor-006.

### A-069 — Executor `last_executed_tick_id` paired direct write
- **Severity:** P2 · **Category:** dedupe
- **Locations:** `src/agents/executor/agent.py:317-393`.
- **Evidence:** Direct mutation + state_delta double-write for `executions`/`last_executed_tick_id`/`positions` — only `positions` needs in-tick visibility.
- **Suggested action:** refactor (drop direct writes for executions/tick_id; retain positions bridge).
- **Origin:** F-executor-009.

### A-070 — Doc-fix: §A row for executor-internal bare `positions`
- **Severity:** P2 · **Category:** policy-mismatch (doc)
- **Suggested action:** add §A row per §7.3.
- **Origin:** F-executor-005.

### A-071 — Two `_coerce_portfolio` copies
- **Severity:** P2 · **Category:** dedupe
- **Locations:** `src/agents/strategist/context_shim.py:55-72` vs `enricher.py:73-89`.
- **Suggested action:** consolidate (private helper or `Portfolio` classmethod).
- **Origin:** D-008, F-strategist-003.

### A-072 — Three mid-tick `broker.get_portfolio()` calls
- **Severity:** P2 · **Category:** policy-mismatch / dedupe
- **Locations:** `risk_gate/agent.py:100-104`; `executor/agent.py:205`; `snapshot/agent.py:38`.
- **Suggested action:** investigate (lean on `state["portfolio"]`).
- **Origin:** D-009, F-broker-004.

### A-073 — Triple direct-write + state_delta pattern
- **Severity:** P2 · **Category:** dedupe
- **Locations:** executor/snapshot/memory writer.
- **Suggested action:** investigate (`write_durable` helper).
- **Origin:** D-010, F-agents-misc-012.

### A-074 — Deterministic-analyst module-level singletons
- **Severity:** P2 · **Category:** dedupe / dead-code
- **Locations:** `src/agents/analysts/{technical,social}/agent.py` + `__init__.py` re-exports.
- **Suggested action:** delete (rewrite two test assertions to use factory).
- **Origin:** D-011, F-analysts-011.

### A-075 — `DOMAINS` frozenset duplicated
- **Severity:** P2 · **Category:** dedupe / policy-mismatch
- **Locations:** `src/data/registry.py:101`; `src/data/config.py:18`.
- **Suggested action:** investigate (move literal to leaf or startup assertion).
- **Origin:** D-013, F-data-012.

### A-076 — `schema_cap` helper duplicated
- **Severity:** P2 · **Category:** dedupe
- **Locations:** `src/config/analysts.py:187-207`; `src/config/strategist.py:152-174`.
- **Suggested action:** consolidate (free `apply_slack`).
- **Origin:** D-015, F-ops-010.

### A-077 — `_audit_capture_enabled` single-caller
- **Severity:** P2 · **Category:** over-abstraction
- **Locations:** `src/backtest/cache/store.py:867`.
- **Suggested action:** refactor (inline; or moot if A-043 collapses inline).
- **Origin:** F-backtest-009.

### A-078 — Upstream verifiers hard-code `agreement_with_cache=True`
- **Severity:** P2 · **Category:** silent-failure (always-green tripwire)
- **Locations:** `src/backtest/audit/upstream_verifier.py:159-217`.
- **Evidence:** `_verify_filing`/`_verify_news` placeholders; `upstream_disagreement` can never fire; SUMMARY permanently green.
- **Suggested action:** investigate (implement or return `None` and render neutrally).
- **Origin:** F-backtest-010.

### A-079 — `test_end_to_end_smoke.py` mega-file (756 LoC)
- **Severity:** P2 · **Category:** test-consolidation
- **Suggested action:** investigate (split per concern).
- **Origin:** F-backtest-013, T-106.

### A-080 — `last_snapshot` vs `last_executed_tick_id` parallel high-water marks
- **Severity:** P2 · **Category:** dedupe
- **Suggested action:** investigate (collapse or atomic-write helper).
- **Origin:** D-027.
- **Status:** investigated — no action (plan-12). The suggested "investigate" was
  carried out: the two keys are **not** parallel high-water marks. `last_executed_tick_id`
  is a bare tick-id *string* read solely by the executor's idempotency guard
  (`executor/agent.py:79`, `== tick_id`); `last_snapshot` is a snapshot *payload dict*
  read solely by the driver's completion check (`driver.py:738`, `snap.get("tick_id")`).
  No call site reads them together (grepped `src/` + `tests/`), so neither "collapse"
  nor an "atomic-write helper" applies — they are written by different agents at
  different pipeline stages, and a coalescing accessor would be type-incoherent (string
  vs dict) and would regress execution idempotency. Closed as not-a-duplication.

### A-081 — Live tick `BaseException` swallow
- **Severity:** P2 · **Category:** cross-lifecycle
- **Locations:** `src/orchestrator/tick.py:259-270` vs `src/backtest/driver.py:588-590`.
- **Evidence:** Live catches `BaseException` (swallows `KeyboardInterrupt`/`SystemExit`); backtest deliberately doesn't.
- **Suggested action:** refactor (narrow catch; reuse `_log_exception_chain`).
- **Origin:** F-orch-011, D-025.

### A-082 — Dormant data-registry tail (7 unused schemas)
- **Severity:** P2 · **Category:** schema-duplication
- **Evidence:** `EarningsHistory`, `EarningsReport`, `AnalystConsensusBundle`, `AnalystRating`, `AnalystRevision`, `ShortInterestSnapshot`, `OptionContract` in `DOMAIN_SHAPES` with no analyst wrapper.
- **Suggested action:** delete with A-036 or wire consumer.
- **Origin:** D-026.

### A-083..A-097 — P3 items

- **A-083** `headline_polarity_mean` alias nit (D-018).
- **A-084** `_git_sha7` vs `_git_sha_full` (D-019, F-backtest-007).
- **A-085** `build_telemetry_record_from_logs` orphan (D-020, F-backtest-006).
- **A-086** `state["thesis"]`/`user:thesis` residue (D-024, F-strategist-009).
- **A-087** `TickState` unused (F-orch-005, F-orch-015).
- **A-088** `_dispatch_app_name` over-abstraction (F-orch-016).
- **A-089** `BrokerMode._value2member_map_` private access (F-orch-012).
- **A-090** `lifecycle/scheduler.py` Cloud Scheduler shells (F-orch-014) · **Human gate**.
- **A-091** `_check_live_tables_empty` Postgres `public.` assumption (F-orch-013).
- **A-092** `BUFFER_MAX` unused (F-agents-misc-011).
- **A-093** Triple structured-log emission pattern (F-agents-misc-014).
- **A-094** `_has_real_smart_money` over-abstraction (F-agents-misc-013).
- **A-095** `log_cache_hit_to_state` no-op (F-analysts-004).
- **A-096** `report_cache.py` importlib gymnastics (F-analysts-014).
- **A-097** Misc nits: empty `src/deploy/` (F-ops-002); legacy `emit_analyst_totals`/`_header` (F-ops-003); `get_handles` (F-ops-004); `SPYMetrics`/`_metrics_from_series` (F-ops-005, D-012); two-namespace tuple (F-ops-008); missing config-loader tests (F-ops-009); `config/README.md` missing `watchlist_smoke.json` (F-ops-011); empty `baselines/__init__.py` (F-ops-013); EDGAR/pit_composite bare `except` (F-data-019); `timeguard.py` wall-clock counter (F-data-008); per-domain `__init__.py` double-bookkeeping (F-data-009); `quiver_http_timeout_seconds` (F-data-010); politician_trades fmp/quiver dup (F-data-011); blanket noqa E402 (F-data-018); `_trace_maybe` cross-package underscore import (F-risk_gate-007); `_build_memory_writer` indirection (F-orch-006); duplicate session-service tests (F-orch-008, T-109); doc-only `__init__.py` (F-backtest-014); stale "Band 4" comment (F-backtest-015); reporting.py N/A-by-string (F-backtest-008); `decision_writer.py` BaseAgent overhead (F-strategist-013); `digest_defaults.py` single-dict module (F-contract-012); `strategist_prompt.render_all_ticker_blocks` single caller (F-contract-014); T212 PAPER/LIVE URLs un-smoke-tested (F-broker-010, F-broker-011); strategist enricher gap on `intent=None` (F-strategist-012); joiner verdict/evidence consistency test (F-analysts-016); `decision_tags` plumbing unread (F-strategist-006).

---

## § Human gates outstanding

All six gates resolved 2026-05-26 — see intent.md §8 for authoritative
resolutions.

| Gate | Resolution | Cascade |
|---|---|---|
| **A-021** smart_money wiring | §8.1 — shelved confirmed; revise intent §7.1. Keep module + tests as dormant scaffolding. | A-022 moot, A-033 keep, defensive consumers stay |
| **A-016** deterministic `AnalystReport` | §8.2 — relax validator; delete synthetic-prose paths in technical/social/smart_money extractors. | Pairs with A-013, A-049 |
| **A-030** `BufferEntryRow` shell | §8.3 — delete CRUD + ORM + test + the `_STOCKBOT_TABLES` entry. | A-042 (`MemoryProjection`) — same pass |
| **A-012** `scripts/trace_tick.py` | §8.4 — delete outright. | none |
| **A-090** Cloud Scheduler shells | §8.5 — keep as-is; Cloud Scheduler is the plan. | none |
| **A-056** risk_gate clamp order | §8.6 — source wins; update intent §2.4. Zero code change. | none |

## § Disagreements between auditors

From dedupe.md:
1. Intent §3.2 cluster 9 (verdict/signal) — synthesis finds no live duplication; retire from §3.2.
2. Intent §3.2 cluster 10 (tick/cycle/run) — terms stable, non-overlapping. Not a dedupe.
3. **D-007 severity** — module audit P1; synthesis considered P0; concurred P1 (rare conjunction required). Recorded as A-017 P1 with note.

From intent.md §6 (resolved §7):
- §6.1 smart_money — resolved §7.1 (runs every tick) but **source contradicts** → A-021.
- §6.2 strategist enrichment — resolved §7.2 (callback dead; enricher live).
- §6.3 bare-key positions — resolved §7.3 (executor-internal only).
- §6.4 data-domain count — resolved §7.4 (5 + 3 layered).

Cross-module severity disagreements taken as higher: F-broker-004 (P1) vs D-009 (P2) → A-072 P2 with broker-policy concern in Notes; F-analysts-001 P0 vs F-analysts-002/-003 P1 → kept as A-021 P1 cascade.

## § Categories summary

| Category | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|
| silent-failure | 8 | 7 | 5 | 0 | 20 |
| dead-code | 0 | 8 | 7 | 7 | 22 |
| dead-test | 0 | 3 | 4 | 0 | 7 |
| dedupe | 2 | 4 | 8 | 3 | 17 |
| over-abstraction | 0 | 1 | 4 | 2 | 7 |
| policy-mismatch | 1 | 3 | 4 | 2 | 10 |
| test-gap | 0 | 3 | 4 | 0 | 7 |
| cross-lifecycle | 2 | 1 | 1 | 0 | 4 |
| vocabulary-collision | 2 | 1 | 0 | 1 | 4 |
| schema-duplication | 0 | 0 | 1 | 1 | 2 |
| test-consolidation | 0 | 0 | 3 | 0 | 3 |
| misc | 0 | 0 | 0 | 1 | 1 |

(Cells overlap where a finding holds multiple tags.)

## § Modules summary

| Module | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|
| analysts | 1 | 4 | 1 | 2 | 8 |
| strategist | 0 | 3 | 5 | 1 | 9 |
| executor | 1 | 0 | 7 | 0 | 8 |
| risk_gate | 2 | 2 | 5 | 0 | 9 |
| contract | 0 | 1 | 5 | 0 | 6 |
| data | 1 | 4 | 4 | 1 | 10 |
| backtest | 0 | 3 | 5 | 1 | 9 |
| broker | 3 | 0 | 0 | 1 | 4 |
| orchestrator-lifecycle | 3 | 1 | 1 | 4 | 9 |
| agents-misc | 0 | 5 | 2 | 3 | 10 |
| ops | 1 | 0 | 0 | 1 | 2 |
| cross-cutting (D/T) | 3 | 8 | 1 | 1 | 13 |

## § What was deduplicated

- A-009 merges F-orch-001 + D-022 + F-orch-009 + T-005 + T-008.
- A-010 merges F-orch-002 + D-023.
- A-011 merges F-orch-004 + D-021 + F-orch-010 + T-005 (subset).
- A-013 merges D-001 + F-strategist-011 + F-contract-004 + F-executor verb_dispatch sites.
- A-014 merges D-002 + F-executor-004 + F-strategist-007 + F-backtest-005.
- A-015 merges D-014 + F-analysts-015 + F-contract-005 (silent-fill) + F-strategist-006 (no-data branch).
- A-017 merges D-007 + F-risk_gate-003/-009/-013 + T-005 (stale-verb).
- A-018 (T-001) subsumes F-orch-007, F-orch-015, F-agents-misc-010, F-executor-010, F-agents-misc-007, F-contract-008, F-contract-009, F-analysts-013.
- A-019 (T-002) subsumes §G.7 attractor across F-contract-005, F-analysts-013, F-data-004, F-agents-misc-006.
- A-020 (T-005) subsumes F-broker-008, F-data-014, F-orch-009, F-orch-010, F-risk_gate-009.
- A-024 (T-103) subsumes F-strategist-002.
- A-026 sibling: F-strategist-005.
- A-033 (T-104) subsumes F-analysts-003.
- A-036 (T-105) subsumes F-data-001, F-data-016, D-026.
- A-040 merges F-data-005 + F-data-014 (cementing-tests).
- A-043 merges F-backtest-001 + D-017.
- A-047 (D-023) overlaps A-010 as cross-lifecycle restatement.
- A-051 merges D-006 + F-contract-006 + F-strategist-009 (schema split).
- A-052 merges F-contract-007 + F-strategist-010.
- A-059 (T-108) merges F-risk_gate-010.
- A-067 (T-101) merges F-executor-011 (and same-class F-orch-008/F-data-017/F-risk_gate-010 layout sub-cases).
- A-071 merges D-008 + F-strategist-003.
- A-072 merges D-009 + F-broker-004.
- A-073 merges D-010 + F-agents-misc-012.
- A-074 merges D-011 + F-analysts-011.
- A-075 merges D-013 + F-data-012.
- A-076 merges D-015 + F-ops-010.
- A-079 (T-106) merges F-backtest-013.
- A-084 merges D-019 + F-backtest-007.
- A-085 merges D-020 + F-backtest-006.
- A-086 merges D-024 + F-strategist-009 (thesis aspect).
- A-080 (D-027): no F-side child; promoted from cross-module pattern.
- A-081 merges F-orch-011 + D-025.

## § Findings not in the main list

- F-agents-misc-002 — empty package marker, by-design.
- F-agents-misc-016 — writer.py `as_of` compliance, clean.
- F-broker-007 — `Portfolio.market_value`/`total_value`/`current_weights` earn their keep.
- F-broker-010/-011 — pre-deployment scaffolding, rolled into A-097.
- F-data-018 — blanket noqa E402, rolled into A-097.
- F-orch-006 — `_build_memory_writer` indirection, rolled into A-097.
- F-backtest-011/-012 — politician_trades / notable_holders shell providers; load-bearing per memory; explicit no-action.
- F-backtest-014 — doc-only `__init__.py`, rolled in.
- F-ops-012 — `_reset_for_tests` test hook, keep.
- F-ops-014 — canonical positive-observability test, no action.
- F-orch-016 collapsed into A-088.
- T-007, T-008 partial — compliance confirmations.
- T-201..T-210 — gap-filling test proposals, tracked under test-strategy §6 menu (not findings).
- T-301..T-303 — meta-policy edits, not codebase findings.
