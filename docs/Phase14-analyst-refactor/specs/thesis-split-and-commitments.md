# Thesis split & derived commitments

**Status:** settled — every decision below is marked *Decision 2026-08-12* and
is agreed. Implementation follows in
`docs/Phase14-analyst-refactor/plans/plan3d-thesis-split-and-commitments.md`.

**Context:** follows the `first-month-6` diagnosis. Plan 3c landed the analyst
recalibration and the digest aggregate; the analysts now carry dated horizons
(technical 60d, news 20d, fundamental 90d, verified 600/600 in the run). The
`first-month-6` result is that the signal layer improved and the *holding*
layer did not convert it: total return +0.82% against a matched-exposure SPY
of +1.70% (IR −2.92), win rate 22.2%, median hold **6 sessions** against
20/60/90-session signals, and realised P&L across all 9 closed round-trips of
**−$1,196** while the run's +$820 came entirely from positions never closed.

This spec is the "old lever C" (thesis-memory / horizon-holding machinery)
that `analyst-lean-recalibration.md` deliberately deferred with the note
*"revisit only if churn metrics say otherwise"*. The churn metrics say
otherwise.

---

## What the run showed

Findings from `backtests/long-baseline-2025/runs/first-month-6` (30 ticks,
20 tickers, 600 thesis rows):

1. **The book is mostly not a position book.** 63% of the 600 rendered thesis
   rows are `[NO POSITION]`. 221 rows (37% of the entire book) are *bearish*
   theses on tickers the bot cannot short — `risk_gate/constraints.py:83`
   clamps every short to zero, so those theses are unfalsifiable by
   construction and can never be acted on. A further 79 rows (13%) are
   bullish-with-no-position: conviction the strategist declined to execute.
   95 of the 121 `update` verbs in the run (79%) were spent writing prose for
   tickers the bot did not hold.

2. **Nothing in the prompt records a commitment.** The strategist is not
   horizon-blind about *signals* — `strategist_prompt.py:733-736` renders
   `horizon: ~{H}d` correctly on every verdict, every tick. It is horizon-blind
   about *positions*: no elapsed count, no entry horizon, and no denominator.
   The single clause that was written to bridge the two —
   `strategist_prompt.py:741-750`, commented as *"the churn root-cause fix: the
   strategist was horizon-blind"* — **renders zero times in 600 rows**. It is
   gated on `ma200_flip_days`, which `extractors/technical.py:499` only emits
   with ≥200 bars; the cache holds 82 bars at window start. The extractor is
   correct, its unit tests pass, and the feature is absent in every real row.

3. **Consequently, 0 of 190 sampled rationales contain any temporal
   reasoning** — no elapsed days, no remaining horizon, no reference to how
   long a view has been held. The date is on line 14 of the prompt and
   `opened_at` is in the thesis block, so the arithmetic is available; the
   model simply has no denominator to divide by, because nothing in the prompt
   records what was committed to.

4. **The churn is in the prose, not the signals.** Measured over the run:

   | Object | Revisions per ticker over 30 ticks |
   |---|---|
   | Strategist prose `rationale` | **9.5** (one every ~3.2 ticks) |
   | Analyst direction — technical | 0.8 |
   | Analyst direction — news | 2.0 |
   | Analyst direction — fundamental | 0.2 |

   All three analysts together change direction 3.1×/ticker/month. A ledger
   derived from *verdicts* therefore churns at roughly a third of the rate of
   one derived from *prose* — which is what makes horizon-length commitments
   viable at all.

5. **A simulated commitment ledger is cheap.** Replaying the run's 41 buy
   events with one commitment per analyst, each ageing to its own horizon:
   mean **2.57** live commitments per ticker-tick (max 4), across 338
   ticker-ticks. Opposing-direction pairs occur on **2.1%** of ticker-ticks,
   and every occurrence was MPWR's 90-day fundamental — the worst case for
   keeping both legs, since a superseded bearish would squat in the prompt for
   another four months.

