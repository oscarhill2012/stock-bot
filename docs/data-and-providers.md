# Data & Providers

> **Status:** Consolidated reference for StockBot's data layer. Single source
> of truth as of 2026-05-17. Supersedes (and replaces) the prior `v1` / `v2`
> / `v3` drafts — those files are removed in the same commit that lands this
> one. The journey lives in git history if anyone needs to retrace it.
>
> **Companion:** `docs/superpowers/specs/provider-research/` carries the
> fifteen per-row research reports and the free-wins audit that this doc
> distils.

This document answers four questions in order:

1. **What does each analyst currently see, what could it see, and what is
   structurally impossible to see for free?** (§3 — input surface)
2. **Which providers do we use, and what would it cost to upgrade?** (§5 –
   §6 — free catalogue + paid menu)
3. **What "free wins" sit in our own code, waiting to be plumbed?** (§4 —
   audit findings)
4. **What is the agreed scope of the next implementation push?** (§9 —
   decisions log, and the unified spec at
   `docs/Phase7-pre-backtest-cleanup/providers-and-silent-gaps-spec.md`
   which this doc is the brief for)

---

## 1. The five analysts at a glance

| # | Analyst | Engine | Lookback | Primary data domains |
|---|---|---|---|---|
| 1 | Technical | deterministic (TA-Lib + heuristics) | 1y daily bars | OHLCV |
| 2 | Fundamental | LLM (Gemini) | 30d insider, last 3 filings per form | Company ratios, MD&A, Risk Factors, Form 4 |
| 3 | News | LLM (Gemini) | 7d, ≤20 articles | Per-ticker news articles |
| 4 | Social | deterministic (heuristics) | snapshot | Reddit + Twitter + StockTwits aggregates |
| 5 | SmartMoney | deterministic (heuristics) | 30d politicians, 90d holders | Congressional trades, SC 13D/G, 13F, Form 144 |

All five run in parallel inside `analyst_pool` (a `ParallelAgent`); each
emits an `AnalystVerdict` (`lean`, `magnitude`, `confidence`,
`key_factors`, `is_no_data`). The Strategist aggregates them per ticker.

---

## 2. Procurement list (the 17-row data inventory)

Single deduplicated brief — one row per unique data type. Status reflects
the post-spec stack (after `providers-and-silent-gaps-spec.md` lands). PIT =
point-in-time correctness for backtest replay.

| # | Data type | Lookback | PIT | Chosen free provider | Status |
|---|---|---|---|---|---|
| 1 | Daily OHLCV (per ticker) | 2y | trivial | yfinance | wired |
| 2 | Daily OHLCV (SPY + 11 sector ETFs) | 2y | trivial | yfinance (`yf.download` bulk, once per tick) | new plumbing |
| 3 | 5-min intraday OHLCV | — | — | **deferred** (decision §9.2) | — |
| 4 | Options summary (`iv_rank`, `atm_iv_30d`, `put_call_ratio`) | — | none free | **dropped from v1 backtest** (decision §7.1) | — |
| 5 | Company ratios (full set, 13 fields) | snapshot | hard | edgartools XBRL + yfinance for 3 leaky fields | extend `pit_composite` |
| 6 | Latest earnings | last 4 quarters | trivial | Finnhub `/calendar/earnings` | new provider |
| 7 | SEC filings prose (10-K MD&A / Risk Factors / 8-K body) | last 3 per form | trivial | edgartools | extend for 8-K branch |
| 8 | Insider trades (Form 4) | 30d | trivial | edgartools | wired; extend for officer flags + derivative extras |
| 9 | Earnings call transcripts | — | — | **deferred** (decision §9.4) | — |
| 10 | Analyst consensus & targets | snapshot | hard | yfinance (`upgrades_downgrades` + `analyst_price_targets`) | new adapter |
| 11 | Short interest | 90d | trivial | FINRA API | new provider |
| 12 | News articles + sentiment + topic | 7d, ≤20/ticker | trivial | Alpha Vantage `NEWS_SENTIMENT` | new provider, swap from Tiingo |
| 13 | Social sentiment aggregates | snapshot + 30d baseline | impossible-cheap | StockTwits (forward-cache only) | new provider, accept `is_no_data` for pre-2026 windows |
| 14 | Politician trades | 30d (by disclosure) | medium | FMP `/senate-trading` (live) + Senate/House Stock Watcher GitHub (historical backfill) | FMP wired; Stock Watcher new |
| 15 | SC 13D / 13G filings | 90d | trivial | edgartools | wired; **silent gap is in extractor** |
| 16 | 13F quarterly holdings | last 2 quarters | medium (45d lag) | edgartools (reverse-index built by us) | new provider |
| 17 | Form 144 planned sales | 30d | trivial | edgartools | new provider |

