"""Static watchlist from config/watchlist.json."""
from __future__ import annotations

import json
from pathlib import Path

# Project root is two levels above src/orchestrator/.
_WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.json"


def get_watchlist() -> list[str]:
    """Return the watchlist tickers."""
    if not _WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"Watchlist not found: {_WATCHLIST_PATH}")
    with _WATCHLIST_PATH.open() as f:
        return json.load(f)["tickers"]
