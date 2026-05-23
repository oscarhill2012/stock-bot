"""Tests that DecisionLogger writes one JSON file per executed Fill.

Exercises the snapshot builder against the *real* state shape produced by the
live pipeline (stances list, ``temp:ticker_evidence_objects`` list, ``positions``
dict).  An earlier version of these tests fed the logger the wrong key names —
``ticker_stances`` instead of ``stances``, ``evidence_view`` instead of
``temp:ticker_evidence_objects``, ``held_view`` instead of ``positions`` — which
matched the logger's broken lookups and let four bugs sit undetected until a
production backtest produced 108 snapshots all carrying empty
``strategist_decision`` / ``strategist_view`` payloads.
"""
from __future__ import annotations

import json
from pathlib import Path


def _make_state() -> dict:
    """Construct a minimal-but-realistic state dict mirroring what the live pipeline writes at fill time.

    Includes:
    - Two filled executions (one BUY, one SELL).
    - A ``strategist_decision`` carrying a ``stances`` list, plus the four
      decision-level fields (``reasoning``, ``thesis``, ``decision_tag``,
      ``confidence``) the snapshot now surfaces.
    - ``temp:ticker_evidence_objects`` — the list of per-ticker TickerEvidence
      dumps the strategist's context shim writes.
    - ``positions`` — the per-ticker PositionThesis dump book.  Populated for
      the held ticker (SIVB); absent for the flat-and-being-opened one (AAPL).
    """

    return {
        "as_of":      "2023-03-13T09:30:00-04:00",
        "tick_phase": "open",
        "tick_id":    "tick-1",

        "executions": [
            {
                "order": {
                    "ticker":   "SIVB",
                    "action":   "SELL",
                    "quantity": 120,
                },
                "status":           "filled",
                "actual_price":     42.31,
                "actual_quantity":  120,
                "broker_order_id":  "b1",
            },
            {
                "order": {
                    "ticker":   "AAPL",
                    "action":   "BUY",
                    "quantity": 50,
                },
                "status":           "filled",
                "actual_price":     150.10,
                "actual_quantity":  50,
                "broker_order_id":  "b2",
            },
        ],

        # Real key: list-of-dicts, one per watchlist ticker, each carrying a
        # ``ticker`` field used as the lookup index.
        "temp:ticker_evidence_objects": [
            {
                "ticker":      "SIVB",
                "tick_id":     "tick-1",
                "per_analyst": {"technical": {"lean": "bearish"}},
                "aggregate":   {
                    "lean": "bearish", "magnitude": 0.8, "confidence": 0.9,
                    "disagreement": 0.1, "summary": "3/4 bearish",
                },
                "weights":     {"technical": 0.4, "fundamental": 0.3, "news": 0.3},
            },
            {
                "ticker":      "AAPL",
                "tick_id":     "tick-1",
                "per_analyst": {"technical": {"lean": "bullish"}},
                "aggregate":   {
                    "lean": "bullish", "magnitude": 0.6, "confidence": 0.7,
                    "disagreement": 0.2, "summary": "2/3 bullish",
                },
                "weights":     {"technical": 0.4, "fundamental": 0.3, "news": 0.3},
            },
        ],

        # Real shape: ``stances`` is a list, not a dict.  Each entry carries
        # ``ticker`` plus the per-ticker fields the snapshot surfaces.
        "strategist_decision": {
            "stances": [
                {
                    "ticker":           "SIVB",
                    "preferred_weight": 0.0,
                    "conviction":       0.85,
                    "rationale":        "Thesis broken — closing the position.",
                    "horizon":          None,
                    "target_price":     None,
                    "stop_price":       None,
                    "catalyst":         None,
                    "close_reason":     "Thesis broken",
                    "trim_reason":      None,
                },
                {
                    "ticker":           "AAPL",
                    "preferred_weight": 0.05,
                    "conviction":       0.7,
                    "rationale":        "Opening on bullish technical setup.",
                    "horizon":          "swing",
                    "target_price":     160.0,
                    "stop_price":       145.0,
                    "catalyst":         "Earnings beat expected next week",
                    "close_reason":     None,
                    "trim_reason":      None,
                },
            ],
            "close_reasons":  {"SIVB": "Thesis broken"},
            "trim_reasons":   {},
            "reasoning":      "Rotating out of regional banks into mega-cap tech on the back of the SIVB blowup.",
            "thesis": "Regional bank stress is the dominant risk; rotate to balance-sheet-strong mega-caps.",
            "decision_tag":   "rotate_to_megacap",
            "confidence":     0.78,
        },

        # The held-position book.  SIVB is currently held; AAPL is flat.
        "positions": {
            "SIVB": {
                "ticker":          "SIVB",
                "opened_at":       "2023-03-01T14:30:00+00:00",
                "opened_price":    268.5,
                "opened_tag":      "regional_bank_long",
                "rationale":       "Above-average net interest margin and deposit growth.",
                "horizon":         "swing",
                "target_price":    320.0,
                "stop_price":      240.0,
                "catalyst":        "Q1 earnings",
                "last_reviewed_at":"2023-03-13T09:30:00+00:00",
                "last_review_note":"",
                "opened_tick_id":  "tick-prior",
            },
        },

        "clamps": [],
    }


