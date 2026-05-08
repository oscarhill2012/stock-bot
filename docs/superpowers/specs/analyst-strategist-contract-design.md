# Analyst → Strategist Contract — Design

**Status:** Spec, awaiting plan.
**Roadmap context:** Goal 2 of three — see `docs/superpowers/backlog.md` § "Strategist Roadmap".
**Predecessor:** `docs/superpowers/specs/strategist-v2-design.md` (Goal 1, in flight). v2 deliberately left analyst inputs unchanged so they could be redesigned here.
**Successor:** Knowledge-base / self-improvement loop (Goal 3, B2). This spec builds the substrate that loop will read from.

---

## Problem

Today the strategist receives four heterogeneous lists of `AnalystSignal`-shaped objects (`technical_signals`, `fundamental_signals`, `sentiment_signals`, `smart_money_signals`). Three of those analysts emit only `direction + confidence + key_factors[]` — free-text bullets. The fourth (`SmartMoneySignal`) doesn't even subclass `AnalystSignal`, has no `confidence`, and is sparse-by-design. The strategist's prompt drops all four into a Jinja-style template and adds a verbal "trust SmartMoney 2-3×" hint.

This produces three concrete problems:

1. **The strategist re-derives meaning every tick from prose.** Any structured aggregation — weighted vote, disagreement detection, override rationale — has to be re-done by the LLM in-prompt, which is slow and inconsistent across runs.
2. **Weighting is unmeasurable.** Whatever `ANALYST_WEIGHTS` "means" lives in prose. We can't A/B it, can't tune it from data, and can't tell whether it's helping or hurting.
3. **There is no compact, KB-friendly artefact per (ticker, tick).** The substrate Goal 3 needs — "the last N times the *signal shape* looked like X, here's what happened" — doesn't exist as a single addressable object. The cost of *not* fixing this is real: every paper-trading week without a clean primitive is a week of lossy data the KB can't reason over.

## Goal

Make the surface between analysts and the strategist:

- **Compact** — one canonical object per ticker per tick, instead of four lists.
- **Informative** — both the deterministic numerics analysts compute *and* the LLM judgement on top of them are first-class fields.
- **KB-transferable** — the same object the strategist consumes is the object persisted; Goal 3 reads it directly without bespoke parsing.
- **Drift-resistant** — the numeric primitives don't depend on which Gemini version interpreted them.

After this work, plugging in a new analyst, reweighting an existing one, or shipping the KB read-path becomes a config or addition rather than a redesign.

## Non-goals (explicitly deferred)

- **Per-evidence-key weighting (B5).** The contract leaves a slot for nested `{analyst: {key: weight}}` weighting, but only per-analyst weights are wired in this spec.
- **KB read path / lookup primitive / outcome attribution joins.** The contract produces and persists the KB primitive; reading it, embedding it, joining it to `TradeLogRow` outcomes is all B2.
- **Discretisation / feature buckets** (e.g., `rsi_zone`). Numerics are stored richly; bucketing decisions belong with KB design once we have data.
- **Replay tooling** (B8). Phase 3 of the rollout would benefit from "rerun the digest from stored rows" but it's not in scope here.
- **Sub-tick exits, trailing stops, risk-clamp persistence, cost observability.** Independent backlog items.
- **Live-trading gate change.** This spec ships entirely under paper trading.

---

## Approach (synthesis of brainstorm decisions)

| Decision | Choice |
|---|---|
| Analyst role | **Hybrid.** Code computes a deterministic numeric feature vector; LLM is given features + raw data and emits direction/confidence + short rationale. Both fields are first-class on the contract. |
| Pre-digest stage | **Code-only digest.** A pure-Python step collapses the four per-analyst contributions into one `TickerEvidence` per ticker. No LLM, no I/O. |
| Weighting | **Equal weights default**, applied mathematically inside the digest. The verbal "trust SmartMoney 2-3×" hint is removed from the prompt. The slot supports learned weights later. |
| SmartMoney sparseness | **Unify under a common base + neutral-fill + `is_no_data` flag.** Tickers with no smart-money activity get a neutral verdict with the flag set; the digest's aggregation skips abstaining analysts. |
| Persistence scope | **Write-path only.** Both per-analyst and per-ticker rows are persisted from day one. Read path / KB lookup / replay deferred. |
| Module layout | **`src/contract/` is pure types + math.** `src/config/digest.py` owns tunable knobs. Per-analyst feature extractors live next to each analyst. |
| Indicators | **`pandas-ta`** for the technical indicator set. |
| Missing values | **Per-feature presence flag** on critical features that can be genuinely absent (e.g., `forward_pe`, `beta`, `debt_to_equity`). Other features zero-fill. |

