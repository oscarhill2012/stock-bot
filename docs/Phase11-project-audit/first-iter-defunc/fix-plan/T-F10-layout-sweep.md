# T-F10 — Layout sweep (pure structural)

**Wave:** 1 (serial — foundation; merges before Wave 2 dispatches)
**Pairs source-audit fix:** none
**Branch:** `fix/T-F10-layout-sweep`
**Depends on:** none
**Estimated diff size:** large (file-count) / small (semantic)

## Scope

Reorganise the `tests/` tree so it mirrors `src/` per `docs/test-policy.md` §B
and collapses the four-or-more parallel mirror trees that have accumulated
through successive reorganisations. No semantic test change at all — every
move is a `git mv` plus the minimum import-path patch required to keep
imports resolving. The objective is to make subsequent test-audit PRs
(SmartMoney deletion, unused-domain pull, marker pass, surfacing-test
rewrites) inspectable: today their diffs are dominated by layout noise that
masks the real correctness changes.

### In scope

**1. Move the 65 loose `tests/unit/*.py` files into the mirror tree.**

Destinations (per `layout-and-fixtures.md` P1-01):

`tests/unit/agents/analysts/` — heuristics / verdict / prompt files
- `test_analyst_heuristics.py` → `tests/unit/agents/analysts/test_analyst_heuristics.py`
- `test_analyst_name_literal.py` → `tests/unit/contract/test_analyst_name_literal.py`
- `test_analyst_config_rationale_budget.py` → `tests/unit/agents/analysts/test_analyst_config_rationale_budget.py`
- `test_analyst_prompts_anti_truncation.py` → `tests/unit/agents/analysts/test_analyst_prompts_anti_truncation.py`
- `test_derive_technical_verdict.py` → `tests/unit/contract/extractors/test_technical_verdict.py`
- `test_derive_social_verdict.py` → `tests/unit/contract/extractors/test_social_verdict.py`
- `test_derive_smart_money_verdict.py` → `tests/unit/contract/extractors/test_smart_money_verdict.py` *(SmartMoney delete in T-F07 may remove this; here the move is unconditional)*
- `test_extract_fundamental_features.py` → `tests/unit/contract/extractors/test_fundamental_extra.py`
- `test_extract_social_features.py` → `tests/unit/contract/extractors/test_social_extra.py`
- `test_smart_money_fetch.py` → `tests/unit/agents/analysts/smart_money/test_fetch.py` *(precondition: create `tests/unit/agents/analysts/smart_money/`)*
- `test_smart_money_gate.py` → `tests/unit/agents/analysts/smart_money/test_gate.py`
- `test_social_analyst_run.py` → `tests/unit/agents/analysts/social/test_run.py` *(precondition: create `tests/unit/agents/analysts/social/`)*
- `test_social_fetch.py` → `tests/unit/agents/analysts/social/test_fetch.py`
- `test_news_prompt_bearish_nudge.py` → `tests/unit/agents/analysts/news/test_prompt_bearish_nudge.py`
- `test_news_prompt_render.py` → `tests/unit/agents/analysts/news/test_prompt_render.py`
- `test_news_prompt_report_required.py` → `tests/unit/agents/analysts/news/test_prompt_report_required.py`
- `test_fundamental_prompt_decision_rule.py` → `tests/unit/agents/analysts/fundamental/test_prompt_decision_rule.py`
- `test_fundamental_prompt_render.py` → `tests/unit/agents/analysts/fundamental/test_prompt_render.py`
- `test_fundamental_prompt_report_required.py` → `tests/unit/agents/analysts/fundamental/test_prompt_report_required.py`

`tests/unit/agents/strategist/` (sibling tree already exists)
- `test_strategist_schema.py` → `tests/unit/agents/strategist/test_strategist_schema.py`
- `test_strategist_prompt_risk_substitutions.py` → `tests/unit/agents/strategist/test_strategist_prompt_risk_substitutions.py`
- `test_strategist_prompt_worked_examples_ticker.py` → `tests/unit/agents/strategist/test_strategist_prompt_worked_examples_ticker.py`

