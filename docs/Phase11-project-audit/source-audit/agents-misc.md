# Source audit — agents miscellaneous (snapshot / contract / memory / loose)

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 9
**Findings:** 2 P0 · 4 P1 · 3 P2 · 1 P3

## Summary

This file aggregates four small areas under `src/agents/`: the **snapshot**
subsystem (one file, `snapshot/agent.py`), the **contract** writer
subsystem (one file, `contract/evidence_writer.py`), the **memory**
subsystem (five files under `memory/`), and the loose helper modules
directly under `src/agents/` (`llm_retry.py`, `isolated_failure.py`,
`__init__.py`).  Two themes dominate.  **First**, contract-bearing
state lifetimes are wobbling: the Snapshotter and MemoryWriter both
write keys that affect downstream behaviour without a §A row to anchor
them (`starting_capital`, `spy_start_price`) or rely on cross-tick
carry-over for fields the contract says must come from persistence
(`memory_buffer`, `day_digest` — known, Spec C deferred).  **Second**,
the silent-failure attractor pattern §A.7 warns against shows up on
the Snapshotter's SPY fetch and in low-key fallbacks scattered through
MemoryWriter's decoding logic.  The Snapshot/Contract findings are
load-bearing P0; the memory findings are mostly P1/P2 dead code and
fallback hygiene; the loose `llm_retry.py`/`isolated_failure.py`
modules came out clean (their compositional invariants are tested and
exercised everywhere — they are the model the rest of the codebase
should aspire to).  Cross-subsystem dependencies: `last_snapshot` is
read by `src/backtest/driver.py:616`; `starting_capital` /
`spy_start_price` bugs in `Snapshotter` interact with
`src/lifecycle/initialise.py`'s anchor-row design; the deferred
`memory_buffer` / `day_digest` finding intersects with Spec C and
`src/orchestrator/tick.py:151-152` / `src/backtest/runner.py:562-563`
seeds.

## Findings

### P0-01 · C5 silent-failure attractor · Snapshotter swallows every SPY-fetch error to `spy_price = 0.0`

- **Location:** `src/agents/snapshot/agent.py:60-74`
- **Confidence:** high
- **Description:**
  The SPY price-history fetch is wrapped in
  `try: ... except Exception: spy_price = 0.0` with the inline comment
  "defensive; never crash the tick".  This is the canonical §A.7
  anti-pattern — a contract-bearing field (`spy_price`, which feeds
  `spy_return_pct`, `excess_return_pct`, `spy_value_if_held`, and the
  whole equity-curve / benchmark comparison) is silently zeroed on any
  failure shape: network error, provider import error,
  `STOCKBOT_STRICT_AS_OF` veto, `KeyError` on a malformed bar.  The
  downstream `bot_return_pct - spy_return_pct = bot_return_pct - 0` is
  numerically valid, the snapshot row is written, the driver's
  `last_snapshot` completion check passes, and the failure surfaces
  only as a flat SPY line in the equity-curve plot hours later.  No
  log message, no `branch_failed` warning, no test would catch it.
  Memory schema test policy §A.7 explicitly names this shape.
- **Suggested action:**
  Either propagate the exception (the driver already catches mid-tick
  failures via the pipeline-completion guard at `driver.py:608`), or
  set `spy_price = None` and have `save_portfolio_snapshot` reject the
  row (loudly) when it is missing.  At minimum, narrow the catch to
  `(ProviderError, asyncio.TimeoutError)` and `_LOGGER.warning` with
  `kind="spy_fetch_failed"` so terminal-log aggregation surfaces it.

### P0-02 · C4 contract violation · Snapshotter writes anchors (`starting_capital`, `spy_start_price`) bypassing `state_delta` and not in §A

- **Location:** `src/agents/snapshot/agent.py:77-83`
- **Confidence:** high
- **Description:**
  The Snapshotter writes two keys —
  `state["starting_capital"]`, `state["spy_start_price"]` — through
  direct dict mutation with no accompanying `state_delta` event,
  violating §C-Rule 1.  Both keys are also absent from the §A field
  schema, violating §A's "row required for every contract-bearing
  field"; the keys are clearly contract-bearing because they
  determine `bot_return_pct` and `spy_return_pct` for every subsequent
  tick.  In **backtest** the bug is partially masked by
  `src/backtest/driver.py:602-606`'s `state.update(updated_state)`
  carry-forward — direct mutations on `ctx.session.state` end up in
  the updated_state dict and survive the next tick.  In **live** every
  tick is a fresh Cloud Run Job process: `state["starting_capital"]
  not in state` is always true, so the anchor is re-set to the current
  tick's `bot_total` and `bot_return_pct` is permanently ~0%.  This
  is the same class of bug §A and §C-Rule 1 are designed to prevent,
  and it cannot be detected by a backtest because the carry-forward
  hides it.
