"""Portfolio + Position dataclasses. No I/O — see broker.protocol."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Position(BaseModel):
    """A single open holding in the portfolio."""

    quantity: float       # number of shares held
    avg_cost: float       # volume-weighted average purchase price
    last_price: float     # most recent market price (updated by broker on each tick)

    @property
    def market_value(self) -> float:
        """Current market value of this position."""
        return self.quantity * self.last_price


class Portfolio(BaseModel):
    """Snapshot of the bot's full portfolio at a point in time."""

    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)

    @property
    def total_value(self) -> float:
        """Cash plus the market value of all open positions."""
        return self.cash + sum(p.market_value for p in self.positions.values())

    def current_weights(self) -> dict[str, float]:
        """Return each ticker's fraction of total portfolio value."""
        total = self.total_value
        if total == 0:
            return {}
        return {t: p.market_value / total for t, p in self.positions.items()}

    @classmethod
    def from_state_value(cls, value: Portfolio | dict | None) -> Portfolio:
        """Coerce a session-state value into a Portfolio — the canonical door.

        The single sanctioned way to read ``state["portfolio"]`` across the
        codebase.  Raises on missing or malformed input rather than
        silently producing an empty portfolio — silent empties were the
        source of the "tick T+1 strategist sees no holdings" class of
        bugs catalogued in audit finding A-014 / A-071.

        Args:
            value: Either a live ``Portfolio`` instance, a
                ``Portfolio.model_dump(mode="json")`` dict (the
                cross-tick storage shape), or ``None``.

        Returns:
            A ``Portfolio`` instance.

        Raises:
            ValueError: If ``value`` is ``None`` or a malformed dict.
            TypeError:  If ``value`` is any other type.
        """
        # Pass-through for already-validated instances — the hot path
        # inside a single tick where the dict has been coerced once and
        # stashed back as the object.
        if isinstance(value, cls):
            return value

        # Missing portfolio is a contract violation, not a cold-start
        # fall-back.  Cold start must seed state["portfolio"] explicitly
        # via Portfolio(cash=starting_capital).model_dump(mode="json").
        if value is None:
            raise ValueError(
                "state['portfolio'] missing — every tick must seed it at "
                "Phase 2 (live: orchestrator/tick.py; backtest: driver.py)."
            )

        if isinstance(value, dict):
            try:
                return cls.model_validate(value)
            except Exception as exc:  # noqa: BLE001 — re-raised below with context
                raise ValueError(
                    f"state['portfolio'] malformed: {exc}"
                ) from exc

        raise TypeError(
            f"state['portfolio'] unexpected type: {type(value).__name__}"
        )
