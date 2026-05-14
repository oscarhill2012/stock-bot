# Analyst Surface Redesign — Design

## Origin and framing

This spec consolidates three concerns surfaced while reading the AAPL surface trace at `docs/surface-traces/trace-20260513T165408-9adf5766-AAPL.json`:

1. **Input shape is wasteful.** The Fundamental fetch drags 252 rows of OHLC history through `state["fundamental_data"]` because `get_stock_stats()` returns a bundle containing both fundamentals *and* price history. Fundamental never uses the history; it sits as inert payload.
2. **LLM analyst output is too compressed.** News and Fundamental analysts ingest substantial unstructured input (articles, MD&A excerpts, insider footnotes) and emit a verdict whose richest field is a ≤160-char rationale and ≤8 closed-vocabulary tags. The Strategist sees only that verdict — not the LLM's underlying reasoning. ~95% of the analyst's cognition is thrown away after one sentence.
3. **Repeat work is unbounded.** Today every tick re-prompts the News and Fundamental LLMs even when the underlying articles / filings have not changed. With higher tick cadence (≥4/day) this becomes the dominant cost.

This spec **implements [[B9]] (sparse-execution gate) and [[B14]] (per-stock per-analyst prose reports)** from `docs/superpowers/backlog.md`, plus the small data-model split (1) and the trace-fidelity fix surfaced during this brainstorm. When this spec ships, B9 and B14 are retired from the backlog. A new entry for memory + KB + RAG continuity is *not* added here — that work lives in [[B11]] and the spec leaves room for it.

The spec is deliberately *additive*. Existing closed-vocabulary verdicts stay; the new `report` field is layered on top. The cache sits in front of the existing LLM call. The data-model split is a typed renaming, not a behaviour change. No piece of this changes the Strategist's contract beyond presenting it with strictly more information.

## Goals (in priority order)

1. **Stop wasting input.** Split `StockStats` so Fundamental never carries OHLC history it doesn't use.
2. **Recover analyst cognition.** Add a structured prose `report` field to the LLM analysts (News + Fundamental) so the Strategist sees the analyst's actual reasoning, not just its verdict tags.
3. **Cap LLM cost across ticks.** Hash-cache the LLM analyst's verdict + report on its input set; reuse byte-identical hits without re-prompting.
4. **Fix the trace fidelity bug** so this work — and all future surface-trace debugging — actually shows what the LLM saw.
5. **Reduce tick cadence** from current frequency to 2 ticks/day (with headroom to scale up to 3 intraday once the cache and richer reports prove themselves on paper data).

## Non-goals

- **No analyst memory, KB, or RAG.** Cross-tick continuity, "what changed since last tick" awareness, prior-report-as-context — all deferred to [[B11]] (RAG substrate) and the future analyst-memory brainstorm.
- **No new LLM hops.** Technical, SmartMoney, Social stay deterministic. The closed-vocabulary verdict semantics for News and Fundamental are unchanged. This spec does not need to clear the [[B16]] ratchet because no new LLM call is introduced.
- **No deterministic-analyst confidence recalibration.** The trace shows Technical firing `bearish, conf=0.90` mechanically because all five rules fired, not because the regime warrants 90% confidence. Calibration is a real concern but separable — captured as a new backlog entry.
- **No Strategist rewrite.** Strategist prompt layout changes only to consume the richer per-analyst surface. No change to its decision rules or output schema.
- **No retroactive backfill.** First tick after deploy runs as a cache miss for every analyst-ticker pair. No need to "warm" the cache from history.

---

## 1. Data model split (Goal 1)

### Current state

`src/data/models/stock_stats.py` defines a single `StockStats` model whose shape is:

```
StockStats {
  ticker:                  str
  history:                 list[OHLCVBar]   # 252 daily rows
  market_cap:              float | None
  trailing_pe:             float | None
  forward_pe:              float | None
  beta:                    float | None
  dividend_yield:          float | None
  fifty_day_average:       float | None
  two_hundred_day_average: float | None
  last_price:              float | None
  sector:                  str | None
  long_name:               str | None
}
```

