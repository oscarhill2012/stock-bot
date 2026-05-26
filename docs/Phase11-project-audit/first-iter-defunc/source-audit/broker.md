# Source audit — `src/broker/`

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 5 (`__init__.py`, `protocol.py`, `portfolio.py`, `fake.py`, `trading212.py`)
**Findings:** 0 P0 · 2 P1 · 2 P2 · 0 P3

## Summary

The broker subsystem is small and well-shaped: `protocol.Broker` is the single
abstraction, both `FakeBroker` and `Trading212Broker` implement it
structurally, and the Rule-7 / §D-3 carve-out in `contract-invariants.md`
explicitly justifies the dual implementation, so the `Protocol` is *not*
overabstraction. `Portfolio` / `Position` are lightweight Pydantic value
objects with widely-used helpers and are not in scope for tidying. The
worrying finds are concentrated in `trading212.py`: a near-certain latent
runtime bug in the JSON-decoding path (`await resp.json()` against
synchronous `httpx.Response.json()`), and `position_size` declared on the
protocol but called nowhere in `src/`, `tests/`, or `scripts/`. Both are
non-blocking today because the bot is pre-deployment, but they will fire
the moment a live tick lands.

Cross-subsystem notes for the consolidator:
- Every `Trading212Broker(...)` call site in `src/orchestrator/tick.py`,
  `scripts/initialise.py`, and `scripts/trace_tick.py` passes
  `instrument_map={}`. That makes `_instrument(ticker)` raise
  `BrokerRejection` for any real ticker — a wiring bug in the *callers*,
  not in this subsystem. Flag for orchestrator and scripts audits.
- `Trading212Broker.get_portfolio` silently skips T212 positions whose
  internal code is not in the reverse instrument map (`continue` at
  `trading212.py:108`). Combined with empty `instrument_map={}`, the live
  portfolio surface would be `cash + {}` with no error. Recorded below as
  a silent-failure attractor scoped to this subsystem; the caller-side
  fix lives elsewhere.

## Findings

### P1-01 · C5 silent-failure attractor · `Trading212Broker` JSON decoding awaits a synchronous result

- **Location:** `src/broker/trading212.py:58`, `:77`, `:92`, `:100`
- **Confidence:** high
- **Description:**
  Four call sites use the pattern
  `data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()`.
  `httpx.Response.json` is a **synchronous** method that returns a dict, and
  `callable(...)` is True for both sync and async callables, so in production
  the `await` branch is always taken. `await <dict>` raises `TypeError:
  object dict can't be used in 'await' expression`. The unit tests
  (`tests/unit/test_trading212_request_construction.py`) pass only because
  they use `AsyncMock`, which makes `.json` itself an `AsyncMock` so the
  `await` happens to succeed. As soon as a real `httpx.AsyncClient` request
  lands, every `submit_market` / `position_size` / `get_portfolio` call
  raises before returning. This is C5 because the test-vs-prod surface
  divergence is exactly the "looks fine in tests, fails at runtime" shape
  the silent-failure attractor rule targets — and it's a contract surface
  (`Fill` / `Portfolio`) that downstream agents rely on. Not P0 only
  because the bot is pre-deployment; the moment a live tick runs this
  fires.
- **Suggested action:**
  Drop the conditional and call `resp.json()` directly (sync). Update the
  test mocks to set `.json` as a `MagicMock` returning a dict (no `await`),
  or restructure the helper around a private `_decode_json(resp)` method
  that production overrides at one well-known point.

### P1-02 · C5 silent-failure attractor · `Trading212Broker.get_portfolio` drops unknown-instrument positions silently

