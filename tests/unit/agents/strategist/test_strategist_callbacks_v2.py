"""Strategist v2 before/after callback tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.strategist.agent import _strategist_validation_callback
from agents.strategist.derivation import StrategistContractViolation
from agents.strategist.schema import PositionThesis, StrategistDecision
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
    held, so omitting it must now raise.  The fix is to supply an explicit hold
    stance for MSFT alongside the AAPL open stance.  A flat-ticker omission
    (e.g. a third ticker with no portfolio weight) would still be permitted.
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
                    intent="open",
                    weight=0.05,
                    rationale="open",
                    horizon="swing",
                    target_price=210.0,
                    stop_price=185.0,
                ),
                # MSFT is held — must supply an explicit stance (Spec B / D3).
                # Using a hold stance to indicate "no change, thesis intact";
                # hold is weight-forbidden, so target_weights["MSFT"] → 0.0.
                TickerStance(
                    ticker="MSFT",
                    intent="hold",
                    reason="thesis intact, no new evidence",
                ),
            ],
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )

    # Must not raise — both held ticker (MSFT) and new open (AAPL) are covered.
    assert _strategist_validation_callback(_Ctx(state)) is None

    # target_weights reflects the derivation output: open writes weight,
    # hold writes 0.0 (no weight on hold).
    decided = state["strategist_decision"]
    target_weights = decided["target_weights"]
    assert target_weights["AAPL"] == 0.05          # new open
    assert target_weights["MSFT"] == 0.0           # hold carries no weight


def test_after_held_ticker_omission_raises_contract_violation():
    """Omitting a held ticker raises StrategistContractViolation (Spec B / D3).

    This is the negative case for ``test_after_requires_explicit_stance_for_held_tickers``
    — confirms the callback surfaces the violation before the omission propagates
    downstream.
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
                    intent="open",
                    weight=0.05,
                    rationale="open",
                    horizon="swing",
                    target_price=210.0,
                    stop_price=185.0,
                ),
                # MSFT intentionally omitted — must raise per Spec B / D3.
            ],
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )

    with pytest.raises(StrategistContractViolation) as excinfo:
        _strategist_validation_callback(_Ctx(state))

    # The error must name the uncovered held ticker.
    assert "MSFT" in str(excinfo.value)
    assert "Held position(s)" in str(excinfo.value)


def test_after_raises_on_extras():
    """Off-watchlist tickers in the decision abort the tick."""
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="t",
        strategist_decision=StrategistDecision(
            stances=[
                TickerStance(ticker="AAPL", intent="hold", reason="test hold"),
                TickerStance(
                    ticker="GOOG",
                    intent="open",
                    weight=0.05,
                    rationale="open",
                    horizon="swing",
                    target_price=200.0,
                    stop_price=170.0,
                ),
            ],
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
    )
    with pytest.raises(StrategistContractViolation, match="GOOG"):
        _strategist_validation_callback(_Ctx(state))


def test_nonzero_stance_without_lifecycle_fields_fails_at_schema():
    """An open stance missing horizon/target_price/stop_price fails at the
    schema level — it never reaches the after-callback.

    The ``_require_intent_fields`` validator on ``TickerStance`` enforces
    this so a malformed LLM response fails ADK's ``output_schema`` parse
    loudly instead of silently degrading.  This test pins that contract.
    """
    with pytest.raises(ValidationError) as excinfo:
        TickerStance(
            ticker="AAPL",
            intent="open",
            weight=0.05,
            rationale="open",
            # horizon, target_price, stop_price intentionally absent
        )
    msg = str(excinfo.value)
    assert "AAPL" in msg
    assert "horizon" in msg
    assert "target_price" in msg
    assert "stop_price" in msg