`get_stock_stats(ticker)` returns this bundle. Both `technical_fetch_callback` and `fundamental_fetch_callback` call it; only Technical uses `history`.

### Target shape

Split into two purpose-scoped models, exposed through two provider functions sharing the underlying API call so we don't double the data-source cost.

**`src/data/models/price_history.py` (new):**

```python
class PriceHistory(BaseModel):
    """OHLCV bars for a ticker, ordered oldest -> newest."""
    ticker: str
    bars:   list[OHLCVBar]
```

**`src/data/models/company_ratios.py` (new):**

```python
class CompanyRatios(BaseModel):
    """Scalar company-level fundamentals + summary stats."""
    ticker:                  str
    long_name:               str | None
    sector:                  str | None
    market_cap:              float | None
    trailing_pe:             float | None
    forward_pe:              float | None
    beta:                    float | None
    dividend_yield:          float | None
    fifty_day_average:       float | None
    two_hundred_day_average: float | None
    last_price:              float | None
```

The 50-day and 200-day moving averages live in `CompanyRatios` (not `PriceHistory`) because the provider serves them as scalars; they are summary statistics, not bars.

**Provider layer** (likely `src/data/providers/yfinance.py` or equivalent) adds:

```python
async def get_price_history(ticker: str) -> PriceHistory: ...
async def get_company_ratios(ticker: str) -> CompanyRatios: ...
```

Both functions may share a single underlying yfinance call (cached for the duration of one tick) and project the response into their respective typed models. No double API cost.

**`StockStats` is retired.** Every callsite (Technical fetch, Fundamental fetch, tests, anywhere else) is updated to request whichever model it actually needs.

### Consumer changes

- `technical_fetch_callback` becomes: `price_history = await get_price_history(ticker); ratios = await get_company_ratios(ticker)`. Stores both under `state["technical_data"][ticker] = {"price_history": ..., "ratios": ...}` (or two top-level keys — implementation plan decides naming).
- `fundamental_fetch_callback` calls **only** `get_company_ratios(ticker)`. The 252-row history is no longer in `fundamental_data`.

### Feature extractor changes

`src/contract/extractors/technical.py` reads from `price_history.bars` instead of `stats.history`. `src/contract/extractors/fundamental.py` reads from `company_ratios.trailing_pe` instead of `stats.trailing_pe`. Names of feature outputs (`pe_trailing`, `pct_change_5d`, etc.) stay identical so downstream (digest, strategist) is unaffected.

### Forward compatibility

The split establishes a pattern for future expansion: each analyst composes its inputs from a catalogue of small, single-purpose data models. When new providers come online (transcripts, options chain, intraday quotes), they get their own models and join the catalogue rather than bolting onto an existing kitchen-sink model.

---

## 2. Analyst report schema (Goal 2)

### Asymmetric design

| Analyst       | Verdict (existing)    | `report` field (new)                          | Strategist prompt surface                           |
|---------------|-----------------------|-----------------------------------------------|-----------------------------------------------------|
| Technical     | deterministic         | none                                          | verdict + features as labelled bullets              |
| SmartMoney    | deterministic         | none                                          | verdict + features as labelled bullets              |
| Social        | deterministic         | none                                          | verdict + features as labelled bullets              |
| News          | LLM (closed-vocab)    | `AnalystReport` (summary + drivers)           | verdict + report                                    |
| Fundamental   | LLM (closed-vocab)    | `AnalystReport` (summary + drivers)           | verdict + report + features as labelled bullets     |

**Why asymmetric:** LLM is valuable where it converts unstructured -> structured. News and Fundamental ingest prose (articles, MD&A) and need an LLM to compress it. Technical's features are already structured (`rsi_14`, `dist_from_high_52w_pct`) — a narrator over numbers adds a translation layer that can hallucinate without adding insight. The deterministic verdict, paired with features surfaced as human-readable bullets, gives the Strategist the same visual weight as a narrative report without paying for a fake narrator. See discussion in `docs/Phase5-analyst-refine/spec.md` (minimum-LLM baseline policy) and [[B16]].

### `AnalystReport` schema

New pydantic model in `src/contract/evidence.py` alongside `AnalystVerdict`:

