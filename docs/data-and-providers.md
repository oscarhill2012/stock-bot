# Data & Providers

This file is the canonical reference for **what data StockBot consumes** and **where we can get it from**. It has two parts:

1. **What we currently require** — the exact fields the four analysts read each tick. Use this as the contract any new provider must satisfy.
2. **Provider catalogue** — for every data type, the live-fetch options, the historical-by-date options (e.g. "give me 7 Aug 2025"), and the free/paid tradeoffs.

> Research sourced via Context7 (May 2026). Free-tier numbers and rate limits change — confirm on the provider's pricing page before committing.

---

## Part 1 — Data StockBot Currently Requires

The single contract is `StockSignalBundle` in `src/data/models/bundle.py:22`. Every analyst reads a slice of this bundle (or fetches its own slice via the same providers — see `src/agents/analysts/*/fetch.py`). Any replacement provider must populate the same Pydantic models, defined in `src/data/models/`.

### A. Market data — `StockStats` (`market.py`)

Used by the **Technical analyst**. Source today: `yfinance`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `history` | `list[OHLCBar]` | Daily OHLCV bars over period (default `1y`, `1d`). Bar = `timestamp, open, high, low, close, volume`, split/dividend adjusted |
| `market_cap` | float? | Market capitalization (USD) |
| `trailing_pe` / `forward_pe` | float? | Trailing & forward P/E |
| `beta` | float? | 5-yr beta |
| `dividend_yield` | float? | Trailing dividend yield |
| `fifty_day_average` / `two_hundred_day_average` | float? | 50d / 200d MAs of close |
| `last_price` | float? | Most recent trade price |
| `sector` / `long_name` | str? | Industry sector + full company name |

### B. SEC filings — `Filing` (`filings.py`)

Used by the **Fundamental analyst**. Source today: SEC EDGAR via `edgartools`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `form_type` | str | 10-K, 10-Q, 8-K, etc. |
| `filed_at` | datetime | Filing timestamp |
| `accession_no` | str | EDGAR accession |
| `title` | str | Filing title |
| `url` | str | EDGAR URL |
| `risk_factors_excerpt` | str? | First ~2k chars of Item 1A |
| `mda_excerpt` | str? | First ~2k chars of Item 7 (MD&A) |

### C. News — `NewsArticle` (`news.py`)

Used by the **Sentiment analyst**. Source today: Finnhub `company-news`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `headline` | str | Article headline |
| `summary` | str | Article body/summary |
| `url` | str | Article URL |
| `source` | str | Publisher |
| `published_at` | datetime | Publication time |
| `sentiment` | float? | Per-article sentiment in `[-1, 1]` if provided |

### D. Social sentiment — `SocialSentiment` (`sentiment.py`)

Used by the **Sentiment analyst**. Source today: Finnhub `stock/social-sentiment`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `snapshots` | `list[SocialSentimentSnapshot]` | Per-platform: `platform`, `mention_count`, `positive_score`, `negative_score`, `score` |
| `aggregate_score` | float | Mention-weighted net sentiment |

### E. Insider trades — `InsiderTrade` (`trades.py`)

Used by the **Smart Money analyst**. Source today: SEC Form 4 via `edgartools`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `insider_name` | str | Person |
| `insider_title` | str? | Role (CEO, CFO, Director...) |
| `side` | `buy/sell/exchange/unknown` | Direction |
| `shares` | float | Share count |
| `price_per_share` | float? | Reported price |
| `transaction_date` | date | Trade date |
| `filed_at` | datetime? | Filing time |
| `form_type` | str | Default `"4"` |

### F. Politician trades — `PoliticianTrade` (`trades.py`)

Used by the **Smart Money analyst**. Source today: Quiver Quant (currently soft-failing — no key).

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `politician` | str | Name |
| `chamber` | str? | "House" or "Senate" |
| `party` | str? | Party |
| `side` | `buy/sell/exchange/unknown` | Direction |
| `transaction_date` | date | Trade date |
| `disclosure_date` | date? | Disclosure date (often weeks behind) |
| `amount_min_usd` / `amount_max_usd` | float? | STOCK Act discloses ranges |

### G. Notable holders — `NotableHolder` (`trades.py`)

Used by the **Smart Money analyst**. Source today: SEC SC 13D/G via `edgartools`.

| Field | Type | Description |
|---|---|---|
| `ticker` | str | Symbol |
| `holder` | str | Fund/investor name |
| `form_type` | str | `SC 13D`, `SC 13G`, `/A` amendments |
| `intent` | `active/passive/unknown` | 13D=activist, 13G=passive |
| `is_amendment` | bool | True for 13D/A, 13G/A |
| `filed_at` | datetime | Filing time |
| `accession_no` | str | EDGAR accession |
| `url` | str? | EDGAR link |

