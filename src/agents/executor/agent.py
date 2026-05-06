"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator, Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from broker.protocol import Broker, BrokerRejection
from orchestrator.state import Execution, Order


class ExecutorAgent(BaseAgent):
    name: str = "Executor"
    broker: Any  # Broker protocol — Any to avoid Pydantic issues with Protocol
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        tick_id: str = state.get("tick_id", "unknown")

        # Idempotency guard
        if state.get("last_executed_tick_id") == tick_id:
            return
        yield

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
                # Handle position close
                if order.action == "SELL" and order.ticker in positions:
                    thesis = positions.get(order.ticker)
                    if thesis and self.db_session:
                        from orchestrator.persistence import save_trade_log_entry
                        opened_price = (
                            thesis.get("opened_price") if isinstance(thesis, dict)
                            else thesis.opened_price
                        )
                        opened_at = (
                            thesis.get("opened_at") if isinstance(thesis, dict)
                            else thesis.opened_at
                        )
                        closed_at = datetime.now(tz=timezone.utc)
                        holding_hours = int(
                            (closed_at - (
                                datetime.fromisoformat(opened_at) if isinstance(opened_at, str)
                                else opened_at
                            )).total_seconds() / 3600
                        )
                        pnl_pct = (fill.price - opened_price) / opened_price * 100
                        save_trade_log_entry(self.db_session, {
                            "ticker": order.ticker,
                            "opened_at": opened_at,
                            "closed_at": closed_at,
                            "opened_price": opened_price,
                            "closed_price": fill.price,
                            "pnl_dollar": (fill.price - opened_price) * fill.quantity,
                            "pnl_pct": pnl_pct,
                            "holding_period_hours": holding_hours,
                            "horizon_intent": thesis.get("horizon") if isinstance(thesis, dict) else thesis.horizon,
                            "opened_tag": thesis.get("opened_tag") if isinstance(thesis, dict) else thesis.opened_tag,
                            "closed_tag": state.get("strategist_decision", {}).get("decision_tag", "unknown"),
                            "opened_rationale": thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale,
                            "close_reason": state.get("strategist_decision", {}).get("close_reasons", {}).get(order.ticker, ""),
                            "catalyst_realised": False,
                        })
                    del positions[order.ticker]
            except BrokerRejection as e:
                exec_record = Execution(
                    order=order,
                    status="rejected",
                    error=str(e),
                )

            executions.append(exec_record.model_dump())

        state["executions"] = executions
        state["positions"] = positions
        state["last_executed_tick_id"] = tick_id


def build_executor(broker: Broker, db_session=None) -> ExecutorAgent:
    return ExecutorAgent(broker=broker, db_session=db_session)
