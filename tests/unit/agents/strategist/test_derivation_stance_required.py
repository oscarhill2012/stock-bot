"""Chunk 5 — D3 derivation tests for the held-stance post-condition.

Spec B removes the carry-forward block that padded ``target_weights`` for
un-emitted *held* tickers and replaces it with an explicit post-condition:
every pre-tick held ticker MUST have a matching stance in the strategist's
output.

Carry-forward for *flat* watchlist tickers (the active-stances model) stays
in place — flat tickers carry no implicit commitment, so omitting them remains
legal.

This module pins the two halves of the new contract:
  * D3-violation case — a held ticker with no stance raises
    ``StrategistContractViolation``.
  * D3-compliant case — a flat ticker with no stance is OK.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    *,
    current_weights: dict[str, float],
    watchlist:       list[str],
) -> TickContext:
    """Build a TickContext fixture with sensible defaults.

    Parameters
    ----------
    current_weights:
        Mapping of ticker → current portfolio weight (pre-tick).
    watchlist:
        Full watchlist of tracked tickers for this tick.

    Returns
    -------
    TickContext
        A TickContext seeded with fixed tick metadata for deterministic tests.
    """

    return TickContext(
        tick_id          = "tick_005",
        decision_tag     = "afternoon_sweep",
        now              = datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        current_weights  = current_weights,
        watchlist        = watchlist,
    )


def test_held_ticker_without_stance_raises_validation_error() -> None:
    """A pre-tick held ticker with no matching stance must raise.

    AVGO is held at 0.05; the strategist emits a stance only for MSFT
    (a flat watchlist ticker).  Derivation must refuse — silent
    carry-forward is no longer permitted on held positions.
    """

    stances = [
        TickerStance(
            # MSFT is flat; the stance merely establishes that derivation ran
            # far enough to reach Pass 1.5 and encounter the uncovered held ticker.
            ticker  = "MSFT",
            intent  = "open",
            weight  = 0.03,
            rationale    = "Open on bullish technical setup",
            horizon      = "swing",
            target_price = 450.0,
            stop_price   = 380.0,
        ),
    ]

    with pytest.raises(StrategistContractViolation) as excinfo:
        derive_decision_fields(
            stances,
            _ctx(
                current_weights = {"AVGO": 0.05},
                watchlist       = ["AVGO", "MSFT", "XOM"],
            ),
        )

    # The error message must name the violated ticker so the LLM-facing
    # log is debuggable.  Assert on the specific message format to pin the
    # contract more precisely than a substring match.
    assert "AVGO" in str(excinfo.value)
    assert "Held position(s)" in str(excinfo.value)


def test_flat_ticker_without_stance_is_ok() -> None:
    """Omitting a flat watchlist ticker is the active-stances model — legal.

    AVGO is held and has a stance; MSFT and XOM are flat and have no
    stance.  Derivation must succeed and pad target_weights for the
    flat tickers with their current weight (0.0).
    """

    stances = [
        TickerStance(
            # AVGO is held — the strategist reviews it and decides to hold:
            # no weight change, thesis intact.  ``intent="hold"`` with
            # ``reason`` satisfies the verb-conditional validator.
            intent       = "hold",
            ticker       = "AVGO",
            reason       = "No new evidence; commitments unchanged.",
        ),
    ]

    derived = derive_decision_fields(
        stances,
        _ctx(
            current_weights = {"AVGO": 0.05},
            watchlist       = ["AVGO", "MSFT", "XOM"],
        ),
    )

    # Held ticker — its emitted weight falls back to 0.0 (hold carries no weight).
    assert derived.target_weights["AVGO"] == 0.0
    # Flat tickers — carry-forward pads to 0.0 (their current weight).
    assert derived.target_weights["MSFT"] == 0.0
    assert derived.target_weights["XOM"]  == 0.0
