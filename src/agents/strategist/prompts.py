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

from config.risk_gate import get_risk_gate_config
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

# R5 — risk-gate percentages, resolved at import time from
# ``config/risk_gate.json`` so a future config edit auto-updates the
# prompt without code change.  The integer-rounded percentages match
# how the LLM thinks about caps (and what the gate enforces — the gate
# operates on the float fractions, so 0.05 vs "5 %" stay aligned).
_RISK              = get_risk_gate_config()
_MAX_POSITION_PCT  = int(round(_RISK.max_position_weight  * 100))
_MAX_DELTA_PCT     = int(round(_RISK.max_delta_per_ticker * 100))
_MAX_TURNOVER_PCT  = int(round(_RISK.max_total_turnover   * 100))
_CASH_FLOOR_PCT    = int(round(_RISK.cash_floor_weight    * 100))

# Conditional cash-floor stanza — operator can re-introduce a floor by
# editing the JSON; the prompt re-renders accordingly without code change.
if _RISK.cash_floor_weight <= 0.0:
    _CASH_FLOOR_STANZA = (
        "- No cash floor — full deployment is permitted when conviction "
        "supports it."
    )
else:
    _CASH_FLOOR_STANZA = (
        f"- Watchlist weight sum capped at "
        f"{100 - _CASH_FLOOR_PCT}% (Cash reserve ≥{_CASH_FLOOR_PCT}%)."
    )

# ─────────────────────────────────────────────────────────────────────────────
# Spec B — Mode header templates
# ─────────────────────────────────────────────────────────────────────────────
# These two literal strings drive the cold-start vs incremental framing
# described in the spec at lines ~562-580.  Selection happens in
# ``StrategistContextShim._run_async_impl``, which substitutes the count and
# emits the chosen template under ``temp:strategist_mode``.  The strategist
# instruction template carries a ``{temp:strategist_mode}`` placeholder which ADK's
# ``inject_session_state`` resolves at runtime.

COLD_START_MODE_TEMPLATE: str = (
    "Cold start — your portfolio is empty.  No prior open positions to evaluate.  "
    "Build an initial portfolio by scanning the watchlist evidence below.  Open "
    "1-3 high-conviction entries.  You may also write or revise the standing "
    "market thesis if you have a view."
)

INCREMENTAL_MODE_TEMPLATE: str = (
    "Incremental — you have {N} held positions opened on prior ticks.  Each is "
    "rendered below with the commitments you made on entry and the evolution "
    "since.  For every held position you MUST emit a stance (hold / trim / "
    "close / update) with a 'what has changed' reason.  You may also scan the "
    "watchlist evidence for fresh entry candidates and open new positions."
)

# Raw template — uses ``{{NAME}}`` markers for the build-time cap substitution
# below so that runtime ``{portfolio}``/``{tickers}`` placeholders survive
# untouched for ADK's ``.format()`` pass.
_RAW_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You decide a
per-ticker stance for the next trading hour.

## Mode
{temp:strategist_mode}

