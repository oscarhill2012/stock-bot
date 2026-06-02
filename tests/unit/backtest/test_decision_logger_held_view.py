"""Test that decision_logger reads user:positions for held_view_at_decision.

Audit finding A-014: the executor's in-tick BUY→SELL bridge must live under
``temp:executor_positions_bridge`` so external readers cannot accidentally
consume it.  ``decision_logger`` must read ``user:positions`` (the persistent
cross-tick thesis-book), NOT the bare ``state["positions"]`` key (which was
the bridge) or ``temp:executor_positions_bridge``.

Running this test against unmodified source code should FAIL because the old
code reads ``state.get("positions")`` — which in this fixture is seeded with
the "bridge-leak-bare" divergent value — and the assertion expects "real"
(from ``user:positions``).
"""
from __future__ import annotations

import json
from pathlib import Path


def _make_held_view_state() -> dict:
    """Build a minimal state dict with DIVERGENT values for held-view sources.

    Intentionally seeds three keys for AAPL's held-view with different
    ``rationale`` strings so the test can assert which source the logger used:

    - ``state["user:positions"]``                  → rationale = "real"
    - ``state["temp:executor_positions_bridge"]``  → rationale = "bridge-leak"
    - ``state["positions"]``                       → rationale = "bridge-leak-bare"

    The old code reads ``state.get("positions")`` → produces "bridge-leak-bare".
    The corrected code reads ``state.get("user:positions")`` → produces "real".

    Returns
    -------
    dict
        A minimal but structurally valid state dict for a single filled BUY
        execution on AAPL.
    """
    return {
        "as_of":      "2023-03-13T09:30:00-04:00",
        "tick_phase": "open",
        "tick_id":    "tick-1",

        "executions": [
            {
                "order": {
                    "ticker":   "AAPL",
                    "action":   "BUY",
                    "quantity": 50,
                },
                "status":           "filled",
                "actual_price":     150.10,
                "actual_quantity":  50,
                "broker_order_id":  "b1",
            },
        ],

        # Minimal evidence object so _build_snapshot does not blow up on the
        # evidence lookup path.
        "temp:ticker_evidence_objects": [
            {
                "ticker":      "AAPL",
                "tick_id":     "tick-1",
                "per_analyst": {"technical": {"lean": "bullish"}},
                "aggregate":   {
                    "lean": "bullish", "magnitude": 0.6, "confidence": 0.7,
                    "disagreement": 0.2, "summary": "2/3 bullish",
                },
                "weights": {"technical": 0.4, "fundamental": 0.3, "news": 0.3},
            },
        ],

        "strategist_decision": {
            "stances": [
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.05,
                    "rationale": "Bullish technical setup.",
                    "catalyst":  None,
                },
            ],
            "sell_reasons":   {},
            "update_reasons": {},
            "reasoning":      "Opening AAPL on technical momentum.",
            "thesis":         "Momentum play on AAPL.",
            "decision_tag":   "open_aapl",
            "confidence":     0.70,
        },

        # ── Three divergent sources for the held-view lookup ────────────────
        # The test uses AAPL so we can assert which dict was consulted.

        # Correct source — persistent cross-tick thesis-book (A-014 target).
        "user:positions": {
            "AAPL": {"rationale": "real"},
        },

        # Executor's in-tick bridge (temp: namespace, executor-internal).
        # After the rename, this should NOT be read by decision_logger.
        "temp:executor_positions_bridge": {
            "AAPL": {"rationale": "bridge-leak"},
        },

        # Old bare-key bridge — seeded so the ORIGINAL code produces a
        # concrete wrong value ("bridge-leak-bare"), making the FAIL
        # unambiguous rather than a None vs "real" comparison.
        "positions": {
            "AAPL": {"rationale": "bridge-leak-bare"},
        },

        "clamps": [],
    }


def test_held_view_reads_user_positions_not_bridge(tmp_path: Path) -> None:
    """decision_logger must read user:positions for held_view_at_decision.

    Guards audit finding A-014.  Seeds ``state["positions"]`` (old bare
    bridge key) with the value "bridge-leak-bare" and ``state["user:positions"]``
    with "real".  After the fix, the written snapshot must contain "real".

    Before the fix, ``state.get("positions")`` returns "bridge-leak-bare" and
    the assertion fails on a concrete wrong value — not a silent None.
    """
    from backtest.decision_logger import DecisionLogger

    logger = DecisionLogger(output_dir=tmp_path, window_key="x")
    logger.on_executions(_make_held_view_state())

    # Exactly one filled BUY → exactly one JSON file.
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1, f"expected 1 snapshot, got {[f.name for f in files]}"

    snapshot = json.loads(files[0].read_text())
    held = snapshot["strategist_view"]["held_view_at_decision"]

    # The persistent thesis-book value must be used, NOT the bridge value.
    assert held is not None, (
        "held_view_at_decision was None — user:positions was not read "
        "(or user:positions was absent from state)"
    )
    assert held["rationale"] == "real", (
        f"held_view_at_decision came from wrong source: rationale={held['rationale']!r}. "
        "Expected 'real' (user:positions); "
        "'bridge-leak-bare' means old bare-key was read; "
        "'bridge-leak' means temp: bridge was read."
    )
