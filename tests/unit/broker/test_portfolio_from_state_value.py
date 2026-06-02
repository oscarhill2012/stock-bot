"""Tests for Portfolio.from_state_value — the single sanctioned coercion
helper for reading ``state["portfolio"]`` across the codebase.

See plan-03-state-helpers.md §1 for the design rationale.  Every site that
reads ``state["portfolio"]`` MUST go through this classmethod; direct dict
access or ad-hoc ``Portfolio(**d)`` calls are banned by that plan.
"""

import pytest

from broker.portfolio import Portfolio, Position

# ---------------------------------------------------------------------------
# Shared fixture — a portfolio with one open position, used across tests
# that need to round-trip through model_dump.
# ---------------------------------------------------------------------------

def _make_portfolio_with_position() -> Portfolio:
    """Return a Portfolio containing a single AAPL position for fixture use.

    Returns:
        Portfolio with cash=500.0 and one AAPL position (qty=3, last=42.0).
    """
    return Portfolio(
        cash=500.0,
        positions={
            "AAPL": Position(quantity=3.0, avg_cost=40.0, last_price=42.0),
        },
    )


# ---------------------------------------------------------------------------
# Test 1: live Portfolio instance passes straight through (identity check)
# ---------------------------------------------------------------------------

def test_from_state_value_passes_through_instance():
    """A live Portfolio object must be returned as-is (same identity).

    The hot path inside a single tick coerces once and stashes the object
    back; subsequent reads must not re-validate unnecessarily.
    """
    p = Portfolio(cash=100.0)

    result = Portfolio.from_state_value(p)

    assert result is p


# ---------------------------------------------------------------------------
# Test 2: JSON-mode dict dump round-trips back to a valid Portfolio
# ---------------------------------------------------------------------------

def test_from_state_value_validates_dict_dump():
    """model_dump(mode='json') dict must be coerced back into a Portfolio.

    This is the cross-tick storage shape: the orchestrator persists the
    portfolio as a plain JSON-serialisable dict and rehydrates it on the
    next tick.
    """
    original = _make_portfolio_with_position()
    dumped = original.model_dump(mode="json")

    out = Portfolio.from_state_value(dumped)

    assert isinstance(out, Portfolio)
    assert out.cash == 500.0
    assert out.positions["AAPL"].quantity == 3.0
    assert out.positions["AAPL"].avg_cost == 40.0
    assert out.positions["AAPL"].last_price == 42.0


# ---------------------------------------------------------------------------
# Test 3: None raises ValueError with a clear "missing" message
# ---------------------------------------------------------------------------

def test_from_state_value_raises_on_none():
    """Passing None must raise ValueError mentioning 'state[.portfolio.] missing'.

    A missing portfolio is a contract violation — cold start must seed
    state['portfolio'] explicitly, never rely on a silent fall-back empty.
    """
    with pytest.raises(ValueError, match=r"state\[.portfolio.\] missing"):
        Portfolio.from_state_value(None)


# ---------------------------------------------------------------------------
# Test 4: malformed dict raises ValueError with a clear "malformed" message
# ---------------------------------------------------------------------------

def test_from_state_value_raises_on_malformed_dict():
    """A dict with invalid field types must raise ValueError mentioning 'malformed'.

    Silently returning an empty portfolio for corrupt state would mask
    data-loss bugs (audit findings A-014, A-071).
    """
    bad_dict = {"cash": "not-a-number", "positions": []}

    with pytest.raises(ValueError, match=r"state\[.portfolio.\] malformed"):
        Portfolio.from_state_value(bad_dict)


# ---------------------------------------------------------------------------
# Test 5: wrong type (e.g. bare int) raises TypeError with "unexpected type"
# ---------------------------------------------------------------------------

def test_from_state_value_raises_on_wrong_type():
    """Any non-Portfolio, non-dict, non-None value must raise TypeError.

    Provides a clear error message rather than a confusing AttributeError
    if some upstream code accidentally writes the wrong type into state.
    """
    with pytest.raises(TypeError, match=r"state\[.portfolio.\] unexpected type"):
        Portfolio.from_state_value(42)  # type: ignore[arg-type]
