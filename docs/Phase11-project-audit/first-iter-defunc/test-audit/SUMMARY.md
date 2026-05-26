# Test audit — consolidated summary

**Date:** 2026-05-25
**Rubric:** `RUBRIC.md` (T1–T8 categories, P0–P3 severity bands)
**Reports consolidated:** 16 per-subsystem audits under `docs/Phase11-project-audit/test-audit/*.md`
**Test files in scope:** ~211 `.py` test files; ~1,210 collected tests
**Total findings:** 178

| Severity | Count | Meaning |
|---|---|---|
| **P0** | 32 | Test masks, defends, or fails to surface a real source-side bug. |
| **P1** | 67 | Drift risk — parallel-branch fixture, dead test, weak completion-only assertion on a load-bearing path. |
| **P2** | 60 | Hygiene — layout sprawl, mock-at-wrong-level, redundant scaffolding. |
| **P3** | 19 | Cosmetic — docstrings, stray imports, name collisions. |

The single most important observation: **the test suite encodes the silent-failure attractor pattern as correct behaviour in at least nine distinct places.** Source-audit Theme 1 is mirrored exactly by Theme A below; every source-audit fix in F4 lands inside a regression test that currently asserts the broken behaviour is desired. Test rewrites are co-required, not optional.

## Headline counts per subsystem

| Subsystem | P0 | P1 | P2 | P3 | Files | Tests | Report |
|---|---|---|---|---|---|---|---|
| `agents/strategist` | **4** | 7 | 6 | 2 | 33 | 198 | strategist.md |
| `agents/executor` | **3** | 6 | 4 | 1 | 8 | 41 | executor.md |
| `agents/risk_gate` | **2** | 0 | 6 | 1 | 6 | 26 | risk-gate.md |
| `agents/{snapshot,memory,contract,*}` | **4** | 5 | 4 | 1 | 16 | 67 | agents-misc.md |
| `agents/analysts/{fundamental,news}` (LLM) | **2** | 4 | 6 | 2 | 15 | 77 | analysts-llm.md |
| `agents/analysts/{technical,social,smart_money,*}` | **3** | 8 | 7 | 2 | 21 | 164 | analysts-deterministic.md |
| `orchestrator` | **4** | 5 | 4 | 2 | 14 | 41 | orchestrator.md |
| `lifecycle` | **4** | 4 | 4 | 1 | 7 | 16 | lifecycle.md |
| `backtest` (+ cache/providers/audit) | **1** | 4 | 5 | 1 | 50 | ~155 | backtest.md |
| `broker` | **1** | 1 | 2 | 1 | 4 | 16 | broker.md |
| `data/providers` | **3** | 2 | 7 | 1 | 24 | 193 | data-providers.md |
| `data/models` + top-level | 0 | 5 | 5 | 1 | 18 | 101 | data-models-and-top-level.md |
| `contract` (extractors + schemas) | 0 | 9 | 8 | 2 | 22 | 184 | contract-package.md |
| `observability` | **1** | 2 | 3 | 1 | 13 | 79 | observability.md |
| `config` + `baselines` | 0 | 4 | 5 | 1 | 8 | 38 | config-and-baselines.md |
| layout + fixtures (cross-cutting) | 0 | 3 | 12 | 4 | (meta) | (meta) | layout-and-fixtures.md |

## Cross-cutting themes

### Theme A — Tests that codify silent-failure attractors as correct
Largest theme by impact. Nine tests across six subsystems assert that the wrong source-side behaviour is right; each fails when source-audit F4 lands — and that is the point.
- `agents-misc.md P0-01` — Snapshotter SPY-fetch swallow defended by positive test asserting flat-line equity curve.
- `observability.md P0-01` — `_trace_maybe` swallow asserted via raising `RuntimeError` inside wrapped fn and asserting silent return.
- `analysts-llm.md P0-01`/`P0-02` — `test_fetch_degrades_on_provider_error` twin tests (news + fundamental) assert empty bundle, no `feature_warning`.
- `data-providers.md P0-01`/`P0-02` — EDGAR filings & insider-trades `except Exception: continue` undefended; happy-path tests pin silent-skip as shape.
- `data-providers.md P0-03` — `test_social_sentiment_handles_403` asserts `result is not None` after Finnhub 403.
- `analysts-deterministic.md P0-03` — per-ticker swallow asserted as correct across three tests.
- `executor.md P0-01` — BUY-without-matching-stance silent-skip asserted as correct.
- `risk-gate.md P0-01`/`P0-02` — *absence* findings: zero tests for either raise path.

