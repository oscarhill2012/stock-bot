"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.executor._verb_dispatch import apply_stance_to_thesis
from agents.strategist.position_thesis import PositionThesis as NewPositionThesis
from agents.strategist.stance_schema import TickerStance
from broker.protocol import Broker, BrokerRejection
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe
from orchestrator.state import Execution, Order

# Module-level logger used by the DecisionLogger hook and the
# after-agent thesis-writer.  Named after the module so log lines route
# to ``agents.executor.agent`` for grep-ability.
logger = logging.getLogger(__name__)


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
        # the correct source for in-tick reads.  The bridge remains in place as
        # the BUY→SELL in-tick channel; ``new_positions`` (the strategist's
        # pre-computed thesis) was the only thing removed in Band 6.
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

                # BUY: assemble the thesis from the open-intent stance + fill price
                # and record it in the *legacy* position book so SELL can recover
                # it in the same tick.  The after_agent_callback writes the
                # new-model ``user:positions`` after the run loop completes.
                #
                # Band 6 change: the strategist no longer pre-computes
                # ``new_positions``; the executor is the only agent with an
                # honest fill price, so PositionThesis assembly belongs here.
                # We find the ``intent="open"`` stance for this ticker and call
                # ``apply_stance_to_thesis`` — the same helper used by the
                # after-callback — so there is exactly one assembly path.
                if order.action == "BUY":
                    decision_raw = state.get("strategist_decision") or {}
                    stances_raw  = decision_raw.get("stances") or []

                    # Find the open-intent stance for this ticker.
                    open_stance = next(
                        (
                            TickerStance.model_validate(s) if isinstance(s, dict) else s
                            for s in stances_raw
                            if (s.get("ticker") if isinstance(s, dict) else s.ticker) == order.ticker
                            and (s.get("intent") if isinstance(s, dict) else s.intent) == "open"
                        ),
                        None,
                    )

                    if open_stance is not None:
                        # Resolve the tick timestamp: use state["as_of"] when
                        # present (backtest replay) so opened_at is deterministic.
                        # Fall back to wall-clock on live runs.
                        raw_as_of = state.get("as_of")
                        resolved_as_of = resolve_as_of(
                            raw_as_of,
                            allow_wallclock=True,
                            site="executor/agent.BUY",
                        )

                        new_thesis = apply_stance_to_thesis(
                            open_stance,
                            prior_row  = None,
                            fill_price = fill.price,
                            tick_id    = tick_id,
                            as_of      = resolved_as_of,
                        )

                        if new_thesis is not None:
                            thesis_dict = new_thesis.model_dump(mode="json")

                            # Stash the decision_tag as ``opened_tag`` so the
                            # SELL path (and trade-log persistence) can read it.
                            # ``position_thesis.py:PositionThesis`` does not
                            # carry ``opened_tag`` natively; we add it here as
                            # an extra key rather than mutating the canonical
                            # schema.  This mirrors the old ``new_positions``
                            # behaviour where the strategist's ``opened_tag``
                            # came from ``decision_tag``.
                            if "opened_tag" not in thesis_dict or thesis_dict["opened_tag"] is None:
                                # Fallback to tick_id guarantees a non-null string —
                                # trade_log_row.opened_tag is Mapped[str] (non-nullable,
                                # see orchestrator/persistence.py:97), so the DB write
                                # would fail without a concrete value here.
                                # decision_raw is still in scope from line ~125; no re-read needed.
                                thesis_dict["opened_tag"] = (
                                    decision_raw.get("decision_tag") or tick_id
                                )

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
                        if thesis:
                            # ── Compute close-trade details once ──────────
                            # Hoisted out of the prior ``if self.db_session``
                            # block so the rolling in-memory log below is
                            # written even when no DB session is wired (the
                            # strategist's context render does not need one).
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
                            close_reason = (
                                state.get("strategist_decision", {})
                                     .get("close_reasons", {})
                                     .get(order.ticker, "")
                            )

                            if self.db_session:
                                from orchestrator.persistence import save_trade_log_entry

                                save_trade_log_entry(self.db_session, {
                                    "ticker":              order.ticker,
                                    "opened_at":           opened_at_dt,
                                    "closed_at":           closed_at,
                                    "opened_price":        opened_price,
                                    "closed_price":        fill.price,
                                    "pnl_dollar":          (fill.price - opened_price) * fill.quantity,
                                    "pnl_pct":             pnl_pct,
                                    "holding_period_hours": holding_hours,
                                    # ``horizon_intent`` dropped in iter-3 — the field was removed
                                    # from ``TradeLogRow`` (Bug #9: hallucinated 80 % of the time).
                                    "opened_tag":          thesis.get("opened_tag") if isinstance(thesis, dict) else getattr(thesis, "opened_tag", None),
                                    "closed_tag":          state.get("strategist_decision", {}).get("decision_tag", "unknown"),
                                    "opened_rationale":    thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale,
                                    "close_reason":        close_reason,
                                    "catalyst_realised":   False,
                                    # FK columns linking this trade back to the deliberation ticks
                                    # that opened and closed the position (added in Plan C, task C11).
                                    "opening_tick_id": (
                                        thesis.get("opened_tick_id") if isinstance(thesis, dict)
                                        else getattr(thesis, "opened_tick_id", None)
                                    ) or None,
                                    "closing_tick_id": state.get("tick_id"),
                                })

                            # ── Rolling closed-trades log ────────────────────
                            # A compact in-memory mirror of the DB trade_log,
                            # capped at the last 10 closes.  Read by
                            # ``StrategistContextShim`` next tick to render a
                            # "Recent round-trips" block in the strategist's
                            # prompt — gives the LLM visibility of its own
                            # outcome history (P&L, hold time, close reason)
                            # without paying an extra DB round-trip per tick.
                            # Lives under the ``user:`` namespace so it
                            # persists across ticks via ADK's session service.
                            closed_log = list(state.get("user:closed_trades_log") or [])
                            closed_log.append({
                                "ticker":        order.ticker,
                                "closed_at":     closed_at.isoformat(),
                                "pnl_pct":       round(pnl_pct, 2),
                                "holding_hours": holding_hours,
                                "close_reason":  close_reason or "",
                            })
                            state["user:closed_trades_log"] = closed_log[-10:]

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
        state["positions"]              = positions    # Band 4 bare-key BUY→SELL bridge
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
            # Defensive: a logger failure must never abort the tick — but it
            # must NOT be silent either.  The previous
            # ``contextlib.suppress(Exception)`` here meant any serialisation
            # bug in the strict snapshot serialiser would skip the decision
            # write with zero log output, leaving the ``decisions/`` directory
            # empty across an entire run.  We now log loudly with the full
            # traceback so a regression surfaces on the very first tick.
            try:
                dl.on_executions(dict(state))
            except Exception:
                logger.warning(
                    "decision_logger: on_executions raised for tick=%s — "
                    "snapshot NOT written; tick continues",
                    tick_id,
                    exc_info = True,
                )

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
        # ``"positions"`` is the Band 4 bare-key BUY→SELL bridge — kept intentionally.
        # Include ``user:closed_trades_log`` in the delta only when this tick
        # actually mutated it (i.e. at least one close happened).  Writing
        # the key unconditionally would clobber the persisted value with the
        # current in-memory snapshot on every tick, but since the snapshot
        # IS the source of truth after the in-tick mutation above this is
        # safe — kept conditional purely to keep the delta minimal.
        delta = {
            "executions":            executions,
            "last_executed_tick_id": tick_id,
            "positions":             positions,    # Band 4 bare-key BUY→SELL bridge
        }
        if "user:closed_trades_log" in state:
            delta["user:closed_trades_log"] = state["user:closed_trades_log"]

        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta=delta),
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
    prior_positions:   dict[str, dict] = dict(state.get("user:positions", {}))
    updated_positions: dict[str, dict] = dict(prior_positions)

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

        # Resolve the tick timestamp consistently with the BUY path in
        # _run_async_impl.  Passing state.get("as_of") raw would propagate
        # None into PositionThesis.opened_at on live ticks where as_of is
        # absent — a silent failure the BUY path already avoids.
        raw_as_of = state.get("as_of")
        resolved_as_of = resolve_as_of(
            raw_as_of,
            allow_wallclock=True,
            site="executor/agent.callback",
        )

        try:
            new_row = apply_stance_to_thesis(
                stance,
                prior_row  = prior_row,
                fill_price = fill_price,
                tick_id    = state.get("tick_id", "unknown"),
                as_of      = resolved_as_of,
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
            updated_positions.pop(ticker, None)
        else:
            updated_positions[ticker] = new_row.model_dump(mode="json")

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

    state["user:positions"] = updated_positions
    state["user:thesis"]    = new_thesis

    return None


def build_executor(broker: Broker, db_session=None) -> ExecutorAgent:
    """Factory used by the pipeline builder to wire in the broker and DB session.

    Unchanged by Spec B except that the constructed ``ExecutorAgent`` now
    registers ``_executor_thesis_writer_callback`` as its after-callback via
    the constructor.
    """

    return ExecutorAgent(broker=broker, db_session=db_session)
