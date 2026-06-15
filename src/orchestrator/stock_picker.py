"""Static watchlist from config/watchlist.json.

The watchlist file supports two formats:

  Legacy (flat list of strings):
    {"tickers": ["AAPL", "MSFT", ...]}

  Extended (list of objects with symbol + name):
    {"tickers": [{"symbol": "AAPL", "name": "Apple"}, ...]}

Both formats are normalised on load.  All existing callers receive a
``list[str]`` of ticker symbols unchanged.  The extended format additionally
exposes company names via :func:`get_watchlist_with_names`.
"""
from __future__ import annotations

import json
from pathlib import Path

# Project root is two levels above src/orchestrator/.
_WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.json"


def _load_raw() -> list[dict | str]:
    """Read and return the raw ``tickers`` list from watchlist.json.

    Returns
    -------
    list[dict | str]
        The raw list items — either plain strings (legacy format) or dicts
        with at least a ``symbol`` key (extended format).

    Raises
    ------
    FileNotFoundError
        If the watchlist config file does not exist.
    """
    if not _WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"Watchlist not found: {_WATCHLIST_PATH}")

    with _WATCHLIST_PATH.open() as f:
        return json.load(f)["tickers"]


def normalise_to_symbols(raw: list[dict | str]) -> list[str]:
    """Reduce a raw ``tickers`` list to a flat list of symbol strings.

    Accepts either the legacy (list-of-strings) or the extended
    (list-of-objects) representation and returns just the symbols.  Centralising
    this here ensures every caller — including those that read watchlist.json
    directly with their own path (e.g. the backtest runner) — normalises the
    two formats identically, rather than re-deriving the logic and drifting.

    Parameters
    ----------
    raw:
        The raw ``tickers`` list as parsed from watchlist.json — each item is
        either a plain string (legacy) or a dict with at least a ``symbol`` key
        (extended format).

    Returns
    -------
    list[str]
        Ticker symbols in their original order.
    """
    return [
        item if isinstance(item, str) else item["symbol"]
        for item in raw
    ]


def get_watchlist() -> list[str]:
    """Return the watchlist tickers as a flat list of symbol strings.

    Normalises both the legacy (list-of-strings) and the extended
    (list-of-objects) formats, so existing callers are unaffected by the
    format upgrade.

    Returns
    -------
    list[str]
        Ticker symbols in the order they appear in watchlist.json.
    """
    return normalise_to_symbols(_load_raw())


def get_watchlist_with_names() -> list[dict[str, str]]:
    """Return the watchlist as a list of ``{"symbol": ..., "name": ...}`` dicts.

    For the legacy format (plain strings), the ``name`` field defaults to the
    symbol itself — callers should treat it as "best available".

    Returns
    -------
    list[dict[str, str]]
        Each entry has ``"symbol"`` and ``"name"`` keys.
    """
    raw = _load_raw()

    result: list[dict[str, str]] = []

    for item in raw:
        if isinstance(item, str):
            # Legacy format: no name available — fall back to the symbol.
            result.append({"symbol": item, "name": item})
        else:
            result.append({
                "symbol": item["symbol"],
                "name":   item.get("name", item["symbol"]),
            })

    return result
