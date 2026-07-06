# Spec — Relative-strength in the Technical analyst lean/confidence

**Status:** Draft for sign-off. Implementation-ready *contingent* on the open
questions in §5 being resolved — in particular, the evidence in §1 does **not**
support the iter-11 audit's optimistic framing, and the recommended option (§2)
is the *conservative* one, not the aggressive "lean = relative strength" one.

**Scope:** the deterministic Technical analyst only
(`src/contract/extractors/technical.py`). No change to any other analyst, the
strategist, the eval, or the broker. British English throughout.

**Motivating audits:**
- `docs/Phase13-analyst-improvement/audit-iter-11/technical-and-eval-audit.md`
  (the regime sign-flip; the "biggest untapped lever" claim about
  `relative_strength_vs_spy`).
- `docs/Phase13-analyst-improvement/audit-iter-10/technical-audit.md`
  (the current feature→verdict mapping in detail).

---

## 0. TL;DR for the reviewer

The iter-11 audit asserts that the verdict logic ignores the
`relative_strength_vs_spy_*` features it already computes, and that *"leaning on
relative strength rather than absolute `pct_change_20d` would align the signal
with the metric and likely fix the baseline +20d sign."*

**The first half is true; the second half is not supported by the data and is
probably false.** I re-derived the full-population forward-excess numbers (§1)
directly from the iter-11 traces joined to each window's golden cache, replicating
the scoreboard's own methodology. The finding:

- `relative_strength_vs_spy_{5d,20d}` **is computed and 100 % populated** in both
  iter-11 runs.
- `relative_strength_vs_sector_{5d,20d}` is **0 % populated** — the sector refetch
  had not landed when these runs were produced. Any sector-based rule is therefore
  **unverifiable on current artefacts** and must be gated to degrade gracefully.
- The correlation between RS-vs-SPY and forward *excess* return is **near zero and
  sign-inconsistent across windows and horizons** (Pearson r ranges from +0.069 to
  −0.128). At the extremes the relationship is **contrarian, not momentum-like**:
  the biggest *laggards* (most negative RS) post the *highest* forward excess in
  both windows.
- Consequently, **"lean = sign(RS)" (design option b) would be actively wrong** at
  the extremes and would degrade the iran window. Even the milder "gate" (option a)
  flips sign between baseline +5d (helps) and baseline +20d / all iran (hurts).

The recommendation (§2) is therefore **option (c): use RS to modulate
*confidence/magnitude* only, never the lean direction**, plus an *optional,
sign-off-gated* narrow gate (option a) restricted to the one regime-robust pocket.
This is deliberately unambitious because the honest evidence does not justify a
direction-changing rule on a 2-window, autocorrelation-inflated sample.

---

## 1. Problem statement & evidence

### 1.1 What the current logic does (confirmed by reading the source)

`derive_technical_verdict` (`src/contract/extractors/technical.py:574`) sets the
lean from the **sign of `pct_change_20d`** (absolute 20-day momentum), gated by a
±`momentum_neutral_band_pct` dead-band, the RSI mean-reversion / capitulation
flips, and the golden/death-cross suppression gates. It reads these feature keys:
`pct_change_20d`, `pct_change_5d`, `rsi_14`, `vol_ratio_20d`,
`dist_from_high_52w_pct`, `dist_from_low_52w_pct`, `golden_cross`, `death_cross`,
`atr_pct_14`.

`extract_technical_features` (`technical.py:371`, lines 530–569) **does compute**
`relative_strength_vs_spy_{5d,20d}` and `relative_strength_vs_sector_{5d,20d}` and
emits them as extra feature keys. **`derive_technical_verdict` never references any
of them** — confirmed by inspection: the only `features.get(...)` / `features[...]`
reads in the verdict function are the nine keys above. The RS features are
strategist-facing context, dead to the lean. This matches the audit's claim.

### 1.2 RS feature population in the iter-11 runs (measured)

