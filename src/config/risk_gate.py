"""Loader for ``config/risk_gate.json`` — the five risk-gate constants.

The constants govern position-sizing constraints applied by
``src/agents/risk_gate/constraints.py`` and surfaced to the strategist
prompt via ``src/agents/strategist/prompts.py``.  Centralising them in
JSON matches the project-wide "all configuration in config/*.json"
convention and makes operator tuning a config edit rather than a code
edit.

The module-level singleton ``get_risk_gate_config()`` is the production
entry point; ``load_risk_gate_config(path=...)`` exists for tests that
want to feed a custom file.

A note on coupling: ``src/orchestrator/state.py`` re-exports each field
under its uppercase constant name so every existing
``from orchestrator.state import …`` call site keeps working unchanged.
The strategist prompt module reads this loader directly so the
prompt-stated percentages stay in lockstep with the gate-enforced ones.
The ``TickerStance`` schema validator also reads ``max_delta_per_buy``
from here, making this the single source of truth for the per-buy cap —
prompt, schema, and gate all converge on one number.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path.  The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather
# than relative to this file.
_DEFAULT_PATH = Path("config/risk_gate.json")


class RiskGateConfig(BaseModel):
    """Top-level shape of ``config/risk_gate.json``.

    Attributes
    ----------
    min_held_weight:
        Minimum weight above which a position is considered "open".
        Positions at or below this threshold are treated as closed when
        computing open-position telemetry.
    max_position_weight:
        Single-ticker concentration cap — no ticker may exceed this
        fraction of the portfolio after the risk gate runs.
    cash_floor_weight:
        Minimum cash reserve fraction.  When total invested weight would
        exceed ``1 - cash_floor_weight``, all weights are scaled down
        proportionally.  Set to ``0.00`` to allow the strategist to be
        fully invested.
    max_total_turnover:
        Maximum total portfolio turnover per tick (sum of absolute weight
        changes across every ticker).  Caps aggregate churn even when no
        single position is the offender.
    max_delta_per_buy:
        Per-buy stance delta cap (fraction of portfolio).  This is the
        single source of truth for the buy cap — three layers converge on
        it: the ``TickerStance`` schema validator rejects any buy whose
        ``weight`` exceeds this value, the strategist prompt renders the
        cap into the model's instructions, and
        ``constraints.apply_buy_delta_clamp`` clamps anything that slips
        through the schema (e.g. via ``model_construct``).  Sells are
        intentionally uncapped on a per-stance basis — the strategist may
        close any held position in a single tick — so only the buy
        direction needs a per-stance ceiling.
    """

    min_held_weight:      float = Field(ge=0.0, le=0.10)
    max_position_weight:  float = Field(gt=0.0, le=1.0)
    cash_floor_weight:    float = Field(ge=0.0, le=0.50)
    max_total_turnover:   float = Field(gt=0.0, le=2.0)
    max_delta_per_buy:    float = Field(gt=0.0, le=1.0)


def load_risk_gate_config(*, path: Path | None = None) -> RiskGateConfig:
    """Read and validate ``config/risk_gate.json``.

    Parameters
    ----------
    path:
        Override the default path.  Useful in tests that want to supply a
        temporary file without touching the source tree.

    Returns
    -------
    RiskGateConfig
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist at the resolved path.
    json.JSONDecodeError
        If the file content is not valid JSON.
    pydantic.ValidationError
        If the parsed payload fails schema validation.
    """
    p = path or _DEFAULT_PATH
    payload = json.loads(p.read_text(encoding="utf-8"))
    return RiskGateConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_risk_gate_config() -> RiskGateConfig:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is only
    read once per process.  A process restart is required after editing
    ``config/risk_gate.json`` — the constants are resolved at import time
    and baked into ``orchestrator.state``'s module-level names.

    Returns
    -------
    RiskGateConfig
        Validated configuration singleton.
    """
    return load_risk_gate_config()
