# Source audit — src/backtest/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 23 (driver, runner, schedule, settings, windows, reporting, decision_logger, __init__, cache/{store,schema,fetcher,migrations,__init__}, providers/{_store_handle,price_history,news,company_ratios,filings,insider_trades,politician_trades,social_sentiment,notable_holders}_cache, audit/{telemetry,tripwires,upstream_verifier,deep_dump,auditing_store,__init__})
**Findings:** 1 P0 · 3 P1 · 6 P2 · 2 P3

## Summary

`src/backtest/` is the cache-replay harness: `runner.py` walks a window of
NYSE ticks, `driver.py` runs the live pipeline once per tick under a
cache-backed provider stack, and the cache/audit/reporting subtrees support
PIT-correctness verification and post-run analysis. The dominant theme is
**Audit+Reporting drift** — the leak-detection layer references a
`notable_holders.as_of_date` column that does not exist on the model, which
silently disables PIT verification for that domain (P0). Secondary themes
are **two parallel cache-read capture mechanisms** (`CachedDataStore._audit_record`
hook vs `AuditingStore` decorator) that need a clear owner, and **doc/code
drift** between `contract-invariants.md §A` and the actual line numbers of
the per-tick `last_snapshot` assertion. **Worst grouping: Audit+Reporting**
(it owns the single P0 plus two P2/P1 findings touching the same surface).
No cross-subsystem dependencies block this audit, but the consolidator
should pair the P1 reference-prices divergence with the orchestrator audit.

## Findings

### Top-level

#### P1-01 · C7 doc/code drift · `last_snapshot` assertion line numbers in contract

- **Location:** `docs/contract-invariants.md` §A (the `last_snapshot` row);
  actual code at `src/backtest/driver.py:615-624`.
- **Confidence:** high
- **Description:**
  Contract §A row for `last_snapshot` cites
  `src/backtest/driver.py:393-401` as the per-tick assertion site. The
  driver has grown since that line range was recorded; lines 393-401 now
  hold the `_drain_logs_cache_hits` helper, and the per-tick `last_snapshot`
  assertion now lives at lines 615-624. Future readers tracing the
  contract back to source will land on the wrong code and may conclude
  the assertion was removed. File under `subsystem: docs/contract-invariants`
  per rubric §C7.
- **Suggested action:**
  Update the §A `last_snapshot` row to the current line range
  (`src/backtest/driver.py:615-624`) — single-line doc edit, no source change.

#### P1-02 · C3 over­abstraction / divergence · Reference-prices lookback diverges between runner and driver

- **Location:** `src/backtest/driver.py:286-288` vs
  `src/backtest/runner.py:537`.
- **Confidence:** medium
- **Description:**
  `runner.py:_seed_reference_prices` (Phase 1 safety-net seed) reads the
  full window via `as_of=None`, while `driver.py` per-tick re-seeds
  `reference_prices` with a hardcoded 365-day lookback. The two seeders
  disagree on how much history to expose, and neither value is justified
  in a code comment. The driver value is also a magic number that should
  arguably live in `config/data.json` next to the other window-size knobs.
  Live `tick.py:_fetch_reference_prices` uses `period="1y"` (yfinance
  string) — closer to the driver, but the units are not commensurable.
- **Suggested action:**
  Pull the lookback into a single named constant (or config key) consumed
  by both `driver.py` and `runner.py`. If the runner safety-net is meant
  to "expose everything", document that explicitly in a comment so it does
  not read as a bug.

#### P2-01 · C3 over­abstraction · `except (AttributeError, Exception)` is a tautology

- **Location:** `src/backtest/driver.py:551`.
- **Confidence:** high
- **Description:**
  The except clause lists `AttributeError` and `Exception`. Since
  `AttributeError` is already a subclass of `Exception`, the tuple is
  redundant — only `Exception` is meaningful. The corresponding live
  path at `src/orchestrator/tick.py:260` catches
  `(AttributeError, BaseException)`, which is broader (covers
  `KeyboardInterrupt` / `GeneratorExit` from ADK 1.32 runner teardown).
  The driver's narrower catch is likely an oversight when the live code
  was widened; if a `BaseException` escapes ADK teardown mid-window the
  whole run will fail rather than logging-and-continuing.
- **Suggested action:**
  Either (a) match the live path: catch `(AttributeError, BaseException)`
  with the same comment block explaining the ADK 1.32 quirk, or
  (b) drop `AttributeError` from the tuple and catch just `Exception`.
  Pair with the orchestrator audit so live/backtest stay symmetric.

#### P2-02 · C3 over­abstraction · `runner.py` mutates process-global signal handlers

- **Location:** `src/backtest/runner.py:400-418` (signal handler install
  + restore inside `try/finally`).
