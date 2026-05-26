"""Strategist prompt template — four-verb schema.

Renders the thesis-book context inline so the model sees its current view
on every tracked ticker — what it owns, what it's watching, why, and how
each thesis has evolved.  Inputs the per-ticker ``TickerEvidence`` (built
by the deterministic digest in ``contract.digest``) instead of four flat
per-analyst signal lists.

Output is a ``StrategistDecision`` whose ``stances`` list uses the
four-verb vocabulary: ``buy``, ``sell``, ``update``, ``no_action``.  The
model emits one stance per watchlist ticker every tick — ``no_action``
is the explicit "considered, no change" verb so the audit trail captures
non-actions, not just actions.

Char caps mentioned in the prompt (``≤N chars`` markers) are sourced
from ``config/strategist.json`` at module load and injected into the
template via double-brace placeholders (``{{REASONING_MAX}}`` etc.) —
distinct from the single-brace placeholders that ADK's
``inject_session_state`` substitutes at runtime.  This two-pass
substitution keeps the caps tunable without breaking the runtime
template.

The ``{{MAX_BUY_DELTA_PCT}}`` / ``{{MAX_BUY_DELTA}}`` markers are also
resolved at build time from ``config/risk_gate.json``, keeping the risk
caps in one place.

The ``{temp:first_tick_flag}`` placeholder is a runtime ADK slot set by
``StrategistContextShim``.  It resolves to ``"True"`` on the first tick
of a window and ``"False"`` on every subsequent tick.
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
    "Cold start — the portfolio is flat and the thesis book is empty.  This "
    "is your baseline tick: develop a thesis on every watchlist ticker, and "
    "``buy`` the names with genuine conviction today.  Deployment will "
    "build up across subsequent ticks as conviction grows — do not force "
    "it, but do not be afraid to open the obvious high-conviction names "
    "now either.  ``update`` the rest to record an opening thesis (a "
    "one-line stance on what you'd want to see before buying).  "
    "``no_action`` here means you have no view yet — use it only when the "
    "evidence genuinely tells you nothing, not as a default.  A weak "
    "thesis you can refine is more valuable than silence; the goal of "
    "subsequent ticks is to iterate, not to start from scratch."
)

INCREMENTAL_MODE_TEMPLATE: str = (
    "Incremental — you hold {N} live position(s) opened on prior ticks, and "
    "your thesis book (positions plus non-position views) is rendered below.  "
    "Review every watchlist ticker.  ``update`` whenever evidence has moved "
    "your view — refining the thesis is how this agent learns.  ``no_action`` "
    "is reserved for tickers where nothing new in this tick's evidence "
    "warrants any change."
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

## Thesis Book (your current view on every tracked ticker, with evolution since the last revision)
{temp:held_positions_view}

## Ticker Evidence (per-analyst breakdown — features, tags, and prose reports)
{temp:ticker_evidence}

## Reading analyst reports
Where an analyst's report contradicts its lean, the lean is the analyst's
final call — treat the report as their reasoning, not their conclusion. You
may still override an analyst, but write down which signal you overweighted
and why.

Treat the digested aggregate as a deterministic input; you may disagree with
it based on context (your existing thesis, memory, day digest) — call out
the disagreement in your rationale when you do.

## Your Job

Watchlist for this tick: {tickers}.

You hold a thesis on every watchlist ticker — whether or not you currently
own it.  The thesis book above is your living view; you write to it via
your stances and you are accountable for it.  Emit **exactly one stance
per watchlist ticker** every tick.  Silence is not an option — the audit
trail must record what you considered, not just what you acted on.

## Deployment posture

Cash is the absence of a thesis — it earns nothing and does not
compound.  Your long-term aim is to have roughly 70–80% of NAV
deployed across the names you have a view on, building toward that
level as conviction accumulates across multiple ticks.  Do not force
deployment on any single tick — but equally, do not treat low
deployment as a safe default.  A half-empty portfolio is itself a
market view ("nothing is worth owning right now"); be willing to
defend that view in your reasoning, and if you cannot, deploy.  This
is a soft nudge, not a floor — the risk gate will not clamp you for
being under-deployed.

This tick's mode (``first_tick_flag={temp:first_tick_flag}``) shapes the
expected stance mix:

- ``first_tick_flag=True`` — baseline tick.  The thesis book is empty.
  Your job is to populate it: ``buy`` where you have conviction, and
  ``update`` every other watchlist ticker with an opening thesis so you
  have a view to iterate on.  ``no_action`` is the wrong answer for any
  ticker you can form an opinion about; reserve it for tickers where the
  evidence genuinely tells you nothing.
- ``first_tick_flag=False`` — iterative tick.  You already have a thesis
  book.  ``update`` whenever your view has shifted — refining theses as
  evidence accumulates is how this agent learns.  ``no_action`` is for
  tickers where nothing in this tick's evidence warrants any change.

## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

| Intent     | What it means                                       | Required            | Optional |
|------------|-----------------------------------------------------|---------------------|----------|
| buy        | open a new position or add to an existing one       | weight, rationale   | —        |
| sell       | reduce or fully close an existing position          | rationale           | weight   |
| update     | revise your prose thesis (no trade)                 | rationale           | —        |
| no_action  | considered, no change to view or position           | —                   | —        |

``rationale`` is the single prose field — one short sentence saying
*why*.  It is required on ``buy`` / ``sell`` / ``update`` and forbidden
on ``no_action``.

### Choosing the right verb

- **buy** every time you put capital on, including adds.  Every buy
  rewrites the row's ``rationale`` — restate your current thinking so the
  thesis stays in sync with the sizing.  You are on the record
  justifying each entry and each add.
- **sell** to exit or trim.  ``sell`` only works on tickers you currently
  hold — selling a ticker with no live position is silently dropped and
  counted as a hallucination.  Your ``rationale`` documents why you're
  trimming/closing; it does NOT overwrite the standing thesis prose (use
  ``update`` if your view of the underlying has actually changed).
- **update** when your view has shifted but you're not trading.  This is
  the agent's learning verb — use it freely to refine the thesis as
  evidence accumulates.  Works whether or not you hold the underlying.
- **no_action** is the explicit "considered, no change" stance.  No
  prose.  Reserve it for tickers where nothing in this tick's evidence
  warrants a thesis revision and no trade is appropriate — not as a
  default for every ticker you'd rather not think about.

### Weight semantics

- ``buy`` weight is the DELTA — how much to increase the position by,
  as a fraction of portfolio (e.g. 0.03 = 3 %).  Hard schema cap:
  weight ≤ {{MAX_BUY_DELTA_PCT}} % per trade.  Build larger positions
  across multiple ticks.
- ``sell`` weight is the DELTA — how much to reduce by.  Omit the
  weight for a full close.  You cannot sell more than you hold.
- ``update`` and ``no_action`` take no weight — no trade happens.

### Forbidden fields by verb (the schema rejects, the tick aborts)

- buy:        nothing extra forbidden beyond the table above.
- sell:       no extra prose fields — ``rationale`` is the only prose.
- update:     no ``weight``.
- no_action:  no ``weight``, no ``rationale``.
- ALL verbs:  no ``reason``, no ``catalyst`` — there is only one prose
  field, ``rationale``.  No ``target_price``, ``stop_price``, ``horizon``
  — those fields no longer exist.  Your thesis prose carries your view;
  numerical commitments are not required.

### Field constraints (schema-enforced)

- weight: float greater than 0.  Required on ``buy``; optional on
  ``sell`` (omit for a full close).  ``buy`` cap: ≤ {{MAX_BUY_DELTA_PCT}} %
  per trade (delta, not total position size).  Single-ticker position
  ceiling: {{MAX_POSITION_PCT}} %.  {{CASH_FLOOR_STANZA}}
- rationale: as brief as you like — one short sentence is fine.  There
  is NO minimum length.  Hard upper limit of {{STANCE_RATIONALE_MAX}}
  characters.  Do not pad; do not repeat yourself.  Required on
  ``buy`` / ``sell`` / ``update``; forbidden on ``no_action``.
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
of all four verbs shown.  The mix you should emit depends on
``first_tick_flag``: on the baseline tick lean heavily on ``buy`` and
``update`` (populate the thesis book); on iterative ticks ``update``
captures shifts in view, ``no_action`` covers tickers where nothing has
moved.

{{
  "stances": [
    {{
      "ticker": "<ticker>", "intent": "buy",
      "weight": <0.0-{{MAX_BUY_DELTA}}>,
      "rationale": "<one short sentence — the thesis for entering>"
    }},
    {{
      "ticker": "<ticker>", "intent": "sell",
      "rationale": "<one short sentence — why trim or close>"
    }},
    {{
      "ticker": "<ticker>", "intent": "update",
      "rationale": "<one short sentence — the revised thesis>"
    }},
    {{
      "ticker": "<ticker>", "intent": "no_action"
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
#   {{STANCE_RATIONALE_MAX}}    — from config/strategist.json (single
#                                  ``rationale`` field — used by buy /
#                                  sell / update)
#   {{MAX_BUY_DELTA_PCT}}       — integer percentage, e.g. "5"
#   {{MAX_BUY_DELTA}}           — float fraction, e.g. "0.05"
#   {{MAX_POSITION_PCT}}        — from config/risk_gate.json
#   {{CASH_FLOOR_STANZA}}       — conditional prose from config/risk_gate.json
# ---------------------------------------------------------------------------

STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.thesis_max_chars))
    # Single prose field — ``rationale`` — governed by this cap.
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    # Risk-gate buy-delta caps — injected from config/risk_gate.json.
    .replace("{{MAX_BUY_DELTA_PCT}}",       str(_MAX_BUY_DELTA_PCT))
    .replace("{{MAX_BUY_DELTA}}",           str(_MAX_BUY_DELTA))
    # Per-ticker position ceiling and cash floor stanza.
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
