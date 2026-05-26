# Module audit — `src/broker/`

Audit date: 2026-05-26. Source under audit: `src/broker/{__init__,protocol,portfolio,fake,trading212}.py`. Tests: `tests/unit/test_fake_broker.py`, `tests/unit/test_trading212_request_construction.py`, `tests/integration/test_executor_with_fake_broker.py`.

The broker module is small (~4 files, ~250 LoC); the headline issues are (a) production code reaching into `FakeBroker._prices` and (b) Trading212Broker `get_portfolio` silently dropping unknown instruments. Both are P0.

---

## F-broker-001
- **Category:** silent-failure / dedupe-candidate (cross-broker divergence)
- **Severity:** P0
- **Location:** `src/agents/risk_gate/agent.py:101-104` (consumer) vs `src/broker/fake.py:21` and `src/broker/trading212.py` (no equivalent).
- **Evidence:**
  ```
  if hasattr(self.broker, "_prices"):
      for t, p in self.broker._prices.items():
          if t not in prices:
              prices[t] = p
  ```
  `_prices` is a private FakeBroker test injection point (`src/broker/fake.py:21`). Trading212Broker has no such attribute, so in live mode the `hasattr` branch is silently skipped, meaning the risk-gate has no price fallback for watchlist tickers not currently held. In backtest it has one. The two brokers are NOT implementation-agnostic to the executor / risk_gate as the contract claims (`docs/contract-invariants.md §D3`, `intent.md §2.10` — "Brokers respect the same interface so executor and risk gate are implementation-agnostic").
- **Intent violated:** §D3 ("broker is wiring, not contract"), §2.10 invariant ("implementation-agnostic"), §A.7 silent-failure rule (test policy).
- **Suggested action:** investigate — either lift the price-source out of the broker (risk_gate gets prices from `reference_prices`, not the broker) or add a public `get_prices()` method to the Broker protocol. The `hasattr(broker, "_prices")` smell is a leaking test injection point used by production.
- **Notes:** This is exactly the FakeBroker-vs-Trading212 behavioural divergence the audit was asked to flag. The user-flagged "silent failures" recurring-bug class.

## F-broker-002
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/broker/trading212.py:104-113`.
- **Evidence:**
  ```
  for it in items:
      code = it["ticker"]
      if code not in rev:
          continue
      positions[rev[code]] = Position(...)
  ```
  `get_portfolio` silently drops any Trading 212 position whose instrument code is not in the operator-supplied `instrument_map`. The pipeline would then see a smaller portfolio than the broker actually holds, the risk-gate would mis-clamp concentration, the executor would mis-bridge BUY→SELL, and nothing logs or raises. A manual trade in T212 on a ticker not in `instrument_map` becomes invisible.
- **Intent violated:** §A `portfolio` row ("Broker is source of truth"); §A.7 silent-failure rule.
- **Suggested action:** log a warning per dropped instrument (at minimum) or raise. Pre-deployment we have no monitoring, so silent drop is doubly dangerous.

## F-broker-003
- **Category:** silent-failure / bug
- **Severity:** P0
- **Location:** `src/broker/trading212.py:58`, `:77`, `:92`, `:100`.
- **Evidence:**
  ```
  data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
  ```
  This pattern appears four times. `httpx.Response.json()` is a synchronous method — it returns a `dict`, not a coroutine. Awaiting a `dict` raises `TypeError: object dict can't be used in 'await' expression`. The `callable(...)` check is always true (`json` IS a method on `Response`), so the await branch is the one taken at runtime against the real client. The only reason this hasn't blown up is that the broker is never actually used against the real T212 API yet (pre-deployment). The unit tests pass because they `AsyncMock` `.json` so it returns a coroutine.
- **Intent violated:** n/a (correctness bug).
- **Suggested action:** delete the `await`-guarded branch, call `resp.json()` directly. The `callable(...)` ternary is dead defensive code that masks a real bug behind a mocked-only test path.
- **Notes:** Combined with F-broker-002, this means Trading212Broker has never been exercised end-to-end against a real T212 endpoint. Anything that would only fail on real HTTP is unverified.

