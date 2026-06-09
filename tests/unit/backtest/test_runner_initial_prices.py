"""Ensure FakeBroker is seeded with real prices from the first available
OHLCV bar within the backtest window, not 0.0.

A zero-priced bootstrap tick produces artefactual equity-curve moves on
the second tick when the broker's mid-tick price refresh kicks in.
A-046: the 0.0 fallback is now an error — missing tickers raise ValueError.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backtest.runner import _seed_initial_prices  # new helper we extract


def _make_fake_store(bars: dict[str, list[tuple[datetime, float]]]):
    """Tiny stub matching the .read_ohlcv signature used by the runner.

    Parameters
    ----------
    bars : dict[str, list[tuple[datetime, float]]]
        Mapping of ticker to a list of (timestamp, close) pairs.

    Returns
    -------
    object
        A stub object with a ``read_ohlcv`` method.
    """

    class _Stub:
        def read_ohlcv(self, ticker, start, end):  # noqa: D401 — stub
            """Return bars for *ticker* that fall within [start, end]."""
            rows = bars.get(ticker, [])
            return [
                type("Bar", (), {"timestamp": ts, "close": close})()
                for ts, close in rows
                if start <= ts <= end
            ]

    return _Stub()


def test_initial_prices_use_first_bar_close():
    """Seed map uses the first in-window bar's close per ticker."""

    store = _make_fake_store(
        {
            "AAPL": [(datetime(2024, 1, 2, 14, tzinfo=UTC), 187.0)],
            "MSFT": [(datetime(2024, 1, 2, 14, tzinfo=UTC), 372.5)],
        }
    )

    prices = _seed_initial_prices(
        store=store,
        tickers=["AAPL", "MSFT"],
        window_start=datetime(2024, 1, 2, tzinfo=UTC),
        window_end=datetime(2024, 1, 5, tzinfo=UTC),
    )

    assert prices == {"AAPL": 187.0, "MSFT": 372.5}


def test_initial_prices_raise_when_no_bar_available():
    """A ticker with no in-window bar must raise — never silently seed 0.0.

    The 0.0 default let FakeBroker accept zero-priced BUYs, silently
    corrupting the backtest portfolio (A-046).
    """

    store = _make_fake_store({})

    with pytest.raises(ValueError, match="NEWCO"):
        _seed_initial_prices(
            store=store,
            tickers=["NEWCO"],
            window_start=datetime(2024, 1, 2, tzinfo=UTC),
            window_end=datetime(2024, 1, 5, tzinfo=UTC),
        )


def test_initial_prices_report_all_missing_tickers():
    """Every missing ticker is listed in a single error — not fix-and-retry.

    When multiple tickers lack in-window bars the function must collect them
    all before raising so the operator sees the full list at once.
    """

    store = _make_fake_store(
        {"AAPL": [(datetime(2024, 1, 2, 14, tzinfo=UTC), 187.0)]}
    )

    with pytest.raises(ValueError) as excinfo:
        _seed_initial_prices(
            store=store,
            tickers=["AAPL", "GHOST", "PHANTOM"],
            window_start=datetime(2024, 1, 2, tzinfo=UTC),
            window_end=datetime(2024, 1, 5, tzinfo=UTC),
        )

    msg = str(excinfo.value)
    assert "GHOST" in msg
    assert "PHANTOM" in msg
    assert "AAPL" not in msg  # ticker WITH data must not appear as missing
