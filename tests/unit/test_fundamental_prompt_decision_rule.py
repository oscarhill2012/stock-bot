"""D2.1 — fundamental decision rule rewritten with neutral anchors.

The previous triple-AND-conjunction bullish trigger was structurally
unreachable for mega-cap watchlists, producing 0 bullish across 920
verdicts.  The replacement is anchor-based: routine 10b5-1 sales are
NEUTRAL not bearish, absence of activity is neutral, going-concern
language overrides, conflicting inputs land neutral.
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


def test_new_anchors_present() -> None:
    """The four neutral anchors must appear in the rendered prompt."""

    rendered = build_fundamental_instruction(vocab=_vocab())

    # Routine 10b5-1 = neutral
    assert "Routine 10b5-1" in rendered
    assert "NOT bearish" in rendered

    # Absence = neutral
    assert "Absence of insider activity is neutral" in rendered

    # Going-concern override
    assert "Going-concern language present" in rendered

    # Conflicting → neutral low conf
    assert "Conflicting inputs" in rendered


def test_old_and_conjunction_absent() -> None:
    """The structurally-unreachable AND-conjunction must be gone."""

    rendered = build_fundamental_instruction(vocab=_vocab())
    assert "cluster open-market buys" not in rendered
    assert "raised guidance" not in rendered or "Routine 10b5-1" in rendered

    # Combined assertion guards against the *AND-conjunction phrasing* specifically.
    assert "strongly bullish" not in rendered.lower()