`tests/unit/agents/memory/` (create — does not yet exist)
- `test_memory_compress.py` → `tests/unit/agents/memory/test_memory_compress.py`
- `test_memory_eviction.py` → `tests/unit/agents/memory/test_memory_eviction.py`
- `test_memory_schema.py` → `tests/unit/agents/memory/test_memory_schema.py`
- `test_memory_writer_agent.py` → `tests/unit/agents/memory/test_memory_writer_agent.py`

`tests/unit/agents/risk_gate/` (create — does not yet exist)
- `test_risk_gate_config_loader.py` → `tests/unit/agents/risk_gate/test_risk_gate_config_loader.py`
- `test_risk_gate_constraints.py` → `tests/unit/agents/risk_gate/test_risk_gate_constraints.py`
- `test_risk_gate_orders.py` → `tests/unit/agents/risk_gate/test_risk_gate_orders.py`

`tests/unit/broker/` (create — does not yet exist)
- `test_fake_broker.py` → `tests/unit/broker/test_fake_broker.py`
- `test_portfolio.py` → `tests/unit/broker/test_portfolio.py`
- `test_trading212_request_construction.py` → `tests/unit/broker/test_trading212_request_construction.py`

`tests/unit/observability/` (already has 8 sibling files)
- `test_trace_writer.py` → `tests/unit/observability/test_trace_writer.py`
- `test_trace_writer_exception_logging.py` → `tests/unit/observability/test_trace_writer_exception_logging.py`
- `test_trace_maybe_noop.py` → `tests/unit/observability/test_trace_maybe_noop.py`
- `test_llm_trace_callbacks.py` → `tests/unit/observability/test_llm_trace_callbacks.py`

`tests/unit/orchestrator/` (already exists)
- `test_tick_entrypoint.py` → `tests/unit/orchestrator/test_tick_entrypoint.py`
- `test_tick_state.py` → `tests/unit/orchestrator/test_tick_state.py`

`tests/unit/backtest/` (already exists)
- `test_embeddings.py` → `tests/unit/backtest/test_embeddings.py`
- `test_buffer_persistence.py` → `tests/unit/backtest/test_buffer_persistence.py`
- `test_decision_logger_strict_serialiser.py` → `tests/unit/backtest/test_decision_logger_strict_serialiser.py`
- `test_reporting_span_names.py` → `tests/unit/backtest/test_reporting_span_names.py`
- `test_plot_equity.py` → `tests/unit/backtest/test_plot_equity.py`
- `test_equity_curve.py` → `tests/unit/backtest/test_equity_curve.py`
- `test_spy_metrics.py` → `tests/unit/backtest/test_spy_metrics.py`
- `test_snapshot_persistence.py` → `tests/unit/backtest/test_snapshot_persistence.py`

`tests/unit/data/` (mirror tree)
- `test_dedup.py` → `tests/unit/data/test_dedup.py`
- `test_evidence_index.py` → `tests/unit/data/test_evidence_index.py`
- `test_evidence_row_persistence.py` → `tests/unit/data/test_evidence_row_persistence.py`
- `test_trade_log.py` → `tests/unit/data/test_trade_log.py`
- `test_form4_parser.py` → `tests/unit/data/providers/test_insider_trades_form4_parser.py`
- `test_insider_model_roundtrip.py` → `tests/unit/data/models/test_insider_model_roundtrip.py`

`tests/unit/lifecycle/` (create — does not yet exist)
- `test_initialise.py` → `tests/unit/lifecycle/test_initialise.py`
- `test_initialise_cli.py` → `tests/unit/lifecycle/test_initialise_cli.py`
- `test_hard_reset.py` → `tests/unit/lifecycle/test_hard_reset.py`
- `test_hard_reset_cli.py` → `tests/unit/lifecycle/test_hard_reset_cli.py`
- `test_init_db_script.py` → `tests/unit/lifecycle/test_init_db_script.py`
- `test_lifecycle_initialise.py` → `tests/unit/lifecycle/test_lifecycle_initialise.py`
- `test_session_service_factory.py` → `tests/unit/lifecycle/test_session_service_factory.py`

