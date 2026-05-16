# PIT Correctness and Audit — v2 (post-backtest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** **Placeholder — awaiting first-backtest audit-log review.** This plan exists so the v1 plan stays focused on what actually ships in Phase 6 and so the deferred work is not lost. Concrete steps are written only after the first real backtest produces an audit log; at that point §2 below is replaced with task-by-task implementation steps in the v1 plan's house style.

**Goal:** Implement the two HIGH-severity fixes the v1 spec deferred to v2 — yfinance retroactive-adjustment handling and `pit_composite` `acceptedDateTime` semantics — plus any new leaks the first audit log surfaces.

**Reference spec:** `docs/Phase8-post-backtest-fixing/specs/pit-correctness-and-audit-v2.md`.

**Shell convention:** Bash tool runs in the project root. Never prepend `cd <root> &&`. Run pytest as `PYTHONPATH=src .venv/bin/python -m pytest …`, ruff as `PYTHONPATH=src .venv/bin/python -m ruff check …`.

**Style:** British English everywhere (comments, prose, identifiers). Function docstrings required. Whitespace for legibility.

**Rollout note:** Each task lands as an independent commit, continuing the v1 plan's numbering (Tasks 9, 10, …). Both deferred tasks must land before any second backtest window is configured.

---

## 1 — Pre-conditions before this plan is fleshed out

- [ ] The first real backtest (Phase 6 SVB-2023 window or equivalent) has been run end-to-end.
- [ ] The Layer-1 audit-log tripwire summary for that run has been reviewed by a human.
- [ ] The reviewer has decided, for **Task 9 (yfinance)**, whether to take mitigation (a) cache unadjusted + maintain splits separately, or (b) assert fill-date < first-split-date. The v2 spec leaves this open.
- [ ] The Phase 6 data-fill implementation has reached the point where `src/data/providers/company_ratios/pit_composite.py` exists (or is about to). **Task 10 (`pit_composite`)** lands during or immediately after that work.
- [ ] Any newly observed leaks from the audit log have been triaged into the v2 spec's §4 "Newly observed leaks" table.

Until all pre-conditions hold, do **not** start writing concrete task steps. The whole point of deferring this plan is to avoid committing to an implementation strategy before the first audit log informs the choice.

---

## 2 — Tasks (to be written post-backtest)

### Task 9: yfinance unadjusted + split-aware adjustment

**Files (provisional):**
- Edit: `src/data/providers/stats/yfinance.py`
- Edit: `src/backtest/cache/store.py` (split-event table read path) — if mitigation (a) chosen
- Edit: `src/backtest/cache/schema.py` — if mitigation (a) chosen (new `SplitEventRow`)
- Edit: `src/backtest/cache/fetcher.py` — if mitigation (b) chosen (fill-time assertion)
- Test: `tests/backtest/leak_regressions/test_yfinance_unadjusted_or_split_aware.py`

**What & why:** See v2 spec §2 row 3. Steps to be written after mitigation choice is made.

### Task 10: `pit_composite` stamps `acceptedDateTime`

**Files (provisional):**
- Edit: `src/data/providers/company_ratios/pit_composite.py`
- Test: `tests/backtest/leak_regressions/test_pit_composite_uses_accepted_datetime.py`

**What & why:** See v2 spec §2 row 4. Steps to be written once the Phase 6 data-fill implementation of `pit_composite` is in place.

### Tasks 11+: Newly observed leaks

Reserved. One task per row added to v2 spec §4 after the first audit-log review.

---

## Self-review

After all v2 tasks land, walk the v2 spec's §2 fix list one more time and assert each row is covered by a Task here. Re-run the full leak-regression suite (`tests/backtest/leak_regressions/`) plus the end-to-end smoke test, and confirm the audit-log tripwire summary on the original SVB-2023 window remains clean.
