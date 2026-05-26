# T-F01 — Surfacing primitive and silent-failure inverts

**Wave:** 4 (keystone)
**Pairs source-audit fix:** F4 (silent-failure attractor sweep across analysts, data providers, observability, snapshotter, executor)
**Branch:** `fix/T-F01-surfacing-primitive-and-inverts` (single-PR option) or `fix/T-F01a-surfacing-primitive` + `fix/T-F01b-silent-failure-inverts` (split option — see recommendation below)
**Depends on:** T-F10 (layout sweep — finalised paths), T-F07 (SmartMoney delete — removes one batch of inverts from this spec)
**Estimated diff size:** large (single PR) / medium + medium (split)

## Scope

Introduce a single shared surfacing primitive
(`emit_feature_warning(...)` and `emit_branch_failed(...)`) used by
every agent / provider / observability site that currently swallows an
upstream failure into a defensible-looking neutral payload, then walk
the silent-failure attractor inventory the source-audit catalogued and
either (a) replace the swallow with a raise where the violation is a
contract failure, or (b) replace the swallow with a `branch_failed`
warning + bounded degraded payload where the failure is genuinely
recoverable. In every site where a test currently *asserts the
swallow as correct*, invert the test to assert the new surfacing
behaviour — this is the "test inversion" half of the keystone work.
The primitive itself is small; the application sites + paired test
inversions are substantial, which drives the single-vs-split
recommendation below.

### In scope

- **New helper module** `src/agents/_common/surfacing.py` (location to be
  confirmed against the layout sweep — could also be
  `src/contract/surfacing.py` if the keystone wants the primitive in the
  contract package). The module exports:
  - `emit_feature_warning(logger, *, agent: str, ticker: str | None,
    feature: str, reason: str, exc: BaseException | None = None) ->
    None` — emits a `WARNING`-level log record carrying a stable
    `branch_failed=True` marker plus structured context, **does not**
    swallow the exception itself (caller chooses to raise or continue).
  - `emit_branch_failed(logger, *, branch: str, reason: str, exc:
    BaseException | None = None) -> None` — analogous primitive for
    coarse-grained branch failures (an entire analyst, a whole tick
    phase).
  - One pytest fixture (`tests/_common/fixtures.py` or
    `conftest.py`-level): `assert_no_branch_failed(caplog)` —
    standardises the test-side assertion so every inverted test reads
    the same way.
