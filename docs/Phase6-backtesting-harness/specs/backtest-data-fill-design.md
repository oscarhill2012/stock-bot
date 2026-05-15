# Backtest data fill — design

## 1 — Scope and goal

The Phase 6 backtest harness in `src/backtest/` is feature-complete and runs
fully locally: it replays the unmodified live pipeline against a SQLite golden
cache, producing per-tick traces, decision snapshots, an equity curve and a
metrics file. It is already wired for the SVB-stress 2023-03 window, but the
cache is empty because **the live providers it relies on cannot produce
point-in-time (PIT) historical data**.

This spec covers the work needed to fill that cache from free historical data
sources, so the harness can actually replay a window. The deliverable is:

1. A backtest cache populated from free APIs for the SVB-stress 2023-03 window
   (and every subsequent window thereafter).
2. A live data surface that is structurally PIT-correct, so the same providers
   work for live trading and for backtest fill from a single code path.
3. Provider-agnostic: switching between providers (free Tiingo ↔ paid Quiver
   etc.) is a single `config/data.json` edit, never a code change.

### Out of scope

- **Social sentiment historical** — no free source exists. Live finnhub provider
  stays as a soft-fail. Add later as a bonus.
- **Cloud parallel execution** — `cache_path` and `runs_root` are already
  config-driven; a future commit can swap them to GCS paths + Cloud Run Jobs.
  No code restructure is needed to enable that path.
- **Live trading deployment** — separate work.


## 2 — Execution model and data flow

Three flows, kept clearly separate:

```
                            ┌────────────────────────────────────────────────┐
                            │  LIVE pipeline (production)                    │
                            │                                                │
                            │  agents/* ──> data.<wrapper>(...)              │
                            │              │                                 │
                            │              │  as_of=None (default)           │
                            │              ▼                                 │
                            │  registry.dispatch ──> provider(<as_of=now>)   │
                            │              │                                 │
                            │              ▼                                 │
                            │  upstream API ──> live response                │
                            └────────────────────────────────────────────────┘

                            ┌────────────────────────────────────────────────┐
                            │  BACKTEST FILL (one-shot, before replay)       │
                            │                                                │
                            │  scripts/backtest_fetch.py --window <key>      │
                            │              │                                 │
                            │              │  loops watchlist × domains,     │
                            │              │  passes as_of = window.end      │
                            │              ▼                                 │
                            │  data.<wrapper>(ticker, as_of=<historical>)    │
                            │              │                                 │
                            │              ▼                                 │
                            │  registry.dispatch ──> provider(<as_of=…>)     │
                            │              │                                 │
                            │              ▼                                 │
                            │  upstream API (historical query) ──> rows      │
                            │              │                                 │
                            │              ▼                                 │
                            │  CachedDataStore.write_<domain>  →  SQLite     │
                            └────────────────────────────────────────────────┘

                            ┌────────────────────────────────────────────────┐
                            │  BACKTEST REPLAY (per tick)                    │
                            │                                                │
                            │  Driver loop ──> agents/* (unchanged)          │
                            │              │                                 │
                            │              │  state["as_of"] = tick.as_of    │
                            │              ▼                                 │
                            │  data.<wrapper>(..., as_of=<tick>)             │
                            │              │                                 │
                            │              ▼                                 │
                            │  registry.dispatch ──> "cache" provider        │
                            │              │                                 │
                            │              ▼                                 │
                            │  CachedDataStore.read_<domain>  (PIT filter)   │
                            └────────────────────────────────────────────────┘
```

### Invariants

- **Live providers default `as_of=None`** → resolves to wall-clock "now"
  internally. Live code never has to think about `as_of`.
- **Backfill is the only caller that passes a historical `as_of`** to live
  providers.
- **Replay never calls live providers at all** — `Runner.run()` calls
  `set_active_provider(domain, "cache")` for every domain before the first
  tick (`src/backtest/runner.py:196`).
- **Cache schema is unchanged.** Every PIT column required (`filed_at`,
  `published_at`, `as_of_date`) already exists in `src/backtest/cache/schema.py`.

### Storage (local-first, cloud-later seam)

- `backtests/cache/store.sqlite` — shared cache, append-only, gitignored.
- `backtests/runs/<run-id>/` — per-run artefacts, gitignored.
- Cloud-later: `cache_path` and `runs_root` are already config-driven; switching
  to GCS or Cloud SQL is a config edit plus a small storage adapter. Not in
  this spec.


