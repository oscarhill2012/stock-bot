"""Shared helpers for politician-trade providers (FMP and Quiver Quant).

Both providers work with the same canonical data shapes — a ``TradeSide``
literal, ``YYYY-MM-DD`` date strings, and ``"$15,001 - $50,000"`` amount
ranges — so the parsing helpers are identical across providers.  Extracting
them here removes duplication and ensures both providers coerce identically.

Providers that depend on these helpers:
    - ``data.providers.politician_trades.fmp``
    - ``data.providers.politician_trades.quiver``
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from data.models import TradeSide

# ---------------------------------------------------------------------------
# Canonical mapping from free-text transaction-type strings → TradeSide.
#
# Both FMP and Quiver use the same vocabulary in their ``type``/``Transaction``
# fields, so one shared map covers both providers.
# ---------------------------------------------------------------------------
_SIDE_MAP: dict[str, TradeSide] = {
    "purchase":       "buy",
    "buy":            "buy",
    "sale":           "sell",
    "sale (full)":    "sell",
    "sale (partial)": "sell",
    "sell":           "sell",
    "exchange":       "exchange",
}


def _coerce_side(raw: Any) -> TradeSide:
    """Map a provider's transaction-type string to the canonical ``TradeSide`` literal.

    Parameters
    ----------
    raw:
        The raw transaction-type value from the provider JSON (``type``,
        ``Transaction``, etc.).  May be ``None`` or any non-string type.

    Returns
    -------
    TradeSide
        Canonical side; ``"unknown"`` when unrecognised or absent.
    """
    if not raw:
        return "unknown"
    return _SIDE_MAP.get(str(raw).strip().lower(), "unknown")


def _parse_date(raw: Any) -> date | None:
    """Coerce ``YYYY-MM-DD`` strings (or ISO-8601 with timezone) into ``date``.

    Tries ISO-8601 first (handles ``"2024-01-15T00:00:00Z"`` style), then
    falls back to a plain ``"%Y-%m-%d"`` parse on the first 10 characters.
    Returns ``None`` on any parse failure or absent input.

    Parameters
    ----------
    raw:
        The raw date value from the provider JSON.  May be a string,
        ``None``, or any other type.

    Returns
    -------
    date | None
        Parsed date, or ``None`` if the value is missing or unparseable.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_amount_range(raw: Any) -> tuple[float | None, float | None]:
    """Parse ``"$15,001 - $50,000"``-style amount strings into a numeric range.

    Both providers use the same disclosure format for trade sizes.  Strips
    ``$`` and ``,`` separators before splitting on ``-``.

    Parameters
    ----------
    raw:
        The raw amount / range string from the provider JSON (``amount``,
        ``Range``, ``Amount``, ``Trade_Size_USD``, etc.).

    Returns
    -------
    tuple[float | None, float | None]
        ``(min_usd, max_usd)`` parsed from the range string, or
        ``(None, None)`` when the input is absent or unparseable.
    """
    if raw is None:
        return None, None

    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None, None

    parts = [p.strip() for p in text.split("-")]
    try:
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
        # Single-value string (e.g. "$1000") — use as both bounds.
        return float(parts[0]), float(parts[0])
    except ValueError:
        return None, None
