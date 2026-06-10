"""Shared test helpers — assertions, factories, and fixtures.

Not a production package.  Tests-only.
"""
from tests._helpers.degradation import assert_no_silent_degradation
from tests._helpers.tick_state import make_tick_state

__all__ = ["assert_no_silent_degradation", "make_tick_state"]
