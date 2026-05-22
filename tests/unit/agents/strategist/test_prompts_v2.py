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
    assert "TickerStance" in STRATEGIST_INSTRUCTION
    assert "preferred_weight" in STRATEGIST_INSTRUCTION
    assert "conviction" in STRATEGIST_INSTRUCTION


def test_template_documents_lifecycle_hint_rules():
    """The prompt must communicate the lifecycle-hint contract:

    - any non-zero stance carries horizon / target_price / stop_price;
    - CLOSE stances carry close_reason;
    - TRIM stances carry trim_reason.

    The "non-zero ⇒ lifecycle hints" rule was tightened (any positive
    weight, not just opens) when ``TickerStance._require_lifecycle_hints_on_nonzero``
    was added — see ``stance_schema.py``.  The prompt mirrors that change.
    """

    text = STRATEGIST_INSTRUCTION

    # Lifecycle action vocabulary still surfaces — CLOSE and TRIM remain
    # explicit because they each carry a distinct reason field.
    assert "CLOSE" in text and "TRIM" in text

    # Any held position requires the exit-discipline triple — the HOLD row
    # in the lifecycle table documents this (R5 removed the old "non-zero"
    # prose; the table is now the single source of truth).
    assert "HOLD" in text
    assert "horizon" in text
    assert "target_price" in text
    assert "stop_price" in text

    # Reason fields keyed to the lifecycle action.
    assert "close_reason" in text
    assert "trim_reason" in text


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
    # Pre-substitute the temp:-prefixed slots so .format() can handle them.
    template = (
        STRATEGIST_INSTRUCTION
        .replace("{temp:held_positions_view}", "(No held positions — portfolio is flat.)")
        .replace("{temp:ticker_evidence}",     "AAPL\n  Aggregate: bullish (magnitude 0.42)")
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
