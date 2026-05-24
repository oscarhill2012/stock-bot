# config/

Project-wide JSON configuration. One file per concern. Loaders live in `src/`
and reference these files by relative path (resolved from the project root).

| File | Purpose | Loader |
|---|---|---|
| `data.json` | Active provider per data domain + fetch defaults + HTTP timeout | `src/data/config.py` (`get_config()`) |
| `watchlist.json` | The list of tickers the bot trades | `src/orchestrator/stock_picker.py` (`get_watchlist()`) |
| `analyst_heuristics.json` | Thresholds + closed-vocabulary tag lists for all five analysts | `src/agents/analysts/heuristics.py` (`load_heuristics()`) |
| `analysts.json` | Per-analyst input caps + LLM output caps + report cache toggle | `src/config/analysts.py` (`get_analysts_config()`) |
| `schedule.json` | Tick cadence — how many ticks per day and their ET times | `src/config/schedule.py` (`get_schedule_config()`) |
| `strategist.json` | Character caps on strategist LLM free-text fields | `src/config/strategist.py` (`get_strategist_config()`) |
| `risk_gate.json` | Five position-sizing constraints for the risk gate | `src/config/risk_gate.py` (`get_risk_gate_config()`) |
| `models.json` | LLM + embedding model IDs for every model-using component | `src/config/models.py` (`get_models_config()`) |
| `retry_429.json` | Backoff + retry policy for Vertex AI HTTP 429 (RESOURCE_EXHAUSTED) responses. Per-agent timeout/schema retry counts live in `analysts.json` / `strategist.json`. | `src/config/retry_429.py` (`get_retry_429_policy()`) |
| `backtest_windows.json` | Era-keyed historical date windows for the backtest harness | `src/backtest/windows.py` (`load_windows()`) |
| `backtest_settings.json` | Backtests root (cache + runs nest per-window underneath), tick schedule, and lookback defaults for backtesting | `src/backtest/settings.py` (`get_backtest_settings()`) |

When adding or changing a config value: update the JSON file, then update the
relevant section in this README.

---

## `data.json` — data-provider shell

Selects the active provider for each data domain and tunes the fetch defaults
shared by all providers. Adding a new provider is a one-file drop in
`src/data/providers/<domain>/<name>.py` plus a one-line edit here.

