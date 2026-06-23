# Spec — News analyst: reduce harm from over-confident bearish verdicts

**Status:** Draft, awaiting sign-off.
**Scope:** the News LLM analyst only (`src/agents/analysts/news/`). No change to
fundamental, technical, strategist, or the executor.
**Type:** harm-reduction, not alpha-generation. The iter-11 audit established that
news has *no measurable edge at its primary +1d horizon*; the explicit aim here is
to stop a repeatable loss-maker without lobotomising the genuine bearish signal.

---

## 1. Problem statement

### 1.1 The pathology (from the iter-11 audit)

`docs/Phase13-analyst-improvement/audit-iter-11/llm-analysts-audit.md` finding **N-2**:
the News analyst issues **high-confidence bearish verdicts on genuinely-negative
but already-public news**, on momentum names that then continue up — the classic
"sell-the-news" / priced-in trap. This is the one finding that survived
cluster-robust inference (news +20d bearish **−531.6 bps, t=−2.91, p=0.015**), so
it is real and repeatable, not a small-n artefact like the fundamental cells.

Named offenders (audit + re-derived below):

| ticker | window | date | conf | bearish thesis | +20d actual |
|--------|--------|------|-----:|----------------|------------:|
| AAPL | baseline | 2025-09-11 | 0.8 | iPhone-17 "underwhelming", lacks AI innovation | **+12.6 %** |
| AAPL | baseline | (2nd tick) | 0.8 | same thesis restated | **+11.1 %** |
| GOOGL | baseline | — | 0.8 | — | **+9.6 %** |
| AMZN | iran | 2026-03-24 | 0.8 | AWS Bahrain drone-attack disruption | **+15.4 %** |
| AMZN | iran | 2026-02-13 | 0.9 | $200 B capex, longest losing streak | **+10.1 %** |

The prompt **already** carries an "already-priced-in discount" section
(`news/prompts.py` lines 131–147) instructing exactly this caution. The model
reads it and emits conf 0.8–0.9 bearish anyway.

### 1.2 Quantification (re-derived this session, stdlib parse of iter-11 artefacts)

Two complementary cuts. The **verdict-level** cut (all 1 200 news verdicts per
window from `traces/*.json → 02_news_verdict.data`, with trailing 20d momentum
recomputed from `01_fetch_technical` close bars) is what the scoreboard scores.
The **traded-decision** cut (`decisions/*.json`) is the smaller subset the
strategist acted on, and carries forward returns + the technical analyst's
`pct_change_20d` directly.

**Verdict-level — bearish confidence is undifferentiated and never low:**

| window | news verdicts | bearish | bearish conf ≥0.7 | mean bearish conf | conf ≥0.8 |
|--------|--------------:|--------:|------------------:|------------------:|----------:|
| baseline (rising) | 1 200 | 37 | **37 (100 %)** | 0.79 | 31/37 |
| iran (falling) | 1 200 | 77 | **77 (100 %)** | 0.78 | 59/77 |

Every single bearish verdict in both windows is conf ≥0.7. The model has **no
"tentative bearish" mode** — when it goes bearish it goes loud.

**Verdict-level — the priced-in / positive-momentum overlap is regime-dependent:**

| window | bearish hi-conf on **rising** 20d (mom>0) | on **strong-up** 20d (mom>+5 %) | median 20d mom |
|--------|------------------------------------------:|--------------------------------:|---------------:|
| baseline (rising) | 23/37 (**62 %**) | 22/37 (**59 %**) | **+8 %** |
| iran (falling) | 23/77 (30 %) | 7/77 (9 %) | −5 % |

So in the **rising tape the trap is dominant** — the majority of loud bearish
calls are on names already ripping higher (the −531 bps +20d cell). In the
**falling tape the bearish calls are mostly on genuinely-falling names** (median
−5 % momentum) and are largely *correct* — the iran +20d bearish cell is
+46.9 bps, i.e. roughly neutral. This regime split is the central design
constraint (see §3): a momentum gate must only bite when momentum is strongly
positive, or it will suppress the correct iran-window bearish calls.

