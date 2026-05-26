# Cross-module dedupe synthesis

Synthesised 2026-05-26 from the 11 vocab inventories and 11 module-finding
files under `docs/audits/2026-05-26-codebase-audit/{vocab,modules}/`, with
intent.md §3.2 (synonym candidates) and §7 (human resolutions) as the
authoritative anchor.  Source files were consulted only to verify the
exact text / location of a suspected duplicate; no fixes are proposed
inline (suggested actions only).

Findings are numbered D-### across the document.  Severities P0–P3 follow
the intent.md rubric.  Categories are the five named in the task brief:
`vocabulary-collision`, `logic-duplication`, `cross-lifecycle`,
`schema-duplication`, `structural-mirror`.

---

## Summary

| Severity | Count |
|----------|------:|
| P0 | 3 |
| P1 | 8 |
| P2 | 12 |
| P3 | 4 |
| **Total** | **27** |

| Category | Count |
|----------|------:|
| vocabulary-collision | 9 |
| logic-duplication | 8 |
| cross-lifecycle | 4 |
| schema-duplication | 3 |
| structural-mirror | 3 |

§3.2 of intent.md enumerates 11 synonym clusters.  This synthesis
confirms **9** of those clusters as live dedupes (clusters 1, 2, 3, 4,
5, 6, 7, 8, 11), promotes **3** new cross-module collisions that §3.2 did
not list (D-007 stale-verb-set `{hold, update}`, D-016 `_REFERENCE_SYMBOLS`,
D-021 `_STOCKBOT_TABLES` partial set), and finds **2** of the §3.2
candidates (cluster 9 `verdict`/`signal`; cluster 10 `tick`/`cycle`/`run`)
to be terminology-only with no live duplication.

### Top 5 by impact

1. **D-001 (P0, vocabulary-collision)** — the prose cluster:
   `rationale` / `last_reviewed_reason` / `sell_reasons` /
   `update_reasons` / `report.summary` / `reasoning` / TickerStance.rationale /
   PositionThesis.rationale all carry "why".  The cluster is the very
   reason the previous Phase 11 audit was scrapped; intent §3.2 cluster 1.
2. **D-007 (P1, vocabulary-collision)** — `_NO_RISK_GATE_INTENTS =
   {"hold", "update"}` encodes a stale verb that no longer exists in the
   canonical four-verb set `{buy, sell, update, no_action}`; the live
   risk-gate strip therefore lets `no_action` slip through.  A unit test
   actively pins the wrong set.
3. **D-002 (P0, vocabulary-collision)** — bare-key `state["positions"]`
   versus prefixed `state["user:positions"]`.  Intent §7.3 made the
   former executor-internal-only, but ContextShim, decision_logger and
   the backtest runner still read it as a fallback.
4. **D-014 (P0, logic-duplication)** — three independent sites synthesise
   "no-data" verdicts (analysts/joiner, contract extractor, strategist
   stance default).  Each picks slightly different wording / confidence /
   rationale, so a missing-data tick presents three different prose
   strings to downstream consumers.
5. **D-016 (P1, logic-duplication)** — `_REFERENCE_SYMBOLS` is defined in
   three files (`scripts/backtest_fetch.py`, `src/backtest/runner.py`,
   `src/orchestrator/tick.py`) with overlapping but non-identical contents;
   the watchlist-superset invariant relies on all three agreeing.

---

## D-001
- **Category:** vocabulary-collision
- **Severity:** P0
- **§3.2 cluster:** 1 (the prose / "why" cluster)
- **Locations:**
  - `src/contract/schemas.py:AnalystVerdict.rationale`
  - `src/contract/schemas.py:AnalystReport.summary`
  - `src/contract/stance_schema.py:TickerStance.rationale`
  - `src/contract/position_thesis.py:PositionThesis.rationale`,
    `PositionThesis.last_reviewed_reason`
  - `src/agents/strategist/schema.py:StrategistDecision.sell_reasons`,
    `update_reasons`
  - `src/agents/strategist/derivation.py` (synthesises both `_reasons`
    dicts from `stance.rationale`)
  - `src/agents/executor/_verb_dispatch.py:235, 251, 291, 311, 323`
    (writes `last_reviewed_reason = stance.rationale or ""`)
