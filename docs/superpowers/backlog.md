# Future Development Backlog

A living index of work explicitly deferred during prior brainstorming sessions. Each segment below is sized to be roughly one future brainstorming session — pick one up via `/superpowers:brainstorming`, re-read the spec it was deferred from, then proceed.

When a segment is specced, move it from this file into a real spec under `docs/superpowers/specs/`. When new ideas surface from operating the bot, add them here.

---

## Strategist Roadmap (the spine)

The strategist is being grown in three goals. Items below are tagged with which goal they belong to.

- **Goals 1 + 2 — Strategist v2 + Analyst → Strategist contract.** *Specced and planned together under Phase 4.* Spec: `docs/Phase4-stratergist-and-analysts/spec.md`. Implementation broken into four sub-plans, each invocable via `superpowers:subagent-driven-development`:
  - `plan-A-contract-scaffolding.md` — additive types, digest aggregator, config (zero integration risk)
  - `plan-B-extractors-dual-emit.md` — per-analyst feature extractors + dual-emit callback (legacy `*_signals` and new `*_evidence` coexist)
  - `plan-C-strategist-v2.md` — strategist rewrite against the new contract, per-ticker stance output, derived lifecycle, held-position context, TradeLog FKs
  - `plan-D-cleanup.md` — drop dual-emit, persist `AnalystEvidenceRow` + `TickerEvidenceRow`, retire `AttributionWriter`, delete legacy `<Analyst>Signal` schemas
  Substrate from the earlier dropped council / exit-rules specs (lifecycle validation, telemetry tables, `opening_tick_id`/`closing_tick_id` FKs) was absorbed into Plan C.
- **Goal 3 — Knowledge base / self-improvement.** *Long arc.* The strategist learns from its own outcomes. The user's framing: "save the signal, not the trade." Stock-agnostic pattern recall, not a vector DB of past trades. Needs Phase 4 telemetry shipped + weeks of paper data before it can be designed concretely.

A few items previously in this backlog (council debate, persona memory, persona model diversity) are gone — they assumed the council architecture, which v2 dropped. If multi-LLM deliberation ever comes back, it'll be a fresh design conversation gated on Goal 3 outcome data.

---

## Tier 1 — Major (likely become full specs)

> *B1 (Analyst → Strategist contract) is consolidated into Phase 4 — see `docs/Phase4-stratergist-and-analysts/spec.md` and plans A–D. Numbering retained; B5 still references "Goal 2" semantics.*

### B2. Knowledge base — design the learning loop  *(Goal 3 — long arc, design only)*

**Origin:** The user's explicit Goal 3 framing: the bot should learn from outcomes. "We make money because we notice signals that infer we can earn money. Save the signal, not the trade." Stock-agnostic pattern recall, not a vector DB of past trades.

**Substrate already in place after Phase 4 (Goals 1 + 2):**
- `TickerStanceRow` — per-ticker strategist stance per tick (rationale, conviction, lifecycle, evidence_refs). *(Plan C.)*
- `StrategistDecisionRow` — final tick decisions with full metadata. *(Plan C.)*
- `TradeLogRow.opening_tick_id` / `closing_tick_id` — outcome attribution joins back to the tick that opened/closed each position. *(Plan C.)*
- `TickerEvidenceRow` — the canonical per-ticker per-tick evidence object (aggregate direction, confidence, disagreement, snapshotted weights). *This is the KB lookup primitive.* *(Plan D.)*
- `AnalystEvidenceRow` — per-analyst-per-ticker structured features + verdict, JSON-extensible. *(Plan D.)*
- `PositionThesis` rendered into prompts — the strategist's stated *why* is now persisted alongside the *what*. *(Plan C.)*

**The goal in plain English:** when the strategist is about to act on signal pattern X, it should know "the last N times we saw something shaped like X, here's what happened." Not "the last time we bought AAPL," but "the last time technicals looked oversold while smart-money inflows were trending positive."

