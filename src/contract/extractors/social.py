"""Social-sentiment feature extractor + deterministic verdict derivation.

Consumes the Finnhub ``stock_social_sentiment`` payload (pre-aggregated; no raw
posts ever flow through here) and produces a fixed-shape feature dict plus
a deterministic ``AnalystVerdict`` via ``derive_social_verdict``.

The ``SocialSentiment`` Pydantic model (from ``data.models``) is the structured
form of the provider output.  The extractor accepts either the structured model
or the raw dict-shaped payload that the fetch callback stores per-ticker.
"""
from __future__ import annotations

from typing import Any

from agents.analysts.heuristics import SocialHeuristics
from contract.evidence import AnalystVerdict

# Canonical set of keys emitted by this extractor — used in tests and by
# the evidence callback to validate completeness.
_KEYS: tuple[str, ...] = (
    "mention_count_total",
    "mention_count_reddit",
    "mention_count_twitter",
    "aggregate_score",
    "score_velocity_24h",
    "platform_score_disagreement",
    "is_no_data",
)


def _net(scores: dict[str, Any]) -> float:
    """Compute one platform's net polarity score: positive_score - negative_score.

    Args:
        scores: Platform sub-dict from the social payload, containing optional
                ``positive_score`` and ``negative_score`` float fields.

    Returns:
        Net polarity as a float; defaults to 0.0 for missing fields.
    """
    pos = float(scores.get("positive_score") or 0.0)
    neg = float(scores.get("negative_score") or 0.0)
    return pos - neg


def extract_social_features(raw: dict[str, Any], ticker: str) -> dict[str, float]:
    """Reduce the per-ticker social payload to the Phase-5 feature vector.

    Expected ``raw`` shape (one ticker's slice of ``state["social_data"]``):

    .. code-block:: python

        {
            "reddit":  {"mention_count": int, "positive_score": float, "negative_score": float},
            "twitter": {"mention_count": int, "positive_score": float, "negative_score": float},
        }

    Missing inputs yield zeros and set ``is_no_data=1.0``.  The ``ticker``
    argument is accepted for API uniformity with the other extractors but is
    not used in the computation.

    Args:
        raw:    Per-ticker raw social data dict, or ``{}`` if fetch failed.
        ticker: Ticker symbol (unused; kept for uniform extractor signature).

    Returns:
        Dict mapping every key in ``_KEYS`` to a float value.
    """
    reddit  = raw.get("reddit")  or {}
    twitter = raw.get("twitter") or {}

    n_reddit  = float(reddit.get("mention_count")  or 0.0)
    n_twitter = float(twitter.get("mention_count") or 0.0)
    n_total   = n_reddit + n_twitter

    # No mentions at all → zero everything and flag the no-data path.
    if n_total == 0:
        return {k: (1.0 if k == "is_no_data" else 0.0) for k in _KEYS}

    reddit_net  = _net(reddit)
    twitter_net = _net(twitter)

    # Mention-weighted aggregate score across both platforms.
    aggregate = (reddit_net * n_reddit + twitter_net * n_twitter) / n_total

    # Platform disagreement = absolute gap between per-platform net scores.
    # Large when one platform is bullish and the other bearish.
    disagreement = abs(reddit_net - twitter_net) if (n_reddit and n_twitter) else 0.0

    return {
        "mention_count_total":         n_total,
        "mention_count_reddit":        n_reddit,
        "mention_count_twitter":       n_twitter,
        "aggregate_score":             aggregate,
        "score_velocity_24h":          0.0,   # placeholder — prior-tick delta wired later
        "platform_score_disagreement": disagreement,
        "is_no_data":                  0.0,
    }


def derive_social_verdict(features: dict[str, float], h: SocialHeuristics) -> AnalystVerdict:
    """Map the social feature vector to an ``AnalystVerdict`` using Phase-5 heuristics.

    Pure function with no I/O or global state — safe for table-driven unit tests
    and for inline calls inside the async fetch callback.

    Rules (see spec §"derive_social_verdict"):

    - **Lean** — sign of ``aggregate_score``; within the neutral band → "neutral".
    - **Magnitude** — ``|score| × scale``, boosted when mention volume is high.
    - **Confidence** — base + volume boost − disagreement penalty; clamped to [0, 1].
    - **key_factors** — closed vocabulary:
      ``{positive, negative, mixed, high_volume, low_volume,
      reddit_dominant, twitter_dominant, platforms_agree, platforms_disagree}``.

    Args:
        features: Feature dict produced by ``extract_social_features``.
        h:        Frozen ``SocialHeuristics`` config section.

    Returns:
        A validated ``AnalystVerdict`` instance.
    """
    # ── No-data short-circuit ─────────────────────────────────────────────────
    if features.get("is_no_data", 0.0) >= 1.0 or features["mention_count_total"] == 0:
        return AnalystVerdict(
            lean="neutral",
            magnitude=0.0,
            confidence=0.0,
            rationale="no social mentions",
            key_factors=[],
            is_no_data=True,
        )

    score   = features["aggregate_score"]
    n_total = features["mention_count_total"]

    # ── Lean ──────────────────────────────────────────────────────────────────
    if score > h.score_neutral_band:
        lean = "bullish"
    elif score < -h.score_neutral_band:
        lean = "bearish"
    else:
        lean = "neutral"

    # ── Magnitude ─────────────────────────────────────────────────────────────
    magnitude = min(abs(score) * h.score_to_magnitude_scale, h.magnitude_cap)
    if n_total > h.high_volume_mentions:
        magnitude = min(magnitude + h.high_volume_magnitude_boost, h.magnitude_cap)

    # ── Confidence ────────────────────────────────────────────────────────────
    confidence = h.confidence_base
    if n_total >= h.confidence_volume_floor:
        confidence += h.confidence_boost_step
    if features["platform_score_disagreement"] > h.platform_disagreement_threshold:
        confidence -= h.confidence_penalty_step
    confidence = max(0.0, min(1.0, confidence))

    # ── Key factors (closed vocabulary) ──────────────────────────────────────
    factors: list[str] = []

    # Polarity tag — one of {positive, negative, mixed}.
    if lean == "bullish":
        factors.append("positive")
    elif lean == "bearish":
        factors.append("negative")
    else:
        factors.append("mixed")

    # Volume tag — high / low / (nothing if in between).
    if n_total > h.high_volume_mentions:
        factors.append("high_volume")
    elif n_total < h.confidence_volume_floor:
        factors.append("low_volume")

    # Platform agreement / disagreement.
    if features["platform_score_disagreement"] > h.platform_disagreement_threshold:
        factors.append("platforms_disagree")
    else:
        factors.append("platforms_agree")

    # Dominant platform — only if one platform has >2× the mentions of the other.
    n_reddit  = features["mention_count_reddit"]
    n_twitter = features["mention_count_twitter"]
    if n_reddit > 2 * n_twitter:
        factors.append("reddit_dominant")
    elif n_twitter > 2 * n_reddit:
        factors.append("twitter_dominant")

    # Rationale assembled from fired key_factors, capped at 160 chars.
    rationale = ", ".join(factors)[:160]

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
    )
