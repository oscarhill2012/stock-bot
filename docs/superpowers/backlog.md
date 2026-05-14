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
- **Phase 5 — Analyst re-categorisation + deterministic-first baseline.** *Specced.* Spec: `docs/Phase5-analyst-refine/spec.md`. Plan: `docs/Phase5-analyst-refine/plan.md`. Restructures the analyst pool to 5 concerns (Technical / Fundamental / News / Social / SmartMoney) — three deterministic, two narrowed-LLM — with a closed-vocabulary `key_factors` set and a surface-trace harness. Operationalises the minimum-LLM baseline policy that [[B16]] codifies as a ratchet for any future LLM expansion.
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

**RAG flavours to weigh during the brainstorm** *(the same corpus can be retrieved over in multiple ways; v1 likely picks one)*:

- **Semantic RAG** — embed the corpus once, fetch top-k by vector similarity at prompt time. Static index, ticker-keyed cosine lookup. Cheapest. Suits filings (low churn, long shelf-life).
- **Agentic RAG** — the analyst (or a separate retrieval sub-agent) iteratively decides what to search for, reads the result, then decides whether to search further. Suits exploratory questions ("what did this company say last quarter about supply chain?") where the right query isn't knowable upfront. More expensive; trace-justified under [[B16]].
- **Dynamic / fresh-corpus RAG** — the corpus is rebuilt continuously as new filings/news land; retrieval includes recency weighting and de-duplicates against previously-injected chunks. Suits news (high churn, short shelf-life) more than filings.

These aren't mutually exclusive — a plausible progression is semantic-only over filings (v1) → layered dynamic-fresh on top for news (v2) → agentic retrieval once the closed-vocab `key_factors` give the analyst something concrete to query against (v3).

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

### TradingAgents-inspired explorations (B12, B14)

The two entries below — and [[B13]] in Tier 2 — are deferred experiments inspired by the TradingAgents paper (`docs/papers/TradingAgents.pdf`). They are *not* commitments. Each must clear [[B16]]'s ratchet checklist (trace-data evidence of a baseline gap, minimal-hop justification, cost estimate, shelve-criterion) before being scheduled. Listed here so the inspiration source isn't lost when the next brainstorm picks one up.

---

### B12. Bull/Bear researcher debate over the analyst pack  *(TradingAgents-inspired, experimental)*

**Origin:** Surfaced during the post-paper-reading review of `docs/papers/TradingAgents.pdf`. The paper inserts a Bull/Bear researcher debate *between* the analyst reports and the trader's final decision. Each researcher reads the same analyst output and argues their side; the Trader (≈ our Strategist) reads the debate transcript rather than the raw analyst pack.

**Distinction from [[B13]] (risk debate):** B12 debates *direction* (should we be long, short, or flat on this ticker?). B13 debates *sizing* (given the direction, how aggressive should the position be?). Both can co-exist; B12 sits logically upstream of B13.

**The goal — experiment, not copy:** test whether inserting a two-agent (or three: Bull / Bear / Neutral) directional debate between the analyst pack and the Strategist materially improves per-ticker stance quality, measured against a surface-trace baseline of the same evidence going directly to the Strategist.

