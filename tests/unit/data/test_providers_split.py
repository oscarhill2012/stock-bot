"""Smoke test that the two yfinance providers project from the same raw payload.

We do not hit the network — the test patches ``yf.Ticker`` to return a fake.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from data.providers.stats import yfinance as prov


def _fake_yf_ticker(symbol: str) -> MagicMock:
    """Build a fake yfinance Ticker with a tiny history + info payload."""
    df = pd.DataFrame(
        {
            "Open":   [100.0, 101.0],
            "High":   [102.0, 103.0],
            "Low":    [ 99.0, 100.0],
            "Close":  [101.0, 102.5],
            "Volume": [1_000.0, 1_200.0],
        },
        index=pd.DatetimeIndex([datetime(2026, 5, 1), datetime(2026, 5, 2)]),
    )
    t = MagicMock()
    t.history.return_value = df
    t.info = {"trailingPE": 20.1, "longName": "Test Co", "sector": "Tech"}
    t.fast_info = {"last_price": 102.5}
    return t


def test_price_history_and_ratios_share_underlying_call() -> None:
    """Fetching both for the same ticker must not double-call yfinance."""
    # Clear the lru_cache so the test is hermetic.
    prov._yt_raw.cache_clear()

    with patch.object(prov.yf, "Ticker", side_effect=_fake_yf_ticker) as ticker_mock:
        ph = prov._fetch_price_history("AAPL", "1y", "1d")
        cr = prov._fetch_company_ratios("AAPL", "1y", "1d")

    # The lru_cache guarantees one Ticker construction per (symbol, period, interval).
    assert ticker_mock.call_count == 1
    assert ph.ticker == "AAPL"
    assert len(ph.bars) == 2
    assert ph.bars[-1].close == 102.5
    assert cr.trailing_pe == 20.1
    assert cr.last_price == 102.5
