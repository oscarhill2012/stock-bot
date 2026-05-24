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
| open   | enter a flat ticker (current weight 0)  | weight, rationale, horizon, target_price, stop_price | catalyst                                   |
| add    | grow an existing position               | weight, reason                             | horizon, target_price, stop_price, catalyst (updates)|
| trim   | reduce an existing position (not to 0)  | weight, reason                             | —                                                    |
| close  | exit an existing position completely    | reason                                     | —                                                    |
| hold   | no trade — review only                  | reason (what has changed since open)       | —                                                    |
| update | no trade — revise the thesis            | reason + ≥1 of target_price/stop_price/horizon/catalyst | the remaining of those four              |

### Required-fields cheat-sheet — re-read before every open

OPEN demands FIVE fields in the JSON object, every time:
  1. weight         (float in (0, 1])
  2. rationale      (string, ≤{{STANCE_RATIONALE_MAX}} chars)
  3. horizon        (one of "intraday", "swing", "long_term")
  4. target_price   (float, the price level your thesis is targeting)
  5. stop_price     (float, the price level that invalidates your thesis)

Emitting an ``open`` stance without all five is the most common decision-killer.
Before you commit to ``intent: "open"``, verify your JSON object contains
every one of those five keys with a non-null value.  If you cannot fill any
of the five, choose a different verb instead (e.g. ``hold`` to pass on the
trade this tick).

Schema-level rules (failing these means ADK rejects your response):
- weight: float in (0, 1] on open/add/trim — long-only, 0.0 not permitted
  (use intent="close" instead).  Omit weight entirely (null) on close/hold/update
  — emitting 0.0 or any number on those verbs is rejected by the schema.
  The risk gate clamps single-ticker weight at {{MAX_POSITION_PCT}}%, per-tick delta
  at {{MAX_DELTA_PCT}}%, and total turnover at {{MAX_TURNOVER_PCT}}%.  Propose
  values that already respect these.
  {{CASH_FLOOR_STANZA}}
- horizon: one of "intraday", "swing", "long_term".
- rationale: ≤{{STANCE_RATIONALE_MAX}} chars.  FROZEN at open — you cannot change it later.
  This is a HARD cap, not a soft target.  Write tight, evidence-led prose.
- reason / catalyst: ≤{{STANCE_RATIONALE_MAX}} chars each.  Also hard caps.
- confidence (decision-level): float in [0.0, 1.0].
- reasoning (decision-level): ≤{{DECISION_REASONING_MAX}} chars.
- thesis (decision-level, optional — null carries the prior thesis forward): ≤{{DECISION_THESIS_MAX}} chars.
- decision_tag (decision-level): snake_case label, ≤40 chars.
- Off-watchlist tickers are rejected.

## Two worked examples

OPEN (currently flat, opening at 0.05):
{{"ticker": "XYZ", "intent": "open", "weight": 0.05,
"rationale": "Strong fundamentals, bullish technical setup",
"horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
"catalyst": "earnings beat expected next week"}}

CLOSE (held at 0.05, exiting):
{{"ticker": "XYZ", "intent": "close",
"reason": "guidance cut invalidates thesis"}}
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
    # ``rationale`` cap also governs ``reason`` and ``catalyst`` in the new vocab.
    .replace("{{STANCE_RATIONALE_MAX}}",    str(_STANCE.rationale_max_chars))
    # R5 — risk-gate percentages injected from config/risk_gate.json.
    .replace("{{MAX_POSITION_PCT}}",        str(_MAX_POSITION_PCT))
    .replace("{{MAX_DELTA_PCT}}",           str(_MAX_DELTA_PCT))
    .replace("{{MAX_TURNOVER_PCT}}",        str(_MAX_TURNOVER_PCT))
    .replace("{{CASH_FLOOR_STANZA}}",       _CASH_FLOOR_STANZA)
)