**Key questions to brainstorm:**
- What does "stock-agnostic signal pattern" actually mean as a lookup primitive? Embedding of the analyst evidence vector? Cluster of evidence shapes? Discretised feature buckets? Hand-coded archetypes?
- Cold-start: how many ticks of paper data before the loop has anything to say? What does the strategist do *before* that threshold — operate identically to v2?
- What does the loop *do* once it has learned something? Re-bias `ANALYST_WEIGHTS`? Inject context into the strategist prompt ("similar setups historically resolved bearishly")? Veto certain decision-tag patterns?
- How do we avoid overfitting to a tiny paper-trading sample, especially since paper conditions are rosier than live?
- Storage shape: separate "lessons" table? Annotated `TickerStanceRow`? A side index that maps signal-pattern → outcome statistics?
- Read path vs write path: when does the loop *learn* (between ticks? batched nightly?) vs *get consulted* (every tick? only on novel patterns?)?

**Dependencies:** Phase 4 shipped (all four plans A–D) + ~weeks of paper-trading data accumulated. This brainstorm is design-only until that data exists; jumping to implementation early risks designing for an imaginary distribution.

**Likely outcome of the brainstorm:** decompose Goal 3 into sub-projects (e.g., "outcome attribution table," "signal pattern primitive," "lookup → prompt injection," "weight learning"). Each becomes its own spec.

---

### B11. RAG / retrieval substrate over filings, news, and transcripts  *(prose-corpus knowledge base)*

**Origin:** Surfaced during the analyst-LLM narrowing brainstorm (`docs/superpowers/specs/analyst-llm-narrowing-design.md`). The user explicitly framed this as "the next big step" — the narrowing refactor was scoped to leave room for retrieval *without designing it*. The narrowed fundamental and sentiment LLMs are the natural seats: they already read prose, they emit closed-vocabulary tags that index the underlying source documents, and a retrieval layer would augment their prompts ("here is what this company said last quarter", "here is a similar past headline cluster") without changing the analyst topology.

**Distinction from B2 (Goal 3 — knowledge base):**

- **B2** is *outcome-attribution* learning: "save the signal, not the trade" — pattern-recall over historical analyst-evidence + outcome pairs. Stock-agnostic. Lookup primitive: a feature-vector shape.
- **B11** is *document retrieval*: given the current ticker and analyst, fetch relevant past prose to inject into the prompt. Ticker-keyed. Lookup primitive: a document chunk.

Both are "knowledge bases" in the loose sense; they answer different questions and likely use different storage. They may share infrastructure later, but the brainstorms are separate.

**Substrate already in place after the analyst-LLM narrowing refactor:**
- `risk_factors_excerpt` and `mda_excerpt` present per filing via `edgartools` (no scraping).
- News headlines + summaries per Finnhub `company-news`.
- `AnalystEvidenceRow.key_factors` shaped as queryable closed-vocabulary tags (e.g. `risk:cybersecurity_added`) — usable as retrieval *facets* alongside semantic search.
- Composite index `(analyst, ticker, recorded_at)` on `AnalystEvidenceRow` for per-ticker history scans.
- Surface-tracing harness for measuring before/after retrieval impact.

