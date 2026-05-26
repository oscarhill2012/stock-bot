# Source audit — consolidated summary

**Date:** 2026-05-25
**Rubric:** `RUBRIC.md`
**Subsystems audited:** 15 (plus two empty packages added by the consolidator)
**Total findings:** ~153

| Severity | Count | Meaning |
|---|---|---|
| **P0** | 10 | Correctness bug, contract violation that can produce wrong outputs, or silent-failure attractor on a load-bearing path. |
| **P1** | 45 | Code-health hazard with high regression risk: active dead branches, parallel old/new one bad merge from divergence, silent failures on degraded paths. |
| **P2** | 65 | Tidy-up without urgency — empty packages, single-impl interfaces, doc drift, cosmetic config violations. |
| **P3** | 33 | Cosmetic — typos, dead imports, stale TODOs. |

| Subsystem | P0 | P1 | P2 | P3 | Report |
|---|---|---|---|---|---|
| `src/orchestrator` | **3** | 4 | 3 | 2 | [orchestrator.md](orchestrator.md) |
| `src/lifecycle` | **2** | 3 | 3 | 2 | [lifecycle.md](lifecycle.md) |
| `src/agents/{snapshot,contract,memory,*}` | **2** | 4 | 3 | 1 | [agents-misc.md](agents-misc.md) |
| `src/agents/risk_gate` | **1** | 0 | 3 | 1 | [agents-risk-gate.md](agents-risk-gate.md) |
| `src/agents/analysts/{technical,social,smart_money,*}` | **1** | 5 | 5 | 2 | [agents-analysts-deterministic.md](agents-analysts-deterministic.md) |
| `src/backtest` (+ cache/providers/audit) | **1** | 3 | 6 | 2 | [backtest.md](backtest.md) |
| `src/agents/strategist` | 0 | 4 | 6 | 3 | [agents-strategist.md](agents-strategist.md) |
| `src/agents/executor` | 0 | 3 | 3 | 2 | [agents-executor.md](agents-executor.md) |
| `src/agents/analysts/{fundamental,news}` | 0 | 2 | 4 | 3 | [agents-analysts-llm.md](agents-analysts-llm.md) |
| `src/contract` (extractors + schemas) | 0 | 4 | 6 | 3 | [contract.md](contract.md) |
| `src/data` (models + top-level) | 0 | 4 | 8 | 3 | [data-models-and-top-level.md](data-models-and-top-level.md) |
| `src/data/providers` | 0 | 4 | 4 | 5 | [data-providers.md](data-providers.md) |
| `src/broker` | 0 | 2 | 2 | 0 | [broker.md](broker.md) |
| `src/observability` | 0 | 2 | 3 | 2 | [observability.md](observability.md) |
| `src/config` + `src/baselines` | 0 | 1 | 4 | 2 | [config-and-baselines.md](config-and-baselines.md) |

Plus two empty packages flagged by the consolidator:

- `src/agents/attribution/` — empty (no `.py` files). P2 dead package; delete.
- `src/deploy/` — empty. P2 dead package; delete.

---

## Cross-cutting themes (the real story)

Severity counts say "tidy up". Themes say something stronger: **most of the
high-severity findings are instances of the same six patterns repeating
across subsystems**. Fix the patterns, the long tail collapses.

### Theme 1 — Silent-failure attractors on load-bearing paths (C5)

The repeating bug class predicted by `feedback_silent_failures_loud_tests`.
At least 10 of the 10 P0s and 15+ of the 45 P1s are this shape. Hot spots:

- **RiskGate** P0 — bare `return` on falsy `strategist_decision` lets the
  tick complete with zero `final_orders`, no surfaced error.
- **Snapshotter** P0 — SPY fetch errors caught into `spy_price = 0.0`,
  flat-lining the equity curve while the driver's pipeline-completion
  check still passes.
- **Orchestrator** P0 — `except (AttributeError, BaseException)` around
  the whole pipeline-run loop is a textbook attractor.
- **LLM analysts** P1 pair — fetch-failure paths produce the same empty
  bundle as genuine no-data; the LLM then self-declares `is_no_data` with
  no `feature_warning` propagated.
- **Data providers** P1 — three EDGAR providers (`filings`,
  `insider_trades`, `notable_holders`) share `except Exception: continue`
  that silently drops rows; Finnhub social-sentiment returns neutral
  empty on free-tier 403s (unavailability indistinguishable from absence).
