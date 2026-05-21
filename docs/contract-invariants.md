# Tick-boundary invariants — the canonical contract

## Purpose

A single canonical contract describing the invariants that must hold at the
boundaries of a *tick* — the unit of work the bot performs whenever the
pipeline runs end-to-end. Both lifecycles must satisfy this contract:

- **Live** — one Cloud Run Job process per tick. Cold-starts each
  invocation; in-memory session state does not survive between ticks.
- **Backtest** — one long-lived Python process iterating a NYSE-scheduled
  tick sequence. Session state could trivially survive between iterations,
  but the contract forbids relying on that for correctness.

The contract describes **invariants, not mechanisms**. It says *what must
be true* at each boundary. *How* each lifecycle achieves that is
implementation. Where the two lifecycles genuinely differ in ways that
cannot change pipeline outputs, an explicit "additive carve-out" rule
applies (§D).

This document is **target-state**. It describes the invariants the
system must satisfy; it does not catalogue current violations. Gaps
between today's code and the contract are addressed by the relevant
refactor workstream.

The contract is grounded in Google ADK 1.34 semantics — every cross-cutting
rule (§C) traces back to a specific documented or source-verified ADK
behaviour.

---

## §A — Field schema

The spine of the contract. One row per top-level key in the ADK session
`state` dict. Every other section refers back to rows here by field name.

**Column meanings:**

| Column | Meaning |
|---|---|
| **Field** | The top-level key in `state`. |
| **Owner** | The single component that writes the field. Exactly one owner — no shared writers. For agents, callbacks attached to that agent count as the agent's writes. |
| **Lifetime** | `tick-scoped` (rebuilt or cleared at the tick boundary) or `cross-tick` (must survive the boundary). |
| **Source of truth** | Where the canonical value lives. State itself is never the source of truth — it is a working copy. |
| **Refresh point** | The §B phase in which the field is (re)populated. |
| **Persistence** | For cross-tick fields, how the value crosses the tick boundary in live. For tick-scoped fields, `n/a`. |
| **Notes** | One-line gotcha or pointer to a §C rule. |

**One row per top-level key only.** Nested fields with their own
contractual semantics (e.g. `positions[ticker].opened_price`) are
documented in the **Notes** column of the parent row, not as separate
rows.

**Scope of the table.** §A lists the contract-bearing fields — fields
that have a cross-tick lifetime, a documented owner outside the pipeline,
or carry an agent's output across an agent boundary. Pipeline-internal
working state (intermediate aggregates passed between agents in the
same tick) is allowed to exist; it is implementation, not contract.
Such fields still obey §C — most importantly, any mutation must ride
on `state_delta` (Rule 1).

<!-- Phase 9 (per-ticker analyst fan-out): the canonical `news_verdicts` and
     `fundamental_verdicts` keys are now written by joiner agents that consolidate
     per-ticker working keys.  The keys' contract values and lifetimes are unchanged —
     only ownership shifted from a batched LlmAgent to a downstream BaseAgent.
     See `docs/Phase9-agent-fanning-per-ticker/spec.md`. -->

### Schema table

