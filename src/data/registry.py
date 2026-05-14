"""Provider shell — `@register` decorator, `dispatch`, shared limiter map.

Adding a provider means writing a single async `fetch(...)` function with a
`@register(domain, name, upstream, rate_per_minute, burst)` decorator. The
registry handles rate-limit acquisition; providers do not call the limiter
themselves.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import get_config
from .rate_limit import AsyncRateLimiter

DOMAINS: frozenset[str] = frozenset({
    # Phase 5: "stats" retired — split into two purpose-scoped domains.
    "price_history",
    "company_ratios",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
})


@dataclass(frozen=True)
class _Entry:
    domain: str
    name: str
    upstream: str
    fn: Callable[..., Awaitable[Any]]


_REGISTRY: dict[tuple[str, str], _Entry] = {}
_LIMITERS: dict[str, AsyncRateLimiter] = {}


def _ensure_limiter(upstream: str, rate_per_minute: float, burst: int) -> AsyncRateLimiter:
    """Get-or-create the limiter for `upstream`. Conflicting limits raise."""
    existing = _LIMITERS.get(upstream)
    if existing is not None:
        if (existing.rate_per_minute, existing.capacity) != (rate_per_minute, burst):
            raise ValueError(
                f"conflicting rate-limit declarations for upstream {upstream!r}: "
                f"already {existing.rate_per_minute}/min burst {existing.capacity}, "
                f"got {rate_per_minute}/min burst {burst}"
            )
        return existing
    lim = AsyncRateLimiter(upstream, rate_per_minute=rate_per_minute, burst=burst)
    _LIMITERS[upstream] = lim
    return lim


def register(
    domain: str,
    name: str,
    *,
    upstream: str,
    rate_per_minute: float,
    burst: int,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorate an async `fetch` function as the `(domain, name)` provider."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain!r}")

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        _ensure_limiter(upstream, rate_per_minute, burst)
        _REGISTRY[(domain, name)] = _Entry(domain, name, upstream, fn)
        return fn

    return deco


async def dispatch(domain: str, *args: Any, **kwargs: Any) -> Any:
    """Call the active provider for `domain` after acquiring its limiter token."""
    cfg = get_config()
    name = cfg.providers[domain]
    try:
        entry = _REGISTRY[(domain, name)]
    except KeyError as exc:
        raise RuntimeError(
            f"no provider registered for ({domain!r}, {name!r}); "
            f"check config/data.json + the provider module is imported"
        ) from exc
    await _LIMITERS[entry.upstream].acquire()
    return await entry.fn(*args, **kwargs)


def active_upstreams() -> set[str]:
    """Upstream identifiers used by the currently active provider set."""
    cfg = get_config()
    return {_REGISTRY[(d, n)].upstream for d, n in cfg.providers.items() if (d, n) in _REGISTRY}


def min_decision_interval_seconds() -> float:
    """Floor on the trading cadence given the active providers' rate budgets."""
    return max(
        (_LIMITERS[u].min_interval_seconds for u in active_upstreams() if u in _LIMITERS),
        default=0.0,
    )
