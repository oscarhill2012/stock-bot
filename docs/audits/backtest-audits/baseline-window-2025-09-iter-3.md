# Baseline window 2025-09 — audit iteration 3

**Run audited:** `backtests/baseline-2025-09/runs/full-backtest-iter-3/`
**Window:** 2025-09-02 → 2025-10-13 (60 ticks, 30 trading days × open + close)
**Git SHA:** `983d131`
**Headline result:** total return **+6.34 %**, Sharpe 5.08, max drawdown
−2.21 %, **+3.36 pp vs matched-exposure SPY** (Information Ratio 2.10),
avg equity exposure 76.6 %. **3 closed round-trips, 0 % win rate.** LLM
cost 11.1 M tokens across 1 855 model calls, 25.2 % cache hit rate.

Iter-3 is the first run on this window where the bot **beats SPY on an
exposure-matched basis** — a genuine reversal of the iter-1/iter-2
underperformance (−2.27 pp / −2.39 pp vs-SPY). The headline is real:
+3.36 pp matched-exposure alpha with IR 2.10 is not a rounding artefact.

But the result demanded scrutiny precisely *because* it is good. Two
facts sit in tension: the bot made money, yet **all three of its closed
round-trips were losses** (AMD −4.1 %, XOM −1.9 %, CRM −5.9 %) and, as
this audit shows, **none of the three analyst signals is well-calibrated
in this window**. The +6.34 % is carried by unrealised gains on 11
still-open positions and by the strategist's *entry selection*, not by
the mechanical analyst leans feeding it. This iteration's job was to
establish whether the good number rests on sound machinery or on a bull
tide lifting a 77 %-long book.

This audit was run as a five-agent fan-out: one agent on the
**analyst → strategist information surface** (data integrity / silent
degradation), and one agent each on the **technical, fundamental, news**
analysts and the **strategist**, correlating every verdict against
realised forward returns (+1 d / +5 d / +20 d).

