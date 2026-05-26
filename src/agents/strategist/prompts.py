"""Strategist v2 prompt template — three-verb schema edition.

Renders held-position context inline so the model sees what it bought, why,
and how the thesis has evolved.  Inputs the per-ticker ``TickerEvidence``
(built by the deterministic digest in ``contract.digest``) instead of four
flat per-analyst signal lists.

Output is a ``StrategistDecision`` whose ``stances`` list uses the three-verb
vocabulary: ``buy``, ``sell``, ``update``.

Char caps mentioned in the prompt (``≤N chars`` markers) are sourced from
``config/strategist.json`` at module load and injected into the template via
double-brace placeholders (``{{REASONING_MAX}}`` etc.) — distinct from the
single-brace placeholders that ADK's ``inject_session_state`` substitutes at
runtime.  This two-pass substitution keeps the caps tunable without breaking
the runtime template.

The ``{{MAX_BUY_DELTA_PCT}}`` / ``{{MAX_BUY_DELTA}}`` markers are also
resolved at build time from ``config/risk_gate.json``, keeping the risk caps
in one place.

The ``{temp:first_tick_flag}`` placeholder is a runtime ADK slot set by
``StrategistContextShim`` (Task 9).  It resolves to ``"True"`` on the first
tick of a window and ``"False"`` on every subsequent tick.
"""
from __future__ import annotations

from config.risk_gate import get_risk_gate_config
from config.strategist import get_strategist_config

# ---------------------------------------------------------------------------
# Config resolved once at import time.
# ---------------------------------------------------------------------------

_cfg      = get_strategist_config()
_DECISION = _cfg.decision_caps
_STANCE   = _cfg.stance_caps

# Risk-gate percentages — the LLM is told about these caps in the prompt and
# the gate enforces them on execution.  Integer-rounded percentages match
# how the model thinks about position sizing.
_RISK             = get_risk_gate_config()
_MAX_POSITION_PCT = int(round(_RISK.max_position_weight  * 100))
_MAX_DELTA_PCT    = int(round(_RISK.max_delta_per_ticker * 100))

# Buy-delta cap — used in the verb table and weight semantics explanation.
# ``_MAX_BUY_DELTA`` is the raw float (e.g. 0.05) and ``_MAX_BUY_DELTA_PCT``
# is the percentage integer (e.g. 5).  Both are injected into the prompt so
# neither the prose section nor the JSON example need hard-coded numbers.
_MAX_BUY_DELTA     = _RISK.max_delta_per_ticker          # e.g. 0.05
_MAX_BUY_DELTA_PCT = int(round(_MAX_BUY_DELTA * 100))    # e.g. 5

# Conditional cash-floor stanza — operator can re-introduce a floor by
# editing config/risk_gate.json; the prompt re-renders accordingly without
# any code change.
_CASH_FLOOR_PCT = int(round(_RISK.cash_floor_weight * 100))
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
# Mode header templates
# ─────────────────────────────────────────────────────────────────────────────
# These two literal strings drive the cold-start vs incremental framing.
# Selection happens in ``StrategistContextShim._run_async_impl``, which
# substitutes the count and emits the chosen template under
# ``temp:strategist_mode``.  The instruction template carries a
# ``{temp:strategist_mode}`` placeholder that ADK's ``inject_session_state``
# resolves at runtime.

COLD_START_MODE_TEMPLATE: str = (
    "Cold start — first tick of the run; portfolio is empty. Begin building a "
    "diversified portfolio. Larger position sizes are reasonable while capital "
    "is plentiful, but there is no rush — opening fewer this tick and adding on "
    "subsequent ticks is equally valid. You may also write or revise the "
    "standing market thesis if you have a view."
)

INCREMENTAL_MODE_TEMPLATE: str = (
    "Incremental — you have {N} held positions opened on prior ticks.  Each is "
    "rendered below with the commitments you made on entry and the evolution "
    "since.  Review each position and emit a stance when your view has changed "
    "or when you want to trade."
)

# ─────────────────────────────────────────────────────────────────────────────
# Raw instruction template
# ─────────────────────────────────────────────────────────────────────────────
# Uses ``{{NAME}}`` markers for build-time cap substitution below so that
# runtime ``{portfolio}``/``{tickers}``/``{temp:...}`` placeholders survive
# untouched for ADK's ``.format()`` pass.
#
# The ``{temp:_last_schema_error}`` placeholder sits at the very top of the
# prompt by design.  On the first attempt it resolves to an empty string and
# adds nothing.  On a schema-retry attempt the ``RetryingAgentWrapper`` has
# written a full correction directive into that key, and it becomes the first
# thing the model reads — placement matters more than wording when steering a
# model away from a repeated failure mode.

