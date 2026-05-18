# Phase 7.5 — config-as-truth — done

Closeout for the `worktree-phase7.5-config-as-truth` branch.

## Scope recap

Make `config/*.json` the single source of truth for every tunable the
runtime consults. Eliminate parallel hardcoded mirrors, retire the
"planned" loader fiction, and delete documented keys that the code
silently ignored.

Spec: `docs/Phase7.5-more-cleanup/specs/config_as_truth.md`
Plan: `docs/Phase7.5-more-cleanup/plans/config-as-truth-v1.md`

## What landed (16 commits)

1. `feat(backtest): add BacktestSettings typed loader` — Pydantic v2 model
   with `extra="forbid"`, `load_backtest_settings_from`, cached
   `get_backtest_settings()`, and `_reset_cache()` test hook.
2. `refactor(backtest): route Runner/reporting through BacktestSettings` —
   Runner accepts `settings=` injection; dict-access → attribute access.
3. `refactor(scripts): route backtest_settings.json through typed loader` —
   `backtest_report`, `backtest_audit_tick`, `debug_cache_audit`.
4. `refactor(backtest): delete tz/open_time/close_time; let calendar own
   session times` — schedule.py rewritten on top of
   `pandas_market_calendars`; early-close days now honoured.
5. `feat(data): promote earnings and short_interest lookback defaults` —
   added `earnings_lookback_quarters` (4) and `short_interest_lookback_days`
   (90) to `FetchDefaults`.
6. `test(contract): land lookback contract tests (xfail-staged)` — turned
   green after the analyst routings landed.
7. `refactor(smart_money): read lookbacks from config/data.json` — dropped
   `POLITICIAN_LOOKBACK_DAYS` and `HOLDER_LOOKBACK_DAYS` constants.
8. `refactor(fundamental): read insider_lookback_days from config` —
   dropped `_INSIDER_LOOKBACK_DAYS`.
9. `refactor(backtest providers): drop lookback_days defaults` —
   `lookback_days` is now a required kwarg on all five cache providers.
10. `chore(scripts): retire backtest_fetch's _ANALYST_LOOKBACK_DAYS mirror`.
11. `refactor(config): rename http_timeout_seconds →
    quiver_http_timeout_seconds and route quiver through config`.
12. `docs(config): reconcile README with Phase 7.5 schema changes`.
13. `chore: clean up ruff lints and stale removal comment`.
14. `test(smoke): switch end-to-end smoke to typed BacktestSettings`.
15. `fix(data): wrappers forward lookback_days to cache providers` —
    regression found in opus final review.  Adds
    `filings_lookback_days=90` to `FetchDefaults` and routes both
    `get_stock_news` and `get_company_filings` through `get_config()`.
    Pinned by `tests/contract/test_wrappers_supply_lookback_to_cache.py`.

## Behavioural shifts (read before merging)

These are **deliberate** runtime changes — not regressions.

- **Politician lookback: 30 → 90 days.** The hardcoded
  `POLITICIAN_LOOKBACK_DAYS = 30` in `smart_money/fetch.py` is gone; the
  analyst now reads `config/data.json::defaults.politician_lookback_days`,
  which has stood at 90 days for the cache fetcher all along. Expect a
  triple-wide window into congressional trades on the analyst side.
- **Notable-holder lookback: 90 → 180 days.** Same story —
  `HOLDER_LOOKBACK_DAYS = 90` retired in favour of
  `defaults.notable_holder_lookback_days = 180`.
- **NYSE early-close days now correctly emit a 13:00 ET close tick.**
  Day-after-Thanksgiving 2024 (and any other early-close day) used to be
  scheduled at the configured `close_time` (16:00 ET) — a silent PIT
  leak. `pandas_market_calendars` now owns session times.
- **`config/data.json` key rename.** `http_timeout_seconds` →
  `quiver_http_timeout_seconds`. Anyone with a local override of the old
  key will get a Pydantic `extra="forbid"` failure on load — expected.

## Verification

- Full default test suite (`pytest -m "not slow and not integration"`):
  859 passed, 2 pre-existing flakes unrelated to this branch.
- Slow end-to-end smoke (`tests/integration/backtest/test_end_to_end_smoke.py`):
  passes.
- Schedule probe (`generate_ticks(date(2024,11,29), date(2024,11,29))`):
  emits `open=09:30 ET`, `close=13:00 ET`. Confirms calendar-driven path.
- Contract tests (`tests/contract/`): 8 passed, no xfails left.

## Out of scope (intentionally deferred)

- Other providers (`finra.py`, `tiingo.py`, `fmp.py`) still carry their
  own `_HTTP_TIMEOUT` constants. Per spec D5 this Phase 7.5 round only
  touched Quiver; a follow-up can sweep the rest if/when those providers
  are exercised in earnest.
- PIT-correctness audit and per-window cache compartmentalisation remain
  on the backlog (memory: `project_backtest_pit_correctness_deferred`,
  `project_backtest_cache_compartmentalisation_deferred`).