---

## Part 2 — Provider Catalogue

For each data type below: **live**, **historical-by-date** (so we can ask "give me 7 Aug 2025 and get data out"), free-tier reality, and notable features. Anything ✅ on historical-by-date supports a `start`/`end` (or equivalent) parameter.

---

### 2A. Market data (OHLCV + fundamentals)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **yfinance** *(current)* | EOD + 15-min delayed quotes | ✅ `start="2025-08-07", end="2025-08-08"`; periods up to `"max"` (~99 yrs) | Free, no key | No published cap (we self-throttle 60/min) | Intraday `1m`/`5m` only available for last 60 days. `auto_adjust=True` for split/dividend-adjusted closes. Unofficial — Yahoo can break it any time. |
| **Alpha Vantage** | Realtime (premium) + EOD | ✅ `TIME_SERIES_DAILY_ADJUSTED` covers 20+ years. Intraday `TIME_SERIES_INTRADAY` with `month=YYYY-MM` for any historical month | Free, key required | **25 calls/day** free; 75/min on $50/mo plan | Has fundamentals: `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`, `OVERVIEW`, `EARNINGS`. Free tier is too tight for a multi-ticker bot. |
| **Polygon.io** | Realtime stocks via REST + WebSocket | ✅ `GetAggs(ticker, multiplier, timespan, from, to)` (from = ISO date or unix-ms) | Free Basic: EOD only, **5 calls/min** | 5 req/min free → unlimited on $29/mo Starter | Highest data quality of the lot. Free plan locks intraday/realtime. Has news, ticker reference, splits, dividends. |
| **Tiingo** | EOD + IEX-only intraday (REST/WS) | ✅ EOD: `/tiingo/daily/<ticker>/prices?startDate=&endDate=`; intraday IEX: `/iex/<ticker>/prices?startDate=&resampleFreq=5min` | Free, key required | **50 unique tickers/day**, 500 req/hour, 1000 req/day | Adjusted + raw closes, dividends, splits in same payload. News endpoint included. Free tier viable for ~50-ticker watchlist. |
| **Financial Modeling Prep (FMP)** | Realtime quotes (paid) + EOD | ✅ `historical-price-full/{symbol}?from=&to=` | **Basic plan free: 250 calls/day**, EOD only | 250/day free → 300/min on $19/mo | 150+ endpoints incl. fundamentals, ratios, insider trades, **senate trades**, ETF holdings. One of the broadest free APIs. |
| **EODHD** | EOD + delayed/live (paid) | ✅ Date-range historical for ~30 yrs | Free 20 calls/day demo | $19.99/mo+ for production | Strong on non-US exchanges. Probably overkill until we go international. |
| **Twelve Data** | Realtime (paid) + EOD | ✅ `time_series?start_date=&end_date=` | Free 800 calls/day, 8/min | Generous free tier | Good fallback if yfinance breaks. |

**Recommendation if yfinance breaks:** Tiingo for EOD (50-ticker free tier matches our likely watchlist size) or FMP (250 calls/day, broader endpoint catalogue). Polygon if/when we go live and want clean realtime.

---

### 2B. SEC filings (10-K / 10-Q / 8-K + Risk Factors / MD&A excerpts)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **EDGAR via `edgartools`** *(current)* | Filings appear within minutes of submission | ✅ `Company("MSFT").get_filings(form=["10-K","10-Q"]).filter(filing_date="2023-01-01:2023-12-31")` and `get_filings(form="4", ticker="MSFT", start_date=..., end_date=...)` | Free, no key | **10 req/sec hard cap** | Identity required (`Name email@x` in User-Agent — set via `set_identity()` and our `EDGAR_IDENTITY` env). Parses XBRL, extracts Item 1A / Item 7 sections natively. 4400+ snippets in docs. |
| **SEC EDGAR direct (raw)** | Same | ✅ Raw `submissions/CIK*.json` + Archive URLs | Free, no key | 10 req/sec | What `edgartools` wraps. Useful only if we want to drop the dependency. |
| **sec-api.io** | Realtime websocket of new filings | ✅ Full-text search + filter by `filedAt` range | Free 100 calls/day | Paid: $39/mo+ | Pre-extracted Item 1A / Item 7 (`ExtractorApi`) — that's the feature we replicated for free with `edgartools`. Worth re-evaluating if extraction quality matters. |
| **Finnhub `/stock/filings`** | Realtime | ✅ `from`/`to` date params | Free tier 60/min | Same global Finnhub bucket | Metadata only — no Item 1A / MD&A excerpts. |
| **FMP `/sec_filings`** | Realtime | ✅ Date filter | 250 calls/day free | n/a | Metadata only, like Finnhub. |