- **Suggested action:**
  Decide which surface owns the anchor (recommended: the DB anchor row
  written by `src/lifecycle/initialise.py:_write_anchor`, since it
  already exists).  Have the Snapshotter read the anchor row from the
  DB on tick 1 (Phase 2 hydration via the lifecycle wrapper) and
  cache it on a §A-documented cross-tick key — or compute returns
  directly from the DB anchor and stop writing the anchors to state
  at all.  Either way, add a §A row or delete the state writes.

### P1-01 · C4 contract violation · Snapshotter reads broker + price provider directly mid-tick

- **Location:** `src/agents/snapshot/agent.py:38, 60-72`
- **Confidence:** medium
- **Description:**
  The Snapshotter is wired as the final sub-agent of the pipeline (see
  `src/orchestrator/pipeline.py:167`) and writes `state["last_snapshot"]`,
  which is a contract-bearing §A row consumed by
  `src/backtest/driver.py:616`.  Per §C-Rule 7 the pipeline must read
  from `state`, not from the broker, the persistence layer, or any
  provider mid-tick.  This agent calls `await self.broker.get_portfolio()`
  and `from data import get_price_history; await get_price_history(...)`
  directly — both are mid-tick external reads.  A near-defence is that
  Snapshotter is conceptually observability (§C-Rule 8), but it writes
  a §A-documented contract-bearing key, so it falls under Rule 7's
  jurisdiction, not Rule 8's.  Confidence is medium because the
  consolidation pass may decide to reclassify `last_snapshot` as
  observability-only and move the driver's completion check off it.
- **Suggested action:**
  Read `state["portfolio"]` (already loaded in Phase 2) instead of
  re-calling the broker, and add SPY to the Phase 2
  `reference_prices` hydration so the Snapshotter reads
  `state["reference_prices"]["SPY"]` instead of calling
  `get_price_history` mid-tick.  Alternatively reclassify the
  Snapshotter as Rule-8 observability and remove `last_snapshot` from
  §A by re-engineering the driver's completion guard.

### P1-02 · C4 contract violation · MemoryWriter relies on cross-tick state-dict carry-over for `memory_buffer` / `day_digest`

- **Location:** `src/agents/memory/writer.py:112, 117, 170-191`; seeds at `src/orchestrator/tick.py:151-152`, `src/backtest/runner.py:562-563`
- **Confidence:** medium
- **Description:**
  `memory_buffer` and `day_digest` are §A cross-tick rows whose
  persistence is documented as "Spec C, deferred".  Today MemoryWriter
  reads them from `state.get("memory_buffer", [])` /
  `state.get("day_digest", "")` (lines 112 / 117) and writes them
  back with both a direct mutation (lines 170-171) and a state_delta
  event (lines 185-192).  In backtest the cross-tick carry-over works
  because `src/backtest/driver.py:589-606`'s `state.update(updated_state)`
  preserves the keys.  In live each tick is a fresh process and the
  `tick.py:151-152` seed re-initialises them to `[]` and `""`, so
  every live tick starts with an empty memory buffer — the strategist
  prompt's `{memory_buffer}` and `{day_digest}` slots are always
  empty.  This is the §B-Phase 2 violation the contract explicitly
  names: "treating a cross-tick field as tick-scoped — seeding it
  with an empty value at Phase 2 instead of reading from persistence."
  Confidence is medium because the contract itself flags Spec C as
  deferred; nonetheless this is the silent-failure shape Spec C is
  expected to close.
- **Suggested action:**
  Track under Spec C; the audit's role is to record that the field
  is currently null on every live tick.  Until Spec C lands, add an
  assertion at Phase 4 that `memory_buffer` is non-empty after the
  first tick, so the gap fails loudly rather than silently producing
  a zero-memory strategist prompt.

### P1-03 · C5 silent-failure attractor · MemoryWriter falls back to "unknown" decision_tag

- **Location:** `src/agents/memory/writer.py:131-134`
- **Confidence:** medium
- **Description:**
  `decision.get("decision_tag", "unknown")` papers over the case where
  the strategist's output is a dict that is missing the field.  The
  strategist schema (`src/agents/strategist/schema.py:103`) declares
  `decision_tag: str` as required, so any time this fallback fires it
  indicates a contract breach upstream — but the BufferEntry is built
  anyway, dedup runs on a garbage tag, the row is persisted, and
  nothing surfaces the breach.  The same shape would surface as a
  `pydantic.ValidationError` in a properly validated path.
- **Suggested action:**
  Drop the `"unknown"` default; let `decision["decision_tag"]` raise
  `KeyError`.  The outer pipeline will catch it and abort the tick,
  surfacing the upstream breach instead of writing a corrupt
  BufferEntry row.

