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

from pydantic import BaseModel, Field, model_validator

from config.analysts import get_analysts_config

AnalystName = Literal["technical", "fundamental", "news", "social", "smart_money"]

# ---------------------------------------------------------------------------
# Cap resolution
# ---------------------------------------------------------------------------
# Char caps on LLM-emitted free-text fields come from ``config/analysts.json``
# via the analyst-config loader.  The values referenced by the schemas are the
# *schema* caps (prompt-facing cap + ``slack_percent`` headroom) — the prompt
# templates still substitute the prompt-facing values.  See the "two-tier
# convention" note in ``src/config/strategist.py`` and the analyst-specific
# rationale in ``src/config/analysts.py``.  The two-tier gap is intentional
# and load-bearing; do **not** "fix" the apparent mismatch with the prompt.
# ---------------------------------------------------------------------------

_cfg          = get_analysts_config()
_OUT          = _cfg.output_caps
_schema_cap   = _cfg.schema_cap                                                # alias for terser Field declarations


class ReportDriver(BaseModel):
    """One driver of an LLM analyst's lean — a labelled, weighted reason.

    Drivers complement the closed-vocab ``key_factors`` field on
    ``AnalystVerdict``: tags are machine-aggregatable; drivers are
    strategist-readable prose with relative weighting.

    Parameters
    ----------
    name:
        Short label for the driver. Capped at
        ``output_caps.report_driver_name_max_chars`` (prompt-facing) plus
        ``slack_percent`` schema headroom.
    direction:
        The directional signal this driver contributes — one of
        "bull", "bear", or "neutral".
    weight:
        Relative importance of this driver vs the others, in [0, 1].
        Drivers within a report should sum roughly to 1.0 but the
        constraint is not strictly enforced.
    body:
        Prose explanation of the driver. No source URLs — synthesise.
        Capped at ``output_caps.report_driver_body_max_chars`` (prompt-facing)
        plus ``slack_percent`` schema headroom.
    """

    name:      str   = Field(min_length=1, max_length=_schema_cap(_OUT.report_driver_name_max_chars))
    direction: Literal["bull", "bear", "neutral"]
    weight:    float = Field(ge=0.0, le=1.0)
    body:      str   = Field(min_length=1, max_length=_schema_cap(_OUT.report_driver_body_max_chars))


class AnalystReport(BaseModel):
    """LLM analyst's qualitative reasoning, paired with the verdict.

    Populated only by the LLM analysts (News, Fundamental). Deterministic
    analysts (Technical, SmartMoney, Social) leave ``AnalystVerdict.report``
    as ``None`` — their cognition is fully captured by the verdict and
    extractor features; they have no prose to summarise.

    Parameters
    ----------
    summary:
        Connective tissue covering the gestalt this tick. Not a bullet list
        — must argue the lean. Capped at
        ``output_caps.report_summary_max_chars`` (prompt-facing) plus
        ``slack_percent`` schema headroom.
    drivers:
        2-4 ``ReportDriver`` entries giving the primary reasons for the
        lean. Min 2 enforces proper differentiation; max 4 prevents dilution.
    """

    summary: str                = Field(min_length=1, max_length=_schema_cap(_OUT.report_summary_max_chars))
    drivers: list[ReportDriver] = Field(min_length=2, max_length=4)


class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=_schema_cap(_OUT.verdict_rationale_max_chars))
    key_factors: list[str] = Field(default_factory=list, max_length=8)
    is_no_data: bool = False

    # New in Phase 5 redesign: LLM analysts populate this; deterministic
    # analysts leave it None. The Strategist prompt surface keys off presence
    # to decide whether to render a "Drivers:" block.
    report: AnalystReport | None = None

    @model_validator(mode="after")
    def _report_required_when_data_present(self) -> AnalystVerdict:
        """Reject verdicts that claim data but omit the report block.

        LLM analysts must emit ``report`` whenever ``is_no_data=False`` — the
        strategist reads the prose to weigh evidence.  Schema-level
        enforcement is the source of truth; the prompt instruction is the
        LLM-facing statement of the same rule.  ``llm_retry`` already
        classifies ``pydantic.ValidationError`` as retryable, so an
        offending LLM response is automatically retried up to the
        configured cap.
        """

        if not self.is_no_data and self.report is None:
            raise ValueError(
                "report is required when is_no_data=False — "
                "the analyst must emit a summary + drivers block "
                "alongside the verdict"
            )
        return self


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

    `raw_text` is an optional pass-through of the raw provider text the LLM
    analyst saw (News headlines, Fundamental filing excerpts).  Empty / None
    for deterministic analysts (Technical, Social, SmartMoney) where there
    is no provider prose.  Capped at 10 000 characters to keep the strategist
    prompt bounded.
    """

    ticker: str
    analyst: AnalystName
    tick_id: str
    recorded_at: datetime
    features: dict[str, float]
    feature_warnings: list[str] = Field(default_factory=list)
    verdict: AnalystVerdict
    raw_text: str | None = Field(default=None, max_length=10_000)
