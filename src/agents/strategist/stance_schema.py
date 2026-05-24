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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    # Narrative for hold/trim/close/update — "what has changed since open".
    # Capped at ``rationale_max_chars`` because the field carries the same
    # paragraph-sized narrative on every verb that uses it; no operational
    # reason to make it tighter than the open-rationale budget.
    reason: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.rationale_max_chars),
        description=(
            "Required for trim / close / hold / update — articulates "
            "what has changed since the position was opened."
        ),
    )

    # Open-specific commitment fields (also mutable on add/update).
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price:   float | None = None

    # Optional context fields accepted on open/add/update.
    catalyst: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.catalyst_max_chars),
    )

    # Brief justification — required on ``open`` (FROZEN at entry per
    # Invariant 3); not used on other verbs.
    rationale: str | None = Field(
        default=None,
        max_length=_schema_cap(_STANCE.rationale_max_chars),
        description=(
            "Required on open (FROZEN at entry — Invariant 3).  "
            "Not used on add/trim/close/hold/update."
        ),
    )

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
