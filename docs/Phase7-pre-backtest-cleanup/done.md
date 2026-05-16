# Phase 7 — Closeout (2026-05-16)

Implemented from `docs/Phase7-pre-backtest-cleanup/pre-backtest-cleanup.md`,
driven by the review in `code-review.md`.

## Blockers fixed

- **B1** — Wall-clock fallback tripwire wired via
  `data.timeguard.drain_wallclock_fallback_count()`; driver drains the
  per-tick counter and surfaces it on the audit tripwire.
- **B2** — `CLAUDE.md` (committed) and `.claude/CLAUDE.md` (gitignored, local
  only) now reference `scripts.backtest_fetch` instead of the dead
  `scripts.backtest_fill`.
- **B3** — `CachedDataStore` gains a per-domain MISSING_TIMESTAMP skip
  counter; the fetcher drains it and writes `fill_audit.json` beside the
  cache whenever shrinkage actually occurs.
- **B5** — Positive provider-level same-day-bar strip assertion added
  under `tests/backtest/leak_regressions/`, so a refactor that bypasses
  `price_history_cache` would no longer be hidden by the smoke-test's
  tripwire exclusion.
- **B6** — Initial-state key-set parity test guards drift between
  `orchestrator/tick.py` and `backtest/runner.py` seeding. `watchlist`
  was dropped from the required set (runner-only; live tick does not
  seed it).
- **B7** — `FakeBroker` is now seeded from each ticker's first OHLCV bar
  in-window via the new `_seed_initial_prices` helper; tickers with no
  data still default to `0.0`.
- **B8** — Forward-return backfill writes
  `forward_returns_actual_date` alongside `forward_returns` so holiday
  drift in the consulted bar is visible in every decision snapshot.

## Dead code removed

- **D2** — `baselines.spy.spy_metrics` deleted; regression test asserts
  it stays gone. `_metrics_from_series` and `SPYMetrics` retained
  (active internal callers).

## Deferred to Phase 8

- B4 (social_sentiment lookback asymmetry) — verified not needed at
  run-time; doc-only.
- D1 (`AuditingStore` consolidation), D3 (lifecycle scheduler),
  D4 (`replay_backtest.py`), D5/D6 (debug scripts), D9
  (`StockSignalBundle`).
- O1–O7 over-abstraction items, except O7 which sits with D1.
- Priority 2/3 test additions (#5–#10 in the review).

## Commits landed (9, in order)

```
02a03ae  fix(backtest): wire wall_clock_fallback_fired tripwire (B1)
799e306  chore(backtest): address review feedback on Task 1
6a91d62  test(backtest): guard initial-state key parity between live and runner (B6)
7665bd1  fix(backtest): seed FakeBroker from first OHLCV bar, not 0.0 (B7)
839a438  test(backtest): positive assertion that open-phase strips same-day bar (B5)
f95d4b8  feat(backtest): surface MISSING_TIMESTAMP write skips in fill_audit.json (B3)
c9a5dce  fix(backtest): record actual-bar dates beside forward returns (B8)
7c400ab  docs: rename CLI references from backtest_fill to backtest_fetch (B2)
94ef4d8  refactor(baselines): delete orphan spy_metrics (D2)
```

## Test count delta

Baseline (Step 0.2): 676 passing.  Final (Step 9.1): **700 passing,
5 deselected** — net **+24** new tests across Tasks 1, 2, 3, 4, 5, 6, 8.

## Ruff delta

Baseline: 39 pre-existing errors across `src/` + `tests/`.  Final: **27**
(several pre-existing violations were tidied as a side-effect of edits
in Tasks 1 and 6).  No new violations introduced.

## End-to-end smoke test

`tests/integration/backtest/test_end_to_end_smoke.py` — **1 passed** with
`audit_complete` and the existing tripwire assertion still clean.
