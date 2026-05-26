# Vocab inventory — `data`

Audited 2026-05-26.

## Analyst-facing domain wrappers (`src/data/__init__.py`)

Public surface — what agents call.  Per §7.4, the authoritative count
is 5 + 3 (smart-money fan-out) = 8 wrappers.

- `get_price_history(ticker, period="1y", interval="1d", *, as_of, phase)` → `PriceHistory`
- `get_company_ratios(ticker, period="1y", interval="1d", *, as_of)` → `CompanyRatios`
- `get_stock_news(ticker, from_date, to_date, *, limit=50, as_of)` → `list[NewsArticle]`
- `get_social_sentiment(ticker, *, as_of)` → `SocialSentiment`
- `get_insider_trades(ticker, *, lookback_days=30, as_of)` → `Form4Bundle`
- `get_public_figure_trades(ticker | None, *, lookback_days=90, as_of)` → `list[PoliticianTrade]`
- `get_notable_holders(ticker, *, lookback_days=180, limit=20, as_of)` → `list[NotableHolder]`
- `get_company_filings(ticker, form_types=("10-K","10-Q","8-K"), limit=5, *, include_excerpts=True, as_of)` → `list[Filing]`

Wrappers for domains that exist in the registry but have **no
analyst-facing wrapper** — `earnings`, `analyst_consensus`,
`short_interest`, `options`.  See F-data-001.

## Provider registrations (`@register(domain, name, upstream, rate_per_minute, burst)`)

Format: `(domain, name)` — upstream / rate_per_minute / burst — file.

### Wrappers' active providers (per `config/data.json`)

- `(price_history, yfinance)` — yfinance / 60 / 30 — `providers/stats/yfinance.py:479`
- `(company_ratios, pit_composite)` — yfinance / 60 / 30 — `providers/company_ratios/pit_composite.py:463`
- `(news, finnhub)` — finnhub / 50 / 10 — `providers/news/finnhub.py:264`
- `(social_sentiment, finnhub)` — finnhub / 50 / 10 — `providers/social_sentiment/finnhub.py:53`
- `(insider_trades, edgar)` — edgar / 600 / 20 — `providers/insider_trades/edgar.py:659`
- `(politician_trades, fmp)` — fmp / 20 / 10 — `providers/politician_trades/fmp.py:209`
- `(notable_holders, edgar)` — edgar / 600 / 20 — `providers/notable_holders/edgar.py:283`
- `(filings, edgar)` — edgar / 600 / 20 — `providers/filings/edgar.py:234`

### Inactive providers (fallback "shells" registered for one-config-flip)

- `(company_ratios, yfinance)` — yfinance / 60 / 30 — `providers/stats/yfinance.py:527`
  — F-data-002: documented "unsuitable for PIT" in its own docstring.
- `(news, tiingo)` — tiingo / 60 / 20 — `providers/news/tiingo.py:105`
- `(news, alpha_vantage)` — alpha_vantage / 5 / 2 — `providers/news/alpha_vantage.py:233`
  — F-data-003: dedupe candidate.
- `(politician_trades, quiver)` — quiver / 30 / 10 — `providers/politician_trades/quiver.py:119`
  — F-data-011.

### Provider registrations with no analyst-facing wrapper

- `(earnings, finnhub)` — finnhub / 50 / 10 — `providers/earnings/finnhub.py:45`
- `(analyst_consensus, yfinance)` — yfinance / 60 / 30 — `providers/analyst_consensus/yfinance.py:132`
- `(short_interest, finra)` — finra / 60 / 20 — `providers/short_interest/finra.py:226`
- `(options, yfinance)` — yfinance / 60 / 30 — `providers/options/yfinance.py:49`
  (live-only shell — always returns `[]`)

— All four: F-data-001.

## Rate-limit budgets (`AsyncRateLimiter`, one per upstream)

Per `data/__init__.py:14-31` docstring + `@register` calls.

- `finnhub` — 50/min, burst 10 (shared by news, social_sentiment, earnings)
- `yfinance` — 60/min, burst 30 (shared by price_history, company_ratios,
  analyst_consensus, options, pit_composite)
- `edgar` — 600/min, burst 20 (shared by filings, notable_holders, insider_trades)
- `quiver` — 30/min, burst 10
- `fmp` — 20/min, burst 10
- `tiingo` — 60/min, burst 20
- `alpha_vantage` — 5/min, burst 2
- `finra` — 60/min, burst 20

`min_decision_interval_seconds()` returns 1 / slowest active limiter's
`rate_per_second`.  With the current active set the floor is governed
by `fmp` (20/min → 3 s) for politician_trades, replacing the historic
~2 s Quiver floor mentioned in `data/__init__.py:27-31` (the docstring
needs updating).

## Domain payload models (`src/data/models/`)

In active analyst use:

- `PriceHistory` — wraps `list[OHLCBar]` (`models/price_history.py`)
- `OHLCBar` (`models/market.py`)
- `CompanyRatios` (`models/company_ratios.py`)
- `NewsArticle` — includes `sentiment: float | None` (intentionally null
  for finnhub; only AV populates it — F-data-020)