**Recommendation:** Stay on `edgartools`. It's the only free option that gives us Risk Factors + MD&A excerpts in-process, and the 10 req/sec cap is generous.

---

### 2C. News (per-ticker articles with timestamps + sentiment)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **Finnhub `company-news`** *(current)* | Yes | ✅ `from=YYYY-MM-DD&to=YYYY-MM-DD` | Free, key required | **60 calls/min, 30 burst** | Per-article sentiment available on premium tier only. Good US coverage. |
| **Alpha Vantage `NEWS_SENTIMENT`** | Yes | ✅ `time_from=YYYYMMDDTHHMM&time_to=YYYYMMDDTHHMM`, `tickers=`, `topics=`, `sort=LATEST/EARLIEST/RELEVANCE`, `limit=1000` | Free 25 calls/day | 25/day | **Sentiment scores included free** (relevance + sentiment label per ticker per article). Excellent for backtesting since both date range and sentiment are first-class. |
| **Tiingo News** | Yes | ✅ `startDate=&endDate=` | Free, key required | Same Tiingo bucket (1000/day) | Per-ticker filter, multiple sources aggregated. |
| **Polygon `/v2/reference/news`** | Yes | ✅ `published_utc.gte=&published_utc.lte=` | Free 5/min | 5/min | Sentiment + insights per ticker on paid tiers. |
| **MarketAux** | Yes | ✅ `published_after=&published_before=` | Free 100 requests/day | 100/day | Cheap, multi-language. |
| **NewsAPI.org** | Yes | Last 30 days only on free | Free 100/day | Limited | Generic news, not finance-tuned. Probably skip. |

**Recommendation for "give me 7 Aug 2025" backtests:** Alpha Vantage `NEWS_SENTIMENT` is purpose-built for this — it returns historical sentiment-scored articles for any date range, no premium needed. Use Finnhub for live (already integrated).

---

### 2D. Social sentiment (Reddit + Twitter)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **Finnhub `/stock/social-sentiment`** *(current)* | Yes | ✅ `from=&to=` | **Premium endpoint** — was free, now paid | n/a | Returns hourly buckets w/ `mention`, `positiveScore`, `negativeScore`, net `score`. Our existing code calls this; on free key it now returns `{}` and the analyst soft-fails. |
| **Quiver Quant `wallstreetbets`** | Daily | ✅ Date-filtered | Trial only | n/a | Reddit-only, but specifically WSB which is the most market-moving sub. |
| **StockGeist** | Realtime | ✅ Hourly snapshots, date range | 30-day free trial | n/a | Reddit, Twitter, news — combined sentiment score. |
| **Reddit API direct (PRAW)** | Yes | Limited (Reddit only exposes ~1000 most recent per sub) | Free, OAuth required | 60/min OAuth | Roll our own subreddit scraper. Free but high-effort. Reddit removed historical archives in 2023; **Pushshift** is no longer public. |
| **Twitter/X API** | Realtime | Historical: paid only ($100/mo+) | Free tier discontinued for content | n/a | Effectively closed for this use case. |

**Recommendation:** Social sentiment is the weakest link in the free-tier landscape. Either (a) accept Finnhub paid, (b) build a PRAW-based WSB scraper for live + accept no historical, or (c) drop the social signal entirely until we go paid. Strategist already tolerates `social=None`.

---

### 2E. Insider trades (Form 4 — officers/directors)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **EDGAR via `edgartools`** *(current)* | Yes | ✅ `Company("TSLA").get_filings(form="4", filing_date="2025-08-01:2025-08-31")` then `f.obj()` for parsed `Ownership` with shares/price/side | Free, no key | 10 req/sec (shared EDGAR bucket) | Authoritative source — every other provider derives from this. `to_dataframe()` for batch analysis. |
| **Finnhub `stock_insider_transactions`** | Yes | ✅ `(symbol, from, to)` | Free 60/min | Shared Finnhub bucket | Pre-parsed; 100 transactions/call cap. Easier than `edgartools` if we don't need Form 4 nuance. |
| **FMP `/insider-trading`** | Yes | ✅ Date filter | 250 calls/day free | n/a | Includes `transactionType` and dollar value. |
| **OpenInsider** | Yes (HTML) | ✅ Date-range queries via URL | Free, no key, no API (scrape) | None published | Best free human-readable view. Scraping HTML is fragile but works. |
| **Quiver Quant `insiders()`** | Yes | ✅ via `quiver.insiders("TSLA")` | Trial only | n/a | Pre-cleaned; same data as EDGAR. |