---

## Architecture

The diagram below is the **end-state** (post-Phase 3). Phase 2 is a transitional shape where the LlmAgent's `output_schema` is unchanged and `pack_callback` translates legacy signals to `AnalystEvidence` — see the Rollout section.

```
analysts (4 parallel, unchanged shape)        digest (NEW, pure Python)         strategist (prompt updated)
──────────────────────────────────            ─────────────────────────         ──────────────────────────
fetch_callback (unchanged) ──┐
                             │                build_ticker_evidence:
features_callback (NEW)      ├─→  state["<analyst>_evidence"]  ─→   collapses 4 → 1 per ticker  ─→  consumes
extract_*_features per                                              applies weights, computes        list[TickerEvidence]
ticker → state                                                       disagreement, fills              one per watchlist
                             │                                       missing analysts                 ticker
LlmAgent runs prompt with    │
features + raw data,         │                writes:
emits AnalystVerdict per     │                  - AnalystEvidenceRow ×4×N
ticker                       │                  - TickerEvidenceRow ×N
                             │
pack_callback (NEW)          │
combines features + verdict  │
into AnalystEvidence ────────┘
```

Module layout:

```
src/
  contract/                    NEW — pure types + math
    __init__.py
    evidence.py                # AnalystVerdict, AnalystEvidence base + 4 subclasses
    ticker_evidence.py         # AggregateVerdict, TickerEvidence
    digest.py                  # build_ticker_evidence, _aggregate, _disagreement_score
  config/
    digest.py                  NEW — DIRECTION_DEAD_ZONE, DEFAULT_ANALYST_WEIGHTS
    watchlist.json             existing
    README.txt                 NEW — documents every config entry
  agents/
    analysts/
      _common.py               # AnalystSignal stays during migration window
      technical/
        features.py            NEW — extract_technical_features (pandas-ta)
        agent.py               # prompt + LlmAgent updated to emit AnalystVerdict
        prompts.py
        fetch.py               # unchanged
        schema.py              # imports AnalystVerdict / TechnicalEvidence from contract/
      fundamental/{features.py, ...}
      sentiment/{features.py, ...}
      smart_money/{features.py, ...}
  orchestrator/
    persistence.py             # adds AnalystEvidenceRow, TickerEvidenceRow
    pipeline.py                # inserts build_ticker_evidence step after analyst pool
```

---

## Contract types

### `src/contract/evidence.py`

```python
class AnalystVerdict(BaseModel):
    """The LLM-judgement half of an analyst's contribution to one ticker on one tick."""
    direction:  Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale:  str   = Field(max_length=160)        # ≤2 short sentences
    is_no_data: bool  = False                         # True = analyst couldn't run / no signal


class AnalystEvidence(BaseModel):
    """Base for one analyst's contribution to one ticker on one tick."""
    ticker:   str
    analyst:  Literal["technical", "fundamental", "sentiment", "smart_money"]
    features: dict[str, float]                        # deterministic numerics from features.py
    verdict:  AnalystVerdict


class TechnicalEvidence(AnalystEvidence):
    analyst: Literal["technical"] = "technical"
    # Documented features keys (lowercase snake_case, units encoded in name):
    #   rsi_14, mom_20d, dist_to_50dma_pct, dist_to_200dma_pct,
    #   vol_ratio_5d_vs_20d, atr_pct, beta, beta_present


class FundamentalEvidence(AnalystEvidence):
    analyst: Literal["fundamental"] = "fundamental"
    # trailing_pe, trailing_pe_present, forward_pe, forward_pe_present,
    # dividend_yield, market_cap_log,
    # rev_growth_yoy_pct, gross_margin_pct, debt_to_equity, debt_to_equity_present


class SentimentEvidence(AnalystEvidence):
    analyst: Literal["sentiment"] = "sentiment"
    # news_avg_sentiment, news_count_24h, social_score_delta,
    # social_aggregate_score, headline_severity_max
    top_headlines: list[str] = Field(default_factory=list, max_length=2)


class SmartMoneyEvidence(AnalystEvidence):
    analyst: Literal["smart_money"] = "smart_money"
    # insider_buy_dollars, insider_sell_dollars, n_insiders,
    # politician_buy_dollars, politician_sell_dollars, n_politicians,
    # sc13d_count, sc13g_count
    insiders:    list[str] = Field(default_factory=list)
    politicians: list[str] = Field(default_factory=list)
```

