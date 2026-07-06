# Strategist agent audit — Phase 13 analyst-improvement

**Date:** 2026-06-22
**Scope:** Read-only audit of the Strategist agent across four backtest runs.
**Status:** Investigation + recommendations only. No source, config, or artefact was modified.

## Runs audited

| Run | Total ret | Sharpe | vs SPY | Win rate | Round-trips | Halluc. | Avg exposure |
|-----|----------:|-------:|-------:|---------:|------------:|--------:|-------------:|
| baseline iter-9 (GOOD) | **+9.12%** | 3.97 | +4.82% | 52.2% | 23 | 9 | 89.6% |
| baseline iter-10 (BAD) | **+4.16%** | 1.94 | -0.14% | 54.2% | 24 | 7 | 87.7% |
| iran iter-9 | -3.74% | — | +1.89% | 64.3% | 14 | 2 | ~91% |
| iran iter-10 | -5.18% | — | +0.45% | 52.6% | 19 | 3 | ~91% |

(SPY closed the baseline window +3.08% and the iran window -5.63%.)

## Methodology

The project `.venv` is absent, so no project modules were imported. Source files were read as
text; artefacts were parsed with stdlib `json` and `sqlite3`. Per-run data was drawn from:

- `traces/<TS>.json` — `04_digest` (strategist input: per-ticker per-analyst leans/features),
  `03_strategist` (output stances), `06_risk_gate_in/_out`, `07_broker_calls`.
- `db.sqlite` — `portfolio_snapshots` (per-tick total value, cash, positions value, position
  count, holdings JSON), `trade_log` (closed round-trips with `pnl_pct`, hold hours,
  `close_reason`), `ticker_stances` (applied `lifecycle_action`).
- `obs/traces/<TS>.json` — OpenTelemetry spans carrying `gen_ai.usage.experimental.reasoning_tokens`
  and `reasoning_tokens_limit` on the strategist `call_llm` span.
- `report/metrics.md` — authoritative headline metrics and the hallucination count.

`bot_return_pct` / `excess_return_pct` columns in `portfolio_snapshots` are all-zero (not
backfilled in the per-run DB), so returns were recomputed from `bot_total_value`. `trade_log.pnl_pct`
was verified to be the per-share price-move percentage (e.g. CRM -4.25% matches its open/close
prices), so it is a per-trade return, not a portfolio-weighted contribution.

---

## HEADLINE — End-of-window fall-off (hypothesis 1): CONFIRMED, but it is a market pullback the strategist rode at ~95% exposure, not a late drop in sell activity

Both baseline runs peak on the **same tick** and give back into the close:

| Run | Peak ret | Peak tick | Final ret | Gave back | Max DD tick |
|-----|---------:|-----------|----------:|----------:|-------------|
| baseline iter-9 | +10.50% | idx 54 (2025-10-09 open) | +9.12% | **-1.38pp** | idx 57 (2025-10-10 close) |
| baseline iter-10 | +6.17% | idx 54 (2025-10-09 open) | +4.16% | **-2.01pp** | idx 57 (2025-10-10 close) |

Equity trajectory, last 8 ticks (iter-9): `8.67 → 10.37 → 10.50 → 10.47 → 10.38 → 6.39 → 8.55 → 9.12`.
The collapse is concentrated in a single session — **2025-10-10** — where the value drops ~3.7%
in two ticks (idx 55→57). This is a **market-wide tech pullback**, not stock-specific:

- iter-9 holdings at the peak (idx 54): MSFT 11.7%, META 11.1%, GOOGL 10.9%, AMD 10.9%,
  NVDA 8.7%, AMZN 7.8%, AVGO 7.2% — i.e. ~70% of the book in megacap tech that fell together.
- Total exposure at the peak was **94–95%** with only **~5% cash**.

**Does sell/trim activity drop late while the book stays ~90% invested?** Yes — modestly.
Sells in the last 8 ticks of iter-9 were `[1,1,1,1,3,0,1,0]` and iter-10 `[2,1,1,2,2,0,0,1]`.
Sell counts by window quartile (baseline iter-9): **Q1 20 / Q2 28 / Q3 23 / Q4 17** — Q4 is the
lowest. The book was carried into the 10-10 pullback near-fully invested with little de-risking.

**But the framing matters.** The strategist does not "stop selling" late; it sells when a *thesis
breaks*, and on 10-09 no theses had broken — the megacap-tech names were all still bullish on the
evidence the day before they fell. With a one-tick-ahead horizon and no price-stop/profit-stop
mechanism, there was no signal in the digest that said "the broad tech complex pulls back tomorrow."
The fall-off is the cost of running ~95% net-long into an undforecastable one-day index move.

