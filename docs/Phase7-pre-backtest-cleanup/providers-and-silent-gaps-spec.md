# Providers & Silent Gaps — v1 implementation spec

> **Status:** Pre-implementation. Ready for `writing-plans` once approved.
> **Brief:** [`docs/data-and-providers.md`](../data-and-providers.md)
> **Companions:** [`docs/superpowers/specs/provider-research/`](../superpowers/specs/provider-research/)
> — `agent_template.md` (per-row research methodology) and
> `free-wins-audit.md` (the audit this spec distils). The 15 row reports
> were summarised into the audit and removed in the same commit that
> moves this spec into `Phase7-pre-backtest-cleanup/`.
> **PR strategy:** one PR at the end of the seven phases; each phase lands
> as its own commit on a single feature branch.

---

## 1. Goal

Bring the data layer to the state where the SVB-stress 2023-03 backtest
can run **without silent zero-features and without leaving free signal on
the floor**. Three classes of work, bundled because the audit (`§4` of the
brief) showed they're entangled at the fetch/extractor boundary:

1. **Silent-gap fixes** — extractors today read fields that the typed model
   does not carry, or read flattened dicts that drop fields the typed model
   already populates. Features silently evaluate to `0.0` and the verdict
   logic degrades. Fix the eleven sites the audit identified.
2. **Provider shells** — add seven new provider adapters (Finnhub earnings,
   Alpha Vantage news, FINRA short interest, Stock Watcher politician
   backfill, StockTwits social forward-cache, yfinance analyst consensus,
   yfinance options live-only shell) so every Section-2 row has a free
   source it can be cached from.
3. **`state["reference_prices"]` plumbing** — fetch SPY + 11 sector ETFs
   once per tick (decision 9.6 in the brief) instead of duplicating the
   reference series per ticker. This unblocks the single biggest missing
   technical signal (`relative_strength_vs_spy`).

---

## 2. Non-goals (read this twice)

This spec is **additive and disciplined**, not a refactor. Specifically:

- **No new abstractions.** The provider/registry/extractor pattern stays
  exactly as-is. The `Provider` protocol does not change. Fetch callbacks
  do not get rewritten — they get the `flatten → typed-passthrough` line
  changed in the four places the audit pinpointed.
- **No model-class consolidation.** Models grow new fields, but no
  rehoming, no inheritance changes, no `BaseModel` re-shaping.
- **No PIT-correctness rework.** The unrelated leak inventory captured in
  the deferred PIT-correctness spec (`project_backtest_pit_correctness_deferred`
  memory) stays deferred. The only PIT-related fix that lands here is
  the `quiver.py` `disclosure_date` filter bug (one-line correctness
  fix, no architectural touch).
- **No swap of any analyst's deterministic-vs-LLM split.** All five
  analysts retain their current engine; the Social analyst stays in
  `is_no_data=True` for pre-2026 windows per decision 9.3 of the brief.
- **No backtest harness changes.** `scripts/backtest_fetch.py` and the
  Phase 6 harness consume the same `Provider` protocol; they should
  light up the new rows automatically once the registry is wired.
- **No CI / tooling work.** Lint + pytest gates stay as-is; the existing
  `pytest -m "not slow and not integration"` filter still applies to all
  new tests added here.

If something in the implementation pulls towards any of the above,
**stop and re-scope** — it belongs in a follow-up spec, not this one.

---

## 3. Scope summary

| Phase | Title | Files touched (new / changed) | Net effort |
|---|---|---|---|
| 1 | Model extensions | 6 changed, 3 new | M |
| 2 | Extractor silent-gap fixes | 5 changed | M |
| 3 | New provider shells | 7 new | M-L |
| 4 | Existing provider extensions | 5 changed | M |
| 5 | `reference_prices` plumbing | 2 changed (registry pre-tick + technical fetch) | S |
| 6 | Config + registry wiring | 2 changed (`config/data.json` + `config/README.md`) | XS |
| 7 | SVB-window smoke test | 1 new test file + manifest assertion | S |

Single PR at the end, seven commits along the way (one per phase). Tests
run between phases; a phase only lands if `pytest -m "not slow and not
integration" -q` is green.

---

## 4. Phase 1 — Model extensions

