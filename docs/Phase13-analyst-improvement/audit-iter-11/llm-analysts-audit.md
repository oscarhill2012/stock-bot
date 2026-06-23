# LLM analysts audit — iter-11 (fundamental + news)

Read-only critical audit of the **fundamental** and **news** LLM analysts.
Scope excludes the technical analyst (separate audit), and social / smart_money
(shelved). British English throughout. No source, config or artefact was
modified.

Runs analysed (both iter-11):

- Baseline (rising market): `backtests/baseline-2025-09/runs/analysts-eval-iter-11/`
- Iran-conflict (falling/volatile): `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-11/`

---

## 1. Methodology — what was parsed

All parsing used `.venv/bin/python` with stdlib only (`json`, `glob`,
`statistics`) plus the run's own `metrics.md`. Source surfaces were read in
full (`fundamental/prompts.py`, `news/prompts.py`, `fetch.py` for both,
`deboilerplate.py`, `_common.py`, `contract/evidence.py`, `backtest/scoreboard.py`,
`config/analysts.json`, the vocab block of `config/analyst_heuristics.json`).

Counts parsed:

| artefact | baseline | iran |
|----------|---------:|-----:|
| trace files (`traces/<TS>.json`) | 60 | 60 |
| ticker-ticks per run (`04_digest.data`) | 1 200 | 1 200 |
| decision files (verdict↔fwd-return joins) | 164 | 147 |
| fundamental verdicts emitted (all leans non-no-data) | 1 200 | 1 200 |
| news verdicts emitted (all leans non-no-data) | 1 200 | 1 200 |

Every one of the 1 200 ticker-ticks carries a populated fundamental **and**
news verdict with a `report` block — there are **zero `is_no_data` verdicts**
in either run for either analyst. Data reaches both LLMs on every tick.

### 1.1 Why the scoreboard `n` is so small (the dedup)

`src/backtest/scoreboard.py` collapses **consecutive identical
`(lean, magnitude, confidence)` tuples** for each `(analyst, ticker)` pair into
a single observation (cache-replay correction, lines 312–354). Re-deriving that
collapse from the traces reproduces the scoreboard `n` exactly:

| analyst | run | rows emitted | fresh (deduped) | replay % |
|---------|-----|-------------:|----------------:|---------:|
| fundamental | baseline | 1 200 | **133** | 88.9 % |
| fundamental | iran | 1 200 | **132** | 89.0 % |
| news | baseline | 1 200 | **737** | 38.6 % |
| news | iran | 1 200 | **691** | 42.4 % |

So the headline `n` (fundamental 133, news 737 baseline) is the count of
*fresh* verdicts. Fundamental verdicts change ~7×/ticker over 60 ticks (the
report cache holds a slowly-moving filing view); news changes ~37×/ticker.
This is correct behaviour — but it means the **fundamental analyst contributes
only ~130 independent observations per month-long window**, and the bearish
subset only ~25. That single fact governs everything below.

### 1.2 Lean distributions and mean confidence (all 1 200 rows)

| | baseline fund | iran fund | baseline news | iran news |
|--|--|--|--|--|
| bullish | 516 | 582 | 541 | 463 |
| neutral | 418 | 306 | 622 | 660 |
| bearish | 266 | 312 | 37 | 77 |
| mean confidence | 0.663 | 0.695 | 0.686 | 0.685 |

Neither analyst is shy: fundamental bets directionally ~70 % of the time and
news ~40–45 %. Mean confidence sits at ~0.67–0.70 regardless of horizon or
regime — confidence is **not well differentiated** (see Finding F-4 / N-3).

---

## 2. Fundamental analyst

### F-1 [EVAL] The fundamental scoreboard is structurally underpowered — this is the dominant bottleneck

Empirical +20d cross-sectional excess-return standard deviation (computed from
the traded names): **572 bps (baseline), 442 bps (iran)**. With that dispersion:

| n (fresh verdicts) | std err of mean | min effect detectable at p<0.05 | effect needed for 80 % power |
|---:|---:|---:|---:|
| 25 (bearish subset) | ~120 bps | ~235 bps | ~336 bps |
| 50 (bullish subset) | ~85 bps | ~166 bps | ~238 bps |
| 130 (all) | ~53 bps | ~103 bps | ~147 bps |

A realistic single-name fundamental edge is **30–50 bps**. Detecting a 50 bps
edge at this dispersion needs **~1 100 fresh verdicts**; a 30 bps edge needs
**~3 100**. We have 130. **No prompt change can move the fundamental t-stats to
significance on a one-month, 20-ticker window** — the verdicts simply do not
move slowly enough to generate independent observations, and the cross-sectional
noise dwarfs any plausible signal.

