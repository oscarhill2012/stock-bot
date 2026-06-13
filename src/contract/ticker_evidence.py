"""TickerEvidence — the per-ticker per-tick aggregate the strategist reads.

Built deterministically from per-analyst AnalystEvidence by
`contract.digest.build_ticker_evidence`. The shape mirrors the persisted
`TickerEvidenceRow` defined in Plan D so a TickerEvidence object can round-
trip to and from SQLite without any field-name translation.

`AggregateVerdict` carries `lean` + `magnitude` + `confidence` + `disagreement`
+ `summary` so the whole cross-analyst stance is one self-contained record —
this is the lookup primitive the future knowledge-base loop (backlog B2) will
key on. `weights` lives at the `TickerEvidence` level (not nested inside the
aggregate) so the snapshotted weighting can evolve independently of stance
fields without breaking aggregate-row equality.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PositiveFloat

from contract.evidence import AnalystEvidence


class AggregateVerdict(BaseModel):
    """Cross-analyst summary stance.

    `magnitude` = |weighted signed-confidence sum| / total weight, the
    "how far from neutral" axis. `lean` is "neutral" when magnitude < dead-zone.
    `confidence` is the mean confidence across contributing (non-no_data)
    analysts — kept separate from magnitude so the KB can distinguish
    high-magnitude/low-confidence setups from high-magnitude/high-confidence
    ones. `disagreement` is variance of signed confidences in [0,1].
    `summary` is a short rendered string ("3/4 bullish, 1 neutral") suitable
    for dropping into prompts or KB lookups.
    """

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    disagreement: float = Field(ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=240)


class TickerEvidence(BaseModel):
    """One row of evidence the strategist sees for a ticker on a tick.

    ``last_price`` carries the live close at evidence-build time so the
    strategist's per-ticker renderer can show "where the ticker is trading
    right now" in the section header — see ``contract.strategist_prompt``.

    ``None`` is the SOLE "no price" sentinel.  Any zero or negative value
    raises a ``ValidationError`` at schema validation — the upstream that fed
    it (typically the technical extractor's ``last_close=0.0`` no-bars case)
    must coerce to ``None`` at the emission site so the absence is loud, not
    silent.  Renderers need only guard against ``None``, never ``0.0``.
    """

    ticker: str
    tick_id: str
    recorded_at: datetime
    per_analyst: dict[str, AnalystEvidence]
    aggregate: AggregateVerdict
    weights: dict[str, float]

    # PositiveFloat rejects 0.0 and negatives at validation time.
    # None is the sole "no price available" signal — producers must coerce
    # the no-bars / no-data case to None rather than 0.0.
    last_price: PositiveFloat | None = None