**Why `features: dict[str, float]` rather than typed attributes.** Keys are documented per subclass but not enforced as Pydantic fields. The KB will add and rename features over time; a JSON-shaped dict means evolving the feature set without schema migrations or breaking old persisted rows. Pydantic field enforcement here would buy little (we control all writers) and cost flexibility.

### `src/contract/ticker_evidence.py`

```python
class AggregateVerdict(BaseModel):
    direction:    Literal["bullish", "bearish", "neutral"]
    confidence:   float = Field(ge=0.0, le=1.0)
    weights_used: dict[str, float]                    # snapshot of weights at this tick


class TickerEvidence(BaseModel):
    """Canonical KB primitive. One per ticker per tick."""
    ticker:      str
    tick_id:     str
    recorded_at: datetime

    per_analyst: dict[str, AnalystEvidence]           # keyed by analyst name
    aggregate:   AggregateVerdict
    disagreement_score: float = Field(ge=0.0, le=1.0) # 0=unanimous, 1=max split
```

`weights_used` is snapshotted into every aggregate so each row is self-describing. The KB never has to ask "what weights were active at tick X."

---

## Digest

### `src/contract/digest.py`

Pure Python, no I/O, no LLM. Runs after the four analysts complete.

```python
ANALYST_NAMES = ("technical", "fundamental", "sentiment", "smart_money")

def build_ticker_evidence(
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    evidence_by_analyst: dict[str, AnalystEvidence],   # may be missing keys
    weights: dict[str, float] | None = None,
) -> TickerEvidence:
    weights = weights or DEFAULT_ANALYST_WEIGHTS
    per_analyst = _fill_missing(ticker, evidence_by_analyst)  # neutral + is_no_data=True
    aggregate   = _aggregate(per_analyst, weights)
    disagree    = _disagreement_score(per_analyst)
    return TickerEvidence(
        ticker=ticker, tick_id=tick_id, recorded_at=recorded_at,
        per_analyst=per_analyst, aggregate=aggregate,
        disagreement_score=disagree,
    )
```

**Aggregation math** — weighted sum of signed-confidence votes, abstaining analysts excluded:

```python
DIRECTION_VALUE = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}

def _aggregate(per_analyst, weights):
    total_weight = 0.0
    weighted_sum = 0.0
    confs = []
    for name in ANALYST_NAMES:
        ev = per_analyst[name]
        if ev.verdict.is_no_data:
            continue                                   # abstain, don't pull toward neutral
        w = weights.get(name, 1.0)
        weighted_sum += w * DIRECTION_VALUE[ev.verdict.direction] * ev.verdict.confidence
        total_weight += w
        confs.append(ev.verdict.confidence)
    if total_weight == 0.0:
        return AggregateVerdict(direction="neutral", confidence=0.0,
                                weights_used=dict(weights))
    score = weighted_sum / total_weight                # in [-1, +1]
    if   score >  DIRECTION_DEAD_ZONE: direction = "bullish"
    elif score < -DIRECTION_DEAD_ZONE: direction = "bearish"
    else:                              direction = "neutral"
    confidence = abs(score) * (sum(confs) / len(confs))
    return AggregateVerdict(direction=direction, confidence=confidence,
                            weights_used=dict(weights))
```

**Disagreement score** — variance of signed confidences across contributing analysts, clamped to `[0, 1]`. Maxes at 1.0 when half are at `+1.0` and half at `-1.0`.

**Three design points worth flagging:**

1. **Abstainers don't vote neutral.** A SmartMoney quiet tick contributes nothing to the aggregate, rather than dragging it toward neutral. This matches its sparse-by-design intent and avoids fictitious neutral pull.
2. **Dead zone on direction.** Without it, one mildly-bullish analyst can flip the call. The default lives in config.
3. **`weights_used` snapshotted.** Self-describing rows.

### `src/config/digest.py`

All tunable knobs:

```python
DIRECTION_DEAD_ZONE = 0.15

DEFAULT_ANALYST_WEIGHTS = {
    "technical":   1.0,
    "fundamental": 1.0,
    "sentiment":   1.0,
    "smart_money": 1.0,
}
```

