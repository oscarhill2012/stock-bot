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
    """With shipped defaults the prompt cites 20 %, 5 %, 50 %, no cash floor."""

    # Import the module fresh so the patched config (if any earlier test
    # mutated state) is re-applied.
    from importlib import reload
    import agents.strategist.prompts as prompts_mod
    reload(prompts_mod)

    text = prompts_mod.STRATEGIST_INSTRUCTION

    assert "20%" in text, "max_position_weight (20 %) must surface in the prompt"
    assert "5%" in text,  "max_delta_per_ticker (5 %) must surface in the prompt"
    assert "50%" in text, "max_total_turnover (50 %) must surface in the prompt"
    assert "No cash floor" in text, "default cash_floor=0 stanza must surface"


def test_substitutions_track_config_changes(tmp_path: Path, monkeypatch) -> None:
    """Editing the config + reloading flips the rendered percentages."""

    cfg_file = tmp_path / "risk_gate.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_held_weight":       0.001,
                "max_position_weight":   0.20,
                "cash_floor_weight":     0.05,
                "max_delta_per_ticker":  0.02,
                "max_total_turnover":    0.40,
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

    assert "2%" in text, "patched max_delta_per_ticker (2 %) must surface"
    assert "40%" in text, "patched max_total_turnover (40 %) must surface"
    assert "Cash reserve ≥5%" in text, "patched cash floor stanza must surface"
    assert "No cash floor" not in text, "default stanza must not coexist"