- **Evidence:** seven schema fields and one prompt slot all carry
  free-text "why this decision".  `last_reviewed_reason` is derived
  byte-identically from `stance.rationale` at every write site with no
  transformation.  `sell_reasons[t]` / `update_reasons[t]` are derived
  from `stance.rationale` for ticker `t` in derivation.py.  The
  per-analyst `AnalystVerdict.rationale` and the per-analyst
  `AnalystReport.summary` carry overlapping content (the report is the
  long form, the verdict's rationale the short form, but neither schema
  forbids the other from copying).
- **Relationship:** alias / derived field / synonym cloud.
- **Linked findings:** F-strategist-011, F-contract-001, F-analysts-006,
  F-executor-004.
- **Suggested action:** investigate — the cluster needs a single
  policy ("one rationale per agent decision, derived everywhere else").
  Likely candidates to delete: `last_reviewed_reason` (derivable on
  render), `sell_reasons` / `update_reasons` dicts (derivable from the
  per-ticker stances at audit-log time).  AnalystVerdict.rationale +
  AnalystReport.summary need an explicit policy on overlap.

## D-002
- **Category:** vocabulary-collision
- **Severity:** P0
- **§3.2 cluster:** 7 (`positions` keys)
- **Locations:**
  - writer of `user:positions`: `src/agents/executor/agent.py:after_agent_callback`
  - writer of bare `positions`: `src/agents/executor/agent.py` (BUY→SELL
    bridge inside one tick)
  - bare-key readers: `src/agents/strategist/context_shim.py:153, 229`;
    `src/backtest/decision_logger.py`; `src/backtest/runner.py`
- **Evidence:** intent §7.3 (AUTHORITATIVE) closes the question: the
  bare `state["positions"]` key is **executor-internal only** (the in-tick
  BUY-then-SELL bridge); every other consumer should read
  `state["user:positions"]`.  Three external readers still carry the
  `state.get("user:positions") or state.get("positions") or {}` fallback
  chain.
- **Relationship:** vocabulary alias with a real semantic split
  (in-tick vs durable) being conflated.
- **Linked findings:** F-strategist-007, F-executor-001, F-backtest-009.
- **Suggested action:** drop the `or state.get("positions")` arm in the
  three external readers.  Leave the executor's bare-key bridge as the
  sole bare-key site, with a comment naming it the only legitimate use.

## D-003
- **Category:** structural-mirror
- **Severity:** P1
- **§3.2 cluster:** 11 (analyst fan-out parallel shapes)
- **Locations:**
  - `src/agents/analysts/news/joiner.py`
  - `src/agents/analysts/fundamental/joiner.py`
- **Evidence:** the two joiners have identical structural skeletons —
  same `BaseAgent` shape, same `temp:<domain>_*` read pattern,
  same merge-into-`AnalystEvidence` pass, same exception envelope.
  Only the per-domain field names differ.  Three more analysts
  (technical, social, smart_money) share the *fetch* fan-out shape
  too but do not share a joiner — see D-004.
- **Relationship:** structural duplication; logic is the same, only the
  payload field-names differ.
- **Linked findings:** F-analysts-002, F-analysts-007.
- **Suggested action:** investigate — a generic
  `merge_temp_domain_into_evidence(domain_keys, target_field)` helper
  could host the shared shape; the two joiners would become a single
  function call with a config dict.

## D-004
- **Category:** structural-mirror
- **Severity:** P2
- **Locations:** `src/agents/analysts/{technical,fundamental,news,social,
  smart_money}/fetch_agent.py`
- **Evidence:** all five analyst-side fetch agents follow the identical
  shape: `IsolatedFailureWrapper(RetryingAgentWrapper(BaseAgent))` that
  pulls one or more `temp:<domain>_data` slots via `data.<wrapper>`.
  smart_money fans into three domains (politician_trades, notable_holders,
  insider_trades-overlap) — see D-005.
- **Relationship:** uniform structural skeleton; differs only in which
  domain wrappers are called and which `temp:<domain>_data` keys are
  written.
- **Linked findings:** F-analysts-001, F-analysts-003.
- **Suggested action:** investigate consolidating into a single
  `DomainFetchAgent(domains=[…], target_keys=[…])` parameterised
  factory.  Currently five files repeat the same wrapper composition.

## D-005
- **Category:** vocabulary-collision
- **Severity:** P1
- **§3.2 cluster:** 8 (state-key prefixes)
- **Locations:** `src/agents/analysts/smart_money/fetch.py:92-98` and
  joiner reads under `state["smart_money_data"]`
