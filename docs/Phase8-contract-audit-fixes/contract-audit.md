# Contract audit — current code vs `contract-invariants.md`

## Purpose

This document is the field-by-field, phase-by-phase, rule-by-rule audit of
the StockBot codebase against `contract-invariants.md`. Each finding cites
file:line so the upcoming refactor workstream can act on it directly.

Findings are **observations**, not prescriptions. Where a fix is obvious
the audit names it; where the spec implies a structural change the audit
flags the surface and defers to the workstream.

A finding is either:

- **conformant** — code behaves exactly as the contract requires;
- **deviation** — code diverges from the contract but the divergence has
  not bitten in practice (often because backtest state carry-over or a
  defensive double-write masks the gap);
- **violation** — code diverges from the contract and the divergence is
  load-bearing for incorrect behaviour today.

The pre-deployment posture (no live process is running) is why most
findings here are **deviations** rather than **violations**: the bug
surface exists, but lack of a live tick has prevented it from biting.
Once the lifecycle wrappers and persistence layer are wired up, every
deviation becomes a live failure mode.

---

## Summary table

| Section | Item | Status | Severity | One-line |
|---|---|---|---|---|
| §A | `tick_id` | conformant | — | Set in both lifecycles at tick start. |
| §A | `tickers` | conformant | — | Loaded from watchlist in both. |
| §A | `portfolio` | conformant | — | Refreshed from broker each tick in both. |
| §A | `reference_prices` | conformant | — | Bulk pulled per tick in both. |
| §A | `positions` | deviation | high | Cross-tick field; seeded `{}` in both lifecycles. No persistence read/write. |
| §A | `memory_buffer` | deviation | high | Cross-tick field; seeded `[]` in both. No persistence read/write. |
| §A | `day_digest` | deviation | high | Cross-tick field; seeded `""` in both. No persistence read/write. |
| §A | `thesis` | deviation | high | Cross-tick field; seeded `""` in both. No persistence read/write. |
| §A | `strategist_decision` | conformant (in-tick carve-out) | — | LlmAgent `output_key` is correct; `_strategist_validation_callback` direct-writes the derived fields but its only consumer (RiskGate) is in the same tick — conformant under the §C-Rule 1 in-tick carve-out. |
| §A | `technical_verdicts` / `fundamental_verdicts` / `news_verdicts` / `social_verdicts` | resolved (A1.5) | — | Spec aligned to the code's plural form 2026-05-20. Technical + social still write via `state_delta` (Rule 1), not `output_key` (resolved by A1.1 / A1.2). |
| §A | `as_of`, `tick_phase` | unmodelled | medium | Set by backtest driver; read by writers + analysts. Not in §A. Live never sets either. |
| §A | `last_executed_tick_id`, `last_snapshot` | unmodelled | low | Used by Executor idempotency + driver assertion. Not in §A. |
| §A | `held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`, `{analyst}_data`, `{analyst}_evidence` | unmodelled (intermediate) | medium | Within-tick agent-to-agent handoffs. Allowed by §A's "intermediate" carve-out, but Rule 1 still applies — currently direct-mutated. |
| §B | Phase 1 (run start) | conformant | — | Symmetric in both lifecycles. |
| §B | Phase 2 (tick start) | deviation | high | Both lifecycles seed cross-tick fields as empty rather than reading from persistence. Backtest sets `as_of` / `tick_phase`; live sets neither. |
| §B | Phase 3 (during tick) | deviation | medium | Multiple Rule 1 violations in callbacks and BaseAgent analysts. See §C-Rule 1. |
| §B | Phase 4 (tick end) | deviation | high | No persistence write for any of the four cross-tick fields. Driver carry-over (`state.update`) masks this in backtest. |
| §C | Rule 1 (state_delta) | deviation | high | At least eight in-pipeline direct-mutation sites. Catalogued below. |
| §C | Rule 2 (`temp:`) | deviation | low | Two textbook `temp:` candidates not prefixed (`held_positions_view`, `ticker_evidence*`). |
| §C | Rule 3 (no re-prompt) | conformant | — | Strategist `after_agent_callback` raises rather than re-prompts; explicit comment at `src/agents/strategist/agent.py:235-246`. |
| §C | Rule 4 (unique `output_key`) | conformant | — | The four analyst keys are unique. Caveat: Technical + Social don't use `output_key` at all — they write directly (separate Rule 1 issue). |
| §C | Rule 5 (LoopAgent termination) | n/a | — | No LoopAgent in current pipeline. Preventative. |
| §C | Rule 6 (AgentTool isolation) | n/a | — | No AgentTool in current pipeline. Preventative. |
| §C | Rule 7 (lifecycle owns persistence) | deviation | medium | Four pipeline-internal agents write directly to the SQLAlchemy DB (`EvidenceWriter`, `StrategistDecisionWriter`, `Executor`, `Snapshotter`). These are audit writes, not cross-tick state reads — but they live in the pipeline, not the lifecycle wrapper. |
| §C | Rule 8 (observability additive) | conformant | — | `TraceWriter` is gated by `STOCKBOT_TRACE=1`; decision logger isolated. |
| §D | D1 / D2 / D3 carve-outs | conformant | — | All three honour the "cannot change pipeline outputs" rule. |
| §D | `as_of` resolution divergence | candidate carve-out | medium | Backtest uses historical `tick.as_of`; live falls back to wall-clock via `resolve_as_of(... allow_wallclock=True)`. Affects timestamps on persisted rows. |
| §E | Persistence subsystem | absent | high | Required by four §A rows; does not exist yet. |

