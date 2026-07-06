# News Analyst Audit — LLM Verdict Quality

**Scope:** Read-only audit of the LLM-based News analyst (per-ticker fan-out)
across four backtest runs. No source, config, or artefact was modified.

**Runs audited**

| Tag | Path | Headline |
|---|---|---|
| baseline-GOOD-iter9 | `backtests/baseline-2025-09/runs/analysts-eval-iter-9` | +9.12 % (+4.82 % vs SPY) |
| baseline-BAD-iter10 | `backtests/baseline-2025-09/runs/analysts-eval-iter-10` | +4.16 % (−0.14 % vs SPY) |
| iran-iter9 | `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-9` | −3.74 % (falling market, beat SPY) |
| iran-iter10 | `backtests/iran-conflict-2026-02/runs/analysts-eval-iter-10` | −5.18 % (falling market) |

**Watchlist (20):** AAPL MSFT NVDA GOOGL AMZN META TSLA AMD AVGO CRM JPM BAC XOM CVX LMT RTX JNJ UNH PG WMT.

News `thinking_budget` was **512 in both iter-9 and iter-10** — news is *not*
the deliberate iter-9→10 change, so this audit focuses on structural quality
rather than the iteration delta.

---

## 1. Methodology

The News analyst is an LLM per-ticker fan-out:
`src/agents/analysts/news/{prompts.py,per_ticker.py,fetch.py,agent.py,joiner.py}`,
config in `config/analysts.json → news` (`max_articles_per_ticker=25`,
`max_generic_articles_per_ticker=10`, `max_summary_chars=1500`,
`roundup_company_threshold=3`, `dedup_title_similarity_threshold=0.85`,
`llm.thinking_budget=512`). Output caps: `output_caps.report_summary_max_chars=1000`,
`verdict_rationale_max_chars=200`.

Parsed with stdlib `python3` only (`.venv` absent — no project import):

- **Full-universe verdicts** — all 20 tickers × 60 ticks × 4 runs from
  `traces/<TS>.json → 02_news_verdict.data[*]` (**4 800 verdicts total**; each
  carries its own `ticker` field). Used for signal/noise, staleness, empty-rationale.
- **Raw article inputs** — `traces/<TS>.json → 01_fetch_news.data[<TKR>].news`
  (the **pre-cap** provider list — ~116–155 articles/ticker). Used for input-quality.
- **Verdict ↔ forward-return joins** — `decisions/<TS>__<TKR>__<side>.json`
  (`analyst_outputs.per_analyst.news.verdict` joined to
  `forward_returns.{+1d,+5d,+20d}`). **644 traded-name decisions**. Used for calibration.

Cadence: 60 ticks = 30 trading days × 2 phases (13:30 "open", 20:00 "close").

> **Important confound on calibration.** The 644 verdict↔return rows are
> *traded-name* decisions only — i.e. names the strategist already chose to buy/sell.
> This biases the sample heavily bullish (62 % bullish, 6 % bearish) and is not a
> clean read of the analyst's full predictive power. Full-universe calibration would
> need a (tick,ticker)→price join that only the decisions files carry. The
> directional findings below are therefore conditional on strategist selection; the
> *bearish* miss (small n=42, but stark) is the most trustworthy signal because
> bearish-and-still-traded is the least selection-biased cell.

There is **no separate `cache/reports/news/` artefact** in the run trees and no
`03_news_llm_<TKR>_in/out` trace key — the news LLM prompt is not separately traced.
Cache-replay was therefore inferred from the article-set hash (see §3), which is
exactly what the cache keys on.

---

## 2. Signal vs noise — the analyst is a structurally-bullish near-constant

Full-universe lean / magnitude / key-factor distribution (1 200 verdicts each):

| Run | neutral | bullish | bearish | mag ≥0.6 | summary mean len |
|---|---|---|---|---|---|
| baseline-iter9 | 53 % | 44 % | **3 %** | 46 % | 486 / 1000 |
| baseline-iter10 | 52 % | 45 % | **4 %** | 47 % | 492 |
| iran-iter9 | 54 % | 39 % | **7 %** | 45 % | 502 |
| iran-iter10 | 54 % | 39 % | **7 %** | 45 % | 495 |

**Findings**