### Theme B — Missing T4 surfacing tests around source P0/P1 paths
Bug has no test on either side. Fix can land but the regression net is missing.
- `orchestrator.md P0-01` — `run_once` BaseException swallow no regression test.
- `orchestrator.md P0-02` — raw `datetime` → `create_session` no DatabaseSessionService test.
- `lifecycle.md P0-01`/`P0-02` — ADK session tables (sessions, user_states, app_states, events) untested by check/reset.
- `broker.md P1-01` — `Trading212Broker.get_portfolio` zero tests.
- `backtest.md P0-01` — `notable_holders` `as_of_date`/`filed_at` leak-detection has no firing test.
- `risk-gate.md P0-01`/`P0-02` — also belong here.

### Theme C — Parallel old/new branches still defended by tests
Source-audit Theme 2 mirrored one-for-one in tests.
- `executor.md P1-01`–`P1-06` — six tests across three dirs assert bare `"positions"` not canonical `user:positions`.
- `contract-package.md P1-01`–`P1-09` — 9 P1s: flat-list `insider_trades` fixtures, dead `"filings"`/`"news_items"` fixture shapes, alias-key tests pinning loser side (`headline_polarity_mean`, `aggregate_score`), `technical._resolve_bars` 3-branch coverage with **zero tests on live branch 2**.
- `strategist.md P1-04`/`P1-05` — legacy `PositionThesis` import + dead `evidence_view.py` still exercised.
- `analysts-deterministic.md P1-01` — SmartMoney delete/fix decision gates ~37 tests across 7 files.
- `data-models-and-top-level.md P1-01`–`P1-04` — model-shape tests for 4 unused Phase 3 domains (T1 zombies).

### Theme D — Layout sprawl
- 65 loose `tests/unit/*.py` files at root, none mirroring `src/` per §B.
- 4 parallel trees for `src/agents/analysts/`: `tests/agents/analysts/`, `tests/analysts/`, `tests/analysts/{news,fundamental}/`, `tests/unit/agents/analysts/`.
- 3 parallel trees for `src/agents/executor/`, 2 for `src/contract/`, 3 for `src/backtest/`.
- 29 duplicate test function names across files; one true bug (`test_output_always_six_chars` defined twice in same file, second overrides first).
- Mirror dirs absent for: memory, risk_gate, broker, lifecycle, scripts.

### Theme E — Completion-only assertions (T3)
- `strategist.md P0-03` — `test_strategist_v2_smoke` asserts object produced, no content.
- `risk-gate.md` family — three tests assert `final_orders` is a list, not contents.
- `lifecycle.md P0-03`/`P0-04` — CLI tests check `rc == 0` and substring-match only.
- `executor.md P0-03` — idempotency test asserts completion, not output equality.
- `orchestrator.md P0-04` — end-to-end no `branch_failed` caplog assertion.

### Theme F — Mock-at-wrong-level (T5)
- `broker.md P0-01` — `AsyncMock` `.json()` masks `await resp.json()`-on-sync bug.
- `agents-misc.md P0-03`/`P0-04` — Snapshotter patches `yfinance.Ticker` and injects `sys.modules["yfinance"]` instead of seam at `data.get_price_history`.
- `strategist.md P0-04` — backtest smokes patch `_build_strategist` with legacy callback, bypassing real `StrategistEnricher`.
- Multiple T5 P1s in `data-providers.md` patch `httpx.get` module-level.

### Theme G — Dead fixtures, conftests, helpers
- `layout-and-fixtures.md P2-04` — `load_fixture`/`fixture_path` documented in §D, used by zero tests.
- `layout-and-fixtures.md P2-05` — `tests/integration/conftest.py` `cache_root` + `make_ctx` dead.
- `config-and-baselines.md P1-02`/`P1-03` — `test_spy_metrics.py` + paired `_still_exists` regression-anchor; deletion-anchor pattern (test exists to prevent deletion of code whose only purpose is to satisfy that test).
- `data-models-and-top-level.md P1-04` — legacy `quiver` politician-trades test (provider commented out in fetcher).

## P0 roll-up (32 findings, ordered by source-audit subsystem)