The iter-10 → iter-11 deltas confirm this empirically. They are sign-flipping
noise, not progress:

| cell (+20d) | iter-10 | iter-11 | Δ |
|--|--:|--:|--:|
| baseline fund bullish | **+126.6** bps | +15.7 bps | −111 |
| baseline fund bearish | −198.3 | −227.1 | −29 |
| iran fund all | −38.7 | −21.3 | +17 |
| iran fund bullish | **−29.4** | **+47.0** | +76 (sign flip) |

A bullish cell swinging 111 bps in one direction (baseline) while the matching
iran cell swings 76 bps the *other* way, on n≈50, after a prompt re-anchor that
was directionally sensible — that is the signature of sampling variance, not a
real response to the change. The temperature drop to 0.3 (commit `386f13e`) was
the right instinct for run-to-run stability, but it cannot fix a small-n
problem; it only narrows the *within-prompt* resampling band, not the
cross-sectional estimation error.

### F-2 [PROMPT-AGENT] The closed-vocabulary mandate is ~30 % violated and nothing rejects it

The prompt mandates closed-vocab `key_factors` (`risk:<x>`, `insider:<x>`,
`tone:<x>`, `guidance:<x>`). Validating every emitted tag against the vocab in
`config/analyst_heuristics.json`:

- baseline: **1 738 / 5 966 tags (29 %) off-vocabulary**
- iran: **1 786 / 6 046 tags (30 %) off-vocabulary**

The model routinely **drops the required prefix** — emitting `macro` (274×),
`revenue_growth` (234×), `profit_margin` (234×), `competition` (208×),
`free_cash_flow` (142×) instead of `risk:macro` etc., and inventing tags that
aren't in any list at all (`revenue_growth_YoY`, `revenue_growth`,
`profit_margin`, `free_cash_flow` are not fundamental vocab tokens — they look
like ratio field names leaking into the tag stream). Example (TSLA, baseline,
`2025-09-02 13:30`): `key_factors=['tone:cautious', 'risk:macro',
'risk:competition', 'risk:supply_chain', 'guidance:lowered',
'insider:planned_sale_dominant']` — clean; but RTX same tick:
`['supply_chain_intensified', 'macro', 'litigation', 'cluster_selling',
'tone:cautious']` — four of five tags are off-vocab (missing `risk:` /
`insider:` prefixes).

`LlmTickerVerdict.key_factors` is typed `list[str]` with only a length cap — **no
enum validation**, so malformed tags pass silently into evidence and the KB
substrate. This corrupts the one machine-aggregatable channel the analysts
produce. It is a real, fixable agent defect — but note it is *not* what is
hurting the scoreboard (the scoreboard scores `lean`, not tags).

### F-3 [DATA / PROMPT-AGENT] The verdicts are genuinely data-grounded — this analyst is NOT hallucinating

Sampled fresh rationales cite specific, correct figures pulled from the
ratios/filings block, proving the data pipeline is feeding real content:

- UNH (baseline, bearish, conf 0.8): *"high trailing P/E of 99.17, coupled with
  a low ROE of 4.83% and declining operating margins … the stock is
  significantly overvalued"* — P/E and ROE are real rendered fields.
- JNJ (baseline, bearish, conf 0.6): *"overall revenue growth has decelerated
  from 6.5% to 4.3% year-over-year … key Immunology products face significant
  biosimilar competition"* — a genuine YoY MD&A delta, exactly what the
  de-boilerplate filter is meant to surface.
- NVDA (baseline, bullish, conf 0.8): *"successful ramp of its Blackwell
  architecture … regulatory headwinds from US export controls"* — segment-level
  MD&A content.

The MD&A de-boilerplate machinery (`deboilerplate.py`) and the
distorted-P/E / sign-labelled-insider fixes (`fetch.py`) are working as
designed and the model is using them. The prompt re-anchor (commit `1656995`,
dropping the null forward-P/E anchor) was a correct, sensible change — there is
no longer any sign of the model reasoning from absent fields. **The reasoning
quality is good. The problem is not input quality or prose quality.**

### F-4 [PROMPT-AGENT] The bearish subset is the only consistently-bad cell, and it is mostly miscalibration

Across both runs and all horizons the bearish fundamental cell is negative and
large (baseline +20d −227 bps, +5d −118; iran +20d −202, p=0.062). The
hit-rate/sign mismatch the brief flagged (baseline +20d bearish hit 56.5 % but
mean −227 bps) is the tell: **the bearish calls are *directionally* right more
than half the time but lose badly on the misses** — a handful of high-confidence
bearish calls on names that then ripped higher dominate the mean. This is a
fat-tail miscalibration, not a systematic anti-signal, and at n=23–27 it is
heavily leverage-able by 2–3 observations. It cannot be distinguished from noise
at this sample size (p=0.37 baseline, 0.06 iran).