6. **A held position was invisible to the strategist for 21 ticks.** The
   2025-10-13 prompt states three different position counts on one screen —
   Mode says 8 live positions, `temp:portfolio_summary` says 9,
   `temp:deployment_readout` says 9 — and the thesis book's own weights sum to
   0.829 against the readout's 87%, while the prompt explicitly instructs the
   model to compute that sum itself. The cause is **DOV**:

   ```
   2025-09-05  buy    → position opened
   2025-09-22  sell   "Closing position as the extremely weak technicals…"
               broker: rejected —
               'sell 28.529340021135052 > held 28.52934002113505 of DOV'
   2025-09-23  update "Initiating a neutral thesis… warranting a wait-and-see
                       approach"
   2025-09-24 → 10-13  no_action × 21
   ```

   A float rounding error — the order quantity exceeds the held quantity by
   2×10⁻¹⁵ shares — so the broker rejected the close. **The thesis row was
   deleted regardless, because deletion is driven by the stance, not the
   fill.** `trade_log` holds no DOV row at all; the position persisted at
   ~4.6% of NAV to the end of the run while the strategist wrote watch-list
   prose about a name it owned.

7. **The prompt contains instructions the schema rejects.** `prompts.py:264`
   and `:270` tell the model to trim with `update` (smaller weight);
   `prompts.py:362` and `:368` state `update` takes no weight and the schema
   rejects it. Separately, `:396` conditions the output mix on
   `first_tick_flag`, a placeholder deliberately absent from the template
   (`context_shim.py:222`), and `:177` frames the decision horizon as "the
   next trading hour" against 20/60/90-day signals.

---

## Principles

- **T1 — One thought per ticker, one ledger of facts.** `rationale` is the
  only prose the strategist authors about a ticker, and it answers *why*.
  Everything time-related — entry date, weight, commitments, trims — is
  computed and answers *what* and *when*. Two competing prose views on one
  ticker is the failure this spec exists to remove; adding a second one would
  reproduce it.

- **T2 — Commitments are derived, never authored.** The LLM cannot write,
  extend, re-anchor or clear a commitment. They are a deterministic function
  of the analyst verdicts present at the moment a buy executed. This is what
  makes them trustworthy as a clock — the model cannot talk itself out of one.

- **T3 — A commitment records what was bought on; it is not a promise to
  hold.** Expiry is informational and never forces an order. The risk gate
  gains no new power from this spec.

- **T4 — Held and not-held are different objects with different budgets.**
  A position carries a full block. A view without exposure carries one line.
  The book stops being 63% prose about things the bot does not own.

- **T5 — Position lifecycle ≠ thesis lifecycle.** `opened_at` is frozen at
  first buy and stays frozen through adds and trims. A trim is a recorded
  fact, not a reset. Nothing the strategist does mid-life re-anchors a clock.

---

## Preconditions — land before the split

Two pre-existing defects (findings 6 and 7) must be fixed first. Neither is
thesis-design work, but the split makes both strictly worse, so they are inside
this plan's scope rather than deferred.

### P-A — the book must follow the fill, not the stance

*Decision 2026-08-12:* two changes, both required.

1. **Clamp the sell quantity to the held quantity** before the order leaves the
   executor. A rounding excess of 2×10⁻¹⁵ shares must not reject a close. The
   clamp is arithmetic, not tolerance-based: never emit a sell quantity greater
   than the broker's reported holding.
2. **Reconcile the position store against the broker every tick.** A ticker the
   broker holds with no row in `user:positions`, or a row with no corresponding
   holding, is a **loud failure** — raise, with both sides named. Row deletion
   on a full close must be conditional on the close actually executing.

Under the current single-book model a divergence degrades to a `[NO POSITION]`
row that at least still renders, which is how DOV survived twenty-one ticks
without anyone noticing. After the split there is no such landing zone: a
divergent ticker vanishes from `user:positions` entirely, carries no watch
note, and its commitments are destroyed while the position lives on. The
reconciliation raise is what makes the split safe.

### P-B — prompt-surface hygiene before the surface grows

*Decision 2026-08-12:* a hygiene sweep lands before any new prompt content.
The rendered prompt is currently **1,581 lines / 115KB**, of which the evidence
block is lines 110–1332 — **77%** — and every instruction on how to read that
evidence, plus the `## Your Job` section itself, sits *after* it. The model
reads 1,223 lines of data before it is told what the task is.

In scope for the sweep:

- **Contradictions:** the two `update`-can-trim instructions; the dangling
  `first_tick_flag` reference; "the next trading hour"; the manual
  sum-the-weights instruction that duplicates and disagrees with
  `temp:deployment_readout`; the cash-floor stanza's "full deployment is
  permitted" beside "Sum > 0.95 — over-deployed".
- **Dated prose:** the Thesis Book heading's "with evolution since the last
  revision" (nothing evolutionary is rendered); "the technical analyst **now**
  gives you three reads"; the developer-addressed maintenance note at
  `:204-207` telling a human to keep the prose in sync with config; the
  `:371-373` paragraph forbidding `target_price` / `stop_price` / `horizon` by
  first teaching all three; the `:213-218` prose describing the dead `anchor:`
  clause and calling technical's horizon "~60-trading-day".
- **Duplication and bloat:** 190 rendered lines whose only value is `0.0`
  (12% of the evidence block — `Cluster buy flag` ×20, `Conviction buy flag`
  ×20, `Conviction sell flag` ×20, `Derivative exercises` ×20,
  `Derivative grants` ×20, and others); every `horizon:` line stating its
  number twice; news carriage stated three times per carried ticker
  (`tags: carried`, the rationale prose, and `[news carried]` on the
  aggregate).
- **Ordering:** `## Your Job`, `## Reading analyst reports` and
  `## Reading the technical reads and analyst horizons` move **above** the
  evidence block. Task framing precedes data.

*Decision 2026-08-12:* the sweep is measured, not judged. Record rendered line
count and byte size before and after; the ordering change and zero-suppression
alone should take the prompt below 1,000 lines, which is the headroom the
commitment lines and watch-note block then spend.

---

## Split the book — `PositionThesis` vs `WatchNote`

Today `state["user:positions"]` holds one row per ticker the agent has formed a
view on, held or not, with position state encoded by whether the entry fields
are populated (`position_thesis.py:17-24`). Both kinds render the same
five-line block. The result is finding (1): the majority of the prompt's thesis
budget is spent on unowned tickers, a third of it on bearish views that are
structurally unactionable.

*Decision 2026-08-12:* split the single book into two stores with two
renderings.

- `state["user:positions"]` holds **only** rows with a live position.
  `PositionThesis.opened_at` / `opened_tick_id` / `opened_price` / `weight`
  become **required** (no longer `| None`) — the discriminator disappears
  because the store is the discriminator.
- `state["user:watch_notes"]` holds views without exposure:
  `WatchNote {ticker, note, last_reviewed_at, last_reviewed_decision}`.
  Rendered one line each under a `## Watch Notes` heading, ordered by ticker.

Verb semantics across the split (`agents/executor/_verb_dispatch.py`):

| Verb | No row | Watch note | Position |
|---|---|---|---|
| `buy` | seed position | **promote** — note discarded, buy rationale becomes `rationale` | add: refresh `rationale`, bump `weight`, `opened_*` untouched |
| `sell` | HALLUCINATED | HALLUCINATED | trim (weight, maybe a position event) or full close (row removed) |
| `update` | seed watch note | refresh note | refresh `rationale` |
| `no_action` | no-op | touch review trail | touch review trail |

*Decision 2026-08-12:* **a full close deletes the position row and does not
seed a watch note.** A reopened position starts clean — new `opened_at`, empty
commitments, empty events. This preserves today's behaviour
(`_verb_dispatch.py:214-219`) and is the honest reading: the bot exited, so it
has no view on the record until it writes one. If the strategist still has a
view after closing, it must spend an `update` to say so, which puts the
continued view in the audit trail rather than inheriting it silently.

*Decision 2026-08-12:* the one-line watch-note render is deliberately a
**budget**, not a formatting choice. The concern that watch notes are "the sort
of stuff we get the strategist stuck doing" is met by making them cheap to
carry and cheap to ignore, not by deleting them. Reconstructing the run's
`user:positions` deltas tick by tick gives 18 position opens, of which **10
were promoted from an existing no-position row** and 8 arrived cold (plus 10
adds to live positions). The majority of entries do come through this surface,
so deleting it would remove a real path to a trade. `note` is capped by a new
`config/strategist.json → position_thesis_caps.watch_note_max_chars` (200).

---

## Commitments — a derived clock per position

