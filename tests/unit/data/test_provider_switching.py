"""Regression guard: switching ``config/data.json`` providers must require zero code changes.

This is the enforcement layer for the "one config flip" feedback rule.  Each
test patches a single ``providers[domain]`` entry in the in-process config and
asserts that ``_dispatch`` routes to the replacement coroutine — not the
previously configured one.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_news_swap_finnhub_to_tiingo_uses_tiingo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting providers[news] to 'tiingo' must route dispatch to the Tiingo coroutine.

    Monkeypatches the internal ``_fetch_news`` function in the Tiingo module so
    the test never makes a real HTTP call.  The ``TIINGO_API_KEY`` env-var is
    also patched so the provider's soft-fail early-exit guard does not trigger.
    """
    import data.providers.news.tiingo as tiingo_mod
    from data import _dispatch
    from data.config import get_config

    monkeypatch.setenv("TIINGO_API_KEY", "fake")

    # Mutate the in-process config; restore in finally so other tests are unaffected.
    cfg = get_config()
    original = cfg.providers["news"]
    cfg.providers["news"] = "tiingo"

    called: dict[str, str | None] = {"who": None}

    def fake_tiingo_fetch(
        symbol: str, start: str, end: str, key: str, limit: int
    ) -> list:
        """Record which provider was invoked."""
        called["who"] = "tiingo"
        return []

    monkeypatch.setattr(tiingo_mod, "_fetch_news", fake_tiingo_fetch)

    try:
        await _dispatch(
            "news",
            "AAPL",
            from_date=datetime(2023, 3, 1, tzinfo=UTC).date(),
            to_date=datetime(2023, 3, 15, tzinfo=UTC).date(),
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
        )
    finally:
        cfg.providers["news"] = original

    assert called["who"] == "tiingo"


@pytest.mark.asyncio
async def test_news_swap_back_to_finnhub_uses_finnhub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flipping back to 'finnhub' routes to the Finnhub coroutine — no code change.

    Monkeypatches ``_fetch_company_news`` so no real Finnhub API call is made.
    """
    import data.providers.news.finnhub as finnhub_mod
    from data import _dispatch
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["news"]
    cfg.providers["news"] = "finnhub"

    called: dict[str, str | None] = {"who": None}

    def fake_finnhub_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        """Record which provider was invoked."""
        called["who"] = "finnhub"
        return []

    monkeypatch.setattr(finnhub_mod, "_fetch_company_news", fake_finnhub_fetch)

    try:
        await _dispatch(
            "news",
            "AAPL",
            from_date=datetime(2023, 3, 1, tzinfo=UTC).date(),
            to_date=datetime(2023, 3, 15, tzinfo=UTC).date(),
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
        )
    finally:
        cfg.providers["news"] = original

    assert called["who"] == "finnhub"


@pytest.mark.asyncio
async def test_politician_trades_swap_fmp_to_quiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``politician_trades`` flips between fmp and quiver via config only.

    Monkeypatches ``_fetch_trades`` in the Quiver module and sets the
    ``QUIVER_QUANT_API_KEY`` env-var so the provider's soft-fail guard does
    not return early before the patched function is reached.
    """
    import data.providers.politician_trades.quiver as quiver_mod
    from data import _dispatch
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["politician_trades"]
    cfg.providers["politician_trades"] = "quiver"
    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake")

    called: dict[str, str | None] = {"who": None}

    def fake_quiver_fetch(symbol: str | None, api_key: str) -> list:
        """Record which provider was invoked."""
        called["who"] = "quiver"
        return []

    monkeypatch.setattr(quiver_mod, "_fetch_trades", fake_quiver_fetch)

    try:
        await _dispatch(
            "politician_trades",
            "AAPL",
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
            lookback_days=30,
        )
    finally:
        cfg.providers["politician_trades"] = original

    assert called["who"] == "quiver"