## 3 — Per-provider fix list

### Critical latent bug

The public wrappers in `src/data/__init__.py` pass `as_of=...` to
`_dispatch()`, but **none of the live leaf providers accept `as_of` in their
signature**. Every live call would raise
`TypeError: fetch() got an unexpected keyword argument 'as_of'`.

This has not been hit because the bot is not deployed (see
[StockBot deployment state — pre-deployment]). It must be fixed before any
live launch, regardless of the backtest work — so it is in scope here.

The cache providers in `src/backtest/providers/` already use the right shape
(`*, as_of: datetime, ..., **_unused`). Live providers must adopt the same
signature.

### Provider changes

| # | File | Action | Detail |
|---|---|---|---|
| 1 | `data/providers/insider_trades/edgar.py` | **Fix** | Replace `date.today() - timedelta(days=lookback_days)` with `as_of.date() - timedelta(days=lookback_days)`. Add `as_of` + `**_unused` to `fetch` and `_list_form4_filings`. |
| 2 | `data/providers/notable_holders/edgar.py` | **Fix** | Same fix as #1 on `_list_holder_filings`. Add `as_of` + `**_unused` to `fetch`. |
| 3 | `data/providers/filings/edgar.py` | **Fix** | `_list_filings` has no date filter. Add `filing_date=":{as_of.date().isoformat()}"`. Add `as_of` + `**_unused` to `fetch` and `_list_filings`. |
| 4 | `data/providers/news/finnhub.py` | **Patch** | Already honours `from_date`/`to_date`. Add `as_of` + `**_unused` to signature so dispatch does not TypeError. No data-logic change. |
| 5 | `data/providers/social_sentiment/finnhub.py` | **Patch** | Add `as_of` + `**_unused`. Premium-only endpoint stays soft-fail. |
| 6 | `data/providers/stats/yfinance.py` (`fetch_price_history`) | **Patch** | Add `as_of` + `**_unused`. Live behaviour unchanged (yfinance period is anchored on "now"). Backfill uses `period="max"` + client-side slice (already implemented in `backtest_fetch.py:75`). |
| 7 | `data/providers/company_ratios/pit_composite.py` | **New** | Replace the current yfinance.info-based ratios with a composite PIT-correct provider. Uses edgartools `EntityFacts.query().as_of(as_of.date())` for raw fundamentals (shares outstanding, EPS, DPS, sector, long_name) and yfinance `period="max" interval="1d"` OHLCV (sliced to `as_of`) for price-dependent fields (`last_price`, `market_cap = shares_out × close`, `trailing_pe = close / eps_ttm`, `dividend_yield = dps_ttm / close`, `fifty_day_average`, `two_hundred_day_average`, `beta` from 1y SPY correlation). yfinance stays as the `price_history` provider for the OHLCV domain. See *Composite provider rationale* below. |
| 8 | `data/providers/politician_trades/fmp.py` | **New** | Free alternative to Quiver: FMP `/senate-trading?symbol=…` + `/senate-disclosure?symbol=…` (250 calls/day free). Keep `quiver.py` registered as a fallback shell. |
| 9 | `data/providers/news/tiingo.py` | **New** | Free historical news provider: `/tiingo/news?tickers=X&startDate=…&endDate=…` (1000/day per ticker). Keep `finnhub.py` registered as a fallback shell. |
| 10 | `data/providers/politician_trades/quiver.py` | **Patch** | Add `as_of` + `**_unused`; replace `date.today() - lookback` with `as_of.date() - lookback`. Stays as fallback. |

### Composite provider rationale (`company_ratios`)

The `CompanyRatios` model carries three classes of field:

- **Identity** — `long_name`, `sector`. Available from XBRL submission metadata.
- **Raw fundamentals** — implicit in `trailing_pe` etc. via EPS, dividends,
  shares outstanding. Available from XBRL `EntityFacts` quarterly facts.
- **Price-dependent / technical** — `last_price`, `market_cap`, `trailing_pe`,
  `forward_pe`, `dividend_yield`, `fifty_day_average`,
  `two_hundred_day_average`, `beta`. None of these are pure XBRL; they all
  require a historical price series anchored on `as_of`.

