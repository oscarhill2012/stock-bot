"""Strategist v2 before/after callback tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.strategist.agent import _strategist_validation_callback
from agents.strategist.derivation import StrategistContractViolation
from agents.strategist.position_thesis import PositionThesis
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position
from contract.evidence import AnalystEvidence, AnalystVerdict


class _State(dict):
    pass


class _Ctx:
    def __init__(self, state: dict):
        self.state = state


def _portfolio(holdings: dict | None = None, cash: float = 1000.0) -> Portfolio:
    """Build a Portfolio with optional ``{ticker: (qty, avg_cost, last_price)}`` holdings."""
    return Portfolio(
        cash=cash,
        positions={t: Position(quantity=q, avg_cost=ac, last_price=lp)
                   for t, (q, ac, lp) in (holdings or {}).items()},
    )


def _ev(analyst: str, lean: str = "neutral", conf: float = 0.0,
        ticker: str = "AAPL") -> AnalystEvidence:
    """Build a single AnalystEvidence row for the given analyst slot."""
    return AnalystEvidence(
        ticker=ticker, analyst=analyst,
        tick_id="t",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        features={}, feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean, magnitude=conf, confidence=conf,
            rationale="x", key_factors=[],
        ),
    )


# ── after callback: active-stances contract ──────────────────────────────────


def test_after_requires_explicit_stance_for_held_tickers():
    """Held tickers must have an explicit stance — omission now raises StrategistContractViolation.

    Pre-Spec-B, omitting a held ticker was read as an implicit hold (carry-forward).
    Spec B / D3 removes that: every pre-tick held ticker must receive an explicit
    stance on every tick.  The active-stances model survives only for flat
    watchlist tickers.

    This test was originally ``test_after_does_not_require_exhaustive_stances``
    (asserting that MSFT's omission was safe).  It is inverted here: MSFT IS
    held, so omitting it must now raise.  The fix is to supply an explicit
    update stance for MSFT alongside the AAPL buy stance.  A flat-ticker
    omission (e.g. a third ticker with no portfolio weight) would still be
    permitted.
    """

    state = _State(
        tickers=["AAPL", "MSFT"],
        positions={},
        portfolio=_portfolio({"MSFT": (5.0, 410.0, 420.0)}, cash=900.0).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(
                    ticker="AAPL",
                    intent="buy",
                    weight=0.05,
                    rationale="Strong FCF-driven thesis",
                ),
                # MSFT is held — must supply an explicit stance (Spec B / D3).
                # Using an update stance to indicate "no trade, thesis unchanged".
                TickerStance(
                    ticker="MSFT",
                    intent="update",
                    rationale="thesis intact, no new evidence",
                ),
            ],
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )

    # Must not raise — both held ticker (MSFT) and new buy (AAPL) are covered.
    assert _strategist_validation_callback(_Ctx(state)) is None

    # target_weights reflects the derivation output: buy writes weight,
    # update carries the current weight forward (0.0 when no prior weight).
    decided = state["strategist_decision"]
    target_weights = decided["target_weights"]
    assert target_weights["AAPL"] == 0.05          # new buy



def test_after_raises_on_extras():
    """Off-watchlist tickers in the decision abort the tick."""
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", intent="update", rationale="test update"),
                TickerStance(
                    ticker="GOOG",
                    intent="buy",
                    weight=0.05,
                    rationale="off-watchlist buy",
                ),
            ],
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    with pytest.raises(StrategistContractViolation, match="GOOG"):
        _strategist_validation_callback(_Ctx(state))


def test_buy_stance_missing_rationale_fails_at_schema():
    """A buy stance missing rationale fails at the schema level — it never
    reaches the after-callback.

    The ``_require_intent_fields`` validator on ``TickerStance`` enforces
    this so a malformed LLM response fails ADK's ``output_schema`` parse
    loudly instead of silently degrading.  This test pins that contract.
    """
    with pytest.raises(ValidationError) as excinfo:
        TickerStance(
            ticker="AAPL",
            intent="buy",
            weight=0.05,
            # rationale intentionally absent — must fail at schema level
        )
    msg = str(excinfo.value)
    assert "AAPL" in msg
    assert "rationale" in msg


def test_after_raises_on_sell_without_rationale():
    """Full sells (closes) missing reason abort the tick at schema re-validation.

    Post-7590ba1 the strategist callback re-validates the decision through
    ``StrategistLLMDecision`` (the narrow LLM-emit schema), which in turn
    runs ``TickerStance``'s verb-conditional validator.  A ``reason``-less
    sell stance fails there with a ``pydantic.ValidationError`` long
    before derivation runs — schema is now the source of truth.

    We bypass Pydantic via ``model_construct`` to simulate a payload that
    somehow reaches the callback with ``rationale=None``.
    """
    thesis = PositionThesis(
        ticker="AAPL", opened_at=datetime.now(tz=UTC),
        opened_tick_id="tick_001", opened_price=192.40, weight=0.05,
        rationale="x", last_reviewed_at=datetime.now(tz=UTC),
        last_reviewed_decision="buy",
    )
    # Build a sell stance with no reason, bypassing schema validation.
    # ``model_construct`` sets fields without running validators — this
    # simulates a payload that somehow reached derivation without a reason.
    bad_stance = TickerStance.model_construct(
        ticker="AAPL",
        intent="sell",
        rationale=None,
    )
    decision = StrategistDecision.model_construct(
        stances=[bad_stance],
        target_weights={},
        decision_tag="x",
        reasoning="x",
        thesis="y",
        confidence=0.5,
    )
    state = _State(
        tickers=["AAPL"],
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=decision,  # already a model instance — not a dict
    )
    with pytest.raises(ValidationError, match="rationale"):
        _strategist_validation_callback(_Ctx(state))


def test_after_raises_on_update_without_rationale():
    """Update stances missing reason abort the tick at schema re-validation.

    Same path as ``test_after_raises_on_sell_without_rationale``:
    ``TickerStance``'s verb-conditional validator rejects a ``reason``-less
    update with ``pydantic.ValidationError`` as soon as the callback
    re-validates the LLM payload.
    """
    thesis = PositionThesis(
        ticker="MSFT", opened_at=datetime.now(tz=UTC),
        opened_tick_id="tick_001", opened_price=410.0, weight=0.05,
        rationale="x", last_reviewed_at=datetime.now(tz=UTC),
        last_reviewed_decision="buy",
    )
    # Build an update stance with no reason, bypassing schema validation.
    bad_stance = TickerStance.model_construct(
        ticker="MSFT",
        intent="update",
        rationale=None,
    )
    decision = StrategistDecision.model_construct(
        stances=[bad_stance],
        target_weights={},
        decision_tag="x",
        reasoning="x",
        thesis="y",
        confidence=0.5,
    )
    state = _State(
        tickers=["MSFT"],
        positions={"MSFT": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"MSFT": (10.0, 410.0, 415.0)}, cash=500).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=decision,  # already a model instance — not a dict
    )
    with pytest.raises(ValidationError, match="rationale"):
        _strategist_validation_callback(_Ctx(state))


def test_after_derives_decision_fields_on_valid_input():
    """The validation callback derives target_weights from stances.

    ``new_positions``, ``sell_reasons``, and ``update_reasons`` are no longer
    derived fields — they were removed (A-013 tail collapse).  We assert only
    on ``target_weights`` and confirm the deleted keys are absent.
    """
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(
                ticker="AAPL",
                intent="buy",
                weight=0.05,
                rationale="Strong FCF-driven thesis",
            )],
            decision_tag="buy_aapl", reasoning="x", thesis="y", confidence=0.7,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is None
    decided = state["strategist_decision"]
    assert decided["target_weights"] == {"AAPL": 0.05}
    # Deleted fields must not appear in the enriched dump.
    assert "new_positions"  not in decided
    assert "sell_reasons"   not in decided
    assert "update_reasons" not in decided
