# Strategist inverted-signal diagnosis — `first-month-5` (2026-07-21)

**Purpose of this document.** A self-contained handoff so a *fresh* Claude Code
session can pick up the strategist inspection cold. It captures the diagnosis of
why the `first-month-5` backtest lost money, the evidence that verifies it, and
the resulting reframe: **the analyst-combine problem is higher-leverage than the
thesis-memory / horizon-holding work (previously "C"), which is now deprioritised.**

---

## TL;DR — the reframe

The bot's conviction is **anti-correlated with forward returns**: it builds
ceiling-weight positions in the names that fall and stays flat/short on the names
that rise. The root cause is **not** thesis-memory handling — it is that **two of
the three analysts are contrarian by construction** (technical = short-term
mean-reversion; fundamental = risk/litigation-fixated), and the strategist's
ad-hoc 3-way combine lets them dominate in a momentum market. The news analyst —
the only one with real forward edge — abstains on no-catalyst ticks and is
outvoted 2-to-0.

**Implication:** the horizon-holding thesis-memory work will not, on its own, make
money on this window — perfect holding would just hold the *wrong* names longer.
Fix the combine (and the deployment-pressure churn) first.

---

## The run

- **Path:** `backtests/long-baseline-2025/runs/first-month-5`
- **Git SHA:** `82a6c56` (i.e. AFTER the full legacy-context removal C1–C5)
- **Window:** `long-baseline-2025`, first month — 29 NYSE sessions, 2025-09-02 → 2025-10-10 (status: interrupted, which is fine for a first-month read)
- **Golden cache:** `backtests/long-baseline-2025/store.sqlite` (`ohlcv_bars`, `company_ratios`, `filings`, `news_articles`, `insider_trades`, …)
- **Per-run DB:** `runs/first-month-5/db.sqlite` — tables `trade_log` (11 round-trips), `ticker_stances` (580), `portfolio_snapshots` (29), `analyst_evidence` (1740)
- **Rendered strategist prompts:** `runs/first-month-5/obs/logs/<tick>-open.json`
- **Prompt extractor helper:** `scratchpad/extract_strat_prompt.py` (recursively finds the "You are the portfolio strategist" block and prints it unescaped)

### Headline metrics (`report/metrics.md`)

| Metric | Value |
|---|---|
| Total return | **−3.78%** |
| Win rate | **27.3%** (3 of 11 round-trips) |
| vs SPY buy-and-hold | **−9.53%** (SPY ~+5.8%) |
| vs matched-exposure SPY | **−7.27%** |
| Sharpe (ann.) | −4.42 |
| Avg equity exposure | 68.1% |
| Closed round-trips | 11 |

---

## Finding 1 — the signal is inverted (the core problem)

Cross-referencing the 2025-09-23 thesis book against actual window returns:

| Bot action | Ticker | Window return | Note |
|---|---|---|---|
| Ceiling 20% "high-conviction bullish" | **DOV** | **−8.4%** | *averaged down* from a 0.012 starter to 0.201 on "technical bounce near 52w low" |
| Ceiling 20% "high-conviction bullish" | **NDAQ** | **−4.2%** | held 26 days, added to ceiling |
| Largest final position (19%) | **DVN** | **−8.3%** | "despite mixed fundamentals" |
| **"High-Conviction Bearish", never bought** | **MPWR** | **+19.5%** (top performer) | flat all month |
| "High-Conviction Bearish" | **STLD** | **+13.4%** | round-tripped once at −2.42% |
| "Bearish", sold after 2 days | **FSLR** | **+15.5%** | sold at **+6.44%**, left ~+9% on the table |

The two ceiling-weight "highest-conviction" longs (DOV, NDAQ) were **both losers**;
the four "high-conviction bearish" names (MPWR, STLD, FSLR, ATO) were **the four
biggest winners**. Conviction is anti-correlated with outcome.

