# todo-fixes.md

A consolidated, severity-ordered backlog of **existing-codebase fixes** —
"bad existing issues" rather than future features.  Distinct from
`docs/superpowers/backlog.md`, which is reserved for new capabilities.

Origins drawn together here:

- The 2026-05-18 codebase rot audit (initiated by user after multi-week,
  multi-session development raised concerns about old-design artefacts and
  layer tension).
- `docs/Phase7-pre-backtest-cleanup/code-review.md` — the 33k-word pre-backtest
  review.  Phase 7 closed out (`done.md`) with B1–B8 fixed, D2 deleted, and
  D1, D3–D9 plus O1–O7 explicitly deferred.  Until now those deferrals had no
  destination tracker (`docs/Phase8-post-backtest-fixing/plans/` only holds
  the unrelated `pit-correctness-and-audit-v2.md`).
- The five "bad-existing-issue"-shaped items that had been parked in
  `docs/superpowers/backlog.md`: **B26, B27, B28, B29, B30**.  These should
  be removed from the backlog once their entries below are read and accepted.
- Net-new findings from the 2026-05-18 audit not previously recorded
  anywhere.

Items here are grouped by what can be implemented together, with each group's
cohesion justified.  Each item carries enough detail for a
`docs/superpowers/specs/<topic>.md` to be written directly from it: goal,
non-goals, current state with file:line citations, key decisions the spec
must resolve, and rough effort.  Severity tags are *production-impact* —
"would this distort a backtest result or a paper trade?" — not cosmetic.

---

## How to read this file

Two tiers, then a special-cases bucket.

1. **Pre-backtest correctness gates** — Groups 1, 2 & 2.5.  These would
   silently distort or invalidate a first real backtest run.  Ship before
   the first trustworthy backtest window.  Group 2.5 was added 2026-05-19
   after the trial 1-tick run aborted on an ADK state-propagation issue
   that will recur every full-window backtest and every live tick.
2. **Deferred until after backtest** — Groups 3 & 4.  Low-risk hygiene that
   does not change pipeline behaviour.  Sequenced after the first backtest so
   the team can focus pre-backtest energy on correctness, not cleanup.  In
   principle nothing here is blocked on backtest evidence — order is a focus
   choice, not a dependency.
3. **Empirically gated** — Group 5.  Cannot be specced honestly until the
   first backtest has produced data.  Trying earlier risks speculative
   redesign.

`docs/Phase8-post-backtest-fixing/plans/pit-correctness-and-audit-v2.md`
remains the home for PIT-correctness work (yfinance retroactive-adjustment,
`pit_composite` `acceptedDateTime`) — that is data-correctness work blocked on
the first audit log review and is **out of scope** for this file.

---

## Pre-backtest correctness gates

### Group 1 — Config-as-truth

**Cohesion justification:** every item is the same disease — a value declared
in `config/*.json` that the running code ignores, or worse, contradicts.  All
four touch the same family of files (`config/*.json`, `config/README.md`,
`src/data/config.py`, the still-missing `src/backtest/settings.py`, plus the
analyst fetch modules).  All four should be policed by the same enforcement
mechanism (a contract test that AST-walks the call sites and refuses
literal-integer magic constants).  Specifying them together avoids designing
the same enforcement test five times.

#### 1.1 — Unify analyst lookback days into `config/data.json` (HIGH)

**Goal.** Make `config/data.json` the single source of truth for every
per-domain lookback window the system uses.  Analyst modules, the aggregator,
and the backtest fetcher all read it at runtime.  No more uncoordinated
hard-coded constants.  A contract test guarantees no future drift.

**Non-goals.**

- Do **not** redesign the heuristics; this is a plumbing change, not a model
  change.
- Do **not** introduce a separate "backtest lookback" config — backtest and
  live must read the same key.
- Do **not** widen scope to "every magic number in the codebase" — confine to
  lookback windows for the four named domains plus the obvious siblings
  (`earnings_lookback_quarters`, `short_interest_lookback_days`).  History
  period/interval may follow in a separate fix only if the spec author wants.

**Current state — three uncoordinated sources, with disagreeing values:**

| Domain | `config/data.json` | `src/data/config.py` Pydantic default | Analyst-module constant | Aggregator kwarg | Backtest cache provider |
|---|---|---|---|---|---|
| `politician_lookback_days` | **90** | 90 | **30** (`src/agents/analysts/smart_money/fetch.py:38`) | 90 (`src/data/aggregator.py:92`) | 90 (`src/backtest/providers/politician_trades_cache.py:29`) |
| `notable_holder_lookback_days` | **180** | 180 | **90** (`src/agents/analysts/smart_money/fetch.py:39`) | 180 (`src/data/aggregator.py:93`) | **365** (`src/backtest/providers/notable_holders_cache.py:27`) |
| `insider_lookback_days` | 30 | 30 | 30 (`src/agents/analysts/fundamental/fetch.py:53`) | 30 (`src/data/aggregator.py:91`) | — |
| `news_lookback_days` | 7 | 7 | — (passed by aggregator) | 7 (`src/data/aggregator.py:90`) | — |

`get_config().defaults.*` is referenced **only** by
`tests/unit/data/test_config.py`.  No production code consumes it.  The
`FetchDefaults` model exists purely to schema-validate the JSON and is then
discarded.  `scripts/backtest_fetch.py` introduced a *fourth* source today as
a tactical fix for the SVB-2023 start-of-window coverage gap
(`_ANALYST_LOOKBACK_DAYS` mirror dict, annotated as duplicate-pending).

**Key decisions for the spec.**

- **Declared vs actual.** For each disagreeing pair, decide which value is
  correct (the literature — Cohen–Malloy–Pomorski for insider, Ziobrowski et
  al. for politician — tends toward 90-day windows).  The cheapest moment to
  revisit values is now.
