"""Shared pytest fixtures for the risk_gate unit-test suite.

Construction approach
---------------------
The ADK ``InvocationContext`` is stubbed with a ``MagicMock`` (matching the
pattern used in ``tests/unit/orchestrator/test_risk_gate.py``).  We set
``session.state`` directly on the mock so no real ADK session machinery is
required — the agent only ever accesses ``ctx.session.state``.

``FakeBroker`` is constructed directly from ``broker.fake``; callers may
inject ``positions`` and ``prices`` dicts to simulate an existing portfolio.

Portfolio source
----------------
``RiskGateAgent._run_async_impl`` reads ``state["portfolio"]`` directly
(audit finding A-072 — the Phase 2 orchestrator refresh seeds it there).
It raises ``RuntimeError`` if the key is absent.  Therefore
``_invocation_context_with_state`` always seeds a cash-only
``Portfolio(cash=100_000.0)`` under ``state["portfolio"]`` when the caller's
``state`` dict does not already supply that key.  Tests that need a specific
portfolio (e.g. existing positions) should pass a serialised portfolio dict
themselves::

    ctx = _invocation_context_with_state(state={
        "strategist_decision": ...,
        "portfolio": Portfolio(cash=50_000.0, positions={...}).model_dump(mode="json"),
    })

Usage
-----
Each fixture returns a *callable* (a factory function), not a pre-built
object.  This lets individual tests supply the state / positions they need:

    ctx   = _invocation_context_with_state(state={"strategist_decision": ...})
    agent = RiskGateAgent(broker=fake_broker_factory(positions={"AAPL": ...}))
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position


# ---------------------------------------------------------------------------
# InvocationContext factory
# ---------------------------------------------------------------------------


@pytest.fixture
def _invocation_context_with_state():
    """Return a factory that builds a minimal ADK InvocationContext stub.

    The stub uses a ``MagicMock`` so that the agent's ``ctx.session.state``
    lookup works without importing any real ADK session machinery.

    ``RiskGateAgent`` reads ``state["portfolio"]`` directly and raises if it
    is absent (A-072).  This factory therefore seeds a cash-only default when
    the caller's ``state`` dict does not already include ``"portfolio"``.

    Returns
    -------
    Callable[[dict], MagicMock]
        ``make(state=...)`` — accepts the session-state dict and returns a
        mock whose ``session.state`` is set to that dict (with a default
        portfolio injected if missing).
    """

    def make(state: dict) -> MagicMock:
        """Build and return the mock InvocationContext.

        Parameters
        ----------
        state:
            Dict to expose as ``ctx.session.state``.  If the dict does not
            contain a ``"portfolio"`` key a cash-only default is injected so
            the risk-gate's A-072 guard does not fire unexpectedly.
        """
        # Ensure state["portfolio"] is present — RiskGateAgent raises RuntimeError
        # if it is missing (A-072: Phase 2 must seed this on every tick).
        if "portfolio" not in state:
            state = {
                "portfolio": Portfolio(cash=100_000.0).model_dump(mode="json"),
                **state,
            }

        session = MagicMock()
        session.state = state

        ctx = MagicMock()
        ctx.session = session
        ctx.invocation_id = "test-invocation"

        return ctx

    return make


# ---------------------------------------------------------------------------
# FakeBroker factory
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_broker_factory():
    """Return a factory that builds a ``FakeBroker`` instance.

    Returns
    -------
    Callable[[dict | None, dict[str, float] | None], FakeBroker]
        ``make(positions=None, prices=None)`` — builds a cash-only broker
        when ``positions`` and ``prices`` are empty or None; otherwise seeds
        the broker with the given positions and price map.
    """

    def make(
        positions: dict | None = None,
        prices: dict[str, float] | None = None,
    ) -> "FakeBroker":
        """Construct and return a ``FakeBroker``.

        Parameters
        ----------
        positions:
            Optional mapping of ticker → ``Position`` (or a plain dict with
            keys ``quantity``, ``avg_cost``, ``last_price``).  Plain dicts
            are coerced via ``Position.model_validate`` so that downstream
            calls to ``portfolio.current_weights()`` / ``market_value`` see
            a proper Pydantic model rather than a raw dict.  Pass ``None`` or
            an empty dict for a cash-only (no-holdings) broker.
        prices:
            Optional mapping of ticker → market price.  These are injected
            directly into ``FakeBroker._prices`` so the broker's
            ``submit_market`` and ``position_size`` methods can resolve tickers
            that are not already in ``positions``.

        Returns
        -------
        FakeBroker
        """
        from broker.fake import FakeBroker

        # Seed with a meaningful cash balance and the caller-supplied price
        # map (defaulting to empty).  Tests that need extra prices can call
        # broker.set_price() afterwards or pass them here.
        broker = FakeBroker(starting_cash=100_000.0, prices=prices or {})

        if positions:
            for ticker, pos in positions.items():
                # Coerce plain dicts so callers can pass convenient kwargs
                # rather than constructing Position objects themselves.
                if isinstance(pos, Position):
                    broker._positions[ticker] = pos
                else:
                    broker._positions[ticker] = Position.model_validate(pos)

        return broker

    return make


# ---------------------------------------------------------------------------
# StrategistDecision factories
# ---------------------------------------------------------------------------


@pytest.fixture
def _decision_with_buy():
    """Return a factory that builds a single-buy-stance ``StrategistDecision``.

    Returns
    -------
    Callable[[str, float], StrategistDecision]
        ``make(ticker, weight)`` — constructs a ``StrategistDecision`` with
        one ``TickerStance(intent='buy', ...)`` and a matching
        ``target_weights`` entry.  The decision object can be passed
        directly into ``session.state['strategist_decision']``.
    """

    def make(ticker: str, weight: float) -> StrategistDecision:
        """Build a minimal ``StrategistDecision`` with a single buy stance.

        Parameters
        ----------
        ticker:
            The stock ticker symbol to buy.
        weight:
            The buy-delta weight.  Must satisfy the ``TickerStance`` schema
            constraint: ``weight`` must lie in the half-open interval
            ``(0, max_delta_per_buy]`` where ``max_delta_per_buy`` is
            ``0.20`` (from ``config/risk_gate.json``).  Values outside this
            range will cause ``TickerStance`` Pydantic validation to raise at
            construction time.

        Returns
        -------
        StrategistDecision
            A fully-validated decision object ready for ``.model_dump()``.
        """
        stance = TickerStance(
            ticker    = ticker,
            intent    = "buy",
            weight    = weight,
            rationale = f"Test buy stance for {ticker}",
        )

        return StrategistDecision(
            stances        = [stance],
            target_weights = {ticker: weight},
            decision_tag   = "test_buy",
            reasoning      = "Test decision — single buy stance",
            confidence     = 0.5,
            sell_reasons   = {},
            update_reasons = {},
        )

    return make


@pytest.fixture
def _decision_with_stances():
    """Return a factory that builds a ``StrategistDecision`` from a stance list.

    Mirrors the ``_decision_with_stances`` helper in
    ``tests/unit/orchestrator/test_risk_gate.py``.

    Returns
    -------
    Callable[[list[TickerStance]], StrategistDecision]
        ``make(stances)`` — computes ``target_weights`` from the stance list
        (buy stances contribute weight; update/no_action stances do not).
    """

    def make(stances: list[TickerStance]) -> StrategistDecision:
        """Build a ``StrategistDecision`` from an arbitrary stance list.

        Non-trading stances (``update``, ``no_action``) do not contribute
        to ``target_weights``; all other stances contribute their weight
        (defaulting to 0.0 if absent).

        Parameters
        ----------
        stances:
            List of ``TickerStance`` objects to embed in the decision.

        Returns
        -------
        StrategistDecision
        """
        # Only trading stances (buy / sell) carry an explicit target weight.
        target_weights = {
            s.ticker: (s.weight or 0.0)
            for s in stances
            if s.intent not in ("update", "no_action")
        }

        return StrategistDecision(
            stances        = stances,
            target_weights = target_weights,
            decision_tag   = "test",
            reasoning      = "Test run — stances fixture",
            confidence     = 0.5,
            sell_reasons   = {},
            update_reasons = {},
        )

    return make
