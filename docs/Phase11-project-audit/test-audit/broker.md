# Test audit — `src/broker/`

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/broker.md` (P1-01, P1-02, P2-01, P2-02)
**Test files in scope:** 4 primary (+ 1 peripheral noted below)
**Tests collected from those files:** 16 (via `pytest <paths> --collect-only -q`)
**Findings:** 1 P0 · 1 P1 · 2 P2 · 1 P3

## Files in scope

Primary — exercise the broker subsystem directly:

- `tests/unit/test_fake_broker.py` — 4 tests, `FakeBroker` order semantics.
- `tests/unit/test_trading212_request_construction.py` — 4 tests, `Trading212Broker` request shaping.
- `tests/unit/test_portfolio.py` — 3 tests, `Portfolio` / `Position` value-object maths.
- `tests/integration/test_executor_with_fake_broker.py` — 5 tests, executor-against-broker integration (uses `FakeBroker` as the broker under test scaffolding).

Peripheral — use `FakeBroker` as harness for tests of other subsystems; **out of scope** for this audit but flagged so the consolidator can avoid double-counting:

- `tests/executor/test_executor_bookkeeping.py` (uses `FakeBroker` to set up executor scenarios).
- Backtest driver/runner tests under `tests/unit/backtest/` and `tests/integration/backtest/` (use `FakeBroker` via `BacktestSettings`).
- Risk-gate, snapshotter, strategist tests that reference `Portfolio` / `Position` for state assembly.

Layout note: the four primary files are all at `tests/unit/test_*.py` (and one `tests/integration/test_*.py`) — flat root-of-`tests/unit` rather than mirrored under `tests/unit/broker/`. Minor §B drift; see P2-02.

## Summary

The broker test suite is small but does what it sets out to do — `FakeBroker` semantics and `Portfolio` arithmetic are well covered with positive-content assertions. The dominant problem is concentrated in `tests/unit/test_trading212_request_construction.py`: every test uses `unittest.mock.AsyncMock` for the HTTP response, which makes `.json` itself an async callable. This is the *exact* mock shape needed to keep the broken `await resp.json() if callable(...) else resp.json()` pattern (source P1-01) green — the tests are not just failing to catch the production bug, they are the reason it shipped looking healthy. Secondary gap: nothing exercises `Trading212Broker.get_portfolio` at all, so the silent-skip-on-unknown-instrument-code behaviour (source P1-02) is entirely untested.

## Findings

### P0-01 · T5 mock-at-wrong-level · `AsyncMock` HTTP responses mask `await resp.json()` runtime bug

- **Location:** `tests/unit/test_trading212_request_construction.py:11-17` and `:37-41`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/broker.md` P1-01
- **Confidence:** high
- **Description:**
  Both happy-path tests build the mocked HTTP client with
  `client = AsyncMock()` (line 11, 37) and then set
  `client.post.return_value.json = AsyncMock(return_value={...})` (line 12, 38).
  Because `AsyncMock()` makes every attribute access return another
  `AsyncMock` by default, and because `.json` is explicitly assigned an
  `AsyncMock`, the production line
  `data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()`
  (src/broker/trading212.py:58, :77, :92, :100) silently succeeds — `await
  <AsyncMock_return_value>` resolves to the dict. In production
  `httpx.Response.json` is **synchronous** and returns a plain dict; the
  `callable(...)` guard is True for both sync and async callables, so the
  `await` branch is *always* taken in real use and immediately raises
  `TypeError: object dict can't be used in 'await' expression`. The
  unit-test mock shape and the production response shape diverge at exactly
  the seam the test is meant to cover. A real-shape mock would catch the
  bug: substitute
  ```python
  resp = Mock(
      json=Mock(return_value={"id": "abc-123", "instrumentCode": ...,
                              "filledQuantity": 1.5, "filledPrice": 200.0}),
      raise_for_status=Mock(return_value=None),
  )
  client = AsyncMock()
  client.post.return_value = resp        # post is async, the *response* is not
  ```
  With that wiring, `await resp.json()` fails immediately — which is the
  signal the test exists to give. This is the canonical T5 the rubric calls
  out: mocked above the leaf so the contract drift is invisible.
- **Suggested action:**
  After source P1-01 lands (drop the conditional, call `resp.json()`
  directly), reshape both tests to use `Mock(json=Mock(return_value=...))`
  for the response while keeping `client.post` as an `AsyncMock`. This
  matches `httpx.Response`'s real shape and would have failed loudly on the
  original `await resp.json()` line. Add an explicit assertion that
  `resp.json` was called *without* being awaited (e.g. via
  `Mock.assert_called_once_with()` on the sync mock).

### P1-01 · T4 missing surfacing test · `Trading212Broker.get_portfolio` has zero tests

