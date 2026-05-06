"""Async token-bucket rate limiters per data-source budget.

Each provider awaits its limiter before issuing an upstream call. When
no tokens are available the coroutine sleeps until one is — there is
no drop, no error, no retry storm. Callers therefore must not rely on
a tight decision cadence: **the slowest limiter sets the floor on how
often a complete signal bundle can refresh**.

That floor is exposed as `slowest_min_interval_seconds(...)` and
surfaced on `StockSignalBundle.min_decision_interval_seconds` so the
strategist agent can guard against trading faster than its data.

Per `docs/data-sources.md` free-tier caps:

- **Finnhub:**     60 calls/min, 30/sec burst, US stocks only.
- **yfinance:**    no published cap; we self-throttle.
- **Quiver:**      free trial limited; ~24h delay on data.
- **EDGAR (SEC):** 10 req/sec hard cap. Free, but every request must
                   carry a contact email in the User-Agent header.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    rate_per_second: float
    capacity: int
    tokens: float
    updated: float


class AsyncRateLimiter:
    """Token-bucket limiter with awaitable `acquire()`.

    `rate_per_minute` sets the long-run average; `burst` (default ≈
    rate_per_minute) sets the largest single-shot allowance.
    """

    def __init__(self, name: str, rate_per_minute: float, burst: int | None = None):
        if rate_per_minute <= 0:
            raise ValueError(f"{name}: rate_per_minute must be > 0")
        capacity = burst if burst is not None else max(1, int(round(rate_per_minute)))
        self.name = name
        self.rate_per_minute = rate_per_minute
        self._bucket = _Bucket(
            rate_per_second=rate_per_minute / 60.0,
            capacity=capacity,
            tokens=float(capacity),
            updated=time.monotonic(),
        )
        self._lock = asyncio.Lock()

    @property
    def rate_per_second(self) -> float:
        return self._bucket.rate_per_second

    @property
    def min_interval_seconds(self) -> float:
        """Asymptotic floor between two successive calls.

        If a decision flow makes choices faster than this, it is acting
        on stale data. Use this to set the trading cadence.
        """
        return 1.0 / self._bucket.rate_per_second

    async def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                b = self._bucket
                b.tokens = min(
                    b.capacity, b.tokens + (now - b.updated) * b.rate_per_second
                )
                b.updated = now
                if b.tokens >= 1.0:
                    b.tokens -= 1.0
                    return
                deficit = 1.0 - b.tokens
                await asyncio.sleep(deficit / b.rate_per_second)


# Per-budget singletons. Finnhub is shared across news + social;
# EDGAR is shared across insider trades + company filings.
FINNHUB = AsyncRateLimiter("finnhub", rate_per_minute=60, burst=30)
QUIVER = AsyncRateLimiter("quiver", rate_per_minute=30, burst=10)
EDGAR = AsyncRateLimiter("edgar", rate_per_minute=600, burst=20)  # SEC's 10 req/sec cap
YFINANCE = AsyncRateLimiter("yfinance", rate_per_minute=60, burst=30)


ALL_LIMITERS: dict[str, AsyncRateLimiter] = {
    "finnhub": FINNHUB,
    "quiver": QUIVER,
    "edgar": EDGAR,
    "yfinance": YFINANCE,
}


def slowest_min_interval_seconds(*limiters: AsyncRateLimiter) -> float:
    """Return the longest min-interval across the given limiters.

    This is the data-refresh floor for any decision flow that uses all
    of these sources. Trading faster than this means the strategist is
    re-deciding without new information.
    """
    return max((l.min_interval_seconds for l in limiters), default=0.0)