- `SocialSentiment`, `SocialSentimentSnapshot` (`models/sentiment.py`)
- `Form4Bundle`, `InsiderTrade`, `InsiderDerivativeTrade`, `TradeSide` (`models/trades.py`)
- `NotableHolder`, `PoliticianTrade` (`models/trades.py`)
- `Filing` (`models/filings.py`)
- `SmartMoneyRaw` — ticker-first aggregate composed by the smart_money
  analyst's fetch from `PoliticianTrade` + `NotableHolder` (`models/smart_money.py`)
- `MISSING_TIMESTAMP`, `is_missing_timestamp` — sentinel for upstream
  rows lacking a parseable timestamp (`models/missing.py`)

In `registry.DOMAIN_SHAPES` but **no consumer code**:

- `EarningsHistory`, `EarningsReport` (`models/earnings.py`) — F-data-001
- `AnalystConsensusBundle`, `AnalystRating`, `AnalystRevision` (`models/analyst_consensus.py`) — F-data-001
- `ShortInterestSnapshot` (`models/short_interest.py`) — F-data-001
- `OptionContract` (`models/options.py`) — F-data-001

## State keys written / read by the data layer

The `data` package itself does **not** read or write `state[...]` keys
— it's a function-style API.  State plumbing happens at the agent
layer.  The wrappers do interact with one thread-local:

- `_FALLBACK_STATE.count` (`data/timeguard.py:57-87`) — per-thread
  wall-clock-fallback counter, drained by the backtest driver via
  `drain_wallclock_fallback_count()`.

## Config keys (`config/data.json`, parsed by `data/config.py`)

`providers: dict[str, str]` — one entry per domain in `_DOMAINS`:

- `price_history`, `company_ratios`, `news`, `social_sentiment`,
  `insider_trades`, `politician_trades`, `notable_holders`, `filings`,
  `earnings`, `analyst_consensus`, `short_interest`, `options`

`defaults: FetchDefaults` (consumed by `data/__init__.py` wrappers and
`scripts/backtest_fetch.py`):

- `news_lookback_days` (default 7) — `data/__init__.py:185`
- `insider_lookback_days` (default 30) — `agents/analysts/fundamental/fetch.py:223`
- `politician_lookback_days` (default 90) — `agents/analysts/smart_money/fetch.py:92`
- `notable_holder_lookback_days` (default 180) — `agents/analysts/smart_money/fetch.py:93`
- `notable_holder_limit` (default 20) — `agents/analysts/smart_money/fetch.py:98`,
  `scripts/backtest_fetch.py:308`
- `filings_per_form` (default 3) — `agents/analysts/fundamental/fetch_agent.py:107`
- `include_filing_excerpts` (default True) — same +
  `scripts/backtest_fetch.py:247`
- `filings_lookback_days` (default 90) — `data/__init__.py:320`

`quiver_http_timeout_seconds: float = 15.0` — `data/config.py:55`,
consumed only at `providers/politician_trades/quiver.py:90` (F-data-010).

## Other internal vocabulary

- `DOMAINS: frozenset[str]` — twice (`registry.py:101`, `config.py:18`)
  — F-data-012 (duplication for circular-import avoidance).
- `DOMAIN_SHAPES: dict[str, DomainShape]` — canonical return shape per
  domain; consumed by `tests/contract/test_provider_shapes.py`.
- `_REGISTRY: dict[(domain, name), _Entry]` (`registry.py:127`).
- `_LIMITERS: dict[upstream, AsyncRateLimiter]` (`registry.py:128`).
- `Provider[T]` — Protocol for async callables returning canonical
  domain payload (`registry.py:43`).  No code uses this as a type
  annotation; documentation only.
- `register(...)` — decorator factory (`registry.py:147`).
- `dispatch(domain, *args, **kwargs)` — public via
  `data._dispatch` re-export (`__init__.py:76`).
- `set_active_provider(domain, name) -> restore` — runtime swap helper
  used only by `src/backtest/runner.py:450` and the swap regression
  tests (F-data-007: doesn't validate name).
- `min_decision_interval_seconds()` — exposed; consumer: see grep
  (`grep -rn min_decision_interval_seconds src/ scripts/` returns
  only the definition and the data `__init__.py` re-export — possibly
  dead in current code; worth confirming in a follow-up).
- `active_upstreams()` — internal helper for the above.
- `AsOfRequiredError`, `resolve_as_of(candidate, *, allow_wallclock,
  site)`, `drain_wallclock_fallback_count()` (`timeguard.py`).
- `SecretMissingError`, `require_key(env_var)` (`secrets.py`).
- `with_retry(fn)` — tenacity wrapper for sync inner-fetch fns (`retry.py`).
- `MISSING_TIMESTAMP: datetime(1, 1, 1, tzinfo=UTC)`,
  `is_missing_timestamp(value)` (`models/missing.py`).
