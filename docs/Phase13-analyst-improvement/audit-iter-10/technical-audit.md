# Technical Analyst Audit — Deterministic Verdict Quality

**Scope:** Read-only audit of the deterministic Technical analyst across four backtest
runs. No source, config, or artefact was modified.

**Runs audited**

| Tag | Path | Headline |
|---|---|---|
| baseline-GOOD-iter9 | `backtests/baseline-2025-09/runs/analysts-eval-iter-9` | +9.12 % (+4.82 % vs SPY) |
| baseline-BAD-iter10 | `backtests/baseline-2025-09/runs/analysts-eval-iter-10` | +4.16 % (−0.14 % vs SPY) |
| iran-iter9 | `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-9` | −3.74 % (falling market) |
| iran-iter10 | `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-10` | −5.18 % (falling market) |

**Watchlist (20):** AAPL MSFT NVDA GOOGL AMZN META TSLA AMD AVGO CRM JPM BAC XOM CVX LMT RTX JNJ UNH PG WMT.

---

## 1. Methodology

The deterministic logic lives in `src/contract/extractors/technical.py`
(`extract_technical_features` + `derive_technical_verdict`); the agent wiring is
`src/agents/analysts/technical/{agent.py,fetch.py}`; thresholds are in
`config/analyst_heuristics.json` (validated by `src/agents/analysts/heuristics.py`).

I parsed artefacts with stdlib `python3` only (no project import — `.venv` absent):

- **Per-tick verdicts + features** for all 20 tickers × 60 ticks × 4 runs from
  `traces/<TS>.json → 04_digest.data[*].per_analyst.technical` (4 800 verdicts total).
  Used for churn/flip and edge-case analysis.
- **Verdict ↔ forward-return joins** from `decisions/<TS>__<TKR>__<side>.json`
  (`analyst_outputs.per_analyst.technical.verdict` + `.features`, joined to
  `forward_returns.{+1d,+5d,+20d}`). 644 traded-name decisions across the 4 runs.
  Used for calibration / hit-rate / confidently-wrong analysis.

Cadence: 60 ticks = 30 trading days × 2 phases (13:30 "open", 20:00 "close").

---

## 2. Feature → verdict mapping (as implemented)

Thresholds shown are the live `config/analyst_heuristics.json` values.

### Lean (precedence order)

1. **No-data fingerprint** → `is_no_data=True`, neutral, when
   `rsi_14 == 0` AND `pct_change_20d` absent AND `atr_pct_14 == 0`.
2. **Momentum neutral band:** if `abs(pct_change_20d) < 0.02` → **neutral**
   (abstain). Fires *before* the sign check.
3. Else **base lean = sign(pct_change_20d)**: >0 bullish, <0 bearish.
4. **Moderate-oversold downgrade:** if lean bearish AND `rsi_14 < 35`
   (`rsi_mean_reversion`) → **neutral** (`rsi_moderate_oversold`).
5. **Capitulation flip:** if `rsi_14 < 25` (`rsi_oversold`) AND `pct_change_5d < 0`
   → **bullish** (overrides everything above).

`golden_cross` / `death_cross`, `rsi_overbought` (>75), 52-week proximity, volume,
and volatility are **context tags only** — they never change the lean.

### Magnitude

`magnitude = min(abs(pct_change_20d) × 4.0, 1.0)` (`pct_change_momentum_scale`),
then `+0.15` if `vol_breakout`, `−0.10` if `vol_dry_up` (both clamped).

### Confidence (base 0.5, clamped [0,1])

- `+0.2` (`confidence_boost_step`) if `momentum_agree` (sign5 == sign20 ≠ 0).
- `+0.2` if `near_52w_high` (`abs(dist_from_high_52w_pct) ≤ 5`) **or**
  `near_52w_low` (`dist_from_low_52w_pct ≤ 5`).
- `−0.3` (`confidence_penalty_step`) if `high_volatility` (`atr_pct_14 > 5`).

