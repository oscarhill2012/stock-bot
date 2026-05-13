# Analyst Re-Categorisation + Deterministic-First — Design Spec

> **Status:** specced. Implementation plan in `plan.md`.
> **Phase:** Post-Phase-4. Replaces the original *Analyst LLM Narrowing* direction with a data re-categorisation step in front of it, and reserves further LLM expansion for testing-justified ratchets.

This spec restructures the analyst tier in two parallel moves:

1. **Re-categorise the data sources** so each analyst's scope reflects its concern (what question is it answering?), not its provider history (which Pydantic model fed it first).
2. **Apply the deterministic-vs-LLM rule** from the original Phase 5 plan: strip LLMs from analysts whose data has no narrative content; keep narrowed LLMs where prose genuinely exists (MD&A / risk-factor excerpts, news headlines + summaries, Form 4 footnotes).

The Phase 4 contract (`AnalystEvidence` / `TickerEvidence` / `AggregateVerdict`) survives unchanged. Every analyst — deterministic or LLM — still emits the same evidence shape; the downstream digest, strategist, risk gate, executor, and persistence are untouched. One small breaking change is unavoidable: the `AnalystName` `Literal` gains two members (`"news"`, `"social"`) and loses one (`"sentiment"`).

---

## Why

Phase 4 introduced deterministic per-analyst feature extractors plus a deterministic digest. With that contract in place, the analyst tier became free to re-organise itself without rippling into downstream code. Three problems with the present shape need that freedom.

### 1. Provider-driven analyst boundaries

Today's analysts were named for the *first* data source they consumed, not for the concern they answer:

- `fundamental` reads only `stats/` company financials. Insider trading (Form 4) and SEC filings (10-K / 10-Q / 8-K) — both genuinely "fundamental" inputs — live elsewhere.
- `sentiment` reads only `news/`. Social-platform sentiment (Finnhub Reddit + Twitter aggregates) is fetched by a separate provider but never reaches an analyst.
- `smart_money` reads insider trades alongside congressional trades and 13F holdings. Insider trades belong to fundamental analysis (officers/directors transacting in their *own* company); 13F and congressional trades are external-observer flows.

The right boundary is *concern*, not *provider*. After this refactor:

- **Fundamental** answers *"What does the company itself look like — financials, narrative disclosures, internal-actor behaviour?"* — reads `stats/` + `filings/` + `insider_trades/`.
- **News** answers *"What is the journalism narrative saying right now?"* — reads `news/`.
- **Social** answers *"What does the retail-investor crowd think right now?"* — reads `social_sentiment/`.
- **SmartMoney** answers *"What are external sophisticated observers doing?"* — reads `politician_trades/` + `notable_holders/`.
- **Technical** is unchanged — *"What is the price doing?"* — reads `stats/`.

### 2. LLMs on numeric-only data have no information advantage

Phase 4's deterministic extractors already reduce the underlying data to feature vectors. For three of the new analyst boundaries, the LLM would re-derive a `lean / magnitude / confidence` triple from the same numbers the extractor exposes, with no prose available to add anything the extractor cannot:

- **Technical** — OHLCV → indicators. No prose, no future RAG channel.
- **Social** — Finnhub `stock_social_sentiment` returns aggregate `mention_count` / `positive_score` / `negative_score` per platform. The raw Reddit/Twitter posts are *not* returned by Finnhub; we receive pre-reduced numerics. An LLM over those numerics is doing arithmetic with extra steps.
- **SmartMoney** — congressional + 13F filings reduced to dollar amounts, sides, and filer roles. Categorical scoring the extractor performs better.

For the remaining two analysts, prose genuinely exists and the LLM has a real job:

- **Fundamental** — `mda_excerpt` and `risk_factors_excerpt` are populated by `edgartools` for every filing. Form 4 footnotes (newly captured — see *Insider expansion* below) carry transaction-level context that the structured fields cannot.
- **News** — Finnhub `company_news` returns headlines AND per-article `summary` text.

The deterministic-vs-LLM rule from the original spec stands: **no prose → strip LLM. Prose exists → keep a narrowed LLM with a closed-vocabulary mandate.**

### 3. Minimum-LLM-as-baseline ratchet

Phase 5 commits the project to a policy: the baseline is the minimum LLM use that meets the prose-reading test (Strategist + Fundamental + News). Future LLM expansion (per-stock prose reports, Bull/Bear debate, RAG retrievers, etc.) must be justified by trace-data evidence that the baseline misses something material, and must be backlogged with explicit shelve-criteria before being scheduled. The surface-trace harness (retained from the original spec, *purpose* repositioned) is the measurement instrument that makes the ratchet possible.

---

## Goals

1. Each analyst has a single coherent concern, fed by the providers that actually serve that concern.
2. Every surviving analyst LLM has a prose-or-prose+numeric-supplement mandate the deterministic extractor cannot fulfil alone.
3. Every analyst (deterministic or LLM) emits the same `AnalystEvidence` shape; downstream code is unchanged.
4. `key_factors` becomes a closed-vocabulary tag list across all five analysts; KB-indexable without backfill.
5. Heuristic thresholds + tag vocabularies live in `config/analyst_heuristics.json`, covering all five analysts.
6. LLM ratchet is project policy: more LLM only when trace data justifies it; deterministic is the baseline.
7. The refactor is the first live-LLM-validated milestone for the project: a surface trace captures full JSON at every pipeline boundary for manual inspection.

## Non-goals

- Designing or implementing RAG, vector stores, or filings KBs. The spec only requires that this refactor leaves a clean slot.
- Sparse / triggered LLM execution (only call fundamental when a new filing has landed). The current every-tick batched call survives.
- Per-stock per-analyst prose reports (TradingAgents-style). Backlog (B14).
- Schedule 13D / 13G activist-letter narrative analyst. Backlog (B10).
- Market-regime analyst (VIX, put/call, AAII sentiment). No provider exists; backlog (B15).
- Strategist prompt, agent code, or schema changes (Plan E covers strategist hardening separately).
- Embedding columns, prompt-versioning systems, LLM cost telemetry.