**Final book (2025-10-10):** 10 positions, 26% cash. Overweight losers —
DVN 19% (−8.3%), CMS 10.7% (+3.4%), DOV 9.9% (−8.4%), HRL 9.8% (−6.1%),
TSCO 8.1% (−10.9%), INVH 4.9% (−6.1%) — underweight winners FSLR 5.0% (+15.5%),
STLD 5.1% (+13.4%). MPWR (+19.5%) absent entirely.

---

## Finding 2 — WHY it's inverted (the MPWR evidence block, verbatim)

MPWR at $917 on 2025-09-23 (→ $978, **+19.5%**). All three analysts as rendered
to the strategist:

- **Technical: BEARISH, magnitude 0.69, confidence 1.00.** Reason: RSI(14) 70.2
  (overbought), +9.4% 20d momentum, at 52-week high, golden cross, +34.6% above
  200d MA. Tag: `reversal_up_fade`. → **The technical analyst is a contrarian
  ~5-day mean-reversion model; it fades every strong up-move.** A momentum name at
  new highs reads *bearish*. (The same model calls FDS *bullish* at RSI 20.8 — a
  falling knife that ended −22.7%.)
- **Fundamental: BEARISH, magnitude 0.60, confidence 0.60.** Reason: new
  class-action lawsuit, intensified macro/geopolitical risk, insider selling
  (−$18.4M, 13 sells, cluster-sell flag). But the *same block* prints
  **Revenue +21.2% YoY, profit margin 81.0%, RoE 49.4%, debt/equity 0.0.** →
  **The fundamental analyst fixates on 10-K risk-factor language and insider sells
  and reads a stellar-quality compounder as bearish.**
- **News: NEUTRAL, magnitude 0.00, confidence 0.10.** "No fresh, material
  company-specific news today." Post-B′ (news pruned to a fresh-surprise detector)
  this is *correct* — but it means the one analyst with real forward edge is
  **silent**, leaving the two contrarian analysts to drive the verdict 2-to-0.

