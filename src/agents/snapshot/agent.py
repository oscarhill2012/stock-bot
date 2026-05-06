"""Snapshotter — records equity curve after every tick."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


class SnapshotterAgent(BaseAgent):
    name: str = "Snapshotter"
    broker: Any
    db_session: Any = None
    starting_capital: float = 10_000.0

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")

        portfolio = await self.broker.get_portfolio()
        bot_total = portfolio.total_value
        bot_cash = portfolio.cash
        bot_positions_value = bot_total - bot_cash
        bot_position_count = len(portfolio.positions)

        # Fetch SPY price
        try:
            import yfinance as yf
            spy_ticker = yf.Ticker("SPY")
            hist = spy_ticker.history(period="1d")
            spy_price = float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
        except Exception:
            spy_price = 0.0

        # Starting capital from state (frozen on first tick)
        if "starting_capital" not in state:
            state["starting_capital"] = bot_total
        start = state["starting_capital"]
        spy_start = state.get("spy_start_price", spy_price)
        if "spy_start_price" not in state:
            state["spy_start_price"] = spy_price

        bot_return_pct = (bot_total - start) / start * 100 if start else 0.0
        spy_return_pct = (spy_price - spy_start) / spy_start * 100 if spy_start else 0.0
        excess_return_pct = bot_return_pct - spy_return_pct
        spy_value_if_held = start * (1 + spy_return_pct / 100)

        snap = {
            "tick_id": tick_id,
            "recorded_at": datetime.now(tz=timezone.utc),
            "bot_total_value": bot_total,
            "bot_cash": bot_cash,
            "bot_positions_value": bot_positions_value,
            "bot_position_count": bot_position_count,
            "spy_price": spy_price,
            "spy_value_if_held": spy_value_if_held,
            "bot_return_pct": bot_return_pct,
            "spy_return_pct": spy_return_pct,
            "excess_return_pct": excess_return_pct,
            "holdings_breakdown": portfolio.current_weights(),
        }

        if self.db_session:
            from orchestrator.persistence import save_portfolio_snapshot
            save_portfolio_snapshot(self.db_session, snap)
            self.db_session.commit()

        state["last_snapshot"] = snap
        return
        yield


def build_snapshotter(broker, db_session=None) -> SnapshotterAgent:
    return SnapshotterAgent(broker=broker, db_session=db_session)
