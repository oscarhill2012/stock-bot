# Strategist Council Design

**Status:** Spec — awaiting implementation plan
**Scope:** Spec 1 of 3 in the larger "deeper agent dive" initiative. Sibling specs (planned, not yet written): exit floor/ceiling rules, signal-pattern memory loop.

## Problem

The strategist tier is currently a single `LlmAgent` (Gemini Pro) that fuses all analyst signals into one `StrategistDecision`. One model, one voice, one set of biases. The risks are:

- **Single point of reasoning failure.** If the autocrat's prompt nudges it toward, say, momentum thinking, every trade carries that bias.
- **No surfaced disagreement.** A real-world investment committee gets value precisely from disagreement — buy/sell/hold has different defenders. Today we can't tell whether the strategist had a close call or a clear conviction.
- **Self-improvement is hard with one voice.** The companion memory-loop spec (Spec 3) wants to learn "which signal patterns predict good trades, stock-agnostically." That's much easier when we have multiple decision-makers whose disagreements correlate with PnL outcomes.

This spec replaces the autocrat with a **three-persona council** that votes in parallel and a **deterministic aggregator** that reconciles their stances into the same `StrategistDecision` shape downstream consumers already validate against. It also lightly enriches the analyst→strategist contract so the council can reason from structured evidence, not just natural-language strings.

## Goals & Non-Goals

**Goals**
- Replace the single strategist with three style-differentiated members (value / momentum / contrarian).
- Make convergence pluggable so the round-robin debate variant can drop in later (Spec 3) without re-architecting the call sites.
- Preserve the external `StrategistDecision` contract so risk gate, executor, memory writer, and attribution writer are unchanged.
- Add structured `evidence` to analyst signals so personas can reason about numbers, not just `key_factors` strings.
- Emit per-persona telemetry (`CouncilTelemetry`) so Spec 3 can mine disagreement-vs-outcome patterns.
- Provide a single `ANALYST_WEIGHTS` config knob so we can globally bias toward more reliable analyst families without rewriting prompts.

**Non-goals (deferred to other specs)**
- Round-robin debate convergence (designed for, not implemented). Spec 3.
- Stop-loss / take-profit enforcement. Spec 2 will turn `PositionThesis.target_price`/`stop_price` from dormant fields into evaluated rules. This spec keeps them populated but unused.
- Stock-agnostic signal-pattern memory and self-improvement. Spec 3.
- Per-evidence-key importance tuning. The memory loop should learn it; we don't hand-pick.
- Persona models other than Gemini Pro. Confound risk too high until the council architecture is proven.

## Architecture

The pipeline is a seven-stage `SequentialAgent`. This spec replaces stage 2 (`strategist_agent`) with a `strategist_council` block that wraps a parallel persona pool and a deterministic aggregator.

```
HourlyTick (SequentialAgent — unchanged stage count)
├── analyst_pool                       (ParallelAgent — signals gain `evidence`)
├── attribution_writer                 (unchanged — persists analyst signals only)
├── strategist_council                 (NEW — SequentialAgent, replaces strategist_agent)
│   ├── persona_pool                   (ParallelAgent)
│   │   ├── value_strategist           (LlmAgent → list[MemberStance], output_key="value_stances")
│   │   ├── momentum_strategist        (LlmAgent → list[MemberStance], output_key="momentum_stances")
│   │   └── contrarian_strategist      (LlmAgent → list[MemberStance], output_key="contrarian_stances")
│   └── council_aggregator             (BaseAgent — pure Python, no LLM)
│         reads {value,momentum,contrarian}_stances + tickers + positions
│         applies asymmetric quorum + confidence-weighted sizing
│         emits StrategistDecision   → state["strategist_decision"]
│         emits CouncilTelemetry     → state["council_telemetry"]   (session-state only)
├── risk_gate_agent                    (unchanged)
├── executor_agent                     (unchanged)
├── memory_writer                      (unchanged)
└── snapshotter                        (unchanged)
```

