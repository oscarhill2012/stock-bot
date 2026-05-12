import pytest

from agents.risk_gate.lifecycle import (
    StrategistContractViolation,
    validate_lifecycle_contract,
)


def test_opening_without_thesis_raises():
    with pytest.raises(StrategistContractViolation, match="Opening NVDA"):
        validate_lifecycle_contract(
            new_weights={"NVDA": 0.05},
            current_weights={"NVDA": 0.0},
            new_positions={},
            close_reasons={},
        )


def test_closing_without_reason_raises():
    with pytest.raises(StrategistContractViolation, match="Closing AAPL"):
        validate_lifecycle_contract(
            new_weights={"AAPL": 0.0},
            current_weights={"AAPL": 0.05},
            new_positions={},
            close_reasons={},
        )


def test_holding_below_min_treated_as_closed():
    # current 0.0005 < MIN; new 0.0008 also < MIN — no transition, no contract
    validate_lifecycle_contract(
        new_weights={"AAPL": 0.0008},
        current_weights={"AAPL": 0.0005},
        new_positions={},
        close_reasons={},
    )


def test_open_with_thesis_and_close_with_reason_passes():
    from agents.risk_gate.lifecycle import _stub_position_thesis
    validate_lifecycle_contract(
        new_weights={"NVDA": 0.05, "AAPL": 0.0},
        current_weights={"NVDA": 0.0, "AAPL": 0.05},
        new_positions={"NVDA": _stub_position_thesis("NVDA")},
        close_reasons={"AAPL": "thesis invalidated"},
    )