- **Location:** `src/broker/trading212.py:104-113`
- **Confidence:** high
- **Description:**
  The loop building the live portfolio does `if code not in rev: continue`
  — any T212 position whose internal instrument code is not in the
  reverse map (`rev = {v: k for k, v in self._instruments.items()}`) is
  silently dropped from the returned `Portfolio`. Combined with the
  caller-side practice of constructing `Trading212Broker(...,
  instrument_map={})` (see cross-subsystem note above), the bot would
  receive a portfolio with the correct cash but **zero** positions, and
  the pipeline downstream of `state["portfolio"]` (RiskGate's weights,
  Strategist's held-view, Snapshotter's holdings_breakdown) would treat
  the bot as flat. No warning, no raise, no `is_no_data` signal —
  classic silent-failure attractor. Out of scope of fix for this
  subsystem is the caller-side instrument-map seeding; in-scope is the
  silent-skip itself.
- **Suggested action:**
  At minimum, log a `WARNING` per skipped instrument code and surface a
  total-skipped count on the call. Preferably, raise on unknown codes so
  the caller is forced to address an under-populated `instrument_map`
  before live trading starts. Pair with whatever fix lands on the
  caller side.

### P2-01 · C1 dead code · `Broker.position_size` has no callers

- **Location:** `src/broker/protocol.py:32`, `src/broker/fake.py:88-90`, `src/broker/trading212.py:70-83`
- **Confidence:** high
- **Description:**
  `position_size(ticker) -> float` is declared on the `Broker` protocol
  and implemented in both `FakeBroker` and `Trading212Broker`, but a
  cross-tree grep
  (`grep -rn "position_size" src/ tests/ scripts/`) returns only the
  declaration and the two implementations. Zero call sites. The Executor
  reads remaining quantity via `broker.get_portfolio()` (see
  `agents/executor/agent.py:193`), not via this method. The protocol
  surface area is unused. Per §C-Rule 7, the broker is a contract-bearing
  seam, but the *protocol method itself* is dead — Rule 7 justifies the
  interface, not every method on it.
- **Suggested action:**
  Drop `position_size` from `protocol.Broker` and from both implementations.
  If a future caller needs it, derive from `get_portfolio()`.

### P2-02 · C7 doc/code drift · `position_size` docstrings claim a use the code does not have

- **Location:** `src/broker/fake.py:89` ("Return shares held for `ticker`…"), `src/broker/trading212.py:71` ("Return shares currently held for `ticker`…")
- **Confidence:** high
- **Description:**
  Cosmetic follow-on to P2-01 — both implementations carry full docstrings
  for a method nothing calls. If P2-01 lands as written this drift
  disappears; flagged separately for the case where P2-01 is downgraded
  and we keep the method but should at minimum note its disuse.
- **Suggested action:**
  Resolve together with P2-01. If the method survives, add a one-line
  comment naming the intended future caller; otherwise delete with the
  method.

## Notes (sub-finding-grade observations, not separate findings)

- `protocol.Broker` is single-implementation in spirit (`FakeBroker` and
  `Trading212Broker`) and that is **load-bearing by contract** per
  `contract-invariants.md` §D-3 and §C-Rule 7. Not flagged as C3
  overabstraction; the rubric §2/C3 "Rule 7 architectural seams"
  exception applies.
- `portfolio.Portfolio` / `Position` are widely consumed across
  `src/agents/strategist/`, `src/agents/risk_gate/`, `src/agents/snapshot/`,
  `src/orchestrator/`, and `src/backtest/`. No dead-code concerns here.
- `FakeBroker.set_price` has two callers — `src/backtest/driver.py:654`
  and `scripts/replay_backtest.py:72` — both legitimate. Not dead.
- `BrokerRejection` is raised by both implementations and caught in
  `src/agents/executor/agent.py:264`. Not dead.
- `Fill` is consumed structurally (`.price`, `.quantity`) by the Executor;
  the type itself is not imported there, which is conventional in this
  codebase — not a finding.
- `FakeBroker` conforms to the protocol's signatures exactly (same async
  method names, same parameter types, same return types). Verified by
  read-through, not by static `isinstance(broker, Broker)` runtime check
  (Python `Protocol`s are structural, so no runtime enforcement exists or
  is needed).
