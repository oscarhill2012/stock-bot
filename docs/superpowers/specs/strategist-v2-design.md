# Strategist v2 Design

**Status:** Spec — awaiting implementation plan
**Supersedes:** `strategist-council-design.md`, `exit-rules-and-telemetry-design.md` (their salvageable parts are folded in here; the council architecture is dropped — see "Why we dropped the council" below).

## Roadmap context

This spec is the first of three planned strategist improvements:

1. **Strategist v2** *(this spec)* — single agent, enriched prompt with held-position context, per-ticker stance output, persistence substrate.
2. **Analyst → Strategist Contract** *(future, separate brainstorm)* — structured `evidence` numerics on `AnalystSignal`, `ANALYST_WEIGHTS` knob, `SmartMoneySignal` normalisation.
3. **Self-improvement / knowledge base** *(future, decomposition brainstorm first)* — outcome-driven learning. The persisted `TickerStanceRow` + `TradeLogRow` outcome FKs introduced here are the substrate.

## Problem

Three coupled gaps in the current strategist tier:

1. **The strategist has no per-position memory in its prompt.** `state["positions"]` already carries the per-ticker `PositionThesis` (rationale, target_price, stop_price, opened_price, horizon, catalyst — populated when the position opened). But the prompt dumps it as `Active Positions: {positions}` — an unstructured `str(dict)` that the model has to fight to read. There's no rendered "AAPL: opened $192 with target $210 / stop $185, currently $198" view.
2. **The output is too coarse to learn from.** `StrategistDecision` emits a flat `target_weights` dict + one global `reasoning` string. Per-ticker rationale is not captured. Trim/add lifecycle decisions are squashed into "size_change" with no first-class category. The persisted record (currently nothing — strategist decisions aren't persisted at all) is too thin for any future outcome-attribution loop.
3. **Trades and decisions cannot be joined.** `TradeLogRow` has no link back to the tick that opened or closed the position. Without those keys, a future learner can't attribute a closed-trade outcome to the deliberation that produced it.

## Goals

- Render per-held-ticker thesis + live state into the strategist prompt as a structured "Held Positions" block.
- Replace flat `target_weights` with per-ticker `TickerStance` output (rationale, conviction, lifecycle hints — all per ticker).
- Make trim/add first-class lifecycle actions alongside open/close/hold.
- Persist `TickerStanceRow` (one row × ticker × tick) — the substrate for Goal 3.
- Add `opening_tick_id` / `closing_tick_id` FKs to `TradeLogRow` so closed-trade outcomes can be joined back to decisions.
- Preserve the downstream contract: `risk_gate`, `executor`, `memory_writer` continue to read `target_weights`, `new_positions`, `close_reasons` from `StrategistDecision` unchanged. Those fields become *derived* in the strategist's after-agent callback from the per-ticker stances.

## Non-goals (deferred)

- **Three-persona council architecture** — dropped (see "Why we dropped the council").
- **Structured `AnalystSignal.evidence`, `ANALYST_WEIGHTS`, `SmartMoneySignal` normalisation** — deferred to the *Analyst → Strategist Contract* brainstorm (Goal 2). Strategist v2 reads analyst signals in their existing shape.
- **Full `PositionPack`** — `running_max_price`, `max_run_up_pct`, `max_drawdown_pct`, `distance_to_target_pct`, `distance_to_stop_pct`, `target_reached`/`stop_breached` booleans, SPY-relative excess return. The minimal "Held Positions" view (thesis fields + a few live numbers) is enough for the floor confirmed in brainstorming. Richer pack is a follow-up spec.
- **History injection** — strategist reading its own past `TickerStance` rows from the DB. That's Goal 3 territory.
- **Self-improvement learning loop itself** — Goal 3.
- **Sub-tick exits, trailing stops, target ratchet** — backlog S3, S4.
- **Persisting `risk_clamps_applied`** — backlog S8.
- **Persona model diversity** — backlog S7.

## Why we dropped the council

- Three Gemini-Pro instances reading the same data with different prompt lenses are not three independent reasoners. They share priors, training-data biases, and correlated failure modes. "Disagreement" measures model uncertainty, not a real bull/bear/contrarian debate.
- Without paper-trading data on the single-strategist baseline, we cannot show the council is better; only assert it should be. Optimising an unmeasured baseline.
- The council's primary value (per-stance telemetry as substrate) is mostly recoverable from per-ticker `TickerStance` output of *one* agent.
- 3× Pro calls per tick for unproven benefit.
- Pluggable convergence + round-robin debate hooks were YAGNI scaffolding for an unspecced future feature.

## Architecture

Pipeline gains one new stage (`strategist_decision_writer`) — was 7 stages, now 8. The strategist itself stays a single `LlmAgent` at the same logical position.

```
HourlyTick (SequentialAgent — 8 stages)
├── analyst_pool                        unchanged
├── attribution_writer                  unchanged
├── strategist_agent                    MODIFIED — single LlmAgent, new prompt + new output schema
├── strategist_decision_writer          NEW    — persists TickerStanceRow per ticker
├── risk_gate_agent                     unchanged
├── executor_agent                      MODIFIED — populates state["positions"][ticker] on open;
│                                                  populates TradeLogRow.opening/closing_tick_id
├── memory_writer                       unchanged
└── snapshotter                         unchanged
```

`strategist_decision_writer` runs *before* `risk_gate_agent` so the council's intent is recorded even if `risk_gate` raises a contract violation. Mirrors the existing `attribution_writer` pattern (writes happen before downstream gates).

## Data Contracts

### New: `TickerStance` (`src/agents/strategist/stance_schema.py`)

The strategist's per-ticker decision and rationale. Emitted as `list[TickerStance]`, exhaustive over the watchlist.

```python
class TickerStance(BaseModel):
    """Strategist's per-ticker decision for one tick."""
    ticker: str
    preferred_weight: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=140)

    # Lifecycle hints — populated only on the matching transition; null otherwise.
    horizon: Literal["intraday", "swing", "long_term"] | None = None
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=80)
    close_reason: str | None = Field(default=None, max_length=120)
    trim_reason: str | None = Field(default=None, max_length=120)
```

The `horizon`, `target_price`, `stop_price`, `catalyst` fields are required when the stance opens a position (current weight ≈ 0 → preferred > 0). `close_reason` is required when closing. `trim_reason` is required when trimming. The validator (after-agent callback) re-prompts if these are missing.

### Modified: `StrategistDecision` (`src/agents/strategist/schema.py`)

Now carries `stances` plus a derived view of the legacy fields downstream consumers already validate against.

```python
class StrategistDecision(BaseModel):
    # NEW — primary content
    stances: list[TickerStance]

    # Existing global fields
    decision_tag: str
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)

    # Derived from stances by the after-agent callback (kept on the model so risk_gate
    # / executor / memory_writer don't need to change). The strategist LLM does NOT
    # emit these directly — they are populated server-side.
    target_weights: dict[str, float] = Field(default_factory=dict)
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    close_reasons: dict[str, str] = Field(default_factory=dict)
    trim_reasons: dict[str, str] = Field(default_factory=dict)   # NEW
```

The LLM's `output_schema` only requires it to emit `stances`, `decision_tag`, `reasoning`, `updated_thesis`, `confidence`. The four derived fields are filled by `derive_legacy_fields(stances, current_positions, tick_context)` in the after-agent callback.

### Modified: `PositionThesis` — one new field

```python
class PositionThesis(BaseModel):
    # ... existing fields unchanged ...
    opened_tick_id: str = ""    # NEW — for outcome attribution; populated when the thesis is built on open
```

Populated by the strategist's `derive_legacy_fields` when constructing a new `PositionThesis` from an "open" stance. Read by the executor when it writes `TradeLogRow.opening_tick_id` / `closing_tick_id`.

### State shape — `state["positions"]`

This spec treats `state["positions"]: dict[ticker, PositionThesis_dict]` as the canonical store of held positions across ticks (matches what `executor.agent.py` already does on close). The implementation plan must verify and, if necessary, add the BUY-side write so that opening a position writes the thesis dict back to `state["positions"][ticker]`. Removed on close (already done).

`PositionThesis` carries `opened_at`, `opened_price`, `rationale`, `horizon`, `target_price`, `stop_price`, `catalyst`, `opened_tag`, `last_review_note` — the floor of "what did we buy and why" the strategist needs.

### New ORM: `TickerStanceRow` (`src/orchestrator/persistence.py`)

```python
class TickerStanceRow(Base):
    __tablename__ = "ticker_stances"
    id: Mapped[int]                = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]           = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]  = mapped_column(DateTime)
    ticker: Mapped[str]            = mapped_column(String, index=True)
    preferred_weight: Mapped[float]    = mapped_column(Float)
    conviction: Mapped[float]      = mapped_column(Float)
    rationale: Mapped[str]         = mapped_column(String)
    horizon: Mapped[str | None]        = mapped_column(String, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    catalyst: Mapped[str | None]   = mapped_column(String, nullable=True)
    close_reason: Mapped[str | None]   = mapped_column(String, nullable=True)
    trim_reason: Mapped[str | None]    = mapped_column(String, nullable=True)
    lifecycle_action: Mapped[str]  = mapped_column(String, index=True)   # open|close|trim|add|hold
    decision_tag: Mapped[str]      = mapped_column(String, index=True)   # tick-level tag, denormalised onto each row
```

`lifecycle_action` and `decision_tag` are denormalised onto every stance row so the table can be queried directly without joining. Storage cost is negligible (~5 rows/tick × ~7 ticks/day = ~35 rows/day for a 5-ticker watchlist).

### Modified: `TradeLogRow` — outcome FKs

```python
# Add to existing TradeLogRow:
opening_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
closing_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
```

Populated by the executor when it writes the trade-log entry on close: `opening_tick_id` from the thesis's `opened_tick_id`, `closing_tick_id` from the current `state["tick_id"]`. Nullable for backwards compat with any pre-spec rows.

## Prompt Template

`STRATEGIST_INSTRUCTION` in `src/agents/strategist/prompts.py` gains a "Held Positions" block between "Current State" and "Analyst Signals", and a per-ticker output instruction.

```
You are the portfolio strategist for an algorithmic trading bot. You decide a per-ticker
stance for the next trading hour.

## Current State
Portfolio: {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest: {day_digest}
Current Thesis: {thesis}

## Held Positions (your prior decisions)              ← NEW
{held_positions_view}

## Analyst Signals
Technical:    {technical_signals}
Fundamental:  {fundamental_signals}
Sentiment:    {sentiment_signals}
Smart Money:  {smart_money_signals}

## Smart Money Bias Instruction
[unchanged from current prompt]

## Your Job
Emit a TickerStance for EVERY watchlist ticker: {tickers}.

Per stance:
- preferred_weight ∈ [0,1]: your ideal portfolio weight next tick
- conviction ∈ [0,1]: how strongly you hold this view
- rationale: ≤140 chars, why
- If proposing to OPEN (current 0 → preferred >0): include horizon, target_price, stop_price; catalyst optional.
- If proposing to CLOSE (current >0 → preferred 0): include close_reason.
- If proposing to TRIM (current >MIN_HELD_WEIGHT → preferred lower but ≥MIN_HELD_WEIGHT): include trim_reason.
- If holding or adding: lifecycle hint fields stay null.

Also emit:
- decision_tag (snake_case, ≤40 chars): this tick's headline decision
- reasoning (≤300 chars): overall summary across all stances
- updated_thesis (≤500 chars): working hypothesis for next tick
- confidence ∈ [0,1]: overall conviction in this tick's plan
```

The line `Active Positions: {positions}` is removed — its content is now rendered by `held_positions_view`.

### `held_positions_view` rendering

Built by a `before_agent_callback` on the strategist that reads `state["positions"]` (the per-ticker thesis dicts) and a current-price lookup, and writes a single string into `state["held_positions_view"]`.

Format per holding (one block per held ticker, separated by blank lines):

```
AAPL
  Opened:    2026-04-22 14:00 at $192.40, weight 0.080
  Why:       insider buying + FCF yield 6.2%
  Aim:       target $210.00 (+9.1% from open)  |  stop $185.00 (-3.9% from open)
  Horizon:   swing
  Catalyst:  Q3 earnings 11/01
  Now:       $198.50  |  weight 0.078  |  +3.2% unrealised
```

Empty case: `"(No held positions — portfolio is flat.)"`.

Missing-data fallbacks:
- No current price available for a ticker → render `Now: (price unavailable)`. Strategist still has thesis context.
- No `target_price` / `stop_price` on thesis → render `Aim: (none set at open)`.
- No `catalyst` → omit the Catalyst line.

**Source of current price + current weight:** `state["portfolio"]` (a `broker.portfolio.Portfolio` instance, possibly serialised as dict). Per `src/broker/portfolio.py`:
- `Portfolio.positions[ticker].last_price` → current price
- `Portfolio.current_weights()` → `dict[ticker, float]` of current portfolio fractions

The renderer reads from `state["portfolio"]` directly. No new state key needed; no extra provider call.

## Lifecycle Derivation

`derive_lifecycle_action(curr_weight, preferred_weight)` in `src/agents/strategist/lifecycle.py`:

```python
from orchestrator.state import MIN_HELD_WEIGHT  # existing constant: 0.001

OPEN_EPSILON        = 0.005   # NEW — strategist-specific
SIZE_CHANGE_EPSILON = 0.02    # NEW — strategist-specific

def derive_lifecycle_action(curr: float, pref: float) -> str:
    if curr < MIN_HELD_WEIGHT:
        return "open" if pref > OPEN_EPSILON else "hold"
    # currently held
    if pref < MIN_HELD_WEIGHT:
        return "close"
    delta = pref - curr
    if delta < -SIZE_CHANGE_EPSILON:
        return "trim"
    if delta > SIZE_CHANGE_EPSILON:
        return "add"
    return "hold"
```

`derive_legacy_fields(stances, current_positions, tick_context)` in `src/agents/strategist/derivation.py`:

- `target_weights[ticker] = stance.preferred_weight` for every stance.
- For "open" stances: build a `PositionThesis` (rationale from stance, target_price/stop_price/horizon/catalyst from stance, opened_at = `tick_context.now`, opened_price = `tick_context.current_prices[ticker]`, opened_tag = `decision_tag`, opened_tick_id = `tick_context.tick_id`, last_reviewed_at = now, last_review_note = ""); add to `new_positions[ticker]`.
- For "close" stances: `close_reasons[ticker] = stance.close_reason`.
- For "trim" stances: `trim_reasons[ticker] = stance.trim_reason`.

## Validation (after-agent callback)

Re-prompts on:
- Missing watchlist tickers (existing pattern, retained).
- Off-watchlist tickers (existing pattern, retained).
- "open" lifecycle action with any of `horizon` / `target_price` / `stop_price` missing.
- "close" lifecycle action with `close_reason` missing.
- "trim" lifecycle action with `trim_reason` missing.

Hard-fails (raise `StrategistContractViolation`, no re-prompt):
- A held position dropping below `MIN_HELD_WEIGHT` without a "close" lifecycle action — i.e. the strategist tried to close-by-arithmetic without an explicit close stance. Forces the strategist to be intentional.
- Existing `validate_lifecycle_contract` checks (run on the derived legacy fields).

## State Contract

| Key | Producer | Consumer | Notes |
|---|---|---|---|
| `positions: dict[str, PositionThesis_dict]` | executor (open ⇒ write; close ⇒ remove) | strategist `before_agent_callback`; `decision_writer`; existing consumers | **MODIFIED** — formalises the existing convention; spec requires the BUY-side write to be present |
| `held_positions_view: str` | strategist `before_agent_callback` | strategist prompt | **NEW** — rendered string for prompt slot |
| `portfolio: Portfolio` (existing) | broker | strategist `before_agent_callback` (read for current_price + current_weight); `derivation` (read for opened_price on new opens) | already populated; no producer changes |
| `strategist_decision: StrategistDecision` | strategist `after_agent_callback` (after derivation) | risk_gate, decision_writer, memory_writer | **MODIFIED** — now contains `stances` + `trim_reasons`; legacy fields preserved |
| existing keys (portfolio, memory_buffer, day_digest, thesis, *_signals, tickers, tick_id) | unchanged | unchanged | unchanged |

## File Layout

```
src/agents/strategist/
├── __init__.py                  unchanged exports (strategist_agent)
├── agent.py                     MODIFIED — same exported symbol; rewritten internally with before/after callbacks
├── prompts.py                   MODIFIED — Held Positions block + per-stance output rules
├── schema.py                    MODIFIED — StrategistDecision gains stances + trim_reasons; PositionThesis gains opened_tick_id
├── stance_schema.py             NEW      — TickerStance
├── lifecycle.py                 NEW      — derive_lifecycle_action; MIN_HELD_WEIGHT, OPEN_EPSILON, SIZE_CHANGE_EPSILON
├── derivation.py                NEW      — derive_legacy_fields(stances, positions, tick_context)
├── held_view.py                 NEW      — render_held_positions_view(positions, current_prices)
└── decision_writer.py           NEW      — strategist_decision_writer (BaseAgent)

src/orchestrator/
├── persistence.py               MODIFIED — TickerStanceRow + TradeLogRow.opening_tick_id/closing_tick_id
└── pipeline.py                  MODIFIED — wires strategist_decision_writer

src/agents/executor/
└── agent.py                     MODIFIED — BUY branch writes state["positions"][ticker] = thesis_dict;
                                            SELL branch populates trade-log opening_tick_id / closing_tick_id
```

**Removed (`docs/`):** `docs/superpowers/plans/strategist-council.md` and `docs/superpowers/plans/exit-rules-and-telemetry.md` — both never executed. Deleted in the cleanup phase.

**Marked superseded (kept on disk):** `docs/superpowers/specs/strategist-council-design.md` and `docs/superpowers/specs/exit-rules-and-telemetry-design.md` — kept for one cycle so the salvage trail is visible. Each gets a "Superseded by strategist-v2-design.md" header.

**Removed (`src/`):** none.

## Failure Modes

| Failure | Behaviour |
|---|---|
| `current_prices` missing for a held ticker | render `Now: (price unavailable)`; strategist proceeds with thesis context |
| `state["positions"]` empty (no holdings) | render `(No held positions — portfolio is flat.)` so the prompt slot is always non-empty |
| Strategist emits non-exhaustive stances | `after_agent_callback` re-prompts (existing pattern) |
| Strategist emits "open" without required hints | `after_agent_callback` re-prompts naming the missing field |
| Strategist arithmetic-closes a position (held → <MIN_HELD_WEIGHT, no `close_reason`) | `StrategistContractViolation` raised — fail fast; surfaces a prompt bug |
| `TickerStanceRow` DB insert fails | log error; do not block tick (telemetry is observability, not contract) |
| Executor opens but `state["tick_id"]` missing | use `state["recorded_at"]` ISO string as fallback identifier; warn |

## Testing Strategy

Mirrors the repo's Tier 1 (no-LLM) / Tier 2 (LLM) convention.

### Tier 1 — pure unit tests, mandatory

| File (new) | Coverage |
|---|---|
| `tests/unit/strategist/test_stance_schema.py` | `TickerStance` field constraints; lifecycle hint optionality; rationale/close_reason/trim_reason length caps |
| `tests/unit/strategist/test_lifecycle_derivation.py` | `derive_lifecycle_action`: each transition (flat→open, flat→hold, held→close, held→trim, held→add, held→hold) including epsilon edges |
| `tests/unit/strategist/test_derivation.py` | `derive_legacy_fields`: `target_weights` per stance; `new_positions` built only for "open" stances with hints populated; `close_reasons` / `trim_reasons` populated correctly; `opened_tick_id` carried into thesis |
| `tests/unit/strategist/test_held_view.py` | `render_held_positions_view`: format matches spec; empty positions → "(No held positions...)"; missing current_price → "(price unavailable)"; missing target/stop → "(none set at open)"; P&L math; multiple holdings rendered with blank-line separator |
| `tests/unit/strategist/test_strategist_validation.py` | `after_agent_callback` re-prompts on: missing tickers, extras, "open" without horizon/target/stop, "close" without close_reason, "trim" without trim_reason; passes on valid input; raises on arithmetic-close |
| `tests/unit/strategist/test_decision_writer.py` | `strategist_decision_writer` produces 1 row per ticker; `lifecycle_action` populated correctly; null lifecycle hint fields persist as NULL; `decision_tag` denormalised correctly |
| `tests/unit/orchestrator/test_persistence_strategist.py` | `TickerStanceRow` round-trip; `TradeLogRow.opening_tick_id` / `closing_tick_id` columns work; query joining `TradeLogRow.opening_tick_id` to `TickerStanceRow.tick_id` returns expected rows |
| `tests/unit/strategist/test_prompts_v2.py` | Held Positions block renders with `held_positions_view` slot; existing prompt slots still fill; per-stance output rules present |
| `tests/unit/orchestrator/test_pipeline_wiring_v2.py` | `strategist_decision_writer` in correct pipeline position (between strategist and risk_gate) |
| `tests/unit/executor/test_open_positions_state.py` | Executor BUY populates `state["positions"][ticker]` with thesis dict including `opened_tick_id`; SELL removes from positions and populates `TradeLogRow.opening_tick_id` / `closing_tick_id` |

### Tier 2 — LLM-touching integration (gated, on-demand)

| File | Coverage |
|---|---|
| `tests/integration/test_strategist_v2_smoke.py` | Full strategist runs with held-position fixture; emits parseable `list[TickerStance]`, exhaustive over watchlist; lifecycle hints present where required; rationale references held-position context |
| `scripts/smoke_run.py` (existing) | 3-tick paper run continues to work end-to-end on the new schema |

### Not tested (intentional)

- Whether the strategist's rationales actually use the held-position context well — subjective; defer to Goal 3 telemetry analysis once data exists.
- Cost impact of the richer prompt — ops concern; tracked in `docs/performance/`.

## Implementation Order

For the impl plan (`docs/superpowers/plans/strategist-v2.md`):

1. **`TickerStance` schema** — Tier 1 tests, then `stance_schema.py`.
2. **`PositionThesis.opened_tick_id`** — schema test, then field addition. Find existing thesis-construction sites and seed the field.
3. **`lifecycle.py` constants + `derive_lifecycle_action`** — Tier 1 tests for each transition, then implementation.
4. **`derivation.py` (`derive_legacy_fields`)** — Tier 1 tests, then implementation.
5. **`held_view.py` (`render_held_positions_view`)** — Tier 1 tests, then implementation.
6. **`StrategistDecision` schema** — schema tests for `stances` and `trim_reasons`, then field additions.
7. **`prompts.py` rewrite** — prompt-rendering tests (slot fills), then template changes.
8. **`agent.py` rewrite** — before/after callback tests, then implementation. Wire `before_agent_callback` (renders held view into state) + `after_agent_callback` (validates + derives legacy fields).
9. **Executor changes** — Tier 1 tests for `state["positions"]` BUY-side write, trade-log FK population, then implementation.
10. **`TickerStanceRow` + `TradeLogRow` extension in `persistence.py`** — round-trip tests, then schema additions.
11. **`strategist_decision_writer`** — Tier 1 test, then implementation.
12. **Pipeline wiring + outer integration test** — wire the new writer, test pipeline structure.
13. **Tier 2 smoke** — full strategist + writer + persistence end-to-end against real LLM.
14. **Cleanup** — append `graphify-out/graph_delta.md` entry; delete `docs/superpowers/plans/strategist-council.md` and `docs/superpowers/plans/exit-rules-and-telemetry.md` (never executed); leave their corresponding `specs/*.md` in place with a "Superseded by strategist-v2-design.md" header.

## Future Work

Pointers for what comes next, so the v2 design can be evaluated in roadmap context:

- **Goal 2 — Analyst → Strategist Contract.** Will reshape `*_signals` state keys with structured `evidence` numerics; add `ANALYST_WEIGHTS` global config knob; subclass `SmartMoneySignal` under `AnalystSignal`. v2's prompt template will need a small update to consume the structured evidence — minor, mechanical.
- **Goal 3 — Knowledge base / self-improvement.** `TickerStanceRow` + `TradeLogRow.opening_tick_id` / `closing_tick_id` are the substrate. Decomposition brainstorm needed: what's a "signal" as a lookup primitive (embeddings? clusters? buckets?), what gets queried at decision time, how learnings feed back into the strategist (re-bias weights? inject "similar past setup" context? veto patterns?).
- **Backlog S3** — sub-tick exit evaluation.
- **Backlog S4** — trailing stops / target ratchet (revisable target_price / stop_price).
- **Backlog S8** — persist `risk_clamps_applied`.
- **Backlog S9** — cost / latency observability.
- **Future PositionPack expansion** — running_max_price, distance-to-trigger flags, SPY-relative excess return, max_run_up/drawdown. The v2 held-positions view is intentionally minimal; expansion happens once we know what the strategist actually uses from richer context.