## Current State
Portfolio:    {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest:   {day_digest}
Thesis:       {thesis}

## Held Positions (your prior decisions, with evolution since open)
{temp:held_positions_view}

## Ticker Evidence (per-analyst breakdown — features, tags, and prose reports)
{temp:ticker_evidence}

## Reading analyst reports
Where an analyst's report contradicts its lean, the lean is the analyst's
final call — treat the report as their reasoning, not their conclusion. You
may still override an analyst, but write down which signal you overweighted
and why.

Treat the digested aggregate as a deterministic input; you may disagree with
it based on context (held position thesis, memory, day digest) — call out
the disagreement in your rationale when you do.

## Your Job

Watchlist for this tick: {tickers}.

**For every held position above**, you MUST emit exactly one stance with
intent ∈ {{hold, trim, close, update}}.  The ``reason`` field on each held
stance must articulate WHAT HAS CHANGED since you opened the position
(price evolution, catalyst status, time elapsed, evidence shift) — even
if your decision is hold.  Silent carry-forward is NOT permitted on held
positions; the validator will reject the response.

**For watchlist tickers you do NOT currently hold**, the active-stances
model applies: emit a stance only for tickers you want to OPEN.  Omitting
a flat ticker carries no implicit commitment.

A "no new opens, all holds" tick is a legitimate response — but every held
position must still have its own stance.

## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

Each stance carries an ``intent`` verb and the fields required for that verb.
The table below is the single source of truth.  "Required" means the schema
WILL reject your response if the field is missing — these are not suggestions.

| Intent | What it means                           | Required fields                            | Optional fields                                      |
|--------|-----------------------------------------|--------------------------------------------|------------------------------------------------------|
| open   | enter a flat ticker (current weight 0)  | weight, horizon, target_price, stop_price, rationale | catalyst                                   |
| add    | grow an existing position               | weight, reason                             | horizon, target_price, stop_price, catalyst (updates)|
| trim   | reduce an existing position (not to 0)  | weight, reason                             | —                                                    |
| close  | exit an existing position completely    | reason                                     | —                                                    |
| hold   | no trade — review only                  | reason                                     | —                                                    |
| update | no trade — revise the thesis            | reason plus one or more of target_price / stop_price / horizon / catalyst | the remaining of those four              |

A missing required field is the most common decision-killer.  If you cannot
fill every required field for the verb you've picked, pick a different verb
(e.g. ``hold`` to pass on a trade you cannot fully thesize this tick).

### Field constraints (schema-enforced)

- weight: float greater than 0 and at most 1.  Required on open/add/trim;
  omit (null) on close/hold/update — emitting a number on those verbs is
  rejected.  Risk gate clamps: single-ticker ≤{{MAX_POSITION_PCT}}%,
  per-tick delta ≤{{MAX_DELTA_PCT}}%, total turnover ≤{{MAX_TURNOVER_PCT}}%.
  {{CASH_FLOOR_STANZA}}
- horizon: one of "intraday", "swing", "long_term".
- target_price / stop_price: floats.  target_price is where your thesis
  pays off; stop_price is where it's invalidated.
- rationale: as brief as you like — one short sentence is fine.  There is
  NO minimum length.  Hard upper limit of {{STANCE_RATIONALE_MAX}}
  characters.  Do not pad; do not repeat yourself.  FROZEN at open — you
  cannot revise it later.
- reason: as brief as you like — one short sentence is fine.  There is
  NO minimum length.  Hard upper limit of {{STANCE_RATIONALE_MAX}}
  characters.  Do not pad.
- catalyst: a single phrase or short sentence.  Hard upper limit of
  {{STANCE_CATALYST_MAX}} characters.
- confidence (decision-level): float between 0.0 and 1.0 inclusive.
- reasoning (decision-level): brief.  Hard upper limit of
  {{DECISION_REASONING_MAX}} characters.  No minimum.
- thesis (decision-level, optional — null carries the prior thesis
  forward): hard upper limit of {{DECISION_THESIS_MAX}} characters.
- decision_tag (decision-level): snake_case label, hard upper limit of
  40 characters.
- Off-watchlist tickers are rejected.

## How to submit your output

Emit ONE JSON object with this exact top-level shape — nothing else:

{{
  "stances": [ ... one stance per ticker you are acting on ... ],
  "decision_tag": "snake_case_label",
  "reasoning": "One short paragraph on the tick as a whole.",
  "thesis": null,
  "confidence": 0.7
}}

Keep every text field short.  One sentence is usually enough; two if
needed.  Do NOT pad, repeat yourself, or restate the field's other
values inside its text.  Stop writing as soon as the point is made.

Worked example — complete output for a 2-ticker tick (one open, one hold):

{{
  "stances": [
    {{
      "ticker": "XYZ", "intent": "open", "weight": 0.05,
      "horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
      "catalyst": "earnings beat expected next week",
      "rationale": "Strong fundamentals and bullish technical setup."
    }},
    {{
      "ticker": "ABC", "intent": "hold",
      "reason": "Thesis intact; no material change since open."
    }}
  ],
  "decision_tag": "ai_momentum_add",
  "reasoning": "Adding XYZ on AI catalyst; ABC carries forward on unchanged thesis.",
  "thesis": null,
  "confidence": 0.7
}}
"""

# Build-time substitution of the cap markers.  ``str.replace`` is used rather
# than ``.format`` so that the runtime ``{...}`` placeholders are not touched.
# Note: ``{{STANCE_CLOSE_REASON_MAX}}`` and ``{{STANCE_TRIM_REASON_MAX}}``
# have been removed — the new prompt unifies both under ``{{STANCE_RATIONALE_MAX}}``,
# since ``reason`` is now the single free-text verb-conditional field.
STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.thesis_max_chars))
    # ``rationale`` cap also governs ``reason`` (both schema-enforced).
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    .replace("{{STANCE_CATALYST_MAX}}",     str(_STANCE.catalyst_max_chars))
    # R5 — risk-gate percentages injected from config/risk_gate.json.
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{MAX_DELTA_PCT}}",           str(_MAX_DELTA_PCT))
    .replace("{{MAX_TURNOVER_PCT}}",        str(_MAX_TURNOVER_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
