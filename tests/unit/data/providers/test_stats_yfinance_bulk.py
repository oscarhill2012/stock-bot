"""Tests for the bulk yfinance download helper ``_bulk_download``.

Verifies that a single ``yf.download`` call is made (one round-trip for all
symbols) and that the MultiIndex DataFrame is correctly unpacked into one
``PriceHistory`` per symbol.

Tier 1 — no real yfinance calls; ``yf.download`` is monkeypatched.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd

from data.models.price_history import PriceHistory


def _make_fake_download(tickers: list[str], num_rows: int = 3):
    """Return a fake ``yf.download`` function that produces a MultiIndex DataFrame.

    Mirrors the real yfinance multi-ticker output format:
    columns are ``(field, ticker)`` pairs.
    """
    def fake_download(tickers_arg, period, interval, **kwargs):
        idx = pd.date_range("2023-01-02", periods=num_rows, freq="D")
        cols = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], tickers]
        )
        return pd.DataFrame(1.0, index=idx, columns=cols)

    return fake_download


def test_bulk_download_returns_one_price_history_per_symbol(monkeypatch):
    """``_bulk_download`` must return exactly one ``PriceHistory`` per requested symbol."""
    from data.providers.stats import yfinance as mod

    monkeypatch.setattr(mod.yf, "download", _make_fake_download(["SPY", "XLK"]))

    out = asyncio.run(
        mod._bulk_download(("SPY", "XLK"), period="1mo", interval="1d", as_of=date.today())
    )

    assert set(out.keys()) == {"SPY", "XLK"}, f"Unexpected keys: {set(out.keys())}"
    assert all(isinstance(ph, PriceHistory) for ph in out.values())


def test_bulk_download_bar_count_matches_dataframe_rows(monkeypatch):
    """Each returned ``PriceHistory`` must contain one bar per DataFrame row."""
    from data.providers.stats import yfinance as mod

    num_rows = 5
    symbols = ("SPY", "XLK", "XLF")
    monkeypatch.setattr(mod.yf, "download", _make_fake_download(list(symbols), num_rows))

    out = asyncio.run(
        mod._bulk_download(symbols, period="1mo", interval="1d", as_of=date.today())
    )

    assert all(len(ph.bars) == num_rows for ph in out.values()), (
        f"Expected each PriceHistory to have exactly {num_rows} bars"
    )


def test_bulk_download_makes_single_yf_call(monkeypatch):
    """``_bulk_download`` must issue exactly one ``yf.download`` call, not one per symbol."""
    from data.providers.stats import yfinance as mod

    call_count = 0

    def counting_download(tickers_arg, period, interval, **kwargs):
        nonlocal call_count
        call_count += 1
        idx = pd.date_range("2023-01-02", periods=2, freq="D")
        cols = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], ["SPY", "XLK"]]
        )
        return pd.DataFrame(1.0, index=idx, columns=cols)

    monkeypatch.setattr(mod.yf, "download", counting_download)

    asyncio.run(
        mod._bulk_download(("SPY", "XLK"), period="1mo", interval="1d", as_of=date.today())
    )

    assert call_count == 1, f"Expected 1 yf.download call, got {call_count}"
