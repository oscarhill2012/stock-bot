# StockBot pre-backtest code review (2026-05-16)

## Executive summary

The codebase is in materially better shape than a six-phase pile-up usually
produces.  The Phase 6 backtest harness (`src/backtest/`) is cohesive,
well-commented, and aware of its own seams (PIT timeguard, audit telemetry,
tripwires, schema-version meta row, NYSE-calendar promotion).  Phase 5's
analyst re-categorisation has clearly landed — the `AnalystSignal` / `*Signal`
families are gone from production paths, the strategist consumes
`TickerEvidence` exclusively, and the analyst pool is a clean five-element
ParallelAgent.  Tests for the leak surfaces that motivated Phase 6 are
present (`tests/backtest/leak_regressions/`, `tests/unit/data/providers/*_as_of.py`).

That said, several issues sit between the harness and a trustworthy backtest:

1.  **One leak tripwire is permanently disabled.**  `driver.py:193` passes
    `wall_clock_fallback_fired=False` as a hard-coded literal rather than
    reading the per-tick flag the spec promises.  This is the single highest-
    impact finding: a wall-clock fallback inside a backtest tick will *not*
    be surfaced by the tripwire summary that the smoke test asserts must be
    clean.
2.  **Doc/script naming drift.**  CLAUDE.md (root + `.claude/`) advertises a
    `scripts.backtest_fill` CLI which does not exist; the actual script is
    `scripts/backtest_fetch.py`.  A user following the README into a fresh
    backtest will get `ModuleNotFoundError` before they see a tick.
3.  **Missing-timestamp policy is inconsistent.**  The store skips writes
    with `MISSING_TIMESTAMP` (good), but the audit telemetry only flags them
    *if* they appear in the read set — by then they have already been
    silently filtered out.  A `missing_timestamp_writes_skipped_count` is the
    obvious missing signal but is not produced.
4.  **`AuditingStore` decorator is fully shadowed** by the store's own
    in-process audit hooks (`_audit_record` / `_audit_enable_capture` /
    `_audit_drain_reads`).  Two parallel mechanisms producing the same
    per-tick read capture; one is used by every tick, the other only by the
    deep-dump CLI.

The top three risks for the first real backtest:

- The tripwire bug above (silent wall-clock fallbacks).
- `default_lookback_days.social_sentiment` is **not** in
  `config/backtest_settings.json`; the smoke fixture doesn't set it either.
  If any analyst depends on it the run will trip a `KeyError` mid-tick.
- The reporting layer reads `recorded_at` and `bot_total_value` from the run
  DB, but the audit-pass smoke test does not assert the snapshot table is
  populated under non-trivial trade scenarios — only the file exists.

---

## Blockers for backtesting

These are issues that will distort or break results.  Address before
running real money or treating outputs as data.

### B1.  `wall_clock_fallback_fired` is hard-coded `False` in the driver

**Location:** `src/backtest/driver.py:166-194`

```python
telemetry = build_telemetry_record(
    tick=tick,
    ...
    wall_clock_fallback_fired=False,   # ← always False
)
```

**What is wrong:**  The spec
(`docs/Phase6-backtesting-harness/specs/pit-correctness-and-audit-design.md`
§4.1 and the plan task 2) describes the strict-mode hook capturing whether
`timeguard.resolve_as_of` ever returned a wall-clock substitute during this
tick.  In the running code, this is unconditionally set to `False`, so the
corresponding tripwire `wall_clock_fallback_fired` in `tripwires.py:79` is
always `False` regardless of what actually happened.

**Why it affects backtests:**  Strict mode normally raises
`AsOfRequiredError`, which would crash the tick.  But the driver wraps
`_run_one_tick` in `except Exception` (line 146) and records a "failed
tick" rather than aborting.  If any code path along the live pipeline
*does* swallow the `AsOfRequiredError` and fabricates a timestamp
defensively, the leak will be invisible.  The smoke test's tripwire
assertion (test_end_to_end_smoke.py:444-460) explicitly checks this flag
must be `False` — but it is *defined* False; the check is vacuous.

**Remediation (no behavioural rewrite):**
- Wire a per-tick counter from `timeguard.resolve_as_of` (e.g.
  `STOCKBOT_WALLCLOCK_FALLBACK_TICK_COUNT` thread-local) and have the
  driver read+reset it post-tick.
