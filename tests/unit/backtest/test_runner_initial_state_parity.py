# tests/unit/backtest/test_runner_initial_state_parity.py
"""Guard against drift between live and backtest initial-state seeding.

ADK's instruction-variable resolver raises ``KeyError`` if any seeded
template variable is absent from the session state.  ``orchestrator.tick``
is the canonical builder for live runs; the backtest ``Runner`` must
mirror its key set so any agent that reads a state variable works
identically under replay.

Note on ``watchlist``:
    The backtest runner seeds both ``tickers`` and ``watchlist`` (the latter
    is the pre-flight-filtered list written into the manifest).  The live
    ``_build_initial_state`` only seeds ``tickers`` — ``watchlist`` is not
    a live-tick ADK template variable.  ``REQUIRED_KEYS`` therefore covers
    the intersection: keys that both sides must always seed.  The
    runner-only ``watchlist`` key is not included because its absence from
    the live builder is intentional, not drift.
"""

from __future__ import annotations

import inspect
import re

from backtest import runner as bt_runner
from orchestrator import tick as live_tick

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_seeded_keys(source: str) -> set[str]:
    """Return literal string keys assigned into a state-like mapping.

    This is a structural shortcut: we scan the function source for
    ``state["<key>"] = ...`` and ``"<key>":`` mapping literals.  The
    intent is *not* perfect parsing; it is to detect divergence early,
    before a live or backtest run crashes with a cryptic ``KeyError``.

    Parameters
    ----------
    source : str
        Python source of the module or function to scan.

    Returns
    -------
    set[str]
        All identifier-like keys discovered.
    """

    keys: set[str] = set()

    for pattern in (
        r'state\[\s*"([a-zA-Z_][a-zA-Z0-9_]*)"\s*\]',
        r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:',
    ):
        keys.update(re.findall(pattern, source))

    return keys


# ---------------------------------------------------------------------------
# The canonical key set that both builders must always include.
#
# ``watchlist`` is intentionally excluded — the live builder seeds only
# ``tickers``; the runner seeds both.  Tracking ``watchlist`` here would
# produce a permanent false-failure for the live side.
# ---------------------------------------------------------------------------

REQUIRED_KEYS: set[str] = {
    "tickers",
    "portfolio",
    "positions",
    "memory_buffer",
    "day_digest",
    "thesis",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_live_tick_seeds_required_keys():
    """Sanity: the live state builder seeds every key in REQUIRED_KEYS."""

    src = inspect.getsource(live_tick)
    seeded = _extract_seeded_keys(src)
    missing = REQUIRED_KEYS - seeded

    assert not missing, (
        f"orchestrator/tick.py is missing initial-state keys: {missing}"
    )


def test_runner_seeds_required_keys():
    """Sanity: the backtest runner seeds every key in REQUIRED_KEYS."""

    src = inspect.getsource(bt_runner)
    seeded = _extract_seeded_keys(src)
    missing = REQUIRED_KEYS - seeded

    assert not missing, (
        f"src/backtest/runner.py is missing initial-state keys: {missing}"
    )


def test_runner_and_live_initial_state_key_sets_match():
    """The two state builders must agree on the REQUIRED_KEYS set.

    If you add a new state variable in the live tick, replicate it in the
    backtest runner (and vice versa), then add it to ``REQUIRED_KEYS``
    above.  This test is the single guard that prevents silent drift.
    """

    live_keys = _extract_seeded_keys(inspect.getsource(live_tick)) & REQUIRED_KEYS
    bt_keys   = _extract_seeded_keys(inspect.getsource(bt_runner)) & REQUIRED_KEYS

    assert live_keys == bt_keys, (
        f"State-seeding drift detected.  "
        f"live - runner = {live_keys - bt_keys}, "
        f"runner - live = {bt_keys - live_keys}"
    )
