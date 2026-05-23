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

**Writer-of-record split (post-critique).**  Strategist's LlmAgent
*decides* the new thesis state by reasoning, but `LlmAgent`s cannot route
a subset of their output to a specific state key, and
`docs/contract-invariants.md` §C-Rule 3 forbids callbacks from yielding
events.  The actual `EventActions(state_delta=...)` writes are emitted by
**MemoryWriter** for both `user:positions` (assembled from the prior dict +
Strategist's stances + Executor's fills) and `user:thesis` (passthrough of
the optional `thesis_revision` field, else carry-forward).  Executor runs
broker calls, captures fills, and yields its own `state_delta` for
`executions` / `last_executed_tick_id` only.  This matches the 2026-05-19
2.5.1 patch's intent (MemoryWriter as the cross-tick persistence writer)
and lets §A keep "Strategist owns" semantics while naming MemoryWriter as
the writer-of-record in a footnote.

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

The two cross-tick rows are repainted under the `user:` prefix and gain a
writer-of-record line.  The amended rows read:

| State key | Owner | Lifetime | Source | Refresh |
|-----------|-------|----------|--------|---------|
| `state["user:positions"]` | Strategist (decides) / **MemoryWriter (writer-of-record)** | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2: implicit ADK merge into the fresh session.  Phase 4: MemoryWriter emits `EventActions(state_delta={"user:positions": ...})`. |
| `state["user:thesis"]` | Strategist (decides) / **MemoryWriter (writer-of-record)** | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table, keyed by `(app_name, user_id)` | Phase 2: implicit ADK merge into the fresh session.  Phase 4: MemoryWriter emits `EventActions(state_delta={"user:thesis": ...})` when the strategist's optional `thesis_revision` field is non-null; otherwise carry-forward. |

Footnote attached to both rows:

> *Strategist's `LlmAgent` reasons about and produces the thesis content
> through its output schema, but cannot itself yield the persistence
> event (§C-Rule 3 forbids callbacks from yielding events; `LlmAgent`s
> route their entire output blob through a single `output_key`).
> MemoryWriter is the BaseAgent that emits the `state_delta`; it
> assembles `user:positions` by applying Strategist's stance verbs to the
> prior dict plus Executor's fill data, and passes `user:thesis` through
> from Strategist's `thesis_revision` field.*

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
state into the returned state dict.  When MemoryWriter emits a
`state_delta` that writes to `state["user:positions"]`,
`DatabaseSessionService` persists the change at the user level — so the
next tick's fresh session sees it.

The pipeline reads and writes two new cross-tick keys:

- `state["user:positions"]: dict[str, dict]`  — keyed by ticker, value is
  a serialised `PositionThesis`.
- `state["user:thesis"]: str` — the standing market thesis.

That is the entirety of the new persistence surface for Spec B.

#### Writer-of-record split

`docs/contract-invariants.md` §C-Rule 3 forbids callbacks from yielding
events, and `LlmAgent`s route their entire output blob through a single
`output_key`.  So Strategist *cannot itself* write a subset of its output
to a specific `user:`-prefixed key.  The persistence write is done by
**MemoryWriter**, a BaseAgent sequenced after Executor in the pipeline:

| Agent | Reads from state | Yields `state_delta` for |
|-------|------------------|--------------------------|
| Strategist (LlmAgent) | held-view, evidence, prior `user:thesis` | (single `output_key`, e.g. `strategist_decision`) |
| Risk gate (BaseAgent) | strategist output, risk caps | `strategist_decision` (filtered/capped) |
| Executor (BaseAgent) | strategist output (stances) | `executions`, `last_executed_tick_id` |
| **MemoryWriter (BaseAgent)** | strategist output (stances + `thesis_revision`), `executions` (fills), prior `user:positions`, prior `user:thesis` | **`user:positions`, `user:thesis`** |

