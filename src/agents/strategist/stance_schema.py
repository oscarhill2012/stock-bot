"""TickerStance — the strategist's per-ticker decision substrate.

The strategist emits one ``TickerStance`` per watchlist ticker on every tick.
Downstream derivation helpers (``derive_legacy_fields``) read a list of stances
and produce the flat ``target_weights``, ``new_positions``, ``close_reasons``,
and ``trim_reasons`` fields that the executor already expects — preserving
backwards compatibility while giving the strategist a richer internal model.

The stance is *not* a trade instruction. It expresses the strategist's desired
portfolio position and the reasoning behind it. The executor translates that
desire into actual orders.

Consumers:
- ``derive_legacy_fields`` (C4) — flattens stances into the legacy output shape
- ``StrategistDecision`` (C7) — embeds the stance list in the decision payload
- The after-callback (C9) — validates stances before persisting
- ``TickerStanceRow`` (C10) — persists each stance to the database
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from config.strategist import get_strategist_config

# Char caps sourced from ``config/strategist.json`` — see ``src/config/
# strategist.py`` for the rationale and the "more is not always better"
# philosophy note.  Resolved at import time so the Field constraints stay
# literal ints, as Pydantic v2 expects.
#
# Note the deliberate gap between the prompt-facing cap (e.g. 200) and the
# schema cap (e.g. 220).  ``_cfg.schema_cap()`` applies the configured
# ``slack_percent`` headroom so the schema absorbs the LLM's natural 1–5%
# character overshoot.  The prompt template still tells the model the
# prompt-facing cap — do not "fix" the mismatch.
_cfg        = get_strategist_config()
_STANCE     = _cfg.stance_caps
_schema_cap = _cfg.schema_cap                                                  # alias for terser Field declarations


class TickerStance(BaseModel):
    """One stance per watchlist ticker per strategist tick.

    Args:
        ticker: The stock ticker symbol (e.g. ``"AAPL"``).
        preferred_weight: Target portfolio weight in ``[0.0, 1.0]``.
            0.0 signals a full close; values above the risk gate's
            single-position cap will be clamped downstream.
        conviction: The strategist's own confidence in this stance,
            in ``[0.0, 1.0]``. Distinct from analyst ``confidence`` —
            conviction is the *synthesised* view after weighing all
            analyst signals, whereas analyst confidence is each analyst's
            self-reported certainty about their individual signal.
        rationale: A short human-readable justification for the stance
            (max 200 chars — intentionally brief; full reasoning lives in
            the LLM's chain-of-thought, not the schema).
        horizon: Investment horizon for a non-zero stance.  Required
            whenever ``preferred_weight > 0`` (opens AND adds); ``None``
            only on full closes or hold-flat stances.
        target_price: Price target (fundamental upside anchor).  Required
            whenever ``preferred_weight > 0``; ``None`` on closes/holds.
        stop_price: Stop-loss level beyond which the thesis is
            invalidated.  Required whenever ``preferred_weight > 0``; the
            risk gate may enforce this independently but the strategist
            must always articulate one when committing capital.
        catalyst: Optional short description of the expected near-term
            catalyst that underpins the stance (max 80 chars).
        close_reason: Why the position is being closed (``preferred_weight
            == 0.0``). Distinct from ``trim_reason`` — a close exits the
            position entirely, whereas a trim just reduces it.
        trim_reason: Why the position size is being reduced but not zeroed.
    """

    ticker: str

    # Target portfolio weight — bounded fraction of total portfolio value.
    preferred_weight: float = Field(ge=0.0, le=1.0)

    # Synthesised conviction after weighing all analyst signals.
    conviction: float = Field(ge=0.0, le=1.0)

    # Brief justification; kept short to encourage clear thinking.
    rationale: str = Field(max_length=_schema_cap(_STANCE.rationale_max_chars))

    # Optional lifecycle / context fields
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=_schema_cap(_STANCE.catalyst_max_chars))

    # Reason fields — only one should be set per stance, depending on action.
    # close_reason: full exit (preferred_weight == 0.0).
    # trim_reason:  partial reduction (preferred_weight > 0.0 but lower than current).
    close_reason: str | None = Field(default=None, max_length=_schema_cap(_STANCE.close_reason_max_chars))
    trim_reason:  str | None = Field(default=None, max_length=_schema_cap(_STANCE.trim_reason_max_chars))

    # ── Non-zero position contract ───────────────────────────────────────────
    # Any stance proposing a non-zero portfolio weight (opens AND adds) must
    # carry the lifecycle hint fields the executor / memory writer need to
    # populate ``PositionThesis``: ``horizon``, ``target_price``, and
    # ``stop_price``.  Enforced here at the schema level so a malformed LLM
    # output fails ADK's ``output_schema`` parse rather than reaching the
    # strategist's after-callback as a silent partial decision.  The earlier
    # ``return _reprompt(...)`` path in the after-callback did not actually
    # re-prompt the LLM — see the strategist-callback bug fix.
    @model_validator(mode="after")
    def _require_lifecycle_hints_on_nonzero(self) -> TickerStance:
        """Reject any stance with ``preferred_weight > 0`` and missing exit discipline.

        A non-zero weight commits capital, so the strategist must always
        articulate the price target it expects to reach (``target_price``),
        the stop-loss level beyond which the thesis is invalidated
        (``stop_price``), and the holding horizon (``horizon``).  Holds and
        full closes (``preferred_weight == 0``) are exempt — those stances
        carry no exit discipline of their own.

        Raises:
            ValueError: if any required field is missing on a non-zero
                stance.  Aggregated into a single message so the LLM sees
                every missing field at once on the re-prompt.
        """

        if self.preferred_weight <= 0.0:

            # Zero-weight stances (full close / hold-flat) have no exit
            # discipline of their own — close_reason / trim_reason are
            # validated elsewhere where current portfolio weights are known.
            return self

        missing: list[str] = [

            name
            for name, value in (
                ("horizon",      self.horizon),
                ("target_price", self.target_price),
                ("stop_price",   self.stop_price),
            )
            if value is None
        ]

        if missing:

            raise ValueError(
                f"Stance for {self.ticker} proposes a non-zero weight "
                f"({self.preferred_weight}) but is missing required lifecycle "
                f"hint fields: {missing}.  Any non-zero stance must include "
                f"horizon, target_price, and stop_price so the executor and "
                f"memory writer can populate PositionThesis correctly."
            )

        return self