Six existing model files grow new fields. Three new model files are
created. Every change is **additive** — existing fields stay; new fields
default to `None` so back-compat with prior cache data is preserved.

### 4.1 `src/data/models/company_ratios.py` (changed)

Add the eight fundamental-ratio fields the extractor already tries to
read (Section 3.3 of brief). All `float | None`:

- `peg` (PEG ratio)
- `revenue_growth_yoy`
- `profit_margin`
- `debt_to_equity`
- `roe` (return on equity)
- `free_cash_flow`
- `analyst_rating_avg` (`recommendationMean`)
- `number_of_analyst_opinions`

Plus the two 52-week extremes (audit 2.1) the technical extractor wants:

- `fifty_two_week_high`
- `fifty_two_week_low`

### 4.2 `src/data/models/filings.py` (changed)

Extend `Filing` for 8-K body capture (Section 3.3 brief, audit 2.4):

- `body_excerpt: str | None` — first ~1,500 chars of the 8-K main body,
  same handling as `mda_excerpt`.
- `items_8k: list[str]` — Item enumeration on 8-K filings
  (e.g. `["2.02", "9.01"]`); empty list on non-8-K forms.

### 4.3 `src/data/models/trades.py` (changed)

**`InsiderTrade`** gains reporter-flag booleans sourced from Form 4 XML
(audit 2.5). Replace the brittle `_role_rank()` regex over title strings
with these authoritative flags:

- `is_officer: bool = False`
- `is_director: bool = False`
- `is_ten_percent_owner: bool = False`

**`InsiderDerivativeTrade`** gains the Table II extras the audit
identified (audit 2.6):

- `expiration_date: date | None`
- `is_indirect_ownership: bool = False`
- `is_late_filed: bool = False`

**`PoliticianTrade`** gains the asset-type discriminator (audit 2.8):

- `asset_type: str | None` — `"stock"`, `"bond"`, `"option"`, or
  free-text fallback. Lets the smart-money extractor filter bond noise.
- `link: str | None` — URL to the congressional disclosure PDF.
- `comment: str | None` — FMP `comment` field (e.g. `"spouse-owned"`).

**`NotableHolder`** gains the body-parsed fields (audit 2.9):

- `percent_of_class: float | None`
- `shares_held: float | None`
- `purpose_excerpt: str | None` — Item 4 prose (13D only); ≤2,000 chars.

### 4.4 `src/data/models/sentiment.py` (changed)

Extend `SocialSentimentSnapshot.platform` Literal to include
`"stocktwits"` alongside the existing platforms.

### 4.5 `src/data/models/news.py` (changed)

`NewsArticle.sentiment` already exists as `float | None` — the Phase 3
provider swap populates it and the Phase 2 extractor fix reads it.

One additive field for the Alpha Vantage per-ticker relevance signal:

- `relevance: float | None` — `[0.0, 1.0]`; Alpha Vantage emits this
  per-ticker per-article so the extractor can down-weight tangentially
  mentioned tickers. Other news providers leave it `None`.

### 4.6 `src/data/models/earnings.py` (new)

```python
class EarningsReport(BaseModel):
    ticker: str
    report_date: date
    fiscal_period: str          # e.g. "Q1 2023"
    eps_actual: float | None
    eps_estimate: float | None
    revenue_actual: float | None
    revenue_estimate: float | None
    surprise_pct: float | None  # provider-derived where available
```

Plus a `EarningsHistory` list-wrapper to match the
`<Bundle>`/`<History>` pattern used elsewhere.

### 4.7 `src/data/models/analyst_consensus.py` (new)

```python
class AnalystRating(BaseModel):
    ticker: str
    as_of: date
    target_high: float | None
    target_low: float | None
    target_mean: float | None
    target_median: float | None
    recommendation_mean: float | None   # 1.0=Strong Buy ... 5.0=Sell
    number_of_analysts: int | None

class AnalystRevision(BaseModel):
    ticker: str
    firm: str
    action: Literal["upgrade", "downgrade", "initiate",
                    "reiterate", "target_raise", "target_cut", "unknown"]
    from_grade: str | None
    to_grade: str | None
    event_date: date
```

### 4.8 `src/data/models/short_interest.py` (new)

