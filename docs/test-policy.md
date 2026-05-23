# Test policy — the canonical rules

## Purpose

A single canonical reference describing what tests in this repository
*should* look like, where they live, what they may and may not do, and the
non-obvious gotchas a fresh test author would otherwise re-discover by
breaking the suite.

This document is **target-state**. The existing suite predates it and will
not satisfy every rule today; gaps are addressed by the test-audit
workstream rather than by silently relaxing the rules.

The audience is "someone (human or subagent) about to write or rewrite a
test". Read it before touching `tests/`.

---

## §A — Hard rules (non-negotiable)

1. **No real API keys, ever.** Tests must not read live secrets, must not
   make outbound calls to Alpha Vantage / Finnhub / FMP / Tiingo / OpenAI /
   Gemini / Anthropic / Trading 212 / yfinance / SEC EDGAR, and must not
   depend on the presence of any `*_API_KEY` env var. The single
   exception is `tests/contract/test_provider_shapes.py`, which is
   explicitly gated and skipped unless live providers are exercised
   deliberately.

2. **No real backtest cache writes.** Only the user populates the canonical
   golden-cache SQLite files under `<backtests_root>/<window>/store.sqlite`
   — those fetches consume rate-limited APIs and represent hours of
   wall-clock budget. Tests that need cache contents must build a *temporary*
   cache inside `tmp_path/` and seed it with synthetic rows. Tests must
   never read or write the live `backtests/` tree.

3. **Backtest-running tests use one tick on the baseline window.** Any test
   that ticks the full pipeline (driver, runner, end-to-end) must use
   `window_key="baseline-2025-09"` and cap execution at a single tick
   (`tick_limit=1` on `runner.run(...)`, or a hand-crafted single-element
   schedule for `driver.run(...)`). The SVB window is reserved for
   manual replay; LLM cost makes multi-tick test runs unaffordable.

4. **LLM calls require opt-in.** Tests that legitimately exercise a real
   LLM must be gated behind `RUN_LLM_TESTS=1` and marked `@pytest.mark.integration`.
   The default `pytest` invocation must not call any LLM.

5. **Stub at the leaf HTTP boundary, not above it.** Every provider has a
   leaf function — `_fetch_company_news`, `_fetch_price_history`,
   `_fetch_xbrl_facts`, `_list_filings`, etc. — that is the seam where
   raw I/O happens. Monkeypatch at that seam; do not stub Pydantic models,
   `data.providers.registry`, or `CachedDataStore` to fake behaviour
   higher up. Higher-up stubs lose the type-checking and contract
   guarantees that the real code path relies on.

6. **Tests own their state.** No test may mutate the live `config/` tree,
   the live `backtests/` tree, the user's home directory, or any module-level
   global that another test reads. Use `tmp_path`, `monkeypatch.setenv`,
   `monkeypatch.chdir`, and `monkeypatch.setattr` so cleanup is automatic.

7. **Tests must surface silent failures loudly.** A passing test is not
   evidence of correct behaviour — only evidence of absent exceptions.
   Subagent-written code, and graceful-degradation code generally, fails
   silently far more often than it raises: swallowed exceptions,
   `is_no_data=True` fallbacks, empty result lists, neutral-by-default
   verdicts, `branch_failed` warnings logged-but-not-propagated. Tests
   structured around "did it raise?" miss every one of those bugs.
   Each test must therefore:

   - **Assert on positive output state, not just on completion.** If the
     pipeline runs without raising but the verdict list is empty, the
     trace dir is empty, or the broker has zero orders, the test has
     verified nothing useful. Make the positive assertion explicit.
   - **Treat degradation paths as failures in happy-path tests.**
     `is_no_data=True`, empty news/insider/verdict lists, and `neutral`
     fallback verdicts must be asserted *against* in the happy path,
     and asserted *for* only in tests that deliberately exercise the
     degraded branch.
   - **Verify the logs the code claims to emit actually fire.** If the
     code says "we log on X", use `caplog` to assert the log line on
     the path that triggers X. Otherwise nothing prevents a future
     refactor from silently removing the log.
   - **Exercise the "everything went wrong" branches deliberately.** For
     every provider and agent, write at least one test that forces an
     upstream failure (raise from the stubbed leaf fetch, return
     malformed data, time out) and asserts the failure surfaces —
     not that the system returns `None` and keeps walking.

---

## §B — Test taxonomy

The five layers, what they cover, and the rule for where each lives.

