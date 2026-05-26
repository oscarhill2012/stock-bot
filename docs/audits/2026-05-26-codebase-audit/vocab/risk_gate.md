# Vocabulary — `risk_gate`

Exhaustive list of names exposed or consumed by `src/agents/risk_gate/`.

## State keys (read)

- `state["strategist_decision"]` — read at `agent.py:49`; dict or
  `StrategistDecision`.

## State keys (write — via yielded Event.state_delta)

- `state["final_orders"]` — list of `Order.model_dump()` dicts
  (`agent.py:179`).
- `state["risk_clamps_applied"]` — list of `ClampRecord.model_dump()`
  dicts (`agent.py:180`).

## State keys (read by tracing helper)

- `state` (whole dict) passed to `_trace_maybe(state, "06_risk_gate_in",
  ...)` and `"06_risk_gate_out"` (`agent.py:92, 163`). The helper
  ultimately reads `state["temp:_trace"]`.

## Schema / config types

- `RiskGateConfig` (`config/risk_gate.py:34`) with fields:
  - `min_held_weight: float`
  - `max_position_weight: float`
  - `cash_floor_weight: float`
  - `max_delta_per_ticker: float`
  - `max_total_turnover: float`
  - `max_buy_delta_per_trade: float` (default 0.05)
- `Order` (`orchestrator/state.py:26`): `ticker`, `action ("BUY"|"SELL")`,
  `quantity`, `est_price`.
- `ClampRecord` (`orchestrator/state.py:35`): `rule`, `ticker`, `before`,
  `after`. Rule literal values:
  - `"max_position"`
  - `"max_delta"`
  - `"cash_floor"`
  - `"max_turnover"`
  - `"no_short"`
  - `"buy_delta_exceeded"`

## Constants (re-exported from `orchestrator.state`, sourced from
`config/risk_gate.json`)

- `MIN_HELD_WEIGHT` — open-position threshold.
- `MAX_POSITION_WEIGHT` — single-ticker concentration cap.
- `CASH_FLOOR_WEIGHT` — minimum cash reserve fraction.
- `MAX_DELTA_PER_TICKER` — per-ticker weight change cap per tick.
- `MAX_TOTAL_TURNOVER` — total portfolio turnover cap per tick.
- `ORDER_EPSILON` — `1e-6`, weight-change threshold below which no order
  is emitted.

## Module-private constants

- `_NO_RISK_GATE_INTENTS: Final[frozenset[str]] = frozenset({"hold",
  "update"})` (`agent.py:21`) — intents stripped from `proposed`.
  Stance verbs that should match: `update`, `no_action` (per intent).

## Public verbs / functions

- `RiskGateAgent(BaseAgent)` (`agent.py:24`) — class. Fields: `name`,
  `broker`.
- `RiskGateAgent._run_async_impl(ctx) -> AsyncGenerator[Event, None]`
  (`agent.py:43`).
- `risk_gate_agent = RiskGateAgent()` (`agent.py:186`) — module-level
  singleton (unreferenced; see F-risk_gate-006).
- `apply_buy_delta_clamp(stances, config) -> list[ClampRecord]`
  (`constraints.py:32`).
- `apply_constraints(proposed, current) -> list[ClampRecord]`
  (`constraints.py:160`).
- `weights_to_orders(target, portfolio, prices) -> list[Order]`
  (`orders.py:8`).

## Private verbs / functions

- `_clamp_negatives(weights, clamps)` — zero out shorts
  (`constraints.py:83`).
- `_clamp_max_position(weights, clamps)` — cap concentration
  (`constraints.py:91`).
- `_clamp_cash_floor(weights, clamps)` — proportional scale
  (`constraints.py:101`).
- `_clamp_max_delta(proposed, current, clamps)` — per-ticker delta cap
  (`constraints.py:119`).
- `_clamp_max_turnover(proposed, current, clamps)` — global turnover
  scale (`constraints.py:137`).

## Internal local-variable names of contractual interest

- `proposed` — working copy of `decision.target_weights` post-strip
  (`agent.py:68`).
- `original_weights` — snapshot of `proposed` before clamping, used for
  the lifecycle (sell-reason) check (`agent.py:89`).
- `_stance_clamps` — clamp records from `apply_buy_delta_clamp`
  (`agent.py:66`).
- `weight_clamps` — clamp records from `apply_constraints`
  (`agent.py:126`).
- `clamps` — concatenation `_stance_clamps + weight_clamps`
  (`agent.py:130`).
- `_close_tickers` — sell stances with `weight is None` (full close)
  whose target is restored to `0.0` post-clamp (`agent.py:119`).
- `_stance_intents` — `{ticker: intent}` map used to drive the strip
  (`agent.py:75`).
- `current_weights` — `portfolio.current_weights()` or `{}`
  (`agent.py:96`).
- `prices` — `{ticker: last_price}` built from `portfolio.positions`,
  augmented from `broker._prices` if available (`agent.py:100-104`).
- `final_orders` / `risk_clamps_applied` — JSON-friendly local snapshots
  for the trace + state_delta (`agent.py:157-158`).

## Constraint / clamp rule names (string literals)

- `"max_position"`, `"max_delta"`, `"cash_floor"`, `"max_turnover"`,
  `"no_short"`, `"buy_delta_exceeded"` — `ClampRecord.rule` Literal.

## Stance-verb references

- `"buy"` — accepted (clamped).
- `"sell"` — accepted; `weight is None` → full close path.
- `"update"` — in `_NO_RISK_GATE_INTENTS` (stripped).
- `"hold"` — in `_NO_RISK_GATE_INTENTS` (legacy; no longer canonical).
- `"no_action"` — referenced via stance schema but NOT stripped (see
  F-risk_gate-003).

## Cross-module imports

- From `orchestrator.state`: `MIN_HELD_WEIGHT`, `MAX_POSITION_WEIGHT`,
  `CASH_FLOOR_WEIGHT`, `MAX_DELTA_PER_TICKER`, `MAX_TOTAL_TURNOVER`,
  `ORDER_EPSILON`, `Order`, `ClampRecord`.
- From `agents.strategist.schema`: `StrategistDecision` (lazy, inside
  `_run_async_impl`).
- From `agents.strategist.stance_schema`: `TickerStance` (TYPE_CHECKING
  only).
- From `agents.strategist.derivation`: `StrategistContractViolation`
  (lazy, inside lifecycle check).
- From `config.risk_gate`: `get_risk_gate_config` (lazy, inside
  `_run_async_impl`).
- From `broker`: `Portfolio`.
- From `observability.trace`: `_trace_maybe`.
- From `google.adk.agents`: `BaseAgent`.
- From `google.adk.agents.invocation_context`: `InvocationContext`.
- From `google.adk.events`: `Event`, `EventActions`.

## Trace step names

- `"06_risk_gate_in"` — pre-clamp payload (`agent.py:92`).
- `"06_risk_gate_out"` — post-clamp payload (`agent.py:163`).

## Audit-relevant invariants surfaced in code

- "buy stances carry a weight delta; sell and update pass through"
  (`constraints.py:64`).
- "long-only bot" — encoded as `_clamp_negatives` (`constraints.py:83`).
- "Sell stances with no explicit weight (full close) bypass the per-
  ticker delta cap" (`agent.py:109-118`).
- "Closing {t} without sell_reason" — `StrategistContractViolation`
  raised (`agent.py:144-148`).
- "Skips tickers where the weight change is smaller than ORDER_EPSILON"
  (`orders.py:28`).
- "no price for {ticker}" — `ValueError` raised by `weights_to_orders`
  (`orders.py:32`).
