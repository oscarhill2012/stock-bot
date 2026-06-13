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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    # ``max_length`` intentionally NOT set on ``name`` / ``body`` — Vertex's
    # constrained decoder treats schema-level ``maxLength`` as a fill target
    # and pads strings (verbatim repetition, hallucinated padding) toward the
    # cap.  Mirrors the strategist treatment of ``reason`` / ``rationale``
    # (commit 7590ba1).  The prompt states the upper bound in words; trust
    # the model to honour it.
    name:      str   = Field(min_length=1)
    direction: Literal["bull", "bear", "neutral"]
    weight:    float = Field(ge=0.0, le=1.0)
    body:      str   = Field(min_length=1)


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

    # ``max_length`` intentionally NOT set on ``summary`` — same Vertex
    # pad-target rationale as ``ReportDriver.name``/``body`` above.  Prompt
    # states the upper bound in words.
    summary: str                = Field(min_length=1)
    drivers: list[ReportDriver] = Field(min_length=2, max_length=4)


class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

    # ``rationale`` is downstream-only: deterministic extractors (Technical,
    # Social, SmartMoney) still populate it as a one-line summary built from
    # their tag list.  LLM analysts (News, Fundamental) no longer emit it —
    # ``report.summary`` carries the same surface and the duplication was
    # driving the constrained-decoder repetition pathology (commit 7590ba1
    # for the strategist's analogous fix).  Default ``""`` lets a verdict
    # built from the LLM emit-schema (``LlmTickerVerdict``) round-trip
    # through ``AnalystVerdict.model_validate`` without supplying the field.
    # ``max_length`` intentionally NOT set — Vertex pad-target rationale; the
    # field is no longer LLM-facing but the cap is no longer load-bearing.
    rationale: str = Field(default="")

    key_factors: list[str] = Field(default_factory=list, max_length=8)
    is_no_data: bool = False

    # New in Phase 5 redesign: LLM analysts populate this; deterministic
    # analysts leave it None. The Strategist prompt surface keys off presence
    # to decide whether to render a "Drivers:" block.
    report: AnalystReport | None = None

    @model_validator(mode="after")
    def _prose_surface_required_when_data_present(self) -> AnalystVerdict:
        """A non-no-data verdict must carry exactly one prose surface.

        - Deterministic extractors populate ``rationale`` (a one-line
          ``", "``-joined factor list) and leave ``report=None``.
        - LLM analysts populate ``report`` (summary + drivers block) and
          leave ``rationale=""``.
        - Carrying both is the old synthetic-prose pathology (extractors
          fabricating an ``AnalystReport`` to satisfy the previous validator)
          and is rejected loudly so it can't silently reappear.
        - ``is_no_data=True`` short-circuits the check; the canonical no-data
          shape is ``rationale="<reason>"`` with ``report=None``.
        """

        # No-data verdicts have their own shape contract; the builder enforces it.
        if self.is_no_data:
            return self

        has_rationale = bool(self.rationale)
        has_report    = self.report is not None

        if has_rationale and has_report:
            raise ValueError(
                "verdict carries both rationale and report — exactly one prose "
                "surface is allowed: rationale (deterministic extractors) OR "
                "report (LLM analysts)"
            )

        if not has_rationale and not has_report:
            raise ValueError(
                "verdict has no prose surface — populate either rationale "
                "(deterministic) or report (LLM)"
            )

        return self


class TickerVerdict(AnalystVerdict):
    """An ``AnalystVerdict`` carrying the ticker it applies to.

    The canonical downstream shape consumed by the strategist evidence view,
    the deterministic extractors, persistence, and decision logger.

    LLM analysts (News, Fundamental) emit the narrower :class:`LlmTickerVerdict`
    instead; the per-ticker LlmAgent's ``output_schema`` is the narrow class,
    and the joiner inflates each LLM emit into a ``TickerVerdict`` for
    downstream consumption — ``rationale`` defaults to ``""`` on the inflated
    object since the LLM no longer emits it.
    """

    ticker: str


