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
from agents.analysts.heuristics import FundamentalVocabulary, load_heuristics


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
    """The prompt must name the config-driven drift-window value (60) in its
    analytic prose, even though the LLM no longer self-reports it as an
    emitted field.

    Task 8 (Phase 14): ``horizon_days`` was removed from the emit schema
    (``LlmTickerVerdict``) and from the OUTPUT CONTRACT / SHAPE EXAMPLE — the
    horizon is now stamped onto the verdict in code, not self-reported by the
    LLM.  The analytic drift-window prose (sign convention + "how to
    analyse") still needs the config-driven trading-day figure so the LLM's
    reasoning about ITS OWN horizon stays in sync with config, never
    hardcoded in the template.
    """
    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())

    horizon = get_analysts_config().fundamental.filing_delta_horizon_days

    # The emit instruction is gone — the LLM is never told to output
    # ``horizon_days`` as a field.
    assert "horizon_days" not in instruction
    # But the numeric drift-window value itself is still woven into the
    # analytic prose (sign convention / "how to analyse" sections).
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


def test_fundamental_prompt_has_no_horizon_emit_instruction():
    """The fundamental prompt no longer tells the LLM to emit horizon_days."""
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.fundamental.prompts import build_fundamental_instruction

    instr = build_fundamental_instruction(load_heuristics().fundamental_vocabulary)

    # The emit field is gone from the OUTPUT CONTRACT and the shape example.
    assert "horizon_days  integer" not in instr
    assert '"horizon_days":' not in instr
    # But the analytic drift-window prose (calendar-day horizon) is retained.
    assert "days (~3 months)" in instr


def test_prompt_marker_prefix_matches_filing_diff_code():
    """The prompt's marker descriptions must match the literal prefix that
    ``filing_diff.py`` actually emits, not a stale synonym.

    Final-review finding (Task 8): the prompt previously described the diff
    markers with the old "[de-boilerplate vs <period>: ...]" wording, but
    ``src/agents/analysts/fundamental/filing_diff.py`` emits
    "[filing-diff vs <period>: ...]".  If the two drift apart again the LLM
    is taught to recognise a prefix it never sees, which lands squarely on
    the near-verbatim quiet-bullish marker the Phase 14 filing-similarity
    rework exists to make reachable — so pin the coherence here.
    """
    instr = build_fundamental_instruction(load_heuristics().fundamental_vocabulary)

    # The exact emitted marker prefix must appear in the LLM-facing prompt.
    assert "[filing-diff vs" in instr

    # The stale terminology must be fully gone — no partial drift allowed.
    assert "de-boilerplate" not in instr.lower()
