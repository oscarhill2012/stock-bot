"""R4 — loader contract for ``config/risk_gate.json``.

The five risk-gate constants used to live as module-level literals in
``src/orchestrator/state.py``.  Moving them to ``config/risk_gate.json``
matches the project-wide "all configuration in config/*.json" convention
and makes R1/R2/R3 (cash-floor removal, max-delta widen, turnover lift)
operator-tunable.

This test covers two contracts:
1. ``load_risk_gate_config(path=...)`` returns a frozen dataclass-like
   object whose fields equal the JSON contents.
2. Importing the constants from ``orchestrator.state`` still resolves
   them by their legacy names so the wider codebase keeps working.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.risk_gate import load_risk_gate_config


def test_loader_maps_each_json_field(tmp_path: Path) -> None:
    """Every JSON key surfaces as an identically-named attribute."""

    p = tmp_path / "risk_gate.json"
    p.write_text(
        json.dumps(
            {
                "min_held_weight":       0.002,
                "max_position_weight":   0.25,
                "cash_floor_weight":     0.05,
                "max_delta_per_ticker":  0.04,
                "max_total_turnover":    0.40,
            }
        ),
        encoding="utf-8",
    )

    cfg = load_risk_gate_config(path=p)

    assert cfg.min_held_weight       == 0.002
    assert cfg.max_position_weight   == 0.25
    assert cfg.cash_floor_weight     == 0.05
    assert cfg.max_delta_per_ticker  == 0.04
    assert cfg.max_total_turnover    == 0.40


def test_state_reexports_resolve_by_legacy_name() -> None:
    """``orchestrator.state`` re-exports the five constants by name."""

    from orchestrator.state import (
        CASH_FLOOR_WEIGHT,
        MAX_DELTA_PER_TICKER,
        MAX_POSITION_WEIGHT,
        MAX_TOTAL_TURNOVER,
        MIN_HELD_WEIGHT,
    )

    for name, value in (
        ("MIN_HELD_WEIGHT",      MIN_HELD_WEIGHT),
        ("MAX_POSITION_WEIGHT",  MAX_POSITION_WEIGHT),
        ("CASH_FLOOR_WEIGHT",    CASH_FLOOR_WEIGHT),
        ("MAX_DELTA_PER_TICKER", MAX_DELTA_PER_TICKER),
        ("MAX_TOTAL_TURNOVER",   MAX_TOTAL_TURNOVER),
    ):
        assert isinstance(value, float), f"{name} must be float, got {type(value)}"

    # R1/R2/R3 defaults — these are the values shipped by this spec.
    assert CASH_FLOOR_WEIGHT    == 0.00
    assert MAX_DELTA_PER_TICKER == 0.05
    assert MAX_TOTAL_TURNOVER   == 0.50
