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
    """Output spec must describe ONE verdict per call, not a list."""

    instruction = build_news_instruction(_vocab())

    # Output schema directive — single TickerVerdict, not a batch.
    assert "Output ONE JSON object" in instruction or \
           "Emit one verdict" in instruction or \
           "single verdict" in instruction.lower()


def test_instruction_honours_output_caps_from_config():
    """`config/analysts.json::output_caps.verdict_rationale_max_chars`
    must still be substituted into the rendered instruction — the per-
    ticker rewrite must NOT bypass the config-driven character cap that
    bounds each analyst's free-text output.
    """

    from config.analysts import get_analysts_config

    instruction = build_news_instruction(_vocab())

    # H4 (Spec A): the prompt now carries the *derived* prompt budget
    # (verdict_rationale_prompt_budget = max_chars − headroom), not the raw
    # schema cap (verdict_rationale_max_chars).  The config path is still
    # exercised — we just assert the right derived value.
    cap = get_analysts_config().output_caps.verdict_rationale_prompt_budget

    # The derived budget value should appear in the prompt (the template
    # writes "≤{rationale_max} chars" — `str.format` substitutes the int).
    assert f"≤{cap} chars" in instruction or f"{cap} chars" in instruction, (
        f"rendered prompt does not contain configured rationale prompt budget {cap}; "
        "the per-ticker rewrite must preserve the config/analysts.json "
        "output_caps substitution path (see Phase 9 spec — config control "
        "of analyst output budgets is an invariant)."
    )
