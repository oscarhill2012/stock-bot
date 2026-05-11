"""Lifecycle action derivation — what is the strategist actually doing?

The strategist emits a `preferred_weight` per ticker. The lifecycle action
falls out of comparing it to the `current_weight`:

- current ≤ ε ∧ preferred > ε       → "open"
- current > ε ∧ preferred ≤ ε       → "close"
- both > ε ∧ preferred + δ < current → "trim"
- both > ε ∧ preferred > current + δ → "add"
- otherwise                          → "hold"

The thresholds prevent micro-adjustments from triggering full-on lifecycle
events. ε guards "is the position effectively flat?"; δ guards "is the size
change meaningful?".
"""
from __future__ import annotations

from typing import Literal

# Minimum weight to treat a position as "effectively held".
# Positions at or below this threshold are considered flat.
OPEN_EPSILON: float = 0.005

# Minimum absolute weight change to treat a resize as meaningful.
# Deltas at or below this threshold result in "hold" rather than "trim"/"add".
SIZE_CHANGE_EPSILON: float = 0.02

# Union of all possible lifecycle actions the strategist can emit per ticker.
LifecycleAction = Literal["open", "close", "trim", "add", "hold"]


def derive_lifecycle_action(
    current_weight: float, preferred_weight: float
) -> LifecycleAction:
    """Derive the lifecycle action for a ticker based on current vs preferred portfolio weight.

    Compares the existing holding size (`current_weight`) with the strategist's
    desired holding size (`preferred_weight`) and classifies the required action
    into one of five buckets: open, close, trim, add, or hold.

    The order of checks matters:
    1. open/close are checked first — they involve a transition across the
       flat-position boundary (OPEN_EPSILON), and are the most significant events.
    2. trim/add are checked next — only meaningful if *both* sides are above
       OPEN_EPSILON (i.e. both are live positions).
    3. hold is the fallback — covers the case where both are flat, or where the
       delta is too small to act on.

    Parameters
    ----------
    current_weight:
        The ticker's current portfolio weight as a fraction [0.0, 1.0].
        Sourced from ``Portfolio.current_weights()``.
    preferred_weight:
        The strategist's desired portfolio weight as a fraction [0.0, 1.0].
        Sourced from the LLM-emitted ``TickerStance.preferred_weight``.

    Returns
    -------
    LifecycleAction
        One of ``"open"``, ``"close"``, ``"trim"``, ``"add"``, or ``"hold"``.
    """
    # Classify each side as "effectively held" (strictly above OPEN_EPSILON).
    # At exactly OPEN_EPSILON the position is treated as flat — avoids
    # spurious open/close on dust positions right at the boundary.
    held = current_weight > OPEN_EPSILON
    wants_held = preferred_weight > OPEN_EPSILON

    # Transition from flat → live: new position opening.
    if not held and wants_held:
        return "open"

    # Transition from live → flat: full exit requested.
    if held and not wants_held:
        return "close"

    # Both sides are live — assess whether a meaningful resize is warranted.
    if held and wants_held:
        # Preferred is significantly below current — reduce position size.
        if preferred_weight + SIZE_CHANGE_EPSILON < current_weight:
            return "trim"

        # Preferred is significantly above current — increase position size.
        if preferred_weight > current_weight + SIZE_CHANGE_EPSILON:
            return "add"

    # Covers: both flat, or live-to-live change below SIZE_CHANGE_EPSILON.
    return "hold"