Observed confidence is therefore almost always 0.5, 0.7, or 0.9.

### key_factors

`trend_up_20d`/`trend_down_20d`, `momentum_agree`/`momentum_disagree`,
`rsi_overbought`/`rsi_oversold`, `rsi_moderate_oversold`, `vol_breakout`/`vol_dry_up`,
`golden_cross`/`death_cross`, `near_52w_high`/`near_52w_low`, `high_volatility`.

---

## 3. The headline non-finding: technical is identical in iter-9 vs iter-10

The user's "inconsistency" framing implicitly suspects the technical analyst behind the
iter-9 (good) vs iter-10 (bad) performance gap. **It is not.**

> Across all 1 200 ticker-ticks in each window, the technical verdict
> (lean, magnitude, confidence) is **byte-for-byte identical** between iter-9 and
> iter-10 — **0 differing verdicts** out of 1 200 common keys, in *both* the baseline
> and the iran windows.

This is expected (the analyst is deterministic and consumes the same cached price data),
but it is the single most important result for the user's question: the +9.12 % vs
+4.16 % baseline split is driven entirely by the **non-deterministic analysts (news /
fundamental LLM) and/or strategist**, not by technical. Any "inconsistency" the user
observed in technical is intra-window churn (Section 4), not cross-iteration drift.

---

## 4. Threshold cliffs and lean churn

Flip rate (consecutive lean changes / ticker-ticks): **9.9 %** baseline,
**11.8 %** iran. Worst offenders flip 10–14 times in 60 ticks:

| Run | Ticker | Flips/60 | Lean mix |
|---|---|---|---|
| baseline | META | 11 | neutral 25 / bearish 29 / bullish 6 |
| baseline | CRM, CVX, RTX, WMT | 10 | mixed |
| iran | NVDA | 14 | bullish 21 / neutral 14 / bearish 25 |
| iran | META | 11 | bullish 15 / neutral 11 / bearish 34 |
| iran | JPM | 11 | bearish 31 / neutral 25 / bullish 4 |

**Cause: the ±0.02 momentum neutral band is a hard cliff straddled by intraday noise.**
104 flips across the 4 runs are band-crossings driven by a `pct_change_20d` move of
**<0.015** — and many happen *within a single calendar day* (open→close tick), e.g.:

```
MSFT  2025-09-15 open→close  bearish→neutral  pct20 -0.0225 → -0.0077
MSFT  2025-09-22 open→close  bullish→neutral  pct20 +0.0271 → +0.0142
RTX   2025-09-04→05          neutral→bullish→neutral  pct20 oscillating 0.0157–0.0233
JPM   2025-09-08→09          bullish→neutral→bullish  pct20 0.0144–0.0288
```

A name parked near ±2 % 20-day return toggles lean on noise no human technician would
act on. The band removes the *worst* churn (a raw sign flip at pct20≈0) but introduces
**two new cliff edges at ±0.02** instead of one at 0. There is no hysteresis: the same
threshold is used for both entry and exit, so a value oscillating around 0.02 flaps every
tick.

**Classification: FIXABLE (mis-design).** Two independent flaws: (a) no hysteresis /
dead-band smoothing on the lean boundary; (b) the analyst runs on *both* the open and
close tick with a 20-day lookback that barely moves intraday, so the open/close pair is
near-duplicate work that manufactures same-day flips.

---

## 5. Edge cases and degenerate features

| Run | n | golden_cross=1 | death_cross=1 | no_data | rsi=0 | vol20 missing | rs_spy20 missing |
|---|---|---|---|---|---|---|---|
| baseline (both iters) | 1200 | 750 (62 %) | 90 (8 %) | 0 | 0 | 0 | 0 |
| iran (both iters) | 1200 | 390 (33 %) | 226 (19 %) | 0 | 0 | 0 | 0 |