- **Location:** new test needed — `tests/unit/test_trading212_request_construction.py` (or a sibling `tests/unit/test_trading212_portfolio.py`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/broker.md` P1-02
- **Confidence:** high
- **Description:**
  The four existing tests cover `submit_market` request construction and
  base-URL selection only. There is no test that calls
  `Trading212Broker.get_portfolio()`, so the silent-skip at
  `src/broker/trading212.py:107-108` (`if code not in rev: continue`) is
  entirely unexercised. Source P1-02 records this as a silent-failure
  attractor: combined with the caller-side `instrument_map={}` wiring in
  `src/orchestrator/tick.py` and friends, the live bot would return a
  portfolio with the correct cash and *zero* positions on the first live
  tick — no warning, no raise. The pipeline downstream (RiskGate,
  Strategist held-view, Snapshotter) would proceed treating the bot as
  flat. The test gap is what lets this ship as P1 rather than being caught
  upstream. The fix in P1-02 changes the behaviour (warn at minimum, raise
  preferably); the surfacing test must lock that new contract in.
- **Suggested action:**
  Add a test `test_get_portfolio_warns_on_unknown_instrument_code` that
  constructs `Trading212Broker(instrument_map={"AAPL": "AAPL_US_EQ"})`,
  mocks the `/portfolio` endpoint to return positions for both
  `AAPL_US_EQ` and `UNKNOWN_XX_EQ`, and asserts either (a) `caplog`
  records a WARNING per the source-fix decision, or (b) a `BrokerRejection`
  / specific exception is raised — depending on which side P1-02 lands.
  Add a complementary happy-path test asserting that known codes survive
  the reverse-map lookup and appear in the returned `Portfolio.positions`.

### P2-01 · T8 layout — primary broker tests live flat under `tests/unit/`, not mirrored under `tests/unit/broker/`

- **Location:** `tests/unit/test_fake_broker.py`, `tests/unit/test_trading212_request_construction.py`, `tests/unit/test_portfolio.py`
- **Source-audit cross-ref:** none (layout-only)
- **Confidence:** medium
- **Description:**
  Per test-policy §B "Unit … live under `tests/unit/` mirroring the source
  tree (e.g. `src/agents/news/fetch.py` → `tests/unit/agents/news/test_fetch.py`)",
  these should live at `tests/unit/broker/test_fake_broker.py`,
  `tests/unit/broker/test_trading212_request_construction.py`, and
  `tests/unit/broker/test_portfolio.py`. The current flat placement isn't
  actively harmful — discovery still works — but it makes it harder to
  grep for "all broker tests" and inconsistent with the way agent and
  backtest subtrees are organised. The integration file
  (`tests/integration/test_executor_with_fake_broker.py`) is correctly
  placed.
- **Suggested action:**
  Move the three unit files under `tests/unit/broker/` in the same PR
  that lands the P0-01 reshape; preserve git history with `git mv`.

### P2-02 · T3 weak assertion · `test_paper_uses_demo_base_url` and `test_live_uses_live_base_url` substring-match only

- **Location:** `tests/unit/test_trading212_request_construction.py:53-64`
- **Source-audit cross-ref:** none
- **Confidence:** medium
- **Description:**
  Both base-URL tests assert only `"demo" in b.base_url` and `"demo" not
  in b.base_url and "trading212" in b.base_url`. They do not pin the
  actual value against the module-level constants `PAPER_BASE` and
  `LIVE_BASE`. A typo that turned the URL into
  `"https://demo.trading-212.com"` (or any other "demo"-containing
  string) would still pass both tests. Low blast radius because the
  constants are only set in one place and rarely touched, but per
  §A.7 "assert on positive output state, not just on completion" the
  test should compare against the constants directly.
- **Suggested action:**
  Replace the substring checks with
  `assert b.base_url == "https://demo.trading212.com"` and
  `assert b.base_url == "https://live.trading212.com"`, or import the
  module constants and equate against them.

### P3-01 · T8 cosmetic · `_make_ctx` `MagicMock`-as-session is opaque

- **Location:** `tests/integration/test_executor_with_fake_broker.py:16-29`
- **Source-audit cross-ref:** none
- **Confidence:** low
- **Description:**
  `_make_ctx` returns a `MagicMock` standing in for `InvocationContext`;
  every attribute the executor reads from the context proxies through
  `MagicMock`'s default attribute behaviour, except `state`,
  `invocation_id`, and `session`. This is fine today but it means future
  executor reads of context attributes will silently get `MagicMock`
  objects rather than the validated values the real ADK framework would
  pass. Already touched by the docstring at line 22-23 ("the executor
  now yields an Event whose `invocation_id` field is a Pydantic-validated
  string, so the mock must return a real string"), so the pattern has
  bitten once already. Not a bug today, but it is a future silent-failure
  attractor.
- **Suggested action:**
  Replace `_make_ctx` with a small dataclass or `SimpleNamespace`-based
  builder that lists the exact attributes the executor reads, so adding a
  new attribute access to the executor produces an `AttributeError`
  rather than a `MagicMock` shadow.

## Notes (sub-finding-grade observations)

- **No T1 dead tests.** `Broker.position_size` is dead in source
  (source-audit P2-01), but **no test** exercises it — `grep -rn
  "position_size" tests/` returns zero hits. When the method is dropped
  from `protocol.Broker` / `FakeBroker` / `Trading212Broker`, no test
  needs to move with it.
- **No T2 parallel-branch defenders** in this subsystem — there is only
  one current implementation surface, no old/new branch coexistence.
- **No T7 hard-rule violations** — no real-API usage, no live-cache
  writes, no LLM calls, all async tests properly marked, no
  `datetime.now()` references in the four primary files.
- **`FakeBroker` test coverage is solid.** The four tests in
  `test_fake_broker.py` each pair a positive-content assertion with the
  call (cash after buy, position quantity after sell, `BrokerRejection`
  raised on insufficient-cash / sell-more-than-held). Nothing to flag.
- **`Portfolio` value-object tests are solid** — all three pin the
  arithmetic with concrete numbers (`pytest.approx`) rather than just
  asserting non-None. Nothing to flag.
- **Cross-subsystem ripple:** P0-01 is the test-side mirror of source
  P1-01. The consolidator should ensure both audits' PRs land together —
  flipping the source to sync `.json()` without reshaping the tests
  would turn the suite red; reshaping the tests without flipping the
  source would turn the suite red. They have to move in lock-step.
- **Source P2-01 (dead `position_size`) and P2-02 (orphaned docstrings)**
  carry no test-side T1 because nothing tests them. They will sweep
  cleanly in the source PR.
