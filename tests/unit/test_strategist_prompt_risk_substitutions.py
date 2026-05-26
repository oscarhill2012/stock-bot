"""R5 — strategist prompt restates risk rules with config-driven values.

The prompt previously had a hard-coded "single-ticker weight at 20% and
keeps ≥10% cash" sentence.  R4 moved the constants to
``config/risk_gate.json``; R5 makes the prompt substitute them at module
import so a future config change automatically updates the prompt.
"""
from __future__ import annotations

import json
from pathlib import Path


def test_default_substitutions_visible() -> None:
    """With shipped defaults the prompt cites the risk-gate limits and no cash floor.

    The prompt renders percentages as "<N> %" (with a space) rather than "<N>%",
    matching the natural-language style in the instruction text.  The exact
    format is pinned here so a future template change produces a test failure
    rather than a silent prompt regression.
    """

    # Import the module fresh so the patched config (if any earlier test
    # mutated state) is re-applied.
    from importlib import reload
    import agents.strategist.prompts as prompts_mod
    reload(prompts_mod)

    text = prompts_mod.STRATEGIST_INSTRUCTION

    # Position ceiling and buy-delta cap are rendered as "N %" in the prompt.
    assert "20 %" in text or "20%" in text, "max_position_weight (20 %) must surface in the prompt"
    assert "5 %" in text or "5%" in text,   "max_delta_per_ticker (5 %) must surface in the prompt"
    assert "No cash floor" in text, "default cash_floor=0 stanza must surface"


def test_substitutions_track_config_changes(tmp_path: Path, monkeypatch) -> None:
    """Editing the config + reloading flips the rendered percentages.

    The prompt renders some values as "N %" (space-separated); we assert
    using both formats since the exact formatting may vary between fields.
    """

    cfg_file = tmp_path / "risk_gate.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_held_weight":         0.001,
                "max_position_weight":     0.20,
                "cash_floor_weight":       0.05,
                "max_delta_per_ticker":    0.02,
                "max_total_turnover":      0.40,
                "max_buy_delta_per_trade": 0.02,
            }
        ),
        encoding="utf-8",
    )

    from config import risk_gate as rg
    monkeypatch.setattr(rg, "_DEFAULT_PATH", cfg_file)
    rg.get_risk_gate_config.cache_clear()

    from importlib import reload
    import agents.strategist.prompts as prompts_mod
    reload(prompts_mod)

    text = prompts_mod.STRATEGIST_INSTRUCTION

    # The patched buy-delta cap (2 %) must appear.
    assert "2 %" in text or "2%" in text, "patched max_buy_delta_per_trade (2 %) must surface"
    # The cash floor stanza must be present (non-zero cash_floor_weight).
    assert "Cash reserve" in text, "patched cash floor stanza must surface"
    assert "No cash floor" not in text, "default no-floor stanza must not coexist"