**Key questions to brainstorm:**
- Corpus scope for v1: filings only, or filings + news? Earnings call transcripts are a third corpus with their own provider story (not currently fetched).
- Storage backend: SQLite + FTS5 for text search? SQLite + sidecar vector store (sqlite-vec, Chroma, LanceDB)? Postgres + pgvector? The choice intersects with deployment.
- Embedding model: cheap (text-embedding-3-small) vs richer; cached vs re-computed on retrieval.
- Retrieval keying: per-ticker history only (cheaper, more focused) vs cross-ticker semantic ("show me other companies that flagged supply_chain risk this quarter")?
- Wiring: a new `before_model_callback` on fundamental/sentiment that augments their prompt? A separate `RetrievalAgent` step that writes a state key the strategist also sees?
- Interaction with the closed-vocabulary `key_factors` shape: does retrieval refine the tag set, or co-exist as a separate prompt block?
- Sparse execution (overlap with B9): does retrieval run every tick, or only when the analyst is being prompted (and B9's gate said yes)?
- Cold-start: until enough filings + news accumulate, retrieval returns thin context. Behaviour during that window?

**Dependencies:** Analyst-LLM narrowing refactor shipped. Independent of B2 in design, though both may benefit from shared embedding infrastructure.

**Likely outcome of the brainstorm:** decompose into sub-specs (e.g. "filings corpus + retrieval", "news corpus + retrieval", "earnings transcript ingestion", "prompt-side retrieval wiring"). Each becomes its own spec under `docs/superpowers/specs/`.

---

## Tier 2 — Medium enhancements

### B3. Real-time / sub-tick exit evaluation

**Origin:** Carried over from the original exit-rules spec (now retired into Phase 4). v2 evaluates exits only at hourly tick boundaries — if AAPL crashes at 14:23, we won't notice until 15:00.

**The goal:** evaluate floors (and possibly ceilings) at sub-hour granularity so the bot doesn't sleep through a flash crash.

**Key questions:**
- Cheapest viable: shorten the tick to 15-min during market hours. Cost is mostly extra LLM calls (~4× per hour).
- Mid-tier: keep hourly strategist tick, add a lightweight price-watcher that fires a forced-exit on hard `stop_price` breaches without re-running the strategist. Where in the pipeline does it live? Does it write a synthetic tick to telemetry, or its own row type?
- Heavier: streaming price feed (Trading 212 doesn't really do this, so it'd mean polling). Probably overkill at paper scale.
- Strategist's role: should the strategist still vote on stop adjustments hourly, knowing a deterministic watchdog will catch breaches between ticks?

**Dependencies:** None hard. More valuable once we're live (paper account doesn't punish flash-crash latency much).

---

### B4. Target/stop revision rules (trailing stops & target ratchet)

**Origin:** v2 keeps `target_price` / `stop_price` *sticky* once set in `PositionThesis`. The strategist can update them via the stance, but there's no mechanical trailing logic.

**Key questions:**
- Allow the strategist to raise the stop tick-by-tick? Or only at PnL milestones (e.g., +5%, +10%)?
- Trailing rule: fixed % below the running max? ATR-based? Percentile of intra-trade volatility?
- Target ratchet: when price approaches target, raise both target and stop?
- Asymmetric handling: easy to *raise* a stop (lock in profit), risky to *lower* it (loss aversion). Should lowering require an explicit decision tag the strategist must justify?
- Telemetry: how do we record stop revisions vs original — extend `TickerStanceRow`, or a dedicated `ThesisRevisionRow`?

**Dependencies:** Phase 4 (Plan C) shipped.

---

### B5. Per-evidence-key analyst weighting

**Origin:** Phase 4 Plan A lands per-family weights with an explicit slot for nested per-key extension (`DEFAULT_ANALYST_WEIGHTS` in `src/contract/digest_defaults.py`, applied mathematically in `src/contract/digest.py`). This is the next refinement on top of that contract.

**The goal:** instead of "trust smart_money 1.5×", learn that "smart_money's `n_politicians > 2` is highly predictive but `total_dollar_value` alone is noise."

