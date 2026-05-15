"""Strict-mode regression: a deliberately broken driver must abort, not leak.

The driver is monkeypatched to *not* set ``state["as_of"]`` for the tick.
With ``STOCKBOT_STRICT_AS_OF=1`` the pipeline must raise
``AsOfRequiredError`` rather than silently falling back to wall-clock time.

Marked ``slow`` because it boots the live pipeline; excluded from the
default pytest run.
"""
from __future__ import annotations

import pytest

from data.timeguard import AsOfRequiredError


@pytest.mark.slow
def test_strict_mode_aborts_when_driver_omits_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipeline call without ``as_of`` in strict mode raises AsOfRequiredError."""
    monkeypatch.setenv("STOCKBOT_STRICT_AS_OF", "1")

    import data  # public wrapper module

    # Live wrappers call resolve_as_of(allow_wallclock=True); strict env vetoes.
    with pytest.raises(AsOfRequiredError):
        # ``get_price_history`` is one of the eight wrappers — passing as_of=None must abort.
        import asyncio
        asyncio.run(data.get_price_history("AAPL", as_of=None))


def test_live_mode_allows_wallclock_when_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With STOCKBOT_STRICT_AS_OF unset, live wrappers still allow fallback."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    from data.timeguard import resolve_as_of
    got = resolve_as_of(None, allow_wallclock=True, site="ohlcv")
    assert got is not None
