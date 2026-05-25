# Baseline window 2025-09 — audit iteration 1

**Run audited:** `backtests/baseline-2025-09/runs/full-backtest-probe-1/`
**Window:** 2025-09-02 → 2025-10-13 (60 ticks, 30 trading days × open + close)
**Headline result:** total return **+2.03 %**, vs-SPY **−2.27 pp**, Sharpe 2.94,
max drawdown −1.16 %, 8 closed round-trips with 25 % win rate.

The probe completed without crashing — a milestone in itself — but
underperformed SPY by enough that we walked the per-tick decision trail
to understand *why*. This document records the audit's findings and the
four fixes landed in iteration 1.

---

## 1. Audit scope

We worked through the run's artefacts:

- `report/metrics.md` — headline figures and per-agent latency.
- `report/equity_curve.png` — visual portfolio vs SPY.
- `audit/*.tick.json` — full per-tick state snapshots (intents, gates,
  fills, salvage events).
- `decisions/*.json` — strategist decision payloads with forward returns.
- `obs/{traces,metrics,logs}/` — token totals, retries, cache hits.

The exercise produced a list of seven bugs / behavioural defects.
Iteration 1 addresses **four of them** (bugs #1, #3, #5, #6, #7 — #5
and #7 are two faces of the same prompt issue and were fixed
together). Bugs #2 and #4 are deferred to a later iteration; see §5.

---

## 2. Bugs addressed in iteration 1

### Bug #1 — JNJ-style salvage gate too narrow (intent=`update` with no fields)

**Symptom.** On several ticks (notably GOOGL early in the window, JNJ
mid-window) the strategist emitted `intent: "update"` while writing
prose like *"Updating target to reflect the new acquisition catalyst"*
— but populated **none** of `target_price` / `stop_price` / `horizon`
/ `catalyst`, and supplied no `reason`. The strict validator path
raised `MissingThesisFieldsError`, which the retry wrapper then
rerolled three times against an LLM that kept saying the same thing,
aborting the tick.

**Root cause.** The earlier salvage shim (commit `5678744`) only
coerced *update-without-thesis-fields-but-with-reason* into `hold`. A
genuine empty payload (no reason either) still raised — but the
emitted shape was structurally identical to a valid `hold`, so the
executor would do nothing in either case.

**Fix.** Widened the salvage gate in
`src/agents/strategist/stance_schema.py` to coerce
*structurally-empty update* (no thesis fields, no weight, reason
optional) into a hold, synthesising a placeholder reason when
`reason is None`. The strict path still fires for genuinely malformed
stances (weight set, or thesis fields with reason missing) so real
bugs surface loudly. WARN log `stance_update_coerced_to_hold` keeps
the salvage observable — a spike in the rate signals the prompt or
verb set needs revisiting.

### Bug #3 — SPY benchmark not apples-to-apples with the bot

**Symptom.** The chart's orange SPY line and the metrics file's
vs-SPY delta disagreed on what SPY had actually done. We could not
trust the headline `−2.27 pp` until the two agreed.

**Root cause.** Two independent methodologies in
`src/backtest/reporting.py`:

- `_compute_vs_spy_delta` used `spy_bars[0].open` to `spy_bars[-1].close`
  (open-of-first-bar → close-of-last-bar).
- `_build_equity_figure` rebased SPY using `spy_bars[0].close` as the
  anchor (close-of-first-bar) and emitted one point per OHLCV bar
  (one per trading day), not per tick.

The chart and the metric were therefore valuing SPY at different
anchor prices and at different intraday cadences from each other and
from the portfolio, which itself snapshots at every tick (open and
close).

**Fix.** New single-source-of-truth helper
`_spy_benchmark_series(equity, cache, starting_cash)`. Models SPY as
a buy-and-hold position the bot opens at the very first tick
(`spy_shares = starting_cash / spy_price_at_first_tick`), then values
it at every subsequent tick using `bar.open` for open-phase ticks and
`bar.close` for close-phase ticks (classifier threshold: 17:00 UTC,
robust to DST and early-close half-days). Both `_compute_vs_spy_delta`
and `_build_equity_figure` now consume this same series so they
cannot drift apart on anchor, phase, or cadence.

### Bug #5 / Bug #7 — Strategist never `add`s and has no entry discipline

**Symptom.** Across the entire window the strategist used `intent:
"add"` zero times, opened only when the cold-start prompt forced it,
and sized every open near the 5 % cap. Combined with a slow-trickle
gain pattern from the existing holdings, the bot drifted while SPY
rallied.

**Root cause.** The instruction template documented `add` in the verb
table but never *explained* when to use it, never told the model the
max position weight or per-tick delta, and gave no entry-discipline
rules (e.g. when to size up vs initial probe).