| Setting | Type | Meaning |
|---|---|---|
| `providers.price_history` | string | Active provider name for OHLCV price history. |
| `providers.company_ratios` | string | Active provider for company fundamentals/ratios (active: `pit_composite`, fallback: `yfinance`) — XBRL fundamentals via edgartools + sliced yfinance OHLCV for price-derived technicals. PIT-correct. |
| `providers.news` | string | Active provider name for news articles (active: `finnhub`, fallback: `alpha_vantage`) — Finnhub `/company-news` endpoint with ~1-year retention. **Sentiment is dropped**: the free tier returns no per-article sentiment, so `NewsArticle.sentiment` is always `None` and the downstream extractor defaults missing sentiment to `0.0`. Swapped from `alpha_vantage` 2026-05-19 to unblock backtest cache-fills on windows within the last year. |
| `providers.social_sentiment` | string | Active provider name for social-sentiment scores (active: `finnhub`). Stays on `finnhub` for v1 — StockTwits (Row #13) deferred; social analyst soft-fails to `is_no_data=True` when data is unavailable. |
| `providers.insider_trades` | string | Active provider name for insider transactions. |
| `providers.politician_trades` | string | Active provider name for politician trades (active: `fmp`, fallback: `quiver`) — Financial Modeling Prep `/senate-trading` + `/senate-disclosure` (free 250/day). |
| `providers.notable_holders` | string | Active provider name for notable holders. |
| `providers.filings` | string | Active provider name for SEC filings. |
| `providers.earnings` | string | Active provider name for quarterly EPS / revenue history (active: `finnhub`). Returns the last four quarters of actuals (provider-internal default). PIT-correct on `report_date`. |
| `providers.analyst_consensus` | string | Active provider name for analyst target prices and rating revisions (active: `yfinance`). **Snapshot-only** — not PIT-correct for `as_of` older than ~7 days. |
| `providers.short_interest` | string | Active provider name for FINRA exchange-listed short-interest snapshots (active: `finra`). PIT-gated on `report_publish_date`. |
| `providers.options` | string | Active provider name for options chain data (active: `yfinance`). **Live-only shell** — backtest `as_of` calls return an empty dict. Row dropped from v1 per spec decision 7.1. |
| `defaults.news_lookback_days` | int | Default lookback window for news fetch. |
| `defaults.insider_lookback_days` | int | Default lookback window for insider trades. |
| `defaults.politician_lookback_days` | int | Default lookback window for politician trades. |
| `defaults.notable_holder_lookback_days` | int | Default lookback window for notable-holder snapshots. |
| `defaults.notable_holder_limit` | int | Max number of notable-holder rows returned. |
| `defaults.filings_per_form` | int | Max filings returned per SEC form type. |
| `defaults.include_filing_excerpts` | bool | Whether to attach filing excerpts to the bundle. |
| `defaults.filings_lookback_days` | int | Lookback window honoured by the backtest filings cache provider. Live EDGAR ignores it. Default 90. |
| `quiver_http_timeout_seconds` | float | HTTP timeout (seconds) for the Quiver Quant congressional-trades provider. |

Each `providers.<domain>` value must be a name registered in the matching
`src/data/providers/<domain>/` module. Validation happens at import time —
unregistered names refuse to import the `data` package.

**Phase 6 notes:**

- `providers.news` was `tiingo`; swapped to `alpha_vantage` in Phase 6 for
  richer per-article sentiment scores and per-ticker relevance filtering,
  then swapped again to `finnhub` (2026-05-19) for the recent-window
  backtest fills. Sentiment richness is traded away for retention reach —
  Finnhub's free tier covers ~1 year of history but exposes no per-article
  sentiment, so the News analyst runs on headline + summary text only.
- `providers.social_sentiment` stays on `finnhub` for v1 — Row #13
  (StockTwits) is deferred to the live-implementation plan because StockTwits
  requires a 30-day forward-cache warm-up before it is useful in backtesting.
  The Social analyst soft-fails to `is_no_data=True` per spec decision 9.3.

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

> **Shelved (2026-05-19).** The SmartMoney analyst is currently excluded from
> the analyst pool in `src/orchestrator/pipeline.py` because both of its
> input streams are unusable: `politician_trades` has no free PIT-correct
> historical source, and `notable_holders` issues a filer-side EDGAR query
> that returns the issuer's own filings rather than 13D/13G holdings of
> that issuer.  The heuristics block below is retained so the analyst can
> be revived in one line once a subject-side notable-holders provider
> lands.

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

### `slack_percent` — prompt-cap vs. schema-cap headroom (analyst outputs)

| Setting | Type | Meaning |
|---|---|---|
| `slack_percent` | int [0–50] | Schema-side headroom on top of every value in `output_caps`. The values there are the **prompt-facing** caps the LLM is told (e.g. "≤160 chars"); the schema in `src/contract/evidence.py` accepts `ceil(prompt_cap × (1 + slack_percent / 100))`. Independent of the strategist's `slack_percent` so each LLM tier can be tuned separately. Default 10. |

Same rationale as the strategist's `slack_percent` (see below) — LLMs tokenise
on subword boundaries and overshoot any stated `≤N chars` cap by ~1–5%, so we
tell them the prompt cap honestly and let the schema absorb the natural
overshoot rather than hard-truncating mid-sentence. The full reasoning lives
in the docstring of `src/config/analysts.py`.

### `output_caps` — analyst LLM free-text output caps

Character caps on the free-text fields emitted by the **LLM** analysts (News,
Fundamental). Deterministic analysts (Technical, SmartMoney, Social) emit no
free text so these caps don't apply to them. The values here are the
prompt-facing caps; the Pydantic schemas in `src/contract/evidence.py` derive
their `Field(max_length=...)` via `AnalystsConfig.schema_cap()`.

| Setting | Type | Meaning |
|---|---|---|
| `output_caps.verdict_rationale_max_chars` | int [50–1000] | Cap on `AnalystVerdict.rationale` — one-line summary of the dominant catalyst/finding. Default 160. |
| `output_caps.verdict_rationale_prompt_headroom_chars` | int | Headroom subtracted from the schema cap to derive the prompt-facing rationale budget. Keeps the LLM's natural overshoot inside the +slack_percent schema cap. Default 50 — raise if a future LLM tightens its character-counting; lower toward 0 if you want the LLM to use the full cap. |
| `output_caps.report_summary_max_chars` | int [200–8000] | Cap on `AnalystReport.summary` — the 3–5 sentence gestalt that argues the lean. Default 2000. |
| `output_caps.report_driver_name_max_chars` | int [20–200] | Cap on `ReportDriver.name` — short label (4–6 words). Default 60. |
| `output_caps.report_driver_body_max_chars` | int [100–4000] | Cap on `ReportDriver.body` — 2–3 sentence explanation per driver. Default 1000. |

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

---

## `strategist.json` — strategist free-text caps

Character caps on every free-text field the strategist LLM emits, plus the
caps on the `PositionThesis` records the strategist persists when opening a
position. Loaded once at boot via
`src/config/strategist.py::get_strategist_config()` (`lru_cache(maxsize=1)`);
a process restart is required after edits because Pydantic bakes the
`max_length` constraints into the model classes at import time.

**Philosophy — more is not always better.** These caps are summary budgets,
not space for the LLM to dump full chain-of-thought. Raising them is cheap
in the short term but bloats prompts and persistence rows, and quietly
nudges the model away from concise reasoning. If we ever feel the urge to
keep raising them, the right move is usually a separate retrieval layer
(RAG over historical rationales) rather than fatter on-tick payloads. Treat
the caps as a forcing function for the LLM to pick its strongest points.

### `slack_percent` — prompt-cap vs. schema-cap headroom

| Setting | Type | Meaning |
|---|---|---|
| `slack_percent` | int [0–50] | Schema-side headroom on top of every cap below. The values in `decision_caps` / `stance_caps` / `position_thesis_caps` are the **prompt-facing** caps the LLM is told (e.g. "≤600 chars"); the schema accepts `ceil(prompt_cap × (1 + slack_percent / 100))`. Default 10. |

LLMs do not count characters reliably — they tokenise on subword boundaries
and treat any `≤N chars` instruction as a fuzzy length *vibe*, so live runs
show the strategist overshooting any stated cap by roughly 1–5% (occasionally
up to 10%). Rather than hard-truncating mid-sentence — losing information
right where the conclusion usually sits — we tell the model the prompt cap
honestly and let the schema absorb the natural overshoot via `slack_percent`.
If validation starts raising on length, the signal is to either raise this
knob or to actually build a soft-clip module; until then it's the simplest
mechanism that keeps data clean without losing meaning. See the docstring of
`src/config/strategist.py` for the full rationale.

### `decision_caps` — top-level `StrategistDecision` fields

| Setting | Type | Meaning |
|---|---|---|
| `decision_caps.reasoning_max_chars` | int [50–2000] | Cap on `StrategistDecision.reasoning` — the overall summary across all stances. Raised from the original 300 after live runs showed Gemini routinely overflowed. Default 600. |
| `decision_caps.updated_thesis_max_chars` | int [50–2000] | Cap on `StrategistDecision.updated_thesis` — the working hypothesis carried into the next tick. Default 500. |

### `stance_caps` — per-ticker `TickerStance` fields

| Setting | Type | Meaning |
|---|---|---|
| `stance_caps.rationale_max_chars` | int [50–1000] | Cap on `TickerStance.rationale` — brief justification for the stance. Default 200. |
| `stance_caps.catalyst_max_chars` | int [20–500] | Cap on `TickerStance.catalyst` — optional near-term catalyst. Default 80. |
| `stance_caps.close_reason_max_chars` | int [20–500] | Cap on `TickerStance.close_reason` — why the position is being fully closed. Default 120. |
| `stance_caps.trim_reason_max_chars` | int [20–500] | Cap on `TickerStance.trim_reason` — why the position is being reduced but not closed. Default 120. |

### `position_thesis_caps` — persisted `PositionThesis` fields

| Setting | Type | Meaning |
|---|---|---|
| `position_thesis_caps.rationale_max_chars` | int [50–2000] | Cap on `PositionThesis.rationale` — why we entered the position. Longer than the per-tick stance rationale because it must survive across many ticks. Default 400. |
| `position_thesis_caps.catalyst_max_chars` | int [20–500] | Cap on `PositionThesis.catalyst` — optional named catalyst for the held position. Default 100. |
| `position_thesis_caps.last_review_note_max_chars` | int [20–1000] | Cap on `PositionThesis.last_review_note` — short note appended each tick we review (but do not close) the position. Default 200. |

The strategist prompt template at `src/agents/strategist/prompts.py` reads
the same config singleton and substitutes the `≤N chars` markers at module
load, so the prompt-facing caps the LLM is told are always the values from
this file. The schema's `Field(max_length=...)` is then derived from those
values via `StrategistConfig.schema_cap()` (see `slack_percent` above) —
the two-tier gap is intentional and load-bearing; do not "fix" it.

---

## `risk_gate.json` — risk-gate position-sizing constraints

The five hard limits applied by `src/agents/risk_gate/constraints.py` to
every proposed portfolio before orders are generated.  Centralised here so
they are operator-tunable without a code change.

Loaded at import time by `src/config/risk_gate.py::get_risk_gate_config()`
(`lru_cache(maxsize=1)`) and re-exported as module-level constants from
`src/orchestrator/state.py` (e.g. `MAX_DELTA_PER_TICKER`) so every
existing `from orchestrator.state import …` call site keeps working
unchanged.  A process restart is required after edits.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `min_held_weight` | float [0.0–0.10] | 0.001 | Minimum weight above which a position is counted as "open". Below this threshold a position is treated as closed for telemetry purposes. |
| `max_position_weight` | float (0.0–1.0] | 0.20 | Single-ticker concentration cap — no ticker may hold more than this fraction of the portfolio after the gate runs. |
| `cash_floor_weight` | float [0.0–0.50] | **0.00** (R1) | Minimum cash reserve fraction. When total invested weight would exceed `1 − cash_floor_weight`, all weights are scaled down proportionally. Set to `0.00` (R1 — raised from `0.10`) to let the strategist be fully invested during the post-baseline backtest. |
| `max_delta_per_ticker` | float (0.0–1.0] | **0.05** (R2) | Maximum weight change per tick per ticker. Widened from `0.01` to `0.05` (R2) so the gate does not force unrealistically slow entry/exit ramps during backtest evaluation. |
| `max_total_turnover` | float (0.0–2.0] | **0.50** (R3) | Maximum total portfolio turnover per tick (sum of absolute weight changes across all tickers). Raised from `0.30` to `0.50` (R3) to match the wider per-ticker delta ceiling. |

**R1/R2/R3 rationale.**  The original values (`cash_floor=0.10`,
`max_delta=0.01`, `max_turnover=0.30`) were conservative placeholders
chosen before any backtest data existed.  After the baseline run the gate
was so tight that the strategist was unable to meaningfully adjust
positions within a single tick — the observed turnover ceiling was the
binding constraint on every tick.  The relaxed envelope (R1+R2+R3) gives
the strategist room to express its views across a one- or two-tick horizon
without artificially suppressing its ability to enter or exit.  The
constraint logic itself is unchanged — only these threshold values move.

---

## `models.json` — LLM + embedding model IDs

Single source of truth for every model identifier the pipeline consumes.
Before this file landed, each agent module hardcoded its own `gemini-…`
literal; a 2026-05-20 incident showed how easily two parallel literals drift
out of sync (the strategist's `_STRATEGIST_MODEL` constant was edited but
the *live* literal lived in a different file, so the swap silently no-op'd
for several backtest runs).

Loaded once at boot via `src/config/models.py::get_models_config()`
(`lru_cache(maxsize=1)`); a process restart is required after edits — the
loader does not hot-reload.

**Convention.** Each agent module reads *its* model ID from this config at
construction time. The value lives here; the selection of which slot to
read lives in the agent's own module. Wiring layers (`pipeline.py`) never
pick a model directly.

| Setting | Type | Meaning |
|---|---|---|
| `strategist` | string | Model ID for the Strategist `LlmAgent` (read by `src/agents/strategist/agent.py::build_strategist`). Currently `gemini-3.5-flash` — trialling next-gen Flash. |
| `news_analyst` | string | Model ID for the News analyst `LlmAgent` (read by `src/agents/analysts/news/agent.py::build_news_analyst`). Currently `gemini-2.5-flash-lite`. |
| `fundamental_analyst` | string | Model ID for the Fundamental analyst `LlmAgent` (read by `src/agents/analysts/fundamental/agent.py::build_fundamental_analyst`). Currently `gemini-2.5-flash-lite`. |
| `memory_compressor` | string | Model ID for the day-digest LLM compressor fallback (read by `src/agents/memory/compress.py::_default_llm_compress`). Only invoked when the concatenated digest exceeds `DIGEST_BUDGET` (2000 chars). Currently `gemini-2.5-flash-lite`. |
| `memory_embedding` | string | Embedding model ID for the memory-buffer dedup embedder (read by `src/agents/memory/embeddings.py::_default_embed`). Distinct family from Gemini chat models, but the same "where does this live" problem belongs in the same config. Currently `text-embedding-005`. |

A contract test (`tests/contract/test_no_hardcoded_models.py`) AST-walks
`src/` and fails CI if any string literal starting with `gemini-` or
`text-embedding-` survives outside docstrings or comments. The escape hatch
for legitimate documentation references is to put the literal in a
docstring or behind a `# noqa: model-literal` comment.

A leading `_comment` field is permitted at the top of `models.json` for an
operator-facing note; the loader strips it before validation.

---

## `retry_429.json` — Vertex AI HTTP 429 backoff + retry policy

Retry policy for Vertex AI `HTTP 429 RESOURCE_EXHAUSTED` responses, applied to
every LLM-bearing agent in the pipeline (Fundamental, News, Strategist). Wraps
each branch in `src/agents/llm_retry.py::RetryingAgentWrapper`, which catches
the 429 and re-runs the inner agent with exponential-with-jitter backoff before
failing the tick.

Loaded once at boot via `src/config/retry_429.py::get_retry_429_policy()`
(`lru_cache(maxsize=1)`); a process restart is required after edits.

Per-agent timeout budgets and schema-validation retry counts live in
`config/analysts.json` and `config/strategist.json` — only the 429 back-off
policy is project-wide and stored here.

**Why this is needed.** Vertex AI's Gemini models share capacity via Dynamic
Shared Quota by default — transient 429s are a normal operating condition
even at modest call volume, because the global pool can saturate from other
customers' traffic. Google's own guidance is that the *client* implements
exponential backoff; ADK does not, and the underlying `google.genai` SDK's
tenacity wrapper excludes 429 from its retry set.

**Scope of retry.** Only 429 (`_ResourceExhaustedError` /
`ClientError(status_code=429)`) triggers retry. 5xx responses and other 4xx
errors propagate immediately — they typically signal a real outage or a
malformed request that retrying cannot fix.

| Setting | Type | Meaning |
|---|---|---|
| `max_attempts` | int ≥1 | Total number of attempts (not retries after the first failure). `1` disables retries entirely. Default 5. |
| `base_delay_seconds` | float >0 | Initial wait before the first retry, in seconds. Subsequent retries grow exponentially with jitter, capped at `max_delay_seconds`. Default 2.0. |
| `max_delay_seconds` | float ≥ `base_delay_seconds` | Upper bound on any single inter-retry wait, in seconds. Default 30.0. |

A leading `_comment` field is permitted at the top of `retry_429.json`; the
loader strips it before validation.

---

## `backtest_windows.json` — era-window definitions

Era-keyed historical windows for the backtest harness. Each entry:

- `start` / `end`: ISO date strings (inclusive); tick schedule covers NYSE business days in the range.
- `notes`: free-form description of the regime this window captures.

Add new windows by editing this file — no code changes needed.

---

## `backtest_settings.json` — backtest runtime settings

Runtime defaults for the backtest harness. All path settings are relative to
the project root.

| Setting | Type | Meaning |
|---|---|---|
| `backtests_root` | string | Single root directory under which every window nests its own cache and runs. Resolves to `<root>/<window>/store.sqlite` for the golden cache and `<root>/<window>/runs/<run-id>/` for run artefacts. Use `cache_path_for_window` / `runs_root_for_window` helpers in `src/backtest/settings.py` to derive concrete paths — never hand-join. |
| `ticks_per_day` | list[string] | Named tick phases emitted each NYSE session (subset of `["open", "close"]`). |
| `failed_tick_abort_ratio` | float [0–1] | Fraction of ticks allowed to fail before the harness aborts the run. |
| `fake_broker_starting_cash` | float | Starting cash balance (USD) for the in-memory fake broker used in backtests. |
| `forward_return_horizons_days` | list[int] | Horizons (in calendar days) over which forward returns are computed for scoring. |
| `ohlcv_warmup_days` | int | Extra calendar days of OHLCV history fetched before the window start during cache fill, so rolling indicators (RSI(14), ATR(14), pct_change_20d) have enough bars to compute on the first tick. |

**Per-window storage layout.** Each window owns its own subtree — there is
no shared cache across windows.  Example:

```
backtests/
└── svb-stress-2023-03/
    ├── store.sqlite          # golden cache for this window only
    └── runs/
        └── svb-stress-2023-03-abc1234/
            ├── manifest.json
            ├── db.sqlite
            └── ...
```

Scripts that only take a `--run-id` (e.g. `backtest_report`,
`backtest_audit_tick`) recover the window key by parsing the run-id
prefix via `backtest.settings.window_from_run_id` — run-IDs follow
`<window>-<7-char git sha>`.

**Why no `tz`/`open_time`/`close_time`?**  NYSE session times — including
early-close days such as the day after Thanksgiving — are owned by
`pandas_market_calendars` (`_NYSE.schedule(...)`).  Honouring a duplicate
config setting here would silently desynchronise tick timestamps from the
PIT cache on every early-close session.  `ticks_per_day` is the only
schedule-shaped knob the harness still owns — it selects which phases of
each session to fire (`"open"`, `"close"`).
