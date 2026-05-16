# Backtest PIT correctness and audit — v2 (post-backtest) design

## 1 — Scope and goal

This spec is the v2 follow-up to
`docs/Phase6-backtesting-harness/specs/pit-correctness-and-audit-design.md`.
The v1 spec deliberately closed the worst-known structural leaks and shipped
the two-layer audit log; two HIGH-severity items were held back because their
priority and chosen mitigation depend on what the first real backtest's audit
log surfaces.

This spec captures those held-back items so they survive the wait and so the
v1 spec stays focused on what actually ships in Phase 6. It is intended to be
extended in-place after the first backtest is reviewed — at that point the
audit log informs (a) whether each item still matters at the originally
estimated severity and (b) which mitigation option to take.

### Guiding invariant (unchanged from v1)

> Data presented to analysts in a backtest at `as_of` is byte-identical to
> what they would see if the live pipeline ran at the same `as_of`. Any
> divergence is a leak and the backtest is fiction.

### Re-scoping checkpoint

The v1 spec was written before the first real backtest was run. The first
backtest will surface findings that change priorities. Treat this v2 spec
as a parking lot for v1's deferred items plus a placeholder for new leaks
the first audit log uncovers. After that audit log is reviewed, this spec
gains:

- A revised fix list (§2 below, extended with any newly observed leaks).
- A revised rollout (§3 below, sequenced against the live deployment plan).
- A revised set of regression tests covering each newly fixed leak.


## 2 — Per-leak fix list (deferred from v1)

The row numbers preserve the v1 spec's §3 numbering so cross-references in
the v1 plan's self-review table stay readable.

| # | Severity | Site | Action | Detail |
|---|---|---|---|---|
| 3 | HIGH | `src/data/providers/stats/yfinance.py::fetch_price_history` + the cache-fill path | **Patch** | Pass `auto_adjust=False` to `yf.download`/`yf.Ticker.history`; cache OHLCV plus a separate split-event table; apply adjustments at read time bounded by `as_of`. Or assert at fill time that the most-recent split date for every ticker is **before** the first split date that would alter any cached bar — fail the fill if violated. Choice deferred to plan stage. |
| 4 | HIGH | `src/data/providers/company_ratios/pit_composite.py` | **Patch (during implementation)** | Stamp `as_of_date` with the SEC `acceptedDateTime` of the underlying 10-K/10-Q, not the fiscal period-end. The Phase 6 data-fill spec describes the provider but not the timestamp semantics; this spec pins them. |

### Why these were deferred

- **Row 3 (yfinance `auto_adjust`).** The plan stage will choose between
  (a) cache unadjusted + maintain splits separately, or (b) assert
  fill-date < first-split-date. The first audit log will show whether
  retroactive adjustment is actually affecting any backtested window
  (e.g. SVB-2023) before we commit to the heavier option.

- **Row 4 (`pit_composite` `acceptedDateTime`).** The Phase 6 data-fill
  spec implements the provider; the timestamp-semantics fix lands during
  that implementation or immediately after. It was held out of v1 to
  avoid coupling the audit-log rollout to data-fill timing.


## 3 — Rollout

The v1 spec shipped eight commits (`feat(data): introduce
timeguard.resolve_as_of` through `feat(backtest): politician_trades +
report_cache PIT hardening`). The v2 rollout extends that sequence:

9. **`fix(providers): yfinance unadjusted + split-aware adjustment`** —
   chosen mitigation for row 3. Regression test under
   `tests/backtest/leak_regressions/test_yfinance_unadjusted_or_split_aware.py`.

10. **`fix(providers): pit_composite stamps acceptedDateTime`** — chosen
    mitigation for row 4. Regression test under
    `tests/backtest/leak_regressions/test_pit_composite_uses_accepted_datetime.py`.

Each lands as an independent commit. Both must produce a clean audit-log
tripwire summary on the original SVB-2023 window before any second window
is run.


## 4 — Newly observed leaks (TBD)

Reserved. Populated after the first backtest's audit log is reviewed. Each
new entry follows the v1 spec's §3 table shape (severity / site / action /
detail) and slots into §3's commit sequence.


## 5 — Future work / known unfixables

The v1 spec's §7 ("Future work / known unfixables") still applies
unchanged — LLM model knowledge cutoff, embedding model cutoff, free-tier
news history depth, and the lack of a live-run audit log. Nothing in this
v2 spec changes those.


## References

- `docs/Phase6-backtesting-harness/specs/pit-correctness-and-audit-design.md`
  — v1 spec; this spec extends it.
- `docs/Phase6-backtesting-harness/plans/pit-correctness-and-audit.md`
  — v1 plan; its self-review table flags rows 3–4 as deferred to this spec.
- `docs/Phase6-backtesting-harness/specs/backtest-data-fill-design.md`
  — prerequisite spec; row 4 lands during/after its implementation.
- Memory: `project-backtest-pit-correctness-deferred` — the original leak
  inventory.
