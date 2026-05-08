"""Unit tests: pacing floor reflects only ACTIVE upstream limiters."""
from __future__ import annotations

import pytest

from data import config as data_config
from data.registry import (
    DOMAINS,
    active_upstreams,
    min_decision_interval_seconds,
    register,
)


def test_min_interval_reflects_only_active_upstreams(monkeypatch, registry_isolation) -> None:
    @register("news", "slow", upstream="slow_up", rate_per_minute=6, burst=1)
    async def _slow(ticker: str, **opts):  # 1 req per 10s
        return []

    @register("news", "fast", upstream="fast_up", rate_per_minute=600, burst=10)
    async def _fast(ticker: str, **opts):  # 1 req per 0.1s
        return []

    # Stub other domains with the fast provider to satisfy DataConfig.
    for d in DOMAINS - {"news"}:
        @register(d, "fast", upstream="fast_up", rate_per_minute=600, burst=10)
        async def _other(*a, **kw):
            return None

    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fast" for d in DOMAINS} | {"news": "slow"}),
    )
    floor = min_decision_interval_seconds()
    assert floor == pytest.approx(10.0, rel=0.01)
    assert active_upstreams() == {"slow_up", "fast_up"}

    # Swap back to fast for news; slow_up no longer in the active set.
    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fast" for d in DOMAINS}),
    )
    assert "slow_up" not in active_upstreams()
    assert min_decision_interval_seconds() == pytest.approx(0.1, rel=0.01)
