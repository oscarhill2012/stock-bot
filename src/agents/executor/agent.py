"""Executor BaseAgent — submits orders via Broker, manages position book."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.executor._verb_dispatch import HALLUCINATED, apply_stance_to_thesis
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

        # Recover the prior held book from the canonical cross-tick thesis
        # store, ``state["user:positions"]`` — written by
        # ``_executor_thesis_writer_callback`` (after_agent_callback) and
        # re-hydrated by ADK at each tick start.  This is the source of truth
        # for positions opened on PRIOR ticks, so a SELL this tick can find
        # the position it is closing (audit A-014).
        #
        # ``positions`` is then mutated locally as orders are processed: a BUY
        # adds the assembled thesis, a full-close SELL removes it.  Same-tick
        # BUY → SELL works through this shared local dict — no state key is
        # needed for in-tick visibility.  We do NOT write ``user:positions``
        # here; the after-callback is its sole writer-of-record.
        positions: dict = dict(state.get("user:positions") or {})

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

                # BUY: assemble the thesis from the buy-intent stance + fill price
                # and record it in the local position book so SELL can recover
                # it in the same tick.  The after_agent_callback writes the
                # new-model ``user:positions`` after the run loop completes.
                #
                # Band 6 change: the strategist no longer pre-computes
                # ``new_positions``; the executor is the only agent with an
                # honest fill price, so PositionThesis assembly belongs here.
                # We find the ``intent="buy"`` stance for this ticker and call
                # ``apply_stance_to_thesis`` — the same helper used by the
                # after-callback — so there is exactly one assembly path.
                if order.action == "BUY":
                    decision_raw = state.get("strategist_decision") or {}
                    stances_raw  = decision_raw.get("stances") or []

                    # Find the buy-intent stance for this ticker.
                    open_stance = next(
                        (
                            TickerStance.model_validate(s) if isinstance(s, dict) else s
                            for s in stances_raw
                            if (s.get("ticker") if isinstance(s, dict) else s.ticker) == order.ticker
                            and (s.get("intent") if isinstance(s, dict) else s.intent) == "buy"
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

                        # Read the window-relative tick index so the thesis row
                        # records when it was written (used by context_shim for
                        # staleness rendering).  Defaults to 0 if absent — safe
                        # for legacy live runs that do not yet populate this key.
                        current_tick_index: int = state.get("user:current_tick_index") or 0

                        new_thesis = apply_stance_to_thesis(
                            open_stance,
                            prior_row          = None,
                            fill_price         = fill.price,
                            tick_id            = tick_id,
                            as_of              = resolved_as_of,
                            current_tick_index = current_tick_index,
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
                            # Retrieve the sell reason from the iter-3 canonical key.
                            _sd = state.get("strategist_decision", {})
                            close_reason = (
                                (_sd.get("sell_reasons") or {})
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
                                    # Same-tick BUYs stash ``opened_tag`` on the
                                    # thesis dict, but a position recovered
                                    # cross-tick from ``user:positions`` has none
                                    # (PositionThesis is extra="forbid").  Fall
                                    # back to the opening tick id — the natural
                                    # traceability proxy — then the current
                                    # tick_id, so the non-nullable trade_log
                                    # column is always populated.
                                    "opened_tag": (
                                        (thesis.get("opened_tag") if isinstance(thesis, dict) else getattr(thesis, "opened_tag", None))
                                        or (thesis.get("opened_tick_id") if isinstance(thesis, dict) else getattr(thesis, "opened_tick_id", None))
                                        or tick_id
                                    ),
                                    "closed_tag":          state.get("strategist_decision", {}).get("decision_tag", "unknown"),
                                    "opened_rationale":    thesis.get("rationale") if isinstance(thesis, dict) else thesis.rationale,
                                    "close_reason":        close_reason,
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
        state["executions"]            = executions
        state["last_executed_tick_id"] = tick_id

        # Note: ``user:positions`` is intentionally NOT mutated here.  Writing
        # it via a direct dict assignment would make the in-memory value visible
        # to the after_agent_callback (_executor_thesis_writer_callback), which
        # reads ``user:positions`` to obtain the *prior* held book.  If the
        # in-tick BUY result is already in the state dict, the callback would
        # see a buy stance against a ticker it thinks is already held — incorrect
        # behaviour.  Instead, ``user:positions`` is written only by the callback
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
        # The mutations above (``executions``, ``last_executed_tick_id``) are
        # visible to same-tick agents via the shared object reference, but they
        # never reach ``DatabaseSessionService`` storage unless they also appear
        # in this delta.
        #
        # ``user:positions`` is intentionally ABSENT from this state_delta.
        # It is the sole responsibility of ``_executor_thesis_writer_callback``
        # (after_agent_callback), which runs after this method completes and
        # writes the richer stance-derived version via ADK's delta-tracked
        # ``ctx.state`` writes.  Including it here would be a double-write
        # that violates the writer-of-record split.
        #
        # The local ``positions`` dict (seeded from ``user:positions`` at the
        # top of this method) is mutated in-tick and consumed locally for the
        # SELL gate — it is NOT propagated to the state_delta.  The after-
        # callback re-derives the canonical ``user:positions`` from the stance
        # list + fill prices and is its sole writer-of-record.
        #
        # ``user:closed_trades_log`` is included in the delta only when this
        # tick actually appended to it (i.e. at least one full close happened).
        # Writing it unconditionally would clobber the persisted value on every
        # tick even when nothing changed; conditional inclusion keeps the delta
        # minimal while still guaranteeing the write survives to cross-tick state.
        delta: dict = {
            "executions":            executions,
            "last_executed_tick_id": tick_id,
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

    # Per-tick counter for strategist hallucinations (e.g. sell on a
    # ticker with no live position).  Bumped by the dispatcher's
    # HALLUCINATED sentinel; surfaced in state for the reporting layer.
    hallucinated_count: int = 0

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

        # Read the window-relative tick index so buy/update stances
        # record ``thesis_last_updated_tick`` on the resulting row.
        # Defaults to 0 when the key is absent (live runs not yet
        # populating it; legacy test sessions).
        current_tick_index: int = state.get("user:current_tick_index") or 0

        try:
            new_row = apply_stance_to_thesis(
                stance,
                prior_row          = prior_row,
                fill_price         = fill_price,
                tick_id            = state.get("tick_id", "unknown"),
                as_of              = resolved_as_of,
                current_tick_index = current_tick_index,
            )
        except AssertionError:
            # Caller bug (e.g. ``buy`` reaching the dispatcher with no fill
            # price).  Strategist hallucinations are now reported via the
            # HALLUCINATED sentinel — they don't raise.  An AssertionError
            # here means our wiring is wrong, not the LLM's output, so we
            # log loudly with the full traceback and continue rather than
            # abort the tick.
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

        if new_row is HALLUCINATED:
            # Strategist emitted a verb that's impossible against the prior
            # state (e.g. sell on a ticker with no live position).  The
            # dispatcher has already logged loudly; we just count it and
            # leave the existing row alone.
            hallucinated_count += 1
            continue

        if new_row is None:
            # Either a full close, or no_action against a ticker with no
            # prior row.  Either way the ticker should not be in the book
            # after this stance — pop covers both ("was there, now gone"
            # and "wasn't there, still isn't").
            updated_positions.pop(ticker, None)
        else:
            updated_positions[ticker] = new_row.model_dump(mode="json")

    # Surface the per-tick hallucination count so the reporting layer can
    # roll it up across a run.  Always written (even when zero) so
    # downstream consumers don't have to guard for missing keys.
    state["temp:hallucinated_stances"] = hallucinated_count
    if hallucinated_count:
        logger.warning(
            "executor: %d hallucinated stance(s) this tick "
            "(tick_id=%s) — see prior warnings for details.",
            hallucinated_count,
            state.get("tick_id", "unknown"),
        )

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
