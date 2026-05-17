# Free-wins audit — fields available but unused

## Summary

Audited all five analyst fetch/extractor pairs, every Pydantic model under
`src/data/models/`, and every provider adapter under `src/data/providers/`.
Tally: **4 Category 1** findings (fetch writes the key, extractor never reads
it), **9 Category 2** findings (provider's upstream returns it, our adapter
drops it), and **7 Category 3** findings (model field declared but never read
downstream, even when a provider does populate it). Headline find: the
Technical analyst's fetch already pulls `CompanyRatios` (via
`get_company_ratios`) alongside `PriceHistory` and stows it as the `ratios`
sub-key, yet the technical extractor never opens that sub-key — so two trivial
free wins (`dist_from_high_52w_pct` and `dist_from_low_52w_pct` via
`fifty_day_average` / `two_hundred_day_average` cross-overs, plus a
volatility-aware confidence modifier off `beta`) sit one `raw.get("ratios")`
call away.

The five "out of scope" items called out by the audit brief are excluded
throughout (52-week bug, polarity/sentiment mismatch, politician amount
midpoint, `notable_holders` skipped by smart_money, the eight ratio gaps,
`Filing.body_excerpt`, `SocialSentimentSnapshot.platform` Literal,
`quiver.py` PIT bug, `Form4Bundle.derivatives` borderline known).

---

## Category 1: Silent extractor gaps

