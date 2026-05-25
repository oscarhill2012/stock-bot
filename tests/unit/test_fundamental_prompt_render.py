"""Tier-1 tests for the Fundamental LLM prompt template.

These tests validate that ``build_fundamental_instruction`` correctly
substitutes closed-vocabulary tokens and produces a prompt containing:

- No residual vocab-slot ``{..}`` tokens (all four vocab groups resolved).
- All closed-vocab terms from the test vocabulary.
- The INSIDER ACTIVITY and INSIDER FOOTNOTES section headings.
- The two expected ADK runtime placeholders ``{fundamental_context}`` and
  ``{ticker}`` that remain for ADK's ``inject_session_state`` to fill.

Note on the plan's original regex assertion
-------------------------------------------
The plan's draft included ``assert not re.search(r"\\{[a-z_]+\\}", rendered)``
to assert no single-brace tokens remain.  That assertion is incompatible with
the two ADK runtime state placeholders ``{fundamental_context}`` and
``{ticker}`` that are intentionally preserved so ADK can inject per-tick
data.  The tests below replace that blanket assertion with explicit positive
checks for the expected runtime tokens instead.

Phase 9 update
--------------
The prompt was refactored from a multi-ticker batch design to a single-ticker
per-branch design.  ``{tickers}`` (plural) is removed; ``{ticker}`` (singular)
is the new runtime key that the branch factory substitutes at build time.
"""
from __future__ import annotations

import re

from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import FundamentalVocabulary


def _vocab() -> FundamentalVocabulary:
    """Return a representative test vocabulary instance."""
    return FundamentalVocabulary(
        guidance=["raised", "maintained", "lowered", "none"],
        tone=["confident", "cautious", "defensive", "mixed"],
        risks=["regulatory", "litigation", "cybersecurity", "going_concern"],
        insider_signals=["cluster_buying", "cluster_selling", "planned_sale_dominant", "mixed"],
    )


def test_vocabulary_placeholders_resolve() -> None:
    """All vocab {placeholder} tokens are substituted by build_fundamental_instruction."""
    rendered = build_fundamental_instruction(_vocab())

    # Vocab slot tokens must be gone.
    assert "{guidance_options}" not in rendered
    assert "{tone_options}" not in rendered
    assert "{risk_tags}" not in rendered
    assert "{insider_signals}" not in rendered

    # Only the two known ADK runtime keys may remain as single-brace tokens.
    # Phase 9: {tickers} (plural) is gone; {ticker} (singular) is the new key.
    stripped = rendered.replace("{fundamental_context}", "").replace("{ticker}", "")
    assert not re.search(r"\{[a-z_]+\}", stripped), (
        "Unexpected single-brace token found in rendered prompt "
        "(only {fundamental_context} and {ticker} are allowed)"
    )


def test_vocabulary_values_appear_in_rendered_prompt() -> None:
    """Each closed-vocab term lands in the rendered prompt."""
    rendered = build_fundamental_instruction(_vocab())
    for term in ("raised", "maintained", "confident", "cluster_buying", "regulatory"):
        assert term in rendered, f"Expected vocab term '{term}' not found in prompt"


def test_insider_supplement_block_present() -> None:
    """The rendered prompt contains the insider section headings.

    These headings appear in the static instruction text (not in the runtime
    ``{fundamental_context}`` block) so the LLM can correlate the data
    structure with the decision rules without relying on the fetch callback's
    formatting.
    """
    rendered = build_fundamental_instruction(_vocab())

    # The prompt must reference the insider activity section by name.
    assert "INSIDER ACTIVITY" in rendered, "Missing 'INSIDER ACTIVITY' section reference"

    # The prompt must reference the insider footnotes section by name.
    assert "INSIDER FOOTNOTES" in rendered, "Missing 'INSIDER FOOTNOTES' section reference"


def test_runtime_placeholders_present() -> None:
    """The ADK state placeholders survive vocab substitution intact.

    Phase 9: the multi-ticker ``{tickers}`` placeholder is replaced by the
    single-ticker ``{ticker}`` key that the per-branch factory substitutes.
    ``{fundamental_context}`` remains — the per-ticker fetch agent writes a
    single-ticker block into ``state["fundamental_context"]`` at run time.
    """
    rendered = build_fundamental_instruction(_vocab())

    # ADK fills fundamental_context from session state each tick.
    assert "{fundamental_context}" in rendered, (
        "ADK runtime placeholder {fundamental_context} is missing from rendered prompt"
    )
    # Per-branch ticker placeholder — substituted by the branch factory.
    assert "{ticker}" in rendered, (
        "Per-branch runtime placeholder {ticker} is missing from rendered prompt"
    )
    # The old multi-ticker placeholder must not be present.
    assert "{tickers}" not in rendered, (
        "Stale multi-ticker placeholder {tickers} still present — Phase 9 "
        "requires per-branch single-ticker phrasing"
    )


def test_decision_rule_present() -> None:
    """The decision-rule block (cluster buys, 10b5-1 discount) appears in the prompt."""
    rendered = build_fundamental_instruction(_vocab())
    assert "10b5-1" in rendered or "planned" in rendered.lower(), (
        "Expected 10b5-1 / planned-sale decision rule not found in prompt"
    )
    assert "cluster" in rendered.lower(), (
        "Expected cluster-buying decision rule not found in prompt"
    )


def test_lean_options_in_prompt() -> None:
    """The prompt lists the three valid lean values."""
    rendered = build_fundamental_instruction(_vocab())
    for lean in ("bullish", "bearish", "neutral"):
        assert lean in rendered, f"Lean option '{lean}' not found in rendered prompt"


def test_report_schema_instructions_present() -> None:
    """The report schema block is present in the rendered prompt.

    Mirrors the equivalent assertion in the News prompt tests — both LLM
    analysts must emit the same report shape so the Strategist surface can
    render them uniformly.  The 2026-05-25 prompt rewrite changed the
    driver-count phrasing to ``list of 2-4 entries``; the constraint is
    unchanged.
    """
    rendered = build_fundamental_instruction(_vocab())

    assert "Report schema:" in rendered, (
        "'Report schema:' heading not found — LLM will not know to emit a report"
    )
    assert "2-4 entries" in rendered, (
        "'2-4 entries' constraint not found — driver count mandate missing"
    )