---

## Analyst pool — final shape

| Analyst | Sources | Implementation | Mandate |
|---|---|---|---|
| **Technical** | `stats/` (OHLCV + indicators) | `BaseAgent` (deterministic) | `derive_technical_verdict(features, h)` over indicators |
| **Fundamental** | `stats/` (financials) + `filings/` (MD&A, risk factors) + `insider_trades/` (Form 4 + footnotes + derivatives) | `LlmAgent` (closed-vocab narrowed) | Read prose (MD&A, risk factors, Form 4 footnotes) + numeric supplement (ratios, insider flows); emit closed-vocab tags + verdict |
| **News** | `news/` (Finnhub headlines + summaries) | `LlmAgent` (closed-vocab narrowed) | Read prose; classify catalyst, novelty, direction, materiality |
| **Social** | `social_sentiment/` (Finnhub Reddit + Twitter aggregates) | `BaseAgent` (deterministic) | `derive_social_verdict(features, h)` over mention / polarity aggregates |
| **SmartMoney** | `politician_trades/` (Quiver) + `notable_holders/` (13F) | `BaseAgent` (deterministic) | `derive_smart_money_verdict(features, h)` over flow + consensus features |

`ParallelAgent("AnalystPool", ...)` grows from 4 to 5 children. The strategist remains the only LLM downstream.

---

## Data re-categorisation — concrete migration

Each analyst's fetch callback and feature extractor are rewired against its new provider set. The `make_evidence_callback` after-callback (writes `{analyst}_evidence`) is unchanged — it remains analyst-agnostic.

### Fetch callbacks

| Analyst | Today's fetch fn | New fetch sources |
|---|---|---|
| Technical | `technical_fetch_callback` | unchanged (`stats/` only) |
| Fundamental | `fundamental_fetch_callback` | **adds** `filings/` + `insider_trades/`; existing `stats/` retained |
| News | (renamed from `sentiment_fetch_callback`) | only `news/`; the `social_sentiment/` call is *removed* and migrates to the new Social analyst |
| Social | new `social_fetch_callback` | only `social_sentiment/` |
| SmartMoney | `smart_money_fetch_callback` | **removes** `insider_trades/`; `politician_trades/` + `notable_holders/` retained |

State keys follow the existing convention: `{analyst}_data` holds the dict of raw per-ticker payloads written by the fetch callback. New keys: `news_data`, `social_data`. Removed: `sentiment_data`.

### Extractors

Each extractor at `src/contract/extractors/<analyst>.py` consumes its analyst's raw data and emits a feature vector:

- **`extract_technical_features`** — unchanged.
- **`extract_fundamental_features`** — gains insider columns (`insider_net_dollars_30d`, `insider_n_buys_30d`, `insider_n_sells_30d`, `insider_cluster_buy_flag`, `insider_cluster_sell_flag`, `insider_planned_sale_ratio`, `insider_max_filer_role_rank`, `insider_derivative_exercise_count`, `insider_derivative_grant_count`); gains filings-derived numeric columns (`days_since_last_filing`, `n_filings_30d`); existing ratio / financials columns retained.
- **`extract_news_features`** (renamed from `extract_sentiment_features`) — unchanged in logic; scoped only to news data.
- **`extract_social_features`** — **new**. Produces `mention_count_total`, `mention_count_reddit`, `mention_count_twitter`, `aggregate_score`, `score_velocity_24h` (delta vs prior tick if available, else 0), `platform_score_disagreement`, `is_no_data`.
- **`extract_smart_money_features`** — loses insider columns; retains politician + 13F columns.

The `feature_warnings` channel introduced by the evidence callback remains a no-op; warnings emission is a follow-up.

### Pipeline composition

`src/orchestrator/pipeline.py::_build_analyst_pool` grows by one child:

```python
def _build_analyst_pool():
    """Build a fresh AnalystPool each tick.

    Five children: three deterministic BaseAgents (Technical, Social,
    SmartMoney) and two narrowed LlmAgents (Fundamental, News).
    """
    from google.adk.agents import ParallelAgent

    from agents.analysts.fundamental.agent import _build_fundamental_analyst
    from agents.analysts.news.agent          import _build_news_analyst
    from agents.analysts.social.agent        import _build_social_analyst
    from agents.analysts.smart_money.agent   import _build_smart_money_analyst
    from agents.analysts.technical.agent     import _build_technical_analyst

    h = load_heuristics()
    return ParallelAgent(
        name="AnalystPool",
        sub_agents=[
            _build_technical_analyst(h.technical),         # BaseAgent
            _build_fundamental_analyst(h.fundamental),     # LlmAgent
            _build_news_analyst(h.news_vocabulary),        # LlmAgent
            _build_social_analyst(h.social),               # BaseAgent
            _build_smart_money_analyst(h.smart_money),     # BaseAgent
        ],
    )
```

---

## Insider expansion (Form 4 deep-pull)

Today's `insider_trades/edgar.py` extracts only `common_stock_purchases` and `common_stock_sales` rows with five fields each (shares / price / date / insider name / title). Form 4 carries substantially more, and the Fundamental analyst's mandate now justifies pulling it.

### `InsiderTrade` model — new fields

```python
class InsiderTrade(BaseModel):
    """One Form 4 common-stock transaction row.

    Captures both the structured fields the existing extractor consumes
    AND the narrative supplement (footnote + transaction code + 10b5-1
    flag) that lets the Fundamental LLM separate mechanical sales from
    discretionary ones.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide                       # "buy" | "sell"
    shares: float
    price_per_share: float | None = None
    transaction_date: date
    filed_at: datetime
    form_type: str                        # "4", "4/A", "3", "5"

    # NEW — narrative + categorical supplement.
    transaction_code: str | None = None   # P/S/A/M/F/G/D/X — Form 4 Table I col 3
    is_10b5_1: bool = False               # From form flag or footnote regex
    footnote: str | None = None           # Free-text footnote on the row (prose)
```