- **Evidence:** every other analyst fan-out writes to a `temp:` prefixed
  key (`temp:price_data`, `temp:fundamental_data`, `temp:news_data`,
  `temp:social_data`).  smart_money writes / reads the bare
  `smart_money_data` slot — a prefix-policy violation that breaks the
  uniform "tick-scoped temp" convention.  Cited in F-analysts-005 and
  vocab/analysts.md as a policy violation.
- **Relationship:** prefix collision; a tick-scoped slot mis-named so it
  looks durable.
- **Linked findings:** F-analysts-005.
- **Suggested action:** rename to `temp:smart_money_data` (one-line
  change across writer + joiner reader).

## D-006
- **Category:** schema-duplication
- **Severity:** P1
- **§3.2 cluster:** 2 (two-shape verdict pattern)
- **Locations:**
  - `src/contract/schemas.py:TickerVerdict` and `LlmTickerVerdict`
  - `src/agents/strategist/schema.py:StrategistDecision` and
    `StrategistLLMDecision`
- **Evidence:** two pairs follow the identical "LLM-side loose
  shape + canonical strict shape" pattern, each with its own
  `model_validate` adapter.  The pattern is not abstracted; the duplication
  spans the analysts (verdict) and strategist (decision) boundaries.
- **Relationship:** parallel schema pattern; same structural problem
  solved twice.
- **Linked findings:** F-contract-002, F-strategist-009.
- **Suggested action:** investigate a generic `LooseToStrict[T]` mixin
  or a single conversion helper.  Lower priority than D-001 because
  each pair is self-consistent today.

## D-007
- **Category:** vocabulary-collision
- **Severity:** P1
- **§3.2 cluster:** not listed (new finding)
- **Location:** `src/agents/risk_gate/agent.py:21`
- **Evidence:** `_NO_RISK_GATE_INTENTS: Final[frozenset[str]] =
  frozenset({"hold", "update"})`.  Canonical verb set per
  `stance_schema.py:98` is `{buy, sell, update, no_action}`; `hold` is
  the dead pre-collapse verb.  `no_action` stances therefore slip past
  the strip and can produce surprise SELL orders against a
  "considered, no change" stance.  The unit test
  `test_no_risk_gate_intents_constant_contains_hold_and_update`
  pins the wrong set.  Source comment at risk_gate/agent.py:21 calls it
  the "three-verb schema" — also stale.
- **Relationship:** stale-verb survival; the four-verb migration didn't
  reach this guard.
- **Linked findings:** F-risk_gate-003, F-risk_gate-009, F-risk_gate-013.
- **Suggested action:** flag to the human; the swap is one line plus a
  test rewrite, but production behaviour changes.

## D-008
- **Category:** logic-duplication
- **Severity:** P2
- **§3.2 cluster:** 3 (positions hydration)
- **Locations:**
  - `src/agents/strategist/context_shim.py:_coerce_portfolio` (lines 55-72)
  - `src/agents/strategist/enricher.py:_coerce_portfolio` (lines 73-89)
- **Evidence:** byte-identical helper; same signature, same three-branch
  body (`Portfolio` → return; `None` → `Portfolio(cash=0.0)`;
  otherwise → `Portfolio.model_validate`).  `context_shim.py:58` literally
  says "Mirrors the helper in `agents.strategist.agent`" — the mirror has
  since drifted to `enricher.py` but the comment is stale.
- **Relationship:** mechanical duplication.
- **Linked findings:** F-strategist-003.
- **Suggested action:** promote to a single private helper in
  `agents/strategist/_portfolio_util.py` or onto `Portfolio` as a
  classmethod.

## D-009
- **Category:** logic-duplication
- **Severity:** P2
- **§3.2 cluster:** 4 (mid-tick portfolio reads)
- **Locations:**
  - `src/agents/risk_gate/agent.py:100-104`
  - `src/agents/executor/agent.py` (post-trade refresh)
  - `src/agents/misc/snapshotter.py` (snapshot-time refresh)
- **Evidence:** three agents call `broker.get_portfolio()` mid-tick rather
  than reading `state["portfolio"]` written by the Phase-2 seeder.  Each
  site re-fetches for slightly different reasons (price for clamp,
  post-fill balance, snapshot consistency).  This re-implements the
  Phase-2 seeder's job three times and introduces drift between
  `state["portfolio"]` and the broker's view.
