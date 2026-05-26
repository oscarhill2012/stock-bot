"""Deterministic per-ticker digest — collapses 4 analysts → 1 TickerEvidence.

Pure Python, no LLM, no I/O. The strategist consumes the output instead of four
separate per-analyst signal lists. See `docs/Phase4-stratergist-and-analysts/spec.md`
for the math + design rationale.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from statistics import mean, variance

from contract.digest_defaults import DIRECTION_DEAD_ZONE
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _lean_sign(lean: str) -> int:
    """Map a lean string to its numeric sign for signed-confidence arithmetic.

    Parameters
    ----------
    lean:
        One of ``"bullish"``, ``"bearish"``, or ``"neutral"``.

    Returns
    -------
    int
        +1 for bullish, -1 for bearish, 0 for neutral.
    """
    return {"bullish": 1, "bearish": -1, "neutral": 0}[lean]


def _fill_missing(
    per_analyst: Mapping[str, AnalystEvidence],
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    weights: Mapping[str, float],
) -> dict[str, AnalystEvidence]:
    """Fill neutral-zero AnalystEvidence for any analyst named in ``weights``
    but absent from ``per_analyst``.

    The fill entries carry ``is_no_data=True`` so the aggregator can safely
    exclude them from confidence and disagreement calculations while still
    maintaining a fixed, invariant output shape regardless of provider
    sparseness.

    Parameters
    ----------
    per_analyst:
        Mapping of analyst name → evidence produced this tick.
    ticker:
        Ticker symbol to stamp on fill entries.
    tick_id:
        Tick identifier to stamp on fill entries.
    recorded_at:
        Timestamp to stamp on fill entries.
    weights:
        Analyst weight map — all keys in this mapping will be present in
        the returned dict.

    Returns
    -------
    dict[str, AnalystEvidence]
        Copy of ``per_analyst`` augmented with neutral-fill entries.
    """
    filled: dict[str, AnalystEvidence] = dict(per_analyst)

    for name in weights:
        if name in filled:
            continue

        filled[name] = AnalystEvidence(
            ticker=ticker,
            analyst=name,  # type: ignore[arg-type]
            tick_id=tick_id,
            recorded_at=recorded_at,
            features={},
            feature_warnings=[],
            verdict=AnalystVerdict(
                lean="neutral",
                magnitude=0.0,
                confidence=0.0,
                rationale="(no analyst output this tick)",
                key_factors=[],
                is_no_data=True,
            ),
        )

    return filled


def _weighted_signed_confidences(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> list[float]:
    """Compute ``weight × sign(lean) × confidence`` for each analyst.

    Analysts marked ``is_no_data`` contribute 0.0 so they don't shift the
    weighted sum but keep the list length fixed to ``len(weights)``.

    Parameters
    ----------
    per_analyst:
        Filled analyst evidence mapping (all weight keys must be present).
    weights:
        Per-analyst weight factors.

    Returns
    -------
    list[float]
        One entry per analyst in ``weights`` order.
    """
    out: list[float] = []

    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            out.append(0.0)
            continue

        sign = _lean_sign(ev.verdict.lean)
        out.append(weights[name] * sign * ev.verdict.confidence)

    return out


def _disagreement(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> float:
    """Variance of per-analyst signed confidences, clamped to [0, 1].

    No-data analysts are excluded so a missing provider doesn't inflate
    disagreement. Signed confidences live in [-1, +1], so two analysts at
    +1.0 and -1.0 produce a variance of exactly 1.0 — the maximum.

    Parameters
    ----------
    per_analyst:
        Filled analyst evidence mapping.
    weights:
        Analyst weight map — determines which analysts are considered.

    Returns
    -------
    float
        Disagreement score in [0, 1].
    """
    signed: list[float] = []

    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            continue
        signed.append(_lean_sign(ev.verdict.lean) * ev.verdict.confidence)

    if len(signed) < 2:
        return 0.0

    return min(variance(signed), 1.0)


def _summary(per_analyst: Mapping[str, AnalystEvidence], weights: Mapping[str, float]) -> str:
    """Render a short human-readable cross-analyst breakdown string.

    Example output: ``"3 bullish / 0 neutral / 1 bearish"``. No-data analysts
    are excluded from counts so the summary reflects only analysts with
    genuine signal this tick.

    Parameters
    ----------
    per_analyst:
        Filled analyst evidence mapping.
    weights:
        Analyst weight map — determines which analysts are counted.

    Returns
    -------
    str
        Human-readable lean breakdown, or ``"no contributing analysts"`` if
        every entry is no-data.
    """
    counts: Counter[str] = Counter()

    for name in weights:
        ev = per_analyst.get(name)
        if ev is None or ev.verdict.is_no_data:
            continue
        counts[ev.verdict.lean] += 1

    if sum(counts.values()) == 0:
        return "no contributing analysts"

    parts = [f"{counts.get(lean, 0)} {lean}" for lean in ("bullish", "neutral", "bearish")]
    return " / ".join(parts)


def _aggregate(
    per_analyst: Mapping[str, AnalystEvidence],
    weights: Mapping[str, float],
) -> AggregateVerdict:
    """Compute the AggregateVerdict from filled per-analyst evidence.

    Algorithm:

    1. Compute a weighted signed-confidence sum across all analysts (no-data → 0).
    2. Divide by total weight to get a normalised ``magnitude`` in [0, 1].
    3. If ``magnitude < DIRECTION_DEAD_ZONE``, lean collapses to ``"neutral"``.
    4. ``confidence`` = mean confidence of contributing (non-no_data) analysts.
    5. ``disagreement`` = variance of per-analyst signed confidences, clamped [0,1].

    Parameters
    ----------
    per_analyst:
        Fully filled analyst evidence mapping.
    weights:
        Per-analyst weight factors.

    Returns
    -------
    AggregateVerdict
        Cross-analyst stance ready for the strategist.
    """
    contributions = _weighted_signed_confidences(per_analyst, weights)
    weighted_sum = sum(contributions)
    total_weight = sum(weights.values()) or 1.0
    magnitude = abs(weighted_sum) / total_weight

    # Apply directional dead zone — small signals resolve to neutral.
    if magnitude < DIRECTION_DEAD_ZONE:
        lean = "neutral"
    elif weighted_sum > 0:
        lean = "bullish"
    else:
        lean = "bearish"

    # Confidence is the mean over analysts that actually contributed signal.
    contributing_confidences = [
        ev.verdict.confidence
        for name in weights
        for ev in (per_analyst.get(name),)
        if ev is not None and not ev.verdict.is_no_data
    ]
    confidence = mean(contributing_confidences) if contributing_confidences else 0.0

    return AggregateVerdict(
        lean=lean,  # type: ignore[arg-type]
        magnitude=min(magnitude, 1.0),
        confidence=min(max(confidence, 0.0), 1.0),
        disagreement=_disagreement(per_analyst, weights),
        summary=_summary(per_analyst, weights),
    )


def build_ticker_evidence(
    per_analyst: Mapping[str, AnalystEvidence],
    ticker: str,
    tick_id: str,
    recorded_at: datetime,
    weights: Mapping[str, float],
    last_price: float | None = None,
) -> TickerEvidence:
    """Collapse per-analyst evidence into one TickerEvidence for the strategist.

    This is the sole public entry-point for Task A4. It is pure Python — no I/O,
    no LLM calls, no side effects. The output shape is invariant: every analyst
    key named in ``weights`` will be present in ``per_analyst`` on the returned
    object, with missing analysts neutral-filled (``is_no_data=True``).

    Parameters
    ----------
    per_analyst:
        Mapping of analyst name → evidence produced this tick. May be sparse
        (not all analysts need to have reported).
    ticker:
        Ticker symbol this evidence relates to.
    tick_id:
        Opaque identifier for this market tick / evaluation window.
    recorded_at:
        UTC timestamp when this evidence was assembled.
    weights:
        Per-analyst weight factors. Must cover every analyst the digest
        considers. Passed through unchanged to ``TickerEvidence.weights`` for
        auditability.
    last_price:
        Optional live close at evidence-build time.  The shim that drives this
        function resolves the value from the portfolio (held tickers) or the
        technical analyst's ``last_close`` feature (non-held tickers) and
        passes it through here so the strategist's per-ticker renderer can
        show the live price in its section header.  ``None`` when no source
        was available.

    Returns
    -------
    TickerEvidence
        Fully populated evidence record ready for the strategist agent.
    """
    filled = _fill_missing(per_analyst, ticker, tick_id, recorded_at, weights)
    aggregate = _aggregate(filled, weights)

    return TickerEvidence(
        ticker=ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        per_analyst=filled,
        aggregate=aggregate,
        weights=dict(weights),
        last_price=last_price,
    )