```python
class ShortInterestSnapshot(BaseModel):
    ticker: str
    settlement_date: date
    report_publish_date: date    # ~8 business-day publish lag — gate on this for PIT
    short_interest: float        # shares
    average_daily_volume: float | None
    days_to_cover: float | None
```

### 4.9 `src/data/models/bundle.py` (changed)

`StockSignalBundle` grows three optional fields so the new providers'
output has a home in the strategist payload:

- `earnings: list[EarningsReport]` (last 4 quarters)
- `analyst_consensus: AnalystRating | None` + `analyst_revisions: list[AnalystRevision]`
- `short_interest: ShortInterestSnapshot | None`

Default empties / `None` preserve back-compat with existing trace files.

---

## 5. Phase 2 — Extractor silent-gap fixes

Eleven sites total. Each fix lives in one of the five extractor files. No
new files. The unifying rule is **"read off the typed object, do not
re-flatten through dicts"** — applied where the fetch callback already
flattens, and applied as a defensive guard where the typed object already
arrives at the extractor.

### 5.1 `src/contract/extractors/technical.py` (changed)

- **Fix A (audit 1.1):** open `raw.get("ratios")` (which the fetch
  callback already stows but currently never consumes). Emit
  `golden_cross` / `death_cross` boolean features from `last_price` vs
  `fifty_day_average` vs `two_hundred_day_average`, and apply a
  `beta`-aware confidence damping factor.
- **Fix B (existing brief):** compute `dist_from_high_52w_pct` and
  `dist_from_low_52w_pct` from `bars[]` (52-week max/min of `bars[].close`),
  with the new `fifty_two_week_high`/`fifty_two_week_low` ratio fields as
  fast-path fallback.
- **Fix C (new, depends on Phase 5):** emit
  `relative_strength_vs_spy_5d/20d` and
  `relative_strength_vs_sector_5d/20d` from
  `state["reference_prices"]`. Sector → ETF dictionary lives in
  `src/contract/extractors/_sector_map.py` (new helper module — exactly
  one constant dict; no class, no I/O).

### 5.2 `src/contract/extractors/fundamental.py` (changed)

- **Fix D (existing brief):** wire the eight Phase-1 ratio fields
  (`peg`, `revenue_growth_yoy`, `profit_margin`, `debt_to_equity`, `roe`,
  `free_cash_flow`, `analyst_rating_avg`, `number_of_analyst_opinions`)
  into `_extract_stats_features`. They already have keys in `_KEYS`; the
  bug is the missing model fields, which Phase 1 fixes.
- **Fix E (audit 1.3):** consume `InsiderTrade.transaction_code`. Split
  the current `insider_net_dollars_30d` aggregate into:
  - `insider_open_market_buy_dollars_30d` (P only)
  - `insider_open_market_sell_dollars_30d` (S only)
  - `insider_tax_withholding_dollars_30d` (F only — kept as diagnostic)
  - `insider_gift_count_30d` (G only — diagnostic)
- **Fix F (Phase 1, depends on §4.3):** replace `_role_rank()` regex
  entirely with the `is_officer` / `is_director` /
  `is_ten_percent_owner` booleans. `_role_rank` is deleted. Safe because
  §14 specifies the SVB cache is rebuilt post-merge — no pre-Phase-1
  trade objects survive into replay.
- **Fix G (audit 1.2):** add three new derivative-table features off
  `Form4Bundle.derivatives`:
  - `insider_option_exercise_value_30d` =
    `sum(underlying_shares * (last_price - strike_price))` filtered to
    `transaction_code == "M"`.
  - `insider_derivative_planned_ratio_30d` mirroring
    `insider_planned_sale_ratio_30d` but over the derivative table.
  - `senior_officer_derivative_grant_shares_30d` (filter on the
    new `is_officer` flag + `transaction_code == "A"`).
- **Fix H (Phase 1, depends on §4.2):** consume `Filing.items_8k` —
  emit `n_item_502_30d` (executive departures),
  `n_item_202_30d` (earnings releases), `n_item_101_30d` (material
  agreements). Counter only; the LLM context block is untouched.

### 5.3 `src/contract/extractors/news.py` (changed)

