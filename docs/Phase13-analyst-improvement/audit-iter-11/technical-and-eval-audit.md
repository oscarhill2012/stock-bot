# Technical Analyst + Eval-Metric Audit — iter-11

**Scope:** Read-only critical audit of (a) the deterministic Technical analyst and
(b) the analyst predictive-power scoreboard that scores *all* analysts. No source,
config, or artefact was modified.

**Runs analysed (both iter-11)**

| Tag | Path | Market |
|---|---|---|
| baseline | `backtests/baseline-2025-09/runs/analysts-eval-iter-11` | rising |
| iran | `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-11` | falling / volatile |

**Method:** stdlib `python3` only (no project import). Per-tick verdicts+features
read from `traces/<TS>.json → 04_digest.data[*].per_analyst.technical`
(1 200 verdicts/run). Verdict↔forward-return joins read from
`decisions/<TS>__<TKR>__<side>.json` (164 baseline / 147 iran traded-name decisions,
each carrying `forward_returns.{+1d,+5d,+20d}`). The scoreboard numbers quoted are
the run's own `report/metrics.md`. **Read alongside the iter-10 audit**
(`docs/Phase13-analyst-improvement/audit-iter-10/technical-audit.md`), which this
report builds on rather than repeats.

---

# PART 1 — Technical analyst

## 1.1 What iter-11 changed (commits `1fb0a8b`, `b15d076`)

Four config-gated rule changes plus a data-side beta/sector fill. All live and
active in these runs (confirmed in `config/analyst_heuristics.json`):

1. **Regime-aware lean suppression** — `suppress_bearish_under_golden_cross=true`,
   `suppress_bullish_under_death_cross=true`. A bearish lean is downgraded to
   neutral while a golden cross holds (and symmetrically bullish under death cross).
   Applied *after* the RSI capitulation flip so a genuine capitulation is never
   clobbered. Verified firing in traces (e.g. AMD 2025-09-02:
   `trend_down_20d … golden_cross, bearish_suppressed_golden_cross` → lean neutral).
2. **Directional 52-week confidence boost** — `directional_52w_confidence=true`.
   The proximity boost now only fires when it corroborates a *bullish* lean,
   killing the harmful unconditional `near_52w_low` boost on bearish names.
3. **Neutral-band confidence damping** — `momentum_band_confidence_floor=0.5`.
   Directional confidence is scaled by a linear ramp from 0.5 at the band edge to
   1.0 at twice the band width. (Note: this damps *confidence*, not the lean — it
   does not reduce lean churn at all; see §1.4.)
4. **Vol-breakout recalibration** — `vol_ratio_breakout 1.5 → 1.3`. Revives the
   dead `+0.15` magnitude boost (audit found it fired 0/644 times at 1.5). Affects
   **magnitude only**, never lean or confidence.
5. **Beta + sector data fill** (`b15d076`) — PIT-correct trailing beta and a static
   sector map. The beta-damping feature is gated **OFF** (`beta_confidence_damping_enabled=false`),
   and the commit's own offline validation confirms it is verdict-neutral. So
   `b15d076` does **not** change a single technical verdict in these runs. Its only
   live effect on Technical is that `sector` is now populated — which matters for
   the *eval* (Part 2), not the analyst.

## 1.2 iter-10 → iter-11 scoreboard delta (Technical only)

Headline cells, from each run's `report/metrics.md`:

| Run / horizon / subset | iter-10 | iter-11 | verdict |
|---|---|---|---|
| iran +5d★ all     | +22.9 bps, 55.8%, t+2.39, p .017 | **+27.7 bps, 57.4%, t+3.12, p .002** | improved |
| iran +5d★ bullish | +33.4, 55.1%, p .076 | **+47.4, 56.8%, p .022** | improved → significant |
| iran +5d★ bearish | +29.3, 56.4%, p .112 | **+39.3, 57.9%, p .035** | improved → significant |
| iran +1d all      | +5.0, 52.9%, p .39  | +7.2, 54.4%, p .18 | slightly better |
| iran +20d all     | +7.8, 49.6%, p .67  | +19.2, 50.9%, p .27 | better, still insignificant |
| baseline +5d★ all | +0.6, 42.1%, p .97  | +6.0, 43.0%, p .65 | marginally better, still noise |
| baseline +20d all | **−71.8, 45.2%, t−2.45, p .015** | **−64.4, 45.0%, t−2.26, p .024** | still significantly ANTI-predictive |
| baseline +20d bullish | −58.6, 40.4%, p .12 | −50.6, 41.1%, p .19 | slightly less bad |
| baseline +20d bearish | −211.7, 60.0% | −263.7, 60.0% | unchanged-bad (tiny n=85) |

