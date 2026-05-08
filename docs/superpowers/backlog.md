# Future Development Backlog

A living index of work explicitly deferred during prior brainstorming sessions. Each segment below is sized to be roughly one future brainstorming session — pick one up via `/superpowers:brainstorming`, re-read the spec it was deferred from, then proceed.

When a segment is specced, move it from this file into a real spec under `docs/superpowers/specs/`. When new ideas surface from operating the bot, add them here.

---

## Strategist Roadmap (the spine)

The strategist is being grown in three goals. Items below are tagged with which goal they belong to.

- **Goal 1 — Strategist v2.** *In flight.* Spec: `docs/superpowers/specs/strategist-v2-design.md`, plan: `docs/superpowers/plans/strategist-v2.md`. Single LLM strategist, per-ticker stance output, knows about its own held positions (rendered thesis + live state), lifecycle is derived not declared. Substrate from the dropped council/exit-rules specs (lifecycle validation, telemetry tables, `opening_tick_id`/`closing_tick_id` FKs) was absorbed into v2.
- **Goal 2 — Analyst → Strategist contract.** *Next brainstorm.* Make the analyst surface predictable, weighted, and structured enough that the strategist (and later, the knowledge base) can reason over it without bespoke parsing per analyst.
- **Goal 3 — Knowledge base / self-improvement.** *Long arc.* The strategist learns from its own outcomes. The user's framing: "save the signal, not the trade." Stock-agnostic pattern recall, not a vector DB of past trades. Needs Goal 1 telemetry shipped + weeks of paper data before it can be designed concretely.

A few items previously in this backlog (council debate, persona memory, persona model diversity) are gone — they assumed the council architecture, which v2 dropped. If multi-LLM deliberation ever comes back, it'll be a fresh design conversation gated on Goal 3 outcome data.

---

## Tier 1 — Major (likely become full specs)

### B1. Analyst → Strategist contract  *(Goal 2 — next up)*

**Origin:** Identified during the strategist v2 retrospective. v2 fixes the strategist's *internal* legibility (per-ticker stance, lifecycle derivation, held-position context) but leaves the *input* surface unchanged. Today the strategist sees a heterogeneous bag of `AnalystSignal` objects with free-form `evidence` dicts and a single per-family weight in `ANALYST_WEIGHTS`. The strategist re-derives meaning each tick.

**The goal in plain English:** the strategist should consume a clean, schema-stable signal surface where each analyst's evidence has known keys, each evidence key carries an explicit "this is what bullish/bearish means here" interpretation, and weights are applied consistently. After this work, plugging in a new analyst or reweighting an existing one is a config change, not a prompt change.

**Scope hints (for the brainstorm):**
- Audit current analysts (technical, fundamental, sentiment, smart_money) — what does each emit as `evidence`? Where does `SmartMoneySignal` diverge from the base `AnalystSignal`? Should they unify?
- Decide whether evidence becomes a typed schema per analyst (Pydantic subclasses) or stays a dict with documented keys.
- Decide where weighting lives: in the prompt as verbal trust hints, in a numerical aggregation step before the prompt, or in a new "signal pre-digest" agent.
- `ANALYST_WEIGHTS` today is per-family. Does v2 of the contract still hand the strategist 4 separate signals, or does a pre-digest collapse them into one structured "market read" object?
- How does this contract make Goal 3 (knowledge base) easier — i.e., what does an "outcome-attributed signal" look like once we want to ask "did this evidence shape predict the outcome?"

**Key questions to brainstorm:**
- What's the minimum schema we can lock in now without painting ourselves into a corner before the knowledge base exists?
- Is there a "signal pre-digest" stage worth adding to the pipeline, or does the strategist consume raw signals directly under a tighter contract?
- How should we handle analysts that legitimately disagree (e.g., fundamentals bullish, sentiment bearish)? Today that's the strategist's problem; should the contract give it a structured way to see the disagreement?
- Per-analyst confidence calibration: are confidences across analysts comparable today? (Almost certainly not.) How do we make them comparable enough to weight?

**Dependencies:** Goal 1 (strategist v2) shipped, so we have a stable consumer with telemetry to validate against.

---

### B2. Knowledge base — design the learning loop  *(Goal 3 — long arc, design only)*

**Origin:** The user's explicit Goal 3 framing: the bot should learn from outcomes. "We make money because we notice signals that infer we can earn money. Save the signal, not the trade." Stock-agnostic pattern recall, not a vector DB of past trades.