`src/contract/digest.py` imports these. The contract module stays free of magic numbers — it's the math, the config has the values.

### `src/config/README.txt`

Documents every config entry. New `digest.py` block (and existing `watchlist.json` documented alongside):

```
src/config/digest.py
--------------------
Tunable knobs for the analyst→strategist digest step (src/contract/digest.py).
All values are hand-tuned defaults. The plan is for Goal 3 (knowledge base)
to learn data-driven replacements once paper trading has produced enough ticks.

DIRECTION_DEAD_ZONE  (float, typical range 0.0 - 0.30)
  When the weighted aggregate score for a ticker has |score| <= DEAD_ZONE,
  the aggregate direction is reported as "neutral" instead of bullish or
  bearish. Wider zone = fewer flips, more neutral calls. Narrower zone =
  more reactive but noisier. 0.0 disables the dead zone entirely.

DEFAULT_ANALYST_WEIGHTS  (dict[str, float])
  Per-analyst weight applied when aggregating signed-confidence votes into
  the headline aggregate. Keys must be the four analyst names. Equal weights
  (1.0 each) is the current default — we have no paper-trading data yet
  proving any other weighting helps. SmartMoney's quiet ticks abstain
  rather than vote, so its weight only matters on ticks where it actually
  produced a signal.
  Future: per-evidence-key weighting (backlog B5) will extend this to a
  nested {analyst: {feature_key: weight}} shape.
```

---

## Feature extractors

One per analyst, lives next to each analyst in `features.py`. Pure-Python, deterministic, no LLM, no network. Reads from session state populated by the existing `fetch.py` callbacks (no refetching, no extra cost).

```python
# src/agents/analysts/technical/features.py
def extract_technical_features(ticker: str, stats: StockStats) -> dict[str, float]:
    closes = [bar.close for bar in stats.history]
    last   = stats.last_price or closes[-1]
    return {
        "rsi_14":              _rsi_pandas_ta(closes, period=14),
        "mom_20d":             _percent_change(closes, lookback=20),
        "dist_to_50dma_pct":   _pct_dist(last, stats.fifty_day_average),
        "dist_to_200dma_pct":  _pct_dist(last, stats.two_hundred_day_average),
        "vol_ratio_5d_vs_20d": _vol_ratio(stats.history, short=5, long=20),
        "atr_pct":             _atr_pandas_ta(stats.history, period=14),
        "beta":                stats.beta or 0.0,
        "beta_present":        1.0 if stats.beta is not None else 0.0,
    }
```

The other three follow the same shape against their respective provider models (`Filing`, `NewsArticle[] + SocialSentiment`, insider/politician/13D-G feeds). Specific keys per analyst are documented in the type docstrings (see "Contract types").

**Per-analyst pipeline integration.** Each analyst's existing `fetch_callback` is unchanged. Two new callbacks are added per analyst:

- `features_callback` (before LLM) — runs `extract_*_features` for every ticker, stashes `state["<analyst>_features"]`.
- `pack_callback` (after LLM) — reads the existing `state["<analyst>_signals"]` plus `state["<analyst>_features"]` and packs them into a `<Analyst>Evidence` per ticker, stashes `state["<analyst>_evidence"]`.

In Phase 2 the LLM's `output_schema` is **unchanged** — the analyst still emits `list[<Analyst>Signal]` exactly as today. The `pack_callback` translates: `direction` and `confidence` lift directly off the existing signal; `rationale` is composed by joining `key_factors` (already capped at 3 × 80 chars). This keeps the legacy `*_signals` writers unbroken during the dual-write window.

In Phase 3 the LLM's `output_schema` changes to `list[AnalystVerdict]` (no more `key_factors`), the `pack_callback` simplifies, and the legacy state keys are retired.

The LLM prompt is updated in Phase 2 to receive both the raw data and the pre-computed numerics. The LLM never sees a `features` dict it might "improve" — features are computed and frozen *before* the LLM runs. This is what makes the contract drift-resistant: the numeric primitives don't depend on the model version.

---

## Strategist consumption

**State key changes.** During migration, both old and new shapes coexist. The LlmAgent's `output_key` keeps writing the four existing signal lists in Phase 2 (and stops in Phase 3 when output_schema changes to `AnalystVerdict`).