**Verdict on iter-11: a real but small directional improvement in the trending
(iran) window; a marginal, non-decisive nudge in the rising (baseline) window —
and the headline baseline pathology (significantly anti-predictive at +20d) is
*not fixed*.** The bearish-suppression and directional-confidence changes did what
the commit message claimed on the iran window (bullish/bearish +5d crossed into
significance). They did **not** rescue baseline +20d, because that anti-signal is
not a bearish-lean problem — it is a *bullish*-lean-vs-rising-market problem
(§1.3), which none of the four knobs touch.

This is consistent with the iter-10 audit's central non-finding: the analyst is
deterministic, so iteration only moves it as far as the rules move. The rules
moved the right direction; the magnitude is within noise on the window that
matters most (see Part 2 power analysis — almost none of these deltas are
statistically distinguishable from zero on a 1-month window).

## 1.3 The regime sign-flip, dissected `[EVAL]+[RULES]`

This is the central finding. The *same* unconditional momentum/RSI logic is
strongly predictive in iran (+5d) and significantly **anti**-predictive in baseline
(+20d). The cause is **not** that momentum "works in trends and reverts in chop."
It is the interaction of two facts:

1. **The lean is long-biased momentum.** Baseline lean mix is 679 bullish / 158
   bearish / 363 neutral (57% bullish). The analyst rides the trend: in a rising
   market almost everything it touches leans bullish.

2. **The eval scores *excess* return (ticker − peer-group mean), not raw return.**
   So a bullish call only "wins" if the named stock *beats the universe*, not if it
   merely rises.

Joining the baseline decisions to forward returns makes the mechanism explicit:

```
baseline, traded universe mean +20d = +2.55%   (everything rose)
  bullish lean: raw +20d = +2.40%  (good in absolute terms)
                BUT excess +20d ≈ −3 to −50 bps, hit 41–47%  (LOST vs peers)
```

In a broad rally the technical analyst piles into the names that already have the
strongest 20-day momentum (mega-cap leaders). Over the next 20 days the *laggards*
catch up (mean reversion at the cross-section level) and the momentum leaders
underperform the rising average. The bullish lean is right on direction and **wrong
on relative selection** — which is exactly what a selection-skill metric punishes.
At +5d the effect hasn't had time to bite (excess ≈ flat); by +20d it is a
significant −64 bps.

In iran (falling/volatile) the picture inverts because the cross-section *disperses*
instead of compressing: when the market is falling, the names momentum avoids
(the weak ones) fall hardest, so avoiding them (or leaning bearish on them) earns
positive excess. Iran traded universe mean +20d = −0.60%; bearish-lean names did
worse than that, so the short-side excess is positive; bullish-lean names (the
relative-strength survivors) beat the falling average. Momentum-as-relative-strength
pays in a dispersing/falling tape and is punished in a compressing/rising tape.

**Driver of the bad baseline +20d calls:** it is the **bullish lean on high-momentum
mega-caps in a rally**, full stop — not a specific factor tag. `bullish+golden_cross`
(n=86) returned +2.09% raw but only 55.8% up-rate and *negative excess*; the regime
gate cannot help here because the gate only suppresses *bearish*-under-golden and
*bullish*-under-death. There is no gate for "bullish-momentum-in-a-compressing-rally"
because price-only features cannot see cross-sectional compression.

**Tag this `[EVAL]` as much as `[RULES]`:** the sign-flip is partly an artefact of
scoring relative (peer-neutralised) excess on a long-biased absolute-momentum
signal. The analyst's *absolute* directional calls are fine in both regimes
(bullish raw +20d positive in both); the *relative* score flips because relative
performance of momentum flips with the regime. A strategist that consumes the lean
for absolute long/flat decisions is not exposed to this flip; a metric that grades
relative selection is.

