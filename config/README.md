# config/

Project-wide JSON configuration. One file per concern. Loaders live in `src/`
and reference these files by relative path (resolved from the project root).

| File | Purpose | Loader |
|---|---|---|
| `data.json` | Active provider per data domain + fetch defaults + HTTP timeout | `src/data/config.py` (`get_config()`) |
| `watchlist.json` | The list of tickers the bot trades | `src/orchestrator/stock_picker.py` (`get_watchlist()`) |

When adding or changing a config value: update the JSON file, then update the
relevant section in this README.

---

## `data.json` — data-provider shell

Selects the active provider for each data domain and tunes the fetch defaults
shared by all providers. Adding a new provider is a one-file drop in
`src/data/providers/<domain>/<name>.py` plus a one-line edit here.

| Setting | Type | Meaning |
|---|---|---|
| `providers.stats` | string | Active provider name for stock stats (price, fundamentals, history). |
| `providers.news` | string | Active provider name for news articles. |
| `providers.social_sentiment` | string | Active provider name for social-sentiment scores. |
| `providers.insider_trades` | string | Active provider name for insider transactions. |
| `providers.politician_trades` | string | Active provider name for politician trades. |
| `providers.notable_holders` | string | Active provider name for notable holders. |
| `providers.filings` | string | Active provider name for SEC filings. |
| `defaults.news_lookback_days` | int | Default lookback window for news fetch. |
| `defaults.insider_lookback_days` | int | Default lookback window for insider trades. |
| `defaults.politician_lookback_days` | int | Default lookback window for politician trades. |
| `defaults.notable_holder_lookback_days` | int | Default lookback window for notable-holder snapshots. |
| `defaults.notable_holder_limit` | int | Max number of notable-holder rows returned. |
| `defaults.history_period` | string | yfinance-style period for stats history (e.g. `"1y"`). |
| `defaults.history_interval` | string | yfinance-style interval for stats history (e.g. `"1d"`). |
| `defaults.filings_per_form` | int | Max filings returned per SEC form type. |
| `defaults.include_filing_excerpts` | bool | Whether to attach filing excerpts to the bundle. |
| `http_timeout_seconds` | float | Shared HTTP timeout applied to provider clients. |

Each `providers.<domain>` value must be a name registered in the matching
`src/data/providers/<domain>/` module. Validation happens at import time —
unregistered names refuse to import the `data` package.

---

## `watchlist.json` — tradeable universe

The static set of tickers the bot considers each tick.

| Setting | Type | Meaning |
|---|---|---|
| `tickers` | list[string] | Watchlist tickers (e.g. `["AAPL", "MSFT", ...]`). Order is not significant. |

Loaded once via `orchestrator.stock_picker.get_watchlist()`. Strategist + risk
gate both expect every ticker in this list to appear in their inputs (see
`make_exhaustive_validator`).
