# Fundamental Analyst Audit (Phase 13)

**Scope:** the LLM-based Fundamental analyst (per-ticker fan-out), audited across four
backtest runs:

| Run | Window | Return | vs SPY | fundamental thinking_budget |
|---|---|---|---|---|
| `baseline-2025-09/analysts-eval-iter-9` | rising | +9.12% | +4.82% | **2048** |
| `baseline-2025-09/analysts-eval-iter-10` | rising | +4.16% | -0.14% | **512** |
| `iran-conflict-2026-02/analysts-eval-iter-9` | falling | -3.74% | +2.03% | **2048** |
| `iran-conflict-2026-02/analysts-eval-iter-10` | falling | -5.18% | +0.59% | **512** |

Watchlist (20): AAPL MSFT NVDA GOOGL AMZN META TSLA AMD AVGO CRM JPM BAC XOM CVX LMT
RTX JNJ UNH PG WMT.

This was a **read-only** investigation. No source, config, or artefact was modified.

---

## Methodology

The project `.venv` is absent, so no project modules were imported. Artefacts were
parsed with stdlib `json` (system `python3`); source was read as text.

The richest artefact is **`obs/traces/<TS>-{open,close}.json`** — OpenTelemetry spans.
Each `call_llm` span carries the full LLM request (`gcp.vertex.agent.llm_request`),
response (`gcp.vertex.agent.llm_response`), and crucially
`gen_ai.usage.experimental.reasoning_tokens_limit` (the thinking budget actually sent)
and `gen_ai.usage.experimental.reasoning_tokens` (the budget actually spent). A
`call_llm` span is attributed to a ticker by walking parent spans to the
`invoke_agent FundamentalAnalyst_<TKR>` ancestor.

**Fresh-vs-cache detector (validated):** a fresh model call produces a `call_llm` span
with token attributes and a multi-second duration; a cache hit produces a `call_llm`
span with **no** token attributes and ~2ms duration (it never reaches the model). This
was cross-checked against the on-disk cache and the per-tick `02_fundamental_verdict`.

Forward returns (`+1d/+5d/+20d`) live only in `decisions/<TS>__<TKR>__<side>.json`, so
calibration uses the traded-decision sample (644 decisions) — a strategist-filtered,
biased subset. Full-population lean distributions use the 4,800 trace verdicts.

---

## HEADLINE: the cache confound is REAL but works the OPPOSITE way to the hypothesis

The brief hypothesised that iter-10's fundamental outputs are mostly **byte-identical
cross-run replays of iter-9's 2048-budget reports**, so the 2048→512 change never
actually bit. **The evidence refutes this.** The opposite is true: the budget change
*did* propagate to essentially all iter-10 verdicts.

### How the cache actually works

- Cache key is `(input_hash, prompt_version)` (`src/agents/analysts/report_cache.py`).
  Neither component includes `thinking_budget` — so far the brief is correct.
- **But the on-disk store is a single-slot-per-ticker, last-writer-wins file**
  (`cache/reports/fundamental/<TKR>.json` holds exactly one record:
  `input_hash, prompt_version, verdict, report, stored_at`). When a ticker's input_hash
  changes (new filing rolls in), the slot is overwritten.
