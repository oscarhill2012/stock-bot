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


def test_instruction_is_a_pure_fresh_surprise_detector():
    """The news analyst is a pure fresh-surprise detector — it must NOT try
    to locate, decay, or exhaust a multi-day drift window itself.

    That temporal-persistence job moved downstream to the strategist/thesis
    layer (Phase 14 follow-up). The prompt must:

    - Still use the PREVIOUSLY SEEN section — but only as a novelty check
      (is a fresh article genuinely new vs a rehash), not as an age/window
      tracker.
    - Contain NO window-position bands, decay, exhaustion, or REVERSAL
      language — that behaviour has been deleted outright.
    - Explicitly distinguish "no fresh surprise today" (absence of new
      information) from "a prior catalyst has faded" — the analyst holds
      no state about prior catalysts to fade.
    - Still lean neutral, with low confidence, absent a genuine surprise.
    """
    instruction = build_news_instruction(_vocab())

    # The PREVIOUSLY SEEN section survives — but purely as a novelty check.
    assert "PREVIOUSLY SEEN" in instruction, (
        "Expected 'PREVIOUSLY SEEN' novelty-check reference in prompt"
    )
    assert "novelty check" in instruction.lower(), (
        "Expected the PREVIOUSLY SEEN section to be framed as a novelty "
        "check, not a window-position tracker"
    )

    # Deleted window-position / decay / exhaustion / reversal language must
    # be gone — this was the source of the dead-neutral bug (10/20 tickers
    # reading neutral 0.00 mid-run once the 7-day fetch outran the 20-day
    # window).
    assert "REVERSAL" not in instruction, (
        "Stale-news-predicts-REVERSAL language should have been deleted"
    )
    assert "exhausted" not in instruction.lower(), (
        "Window-exhaustion language should have been deleted"
    )
    assert "trading days ago" not in instruction, (
        "Window-position age band ('N trading days ago') should be gone"
    )
    assert "re-anchor" not in instruction.lower(), (
        "Drift re-anchoring language should have been deleted — a pure "
        "detector has no existing drift to re-anchor against"
    )

    # The absence-vs-fading distinction must be spelled out explicitly —
    # this is the semantic contract the downstream consumer relies on.
    assert "absence of new information" in instruction, (
        "Expected the prompt to spell out that a neutral tick means "
        "absence of new information, not a faded prior catalyst"
    )
    assert "not tracking prior catalysts" in instruction.lower() or \
        "not tracking a window" in instruction.lower(), (
        "Expected the prompt to state plainly that the analyst holds no "
        "state about prior catalysts / drift windows"
    )

    # Staleness / no-surprise guidance — lean neutral absent a genuine
    # surprise.
    assert "neutral" in instruction.lower(), (
        "Expected neutral-lean guidance for no-surprise news in prompt"
    )


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


def test_news_prompt_has_no_horizon_self_report():
    """The news prompt no longer instructs the LLM to emit horizon_days."""
    from agents.analysts.heuristics import load_heuristics

    instr = build_news_instruction(load_heuristics().news_vocabulary)

    assert "horizon_days" not in instr
    assert "Set horizon_days to roughly 5" not in instr


def test_decision_rule_is_surprise_classification_not_sentiment_reaction():
    """The decision rule classifies the surprise and judges its direction —
    the old react-to-sentiment framing must be gone, and so must the STEP 3
    drift-window-positioning step this analyst no longer owns."""
    instruction = build_news_instruction(_vocab())

    assert "SURPRISE CLASSIFICATION" in instruction
    assert "DRIFT POSITIONING" not in instruction  # deleted — no longer this analyst's job
    assert "PREVIOUSLY SEEN" in instruction        # explains the stale section


def test_instruction_explains_the_two_context_sections():
    """The prompt must tell the model what FRESH vs PREVIOUSLY SEEN mean —
    the sections only exist because the pre-filter builds them."""
    instruction = build_news_instruction(_vocab())

    assert "FRESH ARTICLES" in instruction
    assert "headline" in instruction.lower()