**Verdict — fundamental: there is a plausible, modest real signal, but it is
unmeasurable on this eval and unimprovable by prompt tuning at iter-11.** The
prose is good, the data is good, the re-anchor was correct. We are now polishing
noise. Continuing to A/B prompt variants against this scoreboard will produce
±100 bps sign-flips forever.

---

## 3. News analyst

### N-1 [EVAL/PROMPT-AGENT] The +1d primary horizon is pure noise; the +20d "destructive bearish" signal is a small-n artefact

News is scored primarily at +1d (correct — news decays fast). At +1d the
**`all` cell is +1.3 bps (baseline) / −7.0 bps (iran)** — statistical zero
(p=0.79 / n/a). The news analyst has **no measurable 1-day edge** in either
direction. This is the honest headline: at its own primary horizon, the news
analyst adds nothing detectable.

The alarming numbers are all at +20d, the *wrong* horizon for news, and on
tiny bearish subsets: baseline +20d bearish −531.6 bps (n=26, p=0.016); iran
+20d bearish **+46.9** bps (n=48, p=0.51). The sign **flips between regimes** —
that alone shows the −531 figure is not a stable property of the analyst. At
n=26 with 572 bps dispersion, a −531 bps mean is ~3 confidently-wrong calls.

### N-2 [DATA/PROMPT-AGENT] The bearish-news pathology is the "sell the bad news" trap, and the prompt does not prevent it

This is the most interesting real finding. The worst bearish news calls are
*correct readings of genuinely-negative news on names that then rallied hard*:

- AAPL (baseline, `2025-09-11`, conf **0.8**, bearish): *"investors and analysts
  appear underwhelmed … perceived lack of significant [AI] innovation"* (iPhone
  17 launch). **+20d actual +12.6 %.**
- AMZN (iran, `2026-03-24`, conf 0.8, bearish): *"AWS data centers in Bahrain …
  disrupted by drone attacks … premarket stock declines."* **+20d +15.4 %.**
- AMZN (iran, `2026-02-13`, conf **0.9**, bearish): *"$200 billion capex plan …
  longest losing streak in years."* **+20d +10.1 %.**

Even at the **+1d** primary horizon, traded bearish-news names *rose*: mean +1d
raw return **+1.28 % (baseline, n=6) / +1.72 % (iran, n=13)**. The signal is
anti-predictive at every horizon. The mechanism is classic: by the time a
negative story is "reported everywhere" it is already priced, and large-caps
mean-revert off the panic. The prompt *has* an "already-priced-in discount"
section (`news/prompts.py` lines 131–147) instructing exactly this caution — but
the model is still emitting conf 0.8–0.9 bearish on multi-outlet negative
stories. **The discount guidance is present but not being obeyed.** This is a
prompt-adherence failure on a genuinely identifiable pattern.

### N-3 [DATA] The raw feed is fresh but heavily macro-polluted; the re-rank helps but cannot fully de-noise sentiment

