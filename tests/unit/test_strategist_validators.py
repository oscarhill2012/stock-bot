from unittest.mock import MagicMock
from agents.strategist.agent import _strategist_validation_callback


def _make_ctx(state: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state
    return ctx


def test_validator_returns_none_when_all_covered():
    ctx = _make_ctx({
        "strategist_decision": {
            "target_weights": {"AAPL": 0.1, "MSFT": 0.0},
            "decision_tag": "test",
            "reasoning": "ok",
            "updated_thesis": "ok",
            "confidence": 0.7,
        },
        "tickers": ["AAPL", "MSFT"],
        "positions": {},
    })
    result = _strategist_validation_callback(ctx)
    assert result is None


def test_validator_flags_missing_tickers():
    ctx = _make_ctx({
        "strategist_decision": {
            "target_weights": {"AAPL": 0.1},  # MSFT missing
            "decision_tag": "test",
            "reasoning": "ok",
            "updated_thesis": "ok",
            "confidence": 0.7,
        },
        "tickers": ["AAPL", "MSFT"],
        "positions": {},
    })
    result = _strategist_validation_callback(ctx)
    assert result is not None
    assert "MSFT" in result.parts[0].text


def test_validator_flags_off_watchlist():
    ctx = _make_ctx({
        "strategist_decision": {
            "target_weights": {"AAPL": 0.1, "MSFT": 0.0, "TSLA": 0.05},  # TSLA extra
            "decision_tag": "test",
            "reasoning": "ok",
            "updated_thesis": "ok",
            "confidence": 0.7,
        },
        "tickers": ["AAPL", "MSFT"],
        "positions": {},
    })
    result = _strategist_validation_callback(ctx)
    assert result is not None
    assert "TSLA" in result.parts[0].text
