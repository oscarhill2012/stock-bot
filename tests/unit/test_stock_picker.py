import pytest
from orchestrator.stock_picker import get_watchlist


def test_get_watchlist_returns_list():
    tickers = get_watchlist()
    assert isinstance(tickers, list)
    assert len(tickers) > 0


def test_get_watchlist_contains_expected_tickers():
    tickers = get_watchlist()
    assert "AAPL" in tickers
    assert "MSFT" in tickers
