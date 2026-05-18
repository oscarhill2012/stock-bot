# Phase 7.6 — Data-shape contracts — done

**Closed:** 2026-05-18
**Branch:** `worktree-phase7.6-data-shape-contracts`
**Commits:** 19 (one audit, four foundations, twelve per-domain,
one smart-money reshape, one aggregator delete, one final sweep)

---

## What landed

- **`Provider[T]` Protocol + `DomainShape` dataclass** — added to
  `src/data/registry.py`.  Every registered domain now declares its
  cardinality (`"single"` / `"list"` / `"bundle"`) and its canonical
  return type in a single `DOMAIN_SHAPES` dict (12 entries).

- **Parametrised contract test** —
  `tests/contract/test_provider_shapes.py` enforces live and cache
  return shapes for all 12 domains.  Domains whose provider is
  absent or live-only are correctly skipped/xfail-guarded.  Both
  pending sets (`_LIVE_PENDING`, `_CACHE_PENDING`) are empty at close.

- **`SmartMoneyRaw` Pydantic model** (`src/models/smart_money.py`) —
  replaces the category-first nested-dict shape in
  `state["smart_money_data"]`.  State is now ticker-first:
  `dict[str, SmartMoneyRaw]`.

- **12 per-domain alignment commits** — audit document
  (`docs/Phase7.5-more-cleanup/plans/data_shape_contracts_v1.md`)
  records the original drift inventory.  Commits with `chore(phase7.6)`
  confirm no-drift domains; `refactor(...)` commits fix drift.

- **Aggregator + orphan deletion** — `src/data/aggregator.py` removed,
  along with three orphaned tests and the `__all__` re-exports.
  `StockSignalBundle` and `ProviderError` dead models also removed.

---

## Behavioural changes worth flagging

These changes alter observable return types or error semantics.
Any caller that relied on the old shape must be updated (all known
callers were updated as part of their respective commits).

| Domain | Old behaviour | New behaviour |
|---|---|---|
| `company_ratios` cache | returned `None` on miss | raises `KeyError` |
| `social_sentiment` cache | returned `None` on miss | returns empty `SocialSentiment()` |
| `analyst_consensus` live | returned a bare tuple | returns `AnalystConsensusBundle` |
| `options` live | returned a `dict` | returns `list[OptionContract]` |
| `state["smart_money_data"]` | category-first nested dict (`["politicians"][ticker]`) | ticker-first `dict[str, SmartMoneyRaw]` |
| `_common.py` `make_evidence_callback` | passed raw objects to extractors | calls `.model_dump()` on Pydantic instances before passing |

---

## Acceptance grep evidence

All greps run against `HEAD` of `worktree-phase7.6-data-shape-contracts`
on 2026-05-18.

```
# 1a) xfail markers — no actual xfail decorators on any test
#     (hits are comments and parametrize logic conditioned on empty sets)
$ grep -n "xfail" tests/contract/test_provider_shapes.py
4:Phase 7.6 lands this test with xfail markers on every domain whose live
6:task removes one xfail by aligning live and cache providers.
23:Domains in ``_PENDING_ALIGNMENT`` are marked ``xfail(strict=True)`` — the
563:    Domains in ``_LIVE_PENDING`` get ``xfail(strict=True)`` because their live
579:                    marks=pytest.mark.xfail(
593:    Domains in ``_CACHE_PENDING`` get ``xfail(strict=True)`` because their
609:                    marks=pytest.mark.xfail(
# No xfail decorator fires at runtime — both pending sets are empty.

# 1b) StockSignalBundle / get_stock_signal_bundle — no output (clean)
$ grep -rn "get_stock_signal_bundle\|StockSignalBundle" src/ tests/ scripts/
(no output)

# 1c) Pending sets — both are empty set()
$ grep -n "_PENDING_ALIGNMENT\s*=\|_LIVE_PENDING\s*=\|_CACHE_PENDING\s*=" tests/contract/test_provider_shapes.py
553:_LIVE_PENDING: set[str] = set()
557:_CACHE_PENDING: set[str] = set()

# 1d) DomainShape instantiation count — 12
$ grep -c "DomainShape(" src/data/registry.py
12
# Breakdown (all 12 are domain entries in DOMAIN_SHAPES; the class
# definition itself uses a dataclass decorator, not DomainShape()):
#   price_history, company_ratios, news, social_sentiment,
#   insider_trades, politician_trades, notable_holders, filings,
#   earnings, analyst_consensus, short_interest, options

# 1e) Old smart_money_data category keys — no output (clean)
$ grep -rn 'smart_money_data\["politicians"\]\|smart_money_data\["notable_holders"\]' src/ tests/
(no output)
```

---

## Final test results

### Unit suite (not slow, not integration)

```
2 failed, 878 passed, 4 skipped, 9 deselected, 5 warnings in 20.82s
```

Both failures are pre-existing tech debt, unrelated to Phase 7.6
(see Follow-ups below).

### End-to-end smoke (`tests/integration/backtest/test_end_to_end_smoke.py`)

```
1 passed, 4 warnings in 6.14s
```

---

## Risks recorded for the next phase

- **No startup-time registry completeness check.** Any future provider
  added to `DOMAIN_SHAPES` must also pass the contract test, but there
  is currently no import-time or startup-time assertion that catches a
  missing entry.  A registry validator could enforce parity; mitigation
  deferred per spec's "anything you want strengthened here?" gate.

- **Four live-only domains have no cache contract test.**
  `analyst_consensus`, `earnings`, `options`, and `short_interest` are
  correctly skipped (`SKIPPED: has no cache provider — live-only`) rather
  than failing, but they are not exercised in offline test runs.  If a
  cache provider is added later, the test will automatically pick it up.

---

## Follow-ups

- **Pre-existing test failures (out-of-scope tech debt, not introduced
  by Phase 7.6):**
  - `tests/replay/test_replay_30days.py::test_replay_30_days_runs_and_produces_executions`
  - `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py::test_fetch_accepts_as_of_kwarg`
  Both are environment-related; neither touches the data-shape contract
  work.  Queued for investigation in a separate session.

- **Phase 7.5 spec/plan amendments** — any plan sections that referenced
  `aggregator.py` or the old `StockSignalBundle` surface should be
  annotated as superseded now that 7.6 has deleted those artefacts.
