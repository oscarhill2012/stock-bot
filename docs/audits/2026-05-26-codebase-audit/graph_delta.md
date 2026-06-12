# Graph delta

Structural changes (module additions, symbol deletions, renames, and
re-homings) made while remediating the 2026-05-26 codebase audit. One dated
entry per plan. The intent is to keep a running record of how the
module/symbol graph shifted, so a later audit can diff against the snapshot
the original findings were written against.

---

## 2026-06-12 — plan-12 (P3 tail sweep)

Branch `plan-12-p3-sweep`. Twenty-eight commits over `main..HEAD`. The plan's
governing rule was **"no structural changes — this is a tail sweep"**, so the
graph delta is deliberately small: one sanctioned new helper module, a set of
dead-symbol deletions, two inlinings, one rename, and one constant re-homing.

### Modules added

- `src/data/providers/politician_trades/_common.py` — extracts the
  byte-identical helpers (`_SIDE_MAP`, `_coerce_side`, `_parse_date`,
  `_parse_amount_range`) shared by the `fmp` and `quiver` providers
  (A-097.m / F-data-011). **Both providers remain registered** — the
  one-config-flip provider swap is preserved; only the duplicated helper
  bodies were consolidated.

This is the *only* new `src/` module in the branch.

### Modules deleted

- `src/contract/digest_defaults.py` — single-dict module folded into
  `src/contract/digest.py`; the constants `DEFAULT_ANALYST_WEIGHTS` and
  `DIRECTION_DEAD_ZONE` now live there (A-097.v / F-contract-012). All
  importers updated to `from contract.digest import ...`.

### Symbols deleted (dead code)

- `observability.otel_setup.get_handles` (+ its two tests) — A-097.c / F-ops-004.
- `"stockbot"` entry removed from `otel_setup.captured_namespaces` — A-097.e / F-ops-008.
- `orchestrator.tick.TickState` (+ its import + `test_tick_state.py`) — A-087 / F-orch-005.
- `BUFFER_MAX` — A-092 / F-agents-misc-011.
- `contract.strategist_prompt.render_all_ticker_blocks` — inlined into its
  single caller `agents.strategist.context_shim` (A-097.w / F-contract-014).
- `orchestrator.pipeline._build_memory_writer` — inlined at its single call
  site (A-097.p / F-orch-006).
- The `importlib.util` filesystem-loader block in
  `agents.analysts.report_cache`, plus the eager `from .agent import …`
  re-exports from `agents/analysts/news/__init__.py` and
  `…/fundamental/__init__.py`. This broke the report_cache import cycle
  (A-096 / F-analysts-014). Verified no caller used the package-root symbols —
  every importer uses the fully-qualified `…news.agent` / `…fundamental.agent`
  path.
- Stale "Band 4 will wire the Executor writer-of-record" forward-intent comment
  in `backtest/runner.py` (A-097.s / F-backtest-015). The accurate `(Band 4)`
  attribution label in `backtest/driver.py` was deliberately kept.

### Symbols renamed

- `observability.trace._trace_maybe` → `observability.trace.trace_maybe`
  (public). Updated across all 15 consumers; no back-compat alias left behind
  (A-097.o / F-risk_gate-007).

### Symbols added

- `orchestrator.tick._resolve_broker_mode` — raises `ValueError` on an unknown
  broker mode instead of silently coercing to PAPER (A-089 / F-orch-012).
- Tests: `tests/unit/config/test_models_config.py`,
  `tests/unit/config/test_retry_429_config.py` (A-097.f / F-ops-009), plus
  verdict/evidence consistency assertions added to the existing news and
  fundamental joiner tests (A-097.z / F-analysts-016).

### Deliberately kept (not deleted)

- `baselines.spy._metrics_from_series` / `SPYMetrics` — guarded by
  `tests/unit/baselines/test_spy_metrics_removed.py`; the public `spy_metrics`
  was already removed in Phase 7 (A-097.d).
- `agents.strategist.derivation.DerivedFields.decision_tags` — dormant
  scaffolding for the planned Spec B/C memory-writer path; only the misleading
  docstring was corrected (A-097.aa / F-strategist-006).
- `lifecycle.scheduler` Cloud Scheduler shells — keep-decision per Human gate
  §8.5; recorded in the module docstring (A-090).
- Both `politician_trades` providers (see _common.py above).

### Deferred (no graph change)

- A-093 (triple structured-log emission) — field sets diverge; a shared emitter
  would be lossy (plan-12 §3.2).
- A-097.t (reporting N/A-by-string) — intentional `metrics.md` output from the
  rf-adjusted Sharpe/IR feature (commit 91a97e5).
- A-097.x (T212 PAPER/LIVE URL smoke tests) — out of scope per §3.3; deferral
  recorded in `test-strategy.md`.

See `FINDINGS.md` for the per-ID status ledger (including the full A-097 a–aa
disposition table) and `plans/plan-12-p3-sweep.md` for the plan itself.
