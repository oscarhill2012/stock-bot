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
    """Build a fake yfinance Ticker with a tiny two-bar history frame.

    Only ``history`` is populated — ``_yt_raw`` no longer fetches ``info`` /
    ``fast_info`` on the price_history path (those scrapes were dropped with
    the plan-08 ``company_ratios`` cull).
    """
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


def test_price_history_skips_info_and_fast_info() -> None:
    """``_yt_raw`` must not fetch ``yt.info`` / ``yt.fast_info`` on the price path.

    Those two eager scrapes existed only to feed the now-culled
    ``company_ratios`` yfinance provider (plan-08 A-038).  ``yt.info`` in
    particular triggers a heavy separate yfinance round-trip, so touching it on
    every ``price_history`` cache miss is pure waste.  This test guards against
    the eager fetch creeping back into the hot path.
    """
    prov._yt_raw.cache_clear()

    df = pd.DataFrame(
        {
            "Open":   [100.0],
            "High":   [102.0],
            "Low":    [ 99.0],
            "Close":  [101.0],
            "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex([datetime(2026, 5, 1)]),
    )

    # A probe Ticker that records whether the snapshot-leaky attributes are
    # read.  The properties return *normally* (so the provider's own
    # ``try/except`` can never mask the access) but flip a class-level flag the
    # moment they are touched.
    class _ProbeTicker:
        info_accessed = False
        fast_accessed = False

        def history(self, *_a, **_k):
            return df

        @property
        def actions(self):
            return pd.DataFrame()

        @property
        def info(self):
            type(self).info_accessed = True
            return {"trailingPE": 20.1}

        @property
        def fast_info(self):
            type(self).fast_accessed = True
            return {"last_price": 101.0}

    with patch.object(prov.yf, "Ticker", side_effect=lambda *_a, **_k: _ProbeTicker()):
        ph = prov._fetch_price_history("AAPL", "1y", "1d")

    # The price-history mapping must still work off the raw history frame.
    assert ph.bars[-1].close == 101.0

    # The perf guarantee: neither snapshot-leaky attribute was fetched.
    assert not _ProbeTicker.info_accessed, \
        "yt.info must not be fetched on the price_history path"
    assert not _ProbeTicker.fast_accessed, \
        "yt.fast_info must not be fetched on the price_history path"