- **Read path.** Does the aggregator pull from `get_config().defaults` and
  forward as kwargs (today's signature, just sourced from config), or do the
  analyst fetch modules read the config directly?  Trade-off: kwarg flow keeps
  the aggregator's call surface readable but spreads config knowledge; direct
  reads centralise the dependency but couple analyst code to the config
  loader.
- **Enforcement.** `tests/contract/test_no_magic_lookbacks.py` — an AST walker
  that asserts every `lookback_days=` keyword in `src/agents/analysts/` and
  `src/data/aggregator.py` is bound to a name from the config loader, not a
  literal int.
- **Sequencing.** Per-domain commits (low risk, four passes) vs one bundled
  PR (high churn, less ambiguity).

**Effort.** ~one phase.  Every call-site change is a one-liner across roughly
five files; the contract test is the bulk of the work.

**Origin.** Surfaced 2026-05-18 during SVB-2023 backfill validation; logged as
`B30` in `docs/superpowers/backlog.md:475`; expanded in the 2026-05-18 audit.

---

#### 1.2 — Either honour or remove `config/backtest_settings.json` schedule keys (MED)

**Goal.** Make `config/backtest_settings.json` keys mean what `config/README.md`
says they mean.  Either `src/backtest/schedule.py` reads `tz`, `open_time`,
`close_time`, `ticks_per_day` from config, or the keys are deleted and
`config/README.md` is updated to reflect the hard-coding.

**Non-goals.**

- Do **not** add new schedule features (multiple intraday ticks, pre-/post-
  market) — this is a config-truth fix, not a functionality change.
- Do **not** introduce a separate `schedule.json` file — the keys belong in
  `backtest_settings.json` if they live in config at all.

**Current state.**

- `config/backtest_settings.json:3-7` declares `ticks_per_day: ["open","close"]`,
  `tz: "America/New_York"`, `open_time: "09:30"`, `close_time: "16:00"`.
- `src/backtest/schedule.py:15,20-22` hard-codes the same values as module
  constants (`Phase = Literal["open","close"]`, `_NY = ZoneInfo(...)`,
  `_OPEN_TIME = time(9,30)`, `_CLOSE_TIME = time(16,0)`).
- `config/README.md` documents the JSON keys as authoritative.  Editing them
  does nothing.

**Key decisions for the spec.**

- **Honour vs delete.** Will the bot ever want a non-NYSE schedule, an
  extended-hours tick, or per-window tick cadence?  If yes, honour the
  config.  If no for the foreseeable future, delete the keys to remove the
  trap.
- **Calendar coupling.** `pandas_market_calendars` is already imported and
  hard-codes "NYSE"; honouring `tz` from config without also exposing the
  calendar name is half a fix.

**Effort.** Small.  Either ~20 lines of loader + a few tests, or a 4-line
config-and-README edit.

**Origin.** 2026-05-18 audit, net-new (not in Phase 7 review).

---

#### 1.3 — Either honour or remove `http_timeout_seconds` (MED)

**Goal.** Stop carrying a config key that no provider reads.  Either every
HTTP-talking provider takes its timeout from `get_config().http_timeout_seconds`,
or the key (and its `FetchDefaults` field) is deleted.

**Non-goals.**

- Do **not** introduce per-provider timeout overrides as part of this fix —
  if needed, a separate spec.
- Do **not** unify on a single global timeout if some providers genuinely need
  different windows (Alpha Vantage long-window news vs Finnhub short polls).
  In that case, delete the misleading global key.

**Current state.**

- `config/data.json:30` declares `http_timeout_seconds: 15.0`.
- `src/data/config.py:49` mirrors the default.
- The only reference outside the loader is
  `src/data/providers/politician_trades/quiver.py:18`:
  `_HTTP_TIMEOUT = 15.0  # mirrors today's settings.http_timeout_seconds default`
  — i.e. a comment explicitly acknowledging the drift, never fixing it.
- No provider in `src/data/providers/` reads `get_config().http_timeout_seconds`.

**Key decisions for the spec.**

- **One timeout for all, or per-provider?**  Audit each provider for what
  timeout window it actually needs.  If the answer is "they all want
  different things", deleting the global key is the right move.
- **HTTP-client-level vs per-call timeout.**  Most providers wrap `httpx`;
  pinning the timeout in the shared client factory is cleaner than per-call.

**Effort.** Small.  Audit + ~10 lines of plumbing or a deletion.

**Origin.** 2026-05-18 audit, net-new.

---

#### 1.4 — Create `src/backtest/settings.py` as the loader `config/README.md` promises (MED)

**Goal.** Stop the ad-hoc `json.loads(Path("config/backtest_settings.json").read_text())`
pattern.  Introduce the typed loader `config/README.md` already advertises and
have every consumer call it.

**Non-goals.**

- Do **not** redesign the backtest settings schema — fix the loader first,
  refactor the keys (1.2, 1.3) as separate work.
- Do **not** generalise into a "settings registry" — one loader, one cache,
  same pattern as `src/data/config.py`.

**Current state.**

- `config/README.md:15` lists the loader as **"`src/backtest/settings.py`
  (planned)"**.  The file does not exist.
- Five consumers parse the JSON directly:
  `scripts/backtest_report.py:44`, `scripts/backtest_audit_tick.py:88`,
  `scripts/backtest_fetch.py:407`, `scripts/debug_cache_audit.py:451`,
  `src/backtest/runner.py:185,196`.
- `src/backtest/reporting.py:61` consumes the dict produced by the runner.

**Key decisions for the spec.**

- **Pydantic model shape.** Mirror `DataConfig` / `FetchDefaults` (Pydantic +
  `lru_cache`).  Same `_reset_cache` test hook.
- **Sequencing with 1.2 and 1.3.** Either (a) write the loader first, then
  use the cleanup of 1.2/1.3 to delete unused fields, or (b) fold 1.2/1.3
  into the same PR so the loader is born with the right schema.  (b) is one
  commit, (a) is three; team preference.

**Effort.** Small.  ~40 lines of loader + tests + one find-and-replace pass
across the five consumers.

**Origin.** 2026-05-18 audit, net-new.

---

### Group 2 — Data-shape contracts at the layer boundaries

**Cohesion justification:** every backtest tick crosses cache →
provider → aggregator → analyst boundaries.  If those boundaries do not agree
on shape, the backtest is comparing apples to oranges across the live/cache
divide.  Each item below is a different leak surface in the same contract;
all three benefit from the same audit step (cataloguing per-domain return
shape) and the same enforcement test (contract-level type assertion).
Specifying them together avoids running the same audit three times.

#### 2.1 — Provider Protocol return-type unification (HIGH)

**Goal.** Every `Provider` Protocol domain (the 14 listed below) declares one
canonical return type, and both the live provider and the backtest cache
provider must return that exact type.  No reconciling wrappers.

**Non-goals.**

- Do **not** rewrite the cache store schema in this fix — flat tables stay
  flat; reconstitution-to-canonical-type happens in the cache provider's
  return path.
- Do **not** introduce streaming/async-iterator return types — keep
  call-and-return semantics.

**Current state.**

- 14 domains:  `price_history`, `company_ratios`, `news`, `social_sentiment`,
  `insider_trades`, `politician_trades`, `notable_holders`, `filings`,
  `earnings`, `analyst_consensus`, `short_interest`, `options`,
  plus the two sub-types under insider_trades (trades + derivatives).
- Known divergence: `insider_trades` — live EDGAR returns `Form4Bundle`,
  cache returned `list[InsiderTrade]` until the v1 wrap at
  `src/backtest/providers/insider_trades_cache.py:14-16` papered it over.
- The wrap fixed Smart_Money silently degrading to `is_no_data` but the
  underlying contract drift remains; will recur the next time a model
  evolves.

**Key decisions for the spec.**

- **Type location.** Per-domain Pydantic model exported from `data/models/`,
  or a generic `Provider[T]` on the Protocol itself?
- **Conformance enforcement.** Runtime `isinstance` check in the `register`
  decorator (cheap, runs on import), or static type-checking only?
- **Cache-store layer.** Stays flat with per-cache reconstitution (today),
  or grows `read_<domain>_bundle()` methods returning the canonical type
  directly?
- **Audit step.** Catalogue every domain's current vs target return shape
  before writing the fix — the audit is the bulk of the work.
- **Migration.** Per-domain (14 PRs, low risk) vs bundled (one PR, high
  churn).

**Effort.** Roughly two phases — audit + per-domain migration.  The audit
itself is one PR.

**Origin.** `B26` in `docs/superpowers/backlog.md:186`; surfaced during
`providers-and-silent-gaps-v1` PR commit `900c720`.

---

#### 2.2 — Normalise `state["smart_money_data"]` shape to per-ticker convention (MED)

**Goal.** Make the smart_money state-key follow the same `{ticker: payload}`
shape as every other analyst's per-ticker raw-data slot.

**Non-goals.**

- Do **not** redesign the smart_money analyst's category split (politicians
  vs notable_holders) at the *fetch* layer — those remain separate fetch
  paths.  Only the *state-key shape* changes.
- Do **not** rename the state key.

**Current state.**

- Every other analyst stores `state["<analyst>_data"]` as
  `{ticker: payload}`.
- Smart_money stores `state["smart_money_data"]` as
  `{"politicians": {ticker: [...]}, "notable_holders": {ticker: [...]}}` —
  two-level nesting keyed by *category first, ticker second*.
- The shape inconsistency caused a slicing bug in
  `agents/analysts/smart_money/agent.py` (`data.get(ticker, {})` always
  returned `{}` because the top-level keys were category names).  Phase 7
  fixed it by reshaping per-ticker at dispatch time, but the underlying
  convention break is still there as a footgun.
- The typed `SmartMoneyRaw` Pydantic model from Phase 7 partially pins
  per-ticker shape but the state-key layout doesn't follow.

**Key decisions for the spec.**

- **Reshape timing.** At fetch (writes flow into the canonical shape), at
  state-write (a transform step), or eliminated by giving smart_money its
  own typed state-key?
- **Backward-compatibility window.** None needed — backtest is pre-deployment,
  no consumers exist yet outside the analyst pool.

**Effort.** Small.  ~50 lines plus the agent's slicing path.

**Origin.** `B27` in `docs/superpowers/backlog.md:439`.

---

#### 2.3 — Tighten or split `aggregator.get_stock_signal_bundle` surface (MED)

**Goal.** Reduce the aggregator's `get_stock_signal_bundle` call signature so
it no longer accepts every lookback as a kwarg (those should be sourced from
config per 1.1) and no longer returns the catch-all `StockSignalBundle`.
Each analyst's fetcher takes responsibility for what *it* needs.

**Non-goals.**

- Do **not** delete the aggregator outright — there's a real use case for
  fetching multiple domains in one call.  The fix tightens the surface, not
  the existence.