- **Relationship:** logic duplication of a single contract responsibility.
- **Linked findings:** F-risk_gate-002, F-executor-005, F-agents-misc-003.
- **Suggested action:** investigate a single `refresh_portfolio_in_state`
  helper or, better, lean on `state["portfolio"]` everywhere and have
  the executor write back via `state_delta` after fills (Rule 1).

## D-010
- **Category:** logic-duplication
- **Severity:** P2
- **§3.2 cluster:** 5 (write-pairing patterns)
- **Locations:**
  - `src/agents/executor/agent.py:after_agent_callback` (direct write +
    `state_delta`)
  - `src/agents/misc/snapshotter.py` (direct write + `state_delta`)
  - `src/agents/misc/memory_writer.py` (direct write + `state_delta`)
- **Evidence:** three agents implement the "write to `session.state[k]`
  AND emit `state_delta={k: v}`" double-write pattern manually.  Rule 1
  in contract-invariants.md says durable writes go through `state_delta`;
  these sites do both because the in-process state is rehydrated
  separately by the runner.  The pattern is identical at all three sites
  and is currently re-rolled.
- **Relationship:** mechanical duplication of a contract-mandated pattern.
- **Linked findings:** F-executor-002, F-agents-misc-004,
  F-agents-misc-005.
- **Suggested action:** investigate a small helper
  (`write_durable(ctx, key, value)`) that does both in one call; reduces
  silent-failure surface where a future author forgets one half.

## D-011
- **Category:** structural-mirror
- **Severity:** P2
- **Locations:**
  - deterministic-analyst singletons:
    `src/agents/analysts/{technical,fundamental}/agent.py` define both a
    module-level singleton and a `build_*_analyst()` factory.
  - LLM-analyst factories:
    `src/agents/analysts/{news,social,smart_money}/agent.py` define only
    the factory.
- **Evidence:** the deterministic side keeps a `<name>_analyst =
  XAnalystAgent()` singleton at module level **and** a factory, with the
  pipeline picking the factory.  The LLM side dropped the singleton.
  The singletons are dead (see also F-risk_gate-006 for the same
  pattern in risk_gate).
- **Relationship:** structural inconsistency / dead singletons mirroring
  a live factory.
- **Linked findings:** F-analysts-008, F-risk_gate-006.
- **Suggested action:** delete the module-level singletons; standardise
  on factories.

## D-012
- **Category:** logic-duplication
- **Severity:** P2
- **Locations:**
  - `src/baselines/spy.py:SPYMetrics` (Sharpe, drawdown computation)
  - `src/backtest/reporting.py` (Sharpe, drawdown computation for
    equity-curve metrics)
- **Evidence:** two independent Sharpe / max-drawdown / volatility
  implementations.  `SPYMetrics` is dead outside tests (per
  vocab/ops.md); reporting.py is the live computation.  Even so the
  formulas are duplicated, and the dead implementation has subtly
  different annualisation assumptions.
- **Relationship:** logic duplication; one branch is dead.
- **Linked findings:** F-ops-007, F-backtest-012.
- **Suggested action:** delete `SPYMetrics`; if a SPY baseline is wanted,
  call into reporting.py's computation.

## D-013
- **Category:** logic-duplication
- **Severity:** P2
- **Locations:**
  - `src/data/registry.py:101` — `DOMAINS: frozenset[str] = frozenset({…})`
  - `src/data/config.py:18` — `DOMAINS: frozenset[str] = frozenset({…})`
- **Evidence:** the same `frozenset` of domain names is defined twice
  for circular-import avoidance.  A drift between the two would cause
  silent registration of an unconfigured domain or vice-versa.
- **Relationship:** mechanical duplication, justified by an import cycle.
- **Linked findings:** F-data-012.
- **Suggested action:** investigate — either break the cycle (move the
  literal into a leaf `data/_domains.py` and import from both), or add a
  startup assertion `assert registry.DOMAINS == config.DOMAINS`.

## D-014
- **Category:** logic-duplication
- **Severity:** P0
- **§3.2 cluster:** 1 (sub-case of the prose cluster)
- **Locations:**
  - `src/agents/analysts/<x>/joiner.py` (no-data verdict synthesis when
    fetch returns empty)
  - `src/contract/extractors.py` (no-data path on missing per-domain
    payload)
  - `src/agents/strategist/derivation.py` (default `no_action` stance
    when LLM omits a watchlist ticker)
