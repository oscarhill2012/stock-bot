"""TickerStance — the strategist's per-ticker decision substrate.

**Four-verb canonical form.**

The strategist emits one ``TickerStance`` per watchlist ticker per tick.
There is no "silence is hold" rule: every ticker gets an explicit verb so
the audit trail records what the agent considered, not just what it acted
on.  ``no_action`` is the explicit "considered, no change" stance.

Verb vocabulary
---------------
    buy       — enter a position on a thesis or increase an existing one.
                Required: ticker, intent, weight (0 < w ≤ 0.05), rationale.
                Optional: catalyst.
                Refreshes the thesis row's rationale — the agent is on the
                record justifying every entry and every add.

    sell      — reduce or fully close an existing position.
                Required: ticker, intent, reason.
                Optional: weight (0 < w ≤ 1.0).  Absent weight ⇒ full close;
                the thesis row is removed and the trade lands in the trade
                log.

    update    — revise the prose thesis without trading.  Works whether or
                not the agent holds a position; if no thesis row exists yet
                the update seeds one.
                Required: ticker, intent, reason.

    no_action — explicit "considered, no change."  No trade, no prose
                revision.  Used when the agent reviewed the ticker and
                chose to hold its current view and (if any) position.
                Required: ticker, intent.

Field surface deliberately narrow: no horizon / target_price / stop_price.
The iter-2 audit found those were hallucinated 80 % of the time and
never consumed downstream — see docs/backtest-audits/baseline-window-
2025-09-iter-2.md, Bug #9.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

# 5 % buy-delta cap is the schema-level hard ceiling — risk gate may
# clamp tighter.  Defined as a literal so Pydantic accepts it.
_MAX_BUY_DELTA = 0.05


class TickerStance(BaseModel):
    """One stance per ticker per tick — see module docstring for verb rules.

    ``extra="forbid"`` rejects stale callers passing deleted fields
    (target_price / stop_price / horizon / preferred_weight / conviction
    / close_reason / trim_reason) with a loud ``ValidationError``.

    Args:
        ticker:    The stock ticker symbol (e.g. ``"AAPL"``).
        intent:    Stance verb — one of buy / sell / update / no_action.
                   See module docstring for verb-conditional field rules.
        weight:    Position-delta weight.  Semantics vary by verb:
                     buy       → required, 0 < w ≤ 0.05 (per-trade delta cap).
                     sell      → optional, 0 < w ≤ 1.0 (partial trim delta).
                                 Absent means full close.
                     update    → forbidden (no trade occurs).
                     no_action → forbidden (no trade occurs).
        catalyst:  Optional short description of the near-term catalyst.
                   Accepted on buy stances only.
        rationale: Entry / add thesis.  Required on buy; forbidden on the
                   other verbs.
        reason:    Exit or revision rationale.  Required on sell and update;
                   forbidden on buy and no_action.
    """

    # Forbid extra kwargs — deleted fields (target_price, stop_price,
    # horizon, preferred_weight, conviction, close_reason, trim_reason)
    # will raise ValidationError rather than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    ticker: str

    # ── Canonical intent verb (required) ─────────────────────────────────────

    intent: Literal["buy", "sell", "update", "no_action"] = Field(
        description="Stance verb.  See module docstring.",
    )

    # Weight semantics depend on the verb (validator below enforces):
    #   buy       → required, 0 < w ≤ 0.05 (delta-per-trade cap)
    #   sell      → optional, 0 < w ≤ 1.0  (delta; absent = full close)
    #   update    → forbidden
    #   no_action → forbidden
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    catalyst: str | None = Field(default=None)
    rationale: str | None = Field(default=None)
    reason: str | None = Field(default=None)

    # ── Verb-conditional validator ────────────────────────────────────────────

    @model_validator(mode="after")
    def _require_intent_fields(self) -> TickerStance:
        """Enforce verb-conditional field contract.  See module docstring.

        Fires at parse time so a malformed LLM output fails early with a
        descriptive error rather than silently producing a partial stance
        that reaches the executor.

        Raises:
            ValueError: describing the violated rule.  Multiple violations
                are aggregated so the LLM sees all missing fields at once
                on a re-prompt.
        """

        match self.intent:

            case "buy":
                # Collect missing required fields.
                missing = [
                    name for name, value in (
                        ("weight",    self.weight),
                        ("rationale", self.rationale),
                    )
                    if value is None
                ]
                if missing:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='buy' but is "
                        f"missing required fields: {missing}.  buy requires "
                        f"weight (0 < w ≤ {_MAX_BUY_DELTA}) and rationale."
                    )

                # weight must be strictly positive and within the per-trade cap.
                if self.weight is not None and (
                    self.weight <= 0.0 or self.weight > _MAX_BUY_DELTA
                ):
                    raise ValueError(
                        f"Stance for {self.ticker!r}: buy weight {self.weight} "
                        f"is outside the allowed range (0, {_MAX_BUY_DELTA}]. "
                        f"5 % is the per-trade delta cap; the risk gate may "
                        f"clamp tighter."
                    )

                # reason is semantically wrong on a buy — use rationale.
                if self.reason is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: 'reason' is forbidden on "
                        f"buy — use 'rationale' for the entry thesis."
                    )

            case "sell":
                # reason documents why the position is being reduced/closed.
                if self.reason is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='sell' but "
                        f"reason is missing — document why."
                    )

                # Partial-sell weight must be strictly positive (> 0).
                # Absent weight is valid and means full close.
                if self.weight is not None and self.weight <= 0.0:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: sell weight must be > 0 "
                        f"(or absent for a full close)."
                    )

                # rationale is semantically wrong on a sell — use reason.
                if self.rationale is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: 'rationale' is forbidden "
                        f"on sell — use 'reason'."
                    )

                # catalyst is accepted on buy stances only (module docstring).
                # A sell that carries a catalyst field is almost certainly an
                # LLM copy-paste error from a prior buy stance — reject loudly.
                if self.catalyst is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: 'catalyst' is forbidden "
                        f"on sell — catalyst is accepted on buy stances only."
                    )

            case "update":
                # reason articulates what has changed in the prose thesis.
                if self.reason is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='update' but "
                        f"reason is missing — update requires prose."
                    )

                # update is prose-only; weight, rationale, and catalyst are
                # forbidden (no trade occurs, no entry thesis restatement).
                forbidden = [
                    name for name, value in (
                        ("weight",    self.weight),
                        ("rationale", self.rationale),
                        ("catalyst",  self.catalyst),
                    )
                    if value is not None
                ]
                if forbidden:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: update accepts only "
                        f"'reason'; forbidden fields present: {forbidden}."
                    )

            case "no_action":
                # no_action is the explicit "considered, no change" verb.
                # No trade, no prose — every field besides ticker + intent
                # is forbidden.  Reject loudly so an LLM that copy-pastes a
                # rationale or weight onto a no_action stance fails fast.
                forbidden = [
                    name for name, value in (
                        ("weight",    self.weight),
                        ("rationale", self.rationale),
                        ("reason",    self.reason),
                        ("catalyst",  self.catalyst),
                    )
                    if value is not None
                ]
                if forbidden:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: no_action takes only "
                        f"ticker + intent; forbidden fields present: {forbidden}."
                    )

        return self
