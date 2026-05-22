"""D1.2 — news prompt requires `report` whenever `is_no_data=false`.

The prompt previously said ``omit only when is_no_data=true``; the LLM
violated the instruction at 30.7 % across the baseline-2025-09 run.
D1.1 closes the loophole at the schema; D1.2 strengthens the wording
the LLM sees so the prompt and the schema sing in unison.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_report_required_wording_present() -> None:
    """The strengthened wording must appear in the rendered prompt."""

    rendered = build_news_instruction(_vocab())
    assert "REQUIRED whenever is_no_data=false" in rendered
    assert "Omit ONLY when" in rendered
    assert "summary plus 2 drivers" in rendered


def test_legacy_omit_only_wording_absent() -> None:
    """The old softer wording must not coexist with the new hard rule."""

    rendered = build_news_instruction(_vocab())
    assert "omit only when is_no_data=true" not in rendered