MemoryWriter centralises the verb→`PositionThesis` mutation logic.
Executor centralises broker-call dispatch.  Both agents share a single
verb-dispatch helper module (`src/agents/_verb_dispatch.py`, new) so the
verb semantics are defined in exactly one place.

This matches the spirit of the 2026-05-19 patch in `docs/todo-fixes.md`
item 2.5.1 (MemoryWriter as the cross-tick writer for `memory_buffer`,
`day_digest`, `thesis`) and extends MemoryWriter to also own
`user:positions`.  Today's pre-spec `positions` writes from Executor
(bare key) are removed — Executor's `state_delta` now carries only
broker-effect keys.

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
| `state["user:positions"]` | Strategist (decides) / MemoryWriter (writer-of-record) | cross-tick (user-scoped) | ADK `DatabaseSessionService` `user_state` table | Phase 2 read via implicit ADK merge; Phase 4 write via MemoryWriter `state_delta` |

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

- Executor runs broker calls and emits `state_delta` for `executions`
  and `last_executed_tick_id` only.
- MemoryWriter (sequenced after Executor) reads Strategist's stances,
  Executor's fills, and the prior `user:positions`, then emits a single
  `state_delta` containing the new `user:positions` (and `user:thesis`).
  `DatabaseSessionService` persists both keys to the `user_state` table
  on event ingestion.
- No separate Phase 4 lifecycle agent is needed.  Crash recovery falls
  out for free: any `state_delta` event that completed before the crash
  is durable; any event after the crash is lost (the next tick simply
  re-reads the last-good state).  Because MemoryWriter emits a single
  event, the cross-tick state is all-or-nothing per tick.

### `state["user:thesis"]` lifecycle

Same persistence mechanism as `state["user:positions"]`.  Active-model:
the strategist emits a thesis revision only when it wants to change the
text; omission leaves the prior thesis in place.  The thesis is a
free-form string; the strategist's output schema gains an optional
`thesis_revision: str | None` field.  MemoryWriter consumes
`thesis_revision`: when non-null, the new value is written to
`state["user:thesis"]` via MemoryWriter's `state_delta` (the same event
that carries `user:positions`).  When null, the prior `user:thesis` is
carried forward (MemoryWriter writes the prior value to its own event so
the event payload remains explicit — the carry-forward is a deliberate
re-write, not an absence).

This matches the 2026-05-19 patch's intent (MemoryWriter as the writer
of `thesis`) and namespace-shifts the key from bare `thesis` to
`user:thesis`.

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
`PositionThesis` dict ever appears in the LLM output — MemoryWriter
assembles the dict downstream by applying stance verbs to the prior
`state["user:positions"]` plus Executor's fill data.  This keeps the LLM
focused on "decide" and prevents the LLM from seeing and re-emitting its
own prior-tick `PositionThesis` rows (which would re-introduce the
anti-anchoring failure mode Principle 1 closes).

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
update mechanism (consumed by MemoryWriter; non-null overwrites
`user:thesis`, null carries forward).

### Shared verb-dispatch helper

A new module `src/agents/_verb_dispatch.py` defines the canonical
mapping from `(verb, prior_thesis_row, stance_fields, fill_price)` →
`(new_thesis_row, broker_call_or_none, trade_log_row_or_none)`.  Both
Executor and MemoryWriter import the helper so the verb semantics are
defined in exactly one place.