def test_logs_one_file_per_filled_execution_with_populated_content(tmp_path: Path) -> None:
    """Two filled executions produce two snapshots with populated strategist content.

    The bug this test guards against: prior to the logger fix, every snapshot
    landed on disk with ``strategist_decision = {stance: {}, close_reason: "",
    reasoning_excerpt: ""}`` and ``strategist_view = {ticker_evidence: {},
    held_view_at_decision: null}`` because the lookups used keys that don't
    exist in real state.  We assert *populated content*, not just key presence.
    """
    from backtest.decision_logger import DecisionLogger

    logger = DecisionLogger(output_dir=tmp_path, window_key="svb-stress-2023-03")
    logger.on_executions(_make_state())

    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2
    assert any("SIVB__sell" in f for f in files)
    assert any("AAPL__buy"  in f for f in files)

    # ── SIVB SELL snapshot ────────────────────────────────────────────────────
    sivb_path = next(tmp_path / f for f in files if "SIVB__sell" in f)
    sivb      = json.loads(sivb_path.read_text())

    # Top-level shape still intact.
    for key in (
        "decision_id", "tick", "ticker", "side", "execution",
        "analyst_inputs", "analyst_outputs", "strategist_view",
        "strategist_decision", "risk_gate", "forward_returns",
    ):
        assert key in sivb, f"missing key: {key}"

    # Strategist decision section is no longer empty.
    sd = sivb["strategist_decision"]
    assert sd["stance"]["ticker"]           == "SIVB"
    assert sd["stance"]["preferred_weight"] == 0.0
    assert sd["stance"]["close_reason"]     == "Thesis broken"
    assert sd["close_reason"]               == "Thesis broken"
    assert sd["reasoning"].startswith("Rotating out of regional banks")
    assert sd["thesis"].startswith("Regional bank stress")
    assert sd["decision_tag"]               == "rotate_to_megacap"
    assert sd["confidence"]                 == 0.78

    # Strategist view section is populated.
    sv = sivb["strategist_view"]
    assert sv["ticker_evidence"]["ticker"]            == "SIVB"
    assert sv["ticker_evidence"]["aggregate"]["lean"] == "bearish"

    # Held position thesis dump is the full PositionThesis (this ticker IS held).
    held = sv["held_view_at_decision"]
    assert held is not None
    assert held["ticker"]        == "SIVB"
    assert held["opened_price"]  == 268.5
    assert held["target_price"]  == 320.0
    assert held["stop_price"]    == 240.0
    assert held["rationale"].startswith("Above-average")

    # ── AAPL BUY snapshot (newly-opened — no prior position) ─────────────────
    aapl_path = next(tmp_path / f for f in files if "AAPL__buy" in f)
    aapl      = json.loads(aapl_path.read_text())

    sd_a = aapl["strategist_decision"]
    assert sd_a["stance"]["ticker"]           == "AAPL"
    assert sd_a["stance"]["preferred_weight"] == 0.05
    assert sd_a["stance"]["target_price"]     == 160.0
    assert sd_a["close_reason"]               == ""  # AAPL not in close_reasons
    assert sd_a["reasoning"].startswith("Rotating out of regional banks")  # tick-level field shared across both fills

    # AAPL is flat — held_view_at_decision is None because no prior thesis exists.
    assert aapl["strategist_view"]["held_view_at_decision"] is None
    assert aapl["strategist_view"]["ticker_evidence"]["aggregate"]["lean"] == "bullish"


def test_skips_rejected_executions(tmp_path: Path) -> None:
    """A rejected order does not produce a decision snapshot."""
    from backtest.decision_logger import DecisionLogger

    logger = DecisionLogger(output_dir=tmp_path, window_key="x")

    state = {
        "as_of":      "2023-03-13T09:30:00-04:00",
        "tick_phase": "open",
        "tick_id":    "tick-1",

        "executions": [
            {
                "order": {
                    "ticker":   "X",
                    "action":   "BUY",
                    "quantity": 1,
                },
                "status": "rejected",
                "error":  "insufficient funds",
            },
        ],

        # Empty real-shaped containers — the logger should not crash on absent data.
        "temp:ticker_evidence_objects": [],
        "strategist_decision":          {},
        "positions":                    {},
        "clamps":                       [],
    }

    logger.on_executions(state)

    assert list(tmp_path.glob("*.json")) == []