`tests/unit/scripts/` (create — does not yet exist)
- `test_smoke_run_cli.py` → `tests/unit/scripts/test_smoke_run_cli.py`
- `test_replay_backtest_cli.py` → `tests/unit/scripts/test_replay_backtest_cli.py`
- `test_stock_picker.py` → `tests/unit/scripts/test_stock_picker.py`
- `test_schedule_config.py` → `tests/unit/scripts/test_schedule_config.py`
- `test_scheduler_yaml.py` → `tests/unit/scripts/test_scheduler_yaml.py`
- `test_cloudbuild_yaml.py` → `tests/unit/scripts/test_cloudbuild_yaml.py`

**2. Collapse the four parallel analyst trees.**

(`layout-and-fixtures.md` P1-02, P2-07, P2-08)

- `tests/agents/analysts/test_evidence_callback.py` → `tests/unit/agents/analysts/test_evidence_callback.py`
- `tests/agents/memory/test_writer_smart_money_seen.py` → `tests/unit/agents/memory/test_writer_smart_money_seen.py` *(SmartMoney delete in T-F07 may remove this; here move is unconditional and T-F07 deletes from the new location)*
- `tests/agents/test_isolated_failure.py` → `tests/unit/agents/test_isolated_failure.py`
- `tests/agents/test_output_caps_per_ticker.py` → `tests/unit/agents/test_output_caps_per_ticker.py`
- `tests/analysts/test_smart_money.py` → `tests/unit/agents/analysts/smart_money/test_construction.py`
- `tests/analysts/test_technical.py` → `tests/unit/agents/analysts/technical/test_construction.py` *(precondition: create `tests/unit/agents/analysts/technical/`)*
- `tests/analysts/test_branch_composition.py` → `tests/unit/orchestrator/test_branch_composition.py`
- `tests/analysts/test_cache_callbacks_per_ticker.py` → `tests/unit/agents/analysts/test_cache_callbacks_per_ticker.py`
- `tests/analysts/test_per_ticker_branch.py` → `tests/unit/agents/analysts/test_per_ticker_branch.py`
- `tests/analysts/news/test_fetch_agent.py` → `tests/unit/agents/analysts/news/test_fetch_agent.py`
- `tests/analysts/news/test_joiner.py` → `tests/unit/agents/analysts/news/test_joiner.py`
- `tests/analysts/news/test_prompts.py` → `tests/unit/agents/analysts/news/test_prompts.py`
- `tests/analysts/fundamental/test_fetch_agent.py` → `tests/unit/agents/analysts/fundamental/test_fetch_agent.py`
- `tests/analysts/fundamental/test_joiner.py` → `tests/unit/agents/analysts/fundamental/test_joiner.py`
- `tests/analysts/fundamental/test_prompts.py` → `tests/unit/agents/analysts/fundamental/test_prompts.py`

After the moves, `tests/agents/` and `tests/analysts/` (and their nested
`news/` / `fundamental/` / `memory/` / `analysts/` subdirs) are empty
except for `__init__.py` and `__pycache__`. Delete both trees.

**3. Collapse the three executor trees.**

(`layout-and-fixtures.md` P2-01)

- `tests/executor/test_executor_bookkeeping.py` → `tests/unit/agents/executor/test_bookkeeping.py`
- `tests/unit/executor/test_open_positions_state.py` → `tests/unit/agents/executor/test_open_positions_state.py`
- `tests/unit/agents/test_executor_decision_hook.py` → `tests/unit/agents/executor/test_decision_hook.py`

Delete the now-empty `tests/executor/` and `tests/unit/executor/`.

**4. Consolidate `tests/contract/` and `tests/unit/contract/`.**

(`layout-and-fixtures.md` P2-02; `contract-package.md` P2-08)

Move *behavioural-shape* tests into `tests/unit/contract/`, keep
*layer-boundary* tests in `tests/contract/`:

- `tests/contract/test_evidence_schema.py` → `tests/unit/contract/test_evidence_schema.py`

Leave the four genuine layer-boundary tests in place under `tests/contract/`
(they assert on signatures / config-sourcing and warrant the `contract`
marker once T-F11 lands):
- `tests/contract/test_http_timeout_sourced_from_config.py`
- `tests/contract/test_lookbacks_sourced_from_config.py`
- `tests/contract/test_no_hardcoded_models.py`
- `tests/contract/test_provider_shapes.py`
- `tests/contract/test_schedule_sourced_from_config.py`
- `tests/contract/test_wrappers_supply_lookback_to_cache.py`