## F-broker-004
- **Category:** policy-mismatch
- **Severity:** P1
- **Location:** `src/agents/executor/agent.py:205` (`await self.broker.get_portfolio()`), `src/agents/risk_gate/agent.py:95`, `src/agents/snapshot/agent.py:38`.
- **Evidence:** `intent.md §2.10` "Executor does not call the broker mid-tick except at execute time"; `contract-invariants.md §A` "`state['portfolio']` is a working copy refreshed from the broker at the start of every tick". Yet `risk_gate/agent.py:95` calls `await self.broker.get_portfolio()` mid-tick (instead of reading `state["portfolio"]`), `snapshot/agent.py:38` does the same, and `executor/agent.py:205` re-queries the broker after each SELL fill.
- **Intent violated:** §2.10 broker invariant; Rule 7 (pipeline reads from state, not broker).
- **Suggested action:** investigate — the executor's post-SELL re-query is documented as intentional ("the broker already performed the subtraction atomically"), and may be a justified exception. Risk-gate and snapshotter look like genuine policy drift: both could read `state["portfolio"]`.
- **Notes:** Cross-cuts broker module but the broker side is fine; flagging here per the audit prompt's policy-mismatch question.

## F-broker-005
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/broker/protocol.py:32`, `src/broker/fake.py:88-90`, `src/broker/trading212.py:70-83`.
- **Evidence:**
  ```
  $ grep -rn "\.position_size\b" src/ scripts/ tests/ --include='*.py'
  src/broker/protocol.py:32:    async def position_size(self, ticker: str) -> float: ...
  src/broker/trading212.py:70:    async def position_size(self, ticker: str) -> float:
  src/broker/fake.py:88:    async def position_size(self, ticker: str) -> float:
  ```
  Zero call sites outside the three definitions. `position_size` is on the `Broker` protocol but no consumer (executor, risk_gate, snapshotter, orchestrator) uses it — they read `state["portfolio"].positions[ticker].quantity` from the cached portfolio working copy instead.
- **Intent violated:** n/a.
- **Suggested action:** delete from `Broker` protocol, `FakeBroker`, and `Trading212Broker`. Removes one HTTP endpoint coupling from the live broker.

## F-broker-006
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/broker/fake.py:24-28` (`set_price`).
- **Evidence:**
  ```
  $ grep -rn "broker\.set_price\|\.set_price\b" src/ scripts/ tests/ --include='*.py'
  src/backtest/driver.py:702
  scripts/replay_backtest.py:72
  tests/executor/test_executor_bookkeeping.py:269
  ```
  `set_price` is FakeBroker-only (not on the `Broker` protocol) and is called from `src/backtest/driver.py:702` — i.e. production backtest code. This is structurally fine (the driver knows it's wiring a FakeBroker) but combined with F-broker-001's `_prices` leak it means the backtest pipeline mutates broker state through two un-protocoled channels.
- **Intent violated:** §D3 (broker-implementation-as-wiring).
- **Suggested action:** investigate — either promote `set_price` to a `BacktestBroker` sub-protocol or accept that backtest driver legitimately knows about FakeBroker. Either way, document so the audit isn't repeated.

## F-broker-007
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/broker/portfolio.py:14-17` (`Position.market_value`), `:27-29` (`Portfolio.total_value`), `:31-36` (`Portfolio.current_weights`).
- **Evidence:** All three are one-line wrappers over dict/attribute access. `total_value` is used in `enricher.py`, `context_shim.py:415`, `snapshot/agent.py:39`; `current_weights` in `risk_gate/agent.py:96`, `orders.py:20`, `enricher.py:175`, `snapshot/agent.py:117`; `market_value` in `context_shim.py:462`. So they ARE used — the over-abstraction concern is mild. Keep.
- **Intent violated:** n/a.
- **Suggested action:** no action — flagging only because the audit prompt called this out; on inspection the methods earn their keep.

## F-broker-008
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/test_trading212_request_construction.py:11-49`.
- **Evidence:** Tests mock `client.post.return_value.json = AsyncMock(...)`, which makes `resp.json()` return a coroutine (matching the buggy `await resp.json()` path in F-broker-003). A test that only passes because it conforms to a bug it doesn't surface is anti-pattern §E "trusting per-Runner settings overrides" cousin — it cements the bug. The tests pass even though the live code path would `TypeError` against a real `httpx.Response`.
- **Intent violated:** test-policy §A.7 ("tests must surface silent failures loudly"), §E ("It didn't raise, therefore it works").
- **Suggested action:** rewrite test to use `client.post.return_value.json = MagicMock(return_value={...})` (sync mock matching real httpx behaviour). Will fail until F-broker-003 is fixed — which is the point.

## F-broker-009
- **Category:** test-gap
- **Severity:** P2
- **Location:** `tests/unit/test_trading212_request_construction.py`.
- **Evidence:** No test exercises `Trading212Broker.get_portfolio` (the silent-drop in F-broker-002 has no failing test). No test for a non-200 response code (does it raise `BrokerRejection` or `httpx.HTTPStatusError`?). No test for `_instrument` raising when ticker not in map. No test for `position_size` (would let us delete it without fear per F-broker-005).
- **Intent violated:** §A.7.
- **Suggested action:** add coverage for HTTP error path (asserts `BrokerRejection` raised, not silently a default Fill), `get_portfolio` unknown-instrument behaviour, and `_instrument` rejection.

## F-broker-010
- **Category:** policy-mismatch (doc-only)
- **Severity:** P3
- **Location:** `src/broker/protocol.py:25-26` (docstring mentions "any future adapters").
- **Evidence:** Pre-deployment, only one live broker exists; no other adapter is on the roadmap. The "future adapters" phrasing is YAGNI.
- **Intent violated:** n/a.
- **Suggested action:** trim docstring; not load-bearing.

## F-broker-011
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/broker/trading212.py:11-12`.
- **Evidence:**
  ```
  PAPER_BASE = "https://demo.trading212.com"
  LIVE_BASE  = "https://live.trading212.com"
  ```
  Pre-deployment — no paper instance is running, no live instance is running. The mode/URL plumbing exists but is exercised only by `test_paper_uses_demo_base_url` / `test_live_uses_live_base_url`. Not dead per se (`scripts/initialise.py:37` and `src/orchestrator/tick.py:296,302` construct the broker), but un-exercised end-to-end.
- **Intent violated:** n/a.
- **Suggested action:** no removal — keep as scaffolding. Note in audit summary that broker live wiring has never been smoke-tested.

---

## Cross-cutting summary

- **P0 silent failures (3):** F-broker-001 (FakeBroker._prices leak into production risk-gate), F-broker-002 (T212 unknown-instrument silent drop), F-broker-003 (`await resp.json()` is a never-tripped runtime bug masked by mock shape).
- **P1 dead code / policy drift (3):** F-broker-004 (mid-tick `get_portfolio` calls), F-broker-005 (`position_size` zero refs), F-broker-006 (`set_price` un-protocoled).
- **P2 (3):** F-broker-007 (mild over-abstraction, keep), F-broker-008 (test cements bug), F-broker-009 (T212 test gaps).
- **P3 (2):** F-broker-010, F-broker-011.

**Top three for human attention:**
1. **F-broker-003** — `await resp.json()` against the real httpx client will `TypeError` on first live call. Pre-deployment masks it; deployment will fail loudly. Easy fix, but the dead-defensive `callable(...)` ternary needs to come out at the same time.
2. **F-broker-001** — production code (`risk_gate/agent.py:101`) reaches into `FakeBroker._prices`. In live mode the fallback silently disappears, in backtest it's present. This is exactly the divergent-behaviour bug the audit was scoped to find.
3. **F-broker-002** — Trading212Broker `get_portfolio` silently drops positions whose instrument code is not in `instrument_map`. Combined with no test coverage (F-broker-009), the first live deploy could under-report holdings without any log line.