- **Confidence:** medium
- **Description:**
  The runner installs a SIGINT handler at the start of a run and restores
  the prior handler in `finally`. This is fine for a CLI entrypoint but
  the runner is also called from `scripts/replay_backtest.py` and tests;
  any test that imports `runner` and crashes between install and restore
  leaves the global signal disposition in an inconsistent state. The pattern
  also blocks composition (e.g. running two windows back-to-back in one
  process). Low blast radius given current usage, but worth tightening.
- **Suggested action:**
  Move the signal-handler dance into a context manager
  (`with _install_sigint_handler(): ...`) so the restore is guaranteed
  even if `runner.run` raises before reaching `finally`, and so callers
  can opt out (e.g. when embedded in a parent process that owns signals).

#### P3-01 · C7 doc/code drift · Backtest/live exception-catch comment divergence

- **Location:** `src/backtest/driver.py:551` (backtest) and
  `src/orchestrator/tick.py:260-270` (live).
- **Confidence:** high
- **Description:**
  The live `run_once` catches `(AttributeError, BaseException)` with a
  block comment explaining the ADK 1.32 runner cleanup bug. The backtest
  driver catches a narrower tuple with a sparser comment. The two are
  meant to be lifecycle-symmetric (per `§C-Rule 2`); the comment drift is
  cosmetic but obscures the fact that the live path absorbs more failure
  modes. Subsumed by P2-01 if that finding is actioned together.
- **Suggested action:**
  Land alongside P2-01: when the tuple is harmonised, copy the live
  block comment verbatim so future readers see the same explanation in
  both lifecycles.

### Cache+Providers

#### P1-03 · C2 parallel branches · Two cache-read capture mechanisms

- **Location:** `src/backtest/cache/store.py:871-902`
  (`_audit_record` hook on `CachedDataStore`) vs
  `src/backtest/audit/auditing_store.py` (`AuditingStore` decorator).
- **Confidence:** high
- **Description:**
  The codebase carries two implementations of "record every cache read
  for audit": (1) a private `_audit_record` hook embedded directly in
  `CachedDataStore`, switched on by `_audit_enable_capture()` and drained
  by the driver per tick; and (2) a free-standing `AuditingStore`
  decorator that wraps a `CachedDataStore` and intercepts the same calls
  via `__getattr__`-style forwarding. Mechanism (1) is the live path
  used by `driver.py:192`; mechanism (2) is only used by
  `scripts/backtest_audit_tick.py` and a few tests. Their default
  parameters even differ (e.g. `AuditingStore.read_news` defaults
  `lookback_days=7`, the inner store defaults to `30`), so wrapping the
  decorator around a `CachedDataStore` and then calling `read_news()` with
  no args quietly changes window size. Classic C2: two parallel paths,
  one bad merge from divergence.
- **Suggested action:**
  Pick one. Either (a) delete `AuditingStore` and have the deep-dump
  script enable `_audit_enable_capture()` on the same store the driver
  uses, or (b) delete the `_audit_record` hook and let the driver wrap
  its store in `AuditingStore` for the run. (a) is the smaller refactor
  given the live path already depends on the hook. Either way: align
  the default-arg lists so a wrapped call and an unwrapped call return
  the same data.

#### P2-03 · C1 dead code candidate · `politician_trades_cache.fetch` while domain is disabled

- **Location:** `src/backtest/providers/politician_trades_cache.py`
  (whole file) and `src/backtest/cache/fetcher.py:51` (writer map entry).
