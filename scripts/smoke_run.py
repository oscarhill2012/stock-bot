"""Local end-to-end smoke run: 3 ticks against FakeBroker with real LLMs + data.

Cost: ~$0.20/run (Gemini Flash analysts + Pro strategist).

Usage:
    PYTHONPATH=src python -m scripts.smoke_run
    PYTHONPATH=src python -m scripts.smoke_run --ticks 1
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from broker.fake import FakeBroker
from orchestrator.stock_picker import get_watchlist
from orchestrator.tick import run_once


def build_runner_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticks", type=int, default=3)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    return p.parse_args(argv)


async def smoke(ticks: int, starting_cash: float) -> None:
    tickers = get_watchlist()
    import yfinance as yf
    prices = {}
    for t in tickers:
        h = yf.Ticker(t).history(period="1d")
        prices[t] = float(h["Close"].iloc[-1]) if not h.empty else 100.0

    broker = FakeBroker(starting_cash=starting_cash, prices=prices)

    for i in range(ticks):
        print(f"\n=== Tick {i+1}/{ticks} ===")
        state = await run_once(broker)
        executions = state.get("executions", []) if isinstance(state, dict) else []
        print(f"  Executions: {len(executions)}")
        portfolio = await broker.get_portfolio()
        print(f"  Cash: ${portfolio.cash:,.2f}   Positions: {len(portfolio.positions)}")


def main(argv: list[str] | None = None) -> int:
    args = build_runner_args(argv)
    asyncio.run(smoke(args.ticks, args.starting_cash))
    return 0


if __name__ == "__main__":
    sys.exit(main())