```python
# src/agents/_verb_dispatch.py (new)

def resolve_broker_call(
    stance: TickerStance,
    *,
    prior_row: PositionThesis | None,
) -> BrokerCall | None:
    """Map a stance to the broker call it requires (None for no-trade verbs).

    Pure function — no state mutation, no I/O.  Executor uses this to
    decide what to send to the broker; MemoryWriter does not call it.
    Hold and update stances return ``None``.
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

    Returns ``None`` for close (deletes the row).  Pure function —
    MemoryWriter calls this for each stance and builds the new
    ``user:positions`` dict.  Executor does not call it.

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

### Executor — broker calls only

`src/agents/executor/agent.py` is reshaped to call only
`_verb_dispatch.resolve_broker_call(...)`, dispatch the resulting
`BrokerCall` (if any), capture the fill, and yield one `state_delta`:

```python
class Executor(BaseAgent):
    """Translates trading verbs into broker calls.

    Reads ``state["strategist_decision"]`` for the (risk-gated) stances,
    runs the broker calls in stance order, captures fill prices, and
    yields a single ``state_delta`` carrying ``executions`` and
    ``last_executed_tick_id``.  It does NOT touch ``user:positions`` or
    ``user:thesis`` — those are MemoryWriter's responsibility (see
    "Writer-of-record split" above).
    """

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
```

The pre-spec yield for `state["positions"]` (bare key) on
`src/agents/executor/agent.py` is removed.

### MemoryWriter — `user:positions` and `user:thesis` assembly

`src/agents/memory_writer/agent.py` (already present, today writes
`memory_buffer` / `day_digest` / `thesis` bare-key for the 2.5.1 patch)
gains the assembly of `user:positions` from stances + fills and the
namespace-shift of `thesis` → `user:thesis`:

```python
class MemoryWriter(BaseAgent):
    """Writer-of-record for cross-tick state.

    Reads:
      - ``state["strategist_decision"]`` for stances + thesis_revision.
      - ``state["executions"]`` for Executor's fills.
      - Prior ``state["user:positions"]`` (already merged into the
        session by ADK at Phase 2).
      - Prior ``state["user:thesis"]``.

    Emits a single ``state_delta`` event containing:
      - ``user:positions``: new dict, assembled via
        ``apply_stance_to_thesis(...)`` per stance.
      - ``user:thesis``: ``thesis_revision`` if non-null, else
        carry-forward.

    In Spec C this agent will also write ``user:memory_buffer`` and
    ``user:day_digest`` in the same ``state_delta``.
    """

    async def _run_async_impl(self, ctx):
        decision = ctx.state["strategist_decision"]
        executions = {
            e["stance"]["ticker"]: e for e in ctx.state.get("executions", [])
        }
        prior_positions: dict[str, dict] = ctx.state.get("user:positions", {})

        new_positions: dict[str, dict] = dict(prior_positions)  # shallow copy

        for stance in decision.stances:
            ticker = stance.ticker
            fill_price = (executions.get(ticker) or {}).get("fill_price")

            new_row = apply_stance_to_thesis(
                stance,
                prior_row=PositionThesis(**prior_positions[ticker])
                          if ticker in prior_positions else None,
                fill_price=fill_price,
                tick_id=ctx.state["tick_id"],
                as_of=ctx.state["as_of"],
            )

            if new_row is None:
                # Close — drop the ticker.
                new_positions.pop(ticker, None)
            else:
                new_positions[ticker] = new_row.model_dump(mode="json")

        new_thesis = (
            decision.thesis_revision
            if decision.thesis_revision is not None
            else ctx.state.get("user:thesis", "")
        )

        yield Event(
            actions=EventActions(state_delta={
                "user:positions": new_positions,
                "user:thesis":    new_thesis,
            }),
        )