**Rows that drop out of v1:** #3 (intraday), #4 (options), #9
(transcripts). All three are reactivation candidates once paid budget
materialises or the proof-of-concept clears.

---

## 3. Input surface — current vs proposed

For each analyst: what the extractor consumes **today**, what will be
available **after the v1 spec lands**, and what is structurally
**impossible without paid providers**.

### 3.1 The "silent gap" pattern (read this first)

Three of five extractors today read fields the corresponding Pydantic model
does not carry, or read flattened dicts that drop fields the typed model
already populates. The features evaluate to `0.0` and the verdict logic
silently degrades. The audit (§4) confirms the pattern is broader than the
original v2 brief realised:

- **Provider returns rich typed object** → fetch callback **flattens** it
  into a loose dict → extractor **reads from the dict** and finds half the
  data missing.

The fix the v1 spec adopts is a one-line rule: **fetch callbacks pass
typed Pydantic models through unchanged; extractors read fields off the
typed object.** This is not a refactor — it's a change to one or two lines
per fetch callback, applied consistently.

### 3.2 Technical analyst

**Today consumes** (`src/contract/extractors/technical.py`):
- `bars[]` from `PriceHistory` — full OHLCV time series.
- Features emitted: `rsi_14`, `pct_change_5d/20d`, `vol_ratio_20d`,
  `atr_pct_14`, `dist_from_high_52w_pct` (**silently 0.0** — reads
  non-existent `high_52w` key), `dist_from_low_52w_pct` (same).

**After v1 spec:**
- All of the above + correctly-computed 52-week distances from `bars[]`.
- Plus `ratios` sub-key (already fetched, currently dropped — audit 1.1):
  `fifty_day_average`, `two_hundred_day_average`, `beta`, `last_price`,
  `market_cap`. Enables a `golden_cross` / `death_cross` flag and
  beta-aware confidence damping.
- Plus SPY + sector-ETF baseline via `state["reference_prices"]` —
  enables a `relative_strength_vs_spy` / `relative_strength_vs_sector`
  family of features (single largest missing signal in any equity TA
  framework).

**Structurally impossible without paid providers:**
- Intraday / multi-timeframe context (5-minute bars, weekly bars beyond
  resampled-daily). Daily-only is fine for daily-cadence pipeline.
- Genuine options-implied signals (`iv_rank`, gamma exposure, put/call
  ratio with depth). yfinance gives a noisy live snapshot only.

### 3.3 Fundamental analyst

**Today consumes** (`src/contract/extractors/fundamental.py`):
- `CompanyRatios`: `trailing_pe`, `forward_pe`, `beta`, `dividend_yield`,
  `market_cap`, `sector`, `long_name`, `fifty_day_average`,
  `two_hundred_day_average`, `last_price`. Reads 7 more fields that **do
  not exist on the model** (peg, revenue_growth_yoy, profit_margin,
  debt_to_equity, roe, free_cash_flow, analyst_rating_avg) — all silently
  0.0.
- `filings[]`: `form_type`, `filed_at`, `mda_excerpt`,
  `risk_factors_excerpt`, `accession_no`, `url`. **8-K bodies are
  skipped** by the EDGAR adapter today.
- `Form4Bundle.trades[]` (common stock): `side`, `shares`,
  `price_per_share`, `insider_name`, `insider_title`, `filed_at`,
  `is_10b5_1`. **Does not read `transaction_code`** (P/S/F/G) — so
  open-market buys are not separated from tax-withholding noise.
- `Form4Bundle.derivatives[]`: counted only — `transaction_code == "M"`
  exercises and `"A"` grants. Every other derivative field (`strike_price`,
  `underlying_shares`, `expiration_date`, `is_10b5_1`, footnote) is
  discarded.

**After v1 spec:**
- `CompanyRatios` extended with 8 missing fields (XBRL-sourced for the 5
  PIT-correct ones: peg, revenue_growth_yoy, profit_margin, debt_to_equity,
  roe, free_cash_flow; yfinance snapshot for forward_pe + analyst_rating_avg
  marked as "leaky", documented in backtest manifest).
- `Filing.body_excerpt` (8-K bodies, ~1500 chars) populated.
- `Filing.items_8k: list[str]` populated (e.g. `["2.02", "9.01"]`) — enables
  Item-specific counters (5.02 executive departures, 1.01 material
  agreements).
- `InsiderTrade.transaction_code` consumed — separate `insider_open_market_buy_dollars`
  (P-only) from current `insider_net_dollars_30d` aggregate (currently
  polluted by F = tax withholding noise).
