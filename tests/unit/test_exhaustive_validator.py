from unittest.mock import MagicMock

from agents.analysts._common import make_exhaustive_validator


def _make_ctx(signals: list, tickers: list) -> MagicMock:
    ctx = MagicMock()
    ctx.state = {"signals": signals, "tickers": tickers}
    return ctx


def test_validator_returns_none_when_all_covered():
    signals = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
    ctx = _make_ctx(signals, ["AAPL", "MSFT"])
    validator = make_exhaustive_validator("signals")
    result = validator(ctx)
    assert result is None


def test_validator_returns_content_when_missing():
    signals = [{"ticker": "AAPL"}]
    ctx = _make_ctx(signals, ["AAPL", "MSFT"])
    validator = make_exhaustive_validator("signals")
    result = validator(ctx)
    assert result is not None
    assert "MSFT" in result.parts[0].text


def test_validator_returns_none_for_empty_tickers():
    ctx = _make_ctx([], [])
    validator = make_exhaustive_validator("signals")
    result = validator(ctx)
    assert result is None