**Traded-decision cut (smaller, strategist-acted subset) — bearish news loses at
short horizons too:** baseline traded bearish-news (n=6) mean **raw +1d +127.8 bps,
+20d +427.9 bps**; iran traded bearish (n=13) **raw +1d +172.0 bps**. Even one day
out, names the analyst called bearish on *rose*. (Excess/peer-demeaned figures are
noisier on this tiny traded n and are not the headline — the verdict-level
scoreboard cell is.)

---

## 2. Root-cause framing

> Is the model failing to **apply** the existing priced-in guidance
> (prompt-adherence), or failing to **recognise** the priced-in setup because it
> lacks the momentum context (missing-context)?

**Both, but the binding constraint is missing-context.**

1. **Missing context (binding).** The News analyst's entire input is
   headlines + summaries + an `As of:` date + per-article age
   (`fetch_agent.py` → `_build_ticker_news_context`). The article dicts carry only
   `headline, summary, url, source, published_at, sentiment, relevance` — **no
   price, no return, no momentum**. So the analyst *cannot* see that AAPL is up
   8 % on the month while it reads "iPhone-17 underwhelms". The priced-in section
   asks it to judge "has the market already absorbed this?" using *only article
   age* as a proxy — and article age is a poor proxy for priced-in-ness on a
   momentum name. No prompt wording can fix a judgement the model has no data to
   make.

2. **Prompt-adherence (secondary).** Even on the inputs it *does* have (article
   age, multi-outlet repetition), the model ignores the discount: it emits conf
   0.8–0.9 on multi-day-old, widely-syndicated negative stories that the
   recency/dedup machinery has already flagged as stale and clustered. The
   "lower confidence … lean neutral" instruction is advisory and unobeyed. The
   100 %-of-bearish-is-conf≥0.7 statistic is the proof: the discount language
   produces *zero* low-confidence bearish verdicts.

**Architectural note that constrains the fix.** Technical, fundamental and news
analysts run **concurrently** inside one `AnalystPool` `ParallelAgent`
(`orchestrator/pipeline.py` lines 90–101). The technical analyst's momentum
verdict is therefore **not guaranteed to be in `state` when the news branch
runs** — any option that feeds momentum to news must either (a) recompute it from
price bars inside the news path, or (b) re-order the pipeline so the deterministic
technical analyst completes before the news branch. This is the single biggest
implementation cost and is called out per-option below.

---

## 3. Design options

All three target the *same* failure; they differ in where the leverage sits and
how much they risk over-fitting to two windows.

### Option A — Strengthen the prompt's priced-in section + few-shot the failure