- `InsiderTrade` gains `is_officer`, `is_director`, `is_ten_percent_owner`
  booleans (sourced from Form 4 XML reporter flags, not regex over title
  strings — replaces brittle `_role_rank()`).
- `InsiderDerivativeTrade` gains `expiration_date`, `is_indirect_ownership`,
  `is_late_filed` — enables `insider_option_exercise_value` (in-the-money
  conviction vs underwater forced exercise), filters out family-trust noise.
- Latest quarterly earnings from Finnhub: `report_date`, `eps_actual`,
  `eps_estimate`, `revenue_actual`, `revenue_estimate`. `guidance_text` not
  free — derived from 8-K bodies by the Fundamental LLM.
- Analyst consensus / price targets from yfinance: `target_high`, `target_low`,
  `target_mean`, `recommendation_mean`, `number_of_analyst_opinions`,
  recent revisions (PIT-clean — `upgrades_downgrades` returns dated events).
- Short interest from FINRA: bi-monthly cadence, filter on
  `report_publish_date <= as_of` (~8 business-day publish lag).

**Structurally impossible without paid providers:**
- True XBRL PIT for `forward_pe` and `analyst_rating_avg` — both are
  snapshot-only on every free provider; they'll forward-leak in backtests
  and are flagged as such.
- Earnings call transcripts (decision §9.4 defers).
- Structured forward guidance (`guidance_text` as a named field) — no free
  source; LLM derives from 8-K bodies.

### 3.4 News analyst

**Today consumes** (`src/contract/extractors/news.py`):
- `headline`, `summary`, `source`, `url` from `NewsArticle`.
- Reads `item.get("polarity")` — **`NewsArticle` carries `sentiment`, not
  `polarity`**. Silent field-name mismatch → all polarity-derived features
  permanently 0.0.
- `published_at` is serialised through state but **never read by the
  extractor** — every article weighted equally regardless of age.
- Features: `news_count_7d`, `pct_news_positive/negative_7d`,
  `headline_polarity_mean_7d`, `social_volume_z` (dead code).

**After v1 spec:**
- Provider swap to Alpha Vantage `NEWS_SENTIMENT` — only free provider
  with per-article `sentiment_score`, canonical `topic`, and
  `relevance_score_per_ticker`. Extractor reads `sentiment` (matching the
  model field).
- Recency weighting added — `news_count_24h`, `news_count_72h`,
  `headline_polarity_recency_weighted` (exponentially decaying),
  `hours_since_latest_news`.
- Topic-aware filtering at the extractor — count earnings-tagged news
  vs noise.

**Structurally impossible without paid providers:**
- Article cluster dedup (`cluster_id`) — no free provider exposes
  syndication grouping. Drop to nice-to-have.
- Real-time event-driven cadence (vs daily polling). Free quotas force
  batch fills.

### 3.5 Social analyst

**Today consumes** (`src/contract/extractors/social.py`):
- A flattened `{platform: {mention_count, positive_score, negative_score}}`
  dict — the fetch callback strips the typed `SocialSentiment` object and
  drops `aggregate_score` + per-snapshot `score` that the provider
  populates.
- Features: `mention_count_total/reddit/twitter`, `aggregate_score`
  (recomputed from scratch), `platform_score_disagreement`,
  `score_velocity_24h` (hard-coded 0.0 placeholder).

**After v1 spec:**
- Stop flattening — pass `SocialSentiment` through to the extractor; read
  `score` and `aggregate_score` directly. Two-line free win.
- `SocialSentimentSnapshot.platform` Literal extended with `"stocktwits"`.
- StockTwits forward-cache provider wired (`/streams/symbol/{SYM}.json`).
  Live-only — does not back-fill historical windows. After ≥30 days of
  capture, 2026+ backtests get real social signal; 2023 SVB-era backtest
  still soft-fails to `is_no_data=True` per decision §9.3.
- `score_velocity_24h` wired — store prior-tick aggregate in memory and
  diff. No new data, just a one-line plumb.

**Structurally impossible without paid providers:**
- Historical social aggregates for pre-2026 backtest windows (Quiver
  Premium ~$30/mo bundles this with politician historical).
- Influencer-weighted vs raw mention counts.
- Twitter / X coverage (their free API is effectively closed).

### 3.6 SmartMoney analyst

**Today consumes** (`src/contract/extractors/smart_money.py`):
- `politicians[ticker]`: `side`, `transaction_date`, `disclosure_date`,
  `politician`, `chamber`, `party`. **Reads `filing.get("amount")`** —
  `PoliticianTrade` carries `amount_min_usd`/`amount_max_usd`, never a
  flat `amount` key → every `total_dollar_value_*` and `net_flow_dollar`
  is 0.0. Verdicts run on **trade counts only**.
