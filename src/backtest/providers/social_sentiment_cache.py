"""Social-sentiment cache provider — returns an empty ``SocialSentiment`` in v1.

Historical social-sentiment ingestion is tracked as a separate backlog item
(backlog B19).  Rather than returning ``None`` (which diverges from the live
provider's canonical ``single / SocialSentiment`` shape), the cache now returns
a well-typed empty model.  Downstream agents already handle empty snapshots
the same way they handled ``None`` — no social signals are present, so they
degrade gracefully.

When backlog B19 lands and the cache store has real social-sentiment rows, this
provider will read from the store.  Until then the empty model is a valid
structural placeholder that keeps the contract test green.
"""
from __future__ import annotations

from datetime import datetime

from data.models.sentiment import SocialSentiment
from data.registry import register


@register(
    "social_sentiment", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(ticker: str, *, as_of: datetime, **_unused) -> SocialSentiment:
    """Return an empty ``SocialSentiment`` — backlog B19 will populate real data.

    No cache store is consulted in v1; the returned model has an empty
    ``snapshots`` list and a zero ``aggregate_score``.  This satisfies the
    canonical ``single / SocialSentiment`` shape while backlog B19 is pending.

    Parameters
    ----------
    ticker:
        Ticker symbol — stamped onto the returned model's ``ticker`` field.
    as_of:
        Point-in-time boundary (accepted for signature compatibility; not used
        until backlog B19 adds real ingestion).

    Returns
    -------
    SocialSentiment
        Empty model for ``ticker`` — no snapshots, aggregate_score 0.0.
    """
    # Return a structurally valid empty model rather than None, so the return
    # type matches DOMAIN_SHAPES["social_sentiment"] = single/SocialSentiment.
    return SocialSentiment(ticker=ticker, snapshots=[], aggregate_score=0.0)
