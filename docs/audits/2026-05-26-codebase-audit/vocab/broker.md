# Vocabulary — `src/broker/`

## Protocol methods (`Broker`, `src/broker/protocol.py`)
- `submit_market(ticker, action: "BUY"|"SELL", quantity) -> Fill` — async; the only order-submission method (market orders only, no limit/stop).
- `position_size(ticker) -> float` — async; shares held for a ticker. **Zero callers outside the protocol/implementations** (F-broker-005).
- `get_portfolio() -> Portfolio` — async; cash + positions snapshot. The Phase 2 portfolio-refresh source.

## Models (`src/broker/portfolio.py`, `src/broker/protocol.py`)
- `Fill(id: str, ticker, action: "BUY"|"SELL", quantity, price)` — Pydantic, broker-assigned id, execution price/qty (may differ from request).
- `Position(quantity, avg_cost, last_price)` — Pydantic; volume-weighted cost basis; `last_price` mutated by FakeBroker on `set_price`.
- `Position.market_value` (property) — `quantity * last_price`.
- `Portfolio(cash: float, positions: dict[str, Position])` — Pydantic snapshot.
- `Portfolio.total_value` (property) — `cash + sum(market_value)`.
- `Portfolio.current_weights() -> dict[str, float]` — ticker → fraction of total_value; returns `{}` when total is zero.

## Exception types (`src/broker/protocol.py`)
- `BrokerRejection(Exception)` — broker refused order; expected to be logged and recorded as `status="rejected"` Execution, not crash the tick (per `intent.md §2.3`).

## Implementations

### `FakeBroker` (`src/broker/fake.py`)
- Ctor: `(starting_cash: float, prices: dict[str, float])`.
- State: `_cash: float`, `_positions: dict[str, Position]`, `_prices: dict[str, float]`, `_order_seq: itertools.count(1)`.
- `set_price(ticker, price) -> None` — not on protocol; also marks open position to market. Called from `backtest/driver.py:702`, `scripts/replay_backtest.py:72`, `tests/executor/test_executor_bookkeeping.py:269`.
- `submit_market` rejects on: unknown price, insufficient cash (BUY), oversell (SELL). VWAP cost basis on additive BUY; cost basis unchanged on partial SELL; position deleted on zero-qty SELL.
- Fill id format: `"fake-{counter}"`.
- No slippage, no partial fills, no latency.

### `Trading212Broker` (`src/broker/trading212.py`)
- Ctor: `(*, mode: "paper"|"live", api_key: str, http_client: httpx.AsyncClient, instrument_map: dict[str, str])`.
- State: `mode`, `base_url`, `_api_key`, `_client`, `_instruments`.
- Module constants: `PAPER_BASE = "https://demo.trading212.com"`, `LIVE_BASE = "https://live.trading212.com"`.
- Auth header: `Authorization: {api_key}` (no `Bearer` prefix — T212 uses raw key).
- `_headers() -> dict[str, str]` — Authorization + Content-Type.
- `_instrument(ticker) -> str` — instrument code lookup; raises `BrokerRejection` on miss.
- `submit_market`: POST `/api/v0/equity/orders/market` with `{"instrumentCode", "quantity"}` (signed: negative for SELL). Converts `HTTPStatusError` → `BrokerRejection`.
- `position_size`: GET `/api/v0/equity/portfolio`, linear scan for matching code.
- `get_portfolio`: GET `/api/v0/equity/account/cash` (free balance) + `/api/v0/equity/portfolio`. Reverses `instrument_map`; **silently `continue`s over unknown instrument codes (F-broker-002)**.

### FakeBroker vs Trading212Broker — behavioural deltas (F-broker-001)
- Slippage modelling: FakeBroker = none; T212 = whatever the API returns. Backtest fills at exact reference price.
- Rejection codes: FakeBroker raises with descriptive string; T212 wraps `HTTPStatusError` body verbatim.
- Partial fills: FakeBroker never partial-fills (returns requested qty); T212 reports `filledQuantity` (could be < requested) — no partial-fill handling in executor.
- Test-injection surface: FakeBroker exposes `_prices`/`set_price`; T212 has nothing equivalent. **Production risk-gate code reads `broker._prices` via `hasattr` — diverges live vs backtest.**
- Mark-to-market: FakeBroker re-marks via `set_price`; T212 returns whatever `currentPrice` the API gives.
- HTTP-bug: T212 uses `await resp.json() if callable(...) else resp.json()` (F-broker-003).

## Module exports (`src/broker/__init__.py`)
- `Broker`, `BrokerRejection`, `Fill`, `Portfolio`, `Position`, `FakeBroker`, `Trading212Broker`.

## Config keys / wiring
- Trading212Broker constructed in `src/orchestrator/tick.py:302` (live tick) and `scripts/initialise.py:37` (anchor); both pull `api_key` from env (T212_API_KEY-style; not read inside broker module itself).
- FakeBroker constructed in `src/backtest/runner.py:461` with `starting_cash` from `BacktestSettings.fake_broker_starting_cash` and a `prices` map seeded from the cache.
- `instrument_map` for T212 is built once at startup from the `/instruments` endpoint (out of broker module; orchestrator-level).
- No env-var reading inside `src/broker/` itself — credentials are passed in by callers. Clean.

## Consumers (where broker methods are called from)
- `get_portfolio`: `agents/executor/agent.py:205`, `agents/risk_gate/agent.py:95`, `agents/snapshot/agent.py:38`, `lifecycle/initialise.py:93`, `orchestrator/tick.py:129`, `backtest/driver.py:276`, `backtest/runner.py:535`.
- `submit_market`: `agents/executor/agent.py` (the only legitimate caller; verified via grep).
- `position_size`: **no callers**.
- `set_price`: `backtest/driver.py:702`, `scripts/replay_backtest.py:72`, `tests/executor/test_executor_bookkeeping.py:269`.
- `_prices` (private): `agents/risk_gate/agent.py:101-102` — **production code reading test-only attribute (F-broker-001).**
