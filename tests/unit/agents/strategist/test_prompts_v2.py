"""Strategist v2 prompt tests — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_template_has_held_positions_slot():
    assert "{held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_ticker_evidence_slot():
    assert "{ticker_evidence}" in STRATEGIST_INSTRUCTION


def test_template_has_state_slots():
    """Every state slot the C9 callback must populate is present in the template.

    Note that ``{tickers}`` deliberately appears twice in the template — once
    in the "Your Job" section and once on the final "Watchlist" line — and
    ``str.format`` fills both occurrences in a single call. Substring checks
    cannot distinguish one occurrence from two; the runtime guard is the
    ``.format(...)`` call in ``test_template_renders_with_all_required_slots``
    which raises ``KeyError`` if a slot is missing.
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

    # Non-zero stances require the exit-discipline triple.
    assert "non-zero" in text
    assert "horizon" in text
    assert "target_price" in text
    assert "stop_price" in text

    # Reason fields keyed to the lifecycle action.
    assert "close_reason" in text
    assert "trim_reason" in text


def test_template_renders_with_all_required_slots():
    """Smoke test — ``str.format`` raises ``KeyError`` if any slot is missing.

    The ``.format(...)`` call itself is the primary guard: if a future edit
    introduces an unfilled ``{slot}`` the test fails with a ``KeyError``
    before the assertions ever run. The two ``assert`` lines below are a
    lightweight sanity check that the rendered output is non-empty and
    contains the values we passed in.
    """
    rendered = STRATEGIST_INSTRUCTION.format(
        portfolio="cash=100, positions={}",
        memory_buffer="[]",
        day_digest="(empty)",
        thesis="(empty)",
        held_positions_view="(No held positions — portfolio is flat.)",
        ticker_evidence="AAPL\n  Aggregate: bullish (magnitude 0.42)",
        tickers="['AAPL','MSFT']",
    )
    assert "No held positions" in rendered
    assert "AAPL" in rendered
