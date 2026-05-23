"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.executor._verb_dispatch import apply_stance_to_thesis
from agents.strategist.position_thesis import PositionThesis as NewPositionThesis
from broker.protocol import Broker, BrokerRejection
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe
from orchestrator.state import Execution, Order


class ExecutorAgent(BaseAgent):
    """ADK agent that submits the risk-gated orders to the broker and records results.

    Responsibilities:
    - Submit each Order from state["final_orders"] via the broker.
    - Record fill details and slippage in state["executions"].
    - Write a trade-log entry to the DB when a position fully closes.
    - Idempotency guard: skips execution if tick_id was already processed.
    - After the run loop completes, the after_agent_callback
      (_executor_thesis_writer_callback) assembles and writes
      user:positions / user:thesis to persistent state.
    """

    name: str = "Executor"
    broker: Any  # Broker protocol — typed as Any to avoid Pydantic's Protocol issues
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, *, broker, db_session=None, name: str = "Executor", **kwargs):
        """Initialise the executor and wire the thesis-writer after-callback.

        Parameters
        ----------
        broker
            Broker instance (``FakeBroker`` for backtests,
            ``Trading212Broker`` for live runs).
        db_session
            Optional SQLAlchemy session for trade-log persistence.
        name
            Agent name passed to ADK (defaults to ``"Executor"``).
        """

        # Pass all fields — including broker and db_session — through to the
        # Pydantic ``BaseModel.__init__`` that ADK's BaseAgent uses.  The
        # ``after_agent_callback`` is wired here so the thesis-writer callback
        # is always registered whenever an ``ExecutorAgent`` is constructed.
        super().__init__(
            name                 = name,
            broker               = broker,
            db_session           = db_session,
            after_agent_callback = _executor_thesis_writer_callback,
            **kwargs,
        )

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

        # Resolve the working position book via the Band 4 bare-key bridge.
        # ``user:positions`` is written by ``_executor_thesis_writer_callback``
        # (after_agent_callback) AFTER this method completes, so it is not yet
        # available in state at this point during same-tick execution.  The
        # bare-key bridge (``"positions"``) carries the cross-tick value and is
        # the correct source for in-tick reads.  Band 6 will remove this bridge
        # once all callers migrate to reading from the after-callback's output.
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

                # BUY: record the thesis in the *legacy* position book so SELL can
                # later recover it in the same tick.  The after_agent_callback writes
                # the new-model ``user:positions`` after the run loop completes.
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
                                raw_as_of,
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
        state["positions"]              = positions    # legacy bridge (Band 6: remove)
        state["last_executed_tick_id"]  = tick_id

        # Note: ``user:positions`` is intentionally NOT mutated here.  Writing
        # it via a direct dict assignment would make the in-memory value visible
        # to the after_agent_callback (_executor_thesis_writer_callback), which
        # reads ``user:positions`` to obtain the *prior* held book.  If the
        # in-tick BUY result is already in the delta, the callback would see
        # an open stance against a ticker it thinks is already held — assertion
        # failure.  Instead, ``user:positions`` is written only by the callback
        # (via ADK's delta-tracked state writes) and propagated cross-tick via
        # the state_delta key in the yielded Event below.

        # Surface trace — no-op unless state["temp:_trace"] is set by trace_tick.py.
        _trace_maybe(state, "07_broker_calls", executions)

        # Decision-snapshot hook — no-op in live runs that do not set
        # ``state["temp:_decision_logger"]``.  The backtest runner installs one
        # DecisionLogger per run; once we deploy to paper/live the same hook
        # will continuously grow the RAG-seed corpus.
        dl = state.get("temp:_decision_logger")
        if dl is not None:
            # Defensive: a logger failure must never abort the tick.
            with contextlib.suppress(Exception):
                dl.on_executions(dict(state))

        # Cross-tick propagation — ADK's session service only merges mutations
        # into storage via an Event whose ``actions.state_delta`` carries them.
        # The in-tick mutations above (state["positions"]) are visible to
        # same-tick agents via the shared object reference, but they never reach
        # ``DatabaseSessionService`` storage unless we also include them here.
        # Without this, tick T+1 reads the pre-T value of the position book
        # from a freshly-deserialised session.
        #
        # ``user:positions`` is intentionally ABSENT from this state_delta.
        # It is the sole responsibility of ``_executor_thesis_writer_callback``
        # (after_agent_callback), which runs after this method completes and
        # writes the richer stance-derived version via ADK's delta-tracked
        # ``ctx.state`` writes.  Including it here would be a double-write
        # that violates the Band 4 writer-of-record split.
        #
        # Legacy "positions" key is kept as a bridge (Band 6 will remove it).
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "executions":            executions,
                "last_executed_tick_id": tick_id,
                "positions":             positions,    # legacy bridge (Band 6: remove)
            }),
        )