- **Observability** P1 — `_trace_maybe` swallows snapshot exceptions to
  log-only; `terminal_log` callback swallows `usage_metadata` errors with
  bare `pass`.
- **Executor** has its own variants (P1/P2 family in [agents-executor.md](agents-executor.md)).

The pattern across all of these: an exception path that **should raise**
or **should set an explicit `feature_warning`** instead returns
"benign empty" — and no test asserts the surfacing behaviour. The fix
plan should land a uniform surfacing primitive (raise / `feature_warning`
emission helper) and apply it to every named site above.

### Theme 2 — Parallel old/new branches (C2)

Almost as pervasive as Theme 1. Active sites:

- **Executor + Strategist context shim + backtest decision logger:** bare
  `"positions"` key written cross-tick by Executor alongside the
  canonical `user:positions` Spec-B writer. Two external readers
  consume the bare key. One swallowed exception from divergence.
- **Strategist:** two `PositionThesis` Pydantic classes coexisting —
  `schema.py` (legacy, materially different required fields) vs
  `position_thesis.py` (canonical). Production only reads canonical;
  legacy anchored solely by four test files.
- **Strategist:** `evidence_view.py` is dead in `src/` — production
  renders via `contract/strategist_prompt.py:render_all_ticker_blocks`,
  a parallel rendering implementation.
- **Contract / extractors:** four extractor sites carry parallel raw-payload
  shapes (`fundamental.Form4Bundle` vs flat-list, `technical._resolve_bars`
  three-branch, `news` three-key alternatives, `smart_money`
  filings/transactions/politician_trades). Production picks one branch;
  the others are test-only fossils.
- **Contract / extractors:** back-compat alias feature keys
  (`headline_polarity_mean` ⇄ `_7d`, `aggregate_score` ⇄
  `social_aggregate_score`). The strategist prompt reads the alias side;
  the "primary" name could be deleted with no effect.
- **Data layer:** four entire wired-but-unused domains — `earnings`,
  `analyst_consensus`, `short_interest`, `options`. Config + registry +
  `DOMAIN_SHAPES` + Pydantic models + provider modules all present; no
  agent consumes any of them.
- **Config:** `StanceCaps.close_reason_max_chars` /
  `trim_reason_max_chars` marked DEPRECATED, still in `strategist.json`,
  documented in `README.md` as capping fields that were deleted in Spec B.

Pattern: the "current" implementation works, the "old" one survives, the
two are still wired but only one is read. Each is an ambush for the next
contributor. Consolidation candidates: kill the dead side wherever
production is unambiguous; for genuine "kept for fallback" cases, mark
the dormant side at the source.

### Theme 3 — Phase 1 / Phase 2 contract violations by erasure (C4)

The most concerning P0 cluster — these can corrupt cross-tick state
silently:

- **Lifecycle** (×2 P0) — `_check_live_tables_empty` and `hard_reset`
  only know about three legacy ORM tables (`buffer_entries`, `trade_log`,
  `portfolio_snapshots`). Since Spec B, `user:positions` and
  `user:thesis` live in ADK `DatabaseSessionService` tables (`sessions`,
  `user_states`, `app_states`, `events`). So:
  - "Fresh" boot check passes while ADK state survives → stale thesis
    book resurrected.
  - "Hard reset" leaves the canonical thesis book in place.
- **Orchestrator** P0 — live `tick.py:151-152` seeds `memory_buffer=[]`
  and `day_digest=""` empty at Phase 2 instead of hydrating from §E
  persistence. (Spec C is deferred so the persistence backend isn't
  built yet, but the seeding pattern is wrong now.)
- **Orchestrator** P0 — `tick.py:148` writes a raw `datetime` into
  `create_session(state=...)`; `DatabaseSessionService` will fail to
  JSON-serialise (latent because live isn't deployed; per the user
  memory `feedback_as_of_boundary_coercion` this is the recurring
  boundary-coercion class).
- **Backtest** P0 — `notable_holders` provider maps to a non-existent
  `as_of_date` field in two audit-layer files; the real PIT column is
  `filed_at`. Leak detection for that whole domain silently disabled.

These four want a coordinated fix plan: hard-reset semantics + the
table list, Phase 2 hydration helpers, the `datetime` boundary coercion.

### Theme 4 — §C-Rule 7 boundary violations