```python
class ReportDriver(BaseModel):
    """One driver of the analyst's lean — a labelled, weighted reason."""
    name:      str                                   # short label, e.g. "EU App Store ruling"
    direction: Literal["bull", "bear", "neutral"]
    weight:    float                                 # [0, 1] — relative importance vs other drivers
    body:      str                                   # 2-3 sentences of reasoning

class AnalystReport(BaseModel):
    """LLM analyst's qualitative reasoning, paired with the verdict."""
    summary: str                                     # 3-5 sentences of connective tissue
    drivers: list[ReportDriver]                      # 2-4 entries (LLM-enforced via prompt)
```

`AnalystVerdict` gains an optional field:

```python
class AnalystVerdict(BaseModel):
    ...existing fields unchanged...
    report: AnalystReport | None = None              # populated only by LLM analysts
```

### Relationship between `key_factors` and `drivers`

Both are emitted — neither is derived from the other. They serve different purposes:

- `key_factors`: ≤8 closed-vocabulary tags. Machine-aggregatable across the corpus ("how often did `catalyst:legal` appear over the last 100 ticks?"). Tight type-1 audit trail.
- `drivers`: 2-4 named drivers with weights and prose bodies. Strategist-readable, less aggregatable, richer.

The LLM prompt asks for both. We do *not* enforce structural overlap (e.g. "every driver must map to a key_factor"); the LLM is free to emit a closed-vocab tag for which no driver exists if a peripheral signal warrants the tag but not a driver entry.

### Prompt changes

`src/agents/analysts/news/prompts.py` and `src/agents/analysts/fundamental/prompts.py` are extended to instruct the LLM to emit the `report` field alongside the existing verdict. The closed-vocabulary tag instructions stay; new instructions are appended for the `report` shape.

Indicative addition (newsprompt; fundamental gets a mirrored block):

```
Additionally, emit a 'report' object alongside your verdict, with:

  summary  3-5 sentences of connective tissue covering the gestalt
           this tick — not a list. Argue your lean.

  drivers  2-4 entries. Each driver:
    name       short label (4-6 words)
    direction  bull | bear | neutral
    weight     [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised
    body       2-3 sentences explaining the driver. Do NOT cite source
               URLs; synthesise.

The report is your reasoning; the verdict is your conclusion. They must
be consistent — the lean and direction-weighted driver mix should agree.
```

### Caps revised

With 2 ticks/day and the report cache, we widen the truncation caps so the LLM has more raw material to work with:

| Cap                             | Before | After  |
|---------------------------------|--------|--------|
| News: articles per ticker       | 10     | 20     |
| News: summary chars per article | 300    | 500    |
| Fundamental: MD&A chars         | 500    | 1500   |
| Fundamental: risk-factor chars  | 500    | 1500   |
| Fundamental: insider footnotes  | 5      | 5      |
| Fundamental: footnote chars     | 200    | 400    |

Constants in `news/fetch.py` (`_MAX_HEADLINES`, `_MAX_SUMMARY_CHARS`) and `fundamental/fetch.py` (filing-excerpt slicing, `_MAX_FOOTNOTES`, `_MAX_FOOTNOTE_CHARS`) move into `config/analysts.json` (new file — see § Configuration).

---

## 3. Strategist prompt restructure (Goal 2 cont.)

### Current state

The Strategist receives per-analyst verdict JSON only:

```
[NewsAnalyst]        said: {verdicts JSON ~120 tokens}
[FundamentalAnalyst] said: {verdicts JSON ~120 tokens}
```

The deterministic analysts' verdicts come in through the digest, not the prompt. The Strategist has no visibility into the underlying features.

### Target state

The Strategist prompt grows a per-ticker block that surfaces all five analysts at equal visual weight. Numeric analysts get their features as labelled bullets; LLM analysts get verdict + report.