| Field | Owner | Lifetime | Source of truth | Refresh point | Persistence | Notes |
|---|---|---|---|---|---|---|
| `tick_id` | Tick bootstrap | tick-scoped | Wall clock | Phase 2 (tick-start) | n/a | Deterministic per-tick identifier. |
| `tickers` | Tick bootstrap | tick-scoped | `config/watchlist.json` | Phase 2 | n/a | The active watchlist for this tick. |
| `portfolio` | Broker | tick-scoped (as state); cross-tick (as broker reality) | Broker API | Phase 2 | Broker holds it — `state["portfolio"]` is a working copy refreshed from the broker at the start of every tick. | Pipeline reads but does not mutate. |
| `reference_prices` | Tick bootstrap | tick-scoped | Bulk yfinance pull | Phase 2 | n/a | Cached for the duration of the tick. |
| `positions` | Strategist (via `state_delta`) | **cross-tick** | Persistence layer (see §E) | Phase 2 (read), Phase 4 (write) | Persistence subsystem — see §E. | The *thesis book*. Per-position entry rationale + exit basis. Distinct from `portfolio` (broker truth) — `positions` is strategist intent. |
| `memory_buffer` | MemoryWriter (via `state_delta`) | **cross-tick** | Persistence layer (see §E) | Phase 2 (read), Phase 4 (write) | Persistence subsystem — see §E. | Experiential memory. Cross-position learning log. |
| `day_digest` | MemoryWriter (via `state_delta`) | **cross-tick** | Persistence layer (see §E) | Phase 2 (read), Phase 4 (write) | Persistence subsystem — see §E. | Summarised day-level context. Exact lifetime and rebuild rule deferred to §E. |
| `thesis` | Strategist (via `state_delta`) | **cross-tick** | Persistence layer (see §E) | Phase 2 (read), Phase 4 (write) | Persistence subsystem — see §E. | Strategist's standing market thesis. |
| `strategist_decision` | Strategist (`output_key`) | tick-scoped | Strategist LLM call | Phase 3 (during-tick) | n/a | Consumed by RiskGate and Executor downstream in the same tick. |
| `technical_verdicts` | TechnicalAnalyst (`state_delta`) | tick-scoped | TechnicalAnalyst deterministic extractor | Phase 3 | n/a | Unique key — see §C-Rule 4. Yielded as a list of per-ticker verdict dicts; written via `state_delta` (Rule 1) — TechnicalAnalyst is a BaseAgent, not an LlmAgent, so no `output_key`. |
| `fundamental_verdicts` | FundamentalJoinerAgent (`state_delta`) | tick-scoped | FundamentalJoinerAgent consolidation of per-ticker working keys | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `news_verdicts` | NewsJoinerAgent (`state_delta`) | tick-scoped | NewsJoinerAgent consolidation of per-ticker working keys | Phase 3 | n/a | Unique key — see §C-Rule 4. |
| `social_verdicts` | SocialAnalyst (`state_delta`) | tick-scoped | SocialAnalyst deterministic extractor | Phase 3 | n/a | Unique key — see §C-Rule 4. Yielded as a list of per-ticker verdict dicts; written via `state_delta` (Rule 1) — SocialAnalyst is a BaseAgent, not an LlmAgent, so no `output_key`. |
| `tick_phase` | Tick bootstrap | tick-scoped | Lifecycle wrapper | Phase 2 | n/a | Literal string — live sets `"live"`; backtest sets the schedule's `tick.phase` (`"open"` / `"close"`). Decorative for the pipeline today; consumed by observability/tracing surfaces. Documented in §A so future agents that branch on phase have a contractual hook. |
| `last_executed_tick_id` | Executor (`state_delta`) | tick-scoped | Executor's idempotency handshake | Phase 3 | n/a | Set to the current `tick_id` after the Executor finishes its run. Read by the Executor itself at the top of the next invocation as an idempotency guard. Written via `state_delta` (Rule 1); a paired direct write is currently retained as defensive belt-and-braces (out of A1 scope — see todo-fixes 2.5.x). |
| `last_snapshot` | Snapshotter (`state_delta`) | tick-scoped | Snapshotter's pipeline-completion handshake | Phase 3 | n/a | Set at the end of the tick. Read by the backtest driver's per-tick assertion (`src/backtest/driver.py:393-401`) to confirm the pipeline reached the Snapshotter. Written via `state_delta`; the paired direct write is defensive (out of A1 scope). |

The four cross-tick rows (`positions`, `memory_buffer`, `day_digest`,
`thesis`) all depend on the persistence subsystem described in §E.
Until that subsystem exists, those rows describe target-state and any
lifecycle that ships without true persistence for them violates the
contract.

Two of the tick-scoped rows (`last_executed_tick_id`, `last_snapshot`)
exist as **in-tick handshake keys** — written and read inside a single
tick, with no cross-tick contract. They are listed in §A only because
their owners and refresh points are stable enough to lock down; they
do not require persistence and the lifecycle wrapper does not touch
them.

---

## §B — Lifecycle phases

Four phases. Each phase spells out what must be true at that boundary.
Phases reference §A rows by name; they do not restate field semantics.

### Phase 1 — Run-start (once per process)

Fires once when the process boots. Live: at the top of the Cloud Run Job
entrypoint, before any tick work. Backtest: at the top of `runner.py`,
before the schedule loop.

**Invariants:**

- Broker connection established. `portfolio` is readable.
- Configuration loaded (`tickers`, runtime settings).
- Persistence layer (§E) ready — DB connection open, schema verified.
- Provider implementations wired (live providers in live; cache-backed
  providers in backtest).
- No `state` dict exists yet. This phase produces the inputs that Phase 2
  consumes.

**Live ≡ backtest:** identical work. The two lifecycles differ only in
*which* concrete broker, persistence, and provider implementations get
wired up.

### Phase 2 — Tick-start (every tick, before pipeline runs)

The critical phase. Builds the initial `state` dict for the tick. This is
where the two lifecycles historically diverged; the contract closes the
gap by forbidding state-dict carry-over as a source of cross-tick data.

**Invariants — for every §A row:**