One concentrated finding from the orchestrator audit: **four pipeline
sub-agents import `orchestrator.persistence` and write SQLAlchemy rows
mid-tick** — `EvidenceWriter`, `StrategistDecisionWriter`,
`SnapshotterAgent`, `ExecutorAgent`. Rule 7 says the lifecycle wrapper
owns persistence, not the pipeline. Each of those four subsystem
reports surfaces the seam from its own side.

Decision needed: either (a) carve them out in §C-Rule 7 (Spec B–style
"this is a known seam, here's why") or (b) lift the persistence writes
above the pipeline into a Phase 4 wrapper. (a) is a doc change; (b) is
structural and probably the right answer, but expensive.

### Theme 5 — Doc/code drift in `contract-invariants.md` itself

The yardstick is wrong in at least five places. Every one of these
should be patched **before** any fix PRs land, otherwise the fix PRs
will be reviewed against a drifted spec.

- §C-Rule 1 (lines 244-251) — cites `_strategist_validation_callback`
  at the wrong file:line (off by ~329 lines) and frames a now-replaced
  test-only shim as the "canonical instance today". Production has
  used `StrategistEnricher` (a real BaseAgent yielding a `state_delta`
  event) since 2026-05-25.
- §A `user:thesis` row — footnote references `thesis_revision`; actual
  field is `thesis`.
- §A `last_snapshot` row — `src/backtest/driver.py:393-401` line-number
  drift.
- §A — no rows for `*_evidence` keys (`news_evidence`,
  `fundamental_evidence`, plus the technical/social analogues).
  Written every tick and persisted via `agents/contract/evidence_writer.py`
  to the DB. The contract pretends these don't exist.
- §A — no rows for `smart_money_*` keys.

### Theme 6 — Live-only latent bugs masked by pre-deployment

The bot has never run live. Several findings are bombs that will detonate
on first live tick. These are not test failures; tests pass.

- **Broker** P1 — `Trading212Broker.resp.json()` is `await`-ed against
  a synchronous `httpx.Response.json()`. Unit tests use `AsyncMock` and
  hide it. First live API call → `TypeError`.
- **Broker** P1 — `get_portfolio` silently `continue`s past T212
  positions whose code isn't in the reverse instrument map. Every
  current call site passes `instrument_map={}`. Result: a live
  `get_portfolio` returns cash-only, no holdings, no warning.
- **Snapshotter** P0-paired — `starting_capital` / `spy_start_price`
  writes are masked by the backtest carry-forward but broken under
  live cold-start.
- **Orchestrator** P0 — the raw-`datetime` write into
  `create_session` will fail `DatabaseSessionService` JSON
  serialisation on first live tick.

Together these mean live-deployment day is a debugging fire-drill.
Pre-deployment is a free window to fix them; they should land before
any paper-trading attempt.

---

## Open strategic questions

These don't have an obvious answer from the audit; the consolidator is
deferring them to you.

1. **SmartMoney: delete or fix?** It's shelved at
   `orchestrator/pipeline.py:88`. The deterministic-analysts audit found
   a P0 (`temp:`-prefix mismatch between writer and reader) and the
   contract-extractors audit found a Pydantic-vs-dict bug that would
   AttributeError. Both dissolve if SmartMoney is deleted. Several
   findings in `data-providers.md` and `data-models-and-top-level.md`
   only matter if SmartMoney comes back. **Recommend: delete unless
   there is an active plan to re-enable**, since the bugs are real but
   the code is unreached.

2. **Four wired-but-unused data domains:** `earnings`,
   `analyst_consensus`, `short_interest`, `options`. The wiring is real;
   the consumers don't exist. Either build the consumer agents (large
   scope) or pull the wiring (one cleanup PR). **Recommend: pull the
   wiring; reinstate when a real consumer lands.**

3. **§C-Rule 7 decision** (Theme 4). Carve-out (cheap doc change) vs lift
   persistence above the pipeline (real refactor). The carve-out is the
   pragmatic choice; the lift is the principled one.

4. **Test-anchored zombies.** Several pieces of dead-looking code in
   `src/` (legacy `PositionThesis`, parallel extractor branches, dead
   memory helpers) are not actually dead — tests still exercise them.
   The fix-plan workstream can't delete them without also touching
   `tests/`. **The test audit (next workstream) needs to be told which
   of these are slated for deletion** so it can drop the anchoring
   tests in the same pass.