```
=== AAPL ===

[Technical]  lean: bearish  magnitude: 0.49  confidence: 0.90
  RSI(14):                  76.0   (overbought)
  20d momentum:             +12.3%
  5d momentum:              +4.1%
  Distance from 52w high:   0.0%   (at high)
  Distance from 52w low:    +84.2%
  Volume vs 20d avg:        1.10x
  ATR%(14):                 2.07
  -> Rationale tags: trend_up_20d, rsi_overbought, near_52w_high

[Fundamental]  lean: bearish  magnitude: 0.6  confidence: 0.7
  P/E (trailing/forward):   36.2 / 31.3
  Profit margin:            (no data)
  Insider net 30d:          -$72.0M  (4 sells, cluster_sell flag set)
  Top filer role rank:      4  (CFO/SVP)
  Days since last filing:   12.7
  -> Closed-vocab tags: insider:discretionary_sale_dominant, risk:guidance_change
  -> Report summary:
     "Discretionary insider selling dominates a quiet filing tick.
      The ~$71M block from the CFO + cluster of senior officer sales
      lands without a 10b5-1 plan to discount their weight..."
  -> Drivers:
       * Discretionary sale dominance  (bear, w=0.55):
         <body>
       * Filings tone steady           (neutral, w=0.20):
         <body>

[News]  lean: neutral  magnitude: 0.3  confidence: 0.7
  Article count 7d:         50
  -> Closed-vocab tags: catalyst:legal, catalyst:regulatory, novelty:low, direction:mixed
  -> Report summary: "Two converging negatives this tick..."
  -> Drivers:
       * EU App Store ruling     (bear, w=0.5): <body>
       * Gemini-on-Android push  (bear, w=0.3): <body>

[SmartMoney]  is_no_data: true
[Social]      is_no_data: true
```

The Strategist's existing instructions get one short addition:

> Where an analyst's report contradicts its lean, the lean is the analyst's final call — treat the report as their reasoning, not their conclusion. You may still override an analyst, but you must write down which signal you chose to overweight and why.

### Implementation surface

A new renderer in `src/contract/digest.py` (or a sibling `src/contract/strategist_prompt.py`) takes the `TickerEvidence` for a ticker and produces the per-ticker block above. The Strategist's prompt template invokes this renderer (or pre-builds the text and injects via state placeholder, depending on the existing strategist prompt pattern — implementation plan resolves).

Deterministic analyst feature-labelling lives in a small module mapping `{analyst, feature_key} -> {label, formatter, optional inline_interpretation}`. Adding a new feature is a one-line entry. Example for Technical:

```python
TECHNICAL_BULLETS = [
    ("rsi_14",                 "RSI(14):",                lambda v: f"{v:.1f}",  rsi_band),
    ("pct_change_20d",         "20d momentum:",           pct_signed,            None),
    ("dist_from_high_52w_pct", "Distance from 52w high:", pct_signed,            position_band),
    ...
]
```

This keeps "what the strategist sees" auditable and version-controlled, rather than hidden in formatting strings scattered through the codebase.

---

## 4. Report cache (Goal 3)

### What the cache memoises

For each `(analyst, ticker)` pair where `analyst` is an LLM analyst (News or Fundamental), the cache stores the function:

```
f(input_set) -> (verdict, report)
```

where `input_set` is the analyst's view of the world for this ticker (the article list for News; the filing + insider + ratio bundle for Fundamental). The cache key is a stable hash of `input_set` plus a prompt-version fingerprint (see below).

### Hash inputs

**News:**

```python
def news_hash_inputs(articles: list[NewsArticle]) -> bytes:
    # Sort by URL then published_at for stable order.
    items = sorted((a.url, a.published_at.isoformat()) for a in articles)
    return blake2b(json.dumps(items, sort_keys=True).encode()).digest()
```

**Fundamental:**

```python
def fundamental_hash_inputs(
    ratios:  CompanyRatios,
    filings: list[Filing],
    insider: Form4Bundle,
) -> bytes:
    payload = {
        # Ratios rounded so meaningless float jitter doesn't bust cache.
        "ratios": {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in ratios.model_dump().items()
        },
        "filings": sorted(f.accession_no for f in filings),
        "insider_trades": sorted(
            (t.insider_name, t.transaction_date.isoformat(), t.shares, round(t.price_per_share, 2))
            for t in insider.trades
        ),
        "insider_derivatives": sorted(
            (d.insider_name, d.transaction_date.isoformat(), d.transaction_code)
            for d in insider.derivatives
        ),
    }
    return blake2b(json.dumps(payload, sort_keys=True).encode()).digest()
```