*Decision 2026-08-12:* every `PositionThesis` carries
`commitments: list[Commitment]`, holding **at most one entry per analyst**
(maximum three), written by the executor on every `buy`.

```
Commitment
  analyst             Literal["technical", "news", "fundamental"]
  lean                Literal["bullish", "bearish"]
  horizon_days        int                 # the analyst's own horizon at anchor time
  anchored_at         datetime            # UTC, ISO-stringified into ADK state
  anchored_tick_id    str
  contradicted_since  datetime | None
```

**Source.** The three canonical `*_verdicts` `VerdictBatch` state keys
(`technical_verdicts`, `news_verdicts`, `fundamental_verdicts`), read at
executor time. Not `temp:ticker_evidence_objects` — that key is produced by the
strategist's context shim and is a `temp:` handle; the canonical batches are
plain state keys already published upstream of the executor by the joiners.
`horizon_days` comes off the verdict itself, so a later config change to a
horizon does not retroactively rewrite live commitments.

**Write rules**, applied per analyst on every `buy` (entry or add):

| Verdict at buy time | Existing commitment | Action |
|---|---|---|
| directional | none | create, `anchored_at = as_of`, `contradicted_since = None` |
| directional, same lean | present | refresh `anchored_at` to now; clear `contradicted_since` |
| directional, opposite lean | present | **leave `lean` / `horizon_days` / `anchored_at` untouched**; set `contradicted_since = as_of` if not already set |
| neutral, abstain, or no-data | any | no change — neither creates nor contradicts |