def _executor_thesis_writer_callback(callback_context) -> None:
    """Assemble user:positions / user:thesis from this tick's stances + fills.

    Runs after Executor's ``_run_async_impl`` has yielded its
    broker-effect ``state_delta`` (``executions``,
    ``last_executed_tick_id``).  Reads the just-emitted executions,
    the strategist decision, and the prior ``user:positions`` already
    merged into session state at Phase 2.  Writes the new
    ``user:positions`` and ``user:thesis`` via delta-tracked
    ``ctx.state[key] = value``; ADK's ``_handle_after_agent_callback``
    (base_agent.py:489–544) then auto-yields a state-delta Event from
    the accumulated delta, which the runner ingests through
    ``SessionService.append_event``.  ``DatabaseSessionService``
    persists ``user:``-prefixed keys to the ``user_state`` table.

    See contract-invariants.md §C-Rule 1 amendment (2026-05-23) for
    why this auto-yield path is conformant with Rule 1.

    Returns ``None`` — no re-prompt content (Rule 3).

    Parameters
    ----------
    callback_context
        ADK ``CallbackContext`` (or ``Context``) injected by the runner.
        Carries ``callback_context.state``, a delta-tracked ``State``
        object whose ``__setitem__`` records writes for the auto-yield.
    """

    state = callback_context.state

    # ---- decision + executions (this tick's outputs) -------------------

    decision_raw = state.get("strategist_decision")

    # Bail out gracefully if no decision is present (e.g. skipped ticks).
    if decision_raw is None:
        return None

    # Accept both dict (JSON round-tripped from session) and Pydantic object.
    from agents.strategist.schema import StrategistDecision

    decision = (
        StrategistDecision.model_validate(decision_raw)
        if isinstance(decision_raw, dict)
        else decision_raw
    )

    # Build a fill-price lookup by reading the actual_price field from each
    # execution record.  Execution records use the ``actual_price`` field
    # (from ``Execution.actual_price`` after model_dump).
    fill_prices: dict[str, float | None] = {}
    for row in state.get("executions", []):
        if not row:
            continue
        # Execution records carry actual_price (from Execution.actual_price).
        ticker = (
            (row.get("order") or {}).get("ticker") or
            (row.get("stance") or {}).get("ticker") or
            ""
        )
        if ticker:
            fill_prices[ticker] = row.get("fill_price") or row.get("actual_price")

    # ---- prior persisted thesis book (Phase 2 merge) -------------------
    # Shallow copy so we can mutate without affecting the merged dict
    # ADK keeps around for the in-tick view.
    prior_positions: dict[str, dict] = dict(state.get("user:positions", {}))
    new_positions:   dict[str, dict] = dict(prior_positions)

    for stance in (decision.stances or []):

        # Skip legacy stances with no intent — they belong to the old code path.
        if stance.intent is None:
            continue

        ticker     = stance.ticker
        fill_price = fill_prices.get(ticker)

        prior_row = (
            NewPositionThesis.model_validate(prior_positions[ticker])
            if ticker in prior_positions else None
        )

        try:
            new_row = apply_stance_to_thesis(
                stance,
                prior_row  = prior_row,
                fill_price = fill_price,
                tick_id    = state.get("tick_id", "unknown"),
                as_of      = state.get("as_of"),
            )
        except (AssertionError, ValueError):
            # Log and skip — do not abort the tick on a thesis-write failure.
            # Silent failure here is acceptable because the broker call already
            # landed; losing the thesis update is a monitoring concern, not a
            # correctness crash.  Per "silent failures are the recurring bug
            # class" policy, this is the only place a swallowed exception is
            # appropriate — and the exception type is narrow.
            import sys
            import traceback as _tb
            print(
                f"[executor/_executor_thesis_writer_callback] "
                f"apply_stance_to_thesis raised for {ticker!r} "
                f"(intent={stance.intent!r}): "
                f"{_tb.format_exc()}",
                file=sys.stderr,
            )
            continue

        if new_row is None:
            # close stance — drop the ticker from the position book.
            new_positions.pop(ticker, None)
        else:
            new_positions[ticker] = new_row.model_dump(mode="json")

    # ---- thesis carry-forward (explicit re-write) ----------------------
    # ``decision.thesis is not None`` means the strategist is actively
    # updating the standing thesis.  ``None`` is the carry-forward sentinel.
    new_thesis: str = (
        decision.thesis
        if decision.thesis is not None
        else state.get("user:thesis", "")
    )

    # ---- delta-tracked writes — ADK auto-yields the event --------------
    # Writing via ``state[key] = value`` (where state is ADK's ``State``
    # object) records the delta in ``_event_actions.state_delta`` so the
    # runner's ``_handle_after_agent_callback`` auto-yields a state-delta
    # Event, which ``SessionService.append_event`` then persists.

    state["user:positions"] = new_positions
    state["user:thesis"]    = new_thesis

    return None


def build_executor(broker: Broker, db_session=None) -> ExecutorAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session.

    Unchanged by Spec B except that the constructed ``ExecutorAgent`` now
    registers ``_executor_thesis_writer_callback`` as its after-callback via
    the constructor.
    """

    return ExecutorAgent(broker=broker, db_session=db_session)
