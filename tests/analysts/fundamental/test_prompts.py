"""Tests for the single-ticker Fundamental prompt template.

Verifies that ``build_fundamental_instruction`` produces a prompt that:

- Addresses a SINGLE ticker per call rather than "each ticker in the batch".
- Describes ONE JSON object output, not a batch array.
- Preserves the prose-cap substitutions from ``config/analysts.json``
  (``report_summary_max_chars`` / ``report_driver_body_max_chars``) — config
  still controls LLM output budgets; only the surface they bind to has
  moved from ``rationale`` to ``report``.
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
    """Output contract must describe ONE verdict per call with the required fields.

    Mirrors the news prompt test — see ``tests/analysts/news/test_prompts.py``
    for the full rationale.  ``is_no_data`` and ``report`` are now REQUIRED
    on every emit; the contract block in the prompt is the LLM-facing mirror
    of ``LlmTickerVerdict``.
    """

    instruction = build_fundamental_instruction(_vocab())

    assert "OUTPUT CONTRACT" in instruction
    assert "REQUIRED"        in instruction
    assert "is_no_data"      in instruction
    assert "report"          in instruction


def test_instruction_honours_output_caps_from_config():
    """Prose-only caps from ``config/analysts.json::output_caps`` must still
    be substituted into the rendered instruction — mirror of the news test.

    After the 2026-05-25 schema split the prose budget moved from the
    ``rationale`` field to ``AnalystReport.summary`` + per-driver bodies;
    both are bound from config and must reach the rendered prompt or the
    config-driven budget contract is silently broken.
    """

    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())

    out_caps = get_analysts_config().output_caps

    assert str(out_caps.report_summary_max_chars) in instruction, (
        "rendered prompt does not contain the configured "
        "report_summary_max_chars value — the output_caps substitution path "
        "is broken in build_fundamental_instruction()."
    )

    assert str(out_caps.report_driver_body_max_chars) in instruction, (
        "rendered prompt does not contain the configured "
        "report_driver_body_max_chars value — the output_caps substitution "
        "path is broken in build_fundamental_instruction()."
    )
