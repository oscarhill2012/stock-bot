"""Snapshotter — records equity curve after every tick."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from broker.portfolio import Portfolio
from data.timeguard import resolve_as_of

logger = logging.getLogger(__name__)


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

        # A-072: read the Phase 2 canonical snapshot rather than re-pulling
        # from the broker mid-tick.  Same rationale as risk_gate — the
        # broker remains source-of-truth, but Phase 2 already published it.
        portfolio = Portfolio.from_state_value(state.get("portfolio"))
        bot_total          = portfolio.total_value
        bot_cash           = portfolio.cash
        bot_positions_value = bot_total - bot_cash
        bot_position_count  = len(portfolio.positions)

        # Resolve the tick clock first so the SPY lookup below sees the right
        # as_of.  Backtest replays inject ``state["as_of"]``; live runs fall
        # back to wall-clock via ``resolve_as_of(allow_wallclock=True)``.
        raw_as_of = state.get("as_of")
        recorded_at = resolve_as_of(
            raw_as_of,
            allow_wallclock=True,
            site="snapshot/agent",
        )

        # Fetch the latest SPY close via the registered price-history provider
        # so the call honours STOCKBOT_STRICT_AS_OF and goes through the cache
        # in backtest replays (instead of leaking the wall-clock SPY price into
        # historical snapshots).  Live runs dispatch to the yfinance provider.
        #
        # A bare ``except: spy_price = 0.0`` silently destroys every return
        # calc, because the first tick anchors ``spy_start_price``; a 0.0
        # anchor turns every subsequent ``(spy_price - 0) / 0 * 100`` into
        # nonsense.  Policy:
        #   * First tick (no ``spy_start_price`` yet) — re-raise, since the
        #     anchor is load-bearing and cannot be reconstructed later.
        #   * Subsequent ticks — log a WARNING with traceback and reuse
        #     ``state["last_spy_price"]`` (the prior tick's value).  Never
        #     silently substitute 0.0.
        from data import get_price_history

        tick_phase = state.get("tick_phase")
        first_tick = "spy_start_price" not in state

        try:
            spy_hist = await get_price_history(
                "SPY",
                period   = "5d",
                interval = "1d",
                as_of    = recorded_at,
                phase    = tick_phase,
            )
            if not spy_hist.bars:
                raise RuntimeError(
                    f"SPY price history returned no bars at "
                    f"as_of={recorded_at.isoformat()}"
                )
            spy_price = float(spy_hist.bars[-1].close)
        except Exception:
            if first_tick:
                # Re-raise — anchoring at 0.0 would permanently break the run.
                logger.exception(
                    "snapshotter: SPY fetch failed on first tick at %s; "
                    "refusing to anchor spy_start_price at 0.0",
                    recorded_at.isoformat(),
                )
                raise
            prior = state.get("last_spy_price")
            if prior is None or float(prior) <= 0.0:
                logger.exception(
                    "snapshotter: SPY fetch failed at %s and no prior anchor "
                    "available", recorded_at.isoformat(),
                )
                raise
            logger.warning(
                "snapshotter: SPY fetch failed at %s; reusing "
                "last_spy_price=%.4f", recorded_at.isoformat(), float(prior),
                exc_info=True,
            )
            spy_price = float(prior)

        # Cache for the next tick's fallback path.
        state["last_spy_price"] = spy_price

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
        #
        # ISO-stringify before storing — this dict is published into ADK
        # session state below (``state["last_snapshot"]`` + state_delta) and
        # the backtest's ``DatabaseSessionService`` JSON-serialises the
        # whole session state on every ``append_event``.  json.dumps cannot
        # encode a raw ``datetime`` (TypeError "Object of type datetime is
        # not JSON serializable") and would abort the tick mid-snapshot.
        # ``save_portfolio_snapshot`` calls ``resolve_as_of`` on whatever
        # shape it receives, so the ISO string round-trips losslessly into
        # the SQLAlchemy ``DateTime`` column.  Same coercion the backtest
        # driver applies to ``state["as_of"]`` for exactly the same reason
        # (see ``backtest/driver.py:494-499``).
        snap = {
            "tick_id":              tick_id,
            "recorded_at":          recorded_at.isoformat(),
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

        # Publish the snapshot into ADK session state.  The direct dict
        # assignment below is sufficient for any *in-tick* reader on this
        # same ``ctx.session`` reference, but ADK's
        # ``InMemorySessionService`` only merges mutations into the
        # *storage* session via an Event whose ``actions.state_delta``
        # carries them.  The backtest driver re-fetches the session at the
        # end of every tick (``session_service.get_session``) and checks
        # ``state["last_snapshot"]["tick_id"]``; without the yielded
        # state_delta below, the re-fetched copy lacks ``last_snapshot``
        # entirely and the driver aborts the whole run with
        # "pipeline did not reach snapshotter for tick ...".
        #
        # The wider cross-tick state-propagation issue (MemoryWriter's
        # ``memory_buffer`` / ``day_digest`` / ``thesis`` and Executor's
        # ``executions`` / ``last_executed_tick_id`` rely on direct
        # ``state[k]=v`` mutations that are silently lost between ticks)
        # is tracked in ``docs/todo-fixes.md`` under Group 2.5 —
        # cross-tick ADK session state propagation.
        state["last_snapshot"] = snap

        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={"last_snapshot": snap}),
        )


def build_snapshotter(broker, db_session=None) -> SnapshotterAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session."""
    return SnapshotterAgent(broker=broker, db_session=db_session)