- `notable_holders[ticker]`: **fetched, cached, never read.** Whole data
  source wasted at the extractor boundary.
- Features: `n_politicians`, `n_buys_30d`, `n_sells_30d`,
  `total_dollar_value_buys/sells` (broken), `net_flow_dollar` (broken),
  `is_no_data`.

**After v1 spec:**
- Politician `amount` plumbing fixed — use midpoint of
  `amount_min_usd`/`amount_max_usd`.
- `PoliticianTrade.asset_type` populated by FMP — filter bond trades out
  of stock-signal aggregates.
- Notable-holder features emitted: `n_active_13d_30d`, `n_passive_13g_30d`,
  `n_amendments_30d`, `notable_holder_present`.
- SC 13D/G filing body parsed (per audit 2.9): `NotableHolder` gains
  `percent_of_class`, `shares_held`, `purpose_excerpt`. Distinguishes 5%
  passive index reweight from 9% activist position — currently lumped
  together.
- 13F quarterly holdings from edgartools (reverse-index of ~100-300
  curated fund CIKs): `fund_name`, `shares`, `value`,
  `change_vs_prior_quarter`. Asymmetric 45-day filing lag tolerated by
  strategist.
- Form 144 planned sales from edgartools: `insider_name`, `planned_shares`,
  `planned_date`, `filed_at`. XML availability begins ~2022 — pre-2022
  windows correctly return empty.
- `quiver.py` PIT bug fixed (filters on `disclosure_date` not
  `transaction_date`) for completeness — Quiver itself stays inactive
  (paid-only).

**Structurally impossible without paid providers:**
- Government contract awards (USAspending.gov is free but out of scope
  for v1).
- Hedge fund letters (qualitative, no clean API).
- Real-time 13F (45-day lag is SEC-imposed, not provider-imposed).

---

## 4. Free wins (audit findings folded into the v1 spec)

The free-wins audit (`docs/superpowers/specs/provider-research/free-wins-audit.md`)
walked every model, extractor, and provider adapter. 20 findings; 11
included in the v1 spec; 3 skipped (inactive providers); 6 deferred to
backlog.

### 4.1 Included in v1 spec

**Silent extractor gaps (4):**
1. Technical extractor opens the `ratios` sub-key (50d/200d MA cross,
   beta-aware confidence). *Audit 1.1.*
2. Insider `transaction_code` filtering (P/S/F/G) — separates real
   conviction from tax-withholding noise. *Audit 1.3.*
3. News recency weighting + 24h/72h counts. *Audit 1.4.*
4. Form 4 derivative table beyond exercise/grant counting (option
   exercise value, planned-ratio mirror). *Audit 1.2.*

**Provider-side dropped data (5):**
5. yfinance `info` adapter expansion — `fiftyTwoWeekHigh/Low`,
   `recommendationMean`, `numberOfAnalystOpinions`. Rides along with the
   8-field `pit_composite` extension. *Audit 2.1 subset.*
6. EDGAR Form 4 reporter flags — `is_officer`, `is_director`,
   `is_ten_percent_owner`. Replaces brittle title-regex
   `_role_rank()`. *Audit 2.5.*
7. EDGAR filing `items` list (8-K Item enumeration). *Audit 2.4.*
8. Form 4 derivative extras — `expiration_date`, `is_indirect_ownership`,
   `is_late_filed`. *Audit 2.6.*
9. FMP politician `asset_type` filter — drops bond trades from stock
   aggregates. *Audit 2.8.*
10. SC 13D/G filing body parsing — `percent_of_class`, `shares_held`,
    `purpose_excerpt`. Same EDGAR pattern as 10-K MD&A extraction.
    *Audit 2.9.*

**Model fields with no consumer (2 — fixed by "stop flattening" rule):**
11. Social `score` (per-snapshot) and `aggregate_score` (top-level)
    passed through unchanged. *Audit 3.6 + 3.7.*

### 4.2 Skipped (inactive provider / deprecated)

- Finnhub news `category` field (audit 2.2) — we're swapping away from
  Finnhub as primary news on Row #12. Re-flag if Finnhub is re-promoted.
- Tiingo news `tags` / `crawlDate` (audit 2.3) — Tiingo deprecated for
  news in favour of Alpha Vantage.
- Quiver `Held` / `excess_return` (audit 2.7) — Quiver is paid-only and
  inactive. Re-flag if subscription happens.

### 4.3 Deferred to backlog

Fields below survived a "would the strategist demonstrably use this"
filter; flagged for re-evaluation after the first backtest reveals which
signals matter. None are silent gaps — adding them is genuine new feature
work, not unblocking existing code.

