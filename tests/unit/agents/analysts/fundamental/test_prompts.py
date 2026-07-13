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


def test_instruction_carries_filing_delta_horizon():
    """The prompt must name horizon_days and the config-driven value (60).

    Phase 14: the emit schema requires horizon_days; the prompt is where the
    LLM learns WHAT to emit.  The value must come from config
    (fundamental.filing_delta_horizon_days), never be hardcoded in the
    template, so a config change re-tunes the horizon without a code edit.
    """
    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())

    horizon = get_analysts_config().fundamental.filing_delta_horizon_days

    assert "horizon_days" in instruction
    assert str(horizon) in instruction


def test_instruction_states_lazy_prices_sign_convention():
    """The diff-oriented sign convention must be stated, both branches.

    Substantive year-over-year change → bearish by default; a performed diff
    that found essentially nothing → quiet-bullish.  Greppable phrases pin
    the doctrine so a future prompt edit cannot silently drop it.
    """
    instruction = build_fundamental_instruction(_vocab())

    assert "BEARISH by default" in instruction
    assert "quiet-bullish" in instruction


def test_instruction_forbids_reading_markers_as_no_change():
    """NO-COMPARISON markers must be excluded from the quiet-bullish branch.

    An incorporated-by-reference stub (XOM 10-K) or a missing prior-year pair
    renders a marker, not a diff.  The prompt must tell the LLM that markers
    mean 'comparison unavailable' — treating them as 'nothing changed' would
    manufacture quiet-bullish leans from data gaps.
    """
    instruction = build_fundamental_instruction(_vocab())

    assert "no prior-year pair" in instruction
    assert "too short to diff" in instruction
    assert "NOT evidence of stability" in instruction


def test_prompt_describes_scale_and_diff_direction():
    """The rendered prompt must teach magnitude-from-scale, direction-from-diff,
    and must NOT carry the stale volume heuristic.

    Task 8 (Phase 14 filing-similarity rework): with similarity-based dedup,
    "how much text survived the diff" is a threshold artefact, not a proxy
    for how heavily a filing was rewritten — so the old VOLUME heuristic
    must be gone, replaced by guidance to read magnitude off the new
    self-relative "scale:" line (Task 7) and direction off the diff content
    and NUMERIC DELTAS block (Task 3).
    """
    instr = build_fundamental_instruction(_vocab())

    assert "scale:" in instr
    assert "NUMERIC DELTAS" in instr
    # The old survival=rewrite heuristic must be gone (survival no longer proxies
    # rewriting under similarity dedup).
    assert "Heavy survival across sections" not in instr
    assert "heavily rewritten filing = stronger bearish prior" not in instr
