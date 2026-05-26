# T-F02 — Missing surfacing tests

**Wave:** 4 (parallel)
**Pairs source-audit fix:** F10 (`notable_holders` `filed_at` mapping bug); F3 sibling (`run_once` narrow-the-except); F8 sibling (`Trading212Broker.get_portfolio` raise/warn)
**Branch:** `fix/T-F02-missing-surfacing-tests`
**Depends on:** T-F01a (the surfacing primitive — every test in this spec asserts on it). T-F04 owns the `Trading212Broker.get_portfolio` *source* change; this spec adds the *test* that pins it.
**Estimated diff size:** small

## Scope

Add the four "missing surfacing test" gaps that the audit found but
that do **not** fit cleanly into T-F01's inversion sweep — either
because they are entirely new tests (no existing test to invert), or
because the corresponding source fix lives in a different wave (T-F04
owns the broker raise; T-F01 owns the silent-failure inverts). Every
test in this spec asserts positively on the T-F01a surfacing primitive
(`branch_failed=True` log record, `caplog.records`, or
`pytest.raises`) rather than on absence of an error.

### In scope

- **New test for `notable_holders` filed_at leak detection.** Extend
  `tests/backtest/audit/test_tripwires.py` with
  `test_notable_holders_filter_key_after_as_of_fires`: seed one
  `NotableHolder` row with `filed_at = as_of + 1 day`, drive the audit
  pipeline, assert `tripwires.any_filter_key_after_as_of is True` and
  `summary.by_domain["notable_holders"]["max_filed_at"] > as_of`. The
  test must fail against `HEAD` (because the source code maps the
  non-existent `as_of_date` field, the leak detector never fires) and
  pass once the source-audit `backtest.md` P0-01 fix lands in T-F02's
  paired source change (renaming `as_of_date` to `filed_at` at
  `src/backtest/audit/telemetry.py:187` and
  `src/backtest/audit/upstream_verifier.py:101-102`).
- **New test file for `run_once` exception narrowing.** Create
  `tests/unit/orchestrator/test_tick_run_once_exception_handling.py`
  with three scenarios (per test-audit `orchestrator.md` P0-01):
  1. `test_run_once_propagates_non_adk_exceptions` — monkeypatch
     `Runner.run_async` to be an async generator that raises
     `RuntimeError("synthetic")`; assert `run_once` re-raises (current
     HEAD swallows; this is the regression guard for the source-side
     narrow).
  2. `test_run_once_swallows_known_adk_teardown_bug` — raise
     `AttributeError("'NoneType' object has no attribute 'partial'")`
     from the generator, use `caplog` to assert the warning fires, and
     assert the function returns a dict containing `last_snapshot` (the
     Rule-8 success-handshake key the backtest driver already gates
     on at `src/backtest/driver.py:393-401`).
  3. `test_run_once_asserts_pipeline_reached_snapshotter` — raise the
     teardown bug *but* return a session whose state lacks
     `last_snapshot`; assert the function still re-raises rather than
     masking a genuine mid-tick pipeline failure as the harmless ADK
     teardown.
- **New test for `Trading212Broker.get_portfolio`** at
  `tests/unit/broker/test_trading212_portfolio.py` (post-layout-sweep
  location; pre-sweep this lives at
  `tests/unit/test_trading212_portfolio.py`). Two tests per test-audit
  `broker.md` P1-01: (a)
  `test_get_portfolio_warns_on_unknown_instrument_code` builds
  `Trading212Broker(instrument_map={"AAPL": "AAPL_US_EQ"})`, mocks the
  `/portfolio` endpoint to return positions for both `AAPL_US_EQ` and
  `UNKNOWN_XX_EQ`, and asserts `caplog` carries a `WARNING` (or
  `pytest.raises` if T-F04 picks the raise option) for the unknown
  code; (b)
  `test_get_portfolio_includes_known_instrument` happy-path counterpart.
- **End-to-end `branch_failed` caplog guard.** Extend
  `tests/integration/test_state_delta_user_prefix_end_to_end.py` per
  test-audit `orchestrator.md` P0-04:
  1. Add `caplog.set_level(logging.WARNING)` at the top of the
     pipeline-driving test.
  2. Assert `not any("branch_failed" in r.message or
     r.__dict__.get("branch_failed") for r in caplog.records)` after
     the runner completes.
  3. Add a positive log assertion that the executor's
     `_executor_thesis_writer_callback` actually fired (lookup the
     exact INFO message in `src/agents/executor/agent.py` at
     implementation time).
  4. Add an event-count guard: collect events from `runner.run_async`
     and assert at least one carries a `state_delta` with a
     `user:positions` key. This guards against a regression that
     silently disabled the auto-yield path — today the test passes
     only because of the seed-then-reload coincidence noted in the
     test-audit P0-04 description.

### Out of scope

- The silent-failure inverts that T-F01 already owns (analysts,
  observability, snapshotter, executor BUY-without-stance, fill-price
  fallback, EDGAR, Finnhub, memory writer).
- The strategist `tick_id="unknown"` and decision_writer silent-no-op
  tests — owned by T-F05.
- The `_build_initial_state` empty-seed contract violation
  (`orchestrator.md` test P0-03) — Spec C / Phase 2 hydration is
  *deferred this cycle* per `README.md` Decision 6. The test stays as
  it is; this spec does **not** add the cross-tick survival test.
