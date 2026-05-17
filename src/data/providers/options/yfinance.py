"""yfinance options — live-only shell.

Snapshot-only; not PIT-correct.  Row #4 is dropped from the v1 backtest per
decision 7.1 of docs/Phase7-pre-backtest-cleanup/providers-and-silent-gaps-v1.md.

This module exists so the registry has a non-empty entry for the ``options``
domain.  It returns an empty dict for any ``as_of`` in the past (the normal
backtest-replay path), and also for same-day calls — the live wiring that
would call ``yfinance.Ticker(symbol).option_chain(expiry)`` is deferred to a
follow-up spec.

Why live-only?
--------------
yfinance option chains reflect the *current* state of the market: strikes,
implied volatility, and open interest are all point-in-time snapshots that
change tick by tick.  There is no historical replay endpoint in yfinance, so
any backtest that consumed options data would silently receive today's values
rather than the values that existed on the simulated date.  Returning ``{}``
makes the data absence explicit and allows analyst agents to degrade gracefully
rather than ingest anachronistic data.

Registry parameters
-------------------
The ``upstream="yfinance"`` limiter is shared with ``stats/yfinance.py`` and
``analyst_consensus/yfinance.py``.  Rate-limit parameters must match those
existing declarations exactly; the registry raises ``ValueError`` on conflict.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from data.registry import register

# ── Rate-limit parameters ─────────────────────────────────────────────────────

# Must match the existing yfinance limiter declared in stats/yfinance.py and
# analyst_consensus/yfinance.py.  The registry enforces consistency — any
# mismatch raises ``ValueError`` at import time.
_RATE_PER_MINUTE = 60
_BURST            = 30


# ── Provider ──────────────────────────────────────────────────────────────────

@register(
    domain="options",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=_RATE_PER_MINUTE,
    burst=_BURST,
)
async def fetch(symbol: str, *, as_of: date, **_: Any) -> dict[str, Any]:
    """Fetch options data for ``symbol`` — live-only shell; returns ``{}`` for historical dates.

    For backtest replay (``as_of`` in the past) this function returns an empty
    dict immediately without making any network call.  The caller is expected
    to treat an empty options dict as "no options data available" and proceed
    without it.

    For same-day calls the function also returns ``{}`` — live wiring via
    ``yfinance.Ticker(symbol).option_chain(expiry)`` is deferred to a
    follow-up spec.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        The simulation or backtest date.  When this is earlier than today the
        function short-circuits and returns ``{}``.
    **_:
        Absorbs extra keyword arguments forwarded by ``dispatch`` (e.g.
        ``expiry``, ``option_type``) so callers do not need to filter kwargs.

    Returns
    -------
    dict[str, Any]
        Always ``{}`` in v1 (live wiring deferred).
    """
    # Short-circuit for any historical or same-day as_of.
    # The ``<`` comparison covers all past dates; ``==`` covers today.
    # Both paths return ``{}`` in this shell — the live implementation will
    # replace the today/future branch in a follow-up spec.
    if as_of < date.today():
        return {}

    # Live-mode placeholder — same-day call also returns empty until the live
    # wiring spec is implemented.
    return {}
