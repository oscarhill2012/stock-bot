"""Tests that DecisionLogger writes one JSON file per executed Fill."""
from __future__ import annotations

import json
from pathlib import Path

from backtest.decision_logger import DecisionLogger


def test_logs_one_file_per_filled_execution(tmp_path: Path) -> None:
    """Two filled executions in one tick produce two snapshot files."""
    logger = DecisionLogger(output_dir=tmp_path, window_key="svb-stress-2023-03")

    state = {
        "as_of": "2023-03-13T09:30:00-04:00",
        "tick_phase": "open",
        "tick_id": "tick-1",
        "executions": [
            {"order": {"ticker": "SIVB", "action": "SELL", "quantity": 120,
                        "est_price": 42.5},
             "status": "filled", "actual_price": 42.31, "actual_quantity": 120,
             "broker_order_id": "b1"},
            {"order": {"ticker": "AAPL", "action": "BUY", "quantity": 50,
                        "est_price": 150.0},
             "status": "filled", "actual_price": 150.10, "actual_quantity": 50,
             "broker_order_id": "b2"},
        ],
        "evidence_view": {"SIVB": {"technical": {}, "fundamental": {}}},
        "strategist_decision": {
            "ticker_stances": {"SIVB": {"action": "SELL"}, "AAPL": {"action": "BUY"}},
            "close_reasons":  {"SIVB": "thesis broken"},
        },
        "clamps": [],
    }

    logger.on_executions(state)

    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2
    assert any("SIVB__sell" in f for f in files)
    assert any("AAPL__buy"  in f for f in files)

    # One sample file is well-formed and contains the expected top-level keys.
    sample = json.loads((tmp_path / files[0]).read_text())
    for key in ("decision_id", "tick", "ticker", "side", "execution",
                "analyst_inputs", "analyst_outputs", "strategist_view",
                "strategist_decision", "risk_gate", "forward_returns"):
        assert key in sample, f"missing key: {key}"
    assert sample["forward_returns"] is None  # backfilled by reporting


def test_skips_rejected_executions(tmp_path: Path) -> None:
    """A rejected order does not produce a decision snapshot."""
    logger = DecisionLogger(output_dir=tmp_path, window_key="x")

    state = {
        "as_of": "2023-03-13T09:30:00-04:00", "tick_phase": "open",
        "tick_id": "tick-1",
        "executions": [{
            "order": {"ticker": "X", "action": "BUY", "quantity": 1,
                       "est_price": 1.0},
            "status": "rejected", "error": "insufficient funds",
        }],
        "evidence_view": {}, "strategist_decision": {}, "clamps": [],
    }

    logger.on_executions(state)

    assert list(tmp_path.glob("*.json")) == []