- **Tick-scoped fields** are populated fresh from their `Source of truth`
  (clock, config, broker, bulk data pull).
- **Cross-tick fields** are populated from their `Persistence` source —
  the persistence layer (§E). Reading them from a leftover in-memory
  `state` dict is **not permitted**, regardless of lifecycle.
- After Phase 2 completes, the `state` dict satisfies §A in full. No field
  the pipeline reads is left undefined.

**Live ≢ backtest mechanically** — live constructs a fresh state from
scratch; backtest must overwrite any inherited values from the previous
iteration with values read from persistence. **Live ≡ backtest
contractually** — both end Phase 2 with a state dict whose cross-tick
fields came from the persistence layer, not from prior in-memory state.

A common failure mode is treating a cross-tick field as tick-scoped —
seeding it with an empty value at Phase 2 instead of reading from
persistence. This is a Phase 2 violation: the field's §A row mandates
hydration from persistence, regardless of how cheap the empty seed
looks.

### Phase 3 — During-tick (pipeline execution)

The SequentialAgent runs. The invariants for this phase are §C
cross-cutting rules in action:

- All state writes ride on `EventActions(state_delta=...)` events
  (Rule 1).
- ParallelAgent branches write to unique `output_key`s (Rule 4).
- Callbacks return `None` to pass through or a final response to
  replace — they never re-prompt (Rule 3).
- `temp:` keys are invocation-local and do not outlive the tick
  (Rule 2).
- The pipeline reads from `state`; it does not read the broker, the
  persistence layer, or any provider directly. Providers are wired in at
  Phase 1; broker truth is loaded into `state` at Phase 2 (Rule 7).

No phase-specific invariants beyond the §C rules — Phase 3 is the phase
where §C is enforced.

### Phase 4 — Tick-end (every tick, after pipeline runs)

**Invariants:**

- All cross-tick fields written during the tick (via `state_delta`) are
  **persisted** to the persistence layer (§E) before the process can exit
  or proceed. State-dict-only writes are not durable.
- Broker has been called for any executed trades. `portfolio` and
  `positions` as the broker sees them are consistent with the Executor's
  emitted intents.
- Observability writes (trace, decision log, snapshot) have flushed.
- Tick-scoped fields may be discarded. Live discards them by exiting the
  process; backtest discards them by overwriting in Phase 2 of the next
  tick.

**Live ≡ backtest:** the same persistence work happens in both. Backtest
is allowed to skip nothing here — if live persists it, backtest persists
it too. This is the symmetric write half of the Phase 2 symmetric read.

---

## §C — Cross-cutting rules

Eight rules. Rules 1, 4, 5, 6 are direct ADK 1.34 semantics — non-
negotiable, documenting what the framework does. Rules 2, 3 are ADK
semantics we choose to respect (the bot will not crash if ignored, but
state will not behave). Rule 7 is the load-bearing architectural rule
that lets one pipeline serve both lifecycles. Rule 8 is the additive-
carve-out enabler.

### Rule 1 — State mutation must ride on Events

All writes to session state must go through `EventActions(state_delta=...)`
events yielded by an agent. Direct mutation of the `state` dict (e.g.
`ctx.session.state[key] = value` from a callback or tool) is **not
durable** on real session backends — only the event-driven `state_delta`
path persists through `SessionService.append_event`.

**ADK grounding:** `Session.state` mutations made outside an event are
in-memory only. `BaseSessionService.append_event` is the documented
persistence channel for state updates.

**Implication:** Strategist's `after_agent_callback` writing the thesis
book must emit a `state_delta`, not poke the dict.

**In-tick callback carve-out (added 2026-05-20).**  ADK
``after_agent_callback``s cannot yield Events (Rule 3) but are the only
place certain LLM-output validation + derivation can run (they need
runtime access to ``state["portfolio"]`` and ``state["tickers"]``, which
``output_schema`` does not see).  Where such a callback writes to a
state key whose only consumer is **another agent in the same tick**,
that direct write is conformant.  The carve-out does NOT apply if the
key escapes the tick — cross-tick keys must still go through
``state_delta``.

The canonical instance today is the Strategist's
``_strategist_validation_callback`` (see
``src/agents/strategist/agent.py:383``), which rewrites
``state["strategist_decision"]`` with the derived legacy fields
(``target_weights``, ``new_positions``, ``close_reasons``,
``trim_reasons``).  Its only consumer is the downstream RiskGate agent
in the same tick.

### Rule 2 — `temp:` is invocation-scoped only

