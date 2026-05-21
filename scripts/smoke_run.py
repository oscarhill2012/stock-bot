"""Local end-to-end smoke run: 3 ticks against FakeBroker with real LLMs + data.

Cost: ~$0.20/run (Gemini Flash analysts + Pro strategist).

Usage:
    PYTHONPATH=src python -m scripts.smoke_run
    PYTHONPATH=src python -m scripts.smoke_run --ticks 1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from broker.fake import FakeBroker
from observability.terminal_log import setup_terminal_logging
from orchestrator.stock_picker import get_watchlist
from orchestrator.tick import run_once


def build_runner_args(argv: list[str] | None = None):
    """Parse CLI arguments for the smoke runner.

    Args:
        argv: Optional argument list for testing; defaults to ``sys.argv[1:]``.

    Returns:
        Parsed ``argparse.Namespace`` with ``ticks`` and ``starting_cash`` fields.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticks", type=int, default=3)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    p.add_argument(
        "--log-level",
        choices=("minimal", "info", "debug"),
        default="minimal",
        help=(
            "verbosity of terminal output: minimal (default) = tick banners + "
            "summary rows + WARNINGs; info = also cache + per-branch failures; "
            "debug = full firehose including ADK chatter."
        ),
    )
    return p.parse_args(argv)


async def smoke(ticks: int, starting_cash: float) -> None:
    """Run ``ticks`` sequential ticks against a FakeBroker.

    Fetches today's closing prices for each watchlist ticker so the FakeBroker
    has realistic values to work with, then iterates the tick pipeline.

    Args:
        ticks:         Number of ticks to run.
        starting_cash: Initial cash balance for the FakeBroker.
    """
    tickers = get_watchlist()
    import yfinance as yf
    prices = {}
    for t in tickers:
        h = yf.Ticker(t).history(period="1d")
        prices[t] = float(h["Close"].iloc[-1]) if not h.empty else 100.0

    broker = FakeBroker(starting_cash=starting_cash, prices=prices)

    for i in range(ticks):
        # Pass the label so the tick banner reads "Tick 1/3" etc.
        state = await run_once(broker, tick_label=f"{i + 1}/{ticks}")
        portfolio = await broker.get_portfolio()
        # Print a brief portfolio summary after the tick's own log output.
        print(
            f"\n  Cash: ${portfolio.cash:,.2f}   "
            f"Positions: {len(portfolio.positions)}"
        )


def main(argv: list[str] | None = None) -> int:
    """Entry point for the smoke runner.

    Installs the terminal-log handler (human-readable banners + per-call
    rows) and enables the observability env-var gate before running the
    async smoke loop.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code (0 on success).
    """
    args = build_runner_args(argv)

    # Install human-readable terminal logging BEFORE any agent code runs so
    # the ADK noisy loggers are muted from the first import.
    setup_terminal_logging(mode=args.log_level)

    # Activate the per-call observability callbacks in the analyst branches.
    # The env-var gate keeps backtest runs and unit tests free of this overhead.
    os.environ["STOCKBOT_TERMINAL_LOG"] = "1"

    asyncio.run(smoke(args.ticks, args.starting_cash))
    return 0


if __name__ == "__main__":
    sys.exit(main())
