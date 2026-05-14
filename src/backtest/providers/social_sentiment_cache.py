"""Social-sentiment cache provider — deliberately returns ``None`` in v1.

Historical social-sentiment ingestion is tracked as a separate backlog item
(see ``docs/superpowers/backlog.md``).  The strategist already tolerates a
``None`` social evidence field, so the analyst pool degrades gracefully when
this provider is active.

Registered as ``upstream="cache"`` alongside the other cache providers so
the runner can uniformly call ``set_active_provider("social_sentiment", "cache")``
without special-casing the social domain.
"""
from __future__ import annotations

from datetime import datetime

from data.registry import register


@register(
    "social_sentiment",
    "cache",
    upstream="cache",
    rate_per_minute=1_000_000,
    burst=1_000,
)
async def fetch(ticker: str, *, as_of: datetime, **_unused) -> None:
    """Always return ``None`` — backlog item covers building a historical scraper.

    Parameters
    ----------
    ticker:
        The equity symbol (accepted for signature uniformity; ignored).
    as_of:
        Point-in-time ceiling (accepted for signature uniformity; ignored).
    **_unused:
        Absorbs any other live-provider kwargs.

    Returns
    -------
    None
        Always ``None``; the strategist treats missing social evidence as
        "no information" rather than a hard failure.
    """
    return None