- **Confidence:** medium
- **Description:**
  Per user memory `project_politician_trades_disabled`, the
  `politician_trades` domain is commented out in
  `orchestrator/registry._build_provider_fns` and the analyst degrades
  gracefully. The cache provider, its `@register("politician_trades",
  "cache", …)` decorator, and the writer entry in `fetcher.py` are still
  present and tested. This is intentional ("keep fallback shell providers
  registered" per the provider-switching memory), but the file should
  carry a header comment explaining that the registration is deliberately
  kept active despite the domain being disabled upstream — otherwise a
  future cleanup will delete it.
- **Suggested action:**
  Add a module-level docstring to `politician_trades_cache.py` pointing
  at the `project_politician_trades_disabled` rationale, and a one-line
  comment next to the `_WRITER_BY_DOMAIN` entry. No code change.

#### P2-04 · C5 silent-failure attractor · `social_sentiment_cache.fetch` unconditionally returns empty

- **Location:** `src/backtest/providers/social_sentiment_cache.py`
  (whole file).
- **Confidence:** medium
- **Description:**
  The cache provider unconditionally returns an empty `SocialSentiment`
  object regardless of `(ticker, as_of)`. This is intentional (backlog
  B19, no historical free source), but the implementation surface is
  indistinguishable from "cache miss" — analysts cannot tell whether the
  empty payload means "no signal today" or "this provider is a stub".
  Cross-reference `test-policy §A.7` (silent-failure attractors): a
  positive assertion in the analyst should flag the difference, but the
  provider itself does not raise.
- **Suggested action:**
  Raise an explicit `ProviderNotAvailable` (or set
  `is_no_data=True` with a `reason="stub"` field on the model) so the
  caller can branch on the cause. At minimum, add a startup-time WARNING
  log when this provider is selected so backtest runs surface the stubbing
  in logs.

#### P3-02 · C7 doc/code drift · `cache/fetcher.py` excludes `politician_trades` from fetch script but writer map keeps the entry

- **Location:** `src/backtest/cache/fetcher.py` (writer map at top, plus
  the script-CLI list further down that omits the domain).
- **Confidence:** medium
- **Description:**
  The `_WRITER_BY_DOMAIN` map at the top of the module still includes
  `politician_trades`, but the fetcher CLI's domain list omits it. A
  reader scanning the map will conclude the domain is fetched, then be
  surprised by the CLI skipping it. Cosmetic, but a one-line comment
  resolves it.
- **Suggested action:**
  Add `# kept for shell-provider parity; CLI skips by default — see
  project_politician_trades_disabled` next to the writer-map entry.

### Audit+Reporting

#### P0-01 · C5 silent-failure attractor · `notable_holders` mapped to non-existent `as_of_date` field

- **Location:** `src/backtest/audit/telemetry.py:187` and
  `src/backtest/audit/upstream_verifier.py:101-102` (the `_filter_key`
  mapping).
- **Confidence:** high
- **Description:**
  Both audit modules carry a domain→PIT-field map. For `notable_holders`
  both files map to `"as_of_date"`. The `NotableHolder` model has no
  such field — its PIT column is `filed_at` (per `cache/schema.py`
  `NotableHolderRow.filed_at` and the model definition in
  `data/models.py`). The consequence: the tripwire computation in
  `tripwires.py` reads `max_filed_at` from the telemetry record, never
  finds it (because `telemetry.py` wrote under a key that doesn't exist
  on the rows), and silently never tripwires `notable_holders`. Leak
  detection for this domain is therefore disabled. This is exactly the
  silent-failure pattern called out in `feedback_silent_failures_loud_tests`:
  no exception, no log, just "everything looks fine" while a whole domain
  bypasses verification. Load-bearing because notable-holders changes
  drive the strategist's institutional-flow signals.
- **Suggested action:**
  Change `"as_of_date"` to `"filed_at"` in both files, and add an
  assertion at telemetry-record construction that every mapped field
  actually exists on the row model (catches future renames at write-time
  rather than silently). Pair with a tripwire-fixture test that asserts a
  late `filed_at` on a `NotableHolderRow` actually trips the
  `notable_holders` tripwire.

#### P2-05 · C1 dead code · `build_telemetry_record_from_logs` only referenced from tests

- **Location:** `src/backtest/audit/telemetry.py:77` (the function).
- **Confidence:** medium
- **Description:**
  `grep -rn build_telemetry_record_from_logs src/ tests/ scripts/` shows
  the function is defined in `telemetry.py` and referenced only by
  `tests/backtest/audit/test_telemetry.py`. The driver builds its
  per-tick record via the inline `_drain_logs_cache_hits` path
  (`driver.py:393-401`-ish region post-summary) rather than calling
  this helper. Either the helper is the canonical builder the driver
  *should* be using, or it's an abandoned earlier draft kept alive by
  its test.
- **Suggested action:**
  Decide which is canonical. If the helper is the intended public
  surface, refactor the driver to call it (and drop the inline
  drainer). If the driver's inline path is canonical, delete the helper
  and the test that exists only to keep it alive.

#### P2-06 · C1 dead code · `db_writes_recorded_at` field is always `{}` from the driver

- **Location:** `src/backtest/driver.py:366` (the empty-dict passthrough)
  and `src/backtest/audit/telemetry.py` (the field on the record schema).
- **Confidence:** medium
- **Description:**
  The driver constructs each per-tick audit record with
  `db_writes_recorded_at={}`, hardcoded. No code path computes a real
  value. The field appears designed for a future feature ("when did
  state writes land vs reads?") that was never implemented. Empty dict
  is a silent default — readers will assume the records contain real
  timing info.
- **Suggested action:**
  Either implement the timing capture (instrument the
  `BaseSessionService.append_event` call site in the driver) or remove
  the field from the record schema. If the feature is intentionally
  deferred, add a `# TODO(<spec-link>):` comment so it isn't deleted by a
  drive-by cleanup.
