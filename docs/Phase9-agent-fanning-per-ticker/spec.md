# Phase 9 — Per-ticker fan-out for LLM analysts

## Problem

Both LLM analysts — News and Fundamental — currently run **one batched
LLM call per tick** that covers every watchlist ticker in a single
`VerdictBatch`. Two symptoms motivated this redesign:

1. **Output-token overflow.** With `gemini-2.5-flash-lite`'s default
   8,192-token output budget and per-ticker schema caps allowing
   ~1,750 output tokens per ticker (2,000-char summary + 4×1,000-char
   driver bodies + rationale + key_factors + JSON overhead), any
   watchlist beyond ~4 tickers risks truncating the JSON mid-string. A
   backtest tick on 2026-02-11 crashed exactly this way ("EOF while
   parsing a string at line 5490 column 303"), taking the whole tick
   down.
2. **Signal dilution.** Asking Flash-Lite to reason about N tickers
   simultaneously degrades per-ticker focus. Each verdict is composed
   under context-pressure from every other ticker's data. The output
   crashes are the loud failure mode; quality dilution on the ticks
   that *do* succeed is the silent one.

A cheap fix existed (cap-diet the schema, raise `max_output_tokens`),
but it would have kept the underlying batched-cognition design
in-place. This phase fixes the cognition.

## Goals

- **Per-ticker focused LLM reasoning** for News and Fundamental — each
  LLM call sees exactly one ticker's data and emits exactly one
  verdict.
- **Crash resilience.** A single ticker's failure (persistent 429,
  malformed JSON, anything) must not abort the tick. The failing
  branch produces a synthetic no-data verdict and the tick proceeds.
- **Cache hit efficiency.** Today, a single cache miss forces a full
  N-ticker LLM call. After this phase, each ticker's branch
  short-circuits independently — N-1 cache hits and one miss = one
  LLM call.
- **Contract conformance.** The §A invariant table's canonical keys
  (`news_verdicts`, `fundamental_verdicts`) remain the
  tick-scoped contract handoff. Only the Owner column changes (per
  §A row maintenance below).

## Non-goals

- **Parallel concurrent LLM calls.** Explicitly rejected — Vertex DSQ
  rate-limit amplification, more concurrent state-write mechanics, no
  meaningful wall-clock benefit at watchlist sizes we expect.
- **Schema cap tightening.** Out of scope here; per-ticker output is
  bounded by definition (one verdict per call ≪ output budget). A
  separate cleanup pass on `report_summary_max_chars` / driver body
  caps can land later if we want even tighter outputs.
- **Touching the deterministic analysts** (Technical, Social,
  SmartMoney). They already iterate tickers internally and have no
  output-token concerns.
