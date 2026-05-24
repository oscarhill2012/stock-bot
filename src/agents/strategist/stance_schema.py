"""TickerStance — the strategist's per-ticker decision substrate.

The strategist emits one ``TickerStance`` per watchlist ticker on every tick.
Downstream derivation helpers (``derive_legacy_fields``) read a list of stances
and produce the flat ``target_weights``, ``close_reasons``, and ``trim_reasons``
fields that downstream agents expect.  ``new_positions`` was removed in Band 6
(see module docstring for why).

The stance is *not* a trade instruction. It expresses the strategist's desired
portfolio position and the reasoning behind it. The executor translates that
desire into actual orders.

Consumers:
- ``derive_legacy_fields`` (C4) — flattens stances into the legacy output shape
- ``StrategistDecision`` (C7) — embeds the stance list in the decision payload
- The after-callback (C9) — validates stances before persisting
- ``TickerStanceRow`` (C10) — persists each stance to the database

Verb vocabulary (Spec B — Band 3)
----------------------------------
The ``intent`` field replaces the implicit ``preferred_weight``-derived action
for the richer vocabulary.  Full verb set:

    open   — enter flat → held; broker BUY to ``weight``.
    add    — increase existing position; broker BUY delta to ``weight``.
    trim   — reduce existing position (not to zero); broker SELL delta.
    close  — full exit; broker SELL all.
    hold   — no trade; review fields only.  ``reason`` required.
    update — no trade; mutate thesis fields only.  ``reason`` + at least
             one of target_price / stop_price / catalyst / horizon required.

The legacy ``preferred_weight`` / ``conviction`` / ``close_reason`` /
``trim_reason`` fields remain on the model for backward compatibility
with ``derive_legacy_fields`` and the existing test suite.

``new_positions`` (the pre-computed ``PositionThesis`` for each ``open``
stance) was removed from the derivation pipeline in Band 6.  The executor
now assembles it from the fill price + stance via ``apply_stance_to_thesis``.
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
            Legacy field — 0.0 signals a full close; values above the risk
            gate's single-position cap will be clamped downstream.
        conviction: The strategist's own confidence in this stance,
            in ``[0.0, 1.0]``. Distinct from analyst ``confidence`` —
            conviction is the *synthesised* view after weighing all
            analyst signals, whereas analyst confidence is each analyst's
            self-reported certainty about their individual signal.
        intent: Stance verb.  See Spec B §'Stance vocabulary'.  New in
            Band 3 — replaces the implicit ``preferred_weight``-derived
            action.  Accepted values: ``open``, ``add``, ``trim``,
            ``close``, ``hold``, ``update``.
        weight: Post-stance portfolio weight in ``[0, 1]``.  Required for
            open/add/trim; ignored for close/hold/update.
        reason: Required for hold/trim/update — the "what's changed since
            opening" articulation.  Ignored for open/add/close.
        rationale: A short human-readable justification for the stance.
            Required on ``open`` (FROZEN at entry — Invariant 3); ignored
            on add/trim/close/hold/update.  Also present on legacy stances
            as a free-text field.
        horizon: Investment horizon.  Required on ``open`` (seeds the
            PositionThesis); optional on add/update; ignored on
            trim/close/hold.
        target_price: Price target (fundamental upside anchor).  Required
            on ``open``; optional on add/update.
        stop_price: Stop-loss level.  Required on ``open``; optional on
            add/update.
        catalyst: Optional short description of the expected near-term
            catalyst.
        close_reason: Legacy field — why the position is being closed.
            Distinct from ``trim_reason`` — a close exits the position
            entirely, whereas a trim just reduces it.
        trim_reason: Legacy field — why the position size is being reduced
            but not zeroed.
    """

    ticker: str

    # ── Legacy weight/conviction fields (preserved for backward compat) ──────

    # Target portfolio weight — bounded fraction of total portfolio value.
    preferred_weight: float = Field(ge=0.0, le=1.0)

    # Synthesised conviction after weighing all analyst signals.
    conviction: float = Field(ge=0.0, le=1.0)

    # ── Spec B Band 3: new intent enum + verb-conditional fields ─────────────

    intent: Literal["open", "add", "trim", "close", "hold", "update"] | None = Field(
        None,
        description="Stance verb.  See Spec B §'Stance vocabulary'.",
    )

    weight: float | None = Field(
        None,
        description=(
            "Post-stance portfolio weight in [0, 1].  Required for "
            "open/add/trim.  Ignored for close/hold/update."
        ),
    )

    reason: str | None = Field(
        None,
        description=(
            "Required for hold/trim/update — the 'what's changed since "
            "opening' articulation.  Ignored for open/add/close."
        ),
    )

    # Optional lifecycle / context fields (exist on legacy model too).
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=_schema_cap(_STANCE.catalyst_max_chars))

    # Brief justification; kept short to encourage clear thinking.
    # On legacy stances this is always populated; on new intent-based stances
    # it is only required on ``open`` (FROZEN at entry per Invariant 3).
    rationale: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.rationale_max_chars),
        description=(
            "Required on open (FROZEN at entry — Invariant 3).  "
            "Ignored on add/trim/close/hold/update."
        ),
    )

    # ── Legacy reason fields (preserved for backward compat) ─────────────────

    # close_reason: full exit (preferred_weight == 0.0).
    # trim_reason:  partial reduction (preferred_weight > 0.0 but lower than current).
    close_reason: str | None = Field(default=None, max_length=_schema_cap(_STANCE.close_reason_max_chars))
    trim_reason:  str | None = Field(default=None, max_length=_schema_cap(_STANCE.trim_reason_max_chars))

    # ── Non-zero position contract (legacy validator) ─────────────────────────
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

        Note: this validator only fires when the legacy ``preferred_weight``
        field is present and non-zero.  Intent-based stances (``intent``
        field set) are validated by ``_require_intent_fields`` instead.

        Raises:
            ValueError: if any required field is missing on a non-zero
                legacy stance.  Aggregated into a single message so the LLM
                sees every missing field at once on the re-prompt.
        """

        # Skip if this is a new intent-based stance — the other validator handles it.
        if self.intent is not None:
            return self

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
                # ``rationale`` is also required on non-zero legacy stances so
                # ``derive_legacy_fields`` can construct a valid PositionThesis
                # (whose ``rationale`` field is required).  Omitting it here
                # used to let the schema pass but fail later at derivation.
                ("rationale",    self.rationale),
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

    @model_validator(mode="after")
    def _require_intent_fields(self) -> TickerStance:
        """Validate verb-conditional field requirements when ``intent`` is set.

        Each verb in the Spec B stance vocabulary has a set of required fields.
        This validator enforces those rules at schema parse time so a malformed
        LLM output fails early with a clear message rather than silently
        propagating a partial stance to the executor.

        Verb rules (Spec B §'Validation rules'):
            open:   weight, target_price, stop_price, catalyst, horizon,
                    rationale all required.
            add:    weight required.
            trim:   weight + reason required.
            close:  no additional required fields beyond ticker + intent.
            hold:   reason required.
            update: reason + at least one of target_price / stop_price /
                    catalyst / horizon required.

        Raises:
            ValueError: describing the violated rule and, where applicable,
                suggesting the alternative verb.
        """

        # Only applies when the new intent field is present.
        if self.intent is None:
            return self

        match self.intent:

            case "open":
                # ``open`` seeds a new PositionThesis — all commitment fields
                # are required to populate the row with complete data.
                missing = [
                    name for name, value in (
                        ("weight",       self.weight),
                        ("target_price", self.target_price),
                        ("stop_price",   self.stop_price),
                        ("catalyst",     self.catalyst),
                        ("horizon",      self.horizon),
                        ("rationale",    self.rationale),
                    )
                    if value is None
                ]
                if missing:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='open' but is missing "
                        f"required fields: {missing}.  All of weight, target_price, "
                        f"stop_price, catalyst, horizon, rationale are required on "
                        f"open (they seed the PositionThesis row)."
                    )

            case "add":
                # ``add`` increases an existing position — only weight is
                # strictly required; commitment fields are optional (they
                # mutate the existing PositionThesis if supplied).
                if self.weight is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='add' but weight is "
                        f"missing.  weight is required on add so the executor knows "
                        f"the target size after the buy."
                    )

            case "trim":
                # ``trim`` reduces but does not close — weight anchors the
                # new target size and reason articulates what changed.
                missing = [
                    name for name, value in (
                        ("weight", self.weight),
                        ("reason", self.reason),
                    )
                    if value is None
                ]
                if missing:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='trim' but is missing "
                        f"required fields: {missing}.  Did you mean 'close' (no weight "
                        f"needed) or 'hold' (no weight change, but reason still required)?"
                    )

            case "close":
                # ``close`` exits fully — no extra required fields; the
                # executor sells the entire position.
                pass

            case "hold":
                # ``hold`` is a no-trade review — reason articulates what
                # changed (or didn't) since opening.
                if self.reason is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='hold' but reason is "
                        f"missing.  reason is required on hold to articulate what has "
                        f"changed since the position was opened."
                    )

            case "update":
                # ``update`` mutates thesis fields with no trade — reason is
                # required, plus at least one field to actually update.
                errors: list[str] = []

                if self.reason is None:
                    errors.append(
                        "reason is missing — reason is required on update to "
                        "articulate why the thesis parameters are changing"
                    )

                # Require at least one of the mutable commitment fields.
                has_update = any(
                    v is not None for v in (
                        self.target_price,
                        self.stop_price,
                        self.catalyst,
                        self.horizon,
                    )
                )
                if not has_update:
                    errors.append(
                        "at least one of target_price / stop_price / catalyst / "
                        "horizon must be supplied so the update has something to "
                        "mutate — did you mean 'hold' (no thesis fields changing)?"
                    )

                if errors:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='update' but: "
                        + "; ".join(errors)
                    )

        return self
