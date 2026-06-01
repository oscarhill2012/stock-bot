# Long-baseline 2025 — audit iteration 1 (first 6-month run)

**Run audited:** `backtests/long-baseline-2025/runs/full-backtest-iter-1/`
**Window:** 2025-09-02 → 2026-03-02 intended; **interrupted after 186 ticks
(~4.5 months)** on 2026-05-27 09:50 UTC at tick `2026-01-13T21:00:00`.
**Headline result:** total return **+3.61 %** over the truncated run,
vs-SPY **−5.88 pp**, vs matched-exposure SPY **−2.94 pp**, Sharpe 0.99,
max drawdown −5.77 %, **19 closed round-trips** at 42.1 % win rate.
LLM cost 23.7 M tokens across 4 030 model calls, 48.5 % cache hit rate,
**59 hallucinated stances** (vs 4 in the 30-day iter-5).
**Apples-to-apples baseline:** `baseline-2025-09/runs/full-backtest-iter-5/`
— same git SHA `8f0f94d`, same 20-ticker watchlist, same start date,
30-day window.

The user flagged two symptoms after the run aborted: (a) performance
worse from the very first tick than the 30-day window on the same
period; (b) measurably more hallucinations than the 30-day comparison.
Both symptoms were initially attributed to memory degradation. The
audit traced them to two distinct, silently-degrading mechanisms — one
in the data cache, one in the strategist's verb-emission behaviour.
Memory itself was verified clean.