### Prompt-version fingerprint

The cache key is `(input_hash, prompt_version)`. `prompt_version` is a short string baked into the analyst module (e.g. `NEWS_PROMPT_VERSION = "2026-05-14-a"`). Editing the prompt template (or the closed vocabulary) requires bumping the version, which invalidates all cached entries on next deploy. This prevents stale cache hits when the prompt semantics change.

### Storage

**Decision:** disk JSON files at `cache/reports/<analyst>/<ticker>.json`. `cache/` is gitignored.

Each file contains a single-entry-at-a-time record (no history; the next miss overwrites):

```json
{
  "input_hash": "blake2b:abc123...",
  "prompt_version": "2026-05-14-a",
  "verdict": { /* AnalystVerdict.model_dump() */ },
  "report":  { /* AnalystReport.model_dump()  */ },
  "stored_at": "2026-05-13T16:54:10Z"
}
```

**Why disk JSON, not a SQLAlchemy row:**

- Cache is *operational state*, not telemetry. SQLAlchemy persistence (`orchestrator/persistence.py`) stores facts about what the bot did; cache stores derived-but-rederivable LLM outputs. Keeping them separate avoids polluting the schema with throwaway data.
- Single-process bot, no concurrency. A file write is atomic via `os.replace`.
- Disk cache is trivial to inspect, blow away, or selectively invalidate during development.
- If the bot ever runs distributed (e.g. multiple processes per region), this knob revisits — but [[B11]] / [[B2]] would force that conversation first.

**Open knob deferred to the implementation plan:** if the implementation plan finds that operating on JSON files creates messy code paths around partial writes or test isolation, it may swap to a small `LlmVerdictCacheRow` in SQLAlchemy. Either choice is consistent with this spec; only the storage backend changes.

### Flow

```
fetch (articles | filings/insider/ratios)
   |
   v
compute input_hash + lookup current prompt_version
   |
   v
read cache/reports/<analyst>/<ticker>.json (if exists)
   |
   +-- hit (input_hash == cached AND prompt_version == cached):
   |     skip LLM call.
   |     load (verdict, report) from cache into state.
   |     emit trace marker "cache_hit".
   |
   +-- miss (no file OR input_hash differs OR prompt_version differs):
         call LLM with full prompt.
         parse (verdict, report).
         atomically write new cache entry.
         emit trace marker "cache_miss".
```

### Always-recompute path

Even on cache hit, the **deterministic feature extractor still runs every tick**. Features feed the digest aggregate; their cost is negligible (numpy / pandas math on at most 252 rows). Only the LLM call is gated.

### Eviction

No TTL in v1. The cache is keyed on the input itself, so when the input meaningfully changes the cache busts on its own. Stale entries simply persist on disk until overwritten or manually deleted.

**Force-refresh ceiling — explicit non-feature in v1.** B9's brainstorm raised "do we want a force-refresh-every-N-ticks ceiling so we never sit on a stale verdict for unbounded time?" The answer for v1 is no, *because the hash is sensitive enough to bust naturally*. News article URL-sets change whenever any article rolls in or out of the 7-day window; filings change whenever a new 10-K / 10-Q / 8-K lands; insider records change on any new Form 4. The only scenario where the cache persists unbounded is a ticker for which nothing material has moved — which is exactly the case we *want* to skip the LLM on.

### Watchlist changes

If a ticker leaves the watchlist, its cache files sit on disk unused — harmless. If a ticker joins, it cache-misses on its first tick (already covered by the "fresh deploy" non-feature).

### Concurrency

The orchestrator runs analysts in parallel (`ParallelAgent`), but each analyst processes its tickers sequentially within its callback. So `cache/reports/news/AAPL.json` and `cache/reports/fundamental/AAPL.json` are written by separate agents on separate paths. No file is written by two writers concurrently.

---

## 5. Trace fidelity fix (Goal 4)

### The bug