---

## §A — Field-by-field audit

The audit walks every §A row, then catalogues fields that are present in
the running code but not in §A.

### `tick_id` — conformant

- **Live:** `src/orchestrator/tick.py:_build_initial_state` writes `tick_id`
  into the initial state dict (one tick = one process invocation; the id
  is constructed from the run timestamp).
- **Backtest:** `src/backtest/driver.py:192-202` writes `state["tick_id"]`
  per tick before the pipeline runs.
- **Consumed by:** `Executor.last_executed_tick_id` idempotency check
  (`src/agents/executor/agent.py:42`), `Snapshotter.last_snapshot.tick_id`
  consistency check (`src/agents/snapshot/agent.py:111`), driver post-tick
  assertion (`src/backtest/driver.py:393-401`).

### `tickers` — conformant

- **Live:** `src/orchestrator/tick.py:_build_initial_state` loads from the
  configured watchlist source.
- **Backtest:** `src/backtest/runner.py:475-491` seeds `state["tickers"]`
  (and `state["watchlist"]` — see below) from
  `config/watchlist.json` after pre-flight filtering.
- **Read by:** every analyst's fetch callback iterates `state["tickers"]`
  (e.g. `src/agents/analysts/technical/fetch.py:4`, `news/fetch.py:3`).

### `portfolio` — conformant

- **Live:** `src/orchestrator/tick.py:_build_initial_state` calls the
  broker and stores `portfolio.model_dump(mode="json")`.
