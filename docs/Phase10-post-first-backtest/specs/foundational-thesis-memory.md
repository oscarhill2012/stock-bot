# Foundational Thesis Memory

**Status:** Draft — design (post-critique revision)
**Sequenced after:** Spec A (surgical correctness fixes — items S1, S2, S6 are pre-conditions)
**Sequenced before:** Spec C (experiential memory — `memory_buffer` + `day_digest`), then 2.5.3 + 2.5.4 cleanup (lifecycle hooks), then Spec D (PIT-correctness leak-audit)
**Scope grown post-critique:** also carries amendments to `docs/contract-invariants.md` §A and §C-Rule 7 (see "Contract amendments" section). Both files move in one PR.

---

## Summary

The strategist agent currently produces byte-identical rationale across all 46
sampled ticks of the `baseline-2025-09 / first-test` run.  The cause is two
problems compounding:

1. **No cross-tick persistence of `state["positions"]` or `state["thesis"]`.**
   Live (`src/orchestrator/tick.py`) reseeds these to `{}` and `""` on every
   tick and creates a fresh ADK session per invocation; backtest
   (`src/backtest/driver.py`) relies on an in-memory dict carry that
   `docs/contract-invariants.md` §B Phase 2 forbids relying on for
   correctness.  The cross-tick fields documented under §A as
   `Owner=Strategist, Lifetime=cross-tick, Source=Persistence layer (§E)` have
   no persistence layer behind them today.  Result: `held_view_at_decision:
   null` on every persisted decision (see
   `backtests/baseline-2025-09/runs/first-test/report/analysis_computational.md`
   §3.4).

2. **Prompt isomorphism across ticks.**  Even if the persistence gap were
   closed, the strategist prompt at tick *N* would still look structurally
   identical to tick 1's (same evidence shapes, slow-moving signals, same
   instruction text).  A rational LLM produces near-identical output for
   near-identical inputs — the byte-identical rationale is not pathology, it
   is the LLM doing the obvious thing.

This spec closes the persistence gap and reshapes the strategist prompt so
that tick *N* is *structurally different* from tick 1.  It does so without
hiding information from the LLM — anti-anchoring is achieved via prompt
*framing* (a forced "what's changed" delta), not via *information hiding*
(the original rationale stays visible).

The persistence subsystem uses **ADK's `user:`-prefixed state**, which the
`DatabaseSessionService` already persists in a separate `user_state` table
keyed by `(app_name, user_id)`.  Each live tick still creates a fresh ADK
session (matching today's Cloud Run Job semantics), and `state["user:positions"]`
arrives pre-populated because ADK merges user state into every new session
for the same `user_id`.  No new SQL tables are required for V1.  The
backtest driver switches from `InMemorySessionService` to
`DatabaseSessionService` so the same mechanism applies symmetrically.

**Writer pattern (revised 2026-05-23).**  Strategist's `LlmAgent` *decides*
the new thesis content by reasoning — its `output_schema` carries `stances`
plus optional `thesis_revision` and lands at `state["strategist_decision"]`
via the agent's `output_key`.  The downstream persistence writes for
`user:positions` and `user:thesis` are performed by an
**`after_agent_callback` attached to Executor**.  The callback reads
Strategist's decision plus Executor's just-emitted fills, applies the verb
dispatch, and writes via `ctx.state["user:positions"] = ...` /
`ctx.state["user:thesis"] = ...`.  ADK's `_handle_after_agent_callback`
(`google/adk/agents/base_agent.py:538-545`) auto-yields a state-delta
`Event` containing the callback's accumulated `state._delta`;
`DatabaseSessionService.append_event` then persists the `user:`-prefixed
keys to the `user_state` table.  Executor's `_run_async_impl` itself
continues to yield `state_delta` for `executions` / `last_executed_tick_id`
only — the broker-effect keys and the thesis-book write are separated by
phase (broker effects first, then the after-callback) but unified inside
one agent.  This conforms with §C-Rule 1 (state mutation rides on an
event) once the rule's in-tick callback carve-out is extended to cover
the auto-emit path for cross-tick keys (see "Contract amendments" below).
The pre-existing MemoryWriter agent is **untouched** by Spec B — it
continues to own `memory_buffer` / `day_digest` for Spec C, and its bare-
key `thesis` write is dropped in favour of the new Executor-callback
writer for `user:thesis`.

**Namespace partitioning.**  Paper, live, and backtest run under disjoint
ADK `app_name` values (`StockBot-paper`, `StockBot-live`,
`StockBot-backtest-<window_id>`).  `user_id` stays a stable identity
(`stockbot`).  Backtest gets per-window namespacing so re-running a window
from cold start cannot inherit prior-run thesis.  Cross-contamination
between paper and live is structurally impossible.

---

## Scope

### In scope

- A persistence subsystem for the cross-tick fields named in §A of
  `docs/contract-invariants.md`: `state["positions"]` and `state["thesis"]`.
- A typed Pydantic model (`PositionThesis`) capturing the per-position thesis
  carried across ticks.
- A redesigned strategist prompt that surfaces evolution rather than echoing
  prior conclusions.
- Six stance verbs in the strategist's output vocabulary —
  `open`, `add`, `trim`, `close`, `hold`, `update` — with an executor mapping
  that allows the thesis to mutate without trading.
- Removal of `src/agents/strategist/derivation.py:254-271` carry-forward
  semantics; replaced by an explicit "stance required per held position"
  rule.
- Symmetric live + backtest behaviour: both run on
  `DatabaseSessionService`; cross-tick reads survive process restarts (live)
  and tick boundaries (backtest).

### Out of scope

- **Experiential memory** (`state["memory_buffer"]`, `state["day_digest"]`)
  — covered by Spec C.  We persist `user:positions` and `user:thesis` only.
- **Correctness fixes** (S1 `reference_prices` PIT-clamp, S2 executor
  `del positions` only on true close, S6 `decision_tag` enum, plus D1, D2)
  — covered by Spec A.  This spec depends on S1, S2, S6 having landed.
- **PIT-correctness leak audit** — covered by Spec D (deferred per
  `project_backtest_pit_correctness_deferred` memory).
- **Schema migrations and analytics queryability for the thesis book.**
  V1 uses ADK user state (a JSON dict per user).  If schema-evolution pain
  or analytics queryability become real costs, the "Future work" section
  documents the graduation path to typed SQL tables.
- **Catalyst lifecycle parsing.**  The `catalyst` field stays free-form
  text; we do not parse "next week" into a date or compute fired / pending
  / expired status.  The LLM reads the catalyst text and the
  `Held for: N ticks · M hours · D days` evolution line, and decides for
  itself.

### Pre-conditions (from Spec A)

The following Spec A items must land before this spec is implemented.

- **S1 — `reference_prices` PIT-clamp.**  Spec A re-reads
  `reference_prices` per tick with `end=as_of.date()`.  Without it, any new
  memory consumer that reads `state["reference_prices"]` will see future
  bars (the technical extractor's downstream re-clamp is defence-in-depth,
  not the gate).  Source: `analysis_computational.md` §1.1.
- **S2 — Executor only deletes from `state["positions"]` on true close.**
  Today the executor's deletion semantics are ambiguous; with thesis memory
  in place, an erroneous `del state["positions"][ticker]` on an `add`/`trim`
  destroys the thesis row.
- **S6 — `decision_tag` enum.**  Spec B introduces two new verbs (`hold`,
  `update`); Spec A's enum extension is the right home for that change so
  the validation layer is one diff, not two.

---

## Contract amendments

Spec B's persistence model deviates from `docs/contract-invariants.md` as
written.  The amendments below land in the same PR as the Spec B
implementation and resolve the deviations explicitly, not by drift.

### §A schema — `positions` and `thesis` rows

The two cross-tick rows are repainted under the `user:` prefix.  Both
rows have **Executor** as the single owner — per §A's "for agents,
callbacks attached to that agent count as the agent's writes" rule, the
new `after_agent_callback` is Executor's write surface.  The amended
rows read:

| State key | Owner | Lifetime | Source | Refresh |
|-----------|-------|----------|--------|---------|
| `state["user:positions"]` | Executor's `after_agent_callback`† | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2: implicit ADK merge into the fresh session.  Phase 4: callback writes via `ctx.state["user:positions"] = ...`; ADK's `_handle_after_agent_callback` auto-yields a state-delta event; `DatabaseSessionService.append_event` persists it. |
| `state["user:thesis"]` | Executor's `after_agent_callback`† | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2: implicit ADK merge into the fresh session.  Phase 4: callback writes via `ctx.state["user:thesis"] = ...` (passthrough of Strategist's optional `thesis_revision`, else carry-forward of the prior value).  Same auto-yielded event as above. |

Footnote attached to both rows:

> *Strategist's `LlmAgent` reasons about and produces the thesis content
> through its `output_schema` (`stances` + optional `thesis_revision`,
> landing at `state["strategist_decision"]` via the agent's
> `output_key`).  The persistence-bearing `EventActions(state_delta=…)`
> for the `user:`-prefixed keys is auto-yielded by ADK from Executor's
> `after_agent_callback`, which assembles the new `user:positions` dict
> by applying Strategist's stance verbs to the prior dict plus
> Executor's own fill data and passes `user:thesis` through from
> `thesis_revision`.  See §C-Rule 1's auto-yielded-callback-write
> clarification for why this is conformant.*

### §C-Rule 1 — auto-yielded delta-tracked callback writes (added 2026-05-23)