- **Fix I (existing brief — polarity/sentiment mismatch):** read
  `item.get("sentiment")` (matching the model field name) and remove the
  `polarity` lookup entirely. Coupled with the Phase 3 Alpha Vantage
  adapter populating `sentiment` from `overall_sentiment_score`.
- **Fix J (audit 1.4):** time-weight articles. Parse `published_at`,
  bucket by age (`24h`, `72h`, `7d`), and emit:
  - `news_count_24h`, `news_count_72h`
  - `headline_polarity_recency_weighted` (exponential decay, 24-hour
    half-life by default; constant exposed at top of file)
  - `hours_since_latest_news`

### 5.4 `src/contract/extractors/social.py` (changed)

- **Fix K (audit 3.6 + 3.7, "stop flattening" rule):** the existing
  social fetch callback at `src/agents/analysts/social/fetch.py:65-72`
  flattens `SocialSentiment` into a per-platform dict. Change the
  callback to pass the typed object through, then change the extractor
  to read `snap.score` and the top-level `aggregate_score` directly
  (instead of recomputing from `positive_score - negative_score`). Wire
  `score_velocity_24h` off the previous tick's `aggregate_score` stored
  in `state["memory_buffer"]` — single-line plumb, not new data.

### 5.5 `src/contract/extractors/smart_money.py` (changed — existing brief item already covered)

- **Fix (existing brief):** read `(amount_min_usd + amount_max_usd)/2`
  midpoint instead of the non-existent `amount` key. Plumb the new
  `asset_type` filter from §4.3 (skip rows where
  `asset_type in {"bond"}`).
- **Fix (existing brief):** consume `notable_holders[ticker]`. Emit
  `n_active_13d_30d`, `n_passive_13g_30d`, `n_amendments_30d`,
  `notable_holder_present`. With Phase 4 body-parsing, also emit
  `max_percent_of_class_30d` and `total_shares_held_30d`.

---

## 6. Phase 3 — New provider shells

Seven new files, one provider per file, all under
`src/data/providers/<domain>/<vendor>.py`. Each implements the existing
`Provider` protocol (no new protocol surface). Each is registered in the
provider registry (Phase 6).