- `CompanyRatios.long_name`, `sector` (audit 3.1) — identity metadata
  with no current feature consumer.
- `CompanyRatios.dividend_yield` (audit 3.3) — populated, no extractor
  reads it.

---

## 5. Free provider catalogue (post-spec)

Seven active providers + one static data source. Each entry lists what it
serves, the binding free-tier constraint, and any new-file / extension
work the v1 spec requires. Total monthly cost: **$0**.

### 5.1 edgartools — SEC EDGAR (MIT-licensed Python library)

- **Rows served:** 5 (partial — XBRL fundamentals), 7 (filings prose), 8
  (Form 4), 15 (SC 13D/G), 16 (13F — new), 17 (Form 144 — new).
- **Binding constraint:** SEC fair-access policy = 10 req/s per IP
  (`_LIMITERS["edgar"]` already capped at 600/min). Mandatory `User-Agent`
  via `set_identity()` (`data.secrets.require_key("EDGAR_IDENTITY")`).
- **History depth:** Full archive back to 1994. Form 144 XML availability
  begins ~2022 — pre-2022 windows correctly return empty.
- **v1 spec work:** Existing providers (`filings`, `insider_trades`,
  `notable_holders`) get adapter extensions for 8-K bodies, officer
  flags, derivative extras, and SC 13D/G body parsing. New providers for
  13F (with curated CIK reverse-index) and Form 144.
- **Headline:** the single biggest force-multiplier in the stack —
  6 rows on one library, one identity, one rate-limit bucket.

### 5.2 yfinance — Yahoo Finance unofficial Python scraper

- **Rows served:** 1 (OHLCV per ticker), 2 (OHLCV SPY + 11 sector ETFs),
  5 (3 snapshot fields: `forward_pe`, `analyst_rating_avg`, plus the new
  `fiftyTwoWeekHigh/Low` from audit 2.1), 10 (`upgrades_downgrades` +
  `analyst_price_targets`).
- **Binding constraint:** No formal quota; opaque Yahoo IP-level throttle
  (community-reported ~2000 req/hour). Bulk `yf.download([…tickers],
  threads=True)` is the cheap path for Row 2.
- **History depth:** 20+ years OHLCV; analyst `upgrades_downgrades`
  several years deep (967 rows for AAPL).
- **v1 spec work:** Existing `stats/yfinance.py` extended for audit 2.1
  fields. New adapter `analyst_consensus/yfinance.py` for Row 10.
- **ToS posture:** Personal / research use only. Acceptable for
  pre-deployment per decision §9.1. Tagged in registry as
  "swap-before-live".

### 5.3 Finnhub (`finnhub-python` SDK)

- **Rows served:** 6 (earnings — primary).
- **Binding constraint:** 60 calls/min, 30 calls/sec global. Comfortably
  handles 50-100 tickers daily.
- **History depth:** Multi-year for earnings calendar.
- **v1 spec work:** New `src/data/providers/earnings/finnhub.py`.

### 5.4 Alpha Vantage `NEWS_SENTIMENT`

- **Rows served:** 12 (news — primary). Cannot serve other Alpha Vantage
  endpoints without breaking Row 12.
- **Binding constraint:** **25 calls/day hard cap** (shared across all
  Alpha Vantage functions on a free key). 5 calls/min. Forces
  multi-ticker batching (`tickers=t1,t2,…`) + 30-day `time_from`/`time_to`
  chunks. For 50 tickers × 30-day SVB window: ~10 calls (half a day at
  25/day budget). For 50 tickers × 2-year backfill: ~240 calls (~10
  days). See §6 paid menu if scaling pushes this into pain.
- **History depth:** Archive commonly ~2022 onward per docs samples.
  **Verify empirically on first cache fill** (verification checklist §8).
- **v1 spec work:** New `src/data/providers/news/alpha_vantage.py`;
  `config/data.json` swap from current `news: "tiingo"`.

### 5.5 FINRA API Platform

- **Rows served:** 11 (short interest).
- **Binding constraint:** OAuth2 client-credentials flow; bearer token
  ~12h expiry; no published per-minute cap on the consolidated
  short-interest endpoint. Bi-monthly cadence → ~50 records/fortnight for
  a 50-ticker watchlist.
- **History depth:** Multi-year archive (≥2010 for most listed names).
- **v1 spec work:** New `src/data/providers/short_interest/finra.py`.
  **Must use the exchange-listed (NYSE/Nasdaq) dataset path, not OTC** —
  verify production endpoint against FINRA Data Browser before greenlit
  (verification checklist §8).

### 5.6 FMP — Financial Modeling Prep

