# tests/unit/baselines/test_spy_metrics_removed.py
"""Regression: spy_metrics was removed in Phase 7 as orphaned dead code.

The internal helper ``_metrics_from_series`` remains and is exercised by
``test_spy_metrics.py``.  This test ensures ``spy_metrics`` is not
silently reintroduced without justification.
"""

from __future__ import annotations

import baselines.spy as spy


def test_spy_metrics_symbol_is_gone():
    """Public ``spy_metrics`` must not return — see docs/Phase7."""

    assert not hasattr(spy, "spy_metrics"), (
        "spy_metrics was deliberately removed in Phase 7; "
        "see docs/Phase7-pre-backtest-cleanup/."
    )


def test_metrics_from_series_still_exists():
    """The internal helper that the SPY metrics test imports is retained."""

    assert hasattr(spy, "_metrics_from_series")