- **Backtest:** `src/backtest/runner.py:475-491` seeds it; `driver.py:227-229`
  refreshes from the FakeBroker each tick (commit `5dcaac4` introduced this
  refresh — without it, backtest's working copy went stale across ticks).
- **Read by:** every consumer reads `state["portfolio"]`. Strategist's
  instruction template resolves `{portfolio}` (ADK raises `KeyError:
  'Context variable not found: portfolio'` if absent — see
  `.claude/CLAUDE.md` "Important implementation notes").

### `reference_prices` — conformant

- **Live:** `src/orchestrator/tick.py:_build_initial_state` writes the
  bulk yfinance pull (one entry per ticker) into `state`.
- **Backtest:** `src/backtest/runner.py:475-491` seeds an equivalent dict
  from the cache-backed providers.
- **Read by:** Technical analyst's relative-strength feature
  (`src/agents/analysts/technical/agent.py:117`).

### `positions` — DEVIATION (high)

- **Live:** `src/orchestrator/tick.py` initial state contains
  `"positions": {}`. No rehydration from any persisted source.
- **Backtest:** `src/backtest/runner.py:475-491` contains `"positions": {}`.
  Cross-tick survival in backtest happens **only via** the driver's
  `state.update(dict(updated.state))` carry-over at
  `src/backtest/driver.py:382-383`, **not** via a persistence read.
- **Spec demands:** §A row says `Source of truth: Persistence layer
  (see §E)`, `Refresh point: Phase 2 (read), Phase 4 (write)`, with a
  `Persistence` cell pointing at the persistence subsystem.
- **Owner:** spec says Strategist (via `state_delta`). Code: see Rule 1
  audit — `_strategist_validation_callback` direct-mutates and does not
  yield a `state_delta` for `positions`. The Executor *does* yield a
  `state_delta` containing `positions`
  (`src/agents/executor/agent.py:202-210`), so the Executor is currently
  the de-facto writer (capturing post-trade state) rather than the
  Strategist capturing thesis intent. This is the "split ownership"
  described in commit `50f0f60`: Strategist owns intent, Executor owns
  fact. Spec compresses both back to Strategist as owner — to revisit
  in the persistence design.
- **Why it hasn't bitten:** no live process running; backtest's
  in-memory carry-over hides the absent persistence.

### `memory_buffer` — DEVIATION (high)

- **Live:** `src/orchestrator/tick.py` seeds `"memory_buffer": []`.
- **Backtest:** `src/backtest/runner.py:475-491` seeds `"memory_buffer": []`.
- **Writer:** `MemoryWriter._run_async_impl`
  (`src/agents/memory/writer.py:181-189`) yields a correct
  `Event(actions=EventActions(state_delta={"memory_buffer": ...}))`. The
  yield is correct (Rule 1 satisfied for the in-tick write).
- **Persistence:** no DB write — the `state_delta` is in-memory only. On
  the next tick, both lifecycles re-seed `[]` and lose history.
- **Same masking pattern as `positions`.**

### `day_digest` — DEVIATION (high)

- **Live:** `src/orchestrator/tick.py` seeds `"day_digest": ""`.
- **Backtest:** `src/backtest/runner.py:475-491` seeds `"day_digest": ""`.
- **Writer:** `MemoryWriter._run_async_impl` yields the field in its
  `state_delta` (correct Rule 1 pattern; see line 181-189).
- **Persistence:** absent; same masking pattern.

### `thesis` — DEVIATION (high)

- **Live:** `src/orchestrator/tick.py` seeds `"thesis": ""`.
- **Backtest:** `src/backtest/runner.py:475-491` seeds `"thesis": ""`.
- **Writer:** also `MemoryWriter` (state_delta includes the key); no
  Strategist write of `thesis` in current code despite the spec naming
  Strategist as owner. Worth resolving in the persistence design.
- **Persistence:** absent; same masking pattern.

### `strategist_decision` — CONFORMANT (in-tick carve-out)

- **Owner (per spec):** Strategist via `output_key`.
- **Code (output_key):** `src/orchestrator/pipeline.py:88` sets
  `output_key="strategist_decision"` on the Strategist LlmAgent.
- **Code (callback that ALSO writes):**
  `_strategist_validation_callback` in
  `src/agents/strategist/agent.py:383` performs
  `state["strategist_decision"] = decision.model_dump(mode="json")` after
  validation. This is a direct dict mutation — normally a Rule 1 deviation.
- **Why it is conformant:** the callback is an `after_agent_callback`,
  which cannot yield Events (Rule 3). It is the only place where the
  decision can be normalised into a JSON-friendly dict using runtime access
  to ``state["portfolio"]`` and ``state["tickers"]``. The rewritten key's
  sole consumer (RiskGate) is in the same tick; the key is tick-scoped and
  never crosses a tick boundary. This satisfies the §C-Rule 1 **in-tick
  callback carve-out** — see ``contract-invariants.md`` §C-Rule 1 for the
  canonical definition.

### `technical_verdicts` / `fundamental_verdicts` / `news_verdicts` / `social_verdicts` — RESOLVED (A1.5 + A1.1/A1.2)

Two distinct issues here, plus a naming drift.

**Naming drift — RESOLVED (A1.5, 2026-05-20).** The spec has been
updated to match the code's plural form. The four keys as wired in
code:

- Fundamental: `src/agents/analysts/fundamental/agent.py:164` —
  `output_key="fundamental_verdicts"`.
- News: `src/agents/analysts/news/agent.py:119` —
  `output_key="news_verdicts"`.
- Technical: `src/agents/analysts/technical/agent.py` — yields
  `state_delta={"technical_verdicts": ...}` (resolved by A1.1).
- Social: `src/agents/analysts/social/agent.py` — yields
  `state_delta={"social_verdicts": ...}` (resolved by A1.2).

**Direct mutation in BaseAgent analysts.** The spec column "Owner" reads
"TechnicalAnalyst (`output_key`)" for technical/social, but those two
analysts are `BaseAgent` subclasses (`src/agents/analysts/technical/agent.py:40`,
`src/agents/analysts/social/agent.py:40`) — deterministic heuristics, not
`LlmAgent`s. They cannot use `output_key`; they write via
`state["technical_verdicts"] = ...` / `state["social_verdicts"] = ...`
directly in `_run_async_impl`.

This is a Rule 1 deviation inside a `ParallelAgent`. Rule 4 is still
satisfied (the four keys are unique), but the mechanism is direct
dict mutation, not an `EventActions(state_delta=...)` yield. On a real
session backend these writes are not durable; downstream consumers see
them only because the same in-memory dict is shared within an
invocation.

**Why the previous Rule 1 deviation hadn't bitten before A1.1/A1.2:**
in-memory session services keep direct mutations visible across the
SequentialAgent boundary; the contract failure would surface only on a
persistence-backed session service. A1.1 / A1.2 close the gap.

### `as_of` — UNMODELLED (medium)

- **Backtest:** `src/backtest/driver.py:194` sets
  `state["as_of"] = tick.as_of` (historical timestamp).
- **Live:** never set. `src/orchestrator/tick.py:_build_initial_state`
  omits the key entirely.
- **Read by:** every downstream writer — `EvidenceWriter`
  (`src/agents/contract/evidence_writer.py:80-85`),
  `StrategistDecisionWriter`
  (`src/agents/strategist/decision_writer.py:89-94`), `MemoryWriter`,
  Technical analyst's historical feature path
  (`src/agents/analysts/technical/agent.py:103-117`).
- **Resolution layer:** `src/data/timeguard.py:resolve_as_of(...,
  allow_wallclock=True)` falls back to `datetime.now(tz=UTC)` if the
  caller passes `None`. The `STOCKBOT_STRICT_AS_OF=1` env var (set
  during backtest runs) turns the wall-clock fallback into a veto.
- **Contract gap:** the field is real, the field is consumed by the
  pipeline, and live vs backtest behave differently — but §A doesn't
  document it. Two options:
  1. Add `as_of` to §A as a tick-scoped field owned by the lifecycle
     wrapper (Phase 2 setter). Live must set it from wall clock;
     backtest from `tick.as_of`. The wall-clock-fallback path in
     `resolve_as_of` becomes dead code (or strict-mode becomes the
     contract).
  2. Promote it to §D as a carve-out, but it fails the "cannot change
     pipeline outputs" test (timestamps on persisted rows differ),
     so option 1 is the right call.

### `tick_phase` — UNMODELLED (low)

- **Backtest:** `src/backtest/driver.py:195` sets
  `state["tick_phase"] = tick.phase`.
- **Live:** never set.
- **Read by:** observability/tracing surfaces. Effectively decorative
  in pipeline outputs today.
- **Contract gap:** if the field stays decorative, drop it; if it's
  load-bearing for any agent decision, add to §A.

### `last_executed_tick_id`, `last_snapshot` — UNMODELLED (low)

- **`last_executed_tick_id`:** Executor writes via direct mutation
  (`src/agents/executor/agent.py:171`) and also via a yielded
  `state_delta` (`src/agents/executor/agent.py:202-210` — mixed
  pattern; see Rule 1 below). The driver-side post-tick assertion
  (`src/backtest/driver.py:393-401`) consumes
  `state["last_snapshot"]["tick_id"]`, not `last_executed_tick_id`,
  but the Executor's idempotency guard reads it
  (`src/agents/executor/agent.py:42`).
- **`last_snapshot`:** Snapshotter writes direct + state_delta
  (`src/agents/snapshot/agent.py:132-138`). Read by the driver's
  pipeline-completion assertion.
- **Contract gap:** both are in-tick handshake fields (Executor
  idempotency, driver completion check). Add to §A as tick-scoped, or
  re-classify them as observability-internal.

### `watchlist` — UNMODELLED (low)

- **Backtest:** `src/backtest/runner.py:475-491` seeds
  `state["watchlist"]` separately from `state["tickers"]`.
- **Live:** never set.
- **Read by:** `src/backtest/driver.py:205,283` consumes
  `state.get("watchlist", [])` for the per-tick broker price refresh.
- **Contract gap:** in live, the equivalent code path doesn't exist
  (single-process invocation; broker prices are pulled at Phase 2 once).
  Either fold `watchlist` into `tickers` (same content), or add to §A
  scoped to backtest only. Mild — the duplicate today is the cheap-and-
  identical case.

### `held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`, `{analyst}_data`, `{analyst}_evidence` — UNMODELLED (intermediate)

These are within-tick agent-to-agent handoffs. §A's "Scope of the table"
note explicitly allows them: pipeline-internal working state is
implementation, not contract.

- `held_positions_view` — built by `_held_view_before_callback`
  (`src/agents/strategist/agent.py:70`) via direct mutation. Consumed by
  the Strategist instruction template (`src/agents/strategist/prompts.py:43`).
- `ticker_evidence` / `ticker_evidence_objects` — built by
  `_evidence_view_before_callback`
  (`src/agents/strategist/agent.py:176-177`) via direct mutation. Consumed
  by the Strategist instruction template
  (`src/agents/strategist/prompts.py:46`) and by `EvidenceWriter`
  (`src/agents/contract/evidence_writer.py:106`).
- `{analyst}_data` — set by each analyst's fetch callback
  (`src/agents/analysts/technical/fetch.py:4`,
  `fundamental/fetch.py:211`, `news/fetch.py:3`, `social/fetch.py:29`).
- `{analyst}_evidence` — set by each analyst's after callback. Consumed
  by `EvidenceWriter` (`src/agents/contract/evidence_writer.py:88-89`).

All of these are direct dict mutations from callback / `_run_async_impl`
code paths — Rule 1 applies, and Rule 2 (`temp:`) is a clean fit for
`held_positions_view` and the two `ticker_evidence*` keys. See Rule 1
and Rule 2 audits below.

---

## §B — Phase boundary audit

### Phase 1 — Run-start — CONFORMANT

- **Live:** the Cloud Run Job entrypoint instantiates broker, persistence,
  and providers before calling the per-tick entry. Pre-deployment, the
  surface is `src/orchestrator/tick.py` plus its imports.
- **Backtest:** `src/backtest/runner.py:__init__` (around lines
  180-240) loads windows, wires the FakeBroker, opens the
  per-run SQLite DB, and constructs the cache-backed providers.
- **Symmetric:** both lifecycles do equivalent work; only the concrete
  implementations differ (live brokers/providers vs fake). The §D-D3
  carve-out covers the difference.

### Phase 2 — Tick-start — DEVIATION (high)

The critical phase. The spec demands every cross-tick field be loaded
from the persistence layer, not seeded fresh.

**Live (`src/orchestrator/tick.py:_build_initial_state`):**
```python
return {
    "tick_id": tick_id,
    "tickers": tickers,
    "memory_buffer": [],   # cross-tick → seeded empty
    "day_digest":    "",   # cross-tick → seeded empty
    "thesis":        "",   # cross-tick → seeded empty
    "positions":     {},   # cross-tick → seeded empty
    "portfolio":     portfolio.model_dump(mode="json"),
    "reference_prices": {sym: ph.model_dump(mode="json") for ...},
}
```
No `as_of` seeded. No persistence read. The four cross-tick fields are
seeded with empty values every tick.

**Backtest (`src/backtest/runner.py:475-491`):**
```python
state: dict = {
    "tickers":          wl_filtered,
    "watchlist":        wl_filtered,
    "portfolio":        portfolio.model_dump(mode="json"),
    "positions":        {},
    "memory_buffer":    [],
    "day_digest":       "",
    "thesis":           "",
    "reference_prices": {...},
}
```
Same seed-empty pattern. `tick_id` and `as_of` are set later by the
driver per tick (`src/backtest/driver.py:192-202`). Then on each tick
the driver does `state.update(dict(updated.state))` at line 382-383 —
**this is the silent backstop**. The four cross-tick fields keep their
values across ticks not because a Phase 2 persistence read populated
them, but because the previous tick's in-memory state was carried over.

**Spec violation:** Phase 2 demands persistence-backed reads. Both
lifecycles fail this requirement.

**Live also missing the `as_of` / `tick_phase` setters** — see the §A
unmodelled-field findings above.

### Phase 3 — During-tick — DEVIATION (medium)

The pipeline topology is correct (single SequentialAgent with the
spec-conformant ordering): see `src/orchestrator/pipeline.py:109-121`:

```
HourlyTick (Sequential)
  → AnalystPool (Parallel: Technical, Fundamental, News, Social)
  → EvidenceWriter
  → Strategist (LlmAgent)
  → StrategistDecisionWriter
  → RiskGateAgent
  → Executor
  → MemoryWriter
  → Snapshotter
```

This topology is identical in live and backtest — the spec's "agent
composition and ordering is non-additive" condition (§D non-carve-outs)
holds.

The deviations during Phase 3 are all Rule 1 / Rule 2 violations,
detailed below.

### Phase 4 — Tick-end — DEVIATION (high)

The spec demands all cross-tick `state_delta`s written during the tick
be persisted to the persistence layer before the process exits or the
next tick begins.

**Current code:**

- **Live:** no Phase 4 work for cross-tick state. The process simply
  exits; whatever was in `state` is dropped. `MemoryWriter`'s
  `state_delta` is in-memory only; once the Cloud Run Job process dies,
  it is lost.
- **Backtest:** no persistence write either. The next-tick survival
  comes from `state.update` at `src/backtest/driver.py:382-383`, not
  from a persistence read at the next Phase 2.

**In-pipeline DB writes that DO happen** (these are audit logs, not
cross-tick state persistence — see Rule 7 audit):

- `EvidenceWriter.commit()` writes `AnalystEvidenceRow` +
  `TickerEvidenceRow` (`src/agents/contract/evidence_writer.py:127`).
- `StrategistDecisionWriter.commit()` writes `TickerStanceRow`
  (`src/agents/strategist/decision_writer.py:109`).
- `Executor.save_trade_log_entry(...)` writes trade log rows
  (`src/agents/executor/agent.py:131`).
- `Snapshotter.save_portfolio_snapshot(snap)` writes portfolio
  snapshot rows (`src/agents/snapshot/agent.py:111`).

None of these read or persist the four cross-tick `state` fields.
They are audit/observability writes that happen to live in Phase 3
(mid-pipeline) rather than a clean Phase 4. See Rule 7 audit.

---

## §C — Cross-cutting rules audit

### Rule 1 — State mutation must ride on Events — DEVIATION (high)

The contract demands every state write be a yielded
`Event(actions=EventActions(state_delta={...}))`. Audit found the
following direct-mutation sites in production code paths:

| Site | File:line | What it writes | Severity |
|---|---|---|---|
| Strategist held-view callback | `src/agents/strategist/agent.py:70` | `state["held_positions_view"]` | medium |
| Strategist evidence-view callback | `src/agents/strategist/agent.py:176-177` | `state["ticker_evidence"]`, `state["ticker_evidence_objects"]` | medium |
| Strategist validation callback (conformant — in-tick carve-out) | `src/agents/strategist/agent.py:383` | `state["strategist_decision"]` (overwriting the `output_key` write) | conformant (in-tick carve-out) |
| RiskGate | (single-tick consumer; no state_delta yield) | `state["final_orders"]`, `state["risk_clamps_applied"]` | low |
| Executor | `src/agents/executor/agent.py:169-171` | `state["executions"]`, `state["positions"]`, `state["last_executed_tick_id"]` (then ALSO yields state_delta at 202-210 — **defensive double write**) | low |
| MemoryWriter | `src/agents/memory/writer.py:165-167` | `state["memory_buffer"]`, `state["day_digest"]`, `state["thesis"]` (then ALSO yields state_delta at 181-189) | low |
| Snapshotter | `src/agents/snapshot/agent.py:132` | `state["last_snapshot"]` (then ALSO yields state_delta at 134-138) | low |
| Technical analyst | `src/agents/analysts/technical/agent.py:129` | `state["technical_verdicts"]` | medium |
| Social analyst | `src/agents/analysts/social/agent.py:120` | `state["social_verdicts"]` | medium |
| Analyst fetch callbacks (×4) | `src/agents/analysts/{technical,fundamental,news,social}/fetch.py` | `state["{analyst}_data"]` | low |
| Analyst after-callbacks (×4) | each analyst module | `state["{analyst}_evidence"]` | low |

**Pattern recognition:**

- **Defensive double writes** (Executor, MemoryWriter, Snapshotter):
  the code writes direct *and* yields a `state_delta`. The yield is
  the contract-correct path. The direct write is belt-and-braces
  insurance against ADK propagation timing. Recommend dropping the
  direct writes once the persistence layer is wired and the yield
  path is proven (commit messages on `838f734`, `50f0f60` cover the
  ownership rationale).

- **Direct-only callback writes** (held-view, evidence-view, validation):
  these are the most exposed sites. The fix is to lift them out of
  callbacks into agents that can yield events, or wrap them in an
  agent shim.

- **Direct-only BaseAgent analyst writes** (Technical, Social): the
  cleanest fix is to yield the `state_delta` from `_run_async_impl`
  alongside the trace event. The shape `state[k] = v` becomes
  `yield Event(actions=EventActions(state_delta={k: v}))`.

- **Direct-only RiskGate writes** (`final_orders`, `risk_clamps_applied`):
  consumed in the same tick by Executor. Same fix as analysts.

- **Fetch / after callbacks on analysts**: same fix pattern; or use
  `temp:` keys (Rule 2) if the field is truly invocation-scoped.

### Rule 2 — `temp:` prefix is invocation-scoped — DEVIATION (low)

Spec demands `temp:` keys for analyst-to-strategist handoff. Code uses
no `temp:` prefix at all today. Three textbook candidates:

- `held_positions_view` — derived by Strategist's own before-callback,
  consumed by its own instruction template. Pure invocation-scoped.
- `ticker_evidence`, `ticker_evidence_objects` — derived from analyst
  outputs, consumed by Strategist + EvidenceWriter. Invocation-scoped
  (the persisted form lives in DB rows; the in-state form is
  ephemeral).
- The four `{analyst}_data` keys (`technical_data`, `fundamental_data`,
  `news_data`, `social_data`) are also invocation-scoped — they are
  the fetch-callback handoff to the analyst's own `_run_async_impl`.

Renaming to `temp:held_positions_view` etc. would:
1. Self-document the lifetime;
2. Prevent accidental persistence in the future (ADK's documented
   `temp:` semantics);
3. Surface drift if anyone tries to read them across ticks.

### Rule 3 — Callbacks never re-prompt — CONFORMANT

Verified at `src/agents/strategist/agent.py:235-246` — there's an
explicit comment block stating "why we raise instead of returning
Content", and the validation callback raises `ValueError` rather than
attempting a re-prompt. Spec satisfied.

No other callback in the codebase attempts re-prompting either.

### Rule 4 — ParallelAgent unique `output_key`s — CONFORMANT

The four AnalystPool branches write to four distinct keys:

- `technical_verdicts` (via direct mutation;
  `src/agents/analysts/technical/agent.py:129`)
- `fundamental_verdicts` (via `output_key`;
  `src/agents/analysts/fundamental/agent.py:164`)
- `news_verdicts` (via `output_key`;
  `src/agents/analysts/news/agent.py:119`)
- `social_verdicts` (via direct mutation;
  `src/agents/analysts/social/agent.py:120`)

Different keys, no collision. Rule 4 holds today. **Caveat:** the
mechanism asymmetry (two `output_key`s + two direct writes) is a Rule 1
issue (above), not a Rule 4 issue.

### Rule 5 — LoopAgent termination — N/A (preventative)

No `LoopAgent` is wired into the current pipeline. Spec applies
preventatively; no audit finding.

### Rule 6 — AgentTool isolation — N/A (preventative)

No `AgentTool` is wired into the current pipeline. Spec applies
preventatively; no audit finding.

### Rule 7 — Lifecycle owns cross-tick persistence — DEVIATION (medium)

The spec splits responsibilities:

- **Pipeline:** reads/writes `state` only.
- **Lifecycle wrapper:** Phase 2 persistence read; Phase 4 persistence
  write.

Current code has **four pipeline-internal agents** that write to the
SQLAlchemy DB:

1. `EvidenceWriter` (`src/agents/contract/evidence_writer.py:127`) —
   commits analyst + ticker evidence rows.
2. `StrategistDecisionWriter`
   (`src/agents/strategist/decision_writer.py:109`) — commits ticker
   stance rows.
3. `Executor` (`src/agents/executor/agent.py:131`) — commits trade
   log rows via `save_trade_log_entry` (import at line 101).
4. `Snapshotter` (`src/agents/snapshot/agent.py:111`) — commits
   portfolio snapshot rows.

**Are these Rule 7 violations?** Partly. None of them reads cross-tick
state from the DB — they only write audit/observability rows. By the
spec's strict reading, "cross-tick persistence is the lifecycle's job"
governs the four §A cross-tick fields. These four agents handle audit
data, not cross-tick state.

**But:** they live in the pipeline (`build_pipeline` in
`src/orchestrator/pipeline.py:109-121`), not in the lifecycle wrapper.
A clean implementation of the spec would lift the audit-row writes out
of the pipeline and into the lifecycle wrapper's Phase 4. The cleanest
test: if a future lifecycle (e.g. a notebook-driven REPL) wanted to run
the pipeline without persistence, today's pipeline forces it to provide
an SQLAlchemy session. The spec says the pipeline should be lifecycle-
agnostic.

**Mitigation in current code:** all four accept `db_session=None` and
no-op when absent (the smoke test relies on this). So the dependency is
optional. The recommendation is a refactor to move these writers from
pipeline-internal agents to Phase 4 lifecycle hooks. Not blocking; a
clean-up.

### Rule 8 — Observability is additive — CONFORMANT

`TraceWriter` is gated by `STOCKBOT_TRACE=1`
(`src/orchestrator/pipeline.py:78`) — when off, no callback hooks are
installed. The `_trace`, `_decision_logger` state keys are read-only
from the agent's perspective (agents log into them; they do not consume
them). No agent decision depends on a trace being present.