- **No `is_no_data`, no `rsi=0`, no missing `vol_ratio_20d` / `relative_strength`** in any
  run — the cache is fully warmed, so the nullable-sentinel machinery (Bugs #20/#23) never
  triggers here. Those guards are sound but *untested by these runs*.
- **`golden_cross` / `death_cross` are well-behaved** (not stuck at 0): 62 % golden in the
  rising baseline, flipping to 33 % golden / 19 % death in the falling iran window. The
  feature itself is fine — the problem (Section 6) is that the *verdict ignores it*.

### Dead branch: the entire volume-context block never fires

`vol_breakout` and `vol_dry_up` occur **0 times in all 644 decisions**. Root cause is
structural, not a data gap:

```
vol_ratio_20d  (20-bar mean volume / 50-bar mean volume), n=2400:
  min 0.618 | p5 0.797 | p50 1.006 | p95 1.316 | max 1.545
  > 1.5 (breakout): 1 sample  (0.04 %)
  < 0.7 (dry_up):  20 samples (0.83 %)
```

A ratio of two heavily overlapping multi-week volume averages is mathematically
incapable of reaching 1.5 except in extreme single-day spikes that the averaging smooths
away. So:

- the `vol_breakout` `+0.15` magnitude boost is **unreachable** (dead code);
- `vol_dry_up` fires 0.83 % of the time — effectively dead;
- the magnitude formula reduces to `min(abs(pct20)×4, 1.0)` in practice.

**Classification: FIXABLE (feature defect).** Either compare a short window (e.g. last
1–3 bars) against the 20-bar baseline so a real spike registers, or lower the threshold
to the realised distribution (e.g. >1.3 ≈ p95).

`high_volatility` fires on only 2 % of rows (atr p95 = 4.49 vs threshold 5.0) — its `−0.3`
confidence penalty is rare but at least occasionally active; lower-priority.

---

## 6. Calibration & predictive power (644 decisions, all 4 runs)

### Hit rate by lean × horizon (neutral excluded)

| Horizon | Bullish hit (n=303) | Bullish mean ret | Bearish hit (n=176) | Bearish mean ret | Neutral mean ret (n=165) |
|---|---|---|---|---|---|
| +1d | 53.8 % | +0.56 % | 45.5 % | +0.11 % | +0.32 % |
| +5d | 55.4 % | +1.20 % | 48.3 % | −0.09 % | +0.65 % |
| +20d | 58.4 % | +1.95 % | 47.7 % | +0.64 % | +1.20 % |

- **Bullish lean carries genuine signal** that *strengthens with horizon* (53.8→58.4 %).
  This is the analyst's one working edge.
- **Bearish lean is anti-predictive / noise.** Hit rate is **below 50 % at every horizon**
  (45.5/48.3/47.7 %), and bearish-tagged names had *positive* mean returns at +1d and +20d.
  A coin would do better. (Caveat: the decision sample skews long — a momentum bot rarely
  shorts — but the directional sign is unambiguous and consistent across horizons.)

### Confidence is NOT monotonic with accuracy

+5d hit rate by confidence bucket (directional decisions):

```
conf~0.5: 53.2 % (n77)   conf~0.7: 48.1 % (n158)   conf~0.9: 56.1 % (n228)
```

The 0.7 bucket is the *worst*, and 0.5 beats it. Confidence ≈ "0.5 base +0.2 momentum_agree
+0.2 near-52w" does not rank-order outcomes. Decomposing the boosts:

- `momentum_agree` (+0.2): directional +20d **+1.02 %** (n330) vs `momentum_disagree`
  **+0.95 %** (n149) — the boost separates essentially nothing.
- `near_52w_low` (+0.2) on **bearish** names is **actively harmful**: bearish+near-low had
  **+2.09 %** mean +20d and only **35 % down-rate**, *worse* than bearish-without-near-low
  (+0.22 %, 51 % down-rate). The confidence boost is highest precisely on the falling names
  most likely to bounce.
- `near_52w_high` (+0.2) on **bullish** names is reasonable: +1.85 % mean +20d, 60 % up-rate.

### Magnitude barely correlates with return

Pearson r of signed magnitude (lean × magnitude) vs forward return:
**+0.056 (+1d), +0.074 (+5d), +0.093 (+20d)**. Bucketed signed +20d:
mag-lo +1.00 %, mag-mid +0.46 %, mag-hi +1.96 % — not monotonic at +5d. Magnitude adds a
faint, noisy tilt; it is not a reliable conviction scalar.

**Net:** technical is **mildly additive on the long side, noise-to-harmful on the short
side, and its confidence/magnitude scalars are close to uninformative.**

---

## 7. Confidently-wrong examples (confidence = 0.90)

All worst-12 high-confidence misses, with mechanism:

| Run | Ticker | Tick | Lean | f20 | rsi | pct20 | Factors |
|---|---|---|---|---|---|---|---|
| iran-9 | AVGO | 2026-03-24 close | bearish | **+19.3 %** | 45 | −2.0 % | trend_down, momentum_agree, **near_52w_low** |
| iran-9 | AVGO | 2026-03-23 close | bearish | **+17.7 %** | 48 | −2.2 % | trend_down, momentum_agree, **near_52w_low** |
| baseline | GOOGL | 2025-10-13 close | bearish | **+16.2 %** | 56 | −3.0 % | trend_down, momentum_agree, **golden_cross**, near_52w_high |
| iran-10 | AVGO | 2026-03-20 close | bearish | +14.5 % | 39 | −6.7 % | trend_down, momentum_agree, near_52w_low |
| iran-10 | AMD | 2026-03-17 close | bearish | +12.2 % | 44 | −3.3 % | trend_down, momentum_agree, near_52w_low |
| baseline | CRM | 2025-10-01 close | bearish | +11.8 % | 40 | −7.9 % | trend_down, momentum_agree, **death_cross**, near_52w_low |
| baseline | AVGO | 2025-09-10 close | bullish | −10.6 % | **77** | +18.2 % | trend_up, momentum_agree, **rsi_overbought**, golden_cross, near_52w_high |
| iran-9 | LMT | 2026-03-02 | bullish | −8.9 % | 69 | +7.3 % | trend_up, momentum_agree, golden_cross, near_52w_high |

**Two recurring mechanisms:**

1. **Confident-bearish-into-a-bounce** (the dominant failure). A modest negative 20-day
   return (−2 % to −8 %) sets a bearish lean; `near_52w_low` proximity then boosts
   confidence to 0.90 — exactly when the name is most likely to mean-revert up. The
   GOOGL/CRM cases also carry a `golden_cross` / `death_cross` context tag that the lean
   logic *ignores*: GOOGL is called bearish at 0.90 confidence while flagged
   `golden_cross` (confirmed uptrend regime) and `near_52w_high`. Quantified:
   **bearish+golden_cross (n=24) → 21 % down-rate, +2.23 % mean +20d.**

2. **Confident-bullish-into-exhaustion.** AVGO at RSI 77 / +18 % 20-day gets the maximum
   confident-bullish call right at the blow-off top (−10.6 % over the next 20 days). The
   `rsi_overbought` tag is *informational only* (Bug #12 removed its lean effect) and does
   not temper magnitude or confidence, so the analyst is loudest at the worst entry.

---

## 8. Prioritised findings & recommendations

Recommendations only — **no code was changed.** Ordered highest-leverage first.

### P1 — Bearish lean is anti-predictive; suppress or invert near-low/in-uptrend bearish calls
**FIXABLE.** Bearish hit rate < 50 % at every horizon; bearish+near_52w_low and
bearish+golden_cross are strongly positive forward. The pure-momentum-sign bearish lean has
no edge in this universe (large-cap mean-reverters). Options: (a) gate bearish behind a
regime confirmation — do not lean bearish while `golden_cross` is set; (b) extend the
existing `rsi_moderate_oversold` neutralisation (which *works* — 80 % up-rate, +2.69 %) to
also neutralise bearish-near-52w-low; (c) at minimum stop *boosting confidence* via
`near_52w_low` on bearish names. Highest expected scoreboard lift.

### P2 — `near_52w_low` confidence boost is harmful on bearish names
**FIXABLE (mis-calibration).** Bearish+near-low: 35 % down-rate vs 51 % without. Make the
52-week proximity boost *directional*: `near_52w_high` boosts bullish only, `near_52w_low`
boosts bearish only if it survives a mean-reversion guard — otherwise it should *lower*
confidence or flip toward bounce. Removing the unconditional boost is the safe minimum.

### P3 — Add hysteresis / smoothing to the ±0.02 momentum band, and stop double-running open+close
**FIXABLE.** 104 noise-driven lean flips, many intraday. Introduce a dead-band (separate
enter/exit thresholds, e.g. lean only on |pct20|>0.025, revert to neutral only below 0.015)
or smooth `pct_change_20d`. Separately, evaluate whether a 20-day-momentum analyst needs to
recompute on both the open and close tick at all — the open/close pair manufactures same-day
churn for near-identical inputs.

### P4 — Volume context block is dead code
**FIXABLE (feature defect).** `vol_breakout` (thr 1.5) fired 1/2400 times; `vol_dry_up`
0.8 %. The 20/50-bar ratio cannot reach 1.5. Redefine as short-window (1–3 bar) vs 20-bar
baseline, or recalibrate thresholds to the realised distribution (breakout ≈ p95 ≈ 1.3).
Until then the `+0.15` magnitude boost is unreachable.

### P5 — Confidence and magnitude scalars are near-uninformative
**FIXABLE (mis-calibration).** Confidence is non-monotonic with hit rate (0.7 bucket worst);
`momentum_agree` separates +1.02 % vs +0.95 %; magnitude r ≤ +0.09. Recommend a measured
sweep of `confidence_boost_step` / `pct_change_momentum_scale` against the eval scoreboard
(the config docstrings already flag these as "provisional, pending a sweep"), or replace the
additive scheme with one calibrated to realised hit rates per factor.

### P6 — `rsi_overbought` context tag does nothing to temper confident-bullish-at-the-top
**FIXABLE (lower priority).** Bug #12 intentionally removed the bearish flip (correct — RSI
stays high in strong trends). But the tag now has *zero* effect, leaving confident-bullish
calls at exhaustion (AVGO RSI 77 → −10.6 %). Consider a mild magnitude/confidence
*haircut* (not a lean flip) when `rsi_overbought` coincides with a stretched pct20.

### Non-finding (record explicitly) — technical did NOT cause the iter-9/iter-10 split
The analyst is fully deterministic and produced **identical** verdicts in iter-9 vs iter-10
in both windows (0/1200 differences each). The performance inconsistency the user observed
lives in the LLM analysts (news/fundamental) and/or strategist, not here. Direct any
"inconsistency" investigation there.

### Genuinely UNPREDICTABLE (do not chase)
The +20d blow-off-top reversals on momentum names (AVGO +18 % then −10.6 %) are not
something a 20-day price-only analyst can foresee from price alone — exhaustion timing needs
flow/positioning data the technical feature set does not contain. Tempering confidence (P6)
is reasonable, but expecting the lean to *call the top* is not.

---

## Appendix — exact thresholds (`config/analyst_heuristics.json`)

```
rsi_overbought 75 | rsi_oversold 25 | rsi_mean_reversion 35
pct_change_momentum_scale 4.0 | momentum_neutral_band_pct 0.02
vol_ratio_breakout 1.5 | vol_ratio_dry_up 0.7
atr_high_volatility_pct 5.0 | near_52w_extreme_pct 5.0
confidence_base 0.5 | confidence_boost_step 0.2 | confidence_penalty_step 0.3
magnitude_cap 1.0
```
