"""Unit tests for data.registry — provider shell + dispatch."""
from __future__ import annotations

from data.rate_limit import AsyncRateLimiter


def test_async_rate_limiter_exposes_capacity() -> None:
    lim = AsyncRateLimiter("acme", rate_per_minute=120, burst=10)
    assert lim.capacity == 10


def test_async_rate_limiter_capacity_defaults_to_rounded_rate() -> None:
    lim = AsyncRateLimiter("acme", rate_per_minute=60)
    # When burst is unset, capacity falls back to round(rate_per_minute).
    assert lim.capacity == 60


import asyncio

import pytest

from data import registry
from data.registry import (
    DOMAINS,
    _ensure_limiter,
    active_upstreams,
    dispatch,
    min_decision_interval_seconds,
    register,
)


def test_domains_set_has_seven_known_slots() -> None:
    assert DOMAINS == frozenset({
        "stats",
        "news",
        "social_sentiment",
        "insider_trades",
        "politician_trades",
        "notable_holders",
        "filings",
    })


def test_register_populates_registry(registry_isolation: None) -> None:
    @register("news", "fake", upstream="fake_up", rate_per_minute=600, burst=10)
    async def fetch(ticker: str) -> str:
        return ticker.upper()

    entry = registry._REGISTRY[("news", "fake")]
    assert entry.domain == "news"
    assert entry.name == "fake"
    assert entry.upstream == "fake_up"
    assert entry.fn is fetch


def test_register_unknown_domain_raises(registry_isolation: None) -> None:
    with pytest.raises(ValueError, match="unknown domain"):
        @register("weather", "noaa", upstream="noaa", rate_per_minute=60, burst=1)
        async def fetch(ticker: str) -> str:
            return ticker


def test_ensure_limiter_returns_existing_when_matched(registry_isolation: None) -> None:
    a = _ensure_limiter("up", 60, 10)
    b = _ensure_limiter("up", 60, 10)
    assert a is b


def test_ensure_limiter_conflict_raises(registry_isolation: None) -> None:
    _ensure_limiter("up", 60, 10)
    with pytest.raises(ValueError, match="conflicting rate-limit"):
        _ensure_limiter("up", 120, 10)
    with pytest.raises(ValueError, match="conflicting rate-limit"):
        _ensure_limiter("up", 60, 20)


def test_dispatch_calls_active_provider(monkeypatch: pytest.MonkeyPatch, registry_isolation: None) -> None:
    @register("news", "fake_a", upstream="fake_a", rate_per_minute=6000, burst=10)
    async def fetch_a(ticker: str) -> str:
        return f"a:{ticker}"

    @register("news", "fake_b", upstream="fake_b", rate_per_minute=6000, burst=10)
    async def fetch_b(ticker: str) -> str:
        return f"b:{ticker}"

    from data import config as data_config

    fake_cfg = data_config.DataConfig(
        providers={
            "stats": "fake_a",  # not used
            "news": "fake_b",
            "social_sentiment": "fake_a",
            "insider_trades": "fake_a",
            "politician_trades": "fake_a",
            "notable_holders": "fake_a",
            "filings": "fake_a",
        },
    )
    monkeypatch.setattr(data_config, "_cache", fake_cfg)

    result = asyncio.run(dispatch("news", "AAPL"))
    assert result == "b:AAPL"


def test_active_upstreams_reflects_config(monkeypatch: pytest.MonkeyPatch, registry_isolation: None) -> None:
    for name, up in [("fake_a", "alpha"), ("fake_b", "beta")]:
        @register("news", name, upstream=up, rate_per_minute=6000, burst=10)
        async def fetch(ticker: str, _name: str = name) -> str:
            return _name
        # Register the same name into every domain so DataConfig validates.
        for d in DOMAINS - {"news"}:
            @register(d, name, upstream=up, rate_per_minute=6000, burst=10)
            async def _other(ticker: str) -> str:
                return ""

    from data import config as data_config

    monkeypatch.setattr(data_config, "_cache", data_config.DataConfig(
        providers={d: "fake_a" for d in DOMAINS} | {"news": "fake_b"},
    ))
    ups = active_upstreams()
    assert "alpha" in ups
    assert "beta" in ups
    floor = min_decision_interval_seconds()
    assert floor > 0
