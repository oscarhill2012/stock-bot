"""D1.2 — fundamental prompt requires ``report`` whenever ``is_no_data=false``.

Symmetric companion to the news D1.2 test.  Fundamental's missing-report
rate was lower (3.6 %) but the same loophole — closing it preserves
schema/prompt alignment.
"""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _fundamental_vocab() -> FundamentalVocabulary:
    """Match the construction used by tests/unit/test_fundamental_prompt_render.py."""
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_report_required_wording_present() -> None:
    """The rendered prompt must contain the strengthened D1.2 report-required wording."""
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "REQUIRED whenever is_no_data=false" in rendered
    assert "Omit ONLY when" in rendered
    assert "summary plus 2 drivers" in rendered


def test_legacy_omit_only_wording_absent() -> None:
    """The weaker legacy phrasing (omit only when is_no_data=true) must be removed."""
    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "omit only when is_no_data=true" not in rendered
