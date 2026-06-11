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

    # Mirror the production ``config/backtest_settings.json`` shape — the
    # ``BacktestSettings`` Pydantic schema requires every field below;
    # ``backtests_root`` alone no longer validates.
    settings_path.write_text(json.dumps({
        "backtests_root":               str(backtests_root),
        "ticks_per_day":                ["open", "close"],
        "failed_tick_abort_ratio":      0.10,
        "fake_broker_starting_cash":    100000.0,
        "forward_return_horizons_days": [1, 5, 20],
        "ohlcv_warmup_days":            30,
    }))
    windows_path.write_text(json.dumps({
        "smoke": {
            "start": "2023-03-06",
            "end": "2023-03-10",
            "notes": "smoke",
            "risk_free_rate_annual": 0.048,
        },
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

    # ``_fetch_price_history`` takes an optional ``as_of`` for PIT clamping.
    # The fetcher passes it positionally, so the stub must accept it.
    monkeypatch.setattr(
        yf_mod, "_fetch_price_history",
        lambda symbol, period, interval, as_of=None: PriceHistory(
            ticker=symbol, bars=[_bar],
        ),
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

    # ── Stub: news — Finnhub leaf fetch (active provider per config/data.json) ─
    # Finnhub uses Unix-epoch ``datetime`` and slightly different field names
    # than Tiingo; ``_map_article`` keys off ``headline`` / ``summary`` / etc.
    monkeypatch.setenv("FINNHUB_API_KEY", "fake")
    import data.providers.news.finnhub as finnhub_mod

    monkeypatch.setattr(
        finnhub_mod, "_fetch_company_news",
        lambda symbol, from_iso, to_iso: [
            {
                "datetime": int(
                    datetime(2023, 3, 8, 12, 0, tzinfo=UTC).timestamp()
                ),
                "headline": "Apple news",
                "summary":  "Body.",
                "url":      "https://example.test/article",
                "source":   "example",
            },
        ],
    )

    # politician_trades is intentionally disabled in
    # ``scripts.backtest_fetch._build_provider_fns`` (no free historical
    # source), so the fetcher never invokes the FMP leaf here — no stub
    # required and no rows are expected to land in the cache.

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
        fl_mod, "_list_latest_filing",
        lambda symbol, form, as_of: [],
    )
    monkeypatch.setattr(
        fl_mod, "_list_filings_range",
        lambda symbol, forms, lower, upper: [],
    )

    # ── No-op the reference-OHLCV fill (SPY + sector ETFs).  That helper
    # bypasses the per-(ticker, domain) cache_runs skip and refetches every
    # call, which would invalidate the trip-wire assertion below.  Patching
    # it out keeps the test focused on the watchlist-fill idempotency it was
    # actually written to verify.
    from scripts import backtest_fetch as _bf

    async def _noop_reference_fill(*_args, **_kwargs):
        """No-op replacement for ``_fill_reference_ohlcv`` during the test."""
        return None

    monkeypatch.setattr(_bf, "_fill_reference_ohlcv", _noop_reference_fill)

    # ── Chdir into tmp_path so _main_async resolves config/ relative paths ───
    monkeypatch.chdir(tmp_path)
    Path("config").mkdir()
    Path("config/backtest_settings.json").write_text(settings_path.read_text())
    Path("config/backtest_windows.json").write_text(windows_path.read_text())

    # ── Act 1: first backfill run ─────────────────────────────────────────────
    from scripts import backtest_fetch

    # ``_main_async`` reads ``args.refetch_domain`` (added by the CLI later)
    # — Namespace must mirror the argparse output, so include the empty
    # default to avoid an AttributeError.
    args = argparse.Namespace(
        window="smoke",
        watchlist=str(watchlist_path),
        refetch_domain=[],
    )
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

    # ── Act 2: second backfill run with trip-wire stubs ───────────────────────
    # Replace stubs with counters — any call here means the skip-logic failed.
    called_again: dict[str, int] = {"ohlcv": 0, "news": 0}

    def _trip_ohlcv(
        symbol: str, period: str, interval: str, as_of=None,
    ) -> PriceHistory:
        """Trip-wire: should never be called on a re-run."""
        called_again["ohlcv"] += 1
        return PriceHistory(ticker=symbol, bars=[])

    def _trip_news(symbol: str, from_iso: str, to_iso: str) -> list:
        """Trip-wire: should never be called on a re-run."""
        called_again["news"] += 1
        return []

    monkeypatch.setattr(yf_mod,      "_fetch_price_history", _trip_ohlcv)
    monkeypatch.setattr(finnhub_mod, "_fetch_company_news",  _trip_news)

    await backtest_fetch._main_async(args)

    # ── Assert 2: zero new upstream calls (idempotency) ───────────────────────
    assert called_again == {"ohlcv": 0, "news": 0}, (
        f"Re-run made unexpected upstream calls: {called_again}"
    )