- The on-disk cache files are all stamped **2026-06-22** (iter-10's run day and later),
  i.e. iter-9's entries have been overwritten. iter-9 ran 2026-06-21, iter-10 on
  2026-06-22, **same `git_sha`** (`0cfe3f3`), so identical `prompt_version`
  (`auto:d9304b50497b`).

### The 83% "cache hit" rate is INTRA-run replay, not cross-run

Fresh-vs-cache, measured from `call_llm` spans across all 60 ticks:

| Run | fundamental LLM lookups | fresh | cache | fresh reasoning_limit |
|---|---|---|---|---|
| baseline iter-9 | 1200 | 204 (17.0%) | 996 (83.0%) | 2048 ×204 |
| baseline iter-10 | 1202 | 202 (16.8%) | 1000 (83.2%) | **512 ×202** |
| iran iter-9 | 1200 | 198 (16.5%) | 1002 (83.5%) | 2048 ×198 |
| iran iter-10 | 1200 | 198 (16.5%) | 1002 (83.5%) | **512 ×198** |

Two proofs this is intra-run, not cross-run:

1. **iter-10 tick 1: 18 of 20 tickers were FRESH** (only XOM and LMT hit the cache —
   the two whose iter-9 leftover slot happened to share an input_hash). If iter-10 were
   replaying iter-9 wholesale, all 20 would have hit on tick 1.
2. **Within iter-10, 906 cache-hit verdicts were checked against the most recent fresh
   verdict for the same ticker in the same run — 0 mismatches.** Cache hits replay
   *that run's own* earlier fresh call, generated at *that run's* budget.

So iter-10's reports were regenerated fresh at 512 tokens each time inputs changed, then
replayed within iter-10. The 512 budget reached ~every verdict.

---

## thinking_budget VERDICT: 2048→512 materially changed fundamental reasoning

There is a clean natural experiment: **202 (tick, ticker) cells were fresh in BOTH
baseline runs** — iter-9 at 2048, iter-10 at 512, on byte-identical input data. Direct
comparison:

| Metric | iter-9 (2048) | iter-10 (512) |
|---|---|---|
| Mean reasoning tokens spent | 1466 | 425 |
| Max reasoning tokens spent | 2046 | 510 (clamped) |
| Cells whose iter-9 reasoning exceeded 512 | **202/202 (100%)** | — |
| Output tokens (visible) | 732 | 732 |
| Summary length (chars) | 688 | 692 |
| Driver count | 4.0 | 3.9 |
| Schema parse failures | 0 | 0 |
| Truncated (`finish != stop`) | 0 | 0 |
| **Lean changed vs iter-9** | — | **90/202 = 44.6%** |

Lean transitions iter-9→iter-10: bullish→neutral ×47, neutral→bearish ×17,
neutral→bullish ×16, bearish→neutral ×7, bullish→bearish ×3.

- **Every one of the 202 fresh iter-9 cells used MORE than 512 reasoning tokens** (mean
  1466). Cutting to 512 therefore bit on 100% of fresh calls — the model was clamped to
  roughly a third of the reasoning it would otherwise use.
- The visible output is unaffected (output tokens, summary length, driver count all
  identical; zero truncation, zero schema retries). The damage is entirely in the
  **hidden reasoning depth**, not in output formatting.
- The result is a directional **softening**: net drift toward neutral, and the
  full-population lean distribution confirms it.

Full-population fundamental lean distribution (1200 verdicts/run):

| Run | bullish | neutral | bearish |
|---|---|---|---|
| baseline iter-9 (2048) | 39% | 42% | 19% |
| baseline iter-10 (512) | **25%** | **54%** | 21% |

Across all 1200 baseline cells, **45.0% of final fundamental leans differ** between
iter-9 and iter-10; only 21.8% are byte-identical. Confidence also shifted lower in
iter-10 (more 0.4–0.5 verdicts appeared).

**Conclusion:** the 2048→512 change is NOT a no-op hidden by the cache. It demonstrably
shrank reasoning on 100% of fresh calls and flipped ~45% of leans toward neutral. The
perf swing (+9.12%→+4.16%) is *consistent* with this fundamental softening — **but it
cannot be cleanly attributed to fundamentals alone**, because the strategist thinking
budget was also raised in iter-10 (a confounded A/B), and on the same calibration
evidence (below) the fundamental signal is barely better than a coin-flip in either run,
so the realised perf difference is plausibly dominated by strategist changes + market
noise on a 60-tick sample. The honest statement: the budget change altered fundamental
*outputs* materially; whether it *caused* the perf drop is not separable from the
co-varying strategist change in this data.

---

## Input quality

Sampled fresh prompt context blocks and swept all `analyst_inputs.fundamental` in the
decision files.

**Good (FIXABLE input is the minority):**
- Scalar ratios present for all 20 tickers (market cap, trailing P/E, revenue growth,
  margins, ROE, FCF, 50/200-day avg).
- Filings prose present and de-boilerplated correctly. XOM correctly sources MD&A from
  the **10-Q** (`[de-boilerplate vs 2024-06-30: 19 of 153 paragraphs removed]`), not the
  known 10-K cross-reference stub — the de-boilerplate header machinery works.

**Genuine input gaps (some FIXABLE, some UNPREDICTABLE):**

1. **forward_pe, peg, beta, analyst_rating_avg, sector, 52-week-high are null in 100%
   of decisions** (169/169 baseline, 153/153 iran). This is the single biggest input
   defect. The prompt leans hard on forward P/E and **explicitly instructs the model to
   anchor on forward P/E when trailing P/E is distorted** — that anchor never exists.
   Sector is also null, yet the prompt tells the model to judge multiples
   "relative to its sector". **FIXABLE** (provider/field-mapping gap).
2. **32 baseline (18 iran) decisions have trailing_pe > 200 (flagged "POSSIBLY
   DISTORTED") but forward_pe is null** — the model is told to fall back to forward P/E,
   which is absent. e.g. AMD trailing_pe = 676 with forward_pe = null. The valuation
   read collapses to "no anchor". **FIXABLE.**
3. **All 468 baseline / 454 iran 8-K filings have empty `body_excerpt` (and null
   mda/risk)** — 8-Ks contribute zero prose signal. 8-K body extraction is not
   populating. **FIXABLE.**
4. **Empty insider_trades**, concentrated by ticker: baseline LMT 9/9, XOM 4, UNH 2,
   RTX 2, JPM 2; iran AAPL 9, AVGO 7, CRM 2. LMT's empty insider block in this window is
   a known provider gap — **UNPREDICTABLE/insufficient input** for those cells (the
   prompt correctly treats absence as neutral, so no false signal, but half the evidence
   base is missing).

---

## Output quality

- **Reports are coherent and ticker-specific**, not boilerplate. The summaries argue a
  lean from named evidence (segment growth, P/E, insider flows) and the driver mix is
  consistent with the lean. No template-filling observed.
- **`rationale` is empty in 100% (4800/4800) of verdicts** — expected and correct: the
  LLM analyst does not emit `rationale` (it defaults to "" in the joiner; only
  deterministic extractors populate it). **The 200-char `verdict_rationale_max_chars`
  cap is therefore irrelevant to this analyst** — there is no mid-thought truncation to
  worry about here. The prose lives in `report.summary` (~690 chars, well within its
  cap; zero truncation observed).
- **`is_no_data` never fires (0/4800)** — even the thinnest-input cells (empty insider +
  stub filing) always emit a directional verdict rather than declaring no data. This
  manufactures signal from non-signal.
- **No schema retries, no truncation** in either budget. Output robustness is fine.

---

## Calibration & predictive power

Directional hit-rate on the 644 traded decisions (neutral excluded; bullish=up,
bearish=down):

| Horizon | n | hit-rate | mean signed return |
|---|---|---|---|
| +1d | 381 | 51.2% | +0.128% |
| +5d | 381 | 47.8% | +0.005% |
| +20d | 381 | **47.0%** | -0.034% |

The fundamental signal is **at or below coin-flip at every horizon**, and — contrary to
the expectation that fundamentals should matter MORE at +20d — it gets *worse* with
horizon (51%→48%→47%).

**Lean is anti-/non-predictive at +20d:**

| Lean | n | mean +20d return |
|---|---|---|
| bullish | 314 | +0.60% |
| neutral | 263 | +1.94% |
| bearish | 67 | **+3.02%** |

Bearish names *rose the most*; neutral names beat bullish names. The bearish lean is an
inverted signal in this sample.

**Confidence does not track accuracy (overconfidence):**

| Confidence bucket | n | +20d hit-rate |
|---|---|---|
| 0.6–0.7 | 15 | 13.3% |
| ≥0.7 | 366 | 48.4% |

Almost all directional calls are ≥0.7 confidence, and they hit 48%. Confidence is
bunched in 0.5–0.8 (never <0.4, rarely 0.9), so it carries no discriminating
information.

**Magnitude does not scale with realised move** (mean |+20d| by magnitude bucket:
4.79% / 7.68% / 5.35% / 6.12% — non-monotonic noise).

**Per-run +20d hit-rate:** base9 54.7%, base10 59.0%, iran9 40.5%, iran10 38.8%. Note
base10 (the "bad" run) actually had a *higher* fundamental hit-rate than base9 — further
evidence the perf swing is not driven by fundamental signal quality.

---

## Confidently-wrong examples (concrete)

125 high-confidence (≥0.7) leans were wrong by >3% against their direction at +20d.
Worst cases:

1. **GOOGL, base10, 2025-09-02 — bearish, conf 0.7, mag 0.6 → +21.3% at +20d.**
   key_factors include `planned_sale_dominant` AND `cluster_selling`. The model leaned
   bearish primarily on insider selling it *itself tagged as planned (10b5-1)*. Hard rule
   **R1 says planned-dominant selling must be treated as neutral noise and must NOT drive
   a bearish lean.** This is a rule violation, and the stock rallied 21%. (See R1
   adherence note below — this is one of the rare violations.)
2. **META, iran9, 2026-03-10 — bullish, conf 0.7, mag 0.7 → -17.9% at +20d.** The model
   correctly read strong fundamentals + high P/E and leaned bullish; the stock dropped
   18% in a falling (Iran-conflict) market. Mechanism: fundamentals are slow-moving and
   the macro shock dominated — fundamental confidence on a structurally strong, expensive
   name gave no edge against a market-wide drawdown. **UNPREDICTABLE from fundamentals.**
3. The same META bullish verdict recurs across consecutive ticks (intra-run cache
   replay), so a single wrong fresh call propagates to many decisions until inputs change
   — amplifying one mistake.

**R1 adherence (systematic):** across 4800 verdicts, only **48 of 1028 bearish verdicts
(4.7%)** co-tag `planned_sale_dominant`; among all planned-sale-tagged verdicts the lean
split is bullish 432 / neutral 418 / bearish 48. So R1 is *mostly* obeyed — the GOOGL
case is a rare exception, not a systemic leak. Worth a targeted fix but not the headline.

---

## Prioritised findings & recommendations (recommendations only — no code changed)

**P1 — Fundamental signal has no predictive edge (this is the real problem, not the
budget).** +20d hit-rate 47%, bearish lean inverted (+3.02%), confidence uncorrelated
with accuracy, magnitude uncorrelated with move size. Before tuning budgets, establish
whether this analyst adds *any* alpha. Recommend: compute the analyst's standalone
information coefficient on the full (untraded) verdict population vs next-tick returns,
and consider down-weighting or gating fundamentals in the strategist until it clears a
bar. The +20d-should-beat-+1d expectation fails here.

**P2 — Restore thinking_budget to 2048 for the Fundamental analyst (or A/B it
un-confounded).** The 512 budget clamped 100% of fresh calls (all needed >512) and
flipped 45% of leans toward neutral, halving the bullish share. Whatever signal exists is
being degraded by under-resourced reasoning. Critically, **re-run iter-9 vs iter-10
changing ONLY the fundamental budget** (hold the strategist budget fixed) — the current
comparison is confounded by the simultaneous strategist change, so the perf attribution
is not currently sound.

**P3 — Fill the valuation inputs: forward_pe, peg, sector (and beta, analyst rating,
52w range).** All null in 100% of decisions. The prompt's core method (anchor on forward
P/E, judge multiples sector-relative, fall back to forward P/E when trailing is
distorted) is structurally unsupported by the data. 32 baseline cells hit the
"distorted trailing P/E, no forward anchor" trap. Fixing this is likely higher-leverage
than any prompt edit.

**P4 — Populate 8-K body_excerpt.** 468/468 baseline 8-Ks carry no prose. 8-Ks are the
catalyst/earnings/guidance channel the prompt explicitly relies on; right now they are
dead weight in the filings list.

**P5 — Make `is_no_data` actually fire on thin input.** It never triggers (0/4800).
Cells with empty insider AND stub/short filings (e.g. LMT's 9 empty-insider ticks) still
emit a confident directional lean — manufacturing signal from absence. Either honour
`is_no_data`, or hard-cap confidence when the evidence base is half-empty.

**P6 — Confidence is uninformative; recalibrate or stop trusting it.** Bunched in
0.5–0.8, ≥0.7 calls hit only 48%. If the strategist weights by confidence, it is
weighting noise. Recommend post-hoc confidence recalibration against realised hit-rate,
or collapse confidence to a coarser, validated scale.

**P7 — Cache amplifies single mistakes (intra-run replay).** One wrong fresh verdict
(e.g. META bullish) is replayed across every subsequent tick until inputs change, so a
single bad call drives many decisions. Lower-priority, but worth noting: consider a
max-age / re-evaluation cadence so stale verdicts don't dominate a multi-week window.

**P8 — R1 (planned-sale) edge cases.** 48 bearish verdicts violate R1. Rare (4.7%) but
includes the worst miss (GOOGL +21%). A targeted prompt reinforcement or a deterministic
post-check (drop bearish lean when planned_sale_ratio ≥ 0.80) would close it cheaply.

---

## Is poor performance fixable or just insufficient input?

**Mixed, leaning "fixable inputs + an analyst that may not have edge".** The pipeline is
*not* mangling good input — de-boilerplate works, filings prose is clean, reports are
coherent. But (a) the valuation inputs the prompt depends on are entirely missing (P3/P4
— clearly fixable), and (b) even with the 2048 budget the signal is ~coin-flip and
mis-calibrated (P1/P6 — possibly a deeper "fundamentals don't move 1–4-week price"
limitation, i.e. partly UNPREDICTABLE). The thinking_budget cut is a genuine regression
in reasoning depth (P2) but is *not* the proven cause of the perf swing, because it is
confounded with the strategist change and the fundamental signal is weak in both runs.
