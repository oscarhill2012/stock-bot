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

1. **Pre-backtest correctness gates** — Groups 1 & 2.  These would silently
   distort or invalidate a first real backtest run.  Ship before the first
   trustworthy backtest window.
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