- **Application sites** (each replaces a silent swallow with either a
  raise, or a `emit_feature_warning` call + bounded degraded payload —
  the disposition per site is recorded in the source-audit row noted
  below):
  - `src/agents/analysts/fundamental/fetch_agent.py:118-163` — three
    try/except blocks around `get_company_ratios`,
    `get_company_filings`, `get_insider_trades`. Replace each with a
    surfacing call; keep the partial-payload degradation but emit
    `feature_warning` per failing provider.
  - `src/agents/analysts/news/fetch_agent.py:80-86` — try/except around
    `get_stock_news`. Same treatment.
  - `src/agents/analysts/technical/fetch_callbacks.py` (and equivalent
    in social) — per-ticker exception swallows in the fetch callback
    chain at the line numbers source-audit
    `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P1-04 cites.
  - `src/agents/snapshot/agent.py:60-74` — SPY-fetch swallow that
    silently sets `spy_price = 0.0`. Convert to a hard raise (per
    source-audit `docs/Phase11-project-audit/source-audit/agents-misc.md` P0-01: this is a
    contract violation, not a recoverable degradation — Snapshotter
    must not write a zero anchor).
  - `src/observability/trace.py:168-174` — `_trace_maybe` swallows
    everything; replace with `emit_branch_failed` so the trace failure
    is at least visible.
  - `src/observability/terminal_log.py:403-410` — `usage_metadata`
    silent `pass`; emit `feature_warning` so the metadata gap is
    visible.
  - `src/data/providers/edgar.py:288, 334, 636-637, 698` — EDGAR
    filings/insider-trades/notable-holders bare `except Exception:
    continue` blocks. Each becomes an `emit_feature_warning` call;
    none of these are contract violations (EDGAR is best-effort), but
    every dropped row must be surfaced.
  - `src/data/providers/finnhub.py:79-86` — Finnhub social-sentiment
    swallow. `feature_warning` per failed call.
  - `src/agents/executor/agent.py:124-179` (BUY-without-matching-stance
    silent path) — convert to a hard raise per source-audit
    `docs/Phase11-project-audit/source-audit/agents-executor.md` P1-02 (this is a contract
    violation: an order without an open-intent stance must not reach
    the broker).
  - `src/agents/executor/agent.py:379-390` (fill-price OR-chain dead-key
    fallback) — tighten the lookup and raise on missing key per
    source-audit P1-03; the "OR-chain" pattern is exactly the dead-key
    swallow the primitive is designed to surface.
  - `src/agents/memory/writer.py` — `decision.get("decision_tag",
    "unknown")` fallback per source-audit `agents-misc.md` P1-03.
    `feature_warning` plus retain the literal-string fallback for now
    (T-F05 will handle the analogous strategist-side fallback).
- **Test inversions** — paired one-for-one with the application sites.
  Each inverted test changes from `assert <swallow happened>` to either
  `pytest.raises(...)` or `caplog.records contains branch_failed=True`.
  The full list is the union of the test-audit "T4 missing surfacing
  test" and "T3 only-asserts-completion" findings whose source side is
  in the application-sites list above:
  - `tests/integration/test_snapshotter.py:47-83` — SPY swallow
    inversion (test-audit `agents-misc.md` P0-01).
  - `tests/unit/test_trace_writer_exception_logging.py:33-66` —
    `_trace_maybe` swallow inversion (test-audit `observability.md`
    P0-01).
  - `tests/unit/analysts/fundamental/test_fetch_degrades_on_provider_error.py`
    + the news twin — invert per test-audit `analysts-llm.md` P0-01 /
    P0-02.
  - `tests/unit/test_social_fetch.py:117-136` — invert the
    "assert swallow is correct" pattern per test-audit
    `analysts-deterministic.md` P0-03.
  - `tests/unit/data/providers/test_edgar_filings.py` /
    `test_edgar_insider_trades.py` — undefended P0-01/P0-02 per
    test-audit `data-providers.md`; this is "add test for new raise"
    rather than "invert existing".
  - `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py`
    — strengthen the near-empty `assert result is not None` per
    test-audit `data-providers.md` P0-03.
  - `tests/integration/test_executor_with_fake_broker.py` (the
    BUY-without-matching-stance case) — undefended P0-01 per test-audit
    `executor.md`.
  - The fill-price dead-key fallback test per test-audit `executor.md`
    P0-02.
  - `tests/unit/agents/memory/test_writer.py` — invert the
    `decision_tag="unknown"` fallback test (test-audit `agents-misc.md`
    P1-03).

### Out of scope

- The strategist `tick_id="unknown"` and `decision_writer` silent-no-op
  fallbacks (`agents-strategist.md` P1-04 and P2-05) — owned by T-F05.
- The `notable_holders` `as_of_date` field-mapping bug and its missing
  firing test — owned by T-F02 (it is a *missing* surfacing test, not
  an *existing* test that needs inversion).
- The `run_once` `except (AttributeError, BaseException)` narrow — owned
  by T-F02 (the surfacing primitive is overkill for a single
  narrow-the-except change; T-F02 handles it together with the other
  orphan surfacing tests).
- The `Trading212Broker.get_portfolio` silent-skip — owned by T-F02
  (the surfacing-test-only half) and T-F04 (the source-side raise).
- SmartMoney sites — deleted whole by T-F07 before this PR runs.
- The `MemoryWriter.decision_tag` *literal-string* itself (T-F05 may
  drop the field entirely after dropping the dual `PositionThesis` —
  if so, the surfacing call goes with it; coordinate at merge time).

## Findings closed

The headline source-audit ID is `F4` (silent-failure attractor sweep).
The per-site IDs are listed against the application steps below; the
test-audit side is filed once against each inverted test. Where a
finding might also be closeable in T-F02 / T-F05, it is marked here
only in the spec that owns the *source*-side change — the sibling
spec then closes the *test*-side half.

| Finding ID | File | Description |
|---|---|---|
| `agents-analysts-llm.md` P1-01 (source) | `src/agents/analysts/fundamental/fetch_agent.py` | Replace 3× swallow with surfacing primitive |
| `agents-analysts-llm.md` P1-02 (source) | `src/agents/analysts/news/fetch_agent.py` | Replace `get_stock_news` swallow with primitive |
| `agents-analysts-deterministic.md` P1-04 (source) | `src/agents/analysts/technical/fetch_callbacks.py`, `social/fetch_callbacks.py` | Replace per-ticker swallow with primitive |
| `agents-misc.md` P0-01 (source) | `src/agents/snapshot/agent.py:60-74` | Convert SPY swallow to raise |
| `observability.md` P1-02 (source) | `src/observability/trace.py:168-174`; `terminal_log.py:403-410` | Replace bare swallows with primitive |
| `data-providers.md` P1-01, P1-02, P1-03, P1-04 (source) | `src/data/providers/edgar.py`, `finnhub.py` | Replace bare-`except: continue` blocks with primitive |
| `agents-executor.md` P1-02 (source) | `src/agents/executor/agent.py:124-179` | Raise on BUY-without-matching-stance |
| `agents-executor.md` P1-03 (source) | `src/agents/executor/agent.py:379-390` | Tighten fill-price lookup; raise on missing key |
| `agents-misc.md` P1-03 (source) | `src/agents/memory/writer.py` | Surface the `decision_tag="unknown"` fallback |
| `agents-misc.md` P0-01 (test) | `tests/integration/test_snapshotter.py:47-83` | Invert SPY swallow assertion |
| `observability.md` P0-01 (test) | `tests/unit/test_trace_writer_exception_logging.py:33-66` | Invert `_trace_maybe` swallow assertion |
| `analysts-llm.md` P0-01, P0-02 (test) | `tests/unit/.../test_fetch_degrades_on_provider_error.py` (fundamental + news) | Invert "degrades silently" tests |
| `analysts-deterministic.md` P0-03 (test) | `tests/unit/test_social_fetch.py:117-136` | Invert swallow assertion |
| `data-providers.md` P0-01, P0-02 (test) | `tests/unit/data/providers/test_edgar_*.py` | Add new surfacing assertions |
| `data-providers.md` P0-03 (test) | `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py` | Strengthen near-empty assertion |
| `executor.md` P0-01 (test) | `tests/integration/test_executor_with_fake_broker.py` | Surface BUY-without-stance contract violation |
| `executor.md` P0-02 (test) | same file, fill-price test | Surface dead-key fallback |

## Implementation steps

1. **Land the primitive first.** Create `src/agents/_common/__init__.py`
   and `src/agents/_common/surfacing.py` (or the agreed contract-side
   location). Write the two helpers with full British-English
   docstrings and inline rationale comments. Whitespace-separate
   logical blocks. Export via `__all__`.

2. **Write the helper's own tests** in
   `tests/unit/agents/_common/test_surfacing.py`: assert
   `emit_feature_warning` emits a `WARNING`-level record with the
   structured fields populated, that `exc` is included in `extra`
   when provided, and that the helper does not raise. Same for
   `emit_branch_failed`.

3. **Pilot the primitive at one site** before fanning out — pick
   `src/observability/trace.py:168-174` as the pilot because it is
   self-contained and its inverted test
   (`tests/unit/test_trace_writer_exception_logging.py:33-66`) is
   tight. Land the pilot + its inverted test on the same commit.

4. **Fan out the remaining application sites** in
   subsystem-batched commits — one batch per source-audit subsystem
   so a `git log --oneline` reads cleanly:
   - Batch A (snapshotter + observability + memory writer) — small,
     three files.
   - Batch B (data providers — EDGAR + Finnhub) — four sites.
   - Batch C (analysts — fundamental + news + technical + social
     fetch agents) — five sites across four agents.
   - Batch D (executor — two sites in one file) — small.
   Each batch lands its source change + paired test inversions
   together so CI is green at every commit.

5. **Run the full `.venv/bin/python -m pytest tests/`** after each
   batch; do **not** proceed to the next batch on a red bar.

6. **Update `graphify-out/graph_delta.md`** with an entry for the new
   `_common/surfacing` module and the inverted test files.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in commit
  body).
- [ ] No new audit findings introduced — subagent should re-grep for
  `except Exception:` followed by `continue` / `pass` in `src/` and
  confirm the only remaining hits are intentional (documented per-site
  with a comment naming why surfacing was rejected).
- [ ] Graphify delta entry appended.
- [ ] At least one `caplog`-asserting test exists per inverted site
  (the inversion itself is the regression-guard against the silent-
  swallow returning).

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
```