- Do **not** roll this into 2.1; this is the call-site shape, 2.1 is the
  return-type shape.

**Current state.**

- `src/data/aggregator.py:87-99` accepts every lookback as a kwarg
  (subsumed by 1.1 above) and returns `StockSignalBundle`, a catch-all type
  that bundles every domain's payload regardless of which analyst needs which.
- Phase 7 review D9 flagged this as deferred for post-backtest.

**Key decisions for the spec.**

- **Per-analyst fetchers vs aggregator.** Already each analyst has its own
  `fetch.py`; if they each pull what they need, what's left of the
  aggregator?  Possibly nothing — delete is on the table.
- **Caching.** If two analysts request the same domain, the aggregator was
  the natural de-dup point.  Without it, is the cache layer enough?

**Effort.** Medium.  Touches every analyst fetch and the aggregator.

**Origin.** Phase 7 code-review D9 (`docs/Phase7-pre-backtest-cleanup/code-review.md:360`).

---

### Group 2.5 — Cross-tick ADK session state propagation

**Cohesion justification:** every custom `BaseAgent` subclass in `src/agents/`
inherits the same contract trap — writes to `ctx.session.state[k]=v` are
visible to other agents in the *same tick* (because they share the same
`ctx.session` reference) but are silently dropped from the storage session
that the driver re-fetches between ticks.  All affected agents are policed
by the same enforcement mechanism (a contract test that AST-walks
`_run_async_impl` for state subscript assignments and demands a paired
`state_delta` event) and all share the same fix shape (yield an `Event`
with `EventActions(state_delta={...})`).  Specifying them together avoids
designing the same audit and the same enforcement test twice.

This group lives in the **pre-backtest correctness gates** because the
user explicitly flagged 2026-05-19 that "this issue will haunt us when
running full backtests since they will count as a session and running
live each tick will be a session".  The first multi-tick backtest is the
moment the cross-tick keys (memory buffer, executions, thesis) actually
need to survive between ticks — at one tick the bug is invisible.

#### 2.5.1 — Audit & fix every custom `BaseAgent` that writes cross-tick state (HIGH)

**Goal.**  Every custom `BaseAgent` subclass that mutates session state for
*cross-tick* consumption uses an `Event` with `EventActions(state_delta={...})`
rather than relying on direct `ctx.session.state[k]=v` assignments.  The
driver's per-tick `get_session` check and any subsequent tick that reads
prior-tick state see consistent storage.

**Non-goals.**

- Do **not** rewrite the driver to pull per-tick context from DB + broker
  instead of session state as part of this item — that is the alternative
  architecture (Option B); evaluate only if Option A's audit reveals too
  many sites to police.
- Do **not** add `state_delta` wrappers to `LlmAgent` instances — ADK
  already routes their output through events.
- Do **not** force every state **read** through a wrapper — only **writes**
  are at risk.
- Do **not** strip the in-tick direct `state[k]=v` mutation when adding the
  yielded event; downstream agents in the same tick read the runtime
  `ctx.session` object, not the storage copy, so the direct write still
  matters for same-tick consumers.

**Current state.**  ADK's `InMemorySessionService._copy_session`
(`.venv/lib/python3.14/site-packages/google/adk/sessions/in_memory_session_service.py:39`)
returns a copy of the session on every `get_session(...)`.  The runtime
`ctx.session` handed to agents is a copy; the storage-side session only
sees mutations that arrive through `append_event(event)` and only when
`event.actions.state_delta` is non-empty
(`in_memory_session_service.py:349-363`).  Custom `BaseAgent`s that write
directly to `ctx.session.state[k]=v` and yield nothing (or yield an Event
without `state_delta`) leave those writes orphaned on the runtime copy;
the next `get_session` returns a fresh copy that does not contain them.

Empirically reproduced 2026-05-19:

```python
class WriterAgent(BaseAgent):
    name: str = "Writer"
    async def _run_async_impl(self, ctx):
        ctx.session.state["written_by_writer"] = "hello"
        return
        yield
# After run_async: session_service.get_session().state lacks
# 'written_by_writer'.  The same agent yielding
#   Event(author=..., invocation_id=...,
#         actions=EventActions(state_delta={"written_by_writer": "hello"}))
# propagates correctly.
```

**Affected agents (audit so far — not exhaustive):**

| Agent | State key(s) | Cross-tick? | Status |
|---|---|---|---|
| `src/agents/snapshot/agent.py:SnapshotterAgent` | `last_snapshot` | yes — driver re-reads `last_snapshot.tick_id` via `session_service.get_session` to detect tick completion | **Patched** 2026-05-19 (surgical fix; yields `state_delta` event) |
| `src/agents/memory/writer.py:MemoryWriter` | `memory_buffer`, `day_digest`, `thesis` | yes — memory_buffer is designed to grow across ticks; DB re-seeds masks the symptom but obscures the bug | **Patched** 2026-05-19 (yields `state_delta` event + permissive-read hydration of dict-shaped buffer entries to `BufferEntry`) |
| `src/agents/executor/agent.py:ExecutorAgent` | `executions`, `positions`, `last_executed_tick_id` | yes — RiskGate / thesis-pruner may read prior-tick `executions` from state, and `positions` carries the opening thesis that SELL ticks need to write the closing trade-log row | **Patched** 2026-05-19 (yields `state_delta` event paired with the existing direct mutation) |
| `src/agents/risk_gate/agent.py:RiskGateAgent` | `risk_gate_action`, `risk_gate_notes` | no — in-tick only (consumed by Executor same tick) | safe (in-tick reads see same `ctx.session` reference) |
| `src/agents/analysts/technical/agent.py:TechnicalAnalyst` | `technical_evidence[ticker]` | no — consumed by `evidence_writer` same tick | safe |

The audit must walk every `BaseAgent` subclass under `src/agents/` and
classify each `state[k]=v` site as in-tick-only (safe) or cross-tick
(needs `state_delta`).

**Verification probe (2026-05-19).**  Empirically confirmed cross-tick
propagation now works end-to-end via a 2-tick backtest
(`trial-debug3`, completed status, two `portfolio_snapshots` rows
written).  The probe itself was indirect but unambiguous: between the
first MemoryWriter patch and the read-side hydration fix, the 2-tick
run aborted on tick 2 with
`AttributeError: 'dict' object has no attribute 'decision_tag'` raised
from `detect_repeat` — proving that the previous tick's
`memory_buffer` entry *had* propagated into tick 2's session as a dict
(it would have been an empty list pre-fix).  Pre-fix: storage merge
never happened, so the next tick would have read the seeded
`memory_buffer: []` from `runner.py` and produced a buffer of size 1
on every tick forever — silent compounding-loss bug, exactly the
failure mode the user flagged 2026-05-19.

**Key decisions for the spec.**

- **Option A — surgical fixes per agent.**  For each cross-tick writer,
  add a yielded `Event` with `EventActions(state_delta={...})` paired with
  the direct mutation (defence in depth — direct mutation handles same-tick
  reads, event handles next-tick reads).  Pros: minimal blast radius, no
  architectural shift.  Cons: easy to forget when a new `BaseAgent` is
  added; relies on enforcement (2.5.2) to stay sound.
- **Option B — drop ADK session state for cross-tick handoff entirely.**
  Driver pulls per-tick context from DB (snapshots, executions, memory) +
  broker (positions, cash) at the start of every tick, seeds it freshly
  into the new session, and treats `state` as a per-tick scratch buffer.
  Pros: removes the contract trap; matches "state is a tick cache, DB is
  truth" model.  Cons: bigger refactor; requires audit of every state-key
  *consumer* too, not just every writer.
- **Sequencing.**  Whichever option is chosen ships before the first
  multi-tick backtest is interpreted as a meaningful result — a 30-day
  window with two ticks/day is 60 ticks where the memory buffer is
  supposed to compound, and that compounding is currently silently lost.
- **Defence in depth pairing.**  Even with Option A, the direct mutation
  should stay alongside the yielded event.  Removing it makes same-tick
  consumers read a stale `state[k]` until the runner merges the event,
  which the driver does between agents but not within a single agent's
  `_run_async_impl`.

**Effort.**  Option A: ~one phase (audit + ~5 agents × ~10 lines + the
contract test in 2.5.2).  Option B: ~two phases (state-key consumer
inventory + driver redesign + per-tick seeder refactor + retire the
session-state-as-handoff convention).