---

## §D — Carve-out audit

### D1 — Observability writes — CONFORMANT

Live and backtest write traces to different sinks; the pipeline does
not read them. The decision logger is keyed on the FakeBroker /
Trading212Broker output equally. No contract drift.

### D2 — LLM stubbing in tests — CONFORMANT

The end-to-end smoke test
(`tests/integration/backtest/test_end_to_end_smoke.py`) installs
`before_model_callback` shims that return synthetic `LlmResponse`s.
Per the spec, this honours the callback contract (Rule 3 — return a
replacement, not a re-prompt) and preserves the `state_delta` shape.
No drift.

### D3 — Broker implementation — CONFORMANT

`FakeBroker` and `Trading212Broker` share the broker interface. The
spec's §A `portfolio` row says `Source of truth: Broker API` — agnostic
to which concrete broker. No drift.

### Candidate carve-out — `as_of` resolution divergence — TO PROMOTE TO §A

Already covered under §A unmodelled-fields. Repeating here for
completeness: this divergence fails the "cannot change pipeline outputs"
test (timestamps differ on persisted rows) and should be modelled in
§A as a tick-scoped field owned by the lifecycle wrapper, not added to
§D.

---

## §E — Persistence subsystem audit

The persistence subsystem does not exist yet. Four §A rows depend on
it. The four cross-tick deviations above (`positions`, `memory_buffer`,
`day_digest`, `thesis`) all wait on this subsystem. The follow-up spec
will define schemas, lifecycle hooks, and storage backends.

