# config/

Project-wide JSON configuration. One file per concern. Loaders live in `src/`
and reference these files by relative path (resolved from the project root).

| File | Purpose | Loader |
|---|---|---|
| `data.json` | Active provider per data domain + fetch defaults + HTTP timeout | `src/data/config.py` (`get_config()`) |
| `watchlist.json` | The list of tickers the bot trades | `src/orchestrator/stock_picker.py` (`get_watchlist()`) |
| `analyst_heuristics.json` | Thresholds + closed-vocabulary tag lists for all five analysts | `src/agents/analysts/heuristics.py` (`load_heuristics()`) |
| `schedule.json` | Tick cadence — how many ticks per day and their ET times | `src/config/schedule.py` (`get_schedule_config()`) |

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

---

## `analyst_heuristics.json` — analyst thresholds + vocabularies

Tunable constants consumed by all five analysts. Loaded once at boot via
`src/agents/analysts/heuristics.py::load_heuristics()` (`lru_cache(maxsize=1)`);
values are **not** hot-reloaded — a process restart is required after edits.
The `_check_heuristics()` hook in `src/lifecycle/initialise.py` validates this
file during the pre-flight sequence, so schema errors surface before any ticker
work begins.

### `technical` — deterministic Technical analyst

Thresholds used by `derive_technical_verdict()`.

| Setting | Type | Meaning |
|---|---|---|
| `rsi_overbought` | float [50–100] | RSI level considered overbought. |
| `rsi_oversold` | float [0–50] | RSI level considered oversold. |
| `pct_change_momentum_scale` | float >0 | Divisor scaling daily % change into a magnitude contribution. |
| `vol_ratio_breakout` | float >1 | Volume ratio (current/avg) above which a breakout is signalled. |
| `vol_ratio_dry_up` | float (0–1) | Volume ratio below which volume is considered dried-up. |
| `atr_high_volatility_pct` | float >0 | ATR as % of price above which volatility is flagged as high. |
| `near_52w_extreme_pct` | float >0 | Within this % of a 52-week high/low counts as "near extreme". |
| `confidence_base` | float [0–1] | Starting confidence before signal boosts/penalties. |
| `confidence_boost_step` | float [0–1] | Confidence added per corroborating signal. |
| `confidence_penalty_step` | float [0–1] | Confidence removed per contradicting signal. |
| `magnitude_cap` | float (0–1] | Maximum magnitude value emitted. |

### `social` — deterministic Social analyst

Thresholds used by `derive_social_verdict()`.

| Setting | Type | Meaning |
|---|---|---|
| `score_neutral_band` | float [0–1] | Sentiment scores within ±this value are treated as neutral. |
| `score_to_magnitude_scale` | float >0 | Scales raw sentiment score into a magnitude value. |
| `high_volume_mentions` | int >0 | Mention count above which volume is considered high. |
| `high_volume_magnitude_boost` | float [0–1] | Extra magnitude added when mention volume is high. |
| `confidence_volume_floor` | int ≥0 | Mention count below which confidence is capped at a low floor. |
| `platform_disagreement_threshold` | float [0–1] | Score spread between platforms above which disagreement is flagged. |
| `confidence_base` | float [0–1] | Starting confidence before signal boosts/penalties. |
| `confidence_boost_step` | float [0–1] | Confidence added per corroborating signal. |
| `confidence_penalty_step` | float [0–1] | Confidence removed per contradicting signal. |
| `magnitude_cap` | float (0–1] | Maximum magnitude value emitted. |

### `smart_money` — deterministic SmartMoney analyst

Thresholds used by `derive_smart_money_verdict()`.

| Setting | Type | Meaning |
|---|---|---|
| `multi_filer_min_count` | int ≥1 | Minimum distinct filers before a trade is considered multi-filer consensus. |
| `high_activity_trade_count` | int ≥1 | Trade count above which activity is flagged as high. |
| `lone_filer_confidence_floor` | float [0–1] | Confidence ceiling applied when only one filer is present. |
| `consensus_confidence_ceiling` | float [0–1] | Maximum confidence achievable on consensus signals. |
| `magnitude_cap` | float (0–1] | Maximum magnitude value emitted. |

### `fundamental_vocabulary` — closed-vocabulary tags for the Fundamental LLM

The Fundamental LLM must restrict its tag choices to exactly these lists.
Any tag not in the list will fail the extractor's closed-vocab check.

