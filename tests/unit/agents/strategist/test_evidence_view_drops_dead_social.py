"""M3 — strategist evidence omits ``[Social]`` rows when all are is_no_data.

Social is permanently no-data (no provider wired) — the strategist's
per-ticker block was rendering 20 dead ``[Social] is_no_data: true`` rows
per tick, ~600 chars of dead attention.  This test pins the omission
behaviour and the symmetric "populated row appears when data lands"
contract.
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.evidence_view import _format_per_analyst
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _no_data_verdict() -> AnalystVerdict:
    return AnalystVerdict.model_validate(
        {
            "lean":        "neutral",
            "magnitude":   0.0,
            "confidence":  0.0,
            "rationale":   "no data",
            "key_factors": [],
            "is_no_data":  True,
            "report":      None,
        }
    )


def _minimal_te(per_analyst: dict) -> TickerEvidence:
    """Build a minimal TickerEvidence around a custom per_analyst dict.

    Parameters
    ----------
    per_analyst:
        The analyst map to embed in the ticker evidence.

    Returns
    -------
    TickerEvidence
        A valid TickerEvidence with stub aggregate fields.
    """
    return TickerEvidence(
        ticker      = "AAPL",
        tick_id     = "tick_X",
        recorded_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        per_analyst = per_analyst,
        aggregate   = AggregateVerdict(
            lean         = "neutral",
            magnitude    = 0.0,
            confidence   = 0.0,
            disagreement = 0.0,
        ),
        weights     = {},
    )


def test_no_data_social_row_omitted() -> None:
    """A no-data Social verdict produces no Social line in the rendered block."""

    te = _minimal_te({
        "social": AnalystEvidence(
            ticker      = "AAPL",
            analyst     = "social",
            tick_id     = "tick_X",
            recorded_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            verdict     = _no_data_verdict(),
            features    = {},
        ),
    })

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)
    assert "social" not in joined.lower(), (
        f"expected no social line for is_no_data=True, got: {joined!r}"
    )


def test_populated_social_row_appears() -> None:
    """A populated Social verdict still renders normally."""

    verdict = AnalystVerdict.model_validate(
        {
            "lean":        "bullish",
            "magnitude":   0.6,
            "confidence":  0.7,
            "rationale":   "active social chatter",
            "key_factors": [],
            "is_no_data":  False,
            "report":      {
                "summary":  "Active discussion across stocktwits and reddit.",
                "drivers":  [
                    {"name": "vol-up", "direction": "bull", "weight": 0.6, "body": "x"},
                    {"name": "tone",   "direction": "bull", "weight": 0.4, "body": "y"},
                ],
            },
        }
    )

    te = _minimal_te({
        "social": AnalystEvidence(
            ticker      = "AAPL",
            analyst     = "social",
            tick_id     = "tick_X",
            recorded_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
            verdict     = verdict,
            features    = {},
        ),
    })

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)
    assert "social" in joined.lower(), (
        f"expected social line for populated verdict, got: {joined!r}"
    )