A pure-XBRL provider would have to leave most of those `None`, which would
silently degrade signal quality vs live. Instead, the new provider is a
**single registered leaf** that internally composes XBRL + yfinance OHLCV. The
analyst contract (a fully populated `CompanyRatios`) is preserved.

This is the only domain that needs composite logic. It does **not** introduce
cross-domain dependencies at the registry level — the provider itself owns
both data calls. Other domains stay single-source.

### Plumbing pattern (applied uniformly)

Every live leaf provider follows the cache-provider signature:

```python
# BEFORE
async def fetch(ticker: str, lookback_days: int = 30) -> list[InsiderTrade]:
    ...
    from_iso = (date.today() - timedelta(days=lookback_days)).isoformat()
    ...

# AFTER
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 30,
    **_unused,                      # absorb kwargs other providers care about
) -> list[InsiderTrade]:
    ...
    from_iso = (as_of.date() - timedelta(days=lookback_days)).isoformat()
    ...
```

The `**_unused` is mandatory — it is what makes
[provider-switching-must-be-one-line] true. Any registered provider for a
domain must accept any keyword the wrappers pass.

### Config impact

One edit to `config/data.json` after the new providers land:

```jsonc
{
  "news":              "tiingo",         // was "finnhub"
  "company_ratios":    "pit_composite",  // was "yfinance"
  "politician_trades": "fmp",            // was "quiver"
}
```

Reverting that file is a complete rollback. The fallback providers stay
registered so the flip is always reversible without code changes.

### `scripts/backtest_fetch.py` cleanup

Once leaf providers honour `as_of`, the inline `_build_provider_fns` factory
becomes dead weight. Replace it with direct calls to the public wrappers:

```python
provider_fns = {
    "ohlcv":             lambda t, *, start, end: get_price_history(
        t, period="max", interval="1d",
        as_of=_as_of_close(end)),
    "company_ratios":    lambda t, *, start, end: _fill_quarterly_ratios(
        t, start, end),
    "news":              lambda t, *, start, end: get_stock_news(
        t, from_date=start, to_date=end,
        as_of=_as_of_close(end)),
    "insider_trades":    lambda t, *, start, end: get_insider_trades(
        t, lookback_days=(end - start).days + 14,
        as_of=_as_of_close(end)),
    "politician_trades": lambda t, *, start, end: get_public_figure_trades(
        t, lookback_days=(end - start).days + 14,
        as_of=_as_of_close(end)),
    "notable_holders":   lambda t, *, start, end: get_notable_holders(
        t, lookback_days=(end - start).days + 14,
        as_of=_as_of_close(end)),
    "filings":           lambda t, *, start, end: get_company_filings(
        t, as_of=_as_of_close(end)),
}
```

`_fill_quarterly_ratios` iterates `EntityFacts.query().as_of(quarter_end)`
for every quarter-end inside the window and returns
`list[(snapshot, quarter_end_date)]` so `Fetcher` writes one row per quarter.
Quarterly granularity matches what real analysts see between earnings.

### Unchanged

- `src/data/registry.py` — already provider-agnostic.
- `src/data/__init__.py` — already passes `as_of` correctly.
- `src/backtest/` cache, runner, driver — fully wired.
- `src/backtest/cache/schema.py` — schema already supports every PIT field.
- `config/backtest_settings.json` / `config/backtest_windows.json` — no change.


## 4 — Testing strategy

### Per-provider tests (live providers)

For each of the four changed/replaced live providers, two tests in
`tests/data/providers/<domain>/`:

1. **`as_of` plumbing test** — call the wrapper with
   `as_of=datetime(2023, 3, 10, tzinfo=UTC)`; assert the upstream HTTP call (or
   `Company.get_filings` arg, for edgartools) receives the right date filter.
   Mocked via `respx` / `pytest-httpx` for HTTP providers; monkeypatched
   `edgar.Company` for edgartools. This guards the bug we just fixed.

2. **Empty / degraded test** — provider with no data for the window returns
   `[]`, not an exception. Confirms the graceful-degradation contract.

For the three new providers (Tiingo news, FMP politician trades, edgartools
XBRL ratios), one additional test each:

3. **Live smoke** — marked `@pytest.mark.slow`, opt-in only. Hits the real API
   once for a known ticker + window to confirm the wire format hasn't drifted.
   Auto-skips when the relevant env var is absent.

### Registry-level test