| Layer | Location | Mocks at | Runs every commit? |
|---|---|---|---|
| **Unit** | `tests/unit/<module-mirror>/` | Function inputs only (no I/O, no monkeypatching beyond config caches) | Yes |
| **Integration** | `tests/integration/` | Leaf HTTP/edgar fetches; LLM optional (RUN_LLM_TESTS) | Yes (LLM-gated parts excluded) |
| **Contract** | `tests/contract/` | Verifies layer-boundary invariants — schemas, signatures, config sourcing — usually without execution | Yes |
| **Backtest** | `tests/backtest/` (cache + audit primitives) and `tests/integration/backtest/` (driver / runner / fetcher smoke) | Leaf provider fetches + `_fill_reference_ohlcv` no-op | Yes (subject to §A.3) |
| **Smoke** | `tests/integration/test_*_smoke.py` | Whole-pipeline wiring with all I/O stubbed | Yes |

Rules of thumb for choosing a layer:

- **Touches one function, no I/O** → Unit. Live under `tests/unit/` mirroring
  the source tree (e.g. `src/agents/news/fetch.py` → `tests/unit/agents/news/test_fetch.py`).
- **Wires two or more modules together** → Integration.
- **Asserts on a *signature* or *schema* rather than runtime values** → Contract.
- **Runs the backtest pipeline at any depth** → goes under
  `tests/integration/backtest/` and obeys §A.2 and §A.3.

---

## §C — Pytest markers

Defined in `pytest.ini`; apply at the test or module level.

| Marker | Apply when | Behaviour |
|---|---|---|
| `slow` | Test takes > 1 s, or builds a full provider set | Excluded from default run; opt in with `-m slow` |
| `integration` | Test wires multiple modules or stubs leaf I/O | Included by default; LLM-touching variants gate on `RUN_LLM_TESTS=1` |
| `contract` | Test asserts on a boundary invariant (schema, signature, config) | Included by default |
| `replay` | Long-running historical backtest exercising real data | Excluded; opt in with `-m replay` |

Backtest smoke tests almost always need `slow + integration`.

---

## §D — Conventions

**Naming.** `test_<thing>_<aspect>.py` for files, `test_<scenario>` for
functions. Describe the scenario, not the assertion ("returns_neutral_when_no_data"
beats "test_no_data").

**Fixtures.** Shared fixtures live in `tests/conftest.py` (root) or in a
nested `conftest.py` next to a focused subtree. JSON fixtures live in
`tests/fixtures/` and are loaded via the `load_fixture` fixture.

**Async tests.** Mark with `@pytest.mark.asyncio` (or rely on the
`asyncio_mode = auto` setting). Provider and pipeline code is async; tests
of it should be too.

**Time and money.** Hard-code timestamps (`datetime(2025, 9, 2, 13, 30, tzinfo=UTC)`)
rather than `datetime.now()`. Hard-code prices and shares. A test that
references "yesterday" is a test that breaks at midnight.

**Comments and style.** British English everywhere (`behaviour`, `organisation`,
`analyse`). Function docstrings are mandatory and describe purpose and
parameters. Comment non-obvious set-up.

---

## §E — Anti-patterns (don't do these)

- **Writing into the live cache.** Symlinking `tmp_path/store.sqlite` to a
  real backtests directory, or pointing `backtests_root` at the live tree.
  Both have happened; both deleted.

- **Mocking `CachedDataStore` or `data.providers.registry`.** The real
  store is cheap (SQLite in `tmp_path/`) and the registry is config-driven.
  Mocking either at the wrong level hides contract drift instead of catching it.

- **Trusting per-Runner settings overrides.** `generate_ticks` reads the
  global `BacktestSettings` singleton, not the `settings_override` passed
  to `Runner(...)`. Use `tick_limit=1` to cap; do not assume
  `ticks_per_day=["open"]` will be honoured.

- **Stubbing the wrong news provider.** The active news provider is read
  from `config/data.json` (currently `finnhub`). Stubbing Tiingo or Alpha
  Vantage when Finnhub is active produces a green test that exercises
  nothing.

- **Adding politician_trades expectations.** That domain is intentionally
  disabled in `scripts.backtest_fetch._build_provider_fns` (no free
  historical source). Tests must not stub it or assert on its rows.

- **Forgetting `_fill_reference_ohlcv`.** SPY + the 11 SPDR sector ETFs
  re-fetch every backfill call and bypass the per-(ticker, domain)
  `cache_runs` skip. Backfill-idempotency tests must monkeypatch
  `scripts.backtest_fetch._fill_reference_ohlcv` to a no-op coroutine.

