# Phase 12 — Analyst predictive-power scoreboard

## Problem

The iteration-3 audit of the `baseline-2025-09` window
(`docs/audits/backtest-audits/baseline-window-2025-09-iter-3.md`)
reached an uncomfortable headline: **all three analyst signals —
deterministic technical, LLM fundamental, LLM news — are poorly
calibrated, and two are outright anti-predictive** in that window. The
bot still booked +3.36 pp of matched-exposure alpha, but the audit
traced that to the *strategist's* entry selection reading the full
analyst narratives, **not** to the mechanical lean tokens the analysts
emit.

That conclusion was reached by hand: a one-off join of 1 200
analyst verdicts against realised forward returns, built specifically
for the audit and then discarded. We have no repeatable instrument. We
cannot answer "did fixing bug #21 make fundamental predictive?" without
re-doing the manual join, and we cannot compare predictive power across
windows (bull vs stress) on a like-for-like basis.

This phase builds that instrument: a **repeatable, baseline-corrected
signal-quality metric**, generated automatically by every backtest and
comparable across windows.

### What this phase is NOT

It is explicitly **not** an attempt to *fix* the anti-predictiveness yet.
The audit is emphatic on two points that make a fix premature:

1. **Several "anti-predictive" findings are mechanical bugs, not
   reasoning failures** — `vol_ratio` → `"nanx"` (#20), the missing
   fundamental bullish branch (#21), the GOOGL/CRM revenue mismatch
   (#26, fixed on branch `fix/iter3-audit-fixes`). A model or prompt
   change fixes none of these and would *confound* any comparison.
2. **It is a single six-week bull window** (baseline mean fwd_20d
   ≈ +4.42 %). The audit states the anti-predictive leans "could be
   window artefacts and must be re-checked against a mixed/stress
   window (`iran-conflict-2026-02`) before being treated as
   structural."

The scoreboard's first job is therefore to answer **"is the
anti-predictiveness real?"** — measured *after* the mechanical bugs are
fixed and *across* at least one bull and one stress window. Only if the
deficit survives both does the downstream "model vs prompt vs context"
question become worth answering. That experiment (a frozen-context
single-analyst replay rig) is deliberately deferred to Phase 2 below; it
will score its candidates *against this scoreboard's metric*.

## Goals

- **Baseline-corrected signal quality.** Measure each analyst's
  predictive power as *excess* forward return — return relative to how
  the rest of the watchlist moved on the same tick — so the number
  reflects stock-selection skill, not market direction. A "bullish on
  everything" analyst must score ≈ 0 in a bull window, not brilliantly.
- **Automatic generation.** Produced by every backtest as part of the
  end-of-run report, with no manual step.
- **Cross-window comparability.** The headline is a *per-verdict mean*
  (in basis points), not a sum, so windows of different length compare
  directly.
- **Reproducible and cheaply iterable.** The scorer is a pure function
  over already-persisted data (`analyst_evidence` + the price cache).
  Changing a metric formula re-derives the scoreboard from an existing
  run's `db.sqlite` **without** re-running the backtest.
- **Signal vs noise.** Report a t-statistic so a real edge is
  distinguishable from bull-window noise (the audit's fwd_5d inversion
  was noise at p = 0.17; its fwd_20d inversion was real at p = 0.0018 —
  the scoreboard must let us tell those apart).

## Non-goals (v1 scope boundaries)

- **No use of Google ADK's eval framework.** ADK's `AgentEvaluator` /
  evalsets are *reference-based* — they check whether an agent emitted
  an expected response or tool-call against a golden transcript. We need
  *outcome-based* scoring — did the signal predict the market. The
  valuable artefact is the frozen verdict × forward-return join plus a
  custom market scorer, which ADK does not provide. Forcing ADK's eval
  layer in would be a square peg; we build a small pure function
  instead.
- **No confidence-calibration or magnitude-weighting in the score.**
  v1 is lean-only (direction), as the cheapest measure of predictive
  power. `magnitude` and `confidence` are already columns on
  `analyst_evidence`, so a calibration follow-up (does excess rise with
  confidence? the audit says it *falls*) is cheap later — but not v1.
- **No experiment rig.** The frozen-context single-analyst replay loop
  for A/B-ing models/prompts is Phase 2, gated on the scoreboard first
  showing the deficit survives bug-fixes and a stress window.
- **No new model-provider wiring.** Note for sequencing only:
  `config/models.json` drives Gemini through ADK's native client;
  LiteLLM is **not** wired, so a cross-provider swap has its own
  prerequisite build. Out of scope here; flagged so Phase 2 plans for
  it.

## The metric

For each verdict in `analyst_evidence` (one row per analyst × ticker ×
tick), and for each horizon *h* ∈ `forward_return_horizons_days`
(currently `{1, 5, 20}` — **calendar-day** offsets, matching the
existing `_backfill_forward_returns` convention, *not* trading days):

```
base_price    = the ticker's bar price at the verdict's tick, sampled at
                the tick's intraday phase — bar.open for open-phase ticks
                (recorded_at hour < 17 UTC), bar.close otherwise — read
                from the per-window golden cache.  Available for ALL rows,
                not just traded ones; this is what generalises the
                forward-return backfill from 35 traded decisions to all
                ~1 200 verdicts.  (NB: NOT the live state["reference_prices"]
                the technical analyst reads — the scoreboard runs post-hoc
                from db.sqlite + cache with no live state; and NOT
                features_json, which holds RSI/ATR-type features, not a
                clean price.)

fwd_return_h  = (forward_close_h − base_price) / base_price
                where forward_close_h = the close of the first available
                bar in [as_of_date + h days, as_of_date + h + 4 days] —
                the same calendar-day-offset, first-available-bar lookup
                _backfill_forward_returns already uses (extracted into a
                shared helper, not duplicated)

excess_h      = fwd_return_h
                − mean(fwd_return_h over all watchlist tickers that have
                       a forward return at that SAME tick and horizon)
                # per-tick cross-sectional demean: removes the day's
                # market-wide move, isolating selection skill

position      = +1 if lean == "bullish"
                −1 if lean == "bearish"
                 0 if lean == "neutral"

score_h       = position × excess_h
                # positive only when the lean was right RELATIVE to how
                # everything else moved that day
```

### Aggregation

Per analyst, per horizon, broken down by lean subset
`{all, bullish-only, bearish-only}` (the audit showed bullish-vs-bearish
edge diverges per analyst — e.g. fundamental's *bearish* calls carried
edge while it never emitted bullish):

- **mean excess (bps)** — `mean(score_h)` over the subset, in basis
  points. The headline; comparable across windows.
- **hit-rate** — fraction of non-neutral verdicts in the subset with
  `score_h > 0`.
- **n** — count of verdicts actually scored (coverage).
- **t-stat / p** — `mean(score_h) / standard_error(score_h)` and the
  associated two-sided p-value, to separate real edge from noise.

### Edge cases

- **Window-edge coverage.** Verdicts whose `tick_date + h` bar falls
  beyond the cache (the last ~20 trading days of a window have no +20d
  bar) are **excluded** from that horizon, and `n` reflects the reduced
  coverage. No silent truncation — the reduced `n` is visible in the
  output.
- **Neutral leans → score 0.** An inert, constantly-neutral analyst
  (e.g. fundamental, bug #21) scores exactly 0: no signal, no false
  credit, no false penalty.
- **Cross-sectional mean universe.** The per-tick mean is taken over
  exactly those watchlist tickers that have a non-null forward return at
  that (tick, horizon) — so the demean and the demeaned value use a
  consistent universe.

## Architecture

A single **pure scoring function** plus a thin entrypoint. No new
database tables; no new config (reuses the existing
`forward_return_horizons_days` setting).

```
src/backtest/
  scoreboard.py      # NEW — pure function:
                     #   build_analyst_scoreboard(db, cache, horizons)
                     #     → ScoreboardResult (per analyst × horizon × subset)
                     #   render_scoreboard_md(result) → str
  reporting.py       # MODIFIED — end-of-run, after _backfill_forward_returns:
                     #   call build_analyst_scoreboard(...) and write
                     #   report/analyst_scoreboard.md

scripts/
  backtest_scoreboard.py   # NEW — thin standalone entrypoint:
                           #   point at an existing run's db.sqlite +
                           #   its window cache, call the SAME pure
                           #   function, re-emit the scoreboard.
                           #   Lets us iterate on metric formulas with
                           #   zero re-backtest.
```

### Data flow

1. The backtest runs as today; `analyst_evidence` is persisted per tick
   (already happens) and the price cache is populated for the window
   (already happens).
2. At end-of-run, `reporting.py` already calls
   `_backfill_forward_returns` for traded decisions. Immediately after,
   it calls `build_analyst_scoreboard`, which:
   a. reads all `analyst_evidence` rows;
   b. for each row, looks up `base_price` (reference price at the tick)
      and the forward bars from the cache, computing `fwd_return_h`;
   c. computes per-tick cross-sectional means and demeans;
   d. aggregates per analyst × horizon × subset.
3. `render_scoreboard_md` formats the result; `reporting.py` writes it
   to `report/analyst_scoreboard.md`.
4. `scripts/backtest_scoreboard.py` is the same call path (2b–3) over an
   existing run, for formula iteration without re-running the pipeline.

### Why this shape

`analyst_evidence` and the price cache **already persist**, so the whole
eval is one function reading two things on disk. That keeps it lite
*and* reproducible at once: the standalone entrypoint exists only
because the verdicts are already on disk, so re-scoring is free. The
pure function is unit-testable on a small fixture `db.sqlite` + fixture
bars, with no pipeline, no LLM calls, no network.

## Implementation notes (resolved unknowns)

Verified against the codebase on 2026-06-15 so a subagent can implement
from this spec without re-discovering them:

- **`base_price` source — the cache, phase-matched.** `analyst_evidence`
  does **not** persist a usable price (`features_json` is RSI/ATR-type
  features), and the live `state["reference_prices"]` the technical
  analyst uses is gone post-hoc. So read it from the per-window
  `CachedDataStore.read_ohlcv(ticker, date, date)` and pick
  `bar.open if recorded_at.hour < 17 else bar.close` — the exact
  intraday-phase rule already used by the SPY benchmark in
  `reporting.py` (`_spy_benchmark_series`, ~L971-987). Phase-matching
  the base (rather than blunt close-to-close) also makes the open-phase
  and close-phase ticks of the same day **distinct** observations and
  removes same-day look-ahead at the base.

- **Forward price — reuse, don't duplicate.** `_backfill_forward_returns`
  (`reporting.py` ~L1079-1131) already does the
  `target = as_of_date + timedelta(days=h)` → `read_ohlcv(target,
  target + 4d)` → first-available `bar.close` lookup. Extract that
  per-`(ticker, base_date, h)` lookup into a shared helper
  (e.g. `_forward_close(cache, ticker, base_date, h)`) and call it from
  **both** the decision backfill and the scoreboard, so the two can
  never drift apart on methodology. This is an in-pass refactor of a
  sibling, not new surface.

- **Cross-sectional mean is analyst-independent — compute once per
  `(tick_id, h)`.** The forward return of a ticker at a tick is a market
  fact, not an analyst opinion. Group all verdicts by `tick_id`
  (persisted on every row), compute the mean forward return over the
  distinct tickers in that tick once per horizon, then every analyst's
  verdict at that tick demeans against the same value. `recorded_at`
  (a `DateTime` stamped from `state["as_of"]`) supplies both the date
  and the phase hour.

- **Reading the verdicts.** Query the run's `db.sqlite` via the existing
  `AnalystEvidenceRow` SQLAlchemy model (`src/orchestrator/persistence.py`
  L184) — columns `analyst, ticker, tick_id, recorded_at, lean,
  magnitude, confidence`. No raw SQL, no new table.

- **t-stat.** `scipy` (1.17.1) and `numpy` (2.4.4) are installed; use
  `scipy.stats.ttest_1samp(scores, 0.0)` per analyst × horizon × subset.

- **Missing bars / window edge.** When `read_ohlcv` yields no bar in the
  forward window (last ~h calendar days of the run), that
  `(verdict, horizon)` is dropped — excluded from both the cross-sectional
  mean and the aggregation, with `n` reflecting the reduced coverage.
  Same `None`-handling the backfill already applies.

- **Same-day open/close ticks.** With phase-matched base prices the two
  daily ticks are distinct observations (open→close vs close→close
  returns), so no de-duplication is needed and the t-stat sample is not
  artificially inflated.

## Testing

- **Pure-function unit tests** on a fixture dataset with hand-computed
  expected scores:
  - a bullish lean on a ticker that beat its peers → positive score;
  - a bullish lean in a rising-but-lagging-peers tick → negative excess
    despite positive raw return (the core baseline-correction assertion
    — proves we measure selection, not market direction);
  - a neutral lean → score exactly 0;
  - a verdict at the window edge with no +20d bar → excluded from the
    20d horizon, `n` decremented, present in the 1d/5d horizons;
  - the per-tick cross-sectional mean over a known 3-ticker tick.
- **Assert positive signal, not just "it ran."** Per the project's
  silent-failure policy, tests assert specific expected score values and
  coverage counts, not merely that a scoreboard was produced.
- **Render test**: the markdown contains a row per analyst × horizon ×
  subset with the expected `n`.

## Sequencing (how this phase is used)

1. Land the mechanical-bug fixes (#20, #21, #26) — partly done on
   `fix/iter3-audit-fixes`.
2. Build this scoreboard.
3. Re-run `baseline-2025-09` clean and read the scoreboard: did the
   bug-fixes move the analysts toward predictive?
4. Run the scoreboard on `iran-conflict-2026-02` (stress window): does
   any anti-predictiveness survive a non-bull regime?
5. **Only if a deficit survives both** → Phase 2 experiment rig to test
   model / prompt / context changes, scored against this metric.

## Phase 2 (deferred — not part of this spec)

A frozen-context single-analyst replay harness: freeze
`(input_context, forward-return label)` for N ticker-ticks, replay one
analyst against the frozen context under a candidate model / prompt /
context-shaping change, and score the new lean with this phase's metric
— enabling A/B iteration without an ~11 M-token full backtest. Gated on
this phase's findings. Will also need the LiteLLM wiring noted above if
a cross-provider model is in the candidate set.
