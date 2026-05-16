"""SPY buy-and-hold metrics.

Public surface: ``_metrics_from_series`` (used by tests and the reporting
layer) and ``SPYMetrics`` (the return dataclass).  The former public
``spy_metrics`` function was removed in Phase 7 — it had zero callers;
reporting.py computes its own SPY delta directly from the golden cache.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SPYMetrics:
    cumulative_return: float
    annualised_return: float
    sharpe: float
    max_drawdown: float
    calmar: float


def _metrics_from_series(close: pd.Series) -> SPYMetrics:
    """Compute baseline metrics from a daily close series."""
    if len(close) < 2:
        return SPYMetrics(0.0, 0.0, 0.0, 0.0, 0.0)

    returns = close.pct_change().dropna()
    cumulative = (close.iloc[-1] / close.iloc[0]) - 1.0

    n_days = len(close)
    years = max(n_days / 252.0, 1e-9)
    annualised = (1.0 + cumulative) ** (1.0 / years) - 1.0

    std_daily = returns.std(ddof=0)
    sharpe = (returns.mean() / std_daily * np.sqrt(252)) if std_daily > 0 else 0.0

    running_max = close.cummax()
    drawdown = (close - running_max) / running_max
    max_dd = float(drawdown.min())

    calmar = (annualised / abs(max_dd)) if max_dd != 0 else 0.0

    return SPYMetrics(
        cumulative_return=float(cumulative),
        annualised_return=float(annualised),
        sharpe=float(sharpe),
        max_drawdown=float(max_dd),
        calmar=float(calmar),
    )
