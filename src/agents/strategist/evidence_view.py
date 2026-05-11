"""Render TickerEvidence as a prompt-ready string for the strategist.

One block per ticker: aggregate lean + magnitude + confidence + disagreement,
then a compact per-analyst summary with the locked feature catalogue values.

This module is purely presentational — it contains no business logic and does
not mutate any state. It is called by the strategist agent immediately before
the LLM prompt is assembled.
"""
from __future__ import annotations

from collections.abc import Iterable

from contract.ticker_evidence import TickerEvidence


def _format_features(features: dict[str, float]) -> str:
    """Serialise a feature dict to a compact key=value string.

    Parameters
    ----------
    features:
        Mapping of feature name → numeric value.

    Returns
    -------
    str
        Comma-separated ``key=value`` pairs rounded to three significant
        figures, e.g. ``"rsi_14=60.0, pe_trailing=28.5"``.
        Returns ``"(no features)"`` when the dict is empty.
    """
    if not features:
        return "(no features)"
    return ", ".join(f"{k}={v:.3g}" for k, v in features.items())


def _format_per_analyst(te: TickerEvidence) -> list[str]:
    """Build one formatted line per analyst slot for a TickerEvidence block.

    Always emits lines in the canonical order: technical, fundamental,
    sentiment, smart_money. A missing analyst is noted as ``(missing)``; a
    no-data analyst is noted as ``no_data`` so the LLM can distinguish it from
    a genuine 0.0-confidence neutral verdict.

    Parameters
    ----------
    te:
        The TickerEvidence whose ``per_analyst`` dict will be formatted.

    Returns
    -------
    list[str]
        One string per analyst, each indented with two spaces for easy
        embedding in a larger block.
    """
    lines: list[str] = []
    for analyst in ("technical", "fundamental", "sentiment", "smart_money"):
        ev = te.per_analyst.get(analyst)

        if ev is None:
            # Slot present in the canonical catalogue but absent from this tick's data.
            lines.append(f"  - {analyst:<12} (missing)")
            continue

        if ev.verdict.is_no_data:
            # No-data verdict — no features were available; signal to LLM explicitly.
            lines.append(f"  - {analyst:<12} no_data")
            continue

        # Truncate rationale to keep the per-analyst line compact, but emit a
        # trailing ellipsis when we actually cut so neither the LLM nor a human
        # reader is fooled into treating a clipped sentence as complete.
        rationale = ev.verdict.rationale
        rationale_display = (
            rationale if len(rationale) <= 60 else rationale[:57] + "…"
        )

        lines.append(
            f"  - {analyst:<12} {ev.verdict.lean:<7} mag={ev.verdict.magnitude:.2f} "
            f"conf={ev.verdict.confidence:.2f}  "
            f"[{_format_features(ev.features)}]  — {rationale_display}"
        )

    return lines


def render_ticker_evidence(items: Iterable[TickerEvidence]) -> str:
    """Render a collection of TickerEvidence objects as a prompt-ready string.

    Produces one text block per ticker, each containing:
    - The aggregate lean, magnitude, confidence, and disagreement.
    - An optional summary line from the aggregator.
    - One compact line per analyst (technical, fundamental, sentiment, smart_money).

    Parameters
    ----------
    items:
        Any iterable of TickerEvidence records for the current tick.

    Returns
    -------
    str
        A human- and LLM-readable multi-line string, with ticker blocks
        separated by blank lines. Returns ``"(no evidence this tick)"`` when
        the iterable is empty.
    """
    items = list(items)
    if not items:
        return "(no evidence this tick)"

    blocks: list[str] = []

    for te in items:
        agg = te.aggregate

        # Header: ticker symbol and aggregate cross-analyst stance.
        block = [
            te.ticker,
            f"  Aggregate: {agg.lean} (magnitude {agg.magnitude:.2f}, "
            f"confidence {agg.confidence:.2f}, disagreement {agg.disagreement:.2f})",
        ]

        # Optional human-readable summary from the aggregator, e.g. "3/4 bullish, 1 neutral".
        if agg.summary:
            block.append(f"  Summary: {agg.summary}")

        # Per-analyst breakdown.
        block.extend(_format_per_analyst(te))

        blocks.append("\n".join(block))

    return "\n\n".join(blocks)
