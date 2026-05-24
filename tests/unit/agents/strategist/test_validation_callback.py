# tests/unit/agents/strategist/test_validation_callback.py
"""Test that the strategist validation callback passes the retry counter
to emit_analyst_summary.

The _Ctx shim is copied verbatim from test_strategist_callbacks_v2.py —
a hand-built stand-in for CallbackContext that carries a plain state dict.
The callback only ever reads ``callback_context.state``, so the shim is
sufficient for unit-level testing without importing any ADK internals.
"""
from __future__ import annotations

import pytest

from agents.strategist.agent import _strategist_validation_callback
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio


# ---------------------------------------------------------------------------
# Minimal CallbackContext shim — matches the shim in test_strategist_callbacks_v2.py
# ---------------------------------------------------------------------------

class _State(dict):
    """Thin dict subclass so isinstance checks pass if needed."""


class _Ctx:
    """Minimal CallbackContext shim: the callback only reads .state."""

    def __init__(self, state: dict):
        self.state = state


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _portfolio(cash: float = 1000.0) -> Portfolio:
    """Return an empty portfolio with no positions."""
    return Portfolio(cash=cash)


def _valid_decision() -> dict:
    """Return a minimal valid StrategistDecision dict (open AAPL, flat MSFT omitted).

    The watchlist is ['AAPL'] so we need a stance for AAPL only.
    preferred_weight > 0 requires the lifecycle hint fields.
    """
    return StrategistDecision(
        stances=[
            TickerStance(
                ticker          = "AAPL",
                preferred_weight = 0.05,
                conviction      = 0.7,
                rationale       = "open",
                horizon         = "swing",
                target_price    = 210.0,
                stop_price      = 185.0,
            ),
        ],
        decision_tag = "open_aapl",
        reasoning    = "Test",
        thesis       = "Test thesis",
        confidence   = 0.7,
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_strategist_validation_callback_passes_retries(monkeypatch) -> None:
    """When STOCKBOT_TERMINAL_LOG=1 the callback passes the retry counter
    from ``temp:_obs_strategist_retries`` to ``emit_analyst_summary``.

    The callback is invoked via the _Ctx shim (no real CallbackContext
    needed).  ``emit_analyst_summary`` is monkeypatched so we can inspect
    the ``retries=`` kwarg without producing terminal output.
    """
    captured: list[dict] = []

    def _fake_emit(analyst_label: str, *, calls, ticker_count, retries=None) -> None:
        """Capture the call kwargs for assertion; suppress log output."""
        captured.append({
            "analyst_label": analyst_label,
            "calls":         calls,
            "ticker_count":  ticker_count,
            "retries":       retries,
        })

    monkeypatch.setenv("STOCKBOT_TERMINAL_LOG", "1")
    monkeypatch.setattr(
        "agents.strategist.agent.emit_analyst_summary",
        _fake_emit,
    )

    state = _State(
        tickers              = ["AAPL"],
        positions            = {},
        portfolio            = _portfolio().model_dump(mode="json"),
        tick_id              = "t-retry",
        strategist_decision  = _valid_decision(),
        # Retry counter: one schema-validation retry fired.
        **{"temp:_obs_strategist_calls": [
            {"ticker": "strategist", "elapsed": 2.1, "prompt_tokens": 8000,
             "candidate_tokens": 400, "ok": True},
        ]},
        **{"temp:_obs_strategist_retries": {"schema": 1}},
    )

    result = _strategist_validation_callback(_Ctx(state))

    # Callback must return None on success.
    assert result is None

    assert captured, "emit_analyst_summary was never called"
    call = captured[0]
    assert call["analyst_label"] == "strategist"
    assert call["retries"] == {"schema": 1}, (
        f"Expected retries={{'schema': 1}}; got retries={call['retries']}"
    )
