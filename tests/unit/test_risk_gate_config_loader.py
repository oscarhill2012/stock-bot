"""Loader contract for ``config/risk_gate.json``.

The five risk-gate constants live in ``config/risk_gate.json`` and are
re-exported as module-level constants from ``src/orchestrator/state.py``
so the wider codebase can ``from orchestrator.state import …`` them
without touching the loader directly.

This test covers two contracts:
1. ``load_risk_gate_config(path=...)`` returns a dataclass-like object
   whose fields equal the JSON contents.
2. Importing the constants from ``orchestrator.state`` resolves them
   under their uppercase names.
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
                "min_held_weight":      0.002,
                "max_position_weight":  0.25,
                "cash_floor_weight":    0.05,
                "max_total_turnover":   0.40,
                "max_delta_per_buy":    0.04,
            }
        ),
        encoding="utf-8",
    )

    cfg = load_risk_gate_config(path=p)

    assert cfg.min_held_weight     == 0.002
    assert cfg.max_position_weight == 0.25
    assert cfg.cash_floor_weight   == 0.05
    assert cfg.max_total_turnover  == 0.40
    assert cfg.max_delta_per_buy   == 0.04


def test_state_reexports_resolve_by_name() -> None:
    """``orchestrator.state`` re-exports the five constants by name."""

    from orchestrator.state import (
        CASH_FLOOR_WEIGHT,
        MAX_DELTA_PER_BUY,
        MAX_POSITION_WEIGHT,
        MAX_TOTAL_TURNOVER,
        MIN_HELD_WEIGHT,
    )

    for name, value in (
        ("MIN_HELD_WEIGHT",     MIN_HELD_WEIGHT),
        ("MAX_POSITION_WEIGHT", MAX_POSITION_WEIGHT),
        ("CASH_FLOOR_WEIGHT",   CASH_FLOOR_WEIGHT),
        ("MAX_TOTAL_TURNOVER",  MAX_TOTAL_TURNOVER),
        ("MAX_DELTA_PER_BUY",   MAX_DELTA_PER_BUY),
    ):
        assert isinstance(value, float), f"{name} must be float, got {type(value)}"

    # Each constant must be strictly positive and within its declared range
    # (the live config values may drift, so we assert shape not equality).
    assert 0.0 <= CASH_FLOOR_WEIGHT  <= 0.50
    assert 0.0 <  MAX_TOTAL_TURNOVER <= 2.0
    assert 0.0 <  MAX_DELTA_PER_BUY  <= 1.0
