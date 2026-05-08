# Exit Rules & Telemetry Persistence Design

**Status:** Spec — awaiting implementation plan
**Scope:** Combines what was originally split as Spec 2 (exit floor/ceiling rules + partial trimming) and Spec 3a (telemetry persistence). The "two birds" framing — both touch the strategist→risk_gate boundary, both shape the data substrate Spec 3b will mine, and they share enough machinery that splitting them adds churn without value.

**Depends on:** Spec 1 (Strategist Council) shipped first. This spec extends `MemberStance`, `StrategistDecision`, the council aggregator, and the persona prompt template — all of which Spec 1 introduces.

## Problem

Three coupled gaps in the current strategist tier:

1. **`PositionThesis.target_price` and `stop_price` are dormant.** They're populated when a position opens and never read again. The bot has no first-class concept of "we said we'd take profits at $210" or "our stop is $185" once the trade is on.
2. **The lifecycle contract treats trimming as either a close or noise.** Any drop below `MIN_HELD_WEIGHT` requires a `close_reason`; any change above it is lumped as a generic "size_change". There's no first-class category for "reduce 10% → 5% because target reached but thesis still partially intact" — exactly the move a real PM makes constantly.
3. **Council telemetry exists only in session state.** Spec 1 produces a rich `CouncilTelemetry` (per-persona stances, quorum decisions, disagreement scores) but it isn't persisted. The same is true of the per-tick strategist decision. Spec 3b's self-improvement loop has nothing to mine from past ticks.

This spec resolves all three through one coherent design: surface position-level rule context to the council as evidence (not as a guillotine), give trims first-class lifecycle treatment, and persist the council's deliberations + per-position rule evaluations into queryable tables so future learning can attribute outcomes to decisions.

## Goals & Non-Goals

**Goals**