- **Rows served:** 14 (politician trades — live).
- **Binding constraint:** 250 calls/day on Basic. 50 tickers × 2
  calls/day = 100/day (comfortable); 100 tickers = 200/day (near
  ceiling).
- **History depth:** Endpoint returns full ticker history per call (no
  server-side date filter).
- **v1 spec work:** Already wired correctly at
  `src/data/providers/politician_trades/fmp.py` with proper
  `disclosure_date`-based PIT filter. v1 spec adds the `asset_type`
  field (audit 2.8) and `link`/`comment` fields (audit 2.8 follow-on).
- **Concentration warning:** FMP appears in the paid menu for 6 rows but
  the free quota only supports Row 14. Do not promote to other rows
  without going paid.

### 5.7 StockTwits Public API

- **Rows served:** 13 (social — live forward-cache only).
- **Binding constraint:** Public unauthenticated
  `/streams/symbol/{SYM}.json` ~200 req/hour per IP (verify before
  procurement — verification checklist §8). No daily cap.
- **History depth:** **None.** Pagination walks backwards from now;
  cannot serve historical backtest windows.
- **v1 spec work:** New `src/data/providers/social_sentiment/stocktwits.py`.
  Forward-caches into the SQLite golden cache so 2026+ backtests
  eventually replay real social data; pre-2026 windows continue to
  `is_no_data=True` per decision §9.3.

### 5.8 Senate / House Stock Watcher — static GitHub repos

- **Rows served:** 14 (politician trades — historical backfill).
- **Binding constraint:** Not an API. `git clone` + `git pull`. GitHub
  raw-file rate cap (60 req/hour unauthenticated) only matters on initial
  clone.
- **History depth:** Senate ~2020 onward; House slightly newer. Covers
  2023 SVB window. **Verify repo handles + maintainer status before
  integration** (verification checklist §8) — maintainer (`jeremiak`) has
  stepped back at least once; community forks exist.
- **v1 spec work:** New `src/data/providers/politician_trades/stock_watcher.py`.
  Reads cloned JSON, applies same `disclosure_date`-based PIT filter as
  FMP; optionally combines with repo commit date for strict PIT.

---

## 6. Paid upgrade menu (sorted by leverage per dollar)

If/when paid budget materialises (decision §9.1 makes this a post-concept-proof
question), this is the order to spend in. Each row lists what you get,
what it removes from the v1 stack, and the monthly bill.

### 6.1 Alpha Vantage Premium — $49.99/mo — best single-sub leverage

Closes both of v3's top-named risks in one purchase:
- Lifts the 25/day `NEWS_SENTIMENT` cap (Row #12) → no batch-multi-ticker
  gymnastics, no Finnhub fallback wiring needed.
- Adds `HISTORICAL_OPTIONS` → re-enables Row #4 (options summary), which
  v1 drops entirely.

**Recommended pattern:** Buy one month, fill the cache fast, cancel. Don't
treat as an ongoing sub unless you're consistently growing the watchlist.

### 6.2 Tradier brokerage — free with account — second-biggest free win

Not strictly paid (free with a brokerage account), but flagged here
because it removes the **yfinance personal-use ToS flag** that's the
biggest "swap-before-live" blocker. Replaces yfinance OHLCV with an
official API. Throws in an ORATS-derived IV snapshot. Worth opening
regardless of any other spend.

### 6.3 Polygon.io — $29/mo per product line — second-best paid option

Two separate $29 product lines:
- **Stocks Starter ($29/mo)** — premium news + history. Closes Row #12
  budget pain.
- **Options Starter ($29/mo)** — closes Row #4 history.

Either alone is competitive with Alpha Vantage Premium on its own row;
together they're $58 vs AV Premium's $49.99 — AV wins on cost
consolidation.

### 6.4 Quiver Quant Premium — ~$30/mo — closes Row #13 historical

Bundles social (WSB historical) and politician trades (historical) into
one sub. Closes the **only structurally-impossible-free** gap (Row #13
historical aggregates). Lower priority because decision §9.3 already
accepts `is_no_data=True` for pre-2026 social.

### 6.5 FMP Ultimate — $99/year (~$8/mo!) — quiet bargain

Unlocks the FMP quota across rows 5 (ratios), 6 (earnings), 10 (analyst
consensus), 14 (politicians — already wired). At <$10/mo this is the
cheapest "consolidate multiple rows on one vendor" option. Low priority
because the v1 free stack already covers all four of those rows; it
becomes the natural pick if any of those free providers degrades.

### 6.6 EODHD All-In-One — $79.99/mo — if XBRL extraction breaks

Falls into the "would never buy first" category for v1 but worth knowing.
Closes Row #5 (ratios) without XBRL plumbing. Only relevant if our
edgartools-based XBRL extraction proves operationally unstable.

### 6.7 sec-api.io — $50-200/mo — if EDGAR rate limits bite

Replaces the entire edgartools dependency for Rows 7 / 8 / 15 / 16 / 17.
Only relevant if SEC's 10 req/s cap becomes a bottleneck (it won't at
50-ticker scale).

