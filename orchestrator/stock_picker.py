"""Static watchlist from config/watchlist.json."""
from __future__ import annotations

import json
from pathlib import Path

_WATCHLIST_PATH = Path(__file__).parent.parent / "config" / "watchlist.json"


def get_watchlist() -> list[str]:
    """Return the watchlist tickers."""
    if not _WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"Watchlist not found: {_WATCHLIST_PATH}")
    with _WATCHLIST_PATH.open() as f:
        return json.load(f)["tickers"]