- Or add a `WALLCLOCK_FALLBACKS: list[str]` module-level capture that the
  driver drains.
- Either way, replace the literal `False` with the real value.

### B2.  CLAUDE.md advertises a non-existent CLI (`scripts.backtest_fill`)

**Location:**
- `CLAUDE.md:53-58` — table lists `scripts.backtest_fill`.
- `.claude/CLAUDE.md:106-115` — same.
- Actual script on disk: `scripts/backtest_fetch.py`.
- `scripts/backtest_fetch.py:5` says
  `PYTHONPATH=src python -m scripts.backtest_fetch --window <key>`.

**What is wrong:**  Naming drift between docs and code.  Other doc strings
also refer to `backtest_fetch` (cache-store error message at
`src/backtest/cache/store.py:181`, reporting hints, plan files), so the
*code* is internally consistent under the name `backtest_fetch`; only the
CLAUDE.md tables disagree.

**Why it affects backtests:**  An operator following the project's primary
guidance will produce `ModuleNotFoundError: No module named
'scripts.backtest_fill'` and have no obvious next step.  This is a 30-
second fix but is on the literal critical path.

**Remediation:**  Pick one name (`backtest_fetch` is already established
in code and tests) and rename the CLAUDE.md references.  Do not rename
the script.

### B3.  Missing-timestamp rows are filtered at write time but only surfaced at read time

**Location:**
- Write-side filters: `src/backtest/cache/store.py:357-364, 442-448, 527-533,
  751-757` (news / filings / insider / notable_holders).
- Read-side audit summary: `src/backtest/audit/telemetry.py:165-175` counts
  `missing_count` only against rows already in the cache.

**What is wrong:**  When the fetcher hands the store a `MISSING_TIMESTAMP`
row, the store logs a warning and silently drops it.  The audit telemetry
record cannot see what was dropped — it only iterates rows that survived
the write filter.  The tripwire `missing_timestamp_rows_seen` therefore
fires only on legacy rows already in the cache, not on rows being
fabricated upstream.

**Why it affects backtests:**  A provider whose API change starts emitting
`MISSING_TIMESTAMP` for, say, 30 % of news articles will silently shrink
the analyst's input set with no diagnostic in the audit log.  The
backtest will run clean, but the strategist's prompt will be missing
context, and the only signal is in `logger.warning` lines from the fill
phase — long since lost by the time the backtest runs.

**Remediation:**  Have the store maintain an in-memory counter per domain
(`self._writes_skipped_missing_ts: dict[str, int]`) and expose a
`drain_skipped_writes()` method that the *fetcher* (not the backtest
driver) consumes after a fill and writes into the cache's meta row or a
sibling `fill_audit.json`.  Surface that count in the backtest run's
manifest.

### B4.  `default_lookback_days` is missing `social_sentiment`

**Location:**
- `config/backtest_settings.json:11-17` — only has news / insider_trades /
  politician_trades / notable_holders / filings.
- `tests/integration/backtest/test_end_to_end_smoke.py:238-244` — same
  omission in fixture settings.
- `src/agents/analysts/social/fetch.py` (if it reads
  `default_lookback_days["social_sentiment"]`) would `KeyError`.

**What is wrong:**  Verify whether the social analyst fetch path reads
this key.  If yes, the first backtest will trip a `KeyError`.  If no,
the entry is missing from settings but harmless.  Either way the
asymmetry is noise.  (Did not deep-trace fetch.py for this report — flag
for verification.)

**Remediation:**  Read `src/agents/analysts/social/fetch.py` end-to-end
once; either add the key to `backtest_settings.json` or document the
asymmetry in `config/README.md`.

### B5.  Same-day OHLCV bar is read but tripwire is masked

**Location:**
- `src/backtest/cache/store.py:214-264` — `read_ohlcv(ticker, start, end)`
  inclusive of `end`.
- `src/backtest/audit/tripwires.py:64-68` — `open_tick_sameday_bar` flag.
- `tests/integration/backtest/test_end_to_end_smoke.py:443-448` —
  `open_tick_sameday_bar` is **deliberately excluded** from the leak set.

**What is wrong:**  The smoke test's docstring explains the exclusion:
"the store's inclusive-range query (end=as_of.date()) surfaces the same-
day bar at the raw read level, but the price_history_cache provider
correctly strips it before any analyst receives it."  This is plausible,
but the test never *asserts* that the provider strips it.  The exclusion
list and the leak-regression test
(`test_open_tick_excludes_sameday_bar.py`) both rely on the provider
behaving correctly; if a refactor reroutes that path, the tripwire is
already gated off and no signal reaches the assertions.

