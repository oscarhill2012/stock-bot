"""Strategist v2 prompt tests — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_template_has_held_positions_slot():
    # A2.6: prompt template uses temp:-prefixed placeholder.
    assert "{temp:held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_ticker_evidence_slot():
    # A2.6: prompt template uses temp:-prefixed placeholder.
    assert "{temp:ticker_evidence}" in STRATEGIST_INSTRUCTION


def test_template_has_state_slots():
    """Every state slot the C9 callback must populate is present in the template.

    Note: ``{tickers}`` now appears once in the template (in the "Your Job"
    section).  The previous trailing ``Watchlist: {tickers}`` line was a
    duplicate carried over from the v1 template and was removed when the
    prompt was de-duplicated for the active-stances simplification.
    Substring checks alone cannot verify a slot is wired correctly; the
    runtime guard is the ``.format(...)`` call in
    ``test_template_renders_with_all_required_slots`` which raises
    ``KeyError`` if any slot is missing.
    """
    assert "{portfolio}" in STRATEGIST_INSTRUCTION
    assert "{memory_buffer}" in STRATEGIST_INSTRUCTION
    assert "{day_digest}" in STRATEGIST_INSTRUCTION
    assert "{thesis}" in STRATEGIST_INSTRUCTION
    assert "{tickers}" in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_legacy_signal_slots():
    """Legacy four-list dump replaced by single ticker_evidence block."""
    assert "{technical_signals}" not in STRATEGIST_INSTRUCTION
    assert "{fundamental_signals}" not in STRATEGIST_INSTRUCTION
    assert "{sentiment_signals}" not in STRATEGIST_INSTRUCTION
    assert "{smart_money_signals}" not in STRATEGIST_INSTRUCTION


def test_template_no_longer_has_active_positions_dump():
    assert "Active Positions: {positions}" not in STRATEGIST_INSTRUCTION


def test_template_instructs_per_ticker_stance_output():
    """The template must encode the per-held-position stance requirement.

    Band 2 (Task 5) rewrites the OUTPUT CONTRACT to a single intent-based
    vocabulary.  ``preferred_weight`` and ``conviction`` are gone; the new
    canonical fields are ``intent`` and ``weight``.
    """
    text = STRATEGIST_INSTRUCTION

    # Core invariant — every held position must have an explicit stance.
    assert "you MUST emit exactly one stance" in text

    # The mode placeholder wires the cold-start vs incremental framing.
    assert "{temp:strategist_mode}" in text

    # New canonical field names replace the legacy dual-form vocabulary.
    assert "intent" in text
    assert "weight" in text

    # Legacy field names must not appear as JSON field references — their
    # presence in the output contract would re-introduce the dual-form ambiguity
    # that caused the 2026-05-24 schema-retry storm.
    # Note: the word "conviction" may appear in English prose (e.g. "when conviction
    # supports it"), so we check for the JSON key form ``"conviction"`` (with quotes)
    # rather than the bare word.
    assert "preferred_weight" not in text
    assert '"conviction"' not in text


def test_template_documents_intent_verb_rules():
    """The prompt must communicate the intent-verb contract from the end-state table.

    Band 2 (Task 5) replaces the legacy lifecycle-hint table with a clean
    intent-verb table.  Every verb must be named; the structural thesis fields
    (horizon, target_price, stop_price) remain required for open/add/trim.
    ``close_reason`` and ``trim_reason`` are gone; their role is carried by
    the unified ``reason`` field.
    """
    text = STRATEGIST_INSTRUCTION

    # All six intent verbs must appear in the contract table.
    assert "open" in text
    assert "add" in text
    assert "trim" in text
    assert "close" in text
    assert "hold" in text
    assert "update" in text

    # Structural thesis fields remain required for open/add/trim.
    assert "horizon" in text
    assert "target_price" in text
    assert "stop_price" in text

    # Unified reason field replaces the legacy per-action fields.
    assert "reason" in text

    # Legacy per-action reason fields must not appear.
    assert "close_reason" not in text
    assert "trim_reason" not in text


def test_template_renders_with_all_required_slots():
    """Smoke test — the template must fill cleanly with all required slot values.

    A2.6 renamed two placeholders to ``{temp:held_positions_view}`` and
    ``{temp:ticker_evidence}``.  Python's ``str.format`` / ``str.format_map``
    both interpret the colon as the field/format-spec separator, so neither
    can fill ``temp:``-prefixed keys directly.

    Workaround: use ``str.replace`` to substitute the two ``temp:``-prefixed
    placeholders first (converting them to plain ``{held_positions_view}`` and
    ``{ticker_evidence}`` stand-ins), then call ``.format()`` in the normal
    way.  The guard contract is preserved: any *missing* slot still raises
    ``KeyError`` before the assertions execute.
    """
    # Pre-substitute all temp:-prefixed slots so .format() can handle them.
    # Spec B added {temp:strategist_mode} above ## Current State; it must be
    # substituted here alongside the existing held_positions_view and
    # ticker_evidence slots.
    template = (
        STRATEGIST_INSTRUCTION
        .replace("{temp:strategist_mode}",     "Cold start — your portfolio is empty.")
        .replace("{temp:held_positions_view}", "(No held positions — portfolio is flat.)")
        .replace("{temp:ticker_evidence}",     "AAPL\n  Aggregate: bullish (magnitude 0.42)")
        # Schema-retry feedback slot — empty on first attempt; populated by
        # the RetryingAgentWrapper before each schema retry.  Substitute as
        # an empty string here so .format() does not stumble on the colon.
        .replace("{temp:_last_schema_error}",  "")
    )
    rendered = template.format(
        portfolio="cash=100, positions={}",
        memory_buffer="[]",
        day_digest="(empty)",
        thesis="(empty)",
        tickers="['AAPL','MSFT']",
    )
    assert "No held positions" in rendered
    assert "AAPL" in rendered