## Risks and rollbacks

- **Risk: the snapshotter SPY-raise breaks the backtest smoke** because
  the cache may have empty SPY rows for the canonical window.
  Mitigation: confirm `baseline-2025-09` has SPY price coverage before
  flipping; if not, T-F08 (data-domain pull) and / or a cache backfill
  must land first.
- **Risk: the executor BUY-without-stance raise is too strict** for
  the seed-state shape used by the existing tests. Mitigation: every
  affected test gets a fixture update in the same batch; if a happy-
  path test was previously relying on the silent-swallow it gets
  rewritten to supply the matching stance.
- **Rollback:** feature branch can be discarded at any commit
  boundary; the batched-commit shape means a single batch can be
  reverted without losing the primitive itself.

## Subagent dispatch prompt sketch

> Implement T-F01 (surfacing primitive + silent-failure inverts) per
> `docs/Phase11-project-audit/fix-plan/T-F01-surfacing-primitive-and-inverts.md`. Context:
> `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md`,
> `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md`,
> `docs/Phase11-project-audit/source-audit/agents-misc.md`,
> `docs/Phase11-project-audit/source-audit/observability.md`,
> `docs/Phase11-project-audit/source-audit/data-providers.md`,
> `docs/Phase11-project-audit/source-audit/agents-executor.md`; matching `docs/Phase11-project-audit/test-audit/`
> files; `docs/test-policy.md` §A.7; user memory
> `feedback_silent_failures_loud_tests`. Run the full pytest suite
> after every batch. British English throughout. Commit per batch
> (A → B → C → D) with the primitive + pilot landing first.