| State key | Phase 2 | Phase 3 |
|---|---|---|
| `technical_signals` … `smart_money_signals` (`list[<Analyst>Signal]`) | written by LlmAgent's `output_key`, unchanged shape | retired |
| `<analyst>_features` (`dict[ticker, dict[str, float]]`) | written by `features_callback` | unchanged |
| `<analyst>_evidence` (`list[<Analyst>Evidence]`) | written by `pack_callback` (translates from legacy signal in Phase 2) | written by `pack_callback` (direct from `AnalystVerdict`) |
| **`ticker_evidence`** (`list[TickerEvidence]`) | written by digest step | unchanged |

Strategist reads only `ticker_evidence` from Phase 2 onward — never touches the legacy `*_signals`.

**Prompt change.** The four `{*_signals}` blocks plus the SmartMoney bias paragraph are replaced by:

```
## Per-Ticker Evidence
For each watchlist ticker, you receive a TickerEvidence with:
- aggregate.direction + aggregate.confidence (weighted across analysts)
- disagreement_score (0 = unanimous, 1 = split)
- per_analyst breakdown: each analyst's direction, confidence, and 1-2 sentence rationale
- per_analyst.*.features: deterministic numerics (RSI, P/E, sentiment delta, insider $, etc.)
- per_analyst.smart_money.verdict.is_no_data: True means smart_money was quiet for this ticker

How to read this:
- The aggregate is a starting point, not a verdict. You can override it.
- High disagreement (>0.5) means analysts split — read the per-analyst rationales
  before deciding. A confident aggregate with high disagreement usually means
  one strong analyst overrode the others; that's worth scrutinising.
- Smart-money is_no_data=True means "quiet," not "bearish." Don't treat absence as a vote.

Evidence: {ticker_evidence}
```

The `StrategistDecision` output schema and the lifecycle validator are unchanged from v2. This contract changes only the strategist's *input*.

**Token-count effect.** Prompt is shorter, not longer. Today: four lists of objects, each with prose `key_factors`. Tomorrow: one list, structured features (compact JSON), rationales capped at 160 chars.

---

## Persistence

Two new tables added to `src/orchestrator/persistence.py`. Existing `AttributionSignalsRow` keeps working through the migration; the new shape is additive.

### `AnalystEvidenceRow`

One row per (analyst, ticker, tick). Replaces `AttributionSignalsRow` long-term.

```python
class AnalystEvidenceRow(Base):
    __tablename__ = "analyst_evidence"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id:     Mapped[str]      = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst:     Mapped[str]      = mapped_column(String, index=True)   # technical|fundamental|sentiment|smart_money
    ticker:      Mapped[str]      = mapped_column(String, index=True)

    direction:  Mapped[str]   = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    rationale:  Mapped[str]   = mapped_column(String, default="")
    is_no_data: Mapped[bool]  = mapped_column(Boolean, default=False)

    features_json: Mapped[str]        = mapped_column(String, default="{}")
    extras_json:   Mapped[str | None] = mapped_column(String, nullable=True)
```

### `TickerEvidenceRow`

One row per (ticker, tick). The KB lookup primitive.

```python
class TickerEvidenceRow(Base):
    __tablename__ = "ticker_evidence"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id:     Mapped[str]      = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    ticker:      Mapped[str]      = mapped_column(String, index=True)

    aggregate_direction:  Mapped[str]   = mapped_column(String)
    aggregate_confidence: Mapped[float] = mapped_column(Float)
    disagreement_score:   Mapped[float] = mapped_column(Float)

    weights_used_json: Mapped[str] = mapped_column(String, default="{}")

    __table_args__ = (Index("ix_ticker_evidence_ticker_tick", "ticker", "tick_id"),)
```

**Why JSON columns for `features_json`, `extras_json`, `weights_used_json`.** Feature sets and weight shapes will evolve (B5 adds per-key weights; Goal 3 will add or rename features). JSON columns mean evolving without schema migrations on a growing table. We don't need to query *inside* the JSON in B1 — KB lookups (B2) load rows in Python or use a dedicated store. SQLite JSON1 / Postgres JSONB are both available later if we need them.

**No FKs to `tick_id`.** Other StockBot tables (`TradeLogRow`, `TickerStanceRow`, `PortfolioSnapshotRow`) use `tick_id` as a string identifier without a backing tick table. We follow the convention.