Bug numbering continues from iter-2 (last bug was #17).

---

## 1. Audit scope

Materials walked:

- `manifest.json` — run metadata (git SHA `8f0f94d`, 186 ticks recorded,
  status `interrupted`).
- `report/metrics.md` and `report/equity_curve.png` — headline figures
  and PnL trajectory.
- `db.sqlite` (`portfolio_snapshots`, `ticker_stances`, `trade_log`,
  `analyst_evidence`, `ticker_evidence`, `buffer_entries`).
- `session.sqlite` (`sessions` × 187) — per-tick ADK session state for
  inspection of `memory_buffer`, `day_digest`, `positions`, `thesis`.
- `decisions/*.json` × 199 — strategist decision payloads with full
  analyst evidence attached.
- `obs/logs/*.json` × 186 — per-tick observability logs (used to
  enumerate hallucinated-stance occurrences).
- `store.sqlite` — the window's data cache, cross-compared against
  `backtests/baseline-2025-09/store.sqlite`.
- Source: `src/data/providers/news/finnhub.py`,
  `scripts/backtest_fetch.py`,
  `src/agents/executor/_verb_dispatch.py`,
  `src/agents/memory/{writer,compress}.py`,
  `src/orchestrator/persistence.py`.

---

## 2. Headline finding — memory is NOT the cause

The user's first hypothesis was that the rolling-memory subsystem was
degrading the strategist's decisions late in the run. Verified clean:

- `memory_buffer` cap of 24 entries is respected; sampled FIRST / MID /
  LATE / LAST sessions all show valid buffer state, with oldest
  entries correctly evicted into `day_digest`.
- `day_digest` cap of 2 000 chars is respected (mid-run 1 868 chars,
  late-run plateaued at 2 000 — the LLM compressor is engaging as
  designed once eviction crosses the budget).
- The `is_repeat` semantic-dedup flag fires correctly at ~75 % rate
  mid-run, signalling that the strategist is repeating itself —
  itself a symptom of the upstream bugs documented below, not a
  failure mode of the memory subsystem.

Latent finding (not run-affecting but worth flagging): the
`save_buffer_entry` / `load_recent_buffer` helpers in
`src/orchestrator/persistence.py` have **no callers**. The
`buffer_entries` SQL table is created on every run and never written
to. Memory lives entirely in ADK session state. The dead persistence
path is harmless today but is a footgun for anyone who assumes the
table is authoritative.

---

## 3. Bug #18 — News-cache silent truncation drops the start of the window

**Symptom.** On tick 1 (2025-09-02 13:30 open), with the **same SHA,
same watchlist, and same start date** as iter-5:

| | iter-5 (30d) | iter-1 (6m) |
|---|---|---|
| Tickers with real fundamental data | 19/20 | **5/20** |
| Tickers with real news data | 20/20 | **7/20** |
| First-tick buys | AVGO, BAC, GOOGL, MSFT, WMT, XOM (6) | BAC, XOM (2) |
| Capital deployed by tick 1 | $37k | $10k |

The long run starts catastrophically under-deployed because most
tickers' analyst evidence comes back `is_no_data=True`.

**Root cause.** `src/data/providers/news/finnhub.py` lines 443–446:

```python
all_articles.sort(key=lambda a: a.published_at, reverse=True)
if limit is not None:
    all_articles = all_articles[:limit]
```

The provider chunks Finnhub calls weekly (correct — works around
Finnhub's per-call ~250-article truncation), merges them, sorts
**newest-first**, then takes the first `limit=2000` items (the cap
set by `scripts/backtest_fetch.py:226` for backtest cache-fill).

For high-volume tickers over a 6-month window, total articles exceed
2 000 — so the cache silently keeps only the **most recent** 2 000
and discards everything from the start of the window. Verified in
`long-baseline-2025/store.sqlite`:

| Ticker | rows | earliest cached article (window starts 2025-09-02) |
|---|---|---|
| MSFT  | 2000 | **2026-01-04** |
| GOOGL | 2000 | **2026-01-05** |
| META  | 2000 | **2026-01-04** |
| NVDA  | 2000 | **2026-01-05** |
| AAPL  | 2000 | **2026-01-02** |
| CRM   | 2000 | **2025-10-13** |
| WMT   | 2000 | **2025-11-10** |
| JPM   | 2000 | **2025-11-17** |
| BAC, RTX, UNH, PG, XOM, LMT | <2 000 | covers the whole window |

Every ticker that hit the 2 000 cap has news entirely post-dating the
window start; the bot sees `is_no_data=True` for those names on every
PIT query before the cached date range begins.

**Why the cache audit passed.** The existing audit checks
PIT-correctness (no `published_at > as_of`) and row validity, not
**coverage parity per (ticker, tick)**. A cache that is empty for
early ticks is trivially PIT-correct.

**Why this is invisible in iter-5.** All 20 tickers fit comfortably
under the 2 000 cap in a 30-day window, so the cap never triggers.

**Why performance was worst from the start and recovered late.** The
underperformance maps precisely onto news availability: the most
severe alpha gap is October–November (when capped tickers have zero
news), and recovers in December (when the simulated clock approaches
the cached date range). Concentration of trades in the few
fully-covered tickers (LMT, RTX, UNH, WMT, JNJ, BAC, XOM) — i.e.
**defensives and value, with mega-cap tech essentially absent** — is
the direct consequence and explains the SPY underperformance.

**Recommended fixes (not yet implemented).**

1. In `finnhub.py:443–446`, raise loudly when `len(all_articles) == limit`
   at backtest cache-fill time, or remove the cap on the fill path and
   keep it only for live ticks. The dispatcher's live-tick cap is the
   one that should bound prompt size; the fill should preserve full
   coverage.
2. Extend the cache audit to flag every (ticker, table) where
   `MIN(published_at) > window_start - lookback`. The current "passes
   with similar results" wording is misleading because it tests row
   validity, not coverage.

---

## 4. Bug #19 — Strategist re-emits stale `sell` for non-held tickers

**Symptom.** 59 hallucinated stances in iter-1 versus 4 in iter-5.
Per-tick rate **~5×** higher in the long run.

**Distribution.** Five tickers explain all 59 occurrences:

| Ticker | Count | Pattern |
|---|---|---|
| CVX  | 18 | 18 consecutive ticks 2025-12-01 → 2025-12-11 |
| AVGO | 17 | similar consecutive-tick run |
| BAC  | 17 | similar consecutive-tick run |
| UNH  | 4  | smaller cluster |
| JNJ  | 3  | smaller cluster |

55/59 carry `prior_row="None"` (no thesis row at all); 4/59 carry
`"no-position"` (row exists, no live exposure). 56/59 occur in
December–January (late run).

**Verified mechanism (CVX walk-through, `ticker_stances` table).**

- All of November (38 ticks): strategist emits `update` /
  `no_action` with `preferred_weight=0.0` — "I am watching CVX with
  growing bearish conviction, not holding".
- 2025-12-01 14:30 → 2025-12-11 14:30: **18 consecutive `sell`
  lifecycle actions** for CVX.
- `decisions/` confirms CVX was not held during this stretch — the
  last real exit was Oct 13/16, and re-entry not until Dec 31.

All 18 CVX hallucinations are the **same stale stance, replayed every
tick for ten trading days** until something else changes in the
context. AVGO and BAC show the identical pattern at different
windows. The count is not 59 distinct mistakes — it is 5 chronic ones
on repeat.

**Root cause.** The four-verb vocabulary (`buy` / `sell` / `update` /
`no_action`) is asymmetrically defined: `sell` means "close my
position" while `update` means "express a view without acting". The
strategist conflates them once its rolling thesis prose on a
watched-but-never-held ticker grows emphatically bearish — it reaches
for `sell` to express decisive conviction rather than `update`. The
executor catches every invalid sell via the `HALLUCINATED` sentinel
in `src/agents/executor/_verb_dispatch.py:272`, but **the suppression
never feeds back to the strategist**, so the same context on the next
tick primes the same stance.

**Why this is much worse in the long run.**

1. **Drift needs time.** In 30 days a watched non-held name does not
   accumulate enough negative reinforcement to tip verb choice. Over
   months, the LLM-compressed `day_digest` preserves emphatic
   phrasings ("closing CVX due to strong bearish fundamental signal")
   long after the actual close, and the persistent free-form
   `thesis` blob keeps re-stating the bearish view.
2. **No termination signal.** Once tipped, the same sell re-fires
   until either (a) the digest churns enough to reset the strategist's
   mental model or (b) the strategist opens a position on that ticker.
3. **Coupling with Bug #18.** The five hallucination tickers overlap
   heavily with the names whose news coverage was either incomplete or
   over-concentrated (BAC, UNH, JNJ). With no fresh evidence to revise
   the view tick-to-tick, the prose ossifies and accelerates the
   drift toward a stale `sell`.

So Bug #19 is not independent of Bug #18 — it is a **second
consequence of the same underlying failure mode** (silent degradation
that compounds over time; executor-side suppression without
strategist-side feedback). Both are masked in the 30-day window
because (a) caches do not hit caps and (b) prose drift has not had
time to take hold.

**Recommended fixes (not yet implemented).**

1. **Verb-guard at the strategist.** Forbid `sell` on tickers whose
   freshly-read `positions` field does not contain them — analogous to
   the buy-cap rule. Most cleanly added as a schema-level invariant in
   `agents/strategist/stance_schema.py` rather than relying on prompt
   discipline.
2. **Feed back suppression.** When the executor returns `HALLUCINATED`,
   write `state["user:rejected_stances_last_tick"] = [...]` so the
   strategist's next-tick prompt explicitly sees "you tried to sell X
   with no position — use `update` to express a bearish view". Without
   this loop closure the strategist has no signal that its previous
   stance was dropped.

---

## 5. Memory architecture — follow-up proposal

The audit also surfaced a thinking-aloud observation from the user
worth capturing before it is lost. The current memory subsystem has
two overlapping persistence paths:

- **`memory_buffer`** — 24-entry rolling FIFO of compact decision tags
  + 120-char reasoning summaries + `is_repeat` dedup flag.
- **`day_digest`** — 2 000-char LLM-recompressed running prose
  containing fragments of the strategist's accumulated reasoning.

Both end up serving the same purpose at prompt-render time
(reminding the strategist what it has already said), and Bug #19
suggests that pattern is what lets stale prose drive stale verbs.

**Proposed direction (for future brainstorming, not a decided plan).**

Split the two paths along clear axes:

1. **Persistent signal-keyed trade-outcome memory** — capture every
   round-trip as a structured row (`ticker`, `opened_at`, `closed_at`,
   `pnl_pct`, `holding_hours`, `close_reason`, optional embedding of
   the entry rationale). Capped much higher than 24, queried by
   similarity / ticker / recency at prompt-build time rather than
   blindly slotted in. Bug #6 already wired a tiny version of this
   (`user:closed_trades_log`, cap 10); this would be the structured
   generalisation.

2. **"Current state" header** — replace `day_digest` with a
   programmatically-rendered per-ticker snapshot at the **top** of the
   strategist prompt: current weight, thesis one-liner, ticks since
   last review, last close reason if recently exited. No LLM
   compression in the loop — deterministic rendering from the
   position book and the most recent stance row. The strategist reads
   "real state first, history second", and the verb choice for "I'm
   bearish on CVX but don't hold it" becomes mechanically obvious.

**Trade-off worth surfacing now.** Collapsing `day_digest` into
structured state loses the narrative-prose context the strategist
currently uses to recall its multi-day thinking. Some of the
strategist's coherent decision-making seems to emerge from that
rolling narrative. Replacing it entirely with tables may improve
correctness (no more stale "closing CVX" phrasings priming repeat
sells) at the cost of nuance and continuity. Worth bottoming-out the
trade-off before committing — either by mocking the new prompt
against historical tick state, or by running them in parallel and
diffing strategist decisions.

This proposal subsumes the Bug #19 "feed back suppression" fix —
the rejected-stance list would naturally live in the structured
current-state block. It does not subsume Bug #18, which sits below
the agent layer entirely.

---

## 6. Tripwire summary

The unifying lesson across both bugs: **silent failures that compound
over time**. The 30-day window is short enough that neither bug
surfaces; the 6-month window is the smallest test that exposes
either. Suggested process additions:

- **Coverage parity check** during cache fill — assert that
  `MIN(published_at) ≤ window_start` for every (ticker, table) before
  declaring the cache ready.
- **Hallucination rate watchdog** during runs — if any single ticker
  produces ≥ N consecutive hallucinated stances, raise a tripwire
  rather than silently dropping the stances.
- **Run length in CI** — at least one mid-length (~60-day) backtest
  exercised on PRs that touch the strategist prompt, the verb
  dispatch, or the cache-fill scripts. The 30-day window is too short
  to surface compounding failures.

---

## 7. Bugs catalogued

- **Bug #18** — News-cache silent truncation drops start-of-window
  coverage for high-volume tickers (`finnhub.py:443–446` +
  `backtest_fetch.py:226`).
- **Bug #19** — Strategist re-emits `sell` for non-held tickers in
  consecutive-tick chains; executor suppresses but does not feed back
  (`_verb_dispatch.py:255–272`).

Latent / non-affecting:

- `buffer_entries` SQL table is created but never written
  (`orchestrator/persistence.py` has no callers).