### P1-04 · C7 doc/code drift · `memory/writer.py` references `InMemorySessionService` for cross-tick propagation

- **Location:** `src/agents/memory/writer.py:173-184` (and Snapshotter mirror at `src/agents/snapshot/agent.py:128-142`)
- **Confidence:** high
- **Description:**
  The inline comment explains the dual-write pattern as a workaround
  for `InMemorySessionService` not merging direct mutations.  Spec B
  has moved the backtest to `DatabaseSessionService`; the carry-forward
  semantics in `src/backtest/driver.py:589-606` now decide what
  survives explicitly via `state.update(updated_state)`.  The comment
  is no longer the live story — the dual-write is now belt-and-braces
  against a different mechanism (the Phase 2 re-seed in
  `tick.py:151-152` and the `state.update` slice), not against
  `InMemorySessionService`.  The Snapshotter has the same drifted
  comment block at `agent.py:128-142`.
- **Suggested action:**
  Rewrite both comment blocks to reference `DatabaseSessionService`
  and the `driver.run` carry-forward logic.  Cite §C-Rule 1's
  "auto-yielded delta-tracked callback writes" sub-section since both
  agents now ride on that path.

### P2-01 · C1 dead code · `set_compress_llm` and `_compress_llm` module global

- **Location:** `src/agents/memory/compress.py:9, 12-15, 28`
- **Confidence:** high
- **Description:**
  `set_compress_llm` is a test-injection hook for swapping in a stub
  LLM.  `grep -rn "set_compress_llm" src/ tests/ scripts/` returns
  zero callers outside the definition.  `tests/unit/test_memory_compress.py`
  injects via the explicit `llm_fn=` parameter on `compress()`
  (lines 27 / 37 / 46) and never touches the global setter.  The
  `_compress_llm or _default_llm_compress` fall-through at line 28 is
  therefore always None-then-fall-through.  The setter, the module
  global, and the `_compress_llm or` clause are all dead.
- **Suggested action:**
  Delete `_compress_llm`, `set_compress_llm`, and the `or _compress_llm`
  segment of the resolution chain at line 28.  The `llm_fn=` parameter
  on `compress()` is the only injection path tests use.

### P2-02 · C1 dead code · `set_embedding_provider` and `_embedding_provider` module global

- **Location:** `src/agents/memory/embeddings.py:6, 10-13, 25-27`
- **Confidence:** high
- **Description:**
  Same shape as the `compress.py` finding above:
  `set_embedding_provider` has zero callers in `src/`, `tests/`, or
  `scripts/`.  Tests that need to bypass the real embedder do so by
  monkeypatching at the call site (e.g.
  `tests/integration/test_memory_writer_integration.py:40` —
  `patch("agents.memory.writer.embed", new=AsyncMock(...))`).  The
  module-global indirection is unused.
- **Suggested action:**
  Delete `_embedding_provider`, `set_embedding_provider`, and the
  `if _embedding_provider is not None` branch in `embed()`.  Inline
  the body of `_default_embed` into `embed()` and let tests continue
  to monkeypatch at the call site.

### P2-03 · C1 dead code · `MemoryProjection` is constructed only by its own unit tests

- **Location:** `src/agents/memory/schema.py:22-38`
- **Confidence:** medium
- **Description:**
  `MemoryProjection.from_buffer` is referenced only by
  `tests/unit/test_memory_schema.py:37, 44`.  No `src/` module
  imports it.  The class docstring says it is a "compressed view of
  the buffer for injection into the strategist prompt" but the
  strategist prompt template
  (`src/agents/strategist/prompts.py:91-92`) injects raw
  `memory_buffer` and `day_digest` strings, not a `MemoryProjection`.
  Confidence is medium because the class may be earmarked for the
  same Spec C work the Phase-2 hydration finding (P1-02) flags.
- **Suggested action:**
  Either wire `MemoryProjection.from_buffer` into the strategist
  prompt-construction path (likely the right move when Spec C lands)
  or delete it.  Leaving it in place is misleading documentation of
  a feature that does not exist.

### P3-01 · C7 doc/code drift · Snapshotter docstring describes "snapshot includes the bot's total value, cash, and position count, alongside the current SPY price" but the saved row carries more

- **Location:** `src/agents/snapshot/agent.py:14-24`
- **Confidence:** low
- **Description:**
  The class docstring undersells what `snap` actually contains —
  `holdings_breakdown`, `bot_positions_value`, `spy_value_if_held`,
  three return-pct fields, `tick_id`.  The drift is cosmetic but it
  is the first thing a new reader of the file sees.
- **Suggested action:**
  Rewrite the docstring to enumerate every field the snapshot row
  carries, or shorten it to "Records a `PortfolioSnapshot` row" and
  link to the schema in `orchestrator.persistence`.
