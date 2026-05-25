# T-F04 — Live-only correctness bombs

**Wave:** 3
**Pairs source-audit fix:** F3
**Branch:** `fix/T-F04-live-only-bombs`
**Depends on:** none
**Estimated diff size:** medium

## Scope

Four latent correctness bugs are masked by the bot's pre-deployment
state. Tests pass; the first live tick detonates each one. This PR
fixes the live-only bombs in lock-step with the tests that defend
them, so neither side lands red. The four sites:

1. `Trading212Broker` awaits the **synchronous** `httpx.Response.json()`
   — every `submit_market`/`get_portfolio`/`position_size` call would
   `TypeError` on the first real HTTP response. Defended by
   `AsyncMock` shape in the existing unit suite.
2. `Trading212Broker.get_portfolio` silently `continue`s past
   unknown-instrument codes. Combined with the caller-side
   `instrument_map={}` wiring, live `get_portfolio` returns cash-only
   with zero positions and no warning. Zero test coverage today.
3. The Snapshotter swallows every SPY-fetch exception into
   `spy_price = 0.0`, flat-lining the equity curve while the
   pipeline-completion check still passes. The existing integration
   test actively codifies this as desired behaviour.
4. The live `run_once` path writes a raw `datetime` into
   `create_session(state=...)`; `DatabaseSessionService` cannot
   JSON-serialise it. The backtest driver already coerces this; the
   live path was never given the same fix. Zero regression test.

### In scope

- **Source — `src/broker/trading212.py` (P1-01):**
  - Lines `:58, :77, :92, :100` — drop the
    `data = await resp.json() if callable(...) else resp.json()`
    conditional and call `resp.json()` directly (sync). The
    `callable(...)` probe is dead since `httpx.Response.json` is
    always callable; the conditional was a test-shape artefact.
- **Source — `src/broker/trading212.py` (P1-02):**
  - Lines `:104-113` (`get_portfolio` loop) — replace the silent
    `if code not in rev: continue` with a loud surfacing path.
    Default to **raising** `BrokerRejection` (or a dedicated
    `UnknownInstrumentError`) naming the offending code, since a
    live `get_portfolio` returning a partial portfolio is a
    correctness bug downstream of RiskGate / Strategist / Snapshotter.
    Fall-back option if the dispatcher prefers a soft signal:
    `logger.warning(...)` with `kind="unknown_instrument"` plus a
    counter that surfaces post-call. **Recommended path: raise.**
- **Source — `src/agents/snapshot/agent.py` (P0-01):**
  - Lines `:60-74` — narrow the `except Exception:` swallow. Two
    acceptable shapes:
    - **Preferred:** drop the catch entirely and let the exception
      propagate. The driver's pipeline-completion guard at
      `src/backtest/driver.py:608` already handles mid-tick blow-ups.
    - **Acceptable:** narrow to a specific provider/timeout
      exception set, set `spy_price = None`, and have
      `save_portfolio_snapshot` reject a `None` row loudly. The
      key invariant: no path produces `spy_price = 0.0`.
  - Whichever shape the subagent picks, the result must be that a
    SPY-fetch failure surfaces (raise or loud-log + reject), never
    silently flat-lines the equity curve.
- **Source — `src/orchestrator/tick.py` (P0-03 / P0-02):**
  - Line `:148` — coerce the `as_of` `datetime` to an ISO string
    before it enters `_build_initial_state`'s output, mirroring the
    backtest driver at `src/backtest/driver.py:494-499`. Use the
    existing `resolve_as_of` helper (per the user memory
    `feedback_as_of_boundary_coercion`) — every datetime write to
    state must ISO-stringify first.
  - Verify line `:242-247` (`create_session(state=initial_state)`) is
    happy with the coerced dict. If the subagent prefers, extract a
    shared `_seed_state_for_adk(state)` helper in `src/orchestrator/`
    so both lifecycles (live + backtest) call the same coercion.
- **Tests — `tests/unit/test_trading212_request_construction.py` (broker P0-01):**
  - Lines `:11-17` and `:37-41` — replace the `AsyncMock()` response
    object with a real-shape mock:
    `Mock(json=Mock(return_value={...}), raise_for_status=Mock(return_value=None))`.
    `client.post` remains `AsyncMock`; the **response** is sync.
    Add an explicit assertion that `resp.json` was called without
    being awaited (e.g. `resp.json.assert_called_once()` on the sync
    mock).