**Classification: MIXED.**
- The *structural* part (you give back unrealised gains in a broad pullback when you are 95%
  invested and every thesis is intact) is **STRUCTURAL/UNPREDICTABLE** — it is the correct behaviour
  of a deployed long book, and over the full window iter-9 still beat SPY by +4.82%.
- The *fixable* part is that the strategist has **no tool to bank or hedge unrealised gains** when
  it is at the top of the deployment band (94–95%) with thin cash. See Finding 2.

---

## Finding 2 — "Not a win unless you cash out" / under-selling (hypothesis 2): CONFIRMED. Selling is almost purely defensive; there is no profit-booking discipline in the prompt

### Intent distribution (all ticks, all four runs)

| Run | buy | sell | update | no_action |
|-----|----:|-----:|-------:|----------:|
| baseline iter-9 | 101 | 88 | 248 | 763 |
| baseline iter-10 | 113 | 73 | 281 | 733 |
| iran iter-9 | 96 | 70 | 268 | 766 |
| iran iter-10 | 104 | 73 | 273 | 750 |

Over 60 ticks × 20 tickers = 1,200 stance slots, only **70–88 are sells**, of which only ~14–24
actually close a round-trip (the rest are trims/partials or sells on already-flat names that drop).

### Close-reason analysis (the smoking gun)

Reading every `trade_log.close_reason` string and tagging it:

| Run | profit-take language | deployment-management | thesis/news-break |
|-----|---------------------:|----------------------:|------------------:|
| baseline iter-9 | **1** | 13 | 21 |
| baseline iter-10 | **3** | 17 | 14 |
| iran iter-9 | **1** | 6 | 12 |
| iran iter-10 | **1** | 10 | 15 |

(Tags overlap; a close can cite both a thesis break and over-deployment.) Across **80 closed
round-trips in all four runs, only ~6 cite taking profit at all** — and those that do are reactive
to an extreme technical (GOOGL "RSI > 84", XOM "overbought at 52-week high", AVGO "pre-earnings"),
not a systematic profit rule. **Every other exit is a thesis break or a deployment-management trim.**
This is exactly the prompt's design: `prompts.py` "Holding discipline" section says
*"A rising price is not itself a sell signal... Let winners run; cut losers when the thesis fails"*
and *"Selling is not how you express satisfaction with a winner — it is how you express a changed
view."* Profit-taking is **explicitly discouraged**.

### This is the GOOD vs BAD difference

The win rates are nearly identical (iter-9 52.2%, iter-10 54.2% — iter-10 is actually higher).
The difference is the **payoff ratio**:

| Run | avg WIN | avg LOSS | payoff (win/|loss|) | avg hold |
|-----|--------:|---------:|--------------------:|---------:|
| baseline iter-9 (GOOD) | +4.33% | -1.85% | **2.34** | 252h |
| baseline iter-10 (BAD) | +3.28% | -2.21% | **1.49** | 168h |

iter-10 **cut its winners shorter** (168h vs 252h average hold), banked smaller average gains
(+3.28% vs +4.33%) and let losers run wider (-2.21% vs -1.85%), collapsing the payoff ratio from
2.34 to 1.49 — and did so while *managing deployment more aggressively* (17 deployment-driven
closes vs 13). The under-selling concern is real, but the sharper lesson is the inverse: **iter-10
sold winners too early for deployment-rotation reasons and that is what cost it the alpha.**

**Classification: FIXABLE (prompt/calibration).** The "let winners run, never sell on price"
posture is a deliberate choice; the user is open to introducing profit-booking. The evidence
supports a *targeted* change (partial trims on extreme extension while keeping a runner), not a
blanket "cash out winners" rule — the latter is what iter-10 effectively did and it underperformed.

---

## Finding 3 — Thinking-token change (hypothesis 3): PREMISE NOT SUPPORTED BY THE DATA

The hypothesis was that iter-10's strategist thinking tokens were RAISED vs iter-9 and that this
degraded results. The trace data contradicts this:

- Both runs ran at the **same git SHA** (`0cfe3f3`), and `config/strategist.json` at that SHA has
  `thinking_budget: 2048` (unchanged in the working tree today).
- The strategist `call_llm` span reports `reasoning_tokens_limit = 2048` and `max_tokens = 16000`
  in **both** runs.
- Actual reasoning tokens *used* per tick: iter-9 mean **1766** (median 1816), iter-10 mean
  **1749** (median 1788) — essentially identical, iter-10 marginally *lower*.
- (The uncommitted `config/analysts.json` edit dropping analyst `thinking_budget` 2048→512 is a
  *different, later* experiment and applied to neither of these runs — both predate it.)