**5. Consolidate the three orchestrator trees.**

(`layout-and-fixtures.md` P2-03)

- `tests/orchestrator/test_pipeline_build.py` → `tests/unit/orchestrator/test_pipeline_build.py`

Delete the now-empty `tests/orchestrator/`.

**6. Create the missing mirror dirs.**

Each new directory gets an empty `__init__.py`:
- `tests/unit/agents/memory/__init__.py`
- `tests/unit/agents/risk_gate/__init__.py`
- `tests/unit/broker/__init__.py`
- `tests/unit/lifecycle/__init__.py`
- `tests/unit/scripts/__init__.py`
- `tests/unit/agents/analysts/smart_money/__init__.py`
- `tests/unit/agents/analysts/social/__init__.py`
- `tests/unit/agents/analysts/technical/__init__.py`
- `tests/unit/agents/analysts/news/__init__.py` *(if not present after the move sweep)*
- `tests/unit/agents/analysts/fundamental/__init__.py` *(ditto)*

**7. Fix the genuine duplicate-name bug.**

(`layout-and-fixtures.md` P2-09 — one true bug)

In `tests/unit/observability/test_terminal_log.py`:

- Method at line 71, inside `TestFormatTokens`, named
  `test_output_always_six_chars` — the real test that exercises the
  six-character formatter for token-count rendering. Keep as-is.
- Method at line 102, inside `TestFormatLatency`, also named
  `test_output_always_six_chars` — the second definition silently
  overrides the first only when both live on the same class; here
  they're on different classes so pytest collects both but the name
  collides under `pytest -k`. Rename to
  `test_output_always_six_chars_for_latency` so `-k` selection is
  deterministic and matches the latency class's intent.

**8. Delete dead conftest helpers.**

(`layout-and-fixtures.md` P2-04, P2-05)

In `tests/conftest.py` — delete the `fixture_path` fixture (lines 27-37)
and the `load_fixture` fixture (lines 39-44). Verify zero callers across
the *post-move* suite (`grep -rn "load_fixture\|fixture_path" tests/`
should return nothing inside test bodies).

In `tests/integration/conftest.py` — delete the `cache_root` fixture
and the module-level `make_ctx` factory (lines 16-104 per the audit).
Verify zero callers (`grep -rn "cache_root\|make_ctx" tests/` should
return only the local `_make_ctx` definitions in
`tests/integration/test_*.py`, which are independent of the deleted
factory).

If `tests/integration/conftest.py` ends up empty after the deletions,
delete the file.

**9. Scope down `_clear_analysts_config_cache` autouse.**

(`layout-and-fixtures.md` P2-06)

Move the autouse fixture from `tests/conftest.py:14-24` into a new
`tests/unit/agents/analysts/conftest.py` and a copy in
`tests/integration/conftest.py` (re-creating the file if it was
deleted in step 8). Both copies remain `autouse=True` but scoped to
their subtree.

**10. Update `docs/test-policy.md` if any imports change.**

`§B` and `§D` cite specific paths
(`tests/unit/agents/news/test_fetch.py`, `tests/fixtures/`). After
the moves these citations remain accurate. If `load_fixture` is
deleted (step 8), remove the "loaded via the `load_fixture` fixture"
sentence from §D and replace with: "JSON fixtures live in
`tests/fixtures/`; tests load them via
`Path(__file__).parent.parent / 'fixtures' / 'foo.json'` or an
equivalent absolute-path read."

### Out of scope

- Any semantic test change (new assertions, inverted assertions,
  added markers, deleted tests, content edits).
- Marker additions (`pytestmark`, `@pytest.mark.slow`, etc.) —
  owned by **T-F11**.
- SmartMoney test deletions — owned by **T-F07** (runs against the
  *post-move* paths; this PR moves the files even though some will
  be deleted in T-F07).
- Unused-domain test deletions — owned by **T-F08**.
- The 28 residual cross-domain duplicate test-function names
  (`test_extracts_required_keys` × 4, etc.) — `layout-and-fixtures.md`
  P2-09 notes most are resolved *by* the moves themselves; the residual
  rename pass rides with whichever subsequent PR touches each file.