**The combine is structurally anti-momentum, in a momentum month.** Technical
fades risers / chases fallers; fundamental is bearish on quality; news abstains
absent a catalyst. Net: capital flows into fallers (the technical "bounce near
52-week low" tag is what averaged DOV *down* into a ceiling loss) and away from
the winners the fundamental dislikes.

---

## Finding 3 — the prompt surface (clean, but two live problems)

The rendered strategist prompt was extracted from
`obs/logs/2025-09-23T13-30-00p00-00-open.json` (saved copy:
`scratchpad/strat_0923.txt`). It is **1574 lines**, sections:
Mode · Current State · Thesis Book (17–109) · **Ticker Evidence (110–1328, ~77% of
the prompt)** · Reading analyst reports · Reading horizons · Your Job ·
Deployment posture · OUTPUT CONTRACT · How to submit.

**Removal verified clean.** Zero occurrences of `memory_buffer`, `day_digest`,
Round-trips, or the portfolio-level `Thesis:` line. The C1–C5 sweep did its job.

**Problem A — the instructions say the right thing and are ignored.** The prompt
explicitly states: *"the default is to hold," "a rising price is not itself a sell
signal," "let winners run; cut losers when the thesis fails," "weigh the horizons;
do not obey them."* The behaviour is the opposite. Abstract prose discipline is
losing to a concrete, computed pressure (Problem B).

**Problem B — Deployment posture operationally overrides the hold discipline.**
It renders: *"Cash is not a safe default; it is an active bearish allocation …
every tick where the evidence supports a new position and you stay flat is a tick
of unforced cash drag."* This is a per-tick quantified nag with a target band
(70–95%); it wins against the "let winners run" prose four paragraphs later.
**Evidence:** every one of the 29 ticks' `decision_tag` is
"deploying_into_conviction / capital_rotation / tactical_adds"; lifecycle actions
across 580 stances = 45 buy / 22 sell / 121 update / 392 no_action. It is a churn
treadmill by construction, and it deploys into the anti-momentum picks.

**Problem C — dangling deterministic anchor.** Line ~1335 still instructs *"treat
the digested aggregate as a deterministic input"* — but no aggregate lean is
rendered per ticker (only the three raw analysts). The model free-hands the 3-way
combine with nothing deterministic to anchor to.

**Problem D — bloat.** 77% of the prompt is raw per-analyst evidence prose; the
decision-relevant signal is buried.

---

## Finding 4 — watchlist context (real, but secondary)

Per-ticker window returns (`store.sqlite`, 2025-09-02 → 2025-10-13 closes):

| Winners | | Losers | |
|---|---|---|---|
| MPWR | +19.5% | FDS | −22.7% |
| FSLR | +15.5% | DECK | −20.0% |
| STLD | +13.4% | TSCO | −10.9% |
| ATO | +6.2% | DOV | −8.4% |
| CMS | +3.4% | DVN | −8.3% |
| DGX | +2.8% | HRL | −6.1% |
| | | INVH | −6.1% |

**Equal-weight watchlist: −1.70%** vs SPY ~+5.8%. So roughly half the
underperformance is a **weak watchlist** (a separate watchlist-construction
question). But the bot did **−3.78%**, worse than even a 68%-exposure equal-weight
hold (~−1.2%) — it *added* ~2.6 points of negative selection alpha by
concentrating in the losers.

---

## Ranked root causes (most → least leverage)

1. **Anti-momentum analyst combine** (biggest; NOT thesis-memory). Two of three
   analysts are contrarian by design; the ad-hoc combine lets them dominate →
   long losers, flat/short winners. Horizon-holding cannot fix this.
2. **Deployment-pressure churn** (NOT thesis-memory; prompt-side). "Cash is
   bearish / cash drag" forces per-tick redeployment into the anti-momentum picks
   and overrides the hold discipline.
3. **News (the edge) is outvoted when silent** (NOT thesis-memory). The one
   analyst with forward edge abstains on no-catalyst ticks and can't override two
   contrarians.
4. **Thesis-memory / horizon-holding** (the old "C"). Real, but the *fourth*
   lever — only pays off once 1–3 stop the bot selecting the wrong names.

---

## Open questions for the next session

- **Is the technical analyst supposed to be contrarian at all?** A ~5-day
  mean-reversion lean is structurally anti-momentum. Options: flip/augment it with
  a momentum/trend read, or down-weight the reversal lean in the combine, or make
  the strategist treat `reversal_up_fade` on a golden-cross/above-200d name as a
  *non-signal* rather than bearish.
- **Should the fundamental analyst's risk-factor/insider-sell fixation be
  rebalanced against actual quality metrics?** MPWR (81% margin, 49% RoE, +21%
  growth) reading bearish is the tell.
- **How should the three leans be combined?** The "digested aggregate" is promised
  but not rendered — build a real deterministic aggregate, or make the combine
  rules explicit, rather than free-handing.
- **Deployment posture:** soften/remove the "cash is bearish allocation / cash
  drag" pressure so hold-discipline can win; measure churn before/after.
- **Watchlist construction** (separate track): the universe itself lagged badly;
  worth a look independent of the combine.

## How to re-verify (commands)

```bash
# rendered prompt for any tick
PYTHONPATH=src .venv/bin/python scratchpad/extract_strat_prompt.py \
  backtests/long-baseline-2025/runs/first-month-5/obs/logs/2025-09-23T13-30-00p00-00-open.json

# the 11 round-trips
.venv/bin/python -c "import sqlite3;c=sqlite3.connect('backtests/long-baseline-2025/runs/first-month-5/db.sqlite');\
[print(dict(zip(['t','o','c','pnl','why'],r))) for r in c.execute('select ticker,opened_at,closed_at,round(pnl_pct,2),close_reason from trade_log order by opened_at')]"

# per-ticker window return from the golden cache — see Finding 4
```
