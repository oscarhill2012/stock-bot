# Analyst lean recalibration & anchored horizons

**Status:** draft for discussion — nothing here is agreed until both of us sign off.
**Context:** follows the strategist inverted-signal diagnosis
(`docs/Phase14-analyst-refactor/strategist-inverted-signal-diagnosis.md`) and the
three per-analyst investigations (2026-07-21). Root cause of the `first-month-5`
loss: two of three analysts are contrarian by construction and the news edge is
dropped on the floor within one tick. This spec recalibrates all three analyst
signals and makes the digest aggregate load-bearing (rendered, persisted,
scored); strategist prompt-posture changes (the "cash is bearish" deployment
pressure) are deliberately out of scope and sequenced after.

---

## Principles

- **P1 — Leans are functions of current data.** No analyst reads back its own
  prior verdicts. Persistence comes from slow-moving inputs (a 200d MA embeds
  200 days of memory), not from stored state. Sole exception: the news
  last-fire record (P2), because a catalyst article self-hides in the dedup
  store on the tick after it fires.

- **P2 — Every directional lean carries a data-derived anchor date.** Horizons
  count down from the anchor, never from the tick. Rendered as
  `anchored to <event> on <date>, day N of ~H` so the strategist can
  distinguish "same signal, ageing" from "new signal, born today". Anchors:
  technical = last 200d-MA state flip (from the price series); fundamental =
  release date of the periodic filing the delta was computed against; news =
  catalyst article date (from the last-fire record).

- **P3 — Sign flips require a new anchor.** A lean's sign may only change when
  its anchor event changes (new filing lands, MA state crosses, fresh catalyst
  fires). Between anchors, recomputation may modulate magnitude/confidence
  only. Flips therefore always arrive with a dated, inspectable cause.

- **P4 — An abstain is not a vote.** "No view" must be excluded from the
  digest aggregate and disagreement maths, not averaged in as a neutral.

- **P5 — Magnitude is calibrated to evidence strength.** A statistical tilt
  worth tens of basis points per month (Lazy Prices) must not render at the
  same magnitude scale as a fresh material catalyst.

- **P6 — The combine is deterministic, rendered, and scored.** The strategist
  does not free-hand the 3-way weighting: the digest aggregate is computed
  with config weights, shown in the prompt as the anchor (with licence to
  disagree), persisted every tick, and scored on the scoreboard like an
  analyst — so "is the aggregator providing value?" stays a measurable
  question, not a debate.

---

## Technical — replace the reversal lean with a trend/momentum composite

Current behaviour (confirmed against the run DB): lean is a pure sign-flip of
`pct_change_5d`; ~75–85% of directional calls fight the trend context rendered
beside them; the lean flipped ~6.8×/ticker over the month while the 200d trend
state flipped ~0.8×.

**New deterministic lean** (all inputs already computed by
`extract_technical_features`; change contained to `derive_technical_verdict`
in `src/contract/extractors/technical.py` + config):

| Component | Vote | Weight (config) |
|---|---|---|
| Trend vs 200d MA (golden/death cross corroborates) | +1 above / −1 below | 0.50 |
| 52w anchor (within `near_52w_extreme_pct` of high/low) | +1 near high / −1 near low | 0.25 |
| Relative strength 20d vs SPY (sector as tiebreak) | sign | 0.25 |

- `score = Σ w·vote`; lean = sign(score) outside a neutral band; magnitude
  scaled from |trend_state| and |rel_20d|, capped.
- Confidence = component agreement (3/3 high, 2/3 moderate) × volatility
  damping `1/(1 + max(0, vol_regime_z))` — `vol_regime_z` stops being a
  decorative tag (Barroso & Santa-Clara 2015).
- RSI: rendered context only; at most a magnitude damper at extremes; never a
  sign input. `pct_change_5d`: rendered context only, excluded from the lean
  (the 12-2 momentum convention treats it as reversal-contaminated noise).
- Horizon: `technical.horizon_days = 60` (replaces `reversal_horizon_days`),
  literature-informed starting value; scoreboard forward-return sweep decides
  the final number. Anchor: date of last 200d-MA state flip, rendered as
  "above 200d MA since <date> (<n> sessions)".
- New tag vocabulary: `trend_follow_up/down`, `anchor_52w_high/low`,
  `rel_strength_confirm/diverge` (replaces `reversal_up_fade`,
  `reversal_down_bounce`). Old reversal config keys are deleted, not left
  dangling.

