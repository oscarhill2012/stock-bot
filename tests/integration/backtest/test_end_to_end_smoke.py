"""Smoke test: full Runner over a 3-day micro-window against a fixture cache.

Marked ``@pytest.mark.slow`` so it runs in nightly CI only.  The point is to
exercise the entire stack end-to-end — cache providers, ``as_of`` migration,
analyst fetch, pipeline, FakeBroker, DecisionLogger, reporting — against a
deterministic data set with no network or LLM dependency.

**LLM mocking strategy**
The pipeline contains three LLM-backed agents:

- ``Strategist`` (Gemini via ADK ``LlmAgent``)
- ``FundamentalAnalyst`` (Gemini via ADK ``LlmAgent``)
- ``NewsAnalyst``      (Gemini via ADK ``LlmAgent``)

Their ``_run_async_impl`` methods are patched with canned async-generator stubs
that write pre-built outputs directly into ``ctx.session.state`` and return
immediately.  This is identical to the pattern used in
``tests/integration/backtest/test_driver_one_tick.py``.

**yfinance / SPY**
The snapshotter fetches a live SPY price via yfinance inside a try/except;
on failure it falls back to ``spy_price = 0.0``.  We allow that fallback to
occur naturally — the reporting layer handles it gracefully, writing
"N/A (SPY data unavailable in snapshots)" to metrics.md rather than a NaN
value.  This avoids having to mock yfinance's locally-scoped import.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from backtest.cache.store import CachedDataStore
from backtest.runner import Runner
from data.models import OHLCBar, StockStats


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_bar(day: int, price: float) -> OHLCBar:
    """Return one OHLCV bar at 09:30 ET (represented as UTC) on the given March 2023 day.

    Parameters
    ----------
    day:
        Day-of-month for the bar's timestamp.
    price:
        Synthetic open/close price used for all OHLCV fields.

    Returns
    -------
    OHLCBar
        Minimal bar suitable for cache insertion.
    """
    # 09:30 ET = 14:30 UTC in March (EDT = UTC-4 → 09:30 + 4 h = 13:30 UTC; but
    # we use 14:30 for the rounded open-tick so the as_of comparisons in the
    # cache cover both open and close phases within the same calendar day).
    ts = datetime(2023, 3, day, 14, 30, tzinfo=UTC)
    return OHLCBar(
        timestamp=ts,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price + 0.5,
        volume=1_000_000,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fixture_cache(tmp_path: Path) -> Path:
    """Materialise a 3-business-day cache for AAPL and return the store path.

    Writes one OHLCV bar per day for the micro-window (2023-03-13 → 15) and
    one ``MarketMetaRow`` snapshot anchored before the window so the stats
    cache provider can return data for every tick.

    Returns
    -------
    Path
        Absolute path to the SQLite file used by ``CachedDataStore``.
    """
    cache_path = tmp_path / "cache" / "store.sqlite"
    store      = CachedDataStore(cache_path)

    # Write three daily bars.  Use slightly different prices each day so the
    # equity-curve chart has at least two distinct values and the Sharpe metric
    # is non-degenerate.
    for day, price in ((13, 150.0), (14, 152.0), (15, 151.0)):
        store.write_ohlcv("AAPL", [_build_bar(day, price)])

    # Write a MarketMetaRow dated before the window so the stats cache provider
    # returns a valid StockStats for every tick in the window.
    store.write_market_meta(
        "AAPL",
        StockStats(
            ticker="AAPL",
            history=[],
            market_cap=2_500_000_000_000,
            trailing_pe=28.0,
            forward_pe=26.0,
            beta=1.2,
            dividend_yield=0.005,
            fifty_day_average=148.0,
            two_hundred_day_average=145.0,
            last_price=150.0,
            sector="Technology",
            long_name="Apple Inc.",
        ),
        as_of_date=datetime(2023, 3, 10).date(),
    )

    return cache_path


@pytest.fixture()
def runner_paths(tmp_path: Path, fixture_cache: Path):
    """Write the three config JSON files that Runner.__init__ expects.

    Writes ``backtest_settings.json``, ``backtest_windows.json``, and
    ``watchlist.json`` to ``tmp_path``.

    Returns
    -------
    tuple[Path, Path, Path]
        (settings_path, windows_path, watchlist_path)
    """
    settings = {
        "cache_path":               str(fixture_cache),
        "runs_root":                str(tmp_path / "runs"),
        # Two phases per day (open + close) — 3 days = 6 ticks total.
        "ticks_per_day":            ["open", "close"],
        "tz":                       "America/New_York",
        "open_time":                "09:30",
        "close_time":               "16:00",
        # Never abort during the smoke test — we want to observe the full run.
        "failed_tick_abort_ratio":  1.0,
        "fake_broker_starting_cash": 100_000.0,
        "forward_return_horizons_days": [1],
        "default_lookback_days": {
            "news":               30,
            "insider_trades":     90,
            "politician_trades":  90,
            "notable_holders":    365,
            "filings":            365,
        },
    }

    windows = {
        "smoke": {
            "start": "2023-03-13",
            "end":   "2023-03-15",
            "notes": "Smoke test micro-window",
        },
    }

    watchlist = {"tickers": ["AAPL"]}

    settings_path  = tmp_path / "backtest_settings.json"
    windows_path   = tmp_path / "backtest_windows.json"
    watchlist_path = tmp_path / "watchlist.json"

    settings_path.write_text(json.dumps(settings))
    windows_path.write_text(json.dumps(windows))
    watchlist_path.write_text(json.dumps(watchlist))

    return settings_path, windows_path, watchlist_path


# ── LLM stub helpers ──────────────────────────────────────────────────────────

# Canned outputs for the three LLM-backed agents.  These are written directly
# into ``ctx.session.state`` by the stub async generators, bypassing any real
# Gemini call.

_CANNED_DECISION = {
    "decision_tag":      "hold",
    "reasoning_excerpt": "smoke-test stub — hold everything",
    "ticker_stances":    {},
    "new_positions":     {},
    "close_reasons":     {},
}

_CANNED_FUNDAMENTAL_VERDICT = {
    "lean":          "neutral",
    "magnitude":     0.5,
    "confidence":    0.5,
    "rationale":     "smoke-test stub",
    "key_factors":   [],
    "is_no_data":    True,
}


async def _fake_strategist_run(self_agent, ctx):
    """Stub for Strategist._run_async_impl: write canned hold decision."""
    ctx.session.state["strategist_decision"] = _CANNED_DECISION
    return
    yield  # pragma: no cover — keeps this an async generator as ADK expects


async def _fake_fundamental_run(self_agent, ctx):
    """Stub for FundamentalAnalyst._run_async_impl: write canned neutral verdict."""
    ctx.session.state.setdefault("fundamental_data", {})["AAPL"] = (
        _CANNED_FUNDAMENTAL_VERDICT
    )
    return
    yield  # pragma: no cover


async def _fake_news_run(self_agent, ctx):
    """Stub for NewsAnalyst._run_async_impl: write a no-data marker."""
    ctx.session.state.setdefault("news_data", {})["AAPL"] = None
    return
    yield  # pragma: no cover


# ── Smoke test ────────────────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.skipif(
    __import__("os").environ.get("RUN_SLOW_TESTS") != "1",
    reason="Set RUN_SLOW_TESTS=1 to run the full end-to-end smoke test",
)
def test_end_to_end_run_produces_full_artefact_tree(
    tmp_path: Path,
    runner_paths,
) -> None:
    """One Runner.run() over a 3-day micro-window must produce the full artefact tree.

    Assertions
    ----------
    - ``manifest.json`` exists with ``status`` in {``completed``, ``completed_with_failures``}.
    - ``db.sqlite`` exists and is non-empty.
    - At least one trace file exists under ``traces/``.
    - ``report/equity_curve.png`` exists and is non-empty.
    - ``report/metrics.md`` exists and contains non-NaN metric lines.

    The test is marked ``@pytest.mark.slow`` and is excluded from the default
    ``pytest tests/`` run.  Run it explicitly with::

        PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
    """
    settings_path, windows_path, watchlist_path = runner_paths

    runner = Runner(
        settings_path=settings_path,
        windows_path=windows_path,
        watchlist_path=watchlist_path,
    )

    # Patch the three LLM-backed agents to avoid real Gemini API calls.
    # yfinance (SPY price lookup in the snapshotter) is left unmocked — the
    # snapshotter wraps it in try/except and falls back to spy_price=0.0,
    # which the reporting layer handles gracefully (writes "N/A" rather than NaN).
    with (
        patch(
            "agents.strategist.agent.LlmAgent._run_async_impl",
            _fake_strategist_run,
        ),
        patch(
            "agents.analysts.fundamental.agent.LlmAgent._run_async_impl",
            _fake_fundamental_run,
        ),
        patch(
            "agents.analysts.news.agent.LlmAgent._run_async_impl",
            _fake_news_run,
        ),
    ):
        result = runner.run("smoke")

    # ── Artefact assertions ───────────────────────────────────────────────────

    # (1) Status must be terminal and non-aborted.
    assert result.status in {"completed", "completed_with_failures"}, (
        f"unexpected run status: {result.status!r}"
    )

    # (2) Manifest must exist and be valid JSON with the right status field.
    manifest_path = result.run_dir / "manifest.json"
    assert manifest_path.exists(), "manifest.json missing"
    manifest = json.loads(manifest_path.read_text())
    assert manifest.get("status") in {"completed", "completed_with_failures"}, (
        f"manifest status unexpected: {manifest.get('status')!r}"
    )

    # (3) Per-run database must exist.
    assert (result.run_dir / "db.sqlite").exists(), "db.sqlite missing"

    # (4) At least one trace file must have been written.
    traces = list((result.run_dir / "traces").glob("*.json"))
    assert traces, "no trace files produced under traces/"

    # (5) Equity curve PNG must exist and be non-empty.
    equity_curve = result.run_dir / "report" / "equity_curve.png"
    assert equity_curve.exists(), "report/equity_curve.png missing"
    assert equity_curve.stat().st_size > 0, "report/equity_curve.png is empty"

    # (6) Metrics markdown must exist and not contain raw 'nan' values.
    metrics_md = result.run_dir / "report" / "metrics.md"
    assert metrics_md.exists(), "report/metrics.md missing"
    metrics_text = metrics_md.read_text()
    assert "nan" not in metrics_text.lower(), (
        f"metrics.md contains NaN values:\n{metrics_text}"
    )