- **Tests — `tests/integration/test_snapshotter.py` (agents-misc P0-01 / P0-03 / P0-04):**
  - Invert `test_snapshotter_accepts_iso_string_as_of`: instead of
    asserting silent degrade to `spy_price=0.0` on
    `get_price_history` raising, assert the exception **propagates**
    (or, if the source picks the loud-log shape, assert
    `caplog` records a `kind="spy_fetch_failed"` WARNING and that
    no snapshot row was written / `spy_price is None`).
  - Reshape the snapshotter mocks: replace `patch("yfinance.Ticker", ...)`
    and `sys.modules["yfinance"] = ...` with
    `monkeypatch.setattr("data.get_price_history",
    AsyncMock(return_value=PriceHistory(bars=[Bar(close=470.0, ...)])))`.
    This pins the leaf seam and aligns the test surface with the
    production code path.
  - Add a happy-path test that fixes `bars[-1].close = 470.0` and
    asserts `snap["spy_price"] == 470.0` (positive content
    assertion, not just completion).
  - Tests P0-01, P0-03, P0-04 in agents-misc.md all close with these
    three rewrites.
- **Tests — new — `tests/unit/broker/test_trading212_get_portfolio.py`
  (broker P1-01 test-side):**
  - Add `test_get_portfolio_raises_on_unknown_instrument_code` (or
    `_warns_` depending on the source-fix shape). Construct
    `Trading212Broker(instrument_map={"AAPL": "AAPL_US_EQ"})`, mock
    `/portfolio` to return positions for `AAPL_US_EQ` and
    `UNKNOWN_XX_EQ`, and assert the surfacing behaviour the source
    chose (raise or WARNING-via-`caplog`).
  - Add `test_get_portfolio_happy_path_known_codes_survive`: known
    codes pass the reverse-map lookup and appear in
    `Portfolio.positions` with correct quantity/price.
- **Tests — new — `tests/unit/orchestrator/test_tick_initial_state_json_safe.py`
  (orchestrator P0-02 test-side):**
  - `test_initial_state_json_serialisable`:
    `json.dumps(_build_initial_state(...))` succeeds without raising
    `TypeError: Object of type datetime is not JSON serializable`.
  - `test_initial_state_survives_create_session_round_trip`: build
    a `DatabaseSessionService` against an in-memory sqlite URL, call
    `await svc.create_session(app_name="StockBot-test",
    user_id="stockbot", state=_build_initial_state(...))`, and
    assert it returns without raising. Then fetch the session back
    and assert `state["as_of"]` is a string (ISO-shaped).

### Out of scope

- Broker P2-01 (`position_size` dead method) and P2-02 (matching
  docstrings) — separate cleanup PR.
- Snapshotter P0-02 (cold-start anchors not in §A) — different fix
  pattern; defer to a dedicated PR (likely paired with Spec C
  hydration work).
- Snapshotter P1-01..P1-04 (broker/price-provider read inside the
  pipeline, MemoryWriter cross-tick reads, etc.) — defer.
- Orchestrator P0-01 (`memory_buffer`/`day_digest` empty seed) — Spec C
  deferred.
- Orchestrator P0-02 (BaseException swallow) — large-blast change
  with its own missing-test gap; defer to a dedicated PR with the
  three regression tests sketched in orchestrator test P0-01.
- Orchestrator P1-01..P1-04 (Rule 7 carve-out, `_fetch_reference_prices`
  registry bypass, `TickState`, `_dispatch_app_name` demotion) — defer.
- Layout polish (broker test files moving to `tests/unit/broker/`,
  test P2-01 / P2-02 / P2-03) — defer to T-F10 if not landed there,
  else to a follow-up cleanup PR.
