"""TickerStance — the strategist's per-ticker decision substrate.

**Four-verb canonical form.**

The strategist emits one ``TickerStance`` per watchlist ticker per tick.
There is no "silence is hold" rule: every ticker gets an explicit verb so
the audit trail records what the agent considered, not just what it acted
on.  ``no_action`` is the explicit "considered, no change" stance.

Single prose field
------------------
Every verb except ``no_action`` carries its prose in ONE field:
``rationale``.  The earlier split between ``rationale`` (for entries) and
``reason`` (for exits / revisions) was redundant — the words are
synonymous and the dual vocabulary tripped the model up at parse time
(see the iter-3 backtest where the model emitted ``reason`` on a ``buy``
stance and the schema rejected it).  One field, one name.

Verb vocabulary
---------------
    buy       — enter a position on a thesis or increase an existing one.
                Required: ticker, intent, weight (0 < w ≤ ``max_delta_per_buy``
                from ``config/risk_gate.json``), rationale.
                ``rationale`` is rewritten onto the row — the agent is on
                the record justifying every entry and every add.

    sell      — reduce or fully close an existing position.
                Required: ticker, intent, rationale (here documenting why
                the position is being trimmed / closed).
                Optional: weight (0 < w ≤ 1.0).  Absent weight ⇒ full
                close; the thesis row is removed and the trade lands in
                the trade log.  The standing thesis prose on the row is
                NOT overwritten — sell is a sizing change, use ``update``
                to revise the view.  The sell ``rationale`` is captured
                in ``last_reviewed_reason`` for the audit trail.

    update    — revise the prose thesis without trading.  Works whether
                or not the agent holds a position; if no thesis row exists
                yet the update seeds one.
                Required: ticker, intent, rationale (the revised thesis).

    no_action — explicit "considered, no change."  No trade, no prose
                revision.  Used when the agent reviewed the ticker and
                chose to hold its current view and (if any) position.
                Required: ticker, intent only — no other fields permitted.

Field surface deliberately narrow: no horizon / target_price / stop_price.
The iter-2 audit found those were hallucinated 80 % of the time and
never consumed downstream — see docs/backtest-audits/baseline-window-
2025-09-iter-2.md, Bug #9.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.risk_gate import get_risk_gate_config

logger = logging.getLogger(__name__)

# Per-buy stance delta cap.  Sourced from ``config/risk_gate.json`` so the
# schema validator, the strategist prompt, and the risk-gate clamp all read
# the same value.  Resolved at module-import time via the lru-cached loader
# — a process restart is required after editing the JSON, matching the
# semantics of every other risk-gate constant.
_MAX_BUY_DELTA: float = get_risk_gate_config().max_delta_per_buy


class TickerStance(BaseModel):
    """One stance per ticker per tick — see module docstring for verb rules.

    ``extra="forbid"`` rejects stale callers passing deleted fields
    (target_price / stop_price / horizon / preferred_weight / conviction
    / close_reason / trim_reason / reason / catalyst) with a loud
    ``ValidationError``.

    Args:
        ticker:    The stock ticker symbol (e.g. ``"AAPL"``).
        intent:    Stance verb — one of buy / sell / update / no_action.
                   See module docstring for verb-conditional field rules.
        weight:    Position-delta weight.  Semantics vary by verb:
                     buy       → required, 0 < w ≤ ``max_delta_per_buy``
                                 (per-trade cap, config-driven).
                     sell      → optional, 0 < w ≤ 1.0 (partial trim delta).
                                 Absent means full close.
                     update    → forbidden (no trade occurs).
                     no_action → forbidden (no trade occurs).
        rationale: The prose for this stance.  Required on buy, sell, and
                   update; forbidden on no_action.  See module docstring
                   for how the prose is consumed by each verb.
    """

    # Forbid extra kwargs — deleted fields (target_price, stop_price,
    # horizon, preferred_weight, conviction, close_reason, trim_reason,
    # the now-dropped ``reason`` and ``catalyst`` fields) will raise
    # ValidationError rather than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    ticker: str

    # ── Canonical intent verb (required) ─────────────────────────────────────

    intent: Literal["buy", "sell", "update", "no_action"] = Field(
        description="Stance verb.  See module docstring.",
    )

    # Weight semantics depend on the verb (validator below enforces):
    #   buy       → required, 0 < w ≤ max_delta_per_buy (config-driven cap)
    #   sell      → optional, 0 < w ≤ 1.0 (delta; absent = full close)
    #   update    → forbidden
    #   no_action → forbidden
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    # ``rationale`` is the single prose field.  Required on buy / sell /
    # update; forbidden on no_action.  See module docstring.
    rationale: str | None = Field(default=None)

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
                # buy needs weight + rationale.
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
                        f"Per-trade cap is sourced from "
                        f"``config/risk_gate.json :: max_delta_per_buy``."
                    )

            case "sell":
                # sell documents why the position is being reduced/closed
                # via the shared rationale field.
                if self.rationale is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='sell' but "
                        f"rationale is missing — document why."
                    )

                # Partial-sell weight must be strictly positive (> 0).
                # Absent weight is valid and means full close.
                if self.weight is not None and self.weight <= 0.0:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: sell weight must be > 0 "
                        f"(or absent for a full close)."
                    )

            case "update":
                # update needs a rationale (the revised thesis prose).
                if self.rationale is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='update' but "
                        f"rationale is missing — update requires prose."
                    )

                # update is prose-only; weight is forbidden (no trade occurs).
                if self.weight is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: update accepts only "
                        f"'rationale'; 'weight' is forbidden on update."
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
                    )
                    if value is not None
                ]
                if forbidden:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: no_action takes only "
                        f"ticker + intent; forbidden fields present: {forbidden}."
                    )

        return self
