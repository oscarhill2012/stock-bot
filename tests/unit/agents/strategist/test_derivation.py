"""derive_decision_fields tests — Tier 1, no LLM.

Covers the iter-3 three-verb (buy / sell / update) derivation paths:
  - buy stance → target_weights
  - sell (full)    → target_weights (0.0); rationale on stance
  - sell (partial) → target_weights reduced; rationale on stance
  - update stance  → target_weights unchanged (carry-forward)
  - held ticker omitted → carry-forward (implicit hold)

A-013 tail note: ``sell_reasons`` and ``update_reasons`` were removed from
``DerivedFields``.  The rationale for a sell or update stance lives directly
on the ``TickerStance.rationale`` field; tests below access it there.

NOTE: The pre-iter-3 tests that used the old verb set
(open / add / trim / close / hold) were deleted in the iter-3 sweep —
those verbs are now rejected at the schema level.
"""
from __future__ import annotations

import pytest

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


# ── Three-verb schema tests (iter-3 Task 4) ───────────────────────────────────
# These tests exercise the rewritten Pass 1 dispatch for the canonical
# buy / sell / update vocabulary.  The TickContext constructor is used in
# its simplified form (no tick_id / decision_tag / now required) to keep
# the tests focused on dispatch logic.


def test_derivation_dispatches_buy_to_target_weight():
    """A buy stance writes the delta into target_weights additively."""
    from agents.strategist.derivation import derive_decision_fields
    from agents.strategist.stance_schema import TickerStance
    from agents.strategist.derivation import TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers=set(),
        current_weights={"AAPL": 0.0, "MSFT": 0.0},
    )
    stances = [TickerStance(ticker="AAPL", intent="buy", weight=0.03, rationale="iPhone launch catalyst")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.03

    # Confirm AAPL is not recorded in decision_tags as a sell (it's a buy/entry).
    assert derived.decision_tags.get("AAPL") == "entry"


def test_derivation_dispatches_sell_full_close():
    """A sell stance with no weight is a full close — target_weight = 0.

    Rationale is preserved on the TickerStance itself (A-013 tail —
    sell_reasons dict removed).
    """
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="sell", rationale="thesis invalidated")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.0

    # The rationale is accessible from the stance directly, not a derived dict.
    assert stances[0].rationale == "thesis invalidated"


def test_derivation_dispatches_sell_partial():
    """A sell stance with weight=0.03 reduces current weight by 0.03.

    Rationale is preserved on the TickerStance itself (A-013 tail —
    sell_reasons dict removed).
    """
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="sell", weight=0.03, rationale="trimming on overbought")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.05

    # The rationale is accessible from the stance directly, not a derived dict.
    assert stances[0].rationale == "trimming on overbought"


def test_derivation_update_does_not_change_weight():
    """An update stance carries forward the current weight unchanged."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="update", rationale="revising AI catalyst timeline downward but still holding")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.08

    # Update does not cause an exit tag.
    assert derived.decision_tags.get("AAPL") == "hold"


def test_derivation_held_omission_carries_weight_forward():
    """A held ticker with no stance keeps its current weight (implicit hold)."""
    from agents.strategist.derivation import derive_decision_fields, TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers={"AAPL", "MSFT"},
        current_weights={"AAPL": 0.05, "MSFT": 0.07},
    )
    derived = derive_decision_fields([], ctx)
    assert derived.target_weights["AAPL"] == 0.05
    assert derived.target_weights["MSFT"] == 0.07
