# Baseline window 2025-09 — audit iteration 2

**Run audited:** `backtests/baseline-2025-09/runs/full-backtest-iter-2/`
**Window:** 2025-09-02 → 2025-10-13 (60 ticks, 30 trading days × open + close)
**Headline result:** total return **+1.91 %**, vs-SPY **−2.39 pp** (worse
than iter-1's −2.27 pp), Sharpe 2.43, max drawdown −1.59 %, **16 closed
round-trips** at 25 % win rate (twice iter-1's volume, same win rate).
LLM cost 11 M tokens across 1 863 model calls, 25.1 % cache hit rate.

Iter-1 landed four behavioural fixes (commit `1a6edc5`). Iter-2 was the
control re-run on the same window with the new behaviour in place. The
delta vs iter-1 is **−0.12 pp on the headline** — i.e. the four fixes
neither helped nor harmed the headline number, but did double round-trip
volume. The trade-shape *changed*; the underperformance did not.

This iteration's job: walk the iter-2 decision trail at the specific
levels the user asked about — strategist decision patterns, the
deterministic technical analyst's inputs and outputs, and a spot-check
of the technical and fundamental analysts in the wild — and surface
what is structurally restricting money-making.

The bug numbering continues from iter-1 (which ended at #7).

---

## 1. Audit scope

Materials walked:

- `manifest.json` — run metadata (git SHA `206e8c6`, 60 ticks, 0 failed,
  20-ticker watchlist).
- `report/metrics.md` — headline figures and per-agent latency.
- `decisions/*.json` — 41 strategist decision payloads with forward
  returns (+1 d / +5 d / +20 d).
- `tick/*.json` and `audit/*.tick.json` — per-tick state snapshots.
- Source: `src/contract/extractors/technical.py`,
  `src/contract/strategist_prompt.py`,
  `src/agents/analysts/fundamental/prompts.py`,
  `src/agents/strategist/prompts.py`,
  `src/agents/strategist/context_shim.py`,
  `src/agents/analysts/technical/agent.py`.

The first part of the audit was a counterfactual: take every round-trip,
compare *realised* P&L against *forward-return from open* over 5 and 20
trading days. That single table set the agenda for everything else.

---

## 2. The single most important finding

### Bug #8 — Strategist exits round-trips prematurely; cuts winners short

**Symptom.** Across 16 closed round-trips:

| Ticker | Open       | Close      | Realised | Cf +5 d | Cf +20 d |
|--------|-----------:|-----------:|---------:|--------:|---------:|
| AAPL   | 2025-09-04 | 2025-09-09 |   −2.26 % |  −2.26 % |  +5.23 % |
| AMD    | 2025-09-10 | 2025-09-11 |   −2.43 % |  +1.02 % |  +1.41 % |
| AMD    | 2025-10-06 | 2025-10-06 |  −10.04 % |  −4.43 % |     n/a |
| AMD    | 2025-10-07 | 2025-10-07 |   −1.55 % |  +0.73 % |     n/a |
| BAC    | 2025-09-16 | 2025-10-07 |   −0.18 % |  +2.53 % |  −0.49 % |
| CRM    | 2025-09-17 | 2025-09-30 |   −1.24 % |  +4.05 % |  −0.10 % |
| CVX    | 2025-09-24 | 2025-09-30 |   −2.17 % |  −1.40 % |     n/a |
| GOOGL  | 2025-09-02 | 2025-09-08 |  **+13.08 %** | +12.39 % | **+21.27 %** |
| JPM    | 2025-09-04 | 2025-09-05 |   −1.88 % |  −0.72 % |  +4.50 % |
| META   | 2025-10-02 | 2025-10-06 |   −2.41 % |  −1.31 % |     n/a |
| MSFT   | 2025-09-02 | 2025-09-05 |   −1.09 % |  −0.45 % |  +2.79 % |
| TSLA   | 2025-09-26 | 2025-10-02 |   −1.00 % |  +4.33 % |     n/a |
| UNH    | 2025-09-09 | 2025-09-15 |   +1.61 % |  +0.63 % |  −0.15 % |
| WMT    | 2025-09-02 | 2025-09-03 |   +0.64 % |  +5.19 % |  +5.75 % |
| XOM    | 2025-09-02 | 2025-09-05 |   −2.26 % |  −3.79 % |  −1.89 % |
| XOM    | 2025-09-12 | 2025-09-30 |   +0.43 % |  +2.15 % |  −1.39 % |
| **Σ**  |            |            | **−12.75 %** | **+18.65 %** | **+36.92 %** |

(`n/a` = position closed too close to window end for +20 d forward.)

The aggregate swing is enormous: **−12.75 % realised vs +18.65 %
hold-to-+5 d-from-open**, a 31.4 pp gap from premature exits alone. The
+20 d counterfactual is +36.92 % — a 49.7 pp gap.

Per-trade: **12 of 16 round-trips** (75 %) would have produced higher
P&L if held to +5 d than the strategist's realised exit. The win rate
on hold-to-+5 d would have been **9 / 16 = 56 %** vs the realised 25 %.

**Root cause.** Two reinforcing mechanisms, found in the decision trail:

1. **Thesis review fires every tick.** The strategist treats both the
   open *and* close phase of every trading day (60 reviews over 30 days
   for a position held the full window) as a fresh "is the thesis still
   intact?" question. The horizon field is collected on entry but not
   enforced downstream — the close-rationale text rarely cites holding
   period, instead reading any single tick of bearish input as
   invalidation.
2. **Bearish input bar is set very low.** Recurring close-rationale
   patterns: *"Closing for a small loss (−1.1 %) [...] to prevent
   further losses"*; *"Closing for a 13.1 % gain [...] technicals are
   extremely overbought"*; *"The catalyst has not materialised in the
   first session"*. A single overbought RSI reading, a single insider
   form-4 filing, or a single sub-day chop is enough to trip the close
   gate.

**Fix (proposed).** Two coupled changes:

- **Holding-period anchor in the strategist prompt.** Make the
  `horizon` field load-bearing: when reviewing held positions, the
  prompt must explicitly state how many ticks remain until the horizon
  fires and require a stronger bar for early-exit (e.g. "thesis
  invalidation requires evidence that opposes the *original* catalyst,
  not just opposing intra-window signals"). Phrase the bar in terms
  the model already uses (`catalyst`, `target_price`, `stop_price`).
- **Forward-looking trim verb usage.** The verb table has `trim` — it
  was used zero times in iter-2 (see Bug #10). Holding-period anchor
  alone won't fix exits if the model's only no-confidence option is
  `close`. Add a worked example of `trim` in the prompt (e.g. "if your
  conviction has weakened but the catalyst hasn't been invalidated,
  trim to half-weight rather than closing").

These are prompt changes, ~15-25 lines total. They do not require any
schema, salvage, or executor changes.

---

## 3. Strategist decision patterns

### Bug #9 — Hallucinated target / stop prices, anchored to stale levels

**Symptom.** **20 of 25 opens** (80 %) emit target/stop pairs that are
structurally broken at the fill price. Categories:

| Pattern | Count | Example |
|---|---:|---|
| Target *below* entry (thesis pays off by going *down*) | 9 | GOOGL fill $208.23, target $195, stop $175 |
| Stop *above* entry (would stop out immediately) | 5 | XOM fill $114.18, target $135, stop $118 |
| Both far out of any reasonable range | 6 | AVGO fill $288.75, target **$1 550**, stop **$1 400** |
| Pre-split / stale anchor levels | 3 | NVDA fill $175.30, target $650, stop $480 |

Worst examples:

- **AVGO** open 2025-09-02 at $288.75 → target $1 550 (+437 %), stop
  $1 400 (+385 %). Both above entry.
- **NVDA** open 2025-09-22 at $175.30 → target $650 (+271 %), stop $480
  (+174 %). NVDA had a 10:1 split on 2024-06-10; $650 is a
  pre-split-flavoured number.
- **UNH** open 2025-09-23 at $341.00 → target $750 (+120 %), stop $540
  (+58 %).
- **META** open 2025-10-02 at $722.58 → target $400 (−45 %), stop $310
  (−57 %). The model appears to be anchoring on a 2022 META price.

**Root cause.** Two compounding factors:

1. **The LLM does not know current prices.** Anchor numbers are pulled
   from training-era price ranges, not from the data the strategist is
   given (which *does* include `current_price` per ticker but the model
   does not appear to read it for stop/target generation).
2. **Nothing downstream uses target/stop.** The executor doesn't issue
   stop or take-profit orders — they are advisory text inside the
   stance object only. The risk gate doesn't validate that
   `stop_price < fill_price < target_price`. The strategist itself
   doesn't re-read them on subsequent ticks (close decisions are
   driven by close-rationale prose, see Bug #8). So the numbers
   never get a feedback signal that they are wrong.

**Fix (proposed).** Two pieces:

- **Sanity-validate in the risk gate.** Add a soft check: if
  `intent == "open"` and `stop_price > fill_price * 1.02` or
  `target_price < fill_price * 0.98`, raise a `STANCE_INCONSISTENT`
  clamp and replay the strategist with a corrective hint (same
  mechanism as the existing salvage gate). Cheap; catches the worst
  hallucinations before the trade lands.
- **Inject `current_price` into the entry prompt section.** The price
  is already in `temp:ticker_evidence`; surface it as a single,
  numerically-prominent bullet in the per-ticker block ("Current
  price: $208.23 — your target/stop MUST bracket this number") so the
  LLM has a hard anchor it can read while drafting the JSON.

Risk-gate validation is the load-bearing piece. The prompt nudge alone
is not enough — we have direct evidence the LLM ignores
already-present numerical context (see Bug #16 — it also ignores the
10b5-1 guidance).

### Bug #10 — Iter-1's `add` and entry-discipline fix did not change behaviour

**Symptom.** Across 25 opens in iter-2, `intent == "add"` was used
**zero times** — same as iter-1. Sizing is still uniform at 5 % on 20
of 25 opens; the remaining 5 are at 2-4 %. Iter-1 added a 7-line
paragraph to `src/agents/strategist/prompts.py` covering add semantics
and sizing guidance (commit `1a6edc5`). The behavioural effect was nil.

**Root cause.** Not fully diagnosed in this audit. Two candidates:

1. The COLD_START vs INCREMENTAL mode templates may bypass the new
   paragraph for early-window ticks where positions don't yet exist.
   By the time the bot has multi-day holdings, the model may have
   pattern-locked onto open/close + uniform 5 %.
2. The `add` verb requires `weight` as the *delta* (per verb table)
   but iter-1's added paragraph does not explicitly state "weight is
   the *increase*, not the *new total*" — the model may be avoiding
   `add` because the delta semantics are still ambiguous against
   `open` (which takes the *full* new weight).

**Fix (proposed).** Pure investigative — confirm which template
renders during INCREMENTAL ticks, then either move the new paragraph
into the shared section or sharpen the `add`-vs-`open` weight
semantics. No code change recommended until the template-routing
diagnosis is done; we already paid the cost of one ineffective
prompt edit.

### Bug #11 — AMD churn around the OpenAI catalyst (5 transactions in 3 days)

**Symptom.** AMD timeline in the window:

| Tick                    | Side  | Fill   | Rationale |
|-------------------------|:-----:|-------:|-----------|
| 2025-09-10 20:00 close  | buy   | 159.54 | initial open on OpenAI partnership news |
| 2025-09-11 20:00 close  | sell  | 155.66 | "to prevent further losses" — held 1 day |
| 2025-10-06 13:30 open   | buy   | 226.45 | re-entry on second catalyst wave |
| 2025-10-06 20:00 close  | sell  | 203.71 | "down 10 % since entry **yesterday**" — held 1 phase |
| 2025-10-07 13:30 open   | buy   | 214.85 | third entry |
| 2025-10-07 20:00 close  | sell  | 211.52 | "thesis has failed" — held 1 phase |
| 2025-10-08 13:30 open   | buy   | 212.95 | fourth entry (still held at window end) |

The 2025-10-06 close-rationale literally says "down 10 % since entry
**yesterday**" — but the entry was at 13:30 the same day, not yesterday.
The model fabricated a time anchor that makes the loss look more
significant than it was.

**Root cause.** Same as Bug #6 in iter-1 (no per-ticker P&L history) —
*partially* fixed by the `user:closed_trades_log` rolling 10-row buffer.
The fix added the history but did not add a discipline-rule for
re-entering recently-closed names. Combined with Bug #8 (premature
exits) the result is whiplash trading on names with strong narrative
catalysts.

**Fix (proposed).** Strategist prompt addition: after the existing
"Recent Round-trips" block, add a one-line discipline rule — *"You
have closed this ticker within the last 5 ticks; re-entering requires
a materially new catalyst, not a re-statement of the original
thesis."* The closed-trades log is already in scope; the discipline
rule names it.

---

## 4. Deterministic technical analyst — inputs, outputs, gaps

The user asked specifically: *could it provide more info, is the
current info being provided in the right way*. Both halves of the
answer turned out to be yes.

### Bug #12 — RSI-overbought flip rule sells winners at strength peaks

**Symptom.** In `src/contract/extractors/technical.py` lines 545-549,
`derive_technical_verdict` contains:

```python
if rsi > h.rsi_overbought:           # default 75
    factors.append("rsi_overbought")
    if pct5 > 0:
        lean = "bearish"             # <-- flips a trending lean
```

That is: "if RSI is high *and* 5-day momentum is positive, flip the
lean to bearish." In a window where the bot held three names that
ran 12-30 % in 2-3 weeks (GOOGL, UNH, AMD), this rule fired exactly
when it should not:

| Ticker | Date close   | RSI(14) | 5d mom | Tech lean emitted |
|--------|-------------:|--------:|-------:|------------------:|
| GOOGL  | 2025-09-08   |   86.5  | +6.4 % | bearish (mag 0.68) |
| UNH    | 2025-09-15   |   85.4  | +11.9 %| bearish (mag 0.62?) |
| AMD    | 2025-10-06   |   80.2  | +14.0 %| bearish |

The strategist reads "Technical: bearish, mag 0.6" alongside other
inputs and closes (see Bug #8). In trending markets, **persistent
overbought RSI is a feature of strong trends**, not an exit signal —
mean-reversion logic on a trending leg is the opposite of what should
happen.

**Root cause.** The flip is a one-line shortcut. RSI overbought *with*
positive momentum is not bearish unless paired with a reversal pattern
(divergence, MA breakdown, volume divergence). None of those are
computed.

**Fix (proposed).** Remove the unconditional `lean = "bearish"` line.
Replace with:

- Keep `rsi_overbought` in `factors` (it's information).
- Lean stays at whatever the trend score gave it.
- *Only* flip lean to bearish when overbought RSI coincides with at
  least one of: negative 1-day momentum (price rolling over), a
  death-cross structure (see Bug #13), or distance-from-high < 1 %
  *and* volume-ratio < 1 (volume drying up at the high). Wire one of
  these as a corroborating signal; do not flip on RSI alone.

The threshold (`rsi_overbought = 75`) is also conservative — many
trending names sit at 80+ for days. Consider raising the
config-driven threshold to 85 if the corroboration rule isn't enough.

### Bug #13 — `golden_cross` / `death_cross` computed but never consumed

**Symptom.** `extract_technical_features` computes:

- `golden_cross`: 1.0 if MA50 > MA200 (bullish trend regime).
- `death_cross`: 1.0 if MA50 < MA200 (bearish trend regime).

Both are written into the features dict on every tick, every ticker.
Neither is referenced anywhere in `derive_technical_verdict`. Neither
appears in `src/contract/strategist_prompt.py`'s `TECHNICAL_BULLETS`
(lines 227-240) so the strategist never sees them either. They are
dead features — pure compute cost, zero downstream signal.

**Root cause.** Probably an in-progress addition that never got wired
through. Not a regression — just a wiring gap.

**Fix (proposed).**

- In `derive_technical_verdict`: use death_cross as a *confirming*
  bearish factor and golden_cross as a *confirming* bullish factor.
  Specifically, the RSI-overbought-with-positive-momentum case from
  Bug #12 should only flip bearish *if* `death_cross == 1.0` (i.e.
  the larger structure agrees).
- In `TECHNICAL_BULLETS`: render one line — *"Trend regime: golden
  cross"* / *"death cross"* / *"no clear regime"* — so the strategist
  can also weight it.

### Bug #14 — `vol_ratio_20d = 0.0` as a no-data sentinel triggers `vol_dry_up`

**Symptom.** The features dump for UNH 2025-09-15 close shows
`vol_ratio_20d: 0.0`. UNH is a $300B mega-cap — volume is never zero.
The 0.0 is a sentinel value emitted when the OHLCV series is shorter
than the 20-bar window required to compute the ratio.

Downstream, `derive_technical_verdict` checks `vol_ratio <
h.vol_ratio_dry_up` (default 0.7) and appends `vol_dry_up` to the
factor list as a bearish-flavoured tag. A genuine "no data" state is
being indistinguishable from "volume is 70 % of normal."

**Root cause.** Sentinel-vs-NaN choice. Pythonic NaN would be
detectable; 0.0 is a real value the heuristic happily compares
against.

**Fix (proposed).** In `extract_technical_features`, return `nan` (or
mark the feature missing in `feature_warnings`) when fewer than 20
bars are available. In `derive_technical_verdict`, treat NaN as "no
signal" rather than "extreme low." Aligns with how the fundamental
extractor already emits `feature_warnings` for incomplete data
(e.g. `0.0` for `pe_forward` / `peg` when missing — but those have
the same sentinel issue and should probably move to NaN in a
follow-up).

### Bug #15 — Strategist sees only 7 bullets from the technical analyst

**Symptom.** From `src/contract/strategist_prompt.py` lines 227-240,
`TECHNICAL_BULLETS` surfaces to the prompt:

- RSI(14) with a band annotation
- 20d momentum (%)
- 5d momentum (%)
- Distance from 52w high (%)
- Distance from 52w low (%)
- Volume ratio vs 20d avg
- ATR%(14)
- Plus a comma-joined `factors` rationale tag list

The features that *exist* in the catalogue but **are not surfaced**:

- `golden_cross` / `death_cross` (the regime indicator the strategist
  needs in order to interpret RSI — Bug #13).
- `relative_strength_vs_spy_5d` / `_20d` — answers "is this ticker
  beating its index over the lookback?" (the one number a discretionary
  manager would look at before adding to a winner).
- `relative_strength_vs_sector_5d` / `_20d` — same, vs sector.
- `beta_confidence_damping` — present in `_KEYS` but unused.
- MA50 / MA200 *levels* (not just the cross flag) — would let the
  strategist place stops at structural levels rather than hallucinating
  them (Bug #9).
- ATR-derived suggested stop level (entry − 2 × ATR is a common
  industry-standard initial stop placement).
- Support / resistance from swing highs / lows — not currently computed.

**Root cause.** The bullet registry was built once and not revisited as
features were added. No automated check that "feature in catalogue =⇒
feature in bullets or explicitly excluded."

**Fix (proposed).** Three layers, in increasing order of effort:

1. **Wire the regime + relative strength bullets.** Add 3-4 lines to
   `TECHNICAL_BULLETS` covering `golden_cross/death_cross`,
   `relative_strength_vs_spy_*`, `relative_strength_vs_sector_*`.
   ~10 lines of bullet-template work.
2. **Compute MA50 / MA200 levels and an ATR-stop suggestion.** Pure
   pandas; we already have OHLCV. Adds two more bullet lines and
   gives the strategist real numbers to anchor its stop_price to —
   directly mitigates Bug #9.
3. **Support / resistance.** Deferred — would want a brainstorming
   session on the right detection logic (swing highs/lows vs
   pivot-point methodology vs volume-weighted levels). One future
   brainstorming session worth of scope.

---

## 5. LLM analyst spot-check

### Bug #16 — Fundamental analyst flags 10b5-1 planned sales as bearish despite explicit prompt guidance

**Symptom.** The fundamental analyst's prompt
(`src/agents/analysts/fundamental/prompts.py` lines 131-134) is
explicit:

> Routine 10b5-1 (planned) sales are pre-scheduled and disclosed in
> advance. They are NEUTRAL signal — NOT bearish. Discretionary
> open-market sales are bearish; clusters of them are strongly so.

The verdicts in the wild ignored this guidance on several closes:

| Tick + ticker | `cluster_sell` | `planned_sale_ratio` | OM-sell $ | Net $ | Sells | Verdict |
|---|:---:|:---:|---:|---:|:---:|---|
| WMT 2025-09-03  | 1.0 | 0.0 | $0 | −$400 M | 15 | bearish 0.70 |
| GOOGL 2025-09-08 | 1.0 | 0.0 | $0 | −$19.6 M | 23 | bearish 0.60 |
| AMD 2025-09-11   | 1.0 | 0.0 | $0 | −$39.9 M | 8  | bearish 0.60 |
| CVX 2025-09-30   | 0.0 | **1.0** | $0 | −$636 k | 1 | bearish 0.60 |
| CRM 2025-09-30   | 0.0 | **1.0** | $0 | −$10.7 M | 105 | bearish 0.60 |
| META 2025-10-06  | 1.0 | **1.0** | $0 | −$3.5 M | 9 | neutral 0.40 |

The CVX case is the most telling: a **single** $636 k 10b5-1 planned
sale produced a `bearish 0.60` verdict, which the strategist then
cited as "the fundamental analyst has turned strongly bearish."

The WMT, GOOGL, and AMD cases are arguably defensible (cluster sells
with non-trivial dollar amounts) but those clusters are themselves
mostly 10b5-1 driven if you walk the raw form-4 list — the planned-sale
ratio for those rows is 0.0 only because the extractor's
`is_10b5_1` flag is sparse (some form-4 filings don't disclose 10b5-1
even when the plan exists).

**Root cause.** Two-part:

1. The LLM is not honouring the prompt guidance. Either the per-ticker
   structured insider block does not foreground the planned-sale-ratio
   number prominently enough, or the model is pattern-completing on
   "many sells → bearish" before reading the qualifier.
2. The downstream strategist context (`TECHNICAL_BULLETS`-style
   feature renderer for the fundamental analyst) shows
   `Planned sale ratio: 1.0` *as a raw numeric with no annotation*
   (`src/contract/strategist_prompt.py` line 263 — fourth arg is
   `None`, no band rendering). The strategist therefore sees the
   number, has no neutralisation hint, and weighs it as a bearish
   input even if the fundamental analyst's verdict said neutral.

**Fix (proposed).**

- **Prompt structural emphasis.** Move the 10b5-1 rule to the *first*
  bullet of "Decision guidance" in the fundamental prompt, and lift
  the `planned_sale_ratio` number into the structured block's header
  (e.g. *"⚠ 100 % of these sales are 10b5-1 planned — treat as
  neutral signal"*) so it cannot be skim-read past.
- **Strategist bullet annotation.** Add a band-helper to
  `_PLANNED_SALE_RATIO` (currently `None`): when ratio ≥ 0.7, append
  *"(mostly planned / 10b5-1)"*; when ratio ≥ 0.9, append *"(all
  planned / 10b5-1 — neutral)"*. Mirrors the `cluster_sell` helper
  that already exists. ~8 lines.

### Bug #17 — Aggregator dilutes bullish technical signal into neutral aggregate

**Symptom.** Several decision files show:

```
technical: bullish, mag 0.16
fundamental: neutral, mag 0.30
news:      neutral, mag 0.30
aggregate: neutral, mag 0.14, summary "1 bullish / 2 neutral"
```

The aggregator weighs three analysts with default equal weights; one
neutral 0.30 plus another neutral 0.30 can mathematically outweigh one
bullish 0.16, producing a neutral aggregate. The strategist reads
"Aggregate: neutral" and applies a no-action bias — even if the only
*directional* signal in the bag was bullish.

**Root cause.** The aggregation logic treats neutral verdicts with
non-zero magnitude as quasi-bearish. Neutral with magnitude *should*
mean "no directional signal, low information" — i.e. it should
de-weight, not pull the aggregate down.

**Fix (proposed).** Pure-research first — confirm whether the
aggregation lives in
`src/contract/aggregator.py` (or similar) and what the magnitude-on-
neutral semantics are. If neutral magnitude is genuinely meant as
"confidence in neutrality" then re-weight: aggregate magnitude should
be the magnitude of the *directional* component only, with neutrals
reducing total confidence rather than pulling magnitude. No code change
without that diagnostic first.

---

## 6. Risk gate — observation only

The risk gate fired **zero clamps across all 41 decisions**. That is
not necessarily a bug — most of these decisions are within nominal
sizing — but it does mean the gate provided no protection against the
target/stop hallucinations in Bug #9, the AMD churn in Bug #11, or the
re-entry into recently-closed names. The proposal in Bug #9 (new
`STANCE_INCONSISTENT` clamp) is the right addition; we should consider
the gate as a venue for the discipline rules raised throughout this
audit rather than a separate concern.

---

## 7. Bugs deferred from iter-1, still deferred

- **Bug #2** — analyst evidence freshness gating. No iter-2 evidence
  this caused incremental harm; defer further.
- **Bug #4** — risk-gate × cash-buffer interaction. Same — no clamps
  fired, no evidence the interaction harmed iter-2.

Both should be revisited once Bugs #8, #9, #12, #15, and #16 are
landed — those are the highest-yield interventions.

---

## 8. Recommended iteration-3 scope

Ranked by expected P&L impact, smallest blast radius first:

1. **Bug #9** — risk-gate stance-consistency clamp + `current_price`
   prompt anchor. Catches 80 % of opens that currently emit
   structurally broken stops/targets. ~30 lines including tests.
2. **Bug #12** — drop the unconditional RSI-overbought-flip-to-bearish
   rule. ~5 lines in `derive_technical_verdict` plus a test pair.
3. **Bug #8** — holding-period anchor + `trim` worked-example in the
   strategist prompt. ~20 lines, no schema or executor change.
4. **Bug #16** — fundamental prompt re-emphasis + strategist bullet
   annotation for planned-sale-ratio. ~15 lines + 5 lines test.
5. **Bug #13 / #15** — wire golden/death cross + relative strength
   into both the deterministic verdict and the strategist bullets.
   ~25 lines.
6. **Bug #11** — recent-close re-entry discipline (one-line prompt rule
   piggybacking on the closed-trades log). ~3 lines.

The aggregator quirk (Bug #17) and the `add`-intent regression (Bug
#10) should each be diagnostic first, not code-first. They go into
iter-3 only after we confirm what is actually wrong.

If iter-3 lands Bugs #8, #9, #12, #16, the structural cause of the
premature-exit pattern (which accounts for the entire 31 pp
counterfactual gap) is plausibly resolved. The remaining items are
quality-of-life improvements for the analysts.

---

## 9. Open questions

- The +20 d counterfactual is even better than +5 d. Is the bot's
  natural holding horizon way too short, or are we just lucky that
  September-October 2025 was a strong tape? Worth replaying the same
  fixes on `svb-stress-2023-03` once iter-3 lands to control for
  market regime.
- The model is clearly hallucinating prices on stop/target generation.
  Are similar hallucinations creeping into the *thesis prose* in
  ways we haven't caught? Spot-check 5 thesis statements vs the
  actual evidence pack as a follow-up.
- Two of the three round-trip *winners* (GOOGL, UNH) were closed at
  RSI > 85 — those are exactly the rows Bug #12 would protect. The
  third (XOM-2) was closed flat. Iter-3 should explicitly verify the
  same window's winners are not closed prematurely.