- The `_dispatch_app_name` broker-mode-routing test
  (`orchestrator.md` test P1-04) — owned by T-F04 (live-only bombs).

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `backtest.md` source P0-01 | `src/backtest/audit/telemetry.py:187`, `src/backtest/audit/upstream_verifier.py:101-102` | Fix `as_of_date` → `filed_at` field-mapping |
| `backtest.md` test P0-01 | `tests/backtest/audit/test_tripwires.py` (new test) | `notable_holders` firing-test |
| `orchestrator.md` source P0-02 | `src/orchestrator/tick.py:260-270` | Narrow `except (AttributeError, BaseException)` to `AttributeError` only with `last_snapshot` gate |
| `orchestrator.md` test P0-01 | `tests/unit/orchestrator/test_tick_run_once_exception_handling.py` (new file) | Three regression scenarios |
| `broker.md` test P1-01 | `tests/unit/broker/test_trading212_portfolio.py` (new file) | `get_portfolio` warns/raises on unknown code |
| `orchestrator.md` test P0-04 | `tests/integration/test_state_delta_user_prefix_end_to_end.py` | Add `caplog` `branch_failed` guard + event-count guard |

(The `broker.md` *source* P1-02 raise/warn change itself is owned by
T-F04 — this spec only adds the test that locks the new contract in.)

## Implementation steps

1. **Land the `notable_holders` paired fix first** because it is a
   self-contained one-file source change + one-test new-test:
   - Source: rename `as_of_date` → `filed_at` at
     `src/backtest/audit/telemetry.py:187` and
     `src/backtest/audit/upstream_verifier.py:101-102`.
   - Test: add `test_notable_holders_filter_key_after_as_of_fires` to
     `tests/backtest/audit/test_tripwires.py`, mirroring the
     `test_filter_key_after_as_of_fires` shape already in that file.
   - Verify the test fails against `HEAD` before the source rename
     (record the failing trace in the commit body for the reviewer).
2. **Add the `run_once` exception-handling test file**
   (`tests/unit/orchestrator/test_tick_run_once_exception_handling.py`).
   Write the three scenarios per the description above. Coordinate
   with the T-F02 source-side narrow (the source change is part of
   this spec — the narrow lives at `src/orchestrator/tick.py:260-270`;
   replace the bare `except (AttributeError, BaseException)` with
   `except AttributeError as exc:` gated on the
   `"'NoneType' object has no attribute 'partial'"` message *and* on
   `state.get("last_snapshot")` being present; re-raise otherwise).
3. **Add the `Trading212Broker.get_portfolio` test file.** Choose the
   warn-vs-raise shape to match T-F04's source-side disposition (the
   T-F04 spec records the choice). Mock at the response level
   (`Mock(json=Mock(return_value={...}))`) per test-audit `broker.md`
   P0-01 guidance — not `AsyncMock`, because `httpx.Response.json` is
   synchronous and `AsyncMock` would mask the same shape-drift bug
   T-F04 is fixing.
4. **Extend the end-to-end caplog assertion.** Add the four extensions
   to
   `tests/integration/test_state_delta_user_prefix_end_to_end.py` per
   the description above. Confirm the executor callback's actual log
   level + message string by reading
   `src/agents/executor/agent.py` at implementation time (the
   test-audit P0-04 note labels it `INFO` but does not pin the
   string).
5. **Update `graphify-out/graph_delta.md`** with entries for the new
   test files.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in commit
  body).
- [ ] The `notable_holders` test fails against `HEAD` and passes after
  the source rename — record this in the commit body.
- [ ] The three `run_once` tests fail against `HEAD` and pass after the
  except-narrow — record in commit body.
- [ ] End-to-end test's new `caplog` assertion is the
  *positive-content* shape (`assert no branch_failed` + `assert
  callback fired` + `assert ≥1 user:positions event`), not "didn't
  raise".
- [ ] Graphify delta entry appended.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
```

## Risks and rollbacks

- **Risk: the `run_once` narrow re-raises the ADK 1.32 teardown bug**
  in production runs because the `last_snapshot` gate is too strict.
  Mitigation: scenario 2 of the new test pins the exact swallow
  contract (last_snapshot present → swallow; absent → raise). The
  backtest smoke (`PYTHONPATH=src .venv/bin/python -m
  scripts.backtest_run --window baseline-2025-09 --tick-limit 1`) is
  the empirical check; CI must run it.
- **Risk: the `Trading212Broker.get_portfolio` test pins the wrong
  shape** if T-F04 picks raise but this spec picks warn (or vice
  versa). Mitigation: the two specs cross-reference each other;
  whoever lands second updates the test to match the source-side
  disposition the first PR made.
- **Rollback:** feature branch discardable; the four sub-changes are
  independent and can be split into smaller PRs if the reviewer
  prefers.

## Subagent dispatch prompt sketch

> Implement T-F02 (missing surfacing tests) per
> `docs/Phase11-project-audit/fix-plan/T-F02-missing-surfacing-tests.md`. Context:
> `docs/Phase11-project-audit/source-audit/backtest.md` (P0-01),
> `docs/Phase11-project-audit/source-audit/orchestrator.md` (P0-02),
> `docs/Phase11-project-audit/source-audit/broker.md` (P1-02),
> `docs/Phase11-project-audit/test-audit/backtest.md` (P0-01),
> `docs/Phase11-project-audit/test-audit/orchestrator.md` (P0-01, P0-04),
> `docs/Phase11-project-audit/test-audit/broker.md` (P1-01); `docs/test-policy.md` §A.7 + §G.8;
> `docs/Phase11-project-audit/fix-plan/T-F01-surfacing-primitive-and-inverts.md` for the
> primitive being asserted on. Co-ordinate the broker shape choice
> with T-F04 (live-only bombs). Run the full pytest suite after each
> sub-change. British English throughout.