State keys prefixed with `temp:` are scoped to a single invocation (one
pipeline run = one tick). They do not survive across ticks regardless of
lifecycle. Use `temp:` for analyst-to-strategist handoff *within* a tick;
never for tick-to-tick state.

**ADK grounding:** ADK documents the `temp:` prefix as invocation-scoped
and not persisted by session services.

**Concrete invocation-scoped keys (A2.6 rename, 2026-05-20):**
the strategist's ``temp:held_positions_view``, ``temp:ticker_evidence``,
``temp:ticker_evidence_objects``, and the four analyst raw-data keys
``temp:technical_data`` / ``temp:fundamental_data`` / ``temp:news_data``
/ ``temp:social_data``.  All written by callbacks or the
``StrategistContextShim`` (Task A2.1); all consumed inside a single
tick by the analyst's own ``_run_async_impl`` or by the Strategist's
instruction template.

### Rule 3 — Callbacks never re-prompt

The four callback hooks — `before_agent_callback`, `before_model_callback`,
`after_model_callback`, `after_agent_callback` — accept exactly two
return-value contracts: return `None` to pass through, or return a final
response (`LlmResponse` / `Content`) to **replace** the agent's output.
There is no "try again" return value. Retry-on-failure must be modelled
as a `LoopAgent` wrapping the agent that can fail.

**ADK grounding:** the callback contract in `BaseAgent` / `LlmAgent`
permits only None-or-replacement. `LoopAgent` (with `escalate=True` as
the success/break signal) is ADK's documented retry primitive.

**Implication:** Strategist's `after_agent_callback` cannot re-prompt on
validation failure. For retry-on-bad-JSON, wrap Strategist in a
`LoopAgent`.

### Rule 4 — ParallelAgent branches need unique `output_key`s

`ParallelAgent` runs sub-agents concurrently and merges their
`state_delta`s into a single session. If two branches share an
`output_key`, the merge is order-dependent — last writer wins,
non-deterministically.

**ADK grounding:** `ParallelAgent` does not coordinate branch outputs.
Each branch's `state_delta` is appended in completion order.

**Implication:** the AnalystPool's four analysts must each have a unique
output key. The §A table records the four current keys
(`technical_verdicts`, `fundamental_verdicts`, `news_verdicts`,
`social_verdicts`) explicitly to prevent future drift. All four keys are
now written via `state_delta` (Rule 1): `TechnicalAnalyst` and
`SocialAnalyst` are BaseAgent subclasses that have always used this
mechanism; `NewsJoinerAgent` and `FundamentalJoinerAgent` (Phase 9
per-ticker fan-out) replaced the former batched `NewsAnalyst` /
`FundamentalAnalyst` LlmAgents that used ADK's `output_key` mechanism.
The uniqueness requirement of Rule 4 is satisfied regardless of mechanism.

### Rule 5 — `LoopAgent` must have a terminating condition

A `LoopAgent` without `max_iterations` and without a sub-agent that ever
yields `event.actions.escalate=True` spins forever. Every `LoopAgent` in
the pipeline must have either:

- a `max_iterations` ceiling, **and/or**
- a sub-agent (or callback on a sub-agent) that escalates on success.

Strong preference for **both** — `max_iterations` as a backstop even when
escalate is reliable.

**ADK grounding:** confirmed against `loop_agent.py` source. The loop
exits only on `max_iterations` reached or `escalate=True` observed in a
yielded event's actions.

### Rule 6 — `AgentTool` isolates session state

When an agent is exposed as a tool via `AgentTool`, it runs in a **fresh
`InMemorySessionService`** for each invocation. Only the wrapped agent's
final `state_delta` flows back to the caller — the tool agent cannot
read the caller's session state and cannot leave intermediate state
behind.

**ADK grounding:** `AgentTool` source — `InMemorySessionService` is
instantiated per call.

**Implication:** any agent wrapped as a tool cannot rely on
`state["portfolio"]`, `state["positions"]`, etc. being readable. We
currently do not wrap any agent as a tool; the rule exists to prevent a
future regression.

### Rule 7 — Cross-tick persistence is the lifecycle's job, not the pipeline's

The pipeline (analysts → strategist → executor) reads from and writes to
**state**. It does not read from or write to the persistence layer
(§E), the broker, or any provider for cross-tick data. The lifecycle
wrapper is responsible for:

- reading cross-tick fields from persistence into `state` at Phase 2
  (tick-start)
- writing cross-tick `state_delta`s back to persistence at Phase 4
  (tick-end)

This is the architectural rule that makes the pipeline lifecycle-
agnostic. An agent must never know whether it is running in live or
backtest.

**Implication:** if an agent needs DB access mid-tick, that is a contract
violation — the data it needs belongs in Phase 2 hydration.