- `caller-side` `instrument_map={}` wiring in `src/orchestrator/tick.py`
  and `scripts/` — broker P1-02 cross-subsystem note. The loud
  surfacing in this PR will make those calls fail loudly, which is
  the intended forcing function for the wiring fix — but the wiring
  fix itself belongs to a separate PR (likely a future `scripts/`
  audit follow-up).

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `broker.md` source P1-01 | `src/broker/trading212.py:58,77,92,100` | Drop `await resp.json()` conditional; call sync `resp.json()` directly. |
| `broker.md` source P1-02 | `src/broker/trading212.py:104-113` | Raise (or loud-log) on unknown instrument codes in `get_portfolio`. |
| `agents-misc.md` source P0-01 | `src/agents/snapshot/agent.py:60-74` | Drop or narrow SPY-fetch swallow; never produce `spy_price = 0.0`. |
| `orchestrator.md` source P0-03 | `src/orchestrator/tick.py:148` | ISO-coerce `as_of` before `create_session` (existing `resolve_as_of` helper). |
| `broker.md` test P0-01 | `tests/unit/test_trading212_request_construction.py:11-17,37-41` | Replace `AsyncMock` response with real-shape sync `Mock(json=Mock(return_value=...))`. |
| `broker.md` test P1-01 | `tests/unit/broker/test_trading212_get_portfolio.py` (new) | Add `get_portfolio` coverage for known + unknown codes. |
| `agents-misc.md` test P0-01 | `tests/integration/test_snapshotter.py` | Invert SPY-swallow defending assertion; assert surfacing instead. |
| `agents-misc.md` test P0-03 | `tests/integration/test_snapshotter.py:31-37` | Reshape mock to `monkeypatch.setattr("data.get_price_history", ...)` (leaf seam). |
| `agents-misc.md` test P0-04 | `tests/unit/backtest/test_wall_clock_leakage.py:128-135` | Replace `sys.modules["yfinance"]` injection with leaf-seam monkeypatch. |
| `orchestrator.md` test P0-02 | `tests/unit/orchestrator/test_tick_initial_state_json_safe.py` (new) | Add `json.dumps` + real `DatabaseSessionService` round-trip tests. |

## Implementation steps

1. **Read the four audit reports** in full first:
   `docs/Phase11-project-audit/source-audit/broker.md`, `docs/Phase11-project-audit/source-audit/agents-misc.md`,
   `docs/Phase11-project-audit/source-audit/orchestrator.md`,
   `docs/Phase11-project-audit/test-audit/broker.md`, `docs/Phase11-project-audit/test-audit/agents-misc.md`,
   `docs/Phase11-project-audit/test-audit/orchestrator.md`.
2. **Broker P1-01 — `resp.json()` fix.**
   - Edit `src/broker/trading212.py:58, 77, 92, 100`. Replace each
     `data = await resp.json() if callable(...) else resp.json()`
     with `data = resp.json()`. Confirm no other site in the file
     uses the conditional.
   - Verify by grep that no other code path expects the awaited form.
3. **Broker P1-02 — `get_portfolio` unknown-code surfacing.**
   - Edit `src/broker/trading212.py:104-113`. Replace the silent
     `continue` with `raise BrokerRejection(f"Unknown T212 instrument
     code: {code}; instrument_map is incomplete")` (preferred), or
     `logger.warning("unknown_instrument_code", extra={"code": code,
     "kind": "unknown_instrument"})` + accumulate a `_skipped_codes`
     list on the broker for post-call inspection.
   - Decision: **recommend raise** — the silent skip is the worst
     possible failure mode for live trading. The dispatcher can
     override if there's a stronger argument for warn-only.
4. **Snapshotter P0-01 — drop SPY-fetch swallow.**
   - Edit `src/agents/snapshot/agent.py:60-74`. Preferred shape:
     remove the `try / except Exception: spy_price = 0.0` wrapper
     entirely. Let exceptions propagate; the driver's pipeline
     guard handles them. Fall-back shape: narrow to
     `(ProviderError, asyncio.TimeoutError)`, set `spy_price = None`,
     have `save_portfolio_snapshot` reject `None` loudly.
   - Verify no other site in the snapshotter masks the same failure.