**Why it affects backtests:**  Latent — only matters on a refactor.  But
the design is fragile: a real leak tripwire is permanently set to
"warning expected, ignore".  Flagged for backtest preflight.

**Remediation:**  Move the same-day-strip check from the regression test
into a positive driver-level assertion ("any tick with phase=='open' must
have `price_history.ticker_rows[*].sameday_bar_seen == False` after the
provider runs"), so the existing tripwire becomes a live signal rather
than a known-noisy one.

### B6.  Live `orchestrator/tick.py` initial-state vs `runner.py` initial-state

**Location:**
- `src/backtest/runner.py:287-296` — seeds 7 keys: `tickers`, `watchlist`,
  `portfolio`, `positions`, `memory_buffer`, `day_digest`, `thesis`.
- `src/orchestrator/tick.py` — `_build_initial_state` (per CLAUDE.md note).

**What is wrong:**  CLAUDE.md explicitly notes that the runner must
mirror live tick's `_build_initial_state` to avoid the ADK `KeyError:
'Context variable not found: portfolio'`.  The current runner.py does
include those keys, but no test asserts the *set* matches — only the
unit test `tests/unit/orchestrator/test_tick_initial_state.py` asserts
the live builder's keys.  Drift between the two is undetected until a
backtest crashes.

**Why it affects backtests:**  A new field added to live `_build_initial_state`
(say, `_position_history`) will not propagate to backtests and any agent
reading the new key during a backtest tick will fail.

**Remediation:**  Add a single assertion test that the *key set* matches
between the two callers, importing both and diffing.  Cheap and
defensive.

### B7.  Initial portfolio prices are zero in `runner.py`

**Location:**  `src/backtest/runner.py:237-240`

```python
broker = FakeBroker(
    starting_cash=self._settings["fake_broker_starting_cash"],
    prices={ticker: 0.0 for ticker in wl_filtered},
)
```

**What is wrong:**  Initial prices are set to `0.0`.  `_refresh_broker_prices`
runs at the start of each tick, but if the first tick's `read_ohlcv` returns
empty (holiday, missing bar) the broker enters the pipeline with
zero-priced tickers.  Combined with `_compute_vs_spy_delta` and snapshots,
this can produce a misleading equity curve at the leading edge.

**Why it affects backtests:**  Tick 1 metrics may show artefactual moves
from `0.0 → real_price` on the second tick.

**Remediation:**  Seed initial prices from the first OHLCV bar each ticker
has in the window, or skip equity-curve persistence on the bootstrap tick.

### B8.  `DecisionLogger.forward_returns` is back-filled at end of run

**Location:**  `src/backtest/reporting.py:279-334`

**What is wrong:**  Looks correct — bars are read from the cache, +1/+5/+20d
returns are computed.  But: the lookup uses
`cache.read_ohlcv(ticker, target, target + timedelta(days=4))` and takes
*the first available bar*, which may be more than 4 days off the target if
the window straddles a long holiday closure.  Currently silent — the file
just records the actually-used bar's close as the horizon price without
recording the *horizon error*.

**Why it affects backtests:**  Forward-return metrics will be off-by-N-
days for some decisions silently.  Won't crash, but will distort RAG
supervision signals later.

**Remediation:**  Record the *actual* bar date alongside the return in the
snapshot file (e.g. `forward_returns_actual_date: {"+1d": "2023-03-08", ...}`).

---

## Dead code & orphaned abstractions

### D1.  `src/backtest/audit/auditing_store.py` is shadowed by the store's own audit hooks

**Evidence:**
- `CachedDataStore` has `_audit_enable_capture`,
  `_audit_drain_reads`, and inline `_audit_record` calls on every read
  method (`src/backtest/cache/store.py:822-857`).  The driver uses these.
- `AuditingStore` (`src/backtest/audit/auditing_store.py`) does the same
  thing externally by wrapping the store.
- `AuditingStore` is used in exactly one place: `scripts/backtest_audit_tick.py`
  (Layer 2 deep-dump).

**Verdict:**  Not strictly dead — but two parallel capture mechanisms
exist.  Either:
- Have the deep-dump script enable the store's built-in capture and drop
  `AuditingStore`, or
- Use `AuditingStore` everywhere and remove the inline `_audit_record`
  calls from the store.

The store-internal approach has a real disadvantage: every read method
in `store.py` has an embedded `self._audit_record(...)` call, which is
mixing concerns.  But it is also the one the production path uses, so
the simpler simplification is to delete `AuditingStore` and have the
deep-dump CLI flip `_audit_enable_capture` on then call
`_audit_drain_reads` after the tick.

### D2.  `src/baselines/spy.py::spy_metrics` looks unused

**Evidence:**
- Defined `src/baselines/spy.py:50`.
- Imported only by the SPY metrics test
  (`tests/unit/test_spy_metrics.py:8`) which imports `_metrics_from_series`,
  not `spy_metrics`.
- `grep -rn spy_metrics src tests scripts` returned only the definition.

**Verdict:**  Public function with zero call sites.  Phase-1 vestige —
`reporting.py` computes its own SPY delta directly from the cache.

### D3.  `src/lifecycle/scheduler.py` (Cloud Scheduler shim) is on a dormant code path

**Evidence:**
- Called only from `lifecycle/hard_reset.py:97`, `lifecycle/initialise.py:159`,
  and `scripts/hard_reset.py:81`, `scripts/initialise.py:82`.
- CLAUDE.md project-state note says: "Pre-deployment — no paper or live
  instance is running" and "skip dual-write / paper-data rollout patterns".

**Verdict:**  Not orphaned (lifecycle scripts call it), but the lifecycle
scripts themselves are not run anywhere yet.  Phase 8 candidate, not
Phase 7.

### D4.  `scripts/replay_backtest.py` overlaps with `scripts/backtest_run.py`

**Evidence:**
- `scripts/replay_backtest.py` — 30-day replay harness, `ReplaySummary`
  dataclass, builds its own FakeBroker, drives ticks directly.
- `scripts/backtest_run.py` — uses the Phase 6 `Runner`.
- `tests/replay/test_replay_30days.py` and
  `tests/unit/test_replay_backtest_cli.py` still exercise the replay path.

**Verdict:**  Pre-Phase-6 backtest CLI that the Phase 6 plan was supposed
to replace.  Functionally similar to `backtest_run.py` but doesn't share
the cache, audit telemetry, decision logger, or strict-mode env.  Almost
certainly a candidate for retirement once Phase 7/8 stabilises.

### D5.  `scripts/smoke_run.py` and `scripts/trace_tick.py` overlap with each other

**Evidence:**
- `scripts/smoke_run.py:5-8` and `scripts/trace_tick.py:11` —
  `trace_tick` literally says "The script mirrors the bootstrapping
  logic in `scripts/smoke_run.py`".

**Verdict:**  Two scripts doing similar bootstrapping, only differing
in tracing.  Acceptable; flag as cleanup target.

### D6.  `scripts/test_bundle.py` is a one-off probe script

**Evidence:** `scripts/test_bundle.py:1-8` — "Run: python -m
scripts.test_bundle [TICKER]".  No test imports it, no CI exercises it.

**Verdict:**  Useful for manual ticker debugging.  Live-only path
(hits real data providers).  Document or move into a `scripts/dev/`
subdir.

### D7.  Phase 4 `make_dual_emit_callback` reference still in `_common.py` docstring

**Evidence:**  `src/agents/analysts/_common.py:3-12` documents that
`make_dual_emit_callback` was removed — the comment is correct and
intentional.  Not dead code, but worth grepping that no production
caller still references it: `grep -rn "make_dual_emit_callback" src
tests` returned only the docstring (clean).

### D8.  `src/data/providers/__init__.py` is sparse

**Evidence:**  Empty file beyond imports for registry side effects.
This is intentional — domain modules register themselves on import — but
warrants a comment block explaining the empty-by-design pattern for
future readers.

### D9.  `src/data/aggregator.py` — `get_stock_signal_bundle` and `StockSignalBundle`

**Evidence:**
- `src/data/aggregator.py:18` — "Phase 5: `StockStats` retired".
- `data/models/bundle.py` still defines `StockSignalBundle`.
- Used by `tests/unit/data/test_aggregator.py` and `scripts/test_bundle.py`.

**Verdict:**  The data aggregator's signal bundle is no longer the
strategist's input (the strategist now consumes `TickerEvidence`); only
tests and the manual `test_bundle` probe still touch it.  The bundle is
a residual abstraction from Phase 1-3.  Decide: keep as a debug surface,
or retire after Phase 8.

---

## Over-abstraction hotspots

### O1.  Broker `Protocol` with three implementations

**Evidence:**  `src/broker/protocol.py`, `fake.py`, `trading212.py`.
The `Broker` Protocol is satisfied by `FakeBroker` and `Trading212Broker`.
This is reasonable; flagged only because Executor takes `broker: Any` and
mostly bypasses the Protocol's type benefits (`agents/executor/agent.py:30`).

**Sentence:**  Tighten Executor's type to `Broker` or remove the Protocol.

### O2.  Two trace mechanisms

**Evidence:**  `src/observability/trace.py` — `TraceWriter` + `_trace_maybe`
(no-op stub).  `_trace_maybe` is called from agent fetch callbacks
*just in case* `state["_trace"]` is set.  Only one production path sets
it (`scripts/trace_tick.py`).

**Sentence:**  Keep `_trace_maybe` (zero-cost); remove `TraceWriter`'s
deepcopy identity passthrough once Phase 8 confirms no caller relies on
session-state copy semantics.

### O3.  Per-domain cache provider modules with near-identical structure

**Evidence:**  `src/backtest/providers/{news,filings,insider_trades,
notable_holders,politician_trades,price_history,company_ratios,
social_sentiment}_cache.py` — eight files, all `@register("<domain>",
"cache")` async functions calling the corresponding store reader and
returning the typed list.

**Sentence:**  These could collapse to a single decorated factory
mapped over the eight domains, but the explicit list documents intent
and gives unique import-time error sites — leave as is.

### O4.  `_store_handle` module-global singleton pattern

**Evidence:**  `src/backtest/providers/_store_handle.py` — module-global
`_STORE: CachedDataStore | None` with `set_store / get_store / clear_store`.

**Sentence:**  Classic test-fragility shape (silent cross-test state
bleed if `clear_store` is missed in a fixture teardown).  Add a pytest
autouse fixture that asserts `_STORE is None` at session end, or push
the store into `state["_cache_store"]`.

### O5.  `make_engine` god-node has 32 edges

**Evidence:**  GRAPH_REPORT.md god nodes list — `make_engine` is #2 most
connected.  Used by every CLI, every test that needs a DB, every
lifecycle helper.

**Sentence:**  Expected for a DB factory; no action.

### O6.  Five distinct `make_*_factory` patterns in `orchestrator/persistence.py`

**Evidence:**  `make_engine`, `make_session_factory`, `save_*` helpers
per row type, plus an ad-hoc `create_all`.  Conventional SQLAlchemy
shape but a thin repository wrapper would replace ~80 lines.

**Sentence:**  Out of scope for backtest cleanup; flag for Phase 8.

### O7.  `AuditingStore` (already discussed in D1) — wraps a class that already does its job

**Sentence:**  Delete after deep-dump CLI is rewired to the store's
internal capture (D1 above).

---

## Test coverage gaps

### Untested or thinly tested modules

- **`src/backtest/cache/fetcher.py`** — only one integration test
  (`tests/integration/backtest/test_fetcher_idempotent.py`) and the
  backfill smoke test cover it.  No unit tests for individual fetch
  branches.  Given the fetcher is the *source* of cache contents that
  every backtest reads, this is undertested.

- **`src/backtest/audit/upstream_verifier.py`** — referenced by
  `deep_dump.py` and tested only indirectly via `test_audit_tick_smoke.py`.
  No unit tests for `verify_row` itself.

- **`src/backtest/audit/deep_dump.py`** — single smoke test that the
  files are written.  No assertions about the rows' shape, ordering,
  or `fabricated_timestamp` flag computation.

- **`src/lifecycle/initialise.py`** — covered by
  `test_initialise.py` and `test_lifecycle_initialise.py`, but
  Trading-212-specific branches (live mode) are untested.  Acceptable
  given current state.

- **`src/observability/trace.py`** — `TraceWriter.snapshot` and
  `_trace_maybe` are tested; the deepcopy identity passthrough
  (`__deepcopy__`) is not.

- **`scripts/trace_tick.py`** and **`scripts/test_bundle.py`** — no
  tests.  Tolerable for ad-hoc CLIs but worth flagging.

- **`src/backtest/runner.py::Runner._run_async`** — the giant try/
  except finally block has no direct unit test for the strict-mode
  env-var restore semantics or the SIGINT handler logic.  There are
  `test_runner_sigint.py` and `test_driver_keyboard_interrupt.py`,
  but they exercise the interrupt path narrowly.

### Stale or broken tests

- **`tests/replay/test_replay_30days.py`** — references the legacy
  `scripts.replay_backtest` path.  Should run, but the harness it
  exercises is the pre-Phase-6 replay loop (D4).  Either delete with
  D4 or convert to use the Phase 6 Runner.

- **`tests/unit/test_replay_backtest_cli.py`** — same.

- **`tests/unit/test_cloudbuild_yaml.py`** — validates `deploy/
  cloudbuild.yaml`.  Project state says "pre-deployment", so this is
  testing infra not currently in use.  Not stale (cloudbuild.yaml is
  presumably correct), just out of scope for current goals.

- **`tests/integration/test_pipeline_composition.py:20`** — has both
  `test_pipeline_has_eight_stages` (current) and an older
  `test_pipeline_has_seven_stages` is referenced in graph community
  42 — the older test may have been deleted, in which case the
  community description is stale.  Verify there is no residual
  seven-stage assertion.

- **`tests/unit/test_smart_money_fetch.py`** and friends — many
  smart-money tests describe Phase-5 gating behaviour.  Verify
  they still align with the deterministic BaseAgent shape (the
  fetch callback now always returns `None`).

### Suggested additions, prioritised (backtest-critical first)

Priority 1 — must land before first real backtest:

1.  **Test that `wall_clock_fallback_fired` is wired** — a test that
    drives one tick with `STOCKBOT_STRICT_AS_OF` *unset* and a
    deliberately wallclock-triggering path, then asserts the telemetry
    record shows the flag `True`.  (Fixes B1's invisibility.)
2.  **Test that `_build_initial_state` and `runner.py`'s initial state
    have the same key set.**  Fixes B6.
3.  **Test that the first-tick bootstrap price is non-zero** for any
    ticker with OHLCV in the window.  Fixes B7.
4.  **Test that the smoke test's tripwire-exclusion comment is still
    true** — i.e. that the `price_history_cache` provider strips the
    same-day bar even when the store returns it.  Fixes B5's fragility.

Priority 2 — should land before publishing backtest results:

5.  Forward-return *actual date* recording test (B8).
6.  Audit telemetry: assert `missing_timestamp_writes_skipped_count` is
    surfaced somewhere (B3) — currently no signal at all.
7.  Cache-fill integration test for SPY presence so reporting's vs-SPY
    delta is never `N/A` in published runs.

Priority 3 — Phase 8 / nice to have:

8.  Unit tests for `fetcher.py` happy/sad paths per domain.
9.  Unit tests for `upstream_verifier.verify_row`.
10. Cross-tick state-bleed test for `_store_handle` (O4).

---

## Cleanup (post-backtest safe)

The following are accumulated cruft / minor smells.  None of them
distorts results; all can wait until after the first real backtest.

- Rename CLAUDE.md doc references from `scripts.backtest_fill` to
  `scripts.backtest_fetch` (B2; trivial, but listed here as cleanup
  *alongside* the blocker section since the underlying fix is purely
  textual).
- Delete `src/baselines/spy.py::spy_metrics` (D2) — no callers.  Keep
  `_metrics_from_series` since it has its own test.
- Decide on `scripts/replay_backtest.py` (D4) — either retire or wire
  into the Phase 6 cache.
- Move `scripts/test_bundle.py` and `scripts/trace_tick.py` under a
  `scripts/dev/` subdirectory; they are debug tools, not part of the
  CI surface.
- Consolidate `AuditingStore` with the store's built-in capture (D1).
- Add a comment block to `src/data/providers/__init__.py` explaining
  the empty-by-design pattern (D8).
- Tighten `ExecutorAgent.broker: Any` to `broker: Broker` (O1).
- Convert `_store_handle` module-global to a per-Runner attribute or
  an `InvocationContext` injection (O4).
- Audit Phase 4 commented-out blocks — none found in source files but
  Phase 4-era docstrings (e.g. `_common.py`) reference removed
  abstractions; that's fine for a delete record but should be tagged
  with the phase that did the removal.
- `_compute_vs_spy_delta` uses `date.fromisoformat(str(start_dt)[:10])`
  as a defensive parse — replace with explicit `if isinstance(...,
  datetime)` for clarity.
- The reporting `_write_metrics` Sharpe calculation assumes ticks are
  evenly spaced but with two ticks/day on weekdays only, the assumption
  is approximately right.  Document the approximation in the docstring.

---

## Appendix: module-by-module notes

### `src/agents/`

- **`analysts/`** — Cleanly factored.  Five sub-packages
  (`technical`, `fundamental`, `news`, `social`, `smart_money`) each
  with `agent.py`, `fetch.py`, optional `prompts.py`.  Heuristic
  loading via `heuristics.py` is correct; the `_common.py`
  callbacks (`make_evidence_callback`, `_chain_before`, `_chain_after`)
  are well-named.  `report_cache.py` has the auto-derived prompt-version
  fingerprint (B23 in graph_delta) and good test coverage.
  `cache_callbacks.py` factory looks fine.
- **`contract/evidence_writer.py`** — Single ADK agent that drains
  evidence to the DB.  Note the explicit comment "no try/except wrapping
  the saver loop — a mid-loop failure leaves the…": good intent
  documentation.
- **`executor/agent.py`** — Hooks into `state["_decision_logger"]` only
  inside backtest runs; otherwise the executor's behaviour is the same
  for live + paper + fake.  Solid.
- **`memory/`** — `compress.py`, `dedup.py`, `embeddings.py`, `writer.py`,
  `schema.py`.  All test-covered.  `embeddings.py` has a stub-able
  default; good.
- **`risk_gate/`** — `constraints.py` and `orders.py` are well-isolated.
  `lifecycle.py` is a small helper that integrates well.
- **`snapshot/agent.py`** — Calls `yfinance.Ticker("SPY")` directly,
  which is what the smoke test has to patch.  Could be parameterised
  via a `quote_provider` injection; flag for Phase 8.
- **`strategist/`** — Most-touched community (10+ Phase 5 graph
  entries).  `prompts.py`, `schema.py`, `stance_schema.py`,
  `held_view.py`, `evidence_view.py`, `derivation.py`,
  `lifecycle.py`, `decision_writer.py`, `agent.py` — clean separation,
  good test coverage.

### `src/backtest/`

- **`cache/`** — `schema.py` (SQLAlchemy ORM), `store.py` (façade),
  `fetcher.py` (download/freeze).  Store has a schema-version meta
  row with a hard error on mismatch (good; tested at
  `tests/unit/backtest/cache/test_schema_version_mismatch.py`).
- **`providers/`** — Eight per-domain cache providers + a
  `_store_handle` singleton.  Reuse pattern is clear.
- **`audit/`** — Five files: `auditing_store.py` (orphan candidate,
  D1), `deep_dump.py` (used by `scripts/backtest_audit_tick.py`),
  `telemetry.py` (per-tick record builder), `tripwires.py` (boolean
  rollup), `upstream_verifier.py` (re-fetch comparison).  Decent
  shape, undertested (deep_dump + upstream_verifier).
- **`decision_logger.py`** — Append-only JSON snapshot per executed
  trade.  Forward returns back-filled by reporting.  Solid; tested.
- **`driver.py`** — Tick loop.  Has the wall-clock-fallback bug (B1).
- **`reporting.py`** — Equity curve PNG + metrics.md + forward-return
  back-fill.  Reasonable; flagged in B7/B8.
- **`runner.py`** — Run orchestrator.  Strict-mode env, SIGINT handler,
  manifest write, report generation.  Robust but mostly untested at
  the `_run_async` granularity.
- **`schedule.py`** — `Tick` dataclass + `generate_ticks(start, end)`
  via NYSE calendar promotion.  Clean.
- **`windows.py`** — Window config loader.  Simple, tested.

### `src/broker/`

- **`fake.py`** — In-memory broker.  God node (27 edges).  Solid;
  tested.
- **`portfolio.py`** — `Portfolio` and `Position` Pydantic models.
  Solid; tested.
- **`protocol.py`** — `Broker` Protocol.  Underused by `ExecutorAgent`
  (O1).
- **`trading212.py`** — REST client, paper + live mode.  Untested
  beyond request-construction.  Acceptable while pre-deployment.

### `src/contract/`

- **`evidence.py`** — `AnalystEvidence`, `AnalystVerdict`,
  `AggregateVerdict`, `VerdictBatch`, `TickerEvidence`, etc.  Schema
  cap derived via `config/analysts.py` (Phase 5 work).  Clean.
- **`digest.py` + `digest_defaults.py`** — Deterministic per-ticker
  digest collapse from 4 analysts to one `TickerEvidence`.  Clean;
  tested.
- **`extractors/`** — Five files
  (`technical.py`, `fundamental.py`, `news.py`, `social.py`,
  `smart_money.py`) doing per-analyst feature extraction.  Pure
  functions, tested.
- **`strategist_prompt.py`** and **`ticker_evidence.py`** — Strategist
  prompt rendering + per-ticker evidence aggregation.

### `src/data/`

- **`models/`** — Pydantic models per domain (`filings`,
  `news`, `sentiment`, `trades`, `price_history`,
  `company_ratios`, `bundle`, `market`, `missing`).
  `StockStats` is fully retired (verified by grep — only
  retirement comments remain).  `CompanyRatios` is the replacement
  and is wired through the cache store.
- **`providers/`** — Per-domain provider modules using
  `@register` from `registry.py`.  Eight domains (after
  `stats` was split into `price_history` + `company_ratios`).
- **`registry.py`** — Provider registration + dispatch.  Solid.
- **`aggregator.py`** — Legacy bundle aggregator (D9).
- **`timeguard.py`** — Single-file PIT helper.  Excellent shape.
- **`rate_limit.py`, `retry.py`, `secrets.py`, `config.py`** —
  Cross-cutting utilities, all tested.

### `src/orchestrator/`

- **`pipeline.py`** — Eight-stage `SequentialAgent`.  Clean.
- **`tick.py`** — Live tick entrypoint.  Mirrors but does not
  share initial-state logic with `runner.py` (B6).
- **`persistence.py`** — SQLAlchemy ORM + save helpers.  God node
  (`make_engine`, 32 edges).
- **`state.py`** — `TickState` schema dataclass.  Tested.
- **`stock_picker.py`** — Watchlist loader.  Simple, tested.

### `src/observability/`

- **`trace.py`** — `TraceWriter` + `_trace_maybe`.  Two mechanisms
  (one no-op).  O2 — fine for now.

### `src/lifecycle/`

- **`initialise.py`, `hard_reset.py`, `scheduler.py`** — Pre-flight,
  archive/truncate, Cloud Scheduler shim.  D3 — dormant until paper
  goes live.

### `src/baselines/`

- **`spy.py`** — `spy_metrics` orphan (D2); `_metrics_from_series`
  is tested.
- **`equity_curve.py`** — `compute_equity_curve` used by
  `scripts/plot_equity.py`.  Solid.

### `scripts/`

- **Backtest CLIs:** `backtest_fetch.py`, `backtest_run.py`,
  `backtest_report.py`, `backtest_audit_tick.py`.
- **Live CLIs:** `initialise.py`, `hard_reset.py`, `smoke_run.py`,
  `trace_tick.py`, `init_db.py`, `plot_equity.py`.
- **Legacy:** `replay_backtest.py` (D4), `test_bundle.py` (D6).
- Naming note: CLAUDE.md → `backtest_fill` vs disk → `backtest_fetch`
  (B2).

### `config/`

- Nine JSON files plus `README.md`.  Backtest-relevant: `backtest_settings.json`,
  `backtest_windows.json`, `data.json`, `watchlist.json`,
  `analysts.json`, `strategist.json`, `analyst_heuristics.json`,
  `schedule.json`.
- `backtest_settings.json` is missing a `social_sentiment` entry in
  `default_lookback_days` (B4).  Cross-check whether it's needed.
- `backtest_windows.json` has only one window (`svb-stress-2023-03`).
  Plan promised more windows post-Task 8 (per pit-correctness plan
  rollout notes).  Add more after first window completes.

### `tests/`

- Layout: `unit/`, `integration/`, `analysts/`, `replay/`,
  `agents/`, `backtest/`.  Mostly clean.  Slow markers are
  applied to integration smoke + driver tests; not consistently
  to LLM-heavy tests (could not find any actual Gemini-hitting
  test in this sweep, so the slow marker may be sufficient).
- Backtest test count: 91 backtest-area test functions.  Good
  density.
- Suggested marker discipline: add an `integration` marker
  alongside `slow` and gate on both in CI — currently only the
  end-to-end smoke test uses `@pytest.mark.slow`.

---

*End of report.*