def test_after_raises_on_close_without_close_reason():
    """Full closes missing reason abort the tick.

    ``TickerStance`` enforces ``reason`` at the schema level for close stances,
    so this test bypasses Pydantic validation via ``model_construct`` to
    simulate a stale or in-flight payload that slips through with ``reason=None``.
    The derivation layer must catch it and raise ``StrategistContractViolation``
    before the tick propagates downstream.
    """
    thesis = PositionThesis(
        ticker="AAPL", opened_at=datetime.now(tz=UTC),
        opened_price=192.40, opened_tag="x", rationale="x", horizon="swing",
        last_reviewed_at=datetime.now(tz=UTC),
    )
    # Build a close stance with no reason, bypassing schema validation.
    # ``model_construct`` sets fields without running validators — this
    # simulates a payload that somehow reached derivation without a reason.
    bad_stance = TickerStance.model_construct(
        ticker="AAPL",
        intent="close",
        reason=None,
    )
    decision = StrategistDecision.model_construct(
        stances=[bad_stance],
        target_weights={},
        decision_tag="x",
        reasoning="x",
        thesis="y",
        confidence=0.5,
        close_reasons={},
        trim_reasons={},
    )
    state = _State(
        tickers=["AAPL"],
        positions={"AAPL": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"AAPL": (10.0, 192.40, 198.50)}).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=decision,  # already a model instance — not a dict
    )
    with pytest.raises(StrategistContractViolation, match="reason"):
        _strategist_validation_callback(_Ctx(state))


def test_after_raises_on_trim_without_trim_reason():
    """Trims missing reason abort the tick.

    ``TickerStance`` enforces ``reason`` at the schema level for trim stances,
    so this test bypasses Pydantic validation via ``model_construct`` to simulate
    a payload that reaches derivation with ``reason=None``.  The derivation layer
    must raise ``StrategistContractViolation`` before the tick propagates.
    """
    thesis = PositionThesis(
        ticker="MSFT", opened_at=datetime.now(tz=UTC),
        opened_price=410.0, opened_tag="x", rationale="x", horizon="swing",
        last_reviewed_at=datetime.now(tz=UTC),
    )
    # Build a trim stance with no reason, bypassing schema validation.
    bad_stance = TickerStance.model_construct(
        ticker="MSFT",
        intent="trim",
        weight=0.30,
        reason=None,
    )
    decision = StrategistDecision.model_construct(
        stances=[bad_stance],
        target_weights={},
        decision_tag="x",
        reasoning="x",
        thesis="y",
        confidence=0.5,
        close_reasons={},
        trim_reasons={},
    )
    state = _State(
        tickers=["MSFT"],
        positions={"MSFT": thesis.model_dump(mode="json")},
        portfolio=_portfolio({"MSFT": (10.0, 410.0, 415.0)}, cash=500).model_dump(mode="json"),
        tick_id="t",
        strategist_decision=decision,  # already a model instance — not a dict
    )
    with pytest.raises(StrategistContractViolation, match="trim_reason|reason"):
        _strategist_validation_callback(_Ctx(state))


def test_after_derives_decision_fields_on_valid_input():
    """The validation callback derives target_weights / close_reasons / trim_reasons from stances.

    ``new_positions`` is no longer derived by the strategist callback;
    that assembly was moved to the executor's BUY-path where the real fill
    price is known.  We assert only on target_weights and the reason dicts.
    """
    state = _State(
        tickers=["AAPL"],
        positions={}, portfolio=_portfolio().model_dump(mode="json"), tick_id="tick_X",
        strategist_decision=StrategistDecision(
            stances=[TickerStance(
                ticker="AAPL",
                intent="open",
                weight=0.05,
                rationale="open",
                horizon="swing",
                target_price=210.0,
                stop_price=185.0,
            )],
            decision_tag="open_aapl", reasoning="x", thesis="y", confidence=0.7,
        ).model_dump(mode="json"),
    )
    out = _strategist_validation_callback(_Ctx(state))
    assert out is None
    decided = state["strategist_decision"]
    assert decided["target_weights"] == {"AAPL": 0.05}
    # new_positions is no longer a field on StrategistDecision; the
    # executor assembles the PositionThesis from the fill price + stance.
    assert "new_positions" not in decided
    assert decided["close_reasons"] == {}
    assert decided["trim_reasons"] == {}