`01_fetch_news` carries a **median 87 (mean 117) articles per ticker** with
**0 % empty** — the feed is dense and fresh (0-day-old articles present). But
the top raw headlines for a single name are dominated by macro: NVDA on
`2025-09-16` led with *"New Record Highs on S&P, Nasdaq"*, *"Will the Rally
End With a Rate Cut?"*, *"Alphabet becomes a $3 trillion company"* — the first
NVDA-specific headline was #4. The `_rerank_articles` / roundup-demotion /
dedup machinery (`news/fetch.py`) is well-built and pushes specific stories up,
but in a strongly trending tape the *company-specific* stories themselves carry
macro beta, and a long-only single-name lean reads that beta as company signal.
The dedup is doing real work (the docstring's 116-rehash→16-distinct AMD case),
but the residual is still a sentiment reading, and sentiment ≈ recent price,
which does not predict forward price.

**Verdict — news: at its own +1d horizon the news analyst adds no measurable
signal, and its only large effects (bearish +20d) are (a) at the wrong horizon
and (b) anti-predictive / sign-unstable. This is closer to polishing noise than
fundamental is.** There is *one* fixable behaviour (N-2: stop high-confidence
bearish on already-reported bad news), but fixing it mostly removes a
loss-making bet rather than adding a winning one.

---

## 4. Per-analyst signal/noise verdict

- **Fundamental:** a *plausible* small real signal (the prose and data are
  sound), but **structurally unmeasurable** on this eval (n≈130, bearish n≈25)
  and **not improvable by further prompt tuning** at iter-11. Polishing noise.
- **News:** **no measurable edge at its primary +1d horizon**; the large +20d
  bearish numbers are wrong-horizon, sign-unstable artefacts. One concrete
  fixable pathology (over-confident bearish on priced-in bad news), but fixing
  it removes a loss, not adds a win. Closer to noise than fundamental.

---

## 5. Recommendations (prioritised; "moves the metric" vs "deck chairs")

### Will plausibly move the metric / change what we can learn

1. **Stop A/B-tuning the fundamental prompt against this scoreboard.** [EVAL]
   It is underpowered by ~10×. Every future iteration will produce ±100 bps
   sign-flips that look like signal and aren't. This is the single most
   important recommendation. Re-allocate that effort to (2)/(3).

2. **Change the evaluation, not the prompt, to recover power.** [EVAL] Options,
   roughly in order of leverage:
   - **Pool many windows.** ~10–15 independent month-windows would lift
     fundamental fresh-n from ~130 toward ~1 500 and make 30–50 bps edges
     detectable. This is the only route to *proving* a fundamental signal exists.
   - **Score fundamental at a longer hold with overlapping observations** (it is
     a slow signal) and report a portfolio-level IC / rank-correlation rather
     than a per-cell t-test, which is less starved by tiny subsets.
   - **Do not** keep reading the bearish-subset t-stats as real — at n≈25 they
     are uninterpretable.

3. **Fix the over-confident-bearish-news pathology directly.** [PROMPT-AGENT]
   The model ignores the existing already-priced-in discount. Make it
   mechanical rather than advisory: cap confidence on a bearish lean when the
   triggering story is multi-outlet / >1 day old (the dedup pass already knows
   the cluster size and freshest age — surface "this story appears in N outlets,
   freshest Xd old" into the context and hard-instruct a confidence ceiling).
   This removes a measurable loss-maker. It will not create a winner.

### Worth doing for hygiene, but will NOT move the scoreboard

4. **Enforce the closed vocabulary at the schema boundary.** [PROMPT-AGENT]
   30 % of `key_factors` tags are off-vocab and pass silently into the evidence
   substrate. Add an enum/validator on `key_factors` (or a normalisation pass
   that re-prefixes / drops invalid tags loudly). This protects the future KB
   learning loop — it does not affect the lean-based scoreboard. (Aligns with
   the project's silent-failures-are-the-bug-class memory.)

5. **Leave temperature at 0.3.** [PROMPT-AGENT] Correct call; no change needed.
   Just don't expect it to fix small-n.

### Recommend against

6. Do **not** invest more in news-feed de-noising for the analyst's benefit.
   The re-rank/dedup is already good; the residual problem is that single-name
   sentiment ≈ recent price, which is not predictive at +1d. Better feed
   engineering will not change that.

---

## 6. Meta-question: are we improving, and can we, on the current eval?

**No, and on the current eval we cannot prove it either way.**

The 11 iterations have produced individually-sensible changes (forward-P/E
re-anchor, sign-labelled insider dollars, distorted-P/E guard, MD&A
de-boilerplate, news dedup, temperature 0.3). Read in isolation the *code* is
better. But the *scoreboard* deltas iter-10 → iter-11 are sign-flipping noise
(fundamental iran bullish −29 → +47; baseline bullish +127 → +16), and a power
analysis grounded in the measured 442–572 bps cross-sectional dispersion shows
the fundamental eval can only detect effects above ~150–235 bps — an order of
magnitude larger than any realistic fundamental edge. The user's frustration is
**correct and well-founded**: performance "isn't improving" because the
measurement cannot resolve improvement at this sample size, and several
iterations have been spent chasing noise that looks like signal.

The bottleneck, per the brief's three hypotheses:

- **Fundamental → primarily (1) eval too strict/underpowered.** Data (2) is
  fine; prompt (3) is fine post-re-anchor. The eval is the wall.
- **News → mix of (3) prompt-agent (the unobeyed priced-in discount produces
  loss-making over-confident bearish calls) and (1) wrong primary horizon
  resolution at small bearish n.** Data (2) is dense and fresh; the residual
  noise is intrinsic to sentiment, not a fixable feed defect.

The path to *proving progress is real and possible* is to **fix the measurement
first** (pool windows / portfolio-level IC — recommendation 2), then test the
two genuine agent defects (N-2 bearish confidence cap, F-2 vocab enforcement)
against a powered eval. Continuing to tune prompts against the current
single-window scoreboard will not, and cannot, show improvement.
