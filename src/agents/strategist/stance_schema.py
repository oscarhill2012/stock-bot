"""TickerStance — the strategist's per-ticker decision substrate.

**Single canonical form (Band 3).**

The strategist emits one ``TickerStance`` per watchlist ticker on every tick.
Each stance carries an ``intent`` verb and the fields required for that verb;
there is no legacy dual-form — the old ``preferred_weight`` / ``conviction`` /
``close_reason`` / ``trim_reason`` fields have been deleted.

``derive_decision_fields`` reads a list of stances and produces the flat
``target_weights``, ``close_reasons``, and ``trim_reasons`` fields that
downstream agents expect.

The stance is *not* a trade instruction. It expresses the strategist's desired
portfolio position and the reasoning behind it. The executor translates that
desire into actual orders.

Consumers:
- ``derive_decision_fields`` (C4) — flattens stances into the canonical output shape
- ``StrategistDecision`` (C7) — embeds the stance list in the decision payload
- The after-callback (C9) — validates stances before persisting
- ``TickerStanceRow`` (C10) — persists each stance to the database

Verb vocabulary (Spec B — Band 3, single canonical form)
---------------------------------------------------------
The ``intent`` field is the canonical action verb and is **required** on every
stance.  Verb-conditional field requirements:

    open   — enter flat → held; broker BUY to ``weight``.
             Required: weight, rationale, horizon, target_price, stop_price.
             Optional: catalyst.

    add    — increase existing position; broker BUY delta to ``weight``.
             Required: weight.
             Optional: reason, horizon, target_price, stop_price, catalyst.

    trim   — reduce existing position (not to zero); broker SELL delta.
             Required: weight, reason.

    close  — full exit; broker SELL all.
             Required: reason.
             Forbidden: weight (use 'trim' for a partial exit).

    hold   — no trade; review fields only.
             Required: reason.
             Forbidden: weight.

    update — no trade; mutate thesis fields only.
             Required: reason + at least one of
             target_price / stop_price / catalyst / horizon.
             Forbidden: weight.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.strategist import get_strategist_config


logger = logging.getLogger(__name__)

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

    ``intent`` is required on every stance; verb-conditional fields are
    described in full in the module docstring above.

    ``extra="forbid"`` ensures that stale callers passing deleted fields
    (``preferred_weight``, ``conviction``, ``close_reason``, ``trim_reason``)
    receive a loud ``ValidationError`` rather than silent truncation.

    Args:
        ticker:       The stock ticker symbol (e.g. ``"AAPL"``).
        intent:       Stance verb — one of open / add / trim / close / hold /
                      update.  Required on every stance.
        weight:       Post-stance portfolio weight in ``[0, 1]``.  Required for
                      open/add/trim; forbidden on close/hold/update.
        reason:       Required for trim / close / hold / update — articulates
                      what has changed since the position was opened.
        rationale:    Required on ``open`` (FROZEN at entry — Invariant 3).
                      Not used on other verbs.
        horizon:      Investment horizon.  Required on ``open``; optional on
                      add/update; not used on trim/close/hold.
        target_price: Price target (fundamental upside anchor).  Required on
                      ``open``; optional on add/update.
        stop_price:   Stop-loss level.  Required on ``open``; optional on
                      add/update.
        catalyst:     Optional short description of the expected near-term
                      catalyst.  Accepted on open/add/update.
    """

    # Forbid extra kwargs — deleted fields (preferred_weight, conviction,
    # close_reason, trim_reason) will raise ValidationError rather than being
    # silently ignored.  This catches stale callers immediately.
    model_config = ConfigDict(extra="forbid")

    ticker: str

    # ── Canonical intent verb (required) ─────────────────────────────────────

    intent: Literal["open", "add", "trim", "close", "hold", "update"] = Field(
        description="Stance verb.  See module docstring for verb-conditional rules.",
    )

    # ── Verb-conditional fields ───────────────────────────────────────────────
    #
    # **Field order matters** (Band 3 fix, 2026-05-24):
    #
    # Pydantic v2 emits JSON Schema ``properties`` in declaration order, and
    # Vertex's constrained decoder honours that order when generating output.
    # Previously the long free-text fields (``rationale`` / ``reason``) sat
    # before the structured commitment fields (``horizon`` / ``target_price``
    # / ``stop_price``).  The model would write a clean stance head, then
    # spiral into a repetition attractor inside ``rationale`` (e.g. 7000+
    # chars of " - - - - "), never reaching the required commitment fields
    # — so the per-stance ``model_validator`` rejected the stance for
    # missing ``horizon`` / ``target_price`` / ``stop_price`` despite
    # ``finish_reason=STOP`` and a syntactically clean JSON wrapper.
    #
    # Putting the cheap, well-bounded fields first means the model commits
    # to them while still on-task, and the prose fields come last where
    # any decoder spiral cannot strand a required commitment.
    # ─────────────────────────────────────────────────────────────────────────

    # Target portfolio weight — required on open/add/trim; forbidden on
    # close/hold/update.  The validator below enforces these constraints.
    weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Post-stance portfolio weight in [0, 1].  Required for "
            "open/add/trim.  Forbidden on close/hold/update."
        ),
    )

    # Open-specific commitment fields (also mutable on add/update).  Emitted
    # BEFORE the long prose fields below so the model commits to them before
    # any rationale-spiral can derail the rest of the stance.
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price:   float | None = None

    # Optional context field accepted on open/add/update.  Kept near the
    # structured fields rather than alongside the prose fields for the same
    # reason — it is short and structured.
    catalyst: str | None = Field(default=None)

    # ── Free-text prose fields (declared last on purpose) ────────────────────
    #
    # ``reason`` and ``rationale`` are the long, unbounded fields where
    # Vertex's constrained decoder is most prone to repetition spirals.
    # Declaring them after the structured fields above means a spiral here
    # can only truncate the stance's own prose — it cannot strand
    # ``horizon`` / ``target_price`` / ``stop_price`` unwritten.
    #
    # max_length intentionally NOT set: Vertex's constrained decoder treats
    # schema-level maxLength as a fill target and pads strings (verbatim
    # repetition, hallucinated padding text) toward the cap.  The prompt
    # tells the model the upper bound in words; we trust the model to
    # honour it.

    # Narrative for hold/trim/close/update — "what has changed since open".
    reason: str | None = Field(default=None)

    # Brief justification — required on ``open``; not used on other verbs.
    rationale: str | None = Field(default=None)

    # ── Verb-conditional validator ────────────────────────────────────────────

    @model_validator(mode="after")
    def _require_intent_fields(self) -> TickerStance:
        """Enforce the verb-conditional field contract from the End-state table.

        Each verb in the Spec B stance vocabulary has a set of required and
        forbidden fields.  This validator fires at schema parse time so a
        malformed LLM output fails early with a descriptive message rather than
        silently producing a partial stance that reaches the executor.

        End-state contract (single canonical form — Band 3):

            open   → require weight (> 0), rationale, horizon,
                     target_price, stop_price.  catalyst optional.
            add    → require weight.  Other fields optional updates.
            trim   → require weight, reason.
            close  → require reason.  weight forbidden.
            hold   → require reason.  weight forbidden.
            update → require reason AND at least one of
                     target_price / stop_price / horizon / catalyst.
                     weight forbidden.

        Raises:
            ValueError: describing the violated rule with suggestions where
                applicable.  Multiple violations are aggregated so the LLM
                sees all missing fields at once on the re-prompt.
        """

        match self.intent:

            case "open":
                # ``open`` seeds a new PositionThesis — all commitment fields
                # are required.  catalyst is optional (useful but not blocking).
                # weight must be > 0 — a zero-weight open is meaningless.
                missing = [
                    name for name, value in (
                        ("weight",       self.weight),
                        ("rationale",    self.rationale),
                        ("horizon",      self.horizon),
                        ("target_price", self.target_price),
                        ("stop_price",   self.stop_price),
                    )
                    if value is None
                ]

                # weight=0.0 passes the None check above but is not a valid open.
                if self.weight is not None and self.weight <= 0.0:
                    missing.append("weight (must be > 0.0)")

                if missing:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='open' but is missing "
                        f"required fields: {missing}.  All of weight (> 0), rationale, "
                        f"horizon, target_price, stop_price are required on open — "
                        f"they seed the PositionThesis row."
                    )

            case "add":
                # ``add`` increases an existing position — only weight is
                # strictly required; thesis fields are optional updates.
                if self.weight is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='add' but weight is "
                        f"missing.  weight is required on add so the executor knows "
                        f"the target size after the buy."
                    )

            case "trim":
                # ``trim`` reduces but does not close — weight anchors the new
                # target size; reason articulates what changed.
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
                        f"needed) or 'hold' (no weight change, reason still required)?"
                    )

            case "close":
                # ``close`` exits fully — no size change, so weight is
                # meaningless and forbidden.  reason documents why.
                #
                # We forbid weight entirely rather than silently accepting 0.0
                # because 0.0 creates ambiguity ("did the LLM mean close or
                # hold-flat?").  See Plan 3 'Out of scope' footnote.
                errors: list[str] = []
                if self.reason is None:
                    errors.append(
                        "reason is required on close to document why the "
                        "position is being exited"
                    )
                if self.weight is not None:
                    errors.append(
                        "weight must not be set on close — use intent='trim' "
                        "for a partial exit"
                    )
                if errors:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='close' but: "
                        + "; ".join(errors)
                    )

            case "hold":
                # ``hold`` is a no-trade review — reason articulates what has
                # changed (or not) since opening.  weight is forbidden.
                errors = []
                if self.reason is None:
                    errors.append(
                        "reason is required on hold to articulate what has "
                        "changed since the position was opened"
                    )
                if self.weight is not None:
                    errors.append(
                        "weight must not be set on hold — a hold carries no "
                        "size change; use 'add' or 'trim' to resize"
                    )
                if errors:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='hold' but: "
                        + "; ".join(errors)
                    )

            case "update":
                # ── Salvage shim: update-without-thesis-fields → hold ─────
                #
                # Empirically (Sep 2025 baseline backtest, ticker=GOOGL then
                # JNJ) Vertex Gemini sometimes selects ``intent='update'``
                # while writing prose like *"Updating target to reflect the
                # new acquisition catalyst"* — yet never populates any of
                # ``target_price`` / ``stop_price`` / ``horizon`` /
                # ``catalyst``.  The shape it emits is structurally
                # identical to a valid ``hold`` (reason present, no
                # commitment fields, no weight), so the executor would do
                # nothing either way.  Three retries of the strict raise
                # path produced three reworded prose responses with the
                # same missing fields — the model believes it is updating.
                #
                # Coerce here rather than raise so a single mislabel does
                # not abort the tick.  A WARN log keeps the behaviour
                # observable: if the salvage rate spikes, the prompt or
                # verb set needs revisiting.  Project rule: silent
                # failures are the recurring bug class — the log is the
                # "loud" part of an otherwise quiet salvage.
                #
                # Only coerce when the emitted shape would itself be a
                # valid ``hold`` (reason present, weight not set).  If
                # the stance is malformed in other ways (no reason, or
                # weight set), fall through to the strict validator so
                # the genuine bug surfaces.
                has_update_field = any(
                    v is not None for v in (
                        self.target_price,
                        self.stop_price,
                        self.catalyst,
                        self.horizon,
                    )
                )
                if (
                    not has_update_field
                    and self.reason is not None
                    and self.weight is None
                ):

                    # Truncate the reason for the log line — full text can be
                    # several sentences and would flood structured-log fields.
                    short_reason = (
                        self.reason[:120] + "..."
                        if len(self.reason) > 120
                        else self.reason
                    )

                    logger.warning(
                        "stance_update_coerced_to_hold ticker=%s reason=%r — "
                        "LLM emitted intent='update' with no thesis fields; "
                        "treating as 'hold' since the executor would do nothing "
                        "either way.  Spike in this rate means the prompt or "
                        "verb set needs revisiting.",
                        self.ticker,
                        short_reason,
                    )

                    # Mutate in place — Pydantic v2 allows ``after`` validators
                    # to return a modified instance.  The downstream pipeline
                    # (derivation, executor) sees intent='hold' from this point on.
                    self.intent = "hold"
                    return self

                # ── Original strict validation (genuine bug paths) ────────
                #
                # Reached when the salvage above did NOT match — i.e. the
                # stance has thesis fields (could be valid update), or is
                # missing reason, or sets weight.  Surface the real bug.

                # ``update`` mutates thesis fields with no trade.  reason is
                # required; at least one field to actually update is required;
                # weight is forbidden (no trade = no sizing change).
                errors = []

                if self.reason is None:
                    errors.append(
                        "reason is required on update to articulate why the "
                        "thesis parameters are changing"
                    )

                # Require at least one of the mutable commitment fields.
                # (Redundant with the salvage gate above when reason is
                # present, but kept so the error fires if reason is None
                # and we fell through.)
                if not has_update_field:
                    errors.append(
                        "at least one of target_price / stop_price / catalyst / "
                        "horizon must be supplied so the update has something to "
                        "mutate — did you mean 'hold' (no thesis fields changing)?"
                    )

                if self.weight is not None:
                    errors.append(
                        "weight must not be set on update — no trade occurs; "
                        "use 'add' or 'trim' to change position size"
                    )

                if errors:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='update' but: "
                        + "; ".join(errors)
                    )

        return self