## 1.4 Churn, abstention, no-data (full traces)

| | baseline | iran |
|---|---|---|
| total verdicts | 1 200 | 1 200 |
| no-data | 0 (0.0%) | 0 (0.0%) |
| neutral / abstention | 363 (30.2%) | 430 (35.8%) |
| lean flip rate (consecutive) | 98/1180 = **8.3%** | 123/1180 = **10.4%** |
| worst flippers | CVX 10, RTX 10, MSFT/NVDA/META 7 | NVDA 14, META 11, AVGO 11, GOOGL/JPM 10 |

- **No-data is genuinely zero** — the cache is fully warmed, so the nullable-sentinel
  machinery is untested by these runs (same as iter-10). `[DATA]` clean.
- **Abstention rose** (iter-10 reported ~flip churn but the regime gate now converts
  some directional calls to neutral, lifting the neutral share to 30–36%). This is
  the *intended* effect of suppression — abstaining on regime-conflicted names.
- **Lean flip rate barely moved** vs iter-10 (8.3% / 10.4% here vs 9.9% / 11.8%
  reported in the iter-10 audit). **This confirms the iter-11 band-confidence
  damping did NOT reduce churn** — it scales *confidence*, not the lean, so the
  ±0.02 momentum-band cliff still toggles the lean on intraday noise exactly as
  before. NVDA still flips 14× in 60 ticks. `[RULES]` — the open+close double-run
  and missing lean hysteresis from iter-10 P3 remain unaddressed.

## 1.5 Is the signal real and exploitable? — blunt verdict

**It is real but regime-conditional, and not safely consumable *unconditionally* by
the strategist.** Evidence:

- The one statistically-robust analyst signal in the whole system is Technical +5d
  in iran. But on an honest (autocorrelation-corrected) basis even that is borderline
  (Part 2: honest t ≈ +1.4, not +3.1).
- The *same rules* produce a significantly negative +20d excess in baseline. A
  strategist that weights Technical's lean as a relative-selection signal will be
  *helped* in a falling/volatile tape and *hurt* in a broad rally. That is the
  definition of a signal you cannot consume blind.
- The lean's *absolute* direction is fine in both regimes (bullish names rise in
  baseline, the relatively-strong rise in iran). So the exploitable version is
  "Technical = absolute long/flat momentum tilt," not "Technical = cross-sectional
  stock picker." The strategist should treat it as the former.

**Bottom line:** Technical is a competent absolute-momentum / relative-strength
signal whose *measured* skill flips sign with the regime because the metric grades
relative selection. It is not a coin-flip, but it is also not a regime-robust alpha
source. Eleven iterations of threshold tuning will not change that — the ceiling is
set by "price-only momentum has regime-dependent cross-sectional skill," which is a
property of the *feature set*, not the thresholds.

## 1.6 Findings & prioritised recommendations (Part 1)

- **P1 `[RULES]+[EVAL]` — Decide what Technical's lean *means* to the strategist.**
  Highest leverage, lowest code. The lean is an absolute-momentum tilt; the eval
  grades relative selection. Either (a) document/consume the lean as an absolute
  long/flat tilt and stop expecting cross-sectional alpha from it, or (b) feed the
  analyst a relative-strength-ranked input so its lean *is* cross-sectional. The
  `relative_strength_vs_spy_*` features are already computed (present in traces) but
  the verdict logic **never reads them** — that is the single biggest untapped lever.
  Leaning on relative strength rather than absolute `pct_change_20d` would align the
  signal with the metric and likely fix the baseline +20d sign.

- **P2 `[RULES]` — Lean hysteresis / stop double-running open+close (carried from
  iter-10 P3, NOT done in iter-11).** Confidence damping does not reduce the 8–10%
  noise-driven lean churn. Add separate enter/exit thresholds on the momentum band,
  or run the 20-day analyst once per day. NVDA flipping 14×/60 ticks is pure noise.

- **P3 `[RULES]` — There is still no brake on bullish-momentum-into-a-compressing-rally.**
  The regime gate is asymmetric to the actual baseline failure. A cross-sectional
  rank gate (only lean bullish if relative strength is *positive*) addresses the real
  driver; an RSI-overbought magnitude haircut (iter-10 P6, still un-done) is a weaker
  partial.

