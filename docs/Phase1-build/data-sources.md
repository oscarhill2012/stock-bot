# Data Sources

The data layer exposes **six canonical functions**. Each one lives in `data/providers/<source>.py`, returns a Pydantic model from `data/models/`, and is the only sanctioned way for downstream agents to reach an external API.

## Function ↔ Library Map

| Function | Library | Auth | Notes |
|---|---|---|---|
| `get_stock_news` | `finnhub-python` | `FINNHUB_API_KEY` | Company-tagged news headlines + URLs + per-article sentiment. |
| `get_stock_stats` | `yfinance` | none | OHLCV history + summary fundamentals (P/E, market cap, beta, etc.). |
| `get_public_figure_trades` | `requests` against Quiver Quant REST API | `QUIVER_QUANT_API_KEY` | Congressional / senate trade disclosures. |
| `get_insider_trades` | `edgartools` (direct EDGAR) | `EDGAR_IDENTITY` | Form 4 insider buy/sell transactions. |
| `get_social_sentiment` | `finnhub-python` | `FINNHUB_API_KEY` | Aggregated Reddit + Twitter sentiment scores. |
| `get_company_filings` | `edgartools` (direct EDGAR) | `EDGAR_IDENTITY` | Full-text 10-K / 10-Q / 8-K filings + Item 1A / Item 7 excerpts. |

## Function Sketches

```python
# data/providers/finnhub_news.py
def get_stock_news(ticker: str, from_date: date, to_date: date) -> list[NewsArticle]:
    """Company news between two dates. Wraps finnhub_client.company_news()."""

# data/providers/yfinance_stats.py
def get_stock_stats(ticker: str, period: str = "1y", interval: str = "1d") -> StockStats:
    """OHLCV history + the summary fields from yf.Ticker(...).info."""

# data/providers/quiver_politicians.py
def get_public_figure_trades(ticker: str | None = None,
                             lookback_days: int = 90) -> list[PoliticianTrade]:
    """GET https://api.quiverquant.com/beta/live/congresstrading"""

# data/providers/sec_insiders.py
def get_insider_trades(ticker: str, lookback_days: int = 30) -> list[InsiderTrade]:
    """edgartools Form 4 listing → parsed insider buys/sells with size + role."""

# data/providers/finnhub_social.py
def get_social_sentiment(ticker: str) -> SocialSentiment:
    """finnhub_client.stock_social_sentiment(): mention counts + score per platform."""

# data/providers/sec_filings.py
def get_company_filings(ticker: str,
                        form_types: list[str] = ("10-K", "10-Q", "8-K"),
                        limit: int = 5) -> list[Filing]:
    """edgartools filing listing + per-form section extraction (Item 1A, Item 7)."""
```

## Cross-Cutting Rules

- **Every provider is wrapped in `tenacity` retry** with exponential back-off — flaky third-party APIs must not crash an agent run.
- **Outputs are Pydantic models, never raw dicts.** Agents consume models; tests assert on models.
- **Rate-limit budgets live in `data/providers/__init__.py`** as constants so they are visible at a glance and easy to tune.
- **No agent imports a provider directly.** They go through the orchestrator, which can swap a real call for a cached fixture during tests.

## Free-tier Caveats (worth knowing now)

- **Finnhub free tier:** US stocks only. Social/market sentiment **is** included. Rate limits: **60 calls / minute** and a burst cap of **30 calls / second**. Throttle accordingly — bursting will get us 429'd before the per-minute budget is exhausted.
- **yfinance:** unofficial scrape of Yahoo. No rate limit published, but throttle ourselves anyway. Occasional schema breaks — pin the version.
- **Quiver Quant:** free trial limited; congress data has a ~24h delay even on paid tiers.
- **EDGAR (via edgartools):** free, no API key, no quota. Hard cap of **10 requests / second** enforced by SEC. Every request **must** carry a contact email in the User-Agent — set `EDGAR_IDENTITY="Your Name your@email"` in `.env` before any SEC call.