Parsed all 60 traces × 20 tickers = 1 200 verdicts per run from
`…/runs/analysts-eval-iter-11/traces/<TS>.json →
04_digest.data[*].per_analyst.technical`:

| feature key | baseline present | iran present |
|---|---|---|
| `relative_strength_vs_spy_5d`    | 1200 (100 %) | 1200 (100 %) |
| `relative_strength_vs_spy_20d`   | 1200 (100 %) | 1200 (100 %) |
| `relative_strength_vs_sector_5d` | **0 (0 %)**  | **0 (0 %)**  |
| `relative_strength_vs_sector_20d`| **0 (0 %)**  | **0 (0 %)**  |

Lean mix (confirms the audit's 679/158/363 baseline figure):

| | bullish | bearish | neutral |
|---|---|---|---|
| baseline | 679 | 158 | 363 |
| iran     | 369 | 401 | 430 |

**`relative_strength_vs_sector_*` is entirely absent.** The extractor only emits it
when `raw["ratios"]["sector"]` resolves to a `SECTOR_TO_ETF` key *and* the matching
sector-ETF series is in `state["reference_prices"]`. The MEMORY note records the
sector fill as "a refetch in progress"; these artefacts predate it. **Any
sector-RS logic cannot be validated against current data** and must be written so
that an absent sector RS is a no-op, not a crash or a silent bullish bias.

### 1.3 How often is the lean bullish on an SPY-underperformer, and what does it cost?

Full-population join (all 679 / 369 bullish-lean verdicts, not the strategist-culled
traded subset), excess = ticker forward return − per-tick universe mean forward
return, replicating `scoreboard.py` / `reporting._forward_close` (base price = open
if `recorded_at.hour < 17` UTC else close; forward close = first cached bar in
`[base+h, base+h+4d]`):

**baseline (rising tape)**

| split | n | %of bullish | +20d excess | +20d up-rate |
|---|---|---|---|---|
| bullish, all                | 679 | 100 % | **−50.9 bps** | 40 % |
| bullish & `rs_spy_20d` < 0  | 56  | 8 %   | −153.3 bps | 32 % |
| bullish & `rs_spy_20d` ≥ 0  | 623 | 92 %  | −41.7 bps  | 41 % |

**iran (falling/volatile tape)**

| split | n | %of bullish | +20d excess | +20d up-rate |
|---|---|---|---|---|
| bullish, all                | 369 | 100 % | **+14.4 bps** | 43 % |
| bullish & `rs_spy_20d` < 0  | 4   | 1 %   | +878.5 bps | 100 % |
| bullish & `rs_spy_20d` ≥ 0  | 365 | 99 %  | +4.9 bps   | 42 % |

**Reading:** bullish-on-a-20d-SPY-laggard is *rare* (8 % baseline, 1 % iran) and in
baseline it does underperform the rest of the bullish book (−153 vs −42 bps). But
it is **far too small a slice to explain the −51 bps baseline pathology** — 92 % of
bullish leans already have positive RS-vs-SPY and still average −42 bps. The "fix
the laggard calls" lever touches the tail, not the body, of the problem.

### 1.4 The decisive counter-evidence: RS does not rank-order forward excess

Quintiles of `rs_spy_20d` vs +20d excess, **all 1 200 names** per window:

```
baseline   Q1 rs[-0.176,-0.044] excess +252.2 bps   <- biggest laggards WIN
           Q2 rs[-0.043,-0.009] excess  -63.2 bps
           Q3 rs[-0.009,+0.021] excess -120.0 bps
           Q4 rs[+0.022,+0.067] excess  -70.0 bps
           Q5 rs[+0.067,+0.500] excess   +1.0 bps

iran       Q1 rs[-0.252,-0.050] excess +202.3 bps   <- biggest laggards WIN
           Q2 rs[-0.050,-0.006] excess  -97.1 bps
           Q3 rs[-0.006,+0.026] excess -138.2 bps
           Q4 rs[+0.026,+0.076] excess  +40.9 bps
           Q5 rs[+0.077,+0.244] excess   -7.9 bps
```

Pearson r(`rs_spy`, forward excess), all names:

| | baseline +5d | baseline +20d | iran +5d | iran +20d |
|---|---|---|---|---|
| rs_spy_5d  | +0.069 | +0.047 | −0.128 | −0.104 |
| rs_spy_20d | +0.004 | −0.035 | +0.003 | −0.128 |

**The relationship is near-zero and changes sign across windows/horizons.** In
*both* windows the most-negative-RS quintile has the *highest* positive forward
excess — a contrarian, mean-reverting cross-section, the opposite of what a
momentum-style "lean with relative strength" rule assumes. The iran +20d sign is
flatly contrarian (r = −0.128: higher RS → lower forward excess).

The "suppress bullish on negative RS" delta confirms the fragility:

| | baseline +5d | baseline +20d | iran +5d | iran +20d |
|---|---|---|---|---|
| suppress neg-`rs_spy_5d` bullish | **helps** (−52 vs +24) | **hurts** (+14 vs −85) | hurts | hurts |
| suppress neg-`rs_spy_20d` bullish| helps (−38 vs +1) | **hurts** (−153 vs −42)* | hurts | hurts |

\* the rs_20d gate "helps" baseline at +20d only in the narrow sense that the 56
suppressed names underperform the bullish book; but the *all-names* quintile table
shows the broader negative-RS cohort *outperforms*, so a gate keyed on RS sign is
catching a small bad pocket while being wrong about the population.

**Conclusion for the problem statement:** the regime sign-flip is real (§1.3:
−51 bps baseline vs +14 bps iran on the identical bullish rule), and RS *is* the
unused feature. But RS-vs-SPY is **not a clean alignment lever**; using its sign to
drive the lean would degrade the iran window and is unsupported at honest power
(§4.3). The opportunity is narrower than the audit implies: modulate conviction,
not direction.

---

## 2. Design options (consequential — decide before implementing)

All three keep the existing absolute-momentum lean machinery intact and add an
RS read *after* the lean is set (so they compose with the golden/death-cross gates
and the RSI flips already in place).

### Option (a) — RS as a GATE (suppress/neutralise a conflicted bullish lean)

Neutralise a bullish lean when RS-vs-SPY is negative beyond a threshold
(`rs_spy_20d < −rs_gate_threshold`). Optionally symmetric for bearish + positive RS.

- **Effect on the sign-flip:** marginal and *fragile*. Helps baseline +5d, **hurts
  baseline +20d and all of iran** (§1.4). Only defensible if restricted to a single
  horizon/regime pocket, which is itself an overfit risk.
- **Implementation surface:** small — one conditional block + 1–2 config keys.
- **Overfitting risk:** **high.** The sign of the benefit flips across our only two
  windows. A gate tuned to baseline +5d is tuned to noise.
- **Interaction with existing gates:** composes after golden/death suppression; risk
  of double-suppression (golden-cross *and* RS gate both firing) over-neutralising
  the book and inflating abstention beyond the current 30 %.

### Option (b) — RS as the PRIMARY lean basis (sign of RS replaces/augments sign of pct20)

Lean = sign of a blended momentum/RS score, or sign of RS outright.

- **Effect on the sign-flip:** **likely negative.** §1.4 shows RS sign is
  anti-correlated with forward excess in iran (+20d r = −0.128) and non-monotonic in
  baseline. Leaning *with* RS would short the Q1 laggards that go on to outperform.
- **Implementation surface:** large — rewrites the core lean precedence; touches the
  no-data fingerprint, the band gate, and every downstream rationale tag.
- **Overfitting risk:** moot — it fails on the in-sample data, so it is rejected on
  evidence, not just prudence.
- **Interaction:** would subsume / conflict with the band gate and the cross gates;
  high blast radius.
- **Verdict: REJECT.** Recorded for completeness; the audit floated it, the data
  refutes it.

### Option (c) — RS modulates CONFIDENCE / magnitude only; lean direction unchanged

Leave the lean exactly as today. Scale confidence (and optionally magnitude) by an
RS-corroboration factor: a bullish lean *corroborated* by positive RS keeps/gains
confidence; a bullish lean *contradicted* by negative RS is damped (but still
bullish). Mirror for bearish.

- **Effect on the sign-flip:** does **not** flip the measured sign by itself (the
  scoreboard scores `position(±1/0) × excess`, and position is set by the lean, not
  confidence — see §4.1). It instead makes the strategist *weight* conflicted calls
  less, which is where the real-money benefit lands. **This is a strategist-facing
  improvement that the current analyst-only scoreboard largely cannot measure** —
  an honest limitation, stated up front (§4.4).
- **Implementation surface:** small and localised to the confidence/magnitude block
  (`technical.py` ~lines 808–861). No lean logic touched, so no regression to the
  hard-won iter-11 gates.
- **Overfitting risk:** **low.** Confidence cannot create a wrong-sign trade; the
  worst case of a mis-tuned factor is a suboptimal weight, not an inverted call.
- **Interaction:** composes cleanly after the existing `momentum_band_confidence_floor`
  ramp; both are multiplicative confidence scalers.

### Recommendation (requires sign-off)

**Adopt option (c) as the baseline change.** It is the only option whose downside is
bounded by the evidence: it cannot manufacture the catastrophic wrong-sign trades
that (b) would, nor the regime-fragile suppression that (a) would. It directly
serves the real consumer (the strategist weighting conflicted calls) rather than
the under-powered analyst scoreboard.

**Optionally, behind a *separate*, default-OFF flag**, add the narrow option-(a)
gate **only** as an experiment to be evaluated on a ≥6-month, regime-spanning window
with cluster-robust inference — never shipped ON based on the 2-window evidence.

This recommendation is **explicitly flagged as requiring mutual sign-off** per the
project's "consequential decisions" rule: it deliberately *declines* the audit's
headline suggestion (lean on RS), so the reviewer must actively agree that the
conservative read of the data is correct before any code is written.

---

## 3. Exact implementation surface

(Code below is illustrative for the spec — house style: docstrings + comments.
Final code lands in the implementation pass.)

### 3.1 Functions changed

Only **`derive_technical_verdict`** in `src/contract/extractors/technical.py`.
`extract_technical_features` is **unchanged** — it already emits the RS keys. No
other module changes.

### 3.2 New config keys (`config/analyst_heuristics.json`, `technical` block)

Validated in `src/agents/analysts/heuristics.py::TechnicalHeuristics`. All
default to the **no-op** value so merging the change is verdict-neutral until a
sweep justifies turning it on (mirrors how `beta_confidence_damping_enabled`
shipped OFF).

| key | type | default | range | meaning |
|---|---|---|---|---|
| `rs_confidence_modulation_enabled` | bool | `false` | — | master switch for option (c). When false, behaviour is byte-identical to today. |
| `rs_confidence_corroborate_step` | float | `0.0` | `0.0–1.0` | additive confidence boost when RS-vs-SPY corroborates the lean (positive RS on bullish / negative RS on bearish). `0.0` = no-op default. |
| `rs_confidence_contradict_step` | float | `0.0` | `0.0–1.0` | additive confidence *penalty* when RS-vs-SPY contradicts the lean. `0.0` = no-op default. |
| `rs_corroboration_window` | int | `20` | `{5, 20}` | which `relative_strength_vs_spy_{n}d` key feeds the modulation. |
| `rs_neutral_band` | float | `0.0` | `0.0–0.5` | dead-band on RS magnitude: `abs(rs) < rs_neutral_band` counts as neither corroborate nor contradict, so tiny RS noise does not toggle confidence. |

If the optional experimental gate (option a) is also wanted **for experiment only**:

| key | type | default | range | meaning |
|---|---|---|---|---|
| `rs_gate_enabled` | bool | `false` | — | experiment-only; suppress bullish lean when RS deeply negative. Ships OFF; do not enable without ≥6-month validation. |
| `rs_gate_threshold` | float | `0.0` | `0.0–0.5` | `rs_spy < −threshold` on a bullish lean → neutralise. `0.0` with `rs_gate_enabled=false` is a no-op. |

`config/README.md` **must be updated** with each key, its default, valid range, and
a one-line description (per the project config convention). The README entry should
also record the §1.4 caveat — i.e. that these are conviction modulators, **not** a
licence to lean on RS — so a future tuner does not mistake them for a momentum
signal.

### 3.3 Combination of vs-SPY and vs-sector, and the graceful (null) path

- **Primary signal: vs-SPY.** `relative_strength_vs_spy_{rs_corroboration_window}d`.
  100 % populated in current artefacts, so this is the only signal exercised today.
- **vs-sector when available:** when
  `relative_strength_vs_sector_{window}d` is present *and* non-null (it is absent in
  every current artefact — §1.2), combine as the **average of the two RS reads**
  *only if they agree in sign*; if they disagree in sign, fall back to vs-SPY alone
  (a conflicted cross-section read is not a conviction signal). This rule is dormant
  on current data and exists so the post-refetch runs light it up without a second
  code change.
- **Null path (mandatory):** read both with `features.get(...)` → `None` when absent.
  - If vs-SPY RS is `None` (e.g. <21 bars, or `reference_prices` unseeded): the whole
    RS modulation is a **no-op** for that verdict — confidence/magnitude unchanged,
    lean untouched, and a `rs_unavailable` factor is appended for traceability.
  - If vs-sector is `None` (the current universal case): silently use vs-SPY alone.
    No bias, no crash.
  - This must be asserted by a unit test (§4.2) — per the project's "silent failures
    are the recurring bug class" rule, the absent-RS branch must be *tested to be a
    no-op*, not assumed.

### 3.4 Illustrative confidence-modulation block (option c)

```python
# --- RS-vs-SPY confidence corroboration (option c, config-gated) ----------
# RS does NOT change the lean (see spec §1.4 — its sign is not a reliable
# forward-excess predictor in this universe). It only nudges CONFIDENCE so
# the strategist down-weights a directional call the cross-section contradicts.
if h.rs_confidence_modulation_enabled and lean != "neutral":
    # Pick the configured window; .get() yields None when the extractor could
    # not compute RS (insufficient bars / no reference prices) — treat as no-op.
    rs = features.get(f"relative_strength_vs_spy_{h.rs_corroboration_window}d")

    # Optional sector blend, dormant until the sector refetch lands (spec §3.3):
    rs_sec = features.get(f"relative_strength_vs_sector_{h.rs_corroboration_window}d")
    if rs is not None and rs_sec is not None and copysign(1.0, rs) == copysign(1.0, rs_sec):
        rs = (rs + rs_sec) / 2.0  # agree in sign → average; else keep vs-SPY alone

    if rs is None:
        # Graceful no-op: no RS available this tick. Tag for traceability only.
        factors.append("rs_unavailable")
    elif abs(rs) >= h.rs_neutral_band:
        # Corroboration = RS sign matches lean direction.
        lean_sign = 1.0 if lean == "bullish" else -1.0
        if copysign(1.0, rs) == lean_sign:
            confidence += h.rs_confidence_corroborate_step
            factors.append("rs_corroborates")
        else:
            confidence -= h.rs_confidence_contradict_step
            factors.append("rs_contradicts")
        confidence = max(0.0, min(1.0, confidence))
```

Placement: **after** the existing confidence modifiers and the
`momentum_band_confidence_floor` ramp (so RS modulation operates on the
already-damped value), and the lean is read-only here — guaranteeing no regression
to the iter-11 lean gates.

### 3.5 PIT / leak considerations

- RS is computed in `_relative_strength` with the `as_of` clamp already in place
  (`technical.py:118–193`): reference bars dated after `as_of` are dropped. **No new
  leak surface** — the verdict layer only *reads* the already-clamped feature; it
  does not touch raw bars.
- The verdict function remains a pure function of `features` + `h` — no I/O, no
  globals — so it stays table-test-friendly.
- No look-ahead is introduced: confidence is a function of same-tick features only.

---

## 4. Verification plan

### 4.1 What the scoreboard can and cannot see (read this first)

The scoreboard scores `position(±1/0) × excess`, where `position` is the **lean**
sign. Option (c) does **not** change any lean, so the **per-verdict score is
unchanged** and the analyst scoreboard's mean-excess/t-stat cells will be
*identical* before and after for option (c). That is expected and correct — the
benefit of (c) is on the *strategist's* weighting of conflicted calls, which the
analyst-only scoreboard does not exercise. **Do not interpret an unchanged
scoreboard as "no effect"** for option (c); interpret it as "effect is downstream
of the lean." The re-score procedure (§4.3) is therefore primarily relevant to the
*experimental* option-(a) gate, which *does* move leans.

### 4.2 Unit tests to add (assert positive behaviour, not just "runs")

In the existing technical extractor test module (table-driven, pure function):

1. **Corroboration boosts confidence.** Bullish lean (`pct20 > band`) +
   `relative_strength_vs_spy_20d = +0.05`, modulation enabled with a non-zero
   corroborate step → assert `confidence` is *strictly greater* than the same
   verdict with modulation disabled, and `"rs_corroborates"` in `key_factors`,
   and **`lean` unchanged**.
2. **Contradiction damps confidence.** Bullish lean + `rs_spy_20d = −0.05` →
   assert `confidence` strictly *less* than disabled-baseline, `"rs_contradicts"`
   present, **lean still bullish** (proves option c does not flip direction).
3. **RS dead-band is respected.** `abs(rs) < rs_neutral_band` → confidence equals
   the disabled-baseline (neither factor appended).
4. **Absent vs-SPY RS is a no-op (the silent-failure guard).** Feature key absent →
   confidence equals disabled-baseline, `"rs_unavailable"` appended, no crash.
5. **Absent vs-sector with present vs-SPY uses vs-SPY alone** (the current-data
   path) → behaves exactly as test 1.
6. **Sector blend only when signs agree.** vs-SPY `+0.05`, vs-sector `−0.05`
   (disagree) → vs-SPY alone is used (assert identical to test 1); vs-SPY `+0.05`,
   vs-sector `+0.03` (agree) → averaged RS used.
7. **Master switch off ⇒ byte-identical verdict** to pre-change for a battery of
   feature vectors (regression guard that the default ships verdict-neutral).
8. *(if option a is implemented)* **Gate neutralises a deep-negative-RS bullish
   lean** and appends `rs_gate_suppressed_bullish`; and **does not fire** when
   `rs_gate_enabled=false`.

### 4.3 Before/after re-score procedure (for the experimental gate, option a)

```bash
# Re-score both windows at the new code, default config (everything OFF) — must
# reproduce the existing iter-11 scoreboard byte-for-byte (regression gate).
PYTHONPATH=src .venv/bin/python -m scripts.backtest_scoreboard \
    --run backtests/baseline-2025-09/runs/analysts-eval-iter-11
PYTHONPATH=src .venv/bin/python -m scripts.backtest_scoreboard \
    --run backtests/iran-conflict-2026-02/runs/analysts-eval-iter-11

# Then flip rs_gate_enabled=true (option a experiment only) and re-score the SAME
# traces, comparing Technical's mean-excess SIGN/SIZE and the HONEST cluster-robust
# t — not naive p-values.
```

- The scoreboard now defaults to `scoreboard_neutralise_by="sector"` and
  `scoreboard_inference="cluster_ticker"` (`src/backtest/settings.py`), so judge any
  change by **mean-excess sign and size AND the honest (cluster-robust) t**, never by
  naive significance. The iter-11 audit established that the headline +20d/+5d
  findings are statistical mirages at honest power (honest t ≈ −0.5 / +1.4).
- Note: changing the lean (option a) requires **re-running the pipeline**, not just
  re-scoring, because the persisted verdicts in the existing traces were produced by
  the old logic. Re-scoring alone tests only metric-side changes. State explicitly in
  the implementation PR which path was used.

### 4.4 Overfitting guard — what would *falsely* look like success

We have exactly **two** windows, one rising, one falling, each ~1 month, with
overlapping forward windows (effective independent n ≈ 30–40 at +20d per the iter-11
power analysis). The following must be treated as **non-evidence**:

1. **A scoreboard cell improving in one window/horizon.** §1.4 shows the
   RS-gate benefit *flips sign* between baseline +5d (helps) and baseline +20d / iran
   (hurts). A single green cell is the expected outcome of noise on 2 windows.
2. **Naive-significance p-values.** Already known to be inflated ~4.5× by
   overlapping windows. Any "p < 0.05" on these windows is a hint, not proof.
3. **Tuning `rs_gate_threshold` / the confidence steps until baseline +20d turns
   positive.** That is curve-fitting to one of two windows; it will not generalise.
4. **Option (c) leaving the scoreboard unchanged being read as "broken."** Per §4.1
   that is the *expected* result; success for (c) is a strategist-level metric on a
   longer window, not the analyst scoreboard.

**Honest success criterion:** a change ships ON only if it improves mean-excess
**sign** in *both* regimes (or is sign-neutral in one and positive in the other)
with honest t not contradicting it, evaluated on a **≥6-month, regime-spanning
window** with cluster-robust inference. On the current 2 × 1-month evidence, the
only thing that ships ON is a **verdict-neutral default** (all new keys at their
no-op values); the actual tuning is deferred to the longer-window instrument the
iter-11 audit calls a prerequisite.

---

## 5. Open questions for sign-off

1. **Direction of travel (the big one).** The iter-11 audit recommends *leaning* on
   RS; §1.4 shows RS sign does not predict forward excess and is contrarian at the
   extremes, so this spec recommends the *opposite-conservatism*: RS modulates
   confidence only (option c), with the lean untouched. **Do you accept the
   conservative read, or do you want to see the aggressive option (b) backtested to
   failure first before rejecting it?** (Per the consequential-decisions rule, I need
   active agreement, not "no objection," before writing code.)

2. **Ship anything ON, or land it verdict-neutral?** My recommendation is to land
   option (c)'s machinery with every key at its **no-op default** (zero behavioural
   change), and only turn it on after a ≥6-month window exists. Do you want it dark
   like this, or do you want a non-zero default tuned to current windows (which §4.4
   argues is overfitting)?

3. **Is the experimental option-(a) gate worth implementing at all** if it ships OFF
   and cannot be honestly validated until the long window exists? Or should we defer
   it entirely to keep the surface minimal?

4. **Sector RS dependency.** `relative_strength_vs_sector_*` is 0 % populated today.
   Do we (i) write the sector-blend path now as dormant code (my recommendation —
   it's cheap and lights up post-refetch), or (ii) defer all sector handling until
   the refetch lands and the feature is verified non-null in a fresh run?

5. **Window prerequisite.** The iter-11 eval audit states a ≥6-month,
   regime-spanning window + a de-autocorrelated estimator (now shipped:
   `cluster_ticker`) are prerequisites for trustworthy tuning. Should this spec be
   *blocked* on that window existing, or proceed to land the dark machinery now and
   tune later?