Evidence base: Moskowitz/Ooi/Pedersen 2012 (TSMOM, 1–12m persistence);
George & Hwang 2004 (52w-high anchoring dominates past-return momentum, no
long-run reversal); Faber 2007 / Brock et al. 1992 (200d MA state);
Barroso & Santa-Clara 2015 (vol-scaled momentum).

## Fundamental — recalibrate the Lazy Prices doctrine to what the paper supports

Current behaviour: 71% of all rows bearish; STLD/FSLR bearish 29/29 sessions;
quality metrics demoted to "corroborate/qualify" and unable to rebut any
filing-language change.

Deviations from the paper, and the corrections:

1. **Trigger rarity.** Paper "changers" are the cross-sectional bottom
   quintile (~20% of names). Correction: the filing-delta lean only fires on
   genuinely large deltas — threshold on the existing `filing_similarity`
   score (approximating the quintile cut on our watchlist); mid-range deltas
   are neutral/no-signal.
2. **Magnitude cap.** The L/S alpha is 18–58bps/month portfolio-level; per-name
   it is a weak tilt. Correction: cap filing-delta-driven magnitude (e.g.
   ≤0.4) absent a going-concern-tier catalyst; prompt instruction plus a
   deterministic clamp at the extractor so the LLM cannot exceed it.
3. **Sentiment-signed, not bearish-by-default.** The paper's 14% positive-
   sentiment changes predict significantly *positive* returns. Correction:
   replace "change is bearish unless unambiguously positive" with
   sentiment-signing of the delta; drop the super-majority test for bullish.
4. **Anchored horizon (P2/P3).** Anchor = release date of the periodic filing
   the delta was computed against; drift window 90d from *filing*, magnitude
   decaying toward neutral past exhaustion. Sign pinned to the anchor:
   non-cache-hit re-digests (routine 8-Ks, insider prints, ratio drift)
   modulate magnitude/confidence only. New anchor → sign may change, visibly.
   **Anchor events are: periodic filings (10-K/10-Q) plus a short config list
   of thesis-breaking 8-K event types** (e.g. CEO/CFO departure, guidance
   withdrawal, going-concern language) that re-anchor the clock mid-quarter.
   *Decision 2026-07-21:* agreed. The 8-K list lives in `config/analysts.json`
   and the code at the anchor-classification site must carry a comment marking
   it as a **high-value tuning knob** — widening it re-admits sign churn,
   narrowing it delays reaction to real thesis breaks.
5. **Long-only honesty.** The durable Lazy Prices alpha is the short leg; in a
   long-only book the signal's main job is *avoid/underweight changers*, and
   its bullish side (non-changers) is weak and fast-reverting — bullish
   filing-delta calls stay low-magnitude.

Files: `src/agents/analysts/fundamental/prompts.py` (doctrine rewrite),
`src/contract/extractors/fundamental.py` (clamp + anchor/decay rendering),
`config/analysts.json` (similarity threshold, magnitude cap, decay) +
`config/README.md`.

## News — persist the fire, stop counting the abstain

Current behaviour: the prompt promises "a separate downstream process manages
the multi-day hold" — no such process exists. A fired signal self-zeroes next
tick; the STLD trade was opened on a catalyst and closed one tick later when
the reset was misread as the catalyst fading.

1. **Last-fire record (the missing downstream process).** On every directional
   news verdict, persist `{lean, magnitude, confidence, fired_at}` per ticker
   (single record, overwritten on next fire; reset per backtest window,
   matching `NewsHistoryStore` discipline; all datetimes ISO-stringified per
   the `as_of` state convention). On subsequent abstain ticks the strategist
   sees: "no fresh surprise today — prior bullish catalyst from <date>
   (day N/20), decayed magnitude ≈ X" with linear decay over
   `drift_horizon_days`.
2. **Abstain ≠ neutral vote (P4).** STEP-3 abstains currently emit
   `is_no_data=False` and enter the digest as a real neutral (weight 1.0,
   sign 0). Change: abstains are marked (reuse `is_no_data` or a distinct
   `abstain` flag) and excluded from `_weighted_signed_confidences`,
   mean-confidence, disagreement, and the "N bullish / N neutral / N bearish"
   summary line. Where a live last-fire record exists, its decayed value may
   stand in; otherwise news simply isn't a vote that tick.

Files: `src/agents/analysts/news/joiner.py` (persist),
`src/agents/strategist/context_shim.py` (read/inject),
`src/contract/strategist_prompt.py` (decayed render),
`src/contract/digest.py` (abstain exclusion), `src/contract/evidence.py`
(flag semantics), `src/agents/analysts/news/prompts.py` (STEP-3 wording).