def _no_data_analyst_verdict(*, reason: str) -> AnalystVerdict:
    """Canonical 'we had no data this tick' shape, ticker-free.

    Used by per-analyst extractors which return AnalystVerdict (not
    TickerVerdict — the joiner attaches the ticker later).

    Parameters:
        reason:  short prose explanation (must be non-empty).

    Raises:
        ValueError: if reason is empty or whitespace-only — every
                    no-data site already has a real reason available;
                    silent defaults are the recurring bug class
                    (auto-memory: silent-failures-loud-tests).
    """

    if not reason or not reason.strip():
        raise ValueError(
            "no-data verdict requires a non-empty reason — silent "
            "fallback strings are the bug class this builder closes"
        )

    return AnalystVerdict(
        lean="neutral",
        magnitude=0.0,
        confidence=0.0,
        rationale=reason,
        key_factors=[],
        is_no_data=True,
    )


def build_no_data_verdict(ticker: str, *, reason: str) -> TickerVerdict:
    """Canonical 'we had no data this tick' shape, ticker-attached.

    Single source of truth for the three sites that previously
    hand-rolled no-data verdicts with drifting confidence / wording /
    direction (A-015). Strategist derivation and any joiner that needs
    a per-ticker no-data record should call this.

    Parameters:
        ticker:  symbol the verdict applies to.
        reason:  short prose explanation (must be non-empty).

    Raises:
        ValueError: if reason is empty or whitespace-only.
    """

    if not reason or not reason.strip():
        raise ValueError(
            "no-data verdict requires a non-empty reason — silent "
            "fallback strings are the bug class this builder closes"
        )

    return TickerVerdict(
        ticker=ticker,
        lean="neutral",
        magnitude=0.0,
        confidence=0.0,
        rationale=reason,
        key_factors=[],
        is_no_data=True,
    )