| Field | Meaning |
|---|---|
| `guidance` | Management guidance revision direction. |
| `tone` | Overall management tone on the call/filing. |
| `risks` | Risk tags surfaced in the filing or call. |
| `insider_signals` | Aggregate characterisation of recent insider trade activity. |

**Risk-tag suffix scheme:** a risk tag may carry an optional suffix to signal
change: `<tag>_added`, `<tag>_removed`, or `<tag>_intensified`
(e.g. `litigation_added`, `going_concern_intensified`). The base tag must
appear in the `risks` list; the suffix is appended at extraction time and does
not need its own entry.

### `news_vocabulary` — closed-vocabulary tags for the News LLM

The News LLM must restrict its tag choices to exactly these lists.

| Field | Meaning |
|---|---|
| `catalysts` | The primary event type driving the news story. |
| `novelty` | How new/market-moving the information is. |
| `direction` | Overall sentiment direction of the news batch. |

### `golden_set` — acceptance-gate tunables

| Setting | Type | Meaning |
|---|---|---|
| `min_direction_agreement_pct` | int [0–100] | Minimum % of golden-set tickers that must have consistent direction tags for the acceptance gate to pass. |

---

## `analysts.json` — analyst truncation caps + report cache

LLM context-window caps for the News and Fundamental analysts, plus the
toggle and directory for the hash-based report cache. Loaded once at boot via
`src/config/analysts.py::get_analysts_config()` (`lru_cache(maxsize=1)`); a
process restart is required after edits.

### `news` — News analyst input caps

| Setting | Type | Meaning |
|---|---|---|
| `news.max_articles_per_ticker` | int [1–200] | Maximum article count per ticker fed to the News LLM. Wider than the old hard-coded 10 — default 20. |
| `news.max_summary_chars` | int [1–10000] | Maximum characters of each article's summary kept in the prompt. Default 500 (widened from 300). |

### `fundamental` — Fundamental analyst input caps

| Setting | Type | Meaning |
|---|---|---|
| `fundamental.max_filing_mda_chars` | int [1–20000] | Character cap on the MD&A excerpt for each filing. Default 1500 (widened from 500). |
| `fundamental.max_filing_risk_chars` | int [1–20000] | Character cap on the risk-factors excerpt for each filing. Default 1500 (widened from 500). |
| `fundamental.max_insider_footnotes` | int [0–50] | Maximum insider footnote snippets included in the LLM prompt per ticker. Default 5. |
| `fundamental.max_insider_footnote_chars` | int [1–5000] | Character cap per footnote excerpt. Default 400 (widened from 200). |

### `cache` — LLM report cache

| Setting | Type | Meaning |
|---|---|---|
| `cache.enabled` | bool | Toggle the hash-based LLM report cache. When `false`, every tick re-prompts the LLM (matches pre-redesign behaviour). Default `true`. |
| `cache.directory` | string | On-disk root for cached report files. Must be under the gitignored `cache/` tree. Default `cache/reports`. |

---

## `schedule.json` — tick cadence

Controls how many times per trading day the bot runs its full analyst →
strategist pipeline, and when those ticks fire. Loaded once at boot via
`src/config/schedule.py::get_schedule_config()` (`lru_cache(maxsize=1)`);
a process restart is required after edits.

Tick times are expressed in **Eastern Time (`America/New_York`)** and are
DST-aware by design. The runner converts each time to UTC at scheduling
time using `zoneinfo.ZoneInfo("America/New_York")`, which pulls from the
OS tz database — no manual UTC offset arithmetic is needed. When EDT
(UTC-4) transitions to EST (UTC-5) or vice versa, the scheduled UTC wall
clock times adjust automatically.

| Setting | Type | Meaning |
|---|---|---|
| `ticks_per_day` | int [1–10] | Number of ticks expected per trading day. Must equal the length of `tick_times_et`. |
| `tick_times_et` | list[string] | Ordered list of `HH:MM` tick times in `America/New_York`. Each entry must be a valid 24-hour time. |
| `comment` | string | Operator annotation — not used at runtime. |

**Current schedule:** `09:45 ET` (~15 min after NYSE open) and `16:30 ET`
(~30 min after close). There is deliberate headroom to add a midday
`12:30 ET` tick once the hash-based report cache and richer narrative
reports prove themselves on paper data.