*Decision 2026-07-21:* the carried signal feeds the aggregate **numerically**,
not as prose alone. Mechanism: upstream synthesis — `context_shim.py` fetches
the last-fire record and substitutes the abstain with a synthetic decayed
verdict flagged `carried` *before* calling the digest, so `digest.py` itself
stays a pure function of the verdicts handed to it. Rationale: the diagnosis
showed prose loses to computed numbers (the "let winners run" prose lost to
the quantified cash-drag nag every tick); a prose-only carry would ask the
LLM to overrule a 2–0 bearish arithmetic with a sentence. Numeric carry makes
the STLD failure impossible by construction — the aggregate decays smoothly
(0.70 → 0.66 → … → 0 across the window) instead of cliff-dropping one tick
after the catalyst. The `carried` flag preserves provenance in the render and
the run DB; a new fire overwrites the record; the decay rate/horizon is a
**high-value tuning knob** (comment required at the synthesis site).

## Digest aggregate — make it load-bearing, and fix its silent persistence bug

Findings (2026-07-21): the aggregate computed by `src/contract/digest.py` is
currently **write-only on every path** — never rendered in the strategist
prompt (the instruction at `src/agents/strategist/prompts.py:198` references a
number no template prints), and never persisted: the pipeline runs the
evidence writer *before* the strategist (`src/orchestrator/pipeline.py`), but
`temp:ticker_evidence_objects` is only produced by the strategist's context
shim, so the writer's `state.get(..., []) or []` silently wrote **zero**
`ticker_evidence` rows for the entire `first-month-5` run (while the same
writer's 1740 `analyst_evidence` rows masked the gap). Another instance of
the silent-degradation bug class.

*Decision 2026-07-21:* keep the aggregate and make it load-bearing rather
than delete it. The run was effectively the no-aggregate experiment — the
strategist free-handed the combine and produced inverted ceiling-weight
conviction; a config-weighted deterministic combine is auditable, tunable,
and (once persisted) scoreable. Changes:

1. **Fix persistence (straight bug fix — can land ahead of the rest).** Move
   the `TickerEvidence` persistence loop out of the pre-strategist
   `agents/contract/evidence_writer.py` into
   `agents/strategist/decision_writer.py`, which already runs post-strategist.
   Per the loud-failure convention, an empty `ticker_evidence` list at write
   time should raise, not no-op.
2. **Render the aggregate per ticker** in `strategist_prompt.py` — lean,
   magnitude, confidence, disagreement, and the `carried` provenance where a
   synthetic news verdict contributed — making the dangling
   "treat the digested aggregate as a deterministic input" instruction true.
   The existing "you may disagree with it" licence stays (guard-rail against
   over-anchoring).
3. **Score it.** Add the aggregate as a pseudo-analyst row on the backtest
   scoreboard, and track the stance-vs-aggregate agreement rate per run. If
   the strategist agrees ~100% of the time, the LLM layer is redundant above
   the combine — a finding to act on, either way, with numbers.
4. Aggregate weights (`DEFAULT_ANALYST_WEIGHTS`) promote to `config/`
   (`digest.json` per the loader note already in `digest.py`) + a
   `config/README.md` entry — they are the natural target of scoreboard-driven
   tuning and a **high-value tuning knob** (comment required).

---

## Validation

- Re-run the first month of `long-baseline-2025`; before/after on the named
  cases: MPWR (expect bullish technical, capped fundamental, news fires
  persisting), FSLR/STLD (no more 29/29 bearish fundamental; STLD-style
  one-tick round-trip must not recur), DOV/FDS (no more bounce/knife buys).
- Churn metrics before/after: lean flips per ticker, round-trip count, median
  holding period, decision_tag distribution.
- `ticker_evidence` row count must equal tickers × ticks after the persistence
  fix (a positive-signal assertion, not just "no error").
- Scoreboard forward-return sweep to empirically place `technical.horizon_days`
  (20/40/60/90) once the new lean exists; aggregate pseudo-analyst scored in
  the same pass, plus the stance-vs-aggregate agreement rate.
- Watchlist weakness (−1.70% equal-weight vs SPY +5.8%) is a separate track —
  selection alpha vs the watchlist is the success metric here, not absolute
  return.

## Out of scope (sequenced next)

- Strategist deployment posture ("cash is an active bearish allocation")
  softening — interacts with this package and is measured after it lands.
  (Aggregate rendering was originally deferred here too; pulled into scope
  2026-07-21 — see the digest section.)
- Thesis-memory / horizon-holding machinery (old lever "C") — deliberately
  deferred; anchors + sticky signals are expected to remove most of its
  motivation. Revisit only if churn metrics say otherwise.
- Watchlist construction.
