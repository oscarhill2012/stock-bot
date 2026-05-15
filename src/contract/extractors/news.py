"""News analyst deterministic feature extractor.

Renamed from ``sentiment.py`` / ``extract_sentiment_features`` in Task 6.
Logic is unchanged — scoped to news data only (social_volume_z retained for
backwards compatibility but will not be populated once the social_sentiment
branch is removed from the news fetch callback).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

_KEYS = (
    "news_count_7d",
    "pct_news_positive_7d",
    "pct_news_negative_7d",
    "headline_polarity_mean_7d",
    "social_volume_z",
)


def _zero_features() -> dict[str, float]:
    """Return a zeroed-out dict covering every expected output key.

    Returns:
        dict[str, float]: All news feature keys mapped to 0.0.
    """
    return {k: 0.0 for k in _KEYS}


def extract_news_features(
    raw: Mapping[str, Any],
    ticker: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """Compute the news feature catalogue from raw news data.

    Caller is expected to have already filtered news_items to the last 7 days
    (the analyst's fetch callback is the right place for that). This function
    just summarises whatever it is given.

    Parameters:
        raw:    Raw provider payload containing ``news_items`` (list of dicts,
                each with a ``polarity`` float) and optionally
                ``social_volume_z`` (float, legacy — not populated after the
                social_sentiment branch was removed from news_fetch_callback).
        ticker: Ticker symbol — reserved for future per-ticker adjustments;
                not used in arithmetic today.

    Returns:
        dict[str, float]: Exactly the keys in ``_KEYS``, all cast to float.
    """
    out = _zero_features()

    if not raw:
        return out

    # Accept either "news_items" (canonical) or "news" (legacy alias).
    items = raw.get("news_items") or raw.get("news") or []
    n = len(items)
    out["news_count_7d"] = float(n)

    if n > 0:
        polarities: list[float] = []
        positives = 0
        negatives = 0

        for item in items:
            try:
                p = float(item.get("polarity", 0.0))
            except (TypeError, ValueError):
                # Malformed polarity — treat as neutral rather than crashing.
                p = 0.0

            polarities.append(p)

            if p > 0:
                positives += 1
            elif p < 0:
                negatives += 1

        out["pct_news_positive_7d"] = positives / n * 100.0
        out["pct_news_negative_7d"] = negatives / n * 100.0
        out["headline_polarity_mean_7d"] = sum(polarities) / n

    # social_volume_z is optional — not all providers supply it.
    sv = raw.get("social_volume_z")
    if sv is not None:
        try:
            out["social_volume_z"] = float(sv)
        except (TypeError, ValueError):
            out["social_volume_z"] = 0.0

    return out