### Rule 8 — Observability is additive and contract-neutral

Trace writers, decision loggers, snapshotters: allowed to exist, allowed
to differ between lifecycles, but **must not read or write
contract-bearing state**. They consume the same state both lifecycles
produce; they do not change it. This is the anchor for the additive
carve-outs in §D — anything that cannot change pipeline outputs is exempt
from contract symmetry requirements.

---

## §D — Additive carve-outs

Legitimate divergences between live and backtest. The rule that makes
them legitimate: **they must not change pipeline outputs**.

### D1 — Observability writes

Live writes traces and decision logs to whatever observability sink is
wired up (production target: GCS). Backtest writes them to the per-run
artefact tree (`runs/<run-id>/traces/`, `runs/<run-id>/decisions/`).

**Why contract-neutral:** traces are consumed by humans, not by agents.
The pipeline produces the same `state_delta`s regardless of where they
are logged.

### D2 — LLM stubbing in tests

The end-to-end smoke test short-circuits Strategist / Fundamental / News
via `before_model_callback` shims that return synthetic `LlmResponse`
objects (no Gemini credentials needed).

**Why contract-neutral:** the shim returns a real `LlmResponse`, so from
ADK's point of view the agent ran normally. State-delta shape is
identical. The stub is invisible to everything downstream of the
callback boundary.

### D3 — Broker implementation

Live uses `Trading212Broker`; backtest uses `FakeBroker`. Both implement
the same broker interface.

**Why contract-neutral:** the §A `portfolio` row says
`Source of truth: broker`. Which concrete broker is wiring, not contract.

### Non-carve-outs (named explicitly to prevent drift)

These are **not** additive — both lifecycles must do them identically:

- **Cross-tick state persistence** — both lifecycles read from and write
  to the persistence layer at the documented phases.
- **State-dict shape** — both lifecycles see identical keys at identical
  phases per §A.
- **Agent composition and ordering** — pipeline topology is identical.

A new candidate carve-out passes only if it satisfies the "cannot change
pipeline outputs" test. Anything that can affect what the strategist
sees, what the executor does, or what survives the tick boundary is
non-additive by definition.

---

## §E — Cross-session persistence (followup work)

The contract commits to a cross-session persistence layer. Four §A
rows — `positions`, `memory_buffer`, `day_digest`, `thesis` — have
`Source of truth = persistence layer` and depend on this subsystem for
their cross-tick guarantees. The mechanism is a separate design
(followup spec).

### Requirements established by this contract

1. **Symmetric** — live and backtest read from and write to the same
   persistence layer at the same lifecycle phases (Phase 2 / Phase 4).
2. **Two memory types** — distinct shapes, probably distinct storage:
   - **Thesis memory** (per-position). For each open position: why the
     bot entered, what it expected to happen, what would invalidate the
     thesis, and what would confirm an exit. Read by the strategist
     when considering exits. Keyed by ticker / position id. Lives from
     entry to exit.
   - **Experiential memory** (cross-position). Patterns from past
     trades, daily observations, regime context. Read by the strategist
     when considering new entries and when contextualising the world.
     Time-ordered, bounded retention, probably summarised.
3. **Lifecycle-owned** — the pipeline never reads or writes persistence
   directly. Only the lifecycle wrapper does, per §C-Rule 7.

### Open design questions (move to followup spec)

- Schema for **thesis memory** — one row per position; entry rationale,
  expected catalysts, invalidation conditions, exit criteria. Trigger
  for write (entry); trigger for delete or archive (exit).
- Schema for **experiential memory** — time-ordered log shape,
  summarisation strategy, bounded retention policy. Relationship between
  `memory_buffer`, `day_digest`, and `thesis` — are these three separate
  stores or one store with different views?
- **Live persistence target** — DB choice for the Cloud Run lifecycle.
- **Backtest persistence target** — most likely the existing per-run
  `runs/<run-id>/db.sqlite` SQLAlchemy store. To confirm in the
  followup.
- **Migration / rebuild story** — what happens when the persistence
  schema changes mid-experiment. Probably out of scope for the first
  pass.

### What this contract commits to (regardless of current code)

All four cross-tick rows in §A — `positions`, `memory_buffer`,
`day_digest`, `thesis` — are normatively cross-tick. If today's code
happens to reconstruct any of them from elsewhere (broker truth, fresh
LLM summary, an empty seed), that reconstruction approach is
insufficient by definition: the contract has decided these fields
require true persistence so the bot can carry intent and learning
across ticks. The followup persistence spec implements the mechanism;
this spec fixes the requirement.