- Build a per-tick **`PositionPack`** for every open holding — a deterministic snapshot joining thesis fields, current market data, P&L math, distance-to-trigger metrics, and rule-firing booleans. Hand the pack to all three personas as input.
- Make **trim** and **add** first-class quorum decisions in the council aggregator (alongside open / close / hold), with optional per-ticker `trim_reasons` mirroring the existing `close_reasons` field.
- Persist three new analytics tables: `CouncilStanceRow` (per persona × ticker × tick), `StrategistDecisionRow` (per tick), `PositionPackRow` (per held position × tick) — each focused enough to query directly, denormalised enough to avoid expensive joins for common Spec 3b questions.
- Wire two new writer agents into the pipeline (mirroring `AttributionWriter`'s pattern), running after the council but before risk_gate so council intent is recorded even on downstream failure.
- Add `opening_tick_id` / `closing_tick_id` foreign keys to `TradeLogRow` so closed-trade outcomes can be joined back to the deliberations that produced them.
- Leave the surface clean for Spec 3b's learning loop to plug in without rework.

**Non-goals (deferred to backlog — see `docs/superpowers/backlog.md`)**

- Stop-loss / take-profit *enforcement* via a separate agent. Decided against during brainstorming: the council always decides, rules are advisory inputs, telemetry records overrides for later analysis.
- Sub-tick / real-time exit evaluation. Hourly tick boundary only. (Backlog S3.)
- Trailing stops, target ratchets, mid-hold revision of `target_price`/`stop_price`. Sticky once opened in this spec. (Backlog S4.)
- Persisting `risk_clamps_applied`. Useful but scope creep. (Backlog S8.)
- The self-improvement learning loop itself — what it learns, how it feeds back into prompts/weights. (Backlog S1, requires accumulated telemetry.)
- Round-robin persona debate. (Backlog S2.)

## Architecture

The pipeline keeps the two-tier shape from Spec 1: an outer `SequentialAgent` of named stages, with the strategist stage internally a `SequentialAgent` wrapping persona deliberation and aggregation.

```
HourlyTick (outer SequentialAgent — was 7 stages, now 9)
├── analyst_pool                        (unchanged)
├── attribution_writer                  (unchanged — persists analyst signals)
├── strategist_council                  Spec 1 — gains position_pack_builder as new first sub_agent
│   ├── position_pack_builder           NEW — broker-aware BaseAgent; writes state["position_packs"]
│   ├── persona_pool                    Spec 1 — personas now read packs in their prompts
│   └── council_aggregator              Spec 1 — extended with trim/add classification + clamp + trim_reasons
├── council_telemetry_writer            NEW — persists CouncilStanceRow + StrategistDecisionRow
├── position_pack_writer                NEW — persists PositionPackRow with council_action filled in
├── risk_gate_agent                     unchanged
├── executor_agent                      unchanged
├── memory_writer                       unchanged
└── snapshotter                         unchanged
```

**Why two writers, not one combined `StrategistOutputWriter`:** mirrors the existing per-concern writer pattern (`AttributionWriter`, `MemoryWriter`, `Snapshotter`). Each writer owns one topic, has small focused tests, and a failure in one doesn't drag the other down. Storage cost of separation is zero.

**Why writers run before `risk_gate`:** records the council's *intent* even if `risk_gate` later raises `StrategistContractViolation`. The clamping behaviour itself is already captured in session state via `risk_clamps_applied` (persistence of that is Backlog S8).

**Why the position_pack_builder lives inside `strategist_council`, not as a top-level stage:** it's a deterministic helper coupled to council reasoning. Living inside the council keeps the outer pipeline conceptually clean ("decide" remains one block from the outside).

## Data Contracts

### New: `PositionPack` (`src/agents/strategist/position_pack.py`)

One pack per open holding per tick. Built by `build_position_pack(thesis, current_price, spy_price, current_weight)`; rendered for prompts via `render_packs_for_prompt(packs)` which is just `[pack.model_dump_json(indent=2) for pack in packs]` joined.

```python
class PositionPack(BaseModel):
    """Per-tick deterministic snapshot of one open position."""

    # Identity
    ticker: str

    # Thesis (carried — copied from PositionThesis)
    opened_at: datetime
    opened_price: float
    opened_tag: str
    horizon: Literal["intraday", "swing", "long_term"]
    catalyst: str | None
    rationale: str               # ≤400 from thesis
    target_price: float | None
    stop_price: float | None
    last_review_note: str        # ≤200

    # Live market
    current_price: float

    # Position state
    current_weight: float
    weight_at_open: float

    # P&L
    unrealised_pnl_dollar: float
    unrealised_pnl_pct: float    # vs opened_price

    # Time
    ticks_held: int
    hours_held: float

    # Distance to triggers (None when threshold unset)
    distance_to_target_pct: float | None     # signed; positive = still below target
    distance_to_stop_pct:   float | None     # signed; positive = still above stop

    # Trigger flags
    target_reached: bool | None              # None if no target_price
    stop_breached:  bool | None              # None if no stop_price

    # Running extremes since open
    max_price_since_open: float
    min_price_since_open: float
    max_run_up_pct:   float                  # peak gain vs opened_price
    max_drawdown_pct: float                  # worst drawdown vs opened_price (negative number)

    # Benchmark relative
    spy_return_since_open_pct:    float
    excess_return_since_open_pct: float      # ours minus SPY's
```

### Extended: `PositionThesis` (`src/agents/strategist/schema.py`)

Five new fields. Initialised when the position opens, updated each tick by the pack builder. All fields carry safe defaults so any legacy thesis loaded from a pre-spec session still validates — the aggregator's `build_thesis_from_proposers` is responsible for populating real values on every new open.

```python
class PositionThesis(BaseModel):
    # ... existing fields unchanged ...

    # Stored per-tick state — drives PositionPack derivations:
    running_max_price: float = 0.0        # max(current_price) since open
    running_min_price: float = 0.0        # min(current_price) since open
    spy_price_at_open: float = 0.0        # snapshot for benchmark math
    weight_at_open:    float = 0.0        # snapshot for telemetry "we trimmed from 8% to 5%"
    opened_tick_id:    str   = ""         # join key into StrategistDecisionRow for outcome attribution
```

### Extended: `MemberStance` (`src/agents/strategist/member_schema.py`)

One new optional field. Populated when a persona proposes a strict reduction on a held position (`preferred_weight < current_weight` but still ≥ `MIN_HELD_WEIGHT`).

```python
class MemberStance(BaseModel):
    # ... existing fields unchanged ...
    trim_reason: str | None = Field(default=None, max_length=120)
```

### Extended: `StrategistDecision` (`src/agents/strategist/schema.py`)

One new field, parallel to `close_reasons`.

```python
class StrategistDecision(BaseModel):
    # ... existing fields unchanged ...
    trim_reasons: dict[str, str] = Field(default_factory=dict)
```

### New ORM tables (`src/orchestrator/persistence.py`)

#### `CouncilStanceRow` — one row per persona × ticker × tick

Aggregator outcome fields are denormalised: each of the 3 stance rows for a given ticker carries the same `final_weight` / `quorum_decision` / `disagreement_score`. Storage cost is trivial (~480 rows/day for a 5-ticker watchlist) and querying becomes a simple `WHERE` filter.

```python
class CouncilStanceRow(Base):
    __tablename__ = "council_stances"
    id: Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]       = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    persona: Mapped[str]       = mapped_column(String, index=True)        # value|momentum|contrarian
    ticker: Mapped[str]        = mapped_column(String, index=True)
    preferred_weight: Mapped[float] = mapped_column(Float)
    conviction: Mapped[float]  = mapped_column(Float)
    rationale: Mapped[str]     = mapped_column(String)                     # ≤140
    horizon: Mapped[str | None]      = mapped_column(String, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    catalyst: Mapped[str | None]     = mapped_column(String, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    trim_reason: Mapped[str | None]  = mapped_column(String, nullable=True)
    # Aggregator outcome (same value across the 3 rows for a given ticker × tick)
    final_weight: Mapped[float] = mapped_column(Float)
    quorum_decision: Mapped[str] = mapped_column(String, index=True)       # open|close|trim|add|hold
    disagreement_score: Mapped[float] = mapped_column(Float)
    degraded_member: Mapped[str | None] = mapped_column(String, nullable=True)
```

#### `StrategistDecisionRow` — one row per tick

The contract output. Source of truth for "what did the council decide this tick".

```python
class StrategistDecisionRow(Base):
    __tablename__ = "strategist_decisions"
    id: Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]       = mapped_column(String, unique=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    decision_tag: Mapped[str]  = mapped_column(String, index=True)
    reasoning: Mapped[str]     = mapped_column(String)                     # ≤300
    updated_thesis: Mapped[str] = mapped_column(String)                    # ≤500
    confidence: Mapped[float]  = mapped_column(Float)
    target_weights_json: Mapped[str] = mapped_column(String)
    new_positions_json: Mapped[str]  = mapped_column(String)
    close_reasons_json: Mapped[str]  = mapped_column(String, default="{}")
    trim_reasons_json: Mapped[str]   = mapped_column(String, default="{}")
    mean_disagreement: Mapped[float] = mapped_column(Float)
    degraded_member: Mapped[str | None] = mapped_column(String, nullable=True)
```

#### `PositionPackRow` — one row per held position × tick

The pack as the council saw it, plus what the council did with it. **Spec 3b's primary substrate** for analyses like "when the council overrode `stop_breached=True`, did the position eventually recover?"

```python
class PositionPackRow(Base):
    __tablename__ = "position_packs"
    id: Mapped[int]                = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]           = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]  = mapped_column(DateTime)
    ticker: Mapped[str]            = mapped_column(String, index=True)
    opened_at: Mapped[datetime]    = mapped_column(DateTime)
    opened_price: Mapped[float]    = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    horizon: Mapped[str]           = mapped_column(String)
    catalyst: Mapped[str | None]   = mapped_column(String, nullable=True)
    current_price: Mapped[float]   = mapped_column(Float)
    current_weight: Mapped[float]  = mapped_column(Float)
    weight_at_open: Mapped[float]  = mapped_column(Float)
    unrealised_pnl_pct: Mapped[float] = mapped_column(Float)
    unrealised_pnl_dollar: Mapped[float] = mapped_column(Float)
    ticks_held: Mapped[int]        = mapped_column(Integer)
    hours_held: Mapped[float]      = mapped_column(Float)
    distance_to_target_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_stop_pct: Mapped[float | None]   = mapped_column(Float, nullable=True)
    target_reached: Mapped[bool | None]          = mapped_column(Boolean, nullable=True)
    stop_breached: Mapped[bool | None]           = mapped_column(Boolean, nullable=True)
    max_run_up_pct: Mapped[float]  = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    spy_return_since_open_pct: Mapped[float] = mapped_column(Float)
    excess_return_since_open_pct: Mapped[float] = mapped_column(Float)
    council_action: Mapped[str]    = mapped_column(String, index=True)     # hold|trim|add|close
    rule_overridden: Mapped[bool]  = mapped_column(Boolean, index=True)    # see derivation rule below
```

**`rule_overridden` derivation rule** (computed at write time):
- `True` if `(stop_breached AND council_action != 'close')` — council saw a stop hit and refused to fully exit
- `True` if `(target_reached AND council_action == 'add')` — council saw a target hit and *added* anyway
- `False` otherwise

Holding through a target or trimming-at-target are *not* overrides — those are the natural responses. Spec 3b can refine these classifications later without schema changes.

#### Extended: `TradeLogRow` — outcome attribution join keys

```python
# Add to existing TradeLogRow:
opening_tick_id: Mapped[str] = mapped_column(String, index=True)
closing_tick_id: Mapped[str] = mapped_column(String, index=True)
```

Populated wherever closed-trade rows are *created* today (the executor or a downstream close handler — to be located during planning).

## Council Prompt Updates

The Spec 1 prompt template gains one new section between `## Analyst Signals` and `## Your Job`:

```
## Open Positions (deterministic snapshot — believe these numbers)

{position_packs}    ← rendered by render_packs_for_prompt(packs)

You may decide for each held position to:
- HOLD — keep the weight where it is
- TRIM — reduce the weight (any reduction that keeps weight ≥ MIN_HELD_WEIGHT); include a trim_reason
- CLOSE — set weight to 0; include a close_reason
- ADD  — increase the weight (subject to risk gate caps)

The `rules` block tells you whether the original stop/target hypothesis has fired.
Treat these as inputs to your judgment, not commands. If you choose to override a
fired rule (e.g. holding through stop_breached because the thesis has strengthened),
say so explicitly in your rationale so telemetry can capture the override.
```

The "Your Job" section gains one new bullet:

```
- If proposing to trim (current >MIN_HELD_WEIGHT → preferred lower but still ≥MIN_HELD_WEIGHT): include trim_reason.
```

All three personas see identical position packs and identical rules guidance — personas differ on *how* they weigh evidence (Spec 1's three lenses), not on the bookkeeping.

## Lifecycle Contract & Trim Semantics

The existing `validate_lifecycle_contract` in `src/agents/risk_gate/lifecycle.py` already permits trims today: it only fires when a held position drops *below* `MIN_HELD_WEIGHT` without a `close_reason`. **No change to the validator.** Trims keep both `was_open` and `will_be_open` true; the contract is satisfied.

What does change in the **aggregator** (`src/agents/strategist/aggregator.py`):

1. **Defensive clamp.** If `confidence_weighted_avg` produces a final weight below `MIN_HELD_WEIGHT` for a held position *without* the close-quorum branch having fired (mathematically rare; reachable only when all members propose tiny non-zero weights), clamp the result up to `MIN_HELD_WEIGHT`. Rationale: if no persona triggered a formal close, the aggregator should not unilaterally close the position via averaging arithmetic.
2. **`trim_reasons` population.** When `quorum_decisions[ticker] == "trim"`, build `trim_reasons[ticker]` from the proposing members' `trim_reason` strings, V→M→C order, "|"-joined, capped at 120 chars — same convention as `close_reasons`.

(The trim/add classification itself was added to Spec 1's aggregator during this brainstorm; this spec only extends those branches.)

## State Contract

| Key | Producer | Consumer | Notes |
|---|---|---|---|
| `position_packs: dict[str, PositionPack]` | `position_pack_builder` | persona_pool prompts; `position_pack_writer` | **new — per held ticker** |
| `value_stances`, `momentum_stances`, `contrarian_stances` | persona LlmAgents | aggregator | Spec 1 — now optionally carry `trim_reason` |
| `strategist_decision` | aggregator | risk_gate, telemetry_writer, memory_writer | Spec 1 — now optionally carries `trim_reasons` |
| `council_telemetry` | aggregator | telemetry_writer | Spec 1 — quorum_decisions now include trim/add |

## File Layout

```
src/agents/strategist/
├── __init__.py                         (exports unchanged)
├── council.py                          MODIFIED — strategist_council inner SequentialAgent gains position_pack_builder as first sub_agent
├── personas.py                         (unchanged)
├── aggregator.py                       MODIFIED — MIN_HELD_WEIGHT clamp + trim_reasons population
├── member_schema.py                    MODIFIED — MemberStance gains trim_reason
├── config.py                           (unchanged)
├── prompts.py                          MODIFIED — template gains {position_packs} block + trim_reason instruction
├── schema.py                           MODIFIED — PositionThesis gains 4 running fields; StrategistDecision gains trim_reasons
├── position_pack.py                    NEW — PositionPack model + build_position_pack() + render_packs_for_prompt()
├── pack_builder.py                     NEW — PositionPackBuilder(BaseAgent), broker-aware
├── telemetry_writer.py                 NEW — CouncilTelemetryWriter(BaseAgent)
└── pack_writer.py                      NEW — PositionPackWriter(BaseAgent)

src/orchestrator/
├── persistence.py                      MODIFIED — CouncilStanceRow, StrategistDecisionRow, PositionPackRow + opening_tick_id/closing_tick_id on TradeLogRow
└── pipeline.py                         MODIFIED — wires telemetry_writer + pack_writer after strategist_council
```

**Touched outside `strategist/`:**

- `src/orchestrator/persistence.py` — three new ORM rows + `TradeLogRow` extension
- `src/orchestrator/pipeline.py` — wire two new writers
- `src/agents/executor/agent.py` — the `save_trade_log_entry` call site (lines 93-108 today): pass `opening_tick_id` from `thesis.opened_tick_id` and `closing_tick_id` from `state["tick_id"]`

## Validation & Failure Modes

### Pack builder failures

| Failure | Behaviour |
|---|---|
| Broker call fails fetching current_price for a held ticker | Skip that ticker's pack (don't block the tick); log a warning; council reasons without that pack but with the thesis text. Position can still be acted on but personas know less. |
| `spy_price_at_open` missing on thesis (e.g. position opened before this spec) | All positions opened from this spec onward have `spy_price_at_open` populated by the aggregator's `build_thesis_from_proposers` (extended in this spec to read SPY price from `tick_context`). Pre-spec migration is not a concern — paper trading has not accumulated long-held positions, and any in-flight position at deploy time is closed and re-opened cleanly. The spec deliberately does not introduce a migration flag. |
| Position thesis missing entirely (data corruption) | Skip the pack and log; council sees no pack for that ticker but `positions[ticker] > 0` — handled the same as a "held without thesis" today. |

### Writer failures

| Failure | Behaviour |
|---|---|
| Telemetry writer DB insert fails | Log error; do not block tick. Telemetry is observability, not the contract. |
| Pack writer DB insert fails | Same. |

The council's contract output (`StrategistDecision` in session state) is what risk_gate consumes — writers are best-effort observability.

## Testing Strategy

Mirrors the repo's Tier 1 / Tier 2 convention.

### Tier 1 — pure unit tests, no network, no LLM (mandatory)

| File (new) | Coverage |
|---|---|
| `tests/unit/strategist/test_position_pack.py` | `build_position_pack` math: P&L (dollar + pct), distance %, target_reached / stop_breached booleans, max_run_up_pct, max_drawdown_pct, SPY-relative excess. Edge cases: target_price=None, stop_price=None, fresh open (ticks_held=0). |
| `tests/unit/strategist/test_position_thesis_running_fields.py` | `running_max_price` / `running_min_price` initialise to `opened_price` and update only in the right direction; `spy_price_at_open` snapshotted at open and unchanged thereafter; `weight_at_open` ditto. |
| `tests/unit/strategist/test_aggregator_clamp.py` | Sub-MIN_HELD_WEIGHT trim gets clamped to `MIN_HELD_WEIGHT`; close-quorum branch unaffected by clamp. |
| `tests/unit/strategist/test_aggregator_trim_reasons.py` | `trim_reasons` populated in V→M→C order; `\|`-joined; capped at 120; only populated for `quorum_decision == "trim"` tickers. |
| `tests/unit/strategist/test_pack_builder_agent.py` | `PositionPackBuilder._run_async_impl` writes correct `state["position_packs"]`; broker error on one ticker doesn't fail the whole tick. |
| `tests/unit/strategist/test_telemetry_writer.py` | `CouncilTelemetryWriter` produces correct `CouncilStanceRow` count (3 × |tickers|) + 1 `StrategistDecisionRow`; aggregator outcomes denormalised correctly across the 3 stance rows for one ticker; degraded_member propagated. |
| `tests/unit/strategist/test_pack_writer.py` | `PositionPackWriter` produces 1 row per held ticker; `council_action` populated from quorum_decisions; `rule_overridden` derivation rule correct for all four cases (held-through-stop, added-into-target, normal close-on-target, normal hold). |
| `tests/unit/orchestrator/test_persistence_council.py` | SQLAlchemy round-trip for all 3 new tables; foreign-key joins from `TradeLogRow.opening_tick_id` to `StrategistDecisionRow.tick_id` and `CouncilStanceRow.tick_id` work. |
| `tests/unit/orchestrator/test_pipeline_wiring_exits.py` | Outer pipeline contains telemetry_writer + pack_writer between council and risk_gate; inner `strategist_council` contains pack_builder as first sub_agent. |
| `tests/unit/strategist/test_prompts_with_packs.py` | `{position_packs}` slot fills correctly; empty packs render as empty list `[]`; rich packs render as JSON-serialised list. |

### Tier 2 — LLM-touching integration (gated, on-demand)

| File | Coverage |
|---|---|
| `tests/integration/test_council_with_packs_smoke.py` | Full `strategist_council` runs with held positions in fixture; personas reason about packs end-to-end; output is parseable and references pack data in rationales. |

### Not tested (intentional)

- Whether personas actually *use* the pack data (subjective; defer to Spec 3b telemetry analysis).
- Cost impact of richer prompt (ops concern; tracked in `docs/performance/`, Backlog S9).
- `rule_overridden` outcome attribution (requires PnL data — Spec 3b territory).

## Pipeline Wiring

Edits in `src/orchestrator/pipeline.py`:

```python
from agents.strategist.council import strategist_council          # unchanged
from agents.strategist.telemetry_writer import council_telemetry_writer    # NEW
from agents.strategist.pack_writer import position_pack_writer    # NEW

SequentialAgent(name="HourlyTick", sub_agents=[
    analyst_pool,
    attribution_writer,
    strategist_council,                  # Spec 1; now contains pack_builder as first sub_agent
    council_telemetry_writer,            # NEW
    position_pack_writer,                # NEW
    risk_gate_agent,
    executor_agent,
    memory_writer,
    snapshotter,
])
```

`strategist_council` itself, in `council.py`:

```python
strategist_council = SequentialAgent(
    name="StrategistCouncil",
    sub_agents=[
        position_pack_builder,           # NEW first sub_agent
        persona_pool,                    # Spec 1
        council_aggregator,              # Spec 1
    ],
)
```

## Implementation Order (for the implementation plan that follows)

1. **`PositionThesis` extension** — schema test, then add the 4 running fields. Update existing thesis-creation sites to seed them.
2. **`PositionPack` model + builder** — Tier 1 tests for the math, then `position_pack.py`.
3. **`PositionPackBuilder` BaseAgent** — Tier 1 test for state writes + per-ticker error handling, then `pack_builder.py`. Wire into `strategist_council` as first sub_agent.
4. **Prompt template update** — `prompts.py` gains the `{position_packs}` block and trim_reason instruction. Update the prompt rendering test.
5. **`MemberStance.trim_reason` + `StrategistDecision.trim_reasons`** — schema tests, then field additions.
6. **Aggregator extensions** — Tier 1 tests for clamp + trim_reasons population, then implement.
7. **Three new ORM tables + `TradeLogRow` foreign keys** — round-trip tests, then `persistence.py` additions. Find and update the closed-trade write site for the new fk fields.
8. **`CouncilTelemetryWriter`** — Tier 1 test, then `telemetry_writer.py`.
9. **`PositionPackWriter`** — Tier 1 test (incl. `rule_overridden` derivation), then `pack_writer.py`.
10. **Pipeline wiring + outer integration test** — `pipeline.py` edits and `test_pipeline_wiring_exits.py`.
11. **Tier 2 smoke** — full council with packs against real LLMs to confirm prompt parses and personas use the pack data.

## Future Work

Pointers to `docs/superpowers/backlog.md`:

- **S1 — Self-improvement learning loop.** This spec's persistence is its substrate. Once data is accumulated, S1 brainstorms what to mine and how to feed it back.
- **S3 — Sub-tick exit evaluation.** The hourly cadence is acknowledged; flash-crash latency is the trade-off.
- **S4 — Trailing stops & target ratchet.** This spec keeps target/stop sticky once opened. S4 introduces revisability.
- **S8 — Persisting `risk_clamps_applied`.** Worth pairing with this spec's analytics tables; deferred only for scope reasons.