- **Bearish is nearly absent (3–7 %).** Despite an explicit prompt instruction
  ("Bearish is appropriate for missed guidance, downgrade … do NOT default to
  neutral"), the analyst almost never goes bearish. In the *falling* iran window it
  only nudges from 3 % → 7 %. The analyst has essentially two states — bullish or
  neutral — which makes it a poor downside detector.
- **Magnitude is inflated.** ~45–47 % of all verdicts carry magnitude ≥ 0.6 and
  <2 % carry <0.2. "Expected 1–3 session move size" is being set high almost by
  default, draining the field of discriminative power.
- **`is_no_data` is near-zero (0–1.3 %)** — articles essentially always exist
  (see §4), so the no-data path is irrelevant here.
- The summary cap (1000 chars) is **never** approached (mean ~490, max ~870) — the
  earlier Vertex pad-to-cap pathology is not present; prose length is healthy.

### 2a. Closed-vocabulary is violated — key_factors are double-emitted noise

The prompt mandates a **closed vocabulary** with `<prefix>:<value>` tags
(`catalyst:…`, `novelty:…`, `direction:…`, `material:…`). In practice the LLM emits
**both** the prefixed tag **and** a bare unprefixed duplicate:

```
PREFIXED (legal):  catalyst:4305  novelty:1802  direction:1750  material:934
BARE (illegal):    product_launch:889  regulatory:652  macro:501  legal:402
                   guidance:397  upgrade:334  none:232  m_and_a:154 …  (3 930 total)
```

Roughly **one in three** emitted key_factors is an out-of-vocabulary bare token
duplicating its own `catalyst:` prefix. This is a schema/prompt-adherence defect:

- key_factors are supposed to be the *discriminating* feature, but they collapse to
  a tiny, repetitive set (`product_launch`, `regulatory`, `macro`) that barely
  differentiates one ticker from another.
- The duplication inflates the 8-tag list with redundant entries, crowding out genuine
  distinct drivers.

**Net:** as a cross-sectional signal the verdict is close to a constant — mostly
"bullish/neutral, mag~0.6, conf~0.8, key_factors = {product_launch, regulatory,
macro}". The *report.summary* prose is the only genuinely ticker-specific output.

---

## 3. Staleness / cache replay — modest, and entirely article-set driven

The report cache (`agents/analysts/report_cache.news_hash_inputs`) keys on the
**set of (url, published_at) pairs** for the ticker — summary-text drift does not bust
it, but any article rolling in/out does. So a cache replay happens *iff* the raw
article set is byte-identical to a prior tick.

| Run | consec-tick pairs | identical full verdict (incl summary) | identical lean/mag/conf only | identical **raw article set** |
|---|---|---|---|---|
| baseline-iter9 | 1 180 | 0.4 % | 26 % | 0 % |
| baseline-iter10 | 1 180 | 0.4 % | 27 % | 0 % |
| iran-iter9 | 1 180 | **8.9 %** | 31 % | **9 %** |
| iran-iter10 | 1 180 | **8.9 %** | 33 % | **9 %** |

**Findings**

- The identical-full-summary rate (0.4 % baseline, 8.9 % iran) tracks the
  identical-article-set rate (0 % / 9 %) *almost exactly*. That confirms the only
  source of verbatim verdict repeats is genuine cache replay when no news rolled in —
  it is **not** a bug, and it is **modest** (≤9 %, concentrated in the quieter iran
  window). News is **not** a frozen constant across ticks.
- Intraday open→close (same trading day) shows the same: 0 % identical summary in
  baseline, 8 % in iran. The two daily ticks usually *do* see article-set changes.
- The **26–33 % identical lean/mag/conf** rate is much higher than the cache rate —
  i.e. most directional stability is the **LLM re-deriving the same answer** from a
  changed-but-similar article set, not cache. Longest unchanged runs reach 9–11 ticks
  for mega-caps (NVDA, BAC, MSFT) where the story genuinely doesn't move day-to-day.

**Verdict on staleness:** real but small; not the primary problem. The bigger issue is
that even the *fresh* verdicts don't predict returns (§5).

---

## 4. Input quality — volume is abundant, the 25-cap is the real filter

From the pre-cap `01_fetch_news` lists:

| Run | raw articles/ticker (mean / max / min) | ticker-ticks over 25-cap | mean % company-specific (raw) | zero-specific ticker-ticks | summaries >1500 chars |
|---|---|---|---|---|---|
| baseline-iter9/10 | 116.6 / 332 / 14 | **93 %** | 48 % | 0 % | 0.1 % |
| iran-iter9/10 | 155.3 / 433 / 19 | **99 %** | 42 % | 0 % | 0.1 % |

**Findings**

- **Input is never scarce.** Every ticker has ≥14 articles every tick; ~half are
  company-specific by the simple symbol/name match. There is **no UNPREDICTABLE /
  insufficient-input** ticker in these windows — the analyst always has material to
  read. (This differs from the politician/insider gaps noted elsewhere in the project.)
- **The 25-article cap is the dominant information bottleneck**, not data scarcity:
  93–99 % of ticker-ticks have *more* than 25 articles, so the dedup + specificity
  re-rank in `fetch._build_ticker_news_context` is throwing away the long tail. That
  pipeline (dedup at 0.85 similarity → roundup demotion → specific-first → 25-cap with
  ≤10 generic) is doing the right *kind* of work, and given ~50 % raw specificity it
  is plausibly surfacing the real company news. **Classify: FIXABLE-adjacent** — the
  selection logic is sound but unverifiable from artefacts because the *rendered*
  context block is not traced (see §7 recommendation).