class LlmTickerVerdict(BaseModel):
    """Narrow per-ticker emit-schema for the News + Fundamental LLM analysts.

    Two-class split introduced after the 2026-05-25 schema-failure audit on
    backtest ``post-mem-test-5`` — mirrors the strategist's ``StrategistDecision``
    / ``StrategistLLMDecision`` split (commit 7590ba1).  Root cause: the
    previous emit-schema (``TickerVerdict``) declared ``is_no_data: bool =
    False`` and ``report: AnalystReport | None = None``, both optional in the
    generated JSON Schema.  Vertex's constrained decoder honoured the schema
    and routinely emitted just the canonical five fields (``lean / magnitude
    / confidence / rationale / key_factors``) and stopped, omitting both
    ``is_no_data`` and ``report``.  The Python ``model_validator`` then
    inferred ``is_no_data=False`` from the default and rejected the verdict
    for missing ``report`` — 97 % of all schema retries in the audit window
    matched this exact pattern, and all six terminal isolations were this
    failure mode.

    Three structural fixes are folded into this class — same trio that fixed
    the strategist:

    1. **Required-by-schema, not by post-validator.**  ``is_no_data`` and
       ``report`` are required (no defaults, no ``| None``) so the JSON
       Schema sent to Vertex marks them as mandatory.  The model can no
       longer take the "shortest legal path" that omits them.

    2. **Field order — structured fields before prose.**  Pydantic v2 emits
       JSON-Schema ``properties`` in declaration order and Vertex honours
       that order at decode time.  Cheap, bounded fields are declared first
       so the model commits to them while still on-task; any prose spiral
       inside ``report`` can only truncate its own contents, never strand
       a required structural field.

    3. **No ``max_length`` on prose fields.**  Vertex's constrained decoder
       treats schema ``maxLength`` as a fill target and pads strings toward
       it (verbatim repetition, hallucinated padding).  All prose caps are
       stated in the prompt instead.  The single 7 590-character AMZN
       repetition-loop truncation case in the audit was the visible symptom
       of this same pathology.

    4. **No ``rationale`` field.**  ``report.summary`` carries the same
       surface; emitting both duplicated the prose pressure with no
       downstream gain.  The joiner inflates each emit into ``TickerVerdict``
       (which keeps ``rationale=""`` as a downstream default) so the rest
       of the pipeline is untouched.

    ``model_config = ConfigDict(extra="forbid")`` ensures any drift between
    this class and a stale prompt fails loudly rather than silently dropping
    fields.
    """

    model_config = ConfigDict(extra="forbid")

    # ── Structured commitment fields (declared first on purpose) ────────────
    #
    # All required, all cheap, all bounded.  The model commits to them before
    # any decoder spiral inside the prose payload below.

    ticker: str

    lean: Literal["bullish", "bearish", "neutral"]

    magnitude:  float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)

    # ``is_no_data`` is REQUIRED on the LLM emit (no default, no Optional).
    # The model must decide explicitly: ``true`` → no data this tick, ``false``
    # → real verdict.  Either branch still requires ``report`` below (a
    # ``true`` branch can carry a one-line "no data" summary).  Making it
    # required closes the dominant 2026-05-25 failure mode where the model
    # silently omitted the field.
    is_no_data: bool

    # Closed-vocabulary tags — short, structured, list-bounded.  Declared
    # before ``report`` so the model commits the tags while still on-task.
    key_factors: list[str] = Field(default_factory=list, max_length=8)

    # ── Free-text payload (declared last on purpose) ────────────────────────
    #
    # ``report`` is REQUIRED on every emit (no ``| None``).  Pydantic v2 emits
    # nested object schemas inline, so Vertex sees ``summary`` and
    # ``drivers`` as required sub-fields — the model can no longer stop
    # short of the report block.  ``AnalystReport`` itself has had
    # ``max_length`` removed from its prose fields above for the same
    # pad-target reason.

    report: AnalystReport

    @model_validator(mode="after")
    def _ticker_non_empty(self) -> LlmTickerVerdict:
        """Reject an empty ticker string — would silently break joiner indexing."""
        if not self.ticker:
            raise ValueError("ticker must be a non-empty string")
        return self

    def to_ticker_verdict(self) -> TickerVerdict:
        """Inflate this narrow LLM emit-schema into the canonical TickerVerdict.

        Sole conversion point between the LLM emit-shape and the downstream
        canonical shape — every joiner and consumer goes through this method,
        so the strict-shape boundary is named and singular.

        ``rationale`` defaults to ``""`` on the canonical side: LLM analysts no
        longer emit it (the field's pad-toward-cap pressure was the root cause
        of the 2026-05-25 repetition pathology — see this class's docstring).
        Deterministic analysts populate ``rationale`` directly via
        ``TickerVerdict(rationale=..., ...)`` and never traverse this method.

        Returns:
            TickerVerdict: the canonical downstream shape, with ``rationale``
            defaulted to ``""`` and every LLM-emitted field carried across.

        Raises:
            ValueError: if post-conversion the canonical shape would itself be
                invalid (the ``AnalystVerdict._prose_surface_required_when_data_present``
                validator fires) — re-raised so the failure site names the LLM,
                not a downstream consumer. This is the loud-failure surface that
                replaces the old silent
                ``TickerVerdict.model_validate({**raw_v, "ticker": ticker})``
                pattern duplicated across joiners.
        """

        # ``model_dump`` strips Pydantic's runtime model and emits a plain dict;
        # ``rationale`` is absent (the LLM never emitted it), so the canonical
        # constructor takes the default "" — exactly the downstream contract.
        #
        # Schema-coupling invariant: this conversion relies on every field NAME
        # on ``LlmTickerVerdict`` being a subset of ``TickerVerdict``'s field
        # names.  ``TickerVerdict`` does not set ``extra="forbid"``, so a field
        # renamed on one side without updating the other would silently default
        # on the canonical side rather than raise — a future refactor must
        # update both classes in lockstep.
        payload = self.model_dump()
        return TickerVerdict.model_validate(payload)


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