**Substrate already in place after Goal 1:**
- `TickerStanceRow` — per-ticker strategist stance per tick (rationale, conviction, lifecycle, evidence_refs).
- `StrategistDecisionRow` — final tick decisions with full metadata.
- `TradeLogRow.opening_tick_id` / `closing_tick_id` — outcome attribution joins back to the tick that opened/closed each position.
- `AnalystSignal.evidence` — structured numerics across all analysts (cleaner once Goal 2 lands).
- `PositionThesis` rendered into prompts — the strategist's stated *why* is now persisted alongside the *what*.

**The goal in plain English:** when the strategist is about to act on signal pattern X, it should know "the last N times we saw something shaped like X, here's what happened." Not "the last time we bought AAPL," but "the last time technicals looked oversold while smart-money inflows were trending positive."

**Key questions to brainstorm:**
- What does "stock-agnostic signal pattern" actually mean as a lookup primitive? Embedding of the analyst evidence vector? Cluster of evidence shapes? Discretised feature buckets? Hand-coded archetypes?
- Cold-start: how many ticks of paper data before the loop has anything to say? What does the strategist do *before* that threshold — operate identically to v2?
- What does the loop *do* once it has learned something? Re-bias `ANALYST_WEIGHTS`? Inject context into the strategist prompt ("similar setups historically resolved bearishly")? Veto certain decision-tag patterns?
- How do we avoid overfitting to a tiny paper-trading sample, especially since paper conditions are rosier than live?
- Storage shape: separate "lessons" table? Annotated `TickerStanceRow`? A side index that maps signal-pattern → outcome statistics?
- Read path vs write path: when does the loop *learn* (between ticks? batched nightly?) vs *get consulted* (every tick? only on novel patterns?)?

**Dependencies:** Goal 1 shipped + Goal 2 contract stable + ~weeks of paper-trading data accumulated. This brainstorm is design-only until that data exists; jumping to implementation early risks designing for an imaginary distribution.

**Likely outcome of the brainstorm:** decompose Goal 3 into sub-projects (e.g., "outcome attribution table," "signal pattern primitive," "lookup → prompt injection," "weight learning"). Each becomes its own spec.

---

## Tier 2 — Medium enhancements

### B3. Real-time / sub-tick exit evaluation

**Origin:** Carried over from the original exit-rules spec. v2 evaluates exits only at hourly tick boundaries — if AAPL crashes at 14:23, we won't notice until 15:00.

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

**Dependencies:** Goal 1 shipped.

---

### B5. Per-evidence-key analyst weighting

**Origin:** Goal 2 will land per-family weights. This is the next refinement.

**The goal:** instead of "trust smart_money 1.5×", learn that "smart_money's `n_politicians > 2` is highly predictive but `total_dollar_value` alone is noise."

**Key questions:**
- Storage: extend the Goal 2 contract to a nested `{analyst: {key: weight}}`? Or a separate `EVIDENCE_WEIGHTS` config layered on top?
- Override mechanism: can a learned weighting shadow the hand-set defaults, or must they merge?
- Where does the weighting apply: in the strategist prompt (verbal "trust this more"), in a pre-digest aggregator (mathematical reweighting), or both?

**Dependencies:** Goal 2 (analyst contract) shipped. Strongly coupled to Goal 3 (knowledge base) — this is one of the things that loop should learn rather than have hand-tuned.

---

## Tier 3 — Small follow-ups & easy wins

### B6. Persist `risk_clamps_applied`

**Origin:** Carried over from the original exit-rules spec; not absorbed into v2.

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

**Dependencies:** Goal 1 telemetry shipped (so there's something to replay). Cleaner once Goal 3 is being designed, since validating its experiments is the main use case.

---

## How segments interact

```
Goal 1 (strategist v2, in flight)
   │
   ├── Goal 2 = B1 (analyst contract)
   │       │
   │       └── B5 (per-evidence weighting)   ─┐
   │                                          │
   ├── Goal 3 = B2 (knowledge base, long arc)─┼── (B5 is one of B2's outputs)
   │                                          │
   │                                          └── B8 (replay tooling — validates B2's experiments)
   │
   ├── B3 (sub-tick exit)        — independent, any time
   ├── B4 (trailing stops)       — small extension on top of v2
   ├── B6 (risk clamp persistence) — small follow-up
   └── B7 (cost observability)   — independent, low priority but feeds B2
```

**Rough order if doing them in series:** B1 → B6 → B7 → B2 (long arc) → B5 → B4 → B3 → B8.

Most are independent enough to reorder by what hurts most in operation. The one strict ordering is **B1 before B2**: the knowledge base needs a clean signal contract to reason over.