**Key questions:**
- Storage: extend `DEFAULT_ANALYST_WEIGHTS` to a nested `{analyst: {feature_key: weight}}` shape directly, or a separate `EVIDENCE_WEIGHTS` config layered on top?
- Where in the digest does per-key weighting apply: at feature time (re-scaling features into the aggregate), at vote time (each analyst's confidence is a weighted blend of its features), or both?
- Override mechanism: can a learned weighting shadow the hand-set defaults, or must they merge?
- Snapshotting: `weights_used` on `TickerEvidence.aggregate` is currently a flat `dict[str, float]`. Does it become nested too, or do we add a sibling `feature_weights_used`?

**Dependencies:** Phase 4 (Plans A + D) shipped. Strongly coupled to Goal 3 (knowledge base) — this is one of the things that loop should learn rather than have hand-tuned.

**Related extension:** the same machinery could replace the *entire* deterministic verdict function for `technical` / `smart_money` (see `docs/superpowers/specs/analyst-llm-narrowing-design.md` § Deterministic verdict heuristics) — not just the analyst-level weight. Worth surfacing as a sub-question when this brainstorm runs: are we learning *weights over rules* or *replacing rules with a learned function*? Both are continuous with B5's substrate.

---

### B9. Sparse-execution gate for surviving analyst LLMs

**Origin:** Surfaced during the analyst-LLM narrowing brainstorm (`docs/superpowers/specs/analyst-llm-narrowing-design.md`). After narrowing, the fundamental and sentiment LLMs run every tick — but their inputs are *prose*, and prose changes slowly (10-K filings are current for ~90 days; headlines for hours-to-days). Re-prompting on unchanged prose is wasted spend.

**The goal:** only re-prompt fundamental and sentiment LLMs when their underlying prose has *changed* since the last successful prompt. Cache the prior verdict per ticker and reuse it otherwise.

**Key questions:**
- What is the change-detection primitive per analyst? Filing-recency check for fundamental (any filing in the dump newer than the cached `latest_filed_at`)? Headline URL-set diff for sentiment?
- Where does the cache live — a new `LlmVerdictCacheRow`, or a `cached_from_tick_id` pointer on the existing `AnalystEvidenceRow`?
- Cache-eviction policy: TTL-based, manual-invalidation on watchlist changes, both?
- Invalidate-on-prompt-change: when the prompt template is edited, all cached verdicts are stale. How does the cache key encode prompt version?
- Does the deterministic feature extractor still run every tick? (Probably yes — extractors are cheap and the features feed the digest aggregate every tick regardless.)
- Telemetry: surface-trace needs to distinguish "cached verdict reused" from "fresh LLM call" so debug passes can tell which path fired.
- Cost vs accuracy trade: do we want a force-refresh-every-N-ticks ceiling so we never sit on a stale verdict for unbounded time?

**Dependencies:** Analyst-LLM narrowing refactor shipped. Cleaner once a filings KB exists (B11) since the KB already does the "what's new since last tick?" bookkeeping; before that, the gate logic lives inside each analyst.

---

### B10. Narrative analyst — 13D letters and Form-4 footnotes

**Origin:** Surfaced during the analyst-LLM narrowing brainstorm. The smart_money analyst was switched to deterministic because today's prompt only classifies counts — not because there is no prose to read. SC 13D filings often carry multi-page intent letters ("we plan to nominate two directors", "we believe management should be replaced") and SEC Form 4 footnotes carry context like "shares acquired pursuant to 10b5-1 trading plan adopted 2024-03-15" (i.e. *not* a discretionary buy). The deterministic analyst cannot read these.

**The goal:** add a *new* sibling LLM analyst that reads the prose layer of smart-money filings and emits structured findings in the standard `AnalystEvidence` shape. Runs alongside the deterministic smart_money analyst rather than replacing it.

**Key questions:**
- Naming: `smart_money_narrative`? `activist_intent`? Something covering both 13D letters and Form-4 footnotes?
- Where do we get the prose? `edgartools` returns 13D filings but the letter may be an exhibit — verify the extraction path. Form-4 footnote text is in the XML.
- Verdict surface: bullish/bearish/neutral like the others, or a separate axis (e.g. `intent: activist | passive | strategic | none`)?
- Aggregation: if both `smart_money` (deterministic) and `smart_money_narrative` (LLM) emit verdicts, does the digest treat them as two analysts in the weighted vote, or fold them into one smart-money slot with sub-weighting?
- Strong sparseness overlap with B9: 13D filings are rare per ticker; this analyst would emit `is_no_data=true` on most ticks. Likely lands together with the sparse-execution gate.
- Closed vocabulary for `key_factors`: `intent:activist`, `intent:passive`, `plan:director_nomination`, `plan:replace_management`, `form4:10b5-1_plan`, `form4:open_market`, etc. Where does the vocabulary live — extend `config/analyst_heuristics.json`?

**Dependencies:** Analyst-LLM narrowing refactor shipped. Independent of B9 in principle, but likely co-developed.

---

## Tier 3 — Small follow-ups & easy wins

### B6. Persist `risk_clamps_applied`

**Origin:** Carried over from the original exit-rules spec (now retired into Phase 4); not absorbed into v2.

**The goal:** add a `RiskClampRow` table so we can analyse "did the cash floor / max-position cap block trades that would have been profitable?"

**Effort:** ~one phase. New table in `persistence.py`, write from `risk_gate_agent` (or a tiny writer running after risk_gate). The clamp data is already in session state — just needs flushing.

**Dependencies:** None.

---

### B7. Cost / performance observability

**Origin:** Operational concern noted throughout earlier specs ("LLM cost per tick — tracked in docs/performance/ after first paper-trading week"). Currently ad-hoc.

**The goal:** structured per-tick cost + latency telemetry.

**Key questions:**
- Per-tick: total LLM tokens, total cost, p95 latency per agent.
- Quota fallback events: how often does Pro→Flash trigger, and does the strategist's quality drop measurably?
- Where it lives: a `TickCostRow`? Or a single dashboard query over ADK's session log?

**Effort:** ~half-spec.

**Dependencies:** None. Useful input to Goal 3 (helps quantify "is the knowledge base improving outcomes per dollar spent?").

---

### B8. Decision replay / counterfactual tooling

**Origin:** Implicit in Goal 3 — to validate "would the new logic have made better decisions?", we need to re-run a tick deterministically with different code.

**The goal:** given a `tick_id`, reload all session-state inputs (analyst signals, portfolio snapshot, held theses) and re-run the strategist (or any downstream agent) without re-calling the broker or LLMs.

**Key questions:**
- What state do we need to freeze: analyst signals, portfolio snapshot, `state["positions"]` rendered theses, prompts as sent?
- Where do replays live: a separate CLI? A test-style runner under `tests/replay/`?
- Diffing: how do we render "original decision vs replayed decision" usefully — per-ticker stance diff?

**Dependencies:** Phase 4 telemetry shipped (so there's something to replay). Cleaner once Goal 3 is being designed, since validating its experiments is the main use case.

---

## How segments interact

```
Phase 4 (Goals 1 + 2 — strategist v2 + analyst contract, plans A→B→C→D)
   │
   ├── Analyst-LLM narrowing (specced: docs/superpowers/specs/analyst-llm-narrowing-design.md)
   │     │
   │     ├── B9  (sparse-execution gate)        ─┐
   │     ├── B10 (narrative analyst — 13D/Form4) ─┤── often co-developed
   │     └── B11 (RAG / retrieval substrate)    ─┘
   │
   ├── B5 (per-evidence weighting) ─┐
   │                                 │
   ├── Goal 3 = B2 (knowledge base, long arc) ─┼── (B5 is one of B2's outputs)
   │                                 │
   │                                 └── B8 (replay tooling — validates B2's experiments)
   │
   ├── B3 (sub-tick exit)        — independent, any time
   ├── B4 (trailing stops)       — small extension on top of v2
   ├── B6 (risk clamp persistence) — small follow-up
   └── B7 (cost observability)   — independent, low priority but feeds B2
```

**Rough order if doing them in series:** Phase 4 plans A → B → C → D → analyst-LLM narrowing → B6 → B7 → B9 → B11 → B10 → B2 (long arc) → B5 → B4 → B3 → B8.

Most are independent enough to reorder by what hurts most in operation. Two strict orderings hold: **Phase 4 before B2** (the knowledge base needs a clean signal contract and decision telemetry to reason over) and **analyst-LLM narrowing before B9/B10/B11** (sparse execution, the narrative sibling, and retrieval all assume the narrowed-LLM topology).