**Experimentation path:**
- **v0** — current state: 5-analyst pack → Strategist (the Strategist itself does the bull/bear reasoning internally).
- **v1** — externalise the bull/bear voices as two prompts that emit structured rebuttals; Strategist reads both alongside the digest.
- **v2** — multi-round debate (Bull rebuts Bear's rebuttal, etc.) with a turn cap.

**Key questions:**
- Per-ticker vs per-tick: do Bull/Bear run once per ticker per tick, or only on tickers where the digest is contested (`disagreement_score` over a threshold)? Strong sparse-execution overlap with [[B9]].
- Output shape: free-form transcript injected into the Strategist prompt, or structured `BullCase` / `BearCase` evidence objects parallel to `TickerEvidence`?
- Cost: roughly doubles (or triples) per-ticker LLM calls on contested tickers. Trace-justified expansion under [[B16]].
- Strategist coupling: does the Strategist still see the digest, or only the debate output? (Probably both — losing the digest discards calibration.)
- Aggregation: when Bull and Bear materially disagree about the *evidence itself* (not just its sign), does that propagate as a `disagreement_score` boost?

**Shelve-criteria:** if v1 surface traces show the externalised bull/bear voices restate what the Strategist's internal reasoning already covers — shelve. Revisit if Goal 3 ([[B2]]) outcome attribution shows the Strategist is systematically wrong on contested tickers.

**Dependencies:** Phase 5 shipped. Goes through [[B16]]'s ratchet checklist. Strong design overlap with [[B9]] (only run on contested tickers).

---

### B14. Per-stock per-analyst prose reports  *(TradingAgents-inspired, deferred from Phase 5)*

**Origin:** During the Phase 5 analyst re-categorisation brainstorm the user proposed having each analyst emit a short prose report per ticker (TradingAgents-style, where each analyst writes a few paragraphs that downstream agents read). Deferred from Phase 5 to keep the deterministic-first baseline minimal; the closed-vocabulary `key_factors` tags currently carry the same information in compressed form.

**The goal:** evaluate whether prose-form analyst reports — one short paragraph per analyst per ticker, generated by the same LLM that already runs for Fundamental/News and synthesised mechanically (or via cheap LLM rendering) for the deterministic analysts — help the Strategist make better per-ticker decisions than the current `key_factors` + digest substrate.

**Key questions:**
- Generation cost: Technical/Social/SmartMoney are deterministic — do we synthesise their "report" from features via a template (free), or invoke an LLM to render features into prose (~3× more LLM calls per tick)?
- Storage: does each `AnalystEvidenceRow` gain a `report_prose: str | None` column, or live in a sibling `AnalystReportRow`?
- Strategist consumption: prose reports replace the digest in the prompt, augment it, or are summarised by a separate "Manager" agent (the TradingAgents pattern)?
- Risk of redundancy: if the prose reports just restate the closed-vocab tags, this is pure cost with no signal lift. The trace-data check matters more here than for B12/B13.
- Per-ticker vs per-tick: same sparse-execution question as [[B9]] and [[B12]] — only generate for tickers the Strategist is actively considering acting on?

**Shelve-criteria:** if a v1 surface trace shows the Strategist's per-ticker stance distribution is statistically indistinguishable from the no-prose baseline — shelve. Revisit if Goal 3 outcome attribution shows the Strategist's *rationales* are systematically thin in ways prose context would fix.

**Dependencies:** Phase 5 shipped. Goes through [[B16]]'s ratchet checklist. Strong design overlap with [[B12]] (both add per-ticker LLM hops between analysts and Strategist).

> *Update — B14 partially shipped under `docs/superpowers/specs/analyst-surface-redesign-design.md`. That spec implements the per-analyst prose-report half (News + Fundamental emit a structured `AnalystReport` with summary + drivers; deterministic analysts surface features as labelled bullets) but explicitly excludes the deterministic-analyst LLM-narrator variant the original B14 also flagged. If the deterministic-narrator variant becomes interesting later, it reopens here under [[B16]]'s ratchet.*

---

### B18. Cross-tick analyst memory and "what changed since last tick"

**Origin:** Surfaced during the analyst-surface-redesign brainstorm (`docs/superpowers/specs/analyst-surface-redesign-design.md`). The hybrid analyst-report design originally included a `what_changed` field — the LLM surfaces what's new since the prior tick. Removed from that spec because filling it cleanly requires feeding the prior report into the LLM, which is most of an analyst-memory feature. Doing it half-implemented (no prior context, LLM fabricates the delta) creates a field that promises more than it delivers.

**The goal:** give each LLM analyst persistent memory of its prior verdict + report per ticker, fed back into the next tick's prompt as continuity context. Enables genuine `what_changed`, drift detection ("I said X two ticks ago and now I'm saying not-X — why?"), and prepares the substrate for the calibration loop in [[B2]].

**Distinction from [[B11]] (RAG / retrieval substrate):**

- **B11** retrieves *external* prose (filings, news, transcripts) into the analyst prompt — document chunks the analyst hasn't seen.
- **B18** retrieves the *analyst's own prior output* — degenerate one-document RAG over the analyst's last verdict + report for a ticker.

The two share retrieval primitives but answer different questions. They likely co-design but ship as separate features.

**Substrate already in place after the analyst-surface-redesign spec ships:**
- `AnalystReport` schema (`summary` + `drivers`) on `AnalystVerdict.report`.
- Per-(analyst, ticker) report cache at `cache/reports/<analyst>/<ticker>.json` — already stores the most recent verdict + report with a prompt-version fingerprint. Natural seed for "prior tick" lookup.
- Hash-cache machinery for input change detection (gives `what_changed` a clean denominator: which articles/filings are new vs prior).

**Key questions:**
- Memory shape: just the prior tick's report, or a rolling N reports? Verbatim or summarised? Per-ticker only, or also a portfolio-wide rolling memory?
- Storage: extend the existing report cache (it already stores the prior report; just add a `previous_*` field), or a sibling memory store with its own retention policy?
- Wiring: feed prior report into the prompt unconditionally on every cache miss, or only when an explicit "summarise the delta" instruction is active?
- Hallucination risk: an LLM citing prior context can confidently misremember it. What's the forcing function tying recalled memory back to ground truth — assertions over the cached report? Diffing the LLM's `what_changed` against the deterministic hash-diff?
- Cold-start: first tick after deploy has no prior report. Behaviour? (Probably: omit `what_changed`, emit a "first observation this tick" flag.)
- Eviction: when does memory get forgotten — never, on watchlist removal, on TTL, on a manual invalidation event?
- Interaction with the prompt-version fingerprint: bumping the prompt version invalidates the cache; does it also invalidate memory, or do we let the new prompt "see" the old report?

**Dependencies:** Analyst surface redesign spec shipped (`analyst-surface-redesign-design.md`). Likely co-specced with [[B11]] since they share retrieval / continuity primitives.

**Likely outcome of the brainstorm:** unify [[B11]] and B18 under one retrieval-and-continuity substrate spec, with two sub-features (external corpus retrieval; self-prior-report retrieval) sharing storage + invalidation machinery.

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

### B10. Narrative analyst — 13D letters and Form-4 deep-footnote reading

**Origin:** Surfaced during the analyst-LLM narrowing brainstorm. Phase 5 (analyst re-categorisation) then:
- moved insider data into Fundamental's scope; and
- pulled Form-4 footnote snippets in as a *truncated* supplement to the Fundamental LLM prompt (≤5 footnotes × ≤200 chars each).

Two narrative-prose sources remain unread after Phase 5:
- Full Schedule 13D filings often carry multi-page intent letters ("we plan to nominate two directors", "we believe management should be replaced"). Fundamental does not touch these.
- The Form-4 footnote supplement is intentionally truncated. Long footnotes describing complex arrangements (performance-award clawbacks, prearranged-plan amendments, derivative vesting triggers) are clipped before they reach the LLM.

**The goal:** add a *new* sibling LLM analyst that reads the full narrative layer of owner-intent and insider filings. Runs alongside Fundamental (which reads MD&A + risk factors + the truncated insider supplement) rather than replacing it. After Phase 5 the insider data lives under Fundamental, so the natural pool position is "sibling to Fundamental" rather than "sibling to SmartMoney" — but the digest aggregation question is open (see below).

**Key questions:**
- Naming: `activist_intent`? `owner_narrative`? `insider_narrative`? Something covering both 13D activist intent and Form-4 deep footnotes.
- Where do we get the prose? `edgartools` returns 13D filings but the letter may be an exhibit — verify the extraction path. Form-4 footnote text is already in the XML; we'd lift Phase 5's truncation cap.
- Verdict surface: bullish/bearish/neutral like the others, or a separate axis (e.g. `intent: activist | passive | strategic | none`)?
- Aggregation: how does the digest treat this analyst's verdict — fold into Fundamental's weight as a sub-slot, treat as a sixth pool entry, or some hybrid?
- Strong sparseness overlap with B9: 13D filings are rare per ticker; this analyst would emit `is_no_data=true` on most ticks. Likely lands together with the sparse-execution gate.
- Closed vocabulary for `key_factors`: `intent:activist`, `intent:passive`, `plan:director_nomination`, `plan:replace_management`, `form4:performance_award_clawback`, `form4:10b5-1_plan_amendment`, etc. Extend `fundamental_vocabulary` (since it's a Fundamental sibling) or its own block?

**Dependencies:** Phase 5 analyst re-categorisation shipped (insider lives in Fundamental). Independent of B9 in principle, but likely co-developed. Justified by Phase 5's baseline surface trace — if the truncated Form-4 footnote supplement is visibly underweighted by the strategist, this analyst is the obvious mitigation (and feeds back into [[B16]]'s ratchet-policy framework).

---

### B13. Three-perspective risk debate (Risky / Neutral / Safe)  *(experimental, trace-justified)*

**Origin:** Surfaced during the post-paper-reading review of `docs/papers/TradingAgents.pdf`. The paper's "Risk Management" layer is three personas (Risky, Neutral, Safe) debating the Trader's proposed action before a Fund Manager arbitrates. The hypothesis: explicit risk-perspective tension reduces both over-leveraging and over-conservatism.

**The goal — experiment, not copy:** test whether running the strategist's proposed action through a three-persona risk check produces better risk-adjusted outcomes than the existing deterministic `risk_gate`. The two co-exist: `risk_gate` enforces hard floors (cash, position caps); a Bull/Bear-style risk debate sits *between* the strategist and `risk_gate` and can attenuate position sizes the `risk_gate` would otherwise pass.

**Experimentation path:**
- **v0** — current state: strategist → `risk_gate` (deterministic clamps only).
- **v1** — single LLM "risk reviewer" that adjusts position weights (not direction). Cheap, single-call.
- **v2** — three personas (Risky / Neutral / Safe) with explicit aggregation logic. Closer to TradingAgents' setup.

**Key questions:**
- Authority boundary with the deterministic `risk_gate`: the debate can attenuate sizes; can it veto positions outright?
- Persona configuration: prompt-engineered personas only, or different LLM models per persona (more diversity, more cost)?
- Aggregation: weighted vote? Majority-with-veto? Always defer to Safe in tie-breaks?
- Cost-vs-benefit: paper account is forgiving of bad sizing; the debate's value rises with capital at stake. Likely a pre-live-deployment gate, not a paper-stage one.

**Shelve-criteria:** if v1 surface traces show the risk reviewer either (a) consistently agrees with `risk_gate`'s deterministic output, or (b) under-attenuates to the point of being decorative — shelve. Revisit when going live.

**Dependencies:** Phase 5 shipped (strategist consuming the 5-analyst pack stably). Strategist v2 hardening (Plan E) probably needs to land first so the persona debate has a stable input contract. Goes through [[B16]]'s ratchet checklist before being scheduled.

---

### B15. Market-regime analyst  *(provider-gated)*

**Origin:** During the Phase 5 analyst re-categorisation brainstorm the user noted that *market sentiment* (VIX, put/call ratio, AAII sentiment survey, sector rotation) is a distinct concept from news or social sentiment — it's market-wide regime data, not company-specific. No provider currently fetches it; the spec deferred it.

**The goal:** add a sixth analyst slot for market-regime signals: VIX (volatility / fear index), CBOE put/call ratio, AAII bull/bear survey, sector-rotation indicators. Output is a regime-classifier verdict (`risk_on`, `risk_off`, `transitioning`, `flat`) plus the underlying numerics in features. The strategist consumes the regime verdict as portfolio-wide context rather than per-ticker.

**Key questions:**
- Data sources: VIX (CBOE / Yahoo), put/call (CBOE), AAII (weekly survey — different cadence), sector rotation (ETF-ratio derived). Free vs paid?
- Verdict surface: regime classifier doesn't fit `AnalystVerdict(lean, magnitude, confidence)` cleanly. Extend `AnalystVerdict` with an optional regime axis? Or a separate `MarketContext` evidence object that bypasses the digest?
- Per-ticker vs portfolio-wide: regime is one signal for the whole watchlist, not per ticker. Current `AnalystEvidence` shape is per-ticker. Needs a new persistence path.
- Cadence: VIX is intra-day, AAII is weekly. Combining cadences in one analyst is awkward.
- Strategist consumption: as a prompt block (cheap) or as a sizing multiplier (mechanical risk-on/off bias)?

**Shelve-criteria:** if providers are paid/unavailable and surface traces show strategist decisions don't visibly need regime context (i.e. ticker-level evidence is sufficient even in volatile regimes), shelve indefinitely.

**Dependencies:** A free or cheap provider exists for at least VIX + put/call ratio. AAII can be scraped if needed.

---

### B16. LLM augmentation per analyst — trace-justified ratchet  *(policy anchor)*

**Origin:** Phase 5 commits the project to a minimum-LLM-as-baseline policy: only Fundamental + News + Strategist call LLMs. Adding LLM hops elsewhere (Technical, Social, SmartMoney, or extending Fundamental / News beyond their closed-vocab narrowed mandates) requires *trace-data evidence* that the baseline misses something material. This backlog entry is the policy *anchor* — every concrete LLM-expansion proposal becomes a sub-brainstorm with this entry as its checklist.

**The goal:** when an LLM addition is proposed, run it through a structured justification before it is scheduled.

**Checklist for any LLM-augmentation proposal:**
1. **What baseline gap does the trace show?** Cite specific surface-trace files where the deterministic verdict (or the existing narrowed LLM) demonstrably misses signal that an LLM hop would catch.
2. **What is the minimum LLM hop that closes the gap?** (Not the maximum.)
3. **What is the expected token cost per tick?** Multiplied by tickers × tick rate × runtime hours.
4. **What is the experimentation path** (v0 baseline → v1 minimal LLM → v2 expanded)?
5. **What is the shelve-criterion** — under what trace-data condition do we revert?
6. **Does it overlap with [[B9]] (sparse execution)** — can the LLM hop be cached / gated to non-changing inputs?

**Likely candidates queued behind this gate** (none yet justified by data; listed for posterity):
- Technical LLM that reads chart-pattern descriptions (depends on a chart-image provider).
- Social LLM that reads raw Reddit/Twitter posts (depends on a raw-posts provider; Finnhub aggregate doesn't qualify).
- SmartMoney LLM that reads 13D/13G prose — already its own entry ([[B10]]).
- Fundamental / News mandate expansion beyond the closed-vocab narrowing.

**Dependencies:** Phase 5 shipped with baseline surface trace in place.

**Shelve-criteria for the entry itself:** if after 3 months of paper operation there have been zero LLM-augmentation proposals (i.e. the baseline is consistently adequate), retire as solved-by-omission.

---

### B17. Deterministic-analyst confidence calibration

**Origin:** Surfaced during the analyst-surface-redesign brainstorm (`docs/superpowers/specs/analyst-surface-redesign-design.md`). The AAPL baseline trace at `docs/surface-traces/trace-20260513T165408-9adf5766-AAPL.json` shows Technical firing `bearish, confidence=0.90` because all five deterministic rules fired (`trend_up_20d`, `momentum_agree`, `rsi_overbought`, `near_52w_high`, `near_52w_low`). RSI-overbought-near-52w-high in a strong uptrend can persist for weeks; the regime does not warrant 90% confidence. Today's confidence is rule-firing-count, not probability.

**The goal:** replace rule-count confidence with empirical, regime-aware calibration. Backtest hit-rates by feature combination yield posterior probabilities; confidence becomes "P(direction correct | features fired)" rather than "fraction of rules that fired."

**Why it matters:** the strategist treats deterministic verdict confidence as a cognitive anchor against narrative drift (see `analyst-surface-redesign-design.md` § 2). If that anchor is mis-calibrated, the strategist either over-defers to overconfident deterministic verdicts or under-weights them once it learns they're noisy. Calibration restores the anchor's load-bearing role.

**Key questions:**
- Calibration granularity: per-rule, per-rule-combination, or per-feature-vector embedding? Combinatorial blowup risk with rule-combination.
- Cold-start: until enough paper data accumulates, what does confidence read from? (Probably: keep the rule-count formula as a fallback, gate the empirical override behind a sample-size threshold.)
- Storage: lookup table keyed on feature signature? Embedding + nearest-neighbours? Logistic-regression coefficients shipped in config?
- Per-ticker vs cross-ticker calibration: AAPL overbought behaves differently from NVDA overbought, but per-ticker needs orders of magnitude more data. Likely cross-ticker for v1.
- Where does the recalibrated confidence live: replace the existing `confidence` field, or sit alongside (`confidence_calibrated`) so legacy paths keep working?
- Telemetry: surface-trace needs to show both raw rule-count confidence and calibrated confidence so we can A/B them post-hoc.

**Overlaps:**
- [[B5]] (per-evidence-key analyst weighting) — both touch how analyst features turn into trusted signals; B5 is the weighting side, B17 is the confidence side. Likely co-specced.
- [[B2]] (knowledge base outcome learning) — B17 is essentially a thin slice of B2's outcome-attribution pipeline applied to one specific question. May absorb into B2 rather than ship standalone.

**Dependencies:** Analyst surface redesign spec shipped. Phase 4 telemetry shipped (the hit-rate data lives in `TickerStanceRow` + `TradeLogRow` joins). ~weeks-to-months of paper-trading data for empirical hit-rates.

**Likely outcome of the brainstorm:** decide whether to ship as a standalone Tier 2 spec or fold into B2's design. If standalone, scope is small enough for a single spec + implementation plan.

---

### B19. Historical social-sentiment ingestion  *(unlocks social analyst in backtest)*

**Origin:** Backtest harness spec (`docs/superpowers/specs/backtest-harness-design.md`) explicitly skips social in backtest — Finnhub's social endpoint went paid, the official Reddit API has no historical depth past ~1000 posts, and Twitter/X historical is dead since 2023. The strategist already tolerates `social=None`, so the harness ships without it, but every backtest is one signal short until this lands.

**The goal:** restore historical social sentiment back to ~2022 for free by scraping a Pushshift-successor mirror (pullpush.io / arctic_shift / similar) and computing sentiment locally so we are not dependent on a paid endpoint.

**Key questions:**
- Which mirror is most reliable? pullpush.io is community-run with no SLA — what's the failure mode and what's the fallback?
- Which subreddits earn their place: WSB only, or a broader set (`investing`, `stocks`, ticker-specific subs)?
- Sentiment model: VADER is 3 lines, runs on CPU, decent on social text. FinBERT is more accurate but heavier. Pick one or run both side-by-side?
- How do deletions / edits get handled — snapshot once and freeze, or refresh periodically?
- Where does this wire in: register as a new `social` upstream in the existing provider shell, so live and backtest use it identically.
- Backfill posture: one-time fill into the existing `backtests/cache/store.sqlite` plus an incremental nightly job once live.

**Dependencies:** Backtest harness shipped (so we know what shape the cache expects). Not gated on anything else.

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

### B20. Backtest resumability

**Origin:** Backtest harness v1 treats interruption (Ctrl-C, OOM, transient cache failure mid-run) as terminal — the user starts a fresh `run_id`. Acceptable for short windows; painful as multi-window suites grow.

**The goal:** allow an interrupted run to be resumed against the same `run_id` from the last completed tick.

**Key questions:**
- Checkpoint shape: last-completed-tick in `manifest.json` is probably enough since per-tick artefacts are atomic.
- Resumption safety: refuse to resume if git sha, cache schema version, config snapshot, or watchlist drift since interruption.
- CLI ergonomics: `--resume <run-id>` vs an opt-in flag; what happens on conflict.

**Dependencies:** Backtest harness shipped.

---

### B21. Multi-window orchestration + cross-window dashboards

**Origin:** Backtest harness v1 ships with one configured era window (`svb-stress-2023-03`) to keep scope tight. Real evaluation needs results across multiple regimes (covid recovery, fed pivot, AI rally, election, tariff shock, etc.).

**The goal:** a driver that runs all configured era windows in sequence (or a subset by tag), and a cross-window report comparing Sharpe / vs-SPY / trade count by regime — so we can spot "the bot wins in calm regimes but loses in stress regimes" or vice versa.

**Key questions:**
- Cache pre-fill orchestration: warm every window's cache once up front, or lazily as each run starts?
- Report shape: one combined `metrics.md` table, or per-window + a top-level summary?
- Era tagging: add a `tags: ["high-volatility", "rate-shock", ...]` field to `backtest_windows.json` so we can filter (`--tags rate-shock`)?
- LLM cost guardrails: a full sweep is N× single-window cost. Add a `--dry-run-cost-estimate` mode.

**Dependencies:** Backtest harness shipped. Worth more once B19 (social ingestion) is also in so the analyst pack is complete.

---

### B22. Shared report-cache callback factory (deduplicate News + Fundamental)

**Origin:** Phase 5 analyst-surface-redesign Task 6 shipped a hash-based LLM report cache for the News and Fundamental analysts. The two agents now hold byte-identical copies (~150 LOC each) of `_build_*_cache_callbacks` — the only differences are the analyst label, prompt-version constant, state key, hash function, output key, and trace section name. Flagged in the Opus final review as a non-blocking follow-up; deferred so Phase 5 could close cleanly before backtest.

**The goal:** collapse both copies into a single `_build_report_cache_callbacks(analyst, prompt_version, input_reader, hash_fn, output_key, trace_section)` factory in `src/agents/analysts/_common.py` (where `_chain_before` / `_chain_after` already live after the Task 6 polish pass). News and Fundamental agents become ~10-line call sites passing the differences as arguments.

**Key questions:**
- Factory signature: pass `input_reader` as a `Callable[[state, ticker], Any]` so each analyst owns its dict→typed-model reconstruction (Fundamental rebuilds `CompanyRatios` / `Filing` / `Form4Bundle`; News just reads the article list)? Or pass the state keys and let the factory do generic dict access?
- Where to live: `_common.py` is the obvious home — verify nothing in the news/fundamental imports would now cycle.
- Test strategy: the existing integration tests (`test_news_cache_*`, `test_fundamental_cache_*`) act as the regression net. Add one unit test exercising the factory directly with a stub analyst so a future third analyst's wiring is exercised.
- A future third analyst that wants caching (e.g. politician-trades, fundamentals-deep) becomes a 10-line addition rather than a third 150-LOC mirror — name this as the motivating use case in the spec.

**Dependencies:** None. Pure refactor on top of `worktree-phase5-analyst-surface-redesign`.

---

### B23. Auto-derived prompt-version fingerprint (close the silent-stale-cache risk)

**Origin:** Phase 5 Task 6 keyed the report cache on `(input_hash, prompt_version)`. The version strings (`NEWS_PROMPT_VERSION` / `FUNDAMENTAL_PROMPT_VERSION` in `src/agents/analysts/report_cache.py:43-47`) are hand-maintained constants living in a different file from the prompt templates (`src/agents/analysts/{news,fundamental}/prompts.py`). A contributor editing a template has no structural prompt to bump the constant; if they forget, the cache silently serves stale verdicts generated under the old prompt. Flagged in the Opus final review as a non-blocking follow-up; risk is low while pre-deployment but bites once the cache has accumulated weeks of live entries.

**The goal:** derive each prompt-version string from a hash of its rendered template (plus closed vocabulary) instead of maintaining it by hand. Any edit to the template automatically invalidates every cached entry — no human discipline required.

**Key questions:**
- What to hash: just the rendered instruction text? Instruction + vocab JSON? Instruction + vocab + the `AnalystVerdict` / `AnalystReport` schema fingerprint (catches contract drift too)?
- Reference vocab problem: News and Fundamental render against a `Vocabulary` value that varies tick-to-tick. Hashing the instruction needs a deterministic reference vocab so the version is stable across ticks. Hard-code a `_REFERENCE_VOCAB` constant per analyst, or hash the *template* (pre-substitution) rather than the rendered output?
- Backtest compatibility: a mid-sweep template edit would now invalidate a partially-populated cache. Probably the right behaviour, but the backtest harness should pin the version string for the duration of a sweep — verify this is compatible with the cache layout.
- Migration: existing cache entries on disk use the old string-literal version. First run after this lands invalidates them all. Acceptable, but document it.
- Where to live: probably `src/agents/analysts/report_cache.py` — a `_derive_prompt_version(instruction, schema)` helper, and the module-level constants become `NEWS_PROMPT_VERSION = _derive_prompt_version(...)` at import time.

**Dependencies:** None. Cleanest after [[B22]] (which centralises the cache wiring) but doesn't strictly require it.

---

### B24. Persistence schema refresh — after first backtest runs

**Origin:** Backtest harness design review (May 2026). `src/orchestrator/persistence.py` (~420 lines) hasn't been touched since the early scaffolding. Backtest reuses it via the existing `db_session` seam and per-run `create_all(engine)` pattern — no refactor is required to ship backtest. But the schema carries early-days cruft: no FK relationships between `evidence` / `ticker_stance` / `decision` / `portfolio_snapshot` (all just stamp `tick_id` as a free-string column, no JOINs), inconsistent timestamp column names (`timestamp` vs `recorded_at` vs `opened_at`), no Alembic / migration story, and a single 420-line module that wants splitting per table. Flagged during the spec review as deferred deliberately — refactor-before-X is a classic trap, and backtest runs are the right pressure to learn which schema choices actually hurt.

**The goal:** after one or two backtest runs have surfaced friction in result-summarisation and cross-run analytics, propose and execute a focused persistence refresh that addresses what backtest readers actually need — not speculative cleanup.

**Key questions:**
- Which JOINs does backtest result-summarisation actually need? (Probably `evidence` ↔ `ticker_stance` ↔ `decision` ↔ resulting `fill`.) Those become the FK candidates.
- Should `recorded_at` / `timestamp` / `opened_at` collapse to a single canonical name? Worth the churn?
- Alembic adoption: justified pre-deployment, given fresh-DB-per-run for free? Probably defer until live is on the horizon.
- File layout: keep `persistence.py` monolithic, or split per table?
- Pre-deployment means no live data to migrate, so any breaking schema change is safe — re-runnable backtests are the only consumer.

**Dependencies:** Backtest harness must have produced at least one full run so the refactor has empirical guidance instead of speculation. No code-level dependency beyond that.

---

## How segments interact

```
Phase 4 (Goals 1 + 2 — strategist v2 + analyst contract, plans A→B→C→D)
   │
   ├── Phase 5 (analyst re-categorisation: 5 analysts, deterministic-first baseline)
   │     │     spec: docs/Phase5-analyst-refine/spec.md
   │     │     plan: docs/Phase5-analyst-refine/plan.md
   │     │
   │     ├── analyst-surface-redesign (input split + reports + cache) ─┐
   │     │     consolidates B9 + B14 (LLM-analyst prose half).         │
   │     │     spec: docs/superpowers/specs/analyst-surface-redesign-design.md
   │     │                                                              │
   │     ├── B9  (sparse-execution gate)  — consolidated into above ───┤── often co-developed
   │     ├── B10 (narrative analyst — 13D/Form4)                       │
   │     ├── B11 (RAG / retrieval substrate)                          ─┤
   │     ├── B18 (cross-tick analyst memory — degenerate self-RAG)    ─┘
   │     └── B16 (LLM augmentation ratchet — policy anchor; gates B12/B13/B14 + future LLM hops)
   │
   ├── TradingAgents-inspired explorations (all trace-justified via B16)
   │     ├── B12 (Bull/Bear directional debate over the analyst pack)
   │     ├── B13 (three-perspective risk debate — sizing)
   │     └── B14 (per-stock per-analyst prose reports — LLM half consolidated;
   │              deterministic-narrator variant remains here)
   │
   ├── B15 (market-regime analyst — provider-gated, independent)
   │
   ├── B5  (per-evidence weighting) ─┐
   ├── B17 (deterministic confidence calibration) ─┤── adjacent, may fold into B2
   │                                 │
   ├── Goal 3 = B2 (knowledge base, long arc) ─┼── (B5 + B17 are outputs of B2)
   │                                 │
   │                                 └── B8 (replay tooling — validates B2's experiments)
   │
   ├── B3 (sub-tick exit)        — independent, any time
   ├── B4 (trailing stops)       — small extension on top of v2
   ├── B6 (risk clamp persistence) — small follow-up
   ├── B7 (cost observability)   — independent, low priority but feeds B2
   └── B24 (persistence schema refresh — after first backtest runs; depends on backtest harness completing)
```

**Rough order if doing them in series:** Phase 4 plans A → B → C → D → Phase 5 (analyst re-categorisation) → B16 (ratchet policy operationalised by Phase 5's surface trace) → analyst-surface-redesign (consolidates B9 + half of B14) → B6 → B7 → B11 → B18 (co-specced with B11) → B10 → B2 (long arc) → B5 → B17 (likely folds into B2) → B4 → B3 → B8. B12/B13/B14-deterministic-narrator/B15 fold in only as trace data justifies, ordered ad-hoc against [[B16]]'s checklist.

Most are independent enough to reorder by what hurts most in operation. Two strict orderings hold: **Phase 4 before B2** (the knowledge base needs a clean signal contract and decision telemetry to reason over) and **Phase 5 before B9/B10/B11/B12/B13/B14** (every analyst-side and debate-side experiment assumes the post-Phase 5 5-analyst pack, deterministic baseline, and surface-trace harness).