**Origin.**  2026-05-19 trial-backtest failure — 1-tick run aborted with
`RuntimeError: pipeline did not reach snapshotter for tick 'trial-run-2025-09-02T13:30:00+00:00-open' — last_snapshot.tick_id was None`.
Root-caused via empirical reproduction of the ADK state-propagation
mechanism; surgical Snapshotter fix applied the same day to unblock the
trial run (`backtests/baseline-2025-09/runs/trial-run-fix/manifest.json`
shows status `completed`).

---

#### 2.5.2 — Contract test forbidding orphan cross-tick `state[k]=v` writes (HIGH)

**Goal.**  A contract test that AST-walks every `BaseAgent` subclass in
`src/agents/` and refuses a `ctx.session.state[<key>] = <expr>` assignment
inside `_run_async_impl` unless the same method yields an `Event` whose
`actions=EventActions(state_delta=...)` mentions the same key — or the
key is annotated as `# in-tick-only` (a per-line escape hatch).  Catches
2.5.1-style bugs at lint time, not at "the first multi-tick backtest
silently produces nonsense" time.

**Non-goals.**

- Do **not** ban every `state[k]=v` write — in-tick writes are still
  idiomatic.  The check is "does this key escape the tick?".
- Do **not** require `state_delta` for `LlmAgent` `output_key` writes —
  ADK handles those itself.
- Do **not** widen scope to cover `state.update(...)` or `setattr`-style
  mutations as part of this fix; if those crop up in the audit, add them
  in a follow-up.

**Current state.**  Zero enforcement.  The only feedback channel is "a
later tick reads a stale key".  The Snapshotter incident (2026-05-19)
happened because the driver's completion check noticed the missing key;
for `memory_buffer` and `executions` there is no equivalent assertion
and the failure mode is silent.

**Key decisions for the spec.**

- **Static AST walker vs runtime tracker.**
  - **AST walker** (preferred): a pytest collected at `tests/contract/`
    that parses each agent module, finds `Assign` nodes whose target is a
    `Subscript` on `ctx.session.state`, and checks the enclosing
    `_run_async_impl` for a `Yield` of an `Event` mentioning the same
    key in `state_delta`.  Cheap, runs in CI, no runtime overhead.
    Imperfect (false positives possible — e.g. dynamic key names) but the
    escape-hatch comment handles those.
  - **Runtime tracker**: wrap `ctx.session.state` with a dict subclass
    that records writes and asserts at agent-exit that every recorded
    write key appears in the latest yielded `state_delta`.  Stronger
    signal but adds runtime cost and only fires when an agent is
    exercised in tests.
- **Escape-hatch syntax.**  `# in-tick-only` trailing comment on the
  assignment line, parsed by the walker.  Mirrors `# noqa` convention.
- **Coverage scope.**  Start at `src/agents/` only; widen to other
  `BaseAgent` subclasses if any are added in `src/orchestrator/` or
  `src/backtest/`.