- **Omitting the `report` block on a non-no-data `AnalystVerdict`.** The
  `AnalystVerdict._report_required_when_data_present` schema validator
  raises when `is_no_data=False` and no `report` is supplied. Mock
  verdicts in pipeline tests need `summary` + at least one `ReportDriver`.

- **Comparing against `datetime.now()`.** Tests that pass today and fail
  tomorrow have no value. Pin every clock-sensitive input.

- **Wide-scope `monkeypatch.setattr` on a class.** Patch the smallest
  thing — usually a single module-level leaf fetch — so the rest of the
  code path runs normally.

- **"It didn't raise, therefore it works."** Absence of an exception
  proves only the test did not crash. Without an explicit positive
  assertion on the output (verdict content, trace file contents, broker
  state, log line, persisted row), the test is decorative. This is the
  single biggest source of silent regressions in subagent-driven
  development — see §A.7.

- **Asserting only on counts, never on content.** `assert len(verdicts) == 3`
  passes when three empty-shell verdicts come back from three failed
  branches. Pair every length assertion with at least one content
  assertion (`assert verdicts[0].direction == "bullish"`,
  `assert not verdicts[0].is_no_data`).

---

## §F — Running the suite

```bash
# Default — all unit, integration (non-LLM), contract, backtest tests
.venv/bin/python -m pytest tests/ -v

# Opt in to slow tests
.venv/bin/python -m pytest tests/ -v -m slow

# Opt in to long historical replays
.venv/bin/python -m pytest tests/ -v -m replay

# Include LLM-touching integration tests
RUN_LLM_TESTS=1 .venv/bin/python -m pytest tests/integration/ -v

# Lint
.venv/bin/python -m ruff check src/ tests/ scripts/
```

`PYTHONPATH` is pre-configured via `pytest.ini` (`pythonpath = . src`) so
no manual prefix is needed for `pytest`; the rule only applies to direct
`python -m scripts.*` invocations.

---

## §G — Gotchas encoded by past incidents

These are the surprises real tests have hit. Read them before writing a
new test of the same shape; do not re-derive them painfully.

1. **NYSE open is 13:30 UTC in EDT, 14:30 UTC in EST.** Ticks scheduled
   off by an hour silently produce zero open-phase work.

2. **`scripts.backtest_fetch._main_async` reads `args.refetch_domain`.**
   Hand-built argparse `Namespace` objects must include
   `refetch_domain=[]`, or the call fails with `AttributeError`.

3. **`_fetch_price_history` signature.** The provider-leaf function takes
   `(symbol, period, interval, as_of=None)`. Stubs missing `as_of` raise
   on PIT-clamped calls.

4. **Finnhub article shape.** `datetime` is a Unix-epoch integer (not an
   ISO string), and the fields are `headline` / `summary` / `url` /
   `source` (not Tiingo's `publishedDate` / `description`).

5. **`BacktestSettings` schema.** Constructing one in a test requires
   every field: `backtests_root`, `ticks_per_day`, `failed_tick_abort_ratio`,
   `fake_broker_starting_cash`, `forward_return_horizons_days`,
   `ohlcv_warmup_days`. Mirror `config/backtest_settings.json`.

6. **`pre-deployment` state.** No paper or live instance is running. Tests
   must not assume infrastructure, environment, scheduled jobs, or
   running processes exist.

7. **`is_no_data=True` is a silent-failure attractor.** When a mock or
   fixture omits a field a downstream Pydantic validator requires (e.g.
   the `report` block on a non-no-data `AnalystVerdict`), the agent's
   `isolated_failure` wrapper converts the resulting `ValidationError`
   into a `branch_failed` warning and returns a synthetic no-data
   verdict. The surrounding test still passes — the pipeline ran end
   to end. Always assert `is_no_data=False` on happy-path verdicts so
   this trap fails the test instead of hiding inside it.

8. **`branch_failed` warnings are not benign.** The pipeline emits them
   on the swallow-and-continue path. Pipeline-level tests should
   `caplog.set_level(WARNING)` and assert no `branch_failed` record was
   emitted on the happy path, otherwise an analyst silently dying mid-run
   produces a green test.

---

## §H — How this document evolves

When a test surfaces a new structural lesson — a gotcha, an anti-pattern,
a non-obvious invariant — append it to §E or §G in the same PR. Do not
let the lesson live only in a commit message; commit messages rot, this
file is canonical. Conversely, when a §G entry no longer applies (because
the underlying code changed), delete the entry in the PR that changed
the code.

When a hard rule in §A must change, the change goes through the
brainstorming → spec → review loop, not a silent edit.