### 6.8 Finnhub Premium — ~$50/mo — niche

Promotes Finnhub to serve Rows 10 + 11. Both already have viable free
options; only worth it if consolidation matters more than $50.

---

## 7. Gaps accepted

Two rows have **no viable free PIT path** for the v1 first backtest.
Both gaps were pre-authorised in decisions §9.2 / §9.3 / §7.1.

### 7.1 Row #4 (Options summary) — DROPPED from v1

- Every free options provider is snapshot-only. `iv_rank` specifically
  needs a trailing 252 trading days of daily ATM IV that no free API
  serves or back-fills.
- yfinance's `impliedVolatility` is a known-noisy black-box value with
  quality issues for illiquid contracts. Shipping it would teach the
  Technical analyst to react to noise.
- **Reactivation path:** Alpha Vantage Premium ($49.99 — see §6.1) or
  Polygon Options Starter ($29 — see §6.3).

### 7.2 Row #13 (Social sentiment) — DEGRADED per decision §9.3

- All free social providers are live-only. No retroactive coverage of
  2023 SVB window or any pre-2026 backtest window.
- StockTwits forward-cache provider lands now (§5.7). After ≥30 days of
  capture, 2026+ backtests get real social data; pre-2026 windows
  continue to `is_no_data=True`. The Strategist already tolerates absent
  social verdicts (well-tested path).
- **Reactivation path:** Quiver Quant Premium ($30 — see §6.4) for
  historical aggregates.

### 7.3 Field-level drops within otherwise-shipping rows

| Row | Field dropped | Why |
|---|---|---|
| 6 | `guidance_text` | No free provider serves structured forward guidance. Derive from 8-K bodies (Row 7) via the Fundamental LLM. |
| 12 | `cluster_id` | No free provider exposes dedup hash or canonical syndication grouping. |
| 5 | `forward_pe`, `analyst_rating_avg` (snapshot leak) | Not derivable from XBRL. Both marked leaky and documented in backtest manifest. |
| 5 | `beta` (snapshot leak) | Computable from Rows 1/2 price history (clean PIT) — deferred to v2 spec. |

---

## 8. Verification checklist (TBD before implementation)

Items the per-row reports flagged for empirical re-confirmation. Group A
gates spec implementation; Group B is opportunistic during implementation.

### Group A — verify before opening implementation tickets

1. **Alpha Vantage `NEWS_SENTIMENT` archive depth** — confirm coverage of
   the 2023 SVB-stress window. Empirical test: one call with
   `time_from=20230301T0000&time_to=20230331T0000` on a single mega-cap
   ticker.
2. **FINRA exchange-listed short-interest dataset endpoint path** —
   confirm against FINRA Data Browser. The context7-indexed example
   shows the OTC dataset; the sibling exchange-listed path must be
   identified. Also confirm production access for free-tier credentials
   (the "Mock" suffix in some endpoint names suggests a sandbox split).
3. **StockTwits free-tier rate limit** — historically ~200 req/hour per IP
   on `/streams/symbol`; flagged "verify before procurement".
4. **Senate & House Stock Watcher repo handles + maintainer status** —
   confirm both repos cover 2023 SVB window and cron is fresh.
5. **FMP `/senate-disclosure` chamber semantics** — endpoint is misnamed
   (carries House disclosures despite "senate" in URL). Sanity-check
   `office` field on one House row and one Senate row.

### Group B — verify during implementation

6. **Finnhub earnings estimate vintage** — confirm `epsEstimate` /
   `revenueEstimate` are T-1d snapshots, not post-release revised
   numbers. Spot-check AAPL 2023-Q1 and TSLA 2023-Q2 against a public
   archive.
7. **edgartools amendments in 8-K** — confirm `body_excerpt`
   concatenation policy across multi-Item 8-Ks (2.02 + 9.01).
8. **edgartools v5.31.0 Schedule13D / Schedule13G parser** — the v1 spec
   uses the metadata-only path plus body extraction; track the richer
   parser (`purpose_of_transaction`, `total_percent`, `ReportingPerson`
   arrays) as a v2 backlog item.
9. **Form 144 date coercion** — XML date fields are `MM/DD/YYYY` strings,
   not ISO. The existing `_coerce_date` in `insider_trades/edgar.py` uses
   ISO and will silently return `None`. New helper required in Form 144
   adapter.
