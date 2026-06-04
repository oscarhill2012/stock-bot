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
    - ``user:positions`` — the per-ticker PositionThesis dump book (A-014: the
      decision_logger reads this key, not the old bare ``"positions"`` bridge).
      Populated for the held ticker (SIVB); absent for the flat-and-being-opened
      one (AAPL).
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
        # Uses the iter-3 three-verb schema: buy / sell / update.
        "strategist_decision": {
            "stances": [
                {
                    "ticker":    "SIVB",
                    "intent":    "sell",
                    "rationale": "Thesis broken",
                    "catalyst":  None,
                },
                {
                    "ticker":    "AAPL",
                    "intent":    "buy",
                    "weight":    0.05,
                    "rationale": "Opening on bullish technical setup.",
                    "catalyst":  "Earnings beat expected next week",
                },
            ],
            # sell_reasons / update_reasons removed (A-013 tail);
            # sell rationale lives on the stance itself.
            "reasoning":    "Rotating out of regional banks into mega-cap tech on the back of the SIVB blowup.",
            "thesis":       "Regional bank stress is the dominant risk; rotate to balance-sheet-strong mega-caps.",
            "decision_tag": "rotate_to_megacap",
            "confidence":   0.78,
        },

        # The held-position book.  SIVB is currently held; AAPL is flat.
        # Uses iter-3 PositionThesis fields (no horizon / target_price / stop_price).
        # A-014: decision_logger reads user:positions (the persistent cross-tick
        # thesis-book), not the bare "positions" key (the old in-tick bridge).
        "user:positions": {
            "SIVB": {
                "ticker":                   "SIVB",
                "opened_at":                "2023-03-01T14:30:00+00:00",
                "opened_tick_id":           "tick-prior",
                "opened_price":             268.5,
                "weight":                   0.10,
                "rationale":                "Above-average net interest margin and deposit growth.",
                "catalyst":                 "Q1 earnings",
                "last_reviewed_at":         "2023-03-13T09:30:00+00:00",
                "last_reviewed_decision":   "buy",
                "thesis_last_updated_tick": 0,
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
    # Uses iter-3 key names (sell_reason, iter-3 intent verbs).
    sd = sivb["strategist_decision"]
    assert sd["stance"]["ticker"]       == "SIVB"
    assert sd["stance"]["intent"]       == "sell"   # iter-3 verb (was "close")
    # A-013 tail: sell reason now lives on stance["rationale"] (not the old "reason" key).
    assert sd["stance"]["rationale"]    == "Thesis broken"
    assert sd["sell_reason"]            == "Thesis broken"
    assert sd["reasoning"].startswith("Rotating out of regional banks")
    assert sd["thesis"].startswith("Regional bank stress")
    assert sd["decision_tag"]           == "rotate_to_megacap"
    assert sd["confidence"]             == 0.78

    # Strategist view section is populated.
    sv = sivb["strategist_view"]
    assert sv["ticker_evidence"]["ticker"]            == "SIVB"
    assert sv["ticker_evidence"]["aggregate"]["lean"] == "bearish"

    # Held position thesis dump is the iter-3 PositionThesis (no target/stop/horizon).
    held = sv["held_view_at_decision"]
    assert held is not None
    assert held["ticker"]       == "SIVB"
    assert held["opened_price"] == 268.5
    assert held["rationale"].startswith("Above-average")

    # ── AAPL BUY snapshot (newly-opened — no prior position) ─────────────────
    aapl_path = next(tmp_path / f for f in files if "AAPL__buy" in f)
    aapl      = json.loads(aapl_path.read_text())

    sd_a = aapl["strategist_decision"]
    assert sd_a["stance"]["ticker"]  == "AAPL"
    assert sd_a["stance"]["intent"]  == "buy"   # iter-3 verb (was "open")
    assert sd_a["stance"]["weight"]  == 0.05
    assert sd_a["sell_reason"]       == ""  # AAPL has no sell stance this tick
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
        # A-014: decision_logger reads user:positions, not the bare "positions" key.
        "temp:ticker_evidence_objects": [],
        "strategist_decision":          {},
        "user:positions":               {},
        "clamps":                       [],
    }

    logger.on_executions(state)

    assert list(tmp_path.glob("*.json")) == []