**What exists today that the subsystem can build on:**

- Backtest already has `runs/<run-id>/db.sqlite` with SQLAlchemy
  models for `AnalystEvidenceRow`, `TickerEvidenceRow`,
  `TickerStanceRow`, `TradeLogEntry`, `PortfolioSnapshot`. These are
  audit rows; not the four cross-tick fields. But the SQLAlchemy
  wiring (sessions, savers in `orchestrator/persistence.py`) is
  reusable.
- The `_strategist_validation_callback` already constructs a
  `StrategistDecision` Pydantic model; this is the natural shape for
  thesis-memory rows.
- `MemoryWriter` already yields a `state_delta` with the right shape
  (`memory_buffer`, `day_digest`, `thesis`); it just needs a
  persistence hook on Phase 4 and a rehydration hook on Phase 2.

**What's structurally missing:**

- No live equivalent of the per-run SQLite DB. The Cloud Run lifecycle
  has no persistence backend chosen yet.
- No `positions` writer at all — neither Strategist nor MemoryWriter
  emits a `state_delta` for the thesis book today. (Executor emits a
  `state_delta` for the post-trade `positions` dict — but that's
  post-execution state, not the entry-rationale thesis.) The §A row's
  framing ("Strategist via state_delta") implies a missing writer
  agent. The follow-up persistence spec needs to land this.