*Decision 2026-08-12:* an opposing verdict **flags** the existing commitment
rather than replacing it or adding a second row. Considered and rejected:
(a) *refresh in place* — overwrites the anchor and destroys exactly the reading
that makes the ledger useful ("we bought a 20-day news drift 5 sessions ago and
it has 15 left, so plan the exit inside that window" becomes "news is bearish,
sell now"); (b) *keep both legs until each expires* — preserves the reading but
the simulation shows the only place it fires in a real month is the 90-day
fundamental, where a superseded bearish would sit in the prompt contradicting a
bullish anchored three sessions later, for another 85 sessions, with no
mechanism to resolve it. The flag gives the same timeline in one row, caps the
ledger at three rows per position permanently, and self-clears at the original
expiry.

**Ageing and expiry.** A commitment is live while
`(as_of - anchored_at).days < horizon_days`. Elapsed is counted in **calendar
days**. Expired commitments are pruned at render *and* at the next write, so
the store never accumulates.

*Decision 2026-08-12 (revised, same day):* calendar days, **not** NYSE trading
sessions. The first draft of this spec specified trading sessions on the
reasoning that the horizons are trading-day horizons. Auditing the prompt
surface showed that is not what the codebase does:
`backtest/reporting.py` documents `forward_return_horizons_days` as calendar
days, the fundamental decay in `analysts/fundamental/joiner.py:152-164`
compares `filing_anchor_days` (calendar) against `filing_delta_horizon_days`,
and the news carry already renders `day 12/20` for a catalyst anchored
2025-10-01 read at 2025-10-13 — 12 calendar days, 8 trading days. A
session-counted commitment would put two different clocks behind identical
`day N/H` notation in the same block, which is precisely the multiple-stored
-thoughts failure T1 exists to prevent. **No session-counting helper is added**
— `(as_of - anchored_at).days` is the whole computation.

The prose in `agents/strategist/prompts.py:204-207` and `:213-218` calling
technical's horizon "~60-**trading**-day" is the outlier and is corrected as
part of the hygiene precondition below. `strategist_prompt.py:539-541` already
states the unit correctly.

The news carry render is the working precedent for the format commitments
need — the model has already seen `day N/H` used this way, and commitments
should match it rather than invent a second notation.

*Decision 2026-08-12:* **expiry is informational.** It never forces an order,
never feeds the risk gate, and never changes a stance. The behavioural lever is
the prompt instruction below, not a constraint.

---

## Position events — material trims as facts

*Decision 2026-08-12:* every `PositionThesis` carries
`position_events: list[PositionEvent]` recording material trims:

```
PositionEvent
  kind            Literal["trim"]
  at              datetime
  tick_id         str
  weight_before   float
  weight_after    float
```

A partial `sell` records an event when
`(weight_before - weight_after) / weight_before >= material_trim_fraction`.

*Decision 2026-08-12:* `material_trim_fraction = 0.25`, in
`config/strategist.json` with a `config/README.md` entry. Rationale: the run's
DVN sequence trimmed 0.19→0.15 (−21%) and 0.15→0.12 (−20%) — routine sizing
that should not clutter the block — while a 0.15→0.10 (−33%) is a partial exit
that materially changes what the position means. The threshold is a tuning
knob and carries the `# HIGH-VALUE TUNING KNOB:` marker.

*Decision 2026-08-12:* position events have **no lifecycle meaning**. They do
not touch `opened_at`, do not re-anchor or clear commitments, and do not reset
any counter. This is the direct answer to the "frozen at majority buy" idea
considered earlier and rejected: a field that recomputes *which* buy counts as
the entry is not frozen, and it would re-anchor the clock immediately after a
large add on a name the bot already believed in — the opposite of what the
clock is for. Only the render is bounded, by
`config/strategist.json → position_thesis_caps.max_rendered_position_events`
(3, most recent first).

---

## Removals — no orphaned artefacts

*Decision 2026-08-12:* the following are superseded by the above and are
deleted cleanly, not deprecated.

1. **`PositionThesis.thesis_last_updated_tick` and
   `thesis_last_updated_at`**, their four reset sites in
   `_verb_dispatch.py:175/191/250/262`, the `current_tick_index` parameter
   threaded for them, `context_shim._fmt_updated_date`, and the
   `"  Thesis updated: {date}"` render line. They are a staleness counter on
   the prose. Once commitments carry real dated clocks, prose staleness is a
   second, weaker, competing answer to "how old is our view here" — exactly the
   multiple-stored-thoughts problem T1 exists to prevent. `last_reviewed_at` /
   `last_reviewed_decision` survive: they record that the row was *examined*,
   which the commitment clock does not.

2. **The dead `ma200_flip_days` anchor clause**,
   `strategist_prompt.py:741-750` plus its 724-728 comment block, **and the
   prompt prose at `prompts.py:217-218` that describes it** ("the `anchor:`
   line showing how many sessions the 200d-MA state has held"). It renders
   0/600, it is technical-only, and it measures signal age rather than holding
   period. Commitments supersede its stated intent. `ma200_flip_days` /
   `ma200_state` themselves stay in the extractor — they are correct code that
   will populate once the cache carries 200 bars, and `golden_cross` /
   `death_cross` / `trend_state` are sourced separately from vendor
   `company_ratios` and are unaffected.

3. **The `opened_at is None` discriminator** and every call site that branches
   on it to mean "watched, not held" — `_has_live_position`,
   `context_shim`'s `has_position` / `[NO POSITION]` tag, and the "the thesis
   book can carry watched-only rows whose `[POSITION]` tag predates an executed
   exit" defensive comment at `context_shim.py:754-757`.

The frozen-V1 fixture `tests/fixtures/position_thesis_v1.json` is the schema
gate (`position_thesis.py:40-43`). It must be re-frozen as part of this change,
with the migration made explicit rather than defaulted around — this is a
breaking schema change to a store with no production data behind it
(pre-deployment), so there is no migration path to write and none should be
invented.

---

## Render

*Decision 2026-08-12:* the position block gains one commitment line and at most
one events line; nothing else changes shape.

```
DVN [POSITION]
  Opened at $35.28 on 2025-09-02  (entry weight 0.080)
  Now $32.91  (-6.71%)  current weight 0.121
  Bought on: technical bullish day 41/60 · news bullish day 5/20 (CONTRADICTED
             since 10-08) · fundamental bullish day 41/90
  Trimmed:   10-07 0.19→0.12
  Rationale: ...
```

Day counts are calendar days since each commitment's own `anchored_at`, which
is why the technical and fundamental legs read 41 (anchored at the 09-02 open)
while news reads 5 (re-anchored by a later add). The notation deliberately
matches the news carry line's existing `day 12/20` format.

```
## Watch Notes
FSLR — policy overhang unresolved; revisit after the Q4 guide   (reviewed 10-13)
```

The `[POSITION]` tag is retained for continuity even though the store now makes
it redundant — it is the anchor the model's existing prompt instructions
reference.

---

## Prompt instruction

*Decision 2026-08-12:* the strategist prompt gains one paragraph, and its
operative sentence is the accountability clause, not the description:

> The "Bought on" line records the analyst signals this position was actually
> opened or added on, and how far through each signal's horizon you now are.
> You are not required to hold to expiry, and you are not required to sell at
> expiry. But **if you exit or trim a position while one of its commitments is
> still live, name the commitment you are abandoning and say why it no longer
> holds.** A commitment marked CONTRADICTED means a later read from the same
> analyst reversed — that is information about the remaining runway, not an
> instruction to close.

This is the lever. Findings (2) and (3) show the model never reasons about
elapsed time because it has no denominator; supplying the denominator without
requiring it to be addressed would repeat the `first-month-5` lesson that prose
loses to computed numbers. Requiring the abandonment to be named puts it in the
rationale, in the audit trail, and on the scoreboard.

---

## Validation

- Re-run the first month of `long-baseline-2025`. Primary metrics, before/after:
  **median holding period** (6 sessions), **realised P&L across closed
  round-trips** (−$1,196 on 9 closes), **win rate** (22.2%), and
  **round-trip count**.
- Positive-signal assertions, not absence-of-error — the `ma200_flip_days`
  lesson (correct extractor, passing unit tests, feature absent in 600/600 real
  rows) means every new surface must be asserted against **real cached run
  data**, not fixtures alone:
  - every `[POSITION]` block in a re-run renders ≥1 commitment (a position
    cannot be opened without at least one directional verdict, or the write
    rules are wrong);
  - commitment count per position never exceeds 3, and never exceeds 1 per
    analyst, across every tick of the run;
  - the number of live commitments per ticker-tick sits near the simulated
    2.57, and `CONTRADICTED` appears at roughly the simulated 2.1% — a large
    divergence means the derivation is not reading the verdicts the simulation
    read;
  - `user:positions` contains zero rows without a live position at every tick.
- Behavioural check on the accountability clause: count rationales on `sell`
  stances that name a live commitment. The current baseline is 0/190 rationales
  with any temporal content; anything near zero after the change means the
  clause is not landing and the lever has failed, independent of P&L.
- Thesis-prose churn (9.5 revisions/ticker) is *not* a target. It is expected
  to stay roughly flat — the fix moves the load-bearing content out of the
  prose, it does not slow the writing.

---

## Out of scope (sequenced after)

- **Shorting.** 37% of the current book is bearish views on unshortable
  tickers, which is what motivated the question. The evidence says do not act
  on it yet: bearish hit rates in this run are 41%/41%/36%/42%/39% (all
  sub-50%) with t-stats of 0.03–0.31 and −72.8bps at 90d, against bullish
  54%/52%/52%/55%/57%. The bearish signal has no demonstrated edge to
  monetise, and the long-only clamp is inherited from the Trading 212 target.
  Next step if revisited: score a hypothetical short book on paper from the
  existing forward returns before any code lands.
- **Fundamental magnitude collapse.** 11 distinct magnitudes across 600
  verdicts, with 0.4 and 0.3 covering 98.5% — magnitude is effectively a
  constant. Separate work package; needs a design decision first.
- **Scoreboard dedup distortion.** Value-equality dedup inflates on cache-hit
  decay (FDS: 6 real calls → 13 scored rows) and deflates on collisions (RMD:
  9 distinct input hashes → n=1); 92 true invocations against 73 scoreboard
  -fresh rows. This is why neither "we had a fundamental edge" nor "we lost it"
  is currently supportable. Sequenced **before** the magnitude work.
- **Snapshotter return-column bug.** `snapshot/agent.py:136-148` anchors
  `starting_capital` / `spy_start_price` in per-tick ADK session state, which
  does not survive the tick, so `bot_return_pct` / `spy_return_pct` /
  `excess_return_pct` are 0.0 in 30/30 rows and `spy_value_if_held` is
  byte-identical to `bot_total_value`. Telemetry only — nothing in `src/`
  consumes these for decisions — but it must be fixed before the validation
  metrics above are read off the snapshot table.
- **Entry selection.** 41 buys hit 46% with a negative median at both 5d and
  20d. This spec governs how long a position is held, not which one is opened;
  if holding discipline lands and P&L does not follow, entry selection is the
  next place to look.