The bug numbering continues from the long-baseline iter-1 audit (which
ended at #19).

---

## 1. Audit scope

Materials walked:

- `manifest.json` — run metadata (SHA `983d131`, 60 ticks, 0 failed, 20-ticker watchlist).
- `report/metrics.md` — headline figures and per-agent latency.
- `decisions/*.json` — 35 strategist decision payloads (full analyst
  evidence as the strategist saw it, plus realised forward returns).
- `db.sqlite` — `trade_log` (3 closed trades), `ticker_stances` (1 200),
  `analyst_evidence` (3 600 = 3 analysts × 20 tickers × 60 ticks),
  `portfolio_snapshots` (60).
- `audit/*.tick.json` — 60 per-tick integrity records (tripwires, per-domain row counts).

For the analyst-quality agents, a joined dataset was built
(1 200 rows = 20 tickers × 60 ticks) pairing each analyst's
lean / magnitude / confidence with forward close-to-close returns,
authoritative for the 35 traded rows and computed for the rest so that
"ignored wins" (winners the bot never bought) were answerable.

**Window-bias caveat.** This is a six-week sustained bull window
(baseline mean fwd_5d ≈ **+1.28 %**, fwd_20d ≈ **+4.42 %** — almost
everything rose). All edge claims below are stated **relative to that
baseline**, not in absolute terms; a 60 % bullish hit-rate is worthless
when the neutral baseline is already +1.3 %. Several findings (esp. the
anti-predictive analyst leans) could be window artefacts and must be
re-checked against a mixed/stress window (`iran-conflict-2026-02`)
before being treated as structural.

---

## 2. The reassuring half: the plumbing is clean

Before the defects, what the surface audit **verified sound** — this is
load-bearing, because it means the problems below are in the *signals*,
not in the wiring:

- **Fidelity.** `analyst_outputs.per_analyst` equals
  `strategist_view.ticker_evidence` **byte-for-byte** across all 35
  decisions. The strategist sees exactly what the analysts emit — no
  corruption, truncation, reordering, or dropped analyst.
- **Aggregation math.** `aggregate.magnitude / confidence / disagreement`
  and the `"N bullish / M neutral / K bearish"` summary reconcile to
  floating-point precision across all 35 decisions. Zero mismatches.
- **No forward-return leakage** into strategist reasoning; analyst
  evidence `recorded_at` aligns to its tick on every row.
- **Weights** are a clean `{technical: 1, fundamental: 1, news: 1}` with
  no drift.

The handoff is trustworthy. Everything below is a signal-quality or
data-population problem, not a wiring problem.

---

## 3. Findings

### Bug #20 — `vol_ratio_20d` is NaN for 98.3 % of rows; reaches the strategist as the literal string `"nanx"`  ·  **HIGH**

**Symptom.** The technical extractor emits `vol_ratio_20d: NaN` (literal,
non-standard JSON `NaN`) for **1 180 / 1 200** evidence rows. The key is
present (not absent), so the strategist-prompt renderer formats it via
`f"{nan:.1f}x"` → `"nanx"` and the strategist sees
`Volume vs 20d avg: nanx` on essentially every ticker on every tick.

**Root cause.** The 20-day volume-ratio computation requires ≥ 50 bars;
the cache window opens 2025-08-04 (~43 bars), reaching 50 only on the
final tick. The extractor deliberately uses `NaN` to mean "not
computable" (vs `0.0` = "truly zero"), but the prompt renderer
special-cases only `None`, never `float('nan')`. Independently confirmed
by both the surface agent and the technical agent.

**Impact.** The volatility-regime input is silently absent for the entire
run. The technical analyst is running on a degraded feature set, and the
strategist prompt carries a nonsense token 1 180 times.

**Fix direction.** Either omit the key when NaN, or teach the renderer to
map NaN → `"(no data)"`. Separately, widen the cache warm-up so 50 bars
exist at window open.

---

### Bug #21 — Fundamental analyst never emits `bullish` (0 / 1 200); dilutes aggregate conviction by 21.5 %  ·  **HIGH**

**Symptom.** Across all 1 200 fundamental verdicts: **0 bullish**, 1 120
neutral (93.3 %), 80 bearish. Magnitude is exactly 0 on 61.3 % of rows.
For **15 of 20 tickers** the verdict is constant-neutral for all 60
ticks. NVDA — +171 % revenue growth, 28 % ROE, the strongest AI name in
the book — receives `neutral, mag 0.0, conf 0.3` on every single tick,
identical to a ticker with no data at all.

**Mechanism.** Two compounding causes: (a) the bullish decision path
either does not exist or has thresholds no megacap can clear; (b) insider
"planned_sale_dominant" damping is treated as a **direction veto** — it
neutralises NVDA's growth outright rather than merely trimming
confidence.

**Quantified dilution.** Mean aggregate magnitude with fundamental
included = **0.287**; counterfactual tech+news-only = **0.365**.
Fundamental's near-constant neutrality suppresses aggregate conviction by
**−0.078 (21.5 %)** on every tick, and in 19 ticks it was actively
bearish against a confirmed tech+news bull consensus.

**Redeeming note.** Its *bearish* signal (CVX, PG, AVGO, GOOGL; n = 27
with fwd data) does carry edge: −2.93 pp vs baseline at fwd_20d, 68 %
hit-rate. The analyst can discriminate; it has simply been configured
into permanent caution.

**Fix direction.** Add/repair a bullish branch with megacap-realistic
thresholds; convert insider-sell damping from a veto to a confidence
multiplier; as a stopgap drop fundamental's aggregate weight from 1.0 to
0.5 until bullish works (recovers the 21.5 % suppression).

---

### Bug #22 — Strategist exits are deployment-cap ejections at local bottoms, not thesis exits; the 80 % cap blocks the best names  ·  **HIGH**

**Symptom.** All 3 closed round-trips were losses, and **8 of 11 sell
events cite over-deployment** (`trimming_*`, `at_deployment_limit`)
rather than a broken thesis. Each realised loss was sold into a recovery:

| Ticker | Sold | Realised | fwd_5 d after sale | Note |
|--------|------|---------:|-------------------:|------|
| AMD | 2025-09-09 @ $152 | −4.1 % | **+6.0 %** | Re-bought 2025-10-03 @ $171 — a +12 % re-entry premium on a name it had just sold at a loss |
| XOM | 2025-10-01/02 | −1.9 % | +2.0 % | Least premature; thesis genuinely weak |
| CRM | 3 trims | −5.9 % | +3–4 % after each | Each trim sold into a bounce |

**Collateral damage from the cap.** GOOGL — bullish-tech on 56 / 60
ticks — was trimmed from a 10 % position down to **0.87 %** to fund other
buys (≈ $1.5 k of unrealised gain surrendered). **TSLA was never bought**
despite 59 / 60 bullish-tech ticks and a +23.5 % window move; it sat
blocked behind a `thesis_refinement_at_deployment_limit` stance that
fired on the entire watchlist for **17 consecutive ticks** while the book
was pegged at 79.5–80.5 %.

**Counterpoint — entries are good.** The 24 buys averaged fwd_20d +4.3 %
at a 73 % hit-rate, and 100 % had bullish news at entry. The strategist's
*entry* judgement is where the run's alpha lives. Position sizing,
however, shows no edge yet (corr(weight, fwd_5d) ≈ 0.03) — the new
conviction-weighting is not biting.

**Fix direction.** Raise the hard 80 % ceiling toward 88–90 % or make it
a soft target; rank trim candidates by stop-loss / signal-reversal rather
than "lowest conviction"; add a minimum hold before trim-eligibility and
a re-entry lockout after a non-stop exit (would have prevented the AMD
sell-low/rebuy-high sequence).

---

### Bug #23 — Technical analyst is anti-predictive in a trending regime; treats momentum continuation as reversal risk  ·  **MEDIUM-HIGH**

**Symptom.** Bullish leans *underperform* baseline (fwd_5d −0.15 pp,
fwd_20d −1.01 pp); bearish leans *outperform* (fwd_5d +0.36 pp, fwd_20d
+2.47 pp). The fwd_5d inversion is noise (p = 0.17) but the **fwd_20d
inversion is significant (t = −3.15, p = 0.0018, Cohen d = −0.36).**

**Why.** Factor-level analysis shows `rsi_overbought`, `near_52w_high`
and `trend_up_20d` are all treated as cautionary, yet in this window all
three mark the strongest forward performers. `trend_down_20d` → bearish,
but those names (AMD, NVDA) then snapped back hardest. AMD was bearish
continuously from tick 1 to tick 42, then ran **+43.6 %** fwd_5d. The
analyst is reading a lagging trailing trend as a forward predictor.

**Calibration.** Confidence is a coarse 4-value categorical (61 % of rows
pinned at 0.9) and is **anti-calibrated**: r(conf, fwd_5d) = −0.088 — the
most confident calls do worst.

**Secondary defects.** `beta_confidence_damping` is **always 0.0** (dead
computation). `pct_change_20d = 0.0` sentinel fires for all 20 tickers on
tick 0 (off-by-one on the `> 20` bar guard + the Labor Day gap), and
`is_no_data` fails to catch it because ATR is non-zero — so the
strategist sees "+0.0 % 20d momentum" for the entire watchlist on the
first, all-buys tick.

**Fix direction.** Gate reversal factors behind a trend-regime flag (only
treat `rsi_overbought` / `near_52w_high` as bearish when the 20d trend is
flat/down); remove or repair `beta_confidence_damping`; make
`pct_change_20d` emit `None` (→ "(no data)") when uncomputable. **Do not
over-fit to the 20d inversion** until it is reproduced on a non-bull
window.

---

### Bug #24 — News analyst: bearish calls anti-predictive (83 % wrong), confidence inverted, hype-chasing on 7 tickers  ·  **MEDIUM**

**Symptom.** Bullish leans show **no significant edge** over baseline
(t = 0.025, p = 0.98). Bearish leans (n = 29) are **anti-predictive**:
83 % were followed by a rally, mean fwd_5d **+3.25 %**. Confidence is
inverted — r(conf, fwd_5d) = −0.096 for bullish rows; the model gets
*more* confident as it cites *more* catalysts, which coincides with peak
sentiment. For 7 tickers (AMZN, META, CRM, UNH, CVX, XOM, JPM) bullish
news predicts *negative* forward returns — it is reading
analyst-upgrade / AI-narrative articles that the market has already
priced.

**Why it matters more than the others.** News is the swing vote: it takes
a real side far more often than fundamental and is the analyst the
strategist most often follows into a buy. Its lean also flip-flopped AMD,
XOM and CRM into and out of positions at poor times (it cited the
"key downgrade" that helped trigger the AMD sell-at-the-bottom).

**Fix direction.** Gate or contrarian-weight the bearish signal; add a
novelty/recency filter on `key_factors` to suppress re-reading the same
catalyst tags (72 % of ticks repeat the prior lean); deflate confidence
when many catalysts cluster ("narrative avalanche").

---

### Bug #25 — `analyst_evidence.rationale` empty for all 1 200 rows; reasoning never persisted to the DB  ·  **MEDIUM**

**Symptom.** The top-level `rationale` column is a zero-length string on
**every** row for fundamental and news (and technical's is terse
key-factor text only). The rich LLM reasoning lives in the nested
`verdict.report` object, which `analyst_evidence` has no column for and
which is persisted only to the decision JSONs (traded tickers only).

**Impact.** The obvious audit query — `SELECT rationale FROM
analyst_evidence` — returns blanks, a silent lie. Reasoning for the 985
non-traded ticker-ticks is lost from the database entirely, and any
rationale-truncation bug would be undetectable at the DB level.

**Fix direction.** Add a `report_json` (or populate `rationale` from
`report.summary`) in the persistence layer.

---

### Bug #26 — GOOGL / CRM `revenue_growth_yoy` wrong (−44 % / −46 %): period-duration mismatch in `_ttm_at`  ·  **MEDIUM — VERIFIED, distinct unfixed bug**

**Symptom.** The fundamental features show GOOGL `revenue_growth_yoy =
−44.2 %` (constant, all 60 ticks) and CRM `−45.8 %` (first 4 ticks, then
+9.5 % from tick 5 on). GOOGL's profit_margin of 58.7 % is incompatible
with a 44 % revenue collapse.

**Verified root cause (not what the audit first assumed).** This is
**not** stale cache and **not** the concept-selection bug we already
fixed. The window was fetched 2026-06-14, ~21 h *after* the
concept-selection fix (`b1e78d5`) landed, so the correct revenue concept
is being selected. The remaining defect is a **period-duration
mismatch** in `_ttm_at` (`pit_composite.py`, ~L306-337): it calls
edgartools `FactQuery.latest()`, which returns the single most-recently-
**filed** fact with **no filter on reporting-period length**. So:

- current leg at `as_of = 2025-09-02` → GOOGL's **6-month YTD** revenue
  (~$175 B, from the Q2-2025 10-Q, the latest filing as of that date);
- prior leg at `2024-09-02` → a **12-month annual** value (~$307 B, from
  the FY-2023 10-K, the latest filing before that date);
- ratio = (175 − 307) / 307 ≈ **−43 %** (observed −44.2 %).

The `_select_revenue_series` period-distinctness guard (`now ≠ prior`)
does **not** catch this — the two legs *are* distinct, they just span
different-length periods. CRM's jump to +9.5 % at tick 5 is the same
mechanism resolving: CRM filed its Q2-FY26 10-Q mid-window, after which
both legs become 6-month and the comparison becomes apples-to-apples.

**Scope.** Likely affects **any megacap whose latest filing as-of is a
mid-year YTD**, not just GOOGL/CRM. The prior commits did not touch this:
`b1e78d5` fixed *which concept* to read (GOOGL/CRM were not in its
validation set); `29481ef` fixed the `--refetch-domain` plumbing only.

**Fix path.** Constrain `_ttm_at` to **annual (12-month duration)** facts
for both legs — e.g. edgartools `latest_periods(n=1, annual=True)` or a
post-filter on `period_end − period_start ≈ 12 months` — so current and
prior always cover the same reporting window. Then refetch
`company_ratios` for `baseline-2025-09` (and the two known-stale windows
`long-baseline-2025` / `iran-conflict-2026-02`).

**Mitigating note.** The wrong value did not cause wrong *trades* —
fundamental rated both names neutral throughout (Bug #21), so the bad
data produced noise, not a bad bearish call.

---

### Bug #27 — Dead inputs and empty observability tables  ·  **LOW**

A cluster of minor waste / cosmetic gaps, grouped:

- `smart_money` and `social` appear as keys in `analyst_inputs` on all 35
  decisions but are **always `null`** — fetched (or scaffolded) yet never
  weighted or scored. Wasted work and misleading "we tried" scaffolding.
- `ticker_evidence` table is **empty (0 rows)** — the aggregate is written
  only to decision JSONs.
- `portfolio_snapshots.bot_return_pct / spy_return_pct / excess_return_pct`
  are **0.0 on all 60 rows** — never backfilled. Harmless (metrics.md
  derives returns from `bot_total_value`) but a trap for any DB consumer.

**Fix direction.** Either wire `smart_money` / `social` into the
aggregate or stop fetching them; backfill `ticker_evidence` and the
snapshot return columns, or drop the columns.

---

## 4. The central tension, stated plainly

The bot made **+3.36 pp of matched-exposure alpha** while running three
analyst signals that are, respectively, anti-predictive (technical),
inert (fundamental) and noisy (news). The alpha therefore is **not coming
from the mechanical analyst leans.** The most likely source is the
**strategist's own entry selection** — it reads the analysts' full
`report` narratives (not just the lean tokens) and buys names with
bullish news + bullish tech, and those entries did genuinely outperform
(fwd_20d +4.3 %, 73 % hit). In other words the LLM strategist is doing
the discriminating work the upstream numeric verdicts fail to do.

This is good news (the system has a working brain) and a warning (much of
the analyst apparatus is currently decorative or counterproductive, and a
chunk of the headline is simply being 77 % long in a bull market).

---

## 5. Recommendation on sequencing the model-provider experiment

The stated next step is to trial Anthropic / OpenAI models via Google
ADK's LiteLLM layer. **Several findings above are mechanical bugs, not
reasoning failures** — `vol_ratio` NaN (#20), the missing fundamental
bullish branch (#21), the 80 % deployment cap (#22), the stale GOOGL/CRM
revenue (#26). A model swap fixes none of these, and worse, **they would
confound the A/B comparison**: a new model's score would be polluted by
whether it happened to route around a broken feature.

Recommended order:

1. Fix the cheap mechanical confounds first — at minimum #20, #21, #26,
   and decide the #22 cap policy.
2. Re-run the window clean to get an unconfounded baseline.
3. *Then* swap providers, so the comparison measures reasoning quality
   rather than bug-avoidance.

This is a judgement call, not a settled conclusion — open to discussion.

---

## 6. Bug ledger (this iteration)

| # | Severity | One-line |
|---|----------|----------|
| 20 | HIGH | `vol_ratio_20d` NaN 98.3 % → `"nanx"` reaches strategist |
| 21 | HIGH | Fundamental never bullish (0/1200); dilutes aggregate 21.5 % |
| 22 | HIGH | Deployment-cap exits at local bottoms; blocks TSLA/GOOGL |
| 23 | MED-HIGH | Technical anti-predictive in trend; conf anti-calibrated; 2 dead/sentinel features |
| 24 | MEDIUM | News bearish 83 % wrong; confidence inverted; hype-chasing |
| 25 | MEDIUM | `analyst_evidence.rationale` empty for all 1200 rows |
| 26 | MEDIUM | GOOGL/CRM revenue_growth_yoy wrong — VERIFIED: `_ttm_at` period-duration mismatch (6mo vs 12mo legs); fix + refetch |
| 27 | LOW | Dead inputs (smart_money/social) + empty observability tables |

**Verified clean:** analyst→strategist fidelity (byte-exact),
aggregation math, weights, timestamp alignment, no forward-return leakage.