## Single-PR vs split recommendation

**Recommendation: split into T-F01a (primitive + pilot) and T-F01b
(remaining batches).**

Rationale, weighed honestly against the user-instruction collaboration
clause (assume Claude is wrong by default):

- **Argument for split (T-F01a / T-F01b):**
  - Reviewability — Batch C (analyst fetch agents) alone touches four
    agents and ~12 test files. A single PR carrying the primitive plus
    all five batches would be ~25-30 files; reviewer attention
    degrades sharply past ~15.
  - Bisectability — if a regression surfaces post-merge, a split makes
    it cheap to identify which batch introduced it. A monolith forces
    a `git bisect` across the whole patch.
  - Wave-4 parallelism — T-F02, T-F05, T-F09, T-F12 can run against
    T-F01a (the primitive) the moment it lands; they do not need to
    wait for the inverts. Splitting unblocks the rest of Wave 4 ~1 day
    earlier.

- **Argument against split (single-PR option):**
  - Atomic CI — the project's PR-pairing rule (`README.md` Decision 3)
    favours atomic source-fix + paired test-rewrite. A primitive
    without inverts technically passes pytest (the primitive's own
    tests are green, the application sites are unchanged), so a
    split *does not violate* the rule — but the spirit of the rule is
    "no PR leaves the suite in a degraded state". A primitive sitting
    in `src/` with zero callers for a few hours is a soft form of
    degradation (new dead code).
  - Merge friction — two PRs need two reviews, two rebase passes if
    `main` shifts. Wave-4 is parallel so the rebase exposure is real.

- **Why the split still wins:** the spirit-of-the-rule cost is small
  (the primitive is hour-of-life dead, not weeks), and the
  reviewability + bisectability + Wave-4 unblock gains compound. The
  collaboration clause says "assume Claude is wrong by default"; the
  user is free to push back on this and I would expect that pushback
  if the reviewability argument lands weaker than I think it does.
  The split is the conservative call.

If the user picks the single-PR option, the spec stays exactly as
written — just collapse the two branch names into one. The
implementation steps and acceptance criteria are unchanged.