ADK's `Context.state` (the property used as `callback_context.state`)
is a delta-tracking `State` object (`google/adk/sessions/state.py`).
When a callback writes via `ctx.state[key] = value` the write lands in
both `state._value` (the live in-memory view) and `state._delta` (the
pending event payload) at `state.py:42-47`.  ADK's
`_handle_after_agent_callback` (`google/adk/agents/base_agent.py:489-546`)
then checks `callback_context.state.has_delta()` after the callback
returns; if true, it constructs an `Event` whose `actions` carry the
accumulated `state_delta` and the runner yields it through
`SessionService.append_event` like any agent-produced event.
`DatabaseSessionService` persists `app:` / `user:`-prefixed keys to
their respective tables on that ingestion path.

**Clarification for Rule 1's in-tick callback carve-out.** The carve-out
exists because *direct dict mutation* of a Pydantic object held in state
(e.g. the Strategist `_strategist_validation_callback` mutating
`decision.target_weights = ...` on the object referenced by
`state["strategist_decision"]`) does not produce a `state_delta` event
and is therefore not durable on serialising backends.  That kind of
mutation is conformant only for in-tick consumers reading the same
reference.  The Spec B Executor `after_agent_callback`'s
`ctx.state["user:positions"] = …` write is a *different* mechanism:
it goes through ADK's delta-tracking and is auto-yielded as a real
`state_delta` event.  Cross-tick `user:`-prefixed writes via this
auto-yield path are conformant with Rule 1 by construction — the write
rides on an explicit event, just one ADK emits on the callback's
behalf.

The Strategist validation carve-out (in-tick reference mutation) and
the Executor persistence write (cross-tick delta-tracked auto-yield)
are two distinct patterns; the carve-out applies to the former, the
new clarification covers the latter.

### §C-Rule 2 — runtime observability handles ride on `temp:`

The backtest driver (and, in a follow-on, the live tick) currently
injects two non-serialisable handles into the per-tick state dict:

- `state["_trace"]: TraceWriter`
- `state["_decision_logger"]: DecisionLogger`

These keys exist to give analyst / strategist / executor agents access
to observability writers without threading them through every call
signature.  They are observability-only (Rule 8): contract-neutral,
invocation-scoped, never persisted, never read by another tick.

Today they ride bare-keyed because `InMemorySessionService` happily
keeps non-serialisable Python objects in `session.state`.  Spec B's
switch to `DatabaseSessionService` (see "Backtest changes" below)
breaks this — SQLAlchemy's JSON serialiser cannot round-trip a
`TraceWriter`.  The amendment is to rename both keys under ADK's
`temp:` prefix:

- `state["temp:_trace"]`
- `state["temp:_decision_logger"]`

ADK's `_session_util.extract_state_delta()` (`google/adk/sessions/_session_util.py:48`)
skips `temp:` keys, and `BaseSessionService._trim_temp_delta_state()`
strips them from the event delta before the subclass persistence call.
The handles live in `session.state` for the duration of one tick (so
agents can read them via the same `ctx.state[key]` lookup as before)
but never touch the database.

**Driver injection point:** because ADK's `create_session(state=…)`
seed dict passes through `extract_state_delta` first, `temp:`-prefixed
keys passed there are silently discarded (see `_session_util.py:48`
plus `database_session_service.py:412-485`).  The driver therefore
injects the handles by direct mutation of `adk_session.state` **after**
`create_session(...)` returns — the live session dict accepts them, ADK
preserves them across the in-process invocation, and they never reach
the DB.

Rule 2's "Concrete invocation-scoped keys" list gains
`temp:_trace` and `temp:_decision_logger` alongside the existing
`temp:held_positions_view` etc.  Rule 8 (observability is additive and
contract-neutral) is unaffected — the observability writers continue to
write to artefacts external to the contract surface.

### §C-Rule 7 — clarification

Rule 7 currently reads (paraphrased) "pipeline reads from state only;
lifecycle owns persistence reads/writes", as if the persistence layer
sits *outside* the state dict.  ADK `user:`-prefixed state collapses that
distinction: the `DatabaseSessionService` IS the persistence layer, and
the user_state table IS the lifecycle storage.  An added paragraph
clarifies:

> *ADK `user:`-prefixed keys are the persistence layer for the StockBot
> pipeline.  Reading them via state IS the lifecycle pattern Rule 7
> anticipates — the `DatabaseSessionService` provides the persistence
> boundary that pipeline agents do not need to cross directly.  Pipeline
> agents read `user:`-prefixed keys from state at Phase 2 and write them
> via `state_delta` at Phase 4; ADK persists the writes to the
> `user_state` table on event ingestion.  No separate "Phase 2 hydrator"
> or "Phase 4 persister" agent is required.*

### Why not a separate "LifecycleWrapper" abstraction?

`docs/todo-fixes.md` item 2.5.3 lists "introduce a formal
LifecycleWrapper abstraction" vs "augment existing entry points" as an
open choice.  Spec B picks **augment existing entry points**: the live
entry point (`src/orchestrator/tick.py`) and the backtest driver
(`src/backtest/driver.py`) both rely on the ADK `SessionService` (the
implicit lifecycle wrapper) to mediate persistence.  No new abstraction
is introduced.  This collapses one of 2.5.3's open questions and clears
the runway for the eventual 2.5.4 lifecycle-hooks cleanup once Spec C
also lands.

---

## Problem statement

### The "stuck on tick 1" pathology

Across the 46-tick `first-test` backtest the strategist:

- Produced **byte-identical rationale strings** for AVGO, MSFT, and XOM on
  every tick they were held.
- Showed `held_view_at_decision: null` in every persisted
  `decisions/*.json` even on ticks where positions existed in the broker's
  portfolio.
- Generated 135 decision files (45 ticks × 3 held tickers) of which the
  trade log captured only 3 closed round-trips (see
  `analysis_computational.md` §2.3 — counts opens too, but the qualitative
  point holds).

The naïve diagnosis is "the LLM is stuck — give it memory so it sees its own
past decisions".  That diagnosis is wrong on its own terms.  Adding "here is
what you decided last tick, do you agree?" produces a prompt that is
*equally* isomorphic to tick 1's prompt (same evidence, same prior
endorsement, same outputs).  Any memory scheme that echoes prior conclusions
back inherits the same failure mode.

### The structural diagnosis

The real failure is that tick 2's *prompt* is structurally indistinguishable
from tick 1's:

- The watchlist is the same.
- Slow-moving signals (50d MA, sector ratios, fundamental scalars) have
  barely moved in an hour.
- The evidence-shape (what fields each analyst emits) is identical.
- The instruction text is literally the same string.

Given near-identical inputs, a temperature-low LLM rationally produces
near-identical output.  Byte-identical rationale is not the LLM
malfunctioning — it is the LLM working correctly.

### The fix shape

Memory must make the prompt *structurally* different across ticks, by
surfacing change rather than confirming continuity:

- On tick 1, the prompt contains no `Held Positions` block and a
  `Mode: Cold start — portfolio is empty` header.
- On tick *N* > 1, the prompt contains a populated `Held Positions` block
  rendering the thesis commitments alongside evolution columns
  (price-vs-entry, time-since-entry, distance-to-target/stop, last
  reviewed), and a `Mode: Incremental — you have N held positions` header.
- The strategist must emit an explicit stance for *each* held position
  with a "what's changed since you opened this" reason — silent
  carry-forward is removed.

The prompt is now structurally different at every tick where the held set
changes, and even when the held set is stable the evolution columns mutate
(price moves, time advances, distances narrow).  The LLM no longer has the
option of producing a byte-identical response, because the input is no
longer byte-identical.

---

## Design principles

### Principle 1 — Anti-anchoring via framing, not hiding

The rationale prose written when a position was opened *stays visible* to
the LLM on every subsequent tick.  Anti-anchoring is achieved by labelling
that prose as `Your commitments on entry:` (not `Your prior conclusion:`)
and by an explicit prompt directive that requires the LLM to articulate
*what has changed* before reaching its hold/trim/close/update decision.

The alternative — hiding the rationale entirely and showing only
target/stop/horizon/catalyst — was rejected on the grounds that it
persists a *commitment record*, not a *thesis*.  The point of thesis
memory is to carry the thinking, not just the numbers.

### Principle 2 — Memory exposes evolution, not prior echo

The held-view renders evolution columns (price-vs-entry, time elapsed,
distance to target/stop) and the persisted commitments, but never the
LLM's prior-tick *review reasoning* (the "what's changed" prose written
during a previous `hold` or `update`).  That review reasoning is persisted
to the audit trail but withheld from the next tick's prompt.  This
prevents the LLM from anchoring on its own recent justifications while
keeping the audit trail complete.

### Principle 3 — Required engagement per held position

The strategist must emit a stance for every held position.  The active
stances model (omission = carry-forward) survives only for flat tickers on
the watchlist — for which the LLM has no view to commit to.

### Principle 4 — Cold-start vs incremental framing

The prompt's `{strategist_mode}` placeholder injects different text
depending on whether `state["user:positions"]` is empty.  This single
template, two modes design avoids template proliferation while making the
tick-1 prompt visibly different from the tick-N prompt.

### Principle 5 — Symmetric live and backtest persistence

Both live and backtest run on `DatabaseSessionService`.  Live points it at
the production database (`DATABASE_URL` env var); backtest points it at
`runs/<run-id>/session.sqlite`.  Cross-tick state is read from the user
state table on every tick; no code path relies on leftover in-memory state
(`docs/contract-invariants.md` §B Phase 2).

---

## Architecture

### Persistence model — ADK `user:` state

ADK's session state has built-in scoping prefixes:

| Prefix | Scope | Persistence |
|--------|-------|-------------|
| (none) | per session | dies with session |
| `user:` | per `(app_name, user_id)` | **persists across sessions** |
| `app:` | per `app_name` | persists across users and sessions |
| `temp:` | per session | never persisted |

`DatabaseSessionService` stores `user:`-prefixed keys in a separate
`user_state` table keyed by `(app_name, user_id)`.  On every
`get_session()` or `create_session()` for that user, ADK merges the user
state into the returned state dict.  When Executor's
`after_agent_callback` writes to `state["user:positions"]` (and ADK
auto-yields a state-delta event from the accumulated delta),
`DatabaseSessionService` persists the change at the user level — so the
next tick's fresh session sees it.

The pipeline reads and writes two new cross-tick keys:

- `state["user:positions"]: dict[str, dict]`  — keyed by ticker, value is
  a serialised `PositionThesis`.
- `state["user:thesis"]: str` — the standing market thesis.

That is the entirety of the new persistence surface for Spec B.

#### Writer responsibilities

Strategist's `LlmAgent` decides the new thesis content but cannot
route a subset of its output to a specific `user:`-prefixed state key
(an `LlmAgent` writes its entire output blob through a single
`output_key`).  The cross-tick persistence write is performed inside
**Executor's `after_agent_callback`**, which runs after Executor's
`_run_async_impl` has dispatched broker calls and yielded its
broker-effect `state_delta`.  The callback assembles the new
`user:positions` dict from (a) the just-emitted fills, (b) Strategist's
stances, and (c) the prior `user:positions` already merged into session
state at Phase 2, then writes via `ctx.state["user:positions"] = ...`
/ `ctx.state["user:thesis"] = ...`.  ADK's
`_handle_after_agent_callback` (`base_agent.py:489-546`) auto-yields a
state-delta `Event` carrying the callback's accumulated delta; the
runner ingests it through `SessionService.append_event` exactly as for
agent-yielded events.

| Agent | Reads from state | Yields `state_delta` for |
|-------|------------------|--------------------------|
| Strategist (`LlmAgent`) | held-view, evidence, prior `user:thesis` | single `output_key` `strategist_decision` (plus the existing in-tick validation callback's derived-field reference mutations on the same Pydantic object — covered by Rule 1's in-tick carve-out) |
| Risk gate (BaseAgent) | strategist output, risk caps | `strategist_decision` (filtered/capped) |
| **Executor (BaseAgent)** | strategist output (stances), prior `user:positions` | `_run_async_impl`: `executions`, `last_executed_tick_id`.  `after_agent_callback`: **`user:positions`, `user:thesis`** (auto-yielded by ADK as a second event) |
| MemoryWriter (BaseAgent) | (Spec B leaves untouched) | `memory_buffer`, `day_digest` (Spec C territory).  Today's bare-key `thesis` write is **dropped** — Executor's after-callback now owns that key under the `user:thesis` name. |

The verb-dispatch logic — the pure mapping from stance verb plus prior
thesis row plus fill data to (broker call, new thesis row) — lives
inside Executor's package as `src/agents/executor/_verb_dispatch.py`.
Both Executor's `_run_async_impl` (which needs the broker call) and
Executor's `after_agent_callback` (which needs the new thesis row)
import from there.  There is no cross-package shared module: the verb
semantics live in exactly one place inside the agent that owns both
the broker dispatch and the persistence write.

This *partly* matches the 2026-05-19 patch in `docs/todo-fixes.md`
item 2.5.1 (MemoryWriter as the cross-tick writer for `memory_buffer`,
`day_digest`, `thesis`): `memory_buffer` and `day_digest` remain
MemoryWriter's job for Spec C, but `user:positions` and `user:thesis`
are owned by Executor's after-callback because the assembler needs
the fill data Executor itself produces.  Today's pre-spec `positions`
bare-key writes from Executor's `_run_async_impl` are removed, and
the bare-key `thesis` write from MemoryWriter is dropped — both keys
ride the new `user:`-prefixed callback path.

#### Namespace partitioning by `app_name`

Paper, live, and backtest get disjoint ADK `user_state` rows by varying
`app_name`:

| Mode | `app_name` | `user_id` | Effect |
|------|------------|-----------|--------|
| Live | `"StockBot-live"` | `"stockbot"` | Persistent across Cloud Run Job invocations |
| Paper | `"StockBot-paper"` | `"stockbot"` | Persistent across Cloud Run Job invocations |
| Backtest | `"StockBot-backtest-<window_id>"` | `"stockbot"` | Per-window namespace; re-running a window from cold start cannot inherit prior-run thesis |

`<window_id>` matches `window.id` in `src/backtest/windows.py` (e.g.
`"baseline-2025-09"`).  Per-run isolation within a window (re-running
the same window with `--fresh`) is handled by deleting the
`runs/<run-id>/session.sqlite` file, not by varying `user_id`.

Today's hardcoded `app_name="StockBot"` in
`src/orchestrator/tick.py:179, 217` is replaced by a mode-dispatched
value.  Pre-deployment: no existing `user_state` rows to migrate.

#### Why no separate LifecycleWrapper