5. **`scripts/` boundary.** The audit was `src/`-scoped per the rubric,
   but four findings cross the boundary into `scripts/`:
   - `Trading212Broker(...)` constructions in `scripts/initialise.py`
     and `scripts/trace_tick.py` pass `instrument_map={}`.
   - `scripts/trace_tick.py` seeds `"_trace"` (wrong) instead of
     `"temp:_trace"` (right).
   - Dead-looking scripts noted but not flagged per
     `feedback_test_audit_scope_tests_only` (surface as question).

   **Recommend: open a brief `scripts/` audit as a sibling spec** after
   this one's fix plan lands. Small surface; ~6 scripts; one subagent.

---

## Suggested fix-PR groupings

The reports' per-finding "Suggested action" lines map roughly onto these
PR clusters. Sizing is a sketch; the fix-plan workstream will reconcile.

| PR | Scope | Spans subsystems | Severity |
|---|---|---|---|
| **F1 — Patch the contract doc** | Fix the five drifted spots in `docs/contract-invariants.md` listed in Theme 5. Doc-only. Lands first so subsequent PRs are reviewed against a correct spec. | docs only | — |
| **F2 — Lifecycle reset symmetry** | Bring `_check_live_tables_empty` and `hard_reset` into line with ADK `DatabaseSessionService` tables. The two P0 lifecycle findings. | lifecycle | P0 |
| **F3 — Live-only latent bombs** | Theme 6: fix `Trading212Broker` `await`-on-sync-json, fix `get_portfolio` silent-drop, fix snapshotter cold-start, fix orchestrator `datetime` boundary. | broker, snapshotter, orchestrator, tick wiring (+ scripts/) | P0/P1 |
| **F4 — Surfacing primitive + apply** | Land a uniform "raise or `feature_warning`" helper; apply to every Theme-1 site. ~10 named call sites. Largest single PR; tests must assert surfacing behaviour. | risk_gate, snapshotter, orchestrator, both analyst groups, data-providers, observability, executor | P0/P1 |
| **F5 — Delete SmartMoney (pending decision)** | If you choose delete: rip `src/agents/analysts/smart_money/`, `data/providers/{notable_holders,politician_trades}/*` usage from extractors, `SmartMoneyRaw` model. Resolves a P0 + two P1s by deletion. | smart_money, contract, data | P0/P1 |
| **F6 — Pull unused data domains (pending decision)** | If you choose pull: remove `earnings`/`analyst_consensus`/`short_interest`/`options` from `DOMAIN_SHAPES`, registry, config, README, models. Resolves ~10 findings by deletion. | data, config | P1/P2 |
| **F7 — Drop dual `PositionThesis`** | Delete the legacy `schema.py` `PositionThesis`; coordinate test deletions with the test audit. | strategist | P1 |
| **F8 — Bare `"positions"` → `user:positions`** | Drop the bare-key write from the executor; switch the strategist context shim and the backtest decision logger to read `user:positions`. | executor, strategist, backtest | P1 |
| **F9 — Empty-package + dead-helper cleanup** | Delete `src/agents/attribution/`, `src/deploy/`, the three dead `agents/memory/` helpers, `Broker.position_size`, `emit_analyst_totals` / `emit_analyst_header`, deprecated stance-caps from `config/strategist.json`, doc rows. Single sweep. | many | P2 |
| **F10 — Backtest `notable_holders` field name** | `as_of_date` → `filed_at` in two audit-layer files. Tiny. | backtest | P0 |
| **F11 — Phase 2 hydration symmetry (deferred)** | Theme 3's `memory_buffer`/`day_digest` Phase-2-seed issue depends on Spec C landing the persistence layer; cannot land before then. Track but don't plan yet. | orchestrator, lifecycle | (blocked) |

**Cosmetic findings (P2/P3 drift, dead imports, stale TODOs)** that don't
fit a themed PR should ride along with whichever PR already touches the
file. No dedicated PR for them.

---

## What to do next

The audit doc tree (`docs/Phase11-project-audit/source-audit/`) is the input to the fix-plan
workstream. Your call now:

1. **Read this summary** and skim the per-subsystem reports for the
   findings that surprise you or you want to challenge.
2. **Decide the strategic questions** (especially #1 and #2 — they
   reshape several PRs).
3. **Tell me to commit** the `docs/Phase11-project-audit/source-audit/` tree as one commit.
4. **Open a separate spec** (or just a planning conversation) for the
   fix work — that's where the test-audit sequencing and PR ordering
   gets nailed down.

No code has changed. No commits made. Everything in this audit is
reversible by deleting `docs/Phase11-project-audit/source-audit/`.