- **Cross-ticker reasoning recovery.** The batched prompt let the LLM
  notice relative leans across the watchlist (e.g. "MSFT beat, GOOG
  flat, AAPL guided down" → adjust each lean relatively). This phase
  trades that ability for per-ticker focus. If we later want
  cross-ticker context back, a second-pass aggregator agent is the
  natural place — explicitly out of scope here.

---

## Design overview

### Pipeline topology

Each LLM analyst becomes a `SequentialAgent` of three logical stages,
rebuilt every tick from the current watchlist:

```
NewsAnalystBranch (SequentialAgent):
├── NewsFetchAgent                    # BaseAgent — runs ONCE per tick.
│                                     # Fetches headlines for all tickers,
│                                     # yields state_delta with
│                                     # temp:news_data + news_context.
│
├── NewsLlmAgent_<TICKER_1>           # RetryingAgentWrapper(LlmAgent).
├── NewsLlmAgent_<TICKER_2>           # One LlmAgent per watchlist ticker.
├── ...                               # Each has:
├── NewsLlmAgent_<TICKER_N>           #   - output_schema=TickerVerdict
│                                     #   - output_key=temp:news_verdict_<TICKER>
│                                     #   - per-ticker cache before/after
│                                     #   - single-ticker prompt template
│
└── NewsJoinerAgent                   # BaseAgent — reads N
                                      # temp:news_verdict_<TICKER> keys,
                                      # builds news_verdicts (VerdictBatch)
                                      # AND news_evidence (list[AnalystEvidence]),
                                      # yields both via state_delta.
```

Same shape for Fundamental (`FundamentalAnalystBranch` with
`FundamentalFetchAgent`, N per-ticker `FundamentalLlmAgent_<TICKER>`
branches, and `FundamentalJoinerAgent`).

The outer pipeline is unchanged:

```
HourlyTick (SequentialAgent):
├── AnalystPool (SequentialAgent):    # outer pool composes the analyst branches
│   ├── DeterministicAnalysts (ParallelAgent[Technical, Social])
│   ├── FundamentalAnalystBranch       ← new shape (per-ticker fan-out)
│   └── NewsAnalystBranch              ← new shape (per-ticker fan-out)
├── EvidenceWriter
├── StrategistBranch
├── StrategistDecisionWriter
├── RiskGateAgent
├── Executor
├── MemoryWriter
└── Snapshotter
```

### Why SequentialAgent of per-ticker children, rebuilt per tick

The watchlist is tick-scoped (§A row for `tickers`). ADK's
`SequentialAgent.sub_agents` is set at construction. To honour both
constraints, the analyst branches are built fresh every tick from
`state["tickers"]`. Live (`orchestrator/tick.py:138`) already does this
via per-invocation `build_pipeline(...)`. Backtest
(`backtest/driver.py:173`) currently builds once per window; this
phase moves the build call into the per-tick loop.

---

## Component design

### 1. `NewsFetchAgent` / `FundamentalFetchAgent` (new — BaseAgent)

Both new classes lift the existing `before_agent_callback` fetch logic
(`agents/analysts/news/fetch.py::news_fetch_callback`,
`agents/analysts/fundamental/fetch.py::fundamental_fetch_callback`)
into proper `BaseAgent._run_async_impl` methods that yield their
results via `EventActions(state_delta=...)`.

**Inputs (from state):** `tickers`, `as_of`.

**Outputs (yielded via state_delta):**

- `temp:news_data` — dict keyed by ticker, machine-readable. Unchanged shape.
- `news_context` — multi-ticker formatted text block (today's pattern).
  Retained for the joiner's debug/trace use; **not** read by
  per-ticker LlmAgents (each ticker reads its own slice from a new
  `temp:news_context_<TICKER>` key instead).

**Why split per-ticker context?** Per-ticker prompts get only their
own ticker's headlines — otherwise we leak the batched context back
in. The fetch agent populates one extra key per ticker, e.g.
`temp:news_context_<TICKER>`, which the per-ticker LlmAgent reads via
its ADK instruction placeholder `{news_context}`.

**Contract conformance.** The fetch agent is a BaseAgent that yields
state_delta (Rule 1). All written keys are either contract-bearing
(`news_context` — already in current use) or `temp:`-prefixed (Rule
2). Idempotent for the same `(tickers, as_of)` input.

### 2. Per-ticker LlmAgents

For each ticker, a fresh `LlmAgent` named e.g.
`NewsAnalyst_<TICKER>`. The full per-branch wrapping order is
`IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))` — see §5 for
the rationale. The LlmAgent's own fields:

| Field | Value |
|---|---|
| `name` | `"NewsAnalyst_<TICKER>"` |
| `model` | Read from `config/models.json::news_analyst` (Flash-Lite today) |
| `instruction` | Rendered from a new single-ticker prompt template — see prompt redesign below |
| `output_schema` | `TickerVerdict` (already exists at `contract/evidence.py:116`) |
| `output_key` | `f"temp:news_verdict_{ticker}"` |
| `before_model_callback` | Per-ticker cache check + optional trace |
| `after_model_callback` | Per-ticker cache write + optional trace |
| `before_agent_callback` | **None** — fetch lives in the FetchAgent now |
| `after_agent_callback` | **None** — evidence-build moves to the joiner |

No per-branch `YieldingAnalystWrapper` — there is no after-callback
direct mutation to republish (see "Wrappers retiring" below).

**Per-ticker prompt template.** Today's batched prompt
(`agents/analysts/news/prompts.py::_TEMPLATE`) is rewritten to address
one ticker. Concretely:

- "For each ticker in the batch..." → "For the ticker named below..."
- The closed-vocabulary block is unchanged.
- `{news_context}` placeholder remains but is filled by ADK's
  `inject_session_state` with the per-ticker context block written to
  `temp:news_context_<TICKER>` (the per-ticker LlmAgent's instruction
  is constructed with that resolved key at build time).
- `{tickers}` placeholder is replaced with the single `{ticker}` for
  this branch — also resolved at build time.
- "MUST cover ALL tickers: {tickers}" line is removed.
- Output schema directive is updated for the single-verdict form.

Same treatment for `agents/analysts/fundamental/prompts.py`.

### 3. `NewsJoinerAgent` / `FundamentalJoinerAgent` (new — BaseAgent)

The joiner is where the batched contract handoff is reassembled.

**Inputs (from state):**

- N `temp:news_verdict_<TICKER>` keys (one per watchlist ticker; some
  may carry a synthetic no-data verdict if their branch failed —
  see failure handling).
- `temp:news_data` — still in state, populated by the fetch agent.
- `tickers`, `tick_id`, `as_of`.

**Outputs (yielded as one state_delta event):**

- `news_verdicts` — the canonical `VerdictBatch` dict
  (`{"verdicts": [TickerVerdict, ...]}`). The §A row's contract value.
- `news_evidence` — `list[AnalystEvidence]`, one row per ticker.
  Computed by calling the deterministic feature extractor
  (`extract_news_features`) on each ticker's `temp:news_data` slice
  and pairing the result with the corresponding TickerVerdict.

**Why both keys yielded by the joiner.** Today the LlmAgent's
`output_key` writes `news_verdicts` and the `after_agent_callback`
writes `news_evidence`. Per-ticker fan-out splits the verdict source
across N branches, so a single consolidation point is the cleanest
place to build both canonical keys. Doing it together also means one
`state_delta` event carries the entire analyst output — easier to
trace and reason about.

The existing `make_evidence_callback` factory is retired; its body
moves into the joiner's `_run_async_impl`.

### 4. Cache layer adaptation

`agents/analysts/cache_callbacks.py::make_report_cache_callbacks` is
rewritten for the per-ticker shape:

**Signature change.** Add two parameters:

- `ticker: str` — the single ticker this callback pair is bound to
  (set at LlmAgent construction time by the per-ticker factory).
- `output_schema: type[BaseModel]` — defaults to `TickerVerdict` for
  these analysts; replaces the hardcoded `VerdictBatch`. The
  synthetic LlmResponse on a cache hit now returns a single-verdict
  JSON, not a wrapper.

**Behaviour change.** The `_before` hook no longer iterates
`state["tickers"]`. It checks one ticker. Hit → return synthetic
single-verdict `LlmResponse`. Miss → return `None` (LLM runs). The
"any single miss forces a full LLM call" rule (line 195 in current
code) is deleted — it had no meaning at the per-ticker level.

**Cache file format unchanged.** Each per-ticker cache file
(`cache/reports/news/<TICKER>.json`) still stores
`{verdict, report, input_hash, prompt_version, originating_as_of}`.
The verdict payload type aligns with TickerVerdict either way.

**Existing batched call sites disappear.** Both News and Fundamental
move to the new shape simultaneously, so there is no batched caller
left after this phase. The factory is rewritten in place (no shim,
no parallel API).

### 5. Failure handling per branch

When a per-ticker branch fails — persistent 429 after retries
exhausted, malformed JSON, schema validation error — the
`RetryingAgentWrapper`'s tenacity layer raises after its
configured `max_attempts`. Without intervention, that abort
propagates up the SequentialAgent and kills the tick (current
behaviour today, before this phase).

**The new behaviour:** the joiner agent treats a missing
`temp:news_verdict_<TICKER>` key as "this branch failed" and
synthesises a no-data verdict for that ticker — exactly the same
synthesis the current `make_evidence_callback` does when the LLM
omitted a ticker from the batch:

```python
verdict = AnalystVerdict(
    lean        = "neutral",
    magnitude   = 0.0,
    confidence  = 0.0,
    rationale   = "no verdict from LLM",
    key_factors = [],
    is_no_data  = True,
)
```

This requires per-branch error containment — the
`RetryingAgentWrapper`'s exception must not propagate past its own
branch. Two options:

1. **Wrap each per-ticker LlmAgent in an error-suppressing decorator**
   (e.g. `IsolatedFailureWrapper`) that catches and logs the
   exception, yielding no events. The joiner then sees no
   `temp:news_verdict_<TICKER>` key and falls back to no-data.
2. **Modify `RetryingAgentWrapper`** to optionally swallow exceptions
   after exhausting retries.

Option 1 is cleaner — single responsibility per wrapper,
`RetryingAgentWrapper` keeps its semantics. Implementation: a new
`IsolatedFailureWrapper(BaseAgent)` that proxies the inner and
catches at its own `_run_async_impl` boundary. Per-branch wrapping
order becomes `IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))`.

Telemetry: the failure-suppress wrapper logs the exception with
structured fields (`analyst`, `ticker`, `kind="branch_failed"`,
`exc_type`, `exc_message`) into the per-tick obs/ log stream, so
failed branches are visible in `runs/<id>/obs/logs/<tick>.json` even
though the tick survives. The decision logger snapshot continues to
work because `news_evidence` always has one row per ticker — the
no-data ones are just flagged via `is_no_data=True`.

### 6. Wrappers retiring for these branches

`YieldingAnalystWrapper` exists today purely to republish the
`after_agent_callback`'s direct-mutation of `news_evidence` /
`fundamental_evidence` as a Rule-1-conformant state_delta yield. In
the new design:

- Per-ticker LlmAgents have **no** `after_agent_callback` — the
  evidence-build moves into the joiner.
- The joiner yields `news_evidence` directly via state_delta (Rule 1
  natively).
- ADK's `__maybe_save_output_to_state` path writes the per-ticker
  `temp:news_verdict_<TICKER>` keys via its own mechanism — same as
  today's `news_verdicts` write.

Net effect: `YieldingAnalystWrapper` is no longer used by News or
Fundamental. A grep at implementation time will confirm whether any
other agent still depends on it. If not, the class is deleted (no
dead code). If something else does use it, the class stays — but its
docstring needs updating to reflect that News/Fundamental no longer
wear it.

### 7. Backtest pipeline build cadence

`backtest/driver.py:173` builds the pipeline once at driver
construction:

```python
self._pipeline = build_pipeline(broker, db_session)
```

This must move into the per-tick loop. Concretely, `_run_one_tick`
(currently at `driver.py:line ~393`) becomes responsible for
constructing the pipeline from the tick's `state["tickers"]`:

```python
async def _run_one_tick(self, state: dict) -> None:
    pipeline = build_pipeline(broker=self._broker,
                              db_session=self._db_session)
    # ... existing runner.run_async ...
```

`build_pipeline` already accepts these parameters and is fast (no
network I/O at construction); the per-tick rebuild is a few ms of
Pydantic and ADK setup work. Live (`tick.py:138`) is unchanged — it
already builds per-invocation.

This also lifts the implicit "watchlist must be stable for the run"
invariant — backtests can now drive a per-tick watchlist change if a
future feature wants it (out of scope here but unblocked).

### 8. Schema additions

The existing `TickerVerdict` at `contract/evidence.py:116` is sufficient
for per-ticker outputs — no new schema class is introduced.

`VerdictBatch` (the joiner's output container) is unchanged.

`AnalystEvidence`, `AnalystReport`, `ReportDriver`, `AnalystVerdict`
are unchanged.

### 9. Observability

Per-tick obs/ artefacts shift shape:

- **Per-agent latency.** Today the obs aggregator
  (`backtest/reporting.py::_aggregate_obs_artefacts`) sees one
  `invoke_agent` span per LLM analyst per tick (
  `NewsAnalystRetrying`, `FundamentalAnalystRetrying`). After this
  phase, it sees N spans per analyst per tick (one per ticker
  branch). The aggregator's per-agent table will list rows like
  `NewsAnalyst_AAPL` separately.
- **Cache hit/miss telemetry.** Today's `report_cache_hit` /
  `report_cache_miss` log records (in `cache_callbacks.py`) continue
  to fire — one per branch now instead of one per ticker inside one
  call. Aggregated hit-rate metrics on the metrics.md report stay
  meaningful.
- **New `branch_failed` records** when a per-ticker branch
  exhausts retries — emitted by the IsolatedFailureWrapper. Aggregator
  may add a `branches_failed` count to the per-tick summary.

Per §C-Rule 8, all observability is additive and contract-neutral —
no §A row is affected by these changes.

### 10. §A invariant table updates

Two rows change Owner. The contract values and lifetimes are
unchanged.

| Field | Today | After Phase 9 |
|---|---|---|
| `news_verdicts` | NewsAnalyst (`output_key`) | NewsJoinerAgent (via `state_delta`) |
| `fundamental_verdicts` | FundamentalAnalyst (`output_key`) | FundamentalJoinerAgent (via `state_delta`) |

The §A table edit in `docs/contract-invariants.md` is part of this
phase's implementation work.

No new §A rows — the per-ticker `temp:news_verdict_<TICKER>` and
`temp:fundamental_verdict_<TICKER>` keys are pipeline-internal
working state, not contract-bearing (per §A scope rules).

`temp:news_context_<TICKER>` likewise — pipeline-internal.

---

## Testing strategy

### What stays the same

- The end-to-end smoke test
  (`tests/integration/backtest/test_end_to_end_smoke.py`) — its LLM
  stubs short-circuit at `before_model_callback`, so they will work
  per-branch unchanged once the stub shim is taught to recognise
  per-ticker agent names (currently keys off `"NewsAnalyst"` /
  `"FundamentalAnalyst"`).
- Contract conformance tests around §A row ownership — updated to
  expect the joiner as owner.

### What's new

- **Per-ticker prompt rendering tests** for both analysts: the
  build-instruction function renders a single-ticker template
  correctly; the per-ticker context placeholder is wired correctly.
- **FetchAgent BaseAgent tests** for both analysts: state_delta yield
  carries the expected keys; idempotent on repeated calls; degrades
  gracefully on provider errors.
- **Joiner agent tests** for both analysts: synthesises no-data for
  missing per-ticker keys; correctly builds `news_verdicts` +
  `news_evidence` from N TickerVerdicts; yields exactly one
  state_delta event.
- **IsolatedFailureWrapper tests**: catches and logs without
  propagating; emits structured `branch_failed` records; does not
  write any state.
- **Cache callback tests** for the per-ticker signature: single-ticker
  hit returns single-verdict `LlmResponse`; miss returns `None`;
  cache file format compatibility.
- **Backtest driver tests** verifying per-tick pipeline rebuild does
  not regress watchlist handling.

### What's removed

- The "all-tickers-must-be-cached-or-all-go-to-LLM" cache behaviour
  test, if any (the rule itself is deleted).
- Any test asserting `YieldingAnalystWrapper` wraps News /
  Fundamental — once the wrapper retires for these branches.

---

## Risks and open questions

### Risks

1. **Cost.** N parallel-issued-but-sequential LLM calls have N copies
   of the prompt prefix (closed vocabulary, decision rules, schema)
   in input tokens. Mitigation: Vertex AI prompt caching can
   amortise the shared prefix automatically for Gemini models — to
   confirm at implementation time and configure if available. If
   it's not free, watch the per-tick `tokens.input` metric in
   `metrics.md` after the first backtest.
2. **Wall-clock per tick.** Sequential N calls means
   `~N × per_call_latency` instead of `~per_call_latency`. For a 20-
   ticker watchlist and ~2s per Flash-Lite call, that's ~40s on a
   miss-heavy tick. Acceptable for backtests; acceptable for hourly
   live ticks. If this becomes painful in production, the obvious
   escape is parallel (rejected here on rate-limit grounds, but
   reversible).
3. **`temp:` key proliferation.** N tickers means N per-ticker keys
   for verdicts and N for context blocks. ADK strips them at the
   tick boundary (Rule 2), so no cross-tick leakage. Worth a
   sanity-check test that confirms strip behaviour at scale (e.g.
   50-ticker watchlist).
4. **Joiner sees an empty key dict.** If both the LLM call *and* the
   cache lookup fail for every ticker — say, total provider outage —
   the joiner produces an `news_evidence` list of all-`is_no_data=True`
   rows. Downstream consumers (strategist, evidence writer) must be
   robust to this; today they already are (no-data verdicts are a
   normal path).

### Resolved during brainstorming

- Per-branch failure behaviour — synthetic no-data verdict, tick
  survives (option (a) from brainstorming).
- State-key ownership — joiner agent owns canonical key (option
  (a) from brainstorming).
- Schema — `TickerVerdict`, no new class (option (a) from
  brainstorming).
- Build cadence — rebuild per tick in both lifecycles (option (a)
  from brainstorming).
- Scope — both News and Fundamental together in this phase.

### Deferred (backlog candidates)

- **Cross-ticker context aggregator.** A second-pass agent that
  reads the joined `news_verdicts` and produces sector / relative-
  strength annotations. Restores the cross-ticker reasoning the
  batched prompt had, without giving up per-ticker focus.
- **Schema cap diet.** Independent of fan-out; `report_summary_max_chars`
  and `report_driver_body_max_chars` are still generous. A separate
  pass could halve them with no quality loss.
- **Live observability sink (GCS).** Phase 9 reshapes per-tick obs
  artefacts; the live-side sink is still TBD per the project's
  pre-deployment state.

---

## Out of scope (do not regress, but do not touch)

- Strategist behaviour — unchanged.
- RiskGate / Executor — unchanged.
- Memory subsystem (`MemoryWriter`, memory_buffer, day_digest) —
  unchanged.
- Persistence layer §E — unchanged.
- Provider layer (`data/providers/...`) — unchanged.
- Deterministic analysts (Technical, Social, SmartMoney) — unchanged.

---

## Implementation will produce

See `docs/Phase9-agent-fanning-per-ticker/plans/` for the
implementation plan(s). The expected files / modules touched:

**New:**

- `src/agents/analysts/news/fetch_agent.py` — `NewsFetchAgent(BaseAgent)`
- `src/agents/analysts/fundamental/fetch_agent.py` — `FundamentalFetchAgent`
- `src/agents/analysts/news/joiner.py` — `NewsJoinerAgent(BaseAgent)`
- `src/agents/analysts/fundamental/joiner.py` — `FundamentalJoinerAgent`
- `src/agents/isolated_failure.py` — `IsolatedFailureWrapper(BaseAgent)`
- `src/agents/analysts/news/per_ticker.py` — `build_news_branch_for_ticker(...)`
- `src/agents/analysts/fundamental/per_ticker.py` —
  `build_fundamental_branch_for_ticker(...)`

**Rewritten:**

- `src/agents/analysts/news/agent.py` — single LlmAgent factory →
  per-ticker SequentialAgent factory.
- `src/agents/analysts/fundamental/agent.py` — same.
- `src/agents/analysts/news/prompts.py` — single-ticker template.
- `src/agents/analysts/fundamental/prompts.py` — single-ticker
  template.
- `src/agents/analysts/news/fetch.py` — body moves into
  `fetch_agent.py`; old callback retired.
- `src/agents/analysts/fundamental/fetch.py` — same.
- `src/agents/analysts/cache_callbacks.py` — per-ticker signature.
- `src/orchestrator/pipeline.py::_build_analyst_pool` — composes
  per-ticker branches from watchlist.
- `src/backtest/driver.py` — per-tick pipeline rebuild.
- `docs/contract-invariants.md` §A — Owner column updates.

**Retired (subject to grep confirmation):**

- `src/agents/analysts/_base_yield.py::YieldingAnalystWrapper`
  (if no other consumers).
- `src/agents/analysts/_common.py::make_evidence_callback` — body
  migrates into joiners.

**Configuration:**

- No `config/` changes — `models.json`, `analysts.json`, `data.json`
  all unchanged for this phase.