- Deleting the 32 empty `__init__.py` files (P2-10) — defer, since
  pytest collection is sensitive and a sweep is safer once all moves
  have landed.
- Consolidating `tests/backtest/` into `tests/unit/backtest/`
  (P2-11) — backtest-test audit owns this disposition; this sweep
  leaves it alone.
- Moving `tests/fixtures/position_thesis_v1.json` (P2-12) — coordinate
  with strategist source fix (T-F05).

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `layout-and-fixtures.md` P1-01 | 65 loose `tests/unit/*.py` | Move into mirror tree |
| `layout-and-fixtures.md` P1-02 | analyst test trees ×4 | Collapse to `tests/unit/agents/analysts/` |
| `layout-and-fixtures.md` P2-01 | executor test trees ×3 | Collapse to `tests/unit/agents/executor/` |
| `layout-and-fixtures.md` P2-02 | `tests/contract/` ↔ `tests/unit/contract/` | Move evidence-schema test inward; keep boundary tests in `tests/contract/` |
| `layout-and-fixtures.md` P2-03 | `tests/orchestrator/` | Collapse single file into `tests/unit/orchestrator/` |
| `layout-and-fixtures.md` P2-04 | `tests/conftest.py:27-44` | Delete `load_fixture` / `fixture_path` (dead) |
| `layout-and-fixtures.md` P2-05 | `tests/integration/conftest.py:16-104` | Delete `cache_root` / `make_ctx` (dead) |
| `layout-and-fixtures.md` P2-06 | `tests/conftest.py:14-24` | Scope `_clear_analysts_config_cache` to analyst subtrees |
| `layout-and-fixtures.md` P2-07 | `tests/agents/` | Three-tree analyst sprawl — fold into mirror |
| `layout-and-fixtures.md` P2-08 | `tests/analysts/{news,fundamental}/` | Mirror-image scaffolding moved to canonical |
| `layout-and-fixtures.md` P2-09 (the one true bug) | `tests/unit/observability/test_terminal_log.py:102` | Rename `test_output_always_six_chars` → `test_output_always_six_chars_for_latency` |
| `analysts-deterministic.md` P2-01 (test-side) | analyst test trees | Collapse — same as P1-02 above |

## Implementation steps

1. Create new mirror directories with empty `__init__.py`:
   - `tests/unit/agents/memory/`, `tests/unit/agents/risk_gate/`,
     `tests/unit/broker/`, `tests/unit/lifecycle/`,
     `tests/unit/scripts/`,
     `tests/unit/agents/analysts/{smart_money,social,technical,news,fundamental}/`.
2. `git mv` the 65 loose `tests/unit/*.py` files per the table above.
3. `git mv` the four `tests/agents/` files, the five `tests/analysts/`
   root files, the six `tests/analysts/{news,fundamental}/` files,
   and `tests/orchestrator/test_pipeline_build.py` per the tables
   above.
4. `git mv` the three executor relocations.
5. `git mv tests/contract/test_evidence_schema.py
   tests/unit/contract/test_evidence_schema.py`.
6. Delete empty parent directories: `tests/agents/{analysts,memory}/`,
   `tests/agents/`, `tests/analysts/{news,fundamental}/`,
   `tests/analysts/`, `tests/executor/`, `tests/unit/executor/`,
   `tests/orchestrator/`.
7. Edit `tests/unit/observability/test_terminal_log.py` line 102: rename
   `def test_output_always_six_chars(self):` →
   `def test_output_always_six_chars_for_latency(self):`. No assertions
   change.
8. Edit `tests/conftest.py`: delete the `fixture_path` and
   `load_fixture` fixture functions plus the now-unused `json` and
   `FIXTURES` module-level constants if they have no surviving callers.
   Keep the `_clear_analysts_config_cache` skeleton temporarily — see
   step 10.
9. Edit `tests/integration/conftest.py`: delete `cache_root` and
   `make_ctx`. If the file is now empty (only imports), delete it.