---

## Cross-cutting structural notes

### Naming drift between spec and code — RESOLVED (A1.5, 2026-05-20)

The audit's one naming drift (singular vs plural verdict keys) was
resolved by updating the spec to match the shipping plural form. No
code changes were needed. Historical context retained here so future
audits can trace the decision.

### `memory_buffer` / `day_digest` / `thesis` owner asymmetry

The §A spec lists distinct owners for these three (Strategist for
`thesis`, MemoryWriter for the other two). Code: `MemoryWriter` emits
all three in a single `state_delta` (`src/agents/memory/writer.py:181-189`).
The persistence spec will need to resolve this — either reassign
ownership of `thesis` to MemoryWriter, or split the writer into two
agents.

### Defensive double-write pattern

Three agents (Executor, MemoryWriter, Snapshotter) write each state
field both directly (`state[k] = v`) and via `state_delta` yield. The
direct writes are defensive insurance against ADK propagation timing.
Once Rule 1 is enforced everywhere and the persistence layer is wired,
these direct writes should be removed (one source of truth per write).
Until then they are harmless redundancy.

### Mid-pipeline DB writes

Four pipeline-internal agents commit DB rows mid-pipeline (Rule 7
audit). They violate the spirit of the spec (lifecycle wrapper should
own persistence) but not the letter (these are audit rows, not
cross-tick state). The refactor can defer this until the persistence
subsystem lands, then lift all DB writes into a Phase 4 hook.

