"""Snapshotter — records equity curve after every tick."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from data.timeguard import resolve_as_of


class SnapshotterAgent(BaseAgent):
    """Records a portfolio snapshot (bot vs SPY) into the DB after each tick.

    The snapshot includes the bot's total value, cash, and position count,
    alongside the current SPY price, so the equity_curve module can compute
    relative performance without additional data fetches.

    Starting capital and initial SPY price are frozen into session state on
    the first tick and reused for all subsequent return calculations.
    """

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
        bot_total          = portfolio.total_value
        bot_cash           = portfolio.cash
        bot_positions_value = bot_total - bot_cash
        bot_position_count  = len(portfolio.positions)

        # Resolve the tick clock first so the SPY lookup below sees the right
        # as_of.  Backtest replays inject ``state["as_of"]``; live runs fall
        # back to wall-clock via ``resolve_as_of(allow_wallclock=True)``.
        raw_as_of = state.get("as_of")
        recorded_at = resolve_as_of(
            raw_as_of if isinstance(raw_as_of, datetime) else None,
            allow_wallclock=True,
            site="snapshot/agent",
        )

        # Fetch the latest SPY close via the registered price-history provider
        # so the call honours STOCKBOT_STRICT_AS_OF and goes through the cache
        # in backtest replays (instead of leaking the wall-clock SPY price into
        # historical snapshots).  Live runs dispatch to the yfinance provider
        # and degrade cleanly to "today's close".  Falls back to 0.0 on any
        # provider failure so a single bad bar can never abort the tick.
        spy_price = 0.0
        try:
            from data import get_price_history
            tick_phase = state.get("tick_phase")
            spy_hist = await get_price_history(
                "SPY",
                period   = "5d",
                interval = "1d",
                as_of    = recorded_at,
                phase    = tick_phase,
            )
            if spy_hist.bars:
                spy_price = float(spy_hist.bars[-1].close)
        except Exception:  # noqa: BLE001 — defensive; never crash the tick
            spy_price = 0.0

        # Anchor starting capital and SPY price on the very first tick.
        if "starting_capital" not in state:
            state["starting_capital"] = bot_total
        start = state["starting_capital"]

        if "spy_start_price" not in state:
            state["spy_start_price"] = spy_price
        spy_start = state.get("spy_start_price", spy_price)

        # Compute returns relative to the anchor.
        bot_return_pct  = (bot_total - start) / start * 100 if start else 0.0
        spy_return_pct  = (spy_price - spy_start) / spy_start * 100 if spy_start else 0.0
        excess_return_pct = bot_return_pct - spy_return_pct
        spy_value_if_held = start * (1 + spy_return_pct / 100)

        # ``recorded_at`` was resolved above so the SPY lookup could honour
        # the tick clock; re-use it here for the snapshot row's timestamp.
        snap = {
            "tick_id":              tick_id,
            "recorded_at":          recorded_at,
            "bot_total_value":      bot_total,
            "bot_cash":             bot_cash,
            "bot_positions_value":  bot_positions_value,
            "bot_position_count":   bot_position_count,
            "spy_price":            spy_price,
            "spy_value_if_held":    spy_value_if_held,
            "bot_return_pct":       bot_return_pct,
            "spy_return_pct":       spy_return_pct,
            "excess_return_pct":    excess_return_pct,
            "holdings_breakdown":   portfolio.current_weights(),
        }

        if self.db_session:
            from orchestrator.persistence import save_portfolio_snapshot
            save_portfolio_snapshot(self.db_session, snap)
            self.db_session.commit()

        state["last_snapshot"] = snap
        return
        yield  # required to make this an async generator


def build_snapshotter(broker, db_session=None) -> SnapshotterAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session."""
    return SnapshotterAgent(broker=broker, db_session=db_session)