**Fix.** Added a 7-line paragraph to
`src/agents/strategist/prompts.py` under "Choosing between hold and
update" covering: when `add` is the right call (conviction grew, no
red flags, room under the cap); explicit reference to the
`{{MAX_POSITION_PCT}}` token (mild duplication is acceptable here —
the cap is load-bearing); sizing guidance for opens (small probe vs
full conviction). No ticker-specific bias, no duplicated rules — the
addition is succinct and additive, as agreed.

### Bug #6 — Strategist has no per-ticker P&L history

**Symptom.** After closing TSLA at −4 % the strategist re-opened it
two ticks later at the same conviction, with no acknowledgement that
the previous round-trip had lost money. The model had no working
memory of its own outcomes.

**Fix.** Rolling closed-trades log threaded through three files:

1. `src/agents/executor/agent.py` — on every full close, append a
   compact dict (`ticker`, `closed_at`, `pnl_pct`, `holding_hours`,
   `close_reason`) to `state["user:closed_trades_log"]`, capped at
   the most-recent 10. The same key rides on the yielded
   `state_delta` so it survives the `DatabaseSessionService` round-trip.
2. `src/agents/strategist/context_shim.py` — new `_render_recent_trades`
   helper turns the log into a compact text block (last 8 rows) under
   the prompt slot `temp:recent_trades_view`. Explicit empty-state
   copy when the log is empty.
3. `src/agents/strategist/prompts.py` — new `## Recent Round-trips`
   section renders the slot directly above `## Current State`.

Total source surface: ~30 lines + ~50 test lines, within the "few
lines" budget the user set.

---

## 3. Tests

All four fixes are covered by new or updated unit tests; 29 reporting
tests + 258 strategist / backtest / executor-bookkeeping tests pass
locally:

- `tests/unit/agents/strategist/test_stance_schema.py` — renamed
  `_still_raises` test to `_coerces_to_hold`, asserts the synthetic
  reason text and the WARN log.
- `tests/unit/agents/strategist/test_context_shim.py` —
  `temp:recent_trades_view` now expected in the state-delta key set,
  plus an isinstance assertion.
- `tests/unit/agents/strategist/test_prompts_v2.py` — pre-substitutes
  the new placeholder before `.format()`.
- `tests/executor/test_executor_bookkeeping.py` — new
  `test_full_exit_appends_to_user_closed_trades_log` covering the
  rolling-log write and the state-delta carry.
- `tests/unit/backtest/test_reporting.py` — all four
  `TestComputeVsSpyDelta` cases updated for the `starting_cash`
  parameter; `TestBuildEquityFigure::test_three_lines_when_spy_present`
  rewritten against the new tick-aligned methodology (4 ticks → 4 SPY
  points, anchor at `bar1.open`, open/close phase per UTC hour).

---

## 4. Code changes — file-by-file

| File | Lines (insert/delete) | Notes |
|------|----------------------:|-------|
| `src/agents/executor/agent.py` | +63 / −38 | hoisted close-path compute; appended `user:closed_trades_log` |
| `src/agents/strategist/context_shim.py` | +40 / −0 | `_render_recent_trades` + new state-delta key |
| `src/agents/strategist/prompts.py` | +13 / −0 | sizing/entry paragraph + recent-trades section |
| `src/agents/strategist/stance_schema.py` | +28 / −25 | widened salvage gate, synthesised reason |
| `src/backtest/reporting.py` | +168 / −50 | `_spy_benchmark_series` + refactor of both consumers |

Tests: `+243 / −59` across the five test files listed in §3.

---

## 5. Out of scope for iteration 1

Two bugs from the audit list are deferred to a later iteration:

- **Bug #2** — analyst evidence freshness gating.
- **Bug #4** — risk-gate interaction with cash-buffer rule.

Both need their own design pass; folding them into iter-1 would have
broken the "succinct, no churn" rule the user set for the prompt edit.

---

## 6. Next steps

1. Re-run the same window as `full-backtest-iter-1` and diff the
   per-tick decision trail against `full-backtest-probe-1`:
   - Did the strategist start using `add`?
   - Did the JNJ-style salvage path fire (look for the WARN log)?
   - Did the "Recent Round-trips" block change re-entry behaviour
     after losing closes?
   - Do the chart and the vs-SPY metric now agree to within
     floating-point noise?
2. If the headline vs-SPY delta moved meaningfully, capture metrics
   and start drafting `baseline-window-2025-09-iter-2.md`.
3. If bugs #2 / #4 still bite, promote them out of §5 and into an
   iter-2 scope.