---

## Refactor surface — minimum changes needed for conformance

Ordered by dependency. Items in bold are blocking for §A conformance.

1. **Build the persistence subsystem (§E follow-up spec).** Until this
   exists, the four high-severity cross-tick deviations cannot be
   closed.

2. **Wire Phase 2 persistence reads** in both lifecycles:
   - Live (`src/orchestrator/tick.py:_build_initial_state`) reads
     `positions`, `memory_buffer`, `day_digest`, `thesis` from the
     persistence layer.
   - Backtest (`src/backtest/runner.py:_build_initial_state`) does
     the same. The `state.update` carry-over at
     `src/backtest/driver.py:382-383` becomes redundant for these
     fields (debatable whether to keep it for ephemeral session keys
     like `_trace`).

3. **Wire Phase 4 persistence writes** in both lifecycles. The
   `MemoryWriter` and Strategist `state_delta`s already emit the
   right shape; the lifecycle wrapper subscribes and writes.

4. **Fix Rule 1 deviations** in callbacks and BaseAgent analysts:
   - Convert Strategist callback writes
     (`src/agents/strategist/agent.py:70,176-177`) to yield-based
     writes.
   - The strategist validation callback at :383 is now documented as
     conformant under the in-tick carve-out — see
     ``contract-invariants.md`` §C-Rule 1.
   - Convert Technical / Social analysts'
     `_run_async_impl` writes to yield `state_delta`.
   - Convert RiskGate writes (currently single-tick consumer) to
     yields.
   - Convert analyst fetch + after callbacks to yields or `temp:`
     keys.

5. **Add `temp:` prefix** to invocation-scoped fields
   (`held_positions_view`, `ticker_evidence`, `ticker_evidence_objects`,
   `{analyst}_data`).

6. **Add `as_of` to §A** as a lifecycle-owned tick-scoped field; have
   live set it from wall-clock at Phase 2.

7. **Decide on `tick_phase`, `last_executed_tick_id`, `last_snapshot`,
   `watchlist`** — promote to §A or remove.

8. **Resolve naming drift** (`*_verdict` vs `*_verdicts`).

9. **Drop defensive double writes** in Executor / MemoryWriter /
   Snapshotter once 1-3 are in place.

10. **Lift DB writers** (EvidenceWriter, StrategistDecisionWriter,
    Executor trade log, Snapshotter) into Phase 4 lifecycle hooks
    (Rule 7 cleanup; non-blocking).