- **Contract-spec policy tension** (added 2026-05-20).
  `docs/contract-invariants.md` §C-Rule 1 — *"All
  writes to session state must go through `EventActions(state_delta=...)`
  events yielded by an agent"* — is stricter than this item's
  current escape-hatch design.  The contract makes **no** in-tick
  carve-out; every state write rides on `state_delta`, regardless of
  whether the consumer is the next agent in the same tick or a future
  tick.  The audit (`docs/Phase8-contract-audit-fixes/contract-audit.md` §C-Rule 1)
  records 10+ in-tick direct-mutation sites the contract considers
  deviations — including `held_positions_view`, `ticker_evidence*`,
  `final_orders`, `risk_clamps_applied`, `technical_verdicts`,
  `social_verdicts` — that the current "in-tick is safe" framing
  classifies as fine.  The spec author for 2.5.2 must settle this:
  - **Strict contract (recommended for new code):** drop the
    escape-hatch entirely; the AST walker flags every
    `state[k] = v` inside `_run_async_impl` regardless of consumer
    lifetime.  Forces the in-tick refactors (these are the plan-A1
    workstream from the 2026-05-20 brainstorm).  Higher one-time
    cost; permanent enforcement of the contract.
  - **Pragmatic carve-out (today's design):** keep `# in-tick-only`
    as a permanent escape and accept the contract-deviation rate
    measured by the audit.  Lower cost; permanent gap between code
    and the contract document.
  - **Phased:** strict policy with the escape-hatch initially
    permitted, then a follow-up sweep removes all uses.  Two-step
    landing; preserves enforcement intent.

  Recommend deciding before the AST walker is implemented — the
  walker's emitted error message changes between options.  Also
  note: callbacks (e.g. `before_agent_callback`,
  `after_agent_callback`) cannot yield events at all, so the walker
  either tolerates callback-only writes or the codebase must
  restructure callbacks into `BaseAgent` shims.  Plan A2 (from the
  same 2026-05-20 brainstorm) is the source of those restructures.

**Effort.**  Small.  ~80 lines of AST walker + ~5 unit tests + the
escape-hatch documentation in `docs/` or the agent style guide.

**Origin.**  2026-05-19, paired with 2.5.1.  The Snapshotter incident is
the proof case — every other affected agent (MemoryWriter, Executor) is
currently undetected because no driver-side assertion looks for their
keys.

---

#### 2.5.3 — Replace session-state cross-tick handoff with DB hydration + RAG memory (MED — design phase)

**Contract-invariants lineage** (added 2026-05-20).  This item is the
fix-side counterpart to `docs/contract-invariants.md`
§E (cross-session persistence subsystem) and is the home for the four
high-severity §A cross-tick deviations recorded in
`docs/Phase8-contract-audit-fixes/contract-audit.md`:

| §A field | Lifetime | Current write site | Current read site |
|---|---|---|---|
| `positions` | cross-tick | Strategist intent (missing — no writer today) + Executor fact (`src/agents/executor/agent.py:202-210` state_delta) | both lifecycles seed `{}` at Phase 2 (`src/orchestrator/tick.py:_build_initial_state`, `src/backtest/runner.py:475-491`) — never read from any persistent store |
| `memory_buffer` | cross-tick | MemoryWriter state_delta (`src/agents/memory/writer.py:181-189`) | both lifecycles seed `[]` at Phase 2 — never read from any persistent store |
| `day_digest` | cross-tick | MemoryWriter state_delta (same yield) | both lifecycles seed `""` at Phase 2 — never read from any persistent store |
| `thesis` | cross-tick | MemoryWriter state_delta (same yield); **no Strategist write today despite §A naming Strategist as owner** | both lifecycles seed `""` at Phase 2 — never read from any persistent store |

The four rows above subsume the live bug previously tracked as F1
(`state["positions"]` reset to `{}` on every live tick): it is the
`positions` row, and the same persistence-read-at-tick-start fix closes
it.

The contract names the lifecycle phases that this item must populate:
**Phase 2** (tick-start — `_build_initial_state` in both lifecycles)
reads from the store into `state`; **Phase 4** (tick-end — post-pipeline
code in `src/orchestrator/tick.py` for live and
`src/backtest/driver.py:_run_tick` for backtest) drains
`state_delta`s into the store.  The contract is *additive*: live and
backtest must use the same persistence subsystem and the same hooks
(per §D non-carve-outs in the spec — "cross-tick state persistence is
not a carve-out; both lifecycles do it identically").

**Goal.**  Retire the "session state is the cross-tick handoff" model
in favour of treating session state as a *per-tick scratch buffer*.
Every tick (live or backtest) starts with state seeded from
**authoritative sources**: positions and cash from the broker;
prior-tick memory from a retrieval-augmented generation (RAG) store
that the MemoryWriter writes into; portfolio snapshots from the
persistence DB.  The state_delta plumbing of 2.5.1 becomes a defence
in depth rather than the primary mechanism.

**Non-goals.**

- Do **not** unblock the first multi-tick backtest on this item — the
  surgical fixes from 2.5.1 already do that.  This is the architectural
  follow-up that the user has indicated should be planned *after* the
  uniform state_delta patch lands and is verified.
- Do **not** pick a vector store / embedding model in this item —
  that's the spec's job.  Goal here is just to record the direction
  and the constraints.
- Do **not** widen scope to "all of memory" — the live-side memory
  buffer is the trigger; the day-digest summarisation and the
  evidence/decision logs are separate concerns and can ride on the
  same store but should be specced separately.

**Current state (post 2.5.1).**

- `state_delta` events propagate `memory_buffer`, `day_digest`,
  `thesis`, `positions`, `executions`, `last_executed_tick_id`, and
  `last_snapshot` across ticks within a single ADK
  `InMemorySessionService` lifetime.
- The backtest driver creates one `InMemorySessionService` *per tick*
  (`src/backtest/driver.py:292`) and rehydrates the new session from a
  driver-level dict.  This means even with 2.5.1 in place, the
  session-state handoff is mediated by a driver-private Python dict,
  not by anything persistent.
- The live entrypoint at `src/orchestrator/tick.py:_build_initial_state`
  (lines 85-100) hard-codes `memory_buffer: []`, `day_digest: ""`,
  `thesis: ""`, `positions: {}` every tick — no DB re-load.  In
  cloud-hosted live mode, each scheduled tick is a fresh process
  invocation, so the in-memory handoff is moot and the live bot would
  start every tick amnesiac.

**Key decisions for the spec.**

- **Store choice.**  Vertex AI Vector Search (already used for the
  dedup embeddings — `agents/memory/embeddings.py`), Chroma / DuckDB-VSS
  for local dev, or a SQL-only "memory table" approach (no semantic
  similarity, just last-N retrieval)?  Trade-off: full RAG is more
  capability but more infra; last-N is enough for the
  `MemoryProjection.recent` and `tag_frequency` slots the strategist
  prompt already uses.
- **Retrieval surface.**  At tick-start, what does the seeder ask for?
  Last-N raw entries (simplest)?  Similar entries by current
  market-context embedding (more capability)?  Both?
- **Write surface.**  Same MemoryWriter agent writes both the per-tick
  buffer entry to the store *and* the state_delta for in-process
  consumers, or a separate writer that runs out-of-pipeline?
- **Cloud sync.**  The store has to be reachable from both the
  backtest harness (local) and the future cloud-hosted live tick (one
  GCP region).  Same store with different namespaces, or two stores
  with a sync job?
- **State-as-scratch contract.**  Once the seeder reads from DB/RAG
  rather than the driver dict, can the state_delta plumbing from 2.5.1
  be deleted?  Or is it still load-bearing as a defence-in-depth
  fallback?  This is the same Option A vs Option B question that 2.5.1
  parked.
- **`thesis` ownership.**  `contract-invariants.md` §A names
  Strategist as the owner of `state["thesis"]` alongside `positions`.
  Current code has MemoryWriter writing all three of
  `memory_buffer`, `day_digest`, and `thesis` from a single
  `state_delta` yield (`src/agents/memory/writer.py:181-189`).
  Strategist's `_strategist_validation_callback`
  (`src/agents/strategist/agent.py:388`) does not currently write
  `thesis` and could not yield a `state_delta` even if it did
  (callbacks return None-or-Content; they cannot yield events —
  see contract Rule 3).  Two ways to resolve:
  - **Reassign ownership to MemoryWriter** and update the contract
    spec's §A row.  Cheapest; matches current code.
  - **Add a Strategist-side `thesis` write** via a new `BaseAgent`
    shim that runs after Strategist and yields the
    `state_delta` (this would also be the natural home for Plan A2's
    Strategist-decision JSON coercion shim — same restructure).
    Split MemoryWriter's state_delta in two: it keeps
    `memory_buffer` + `day_digest`, the shim emits `thesis`.
    Cleaner ownership; more code.

  Decision belongs in this spec (not the plan that implements it)
  because it affects the persistence schema design — if Strategist
  owns `thesis`, the thesis-memory schema is keyed on the
  strategist's writes, not MemoryWriter's buffer entries.
- **Lifecycle-wrapper concept.**  `contract-invariants.md` §B names
  four phases: Phase 1 (run-start, once per process), Phase 2
  (tick-start, every tick), Phase 3 (during-tick pipeline), Phase 4
  (tick-end, every tick).  The persistence subsystem attaches at
  Phase 2 (rehydrate from store into `state`) and Phase 4 (drain
  `state_delta`s into store).  Today both lifecycles have *de facto*
  Phase 2 code (`src/orchestrator/tick.py:_build_initial_state` for
  live; `src/backtest/runner.py:475-491` plus
  `src/backtest/driver.py:192-202` for backtest) and *de facto*
  Phase 4 code (post-pipeline tail in both lifecycles — backtest's
  `state.update(dict(updated.state))` at
  `src/backtest/driver.py:382-383` is the closest current
  approximation, but it carries dict-to-dict, not state-to-store).
  Neither is *named* as such.  The spec must decide:
  - **Introduce a formal `LifecycleWrapper` abstraction** with named
    hook methods (`phase_1_setup`, `phase_2_tick_start`,
    `phase_4_tick_end`).  Both lifecycles import and configure the
    same wrapper.  Cleanest contract alignment; bigger refactor.
  - **Augment existing entry points** without a new abstraction —
    just add the Phase 2 read + Phase 4 write at the right line
    numbers.  Smaller change; loses the "lifecycle is a named
    concept" property that contract §C-Rule 7 leans on.

  This decision blocks item 2.5.4 (Lift pipeline-internal DB writers
  into Phase 4 hooks) — 2.5.4 fills the Phase 4 slot that 2.5.3
  defines.

**Effort.**  Two phases (rough).  Phase one is the spec itself + a
proof-of-concept seeder hitting a local Chroma instance to prove the
retrieval surface works against real backtest memory.  Phase two is
the production wiring + cloud-store choice + retirement of the
driver-dict handoff.

**Sequencing.**  Hard-blocked on 2.5.1 being verified at end-to-end
scale (i.e. at least one full-window multi-day backtest passes with
plausible memory-buffer contents).  Without that evidence, redesigning
the cross-tick handoff is speculative.

**Origin.**  2026-05-19 user direction — *"we will work out how memory
persists make sure we apply snapshot fix uniformly and then plan how
to add rag system after"* — after the 1-tick → 2-tick regression
investigation surfaced that the live-tick code path (`tick.py`) is
also amnesiac.  The state_delta fixes only paper over the issue for
in-process multi-tick contexts; the structurally correct answer is
DB/RAG hydration at tick start.

---

#### 2.5.4 — Lift pipeline-internal DB writers into Phase 4 lifecycle hooks (LOW)

**Contract-invariants lineage** (added 2026-05-20).  This item
addresses the medium-severity Rule 7 deviation recorded in
`docs/Phase8-contract-audit-fixes/contract-audit.md` §C-Rule 7.  The contract
(`docs/contract-invariants.md` §C-Rule 7) reads:

> *"The pipeline (analysts → strategist → executor) reads from and
>  writes to **state**.  It does not read from or write to the
>  persistence layer (§E), the broker, or any provider for
>  cross-tick data.  The lifecycle wrapper is responsible for [...]
>  reading cross-tick fields from persistence into `state` at Phase 2
>  (tick-start) [and] writing cross-tick `state_delta`s back to
>  persistence at Phase 4 (tick-end)."*

Today four agents inside the `SequentialAgent` pipeline commit DB
rows mid-run.  Behaviour is correct (the rows land in the right
tables, transactions commit cleanly) — the violation is purely
architectural: the pipeline is not lifecycle-agnostic.  A notebook-
driven REPL or any future third lifecycle would either have to
provide an SQLAlchemy session or carve out the pipeline-internal DB
writes by hand.

This is also *not* the same as 2.5.3 — these are existing
audit/observability writes against settled schemas, not the four
cross-tick state fields.  2.5.3 builds the persistence subsystem
for cross-tick state; 2.5.4 relocates the existing audit writes
into the same Phase 4 hook 2.5.3 introduces.

**Goal.**  Move the four mid-pipeline DB writes out of the pipeline's
`SequentialAgent` and into a Phase 4 lifecycle hook owned by the
wrapper (live entrypoint in `src/orchestrator/tick.py`, backtest
driver in `src/backtest/driver.py`).  The pipeline becomes
lifecycle-agnostic — it produces state; the wrapper persists it.
Both lifecycles call the same hook with the same post-pipeline
`state` shape.

**Non-goals.**

- Do **not** redesign the DB schema or any saver-function signature.
  Schemas (`AnalystEvidenceRow`, `TickerEvidenceRow`,
  `TickerStanceRow`, `TradeLogEntry`, `PortfolioSnapshot`) and savers
  (`save_analyst_evidence`, `save_ticker_evidence`,
  `save_ticker_stance`, `save_trade_log_entry`,
  `save_portfolio_snapshot` in
  `src/orchestrator/persistence.py`) stay as-is — the bytes written
  to disk are identical pre- and post-2.5.4.
- Do **not** bundle this with 2.5.3's cross-tick persistence work.
  2.5.3 introduces the persistence subsystem for the four cross-
  tick `state` fields (`positions`, `memory_buffer`, `day_digest`,
  `thesis`); 2.5.4 only relocates existing audit writes against
  already-settled schemas.  Bundling forces 2.5.4 to wait on store-
  choice decisions it doesn't actually depend on.
- Do **not** change the `db_session=None` no-op semantics that
  `EvidenceWriter`, `StrategistDecisionWriter`,
  `build_executor`, and `build_snapshotter` all honour today —
  `tests/integration/backtest/test_end_to_end_smoke.py` relies on
  passing `db_session=None` to short-circuit DB writes (no real
  DB required to validate the topology).  The Phase 4 hook must
  preserve this dry-run capability.
- Do **not** touch the `TraceWriter` / decision-log observability
  paths.  Those are §D-D1 carve-outs in the contract (additive,
  lifecycle-specific by design); they stay where they are.

**Current state.**  Four DB writers live inside the pipeline's
`SequentialAgent` at `src/orchestrator/pipeline.py:109-121`:

| Writer | Class | DB call site | What it writes |
|---|---|---|---|
| `EvidenceWriter` | `src/agents/contract/evidence_writer.py:35` | `:127` (`self.db_session.commit()` at end of `_run_async_impl`) | one `AnalystEvidenceRow` per analyst/ticker pair (loop over five analyst keys) + one `TickerEvidenceRow` per ticker |
| `StrategistDecisionWriter` | `src/agents/strategist/decision_writer.py:22` | `:109` (commit at end of `_run_async_impl`) | one `TickerStanceRow` per stance in `strategist_decision.stances` |
| Executor (trade log) | `src/agents/executor/agent.py` | `:131` (`save_trade_log_entry(...)`), import at `:101` | one `TradeLogEntry` per executed order, inline within `_run_async_impl` per-order loop |
| Snapshotter (portfolio snapshot) | `src/agents/snapshot/agent.py` | `:110-111` (`save_portfolio_snapshot(...)` inline within `_run_async_impl`) | one `PortfolioSnapshot` per tick |

All four currently:
- Are wired into the `SequentialAgent` returned by `build_pipeline`
  at `src/orchestrator/pipeline.py:109-121` (positions 2, 4, 6, 8
  of the eight sub-agents).
- Take `db_session` as a constructor parameter, defaulting to `None`
  for the no-op short-circuit case.
- Read the state they need (`{analyst}_evidence` and
  `ticker_evidence_objects` for EvidenceWriter; `strategist_decision`
  and `portfolio` for StrategistDecisionWriter; `final_orders` and
  `executions` for Executor; `portfolio` and broker state for
  Snapshotter) which is present post-pipeline because upstream agents
  have already yielded the relevant `state_delta`s — meaning Phase 4
  can read the same keys from `updated.state` after the
  SequentialAgent returns.
- Have **no in-tick consumer** of their DB writes — nothing in the
  pipeline reads `AnalystEvidenceRow` etc. back from the DB during
  the same tick.  The writes are purely outbound audit.
- Honour `db_session is None` short-circuit at the top of
  `_run_async_impl` (e.g. `evidence_writer.py:63-66`,
  `decision_writer.py:48-50`), making them no-ops in the smoke test.

Post-pipeline state is already accessible at both lifecycle exit
points:
- **Live:** `src/orchestrator/tick.py` — the tick entrypoint awaits
  the pipeline and then exits.  The Phase 4 hook attaches between
  `await runner.run_async(...)` and process exit.
- **Backtest:** `src/backtest/driver.py:_run_tick` —
  `state.update(dict(updated.state))` at `:382-383` is the current
  post-pipeline write into the driver-private state dict.  The
  Phase 4 hook attaches around the same point, before the
  driver-level assertion at `:393-401`.

Existing observation in the audit (`contract-audit.md` §C-Rule 7):
"none of them reads cross-tick state from the DB — they only write
audit/observability rows".  This is the load-bearing fact that lets
2.5.4 proceed independently of 2.5.3's RAG/persistence design.

**Key decisions for the spec.**

- **Hook shape.**  Three plausible options:
  - **Function on the lifecycle wrapper** — `wrapper.persist_tick(state,
    db_session)`.  Called once at Phase 4.  Cheapest restructure.
    Couples to whatever wrapper concept 2.5.3 introduces.
  - **Dedicated `TickPersister` object** — instantiated once per
    lifecycle, exposes `persist(state, db_session)` per tick.  More
    testable in isolation, more ceremony.
  - **Keep the four agents as `BaseAgent` shells** but instantiate and
    invoke them *outside* the `SequentialAgent`, from the wrapper's
    Phase 4 code.  Minimal change to existing classes; clearest
    separation from the pipeline; preserves the existing
    `_run_async_impl` logic untouched.

  The third option is the smallest viable change but inherits
  ADK's `Event`-yielding ceremony for what is now plain DB work.
  The first is the cleanest if 2.5.3 has already named the wrapper.
- **Ordering of writes.**  Today the four writers run in this order
  inside the pipeline: EvidenceWriter → StrategistDecisionWriter →
  Executor (trade log inline) → MemoryWriter → Snapshotter.
  Snapshotter is intentionally last because its `last_snapshot`
  state_delta feeds the driver's tick-completion assertion at
  `src/backtest/driver.py:393-401`.  The Phase 4 hook must either
  preserve this ordering or document why a new order is safe.
  Executor's trade-log write is *inline within Executor* — splitting
  it out from the Executor's order-execution logic is a separate
  small refactor (the rest of Executor stays in the pipeline as a
  state-only agent).
- **Transactional boundaries.**  Each agent currently calls
  `self.db_session.commit()` at the end of its own
  `_run_async_impl`.  Phase 4 could:
  - **Preserve current semantics:** four sequential commits, partial
    failures leave earlier rows committed.  Matches the
    `evidence_writer.py:123-126` comment ("no try/except wrapping
    the saver loop — a mid-loop failure leaves the session dirty").
  - **Batch into one commit at the end:** all-or-nothing per tick.
    Cleaner failure model; breaks the current "snapshot row exists
    even if trade-log row failed" assumption that any current
    consumer might depend on.

  Recommend explicit decision in the spec; non-default change.
- **Backwards compatibility with `db_session=None`.**  The hook must
  short-circuit when `db_session is None`, matching every existing
  writer's `if self.db_session is None: return` guard.  The smoke
  test must continue to pass without changes.
- **Executor split.**  Executor today does two distinct things:
  produce executions/positions state_deltas (pipeline-internal) AND
  write trade-log rows to the DB (audit).  Cleanest split: a Phase 4
  `persist_trade_log(state, db_session)` reads `state["executions"]`
  and writes the rows, leaving the in-pipeline Executor stateful but
  DB-free.  Alternative: keep the trade-log write inline and
  exclude Executor from the relocation (Executor remains a Rule 7
  deviation but every other writer is lifted).  Recommend the split.
- **`db_session` plumbing.**  The pipeline's `build_pipeline` today
  accepts `db_session` and threads it through the writers.  After
  the relocation, `build_pipeline` no longer needs `db_session` at
  all (assuming the Executor split above) — the wrapper holds the
  session and passes it to the Phase 4 hook.  This is a minor public-
  surface change to `build_pipeline`; document it.

**Effort.**  Small to medium.  Pure mechanical relocation of four
sites + the Phase 4 hook plumbing + the Executor split.  Estimated
one focused session for spec + plan; one for implementation.  Tests
mostly re-use existing assertions (the same DB rows must appear
post-tick); add a contract test that asserts `build_pipeline()`
contains no agent with a non-None `db_session` attribute (i.e. no
mid-pipeline DB writes survive the refactor).

**Sequencing.**  Blocked on 2.5.3 settling the lifecycle-wrapper
shape.  Concrete blocker: the "hook shape" decision above depends on
whether 2.5.3 introduces a formal `LifecycleWrapper` abstraction or
just augments the existing entry points (see 2.5.3's "Lifecycle-
wrapper concept" decision).  Starting 2.5.4 before 2.5.3 settles
that risks rework.  Once 2.5.3's wrapper shape is fixed, 2.5.4 is a
focused refactor with no further open architectural questions.

**Origin.**  2026-05-20 contract audit — recorded as a Rule 7
deviation in `docs/Phase8-contract-audit-fixes/contract-audit.md` §C-Rule 7
("DEVIATION (medium)").  Distinct from the four cross-tick
deviations (those are 2.5.3 territory) because the DB rows here are
audit writes against settled schemas, not cross-tick state.
Pre-deployment status (`memory/project_stockbot_deployment_state.md`)
means no production observation of this deviation yet; it surfaces
only as the contract is enforced.

---

## Deferred until after backtest (low-risk hygiene)

These do not change pipeline behaviour and are not blocked on backtest data.
They are sequenced after the first backtest only because the team's
pre-backtest energy should focus on correctness (Groups 1 + 2), not cleanup.

### Group 3 — Premature-abstraction & double-mechanism collapse

**Cohesion justification:** every item is the same refactor flavour — "delete
the indirection, inline the call, pick one of the two parallel mechanisms".
Same risk profile (touches widely-used abstractions but changes no
behaviour), same review approach, same blast-radius (low).  Bundling them
into one tracked group lets the spec author sequence them by file overlap.

#### 3.1 — Collapse `AuditingStore` into the store's own audit hooks (MED)

**Goal.** One audit-capture mechanism, used by both the per-tick path and the
deep-dump CLI.  Delete the unused indirection.

**Non-goals.**

- Do **not** remove audit capture — only the *parallel* mechanism.

**Current state.** Two mechanisms produce the same per-tick read capture:
the in-process audit hooks (`_audit_record` / `_audit_enable_capture` /
`_audit_drain_reads` on the store) used by every tick, and the
`AuditingStore` decorator wrapper used only by the deep-dump CLI.

**Effort.** Small.

**Origin.** Phase 7 D1 + O7
(`docs/Phase7-pre-backtest-cleanup/code-review.md:263, 435`).

---

#### 3.2 — Pick one trace mechanism (MED)

**Goal.** Two trace mechanisms become one.

**Non-goals.** Do not redesign the trace JSON schema — only the in-process
plumbing.

**Current state.** `src/observability/trace.py` and the per-tick trace writer
elsewhere capture overlapping data.

**Effort.** Small–medium depending on which is kept.

**Origin.** Phase 7 O2 (`docs/Phase7-pre-backtest-cleanup/code-review.md:386`).

---

#### 3.3 — Tighten `Protocol` surfaces (LOW)

**Goal.** Replace `broker: Any` with `broker: Broker` (Protocol), and audit
the other Protocols (`Provider` is touched by 2.1) for missing type
narrowing.

**Non-goals.** Do not introduce new Protocols.

**Current state.** `agents/executor/agent.py` annotates broker as `Any`
despite three concrete implementations (Phase 7 O1).

**Effort.** Tiny.

**Origin.** Phase 7 O1.

---

#### 3.4 — Replace `_store_handle` singleton with injected handle (MED)

**Goal.** Stop the module-global pattern that causes test-fixture fragility.

**Non-goals.** Do not rewrite the cache layer.

**Current state.** `src/backtest/providers/_store_handle.py` —
module-global `_STORE: CachedDataStore | None` with `set_store / get_store /
clear_store`.  Classic cross-test-state-bleed shape.

**Key decisions for the spec.**

- **Injection point.** Per-Runner attribute, ADK `InvocationContext`
  injection, or context-var?

**Effort.** Medium.  Touches every cache provider.

**Origin.** Phase 7 O4.

---

#### 3.5 — Decompose `make_engine` god-node (LOW)

**Goal.** Reduce `make_engine`'s 34-edge connectivity.

**Non-goals.** No behavioural change.

**Current state.** `make_engine` shows up as a top-5 god-node in the
graphify report; concrete edge list is in
`docs/Phase7-pre-backtest-cleanup/code-review.md:419` (O5).

**Effort.** Small once the decomposition is decided.

**Origin.** Phase 7 O5.

---

#### 3.6 — Consolidate `make_*_factory` helpers in `orchestrator/persistence.py` (LOW)

**Goal.** Five distinct factory patterns become one or two.

**Non-goals.** Do not touch the schema (Group 5's territory).

**Current state.** Phase 7 O6 — five `make_*_factory` patterns in
`src/orchestrator/persistence.py`.

**Effort.** Small.  Pure plumbing.

**Origin.** Phase 7 O6.

> **Explicitly excluded:** Phase 7 O3 (per-domain cache provider modules
> with near-identical structure).  The code review itself decided "leave as
> is — the explicit list documents intent and gives unique import-time error
> sites".  No fix needed.

---

### Group 4 — Dead-code purge & test-fixture consolidation

**Cohesion justification:** every item is either `rm` or a small extraction
into `conftest.py`.  No behavioural impact.  All can land in one or two
sweep-PRs after backtest, reviewed together, since they share the same
"nothing breaks because nothing referenced it" justification.

#### 4.1 — Decide `scripts/replay_backtest.py` future (LOW)

**Goal.** Either wire `replay_backtest.py` into the Phase 6 cache, or delete
it.

**Non-goals.** No half-life "leave it for later".

**Current state.** Overlaps `scripts/backtest_run.py`'s job using the
pre-Phase-6 plumbing.  7 inbound references (all docs/tests).

**Origin.** Phase 7 D4 (`docs/Phase7-pre-backtest-cleanup/code-review.md:312`).

---

#### 4.2 — Consolidate `smoke_run.py` and `trace_tick.py` (LOW)

**Goal.** One smoke/trace script, not two with overlapping purpose.

**Current state.** `scripts/smoke_run.py` (11 refs) + `scripts/trace_tick.py`
(4 refs) cover overlapping ground.  Phase 7 review suggests moving the
survivor to `scripts/dev/`.

**Origin.** Phase 7 D5 (`docs/Phase7-pre-backtest-cleanup/code-review.md:326`).

---

#### 4.3 — Remove or relocate `scripts/test_bundle.py` (LOW)

**Goal.** Move to `scripts/dev/` or delete.

**Current state.** 2 inbound references (only docs).  One-off probe.

**Origin.** Phase 7 D6 (`docs/Phase7-pre-backtest-cleanup/code-review.md:336`).

---

#### 4.4 — Delete empty `src/deploy/` directory (LOW)

**Goal.** Remove the empty stub directory.

**Current state.** Empty since project init (May 9 2026).  Pure cruft.

**Origin.** 2026-05-18 audit, net-new.

---

#### 4.5 — Decide future of `scripts/debug_cache_audit.py` and `scripts/debug_edgar_form4.py` (LOW)

**Goal.** Either move to `scripts/dev/` and document, or delete.

**Current state.** Zero inbound references each.  Recent debug detritus
(May 18 — i.e. today's hacking).  The user knows these exist; an explicit
decision avoids drift.

**Origin.** 2026-05-18 audit, net-new.

---

#### 4.6 — Refresh or remove `scripts/init_db.py` (LOW)

**Goal.** Decide if `init_db.py` is still the right entry, or if `create_all`
in `runner.py` has supplanted it.

**Current state.** 4 inbound references, all in docs.  Functionally
supplanted by the runner's per-run `create_all`.

**Origin.** 2026-05-18 audit, net-new.

---

#### 4.7 — Update `_common.py` docstring re: removed `make_dual_emit_callback` (LOW)

**Goal.** Update the Phase-4-era docstring to remove the reference to the
deleted abstraction.

**Current state.** Phase 7 D7 flagged this.  No code-level impact, comment
hygiene only.

**Origin.** Phase 7 D7 (`docs/Phase7-pre-backtest-cleanup/code-review.md:345`).

---

#### 4.8 — Document empty-by-design `src/data/providers/__init__.py` (LOW)

**Goal.** Add a comment block explaining why the file is intentionally
sparse.

**Origin.** Phase 7 D8 (`docs/Phase7-pre-backtest-cleanup/code-review.md:353`).

---

#### 4.9 — Extract shared `pipeline_with_mocked_llms` fixture (LOW)

**Goal.** Lift the ~200 lines of duplicated LLM-mock scaffolding from
`tests/integration/backtest/test_end_to_end_smoke.py` and
`tests/integration/backtest/test_no_silent_zero_features.py` into a shared
`conftest.py` fixture.

**Non-goals.** Do not change what the tests assert.

**Effort.** Small.  One new conftest, two test files thinned by ~200 lines
each.

**Origin.** `B29` in `docs/superpowers/backlog.md:638`.

---

#### 4.10 — Extract duplicated `aapl_data` fixture to conftest (LOW)

**Goal.** Replace four near-identical `aapl_data` fixtures with one shared
conftest fixture.

**Current state.** Defined identically in four extractor-test files:

- `tests/unit/contract/extractors/test_smart_money.py:21`
- `tests/unit/contract/extractors/test_news.py:22`
- `tests/unit/contract/extractors/test_fundamental.py:31`
- `tests/unit/contract/extractors/test_technical.py:17`

**Effort.** Tiny.

**Origin.** 2026-05-18 audit, net-new.

---

#### 3.8 — Centralise LLM model IDs in config (LOW)

**Goal.** One config file (e.g. ``config/models.json``) holds every
``gemini-*`` ID used across the pipeline.  Each agent reads its model name
from config at construction time; no string literal lives in source.

**Non-goals.**

- Do not introduce per-environment model overrides (prod vs backtest) — one
  ID per agent role for now.
- Do not change the per-agent model choices themselves — only the storage
  site.

**Current state.** Model IDs are hardcoded as string literals in several
sites, with no single source of truth:

- ``src/orchestrator/pipeline.py:83`` — ``model_name = "gemini-2.5-pro"``
  (the strategist's *live* model — what the backtest actually uses).
- ``src/agents/strategist/agent.py:266`` — ``_STRATEGIST_MODEL =
  "gemini-2.5-pro"`` (module-level constant on the agent; **not imported by
  the pipeline**, so editing it has no effect on the backtest path — caught
  on 2026-05-20 when a model swap silently no-op'd).
- Analyst-side ``gemini-2.5-flash-lite`` literals in
  ``src/agents/analysts/*/agent.py`` (technical, social, fundamental, news).

Footguns this caused:

- Two parallel "strategist model" declarations drifted out of sync; the one
  in ``agent.py`` looked authoritative but was dead.
- No grep-once way to inventory which Gemini tier each agent uses, so
  upgrading (e.g. 2.5-pro → 3.x) means hunting through five files.

**Resolution sketch.** Add ``config/models.json`` keyed by agent role
(``strategist``, ``technical``, ``social``, ``fundamental``, ``news``,
``risk_gate_if_any``).  Load once at module import via
``config.models.get_model_config()`` (mirror the ``config/strategist.py``
pattern).  Update ``config/README.md`` per CLAUDE.md convention.  Delete
the now-dead ``_STRATEGIST_MODEL`` constant.  Add a unit test that grep-asserts
no ``gemini-`` literal survives in ``src/``.

**Effort.** Tiny — pure refactor, no behaviour change.

**Origin.** 2026-05-20 backtest debugging — model swap on the dead
``agent.py`` constant silently ran the old model for several runs before the
hardcoded copy in ``pipeline.py:83`` was discovered.

---

## Empirically gated

### Group 5 — Cannot proceed without empirical evidence

**Cohesion justification:** both items have a hard "wait" condition that
*nothing else in this file shares*.  Specifying them prematurely produces
speculative redesign.  They share an "advisory pause" status that warrants
calling out separately.

#### 5.1 — Persistence schema refresh (MED — when triggered)

**Goal.** After one or two backtest runs have surfaced friction in
result-summarisation and cross-run analytics, propose a focused persistence
refresh that addresses what backtest readers actually need — not speculative
cleanup.

**Non-goals.**

- Do **not** start before at least one full backtest has run.
- Do **not** adopt Alembic prematurely — pre-deployment means fresh DB per
  run.
- Do **not** redesign just because the file is 420 lines.

**Current state.** `src/orchestrator/persistence.py` (~420 lines): no FK
relationships between `evidence` / `ticker_stance` / `decision` /
`portfolio_snapshot` (all stamp `tick_id` as a free-string column),
inconsistent timestamp column names (`timestamp` vs `recorded_at` vs
`opened_at`), no Alembic, single module per all tables.

**Trigger.** First end-to-end backtest run on a real window completes and a
human has tried to write a result-summary query.

**Origin.** `B28` in `docs/superpowers/backlog.md:620` (entry numbered B24
in the body; the index renumbers).

---

#### 5.2 — Decide future of `src/lifecycle/scheduler.py` (LOW — when triggered)

**Goal.** Decide whether the Cloud Scheduler shim stays, evolves, or is
deleted.

**Non-goals.** Do **not** refactor it now.  Pre-deployment, with no paper
mode running, any rewrite is speculative.

**Current state.** Phase 7 D3 — dormant module shim for Google Cloud
Scheduler; on a dormant code path until a paper-or-live instance starts
running.

**Trigger.** When `docs/Phase*-deploy/` (or equivalent) opens, decide
fate alongside the other deployment plumbing.

**Origin.** Phase 7 D3 (`docs/Phase7-pre-backtest-cleanup/code-review.md:300`).

---

## Backlog items to remove

These five backlog entries are fully captured above and can be removed from
`docs/superpowers/backlog.md` once the move is accepted:

- **B26** → 2.1
- **B27** → 2.2
- **B28** → 5.1
- **B29** → 4.9
- **B30** → 1.1

The dependency arrow `B30 → fill ⇆ replay parity` at backlog.md:695 should
either be deleted with B30 or rewritten to point at this file.

---

## Appendix — Phase 7.5 review carry-overs

Items flagged by the opus final review of the `config-as-truth` branch
(2026-05-18) that were intentionally deferred rather than landed in that
PR.  Recorded here so they don't get lost.

### A1. Sweep remaining `_HTTP_TIMEOUT` literals in non-Quiver providers

**Files:** `src/data/providers/short_interest/finra.py`,
`src/data/providers/price_history/tiingo.py`,
`src/data/providers/company_ratios/fmp.py` (each carries its own
hardcoded `_HTTP_TIMEOUT = 15.0`).

**Why deferred:** Phase 7.5 spec D5 explicitly scoped the rename and
config plumbing to Quiver only.  Generalising forces a design call —
single global `http_timeout_seconds`, or per-provider
`<provider>_http_timeout_seconds`?  That is a brainstorm, not a cleanup.

**Trigger:** the next time any of those three providers is exercised
in earnest, or when paper-deployment surfaces a need to tune them.

**Effort:** Small once the design choice is made (~1 hour of edits +
contract tests).  Half a day if the choice itself needs a spec.

### A2. Resolve the commented-out `_politician_trades` provider in `scripts/backtest_fetch.py`

**File:** `scripts/backtest_fetch.py:226` (function defined),
`scripts/backtest_fetch.py:262` (commented-out registration in the
provider map).

**Why deferred:** Per the user's standing position
(`memory/project_politician_trades_disabled.md`), there is no free
historical source for congressional trades, so the analyst is allowed
to degrade gracefully and the fetcher is intentionally inert.  Phase
7.5 has no remit to find a paid source or accept analyst-permanent
degradation.

**Trigger:** either (a) a viable historical politician-trades source
appears (Quiver paid tier, FMP backfill, etc.), or (b) a deliberate
decision to retire the politician-trades analyst entirely.

**Effort:** Sub-1-hour cleanup once the trigger is decided.

---

## Decisions that need the user

Two judgement calls are mine in this draft; flag if either should flip:

1. **Group 1 includes the schedule-keys and http_timeout items** (1.2, 1.3,
   1.4) even though they would not, individually, distort a backtest.  I
   grouped them pre-backtest because they share the "config-as-truth" idiom
   with 1.1, the test enforcement mechanism is the same, and the files
   overlap.  If you prefer a stricter "only if it changes backtest results"
   line, 1.2–1.4 move into Group 4.

2. **Group 3 (over-abstraction collapse) is sized for one bundled effort** —
   seven items.  Could split into 3a (audit/observability — items 3.1, 3.2)
   and 3b (general abstractions — 3.3–3.6) if the team wants smaller PRs.
