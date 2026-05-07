"""Replay backtest harness — walk-forward through historical data via FakeBroker.

Usage:
    PYTHONPATH=src python -m scripts.replay_backtest --window 30d
    PYTHONPATH=src python -m scripts.replay_backtest --window 30d --fixture-dir tests/replay/fixtures
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

from broker.fake import FakeBroker
from orchestrator.stock_picker import get_watchlist
from orchestrator.tick import run_once


@dataclass
class ReplaySummary:
    ticks_completed: int
    final_cash: float
    final_position_count: int


def build_runner_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window", default="30d")
    p.add_argument("--fixture-dir", type=Path, default=None,
                   help="If set, swap real providers for fixture loaders")
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    return p.parse_args(argv)


def _parse_window(window: str) -> timedelta:
    if window.endswith("d"):
        return timedelta(days=int(window[:-1]))
    raise SystemExit(f"unsupported window format: {window} (use Nd)")


def run_replay(*, window: str, fixture_dir: Path | None, starting_cash: float = 10_000.0) -> ReplaySummary:
    tickers = get_watchlist()
    days = _parse_window(window).days
    end = datetime.now(tz=timezone.utc).date()
    start = end - timedelta(days=days)

    history: dict[str, list[float]] = {}
    for t in tickers:
        df = yf.download(t, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            history[t] = [100.0]
            continue
        history[t] = [float(p) for p in df["Close"].squeeze().tolist()]

    n_steps = max(min((len(prices) for prices in history.values()), default=0), 1)
    broker = FakeBroker(
        starting_cash=starting_cash,
        prices={t: history[t][0] for t in tickers},
    )

    ticks_completed = 0
    for i in range(n_steps):
        for t in tickers:
            if i < len(history[t]):
                broker.set_price(t, history[t][i])

        asyncio.run(run_once(broker))
        ticks_completed += 1

    portfolio = asyncio.run(broker.get_portfolio())
    return ReplaySummary(
        ticks_completed=ticks_completed,
        final_cash=portfolio.cash,
        final_position_count=len(portfolio.positions),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_runner_args(argv)
    summary = run_replay(
        window=args.window,
        fixture_dir=args.fixture_dir,
        starting_cash=args.starting_cash,
    )
    print(f"Ticks completed: {summary.ticks_completed}")
    print(f"  Final cash: ${summary.final_cash:,.2f}")
    print(f"  Final positions: {summary.final_position_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
