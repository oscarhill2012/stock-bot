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

The lifecycle action for each emitted stance is derived from current weight
vs your ``preferred_weight`` (or the explicit ``intent`` verb on the
stance).  The table below is the single source of truth for which fields
must be set per action; the worked examples at the bottom are illustrations,
not a separate ruleset.

| Action / intent | Current → Preferred         | Required fields                                                                              |
|-----------------|-----------------------------|----------------------------------------------------------------------------------------------|
| OPEN            | 0       → > 0               | horizon, target_price, stop_price, rationale (+ optional catalyst)                            |
| ADD             | > 0     → higher (> 0)      | horizon, target_price, stop_price                                                             |
| HOLD            | > 0     → same              | **reason** (what's changed since open)                                                        |
| TRIM            | > 0     → lower (still > 0) | horizon, target_price, stop_price, **trim_reason** (= reason)                                 |
| CLOSE           | > 0     → 0                 | **close_reason** (= reason).  horizon / target_price / stop_price stay null — you are exiting.|
| UPDATE          | > 0     → same              | **reason**, and at least one of target_price / stop_price / catalyst / horizon                |

### NULL DISCIPLINE — read this twice before emitting any stance

The fields ``horizon``, ``target_price``, and ``stop_price`` are CONDITIONALLY
required.  The conditional is on lifecycle action, not on confidence:

- **OPEN, ADD, TRIM, UPDATE** (any stance with ``preferred_weight > 0``):
  ``horizon``, ``target_price``, ``stop_price`` MUST be **non-null** values
  (a horizon literal, a positive float price, a positive float price).
  Emitting ``null`` for any of them is a hard validation failure — the
  schema validator will reject the entire decision and you will be
  re-prompted.  "I am not sure of the exact target" is not an excuse;
  provide your best estimate.

- **CLOSE, HOLD** (``preferred_weight == 0`` or unchanged hold):
  ``horizon``, ``target_price``, ``stop_price`` MUST be **null** —
  you are exiting (or have already articulated discipline elsewhere), so
  those fields carry no meaning.

The two worked examples below illustrate exactly these two regimes.
Generalising the all-nulls shape of the CLOSE example onto an OPEN stance
is the single most common way decisions get rejected — do not do it.

Schema-level rules (failing these means ADK rejects your response):
- preferred_weight: float in [0.0, 1.0].  Long-only — 0.0 is the floor.

  Hard rules the risk gate enforces after you respond (so a stance that
  violates them will be clamped — propose values that already respect them):
  - Single-ticker weight capped at {{MAX_POSITION_PCT}}%.
  - Per-ticker weight change capped at {{MAX_DELTA_PCT}}% per tick — if you
    want to size up faster, the gate will trim your delta back to
    {{MAX_DELTA_PCT}}% and you ramp over multiple ticks.
  - Total per-tick turnover (sum of |deltas| across watchlist) capped at
    {{MAX_TURNOVER_PCT}}%.
  {{CASH_FLOOR_STANZA}}
- conviction: float in [0.0, 1.0].
- confidence (decision-level): float in [0.0, 1.0].
- horizon: one of "intraday", "swing", "long_term" — or null.
- rationale: ≤{{STANCE_RATIONALE_MAX}} chars.
- catalyst (optional): ≤{{STANCE_CATALYST_MAX}} chars.
- close_reason: ≤{{STANCE_CLOSE_REASON_MAX}} chars.
- trim_reason: ≤{{STANCE_TRIM_REASON_MAX}} chars.
- reasoning (decision-level): ≤{{DECISION_REASONING_MAX}} chars.
- thesis (decision-level, optional — null carries the prior thesis forward): ≤{{DECISION_THESIS_MAX}} chars.
- decision_tag (decision-level): snake_case label, ≤40 chars.
- Off-watchlist tickers are rejected.

## Two worked examples (the rest follow the table above)

OPEN (currently flat, opening at 0.05):
{{"ticker": "XYZ", "preferred_weight": 0.05, "conviction": 0.7,
"rationale": "Strong fundamentals, bullish technical setup",
"horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
"catalyst": "earnings beat expected next week",
"close_reason": null, "trim_reason": null}}

CLOSE (held at 0.05, exiting to 0.0):
{{"ticker": "XYZ", "preferred_weight": 0.0, "conviction": 0.7,
"rationale": "Thesis invalidated by guidance cut",
"horizon": null, "target_price": null, "stop_price": null,
"catalyst": null,
"close_reason": "guidance cut invalidates thesis",
"trim_reason": null}}

REJECTED — DO NOT EMIT (this exact shape is the most common decision-killer):
{{"ticker": "XYZ", "preferred_weight": 0.1, "conviction": 0.7,
"rationale": "Strong setup",
"horizon": null, "target_price": null, "stop_price": null,
"catalyst": null, "close_reason": null, "trim_reason": null}}
↑ preferred_weight > 0 with null horizon/target_price/stop_price triggers
"Stance for XYZ proposes a non-zero weight (0.1) but is missing required
lifecycle hint fields: ['horizon', 'target_price', 'stop_price']" and aborts
the whole decision.  If you are opening, you must commit to a horizon,
a target, and a stop — full stop.
"""

# Build-time substitution of the cap markers.  ``str.replace`` is used rather
# than ``.format`` so that the runtime ``{...}`` placeholders are not touched.
STRATEGIST_INSTRUCTION = (
    _RAW_INSTRUCTION
    .replace("{{DECISION_REASONING_MAX}}",  str(_DECISION.reasoning_max_chars))
    .replace("{{DECISION_THESIS_MAX}}",     str(_DECISION.thesis_max_chars))
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    .replace("{{STANCE_CATALYST_MAX}}",     str(_STANCE.catalyst_max_chars))
    .replace("{{STANCE_CLOSE_REASON_MAX}}", str(_STANCE.close_reason_max_chars))
    .replace("{{STANCE_TRIM_REASON_MAX}}",  str(_STANCE.trim_reason_max_chars))
    # R5 — risk-gate percentages injected from config/risk_gate.json.
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{MAX_DELTA_PCT}}",           str(_MAX_DELTA_PCT))
    .replace("{{MAX_TURNOVER_PCT}}",        str(_MAX_TURNOVER_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