- **Truncation is a non-issue:** only 0.1 % of article summaries exceed the
  1500-char cap, so `max_summary_chars` is barely binding.

---

## 5. Calibration & predictive power — effectively zero, bearish is anti-predictive

644 traded-name decisions, news verdict vs realised forward return.

**Directional hit-rate & mean forward return by lean:**

| Horizon | bullish (n=401) | bearish (n=42) | neutral (n=201) |
|---|---|---|---|
| **+1d** | mean +0.42 %, hit **54 %** | mean **+1.05 %**, hit **26 %** | mean +0.15 % |
| **+5d** | mean +0.67 %, hit **52 %** | mean **+1.24 %**, hit **26 %** | mean +0.68 % |
| **+20d** | mean +1.68 %, hit 58 % | mean +3.87 %, hit 45 % | mean +0.32 % |

**Confidence calibration (directional leans, +5d):**

| confidence bucket | n | hit-rate |
|---|---|---|
| 0.5–0.7 | 2 | 0 % |
| 0.70–0.85 | 319 | **50 %** |
| 0.85–1.0 | 122 | **50 %** |

**Magnitude vs realised |+5d| move:** mag 0.4–0.6 → 2.73 %; mag ≥0.6 → 2.89 %
(essentially flat — high-magnitude calls do *not* precede bigger moves).

**Correlation of signed score (lean × conf × mag) vs forward return:**
+1d = −0.003, +5d = −0.013, +20d = +0.006 — **indistinguishable from zero at every
horizon.**

**Findings**

- **No predictive edge.** The signed-confidence score has ~zero correlation with
  forward returns at the horizons where news *should* bite most (+1d/+5d). Bullish
  hit-rate is ~52–54 % — barely above coin-flip and consistent with the structural
  bullish bias riding a generally-up tape rather than genuine signal.
- **Confidence is uninformative.** 0.70–0.85 and 0.85–1.0 buckets both hit exactly
  50 % at +5d. The model's stated confidence does **not** track its accuracy — a
  direct contradiction of the prompt's intent that confidence reflect "real, unpriced,
  AND material".
- **Bearish is anti-predictive.** When the analyst calls bearish, the stock *rises*
  on average (+1.05 % at +1d, +1.24 % at +5d) and the directional hit-rate is **26 %**
  — significantly *worse* than random. The few bearish calls it makes are
  systematically wrong (small sample, but the least selection-biased cell). This is the
  single most actionable calibration finding: the analyst's negative signal is noise or
  inverted.

---

## 6. Confidently-wrong examples

Top misses ranked by `confidence × magnitude × |+5d miss|`:

| Run | Ticker | Tick | lean | mag/conf | +1d | +5d | +20d | Mechanism |
|---|---|---|---|---|---|---|---|---|
| baseline-iter9 | TSLA | 2025-09-08 open | **bearish** | 0.6 / 0.7 | −2.2 % | **+15.6 %** | +25.0 % | Read "US EV market share at 8-yr low" as fresh bearish; ignored that share-loss was old/priced and a rally followed. Bearish-on-a-ripper. |
| iran-iter9 | AMD | 2026-02-26 | bullish | 0.8 / 0.9 | −4.1 % | **−8.5 %** | −4.5 % | "Multi-$B Meta AI chip deal" read as bullish momentum; macro selloff (geopolitical) overwhelmed the idiosyncratic catalyst. |
| iran-iter10 | META | 2026-03-23 open | bullish | 0.6 / 0.8 | −2.1 % | **−11.5 %** | +4.7 % | "Pivot to AI / custom-chip investment" → bullish; market sold the spend in a risk-off tape. |
| baseline-iter10 | NVDA | 2025-10-10 open | bullish | 0.8 / 0.9 | −2.7 % | −7.1 % | +4.8 % | "UAE AI-chip export approval, record high" — bought the top; the news was the catalyst the market had already run on. |
| baseline-iter9 | NVDA | 2025-10-09 close | bullish | 0.8 / 0.9 | −4.9 % | −6.5 % | +7.5 % | Same UAE-export story, prior tick — already-priced positive treated as fresh bullish. |

**Pattern:** the analyst repeatedly reads a *positive but already-priced* mega-cap
catalyst ("AI chip deal", "export approval", "record high") as a fresh bullish driver
and buys local tops — precisely the failure mode the prompt's "already-priced-in
discount" section was written to prevent, but which it does not enforce in practice.

### Iran window — news did **not** catch the regime

