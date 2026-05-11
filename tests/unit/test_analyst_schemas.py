import pytest
from pydantic import ValidationError

from agents.analysts.sentiment.schema import SentimentSignal
from agents.analysts.smart_money.schema import SmartMoneySignal
from agents.analysts.technical.schema import TechnicalSignal


def test_technical_signal_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        TechnicalSignal(ticker="AAPL", direction="bullish", confidence=1.5)


def test_technical_signal_rejects_too_many_key_factors():
    with pytest.raises(ValidationError):
        TechnicalSignal(
            ticker="AAPL", direction="bullish", confidence=0.8,
            key_factors=["a", "b", "c", "d"]  # max is 3
        )


def test_smart_money_accepts_neutral_with_zero_confidence():
    """Plan B widened SmartMoneySignal so the analyst can emit one record per
    watchlist ticker — including neutral rows for tickers with no insider /
    politician / 13D/G activity. Conviction is null in that case.
    """
    # Neutral, no-activity emission used for tickers without material filings.
    s = SmartMoneySignal(
        ticker="AAPL", direction="neutral", confidence=0.0,
    )
    assert s.direction == "neutral"
    assert s.confidence == 0.0
    assert s.conviction is None
    assert s.insiders == []
    assert s.politicians == []


def test_smart_money_accepts_directional_with_conviction():
    """Active emissions carry direction + confidence + conviction."""
    s = SmartMoneySignal(
        ticker="TSLA",
        direction="bullish",
        confidence=0.8,
        conviction="high",
        insiders=["Elon Musk"],
        total_dollar_value=500_000.0,
    )
    assert s.conviction == "high"
    assert s.total_dollar_value == 500_000.0


def test_sentiment_signal_has_extra_fields():
    s = SentimentSignal(
        ticker="AAPL", direction="bullish", confidence=0.7,
        top_headlines=["headline 1"], social_score_delta=0.3
    )
    assert s.social_score_delta == 0.3
    assert len(s.top_headlines) == 1