10. Move `_clear_analysts_config_cache` out of root `tests/conftest.py`:
    - Create `tests/unit/agents/analysts/conftest.py` with the
      autouse fixture (preserve docstring and clear-on-yield-and-after
      shape).
    - Re-create `tests/integration/conftest.py` (or extend it if it
      survived step 9) with a copy of the same fixture, also
      `autouse=True`.
    - Delete the fixture from the root conftest. Root conftest after
      this PR should only contain shared helpers that genuinely need
      suite-wide scope (today: none).
11. Patch `import` statements only where a relative path breaks
    (none expected — `pytest.ini` sets `pythonpath = . src`, so
    test modules import from `src/`, not from each other).
12. Run `pytest tests/ --collect-only -q | wc -l` before and after; the
    count must be identical (one duplicate-name rename does not add
    or remove tests).
13. Run the full suite once to confirm green.
14. Update `docs/test-policy.md` §D if the `load_fixture` deletion
    requires it (per the "Out of scope" carve-out above).
15. Append a `graph_delta.md` entry under `graphify-out/` per the
    project's structural-change convention.

## Acceptance criteria

- [ ] `pytest tests/ --collect-only -q | wc -l` produces the **same
  count** before and after the PR (this is the load-bearing acceptance
  check).
- [ ] Full `pytest tests/ -v` green.
- [ ] `.venv/bin/python -m ruff check src/ tests/ scripts/` clean.
- [ ] `tests/agents/`, `tests/analysts/`, `tests/executor/`,
  `tests/unit/executor/`, `tests/orchestrator/` no longer exist.
- [ ] No `tests/unit/test_*.py` file at root that ought to mirror an
  `src/` path (verify with `ls tests/unit/test_*.py` returning
  nothing).
- [ ] `pytest -k test_output_always_six_chars` selects exactly two
  tests (one in `TestFormatTokens`, one renamed to
  `test_output_always_six_chars_for_latency` in `TestFormatLatency`).
- [ ] `grep -rn "load_fixture\|fixture_path\|cache_root\|make_ctx" tests/`
  returns no fixture *definitions*. Local `_make_ctx` helpers inside
  test bodies are fine.
- [ ] `graphify-out/graph_delta.md` has an entry dated today
  describing the structural moves.
- [ ] No new audit findings introduced (self-audit against the test-audit
  RUBRIC layout rule).

## Verification commands

```bash
.venv/bin/python -m pytest tests/ --collect-only -q | wc -l
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/ scripts/
```

## Risks and rollbacks

- **Risk:** a `git mv` may leave a stale `__pycache__/` referencing
  the old path, causing local collection oddities. Mitigation:
  the dispatcher should run `find tests/ -name __pycache__ -type d -exec rm -rf {} +`
  inside the worktree before the verification commands.
- **Risk:** the autouse `_clear_analysts_config_cache` move leaves a
  hidden import that another conftest depends on. Mitigation: run the
  full suite once with `-p no:cacheprovider` to confirm collection
  works without any cached layout.
- **Risk:** `tests/integration/test_strategist_v2_smoke.py` is the only
  file currently carrying `pytestmark = pytest.mark.integration` and
  could clash with the conftest scope-down. Mitigation: T-F11 owns
  marker discipline; this PR does not change any marker.
- **Rollback:** the branch can be discarded. `main` is untouched
  until merge. Because every change is a `git mv` plus the rename
  fix plus deletions, `git revert` cleanly undoes the PR.

## Subagent dispatch prompt sketch

> Work on branch `fix/T-F10-layout-sweep` in a git worktree. Read
> `docs/Phase11-project-audit/fix-plan/T-F10-layout-sweep.md` end-to-end, then read
> `docs/Phase11-project-audit/test-audit/layout-and-fixtures.md` for context. Perform every
> move exactly as listed — no semantic test edits, no marker changes,
> no test deletions. Run `pytest tests/ --collect-only -q | wc -l`
> before any change and again at the end; both numbers must match.
> Then run `pytest tests/ -v` and `ruff check src/ tests/ scripts/`.
> Append a dated entry to `graphify-out/graph_delta.md` summarising
> the structural moves. Commit as `fix(tests): collapse parallel
> mirror trees and move loose unit tests into source-mirror layout`
> with finding IDs in the body. Push and open the PR. **Do not skip
> hooks. Do not amend.**