`council_telemetry` is written to session state but **not persisted to a database in Spec 1**. Spec 3 (signal-pattern memory) will introduce a writer + DB table once the schema needs are concrete. Until then, telemetry is ephemeral per-tick state available for in-tick logging only.

### Why a `SequentialAgent` wrapper, not two top-level pipeline nodes

Keeping `persona_pool` and `council_aggregator` inside one `strategist_council` keeps the outer pipeline conceptually unchanged ("decide" remains a single block). Round-robin debate later replaces `persona_pool` with a `DebateRunner` and the outer pipeline keeps the same shape.

## Data Contracts

### New: `MemberStance` (`src/agents/strategist/member_schema.py`)

One council member's opinion on one ticker. Each persona LlmAgent emits `list[MemberStance]`, exhaustive over the watchlist (validated by an existing-style after-agent callback that re-prompts the persona on missing tickers).

```python
class MemberStance(BaseModel):
    """One council member's per-ticker opinion."""
    ticker: str
    persona: Literal["value", "momentum", "contrarian"]   # set by aggregator from output_key, not the LLM
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)

    # Lifecycle hints — populated only when proposing to open (curr 0 → preferred >0)
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)

    # Lifecycle hints — populated only when proposing to close (curr >0 → preferred 0)
    close_reason: str | None = Field(default=None, max_length=120)
```

The `persona` field is set deterministically by the aggregator from which `*_stances` key the stance came out of, not emitted by the LLM. Saves a token, removes a failure mode where a persona could mis-tag itself.

### Updated: `AnalystSignal` (`src/agents/analysts/_common.py`)

Adds an optional structured evidence blob. Backwards compatible — `default_factory=dict`.

```python
class AnalystSignal(BaseModel):
    ticker: str
    direction: str                                       # "bullish" | "bearish" | "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list, max_length=3)
    evidence: dict[str, float | str] = Field(default_factory=dict)   # NEW
```

Per-analyst evidence keys are fixed by convention, documented in each analyst's prompt and schema docstring:

| Analyst | Evidence keys (typical) |
|---|---|
| `technical` | `rsi_14`, `macd_hist`, `volume_zscore`, `breakout_distance_pct`, `atr_pct` |
| `fundamental` | `pe`, `forward_pe`, `debt_to_equity`, `fcf_yield`, `revenue_growth_yoy` |
| `sentiment` | `avg_score`, `score_extremity`, `n_headlines`, `social_score_delta` |
| `smart_money` | `total_dollar_value`, `n_insiders`, `n_politicians`, `conviction_label` |

### `SmartMoneySignal` normalisation

`SmartMoneySignal` currently lives outside the `AnalystSignal` hierarchy. Migrate it under the base so personas can read all four signal types through one lens:

