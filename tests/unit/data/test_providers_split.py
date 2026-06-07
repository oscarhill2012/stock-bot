"""Smoke test that the yfinance price_history provider projects from the raw payload.

The ``company_ratios`` registration was removed from the yfinance stats module
in the plan-08 provider cull (A-038); ``pit_composite`` is now the sole
``company_ratios`` provider.  This file previously verified that both providers
shared the underlying ``_yt_raw`` LRU cache — that invariant is now vacuous
for ``company_ratios``.  The price_history smoke is preserved here to guard
the ``_fetch_price_history`` → OHLCV bar mapping.
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


def test_price_history_uses_lru_cached_raw_call() -> None:
    """``_fetch_price_history`` resolves from the LRU-cached ``_yt_raw`` call.

    Verifies the OHLCV bar mapping is correct and that repeated calls for the
    same (symbol, period, interval) do not re-construct the ``yf.Ticker``.
    """
    # Clear the lru_cache so the test is hermetic.
    prov._yt_raw.cache_clear()

    with patch.object(prov.yf, "Ticker", side_effect=_fake_yf_ticker) as ticker_mock:
        ph = prov._fetch_price_history("AAPL", "1y", "1d")
        # Second call — must hit the LRU cache, not re-construct the Ticker.
        ph2 = prov._fetch_price_history("AAPL", "1y", "1d")

    # Only one Ticker construction expected across both calls.
    assert ticker_mock.call_count == 1
    assert ph.ticker == "AAPL"
    assert len(ph.bars) == 2
    assert ph.bars[-1].close == 102.5
    assert ph2.bars[-1].close == 102.5
