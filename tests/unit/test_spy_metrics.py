# tests/unit/test_spy_metrics.py
"""SPY baseline metrics from a hand-crafted price series."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from baselines.spy import SPYMetrics, _metrics_from_series


def test_metrics_from_flat_series_zero_return():
    s = pd.Series([100.0] * 252)
    m = _metrics_from_series(s)
    assert m.cumulative_return == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0, abs=1e-9)


def test_metrics_from_monotonic_series_positive_return():
    s = pd.Series([100.0 + i for i in range(252)])  # 1y of daily +1
    m = _metrics_from_series(s)
    assert m.cumulative_return == pytest.approx((100 + 251) / 100 - 1)
    assert m.max_drawdown == pytest.approx(0.0, abs=1e-9)
    assert m.sharpe > 0  # positive trend


def test_metrics_from_drawdown_series():
    # rises to 200 then drops to 50
    s = pd.Series([100, 150, 200, 175, 100, 50])
    m = _metrics_from_series(s)
    assert m.max_drawdown == pytest.approx(-0.75)  # 200 → 50
    assert m.cumulative_return == pytest.approx(-0.5)  # 100 → 50