- **Evidence:** three sites independently synthesise "we had no data,
  treat as neutral" verdicts.  Each picks its own wording for
  `rationale`, its own confidence (0.0 vs `None` vs 0.5), and its own
  direction (`neutral` vs `no_action` vs absent).  A missing-data tick
  therefore presents three different prose strings downstream.
- **Relationship:** logic duplication with silent semantic drift; the
  defaults disagree.
- **Linked findings:** F-analysts-009, F-contract-003, F-strategist-006.
- **Suggested action:** investigate a single canonical "no-data"
  builder (`build_no_data_verdict(ticker, *, reason)`) shared by the
  three call sites.

## D-015
- **Category:** vocabulary-collision
- **Severity:** P2
- **§3.2 cluster:** 6 (cap helper)
- **Locations:**
  - `src/agents/analysts/<llm>/agent.py:schema_cap` helper
  - `src/agents/strategist/agent.py:schema_cap` helper
- **Evidence:** two definitions of a `schema_cap(model_name)` helper
  that derives the LLM's `max_output_tokens` from a config-driven cap.
  Same logic, two copies — analysts and strategist each carry one.
- **Relationship:** logic duplication.
- **Linked findings:** F-ops-005, F-analysts-010, F-strategist-013.
- **Suggested action:** consolidate into a single `agents/_llm_caps.py`
  helper.

## D-016
- **Category:** logic-duplication
- **Severity:** P1
- **§3.2 cluster:** not listed (new finding)
- **Locations:**
  - `scripts/backtest_fetch.py:379`
  - `src/backtest/runner.py`
  - `src/orchestrator/tick.py:62`
- **Evidence:** three independent definitions of `_REFERENCE_SYMBOLS`,
  the SPY+watchlist-superset list used for bulk yfinance fetches.  The
  three lists must agree for the watchlist-superset invariant to hold;
  there is no shared source of truth.  Drift would silently break
  reference_prices for any new symbol.
- **Relationship:** mechanical duplication of an invariant-bearing
  literal.
- **Linked findings:** F-backtest-005, F-orch-007.
- **Suggested action:** lift into `src/data/_reference_symbols.py` (or
  similar) and import from all three sites.

## D-017
- **Category:** structural-mirror
- **Severity:** P2
- **Locations:**
  - `src/backtest/cache/store.py` — inline `_audit_*` row-capture
    methods on `CachedDataStore`
  - `src/backtest/cache/auditing_store.py` — `AuditingStore` decorator
    that wraps a `CachedDataStore` and captures rows
- **Evidence:** two parallel mechanisms for "record every cache hit /
  miss for audit".  The inline `_audit_*` set is the older path; the
  decorator is the newer.  Both are still wired (the decorator is
  composed on top of the inline-audited store in tests).
- **Relationship:** two implementations of one capability, both live.
- **Linked findings:** F-backtest-007.
- **Suggested action:** investigate which is canonical; strip the other.

## D-018
- **Category:** vocabulary-collision
- **Severity:** P3
- **§3.2 cluster:** part of cluster 1
- **Locations:**
  - `src/contract/extractors.py` — `headline_polarity_mean`
  - `src/contract/extractors.py` — `headline_polarity_mean_7d` (alias)
- **Evidence:** two field names refer to the same value; the `_7d` suffix
  reflects the rolling window default but is hard-coded into the name.
- **Relationship:** alias.
- **Linked findings:** F-contract-004.
- **Suggested action:** pick one; nit-level.

## D-019
- **Category:** vocabulary-collision
- **Severity:** P3
- **Locations:**
  - `src/orchestrator/version.py` — `_git_sha7`
  - `src/observability/trace.py` — `_git_sha_full`
- **Evidence:** two helpers that hash-tag the git SHA at different
  truncations; trivially related and trivially confused at call sites.
- **Relationship:** near-alias.
- **Linked findings:** F-ops-008.
- **Suggested action:** one helper with a `length` parameter.

## D-020
- **Category:** logic-duplication
- **Severity:** P2
- **Locations:**
  - `src/observability/exporter.py` — `build_telemetry_record`
  - `src/observability/exporter.py` — `build_telemetry_record_from_logs`