**No outcome-attribution joins yet.** `TickerEvidenceRow` does *not* link to `TradeLogRow.opening_tick_id` here. That join is exactly what B2 needs to design properly; adding it ad-hoc now would lock in a shape Goal 3 might want different.

**No backfill of historical data.** Existing `AttributionSignalsRow` rows aren't migrated. They're a different shape and B2 will decide whether they're useful (probably skipped — paper-trading volume since v2 is small).

---

## Testing

**Layer 1 — Feature extractors (unit, fully deterministic).**

```
tests/contract/test_features_technical.py
tests/contract/test_features_fundamental.py
tests/contract/test_features_sentiment.py
tests/contract/test_features_smart_money.py
```

Each test takes a frozen JSON fixture of provider output (`StockStats`, `Filing[]`, `NewsArticle[]`, etc.) and asserts the exact dict from `extract_*_features`. Fixtures live under `tests/fixtures/contract/`. Zero LLM calls, zero network, run on every commit. These tests make the "near-deterministic" claim true.

**Layer 2 — Digest (unit, fully deterministic).**

`tests/contract/test_digest.py` covers:
- All four bullish → aggregate bullish, disagreement near 0.
- 2-bullish 2-bearish at equal confidence → neutral via dead zone, disagreement high.
- Three abstain (`is_no_data`), one bullish → aggregate matches the lone voter.
- All four abstain → degenerate neutral, conf 0.
- Weights table snapshotted into output verbatim.
- Dead-zone boundary cases (`score = ±0.15` exactly).

No analyst LLMs, no provider fixtures — tests construct `AnalystEvidence` objects directly.

**Layer 3 — End-to-end smoke (integration, runs on demand).**

Extend `scripts/smoke_run`: one tick on FakeBroker with real LLMs, asserts `state["ticker_evidence"]` is populated for every watchlist ticker and `target_weights` covers them. No assertion on direction (LLM-driven, unstable) — only shape and exhaustiveness.

---

## Rollout

Three phases, each a separate PR. Rolling all of this in one PR is high-risk on an autonomously-trading bot.

**Phase 1 — Contract types + extractors + digest (no wiring).**
- Add `src/contract/` module (types + digest math).
- Add `src/config/digest.py` + `src/config/README.txt`.
- Add `features.py` to each of the four analyst modules.
- Layer 1 + Layer 2 tests pass. Nothing reads from the new modules yet — purely additive code, lowest risk.

**Phase 2 — Wire into pipeline + persistence.**
- Wire `features_callback` and `pack_callback` into each of the four analyst agents. `output_schema` of each LlmAgent stays unchanged in this phase; `pack_callback` translates the existing `<Analyst>Signal` shape into `<Analyst>Evidence` using `key_factors` for `rationale`.
- Add `build_ticker_evidence` step to the orchestrator after the analyst pool.
- Add `AnalystEvidenceRow`, `TickerEvidenceRow`, and their writer.
- Update each analyst's prompt to include `state["<analyst>_features"]` as read-only context.
- Update strategist prompt to consume `state["ticker_evidence"]`.
- Old `*_signals` state keys keep being written by the LlmAgents (unchanged output_schema); old `AttributionSignalsRow` keeps being persisted. New `<analyst>_evidence`, `ticker_evidence` exist alongside.
- Smoke run + 1-week paper trading at this state.

**Phase 3 — Retire legacy (separate PR after a paper-trading week).**
- Change each LlmAgent's `output_schema` to `list[AnalystVerdict]`. `pack_callback` simplifies (no more translation from `key_factors`).
- Drop the four `*_signals` state keys.
- Drop `AttributionSignalsRow` writes (table stays for historical data).
- Remove `key_factors` and the legacy `<Analyst>Signal` classes from `agents/analysts/_common.py` and per-analyst `schema.py`.
- Lands once Phase 2 has produced a clean week of dual-write data and we've sanity-checked that `TickerEvidenceRow` matches what we'd want.

---

## Effect on other systems

- **`risk_gate`, `executor`, `memory`, `snapshot`** — unchanged. They consume strategist output, which is unchanged.
- **`StrategistDecision` schema** — unchanged. Lifecycle validator unchanged.
- **`docs/data-and-providers.md`** — unchanged. The data contract any provider must satisfy is unchanged; this spec restructures what we *do* with that data after fetching.
- **Cost** — neutral or slightly down. Same number of LLM calls, prompts shorter on average.
- **Live-trading gate** — unchanged. This spec ships under paper trading.
