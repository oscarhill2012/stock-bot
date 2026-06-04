"""Loud-failure tests for the risk_gate agent.

Each test in this file exists because the historical behaviour was to
silently return / no-op on a missing or malformed input. The new contract
is "raise on every missing-input case"; these tests pin that behaviour.

Task 3 tests (A-002, A-005) additionally verify that the price map for
unheld BUY tickers is sourced exclusively from ``state["reference_prices"]``
(bars-array shape, last-bar close), never from the FakeBroker-private
``_prices`` attribute which has no Trading 212 equivalent.
"""
import pytest

from agents.risk_gate.agent import RiskGateAgent, RiskGateInputError


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_missing(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Missing strategist_decision is a wiring bug — must raise loudly."""
    ctx = _invocation_context_with_state(state={})  # no strategist_decision
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_risk_gate_raises_when_strategist_decision_is_none(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """Explicit None counts as missing — must raise, not silently skip."""
    ctx = _invocation_context_with_state(state={"strategist_decision": None})
    agent = RiskGateAgent(broker=fake_broker_factory())

    with pytest.raises(RiskGateInputError, match="strategist_decision"):
        async for _ in agent._run_async_impl(ctx):
            pass


# ---------------------------------------------------------------------------
# Task 3 — A-002 / A-005: reference_prices as canonical price source
# ---------------------------------------------------------------------------

# Canonical bars-array fixture for NVDA at close 950.0.
# Shape mirrors PriceHistory.model_dump(mode="json"):
#   {"ticker": str, "bars": [{"timestamp": str, "open": float, "high": float,
#                              "low": float, "close": float, "volume": int}]}
# PIT clamping trims bars to ≤ as_of, so bars[-1].close is always the
# correct point-in-time close to use when building the price map.
_NVDA_REFERENCE_PRICES = {
    "NVDA": {
        "ticker": "NVDA",
        "bars": [
            {
                "timestamp": "2026-05-26T00:00:00",
                "open":   940.0,
                "high":   955.0,
                "low":    938.0,
                "close":  950.0,
                "volume": 1_000_000,
            }
        ],
    }
}


@pytest.mark.asyncio
async def test_risk_gate_uses_reference_prices_for_unheld_buy(
    fake_broker_factory,
    _invocation_context_with_state,
    _decision_with_buy,
):
    """Price for an unheld BUY comes from state['reference_prices'] bars[-1].close.

    The cash-only default portfolio does not hold NVDA, so the price must be
    read from ``reference_prices`` rather than from ``portfolio.positions``.
    The resulting order's ``est_price`` must equal the seeded close (950.0).
    """
    decision = _decision_with_buy("NVDA", 0.05)

    ctx = _invocation_context_with_state(state={
        "strategist_decision": decision.model_dump(),
        "reference_prices":    _NVDA_REFERENCE_PRICES,
    })
    # Cash-only broker — no positions, no _prices override.
    agent = RiskGateAgent(broker=fake_broker_factory(positions={}))

    events = []
    async for ev in agent._run_async_impl(ctx):
        events.append(ev)

    # Confirm an order was generated for NVDA.
    final_orders = events[0].actions.state_delta["final_orders"]
    nvda_orders = [o for o in final_orders if o["ticker"] == "NVDA"]
    assert nvda_orders, "Expected an NVDA order but none was generated"

    order = nvda_orders[0]
    assert order["action"] == "BUY", f"Expected BUY, got {order['action']}"
    assert order["est_price"] == 950.0, (
        f"Expected est_price 950.0 from reference_prices bars[-1].close, "
        f"got {order['est_price']}"
    )


@pytest.mark.asyncio
async def test_risk_gate_raises_when_reference_price_missing_for_unheld_buy(
    fake_broker_factory,
    _invocation_context_with_state,
    _decision_with_buy,
):
    """Missing reference_prices entry for an unheld BUY ticker must raise ValueError.

    When ``state["reference_prices"]`` has no entry for the BUY ticker and
    the portfolio doesn't hold it, ``weights_to_orders`` must raise
    ``ValueError("no price for <ticker>")`` — not silently skip the order.
    """
    decision = _decision_with_buy("NVDA", 0.05)

    ctx = _invocation_context_with_state(state={
        "strategist_decision": decision.model_dump(),
        "reference_prices":    {},   # NVDA absent — must raise
    })
    agent = RiskGateAgent(broker=fake_broker_factory(positions={}))

    with pytest.raises(ValueError, match="no price for NVDA"):
        async for _ in agent._run_async_impl(ctx):
            pass


@pytest.mark.asyncio
async def test_risk_gate_does_not_read_broker__prices_attribute(
    fake_broker_factory,
    _invocation_context_with_state,
    _decision_with_buy,
):
    """Price sourcing must NOT fall back to ``broker._prices`` (A-002, A-005).

    The old code checked ``hasattr(self.broker, "_prices")`` and used the
    broker's private price map as a gap-filler.  That channel has no
    Trading 212 equivalent and produced silently wrong prices in production.
    After the A-002/A-005 fix, the agent must read ``state["reference_prices"]``
    exclusively — even when ``broker._prices`` carries a conflicting value.

    This test sets ``broker._prices["NVDA"] = 1.0`` (a hostile value) while
    ``reference_prices`` supplies the correct close of 950.0.  If the
    reach-in is still present the order prices at 1.0; if it is gone the
    order prices at 950.0.
    """
    decision = _decision_with_buy("NVDA", 0.05)

    ctx = _invocation_context_with_state(state={
        "strategist_decision": decision.model_dump(),
        "reference_prices":    _NVDA_REFERENCE_PRICES,  # correct close = 950.0
    })
    # Hostile broker._prices for NVDA — must NOT be used after the fix.
    agent = RiskGateAgent(
        broker=fake_broker_factory(positions={}, prices={"NVDA": 1.0})
    )

    events = []
    async for ev in agent._run_async_impl(ctx):
        events.append(ev)

    final_orders = events[0].actions.state_delta["final_orders"]
    nvda_orders = [o for o in final_orders if o["ticker"] == "NVDA"]
    assert nvda_orders, "Expected an NVDA order but none was generated"

    order = nvda_orders[0]
    assert order["est_price"] == 950.0, (
        f"broker._prices reach-in appears still active: est_price is "
        f"{order['est_price']} (expected 950.0 from reference_prices). "
        "The old hasattr(self.broker, '_prices') block must be deleted (A-002/A-005)."
    )