| ID | Finding |
|---|---|
| `orchestrator.md P0-01` | BaseException swallow no regression test |
| `orchestrator.md P0-02` | Raw `datetime` → `create_session` no DatabaseSessionService test |
| `orchestrator.md P0-03` | `_build_initial_state` pins empty Phase-2 seed as contract |
| `orchestrator.md P0-04` | End-to-end no `branch_failed` caplog assertion |
| `lifecycle.md P0-01` | `_check_live_tables_empty` doesn't cover ADK tables |
| `lifecycle.md P0-02` | `hard_reset` doesn't truncate ADK tables |
| `lifecycle.md P0-03` | Initialise CLI test only checks `rc == 0` |
| `lifecycle.md P0-04` | `--yes` flag substring-matches "Archived" |
| `agents-misc.md P0-01` | Snapshotter SPY swallow defended by flat-line test |
| `agents-misc.md P0-02` | Cold-start anchors untested |
| `agents-misc.md P0-03` | Snapshotter patches `yfinance.Ticker` (T5) |
| `agents-misc.md P0-04` | Snapshotter injects fake `sys.modules["yfinance"]` (T5) |
| `risk-gate.md P0-01` | No test for falsy `strategist_decision` raise |
| `risk-gate.md P0-02` | No test for closing-without-`close_reason` raise |
| `analysts-deterministic.md P0-01` | `smart_money_data` vs `temp:` writer/reader mismatch untested |
| `analysts-deterministic.md P0-02` | `make_evidence_callback` not parametrised over smart_money |
| `analysts-deterministic.md P0-03` | Per-ticker swallow codified as correct |
| `backtest.md P0-01` | `notable_holders` leak detection no firing test |
| `strategist.md P0-01` | `tick_id="unknown"` fallback defended |
| `strategist.md P0-02` | `decision_writer` silent no-op codified by inverted assertion |
| `strategist.md P0-03` | `test_strategist_v2_smoke` weak completion-only |
| `strategist.md P0-04` | Backtest smokes patch `_build_strategist` with legacy callback |
| `executor.md P0-01` | BUY-without-matching-stance silent degrade defended |
| `executor.md P0-02` | Fill-price OR-chain dead-key fallback defended |
| `executor.md P0-03` | Idempotency test asserts completion only |
| `analysts-llm.md P0-01` | News `test_fetch_degrades_on_provider_error` codifies attractor |
| `analysts-llm.md P0-02` | Fundamental same-shape twin |
| `data-providers.md P0-01` | EDGAR filings swallow undefended |
| `data-providers.md P0-02` | EDGAR insider-trades twin swallow undefended |
| `data-providers.md P0-03` | Social-sentiment 403 path covered by `result is not None` |
| `broker.md P0-01` | `AsyncMock` masks `await resp.json()` bug (T5) |
| `observability.md P0-01` | `_trace_maybe` swallow asserted as correct |

Twelve reinforce a source-audit P0; eight reinforce a source-audit P1 by codifying as desired; twelve are pure-test issues (T3/T5/missing-test).

## Tests contingent on a source-fix PR

| Source-audit fix | Test rewrites co-required | Companion PR |
|---|---|---|
| **F2 Lifecycle reset symmetry** | `lifecycle.md P0-01`–`P0-04` (4 rewrites for ADK tables + state post-conditions) | Move 7 lifecycle files into `tests/unit/lifecycle/` |
| **F3 Live-only bombs** | `broker.md P0-01`; `agents-misc.md P0-01`–`P0-04`; `orchestrator.md P0-02` | `broker.md P1-01` get_portfolio cover |
| **F4 Surfacing primitive + apply** | All Theme A: `agents-misc.md P0-01`, `observability.md P0-01`, `analysts-llm.md P0-01`/`P0-02`, `data-providers.md P0-01`–`P0-03`, `analysts-deterministic.md P0-03`, `executor.md P0-01`, `risk-gate.md P0-01`/`P0-02` | none |
| **F5 Delete SmartMoney** | `analysts-deterministic.md P0-01`/`P0-02`/`P1-01`–`P1-04` + contract fixture deletions | Layout: collapse 4-tree analyst sprawl |
| **F6 Pull unused domains** | `data-models-and-top-level.md P1-01`–`P1-04` | none |
| **F7 Drop dual `PositionThesis`** | `strategist.md P1-04` + position_thesis fixture move | none |
| **F8 Bare `"positions"`** | `executor.md P1-01`–`P1-06` (6 rewrites across 3 dirs) | none |
| **F10 `notable_holders` field name** | `backtest.md P0-01` add firing test | none |
| **F11 Phase 2 hydration** *(blocked Spec C)* | `orchestrator.md P0-03` | when unblocked |
| Source-side BaseException narrow | `orchestrator.md P0-01` | none |
| Source-side `tick_id="unknown"` | `strategist.md P0-01`/`P0-02` | none |
| Source-side fill-price OR-chain | `executor.md P0-02` | none |
| Source-side `_trace_maybe` narrow | `observability.md P0-01` | none |

**Critical:** never land a source surfacing change without the paired test rewrite — otherwise the source change fails CI against the defending test and an engineer is tempted to skip hooks.

