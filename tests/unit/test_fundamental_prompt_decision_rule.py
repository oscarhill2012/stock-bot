"""Fundamental decision rule — analytical-framework prompt regression guards.

History.  The original D2.1 anchor list was a lookup table ("if X then
bullish, if Y then bearish").  Iter-4 backtest showed the anchors were
read but produced 0.2% bullish leans — the anchors described edge
cases (cluster buys, raised guidance, removed risks) that rarely
appear in mega-cap evidence, while bearish anchors triggered on common
phenomena.  The replacement is an analytical framework that teaches
the model HOW to read each signal source rather than what to map.

These tests pin the *load-bearing semantic markers* of the new
framework so a future edit can't quietly remove them.  They are
deliberately written against ideas, not exact wording — small editorial
changes should not break them.
"""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    """Match the construction used by tests/unit/test_fundamental_prompt_render.py."""
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_framework_sections_present() -> None:
    """The four analytical-framework sections must appear in the rendered prompt."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Section header — locates the framework block.
    assert "How to analyse the evidence" in rendered

    # Section 1 — MD&A tone (verb strength, hedge density).
    assert "MD&A tone" in rendered
    assert "Commitment strength" in rendered
    assert "Hedge density" in rendered

    # Section 2 — insider asymmetry (the load-bearing idea).
    assert "asymmetry is the signal" in rendered
    assert "Routine 10b5-1" in rendered

    # Section 3 — risk-factor change reading.
    assert "boilerplate from new disclosure" in rendered

    # Section 4 — going-concern override.
    assert "going-concern" in rendered.lower()


def test_lean_calibration_present() -> None:
    """The "do not default to neutral" framing must appear — this is the
    behavioural fix the framework is intended to deliver."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Explicit do-not-default-to-neutral language.
    assert "do not default to neutral" in rendered.lower()

    # Confidence calibrated separately from lean — guards against the
    # "I'm not sure" → neutral collapse that iter-4 still showed.
    assert "Calibrate confidence separately from lean" in rendered


def test_old_lookup_table_phrasing_absent() -> None:
    """The structurally-unreachable AND-conjunction and the lookup-table
    framing it lived inside must be gone."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # The old AND-conjunction trigger.
    assert "cluster open-market buys" not in rendered

    # The old "anchors — reason from the evidence; this is not a
    # decision tree" framing the lookup table sat inside.
    assert "anchors —" not in rendered.lower()
    assert "decision tree" not in rendered.lower()
