"""``PositionThesis`` — per-position thesis record for the memory backbone.

Implements the ``PositionThesis`` schema from Spec B §"Schema — PositionThesis".
This module is the single authoritative definition; all consumers (executor,
memory writer, held-view renderer) import from here.

Timestamps
----------
All ``datetime`` fields are stored in UTC by convention.  The ADK session
state propagates them as ISO-8601 strings; ``model_validate`` / ``model_dump``
round-trips through JSON are the authorised serialisation path.

Immutability contract (Invariant 3)
-------------------------------------
``opened_at``, ``opened_tick_id``, ``opened_price``, and ``rationale`` are
written exactly once, at position-open time.  This is documented in the
``Field(description=...)`` text — which the LLM sees in JSON-schema form —
but is *not* mechanically enforced by Pydantic (a Pydantic model is mutable
by default).  The executor is responsible for never overwriting these fields
after the initial write.  If the underlying thesis changes, the correct action
is ``close`` + ``open``, not a verbal revision of ``rationale``.

Schema evolution
----------------
Every new field added to ``PositionThesis`` MUST carry a default value.  The
frozen-V1 fixture at ``tests/fixtures/position_thesis_v1.json`` will fail to
deserialise if a required field is added without one — that test is the gate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PositionThesis(BaseModel):
    """One row of the strategist's thesis book.

    Persisted as a value inside ``state["user:positions"]`` (keyed by
    ticker).  Round-trips through ADK's session state via
    ``model_dump()`` / ``model_validate()`` at the persistence boundary.

    Field lifecycle
    ---------------
    - ``opened_at``, ``opened_tick_id``, ``opened_price`` are written
      once when the position is opened and are immutable thereafter
      (Invariant 3 — see module docstring).
    - ``weight`` is mutated by the executor on every ``add``/``trim``.
    - ``target_price``, ``stop_price``, ``catalyst``, ``horizon`` are
      mutable via the ``update`` stance (no trade) or any other
      stance that supplies them.
    - ``rationale`` is FROZEN at open.  It captures the entry
      commitment.  If the thesis genuinely changes, the right action
      is ``close`` then ``open`` — not a verbal revision.
    - ``last_reviewed_at`` and ``last_reviewed_decision`` track the
      most recent tick that touched this row.
    - ``last_reviewed_reason`` is persisted for the audit trail but is
      NOT rendered into the next tick's prompt (Principle 2).
    """

    # ---- Identity -------------------------------------------------------
    ticker: str = Field(..., description="Ticker symbol, e.g. 'AVGO'.")

    # ---- Entry record (immutable after open — Invariant 3) --------------
    opened_at: datetime = Field(
        ...,
        description=(
            "Timestamp (UTC) of the tick on which the position was opened.  "
            "Matches the convention for ``state['as_of']`` in "
            "``docs/contract-invariants.md`` §A.  "
            "IMMUTABLE after open."
        ),
    )
    opened_tick_id: str = Field(
        ...,
        description=(
            "Tick identifier captured at open time, for traceability.  "
            "IMMUTABLE after open."
        ),
    )
    opened_price: float = Field(
        ...,
        description=(
            "Fill price recorded by the executor at open.  "
            "IMMUTABLE after open."
        ),
    )

    # ---- Current sizing (mutated by add/trim) ---------------------------
    weight: float = Field(
        ...,
        description="Current portfolio weight in [0, 1].",
    )

    # ---- Commitments (mutable via 'update' stance) ----------------------
    target_price: float | None = Field(
        None,
        description="Optional price level at which the thesis would be confirmed.",
    )
    stop_price: float | None = Field(
        None,
        description="Optional price level below which the thesis is invalidated.",
    )
    catalyst: str | None = Field(
        None,
        description="Free-form text describing the event that would confirm the thesis.",
    )
    horizon: Literal["intraday", "swing", "long_term"] = Field(
        ...,
        description="Time horizon over which the thesis is expected to play out.",
    )

    # ---- Entry rationale (FROZEN at open — Invariant 3) -----------------
    rationale: str = Field(
        ...,
        description=(
            "The strategist's reasoning at the moment of opening the position. "
            "IMMUTABLE for the lifetime of the position — if the underlying "
            "thesis changes, the right action is close + reopen.  "
            "Invariant 3: executor must never overwrite this field after the "
            "initial open write."
        ),
    )

    # ---- Review trail ---------------------------------------------------
    last_reviewed_at: datetime = Field(
        ...,
        description="Timestamp (UTC) of the most recent tick whose stance touched this row.",
    )
    last_reviewed_decision: Literal["open", "add", "trim", "hold", "update"] = Field(
        ...,
        description=(
            "Stance verb that produced the most recent review.  Set to "
            "'open' on initial entry (the row's lifetime begins with the "
            "open stance, which counts as the first review).  Never "
            "'close' — close deletes the row."
        ),
    )
    last_reviewed_reason: str = Field(
        ...,
        description=(
            "The strategist's 'what's changed since opening' articulation on the "
            "most recent review.  Persisted to the audit trail; NOT rendered "
            "back into the next tick's prompt."
        ),
    )
