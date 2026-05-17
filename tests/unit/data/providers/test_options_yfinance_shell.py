"""Unit tests for ``data.providers.options.yfinance`` (live-only shell).

The options provider is intentionally a live-only stub for v1 — it returns an
empty dict for any historical ``as_of`` (anything earlier than today), so the
backtest replay never blocks on a missing options cache entry.

Key invariants tested
---------------------
- **Historical soft-fail**: ``fetch`` returns ``{}`` when ``as_of`` is a past
  date (the normal backtest path).
- **Today soft-fail**: ``fetch`` also returns ``{}`` for today's date — the
  live wiring is deferred to a follow-up spec, so even a same-day call returns
  empty rather than raising.
- **Signature compliance**: the function accepts ``**kwargs`` and does not
  raise on unexpected keyword arguments (dispatcher compatibility).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

# ── Soft-fail on historical as_of (primary backtest path) ────────────────────

@pytest.mark.asyncio
async def test_options_shell_returns_empty_for_backtest_as_of():
    """Provider must return ``{}`` for a clearly historical ``as_of`` date.

    This is the invariant the spec mandates: any call with ``as_of`` in the
    past must soft-fail rather than raise.  The backtest replay depends on this
    behaviour — a missing options cache entry must not abort a tick.
    """
    from data.providers.options import yfinance as mod

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10))

    # Accept any of: empty dict, None, or an object with is_no_data=True.
    assert out == {} or out is None or getattr(out, "is_no_data", False)


@pytest.mark.asyncio
async def test_options_shell_returns_empty_for_recent_historical_as_of():
    """Provider must return ``{}`` even for as_of dates close to today.

    Exercises the ``as_of < date.today()`` branch with a date only 7 days
    in the past — confirming the guard is on the comparison, not a fixed
    historical horizon.
    """
    from data.providers.options import yfinance as mod

    recent_past = date.today() - timedelta(days=7)
    out = await mod.fetch("TSLA", as_of=recent_past)

    assert out == {} or out is None or getattr(out, "is_no_data", False)


# ── Soft-fail for today (live wiring not yet implemented) ─────────────────────

@pytest.mark.asyncio
async def test_options_shell_returns_empty_for_today():
    """Provider returns ``{}`` even when ``as_of`` is today.

    Live wiring is deferred to a follow-up spec; the shell therefore returns
    empty for all dates, including same-day calls.
    """
    from data.providers.options import yfinance as mod

    out = await mod.fetch("MSFT", as_of=date.today())

    assert out == {} or out is None or getattr(out, "is_no_data", False)


# ── Dispatcher compatibility — absorbs extra kwargs ───────────────────────────

@pytest.mark.asyncio
async def test_options_shell_absorbs_extra_kwargs():
    """Provider must not raise when the dispatcher passes unexpected kwargs.

    The ``dispatch`` function in ``data.registry`` forwards all caller kwargs
    to the provider function.  Providers declare ``**_`` to absorb unknowns;
    this test confirms that contract is honoured.
    """
    from data.providers.options import yfinance as mod

    # Pass kwargs that a future live caller might supply — the shell ignores them.
    out = await mod.fetch(
        "NVDA",
        as_of=date(2023, 6, 1),
        expiry="2023-07-21",
        option_type="call",
    )

    assert out == {} or out is None or getattr(out, "is_no_data", False)
