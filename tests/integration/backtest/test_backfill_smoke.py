"""End-to-end backfill smoke: scripts.backtest_fetch fills a temp cache PIT-correctly.

Runs entirely offline by monkeypatching every leaf provider's inner HTTP/edgar
helper.  Re-running on the same cache must produce zero new fetches
(idempotency via cache_runs.status='ok').
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.mark.asyncio
async def test_backfill_writes_then_skips_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """One backfill run populates every domain; second run is fully idempotent.

    Parameters
    ----------
    monkeypatch:
        pytest fixture for patching module-level names.
    tmp_path:
        pytest-provided temporary directory, cleaned up after the test.
    """
    # ── Arrange: temp backtests root + config files ──────────────────────────
    # Per-window layout — the fetcher will land its store at
    # ``<backtests_root>/<window>/store.sqlite``.
    backtests_root = tmp_path / "backtests"
    cache_path     = backtests_root / "smoke" / "store.sqlite"
    settings_path  = tmp_path / "backtest_settings.json"
    windows_path   = tmp_path / "backtest_windows.json"
    watchlist_path = tmp_path / "watchlist.json"

    settings_path.write_text(json.dumps({
        "backtests_root": str(backtests_root),
    }))
    windows_path.write_text(json.dumps({
        "smoke": {"start": "2023-03-06", "end": "2023-03-10", "notes": "smoke"},
    }))
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    # ── Import model types used in stubs ─────────────────────────────────────
    from data.models import (
        CompanyRatios,
        OHLCBar,
        PriceHistory,
    )

    # A single OHLCV bar inside the window — reused across multiple stubs.
    _bar = OHLCBar(
        timestamp=datetime(2023, 3, 8, tzinfo=UTC),
        open=170.0,
        high=175.0,
        low=168.0,
        close=173.0,
        volume=1_000_000.0,
    )

    # ── Stub: OHLCV — yfinance leaf fetch ────────────────────────────────────
    import data.providers.stats.yfinance as yf_mod

    monkeypatch.setattr(
        yf_mod, "_fetch_price_history",
        lambda symbol, period, interval: PriceHistory(ticker=symbol, bars=[_bar]),
    )

    # ── Stub: company_ratios — pit_composite XBRL + price series ─────────────
    import data.providers.company_ratios.pit_composite as pit_mod

    monkeypatch.setattr(
        pit_mod, "_fetch_xbrl_facts",
        lambda symbol, as_of_date: SimpleNamespace(
            long_name="Apple Inc.",
            sector="Technology",
            shares_out=15.7e9,
            eps_ttm=6.0,
            dps_ttm=0.92,
        ),
    )
    monkeypatch.setattr(
        pit_mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=[_bar]),
    )

    # ── Stub: news — Tiingo leaf fetch ───────────────────────────────────────
    monkeypatch.setenv("TIINGO_API_KEY", "fake")
    import data.providers.news.tiingo as tiingo_mod

    monkeypatch.setattr(
        tiingo_mod, "_fetch_news",
        lambda symbol, start, end, key, limit: [
            {
                "title":         "Apple news",
                "description":   "Body.",
                "url":           "https://example.test/article",
                "publishedDate": "2023-03-08T12:00:00+00:00",
                "source":        "example",
            },
        ],
    )

    # ── Stub: politician_trades — FMP senate + house ──────────────────────────
    monkeypatch.setenv("FMP_API_KEY", "fake")
    import data.providers.politician_trades.fmp as fmp_mod

    monkeypatch.setattr(
        fmp_mod, "_fetch_senate",
        lambda symbol, key: [
            {
                "transactionDate": "2023-03-07",
                "disclosureDate":  "2023-03-09",
                "firstName": "Nancy",
                "lastName":  "Pelosi",
                "office":    "House",
                "type":      "Purchase",
                "amount":    "$15,001 - $50,000",
            }
        ],
    )
    monkeypatch.setattr(fmp_mod, "_fetch_house", lambda symbol, key: [])

    # ── Stub: insider_trades, notable_holders, filings — empty lists ──────────
    # These domains return nothing; the cache still records them as status='ok'
    # so the idempotency check can verify they are not fetched again.
    import data.providers.filings.edgar as fl_mod
    import data.providers.insider_trades.edgar as ins_mod
    import data.providers.notable_holders.edgar as nh_mod

    monkeypatch.setattr(
        ins_mod, "_list_form4_filings",
        lambda symbol, lookback, as_of: [],
    )
    monkeypatch.setattr(
        nh_mod, "_list_holder_filings",
        lambda symbol, lookback, lim, as_of: [],
    )
    monkeypatch.setattr(
        fl_mod, "_list_filings",
        lambda symbol, form_types, lim, as_of: [],
    )

    # ── Chdir into tmp_path so _main_async resolves config/ relative paths ───
    monkeypatch.chdir(tmp_path)
    Path("config").mkdir()
    Path("config/backtest_settings.json").write_text(settings_path.read_text())
    Path("config/backtest_windows.json").write_text(windows_path.read_text())

    # ── Act 1: first backfill run ─────────────────────────────────────────────
    from scripts import backtest_fetch

    args = argparse.Namespace(window="smoke", watchlist=str(watchlist_path))
    await backtest_fetch._main_async(args)

    # ── Assert 1: every domain has rows for AAPL within the window ───────────
    from backtest.cache.store import CachedDataStore

    store  = CachedDataStore(cache_path)
    end_dt = datetime(2023, 3, 10, 16, 0, tzinfo=UTC)

    ohlcv_rows = store.read_ohlcv("AAPL", date(2023, 3, 6), date(2023, 3, 10))
    assert len(ohlcv_rows) == 1, "Expected 1 OHLCV bar in the window"

    ratios = store.read_company_ratios("AAPL", end_dt)
    assert isinstance(ratios, CompanyRatios), "Expected a CompanyRatios object"
    assert ratios.long_name == "Apple Inc."

    news_rows = store.read_news("AAPL", end_dt, lookback_days=30)
    assert len(news_rows) == 1, "Expected 1 news article in the window"

    trade_rows = store.read_politician_trades("AAPL", end_dt, lookback_days=90)
    assert len(trade_rows) == 1, "Expected 1 politician trade in the window"

    # ── Act 2: second backfill run with trip-wire stubs ───────────────────────
    # Replace stubs with counters — any call here means the skip-logic failed.
    called_again: dict[str, int] = {"ohlcv": 0, "news": 0, "fmp_senate": 0}

    def _trip_ohlcv(symbol: str, period: str, interval: str) -> PriceHistory:
        """Trip-wire: should never be called on a re-run."""
        called_again["ohlcv"] += 1
        return PriceHistory(ticker=symbol, bars=[])

    def _trip_news(symbol: str, start: str, end: str, key: str, limit: int) -> list:
        """Trip-wire: should never be called on a re-run."""
        called_again["news"] += 1
        return []

    def _trip_fmp(symbol: str, key: str) -> list:
        """Trip-wire: should never be called on a re-run."""
        called_again["fmp_senate"] += 1
        return []

    monkeypatch.setattr(yf_mod,     "_fetch_price_history", _trip_ohlcv)
    monkeypatch.setattr(tiingo_mod, "_fetch_news",          _trip_news)
    monkeypatch.setattr(fmp_mod,    "_fetch_senate",        _trip_fmp)

    await backtest_fetch._main_async(args)

    # ── Assert 2: zero new upstream calls (idempotency) ───────────────────────
    assert called_again == {"ohlcv": 0, "news": 0, "fmp_senate": 0}, (
        f"Re-run made unexpected upstream calls: {called_again}"
    )
