"""Tests for the single-ticker News prompt template."""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    """Build a small valid NewsVocabulary for prompt-rendering tests."""

    return NewsVocabulary(
        catalysts=["earnings", "guidance", "macro"],
        novelty=["new", "ongoing", "stale"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_instruction_addresses_single_ticker():
    """The rendered instruction must address ONE ticker, not 'each ticker'."""

    instruction = build_news_instruction(_vocab())

    # Single-ticker phrasing — must NOT mention "each ticker" or "batch".
    assert "each ticker" not in instruction.lower()
    assert "the batch" not in instruction.lower()
    assert "MUST cover ALL tickers" not in instruction

    # Must keep the runtime placeholders that ADK fills per branch.
    assert "{ticker}" in instruction
    assert "{news_context}" in instruction


def test_instruction_contains_closed_vocabulary():
    """Closed-vocab tokens must still substitute into the prompt."""

    instruction = build_news_instruction(_vocab())

    assert "earnings | guidance | macro" in instruction
    assert "new | ongoing | stale" in instruction
    assert "positive | negative | mixed | none" in instruction


def test_instruction_describes_single_verdict_output():
    """Output contract must describe ONE verdict per call with the required fields.

    The 2026-05-25 schema split rewrote the output spec around an explicit
    "OUTPUT CONTRACT" block that names ``is_no_data`` and ``report`` as
    REQUIRED on every emit — these were previously optional and the
    constrained decoder routinely omitted them.  The prose contract is the
    LLM-facing mirror of the ``LlmTickerVerdict`` Pydantic class; if either
    drifts from the other, the rule breaks silently.  Pin both halves.
    """

    instruction = build_news_instruction(_vocab())

    # The new contract header must be present so the LLM is steered toward
    # the required-fields branch rather than the old optional-fields branch.
    assert "OUTPUT CONTRACT" in instruction

    # ``is_no_data`` and ``report`` must be called out as REQUIRED — these
    # are the two fields the decoder was silently omitting.
    assert "REQUIRED" in instruction
    assert "is_no_data" in instruction
    assert "report"     in instruction


def test_instruction_honours_output_caps_from_config():
    """Prose-only caps from ``config/analysts.json::output_caps`` must still
    be substituted into the rendered instruction.

    After the 2026-05-25 schema split, ``rationale`` no longer appears on the
    LLM emit-schema (Vertex's constrained decoder treats ``maxLength`` as a
    fill target and was padding toward the cap).  The prose budget is now
    expressed via the ``AnalystReport`` summary + driver caps, both
    substituted from config so retuning either still flows through.  This
    test pins the substitution path — the values must reach the rendered
    prompt or the config-driven budget contract is silently broken.
    """

    from config.analysts import get_analysts_config

    instruction = build_news_instruction(_vocab())

    out_caps = get_analysts_config().output_caps

    # Summary cap is the dominant prose budget — its value must appear in
    # the rendered prompt (the template writes "{summary_max} characters").
    assert str(out_caps.report_summary_max_chars) in instruction, (
        "rendered prompt does not contain the configured "
        "report_summary_max_chars value — the output_caps substitution path "
        "is broken in build_news_instruction()."
    )

    # Driver body cap covers the per-driver prose budget — same contract.
    assert str(out_caps.report_driver_body_max_chars) in instruction, (
        "rendered prompt does not contain the configured "
        "report_driver_body_max_chars value — the output_caps substitution "
        "path is broken in build_news_instruction()."
    )
