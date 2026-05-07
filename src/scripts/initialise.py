"""Boot the StockBot: pre-flight, anchor snapshot, scheduler resume.

Usage:
    PYTHONPATH=src python -m scripts.initialise --capital 10000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from lifecycle.initialise import (
    BrokerCashMismatch,
    EnvVarMissingError,
    NonEmptyTablesError,
    initialise,
)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def _build_broker(mode: str):
    """Return a Broker instance — tests monkey-patch this."""
    import httpx
    from broker.trading212 import Trading212Broker
    return Trading212Broker(
        mode=mode,
        api_key=os.environ["TRADING212_API_KEY"],
        http_client=httpx.AsyncClient(),
        instrument_map={},
    )


async def main_async(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--capital", type=float, required=True)
    p.add_argument("--broker-mode", default="paper", choices=["paper", "live"])
    p.add_argument("--watchlist", default="src/config/watchlist.json")
    p.add_argument("--scheduler-job", default=os.environ.get("SCHEDULER_JOB"))
    args = p.parse_args(argv)

    db_url = args.db_url or _resolve_default_db_url()
    wl_path = Path(args.watchlist)
    if not wl_path.exists():
        print(f"Watchlist not found: {wl_path}", file=sys.stderr)
        return 1
    watchlist = json.loads(wl_path.read_text())["tickers"]

    broker = _build_broker(args.broker_mode)

    try:
        result = await initialise(
            db_url=db_url,
            starting_capital=args.capital,
            broker_mode=args.broker_mode,
            watchlist=watchlist,
            broker=broker,
            scheduler_job=args.scheduler_job,
        )
    except (NonEmptyTablesError, EnvVarMissingError, BrokerCashMismatch) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    print(f"✓ Cloud SQL reachable")
    print(f"✓ Live tables empty")
    print(f"✓ Required env vars set")
    print(f"✓ Trading 212 reachable, cash ${args.capital:,.2f} matches expected")
    print(f"✓ Wrote anchor snapshot (SPY ${result.anchor_spy_price:.2f})")
    if args.scheduler_job:
        print(f"✓ Resumed Cloud Scheduler job {args.scheduler_job}")
    print()
    print(f"Bot is live ({args.broker_mode} mode). Watchlist: {len(watchlist)} tickers.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
