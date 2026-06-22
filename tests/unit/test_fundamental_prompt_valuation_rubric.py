"""Fundamental prompt — valuation/expectations rubric regression guards.

Iter-5 backtest showed the fundamental analyst ignored the COMPANY RATIOS
block entirely: not one verdict referenced valuation, and the model
repeatedly confused "good company" with "good stock" (e.g. a bullish call on
a megacap trading at a stretched multiple, with no expectations check).  The
prompt described the ratios block but the analytical framework never told the
model to *use* it.

These tests pin the load-bearing semantic markers of the added
valuation-anchor step, the trade-horizon line, the tightened confidence
ladder, and the falsifiable flip-condition requirement.  They are written
against ideas, not exact wording — minor editorial changes should not break
them.
"""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    """Match the construction used by the other fundamental-prompt tests."""
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_valuation_anchor_step_present() -> None:
    """The expectations-anchor step must teach the model to read the ratios
    block and treat the lean as the gap between evidence and what is priced."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    assert "Anchor on EXPECTATIONS first" in rendered
    # Uses the valuation multiples it is given.
    assert "how much growth is already priced" in rendered
    # The central idea: lean = gap between evidence and what's priced.
    assert "the GAP between" in rendered
    # Relative, not fixed-threshold, valuation judgement (sidesteps the
    # sector-blind hardcoded-PE pitfall).  Phase 14: now explicitly
    # sector-relative since the sector field is populated.
    assert "RELATIVE to the company's own history" in rendered
    assert "sector" in rendered


def test_prompt_does_not_anchor_on_unavailable_forward_fields() -> None:
    """Phase 14 realignment: forward P/E, PEG and analyst ratings are 100%
    null in the data feed, so the prompt must NOT instruct the model to anchor
    on or fall back to them — that directs the model at missing data and
    invites hallucinated numbers.

    The old prompt said "anchor on forward P/E instead of trailing P/E" when
    the trailing multiple looked distorted; that anchor never exists.  Pin the
    removal of that instruction so a future edit cannot silently reintroduce
    the dependency on absent fields.
    """

    rendered = build_fundamental_instruction(vocab=_vocab())

    # The forward-P/E anchor instruction must be gone.
    assert "anchor on forward P/E" not in rendered
    # We must not tell the model to fall back to a forward multiple.
    assert "forward P/E instead of trailing" not in rendered
    # The model must be told NOT to invent a numeric forward estimate.
    assert "NEVER invent a" in rendered or "do not infer them" in rendered


def test_prompt_anchors_on_available_fields() -> None:
    """The re-anchored valuation method must lean on fields that actually
    exist: beta as a risk/volatility lens, the sector for sector-relative
    valuation, and a QUALITATIVE forward outlook from the filings prose (since
    there is no numeric forward multiple)."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Beta is framed as a risk/volatility lens, not a directional signal.
    assert "risk/volatility lens" in rendered
    # The forward view comes from the qualitative prose, not a numeric estimate.
    assert "QUALITATIVE outlook" in rendered


def test_prompt_includes_sparse_input_humility() -> None:
    """Thin-evidence cells must be nudged toward lower confidence / neutral
    rather than a confident directional lean — a prompt-level guard against
    manufacturing signal from scant input."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    assert "Sparse input" in rendered
    assert "lean toward NEUTRAL" in rendered


def test_trade_horizon_present() -> None:
    """The lean must be anchored to a days-to-weeks horizon, not an intraday
    one — fundamentals do not move price within a session."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    assert "structural re-rating view" in rendered


def test_confidence_ladder_present() -> None:
    """Confidence is the probability the lean is right, NOT conviction about
    the company; well-known facts already in the price earn low confidence."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Preserve the existing separate-from-lean guard.
    assert "Calibrate confidence separately from lean" in rendered
    # The new behavioural anchor.
    assert "NOT how sure you are" in rendered
    assert "earn high confidence" in rendered


def test_falsifiable_flip_condition_present() -> None:
    """The summary must end with a concrete, named flip condition so the
    verdict is falsifiable rather than vague."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    assert "would flip your lean" in rendered
