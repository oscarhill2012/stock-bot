"""Tunable defaults for the analyst → strategist digest aggregator.

Co-located with `contract.digest` because they're behavioural defaults consumed
only by `build_ticker_evidence`, not runtime-tunable JSON config. Per-key nested
weighting (e.g. `smart_money.n_politicians > 2 ⇒ +x`) is deferred to a future
spec; for now weights are per-analyst-family only. If a future spec needs these
tunable without code changes, promote to `config/digest.json` + a loader.
"""
from __future__ import annotations

DEFAULT_ANALYST_WEIGHTS: dict[str, float] = {
    "technical": 1.0,
    "fundamental": 1.0,
    "sentiment": 1.0,
    "smart_money": 1.0,
}

DIRECTION_DEAD_ZONE: float = 0.15
