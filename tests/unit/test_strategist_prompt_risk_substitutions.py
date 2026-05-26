"""Strategist prompt restates risk rules with config-driven values.

The prompt previously had hard-coded percentages.  The values now live in
``config/risk_gate.json`` and are substituted into the prompt at module
import so a future config change automatically updates the rendered
template.
"""
from __future__ import annotations

import json
from pathlib import Path


def test_default_substitutions_visible() -> None:
    """With shipped defaults the prompt cites the live config values.

    The prompt renders percentages as "<N> %" (with a space) rather than
    "<N>%", matching the natural-language style in the instruction text.
    """

    # Import the module fresh so the patched config (if any earlier test
    # mutated state) is re-applied.
    from importlib import reload

    import agents.strategist.prompts as prompts_mod
    from config import risk_gate as rg

    rg.get_risk_gate_config.cache_clear()
    reload(prompts_mod)

    cfg = rg.get_risk_gate_config()
    text = prompts_mod.STRATEGIST_INSTRUCTION

    # Position ceiling and buy-delta cap render as "N %" in the prompt.
    pos_pct = int(round(cfg.max_position_weight * 100))
    buy_pct = int(round(cfg.max_delta_per_buy   * 100))
    assert f"{pos_pct} %" in text or f"{pos_pct}%" in text, \
        f"max_position_weight ({pos_pct} %) must surface in the prompt"
    assert f"{buy_pct} %" in text or f"{buy_pct}%" in text, \
        f"max_delta_per_buy ({buy_pct} %) must surface in the prompt"

    # Default cash floor is zero, which renders as the "No cash floor" stanza.
    if cfg.cash_floor_weight <= 0.0:
        assert "No cash floor" in text, "cash_floor=0 stanza must surface"


def test_substitutions_track_config_changes(tmp_path: Path, monkeypatch) -> None:
    """Editing the config + reloading flips the rendered percentages."""

    cfg_file = tmp_path / "risk_gate.json"
    cfg_file.write_text(
        json.dumps(
            {
                "min_held_weight":     0.001,
                "max_position_weight": 0.20,
                "cash_floor_weight":   0.05,
                "max_total_turnover":  0.40,
                "max_delta_per_buy":   0.02,
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
    assert "2 %" in text or "2%" in text, "patched max_delta_per_buy (2 %) must surface"
    # The cash floor stanza must be present (non-zero cash_floor_weight).
    assert "Cash reserve" in text, "patched cash floor stanza must surface"
    assert "No cash floor" not in text, "default no-floor stanza must not coexist"