**So the iter-9 → iter-10 regression is NOT a strategist thinking-token change.** With identical
inputs (87% of tick-0 analyst leans matched: 52/60 same) and identical decoding config, the
divergence is attributable to **temperature=1 non-determinism in the strategist itself.** On the
matched cold-start tick the two runs built materially different opening books from near-identical
evidence:

| Ticker | iter-9 | iter-10 |
|--------|--------|---------|
| MSFT | buy 0.12 | buy 0.10 |
| GOOGL | buy 0.12 | buy 0.08 |
| AVGO | buy 0.12 | buy 0.10 |
| XOM | buy 0.12 | buy 0.10 |
| AMZN | buy 0.08 | **update (no buy)** |
| CRM | buy 0.07 | **update (no buy)** |
| JPM | update | **buy 0.05** |
| LMT | update | **buy 0.07** |
| WMT | update | **buy 0.05** |

iter-9 concentrated (7 names, 0.12 in its top conviction picks); iter-10 spread wider (8+ names,
smaller weights, added low-conviction WMT/JPM/LMT starters and skipped AMZN/CRM). The wider,
flatter iter-10 book — the exact "twenty small fragments" the prompt warns against — is the
proximate cause of its weaker payoff, and it arose from sampling variance, not a config knob.

**Classification: STRUCTURAL (run-to-run variance).** The actionable insight is that the
strategist's output is **highly sensitive to sampling at temperature=1**, so single-run A/B
comparisons between iterations are unreliable. See Recommendation 4.

---

## Finding 4 — Hallucinated stances (hypothesis 4): low-impact; the model is not losing intended trades

`metrics.md` reports 9 / 7 / 2 / 3 hallucinated stances per run (sell-on-non-held, silently
dropped). The `ticker_stances.lifecycle_action` table records `sell` as the applied action for the
*valid* sells; the dropped ones are sells emitted against a ticker with no live position. Spot
checks (e.g. baseline iter-9 tick `2025-09-02T20:00` emitting `sell BAC` / `sell UNH` when neither
was ever held; these surface as weight `0.0` in `06_risk_gate_in.proposed_weights`) show these are
**redundant defensive gestures**, not blocked profitable trades — the model is trying to express
"I would not hold this" on a name it already does not hold. Dropping them is correct and costs
nothing. The count is small (≤9/1200 slots) and tracks roughly with sell-eagerness (iran runs,
which had fewer sells, had fewer hallucinations).

**Classification: STRUCTURAL / cosmetic.** Not a source of lost alpha. A minor prompt clarification
could reduce the noise (see Recommendation 5) but it is not a priority.

---

## Finding 5 — Signal extraction (hypothesis 5): the strategist acts on a minority of strong-consensus signals, largely because it already holds the names

Counting ticker-ticks where **all three analysts were bullish with mean confidence > 0.6**:

| Run | strong-bull consensus | strategist BUY | held/update | sold |
|-----|----------------------:|---------------:|------------:|-----:|
| baseline iter-9 | 102 | 18 | 81 | 3 |
| baseline iter-10 | 89 | 19 | 69 | 1 |

The strategist issues a fresh `buy` on only ~18-21% of strong-bull-consensus signals. The large
"held/update" residual is mostly names it **already bought on tick 0 and is holding at size** (the
megacap-tech book), so a fresh buy would breach the 20% position ceiling — that is correct
behaviour, not an override. (A precise held-vs-flat split could not be produced reliably because the
`portfolio_snapshots.recorded_at` ↔ digest `recorded_at` join is fragile in the artefacts; the
qualitative conclusion is robust because the tick-0 buys persist in the trade log.)

Notably there were **essentially zero 3/3-bearish consensus signals** (0 in iter-9, 2 in iter-10).
The analyst pool has a strong structural long-bias — it almost never produces unanimous bearish
agreement — so the strategist's **sell decisions are virtually never backed by analyst consensus;
they are its own thesis-break judgments.** This is consistent with the close-reason analysis in
Finding 2 and is a constraint on any "sell more" change: the analysts will not supply the sell
signal, so any profit-booking rule must live in the strategist prompt/logic itself.

**Classification: STRUCTURAL (analyst long-bias) + informative.** The strategist follows bullish
consensus where capacity allows; it cannot follow bearish consensus because the analysts rarely
produce it.

---

## Finding 6 — Risk gate (hypothesis 6): effectively inert; not a bottleneck

Comparing `06_risk_gate_in.proposed_weights` to `06_risk_gate_out.clamped_weights` per tick:

