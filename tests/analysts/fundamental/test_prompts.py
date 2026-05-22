"""Tests for the single-ticker Fundamental prompt template.

Verifies that ``build_fundamental_instruction`` produces a prompt that:

- Addresses a SINGLE ticker per call rather than "each ticker in the batch".
- Describes ONE JSON object output, not a batch array.
- Preserves the ``output_caps.verdict_rationale_max_chars`` substitution from
  ``config/analysts.json`` (Phase 9 invariant — config controls LLM output
  budgets, not hard-coded values in the template).
"""
from __future__ import annotations

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    """Build a small valid FundamentalVocabulary for prompt-rendering tests.

    Populated from the field names defined in
    ``agents.analysts.heuristics.FundamentalVocabulary`` and the realistic
    values in ``config/analyst_heuristics.json``.  The model rejects missing
    or empty fields, so every list must contain at least one entry.

    Returns
    -------
    FundamentalVocabulary
        A minimal but valid vocabulary instance suitable for rendering tests.
    """
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=[
            "regulatory",
            "litigation",
            "macro",
            "going_concern",
        ],
        insider_signals=[
            "cluster_buying",
            "cluster_selling",
            "planned_sale_dominant",
            "discretionary_sale_dominant",
            "option_exercise_hold",
            "option_exercise_dump",
            "mixed",
        ],
    )


def test_instruction_addresses_single_ticker():
    """The rendered instruction must address ONE ticker, not 'each ticker'."""

    instruction = build_fundamental_instruction(_vocab())

    # Batch phrasing must be gone.
    assert "each ticker" not in instruction.lower()
    assert "the batch" not in instruction.lower()
    assert "MUST cover ALL tickers" not in instruction

    # Runtime placeholders for a single-ticker branch.
    assert "{ticker}" in instruction
    assert "{fundamental_context}" in instruction


def test_instruction_describes_single_verdict_output():
    """Output spec must describe ONE verdict per call, not a batch array."""

    instruction = build_fundamental_instruction(_vocab())

    assert "Output ONE JSON object" in instruction or \
           "single verdict" in instruction.lower()


def test_instruction_honours_output_caps_from_config():
    """`config/analysts.json::output_caps.verdict_rationale_max_chars`
    must still be substituted into the rendered instruction — the per-
    ticker rewrite must NOT bypass the config-driven character cap that
    bounds each analyst's free-text output.
    """

    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())

    # H4 (Spec A): the prompt now carries the *derived* prompt budget
    # (verdict_rationale_prompt_budget = max_chars − headroom), not the raw
    # schema cap (verdict_rationale_max_chars).  The config path is still
    # exercised — we just assert the right derived value.
    cap = get_analysts_config().output_caps.verdict_rationale_prompt_budget

    assert f"≤{cap} chars" in instruction or f"{cap} chars" in instruction, (
        f"rendered prompt does not contain configured rationale prompt budget {cap}; "
        "the per-ticker rewrite must preserve the config/analysts.json "
        "output_caps substitution path (see Phase 9 spec — config control "
        "of analyst output budgets is an invariant)."
    )