Restructure lines 131–147 from advisory prose into an explicit rule, and add a
worked few-shot of the exact failure mode (loud bearish on stale multi-outlet
negative news on a rising large-cap → "this is the trap; lean neutral / cap
confidence").

- **Pro:** zero new data plumbing, zero schema change, no pipeline re-order;
  cheapest to ship and fully reversible.
- **Con:** we have *already* tried advisory priced-in language and it is ignored
  (§2.2). A few-shot may help marginally but bets on the same lever that has
  already failed once. It still gives the model **no momentum data** — it can only
  reason from article age, the weak proxy. Low confidence it moves the metric.
- **Over-fit risk:** low (no thresholds fitted to the data).

### Option B — Feed the analyst the momentum context it needs

Surface a compact, deterministic momentum line into each ticker's news context
block, e.g. appended to the `As of:` header:

```
=== AAPL ===
  As of: 2025-09-11
  Recent price context: +8.2% over last 20 sessions, +1.1% over last 5 (rising).
  [1] [2025-09-11, 0d ago] Apple unveils iPhone 17 ...
```

The momentum is computed from the same price-history bars the technical analyst
already pulls (`pct_change_20d`, `pct_change_5d` in
`contract/extractors/technical.py`). The prompt then instructs: *"when recent
momentum is strongly positive and the negative story is already multi-outlet /
days old, it is likely priced in — lean neutral or cap confidence."*

- **Pro:** addresses the *binding* root cause (missing context). Gives the model
  the one fact it lacks, then lets it judge — preserves model agency, no hard
  mechanical suppression. Keeps correct bearish calls (the model still sees the
  story).
- **Con:** requires the news fetch path to obtain price bars. Cleanest source is
  the technical fetch, but ParallelAgent ordering means it may not be in `state`
  yet — so either re-order the pipeline (technical → analyst-pool) or have
  `NewsFetchAgent` fetch its own price bars (a second price pull per tick).
  Re-introduces price into a path that was deliberately sentiment-only. Still
  relies on the model to *act* on the context — same adherence risk as A, though
  now with the data it was missing.
- **Over-fit risk:** low-moderate (a "strongly positive" wording, not a fitted
  numeric threshold, if we keep it qualitative).

### Option C — Deterministic, observable post-hoc confidence cap (RECOMMENDED, with B's context line)

Add a deterministic post-processing step in the **joiner** that caps confidence
on a **bearish** verdict when trailing momentum is **strongly positive**, and
records *loudly* when it fires. Concretely, in `NewsJoinerAgent` after inflating
each `LlmTickerVerdict`:

```
if verdict.lean == "bearish"
   and momentum_20d is not None
   and momentum_20d >= news.bearish_priced_in_momentum_threshold   # config, e.g. 0.05
   and verdict.confidence > news.bearish_priced_in_confidence_ceiling:  # config, e.g. 0.5
       original = verdict.confidence
       verdict.confidence = ceiling
       # OBSERVABLE: annotate, trace, and terminal-log — never silent.
       verdict.key_factors.append("priced_in_momentum_cap")   # closed-vocab tag (new)
       trace + log: "news bearish cap fired: TICKER conf 0.9 -> 0.5 (mom20=+8%)"
```

- **Pro:** mechanical, so it *cannot* be ignored the way advisory prose is —
  directly removes the measurable loss-maker. The momentum gate (`>= +5 %`) means
  it bites the baseline trap (62 % overlap) but barely touches the iran window
  (9 % overlap), preserving the correct falling-tape bearish calls. Fully
  reversible via config.
- **Con:** it is a *cap*, not a judgement — a blunt instrument. It still needs
  momentum data in the joiner (same plumbing cost as B). A hard numeric threshold
  is fitted-ish to two windows (over-fit risk). It caps but does not *flip* the
  lean, so a capped-but-still-bearish verdict can still cost a little.
- **Over-fit risk:** **moderate** — `+5 %` and the `0.5` ceiling are tuned to the
  observed split. Mitigated by (i) making both config, (ii) the guard test in §5
  that the iran-window bearish cell must not collapse, (iii) choosing a momentum
  threshold from first principles (≈1 monthly σ of a large-cap, not grid-searched
  on the return).

### Recommendation (for sign-off)

**Ship B's momentum-context line AND C's observable cap together, gated behind the
same momentum data.** Rationale: B fixes the binding root cause (gives the model
the fact it lacks) and preserves agency; C is the mechanical backstop for the
*known* adherence failure, made loud per the project's silent-failures rule. A
alone is rejected — it re-pulls the lever that already failed. The momentum data
plumbing is paid once and serves both.

**Resolve before building:** the pipeline-ordering question (re-order technical
ahead of the pool vs. give `NewsFetchAgent` its own price pull). This is a
consequential architectural choice and is the top open question (§6).

---

## 4. Exact implementation surface

### 4.1 New config keys (`config/analysts.json` → `news` block)

| key | type | default | meaning |
|-----|------|--------:|---------|
| `news.bearish_priced_in_momentum_threshold` | float [0–1] | `0.05` | Trailing 20-session return at/above which a *bearish* verdict is treated as likely-priced-in. |
| `news.bearish_priced_in_confidence_ceiling` | float [0–1] | `0.5` | Confidence the cap clamps a triggered bearish verdict down to. |
| `news.bearish_priced_in_cap_enabled` | bool | `true` | Master switch for the deterministic cap (Option C). Lets us A/B the cap against the context-only variant without code edits. |
| `news.show_momentum_context` | bool | `true` | Whether the momentum line (Option B) is rendered into the per-ticker context block. |

`config/README.md` **must** be updated: add these four rows to the existing
`### news` table (around line 221–234), each with range and rationale, matching
the house format.

### 4.2 New closed-vocabulary tag (`config/analyst_heuristics.json` → `news_vocabulary`)

Add `priced_in_momentum_cap` to a vocabulary list the cap can append to. Cleanest
home is a new annotation under `catalysts` is wrong (it is not a catalyst); prefer
extending the **`direction`** or adding a small `annotations` list. **Open
question — see §6.** Whichever list, `config/README.md` `news_vocabulary` section
(line 196+) must document it. NB: the audit (F-2) found 30 % of `key_factors` are
already off-vocab with no validation — appending a tag the model never emits is
safe, but if a future vocab validator lands it must whitelist this annotation.

### 4.3 Prompt edit (`src/agents/analysts/news/prompts.py`)

**Before** (lines 131–147, the "Recency and already-priced-in discount" block) —
unchanged opening, but the priced-in bullets are advisory only.

**After** — insert a momentum-aware bullet and tighten the discount into a rule.
Illustrative (house style, British English):

```
Recency, momentum and already-priced-in discount:
- The context block opens with an ``As of:`` anchor and a ``Recent price
  context`` line giving this name's trailing 20- and 5-session return.  Use it.
- A negative story that is ALREADY multi-outlet and several days old, on a name
  whose recent momentum is strongly POSITIVE, is the textbook priced-in trap:
  the market has had the news and bid the stock UP regardless.  In that
  configuration do NOT emit high-confidence bearish — lean neutral, or if you
  still judge the news materially negative, cap your confidence at moderate and
  say why in the summary.
- Reserve high-confidence bearish for genuinely NEW negative information the
  price has not yet reflected (a fresh, single-source break; a story that post-
  dates the recent run-up).
```

(Plus a one-paragraph worked few-shot of the AAPL iPhone-17 case as the
canonical trap, if Option A's few-shot is folded in.)

The `Recent price context` line is rendered by `_build_ticker_news_context`
(`fetch.py`) when `news.show_momentum_context` is true.

### 4.4 Momentum plumbing

- **`NewsFetchAgent` / `_build_ticker_news_context`:** accept a per-ticker
  `momentum_20d` / `momentum_5d` (float | None) and render the `Recent price
  context` line. **None → render nothing** (loud-safe: no silent fake zero).
- **Source of momentum:** per §6 open question — either read the technical
  evidence already in `state` (requires pipeline re-order so it is present), or
  compute from price bars fetched in the news path. Reuse
  `contract/extractors/technical.py` momentum logic; do **not** re-implement the
  bar-window arithmetic.
- **`NewsJoinerAgent` (Option C cap):** after `to_ticker_verdict()`, apply the
  cap using the same `momentum_20d`. The joiner already has the per-ticker raw
  slice and `state`; momentum must be made available there too.

### 4.5 Observability (mandatory — silent-failures rule)

When the cap fires it MUST be observable on every channel the analyst already
uses:

1. **Trace:** extend the existing `trace_maybe(state, "02_news_verdict", …)` payload,
   or add a sibling `02_news_bearish_caps` trace listing
   `{ticker, original_confidence, capped_to, momentum_20d}` for every fire.
2. **Tag:** append `priced_in_momentum_cap` to `key_factors` so the capped state
   is visible in the persisted evidence and any downstream KB.
3. **Terminal log:** emit a line via the existing `emit_analyst_summary` path (or
   a dedicated counter) so an operator watching a backtest sees "news bearish cap
   fired N×".
4. **Never** drop or neutralise a verdict silently — the cap mutates confidence
   *and records the original*; the lean is preserved.

### 4.6 Schema

No change to `LlmTickerVerdict` or `TickerVerdict` required — the cap mutates the
canonical `confidence` post-inflation and appends to `key_factors` (a
`list[str]`, length-capped at 8 — guard the append so an already-full list raises
or drops the *least* important tag loudly, not the annotation).

---

## 5. Verification plan

### 5.1 Unit tests (new, under `tests/`)

- **Cap fires:** bearish verdict, `momentum_20d = +0.08`, conf 0.9 → confidence
  clamped to ceiling, `priced_in_momentum_cap` tag present, original confidence
  recorded in the trace payload. Assert the *positive* outcome (tag + value),
  not merely "did not crash" (silent-failure rule).
- **Cap does NOT fire — momentum below threshold:** bearish, `momentum_20d =
  +0.02` → confidence untouched, no tag.
- **Cap does NOT fire — wrong lean:** bullish/neutral with high momentum →
  untouched (the cap is bearish-only by construction).
- **Cap does NOT fire — momentum unknown:** `momentum_20d = None` → untouched, and
  the context line renders nothing (no fake zero).
- **Disabled switch:** `bearish_priced_in_cap_enabled = false` → never fires even
  when all trigger conditions hold.
- **Context render:** `show_momentum_context = true` with known momentum renders
  the `Recent price context` line; `None` renders no line; `false` switch renders
  no line.
- **Vocab:** `priced_in_momentum_cap` is in the configured vocabulary list.

### 5.2 Backtest re-score (the real judge)

Re-run both windows to iter-12, then:

```
PYTHONPATH=src .venv/bin/python -m scripts.backtest_scoreboard \
    --run-dir backtests/baseline-2025-09/runs/analysts-eval-iter-12 \
    --window  baseline-2025-09
# and the iran window
```

The scoreboard is now cluster-robust (clustered by ticker). Judge by:

- **Primary:** the **news bearish cell** mean-excess and honest (cluster-robust)
  t. Target on **baseline**: the −531 bps +20d bearish cell moves materially
  toward zero (the loss is removed). Also inspect +1d (the analyst's primary
  horizon) — it should not worsen.
- **Guard against over-suppression (critical):** the **iran** bearish cell
  (currently ≈+47 bps / neutral, mostly *correct* falling-tape calls) must **not
  collapse**. If the cap fires on a large fraction of iran bearish verdicts, the
  momentum threshold is too low — raise it. Concretely: assert cap-fire rate on
  iran bearish stays in single digits (matches the 9 % strong-up overlap), versus
  a majority on baseline.
- **Keep-the-good-ones check:** count bearish verdicts on names that then *fell* —
  these must be largely uncapped. Report capped-vs-uncapped split by realised
  forward sign so we can see we removed losers, not winners.

### 5.3 Anti-overfit discipline

Only two windows exist. State explicitly in the iter-12 doc: the `+5 %` threshold
is chosen as ≈ one monthly volatility unit of a large-cap, **not** grid-searched
against forward returns. Do not tune the threshold to maximise the backtest delta
— that is curve-fitting on n=2 windows and the audit's whole thesis is that this
eval is underpowered for fine tuning. Treat any improvement beyond "the −531 cell
stops being a large negative" as noise until more windows are pooled.

---

## 6. Open questions for sign-off

1. **(Top / architectural) How does momentum reach the news path?**
   Re-order the pipeline so the deterministic technical analyst runs *before* the
   `AnalystPool` (clean, single price pull, but changes pipeline shape and tick
   latency profile), **or** give `NewsFetchAgent` its own price-bar pull (keeps
   news self-contained but double-fetches price and re-introduces price into a
   deliberately sentiment-only path)? This is a consequential, hard-to-reverse
   choice — needs mutual agreement before any code.

2. **Cap vs. context-only.** Do we ship Option C's mechanical cap at all, or trust
   Option B's context line to let the model self-correct? The 100 %-bearish-is-
   conf≥0.7 evidence argues the model won't self-correct on advisory input — but
   B gives it *new* data, which A never did. Proposal: ship both, but keep the cap
   behind `bearish_priced_in_cap_enabled` so we can measure B-alone vs B+C.

3. **Where does the `priced_in_momentum_cap` annotation live in the vocab?**
   It is neither a catalyst, novelty, nor direction. Extend `direction`, add a new
   `annotations` list, or accept it as a non-vocab system tag (given F-2 already
   tolerates off-vocab tags)? Affects the future vocab-validator (F-2 rec 4).

4. **Threshold values.** Are `+5 %` / `0.5` acceptable as first-principles
   defaults, or do we want a different ceiling (e.g. clamp to 0.4, or to the
   analyst's mean neutral confidence)? Must be agreed *before* the run, not tuned
   *from* it.

5. **Should the cap ever flip the lean to neutral** rather than just clamp
   confidence? Clamping is the conservative, reversible choice; flipping removes
   the verdict's vote entirely. Recommend clamp-only for v1.

6. **Horizon framing.** The harm is largest at +20d, but news is scored primarily
   at +1d (config `primary_horizon_by_analyst.news = 1`). Do we accept that the
   fix is justified by a non-primary horizon? (The audit says yes — it is a
   measurable, cluster-robust loss regardless of the primary-horizon label — but
   worth an explicit nod at sign-off.)
```