The natural read of `docs/contract-invariants.md` §C-Rule 7 ("pipeline
reads from state only; lifecycle owns persistence reads/writes") is that
persistence lives outside the state dict.  With ADK `user:`-prefixed
state, the `DatabaseSessionService` IS the persistence layer — there is
no separate wrapper to introduce.  The live entry point
(`src/orchestrator/tick.py`) and the backtest driver
(`src/backtest/driver.py`) augment their existing `_build_initial_state`
construction to dispatch `app_name` by mode, and otherwise rely on ADK
to mediate persistence.  This collapses one of
`docs/todo-fixes.md` item 2.5.3's open questions and is the explicit
amendment to Rule 7 documented in "Contract amendments" above.

### Schema — `PositionThesis`

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PositionThesis(BaseModel):
    """One row of the strategist's thesis book.

    Persisted as a value inside ``state["user:positions"]`` (keyed by
    ticker).  Round-trips through ADK's session state via
    ``model_dump()`` / ``model_validate()`` at the persistence boundary.

    Field lifecycle
    ---------------
    - ``opened_at``, ``opened_tick_id``, ``opened_price`` are written
      once when the position is opened and are immutable thereafter.
    - ``weight`` is mutated by the executor on every ``add``/``trim``.
    - ``target_price``, ``stop_price``, ``catalyst``, ``horizon`` are
      mutable via the ``update`` stance (no trade) or any other
      stance that supplies them.
    - ``rationale`` is FROZEN at open.  It captures the entry
      commitment.  If the thesis genuinely changes, the right action
      is ``close`` then ``open`` — not a verbal revision.
    - ``last_reviewed_at`` and ``last_reviewed_decision`` track the
      most recent tick that touched this row.
    - ``last_reviewed_reason`` is persisted for the audit trail but is
      NOT rendered into the next tick's prompt (Principle 2).
    """

    # Identity --------------------------------------------------------
    ticker: str = Field(..., description="Ticker symbol, e.g. 'AVGO'.")

    # Entry record (immutable after open) -----------------------------
    opened_at: datetime = Field(
        ...,
        description=(
            "Timestamp (UTC) of the tick on which the position was opened.  "
            "Matches the convention for ``state['as_of']`` in "
            "``docs/contract-invariants.md`` §A."
        ),
    )
    opened_tick_id: str = Field(
        ...,
        description="Tick identifier captured at open time, for traceability.",
    )
    opened_price: float = Field(
        ...,
        description="Fill price recorded by the executor at open.",
    )

    # Current sizing (mutated by add/trim) ----------------------------
    weight: float = Field(
        ...,
        description="Current portfolio weight in [0, 1].",
    )

    # Commitments (mutable via 'update' stance) -----------------------
    target_price: float | None = Field(
        None,
        description="Optional price level at which the thesis would be confirmed.",
    )
    stop_price: float | None = Field(
        None,
        description="Optional price level below which the thesis is invalidated.",
    )
    catalyst: str | None = Field(
        None,
        description="Free-form text describing the event that would confirm the thesis.",
    )
    horizon: Literal["intraday", "swing", "long_term"] = Field(
        ...,
        description="Time horizon over which the thesis is expected to play out.",
    )

    # Entry rationale (FROZEN at open) --------------------------------
    rationale: str = Field(
        ...,
        description=(
            "The strategist's reasoning at the moment of opening the position. "
            "Immutable for the lifetime of the position — if the underlying "
            "thesis changes, the right action is close + reopen."
        ),
    )

    # Review trail ----------------------------------------------------
    last_reviewed_at: datetime = Field(
        ...,
        description="Timestamp of the most recent tick whose stance touched this row.",
    )
    last_reviewed_decision: Literal["open", "add", "trim", "hold", "update"] = Field(
        ...,
        description=(
            "Stance verb that produced the most recent review.  Set to "
            "'open' on initial entry (the row's lifetime begins with the "
            "open stance, which counts as the first review).  Never "
            "'close' — close deletes the row."
        ),
    )
    last_reviewed_reason: str = Field(
        ...,
        description=(
            "The strategist's 'what's changed since opening' articulation on the "
            "most recent review.  Persisted to the audit trail; NOT rendered "
            "back into the next tick's prompt."
        ),
    )
```

### Stance vocabulary

The strategist's output schema (`TickerStance`) gains two new `intent`
values: `hold` and `update`.  The full vocabulary becomes:

| Verb | Broker call | `state["user:positions"]` effect | TradeLogRow |
|------|-------------|----------------------------------|-------------|
| `open` | buy to `weight` | new row written | open record (if S6 splits open/close) |
| `add` | buy delta to reach `weight` | `weight` updated; review fields touched | open record |
| `trim` | sell delta to reach `weight` | `weight` updated; review fields touched | none |
| `close` | sell to zero | row deleted | close record |
| `hold` | **none** | review fields only | none |
| `update` | **none** | target / stop / catalyst / horizon updated; review fields touched | none |

The risk gate (currently between strategist and executor) treats `hold`
and `update` as no-trade verbs and passes them through unchanged.  Only
the four trading verbs (`open`, `add`, `trim`, `close`) are subject to
position-size caps, turnover caps, and cash-floor enforcement.

### Prompt structure

The strategist prompt template gains a `{strategist_mode}` placeholder
and a redesigned held-view rendering.  The full template structure is:

```
You are the StockBot strategist agent...
[unchanged role description]

## Mode
{strategist_mode}

## Current State
Portfolio:  {portfolio}
Thesis:     {thesis}

## Held Positions
{temp:held_positions_view}

## Watchlist Evidence
{temp:ticker_evidence}

## Memory Buffer  ← stays empty for Spec B; populated by Spec C
{memory_buffer}

## Day Digest    ← stays empty for Spec B; populated by Spec C
{day_digest}

## Output Requirements
For each held position above, you MUST emit exactly one TickerStance with
intent ∈ {hold, trim, close, update}.  The 'reason' field must articulate
what has changed since you opened the position (price evolution, catalyst
status, time elapsed, evidence shift) — even if your decision is hold.

For tickers on the watchlist that you do NOT currently hold, the active
stances model applies: emit a TickerStance only for tickers you want to
open.  Omitting a flat ticker carries no implicit commitment.

[schema description...]
```

The `{strategist_mode}` text is one of:

**Cold start mode** (when `len(state["user:positions"]) == 0`):

```
Cold start — your portfolio is empty.  No prior open positions to evaluate.
Build an initial portfolio by scanning the watchlist evidence below.  Open
1-3 high-conviction entries.  You may also write or revise the standing
market thesis if you have a view.
```

**Incremental mode** (when `len(state["user:positions"]) > 0`):

```
Incremental — you have {N} held positions opened on prior ticks.  Each is
rendered below with the commitments you made on entry and the evolution
since.  For every held position you MUST emit a stance (hold / trim /
close / update) with a 'what has changed' reason.  You may also scan the
watchlist evidence for fresh entry candidates and open new positions.
```

The injection happens in the existing `StrategistContextShim`
(`src/agents/strategist/context_shim.py`) via `state_delta` — Spec B
extends the shim to compute and inject `temp:strategist_mode` alongside
the existing `temp:held_positions_view` and `temp:ticker_evidence`.

### Held-view rendering

For each held position, the held-view renders:

```
{TICKER}
  Opened on {date} at ${opened_price}  (tick {opened_tick_id})
  Your commitments on entry:
    Rationale:  {rationale}
    Target:     ${target_price}  (+X.X% from entry)
    Stop:       ${stop_price}    (-X.X% from entry)
    Catalyst:   {catalyst}
    Horizon:    {horizon}
  Evolution:
    Held for:   {N} ticks · {M}h · {D} trading days
    Now:        ${current_price}  (+X.X% from entry)
    To target:  +$X.XX  (+Y.Y% from now)
    To stop:    -$X.XX  (-Y.Y% from now)
    Reviewed:   {last_reviewed_at} ({last_reviewed_decision})
```

Notes on what is *not* rendered:

- `last_reviewed_reason` — withheld per Principle 2 (the LLM should not
  read its own prior-tick justification).
- Catalyst fired/pending/expired classification — V1 leaves this to the
  LLM's reading of the catalyst text and the elapsed-time line.

### Stance-output mechanics — D3 (carry-forward removal)

`src/agents/strategist/derivation.py:254-271` currently pads
unaccounted-for watchlist tickers with carry-forward stance values.  This
spec removes that block.  The replacement logic, in
`derive_legacy_fields`:

1. For each `ticker` in `state["user:positions"]`, require a matching
   stance in the strategist's output.  Missing stances raise a
   validation error caught by the existing retry layer
   (`src/agents/llm_retry.py`).
2. For each `ticker` in the watchlist but not in
   `state["user:positions"]`, accept any number of stances (including
   zero).
3. Build the post-tick `state["user:positions"]` dict by applying each
   stance via the executor's verb dispatch (see "Executor changes"
   below).

---

## Detailed design

### `state["user:positions"]` lifecycle

The amended `docs/contract-invariants.md` §A row (see "Contract
amendments" above) reads:

| Field | Owner | Lifetime | Source | Refresh |
|-------|-------|----------|--------|---------|
| `state["user:positions"]` | Executor's `after_agent_callback` | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table | Phase 2 read via implicit ADK merge; Phase 4 write via the callback's auto-yielded state-delta event |

Phase 2 (tick-start) behaviour:

- **Live:** a fresh ADK session is created with the per-tick initial
  state.  `DatabaseSessionService.create_session()` merges the user_state
  row for the mode-dispatched `(app_name, user_id)` pair (e.g.
  `("StockBot-live", "stockbot")`) into the returned state dict.
  `state["user:positions"]` is present from the start of the tick.
- **Backtest:** same mechanism, with `DatabaseSessionService` pointed at
  `runs/<run-id>/session.sqlite` and `app_name` set to
  `f"StockBot-backtest-{window.id}"`.  `user_id` stays `"stockbot"`.  No
  `state.update(dict(...))` carry between ticks (the existing pattern in
  `src/backtest/driver.py:251-253` is removed for `positions`).
- **Paper:** identical to live, with `app_name="StockBot-paper"`.

Phase 4 (tick-end) behaviour:

- Executor's `_run_async_impl` runs broker calls and yields one
  `state_delta` event carrying `executions` and `last_executed_tick_id`.
- Executor's `after_agent_callback` then runs (still inside the same
  invocation of the Executor agent).  It reads Strategist's stances
  from `state["strategist_decision"]`, the just-emitted fills from
  `state["executions"]`, and the prior `user:positions` already in
  `state`, applies `apply_stance_to_thesis(...)` per stance, and writes
  `ctx.state["user:positions"] = …` plus
  `ctx.state["user:thesis"] = …`.
- ADK's `_handle_after_agent_callback` detects the accumulated delta
  and auto-yields a second `Event` from the Executor invocation whose
  `actions.state_delta` carries both keys.  The runner ingests this
  event through `SessionService.append_event`;
  `DatabaseSessionService` persists `user:`-prefixed keys to the
  `user_state` table.
- No separate Phase 4 lifecycle agent is needed.  Crash recovery falls
  out for free: any `state_delta` event already ingested before the
  crash is durable; any event after the crash is lost (the next tick
  re-reads the last-good state).  Because the after-callback assembles
  the full new dicts and writes both keys in one auto-yielded event,
  the cross-tick state is all-or-nothing per *callback* — see Invariant
  2 for the (intentional) asymmetry where Executor's broker-effect
  event ingests before the callback delta would, leaving a real broker
  action without a persisted thesis update on mid-tick failure.

### `state["user:thesis"]` lifecycle

Same persistence mechanism as `state["user:positions"]`.  Active-model:
the strategist emits a thesis revision only when it wants to change the
text; omission leaves the prior thesis in place.  The thesis is a
free-form string; the strategist's output schema gains an optional
`thesis_revision: str | None` field.  Executor's `after_agent_callback`
consumes `thesis_revision`: when non-null, the new value is written to
`state["user:thesis"]` via the callback (same auto-yielded event as
`user:positions`).  When null, the prior `user:thesis` is carried
forward (the callback re-writes the prior value to its own event so the
event payload remains explicit — the carry-forward is a deliberate
re-write, not an absence).

This namespace-shifts the key from bare `thesis` to `user:thesis` and
collapses the 2026-05-19 patch's planned MemoryWriter ownership of
`thesis` into the new Executor-callback writer.  MemoryWriter's bare-
key `thesis` write is dropped in the same change.

### Strategist context shim — `temp:strategist_mode` injection

`src/agents/strategist/context_shim.py` is extended to compute and
inject the mode header text:

```python
async def _build_temp_state(
    state: dict,
) -> dict:
    """Compute temp:* fields rendered into the strategist prompt.

    Reads ``state["user:positions"]`` to determine whether the
    strategist is in cold-start or incremental mode, and emits the
    corresponding header text under ``temp:strategist_mode``.
    """

    positions = state.get("user:positions", {})

    # Mode header — drives the cold-start vs incremental framing
    # (see "Prompt structure" in the spec).
    if not positions:
        mode_text = COLD_START_MODE_TEMPLATE
    else:
        mode_text = INCREMENTAL_MODE_TEMPLATE.format(N=len(positions))

    # ... existing temp:held_positions_view + temp:ticker_evidence logic
    return {
        "temp:strategist_mode": mode_text,
        "temp:held_positions_view": _render_held_view(positions, ...),
        "temp:ticker_evidence": ...,
    }
```

### Held-view rendering — `held_view.py`

`src/agents/strategist/held_view.py` is rewritten to read from
`state["user:positions"]` (rather than `state["positions"]`) and to
render the evolution columns documented above.  The function signature
gains the current `as_of` datetime so it can compute "held for" and
"hours since" deltas.

The "(No held positions — portfolio is flat.)" fallback is preserved for
cold-start mode but in practice is rarely seen, because the cold-start
mode header sits above the held-view and tells the LLM the same thing
more emphatically.

### Strategist output schema

Strategist emits **stances only** (one per active ticker) plus an
optional top-level `thesis_revision: str | None`.  No direct
`PositionThesis` dict ever appears in the LLM output — Executor's
`after_agent_callback` assembles the dict downstream by applying stance
verbs to the prior `state["user:positions"]` plus Executor's own fill
data.  This keeps the LLM focused on "decide" and prevents the LLM from
seeing and re-emitting its own prior-tick `PositionThesis` rows (which
would re-introduce the anti-anchoring failure mode Principle 1 closes).

The existing `TickerStance` model gains:

- Two new `intent` values: `"hold"` and `"update"`.
- A required `reason` field on every stance whose intent is
  in {`hold`, `trim`, `update`} — this captures the "what's changed since
  opening" articulation.  Existing `rationale` field on
  {`open`, `add`} stances is unchanged.
- Commitment fields (`target_price`, `stop_price`, `catalyst`,
  `horizon`) carried per-stance with verb-conditional semantics:
  - `open`: all required (these seed the new `PositionThesis`).
  - `add`: all optional; if supplied, mutates `PositionThesis`.
  - `update`: at least one of `target_price` / `stop_price` /
    `catalyst` / `horizon` required; null fields leave the existing
    value in place.
  - `hold`, `trim`, `close`: ignored if supplied.

The strategist response schema (Pydantic) also gains an optional
top-level `thesis_revision: str | None` field for the market-thesis
update mechanism (consumed by Executor's after-callback; non-null
overwrites `user:thesis`, null carries forward).

### Verb-dispatch helpers (Executor-internal)

A new module `src/agents/executor/_verb_dispatch.py` defines the
canonical mapping from `(verb, prior_thesis_row, stance_fields,
fill_price)` → `(new_thesis_row, broker_call_or_none)`.  Both Executor's
`_run_async_impl` (which needs `resolve_broker_call`) and Executor's
`after_agent_callback` (which needs `apply_stance_to_thesis`) import
from there.  The helpers are pure — no state mutation, no I/O —
and trivially testable.

The module lives **inside Executor's package** rather than at the
`src/agents/` package root because Executor is the single agent that
owns both the broker dispatch *and* the persistence write.  No cross-
package shared module is introduced; the verb semantics have exactly
one owner.

```python
# src/agents/executor/_verb_dispatch.py (new, private to executor)

def resolve_broker_call(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
) -> BrokerCall | None:
    """Map a stance to the broker call it requires (None for no-trade verbs).

    Pure function — no state mutation, no I/O.  Hold and update stances
    return ``None`` so the executor skips the broker dispatch for them.
    """
    # ... dispatch on stance.intent (open/add/trim → buy delta;
    # close → sell-all; hold/update → None) ...


def apply_stance_to_thesis(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
    fill_price: float | None,
    tick_id: str,
    as_of: datetime,
) -> PositionThesis | None:
    """Map a stance + fill data to the new ``PositionThesis`` row for that ticker.

    Returns ``None`` for close (deletes the row).  Pure function — the
    Executor after_agent_callback calls this for each stance and builds
    the new ``user:positions`` dict from the returns.

    ``fill_price`` is the actual fill from Executor's broker call, used
    to set ``opened_price`` on ``open`` and to update the executed
    weight on ``add`` / ``trim``.  ``None`` for ``hold`` / ``update``
    (no broker call ran).
    """
    # ... dispatch on stance.intent (open: new row with fill_price as
    # opened_price; add/trim: weight update; close: return None;
    # hold: review fields only; update: target/stop/catalyst/horizon
    # mutation + review fields) ...
```

### Executor — broker calls plus thesis-book writer

`src/agents/executor/agent.py` gains an `after_agent_callback` that
owns the `user:positions` / `user:thesis` write.  Executor's
`_run_async_impl` is unchanged in shape — it still dispatches broker
calls and yields one `state_delta` for `executions` /
`last_executed_tick_id`.  The bare-key `state["positions"]` write in
the existing implementation is removed.

```python
class Executor(BaseAgent):
    """Translates trading verbs into broker calls and writes the thesis book.

    ``_run_async_impl`` reads ``state["strategist_decision"]`` for the
    (risk-gated) stances, runs the broker calls in stance order,
    captures fill prices, and yields a single ``state_delta`` carrying
    ``executions`` and ``last_executed_tick_id``.

    ``after_agent_callback`` assembles the new ``user:positions`` /
    ``user:thesis`` from the stances + the just-emitted fills + the
    prior ``user:positions``, and writes them via ``ctx.state[…] = …``.
    ADK auto-yields a second state-delta event from the accumulated
    delta on ``Context.state``; ``DatabaseSessionService`` persists the
    ``user:``-prefixed keys to the user_state table.
    """

    def __init__(self, *, broker, name="executor"):
        super().__init__(
            name=name,
            after_agent_callback=_executor_thesis_writer_callback,
        )
        self._broker = broker

    async def _run_async_impl(self, ctx):
        decision = ctx.state["strategist_decision"]
        executions: list[ExecutionRow] = []

        for stance in decision.stances:
            call = resolve_broker_call(
                stance,
                prior_row=ctx.state.get("user:positions", {}).get(stance.ticker),
            )

            if call is None:
                # Hold / update verbs — no broker dispatch.
                executions.append(ExecutionRow(stance=stance, fill_price=None))
                continue

            fill_price = await self._broker.execute(call)
            executions.append(ExecutionRow(stance=stance, fill_price=fill_price))

        yield Event(
            actions=EventActions(state_delta={
                "executions":            [e.model_dump() for e in executions],
                "last_executed_tick_id": ctx.state["tick_id"],
            }),
        )


def _executor_thesis_writer_callback(callback_context):
    """Assemble user:positions / user:thesis and write via delta-tracked state.

    ADK's ``_handle_after_agent_callback`` auto-yields an Event with
    ``actions.state_delta`` containing every key written here.  The
    persistence event for the thesis book therefore rides on Rule 1
    without the callback returning a re-prompt (Rule 3) — see the
    Rule 1 amendment paragraph in "Contract amendments" above.

    Returns ``None`` (no re-prompt content).
    """

    state           = callback_context.state
    decision        = state["strategist_decision"]
    executions      = {
        row["stance"]["ticker"]: row
        for row in state.get("executions", [])
    }
    prior_positions = dict(state.get("user:positions", {}))  # shallow copy

    new_positions: dict[str, dict] = dict(prior_positions)

    for stance in decision.stances:
        ticker     = stance.ticker
        fill_price = (executions.get(ticker) or {}).get("fill_price")

        prior_row  = (
            PositionThesis.model_validate(prior_positions[ticker])
            if ticker in prior_positions else None
        )

        new_row = apply_stance_to_thesis(
            stance,
            prior_row=prior_row,
            fill_price=fill_price,
            tick_id=state["tick_id"],
            as_of=state["as_of"],
        )

        if new_row is None:
            # Close — drop the ticker.
            new_positions.pop(ticker, None)
        else:
            new_positions[ticker] = new_row.model_dump(mode="json")

    # Thesis carry-forward: explicit re-write so the event payload is
    # never an absence (see "user:thesis lifecycle" above).
    new_thesis = (
        decision.thesis_revision
        if decision.thesis_revision is not None
        else state.get("user:thesis", "")
    )

    # Delta-tracked writes — ADK auto-yields a state-delta event.
    state["user:positions"] = new_positions
    state["user:thesis"]    = new_thesis

    return None
```

The shape of the callback — read-from-state, compute-pure, write-via-
delta-tracked-state — keeps it trivially testable.  The Rule 1
clarification covers why the auto-yielded state-delta event satisfies
the cross-tick persistence requirement without an additional writer
agent (see "Contract amendments" above).

### MemoryWriter — unchanged by Spec B

The existing `src/agents/memory_writer/agent.py` (writes `memory_buffer`,
`day_digest`, and a pre-spec bare-key `thesis` for the 2026-05-19 2.5.1
patch) is **left in place untouched** for the `memory_buffer` /
`day_digest` keys.  The bare-key `thesis` write is dropped — that key
is now owned by Executor's after-callback under the `user:thesis` name.
Spec C extends MemoryWriter to take on the experiential memory rows
(`memory_buffer`, `day_digest` namespace shifts) but Spec B touches the
agent only to remove the dropped `thesis` write.

### Risk gate

The risk gate (currently in `src/orchestrator/pipeline.py` between
strategist and executor) gains a verb-aware skip rule:

- `hold` and `update` stances pass through unchanged.
- `open`, `add`, `trim`, `close` stances are subject to the existing
  caps and floors:
  - `MIN_HELD_WEIGHT`, `MAX_POSITION_WEIGHT`, `CASH_FLOOR_WEIGHT`
  - `MAX_DELTA_PER_TICKER`, `MAX_TOTAL_TURNOVER`

If the risk gate trims a stance (e.g. caps `open` at the maximum weight),
the `PositionThesis.weight` reflects the *post-gate* weight, not the
strategist's requested weight.  This is consistent with current
behaviour for `add` / `trim`.

### Backtest changes

- `src/backtest/driver.py` switches from `InMemorySessionService` to
  `DatabaseSessionService` backed by `runs/<run-id>/session.sqlite`.
  The session-service factory is parameterised so the same code path
  serves live (`DATABASE_URL` env) and backtest
  (`runs/<run-id>/session.sqlite`).
- Today's `_trace` / `_decision_logger` keys (driver lines ~216, 225
  pre-Spec B) are **renamed under the `temp:` prefix** —
  `state["temp:_trace"]` and `state["temp:_decision_logger"]` — so ADK
  strips them from the persisted delta and the SQLAlchemy JSON
  serialiser never sees a non-serialisable `TraceWriter` /
  `DecisionLogger`.  Because `create_session(state=…)`'s seed dict
  passes through `extract_state_delta` (which discards `temp:` keys),
  the driver injects the handles by direct mutation of
  `adk_session.state` **after** `create_session(...)` returns.  See the
  §C-Rule 2 amendment for the full mechanism.
- The post-tick `state.update(dict(updated.state))` carry on lines
  ~251-253 is removed *for `positions`* — ADK now handles that.
  Other temporary keys in that carry (if any) are reviewed during
  implementation and either kept or migrated to `temp:` scope.
- `app_name` for backtest is `f"StockBot-backtest-{window.id}"`;
  `user_id` stays `"stockbot"`.  Per-run isolation within a window
  (re-running with `--fresh`) is handled by deleting the
  `runs/<run-id>/session.sqlite` file, not by varying `user_id` — see
  "Namespace partitioning by `app_name`" above.

### Live `tick.py` changes

- `src/orchestrator/tick.py:91-116` — remove the `"positions": {}` and
  `"thesis": ""` lines from `_build_initial_state`.  ADK's user_state
  merge populates `state["user:positions"]` and `state["user:thesis"]`
  automatically.  The comment at lines 67-69 about `docs/todo-fixes.md`
  item 2.5.3 is updated to point at this spec instead.
- `user_id` for live is `"stockbot"` (already used today; no change).
  Different broker modes (paper vs live) get different `app_name`
  values (`"StockBot-paper"` vs `"StockBot-live"`) so paper and live
  user_state rows are isolated.

### Pipeline wiring

`src/orchestrator/pipeline.py` requires no structural changes — the
strategist agent, context shim, risk gate, and executor are all already
in the right order.  The changes are confined to:

- The context shim (`temp:strategist_mode` injection).
- The strategist prompt template (`{strategist_mode}` placeholder; new
  output schema instructions).
- The held-view renderer (`held_view.py`).
- The strategist output schema (`TickerStance` verb enum + optional
  fields).
- `derivation.py` (D3 carry-forward removal).
- The risk gate (verb-aware skip rule).
- The executor (verb dispatch + state_delta emit).

---

## Data flow

### Tick 1 — cold start

1. **Phase 1 (run-start, live only):** Cloud Run Job invocation starts a
   fresh Python process.
2. **Phase 2 (tick-start):** `tick.run_once()` calls
   `session_service.create_session(...)`.  ADK reads the `user_state`
   table for the mode-dispatched `(app_name, user_id)` pair (e.g.
   `("StockBot-live", "stockbot")` for live, or
   `("StockBot-backtest-baseline-2025-09", "stockbot")` for backtest) —
   empty on first ever tick.  `state["user:positions"]` = `{}` and
   `state["user:thesis"]` = `""` (or absent — the shim defaults to
   empty).
3. **Phase 3 (during-tick):**
   - `StrategistContextShim` computes
     `temp:strategist_mode` = cold-start text,
     `temp:held_positions_view` = "(No held positions — portfolio is flat.)",
     `temp:ticker_evidence` = watchlist evidence block.
   - Strategist LLM call produces a response with stances for 1-3
     opens and optionally a `thesis_revision`.  Output lands at
     `state["strategist_decision"]` via the agent's `output_key`.
   - Risk gate filters/caps trading stances and writes the gated
     version back to `state["strategist_decision"]`.
   - Executor's `_run_async_impl` reads `state["strategist_decision"]`,
     calls `_verb_dispatch.resolve_broker_call(...)` per stance,
     dispatches the `buy` calls to the broker, captures fill prices,
     and yields one `state_delta` for `executions` +
     `last_executed_tick_id`.
   - Executor's `after_agent_callback` runs.  It reads
     `state["strategist_decision"]` (stances + `thesis_revision`),
     `state["executions"]` (the fills just emitted by
     `_run_async_impl`), and prior `state["user:positions"]` (empty on
     cold start).  Calls
     `_verb_dispatch.apply_stance_to_thesis(...)` per stance to build
     the new positions dict, then writes
     `ctx.state["user:positions"] = …` /
     `ctx.state["user:thesis"] = …`.  ADK's
     `_handle_after_agent_callback` auto-yields a second event from the
     accumulated delta.  `DatabaseSessionService` persists both keys to
     the `user_state` table on event ingestion.
4. **Phase 4 (tick-end):** the run completes.  No explicit "lifecycle
   agent" is needed — ADK has already persisted the user state via
   Executor-callback's auto-yielded `state_delta`.

### Tick *N* — incremental

1. **Phase 1 (run-start, live):** new Cloud Run Job invocation, fresh
   Python process.
2. **Phase 2 (tick-start):** `session_service.create_session(...)`.
   ADK reads the `user_state` row written on the prior tick.
   `state["user:positions"]` contains the persisted thesis book;
   `state["user:thesis"]` contains the persisted market thesis.
3. **Phase 3 (during-tick):**
   - Shim computes
     `temp:strategist_mode` = incremental text with N filled in,
     `temp:held_positions_view` = rendered held-view with evolution
     columns,
     `temp:ticker_evidence` = watchlist evidence.
   - Strategist LLM call produces:
     - One stance per held ticker (intent ∈ {hold, trim, close, update}),
       each with a 'what's changed' reason.
     - Zero or more open stances for flat watchlist tickers.
     - Optionally a `thesis_revision`.
   - Validation: `derivation.py` checks that every held ticker is
     covered.  Missing stances → retry with a validation-error message
     fed back into the LLM via the existing
     `src/agents/llm_retry.py` layer.
   - Risk gate processes only trading verbs (open/add/trim/close);
     hold/update pass through.
   - Executor's `_run_async_impl` dispatches broker calls for trading
     verbs only; captures fills; yields `state_delta` for
     `executions` + `last_executed_tick_id`.
   - Executor's `after_agent_callback` reads stances, fills, and prior
     `user:positions`; applies verbs (deletions on close, mutations on
     add/trim/update, review-only writes on hold); writes
     `ctx.state["user:positions"] = …` and
     `ctx.state["user:thesis"] = …`.  ADK auto-yields a second event
     carrying both keys.
4. **Phase 4 (tick-end):** ADK persists the new user_state row.

### Crash recovery

If a tick crashes mid-Phase 3:

- Any `state_delta` event already ingested by `DatabaseSessionService`
  is durable.
- Any event after the crash is lost.
- The next tick starts from the last-good user_state row.

Executor's after-callback assembles the full new `user:positions` /
`user:thesis` dicts and writes both keys; ADK auto-yields one event
carrying the whole payload.  The cross-tick state is therefore all-or-
nothing per *callback*: either both keys land or neither does.
Partial application would leave the thesis book inconsistent with the
broker portfolio.  Note that Executor's earlier `state_delta` for
`executions` will already have been ingested by the time the callback
runs, so a crash *between* the two events — the broker-effect event
and the auto-yielded persistence event — leaves a real broker action
without a recorded thesis update.  This asymmetry is intentional and
documented in Invariant 2 (reconciliation drift logged, not auto-
healed).

---

## Error handling & invariants

### Invariant 1 — Held positions are exhaustively covered

After Phase 3, for every `ticker` in the pre-tick
`state["user:positions"]`, exactly one of the following has occurred:

- A stance with that ticker exists in the strategist's output (any of
  hold / add / trim / close / update), OR
- The validation layer has raised a retryable error and the LLM is
  retrying with feedback.

There is no path in which a held ticker silently disappears or silently
carries forward without an explicit stance.

### Invariant 2 — Broker portfolio reconciles with `state["user:positions"]`

After the executor processes all stances:

- For every `ticker` in `state["user:positions"]`, the broker's
  current portfolio holds shares of that ticker with weight ≈ the
  PositionThesis weight (within a tolerance of `MAX_DELTA_PER_TICKER`).
- For every `ticker` the broker holds, there is a row in
  `state["user:positions"]`.

Reconciliation drift between thesis book and broker is logged but not
auto-healed in Spec B.  Drift detection is a follow-on concern — flagged
in Future work.

### Invariant 3 — Rationale is immutable post-open

The executor never writes to `PositionThesis.rationale` except via the
`open` verb.  `update` stances may carry a `rationale` field in their
schema but the executor silently ignores it.  (We may make this a hard
validation error in a follow-on if the LLM tries it.)

### Invariant 4 — `last_reviewed_reason` is never read by the prompt

A test in `tests/strategist/test_held_view.py` asserts that
`last_reviewed_reason` does not appear in the rendered held-view text.
This codifies Principle 2.

### Validation rules (verb / state pre-conditions)

The strategist's output passes through a validator that rejects verbs
issued against an inconsistent state.  Rejections raise a retryable
validation error which the existing
`src/agents/llm_retry.py` layer feeds back to the LLM with a message.

Validation is **field-presence only** — no delta-magnitude prescriptions.
Whether a `trim` produced a strictly-smaller weight is the executor's and
risk-gate's concern, not the validator's.  Spurious failures on
near-no-op deltas (rounding, fractional shares, etc.) are not worth the
retry cost.

| Verb | State pre-condition | Required stance fields |
|------|---------------------|------------------------|
| `open` | ticker is flat (`NOT IN state["user:positions"]`) | `weight`, `target_price`, `stop_price`, `catalyst`, `horizon`, `rationale` |
| `add` | ticker is held | `weight` |
| `trim` | ticker is held (use `close` for total exit) | `weight` |
| `close` | ticker is held | (none) |
| `hold` | ticker is held | `reason` |
| `update` | ticker is held | `reason`, and at least one of `target_price` / `stop_price` / `catalyst` / `horizon` |

The reject messages fed back to the LLM are implementation-level — they
should name the violated rule and suggest the appropriate alternative
verb where one exists.

The "stance required per held" rule (D3) is a complementary post-condition
check: after applying all stances, every pre-tick held ticker must have
been touched.

### Schema-evolution discipline

The `PositionThesis` schema sits inside an ADK JSON blob with no migration
machinery.  To keep cross-version reads safe, the discipline is:

- Every new field added to `PositionThesis` MUST have a default value
  (either `None` for `Optional`, an explicit literal default, or a
  `default_factory`).
- Existing fields MUST NOT change type or semantics without a graduation
  to typed SQL tables (see "Future work").
- Renaming a field requires a Pydantic field alias bridging the old name
  for at least one release of cached user_state rows.

A unit test in `tests/unit/agents/strategist/test_position_thesis.py`
loads a frozen "v1" JSON dict and asserts it deserialises cleanly with
every subsequent code revision — this catches accidental breaking
changes at PR time.

---

## Testing

### Unit tests (new)

- `tests/unit/agents/strategist/test_position_thesis.py`
  - `test_position_thesis_round_trips_through_json` — serialise via
    `model_dump()`, restore via `model_validate()`, assert equality.
  - `test_position_thesis_horizon_validates_enum` — bad horizon raises
    `ValidationError`.
  - `test_position_thesis_v1_frozen_payload_deserialises` — load a
    checked-in fixture representing the V1 wire shape and assert it
    deserialises with the current code (schema-evolution discipline).

- `tests/unit/agents/strategist/test_held_view.py`
  - `test_held_view_empty_renders_cold_start_fallback`.
  - `test_held_view_renders_evolution_columns`.
  - `test_held_view_does_not_leak_last_reviewed_reason` (Invariant 4).
  - `test_held_view_computes_pct_to_target_and_stop_correctly`.
  - `test_held_view_handles_null_target_and_stop` (renders "no target
    set" rather than crashing).

- `tests/unit/agents/strategist/test_context_shim.py`
  - `test_shim_emits_cold_start_mode_when_positions_empty`.
  - `test_shim_emits_incremental_mode_when_positions_present`.
  - `test_shim_n_substitution_in_incremental_text`.

- `tests/unit/agents/strategist/test_derivation.py`
  - `test_held_ticker_without_stance_raises_validation_error` (D3).
  - `test_flat_ticker_without_stance_is_ok` (active-model preserved
    for flat tickers).

- `tests/unit/agents/executor/test_verb_dispatch.py` — covers the
  Executor-private `_verb_dispatch.py` helpers.  No agent wiring;
  pure functions only.
  - `test_resolve_broker_call_open_returns_buy_to_weight`.
  - `test_resolve_broker_call_close_returns_sell_all`.
  - `test_resolve_broker_call_hold_returns_none`.
  - `test_resolve_broker_call_update_returns_none`.
  - `test_apply_stance_open_seeds_new_position_with_fill_price`.
  - `test_apply_stance_hold_touches_review_fields_only`.
  - `test_apply_stance_update_mutates_target_stop_catalyst_horizon`.
  - `test_apply_stance_update_does_not_mutate_rationale` (Invariant 3).
  - `test_apply_stance_close_returns_none_signalling_deletion`.
  - `test_apply_stance_add_preserves_rationale`.

- `tests/unit/agents/executor/test_executor.py`
  - `test_executor_dispatches_buy_for_open_stance`.
  - `test_executor_dispatches_sell_for_close_stance`.
  - `test_executor_no_broker_call_for_hold_stance`.
  - `test_executor_no_broker_call_for_update_stance`.
  - `test_executor_runtime_yields_state_delta_for_executions_only` —
    assert Executor's `_run_async_impl` yields a `state_delta` carrying
    only `executions` + `last_executed_tick_id`, NOT `user:positions`
    or `user:thesis`.
  - `test_executor_open_on_existing_ticker_raises_validation_error`.
  - `test_executor_close_on_flat_ticker_raises_validation_error`.

- `tests/unit/agents/executor/test_thesis_writer_callback.py` — covers
  the `after_agent_callback` that writes `user:positions` /
  `user:thesis`.  Uses an in-memory ADK `CallbackContext` fixture; no
  broker, no LLM.
  - `test_callback_assembles_new_positions_from_open_stance`.
  - `test_callback_uses_executor_fill_price_for_opened_price`.
  - `test_callback_carries_forward_user_thesis_when_revision_null`.
  - `test_callback_overwrites_user_thesis_when_revision_non_null`.
  - `test_callback_close_deletes_ticker_from_user_positions`.
  - `test_callback_hold_only_touches_review_fields`.
  - `test_callback_writes_register_in_state_delta` — assert
    `callback_context.state.has_delta()` is True and `_event_actions
    .state_delta` carries both `user:positions` and `user:thesis`
    after the callback returns.
  - `test_callback_returns_none_no_reprompt` — Rule 3 conformance.

- `tests/unit/orchestrator/test_risk_gate.py`
  - `test_risk_gate_passes_hold_through_unchanged`.
  - `test_risk_gate_passes_update_through_unchanged`.
  - `test_risk_gate_caps_open_at_max_position_weight`.

### Integration tests (new)

- `tests/integration/test_thesis_persistence_round_trip.py`
  - Spin up a `DatabaseSessionService` against an in-memory SQLite,
    create a session for `(app_name="StockBot-test", user_id="stockbot")`,
    write `state["user:positions"] = {...}` via `state_delta`, close
    the session, create a NEW session for the same `(app_name,
    user_id)`, assert that `state["user:positions"]` arrives populated.

- `tests/integration/test_phase2_hydration_from_db_only.py` —
  amendment §B Phase 2 invariant test (finding D from the critique).
  Scenario: process A writes `user:positions` via `state_delta`; the
  same in-memory `DatabaseSessionService` instance is torn down; a
  fresh service is instantiated against the same SQLite; process B
  creates a new session; assert `state["user:positions"]` is the value
  process A wrote.  In particular, no leftover in-process state can
  contribute — only the DB row.

- `tests/integration/test_state_delta_user_prefix_end_to_end.py` —
  finding H.  Build a minimal pipeline (strategist stub → risk-gate
  noop → executor with its after-callback wired), run one tick against
  an in-memory `DatabaseSessionService`, assert that the `user_state`
  row for `(app_name, user_id)` contains the expected `user:positions`
  and `user:thesis` after the tick.  Confirms ADK accepts
  `user:`-prefixed keys auto-yielded from `after_agent_callback`
  state-delta writes and persists them as user-scoped.

- `tests/integration/test_namespace_partitioning.py` — finding K.
  Two sessions, same `user_id`, different `app_name` (e.g. paper vs
  live).  Assert their `user:positions` are disjoint (writing to one
  does not affect the other).

- `tests/integration/test_symmetric_live_backtest_persistence.py` —
  finding M.  Process A writes user_state via the live entry-point
  (`tick.run_once()`); process B reads via the backtest driver path
  (same `app_name`).  Asserts symmetric mechanism — both code paths
  go through the same ADK `DatabaseSessionService` and observe the
  same `user_state` row.

- `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`
  - Run a 5-tick backtest with seeded portfolio against a stub LLM
    that echoes its prompt back.  Assert that the prompts are
    structurally different on ticks 2-5 vs tick 1 (specifically: the
    `Mode` header text differs, and the `Held Positions` block is
    non-empty on ticks 2-5).

### Smoke test (live)

- One-tick live smoke against a paper broker with a seeded
  pre-existing position in the broker portfolio.  Assert that the
  rendered prompt's held-view block includes the seeded position
  (after the first MemoryWriter-driven sync — exact reconciliation
  semantics are a follow-on concern).

### Regression tests (modify existing)

- Tests that today assert against `state["positions"]` need to be
  updated to assert against `state["user:positions"]`.  Grep:
  `state\["positions"\]` across `tests/`.
- Backtest-driver tests that mock `InMemorySessionService` need to be
  updated to mock `DatabaseSessionService` (or to use a real
  in-memory SQLite via the same service).
- Re-verify the 2026-05-19 2.5.1 patch after the namespace shift.
  Executor's `_run_async_impl` yields `executions` and
  `last_executed_tick_id` (the bare `positions` write is removed); its
  new `after_agent_callback` auto-yields `user:positions` and
  `user:thesis`.  MemoryWriter retains `memory_buffer` and `day_digest`
  bare-key writes (Spec C territory); the pre-spec bare `thesis` write
  is dropped.  Any test asserting the exact set of keys yielded by
  either agent — or asserting MemoryWriter is the writer of
  `thesis` — needs updating.

---

## Out of scope / future work

### Graduate to typed SQL tables

If schema evolution becomes painful (e.g. adding a new field requires
backfilling every historical user_state row) or if analytics
queryability matters (e.g. "show me all positions opened in the last
week with target_price > current_price"), the persistence model
graduates to typed SQLAlchemy tables:

- New ORM models `PositionThesisRow` and `ThesisRow` in
  `src/orchestrator/persistence.py`, alongside the existing
  `BufferEntryRow`, `TradeLogRow`, etc.
- A Phase 2 hydrator agent reads from the new tables and emits
  `state_delta` to populate `state["positions"]` (no `user:` prefix
  needed at that point).
- A Phase 4 lifecycle agent reads from final state and upserts the
  tables.

This is a strictly additive migration: the V1 ADK-native path stays
in place until the graduation cuts over.

### Position-level review history

Today `last_reviewed_reason` overwrites on every tick — only the most
recent reason is preserved.  A follow-on could persist a rolling
history (last K reviews) for retrospective analysis, with the same
non-rendered-to-prompt discipline.

### Catalyst lifecycle classification

V1 leaves the catalyst as free-form text and the LLM as the
classifier.  A follow-on could parse catalyst text into structured
(date, condition) tuples and compute fired / pending / expired status
mechanically.  Likely needs an LLM-assisted parser at open time and a
deterministic clock-advance at evaluation time.

### Anti-anchoring A/B audit

We assert (Principle 1) that showing the rationale verbatim is better
than hiding it, on the grounds that hiding it discards the thesis.
After Spec B ships and produces non-identical rationale across ticks,
an A/B comparison of the two rendering modes against a stable backtest
window would let us verify or revise the claim.

### Thesis-book ↔ broker reconciliation

Invariant 2 above says drift between
`state["user:positions"]` and the broker portfolio is logged but not
auto-healed.  A follow-on spec defines reconciliation semantics (e.g.
when the broker shows a holding with no thesis row, auto-write a
"synthetic" thesis flagged as needs-review).

### Multi-portfolio support

Today the live tick uses a single `(app_name, user_id)` pair.  If
multiple portfolios are ever run, each needs its own `user_id`.  The
plumbing already supports this; the only change is configuration.

---

## Implementation notes

### File-level changes (forward index)

| File | Change |
|------|--------|
| `src/agents/strategist/prompts.py` | New `STRATEGIST_INSTRUCTION` with `{strategist_mode}` placeholder; new `COLD_START_MODE_TEMPLATE` and `INCREMENTAL_MODE_TEMPLATE` constants. |
| `src/agents/strategist/context_shim.py` | Compute and emit `temp:strategist_mode`; read `state["user:positions"]` instead of `state["positions"]`. |
| `src/agents/strategist/held_view.py` | Rewrite to render evolution columns; read from `state["user:positions"]`; accept `as_of` parameter. |
| `src/agents/strategist/derivation.py` | Delete lines 254-271 (carry-forward); add "stance required per held" validation. |
| `src/agents/strategist/schemas.py` (or equivalent) | Add `hold` and `update` to `TickerStance.intent` enum; add optional `reason`, `target_price`, `stop_price`, `catalyst`, `horizon`, `rationale` per-stance fields; add top-level `thesis_revision: str \| None`. |
| `src/agents/strategist/position_thesis.py` (new file) | The `PositionThesis` model. |
| `src/agents/executor/_verb_dispatch.py` (new file) | Private (Executor-internal) verb→broker-call and verb→thesis-row helpers (`resolve_broker_call`, `apply_stance_to_thesis`).  Imported by Executor's `_run_async_impl` and Executor's `after_agent_callback`.  No cross-package consumers. |
| `src/agents/executor/agent.py` | Add `after_agent_callback=_executor_thesis_writer_callback` to the Executor constructor.  Reshape `_run_async_impl` to call `resolve_broker_call`, dispatch broker calls, and yield `state_delta` for `executions` + `last_executed_tick_id` only — drop the bare-key `state["positions"]` write.  Implement `_executor_thesis_writer_callback`: read stances + executions + prior `user:positions`, call `apply_stance_to_thesis` per stance, write `ctx.state["user:positions"] = …` / `ctx.state["user:thesis"] = …`, return `None`. |
| `src/agents/memory_writer/agent.py` | Drop the pre-spec bare-key `thesis` write (the key is now Executor's after-callback's responsibility under `user:thesis`).  Other behaviour (`memory_buffer`, `day_digest`) unchanged — Spec C territory. |
| `src/orchestrator/pipeline.py` | Verb-aware risk-gate skip rule.  No structural change to agent ordering — Executor's after-callback runs inside the Executor agent invocation, no new pipeline slot. |
| `src/orchestrator/state.py` | Update `TickState` to reflect `user:positions` and `user:thesis` (or remove the entries that have migrated to user scope). |
| `src/orchestrator/tick.py` | Drop `positions` / `thesis` from `_build_initial_state` (rely on ADK user_state merge); mode-dispatch `app_name` to `"StockBot-live"` / `"StockBot-paper"` (paper vs live broker mode).  Update the obsolete 2.5.3 todo-fixes comment at lines 67-69 to point at this spec. |
| `src/orchestrator/persistence.py` | Parameterise `make_session_service(db_url=…)` so backtest can point it at a per-run SQLite; live keeps `DATABASE_URL`. |
| `src/backtest/driver.py` | Switch from `InMemorySessionService` to `DatabaseSessionService`; rename `state["_trace"]` → `state["temp:_trace"]` and `state["_decision_logger"]` → `state["temp:_decision_logger"]` at every read site; move the handle-injection point to direct mutation of `adk_session.state` **after** `create_session(...)` returns (since `temp:` keys in the `state=` seed are discarded by `extract_state_delta`); remove `state.update(dict(...))` carry for `positions`; set `app_name=f"StockBot-backtest-{window.id}"`, `user_id="stockbot"`. |
| `src/backtest/runner.py` | Wire the new per-run session-service path; delete `runs/<run-id>/session.sqlite` on `--fresh` rerun. |
| `src/observability/trace.py`, `src/agents/analysts/**/fetch*.py`, `src/agents/strategist/{agent.py,context_shim.py}`, `src/agents/executor/agent.py` (read sites) | Rename `state.get("_trace")` → `state.get("temp:_trace")` and `state.get("_decision_logger")` → `state.get("temp:_decision_logger")` at every read site (~12 occurrences — see §C-Rule 2 amendment). |
| `docs/contract-invariants.md` | Apply §A row amendments for `positions`/`thesis` → `user:`-prefixed with single-owner footnote (see "Contract amendments"); add §C-Rule 1 auto-yielded-callback-write clarification; add §C-Rule 2 `temp:_trace` / `temp:_decision_logger` registration; add §C-Rule 7 clarification paragraph. |

### Configuration changes

- `config/data.json` (or wherever applicable): no changes required — the
  `DATABASE_URL` is already an environment variable.
- `config/README.md`: no changes (no new config keys for Spec B).

### `graphify-out/graph_delta.md` entry

After implementation, append a dated entry to
`graphify-out/graph_delta.md` documenting:

- **New modules:**
  - `src/agents/strategist/position_thesis.py` — exports
    `PositionThesis` (Pydantic v2 model).
  - `src/agents/executor/_verb_dispatch.py` — Executor-private
    helpers `resolve_broker_call(stance, *, prior_row) -> BrokerCall |
    None` and `apply_stance_to_thesis(stance, *, prior_row,
    fill_price, tick_id, as_of) -> PositionThesis | None`.  Pure
    functions; imported by Executor's `_run_async_impl` and
    Executor's `after_agent_callback` only.
- **New function:** `_executor_thesis_writer_callback` in
  `agents.executor.agent` — `after_agent_callback` registered on
  Executor; reads stances + executions + prior `user:positions` and
  writes new `user:positions` / `user:thesis` via delta-tracked
  `ctx.state[…] = …`.
- **New `TickerStance.intent` enum members:** `hold`, `update`
  (added to `agents.strategist.schemas`).
- **New constants in `agents.strategist.prompts`:**
  `COLD_START_MODE_TEMPLATE`, `INCREMENTAL_MODE_TEMPLATE`.
- **New call edges:**
  - `agents.executor.agent._run_async_impl` →
    `agents.executor._verb_dispatch.resolve_broker_call`.
  - `agents.executor.agent._executor_thesis_writer_callback` →
    `agents.executor._verb_dispatch.apply_stance_to_thesis`.
  - `orchestrator.tick._build_initial_state` → no longer references
    bare keys `positions` / `thesis` (those entries deleted from the
    seed dict).
- **Removed call edges:**
  - `agents.executor.agent` no longer yields a `state_delta` key for
    `positions` from `_run_async_impl`.
  - `agents.memory_writer.agent` no longer yields a `state_delta` key
    for the bare-key `thesis`.
  - `agents.strategist.derivation` carry-forward block removed
    (lines 254-271 in the pre-spec source).
- **State-key migrations (worth a one-liner in the delta so
  downstream tooling can pick them up):**
  - `state["positions"]` → `state["user:positions"]`.
  - `state["thesis"]` → `state["user:thesis"]`.
  - `state["_trace"]` → `state["temp:_trace"]`.
  - `state["_decision_logger"]` → `state["temp:_decision_logger"]`.
  - `state["held_view_at_decision"]` continues to be set by the
    strategist context shim; its provenance shifts from the
    in-tick `positions` dict to the persisted `user:positions` row.

### Open implementation questions

These can be resolved during plan-writing — none block the design:

1. **Backtest `user_id` collision policy.**  Re-running the same window
   currently overwrites artefacts under `runs/<run-id>/`.  With per-run
   `user_id`, the user_state row is also per-run — safe.  But should
   `runs/<run-id>/session.sqlite` be deleted on each rerun, or are
   incremental reruns a real use case?  Bias: delete on rerun for
   determinism, but confirm during plan.
2. **`temp:strategist_mode` formatting.**  Embedded in the template as
   a literal placeholder or rendered into the instruction string at
   shim time?  The latter requires the shim to build the full
   instruction text rather than just inject a temp key — small but
   meaningful design difference.
3. **`reason` field length cap.**  Should we cap the LLM's
   `what's changed` prose at, e.g. 200 characters, to keep prompt
   inflation bounded as positions age?  Bias: yes, with a soft cap
   (truncate at render-time, never at validation-time).
4. **Test fixture for V1 wire shape.**  The schema-evolution test
   needs a frozen JSON fixture representing the canonical V1
   serialisation of `PositionThesis`.  Generated once during
   implementation, then immutable.  Location: probably
   `tests/fixtures/position_thesis_v1.json`.
