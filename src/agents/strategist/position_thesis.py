"""``PositionThesis`` — per-ticker thesis record for the memory backbone.

Implements the ``PositionThesis`` schema from Spec B §"Schema — PositionThesis".
This module is the single authoritative definition; all consumers (executor,
memory writer, context-shim renderer) import from here.

One book of theses
------------------
The thesis book under ``state["user:positions"]`` holds **one row per ticker
the agent has formed a view on** — owned or not.  There is no held/watched
split.  A row is a row.  Whether the agent has a live position attached is
metadata on the row (the entry fields and ``weight``), not a different kind
of row.

Position state is encoded by the entry fields:

- A row whose entry fields are populated (``opened_at``, ``opened_tick_id``,
  ``opened_price``, ``weight``) describes a ticker the agent owns.
- A row whose entry fields are ``None`` describes a ticker the agent holds
  a view on but does not own (yet).
- A full close removes the row entirely — the trade is captured in the
  ``trade_log`` DB row and the rolling ``user:closed_trades_log`` (rendered
  to the strategist as "Recent round-trips").  The agent must re-form a
  view next tick if it wants to re-engage.

Timestamps
----------
All ``datetime`` fields are stored in UTC by convention.  The ADK session
state propagates them as ISO-8601 strings; ``model_validate`` / ``model_dump``
round-trips through JSON are the authorised serialisation path.

Rationale is mutable
--------------------
Rationale evolves freely via the ``update`` verb on any row, and is refreshed
by a ``buy`` stance (whether the buy is a fresh open or an add — see
``_verb_dispatch.py``).  The accountability mechanism is the ``update`` verb
itself: the agent must justify every change of view in prose that lands in
the audit trail.

Schema evolution
----------------
Every new field added to ``PositionThesis`` MUST carry a default value.  The
frozen-V1 fixture at ``tests/fixtures/position_thesis_v1.json`` will fail to
deserialise if a required field is added without one — that test is the gate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PositionThesis(BaseModel):
    """One row of the strategist's thesis book.

    Persisted as a value inside ``state["user:positions"]`` (keyed by
    ticker).  Round-trips through ADK's session state via
    ``model_dump()`` / ``model_validate()`` at the persistence boundary.

    Field lifecycle
    ---------------
    - ``opened_at``, ``opened_tick_id``, ``opened_price``, ``weight``
      together describe the live position.  They are ``None`` until the
      first ``buy`` stance lands; populated thereafter.  A full close
      removes the row outright.
    - ``rationale`` is mutable on every ``buy`` (entry or add) and every
      ``update``.  The agent is accountable for the prose: each change of
      view must be justified in the rationale, which lands in the audit
      trail.
    - ``last_reviewed_at`` / ``last_reviewed_decision`` track the most
      recent tick that touched this row.  ``no_action`` touches them on
      held rows so the audit shows the agent re-examined the position.
    - ``thesis_last_updated_tick`` resets only on ``buy``/``update`` —
      never on ``no_action``, so the staleness counter measures real
      revisions, not passive confirmations.

    Note (iter-3)
    -------------
    ``target_price``, ``stop_price``, and ``horizon`` were removed in
    iter-3.  The schema is now prose-only: entry context is captured
    by ``rationale``; price targets live exclusively in the strategist's
    free-text reasoning, not the schema.  ``catalyst`` was also removed
    (basically duplicative of ``rationale``).
    """

    # Extra fields from stale callers are rejected loudly rather than silently
    # ignored.  This catches any code that still writes target_price / stop_price
    # / horizon and surfaces the regression at construction time.
    model_config = ConfigDict(extra="forbid")

    # ---- Identity -------------------------------------------------------
    ticker: str = Field(..., description="Ticker symbol, e.g. 'AVGO'.")

    # ---- Position state (None when the agent has no live position) -----
    # Populated when a ``buy`` stance lands; cleared on full close (the
    # row is removed outright in that case).  Partial trims update
    # ``weight`` but leave the open record intact.
    opened_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp (UTC) of the tick on which the position was opened.  "
            "None when the agent holds a view but no position."
        ),
    )
    opened_tick_id: str | None = Field(
        default=None,
        description=(
            "Tick identifier captured at open time, for traceability.  "
            "None when the agent holds a view but no position."
        ),
    )
    opened_price: float | None = Field(
        default=None,
        description=(
            "Fill price recorded by the executor at open.  "
            "None when the agent holds a view but no position."
        ),
    )
    weight: float | None = Field(
        default=None,
        description=(
            "Current portfolio weight in [0, 1].  Mutated on every buy/sell "
            "stance.  None when the agent holds a view but no position."
        ),
    )

    # ---- Rationale (mutable) -------------------------------------------
    rationale: str = Field(
        ...,
        description=(
            "The strategist's current reasoning for this ticker.  Mutable: "
            "refreshed on every ``buy`` (entry or add) and every ``update`` "
            "stance.  Every revision is the agent's on-the-record justification "
            "and lands in the audit trail."
        ),
    )

    # ---- Review trail ---------------------------------------------------
    last_reviewed_at: datetime = Field(
        ...,
        description="Timestamp (UTC) of the most recent tick whose stance touched this row.",
    )
    last_reviewed_decision: Literal["buy", "sell", "update", "no_action"] = Field(
        ...,
        description=(
            "Stance verb that produced the most recent review.  Uses the "
            "four-verb vocabulary: buy / sell / update / no_action.  Set to "
            "'buy' on initial entry (the row's lifetime begins with a buy or "
            "update that opens the thesis).  Never 'sell' after a full close — "
            "full close deletes the row rather than updating it."
        ),
    )
    # ---- Staleness tracking ---------------------------------------------
    thesis_last_updated_tick: int = Field(
        default=0,
        description=(
            "Window-relative tick index at which the thesis was last written "
            "or revised; used by context_shim to render staleness.  Set by "
            "the executor on ``buy`` (entry or add) and ``update`` stances.  "
            "``no_action`` and ``sell`` do NOT reset this — the counter "
            "measures real revisions, not passive confirmations or sizing "
            "changes.  Defaults to 0 for backward compatibility with existing "
            "fixtures that pre-date this field."
        ),
    )