- **P4 `[DATA]` — none.** Inputs are clean (0 no-data, 0 stale-sentinel). The
  bottleneck is **not** data quality for Technical.

**Per-finding bottleneck attribution (the user's three-way question):**
The Technical bottleneck is **(3) the rules**, specifically the feature→verdict
mapping ignoring the relative-strength features it already has — *amplified by
(1) an eval that grades a dimension (relative selection) the rules don't optimise.*
It is **not (2) data.**

---

# PART 2 — The eval metric (scoreboard)

Code: `src/backtest/scoreboard.py` (`build_analyst_scoreboard` /
`render_scoreboard_md`), invoked from `src/backtest/reporting.py`. Forward returns
come from `_forward_close(cache, ticker, as_of_date, h)` in `reporting.py` against
the per-window golden cache.

## 2.1 How it is computed (verified by reading the code)

1. **Load** all `analyst_evidence` rows; sort by (analyst, ticker, recorded_at).
2. **Dedup** consecutive identical `(lean, magnitude, confidence)` tuples per
   (analyst, ticker) into one anchor observation (cache-replay fix). For the
   deterministic Technical analyst this rarely fires (verdicts genuinely change),
   so Technical's n stays high (~608/625); it matters far more for the LLM analysts.
3. **Base price** = phase-matched (`open` if `recorded_at.hour < 17` UTC else
   `close`).
4. **Forward return** per horizon = `(forward_close_h − base_price) / base_price`.
5. **Cross-sectional demean** per tick: `excess = fwd_return − peer_group_mean`.
   Peer group = same sector if `neutralise_by="sector"`, else whole universe.
6. **Score** = `position(±1/0) × excess`. Aggregated per (analyst, horizon, subset)
   into mean-excess-bps, hit-rate (non-neutral only), and
   `scipy.stats.ttest_1samp(scores, 0)` for t-stat / p-value.

The arithmetic is **correct and the design is reasonable.** Phase-matched base
prices, dedup, per-analyst primary horizon, and neutral-excluded hit-rate are all
sound choices. Two structural concerns follow.

## 2.2 Correctness / independence concerns

**[A] Neutralisation mode actually used here is `universe`, not `sector`.**
The default is `neutralise_by="universe"` (scoreboard.py:240) — the comment says
sector data was NULL so it was re-defaulted to universe. `b15d076` has *now*
populated sector, but unless the iter-11 runs were invoked with `neutralise_by="sector"`
they were still scored universe-relative. **Universe neutralisation on a 20-name
mega-cap watchlist is the direct cause of the §1.3 sign-flip**: it demeans against a
basket dominated by the same mega-caps Technical leans bullish on, so a correct
sector call (e.g. long the best energy name) is graded against tech/healthcare moves.
Switching to sector neutralisation would materially change Technical's scores and is
the single most consequential eval setting. **Confirm which mode produced these
numbers before drawing conclusions from them.** (Recommendation, not a change.)

**[B] The t-stats are badly inflated by autocorrelation — this is the big one.**
`ttest_1samp` assumes i.i.d. observations. The scoreboard's observations are *not*
independent on two axes:

- **Overlapping forward windows.** A +20d return measured at consecutive ticks
  shares ~19/20 of its horizon. In a ~30-trading-day window there are only ~1.5
  *non-overlapping* 20-day blocks per ticker. So the genuinely independent +20d
  sample is ≈ 20 names × 1.5 ≈ **30**, not the **608** the test divides by. The
  t-stat is inflated by roughly `sqrt(608/30) ≈ 4.5×`.
- **Cross-ticker correlation within a tick** (the whole market moves together),
  which the cross-sectional demean only partly removes.

Concretely:

| reported | reported t / p | honest (overlap-corrected) t | honest read |
|---|---|---|---|
| baseline +20d all | −2.26 / p .024 ("significantly anti-predictive") | ≈ **−0.5** | **not significant** |
| iran +5d all | +3.12 / p .002 ("strong signal") | ≈ **+1.4** | **suggestive, not significant** |

**Both headline findings the user has been chasing are statistical mirages at
honest power.** The "significantly anti-predictive" baseline +20d is, corrected,
indistinguishable from zero. The "robust" iran +5d is, corrected, a weak positive
hint. `[EVAL]` — the metric is *over-confident*, not too strict: it reports
significance that 30 effective observations cannot support. The fix is a
clustered / Newey-West style standard error, or scoring only non-overlapping
forward blocks, or a block bootstrap. As-is, every p-value in `metrics.md` should
be read as "directional hint," never "proven."

## 2.3 Statistical power: can a 1-month window detect improvement?

Using the observed score dispersion (std backed out from each cell's mean and t):

- baseline +20d per-verdict std ≈ **700 bps**; iran +5d std ≈ **220 bps**.
- Minimum detectable effect (80% power, α .05, two-sided) ≈ `2.8 × SE`.

On the **honest** effective sample size:

| window | effective independent n (+20d) | SE | **MDE** |
|---|---|---|---|
| 1 month (these runs) | ~40 | ~110 bps | **~310 bps** |
| 6 months | ~240 | ~45 bps | **~127 bps** |

The real iter-10→iter-11 Technical deltas are **5–15 bps**. The 1-month MDE is
**~310 bps** — roughly **20–60× larger than the effect being tuned.** 

**Direct answer to the user's worry:** **No — a 1-month window cannot detect the
improvements you are making.** The fine-tuning deltas are real in sign but two
orders of magnitude below the window's detection threshold. Eleven iterations have
felt like no progress because *the instrument cannot resolve the progress*, not
(necessarily) because there is none. Even a 6-month window only gets MDE down to
~127 bps — still larger than a typical per-iteration delta. You would need either
6+ months **and** an honest (de-autocorrelated) estimator to reliably rank two
adjacent iterations, and even then only large changes are detectable.

## 2.4 Is the eval too strict, or are the analysts genuinely weak?

**Neither framing is right. The eval is too *under-powered and over-confident*
simultaneously**, and the analysts are *weak-but-not-zero*:

- Not "too strict": the threshold for significance is, if anything, too *easy* —
  it declares p .002 on 30 effective points (§2.2[B]).
- The analysts have a genuine but small edge (a few bps to a few tens of bps of
  excess, regime-dependent for Technical). That is plausible for price-only
  large-cap signals; it is not nothing, but it is below what one month can measure.

## 2.5 Is the 6-month backtest a prerequisite? — recommendation

**Yes, a longer window is a prerequisite for trustworthy fine-tuning — but it is
necessary, not sufficient.** Two changes are required together:

1. **Extend the window to ≥6 months** (ideally spanning ≥1 rising and ≥1
   falling/volatile regime), to push MDE toward the size of real iteration deltas
   and to let regime-conditional behaviour (§1.3) average out or be measured
   *per regime* rather than confounded.
2. **Fix the estimator** (`[EVAL]`, highest priority, cheap): replace the naive
   `ttest_1samp` with a standard error that accounts for overlapping forward windows
   and cross-sectional correlation (block bootstrap or Newey-West, or score only
   non-overlapping horizon blocks). Without this, a 6-month window will still report
   inflated significance — just with more decimal places.

Until both are in place, the per-iteration scoreboard moves the user has been
reacting to are **inside the noise band** and should not drive accept/reject
decisions on a tuning change. The honest current message is: *Technical has a small,
regime-conditional, real edge; the eval cannot yet tell iter-10 from iter-11; the
top priority is a trustworthy estimator on a longer window before any further
threshold tuning.*

---

## Appendix — live thresholds (`config/analyst_heuristics.json`, technical block)

```
rsi_overbought 75 | rsi_oversold 25 | rsi_mean_reversion 35
pct_change_momentum_scale 4.0 | momentum_neutral_band_pct 0.02
vol_ratio_breakout 1.3 (was 1.5) | vol_ratio_dry_up 0.7
atr_high_volatility_pct 5.0 | near_52w_extreme_pct 5.0
confidence_base 0.5 | confidence_boost_step 0.2 | confidence_penalty_step 0.3 | magnitude_cap 1.0
suppress_bearish_under_golden_cross true | suppress_bullish_under_death_cross true
directional_52w_confidence true | momentum_band_confidence_floor 0.5
beta_confidence_damping_enabled false
```
