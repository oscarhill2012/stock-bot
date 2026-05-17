"""Social-sentiment feature extractor + deterministic verdict derivation.

Phase 7 (providers-and-silent-gaps-v1, Task 2.11 / Fix K):

The Social analyst is dead in v1 — StockTwits is deferred (Row #13); the
Finnhub provider soft-fails to empty snapshots for most tickers.  This task
is *forward-readiness work*: when a Row #13 follow-up provider lands and the
analyst comes back to life, the extractor must already consume the typed
snapshot list shape (not the legacy flattened per-platform dict).

The extractor's call signature is **preserved unchanged**:
``extract_social_features(raw, ticker, *, as_of=None)``
so call-site churn is zero.

New ``raw`` shape (emitted by the updated ``social_fetch_callback``):

.. code-block:: python

    {
        "snapshots":       list[dict],   # [SocialSentimentSnapshot.model_dump(), …]
        "aggregate_score": float | None,
    }

Legacy shape (the old per-platform dict-of-dict) is no longer supported;
``social_fetch_callback`` was updated in the same phase to emit the new shape.

Social is exempted from the "no silent-zero features" assertion in Phase 7
Task 7.1 because ``score_velocity_24h`` is intentionally held at 0.0 in v1
(see the inline comment in the function body for the Row #13 follow-up plan).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

# TYPE_CHECKING guard prevents a circular import at module load time:
# contract.extractors.social ← agents.analysts.heuristics ←
#   agents.analysts.__init__ ← social.agent ← contract.extractors.social.
# Both are resolved lazily inside derive_social_verdict at runtime,
# by which point the module graph is fully initialised.
if TYPE_CHECKING:
    from agents.analysts.heuristics import SocialHeuristics
    from contract.evidence import AnalystVerdict

# Canonical set of keys emitted by this extractor — used in tests and by
# the evidence callback to validate completeness.
_KEYS: tuple[str, ...] = (
    "mention_count_total",
    "mention_count_reddit",
    "mention_count_twitter",
    "social_aggregate_score",
    "aggregate_score",             # back-compat alias — same value as above
    "score_velocity_24h",
    "platform_score_disagreement",
    "is_no_data",
)


def extract_social_features(
    raw: dict[str, Any],
    ticker: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, float | bool]:
    """Reduce the per-ticker social payload to the Phase-7 feature vector.

    Expected ``raw`` shape (Phase 7 canonical — typed snapshot list):

    .. code-block:: python

        {
            "snapshots":       [{"platform": "reddit", "mention_count": 100, …}, …],
            "aggregate_score": 0.5,
        }

    Missing or empty inputs yield ``is_no_data=True`` and zeros for all numeric
    features — Social soft-fails per spec decision 9.3.

    The ``ticker`` argument is accepted for API uniformity with the other
    extractors but is not used in computation.

    Args:
        raw:    Per-ticker raw social data dict, or ``{}`` if fetch failed.
        ticker: Ticker symbol (unused; kept for uniform extractor signature).
        as_of:  Historical clock — reserved for future velocity computation.

    Returns:
        Dict mapping every key in ``_KEYS`` to a float or bool value.
    """
    snapshots        = raw.get("snapshots") or []
    aggregate_score  = raw.get("aggregate_score")

    # Soft-fail: no snapshot data or no aggregate score → no-data path.
    if not snapshots or aggregate_score is None:
        return {
            "mention_count_total":         0.0,
            "mention_count_reddit":        0.0,
            "mention_count_twitter":       0.0,
            "social_aggregate_score":      0.0,
            "aggregate_score":             0.0,
            "score_velocity_24h":          0.0,
            "platform_score_disagreement": 0.0,
            "is_no_data":                  True,
        }

    # Sum mention counts across all snapshots; bucket by platform.
    n_total   = 0.0
    n_reddit  = 0.0
    n_twitter = 0.0

    # Per-platform net scores for disagreement computation.
    reddit_net  = 0.0
    twitter_net = 0.0
    has_reddit  = False
    has_twitter = False

    for snap in snapshots:
        count = float(snap.get("mention_count") or 0)
        n_total += count

        platform = (snap.get("platform") or "").lower()
        pos = float(snap.get("positive_score") or 0.0)
        neg = float(snap.get("negative_score") or 0.0)

        if platform == "reddit":
            n_reddit  += count
            reddit_net = pos - neg
            has_reddit = True
        elif platform == "twitter":
            n_twitter  += count
            twitter_net = pos - neg
            has_twitter = True

    # Platform disagreement = absolute gap between per-platform net scores.
    disagreement = (
        abs(reddit_net - twitter_net)
        if has_reddit and has_twitter
        else 0.0
    )

    # v1: score_velocity_24h held at 0.0 — Social analyst is dead in v1
    # (Row #13 / StockTwits deferred; see plan header + spec decision 9.3).
    # Row #13 follow-up plan will add per-tick memory_buffer wiring to compute:
    #   score_velocity_24h = aggregate_score - state["memory_buffer"].get(
    #       f"previous_aggregate_score:{ticker}", 0.0)
    # Wiring the memory_buffer access here would require extending the
    # extractor signature, which we are deliberately deferring until the
    # analyst is alive again.  Leaving 0.0 keeps the feature shape stable
    # for the no-silent-zero-features test (Social is exempted from that
    # assertion per Phase 7 Task 7.1).
    score_velocity_24h = 0.0

    return {
        "mention_count_total":         n_total,
        "mention_count_reddit":        n_reddit,
        "mention_count_twitter":       n_twitter,
        "social_aggregate_score":      float(aggregate_score),
        "aggregate_score":             float(aggregate_score),   # back-compat alias
        "score_velocity_24h":          score_velocity_24h,
        "platform_score_disagreement": disagreement,
        "is_no_data":                  False,
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
    # Deferred runtime imports — avoids the circular import that arises when
    # loading this module triggers agents.analysts.__init__ (which re-imports
    # this module before it has finished initialising).
    from contract.evidence import AnalystVerdict  # noqa: PLC0415

    # ── No-data short-circuit ─────────────────────────────────────────────────
    if features.get("is_no_data") is True or features.get("is_no_data", 0.0) >= 1.0:
        return AnalystVerdict(
            lean="neutral",
            magnitude=0.0,
            confidence=0.0,
            rationale="no social mentions",
            key_factors=[],
            is_no_data=True,
        )

    # Use aggregate_score (back-compat alias present on both old and new shapes).
    score   = features.get("social_aggregate_score") or features.get("aggregate_score", 0.0)
    n_total = features["mention_count_total"]

    if n_total == 0:
        return AnalystVerdict(
            lean="neutral",
            magnitude=0.0,
            confidence=0.0,
            rationale="no social mentions",
            key_factors=[],
            is_no_data=True,
        )

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
