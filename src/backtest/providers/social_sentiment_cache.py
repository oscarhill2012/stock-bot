"""Social-sentiment cache provider — deliberately returns ``None`` in v1.

Historical social-sentiment ingestion is tracked as a separate backlog item
(see ``docs/superpowers/backlog.md``).  The strategist already tolerates a
``None`` social evidence field, so the analyst pool degrades gracefully when
running a backtest — social signals are simply absent rather than blocking.
"""
from __future__ import annotations

from datetime import datetime

from data.registry import register


@register(
    "social_sentiment", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(ticker: str, *, as_of: datetime, **_unused) -> None:
    """Always return ``None`` — backlog item B19 will populate this domain.

    Parameters
    ----------
    ticker:
        Ticker symbol (accepted for signature compatibility; not used).
    as_of:
        Point-in-time boundary (accepted for signature compatibility; not used).

    Returns
    -------
    None
        Unconditionally; social data is not cached in v1.
    """
    return None