`transaction_code` and `is_10b5_1` feed the extractor's numeric features; `footnote` feeds the LLM prompt as prose.

### `InsiderDerivativeTrade` model — new

Form 4 also reports derivative transactions (option exercises, option grants, RSU vestings) in a separate table. These are often the most signal-rich entries — they carry strike prices, vesting conditions, and explanatory footnotes.

```python
class InsiderDerivativeTrade(BaseModel):
    """One Form 4 derivative-securities transaction row.

    Option exercises, option grants, RSU vestings, warrant transactions.
    Strike + underlying-shares + footnote together describe whether a
    transaction is dilutive vesting, an in-the-money exercise, an
    exercise-and-hold (bullish), or an exercise-and-dump.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide
    derivative_type: str | None = None    # "option", "rsu", "warrant", "performance_award"
    underlying_shares: float
    strike_price: float | None = None
    transaction_date: date
    filed_at: datetime
    transaction_code: str | None = None
    is_10b5_1: bool = False
    footnote: str | None = None
```

### `edgar.py` changes

- `_parse_form4` is extended to read the derivative-transactions table alongside the common-stock tables.
- A new `_extract_footnote(row, form4_obj)` helper resolves a row's footnote reference IDs against the form-level footnote map.
- `transaction_code` is read from each row's `TransactionCode` / `transaction_code` field.
- `is_10b5_1` is derived from the form-level `equity_swap_or_planned_sale` flag if present, else from a regex over the footnote text (`/10b5-?1/i`).
- The fetch function returns a `Form4Bundle` wrapper carrying `trades: list[InsiderTrade]` and `derivatives: list[InsiderDerivativeTrade]`. The Fundamental fetch callback consumes both.

### Fundamental LLM prompt — insider supplement

The Fundamental prompt gains a small structured block of insider numerics plus a list of footnote snippets:

```
--- COMPANY FILINGS (PROSE) ---
{filings_excerpts}

--- INSIDER ACTIVITY (30d, structured) ---
net Form-4 dollars: {insider_net_dollars_30d}
buys / sells (count):     {insider_n_buys_30d} / {insider_n_sells_30d}
cluster_buying:           {insider_cluster_buy_flag}
planned-sale ratio (10b5-1): {insider_planned_sale_ratio}
top filer role:           {insider_max_filer_role_name}
derivative exercises:     {insider_derivative_exercise_count}
derivative grants:        {insider_derivative_grant_count}

--- INSIDER FOOTNOTES (≤5, prose) ---
{insider_footnote_excerpts}
```

The prompt instruction widens beyond strict prose-only: *"Reason over MD&A, risk factors, AND insider activity (both numeric flows and footnote prose). Treat planned 10b5-1 sales as low-signal; treat clustered open-market buys by multiple officers as high-signal; treat exercise-and-hold as bullish (insider declining to sell after exercise); treat exercise-and-dump as bearish."*

### Vocabulary additions

A new tag family in `fundamental_vocabulary`:

```json
{
  "insider_signals": [
    "cluster_buying",
    "cluster_selling",
    "planned_sale_dominant",        // most sales are 10b5-1; discount weight
    "discretionary_sale_dominant",
    "option_exercise_dump",         // exercise then sell — footnote-driven
    "option_exercise_hold",         // exercise without sell — bullish
    "gift_disposal",
    "mixed"
  ]
}
```

Insider tags are emitted via `insider:<value>` prefix in `key_factors`, identical to the existing `risk:` / `tone:` / `guidance:` scheme.

---

## Deterministic verdict heuristics

Three deterministic analysts. Each has a `derive_<analyst>_verdict(features, h: <Analyst>Heuristics) -> AnalystVerdict` function next to its extractor under `src/contract/extractors/`. All three are pure (no I/O, no globals) and unit-testable as plain Python.

### `derive_technical_verdict(features, h: TechnicalHeuristics) -> AnalystVerdict`

