# executor — vocabulary surface

Exhaustive list of state keys, schema fields, config keys, and internal
verbs/functions touched by the executor module. One line each. Inputs
for the cross-module dedupe pass.

## State keys — read

- `state["tick_id"]` — current tick identifier; idempotency guard input.
- `state["last_executed_tick_id"]` — idempotency guard probe (skip if equal to `tick_id`).
- `state["final_orders"]` — list of `Order` dicts produced by RiskGate.
- `state["positions"]` — bare-key thesis book; executor-internal in-tick BUY → SELL bridge (intent §7.3).
- `state["strategist_decision"]` — full `StrategistDecision`; used for stance lookup, `sell_reasons`, `decision_tag`, `thesis`.
- `state["as_of"]` — tick clock; raw value coerced via `resolve_as_of`.
- `state["user:current_tick_index"]` — window-relative integer; written into `thesis_last_updated_tick`.
- `state["user:closed_trades_log"]` — rolling list of last 10 closed trades (read before append).
- `state["user:positions"]` — prior thesis book; read by `after_agent_callback` only.
- `state["user:thesis"]` — prior standing thesis; carry-forward source when `decision.thesis is None`.
- `state["temp:_decision_logger"]` — optional `DecisionLogger` handle.
- `state["executions"]` — read inside `after_agent_callback` to build `fill_prices` dict.

## State keys — write

- `state["executions"]` — list of `Execution.model_dump()` dicts (direct + state_delta).
- `state["positions"]` — bare-key bridge after BUY assembly / SELL removal (direct + state_delta).
- `state["last_executed_tick_id"]` — set to current tick_id (direct + state_delta).
- `state["user:closed_trades_log"]` — last 10 closed-trade summary records (direct + conditional state_delta).
- `state["user:positions"]` — written by `_executor_thesis_writer_callback` via ADK auto-yield.
- `state["user:thesis"]` — written by `_executor_thesis_writer_callback` (carry-forward or new value).
- `state["temp:hallucinated_stances"]` — per-tick integer count of hallucinated stances (always written, even zero).

## Schema fields referenced

- `Order.ticker`, `Order.action`, `Order.quantity`, `Order.est_price` — input order shape.
- `Execution.order`, `Execution.status` (filled/rejected/partial), `Execution.actual_price`, `Execution.actual_quantity`, `Execution.broker_order_id`, `Execution.slippage_bps`, `Execution.error` — output execution shape.
- `TickerStance.ticker`, `TickerStance.intent`, `TickerStance.weight`, `TickerStance.rationale` — read in BUY branch and after-callback.
- `PositionThesis.ticker`, `.opened_at`, `.opened_tick_id`, `.opened_price`, `.weight`, `.rationale`, `.last_reviewed_at`, `.last_reviewed_decision`, `.last_reviewed_reason`, `.thesis_last_updated_tick`, `.opened_tag` (added as extra) — assembled by `apply_stance_to_thesis`.
- `StrategistDecision.stances`, `.decision_tag`, `.sell_reasons`, `.thesis` — read for SELL trade-log + close reason + carry-forward thesis.

## Config keys

- None read directly by the executor module. (Trade-log persistence uses `orchestrator/persistence.py` schemas.)

## Internal verbs / functions

- `ExecutorAgent` (BaseAgent) — agent class.
- `ExecutorAgent.__init__` — wires `after_agent_callback = _executor_thesis_writer_callback`.
- `ExecutorAgent._run_async_impl` — broker-dispatch loop, BUY-thesis assembly, SELL trade-log write, idempotency guard, BUY→SELL bridge, decision-logger hook.
- `_executor_thesis_writer_callback` — assembles `user:positions` and `user:thesis` from stances + fills; writes via ADK delta-tracked `State`.
- `build_executor(broker, db_session=None)` — factory used by the pipeline builder.
- `apply_stance_to_thesis(stance, *, prior_row, fill_price, tick_id, as_of, current_tick_index)` — pure helper; produces new `PositionThesis | None | HALLUCINATED`.
- `resolve_broker_call(stance, *, prior_row)` — pure verb-to-broker-call descriptor (**no production callers** — see F-executor-007).
- `_has_live_position(row)` — private helper; `row is not None and row.opened_at is not None`.
- `_NO_TRADE_INTENTS` — `frozenset({"update", "no_action"})`.
- `HALLUCINATED` / `_Hallucinated` — sentinel for invalid-against-state stance verbs.
- `logger.warning("hallucinated_stance", extra={...})` — stable log message key consumed by `backtest/reporting.py` aggregator.

## Verb vocabulary (four-verb canonical form)

- `buy` — open or add to position; requires `fill_price`; refreshes `rationale` and `thesis_last_updated_tick`.
- `sell` — partial trim (weight given) or full close (weight absent → returns `None`). On no live position, returns `HALLUCINATED`.
- `update` — prose-only revision; no broker call; refreshes `rationale` + `thesis_last_updated_tick`; seeds row if none.
- `no_action` — explicit reviewed-no-change; refreshes review trail only; no rationale or staleness mutation; no-op if no prior row.

## Cross-tick contracts

- Writer-of-record for `user:positions` and `user:thesis` — ADK auto-yields the after-callback's delta-tracked writes as a state-delta event.
- Trade-log rows persisted only on full close (broker remaining_qty ≤ 0); trims write nothing to DB.
- `user:closed_trades_log` capped at last 10 entries; rides on state_delta only when mutated.

## Hooks / observability

- `_trace_maybe(state, "07_broker_calls", executions)` — optional trace surface.
- `state["temp:_decision_logger"].on_executions(dict(state))` — wrapped in try/except with `logger.warning(..., exc_info=True)` (NOT `contextlib.suppress`).