_RAW_INSTRUCTION = """
{temp:_last_schema_error}
You are the portfolio strategist for an algorithmic trading bot. You decide a
per-ticker stance for the next trading hour.

## Mode
{temp:strategist_mode}

## Current State
Portfolio:    {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest:   {day_digest}
Thesis:       {thesis}

## Recent Round-trips (your last closed positions — outcomes you should weigh before re-entering the same tickers)
{temp:recent_trades_view}

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

**First tick of a window** ({temp:first_tick_flag}): emit one stance for
every watchlist ticker so you establish a baseline view.  This is the
only tick where output volume is mandated.

**Every subsequent tick**: emit a stance ONLY when you have something
to say — one of:

  (a) you are buying or selling (intent='buy' or 'sell'),
  (b) you are revising the thesis prose on a held position
      (intent='update'),
  (c) your conviction has shifted enough that you want the audit trail
      to reflect it (also intent='update').

Tickers you do NOT mention this tick carry forward your last stated
view.  Silence is a valid response and means "no change."

## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

| Intent  | What it means                              | Required                         | Optional   |
|---------|--------------------------------------------|----------------------------------|------------|
| buy     | enter flat or increase existing position   | weight, rationale                | catalyst   |
| sell    | reduce or fully close a position           | reason                           | weight     |
| update  | revise thesis prose (no trade)             | reason                           | —          |

**Weight semantics:**

- ``buy`` weight is the DELTA — how much to increase the position by,
  as a fraction of portfolio (e.g. 0.03 = 3 %).  Hard schema cap:
  weight ≤ {{MAX_BUY_DELTA_PCT}} % per trade.  To build a larger
  position, buy across multiple ticks.
- ``sell`` weight is the DELTA — how much to reduce by.  Omit the
  weight for a full close.  Sell is uncapped beyond the current
  position size (you cannot sell more than you hold).
- ``update`` takes no weight — no trade happens.

**Forbidden fields by verb** (the schema rejects, the tick aborts):

- buy:    no ``reason``    (use ``rationale``)
- sell:   no ``rationale`` (use ``reason``)
- update: no ``weight``, no ``rationale``, no ``catalyst``
- ALL verbs: no ``target_price``, ``stop_price``, ``horizon`` — those
  fields no longer exist in the schema.  Your thesis prose carries
  your view; numerical commitments are not required.

**Choosing between sell and update:** if you want to exit (or trim)
the position this tick, use ``sell``.  If you want to revise what you
think but keep holding, use ``update``.  Holding silently (omitting
the ticker) is also valid if your view truly has not changed.

### Field constraints (schema-enforced)

- weight: float greater than 0.  Required on ``buy``; optional on
  ``sell`` (omit for a full close).  ``buy`` cap: ≤ {{MAX_BUY_DELTA_PCT}} %
  per trade (delta, not total position size).  Single-ticker position
  ceiling: {{MAX_POSITION_PCT}} %.  {{CASH_FLOOR_STANZA}}
- rationale: as brief as you like — one short sentence is fine.  There
  is NO minimum length.  Hard upper limit of {{STANCE_RATIONALE_MAX}}
  characters.  Do not pad; do not repeat yourself.  Only on ``buy``.
- reason: as brief as you like — one short sentence is fine.  There is
  NO minimum length.  Hard upper limit of {{STANCE_RATIONALE_MAX}}
  characters.  Do not pad.  Only on ``sell`` and ``update``.
- catalyst: a single phrase or short sentence.  Hard upper limit of
  {{STANCE_CATALYST_MAX}} characters.  Only on ``buy`` (optional).
- confidence (decision-level): float between 0.0 and 1.0 inclusive.
- reasoning (decision-level): brief.  Hard upper limit of
  {{DECISION_REASONING_MAX}} characters.  No minimum.
- thesis (decision-level, optional — null carries the prior thesis
  forward): hard upper limit of {{DECISION_THESIS_MAX}} characters.
- decision_tag (decision-level): snake_case label, hard upper limit of
  40 characters.
- Off-watchlist tickers are rejected.

## How to submit your output

Emit ONE JSON object with this exact shape — nothing else.  Examples
of all three verbs shown; in practice you will often emit zero or one
stance per tick (after the first).

{{
  "stances": [
    {{
      "ticker": "<ticker>", "intent": "buy",
      "weight": <0.0-{{MAX_BUY_DELTA}}>,
      "rationale": "<one short sentence>",
      "catalyst": "<short phrase, optional>"
    }},
    {{
      "ticker": "<ticker>", "intent": "sell",
      "reason": "<what changed, one sentence>"
    }},
    {{
      "ticker": "<ticker>", "intent": "update",
      "reason": "<thesis revision, one sentence>"
    }}
  ],
  "decision_tag": "<snake_case_label>",
  "confidence": <0.0-1.0>,
  "reasoning": "<brief>",
  "thesis": "<optional prose; null carries the prior thesis forward>"
}}

Keep every text field short. One sentence is usually enough; two if
needed. Do NOT pad, repeat yourself, or restate the field's other
values inside its text. Stop writing as soon as the point is made.
"""

# ---------------------------------------------------------------------------
# Build-time substitution of the cap markers.
#
# ``str.replace`` is used rather than ``.format`` so that the runtime
# ``{...}`` placeholders are not touched.
#
# Markers resolved here:
#   {{DECISION_REASONING_MAX}}  — from config/strategist.json
#   {{DECISION_THESIS_MAX}}     — from config/strategist.json
#   {{STANCE_RATIONALE_MAX}}    — from config/strategist.json (governs both
#                                  ``rationale`` and ``reason`` fields)
#   {{STANCE_CATALYST_MAX}}     — from config/strategist.json
#   {{MAX_BUY_DELTA_PCT}}       — integer percentage, e.g. "5"
#   {{MAX_BUY_DELTA}}           — float fraction, e.g. "0.05"
#   {{MAX_POSITION_PCT}}        — from config/risk_gate.json
#   {{CASH_FLOOR_STANZA}}       — conditional prose from config/risk_gate.json
# ---------------------------------------------------------------------------

STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.thesis_max_chars))
    # ``rationale`` cap also governs ``reason`` (both schema-enforced).
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    .replace("{{STANCE_CATALYST_MAX}}",     str(_STANCE.catalyst_max_chars))
    # Risk-gate buy-delta caps — injected from config/risk_gate.json.
    .replace("{{MAX_BUY_DELTA_PCT}}",       str(_MAX_BUY_DELTA_PCT))
    .replace("{{MAX_BUY_DELTA}}",           str(_MAX_BUY_DELTA))
    # Per-ticker position ceiling and cash floor stanza.
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