| Run | ticks with any clamp | individual weights changed |
|-----|---------------------:|---------------------------:|
| baseline iter-9 | 1 / 60 | 7 |
| baseline iter-10 | 3 / 60 | 10 |
| iran iter-9 | 2 / 60 | 2 |
| iran iter-10 | 1 / 60 | 7 |

Almost all clamping is on the **cold-start tick**, where the total-turnover / total-deployment cap
scales every opening buy down proportionally (e.g. iter-9 tick 0: every opening buy multiplied by
~0.72, 0.12→0.086). The only non-cold-start clamps are 2 AMD trades in iran iter-9 hitting the 20%
position ceiling (0.234→0.20, 0.227→0.20). The gate is **not vetoing or reshaping the strategist's
ongoing decisions** — it is a backstop that the strategist almost never triggers.

**Classification: STRUCTURAL / working as intended.** No change recommended.

---

## Prioritised findings & recommendations (recommendations only — not implemented)

### P1 — Introduce *partial* profit-booking on extreme extension, not blanket cash-out [FIXABLE]
**Evidence:** Finding 2. Only ~6 of 80 round-trips took profit at all; the prompt explicitly bans
selling on a rising price. The GOOD run (iter-9) won by letting winners run to a 2.34 payoff; the
BAD run (iter-10) destroyed alpha by *over*-trimming for deployment (payoff 1.49, hold 168h vs 252h).
**Recommendation:** Add a narrow rule to the "Holding discipline" section of `prompts.py` that
permits **trimming a fraction (e.g. 25–33%) of a position that has run to an extreme** (e.g. RSI
elevated *and* materially above entry) while **keeping a runner** — explicitly distinct from a full
exit. Frame it as "bank a portion of an outsized unrealised gain to fund the next idea; keep the
core thesis position." Do **not** adopt a blanket "a win isn't a win until you cash out" rule — the
data shows that posture (iter-10's deployment-rotation churn) underperformed buy-and-hold-the-thesis.
This should be A/B'd across multiple seeds (see P4) before adoption.

### P2 — Give the strategist a deployment-band exit valve for broad pullbacks [FIXABLE, lower confidence]
**Evidence:** Finding 1. Both runs rode a 95%-invested book into the single-session 10-10 tech
pullback and gave back 1.4–2.0pp with only ~5% cash and near-zero late-window selling.
**Recommendation:** Consider whether, when sitting at the top of the 70–95% band (>0.92) with the
book concentrated in one correlated cluster (e.g. >60% megacap tech), the strategist should be
nudged to hold the *lower* end of the band / keep a larger cash buffer. This is lower-confidence:
the behaviour is largely structural (a deployed long book gives back in pullbacks), iter-9 still
beat SPY comfortably, and a one-tick horizon cannot forecast the move. Treat as an experiment, not
a clear win.

### P3 — Address strategist long-bias in sell signalling [STRUCTURAL → partially fixable]
**Evidence:** Finding 5. The analyst pool produces ~zero unanimous-bearish consensus, so the
strategist's sells are unsupported by upstream signal and rely entirely on its own thesis-break
reasoning. Any "sell more / book profit" change (P1) therefore cannot lean on analyst input and
must be self-contained in the strategist prompt. Flag for the analyst-side audit: the long-bias in
analyst leans constrains the whole pipeline's ability to de-risk.

### P4 — Stop drawing iteration conclusions from single runs [PROCESS]
**Evidence:** Finding 3. iter-9 vs iter-10 ran at the *same SHA and same config* yet diverged from
+9.12% to +4.16% purely from temperature=1 sampling — including a materially different cold-start
book from 87%-identical analyst inputs. **Recommendation:** Run each config N≥3 times (varied seed)
and compare distributions, or lower strategist `temperature` for evaluation runs to reduce variance.
The "iter-10 was worse because thinking was raised" premise is false; the regression is noise.

### P5 — Minor: reduce hallucinated-sell noise [COSMETIC]
**Evidence:** Finding 4. ≤9/1200 stance slots are sells on non-held tickers, silently dropped — no
lost trades. **Recommendation:** Optionally tighten the prompt's "sell only works on tickers you
currently hold" line (already present at `prompts.py` "Choosing the right verb") with a reminder to
use `update`/`no_action` to express a negative view on an unheld name. Low priority.

### Not actionable
- **Risk gate (Finding 6):** inert and working as intended; no change.

## Key correction to the brief
The brief stated iter-10's strategist thinking tokens were "RAISED vs iter-9." The trace data shows
both runs used `reasoning_tokens_limit = 2048` and near-identical actual reasoning-token usage
(~1766 vs ~1749). The iter-9→iter-10 regression is **sampling variance at temperature=1**, not a
thinking-budget change.