| # | File | Row | Notes |
|---|---|---|---|
| 1 | `src/data/providers/earnings/finnhub.py` | 6 | Calls `client.earnings_calendar(symbol=…, from=…, to=…)`; populates `EarningsHistory`. New `earnings/` subpackage with `__init__.py`. |
| 2 | `src/data/providers/news/alpha_vantage.py` | 12 | `NEWS_SENTIMENT` endpoint, multi-ticker batched (`tickers=t1,t2,…`), monthly `time_from`/`time_to` chunks. Writes `NewsArticle.sentiment` from `overall_sentiment_score` and `NewsArticle.relevance` from `ticker_sentiment[].relevance_score` (field added in §4.5). |
| 3 | `src/data/providers/short_interest/finra.py` | 11 | OAuth2 bearer flow (~12h token cache). Hits the exchange-listed dataset endpoint (path confirmed in Group A verification item #2). Returns `ShortInterestSnapshot[]`. New `short_interest/` subpackage. |
| 4 | `src/data/providers/politician_trades/stock_watcher.py` | 14 | Reads JSON from a local `git clone` of the Senate + House Stock Watcher repos. Applies `disclosure_date <= as_of` filter (PIT-correct). Repos cloned into `.cache/stock-watcher/{senate,house}/` (path lands in `config/backtest_settings.json`). |
| 5 | `src/data/providers/social_sentiment/stocktwits.py` | 13 | Live-only forward cache. Calls `/streams/symbol/{SYM}.json`, normalises to `SocialSentimentSnapshot` with `platform="stocktwits"`. Returns empty for `as_of` older than first-cache-write date — extractor gracefully degrades to `is_no_data=True` (existing path). |
| 6 | `src/data/providers/analyst_consensus/yfinance.py` | 10 | Wraps `Ticker.upgrades_downgrades` and `Ticker.analyst_price_targets`. Returns `(AnalystRating, list[AnalystRevision])`. New `analyst_consensus/` subpackage. |
| 7 | `src/data/providers/options/yfinance.py` | 4 | **Live-only shell** — implements the protocol so the registry has a non-empty entry, but returns empty/`is_no_data=True` for backtest replay. Documented in module docstring: "Snapshot-only; not PIT-correct. Row #4 is dropped from v1 backtest per decision 7.1." Avoids leaving the row's registry slot unmapped. |

Each new provider:

- Uses the existing `_LIMITERS` infrastructure (one new entry per
  vendor under appropriate keys: `finnhub`, `alpha_vantage`, `finra`,
  `stocktwits`).
- Logs to the existing per-domain logger.
- Has at least one unit test (mocked HTTP) under `tests/data/providers/`.
- Has a single integration test (marked `@pytest.mark.slow`) that
  hits the real upstream for one ticker / one window — skipped by
  default; useful for cache-fill diagnostics.

---

## 7. Phase 4 — Existing provider extensions

Five existing adapters change, all additively. No new files.

### 7.1 `src/data/providers/filings/edgar.py`

Extend the 8-K branch to populate `Filing.body_excerpt` and
`Filing.items_8k`. The `mda_excerpt` extraction at
`filings/edgar.py:166-167` already establishes the body-fetch pattern;
this re-uses it. Item enumeration comes off the edgartools `filing.items`
property.

### 7.2 `src/data/providers/insider_trades/edgar.py`

- Surface `isOfficer` / `isDirector` / `isTenPercentOwner` reporter flags
  from Form 4 XML at `_build_trade()` (audit 2.5).
- Surface `expiration_date`, `DirectOrIndirect`, and `TimelinesFiled`
  flags at `_build_derivative()` (audit 2.6) — mapping to
  `expiration_date`, `is_indirect_ownership`, `is_late_filed`.

### 7.3 `src/data/providers/notable_holders/edgar.py`

Open the filing body (same pattern as `filings/edgar.py:166-167`).
Parse out `percent_of_class`, `shares_held` from the cover-page table.
On 13D forms, capture Item 4 prose as `purpose_excerpt`. On 13G filings
the field stays `None`. Effort flagged "medium" in the audit because of
the per-filing body fetch — guard with the existing EDGAR limiter so we
don't bust the 10 req/s cap on a 50-ticker fill.

### 7.4 `src/data/providers/company_ratios/pit_composite.py`

Extend to populate the five XBRL-derivable new ratios from
`edgartools.Company().get_financial_summary()` (or the equivalent
edgartools API surface):

- `peg`, `revenue_growth_yoy`, `profit_margin`, `debt_to_equity`, `roe`,
  `free_cash_flow`.

The two snapshot-leaky ones (`forward_pe`, `analyst_rating_avg`) come
from yfinance, set with a `provenance: "snapshot"` annotation in the
provider error trail so the backtest manifest can flag them.

### 7.5 `src/data/providers/politician_trades/quiver.py`

**Bug fix only.** Change the date filter from `transaction_date <= as_of`
to `disclosure_date <= as_of` to match the FMP and Stock Watcher
adapters (STOCK Act PIT semantics — politicians' trades are unknown to
the market until disclosed). One-line correctness fix; Quiver itself
stays inactive (paid-only).

### 7.6 `src/data/providers/stats/yfinance.py`

Add `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`, `recommendationMean`, and
`numberOfAnalystOpinions` to the projected `info` dict — populating the
new `CompanyRatios` fields in §4.1. Audit 2.1 subset; six existing fields
stay as-is.

---

## 8. Phase 5 — `state["reference_prices"]` plumbing

Single new responsibility: fetch SPY + 11 sector ETFs **once per tick**,
not once per ticker. Wires decision 9.6 of the brief.

### 8.1 Where it runs

A new pre-tick populator in `src/orchestrator/tick.py` (or wherever
`_build_initial_state` lives — confirm before implementation; that file
seeds the state ADK consumes). Runs after the watchlist is resolved and
before the analyst pool spawns.

### 8.2 What it stores

```python
state["reference_prices"] = {
    "SPY":  PriceHistory(...),
    "XLK":  PriceHistory(...),   # Technology
    "XLF":  PriceHistory(...),   # Financials
    "XLE":  PriceHistory(...),   # Energy
    "XLV":  PriceHistory(...),   # Health Care
    "XLY":  PriceHistory(...),   # Consumer Discretionary
    "XLP":  PriceHistory(...),   # Consumer Staples
    "XLI":  PriceHistory(...),   # Industrials
    "XLB":  PriceHistory(...),   # Materials
    "XLRE": PriceHistory(...),   # Real Estate
    "XLU":  PriceHistory(...),   # Utilities
    "XLC":  PriceHistory(...),   # Communication Services
}
```

Fetched via a single bulk `yf.download([...12 tickers], period=…,
threads=True)` call — one upstream round-trip, twelve typed
`PriceHistory` objects unpacked from the result.

### 8.3 Sector → ETF mapping

Lives at `src/contract/extractors/_sector_map.py` (new file — one
`SECTOR_TO_ETF: dict[str, str]` constant keyed on the strings yfinance
returns in `CompanyRatios.sector`). Used by the technical extractor when
it wants the per-ticker sector reference series.

### 8.4 What changes downstream

The technical extractor reads `state["reference_prices"]` to compute
`relative_strength_vs_spy_5d/20d` and
`relative_strength_vs_sector_5d/20d` (Fix C in §5.1). No other extractor
consumes the reference prices in v1; future analysts (e.g. a
sector-rotation analyst) can read the same key.

---

## 9. Phase 6 — Config + registry wiring

Two files. No code changes outside these.

### 9.1 `config/data.json`

```json
{
  "providers": {
    "price_history":     "yfinance",
    "company_ratios":    "pit_composite",
    "news":              "alpha_vantage",
    "social_sentiment":  "stocktwits",
    "insider_trades":    "edgar",
    "politician_trades": "fmp",
    "notable_holders":   "edgar",
    "filings":           "edgar",
    "earnings":          "finnhub",
    "analyst_consensus": "yfinance",
    "short_interest":    "finra",
    "options":           "yfinance"
  },
  "defaults": {
    "news_lookback_days": 7,
    "insider_lookback_days": 30,
    "politician_lookback_days": 90,
    "notable_holder_lookback_days": 180,
    "notable_holder_limit": 20,
    "history_period": "1y",
    "history_interval": "1d",
    "filings_per_form": 3,
    "include_filing_excerpts": true,
    "earnings_lookback_quarters": 4,
    "short_interest_lookback_days": 90
  },
  "http_timeout_seconds": 15.0
}
```

Two notable swaps from current:
- `news`: `"tiingo"` → `"alpha_vantage"`.
- `social_sentiment`: `"finnhub"` → `"stocktwits"` (finnhub stays
  registered as a fallback shell per the *provider switching* memory).

### 9.2 `config/README.md`

Append a row to the providers table for each of: `earnings`,
`analyst_consensus`, `short_interest`, `options`. Note "live-only" tag on
`options` and "forward-cache" tag on `social_sentiment.stocktwits`.

### 9.3 Provider registry

The registry module (one of `src/data/providers/__init__.py` or a
sibling factory — confirm before implementation) gains entries for each
new vendor key. Existing dispatch contract preserved: `get_provider(domain,
vendor_name) -> Provider`. Fallback shells (Tiingo for news, Finnhub for
social) stay registered so the provider-switching invariant is honoured
(swap via `config/data.json` only, never code).

---

## 10. Phase 7 — SVB-window smoke test

A single new integration test plus one assertion added to the smoke-test
manifest. Confirms the spec's goal end-to-end.

### 10.1 `tests/integration/backtest/test_no_silent_zero_features.py` (new)

```python
@pytest.mark.slow
@pytest.mark.integration
def test_no_silent_zero_features_on_svb_window(tmp_path):
    """Replay the SVB window; assert no extractor returns
    is_no_data=True for any analyst except Social, and no feature key
    is silently 0.0 for >50% of ticks."""
```

Reads the same SVB-window cache the existing end-to-end smoke test
builds; replays one tick; asserts the four-extractor verdict matrix.

### 10.2 Existing smoke test (`tests/integration/backtest/test_end_to_end_smoke.py`)

Add assertion: every analyst except Social emits a non-`is_no_data`
verdict for the SVB tick. (Social explicitly expected to soft-fail per
decision 9.3.)

---

## 11. Verification checklist (preflight)

The five Group A items from the brief (`§8`) **must clear before** Phase
3 work begins on the affected provider. They're cheap empirical tests,
not infra:

1. **Alpha Vantage `NEWS_SENTIMENT` archive depth** for 2023-03. One
   call, single ticker, time-bracketed.
2. **FINRA exchange-listed short-interest dataset endpoint path.**
   Verify against the Data Browser. Confirm production access (not the
   "Mock" sandbox).
3. **StockTwits free-tier rate limit.** Empirical 30-min sampling.
4. **Senate & House Stock Watcher repo handles + maintainer freshness.**
   Confirm 2023 SVB-window coverage.
5. **FMP `/senate-disclosure` chamber semantics.** Spot-check one House
   row and one Senate row's `office` field.

The five Group B items are verified during implementation (no preflight
gate); listed in `§8` of the brief.

---

## 12. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Alpha Vantage 25/day cap blocks 50-ticker SVB cache fill | Med | Brief calc shows ~10 calls for the SVB window; spread across two days if needed. Documented in §5 of brief. |
| FINRA dataset path differs in production vs `Mock` sandbox | Med | Group A item #2; if it diverges, ship Phase 3 with FINRA stubbed `is_no_data=True` and unblock spec; FINRA lands in a follow-up. |
| StockTwits rate-limited harder than ~200/hour expectation | Low | Forward-cache pattern is tolerant — we throttle ourselves. Worst case: extend cache window to 24h staleness. |
| Stock Watcher repos behind on Senate disclosures | Low | Both FMP (live) and Stock Watcher (historical) wired in parallel; FMP shadows the freshness gap. |
| edgartools 8-K body fetch busts SEC rate limit on 50-ticker fill | Low | Existing `_LIMITERS["edgar"]` cap (600/min vs 10 req/s SEC limit) covers it; per-ticker body fetch is N + 3M where M = avg 8-Ks/ticker. |
| Phase 4 `pit_composite` XBRL extension regresses existing extractor | Low-Med | All new fields default `None`; existing 5 fields untouched. Smoke test covers regression. |
| Reference-price plumbing slows tick startup by >1s | Low | Single bulk `yf.download` call vs 50 per-ticker calls is net faster. Smoke test includes a soft-budget assertion on tick wall-clock time. |

---

## 13. Out of scope (explicitly deferred)

Captured here so reviewers can verify *nothing in this spec creeps
into them*:

- Earnings call transcripts (Row #9) — decision 9.4.
- Intraday 5-min OHLCV (Row #3) — decision 9.2.
- True historical options (Row #4) — decision 7.1; live shell only.
- Historical social aggregates pre-2026 (Row #13) — decision 9.3.
- The full PIT-correctness leak audit — owned by
  `project_backtest_pit_correctness_deferred`; sequenced after this
  spec ships.
- Backlog audit items 4.2 / 4.3 (CompanyRatios identity metadata,
  dividend_yield consumer) — flagged as genuine new feature work, not
  silent-gap fixes; revisit after first backtest.
- Provider deduplication / cross-vendor article cluster IDs — no free
  source.
- `forward_pe` and `analyst_rating_avg` snapshot-leak elimination —
  marked leaky in backtest manifest; replacement requires paid
  provider.
- All `frontend-design` / UI work — this spec is data-layer only.

---

## 14. PR / commit strategy

Single feature branch off `main`. Seven commits, one per phase, in the
order presented. Each commit is independently runnable (pytest green)
and individually reviewable. Branch name suggestion:
`providers-and-silent-gaps-v1`.

PR opens after Phase 7 is green locally. PR body links back to this
spec and the brief. CI must pass `pytest -m "not slow and not integration"`
on every phase commit; the `slow` smoke-test runs locally pre-PR.

After merge, the SVB-window cache is rebuilt (`scripts.backtest_fetch
--window svb-stress-2023-03`) and the first backtest run executes
against the new stack. The brief's verification checklist (`§8`
Group B) items get exercised end-to-end at that point; Group A items
will already have been signed off per `§11` of this spec.

---

## 15. After this spec

`writing-plans` skill consumes this spec and emits a per-task
implementation plan. Each phase gets its own grouping of tasks; the
plan is the working document for execution.
