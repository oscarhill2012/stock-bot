"""``PositionThesis`` — per-position thesis record for the memory backbone.

Implements the ``PositionThesis`` schema from Spec B §"Schema — PositionThesis".
This module is the single authoritative definition; all consumers (executor,
memory writer, held-view renderer) import from here.

Timestamps
----------
All ``datetime`` fields are stored in UTC by convention.  The ADK session
state propagates them as ISO-8601 strings; ``model_validate`` / ``model_dump``
round-trips through JSON are the authorised serialisation path.

Immutability contract (Invariant 3) — held positions only
----------------------------------------------------------
For HELD rows: ``opened_at``, ``opened_tick_id``, ``opened_price``, and
``rationale`` are written exactly once, at position-open time (or at the
moment a watched thesis is promoted to held).  This is documented in the
``Field(description=...)`` text — which the LLM sees in JSON-schema form —
but is *not* mechanically enforced by Pydantic (a Pydantic model is mutable
by default).  The executor is responsible for never overwriting these fields
after the initial write.  If the underlying thesis changes, the correct action
is ``sell`` + ``buy``, not a verbal revision of ``rationale``.

For WATCHED rows: ``rationale`` IS mutable.  A watched thesis is an evolving
view on a ticker the bot is monitoring but not yet holding.  Every ``update``
stance on a watched ticker refreshes the rationale.  Invariant 3 attaches
only at the moment the watched row is promoted to held.

Two kinds of row
----------------
Held
    The bot owns a position.  All four entry fields (``opened_at``,
    ``opened_tick_id``, ``opened_price``, ``weight``) are populated.
    Rationale is FROZEN at the promotion/open write.  Discriminator:
    ``is_watched`` property returns ``False``.

Watched
    The bot is tracking a ticker it has not yet bought.  Entry fields are
    all ``None``; ``weight`` is ``None``.  ``rationale`` evolves freely
    with every ``update`` stance.  A ``buy`` stance on a watched ticker
    PROMOTES it to held and FREEZES rationale to the buy stance's rationale
    (the prior watched rationale is discarded).  Discriminator:
    ``is_watched`` property returns ``True``.

The held/watched distinction is encoded structurally by which entry fields
are populated — there is no separate tag field.  The all-or-nothing
invariant is enforced by ``_validate_entry_fields_invariant``.

Schema evolution
----------------
Every new field added to ``PositionThesis`` MUST carry a default value.  The
frozen-V1 fixture at ``tests/fixtures/position_thesis_v1.json`` will fail to
deserialise if a required field is added without one — that test is the gate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PositionThesis(BaseModel):
    """One row of the strategist's thesis book.

    Persisted as a value inside ``state["user:positions"]`` (keyed by
    ticker).  Round-trips through ADK's session state via
    ``model_dump()`` / ``model_validate()`` at the persistence boundary.

    Two kinds of row
    ----------------
    Held (``is_watched == False``)
        Active position.  All entry fields are populated.  ``rationale``
        is FROZEN at open (Invariant 3 — see module docstring).

    Watched (``is_watched == True``)
        Monitored ticker, not yet owned.  Entry fields are ``None``;
        ``rationale`` evolves on every ``update`` stance.  Promoted to
        held by a ``buy`` stance.

    Field lifecycle
    ---------------
    - ``opened_at``, ``opened_tick_id``, ``opened_price`` are written
      once when the position is opened (or watched → held promotion)
      and are immutable thereafter for held rows.  They are ``None``
      for watched rows.
    - ``weight`` is mutated by the executor on every ``buy``/``sell``
      (held rows only).  ``None`` for watched rows.
    - ``catalyst`` is mutable via the ``update`` stance (no trade) for
      held rows; unused (``None``) for watched rows.
    - ``rationale`` is FROZEN at open for held rows (Invariant 3).
      For watched rows, rationale IS mutable — it records the
      strategist's evolving view.  When a watched row is promoted to
      held, the buy stance's rationale REPLACES the watched view.
    - ``last_reviewed_at`` and ``last_reviewed_decision`` track the
      most recent tick that touched this row.
    - ``last_reviewed_reason`` is persisted for the audit trail but is
      NOT rendered into the next tick's prompt (Principle 2).

    Note (iter-3)
    -------------
    ``target_price``, ``stop_price``, and ``horizon`` were removed in
    iter-3.  The schema is now prose-only: entry context is captured
    by ``rationale`` and ``catalyst``; price targets live exclusively
    in the strategist's free-text reasoning, not the schema.
    """

    # Extra fields from stale callers are rejected loudly rather than silently
    # ignored.  This catches any code that still writes target_price / stop_price
    # / horizon and surfaces the regression at construction time.
    model_config = ConfigDict(extra="forbid")

    # ---- Identity -------------------------------------------------------
    ticker: str = Field(..., description="Ticker symbol, e.g. 'AVGO'.")

    # ---- Entry record (immutable after open for held — Invariant 3) -----
    # The four entry fields together discriminate held from watched rows:
    # all four populated ⇒ held; all four None ⇒ watched.  The validator
    # below enforces the all-or-nothing rule.  Callers should prefer the
    # ``is_watched`` property over inspecting any individual field.
    opened_at: datetime | None = Field(
        default=None,
        description=(
            "Timestamp (UTC) of the tick on which the position was opened.  "
            "Matches the convention for ``state['as_of']`` in "
            "``docs/contract-invariants.md`` §A.  "
            "IMMUTABLE after open (held rows only).  "
            "None for watched rows."
        ),
    )
    opened_tick_id: str | None = Field(
        default=None,
        description=(
            "Tick identifier captured at open time, for traceability.  "
            "IMMUTABLE after open (held rows only).  "
            "None for watched rows."
        ),
    )
    opened_price: float | None = Field(
        default=None,
        description=(
            "Fill price recorded by the executor at open.  "
            "IMMUTABLE after open (held rows only).  "
            "None for watched rows."
        ),
    )

    # ---- Current sizing (held only — None for watched) ------------------
    weight: float | None = Field(
        default=None,
        description=(
            "Current portfolio weight in [0, 1].  "
            "Populated and mutated on every buy/sell for held rows.  "
            "None for watched rows (no position, no weight)."
        ),
    )

    # ---- Commitments (mutable via 'update' stance for held) -------------
    catalyst: str | None = Field(
        None,
        description="Free-form text describing the event that would confirm the thesis.",
    )

    # ---- Rationale ------------------------------------------------------
    # For held: FROZEN at open (Invariant 3).
    # For watched: mutable — updated with every 'update' stance.
    rationale: str = Field(
        ...,
        description=(
            "The strategist's reasoning for this ticker.  "
            "For held rows: FROZEN at position-open time — Invariant 3 applies; "
            "the executor must never overwrite this field after the initial open "
            "write.  If the underlying thesis changes, the right action is "
            "sell + buy.  "
            "For watched rows: MUTABLE — evolves with every 'update' stance, "
            "capturing the latest view on a ticker not yet owned.  Discarded "
            "and replaced by the buy-stance rationale at promotion time."
        ),
    )

    # ---- Review trail ---------------------------------------------------
    last_reviewed_at: datetime = Field(
        ...,
        description="Timestamp (UTC) of the most recent tick whose stance touched this row.",
    )
    last_reviewed_decision: Literal["buy", "sell", "update"] = Field(
        ...,
        description=(
            "Stance verb that produced the most recent review, using the "
            "iter-3 three-verb vocabulary.  Set to 'buy' on initial entry "
            "(the row's lifetime begins with the buy stance that opened the "
            "position, which counts as the first review).  Never 'sell' after "
            "the position is closed — close deletes the row rather than "
            "updating it.  For watched rows: 'update' on every revision; "
            "'buy' at the moment of promotion."
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

    # ---- Staleness tracking ---------------------------------------------
    thesis_last_updated_tick: int = Field(
        default=0,
        description=(
            "Window-relative tick index at which the thesis was last written "
            "or revised; used by context_shim to render staleness.  Set by "
            "the executor whenever a ``buy`` (entry or add) or ``update`` stance "
            "is applied.  Defaults to 0 for backward compatibility with "
            "existing fixtures that pre-date this field."
        ),
    )

    # ---- Held vs watched discriminator ----------------------------------
    @property
    def is_watched(self) -> bool:
        """Return True when this row represents a watched (non-held) thesis.

        A watched row has no open record — all four entry fields are None.
        Use this property at call sites instead of inspecting any single
        entry field, so the discriminator can be tightened in one place if
        the invariant ever changes.
        """
        return self.opened_at is None

    # ---- All-or-nothing entry-field invariant ---------------------------
    @model_validator(mode="after")
    def _validate_entry_fields_invariant(self) -> PositionThesis:
        """Enforce that entry fields are either all populated or all None.

        A held row has all four entry fields set; a watched row has none.
        Partial states ("opened_at set but opened_price None") are
        meaningless and indicate a caller bug.

        Returns:
            The validated instance (Pydantic model_validator convention).

        Raises:
            ValueError: When the four entry fields are in a mixed state.
        """
        entry_field_names = ("opened_at", "opened_tick_id", "opened_price", "weight")
        entry_field_values = (self.opened_at, self.opened_tick_id, self.opened_price, self.weight)

        present = [name for name, val in zip(entry_field_names, entry_field_values, strict=True) if val is not None]
        absent  = [name for name, val in zip(entry_field_names, entry_field_values, strict=True) if val is None]

        # All four set ⇒ held; all four None ⇒ watched.  Anything else is a bug.
        if present and absent:
            raise ValueError(
                f"PositionThesis entry fields must be all-or-nothing.  "
                f"Populated: {present}.  None: {absent}.  "
                f"Either supply every entry field (held row) or none of them (watched row)."
            )

        return self
