import pytest
from pydantic import ValidationError

from agents.analysts.technical.schema import TechnicalSignal
from agents.analysts.fundamental.schema import FundamentalSignal
from agents.analysts.sentiment.schema import SentimentSignal
from agents.analysts.smart_money.schema import SmartMoneySignal


def test_technical_signal_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        TechnicalSignal(ticker="AAPL", direction="bullish", confidence=1.5)


def test_technical_signal_rejects_too_many_key_factors():
    with pytest.raises(ValidationError):
        TechnicalSignal(
            ticker="AAPL", direction="bullish", confidence=0.8,
            key_factors=["a", "b", "c", "d"]  # max is 3
        )


def test_smart_money_rejects_neutral():
    with pytest.raises(ValidationError):
        SmartMoneySignal(ticker="AAPL", direction="neutral", conviction="high")


def test_sentiment_signal_has_extra_fields():
    s = SentimentSignal(
        ticker="AAPL", direction="bullish", confidence=0.7,
        top_headlines=["headline 1"], social_score_delta=0.3
    )
    assert s.social_score_delta == 0.3
    assert len(s.top_headlines) == 1