Among traded decisions in the falling iran window, lean stayed overwhelmingly bullish:

| Run | bullish | neutral | bearish |
|---|---|---|---|
| iran-iter9 | 88 | 50 | **15** |
| iran-iter10 | 100 | 49 | **13** |

Even in a geopolitical-stress selloff the analyst issued ~7× more bullish than bearish
calls. It reads company-specific AI/contract headlines in isolation and is blind to the
macro regime. (The iran portfolio *did* beat SPY on a relative basis — see metrics —
but that is the strategist/risk layer dampening exposure, not the news analyst calling
the downturn.)

---

## 7. The empty-rationale finding — by design, not a defect

`rationale` is empty in **98.7–100 %** of news verdicts. This is **BY DESIGN**, not a
bug:

- `contract/evidence.py`: `LlmTickerVerdict.to_ticker_verdict()` defaults
  `rationale=""`; `TickerVerdict.rationale = Field(default="")`. The code comments
  state LLM analysts (News, Fundamental) deliberately stopped emitting `rationale` —
  `report.summary` carries the same prose, and the duplication previously caused a
  Vertex "pad-toward-cap" repetition pathology. `rationale` is now populated *only* by
  the deterministic extractors (Technical, Social, SmartMoney).
- The non-empty handful (0–1.3 %) are the synthesised no-data fallbacks in the joiner
  (`"no verdict from LLM"`).

So: **no action on empty rationale.** The prose lives in `report.summary` (mean ~490
chars, well-formed). Any downstream consumer reading `verdict.rationale` for News/Fundamental
is reading the wrong field — worth a one-line check that the digest/strategist reads
`report.summary`, not `rationale`, for these analysts.

---

## 8. Prioritised findings & recommendations (recommendations only — no code changed)

**P1 — Bearish signal is anti-predictive (26 % hit-rate, stock rises after bearish calls).**
The analyst's most decision-relevant output is inverted/noise. Recommend: (a) review
the bearish decision rule and the already-priced-in discount — the model conflates
"old negative news still circulating" with fresh negative; (b) consider treating News
bearish as low-weight until re-calibrated; (c) add an eval assertion that bearish-call
forward returns are negative on average, failing the run if not (per the project's
"loud tests / assert positive signals" principle).

**P2 — Zero correlation with forward returns + uninformative confidence.**
Signed-score↔return corr ≈ 0 at all horizons; confidence 0.70–0.85 and 0.85–1.0 both
hit 50 %. The verdict carries almost no tradeable edge. Recommend: down-weight News in
the strategist aggregation until it demonstrates edge, and add a calibration gate
(confidence buckets must be monotone in hit-rate) to the analyst eval harness.

**P3 — Structural bullish bias / magnitude inflation.**
3–7 % bearish, ~46 % of verdicts mag ≥0.6. The "positive coverage is the DEFAULT state,
not a reason to be bullish" instruction is not being honoured. Recommend: tighten the
prompt's bullish bar (require a *named, dated, ≤2-day-old* catalyst and an explicit
"is this already priced?" check echoed in the summary), and consider a magnitude
prior/penalty so ≥0.6 is reserved for genuinely large expected moves.

**P4 — Already-priced-in catalysts bought at local tops (NVDA/AMD/META examples).**
The dominant confidently-wrong mechanism. Recommend: make the LLM emit an explicit
`priced_in: bool` / age-of-freshest-catalyst field it must justify, and discount
magnitude/confidence when the driving article is >1 day old (the age data is already
rendered in the context block — it's just not being acted on).

**P5 — Closed-vocabulary violation: ~1/3 of key_factors are out-of-vocab bare
duplicates (3 930 occurrences).** key_factors are near-useless as a discriminating
feature. Recommend: post-validate key_factors against the closed vocab in the joiner
(drop or reject bare tokens), and/or strengthen the prompt's "use these tags ONLY"
constraint with a one-shot negative example. Low effort, removes downstream noise.

**P6 — Rendered LLM context is untraced — input quality is unverifiable.**
`01_fetch_news` shows the pre-cap raw list but not the post-dedup/rerank/25-cap block
the LLM actually read. Recommend: add a `03_news_llm_<TKR>_in` trace (mirroring the
fundamental analyst) so the dedup/specificity pipeline can be audited and the 25-cap's
information loss measured directly. Investigation-enabling, not a correctness fix.

**Non-findings (confirmed healthy / by-design):**
- Empty `rationale` (98.7–100 %) — **by design**; prose is in `report.summary`. §7.
- Cache replay / staleness — **modest (≤9 %)** and entirely explained by unchanged
  article sets; not a frozen-constant problem. §3.
- Input scarcity — **none**; every ticker has abundant articles every tick. §4.
- Summary truncation — **negligible** (0.1 % of summaries exceed 1500 chars). §4.