## Strategic open questions

1. **SmartMoney delete or fix** — gates ~37 tests across 7 files. Recommend **delete** in step with source F5.
2. **Four unused data domains** — earnings, analyst_consensus, short_interest, options. Recommend **pull** in step with source F6.
3. **Layout consolidation scope** — full ~80-file `git mv` PR vs incremental. Recommend **full move in one PR** with `pytest --collect-only` parity check.
4. **`tests/contract/` vs `tests/unit/contract/`** — split is arbitrary; `contract` marker unused. Recommend **consolidate to `tests/unit/contract/`**.
5. **`tests/integration/` marker hygiene** — only 1 of 20 files carries the marker. Recommend **marker pass + clarify `docs/test-policy.md §F`**.
6. **Dead test-only seams in `src/`** — source-audit recommends docstring-tighten on `_metrics_from_series`; test-audit recommends delete-with-tests. Defer to user (pairs with source-audit Open Q #4).
7. **Autouse fixture scope** — `_clear_analysts_config_cache` runs on all 1,210 tests; only ~100 need it. Recommend **scope down to analyst subtrees**.
8. **Duplicate test-name fix scope** — `test_output_always_six_chars` defined twice in `tests/unit/observability/test_terminal_log.py` (second silently overrides first). Recommend **immediate one-line standalone fix**; rest resolved by layout sweep.

## Suggested fix-PR groupings (12)

| PR | Scope | Pairs source | Severity |
|---|---|---|---|
| **T-F1 Invert silent-failure-defending tests** | All Theme A rewrites (Snapshotter, _trace_maybe, fetch-degrades twins, EDGAR, social 403, per-ticker, executor BUY) | F4 | P0 |
| **T-F2 Add missing surfacing tests** | Theme B: BaseException, datetime, risk-gate raises, notable_holders leak, broker get_portfolio | F3/F4/F10 | P0/P1 |
| **T-F3 Lifecycle ADK-tables coverage** | `lifecycle.md P0-01`–`P0-04` rewrites | F2 | P0 |
| **T-F4 Live-only test fixes** | `broker.md P0-01` AsyncMock fix; Snapshotter T5 fixes; datetime boundary | F3 | P0 |
| **T-F5 Strategist test cleanup** | `strategist.md P0-01`–`P0-04` + drop `P1-04`/`P1-05` legacy `PositionThesis`/`evidence_view` | F4/F7 | P0/P1 |
| **T-F6 Executor `"positions"` → `user:positions`** | `executor.md P1-01`–`P1-06` + consolidate to one dir | F8 | P1 |
| **T-F7 Delete SmartMoney test cluster** | ~37 tests across 4 analyst trees + smart_money fixtures | F5 | P0/P1 |
| **T-F8 Delete unused-domain model tests** | `data-models-and-top-level.md P1-01`–`P1-04` + quiver legacy test | F6 | P1 |
| **T-F9 Contract parallel-fixture cleanup** | `contract-package.md P1-01`–`P1-09` + add live-branch-2 coverage for `_resolve_bars` | (parallel-branch) | P1 |
| **T-F10 Layout sweep** | 65 file `git mv` + collapse parallel trees + create missing mirror dirs + delete empty `__init__.py` + fix duplicate-name bug + rename residual collisions + scope-down autouse + delete dead `load_fixture`/`make_ctx`/`cache_root` | independent | P1/P2 |
| **T-F11 Marker pass** | `pytestmark = pytest.mark.integration` to 19 files; tag known-slow; update §F | independent | P1 |
| **T-F12 Completion-only assertion rewrites** | Theme E residue: `risk-gate.md P0-03`-family, `orchestrator.md P0-04`, `executor.md P0-03`, `strategist.md P0-03` | mixed | P0 |

**Sequencing:** T-F10 first (pure `git mv`, makes later diffs inspectable); T-F11 parallel; T-F3/T-F4/duplicate-name fix independent; T-F1+source F4, T-F2+source F3/F4/F10, T-F5+source F4/F7, T-F6+source F8, T-F7+source F5, T-F8+source F6 are **co-required pairings never split**; T-F9/T-F12 tail.

## Headline totals

| Severity | Count |
|---|---|
| P0 | 32 |
| P1 | 67 |
| P2 | 60 |
| P3 | 19 |
| **Total** | **178** |

Largest test surface: `data/providers` (193 tests / 24 files). Densest P0/file ratio: `lifecycle` (4 P0 / 7 files). Densest P1/test ratio: `contract` (9 P1 / 184 tests, all parallel-branch fixtures).