```

The shape of the agent — read-from-state, compute-pure, yield one
`state_delta` — keeps it trivially testable and aligns with the §C-Rule
1 in-tick callback carve-out (the writes ride on an explicit event, not
a callback side-effect).

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
  serves live (`DATABASE_URL` env) and backtest (`runs/<run-id>/session.sqlite`).
- The post-tick `state.update(dict(updated.state))` carry on lines
  ~251-253 is removed *for `positions`* — ADK now handles that.
  Other temporary keys in that carry (if any) are reviewed during
  implementation and either kept or migrated to `temp:` scope.
- `user_id` for backtest is derived from the run-id, e.g.
  `f"backtest-{run_id}"`.  Each run has an isolated user_state row;
  re-running the same window does not pollute prior runs.

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
   - Executor reads `state["strategist_decision"]`, calls
     `_verb_dispatch.resolve_broker_call(...)` per stance, dispatches
     the `buy` calls to the broker, captures fill prices, and emits
     one `state_delta` for `executions` + `last_executed_tick_id`.
   - MemoryWriter reads `state["strategist_decision"]` (stances +
     `thesis_revision`), `state["executions"]` (fills), and prior
     `state["user:positions"]` (empty on cold start).  Calls
     `_verb_dispatch.apply_stance_to_thesis(...)` per stance to build
     the new positions dict.  Emits one `state_delta` carrying
     `user:positions` (newly-opened rows) and `user:thesis`
     (`thesis_revision` if non-null, else `""`).
     `DatabaseSessionService` persists both keys to the `user_state`
     table on event ingestion.
4. **Phase 4 (tick-end):** the run completes.  No explicit "lifecycle
   agent" is needed — ADK has already persisted the user state via
   MemoryWriter's `state_delta`.

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
   - Executor dispatches broker calls for trading verbs only; captures
     fills; emits `state_delta` for `executions` +
     `last_executed_tick_id`.
   - MemoryWriter reads stances, fills, and prior `user:positions`;
     applies verbs (deletions on close, mutations on add/trim/update,
     review-only writes on hold); emits one `state_delta` carrying
     new `user:positions` and `user:thesis`.
4. **Phase 4 (tick-end):** ADK persists the new user_state row.

### Crash recovery

If a tick crashes mid-Phase 3:

- Any `state_delta` event already ingested by `DatabaseSessionService`
  is durable.
- Any event after the crash is lost.
- The next tick starts from the last-good user_state row.

Because MemoryWriter emits a single `state_delta` at the end of stance
processing (not one per stance), the cross-tick state is
all-or-nothing per tick: either every stance applied, or none did.
This is the intended behaviour — partial application would leave the
thesis book inconsistent with the broker portfolio.  Note that
Executor's earlier `state_delta` for `executions` may already have
been ingested when MemoryWriter crashes, leaving a real broker action
without a recorded thesis update — this asymmetry is documented in
Invariant 2 (reconciliation drift logged, not auto-healed).

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

- `tests/unit/agents/test_verb_dispatch.py` — covers the shared
  `_verb_dispatch.py` helper.  No agent wiring; pure functions only.
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
  - `test_executor_emits_state_delta_for_executions_only` — assert
    executor's `state_delta` carries `executions` +
    `last_executed_tick_id` and does NOT touch `user:positions` or
    `user:thesis`.
  - `test_executor_open_on_existing_ticker_raises_validation_error`.
  - `test_executor_close_on_flat_ticker_raises_validation_error`.

- `tests/unit/agents/memory_writer/test_memory_writer.py`
  - `test_memory_writer_assembles_new_positions_from_open_stance`.
  - `test_memory_writer_uses_executor_fill_price_for_opened_price`.
  - `test_memory_writer_carries_forward_user_thesis_when_revision_null`.
  - `test_memory_writer_overwrites_user_thesis_when_revision_non_null`.
  - `test_memory_writer_close_deletes_ticker_from_user_positions`.
  - `test_memory_writer_hold_only_touches_review_fields`.
  - `test_memory_writer_emits_single_state_delta_with_both_keys`.

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
  finding H.  Build a minimal pipeline (strategist stub →
  executor → MemoryWriter), run one tick against an in-memory
  `DatabaseSessionService`, assert that the `user_state` row for
  `(app_name, user_id)` contains the expected `user:positions` and
  `user:thesis` after the tick.  Confirms ADK accepts `user:`-prefixed
  keys in `state_delta` and persists them as user-scoped.

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
- Re-verify the 2026-05-19 2.5.1 patch: the existing yields for
  `executions`, `positions`, `last_executed_tick_id` (Executor) and
  `memory_buffer`, `day_digest`, `thesis` (MemoryWriter) must be
  inspected after the namespace shift — `positions` and `thesis` now
  ride `user:`-prefixed keys; `memory_buffer` / `day_digest` stay bare
  until Spec C.  Any test asserting the exact keys yielded by either
  agent will need updating.

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
| `src/agents/_verb_dispatch.py` (new file) | Shared verb→broker-call and verb→thesis-row helpers (`resolve_broker_call`, `apply_stance_to_thesis`).  Imported by both Executor and MemoryWriter. |
| `src/agents/executor/agent.py` | Reshape to broker-call dispatch only.  Reads stances, calls `resolve_broker_call`, captures fills, emits `state_delta` for `executions` + `last_executed_tick_id`.  **No longer writes `state["positions"]` or `state["user:positions"]`.** |
| `src/agents/memory_writer/agent.py` | Extend to read stances + fills + prior `user:positions`; call `apply_stance_to_thesis` per stance; emit single `state_delta` carrying both `user:positions` and `user:thesis`.  Drops the pre-spec bare-key `thesis` write. |
| `src/orchestrator/pipeline.py` | Verb-aware risk-gate skip rule; confirm MemoryWriter is sequenced after Executor (it already is — no structural change). |
| `src/orchestrator/state.py` | Update `TickState` to reflect `user:positions` and `user:thesis` (or remove the entries that have migrated to user scope). |
| `src/orchestrator/tick.py` | Drop `positions` / `thesis` from `_build_initial_state` (rely on ADK user_state merge); mode-dispatch `app_name` to `"StockBot-live"` / `"StockBot-paper"` (paper vs live broker mode).  Update the obsolete 2.5.3 todo-fixes comment at lines 67-69 to point at this spec. |
| `src/orchestrator/persistence.py` | Parameterise `make_session_service()` so backtest can point it at a per-run SQLite; live keeps `DATABASE_URL`. |
| `src/backtest/driver.py` | Switch from `InMemorySessionService` to `DatabaseSessionService`; remove `state.update(dict(...))` carry for `positions`; set `app_name=f"StockBot-backtest-{window.id}"`, `user_id="stockbot"`. |
| `src/backtest/runner.py` | Wire the new per-run session-service path; delete `runs/<run-id>/session.sqlite` on `--fresh` rerun. |
| `docs/contract-invariants.md` | Apply §A row amendments for `positions`/`thesis` → `user:`-prefixed with writer-of-record footnote (see "Contract amendments"); add §C-Rule 7 clarification paragraph. |

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
  - `src/agents/_verb_dispatch.py` — exports the shared helpers
    `resolve_broker_call(stance, *, prior_row) -> BrokerCall | None`
    and `apply_stance_to_thesis(stance, *, prior_row, fill_price,
    tick_id, as_of) -> PositionThesis | None`.  Both are pure
    functions imported by Executor *and* MemoryWriter so neither
    duplicates verb→side-effect logic.
- **New `TickerStance.intent` enum members:** `hold`, `update`
  (added to `agents.strategist.schemas`).
- **New constants in `agents.strategist.prompts`:**
  `COLD_START_MODE_TEMPLATE`, `INCREMENTAL_MODE_TEMPLATE`.
- **New call edges:**
  - `agents.executor.agent` → `agents._verb_dispatch.resolve_broker_call`.
  - `agents.memory_writer.agent` → `agents._verb_dispatch.apply_stance_to_thesis`.
  - `orchestrator.tick._build_initial_state` → no longer references
    bare keys `positions` / `thesis` (those entries deleted from the
    seed dict).
- **Removed call edges:**
  - `agents.executor.agent` no longer mutates `state["positions"]` or
    `state["user:positions"]` — the corresponding `state_delta` keys
    are gone from its yielded events.
  - `agents.strategist.derivation` carry-forward block removed
    (lines 254-271 in the pre-spec source).
- **State-key migrations (worth a one-liner in the delta so
  downstream tooling can pick them up):**
  - `state["positions"]` → `state["user:positions"]`.
  - `state["thesis"]` → `state["user:thesis"]`.
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
