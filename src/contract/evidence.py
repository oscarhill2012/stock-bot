"""Per-analyst evidence types — code-only digest substrate.

Each analyst returns one AnalystEvidence per ticker per tick. The deterministic
aggregator in `contract.digest` collapses the four analysts' evidence into one
TickerEvidence per ticker.

The schema below is the same shape that Plan D persists to SQLite, so the
contract is identical from Plan A through Plan D. Several fields exist to
support the future knowledge-base / learning loop (see backlog B2):

- `magnitude` is independent of `confidence` so per-evidence-key weighting
  (backlog B5) can learn that some feature ranges matter more than others.
- `key_factors` survives JSON round-tripping and is the structured pattern-
  recall primitive the KB will key off.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money"]


class ReportDriver(BaseModel):
    """One driver of an LLM analyst's lean — a labelled, weighted reason.

    Drivers complement the closed-vocab ``key_factors`` field on
    ``AnalystVerdict``: tags are machine-aggregatable; drivers are
    strategist-readable prose with relative weighting.

    Parameters
    ----------
    name:
        Short label for the driver (4-6 words), 1-60 characters.
    direction:
        The directional signal this driver contributes — one of
        "bull", "bear", or "neutral".
    weight:
        Relative importance of this driver vs the others, in [0, 1].
        Drivers within a report should sum roughly to 1.0 but the
        constraint is not strictly enforced.
    body:
        2-3 sentence explanation of the driver. No source URLs — synthesise.
        1-1000 characters.
    """

    name:      str   = Field(min_length=1, max_length=60)
    direction: Literal["bull", "bear", "neutral"]
    weight:    float = Field(ge=0.0, le=1.0)
    body:      str   = Field(min_length=1, max_length=1_000)


class AnalystReport(BaseModel):
    """LLM analyst's qualitative reasoning, paired with the verdict.

    Populated only by the LLM analysts (News, Fundamental). Deterministic
    analysts (Technical, SmartMoney, Social) leave ``AnalystVerdict.report``
    as ``None`` — their cognition is fully captured by the verdict and
    extractor features; they have no prose to summarise.

    Parameters
    ----------
    summary:
        3-5 sentences of connective tissue covering the gestalt this tick.
        Not a bullet list — must argue the lean. 1-2000 characters.
    drivers:
        2-4 ``ReportDriver`` entries giving the primary reasons for the
        lean. Min 2 enforces proper differentiation; max 4 prevents dilution.
    """

    summary: str                = Field(min_length=1, max_length=2_000)
    drivers: list[ReportDriver] = Field(min_length=2, max_length=4)


class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=160)
    key_factors: list[str] = Field(default_factory=list, max_length=8)
    is_no_data: bool = False

    # New in Phase 5 redesign: LLM analysts populate this; deterministic
    # analysts leave it None. The Strategist prompt surface keys off presence
    # to decide whether to render a "Drivers:" block.
    report: AnalystReport | None = None


class TickerVerdict(AnalystVerdict):
    """An ``AnalystVerdict`` carrying the ticker it applies to.

    LLM analysts (Fundamental, News) emit one of these per watchlist ticker.
    The ``ticker`` field lets the after-callback associate each verdict back
    to its ticker without relying on list ordering.
    """

    ticker: str


class VerdictBatch(BaseModel):
    """Top-level container for an LLM analyst's per-tick output.

    ADK's ``output_schema`` must be a single ``BaseModel`` (not a bare list),
    so per-ticker verdicts are wrapped in this batch object. The agent's
    ``after_agent_callback`` is responsible for unwrapping the ``verdicts``
    list before feature extraction.
    """

    verdicts: list[TickerVerdict] = Field(default_factory=list)


class AnalystEvidence(BaseModel):
    """One analyst's structured output for one ticker on one tick.

    `features` carries the deterministic feature extractor's output (numeric
    only — no strings). Keys are analyst-specific; see Phase 4 spec for the
    locked catalogue per analyst. `feature_warnings` records any
    extractor-emitted issues (missing data window, NaN replacement, etc.) so
    downstream consumers can tell "extractor returned 0.0 because the input
    was missing" apart from "extractor returned a real 0.0".
    """

    ticker: str
    analyst: AnalystName
    tick_id: str
    recorded_at: datetime
    features: dict[str, float]
    feature_warnings: list[str] = Field(default_factory=list)
    verdict: AnalystVerdict