- Subclass `AnalystSignal`
- Keep `direction: Literal["bullish", "bearish"]` (override base — sparse-by-design preserved; no neutrals)
- Derive `confidence` from `conviction` + `total_dollar_value` (mapping documented in the analyst's docstring)
- Move `insiders`, `politicians`, `total_dollar_value` into `evidence` as `n_insiders`, `n_politicians`, `total_dollar_value`, `conviction_label`

### New: `CouncilTelemetry` (`src/agents/strategist/member_schema.py`)

Frozen per-tick record for AttributionWriter to persist. Spec 3's memory loop will mine this.

```python
class CouncilTelemetry(BaseModel):
    """Per-tick frozen record. Strategist itself never reads it."""
    stances: list[MemberStance]                # all 3 × |tickers| stances, flattened
    quorum_decisions: dict[str, str]           # ticker → "open"|"close"|"trim"|"add"|"hold"
    disagreement_score: dict[str, float]       # ticker → variance of preferred_weights, in [0, 0.25]
    degraded_member: str | None = None         # populated when a persona's stance was unavailable
```

### New: `ANALYST_WEIGHTS` (`src/agents/strategist/config.py`)

Single global tuning knob — declared bias toward more reliable analyst families. Rendered into every persona's prompt under an `## Analyst Reliability` section.

```python
ANALYST_WEIGHTS: dict[str, float] = {
    "technical":   1.0,
    "fundamental": 1.0,
    "sentiment":   0.7,
    "smart_money": 1.5,
}
```

Per-evidence-key importance is deliberately **not** declared here — Spec 3's memory loop should learn it from realised PnL rather than hand-pick.

## Persona Prompts

All three personas use a shared template skeleton with one replaceable `{persona_lens}` block. Everything else (analyst weights, current state, signals, output instructions) is identical.

### Shared template

```
You are the {persona_name} strategist on a 3-member trading council.

## Your Lens
{persona_lens}

## Analyst Reliability
Weight analyst signals as follows when forming your view:
{analyst_weights_table}    ← rendered from ANALYST_WEIGHTS

## Current State
Portfolio: {portfolio}
Active Positions (with current weights): {positions}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest: {day_digest}
Current Thesis: {thesis}

## Analyst Signals (with structured evidence)
Technical:    {technical_signals}
Fundamental:  {fundamental_signals}
Sentiment:    {sentiment_signals}
Smart Money:  {smart_money_signals}

## Your Job
Emit a MemberStance for EVERY watchlist ticker: {tickers}.
- preferred_weight ∈ [0,1]: your ideal portfolio weight for this ticker next tick
- conviction ∈ [0,1]: how strongly you hold this view
- rationale: ≤140 chars
- If proposing to open (current 0 → preferred >0): include horizon, target_price, stop_price, optional catalyst.
- If proposing to close (current >0 → preferred 0): include close_reason.

Output: list[MemberStance], exhaustive over the watchlist.
```

### The three lenses (`personas.py`)

```python
VALUE_LENS = """
You are a value investor in the Buffett/Graham tradition. You buy quality
businesses trading below intrinsic value and ignore short-term price noise.
You favour: low PE, healthy free-cash-flow yield, low debt-to-equity, durable
revenue growth, and management with skin in the game.
You are skeptical of: hype-driven rallies, momentum without earnings support,
sentiment swings, technical breakouts.
Your default is to size into undervalued names and sit on cash when nothing
qualifies. You prefer fewer, more concentrated bets you understand deeply.
You hold positions through volatility unless the underlying thesis breaks.
"""

MOMENTUM_LENS = """
You are a momentum trader. You ride trends — buy strength, sell weakness —
and trust the tape over the story. You favour: positive macd histogram,
volume confirmation on breakouts, RSI in 50–70 range (trending but not extreme),
strong relative-strength vs SPY.
You are skeptical of: cheap-looking value traps, contrarian "it'll come back"
arguments, fundamental theses that ignore current price action.
You exit fast when momentum breaks. You don't argue with the market.
You'll size up when multiple technical signals align; you'll go to zero
when the trend rolls over, regardless of fundamentals.
"""

CONTRARIAN_LENS = """
You are a contrarian. You fade extremes — buy panic, sell euphoria — and look
for setups where the consensus is wrong. You favour: high score_extremity on
the wrong side, RSI < 30 or > 70, smart-money buying when retail is selling
(or vice versa), insider activity diverging from price action.
You are skeptical of: trend-chasing, "this time is different" narratives,
crowded longs, anything where the news cycle and price are pointing the same way.
You size up when sentiment is one-sided and smart money is on the other side.
You'll cut a position if the contrarian setup resolves (extremes mean-revert)
even if you haven't fully realised the upside.
"""
```

## Model Choice

All three personas use `gemini-2.0-pro-001`. Same model the current single strategist uses. Three Pro calls in parallel per tick → ~21 Pro calls per market day at hourly cadence — negligible at paper-trading scale.

Reasoning over disagreement (each persona reads three other voices' analyst signals plus non-trivial state and synthesises) is exactly where Pro outperforms Flash. Mixing models would confound evaluation: if a persona underperforms its peers, we wouldn't know if it's the lens or the model.

**Quota fallback:** each persona's LlmAgent falls back to `gemini-2.0-flash-001` on a 429. Persona-local — a single 429 doesn't crash the tick. Telemetry records when a fallback occurred.

## Persona Memory

Each tick is independent. Personas see the shared `memory_buffer`, `day_digest`, and `thesis` (same as the current strategist), but **not** their own or each other's prior `MemberStance` records. Cross-tick persona state belongs in Spec 3.

## CouncilAggregator Policy

Pure Python `BaseAgent`, no LLM, fully unit-testable without network.

### Inputs (from session state)
- `tickers: list[str]` (the watchlist — exhaustive)
- `value_stances`, `momentum_stances`, `contrarian_stances` (each `list[MemberStance]`, exhaustive over watchlist)
- `positions: dict[str, float]` (current ticker → current weight, where `>0` means held)

### Outputs (written to session state)
- `strategist_decision: StrategistDecision` (existing schema; downstream unchanged)
- `council_telemetry: CouncilTelemetry`

### Per-ticker algorithm

```python
def aggregate(stances_by_persona, tickers, positions):
    final_weights, new_positions, close_reasons = {}, {}, {}
    quorum_decisions, disagreement = {}, {}

    for ticker in tickers:
        members = [stances_by_persona[p].by_ticker[ticker] for p in PERSONAS]   # 3 stances
        curr = positions.get(ticker, 0.0)

        prefs = [clamp(m.preferred_weight, 0.0, MAX_PER_TICKER_HINT) for m in members]
        convs = [m.conviction for m in members]

        proposes_open  = sum(1 for p in prefs if p > OPEN_EPSILON)
        proposes_close = sum(1 for p in prefs if p < CLOSE_EPSILON)

        if curr <= CLOSE_EPSILON:                        # currently flat
            if proposes_open >= effective_open_quorum(n_available):
                final = confidence_weighted_avg(prefs, convs)
                quorum_decisions[ticker] = "open"
                new_positions[ticker] = build_thesis_from_proposers(
                    members, ticker, tick_context
                )
            else:
                final = 0.0
                quorum_decisions[ticker] = "hold"
        else:                                            # currently held
            if proposes_close >= CLOSE_QUORUM:
                final = 0.0
                quorum_decisions[ticker] = "close"
                close_reasons[ticker] = first_close_reason(members)
            else:
                final = confidence_weighted_avg(prefs, convs)
                delta = final - curr
                if abs(delta) < SIZE_CHANGE_EPSILON:
                    quorum_decisions[ticker] = "hold"
                elif delta < 0:
                    quorum_decisions[ticker] = "trim"   # partial reduction; no close_reason required
                else:
                    quorum_decisions[ticker] = "add"

        final_weights[ticker] = final
        disagreement[ticker] = variance(prefs)
```

### Sizing — `confidence_weighted_avg`

Average of `preferred_weights` weighted by `conviction`. A dissenter who proposes 0 still contributes their 0 — their dissent dilutes the position size. That's intentional: position size *embeds* the disagreement.

```python
def confidence_weighted_avg(prefs, convs):
    total = sum(convs)
    if total == 0:
        return sum(prefs) / len(prefs)               # fallback: simple mean
    return sum(p * c for p, c in zip(prefs, convs)) / total
```

### Thesis construction (`build_thesis_from_proposers`)

When opening, only members with `preferred_weight > OPEN_EPSILON` are "proposers". The PositionThesis is built from them with **most-conservative defaults**:

| Field | Rule |
|---|---|
| `rationale` | Multi-voice concatenation, capped to 400 chars: `"V: {v_rat} \| M: {m_rat} \| C: {c_rat}"` (skip non-proposers) |
| `horizon` | Shortest-horizon proposer wins (`intraday < swing < long_term`) |
| `target_price` | Minimum non-null target across proposers (most conservative upside) |
| `stop_price` | Maximum non-null stop across proposers (tightest stop) |
| `catalyst` | First non-null catalyst in V→M→C order |
| `opened_tag`, `opened_at`, `opened_price`, `last_reviewed_at`, `last_review_note` | Filled by aggregator from tick context |

Spec 2 will revisit how `target_price` / `stop_price` get evaluated and updated; for Spec 1 they remain populated but unused at runtime.

### Close reason

When any member triggers close, take that member's `close_reason`. If multiple members close, concatenate as `"V: {…} | M: {…}"` capped at 120 chars.

### Remaining `StrategistDecision` fields

| Field | Source |
|---|---|
| `target_weights` | `final_weights` (computed above) |
| `decision_tag` | derived: `"council_{open_count}o_{close_count}c_{trim_count}t_{add_count}a"` |
| `reasoning` | `"council: {n_opens} opens, {n_closes} closes, {n_trims} trims, {n_adds} adds; mean disagreement {x:.2f}"`, capped at 300 |
| `updated_thesis` | preserved unchanged from prior tick's `state["thesis"]`. Spec 3 will own thesis evolution once the memory loop has telemetry to do it sensibly; for Spec 1 the council does not rewrite the global thesis |
| `confidence` | mean of `m.conviction` across stances on tickers where `quorum_decisions[ticker] != "hold"`; defaults to 0 if no actions |
| `new_positions`, `close_reasons` | computed above |

### Constants (`aggregator.py`)

```python
OPEN_QUORUM = 2          # of 3 personas must propose >0 to open
CLOSE_QUORUM = 1         # any persona triggers close
OPEN_EPSILON = 0.005
CLOSE_EPSILON = 0.005
SIZE_CHANGE_EPSILON = 0.02
MAX_PER_TICKER_HINT = 0.30   # defensive clamp; risk gate is the actual enforcer
PERSONAS = ("value", "momentum", "contrarian")

# Degraded mode — see "Failure modes" table below.
# n_available = number of personas whose stance is present this tick.
def effective_open_quorum(n_available: int) -> int:
    if n_available == 3: return OPEN_QUORUM      # 2 of 3 (normal)
    if n_available == 2: return 2                # both remaining must agree
    return 99                                    # n_available <= 1 → opens are blocked
```

## Pipeline Wiring

Single import + sub_agents change in `src/orchestrator/pipeline.py`:

```python
from agents.strategist.council import strategist_council   # was: from agents.strategist.agent import strategist_agent

SequentialAgent(name="HourlyTick", sub_agents=[
    analyst_pool,
    attribution_writer,
    strategist_council,              # was: strategist_agent (position 2)
    risk_gate_agent,
    executor_agent,
    memory_writer,
    snapshotter,
])
```

The pipeline keeps its 7-stage structure; only the position-2 sub_agent reference changes.

## File Layout

```
src/agents/strategist/
├── __init__.py            # exports strategist_council (only public symbol)
├── council.py             # strategist_council = SequentialAgent(persona_pool, aggregator)
├── personas.py            # VALUE_LENS, MOMENTUM_LENS, CONTRARIAN_LENS,
│                          # value_strategist / momentum_strategist / contrarian_strategist (LlmAgents),
│                          # persona_pool (ParallelAgent)
├── aggregator.py          # CouncilAggregator(BaseAgent), constants, helpers,
│                          # inline lifecycle/exhaustive validation
├── member_schema.py       # MemberStance, CouncilTelemetry
├── config.py              # ANALYST_WEIGHTS
├── prompts.py             # COUNCIL_PROMPT_TEMPLATE + render_persona_prompt(lens)
└── schema.py              # UNCHANGED — StrategistDecision, PositionThesis
```

**Touched outside `strategist/`:**
- `src/agents/analysts/_common.py` — `AnalystSignal.evidence`
- `src/agents/analysts/smart_money/schema.py` — subclass `AnalystSignal`; migrate fields into `evidence`
- `src/agents/analysts/{technical,fundamental,sentiment,smart_money}/agent.py` and prompt files — populate `evidence` per analyst's documented key set
- `src/orchestrator/pipeline.py` — replace position-2 import + sub_agent: `_build_strategist()` → `strategist_council`

**Deleted:**
- `src/agents/strategist/agent.py` — legacy single strategist; never used in production, no replay need
- Legacy `STRATEGIST_INSTRUCTION` from `prompts.py`

## State Contract

| Key | Producer | Consumer | Notes |
|---|---|---|---|
| `tickers`, `portfolio`, `positions`, `memory_buffer`, `day_digest`, `thesis` | upstream / persisted | personas | unchanged |
| `technical_signals`, `fundamental_signals`, `sentiment_signals`, `smart_money_signals` | analyst_pool | personas | now carry `evidence` |
| `value_stances`, `momentum_stances`, `contrarian_stances` | each persona LlmAgent (`output_key`) | CouncilAggregator | **new — ephemeral**, overwritten each tick |
| `strategist_decision` | CouncilAggregator | risk_gate, memory_writer | **producer changed**; schema unchanged |
| `council_telemetry` | CouncilAggregator | (none in Spec 1; available for in-tick logging) | **new — session-state only**; persistence deferred to Spec 3 |

## Validation & Failure Modes

### Two layers of validation

1. **Per-persona exhaustive validator** — each persona LlmAgent has `make_exhaustive_validator("value_stances")` (etc.) as its `after_agent_callback`. Re-prompts the *individual persona* if it missed a watchlist ticker. Persona-local — does not re-run the other two.

2. **Council validator (in CouncilAggregator)** — exhaustive weights check + lifecycle contract via `validate_lifecycle_contract`. Runs on the assembled `StrategistDecision` *before* it is written to state. Failure raises `StrategistContractViolation` immediately (no re-prompt) — the deterministic aggregator should never produce invalid output, so a violation is a bug, not a runtime expectation.

### Failure modes

| Failure | Behaviour |
|---|---|
| One persona LLM 429s | persona-local fallback to Flash; continue |
| One persona LLM hard-fails after fallback | aggregator runs with 2 stances; `effective_open_quorum(2) = 2` (both remaining must agree to open); close-by-any-trigger preserved; `disagreement` computed over 2 values; `council_telemetry.degraded_member` flagged |
| Two personas hard-fail | aggregator runs with 1 stance; **opens and upward adds are blocked entirely** (`effective_open_quorum(1) = 99` is unreachable); existing positions can still be held, trimmed, or closed by the surviving member; `council_telemetry.degraded_member` flagged. Operationally a major incident — monitoring should alert |
| Three personas hard-fail | aggregator raises `CouncilStanceUnavailable`; tick fails the same way an LLM strategist failure would today |
| Aggregator validation contract violation | raises `StrategistContractViolation`; tick fails fast |

The "raise the bar when a member is missing" rule is the cautious choice. With one member missing we require unanimity from the remaining two; with two members missing we refuse to open new positions at all. We can only ever loosen risk on degraded-mode capacity; we never tighten the bot's commitments based on a single voice.

## Testing Strategy

Mirrors the repo's Tier 1 (no-LLM) / Tier 2 (LLM) convention.

### Tier 1 — pure unit tests, no network, no LLM (mandatory)

| File (new) | Coverage |
|---|---|
| `tests/unit/strategist/test_member_schema.py` | `MemberStance` field constraints; `CouncilTelemetry` round-trip |
| `tests/unit/strategist/test_analyst_evidence.py` | `AnalystSignal.evidence` defaults to `{}`; existing fixtures still validate; `SmartMoneySignal` migration |
| `tests/unit/strategist/test_aggregator_quorum.py` | Each transition: flat→flat, flat→held (3-of-3), flat→held (2-of-3 dissent), flat→held (1-of-3 quorum miss), held→flat (any-trigger), held→held (hold), held→held (trim), held→held (add) |
| `tests/unit/strategist/test_aggregator_sizing.py` | `confidence_weighted_avg`: weighting, all-zero-conviction fallback, dissent dilution, `MAX_PER_TICKER_HINT` clamp |
| `tests/unit/strategist/test_aggregator_thesis.py` | `build_thesis_from_proposers`: shortest-horizon, min target, max stop, V→M→C catalyst order, dissenter excluded |
| `tests/unit/strategist/test_aggregator_validation.py` | `StrategistContractViolation` raised on missing tickers / off-watchlist tickers / lifecycle contract breaks |
| `tests/unit/strategist/test_aggregator_degraded.py` | One/two/three personas missing; `CouncilStanceUnavailable` on three; `degraded_member` flagged on one or two |
| `tests/unit/strategist/test_prompts.py` | `render_persona_prompt(VALUE_LENS, state)` interpolates `ANALYST_WEIGHTS`; lens slot fills correctly; missing state keys raise clearly |
| `tests/unit/strategist/test_council_telemetry.py` | `disagreement_score` ∈ [0, 0.25]; `quorum_decisions` populated for every ticker |
| `tests/unit/strategist/test_pipeline_wiring.py` | `build_pipeline()` includes `strategist_council` in correct position; sub_agents structure validated |

### Tier 1 fixtures (`tests/fixtures/council/`)

- `three_persona_stances_consensus.json` — all 3 propose opening AAPL
- `three_persona_stances_split.json` — 2 propose open, 1 dissents at 0
- `three_persona_stances_quorum_miss.json` — 1 proposes open, 2 abstain
- `three_persona_stances_close_trigger.json` — 1 proposes close on a held position
- `analyst_signals_with_evidence.json` — full bundle with `evidence` populated for each analyst type

### Tier 2 — LLM-touching integration (gated, on-demand)

| File | Coverage |
|---|---|
| `tests/integration/test_persona_smoke.py` | Each persona LlmAgent emits parseable `list[MemberStance]`, exhaustive over a small watchlist, non-empty rationale |
| `tests/integration/test_council_smoke.py` | Full `strategist_council` runs end-to-end with `FakeBroker` fixtures; produces valid `StrategistDecision`; aggregator validation passes |
| `scripts/smoke_run.py` (existing) | 3-tick paper run continues to work; will exercise the council on real LLMs |

### Not tested (intentional)

- Persona "personality fidelity" (does the value persona think like a value investor?) — subjective; defer to Spec 3 telemetry analysis on real disagreement-vs-PnL data
- LLM cost per tick — ops concern; tracked in `docs/performance/` after first paper-trading week, not in CI
- Round-robin debate — Spec 3
- `target_price` / `stop_price` enforcement — Spec 2

## Implementation Order (for the implementation plan that follows)

1. `MemberStance` + `CouncilTelemetry` schemas — tests, then models
2. `AnalystSignal.evidence` + `SmartMoneySignal` migration — tests, then model changes, then update analyst prompts to populate evidence
3. `ANALYST_WEIGHTS` config + `render_persona_prompt` — tests, then config + prompts.py
4. Persona LlmAgents + `persona_pool` — Tier 2 smoke tests, then `personas.py`
5. `CouncilAggregator` — Tier 1 tests for each transition + validation, then `aggregator.py`
6. `strategist_council` SequentialAgent + pipeline wiring — integration test, then wire it
7. Delete legacy `agent.py`, retarget existing prompt tests, full suite green

## Open Questions Deferred to Later Specs

- **Spec 2 — Exit floor / ceiling rules.** How are `target_price` / `stop_price` evaluated? Are they sticky once set, or revisable per tick? Does breaching a stop force a close, or just escalate to a council vote? Should the council see these levels as inputs?
- **Spec 3 — Stock-agnostic signal-pattern memory & self-improvement.** What gets persisted alongside `CouncilTelemetry` to support pattern recall? How is recall "stock-agnostic" — what's the lookup key (signal-shape clusters, embedding of the analyst signal vector + outcome)? When does round-robin debate convergence get switched on, and what does it gate on?
