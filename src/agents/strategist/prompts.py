"""Strategist v2 prompt template.

Renders held-position context inline so the model sees what it bought, why, and
the targets/stops set on entry. Inputs the per-ticker `TickerEvidence` (built by
the deterministic digest in `contract.digest`) instead of four flat per-analyst
signal lists. Output is a list[TickerStance] exhaustive over the watchlist.

Char caps mentioned in the prompt (``≤N chars`` markers) are sourced from
``config/strategist.json`` at module load and injected into the template via
double-brace placeholders (``{{REASONING_MAX}}`` etc.) — distinct from the
single-brace placeholders that ADK's ``inject_session_state`` substitutes at
runtime.  This two-pass substitution keeps the caps tunable without breaking
the runtime template.
"""
from __future__ import annotations

from config.strategist import get_strategist_config

# Resolve caps once at import time.  The values injected into the prompt are
# the *prompt-facing* caps from ``config/strategist.json`` — what we tell the
# model.  The schemas in ``schema.py`` / ``stance_schema.py`` apply
# ``slack_percent`` headroom on top of these via ``_cfg.schema_cap()``, so
# storage tolerates a 10% overshoot without truncation.  See the "two-tier
# convention" note in ``src/config/strategist.py`` for the rationale.
_cfg      = get_strategist_config()
_DECISION = _cfg.decision_caps
_STANCE   = _cfg.stance_caps

# Raw template — uses ``{{NAME}}`` markers for the build-time cap substitution
# below so that runtime ``{portfolio}``/``{tickers}`` placeholders survive
# untouched for ADK's ``.format()`` pass.
_RAW_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You decide a
per-ticker stance for the next trading hour.

## Current State
Portfolio:    {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest:   {day_digest}
Thesis:       {thesis}

## Held Positions (your prior decisions)
{held_positions_view}

## Ticker Evidence (per-analyst breakdown — features, tags, and prose reports)
{ticker_evidence}

## Reading analyst reports
Where an analyst's report contradicts its lean, the lean is the analyst's
final call — treat the report as their reasoning, not their conclusion. You
may still override an analyst, but you must write down which signal you chose
to overweight and why.

## Your Job
Emit a TickerStance for EVERY watchlist ticker: {tickers}.

Per stance:
- preferred_weight ∈ [0,1]: your ideal portfolio weight next tick.
- conviction ∈ [0,1]: how strongly you hold this view.
- rationale: ≤{{STANCE_RATIONALE_MAX}} chars, why.
- If proposing to OPEN (current ≈ 0 → preferred > 0): include horizon,
  target_price, stop_price; catalyst optional (≤{{STANCE_CATALYST_MAX}} chars).
- If proposing to CLOSE (current > 0 → preferred ≈ 0): include close_reason
  (≤{{STANCE_CLOSE_REASON_MAX}} chars).
- If proposing to TRIM (current > 0 → preferred meaningfully lower but still
  held): include trim_reason (≤{{STANCE_TRIM_REASON_MAX}} chars).
- If holding or adding: lifecycle hint fields stay null.

Treat the digested aggregate as a deterministic input; you may disagree with it
based on context (held position thesis, memory, day digest) — call out the
disagreement in your rationale when you do.

Also emit at the decision level:
- decision_tag (snake_case, ≤40 chars): this tick's headline decision.
- reasoning (≤{{DECISION_REASONING_MAX}} chars): overall summary across all
  stances.  Be concise — this is a summary budget, not space for full
  chain-of-thought.
- updated_thesis (≤{{DECISION_THESIS_MAX}} chars): working hypothesis for next
  tick.
- confidence ∈ [0,1]: overall conviction in this tick's plan.

Watchlist: {tickers}
"""

# Build-time substitution of the cap markers.  ``str.replace`` is used rather
# than ``.format`` so that the runtime ``{...}`` placeholders are not touched.
STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.updated_thesis_max_chars))
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    .replace("{{STANCE_CATALYST_MAX}}",     str(_STANCE.catalyst_max_chars))
    .replace("{{STANCE_CLOSE_REASON_MAX}}", str(_STANCE.close_reason_max_chars))
    .replace("{{STANCE_TRIM_REASON_MAX}}",  str(_STANCE.trim_reason_max_chars))
)