- **`is_no_data`** when `features["rsi_14"] == 0 and features["pct_change_20d"] == 0 and features["atr_pct_14"] == 0` (the extractor's zero-on-empty fingerprint). Returns `lean="neutral"`, `magnitude=0`, `confidence=0`, `is_no_data=True`.
- **Lean** from a composite trend sign: `sign(pct_change_20d)` weighted by agreement with `sign(pct_change_5d)`. Two flips override the trend:
  - `rsi_14 > h.rsi_overbought` → cap lean at neutral; flip to bearish if `pct_change_5d > 0` (exhaustion).
  - `rsi_14 < h.rsi_oversold` → cap at neutral; flip to bullish if `pct_change_5d < 0` (capitulation).
- **Magnitude** — `clamp(|pct_change_20d| × h.pct_change_momentum_scale, 0, h.magnitude_cap)`; boosted when `vol_ratio_20d > h.vol_ratio_breakout`, dampened when `vol_ratio_20d < h.vol_ratio_dry_up`.
- **Confidence** — starts at `h.confidence_base`; `+h.confidence_boost_step` when 5d and 20d momentum agree; `+h.confidence_boost_step` when within `h.near_52w_extreme_pct` of either 52w extreme; `-h.confidence_penalty_step` when `atr_pct_14 > h.atr_high_volatility_pct`. Clamped `[0, 1]`.
- **`key_factors`** — closed vocabulary:
  `{trend_up_20d, trend_down_20d, momentum_agree, momentum_disagree, rsi_overbought, rsi_oversold, near_52w_high, near_52w_low, vol_breakout, vol_dry_up, high_volatility}`.
- **`rationale`** — short template assembled from fired key_factors (e.g. `"trend_up_20d + vol_breakout, but rsi_overbought"`). ≤160 chars.

### `derive_social_verdict(features, h: SocialHeuristics) -> AnalystVerdict` (NEW)

- **`is_no_data`** when `features["mention_count_total"] == 0`. Returns neutral / 0 / 0 / `is_no_data=True`.
- **Lean** from `sign(aggregate_score)` — positive → bullish, negative → bearish, within `[-h.score_neutral_band, h.score_neutral_band]` → neutral.
- **Magnitude** — `clamp(|aggregate_score| × h.score_to_magnitude_scale, 0, h.magnitude_cap)`; boosted by `h.high_volume_magnitude_boost` when `mention_count_total > h.high_volume_mentions`.
- **Confidence** — starts at `h.confidence_base`; `+h.confidence_boost_step` when `mention_count_total >= h.confidence_volume_floor`; `-h.confidence_penalty_step` when `platform_score_disagreement > h.platform_disagreement_threshold` (Reddit and Twitter pulling opposite ways is a weaker signal than agreement). Clamped `[0, 1]`.
- **`key_factors`** — closed vocabulary:
  `{positive, negative, mixed, high_volume, low_volume, reddit_dominant, twitter_dominant, platforms_agree, platforms_disagree}`.
- **`rationale`** — short template, ≤160 chars.

### `derive_smart_money_verdict(features, h: SmartMoneyHeuristics) -> AnalystVerdict`

- **`is_no_data`** when `features["is_no_data"] == 1.0` (extractor flag). Returns neutral / 0 / 0 / `is_no_data=True`.
- **Lean** from `sign(net_flow_dollar)` across politician + 13F flows.
- **Magnitude** — `clamp(|net_flow_dollar| / (total_dollar_value_buys + total_dollar_value_sells + 1), 0, h.magnitude_cap)` — flow asymmetry, not absolute dollar size.
- **Confidence** — floor of `h.lone_filer_confidence_floor` when only one filer / one trade; ceiling of `h.consensus_confidence_ceiling` when `n_filers ≥ h.multi_filer_min_count` AND `(n_buys_30d + n_sells_30d) ≥ h.high_activity_trade_count`; linearly interpolated between.
- **`key_factors`** — closed vocabulary:
  `{net_buying, net_selling, multi_filer_consensus, lone_filer, high_volume_flow, mixed_activity}`.
- **`rationale`** — short template, ≤160 chars.

(Insider-derived tags from the previous design are gone — those concerns are now Fundamental's.)

---

## Narrowed LLM mandates

Both LLM analysts keep their `LlmAgent` shape, their fetch callback, their `make_evidence_callback` after-callback, and their `output_key` state-write convention. What changes is the prompt and the closed-vocabulary it enforces.

### FundamentalAnalyst — prose + insider supplement

Prompt rebuilt against two rules:

1. **The LLM reads prose** — MD&A excerpts, risk-factor excerpts, Form 4 footnotes — to extract findings that the extractor cannot.
2. **The LLM also sees structured insider numerics** (a small block, see *Insider expansion → Fundamental LLM prompt*) as quant context for the prose reasoning.

The model classifies, per ticker:

- `guidance`: one of `{raised, maintained, lowered, none}`.
- `going_concern`: boolean.
- `new_risks` / `removed_risks`: ≤3 each from the closed risk vocabulary.
- `mda_tone`: one of `{confident, cautious, defensive, mixed}`.
- `insider_signal`: one of the `insider_signals` vocabulary.

The model then derives an `AnalystVerdict` from these structured findings:

- **lean** — blended from `(guidance, mda_tone, insider_signal)`. Cluster buying + raised guidance + confident tone → strongly bullish. Discretionary-sale dominance + lowered guidance + cautious tone → strongly bearish. Conflicting inputs → neutral with low confidence.
- **magnitude** — severity (e.g. `going_concern=true` is high) blended with insider intensity.
- **confidence** — filing recency (`days_since_filed`) × insider activity intensity.
- **rationale** — ≤160 chars naming the dominant finding.
- **`key_factors`** — structured tags only, using the prefix scheme:
  - `guidance:<value>`.
  - `tone:<value>`.
  - `risk:<value>`, optionally suffixed with `_added | _removed | _intensified` when the comparison vs the prior filing in the dump warrants.
  - `insider:<value>` from the `insider_signals` vocabulary.
  - `going_concern:true` when flagged.
- **`is_no_data`** — true when no excerpts AND no insider activity present.

The full prompt template lives in `src/agents/analysts/fundamental/prompts.py`. Vocabulary placeholders (`{guidance_options}`, `{tone_options}`, `{risk_tags}`, `{insider_signals}`) are substituted at agent-construction time from `config/analyst_heuristics.json` so adding a tag is a config change, not a code change.

### NewsAnalyst — prose-only mandate (renamed from SentimentAnalyst)

Same shape and closed vocabulary as the original Sentiment LLM. Changes are surface-only:

- Provider input narrowed to `news/` only (social_sentiment migrates to the new Social analyst).
- `output_key` renamed `news_verdicts` (was `sentiment_verdicts`).
- State key `news_data` (was `sentiment_data`).
- Agent class `NewsAnalyst` (was `SentimentAnalyst`).
- Heuristics config block `news_vocabulary` (was `sentiment_vocabulary`).
- `AnalystName` literal value `"news"` (was `"sentiment"`).
- Pydantic model `NewsArticle` is unchanged.

The LLM reasons over headlines + article summaries only. Polarity statistics (already in the extractor's output) are removed from the prompt. Per ticker:

- `dominant_catalyst` from the catalyst vocabulary.
- `novelty` from the novelty vocabulary.
- `direction` from the direction vocabulary.
- `material`: boolean (would a long-only fund act on this?).

Derivation:

- **lean** — from `direction` (positive → bullish, negative → bearish, mixed/none → neutral).
- **magnitude** — from `novelty × material`.
- **confidence** — scales with headline count (low if `< 3` articles).
- **`key_factors`** — `[catalyst:<type>, novelty:<level>, direction:<value>, material:<bool>]`.
- **`is_no_data`** — true when no headlines in window.

---

## Contract invariants — what does NOT change

| Surface | Status |
|---|---|
| `AnalystEvidence` Pydantic schema | unchanged |
| `AnalystVerdict` Pydantic schema | unchanged |
| `TickerEvidence` / `AggregateVerdict` schema | unchanged |
| `build_ticker_evidence` / digest math | unchanged |
| State key `{analyst}_data` (fetch output) convention | unchanged; new analyst names follow the same pattern |
| State key `{analyst}_evidence` (analyst output) convention | unchanged |
| `EvidenceWriter` and `AnalystEvidenceRow` / `TickerEvidenceRow` ORM | unchanged |
| Strategist prompt, agent, schema | unchanged |
| `risk_gate`, `executor`, `memory_writer`, `snapshotter` | unchanged |
| `ParallelAgent` analyst pool composition | grows by one child (4 → 5); structure invariant |

**One unavoidable schema change.** `AnalystName` `Literal` expands from `"technical" | "fundamental" | "sentiment" | "smart_money"` to `"technical" | "fundamental" | "news" | "social" | "smart_money"`. Any pre-existing rows with `analyst="sentiment"` would need migration, but in practice none exist — the bot has never run end-to-end, so no `analyst_evidence` rows are in any database. Validated by a fresh-DB invariant test.

A pre-spec audit confirmed no downstream code parses the *content* of `rationale` or `key_factors` as free text:

- `evidence_view.py:57` treats them as opaque strings.
- `digest.py` reads only `lean`, `confidence`, `magnitude`, `is_no_data`.
- `EvidenceWriter` JSON-dumps the verdict.
- `memory/writer.py` reads aggregate fields only.

---

## Configuration

### `config/analyst_heuristics.json` (new file, covers all five analysts)

```json
{
  "technical": {
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "pct_change_momentum_scale": 4.0,
    "vol_ratio_breakout": 1.5,
    "vol_ratio_dry_up": 0.7,
    "atr_high_volatility_pct": 5.0,
    "near_52w_extreme_pct": 5.0,
    "confidence_base": 0.5,
    "confidence_boost_step": 0.2,
    "confidence_penalty_step": 0.3,
    "magnitude_cap": 1.0
  },

  "social": {
    "score_neutral_band": 0.05,
    "score_to_magnitude_scale": 2.0,
    "high_volume_mentions": 200,
    "high_volume_magnitude_boost": 0.15,
    "confidence_volume_floor": 30,
    "platform_disagreement_threshold": 0.3,
    "confidence_base": 0.4,
    "confidence_boost_step": 0.2,
    "confidence_penalty_step": 0.2,
    "magnitude_cap": 1.0
  },

  "smart_money": {
    "multi_filer_min_count": 3,
    "high_activity_trade_count": 5,
    "lone_filer_confidence_floor": 0.1,
    "consensus_confidence_ceiling": 0.9,
    "magnitude_cap": 1.0
  },

  "fundamental_vocabulary": {
    "guidance":  ["raised", "maintained", "lowered", "none"],
    "tone":      ["confident", "cautious", "defensive", "mixed"],
    "risks":     ["regulatory", "litigation", "cybersecurity", "supply_chain",
                  "macro", "competition", "key_person", "debt_refinance",
                  "going_concern", "guidance_change", "customer_concentration"],
    "insider_signals": ["cluster_buying", "cluster_selling",
                        "planned_sale_dominant", "discretionary_sale_dominant",
                        "option_exercise_dump", "option_exercise_hold",
                        "gift_disposal", "mixed"]
  },

  "news_vocabulary": {
    "catalysts": ["earnings", "guidance", "m_and_a", "regulatory",
                  "product_launch", "legal", "macro", "downgrade",
                  "upgrade", "none"],
    "novelty":   ["high", "medium", "low"],
    "direction": ["positive", "negative", "mixed", "none"]
  },

  "golden_set": {
    "min_direction_agreement_pct": 70
  }
}
```

The risk-tag suffix scheme (`_added | _removed | _intensified`) is documented in `config/README.md`; the JSON lists base tags only, the suffix combinations are implied by the prompt template.

### `src/agents/analysts/heuristics.py` (new module)

Frozen Pydantic models for each section — `TechnicalHeuristics`, `SocialHeuristics`, `SmartMoneyHeuristics`, `FundamentalVocabulary`, `NewsVocabulary`, `GoldenSetConfig` — plus the top-level `AnalystHeuristics`. Field validators enforce ranges (`rsi_overbought ∈ [50, 100]`, `confidence_base ∈ [0, 1]`, etc.). The loader function `load_heuristics()` reads the JSON, validates into `AnalystHeuristics`, and caches via `functools.lru_cache(maxsize=1)` — same pattern as `src/data/config.py:get_config()`.

### Injection

Each agent factory takes its config section at construction:

- `_build_technical_analyst(h: TechnicalHeuristics) -> BaseAgent`
- `_build_fundamental_analyst(vocab: FundamentalVocabulary) -> LlmAgent`
- `_build_news_analyst(vocab: NewsVocabulary) -> LlmAgent`
- `_build_social_analyst(h: SocialHeuristics) -> BaseAgent`
- `_build_smart_money_analyst(h: SmartMoneyHeuristics) -> BaseAgent`

`src/orchestrator/pipeline.py::build_pipeline()` calls `load_heuristics()` once and threads each section into the corresponding factory. The `ParallelAgent` composition is otherwise identical.

### Lifecycle integration

`src/orchestrator/lifecycle.py::initialise()` gains one new check:

```python
def _check_heuristics() -> None:
    """Fail-fast load of analyst heuristics. Surfaces JSON errors at boot."""
    load_heuristics()  # raises ValidationError if malformed
```

Matches the `_check_env` / `_check_broker_cash` pattern. Misconfiguration prevents startup rather than crashing on tick 1.

### Hot-reload — explicitly not supported

Heuristics load once at startup and cache. Changing a threshold requires a bot restart. Consistent with `data.json` treatment and avoids mid-tick consistency hazards across the parallel pool.

---

## Persistence — KB-readiness without migration

The `AnalystEvidenceRow` JSON columns (`features`, `verdict`, `feature_warnings`) accommodate the schema changes — extractor columns expand, vocabulary tags expand, no DB migration needed.

`AnalystName` literal expansion is the one surface to verify: the column is plain `String`, not constrained to an enum at the DB layer, so no migration. A unit test introspects the SQLAlchemy metadata and asserts the column type.

### One new index

A composite index on `AnalystEvidenceRow(analyst, ticker, recorded_at)` is added in the same PR. Justification: useful immediately for per-ticker history retrieval (replay, debug); essential when the future KB scans per-ticker history. Declared as a SQLAlchemy `Index(...)` on the model; picked up by `create_all()` automatically (project is not on Alembic yet).

### Closed-vocabulary discipline

- Every `key_factors` entry is a tag from the closed vocabulary.
- Bare tags for deterministic analysts (`trend_up_20d`, `positive`, `net_buying`).
- Prefixed tags for LLM analysts (`guidance:lowered`, `risk:cybersecurity_added`, `insider:cluster_buying`, `catalyst:earnings`).
- The suffix scheme `_added | _removed | _intensified` on risk tags encodes cross-filing comparison without a new column.
- `rationale` is templated (deterministic) or structured-summary (LLM) — never free-form.

### Example future-KB query (informational; not implemented)

```sql
-- "When did AAPL first show clustered insider buying?"
SELECT MIN(recorded_at)
FROM analyst_evidence
WHERE ticker = 'AAPL'
  AND analyst = 'fundamental'
  AND json_extract(verdict, '$.key_factors') LIKE '%insider:cluster_buying%';
```

The exact JSON-query syntax differs between SQLite (`json_extract`) and Postgres (`jsonb @>`), but the data shape supports both.

### What we explicitly do NOT add

- No embeddings column. Vector retrieval is a Phase-6 stack decision.
- No `prior_evidence_id` foreign key. Suffix tags capture the comparison.
- No new tables. `kb_entry`, `narrative_note`, `filing_excerpt_store` are out of scope.
- No retention / TTL fields.

---

## Surface tracing — first live-LLM validation

This is the milestone for the project's first real LLM run. T1 unit tests and T2 integration smoke tests validate structure; only a real-LLM, single-ticker trace validates that the new prompts and the new contract land what we think they do.

### Trace file shape

One JSON file per tick at `docs/surface-traces/<tick_id>-<ticker>.json` (gitignored), with ordered, labelled sections — one per pipeline boundary:

| Stage | Sections | Captured |
|---|---|---|
| 01 — Fetch | `01_fetch_{analyst}` (×5) | Raw `{analyst}_data` payload for the tested ticker |
| 02 — Deterministic verdicts | `02_{technical,social,smart_money}_verdict` | features + verdict for the tested ticker |
| 03 — LLM verdicts | `03_{fundamental,news}_llm_in`, `_llm_out`, `_verdict` | Rendered prompt, raw LLM response, parsed verdict |
| 04 — Digest | `04_digest` | `ticker_evidence` payload |
| 05 — Strategist | `05_strategist_llm_in`, `_llm_out`, `_decision` | Rendered strategist prompt, raw LLM response, parsed `StrategistDecision` |
| 06 — Risk gate | `06_risk_gate_in`, `_out` | Proposed weights, clamped weights, clamp records |
| 07 — Broker | `07_broker_calls` | List of broker method/args/result triples |

Every LLM call writes an in/out pair. Sections carry `state_keys` references where applicable so the file is grep-able when something goes wrong.

### Implementation — minimal-intrusion `TraceWriter`

A `TraceWriter` class at `src/observability/trace.py` (new module):

```python
class TraceWriter:
    """Append-only JSON snapshot collector for one tick.

    Production runs do not instantiate this; the `trace_tick.py` entrypoint
    sets `state["_trace"]` to an instance, and every callback opportunistically
    routes through `state.get("_trace")`. Production tick state has no
    `"_trace"` key, so the routing is a single dict lookup no-op.
    """
    def snapshot(self, label: str, payload: dict, *, state_keys: list[str] | None = None) -> None:
        """Append a labelled JSON section to the trace."""

    def llm_pair(self, label_base: str, prompt: str, response: str, *, model: str) -> None:
        """Append a paired LLM in/out section."""

    def finalise(self, out_path: Path) -> None:
        """Flush the trace to disk as one JSON document."""
```

Wiring is opt-in via a sentinel in state. Production runs leave `state["_trace"]` unset, and `_trace_maybe(state, ...)` no-ops on a single dict lookup. Touchpoints:

- Fetch callbacks: one snapshot at end (`01_fetch_<analyst>`).
- `run_deterministic_analyst` (the BaseAgent body): one snapshot at end (`02_<analyst>_verdict`).
- `make_evidence_callback`: no additional snapshot (the deterministic-verdict snapshot already covers it).
- LLM agents (fundamental, news, strategist): ADK `before_model_callback` / `after_model_callback` hooks attached only in trace mode, calling `trace.llm_pair(...)`.
- Digest builder: snapshot after `build_ticker_evidence` (`04_digest`).
- Risk gate, executor: before/after snapshots (`06_*`, `07_broker_calls`).

### Entrypoint

`scripts/trace_tick.py`, invoked as:

```bash
PYTHONPATH=src python -m scripts.trace_tick --ticker AAPL [--out docs/surface-traces/]
```

Behaviour:

1. Load heuristics, build the full production pipeline (real LLMs, paper broker — exactly production wiring).
2. Override the watchlist to `[--ticker]` only.
3. Attach a `TraceWriter` to initial state.
4. Run one tick via `orchestrator.tick.run_once`.
5. Write `docs/surface-traces/<tick_id>-<ticker>.json`.
6. On exception in any stage: flush the partial trace, exit non-zero.

`docs/surface-traces/` is added to `.gitignore`.

### What the baseline trace validates

- **Closed-vocabulary adherence** — did fundamental emit `risk:debt_refinance` or invent `risk:debt_problems`? Did news emit a catalyst tag outside the vocabulary? Grepable in the trace.
- **Insider supplement rendering** — does the Fundamental prompt's insider block render with correct numerics and footnote excerpts? Visible in `03_fundamental_llm_in`.
- **Prose-mandate scope** — does fundamental drift into reasoning about technical ratios? Visible in `03_fundamental_llm_out`.
- **Deterministic-baseline coverage** — are the deterministic Technical / Social / SmartMoney verdicts producing sensible lean / magnitude / confidence triples across realistic ticker conditions?
- **Strategist input shape** — does `05_strategist_llm_in` carry the new 5-analyst evidence structure correctly?
- **End-to-end correctness** — a real tick completes with real broker calls?

The PR's acceptance gate: T1 + T2 pass, plus at least one clean surface trace exists for at least one ticker, AND the strategist prompt block in step 5's trace shows the 5-analyst evidence shape rendering correctly.

---

## Test strategy

### Tier 1 — unit (no LLM, runs in CI)

| File | Coverage |
|---|---|
| `tests/unit/test_derive_technical_verdict.py` | Table-driven cases: empty data, overbought-flip, oversold-flip, momentum agree/disagree, vol breakout, near-52w-high, high-volatility penalty. ~15 cases. |
| `tests/unit/test_derive_social_verdict.py` (NEW) | Table-driven: no-data, positive cluster, negative cluster, mixed platforms, low-volume, high-volume, neutral-band. ~12 cases. |
| `tests/unit/test_derive_smart_money_verdict.py` | Table-driven: no-data, single filer, multi-filer consensus, lone filer high volume, mixed buys/sells. ~12 cases. Updated to remove insider fixtures. |
| `tests/unit/test_analyst_heuristics.py` | Schema validation: malformed JSON, out-of-range fields, missing sections, unknown keys all raise `ValidationError`. Includes new `social` block and renamed `news_vocabulary`. |
| `tests/unit/test_evidence_row_persistence.py` (extend) | Round-trip a verdict with `key_factors=["risk:cybersecurity_added", "insider:cluster_buying"]` and analyst names `"news"` / `"social"`. Confirms JSON serialisation preserves the prefix-colon-tag shape. |
| `tests/unit/test_evidence_index.py` (NEW) | Introspect SQLAlchemy metadata; assert the composite `(analyst, ticker, recorded_at)` index is declared. |
| `tests/unit/test_lifecycle_initialise.py` (extend) | Add a `_check_heuristics()` failure-path case. |
| `tests/unit/test_fundamental_prompt_render.py` | Vocabulary placeholders resolve correctly incl. `insider_signals`; rendered prompt does not contain unresolved `{}`-tokens; insider numeric block + footnote excerpts render. |
| `tests/unit/test_news_prompt_render.py` (renamed) | Same logic as old sentiment prompt-render test, paths updated. |
| `tests/unit/test_insider_model_roundtrip.py` (NEW) | `InsiderTrade` with new fields + `InsiderDerivativeTrade` round-trip cleanly through Pydantic; rejects unknown fields under `extra="forbid"`. |
| `tests/unit/test_form4_parser.py` (NEW) | Fixture-based: feed a synthetic Form 4 XML to `_parse_form4`; assert footnotes / codes / 10b5-1 flag / derivative rows are extracted. |
| `tests/unit/test_analyst_name_literal.py` (NEW) | Assert `AnalystName` includes `"news"` and `"social"` and excludes `"sentiment"`. |

Each `derive_*_verdict` test is parameterised on a fixture `*Heuristics` so threshold changes are testable without rebuilding the agent.

### Tier 2 — integration smoke

| File | Coverage |
|---|---|
| `tests/integration/test_analyst_pool_smoke.py` (NEW, `@pytest.mark.integration`) | Build the full `AnalystPool` (5 children) against canned fixtures, run a single tick with the deterministic analysts and LLM analysts mocked, assert all five state keys (`*_evidence`) populate correctly. |
| `tests/integration/test_pipeline_composition.py` (extend) | Confirm post-refactor pipeline wires end-to-end with 5 analyst children. |

### Tier 3 — live surface trace

| Artefact | Purpose |
|---|---|
| `scripts/trace_tick.py` | Entrypoint. Produces the trace. |
| `docs/surface-traces/<tick_id>-<ticker>.json` | One trace per acceptance run. Reviewed manually. |

### Golden-set sanity (T1, parameterised by config)

A test in `tests/unit/test_golden_set.py` runs the existing analyst fixtures through the new `derive_*_verdict` functions and asserts the output is in the same lean direction as the current LLM-emitted verdict ≥ `golden_set.min_direction_agreement_pct` of the time (default 70). Tunable via config. Sanity check that the rule-based verdict hasn't drifted into a different worldview, not a regression bar.

---

## Rollout — single PR, ordered commits

Each commit is independently green (CI passes); the whole PR lands together because the surface-trace validation depends on all of it.

1. **Config + heuristics models.** Add `config/analyst_heuristics.json` (all five sections); add `src/agents/analysts/heuristics.py` with frozen Pydantic models incl. `SocialHeuristics`, `NewsVocabulary`, `FundamentalVocabulary` (with `insider_signals`); update `config/README.md`; wire `_check_heuristics()` into `initialise()`. No agent changes yet. Tests: T1 schema validation + lifecycle init.

2. **`AnalystName` literal expansion.** Update `contract/evidence.py` to set `AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money"]`. Update any analyst-string switches/dispatches. Tests: T1 literal test + round-trip.

3. **Insider provider expansion.** Extend `InsiderTrade` model; add `InsiderDerivativeTrade` model; add `Form4Bundle` wrapper; update `edgar.py` to populate `footnote` / `transaction_code` / `is_10b5_1` / derivative tables. Tests: T1 model round-trip + Form 4 parser fixture test.

4. **Smart_money insider removal.** Drop insider from `smart_money_fetch_callback` and `extract_smart_money_features`. Update closed vocabulary to drop insider-specific tags. Tests: T1 derive + fetch unit tests updated.

5. **Fundamental insider addition.** Update `fundamental_fetch_callback` to pull filings + insider; update `extract_fundamental_features` to produce insider feature columns. Tests: T1 extractor unit tests.

6. **Sentiment → News rename.** Rename `src/agents/analysts/sentiment/` → `news/`, state keys, output_key, prompt module, extractor file. No logic change. Tests: T1 + T2 pass under the new name.

7. **Social analyst (new).** Add `src/agents/analysts/social/` (BaseAgent, fetch callback, extractor at `src/contract/extractors/social.py`, `derive_social_verdict` next to extractor). Wire into pipeline. Tests: T1 derive + render + T2 smoke.

8. **Deterministic technical analyst.** Add `derive_technical_verdict`; replace `LlmAgent` with `BaseAgent`. Delete old prompt module. Tests: T1.

9. **Deterministic smart_money analyst.** Same shape as step 8 for smart_money. Tests: T1.

10. **Narrowed fundamental LLM.** Rewrite `prompts.py` with vocabulary placeholders + insider supplement block. Inject `FundamentalVocabulary`. Tests: T1 prompt render + canned-LLM-output schema validation.

11. **Narrowed news LLM.** Inherit logic from old sentiment LLM. Inject `NewsVocabulary`. Tests: T1.

12. **Persistence index.** Composite index on `AnalystEvidenceRow(analyst, ticker, recorded_at)`. Tests: T1 metadata-introspection test.

13. **Surface tracing.** Add `src/observability/trace.py`; wire `_trace_maybe(...)` no-op hooks; add `scripts/trace_tick.py`; add `docs/surface-traces/` to `.gitignore`. No production-path behaviour change.

14. **Live validation.** Run `trace_tick.py --ticker AAPL` (or another agreed sample). File the resulting trace under `docs/surface-traces/`. Eyeball it. Iterate prompts/heuristics if needed. Acceptance gate.

Steps 1–13 are mechanical and CI-validated. Step 14 is manual and closes the PR.

---

## Things explicitly out of scope

- Strategist prompt or agent changes (Plan E handles strategist hardening).
- Sparse / triggered LLM execution.
- Any RAG, KB, or vector-store work.
- Schedule 13D / 13G activist-letter narrative analyst — backlog (B10).
- Market-regime analyst (VIX, put/call, AAII) — backlog (B15; no provider yet).
- Per-stock per-analyst prose reports (TradingAgents-style) — backlog (B14).
- Bull/Bear or risk-perspective debate layers — backlog (B12, B13).
- LLM augmentation of news or fundamental beyond the closed-vocab narrowed mandate — backlog (B16; justified by trace data).
- Embedding columns, prompt-versioning systems, LLM cost telemetry.
- Replay / backtest tooling for the deterministic analysts (trivial to write when actually needed).
- Automated regression on LLM output (no golden-LLM-response test — too brittle on a real model).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Closed-vocabulary prompt instruction is non-binding; LLM emits tags outside the vocabulary | T1 schema-validation tests catch invalid tags in canned outputs; the surface trace catches them in live runs. If the model proves unreliable, add an after-callback that filters or rejects out-of-vocab tags before evidence is built. |
| Deterministic verdict diverges in spirit from what the LLM was doing | Golden-set sanity test (70% direction agreement, configurable) catches gross drift. |
| Threshold tuning becomes a moving target | All thresholds are in one JSON file with documented ranges and required Pydantic validation. No hot-reload — changes are atomic across restarts. |
| Surface trace files leak sensitive provider data into the repo | `docs/surface-traces/` is in `.gitignore` from day one. The directory is a debug artefact, not committed material. |
| LLM ratchet policy becomes "stack more LLMs forever" | Every LLM addition requires (a) trace-data evidence of a baseline gap and (b) a backlog entry with explicit shelve-criteria. Drafted as backlog-tier discipline; ratchet is policy not vibes. |
| Insider footnote prose explodes prompt token cost | Footnote excerpts capped at 5 per ticker, ≤200 chars each. Token budget for fundamental analyst stays well within Gemini 2.5 Flash limits. |
| Risk-tag suffix scheme (`_added`, `_removed`, `_intensified`) becomes wrong when the prior filing is absent | Prompt instruction: emit suffixed tags only when prior filing exists in the dump; otherwise emit the bare tag. |
| `AnalystName` literal change breaks code reading old `"sentiment"` evidence rows | No such rows exist (bot has never run end-to-end); fresh-DB invariant test confirms. If a paper deployment were live, this would need a migration; pre-deployment, it does not. |

---

## Open follow-ups (not blocking this PR)

- LLM augmentation of deterministic analyst verdicts — only after trace data shows a baseline gap. Backlogged.
- Sparse-execution gate (only call fundamental LLM when a new filing has landed). Phase 6 work once a KB exists to query for filing recency.
- RAG / KB layer over `risk_factors_excerpt`, `mda_excerpt`, headlines, Form 4 footnotes, earnings call transcripts. Phase 6.
- LLM cost telemetry — easy add when it matters.
- Promote deterministic verdict heuristics to a learned model when enough `AnalystEvidence` history accumulates (Goal-3 substrate already in place).
- Schedule 13D / 13G narrative analyst — backlog (B10).
- Form-4 *deep* footnote analyst (a dedicated narrative LLM over insider footnotes alone, separate from Fundamental) — backlog (B10 covers; B16 gates) if trace data shows the footnote supplement is underweighted.
- Bull/Bear and risk-perspective debate layers — backlog (B12, B13).
- Per-stock prose reports — backlog (B14).
- Market-regime analyst (VIX, put/call, AAII) — backlog (B15) once a provider exists.
