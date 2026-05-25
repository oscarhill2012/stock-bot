"""Fundamental prompt requires ``report`` on every emit.

Symmetric companion to the news report-required test — see that file for
the full history.  Fundamental's missing-report rate on baseline-2025-09
was lower (3.6 %) but the same loophole; the 2026-05-25 schema split
closes it at the schema (``LlmTickerVerdict``) and at the prompt
(unconditional REQUIRED) simultaneously.
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
    """The unconditional-required wording must appear in the rendered prompt."""

    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "REQUIRED on every call"      in rendered
    assert "including when is_no_data=true" in rendered


def test_legacy_conditional_wording_absent() -> None:
    """The previous softer wordings must not coexist with the new hard rule."""

    rendered = build_fundamental_instruction(vocab=_fundamental_vocab())
    assert "omit only when is_no_data=true"    not in rendered
    assert "REQUIRED whenever is_no_data=false" not in rendered