5. **Orchestrator P0-03 — `datetime` coercion at `create_session`.**
   - Locate the `resolve_as_of` helper. Memory entry
     `feedback_as_of_boundary_coercion`: every datetime write to
     state must use `resolve_as_of` + ISO-stringify before write.
     Grep `src/` for `resolve_as_of` to find the canonical helper.
   - Edit `src/orchestrator/tick.py:148`. Coerce the `as_of`
     `datetime` to an ISO string at the write site, mirroring
     `src/backtest/driver.py:494-499`.
   - **Strongly recommended:** extract a shared
     `_seed_state_for_adk(state)` helper at
     `src/orchestrator/persistence.py` (or a new
     `src/orchestrator/_state_coercion.py`) and call it from both
     `tick.py:148` and `backtest/driver.py:494-499`. The user
     memory `feedback_as_of_boundary_coercion` calls this out as
     mandatory: "every read of `state["as_of"]` uses `resolve_as_of`,
     every datetime write to state ISO-stringifies first". One
     helper, two call sites.
6. **Broker test P0-01 — reshape `AsyncMock` responses to sync mocks.**
   - Edit `tests/unit/test_trading212_request_construction.py:11-17`
     and `:37-41`. Replace `client.post.return_value.json = AsyncMock(...)`
     with `client.post.return_value = Mock(json=Mock(return_value={...}),
     raise_for_status=Mock(return_value=None))`. `client.post`
     remains `AsyncMock` (the *method* is async, the *response* is
     not).
   - Add `resp.json.assert_called_once()` to each happy-path test
     to lock in the "called without await" contract.
7. **Broker test P1-01 — new `get_portfolio` coverage.**
   - Decide: do these tests live in
     `tests/unit/broker/test_trading212_get_portfolio.py` (preferred,
     post-T-F10 layout) or alongside the existing
     `test_trading212_request_construction.py`? **Recommend new
     file** — keeps the `get_portfolio` surface separate from request
     construction.
   - Write `test_get_portfolio_happy_path_known_codes_survive`:
     mock the T212 `/portfolio` endpoint to return one position;
     assert the returned `Portfolio` contains it with correct
     quantity and price.
   - Write `test_get_portfolio_raises_on_unknown_instrument_code`
     (or `_warns_` — match the source-fix shape from step 3): mock
     the endpoint to return one known + one unknown code; assert
     the chosen surfacing fires (`pytest.raises(BrokerRejection,
     match="UNKNOWN_XX_EQ")` or `caplog` records the WARNING).
8. **Snapshotter tests P0-01 / P0-03 / P0-04 — invert + reshape.**
   - Edit `tests/integration/test_snapshotter.py`. The existing
     `test_snapshotter_accepts_iso_string_as_of` actively asserts
     silent degrade — rewrite it to assert surfacing per the
     source-fix shape from step 4. Rename to
     `test_snapshotter_raises_on_spy_fetch_failure` (or `_logs_loudly_`).
   - Reshape the snapshotter mocks in the same file: replace
     `patch("yfinance.Ticker", ...)` with
     `monkeypatch.setattr("data.get_price_history",
     AsyncMock(return_value=PriceHistory(bars=[Bar(close=470.0,
     ...)])))`. The mock target is the leaf seam used by the
     production code, not the implementation detail.
   - Add `test_snapshotter_records_spy_price_on_happy_path`: assert
     `snap["spy_price"] == 470.0` (positive content).
   - Edit `tests/unit/backtest/test_wall_clock_leakage.py:128-135`.
     Replace `sys.modules["yfinance"] = fake_yf` (no teardown,
     test-policy violation) with
     `monkeypatch.setattr("data.get_price_history",
     AsyncMock(return_value=...))`. Automatic teardown.
9. **Orchestrator test P0-02 — new JSON-safety regression file.**
   - Create `tests/unit/orchestrator/test_tick_initial_state_json_safe.py`.
   - Test 1: `test_initial_state_json_serialisable` —
     `json.dumps(_build_initial_state(...))` succeeds.
   - Test 2: `test_initial_state_survives_create_session_round_trip`
     — build `DatabaseSessionService` against `sqlite:///:memory:`,
     call `await svc.create_session(...)`, fetch back, assert
     `state["as_of"]` is a string in ISO-8601 format.