10. **CUSIP → ticker resolution quality (Row 16)** — ~95% for US
    large-cap equity. Log drop rate during cache fill.

---

## 9. Decisions log

Carried verbatim from the brainstorming session that produced the v2 doc
(2026-05-17), plus the audit-driven scoping decision and one new
implication added by the v3 synthesis pass. Each decision sets scope for
the v1 spec.

### 9.1 Budget — free-only for v1

Strict free-only for the first backtest. Paid is acceptable later, not
now. **Rationale:** prove the multi-agent concept end-to-end before
justifying spend; if the bot cannot signal on free data, paid will not
rescue it. **Implication:** rank providers by cost first; accept degraded
fundamental coverage and likely-absent historical social aggregates.

### 9.2 Bar interval — daily, 1 tick/day

Daily OHLCV bars only. Pipeline ticks once per trading day. Two ticks/day
flagged for a later phase. **Rationale:** tick cadence should match data
refresh cadence. **Implication:** Row #3 explicitly deferred.

### 9.3 Social analyst — keep, accept degraded

Do not retire the Social analyst. Accept that it may soft-fail via
`is_no_data=True` if no free provider serves historical aggregates.
**Rationale:** Strategist already tolerates absent social verdicts; the
cost of *keeping* the analyst wired is near zero; ripping it out and
re-adding later is non-trivial. **Implication:** Row #13 ships at degraded
quality.

### 9.4 Earnings transcripts — deferred

Drop Row #9 from v1. Backlog for a future iteration. **Rationale:**
prompt-size pressure on the Fundamental LLM; requires a separate provider
class. **Implication:** revisit if backtest results show the fundamental
signal is weak.

### 9.5 Silent-gap fixes — coupled with provider work

All silent-gap bugs are fixed as part of the same unified plan as the
provider implementation. **Rationale:** "no point backtesting" while
extractors silently produce 0.0 features. **Implication:** the v1 spec
covers providers, silent gaps, model extensions, extractor fixes, and
`reference_prices` plumbing in one PR.

### 9.6 Sector ETF + SPY — fetched once per tick

Reference price series (SPY + 11 sector ETFs) fetched once per tick into
`state["reference_prices"]` keyed by ETF ticker. Sector → ETF mapping via
an in-code lookup table keyed on `CompanyRatios.sector`. **Rationale:**
prevent per-ticker state duplication bloating trace files (~12,500
redundant bar rows per tick at 50 tickers). **Implication:** pre-tick
reference populator runs before the per-ticker fetch loop.

### 9.7 Watchlist trajectory — 20 min / 50 target / 100 end-goal

Minimum 20 tickers for the first backtest. Target 50 by end of first
paper-trading phase. End-goal 100 tickers, with paid providers acceptable
at that scale. **Implication:** provider research prioritises free options
viable up to 50; per-row note on "scales to 100 free? paid?" so the
upgrade path is visible.

### 9.8 Framing — requirements before providers

Provider names stay out of decisions and requirements analysis until the
provider-research phase completes. Decisions are framed by data shape,
scope, and engineering trade-offs — not by which API can serve them. This
was the v2 doc's central contribution.

### 9.9 Audit scope — one clean push (Tier A + Tier B, skip 2.2/2.7)

The free-wins audit produced 20 findings. After honest cost/benefit
review, the v1 spec includes:
- All Tier A findings (7 items) — silent gaps + small adapter expansions.
- All Tier B findings (4 items) — derivative table features, 8-K items
  list, Form 4 derivative extras, SC 13D/G body parsing.

Skipped: audit 2.2 (Finnhub news category — provider deprecated for news),
2.7 (Quiver fields — inactive provider). **Rationale:** "ensure we are
clean and ready for backtest rather than leaving stuff for later". Spec
grows from ~3 days of focused work to ~5 days; no architectural risk;
all touches stay additive. **Implication:** spec model-extension count
grows from 3 files to 6; extractor-fix count from 4 to 11. No new
abstraction or refactoring.

---

## 10. Next step

Single artifact to read after this doc:

- **`docs/Phase7-pre-backtest-cleanup/providers-and-silent-gaps-spec.md`**
  — the unified implementation spec covering providers, silent-gap fixes,
  model extensions, extractor wiring, and the `reference_prices`
  plumbing. Phased to land as one PR (one commit per phase). After
  approval, the writing-plans skill produces the per-task implementation
  plan against this spec.

Then: implement, run the SVB-window backtest, audit trace files for
`is_no_data=True` on anything other than Row #13 (social — expected) and
Row #4 (options — dropped).
