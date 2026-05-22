"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from broker.protocol import Broker, BrokerRejection
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe
from orchestrator.state import Execution, Order


class ExecutorAgent(BaseAgent):
    """ADK agent that submits the risk-gated orders to the broker and records results.

    Responsibilities:
    - Submit each Order from state["final_orders"] via the broker.
    - Record fill details and slippage in state["executions"].
    - Update the position book (state["positions"]).
    - Write a trade-log entry to the DB when a position fully closes.
    - Idempotency guard: skips execution if tick_id was already processed.
    """

    name: str = "Executor"
    broker: Any  # Broker protocol — typed as Any to avoid Pydantic's Protocol issues
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        tick_id: str = state.get("tick_id", "unknown")

        # Guard against re-running the same tick (e.g. on ADK retry).
        if state.get("last_executed_tick_id") == tick_id:
            return
            yield  # pragma: no cover — keeps this function an async generator

        orders_raw = state.get("final_orders", [])
        orders = [
            Order.model_validate(o) if isinstance(o, dict) else o
            for o in orders_raw
        ]

        executions: list[dict] = []
        positions: dict = dict(state.get("positions", {}))

        for order in orders:
            try:
                fill = await self.broker.submit_market(
                    order.ticker, order.action, order.quantity
                )
                exec_record = Execution(
                    order=order,
                    status="filled",
                    actual_price=fill.price,
                    actual_quantity=fill.quantity,
                    broker_order_id=fill.id,
                    slippage_bps=(
                        abs(fill.price - order.est_price) / order.est_price * 10_000
                        if order.est_price else None
                    ),
                )

                # BUY: record the thesis in the position book so SELL can later
                # recover it.  Stamp ``opened_price`` with the actual fill price
                # here — the strategist could not know the fill price at decision
                # time, so ``PositionThesis.opened_price`` arrives as ``None``
                # from ``derive_legacy_fields`` and is filled in below.  Without
                # this stamp, the next tick's held-view renderer would divide
                # ``(target_price - None) / None`` and crash.
                #
                # The thesis arrives as a JSON-serialised dict (the strategist's
                # after-callback dumps it before re-writing state), so we mutate
                # the dict directly rather than reconstructing the model.  A
                # defensive copy avoids mutating the strategist's decision
                # payload, which downstream code (decision snapshot logger) may
                # still inspect.
                if order.action == "BUY":
                    decision = state.get("strategist_decision") or {}
                    thesis_dict = (decision.get("new_positions") or {}).get(order.ticker)
                    if thesis_dict is not None:

                        # Shallow copy — PositionThesis fields are all scalars
                        # (no nested mutables to worry about).
                        thesis_dict = dict(thesis_dict)
                        thesis_dict["opened_price"] = fill.price
                        positions[order.ticker] = thesis_dict

                # SELL: conditionally write the trade-log entry and remove from the
                # position book — but only when the position is truly closed.
                #
                # We use the broker as the post-fill source of truth rather than
                # computing (prior_qty - fill.quantity) ourselves.  This is
                # intentional: in a concurrent environment another fill could land
                # in the same tick and our local arithmetic would be wrong.  The
                # broker already performed the subtraction atomically, so
                # get_portfolio() is the only honest answer.
                elif order.action == "SELL" and order.ticker in positions:

                    # Query the broker for the quantity remaining after the fill.
                    portfolio_after = await self.broker.get_portfolio()
                    remaining_qty = (
                        portfolio_after.positions.get(order.ticker).quantity
                        if order.ticker in portfolio_after.positions
                        else 0.0
                    )

                    if remaining_qty <= 0.0:
                        # True close — persist the trade log and clear the slot.
                        thesis = positions.get(order.ticker)
                        if thesis and self.db_session:
                            from orchestrator.persistence import save_trade_log_entry

                            opened_price = (
                                thesis.get("opened_price") if isinstance(thesis, dict)
                                else thesis.opened_price
                            )
                            opened_at_raw = (
                                thesis.get("opened_at") if isinstance(thesis, dict)
                                else thesis.opened_at
                            )
                            # Normalise opened_at to a datetime object for SQLAlchemy.
                            opened_at_dt = (
                                datetime.fromisoformat(opened_at_raw)
                                if isinstance(opened_at_raw, str)
                                else opened_at_raw
                            )
                            # Use state["as_of"] if present (backtest replay) so
                            # holding_hours is deterministic against historical ticks.
                            # Fall back to wall-clock on live runs.
                            raw_as_of = state.get("as_of")
                            closed_at = resolve_as_of(
                                raw_as_of if isinstance(raw_as_of, datetime) else None,
                                allow_wallclock=True,
                                site="executor/agent",
                            )
                            holding_hours = int(
                                (closed_at - opened_at_dt).total_seconds() / 3600
                            )
                            pnl_pct = (fill.price - opened_price) / opened_price * 100

                            save_trade_log_entry(self.db_session, {
                                "ticker":              order.ticker,
                                "opened_at":           opened_at_dt,
                                "closed_at":           closed_at,
                                "opened_price":        opened_price,
                                "closed_price":        fill.price,
                                "pnl_dollar":          (fill.price - opened_price) * fill.quantity,
                                "pnl_pct":             pnl_pct,
                                "holding_period_hours": holding_hours,
                                "horizon_intent":      thesis.get("horizon") if isinstance(thesis, dict) else thesis.horizon,
                                "opened_tag":          thesis.get("opened_tag") if isinstance(thesis, dict) else thesis.opened_tag,
                                "closed_tag":          state.get("strategist_decision", {}).get("decision_tag", "unknown"),
                                "opened_rationale":    thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale,
                                "close_reason":        state.get("strategist_decision", {}).get("close_reasons", {}).get(order.ticker, ""),
                                "catalyst_realised":   False,
                                # FK columns linking this trade back to the deliberation ticks
                                # that opened and closed the position (added in Plan C, task C11).
                                "opening_tick_id": (
                                    thesis.get("opened_tick_id") if isinstance(thesis, dict)
                                    else getattr(thesis, "opened_tick_id", None)
                                ) or None,
                                "closing_tick_id": state.get("tick_id"),
                            })

                        # Remove from the live position book.
                        del positions[order.ticker]

                    # else: partial trim — broker still holds shares, so the
                    # position thesis is preserved and no trade-log row is emitted.

            except BrokerRejection as e:
                exec_record = Execution(
                    order=order,
                    status="rejected",
                    error=str(e),
                )

            executions.append(exec_record.model_dump())

        # Direct mutation — visible to any later agent in *this* tick that
        # reads ``ctx.session.state`` (same object reference).
        state["executions"]             = executions
        state["positions"]              = positions
        state["last_executed_tick_id"]  = tick_id

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(state, "07_broker_calls", executions)

        # Decision-snapshot hook — no-op in live runs that do not set
        # ``state["_decision_logger"]``.  The backtest runner installs one
        # DecisionLogger per run; once we deploy to paper/live the same hook
        # will continuously grow the RAG-seed corpus.
        dl = state.get("_decision_logger")
        if dl is not None:
            try:
                dl.on_executions(dict(state))
            except Exception:
                # Defensive: a logger failure must never abort the tick.
                pass

        # Cross-tick propagation — ADK's ``InMemorySessionService`` only
        # merges mutations into the storage session via an Event whose
        # ``actions.state_delta`` carries them.  Without this yielded event,
        # the next tick's ``session_service.get_session`` re-fetch would
        # return a copy of storage that still has the *previous* tick's
        # ``positions`` map, so any cross-tick SELL (BUY in tick T, SELL in
        # tick T+1) would lose the opening thesis and write a partial
        # ``TradeLogRow`` (null ``opening_price`` / ``opened_tag``).  The
        # idempotency guard at the top of this method would also fire on
        # stale ``last_executed_tick_id``.
        #
        # This is the same pattern as the Snapshotter fix (2026-05-19);
        # see ``docs/todo-fixes.md`` Group 2.5 for the wider audit and the
        # planned move to a DB-hydration / RAG model.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "executions":            executions,
                "positions":             positions,
                "last_executed_tick_id": tick_id,
            }),
        )


def build_executor(broker: Broker, db_session=None) -> ExecutorAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session."""
    return ExecutorAgent(broker=broker, db_session=db_session)