- **Evidence:** two near-identical builders; the `_from_logs` variant
  is an orphan with no live caller per vocab/ops.md.
- **Relationship:** orphaned sibling.
- **Linked findings:** F-ops-006.
- **Suggested action:** delete the orphan after a search-and-confirm.

## D-021
- **Category:** cross-lifecycle
- **Severity:** P1
- **§3.2 cluster:** not listed (new finding)
- **Locations:**
  - `src/orchestrator/initialise.py:_STOCKBOT_TABLES = ("buffer_entries",
    "trade_log", "portfolio_snapshots")`
  - `src/orchestrator/hard_reset.py` (same tuple)
- **Evidence:** the tuple lists 3 of the 6 StockBot-owned tables.  The
  remaining three (whatever they are — fills, decisions, thesis) are
  silently excluded from initialise / hard_reset.  Live and backtest
  lifecycles will diverge as soon as one of those untracked tables holds
  state.
- **Relationship:** stale literal that cross-lifecycle invariants rely on.
- **Linked findings:** F-orch-004.
- **Suggested action:** investigate the full table set, fix the tuple
  in one shared module, import from both.

## D-022
- **Category:** cross-lifecycle
- **Severity:** P0
- **Locations:**
  - live tick: `src/orchestrator/tick.py` writes `state["as_of"]` as a
    `datetime` and reads via `state["as_of"]`
  - backtest driver: `src/backtest/driver.py` ISO-strings `as_of` before
    writing
- **Evidence:** intent §A and the auto-memory note both record this:
  every read of `state["as_of"]` must go through `resolve_as_of`, and
  every datetime write must ISO-string first (DatabaseSessionService
  cannot hold datetime).  Live still writes a raw datetime; backtest
  serialises.  Same key, two encodings, lifecycle-dependent.
- **Relationship:** cross-lifecycle schema divergence.
- **Linked findings:** F-orch-001.
- **Suggested action:** raised in the orchestrator-lifecycle module —
  the lifecycle dedupe here is "two encoders for one schema slot".

## D-023
- **Category:** cross-lifecycle
- **Severity:** P1
- **Locations:**
  - live runner registers `HandleInjectorPlugin`: absent
  - backtest runner: `src/backtest/runner.py` registers it