`_make_llm_trace_before` in `src/agents/analysts/news/agent.py:66-95` (and the equivalent in `fundamental/agent.py`, plus the strategist's trace hook) captures only `llm_request.contents` — the user-side message. ADK's `LlmAgent` puts the rendered system instruction (which contains `{news_context}` etc.) in a different field on `LlmRequest`. The trace records `"Run tick trace-..."` and silently drops the prompt content the LLM actually saw.

This means surface traces have been showing us half the picture since the trace harness was wired in Phase 5.

### Fix

1. Verify the correct ADK field at implementation time (likely `llm_request.config.system_instruction` or similar; the implementation plan owns this verification).
2. Concatenate it into the captured prompt under a clear `=== system ===` heading, followed by `=== user ===` for the existing `contents` capture.
3. **Refactor duplicated trace helpers into one shared utility.** `_make_llm_trace_before` and `_make_llm_trace_after` exist in identical-but-duplicated form across `news/agent.py`, `fundamental/agent.py`, and the strategist module. Move to `src/observability/trace.py` as `make_llm_trace_callbacks(section_name: str, model: str) -> tuple[before, after]`. Each agent imports and calls this once.
4. Add a regression test: configure a trace, run an analyst with a known system instruction, assert the captured prompt contains both system and user portions.

### Cache-aware trace markers

When the cache short-circuits the LLM, the trace section for that analyst (`03_news_llm_in` / `03_news_llm_out` etc.) should record something distinguishable from a real LLM call. Proposed:

```json
{
  "model":      "gemini-2.5-flash-lite",
  "prompt":     "(cache hit — input_hash=blake2b:abc..., prompt_version=2026-05-14-a)",
  "response":   "(loaded from cache/reports/news/AAPL.json — stored_at=2026-05-13T...)"
}
```

This makes it obvious when reading a trace which analyst-tickers paid for an LLM round and which were served from cache, with enough provenance to find the cache file.

---

## 6. Tick cadence reduction (Goal 5)

### Current state

Tick cadence lives wherever the orchestrator's scheduler / cron is configured. Likely `config/schedule.json` (per project convention — see `.claude/CLAUDE.md` § Configuration Convention) or hardcoded into a runner script. Implementation plan locates and confirms.

### Target state

Two ticks per day, anchored to US market hours: one shortly after open, one shortly after close. Times need to follow US daylight-saving transitions (EDT in summer, EST in winter), so the config either expresses times as ET and converts at runtime, or specifies the cron in a DST-aware scheduler. Implementation plan picks the mechanism.

Indicative shape:

```json
{
  "ticks_per_day": 2,
  "tick_times_et": ["09:45", "16:30"],
  "comment": "09:45 ET runs ~15 min after NYSE open; 16:30 ET runs ~30 min after close. Headroom to add a midday tick at 12:30 ET once cache + reports prove themselves on paper data."
}
```

`config/README.md` gets a new entry describing the file and its fields.

This is a config-only change. The orchestrator already runs on a schedule; we just slow the schedule down and let it survive DST transitions correctly.

---

## 7. Configuration

A new `config/analysts.json` consolidates the truncation caps currently embedded in `news/fetch.py` and `fundamental/fetch.py`:

```json
{
  "news": {
    "max_articles_per_ticker": 20,
    "max_summary_chars": 500
  },
  "fundamental": {
    "max_filing_mda_chars": 1500,
    "max_filing_risk_chars": 1500,
    "max_insider_footnotes": 5,
    "max_insider_footnote_chars": 400
  },
  "cache": {
    "enabled": true,
    "directory": "cache/reports"
  }
}
```

`config/README.md` documents each setting and its valid range.

The existing module-level constants (`_MAX_HEADLINES`, `_MAX_SUMMARY_CHARS`, etc.) read from this config at startup instead of being hardcoded.

---

## 8. Testing

Tests live under `tests/` following the existing layout.

**Unit tests:**

- `tests/data/test_price_history.py` — round-trip serialisation, ordering invariant (oldest -> newest).
- `tests/data/test_company_ratios.py` — round-trip serialisation, optional field handling.
- `tests/data/test_providers_split.py` — `get_price_history` and `get_company_ratios` over the same underlying provider call return consistent data (e.g. `last_price` matches the close of the most recent bar).
- `tests/contract/test_analyst_report.py` — `AnalystReport` schema validation: rejects empty drivers, rejects weights outside `[0, 1]`, accepts 2 and 4 driver counts.
- `tests/contract/test_report_cache_hash.py` — `news_hash_inputs` and `fundamental_hash_inputs` are stable across argument reorderings, sensitive to single-field changes (new article, new filing, changed price-per-share), and stable across irrelevant float jitter (`pe = 36.23879` -> `36.23880` does not bust cache).

**Integration tests:**

- `tests/agents/test_news_cache_roundtrip.py` — run the news analyst twice with the identical article set; first call invokes the LLM (mocked), second call hits cache and asserts `mock_llm.call_count == 1`.
- `tests/agents/test_news_cache_invalidation.py` — run twice with one article added between calls; assert the second call invokes the LLM and the cache file is updated.
- `tests/agents/test_news_cache_prompt_version.py` — run with `prompt_version="v1"`, then change to `"v2"`; assert the second call invokes the LLM despite identical articles.
- `tests/observability/test_trace_captures_system_instruction.py` — regression test for the trace-fidelity fix.

**Snapshot test for strategist prompt:**

- `tests/agents/test_strategist_prompt_layout.py` — feed a known `TickerEvidence` shape through the renderer; assert the rendered block matches the expected human-readable form (snapshot). Update the snapshot when the layout is intentionally changed.

**Existing tests:**

- All analyst tests under `tests/agents/analysts/` updated for the renamed data models and the optional `report` field on `AnalystVerdict`.
- Closed-vocabulary tests stay green — the closed-vocab semantics are unchanged.
- Existing snapshot / golden-file tests (if any) get fresh expected outputs that include the report.

**Live-trace acceptance:**

After this spec ships, the existing surface-trace harness (`docs/surface-traces/`) is the acceptance gate: a fresh trace must show (a) no 252-row history in `01_fetch_fundamental`, (b) `03_news_llm_in` / `03_news_llm_out` showing the full system instruction, (c) `03_news_llm_out` containing a populated `report` object, and (d) cache markers visible on a second trace run with identical inputs.

---

## 9. Out-of-scope / surfaced during this brainstorm (backlog candidates)

These came up in the brainstorming dialogue and are explicitly *not* in this spec. After spec sign-off, a follow-up commit proposes appending them to `docs/superpowers/backlog.md`:

1. **Deterministic-analyst confidence calibration** *(new Tier 2 entry)*. Today's deterministic verdict confidence is a rule-firing count, not a probability — the AAPL trace shows Technical at `conf=0.90` because five rules fired, regardless of regime. Backtest hit-rates by feature-combination could yield empirical confidence calibration. Likely overlaps with [[B5]] (per-evidence-key weighting) and [[B2]] (knowledge base outcome learning).
2. **Cross-tick analyst memory + "what_changed" awareness**. The hybrid report design originally included a `what_changed` field surfacing what's new since the prior tick. Removed from this spec because filling it cleanly requires feeding the prior report into the LLM, which is most of an analyst-memory feature. Properly belongs under [[B11]] (RAG substrate) or a dedicated analyst-memory brainstorm.

---

## 10. Implementation order (rough)

The implementation plan will sequence this properly. Indicative ordering, lightest-touch first:

1. **Data model split** (§1) — pure typed refactor, no behaviour change. Smallest blast radius.
2. **Trace fidelity fix** (§5) — unblocks all subsequent debugging. Cannot verify the rest without it.
3. **Caps externalised to config** (§7) — pure config refactor; no semantic change yet.
4. **`AnalystReport` schema + prompt extension** (§2) — additive, deterministic analysts unaffected.
5. **Strategist prompt restructure** (§3) — consumes (4); needs the new schema in hand.
6. **Report cache** (§4) — sits on top of (4); needs the report schema stable.
7. **Tick cadence reduction** (§6) — config change, deploys independently.

Each step is independently shippable. If any step misbehaves in paper-trading observation, we can pause without unwinding earlier steps.