*(fetch writes it, extractor doesn't read it)*

### Finding 1.1: Technical fetch stows `ratios` but the technical extractor never opens the sub-key

- **Fetch**: `src/agents/analysts/technical/fetch.py:74-85` calls
  `get_company_ratios(ticker, as_of=...)` and writes the dump as
  `technical_data[ticker]["ratios"] = cr_payload`.
- **Extractor**: `src/contract/extractors/technical.py:117-122` only reads
  `raw.get("price_history")` (and the legacy `"history"` fallback). It never
  inspects `raw.get("ratios")` — the entire scalar fundamentals dict is read in,
  serialised through state, and silently dropped.
- **Data shape**: `CompanyRatios.model_dump()` — at minimum carries
  `fifty_day_average`, `two_hundred_day_average`, `beta`, `last_price`,
  `market_cap`. The fetch's own docstring (line 8) even admits the field is
  "reserved for future cross-feature work".
- **Feature it could enable**:
  - Authentic `dist_from_high_52w_pct` / `dist_from_low_52w_pct` if a provider
    fills those keys (today they default to None and silently bias the
    52-week-proximity heuristic).
  - 50d/200d moving-average cross flag (golden-cross / death-cross) — trivial
    boolean off `last_price` vs `fifty_day_average` vs `two_hundred_day_average`.
  - `beta`-aware confidence damping for highly volatile names (currently
    `atr_pct_14` is the only volatility proxy).
- **Effort to plumb**: small — single `raw.get("ratios") or {}` plus three
  arithmetic lines and one new feature key.

### Finding 1.2: Fundamental fetch writes the raw `Form4Bundle.derivatives` table to state but only the count is extracted

- **Fetch**: `src/agents/analysts/fundamental/fetch.py:168-171,289-294` stores
  the full `Form4Bundle` (including the `derivatives` list) onto
  `fundamental_data[ticker]["insider"]`. It also surfaces formatted derivative
  counts in the LLM-readable `fundamental_context` block.
- **Extractor**: `src/contract/extractors/fundamental.py:315-322` reads
  `bundle.derivatives` only to count `transaction_code == "M"` (exercise) and
  `transaction_code == "A"` (grant). Every other derivative field
  (`derivative_type`, `underlying_shares`, `strike_price`, `is_10b5_1`,
  `footnote`, `insider_title`) is discarded.
- **Data shape**: `list[InsiderDerivativeTrade]` — see
  `src/data/models/trades.py:53-75`.
- **Feature it could enable**:
  - `insider_option_exercise_value` = `sum(underlying_shares * (last_price -
    strike_price))` — measures whether exercises are in-the-money (bullish
    conviction) vs underwater (forced).
  - `insider_derivative_planned_ratio` mirroring the common-stock
    `insider_planned_sale_ratio` — 10b5-1 exercises are mechanical, ad-hoc ones
    are signal.
  - Senior-officer derivative-grant volume — sign of board confidence in
    forward stock price.
- **Effort to plumb**: small — `bundle.derivatives` is already in scope inside
  `_extract_insider_features`; only needs new keys in `_KEYS` + arithmetic
  lines.

### Finding 1.3: Fundamental writes the `insider` bundle with `transaction_code` per trade but the extractor only branches on `code == "M" / "A"` for derivatives

- **Fetch**: `src/agents/analysts/fundamental/fetch.py:288-294` — full
  `Form4Bundle` lands in state with `transaction_code` populated on each
  `InsiderTrade` via the EDGAR provider
  (`src/data/providers/insider_trades/edgar.py:364-366,380`).
- **Extractor**: `src/contract/extractors/fundamental.py:243-334` reads
  `t.side`, `t.shares`, `t.price_per_share`, `t.insider_name`, `t.insider_title`,
  `t.filed_at`, `t.is_10b5_1` — but never `t.transaction_code`. The Form 4
  transaction-code (P = open-market purchase, S = open-market sale, F = tax
  withholding, G = gift, X = option exercise...) carries categorical signal
  that the extractor currently inverts crudely via `side` only.
- **Data shape**: `InsiderTrade.transaction_code: str | None`
  (`src/data/models/trades.py:48`).
- **Feature it could enable**:
  - `insider_open_market_buy_dollars` (filter to `code == "P"`) vs net dollars
    — separates "real" insider conviction from tax-withholding noise (`code ==
    "F"`) that today pollutes `insider_net_dollars_30d`.
  - `insider_gift_count` (`code == "G"`) — gifts are notable for estate-planning
    timing.
- **Effort to plumb**: small — one new categorical aggregation in
  `_extract_insider_features`, plus added keys in `_KEYS`.

### Finding 1.4: News fetch stores `published_at` per article but the extractor never time-weights articles or computes news recency

- **Fetch**: `src/agents/analysts/news/fetch.py:144-149` serialises every
  `NewsArticle` (which includes `published_at: datetime`) into
  `news_data[ticker]["news"]`.
- **Extractor**: `src/contract/extractors/news.py:60-94` reads `polarity` only.
  It counts articles and averages polarity flat — every article weighted
  equally regardless of whether it landed 1 hour ago or 6 days ago. The
  `published_at` field is never inspected (the only place it's touched is
  inside the LLM context block formatter in `fetch.py`, not the deterministic
  extractor).
- **Data shape**: ISO-formatted datetime string in the dumped article dict.
- **Feature it could enable**:
  - `news_count_24h` / `news_count_72h` — newer articles dominate near-term
    price reaction.
  - `headline_polarity_recency_weighted` — exponentially decaying weight by age.
  - `hours_since_latest_news` — flagging stale-coverage tickers.
- **Effort to plumb**: small — parse each item's `published_at`, bucket by
  age, weight; entirely local to the extractor.

---

## Category 2: Provider-side dropped data

*(provider API returns it, our adapter drops it)*

### Finding 2.1: yfinance `Ticker.info` returns ~150 fields; the ratios adapter retains 10

- **Provider**: `src/data/providers/stats/yfinance.py:151-166`
- **API field returned but not stored**: yfinance `info` dict contains, among
  many others, `pegRatio`, `priceToBook`, `enterpriseValue`,
  `enterpriseToEbitda`, `returnOnEquity`, `returnOnAssets`, `profitMargins`,
  `operatingMargins`, `debtToEquity`, `currentRatio`, `quickRatio`,
  `revenueGrowth`, `earningsGrowth`, `freeCashflow`, `operatingCashflow`,
  `sharesShort`, `shortRatio`, `shortPercentOfFloat`, `heldPercentInsiders`,
  `heldPercentInstitutions`, `recommendationKey`, `recommendationMean`,
  `numberOfAnalystOpinions`, `targetMeanPrice`, `targetMedianPrice`,
  `targetHighPrice`, `targetLowPrice`, `fiftyTwoWeekHigh`, `fiftyTwoWeekLow`,
  `regularMarketDayHigh`, `regularMarketDayLow`, `averageVolume`,
  `averageVolume10days`, `floatShares`, `sharesOutstanding`, `bookValue`,
  `priceToSalesTrailing12Months`, `earningsQuarterlyGrowth`,
  `revenueQuarterlyGrowth`, `payoutRatio`, `industry`, `country`, `employees`,
  `auditRisk`, `boardRisk`, `compensationRisk`, `shareHolderRightsRisk`,
  `overallRisk`.
- **Model field it would fit**:
  - `fiftyTwoWeekHigh` / `fiftyTwoWeekLow` → would fill the long-noted
    `dist_from_high_52w_pct` / `dist_from_low_52w_pct` gap in the technical
    extractor — currently fabricated to None.
  - `pegRatio`, `priceToBook`, `enterpriseToEbitda`, `revenueGrowth`,
    `earningsGrowth`, `freeCashflow`, `returnOnEquity`, `profitMargins`,
    `debtToEquity` → already-known Cat 3 (out-of-scope brief mentioned 8
    missing ratios) — flagging here for completeness on the *provider* side.
  - `recommendationMean` / `numberOfAnalystOpinions` → cleanly populates
    `analyst_rating_avg` field the extractor already reads but never sees data
    for.
  - `heldPercentInsiders`, `heldPercentInstitutions`, `sharesShort`,
    `shortPercentOfFloat` → new fields needed; would feed a short-interest /
    institutional-ownership feature dimension that doesn't exist yet.
  - `industry`, `country`, `employees` → identity metadata; sit alongside
    existing `sector`.
- **Effort**: small per field (each is a single `_f(info, "<key>")` line).

### Finding 2.2: Finnhub `company_news` returns `category` and `image`; adapter keeps neither

- **Provider**: `src/data/providers/news/finnhub.py:66-76`
- **API field returned but not stored**: Finnhub's `company_news` rows include
  `category` (e.g. "company news", "earnings"), `image` (thumbnail URL), `id`
  (Finnhub's article ID — useful for dedup across providers), and `related`
  (comma-separated list of related tickers — implies cross-ticker
  co-occurrence).
- **Model field it would fit**: New field `category: str | None` on
  `NewsArticle` (would feed a "n_earnings_articles_7d" feature). `related`
  enables cross-ticker correlation detection but needs a new model entry.
- **Effort**: small for `category`; medium for `related` (multi-ticker
  semantics).

### Finding 2.3: Tiingo News returns `tags`, `crawlDate`, and an article-level `sentiment` but the adapter drops all three

- **Provider**: `src/data/providers/news/tiingo.py:164-176`
- **API field returned but not stored**: Tiingo's response rows carry `tags`
  (list of strings like `["earnings", "guidance"]`), `crawlDate` (when Tiingo
  indexed the row — distinct from `publishedDate`), and (on paid tiers)
  `sentiment` per article. Adapter projects only `title`, `description`,
  `url`, `source`, `publishedDate`.
- **Model field it would fit**:
  - `tags` → new field, or feed a categorical feature directly.
  - `crawlDate` → could resolve "publication lag" vs "syndication lag" — only
    useful as a diagnostic.
  - Provider-supplied sentiment → would populate
    `NewsArticle.sentiment: float | None` (already declared, set to `None`
    today on line 174). Once populated, the news extractor's
    `headline_polarity_mean_7d` could read from `sentiment` as a fallback when
    `polarity` is missing.
- **Effort**: small per field.

### Finding 2.4: EDGAR filings adapter discards filing size, primary document type, and items list

- **Provider**: `src/data/providers/filings/edgar.py:60-111`
- **API field returned but not stored**: edgartools filing objects expose
  `size_bytes` / `size`, `primary_document` (the actual file name —
  `aapl-20231231.htm` vs an exhibit), `items` (8-K item list — "Item 5.02
  Departure of Directors"), `is_xbrl`, `description`. The adapter projects
  `form`, `filing_date`, `accession_no`, `filing_url`, `primary_doc_description`
  (as title), plus the MD&A / risk-factor section text.
- **Model field it would fit**:
  - `items` (8-K) → new field `items_8k: list[str]` on `Filing` — would let the
    extractor count specific items (5.02 = executive departure, 2.02 = earnings
    release, 1.01 = material agreement). Today every 8-K is just "an 8-K".
  - `is_xbrl` → diagnostic only.
- **Effort**: small for `items`; medium if we wanted to surface them in the
  LLM context.

### Finding 2.5: EDGAR insider-trades adapter does not surface `is_director` / `is_officer` / `is_ten_percent_owner` flags

- **Provider**: `src/data/providers/insider_trades/edgar.py:323-384`
- **API field returned but not stored**: Form 4 XML carries per-reporting-person
  flags `isDirector`, `isOfficer`, `isTenPercentOwner`, `officerTitle`. The
  adapter only reads `insider_title` free text and ignores the boolean flags.
- **Model field it would fit**: New booleans on `InsiderTrade`
  (`is_officer: bool`, `is_director: bool`, `is_ten_percent_owner: bool`).
  Would replace the brittle regex over title strings in `_role_rank()` with an
  authoritative source.
- **Effort**: small — three new booleans + one row extraction line.

### Finding 2.6: EDGAR insider-trades adapter discards Table II `expiration_date`, `transaction_timeliness`, and per-row direct/indirect ownership

- **Provider**: `src/data/providers/insider_trades/edgar.py:387-454`
- **API field returned but not stored**:
  `_build_derivative` reads `underlying_shares`, `strike_price`,
  `transaction_date`, `derivative_type`, `transaction_code`, `EquitySwap`,
  footnote — but ignores the `ExpirationDate` column (option lifetime),
  `DirectOrIndirect` (D = held by reporter, I = held via family/trust — much
  weaker signal), and `TimelinesFiled` (late filing penalty flag).
- **Model field it would fit**:
  - `expiration_date: date | None` → measures how long-dated the option is —
    short-dated exercises carry different signal.
  - `is_indirect_ownership: bool` → filters out family-trust noise from genuine
    insider activity.
  - `is_late_filed: bool` → late Form 4 filings (>2 business days after the
    transaction) are SEC-flagged and statistically correlated with poor
    governance.
- **Effort**: small per field (single `_row_get` add each).

### Finding 2.7: Quiver politician-trades adapter drops `last_modified`, `excess_return`, and (paid-tier) `held` / `notes` columns

- **Provider**: `src/data/providers/politician_trades/quiver.py:129-148`
- **API field returned but not stored**: Quiver's `/live/congresstrading`
  response rows can include `last_modified`, `excess_return` (their own
  attribution of the trade vs SPY), `Held` (current shares held), `Notes`
  (free-text). Adapter only reads `TransactionDate`, `Traded`, `Range`,
  `Amount`, `Trade_Size_USD`, `Representative`, `Chamber`, `Party`, `Ticker`,
  `Transaction`, `Type`, `ReportDate`, `Disclosed`.
- **Model field it would fit**:
  - `Held` → new `current_position_shares: float | None` on `PoliticianTrade` —
    "still holding" vs "round-tripped" matters for conviction reading.
  - `excess_return` → new diagnostic field; useful to dedup repeat trades by
    the same politician with the same return signature.
- **Effort**: small per field.

### Finding 2.8: FMP politician-trades adapter drops `link`, `assetType`, and `comment`

- **Provider**: `src/data/providers/politician_trades/fmp.py:166-206`
- **API field returned but not stored**: FMP's
  `senate-trading`/`senate-disclosure` JSON rows return `link` (URL to the
  congressional disclosure PDF), `assetType` (Stock vs Bond vs Option — today
  all are silently treated as Stock), and a `comment` field on certain rows
  (often "spouse-owned" or "option exercise" footnote text).
- **Model field it would fit**:
  - `assetType` → new field on `PoliticianTrade` — would let the smart_money
    extractor filter out bond trades that pollute the stock-signal aggregate.
  - `comment` → free-text supplement, useful for an LLM context block (parallel
    to insider-trade footnotes).
  - `link` → URL field for audit traceability.
- **Effort**: small per field.

### Finding 2.9: EDGAR notable_holders adapter computes none of the percentage / shares ownership data from inside the filing body

- **Provider**: `src/data/providers/notable_holders/edgar.py:55-99`
- **API field returned but not stored**: SC 13D/13G filings contain in the
  filing body (parseable via edgartools' `filing.obj()` as the adapter already
  does for 10-K MD&A) the `cusip`, `percent_of_class`, `shares_held`, plus
  Item 4 ("Purpose of Transaction" — narrative text in 13D filings only). The
  adapter only reads filing-list metadata; it never opens the filing body, so
  the actual *stake size* — the only signal that distinguishes a 5.1% passive
  index reweighting from a 9% activist position — is dropped.
- **Model field it would fit**: New fields on `NotableHolder`:
  `percent_of_class: float | None`, `shares_held: float | None`,
  `purpose_excerpt: str | None` (Item 4 prose, ≈2k chars; parallels Filing's
  `mda_excerpt`).
- **Effort**: medium — requires opening each filing body (additional EDGAR
  per-filing fetch + parsing); same pattern as `filings/edgar.py:166-167`
  already implements for 10-K excerpts.

---

## Category 3: Model fields with no populating provider

*(model declares it, nothing writes it — or nothing reads it after it's written)*

### Finding 3.1: `CompanyRatios.long_name` and `CompanyRatios.sector`

- **Model.field**: `src/data/models/company_ratios.py:33-34`
- **Currently read by extractor?**: no — grep of extractors returns zero hits
  on `long_name` or `sector` (and the technical extractor doesn't even open the
  `ratios` sub-key as documented in Finding 1.1).
- **Free providers from v3 catalogue that could populate it**: already
  populated by both `stats/yfinance.py:153-154` and
  `company_ratios/pit_composite.py:142-143,268-269`. The data lands in
  state-stored payloads and goes unread — diagnostic / identity-only today.
  Would feed a "sector-relative momentum" feature when surfaced.

### Finding 3.2: `CompanyRatios.beta`

- **Model.field**: `src/data/models/company_ratios.py:39`
- **Currently read by extractor?**: no — `_extract_stats_features` in
  `src/contract/extractors/fundamental.py:164-184` never asks for `beta`. The
  pit_composite provider explicitly sets `beta=None`
  (`pit_composite.py:273`) — only the wall-clock yfinance provider populates
  it.
- **Free providers from v3 catalogue that could populate it**: yfinance
  `info.beta` already lands here; the pit_composite path could compute its
  own 1-year SPY correlation (TODO comment on `pit_composite.py:273` flags
  exactly this). Free win once an extractor reads the field.

### Finding 3.3: `CompanyRatios.dividend_yield`

- **Model.field**: `src/data/models/company_ratios.py:40`
- **Currently read by extractor?**: no. Populated by both providers
  (`stats/yfinance.py:159` and `pit_composite.py:257-261`) but the fundamental
  extractor never references `dividend_yield`.
- **Free providers from v3 catalogue that could populate it**: already
  populated. Direct feature wire-up.

### Finding 3.4: `CompanyRatios.fifty_day_average` and `CompanyRatios.two_hundred_day_average`

- **Model.field**: `src/data/models/company_ratios.py:41-42`
- **Currently read by extractor?**: no. Both providers populate them
  (`stats/yfinance.py:160-163` and `pit_composite.py:263-264`). The technical
  extractor would be the natural consumer but doesn't open the `ratios`
  sub-key (Finding 1.1).
- **Free providers from v3 catalogue that could populate it**: already
  populated. Trivial golden-cross / death-cross feature once Finding 1.1 is
  fixed.

### Finding 3.5: `NewsArticle.sentiment`

- **Model.field**: `src/data/models/news.py:16-19`
- **Currently read by extractor?**: no — the news extractor reads `polarity`
  not `sentiment` (this is the polarity/sentiment mismatch flagged as
  out-of-scope, but the field-side gap is worth noting separately). The model
  field is declared and both providers explicitly set it to `None`
  (`finnhub.py:74` and `tiingo.py:174`).
- **Free providers from v3 catalogue that could populate it**: Tiingo (paid
  tier) and Finnhub `news-sentiment` endpoint both expose per-article
  sentiment. Once a provider fills it and the extractor reads it, no other
  plumbing is required.

### Finding 3.6: `SocialSentimentSnapshot.score`

- **Model.field**: `src/data/models/sentiment.py:14-17` — declared as
  "Net sentiment in [-1.0, 1.0] (positive - negative, normalised)".
- **Currently read by extractor?**: partially — `social_fetch_callback` at
  `src/agents/analysts/social/fetch.py:65-72` builds a per-platform dict from
  `snap.mention_count`, `snap.positive_score`, `snap.negative_score` and
  *omits* `snap.score`. The extractor at `extractors/social.py:75-94` then
  re-derives the equivalent via `_net()`. The model's `score` field round-trips
  through the provider (`finnhub.py:49`) and is silently dropped by the
  callback before it reaches the extractor.
- **Free providers from v3 catalogue that could populate it**: already
  populated by `social_sentiment/finnhub.py:49`. Two-line free win:
  `fetch.py` adds the key; extractor reads it instead of recomputing.

### Finding 3.7: `SocialSentiment.aggregate_score` (top-level)

- **Model.field**: `src/data/models/sentiment.py:23-26` — "Mention-weighted
  net sentiment across all platforms".
- **Currently read by extractor?**: no — the fetch callback at
  `src/agents/analysts/social/fetch.py:65-73` converts the typed
  `SocialSentiment` into a flat `{platform: {...}}` dict and discards the
  `aggregate_score` field. The extractor recomputes it from scratch at
  `extractors/social.py:90` under the same name.
- **Free providers from v3 catalogue that could populate it**: already
  populated by `social_sentiment/finnhub.py:89-95`. Same shape of fix as
  Finding 3.6.

---

## Notes

A clean pattern emerges across findings: the **fetch callbacks repeatedly flatten typed Pydantic models into looser dicts** before they reach the extractors, and that flattening is where the silent data loss happens. The Social fetch is the clearest case (Findings 3.6 + 3.7): the typed `SocialSentiment` carries a pre-computed `score` per snapshot and an `aggregate_score` at the top, but the callback rebuilds a flat per-platform dict from three fields only, forcing the extractor to recompute what the provider already returned. The same shape exists for `CompanyRatios` in technical (Finding 1.1 — entire model dropped at the extractor boundary even though the fetch already pays the round-trip cost). Folding "pass the Pydantic model through to the extractor unchanged" into the v3 spec would eliminate Findings 1.1, 3.6, 3.7 at one stroke and make Findings 3.1-3.4 trivially exploitable.

A second pattern across providers: **adapters project the upstream payload onto today's model shape rather than the model's potential shape**. yfinance is the most extreme (Finding 2.1 — 90%+ of `info` discarded), but every Finnhub / Tiingo / EDGAR / Quiver / FMP adapter shows the same shrinkage. None of these are bugs; they were the right call when the model was smaller. The v3 spec should re-baseline.

A third observation worth flagging: `transaction_code` (Finding 1.3) and `is_indirect_ownership` (Finding 2.6) both suggest the insider-trade signal is currently noisier than it needs to be — Form 4 carries categorical metadata the extractor doesn't use, so noise that should be filterable bleeds into the headline `insider_net_dollars_30d` aggregate. Worth calling out separately in the spec because the fix is "subtract noise" rather than "add new dimensions" — different cost / benefit calculus.

Finally: nothing in the audit changes the previously catalogued architectural items (StockSignalBundle composition, sparse-provider error handling, PIT-correctness). The free-wins land entirely inside existing files — no new domain providers, no new model classes, no contract changes. They are genuinely plumbing.