4. **Provider-swap test** in `tests/data/test_provider_switching.py` — flip
   `config/data.json: news → finnhub`, dispatch a call, assert the finnhub
   coroutine ran. Flip to `tiingo`, assert tiingo ran. This guards the
   one-config-flip requirement so a future contributor can't silently
   regress it.

### Backfill integration test

One `@pytest.mark.slow` integration test (`tests/integration/backtest/
test_backfill_smoke.py`):

- Builds a 5-trading-day window with 1 ticker (AAPL).
- Calls `scripts.backtest_fetch.main()` programmatically.
- Asserts every domain has rows in the cache for AAPL with timestamps inside
  the window.
- Re-runs `main()`; asserts no new fetches happen (idempotency via
  `cache_runs.status='ok'`).

### Existing tests

- `tests/integration/backtest/test_end_to_end_smoke.py` already covers the
  replay path and should continue to pass unchanged.
- All non-slow tests must continue to pass:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`.


## 5 — Rollout

Six commits, each independently mergeable to `main`:

1. **`fix(providers): accept as_of kwarg across all leaf providers`**
   — pure plumbing fix; replaces `date.today()` with `as_of.date()` in the
   three buggy providers (insider_trades, notable_holders, filings, quiver);
   adds `as_of` + `**_unused` to all signatures (news, social_sentiment,
   yfinance ratios + price_history). Resolves the latent live `TypeError`. No
   new providers.

2. **`feat(news): add Tiingo provider`** — new `data/providers/news/tiingo.py`,
   registered alongside `finnhub`. Config unchanged.

3. **`feat(politician_trades): add FMP provider`** — new
   `data/providers/politician_trades/fmp.py`, registered alongside `quiver`.
   Config unchanged.

4. **`feat(company_ratios): add PIT-composite provider`** — new
   `data/providers/company_ratios/pit_composite.py` (XBRL fundamentals +
   yfinance historical OHLCV), registered alongside `yfinance`. Config
   unchanged.

5. **`chore(config): switch active providers for backtest-readiness`** — single
   `config/data.json` edit: `news → tiingo`, `politician_trades → fmp`,
   `company_ratios → pit_composite`. Reversible by reverting one file.

6. **`feat(backtest_fetch): backfill via public wrappers`** — drop the inline
   `_build_provider_fns` factory; call the public `data.get_*` wrappers
   directly with `as_of=window.end`. Adds quarterly XBRL snapshots for
   `company_ratios`.

After commit 1, the bot can actually be launched live without TypeError.
After commit 5, the live data surface is upgraded. After commit 6, the
backfill produces a clean SVB-2023 cache.


## 6 — Future work (documented seams, not implemented)

- **Parallel fill** — `Fetcher.run()` is sequential per ticker today. The
  `Fetcher` is already async, and the registry's per-upstream rate limiters
  already serialise concurrent calls correctly, so parallelism is one
  `asyncio.gather` + semaphore away. Add a `--concurrency N` flag to
  `scripts/backtest_fetch.py` when the watchlist outgrows 10 tickers. Before
  turning concurrency on, verify the SQLite cache opens in WAL mode (add
  `PRAGMA journal_mode=WAL` to `CachedDataStore.__init__` if not). The
  per-fetch `Session(engine)` pattern already isolates audit-row writes.

- **Cloud parallel execution** — when many backtest windows need to fan out,
  swap `cache_path` / `runs_root` to GCS-backed paths and run each window as a
  Cloud Run Job. No code restructure needed; only a small storage adapter and
  a job-queue wrapper.

- **Social sentiment historical** — no free source today. If a viable feed
  emerges (e.g. crawl-then-classify on archived Reddit/StockTwits dumps),
  add a new `social_sentiment` provider mirroring the same shape as the
  existing free providers.

- **Paid provider upgrades** — Quiver Quant for richer political-trades data,
  Tiingo paid tier for higher news quota. Both flip via `config/data.json`
  with zero code changes thanks to the fallback shells.


## References

- `src/backtest/runner.py:196` — domain swap to `"cache"` providers
- `src/backtest/cache/store.py` — PIT filter implementation
- `src/backtest/providers/` — cache-provider signature template that live
  providers must mirror
- `scripts/backtest_fetch.py` — backfill orchestration
- `config/data.json` — active provider mapping
- `docs/data-and-providers.md` — full provider research notes (May 2026)
- Memory: `feedback-provider-switching-must-be-one-line` — the
  one-config-flip requirement