**Recommendation:** Stay on `edgartools`. If we want simpler parsing or a backup, Finnhub's `stock_insider_transactions` is a 1-line drop-in.

---

### 2F. Politician trades (US Congress STOCK Act)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **Quiver Quant `congress_trading`** *(current — soft-failing)* | Daily updates | ✅ `quiver.congress_trading("TSLA")` returns full DataFrame; filter by date in pandas | Trial restricted; full access paid | ~30/min when active | The de-facto API for this data. Has `Representative`, `Senator`, `Chamber`, `Party`, `Range`, `TransactionDate`, `ReportDate`. ~24h delay. |
| **FMP `/senate-trades`** | Yes | ✅ Has `symbol` filter (no date param documented; filter client-side) | 250 calls/day free | n/a | Confirmed senate endpoint. House endpoint also exists separately. **Free alternative to Quiver.** |
| **Capitol Trades (unitedstates/congress GitHub)** | Daily scrape | ✅ Full historical archive in repo | Free, scrape | n/a | Open-source archive of disclosures. Unbeatable for backtesting since it's a static repo we can clone. |
| **Senate eFD direct** | Yes | ✅ Full archive | Free, but rate-limited HTML | n/a | Source of truth; PDF parsing required. High-effort. |

**Recommendation:** Switch politician trades to **FMP `/senate-trades`** — it's free (within 250 calls/day), live, and gives us the same fields. Restore Quiver only if we need the lobbying or Reddit feeds it also offers.

---

### 2G. Notable holders (5%+ beneficial ownership — SC 13D / 13G)

| Provider | Live | Historical by date | Free tier | Rate limit | Notes |
|---|---|---|---|---|---|
| **EDGAR via `edgartools`** *(current)* | Yes | ✅ `Company("X").get_filings(form=["SC 13D","SC 13G","SC 13D/A","SC 13G/A"], filing_date="2025-01-01:")` | Free, no key | 10 req/sec | Authoritative. We classify intent (active=13D / passive=13G) and amendment flag in our own provider. |
| **Finnhub `/stock/ownership`** | Yes | Snapshot only — no historical date range | Free tier | Shared bucket | Returns current top holders (institutional + insider %). Different shape from 13D/G — more like 13F-derived holdings. |
| **FMP `/institutional-holder` + `/13f`** | Yes | ✅ Quarterly 13F snapshots; date filter | 250 calls/day free | n/a | 13F is a different filing (every fund >$100M, quarterly). Useful complement, not a replacement. |

**Recommendation:** Stay on `edgartools` for 13D/G. If we ever want quarterly 13F holdings (broader institutional view), FMP is the easiest add-on.

---

## Quick "give me 7 Aug 2025" cheatsheet

To replay a single historical date across all data types:

| Data | Best free option | Call shape |
|---|---|---|
| OHLCV | yfinance | `yf.Ticker("AAPL").history(start="2025-08-07", end="2025-08-08")` |
| OHLCV (backup) | Tiingo | `GET /tiingo/daily/AAPL/prices?startDate=2025-08-07&endDate=2025-08-07` |
| Filings | edgartools | `Company("AAPL").get_filings(form="10-K").filter(filing_date="2025-08-07:2025-08-07")` |
| News + sentiment | Alpha Vantage | `?function=NEWS_SENTIMENT&tickers=AAPL&time_from=20250807T0000&time_to=20250807T2359` |
| Social sentiment | (no free historical) | Cache live snapshots forward; or pay Finnhub |
| Insider trades | edgartools | `Company("AAPL").get_filings(form="4", filing_date="2025-08-07:2025-08-07")` |
| Politician trades | FMP | `GET /senate-trades?symbol=AAPL` then filter `transactionDate==2025-08-07` |
| Notable holders | edgartools | `Company("AAPL").get_filings(form=["SC 13D","SC 13G"], filing_date="2025-08-07:2025-08-07")` |

---

## Cost summary if we replaced everything tomorrow

| Tier | Stack | Monthly cost |
|---|---|---|
| **Pure free** (current) | yfinance + edgartools + Finnhub free + FMP free + Alpha Vantage news | $0, social sentiment unavailable |
| **Cheapest viable paid** | Tiingo + edgartools + Finnhub Premium + FMP free | ~$10–20/mo (Finnhub social) |
| **Production-grade** | Polygon Starter + edgartools + Finnhub Premium + FMP Starter | ~$80/mo, no realtime caps |

The current free stack is the right choice for paper trading. Revisit when we flip the live-trading gate.