10. **Run the suite and verify.**
    - `.venv/bin/python -m pytest tests/ -v`
    - Confirm the four source fixes don't cascade other failures.
    - Confirm the new regression tests fail when the corresponding
      source fix is reverted on a scratch branch (sanity check —
      optional but recommended).
11. **Self-audit against the rubric.** New silent-failure surfaces?
    New mock-at-wrong-level? New completion-only assertions? Run
    `ruff check src/` clean.
12. **Append graphify delta entry** if new files were added
    (`test_trading212_get_portfolio.py`,
    `test_tick_initial_state_json_safe.py`, possibly
    `_state_coercion.py`).

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] All ten findings in the table above closed (cite by ID in
  commit body).
- [ ] No new silent-failure attractors introduced — particularly in
  the broker `get_portfolio` surfacing path and the snapshotter
  exception handling.
- [ ] The two new test files exist, are mirror-aligned, and contain
  positive-content assertions (not completion-only).
- [ ] `resolve_as_of` is used at the `tick.py:148` write site (or
  via the shared `_seed_state_for_adk` helper) — verify in the diff.
- [ ] The four `AsyncMock` response shapes in the broker request
  tests are replaced with real-shape sync `Mock(json=Mock(...))`.
- [ ] Graphify delta entry appended if structural changes.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
.venv/bin/python -m pytest tests/unit/broker/ tests/integration/test_snapshotter.py tests/unit/orchestrator/test_tick_initial_state_json_safe.py -v
```

## Risks and rollbacks

- **Risk — snapshotter exception propagation cascades:** if the
  driver's pipeline guard doesn't actually handle the propagated
  SPY exception cleanly, multiple downstream tests fail. Mitigation:
  pick the loud-log + reject-on-`None` fallback shape and surface
  the failure via `save_portfolio_snapshot` rejection rather than
  raw exception propagation, if the propagation route is too
  disruptive.
- **Risk — broker `get_portfolio` raise breaks the cross-subsystem
  caller-side `instrument_map={}` wiring:** by design — that's the
  forcing function. But if it breaks any pre-existing test that
  passes `instrument_map={}` and calls `get_portfolio` (grep
  `tests/` to find them), those tests will need their own minimal
  instrument_map. Mitigation: grep first, list the affected tests
  in the PR description, and either patch them in-pass or escalate
  to the dispatcher.
- **Risk — `resolve_as_of` shared helper has a different signature
  than the inline coercion at `driver.py:494-499`:** verify the
  helper's signature before extracting `_seed_state_for_adk`. If
  the helpers diverge, the live and backtest lifecycles cannot
  share one coercion path — flag to the dispatcher.
- **Rollback:** feature branch discardable; no `main` impact until
  merge. Each of the four source fixes is independently reversible
  within the PR.

## Subagent dispatch prompt sketch

> Implement T-F04 from `docs/Phase11-project-audit/fix-plan/T-F04-live-only-bombs.md`.
> Read the six audit reports listed in the spec in full first — the
> findings are precise about file paths and line numbers.
>
> Four source-side fixes:
> (1) drop `await resp.json()` conditional in
> `src/broker/trading212.py`,
> (2) raise (preferred) or loud-log on unknown instrument codes in
> `get_portfolio`,
> (3) drop the SPY-fetch swallow in
> `src/agents/snapshot/agent.py:60-74` (preferred shape: remove the
> try/except entirely),
> (4) ISO-coerce `as_of` at `src/orchestrator/tick.py:148` using
> `resolve_as_of` — strongly prefer extracting a shared
> `_seed_state_for_adk` helper that both the live tick and the
> backtest driver call.
>
> Test-side rewrites in lock-step:
> reshape `AsyncMock` responses to sync `Mock`s in the broker
> request-construction tests; add the missing `get_portfolio`
> coverage; invert the snapshotter's SPY-swallow test (it currently
> asserts the bug as desired); reshape the snapshotter and
> wall-clock-leakage mocks at the leaf seam
> (`data.get_price_history`, not `yfinance.Ticker` or `sys.modules`);
> add the two new orchestrator JSON-safety regression tests.
>
> Full `.venv/bin/python -m pytest tests/` must pass green before
> commit. Shell convention: never prepend `cd ".../StockBot" && ...`
> to bash commands.