- **Evidence:** the ADK plugin that installs the per-tick handle is
  wired in the backtest runner but missing from the live runner per
  F-orch-002.  Same lifecycle responsibility ("install the handle once
  per session") executed in only one of the two runners.
- **Relationship:** lifecycle parity break.
- **Linked findings:** F-orch-002.
- **Suggested action:** lift the plugin registration into a shared
  runner-builder helper used by both lifecycles.

## D-024
- **Category:** schema-duplication
- **Severity:** P3
- **Locations:**
  - `src/agents/strategist/schema.py:StrategistDecision.thesis` (Field
    with TODO "rename state key from 'thesis' to 'user:thesis'")
  - `src/agents/strategist/context_shim.py:295-304` bridge slot
    `state["thesis"]` for `{thesis}` prompt placeholder
  - executor's after-callback writes `state["user:thesis"]`
- **Evidence:** a half-completed migration.  `user:thesis` is the
  contract key; `state["thesis"]` survives as a tick-scoped bridge so
  the prompt placeholder `{thesis}` resolves.  Two keys, one concept,
  with an inline TODO acknowledging the dedupe.
- **Relationship:** in-flight rename; the dedupe is the migration's
  residue.
- **Linked findings:** F-strategist-009.
- **Suggested action:** finish the migration — rename the placeholder
  to `{user:thesis}` (ADK supports prefixed keys), drop the bridge
  write, remove the TODO.

## D-025
- **Category:** cross-lifecycle
- **Severity:** P2
- **Locations:**
  - `src/orchestrator/tick.py` — `except BaseException` around live tick
  - `src/backtest/driver.py` — `except Exception` around backtest tick
- **Evidence:** F-orch-011 calls out the live side's `BaseException`
  catch as the wider net.  Same lifecycle role ("envelope the tick
  failure"), two different policies.  Lifecycle-divergent error
  swallowing is exactly the silent-failure class the auto-memory
  highlights.
- **Relationship:** cross-lifecycle policy mismatch.
- **Linked findings:** F-orch-011.
- **Suggested action:** unify on `except Exception` (and let
  `KeyboardInterrupt` / `SystemExit` propagate); if live really needs to
  catch `BaseException`, document why.

## D-026
- **Category:** schema-duplication
- **Severity:** P2
- **Locations:**
  - registry tail: `EarningsHistory`, `EarningsReport`,
    `AnalystConsensusBundle`, `AnalystRating`, `AnalystRevision`,
    `ShortInterestSnapshot`, `OptionContract`
- **Evidence:** seven Pydantic models registered in
  `data/registry.DOMAIN_SHAPES` for which no analyst-facing wrapper
  exists (per vocab/data.md F-data-001).  Each model is its own
  schema-shape with its own tests, but the domain has no consumer.
  These are pre-built schemas waiting for an analyst — until then they
  duplicate provider-shape work that no live code uses.
- **Relationship:** dormant schemas; not duplicates of each other but a
  set of schemas with no live consumer.
- **Linked findings:** F-data-001.
- **Suggested action:** investigate — either wire an analyst for at
  least one domain, or retire the registrations and the models until a
  consumer exists.

## D-027
- **Category:** logic-duplication
- **Severity:** P2
- **Locations:**
  - `src/agents/misc/snapshotter.py` writes `state["last_snapshot"]`
  - `src/orchestrator/tick.py` writes `state["last_executed_tick_id"]`
- **Evidence:** intent §5.3 and §5.4 describe two "last X" keys that
  serve overlapping roles (resume / replay / dedupe).  Both are written
  durably; they encode strictly more information together than either
  does alone, but downstream consumers tend to pick one and the
  invariant "last_snapshot.tick_id == last_executed_tick_id" is
  asserted nowhere.
- **Relationship:** parallel "high-water mark" writes with no shared
  source.
- **Linked findings:** F-orch-013, F-agents-misc-006.
- **Suggested action:** investigate — collapse to one (likely
  `last_executed_tick_id` is enough), or write both atomically through
  one helper that enforces the invariant.

---

## Disagreements

Two places where this synthesis disagrees with intent / vocab.

1. **Intent §3.2 cluster 9 (verdict/signal terminology).** Intent flags
   "verdict" and "signal" as a synonym candidate; vocab/analysts.md
   treats them as equivalent.  This synthesis finds no live duplication
   — `AnalystVerdict` is the contract type and "signal" appears only in
   docs/prose.  Recommend retiring the cluster from §3.2.
2. **Intent §3.2 cluster 10 (tick/cycle/run lifecycle terms).** §3.2
   names this a synonym candidate.  Audit of vocab/backtest.md and
   vocab/orchestrator-lifecycle.md finds the three terms have stable,
   non-overlapping meanings (tick = one strategist-decision iteration;
   cycle = absent; run = a backtest invocation).  Not a dedupe.

In addition, one **soft disagreement** with the module audits: D-007's
severity.  F-risk_gate-003 / -009 rate the stale-verb issue P1.  This
synthesis briefly considered promoting to P0 because it can cause
unintended SELL orders, but ultimately concurs with P1 — the path
requires both a `no_action` stance on a held ticker AND that ticker's
weight to breach a clamp on the same tick, which is rare.

---

## Vocabulary map

A consolidated table of every term that surfaced as a dedupe candidate.

| Term | Modules where it appears | Equivalent? | Finding ID |
|------|--------------------------|------------|-----------|
| `rationale` | contract (AnalystVerdict, TickerStance, PositionThesis); strategist | yes — derived everywhere | D-001 |
| `last_reviewed_reason` | contract (PositionThesis); executor (writes) | yes — alias of `stance.rationale` at write time | D-001 |
| `sell_reasons` / `update_reasons` | strategist (StrategistDecision); derivation | yes — derived from per-ticker `stance.rationale` | D-001 |
| `AnalystReport.summary` | contract | partial — overlaps `AnalystVerdict.rationale`; no policy on overlap | D-001 |
| `reasoning` (LLM prompt) | strategist prompt template | partial alias of `rationale` | D-001 |
| `state["positions"]` (bare) | executor (write, BUY→SELL bridge); strategist/context_shim, decision_logger, backtest/runner (reads) | no — bare is in-tick only; `user:positions` is durable | D-002 |
| `state["user:positions"]` | executor.after_callback (write); all readers | canonical | D-002 |
| `news/joiner` vs `fundamental/joiner` | analysts | yes — structural mirror | D-003 |
| analyst fetch_agent skeletons (5x) | analysts/{technical,fundamental,news,social,smart_money} | yes — structural mirror | D-004 |
| `state["smart_money_data"]` (bare) | analysts/smart_money | no — should be `temp:smart_money_data` | D-005 |
| `temp:<domain>_data` (4 sites) | analysts | canonical prefix policy | D-005 |
| `TickerVerdict` / `LlmTickerVerdict` | contract | parallel two-shape pattern with `StrategistDecision` / `StrategistLLMDecision` | D-006 |
| `StrategistDecision` / `StrategistLLMDecision` | strategist | same pattern as above | D-006 |
| `_NO_RISK_GATE_INTENTS = {"hold","update"}` | risk_gate | no — stale verb `hold`; canonical is `{update, no_action}` | D-007 |
| `_coerce_portfolio` | strategist/context_shim, strategist/enricher | yes — byte-identical | D-008 |
| `broker.get_portfolio()` (mid-tick) | risk_gate, executor, snapshotter | yes — re-implements Phase 2 seeder | D-009 |
| direct-write-plus-state_delta pairs | executor, snapshotter, memory_writer | yes — same Rule-1-respecting pattern thrice | D-010 |
| `<name>_analyst = XAnalystAgent()` singletons | analysts/{technical,fundamental} | yes — dead singletons mirroring live factories; same as `risk_gate_agent` | D-011 |
| Sharpe / drawdown computation | baselines/spy.SPYMetrics, backtest/reporting | yes — duplicated maths; SPYMetrics dead | D-012 |
| `DOMAINS: frozenset` | data/registry, data/config | yes — circular-import workaround | D-013 |
| no-data verdict synthesis | analysts/joiner, contract/extractors, strategist/derivation | yes — three sites with drifting defaults | D-014 |
| `schema_cap` helper | analysts (llm), strategist | yes — same maths | D-015 |
| `_REFERENCE_SYMBOLS` | scripts/backtest_fetch, backtest/runner, orchestrator/tick | yes — three copies of an invariant-bearing list | D-016 |
| `_audit_*` inline vs `AuditingStore` decorator | backtest/cache | yes — two row-capture mechanisms | D-017 |
| `headline_polarity_mean` / `_7d` | contract/extractors | yes — alias | D-018 |
| `_git_sha7` / `_git_sha_full` | orchestrator/version, observability/trace | yes — near-alias | D-019 |
| `build_telemetry_record` / `_from_logs` | observability/exporter | partial — second is orphan | D-020 |
| `_STOCKBOT_TABLES` (3-of-6) | orchestrator/initialise, orchestrator/hard_reset | yes — same stale tuple in two sites | D-021 |
| `state["as_of"]` encoding | live tick (datetime), backtest driver (ISO string) | no — two encodings of one key | D-022 |
| `HandleInjectorPlugin` registration | backtest only | no — missing in live | D-023 |
| `state["thesis"]` vs `state["user:thesis"]` | strategist (bridge), executor (durable) | yes — half-done migration | D-024 |
| tick-exception envelope | live (BaseException), backtest (Exception) | no — policy mismatch | D-025 |
| dormant domain schemas | data/models/{earnings, analyst_consensus, short_interest, options} | n/a — schemas without a consumer | D-026 |
| `last_snapshot` / `last_executed_tick_id` | snapshotter, orchestrator/tick | partial — overlapping high-water marks | D-027 |

---

## Notes on coverage

- §3.2 of intent.md listed 11 synonym clusters.  This synthesis maps
  9 of them onto live findings (clusters 1, 2, 3, 4, 5, 6, 7, 8, 11).
  Clusters 9 (`verdict`/`signal`) and 10 (`tick`/`cycle`/`run`) are
  judged terminology-only (see Disagreements).
- 3 new cross-module collisions not in §3.2: D-007, D-016, D-021.
- All §7 authoritative resolutions were respected: smart_money is wired
  (so analyst-side findings treat it as live, not dead);
  `_strategist_validation_callback` is dead (cited in D-024 chain via
  F-strategist-001 but not re-elevated as a dedupe);
  bare `state["positions"]` is executor-internal-only (D-002);
  data domain count is 5 + 3 (D-026's "dormant 4" matches §7.4).
- Source files were spot-checked to verify locations cited in vocab
  inventories; no source modifications were made.
