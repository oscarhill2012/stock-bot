"""Strategist v2 prompt tests — Tier 1, no LLM."""
from __future__ import annotations

from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_template_has_held_positions_slot():
    assert "{held_positions_view}" in STRATEGIST_INSTRUCTION


def test_template_has_ticker_evidence_slot():
    assert "{ticker_evidence}" in STRATEGIST_INSTRUCTION


def test_template_has_state_slots():
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
    text = STRATEGIST_INSTRUCTION
    assert "OPEN" in text and "CLOSE" in text and "TRIM" in text
    assert "horizon" in text
    assert "target_price" in text
    assert "stop_price" in text
    assert "close_reason" in text
    assert "trim_reason" in text


def test_template_renders_with_all_required_slots():
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
