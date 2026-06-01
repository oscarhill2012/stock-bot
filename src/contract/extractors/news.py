"""News analyst deterministic feature extractor.

Renamed from ``sentiment.py`` / ``extract_sentiment_features`` in Task 6.
Phase 7 (providers-and-silent-gaps-v1) adds:

- Reads ``sentiment`` field on article dicts (replaces ``polarity`` — the model
  field is ``NewsArticle.sentiment``; ``polarity`` was never in the schema).
- Accepts ``articles`` as a canonical key alongside the legacy ``news_items``
  alias.
- Emits time-windowed counters (24 h / 72 h), hours-since-latest-news, and a
  recency-weighted polarity score using exponential decay.
- Accepts ``state={"as_of": ...}`` for backtest replay.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

# Half-life for the exponential time-decay applied to per-article sentiment.
# An article 24 h old contributes 50 % as much as an article from right now.
HALF_LIFE_HOURS = 24.0

_KEYS = (
    "news_count_7d",
    "pct_news_positive_7d",
    "pct_news_negative_7d",
    "headline_polarity_mean_7d",     # canonical mean headline-polarity feature key
    "social_volume_z",
    # Phase 7 additions (Fix J).
    "news_count_24h",
    "news_count_72h",
    "hours_since_latest_news",
    "headline_polarity_recency_weighted",
)


def _zero_features() -> dict[str, float]:
    """Return a zeroed-out dict covering every expected output key.

    Returns:
        dict[str, float]: All news feature keys mapped to 0.0, except for
        ``hours_since_latest_news`` which defaults to 9999.0 (no-data sentinel).
    """
    out = {k: 0.0 for k in _KEYS}
    out["hours_since_latest_news"] = 9999.0
    return out


def _parse_published_at(raw_value: Any) -> datetime | None:
    """Parse a published_at / published value to a UTC-aware datetime.

    Accepts:
    - ``datetime`` objects (with or without timezone).
    - ISO 8601 strings (with or without timezone offset).

    Returns ``None`` on parse failure.
    """
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw_value))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def extract_news_features(
    raw: Mapping[str, Any],
    ticker: str = "",
    *,
    as_of: datetime | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Compute the news feature catalogue from raw news data.

    Accepted article shapes:

    - ``raw["articles"]`` — Phase 7 canonical; each article has ``sentiment``.
    - ``raw["news_items"]`` — legacy alias; each article has ``sentiment``
      (previously ``polarity``, but ``polarity`` was never in the model schema).

    Parameters
    ----------
    raw:
        Raw provider payload.  Expected to contain ``articles`` or ``news_items``
        (list of dicts with a ``sentiment`` float) and optionally
        ``social_volume_z`` (float, legacy).
    ticker:
        Ticker symbol — reserved for future per-ticker adjustments; not used
        in arithmetic today.  Defaults to ``""`` so callers can use
        ``state=`` as the only keyword.
    as_of:
        Legacy historical clock parameter.  Prefer ``state={"as_of": "..."}``
        for Phase 7 callers.
    state:
        Phase 7 pipeline state dict.  ``state["as_of"]`` is used as the
        reference time for window-based features (24 h / 72 h counters,
        recency weights).  Falls back to ``as_of``, then wall-clock.

    Returns
    -------
    dict[str, float]
        Exactly the keys in ``_KEYS``, all cast to float.
    """
    out = _zero_features()

    if not raw:
        return out

    # Resolve the reference time — used for relative-age features.
    if state is not None and state.get("as_of"):
        raw_as_of = state["as_of"]
        if isinstance(raw_as_of, str):
            now = datetime.fromisoformat(raw_as_of)
            now = now if now.tzinfo else now.replace(tzinfo=UTC)
        elif isinstance(raw_as_of, datetime):
            now = raw_as_of if raw_as_of.tzinfo else raw_as_of.replace(tzinfo=UTC)
        else:
            now = datetime.now(tz=UTC)
    elif as_of is not None:
        now = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    else:
        now = datetime.now(tz=UTC)

    # Accept "articles" (canonical) or "news_items" (legacy alias).
    items = raw.get("articles") or raw.get("news_items") or raw.get("news") or []
    n = len(items)
    out["news_count_7d"] = float(n)

    if n > 0:
        sentiments: list[float] = []
        positives = 0
        negatives = 0

        # Per-article time-window counters.
        n_24h = 0
        n_72h = 0
        min_hours_ago = float("inf")   # hours since the most recent article

        # Accumulators for the recency-weighted polarity.
        weighted_sum    = 0.0
        weight_total    = 0.0

        for item in items:
            # Read ``sentiment`` field (the canonical model field name).
            # ``polarity`` was removed per Fix I — do not add a fallback.
            try:
                s = float(item.get("sentiment") or 0.0)
            except (TypeError, ValueError):
                s = 0.0

            sentiments.append(s)

            if s > 0:
                positives += 1
            elif s < 0:
                negatives += 1

            # Determine the article age in hours relative to the reference time.
            pub_raw = item.get("published_at") or item.get("published")
            pub_dt  = _parse_published_at(pub_raw)

            if pub_dt is not None:
                age_hours = (now - pub_dt).total_seconds() / 3600.0
                # Ignore articles from the future (can happen in tests).
                if age_hours < 0:
                    age_hours = 0.0

                if age_hours < min_hours_ago:
                    min_hours_ago = age_hours

                if age_hours <= 24.0:
                    n_24h += 1
                if age_hours <= 72.0:
                    n_72h += 1

                # Exponential decay weight: e^(−age × ln2 / half_life).
                # An article at age=0 h has weight 1.0; at age=HALF_LIFE_HOURS weight 0.5.
                decay   = math.log(2) / HALF_LIFE_HOURS
                weight  = math.exp(-age_hours * decay)
                weighted_sum  += s * weight
                weight_total  += weight

        polarity_mean = sum(sentiments) / n

        out["pct_news_positive_7d"]   = positives / n * 100.0
        out["pct_news_negative_7d"]   = negatives / n * 100.0
        out["headline_polarity_mean_7d"] = polarity_mean

        out["news_count_24h"] = float(n_24h)
        out["news_count_72h"] = float(n_72h)

        if min_hours_ago < float("inf"):
            out["hours_since_latest_news"] = min_hours_ago

        if weight_total > 0:
            out["headline_polarity_recency_weighted"] = weighted_sum / weight_total

    # social_volume_z is optional — not all providers supply it.
    sv = raw.get("social_volume_z")
    if sv is not None:
        try:
            out["social_volume_z"] = float(sv)
        except (TypeError, ValueError):
            out["social_volume_z"] = 0.0

    return out
