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
