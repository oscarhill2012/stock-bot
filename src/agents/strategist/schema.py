"""Strategist output schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agents.strategist.stance_schema import TickerStance
from config.strategist import get_strategist_config

# ---------------------------------------------------------------------------
# Cap resolution
# ---------------------------------------------------------------------------
# All char caps on free-text fields are sourced from ``config/strategist.json``
# via the loader.  Resolving them at import time keeps the ``Field(max_length=
# ...)`` arguments literal, which is what Pydantic v2 expects, while still
# allowing operators to retune via a single JSON edit and a process restart.
#
# Note the deliberate gap between the prompt-facing cap and the schema cap:
# ``_cfg.schema_cap(prompt_cap)`` applies the ``slack_percent`` headroom from
# ``config/strategist.json`` (default 10%) so the schema absorbs the LLM's
# natural 1–5% character overshoot rather than truncating mid-sentence.  The
# prompt template still tells the model the prompt-facing cap (e.g. "≤600
# chars") — see the "two-tier convention" note in ``src/config/strategist.py``
# for the full rationale.  Do **not** "fix" the apparent mismatch — it is
# load-bearing.
# ---------------------------------------------------------------------------

_cfg          = get_strategist_config()
_DECISION     = _cfg.decision_caps
_POS_THESIS   = _cfg.position_thesis_caps
_schema_cap   = _cfg.schema_cap                                                # alias for terser Field declarations


class PositionThesis(BaseModel):
    """Structured rationale for an open position.

    Created by the strategist when a position is opened (with
    ``opened_price=None``, because the strategist has no fill price at
    decision time) and stamped with the real ``opened_price`` by the
    executor after the broker confirms the BUY fill.  Updated on each
    subsequent tick while the position is held.

    Why ``opened_price`` is optional: the strategist deals in intent
    (target_price, stop_price, horizon, rationale); the actual price we
    paid is a fact about execution and cannot be known until the broker
    fills the order.  The previous required-float design forced the
    strategist to invent a 0.0 fallback for fresh opens, which then
    propagated into persistence and crashed the next tick's held-view
    renderer with a divide-by-zero — see the architectural fix in this
    same change.  Legacy rows on disk may still carry ``opened_price=0.0``;
    consumers (held_view, executor SELL handler) treat both ``None`` and
    ``0.0`` as "open price unknown".
    """

    ticker: str
    opened_at: datetime
    opened_price: float | None = None                                                          # stamped by the executor post-fill; None until the BUY clears
    opened_tag: str                                                                            # decision_tag from the opening tick
    rationale: str = Field(max_length=_schema_cap(_POS_THESIS.rationale_max_chars))            # why we entered
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None   = None
    catalyst: str | None = Field(default=None, max_length=_schema_cap(_POS_THESIS.catalyst_max_chars))
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=_schema_cap(_POS_THESIS.last_review_note_max_chars))
    opened_tick_id: str = ""                                                                   # tick_id that opened this position; populated by the executor on BUY (C13); empty for legacy/pre-tick positions


class StrategistLLMDecision(BaseModel):
    """Narrow schema the LLM is asked to emit — derived fields excluded.

    Two-class split (introduced after the 2026-05-24 degenerate-decoding
    investigation): the LLM's ``response_schema`` must be a narrow shape that
    matches what the prompt actually instructs the model to produce.  The
    previous monolithic ``StrategistDecision`` exposed
    ``target_weights`` / ``close_reasons`` / ``trim_reasons`` to the model via
    the JSON Schema even though those fields are filled in by the after-callback
    — the prompt never mentioned them, so the model was constrained to emit
    three top-level fields with zero guidance.  That ambiguity correlated with
    repetition-attractor failures during structured-output decoding (responses
    starting clean then dropping into ``\\n\\n\\n`` or ``"I am. I am."`` loops).

    Field rationale:
        stances:      The substantive per-ticker output.
        decision_tag: Snake_case label naming this tick's decision.
        reasoning:    Overall narrative for the tick.
        confidence:   Float [0,1] over the whole decision.
        thesis:       Optional standing-thesis update (null = carry forward).

    All other downstream fields live on ``StrategistDecision`` and are
    constructed by the strategist's after-callback once the model output has
    been parsed and the derivation pass has run.
    """

    stances: list[TickerStance] = Field(default_factory=list)

    # decision_tag, reasoning, thesis: max_length intentionally NOT set —
    # Vertex's constrained decoder pads string fields toward schema-level
    # maxLength.  The prompt states the upper bound in words; trust the
    # model to honour it.
    decision_tag: str
    reasoning: str

    thesis: str | None = None

    confidence: float = Field(ge=0.0, le=1.0)


class StrategistDecision(BaseModel):
    """Full strategist output — LLM-emitted fields plus derived dicts.

    The LLM emits a ``StrategistLLMDecision`` (the narrow shape above).  The
    after-callback runs ``derive_decision_fields`` on the stance list and
    constructs this richer object, which is what downstream agents
    (``risk_gate``, executor, persistence, decision_logger) consume.

    ``new_positions`` was removed in Band 6.  The executor now assembles the
    ``PositionThesis`` for each ``open`` stance itself from the fill price +
    stance, using ``apply_stance_to_thesis`` in ``executor._verb_dispatch``.
    """

    # Per-ticker stances emitted directly by the LLM; the primary substrate.
    stances: list[TickerStance] = Field(default_factory=list)

    # Weight for every watchlist ticker; must be exhaustive (0 = no position).
    # Derived from stances by the after-callback (C9); defaulted here so that
    # the model can be constructed without them during testing / migration.
    target_weights: dict[str, float] = Field(default_factory=dict)

    decision_tag: str                                                                          # snake_case label for this tick
    reasoning: str = Field(max_length=_schema_cap(_DECISION.reasoning_max_chars))             # overall reasoning summary

    thesis: str | None = Field(
        None,
        description=(
            "Optional standing market thesis update.  When non-null, "
            "MemoryWriter writes the new text to state['thesis'].  "
            "When None, the prior thesis is carried forward unchanged — "
            "None is a carry-forward sentinel, not an explicit clear.  "
            # TODO Band 4: migrate this write to Executor's after_agent_callback
            # and rename state key from 'thesis' to 'user:thesis'.
        ),
        max_length=_schema_cap(_DECISION.thesis_max_chars),
    )

    confidence: float = Field(ge=0.0, le=1.0)

    # Required when closing an existing position (weight >0 → 0).
    close_reasons: dict[str, str] = Field(default_factory=dict)
    # Required when reducing (not zeroing) an existing position.
    trim_reasons: dict[str, str] = Field(default_factory=dict)
