# Spec — Three-layer LLM retry (rate-limit / timeout / schema)

**Status:** Draft for one-hit execution.
**Origin:** brainstorming session 2026-05-23.

---

## 1. Context

The pipeline today wraps every LLM-calling agent in
`src/agents/llm_retry.py::RetryingAgentWrapper`, which retries **only** on
HTTP 429 (Vertex `RESOURCE_EXHAUSTED`).  Two other failure modes the user has
observed in practice are silently un-handled:

- **Wall-clock runaways.** No per-call timeout is configured on any
  `LlmAgent`.  A model that streams forever (or hangs in a callback) blocks
  the tick indefinitely.
- **Schema-validation failures.** ADK validates each LLM output against the
  agent's `output_schema` (`TickerVerdict` for analysts, `StrategistDecision`
  for the strategist) and raises `pydantic.ValidationError` on mismatch.
  Today this propagates straight out — no retry, no second chance.

There is also no cap on `max_output_tokens` anywhere in the codebase, so a
model that goes into an output loop can burn arbitrary tokens before the
schema parse finally fails on the malformed result.

> **Misleading hint:** `graphify-out/graph_delta.md` carries a 2026-05-22
> entry describing exactly the work in this spec as already done.  None of
> that work is committed — the entry is aspirational.  Treat it as a design
> hint, not as ground truth.

The user's stated goals for this spec:

1. **Rate-limit retry layer (429).** Already in place.  Keep its current
   behaviour (5 attempts, 2–30s exponential-jitter backoff) but move its
   config out of the shared file into a dedicated one.
2. **Timeout retry layer.** Wrap every LLM-calling agent in
   `asyncio.wait_for(...)` at a per-agent timeout (60s analyst, 180s
   strategist) and retry up to **3 attempts** with **no backoff** on
   `asyncio.TimeoutError` / `TimeoutError`.  Also enforce a per-agent
   `max_output_tokens` cap on **every** call (not just retries) so output
   loops cannot wedge the tick in the first place.
3. **Schema retry layer.** Recognise `pydantic.ValidationError` as a
   retryable class and retry up to **3 attempts** with **no backoff**.
4. **Telemetry.** A structured `llm_retry_exhausted` ERROR log when any
   class runs out of attempts; per-tick retry counts surfaced on the
   existing terminal-summary rows (e.g. `news: 12/12 ✓ · 3.4s · 24.0k tok ·
   retries rate_limit×2`).

---

## 2. Scope

**In scope.**

- Extend `src/agents/llm_retry.py::RetryingAgentWrapper` to handle three
  retry classes with independent per-class budgets.
- Enforce per-agent timeouts via `asyncio.wait_for(...)`.
- Enforce per-agent `max_output_tokens` via
  `google.genai.types.GenerateContentConfig` on every `LlmAgent` build.
- Rename `config/llm_retry.json` → `config/retry_429.json` and
  `src/config/llm_retry.py` → `src/config/retry_429.py`.  The 429 file
  carries **only** the 429 policy.
- Add `llm: {timeout_seconds, max_output_tokens, timeout_retries,
  schema_retries}` blocks to `config/analysts.json` (`news`, `fundamental`)
  and `config/strategist.json`.
- Replace tenacity-based retry with a hand-rolled loop inside the wrapper.
- Add per-tick retry telemetry to session state and surface it on the
  existing terminal-summary rows.
- Emit one structured `llm_retry_exhausted` ERROR log per exhaustion event.

**Out of scope.**

- A fourth retry class for `StrategistContractViolation` (cross-stance
  contract failures).  Those are systematic prompt-or-pipeline issues that
  retry will not fix; they continue to propagate immediately, matching
  today's behaviour.
- Network-layer `httpx.TimeoutException` recognition.  Those would only
  surface if Vertex itself raised a low-level HTTP timeout before our
  `asyncio.wait_for` fires; we let them fall through to the unhandled
  branch (same as today).
- Any retry budget bigger than 5 for the 429 class.  The current value
  (5) stays.
- Adaptive `max_output_tokens` (different caps on first call vs retry).
  Decided against during brainstorming — one cap per agent, applied
  from the first call.
- Changes to `IsolatedFailureWrapper` behaviour around analyst branches.
  It continues to absorb post-retry-exhaustion failures and let the
  joiner synthesise a no-data verdict.
- Changes to the strategist's "abort the tick on terminal failure"
  behaviour.

---

## 3. Contract anchors

This spec respects the following load-bearing parts of
`docs/contract-invariants.md`:

- **§C Rule 1.** State mutation rides on `Event(state_delta=...)`.  The
  new per-tick retry counters are written by the wrapper via a yielded
  `Event(state_delta=...)` — not by direct `ctx.session.state[...]`
  assignment.
- **§C Rule 8.** Observability is additive and contract-neutral.  The
  terminal-summary suffix and the `llm_retry_exhausted` ERROR log are
  both additive — they neither block the pipeline nor change downstream
  behaviour.
- **`RetryingAgentWrapper` invariants from its own docstring.**  The
  wrapper still buffers inner events until the attempt succeeds, and is
  still only safe to wrap *single LLM-calling agents* (a bare
  `LlmAgent`).  The strategist's "wrap inside the SequentialAgent, not
  around it" invariant from `agents/strategist/agent.py:289-343` is
  preserved verbatim.

---

## 4. Architecture & wrapping topology

No new wrapper classes are introduced.  The existing
`RetryingAgentWrapper` is extended; the existing topology is unchanged.

**Per-ticker analyst branches** (factories:
`agents/analysts/news/per_ticker.py`,
`agents/analysts/fundamental/per_ticker.py`):

```
IsolatedFailureWrapper            ← unchanged; turns terminal failure into
└── RetryingAgentWrapper          ← EXTENDED                    no-data verdict
    └── LlmAgent                  ← gains GenerateContentConfig(max_output_tokens=…)
```

**Strategist branch** (factory: `agents/strategist/agent.py::build_strategist`):

```
SequentialAgent[StrategistBranch]
├── StrategistContextShim         ← unchanged; MUST NOT be wrapped (state_delta invariant)
└── RetryingAgentWrapper          ← EXTENDED
    └── LlmAgent                  ← gains GenerateContentConfig(max_output_tokens=…)
```

The `asyncio.wait_for(...)` enforcement happens **inside**
`RetryingAgentWrapper._run_async_impl`, around the loop that drives the
inner agent.  This measures end-to-end agent run time (model HTTP call +
every model-callback the agent dispatches) — same scope as today's 429
retry.

---

## 5. Configuration

### 5.1. `config/retry_429.json` (renamed from `llm_retry.json`)

Carries **only** the rate-limit policy.  Shape preserved except for the
top-level `_comment` rewrite.

```json
{
  "_comment": "Vertex AI HTTP 429 retry policy. See src/config/retry_429.py for the loader. Timeout and schema retry counts live per-agent in config/analysts.json and config/strategist.json — only the 429 policy is project-wide.",
  "max_attempts":       5,
  "base_delay_seconds": 2.0,
  "max_delay_seconds":  30.0
}
```

Loader: `src/config/retry_429.py::get_retry_429_policy()` (cached
singleton, same shape as the existing `get_retry_config()`).  The
`_reset_cache()` test hook is kept.

### 5.2. `config/analysts.json` — extend

Add `llm` block to each of `news` and `fundamental`:

```json
{
  "news": {
    "max_articles_per_ticker": 25,
    "max_summary_chars":       1500,
    "llm": {
      "timeout_seconds":   60,
      "max_output_tokens": 2000,
      "timeout_retries":   3,
      "schema_retries":    3
    }
  },
  "fundamental": {
    "max_filing_mda_chars":     1500,
    "max_filing_risk_chars":    1500,
    "max_insider_footnotes":    5,
    "max_insider_footnote_chars": 400,
    "llm": {
      "timeout_seconds":   60,
      "max_output_tokens": 2000,
      "timeout_retries":   3,
      "schema_retries":    3
    }
  }
}
```

Loader: extend `src/config/analysts.py` with a nested `LlmCaps` Pydantic
model exposed as `NewsCaps.llm` and `FundamentalCaps.llm`.

### 5.3. `config/strategist.json` — extend

```json
{
  …existing char caps…,
  "llm": {
    "timeout_seconds":   180,
    "max_output_tokens": 8000,
    "timeout_retries":   3,
    "schema_retries":    3
  }
}
```

Loader: extend `src/config/strategist.py::StrategistConfig` with the same
`LlmCaps` field.

### 5.4. Loader-level validation

`LlmCaps` Pydantic constraints:

- `timeout_seconds: float = Field(gt=0, le=600)`
- `max_output_tokens: int = Field(ge=256, le=32_768)`
- `timeout_retries: int = Field(ge=1, le=10)`
- `schema_retries: int  = Field(ge=1, le=10)`

The 429 loader keeps its existing cross-field invariant
(`max_delay_seconds >= base_delay_seconds`).

### 5.5. Default values & rationale

| Knob | Default | Reasoning |
|---|---|---|
| `retry_429.max_attempts` | 5 | Unchanged from today. |
| `retry_429.base_delay_seconds` | 2.0 | Unchanged from today. |
| `retry_429.max_delay_seconds` | 30.0 | Unchanged from today. |
| `news.llm.timeout_seconds` | 60 | User-specified analyst timeout. |
| `fundamental.llm.timeout_seconds` | 60 | Same. |
| `strategist.llm.timeout_seconds` | 180 | User-specified strategist timeout. |
| `news.llm.max_output_tokens` | 2000 | `TickerVerdict` is small (200-char rationale + ≤5 drivers ≤500 chars each + tags). 2000 tokens is ≈5× the realistic max — generous, kills runaways. |
| `fundamental.llm.max_output_tokens` | 2000 | Same schema, same logic. |
| `strategist.llm.max_output_tokens` | 8000 | `StrategistDecision` carries up to one `TickerStance` per watchlist ticker (~10–20). Conservative 4× headroom. |
| `*.timeout_retries` | 3 | User-specified (3 attempts total per the brainstorming Q4 answer). |
| `*.schema_retries` | 3 | Same. |

### 5.6. `config/README.md` update

Rename the `llm_retry.json` row to `retry_429.json` with the narrower
description.  Add the `llm: { timeout_seconds, max_output_tokens,
timeout_retries, schema_retries }` block to both the `analysts.json` and
`strategist.json` row descriptions.

---

## 6. Exception classification

A single `_classify(exc)` function in `src/agents/llm_retry.py` returns
the retry class name (`"rate_limit"`, `"timeout"`, `"schema"`) or `None`
for unhandled exceptions.  Classification walks the `__cause__` chain so
wrapped exceptions still classify correctly.

### 6.1. `rate_limit`

Keep the existing `_is_resource_exhausted` logic verbatim, renamed to
`_classify_rate_limit`:

- `google.adk.models.google_llm._ResourceExhaustedError` (defensive
  import — fall through silently on `ImportError` if ADK ever renames it).
- `google.genai.errors.ClientError` with `status_code == 429`.
- Walks `__cause__`.

### 6.2. `timeout`

- `asyncio.TimeoutError` / `TimeoutError` (the former is an alias for the
  latter from Python 3.11).

Network-layer `httpx.TimeoutException` is **not** classified — it would
only fire if Vertex itself raised an HTTP-layer timeout, which is a real
infra error that retry will not fix.  Falls through to `None`.

### 6.3. `schema`

- `pydantic.ValidationError` (direct or via `__cause__`).

`StrategistContractViolation` (raised by
`agents/strategist/agent.py::_strategist_validation_callback`) is
**not** classified — it is a cross-stance contract failure that fires
*after* the schema parse already passed.  Falls through to `None`,
preserving today's tick-abort behaviour.

### 6.4. Anything else

`None` → re-raise immediately, no budget consumed.  Identical to today.

---

## 7. Wrapper internals

### 7.1. Constructor shape

```python
class RetryingAgentWrapper(BaseAgent):
    inner:           Any              # the LlmAgent
    timeout_seconds: float            # per-instance, from per-agent config
    policies:        dict[str, RetryPolicy]   # {"rate_limit": ..., "timeout": ..., "schema": ...}
    retry_state_key: str              # session-state key for per-tick retry counters

    model_config = {"arbitrary_types_allowed": True}
```

`RetryPolicy` is a small dataclass-or-Pydantic shape:

```python
class RetryPolicy(BaseModel):
    max_attempts: int
    backoff:      Literal["immediate", "exp_jitter"]
    # Only consulted when backoff == "exp_jitter":
    base_delay_seconds: float = 0.0
    max_delay_seconds:  float = 0.0
```

A helper `build_retry_policies(*, timeout_retries, schema_retries)`
composes the dict at factory time:

```python
def build_retry_policies(
    *,
    timeout_retries: int,
    schema_retries:  int,
) -> dict[str, RetryPolicy]:
    """Compose the per-agent retry policy dict.

    The 429 policy is project-wide (loaded from config/retry_429.json);
    timeout and schema policies are per-agent (their max_attempts come
    from the caller; backoff is hard-coded to "immediate" per the design
    decision in the brainstorming session).
    """
    return {
        "rate_limit": get_retry_429_policy(),
        "timeout":    RetryPolicy(max_attempts=timeout_retries, backoff="immediate"),
        "schema":     RetryPolicy(max_attempts=schema_retries,  backoff="immediate"),
    }
```

### 7.2. Run loop

```python
async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
    """Drive the inner agent with per-class retry + per-call wall timeout."""

    # Per-attempt event buffer — rebound at the start of every attempt
    # so a failed attempt's events are discarded before the retry.
    events: list[Event] = []

    # Per-class attempt counters — decremented when that class's
    # exception is raised; exhaustion of any one class re-raises.
    remaining = {cls: pol.max_attempts for cls, pol in self.policies.items()}

    while True:
        events = []

        try:
            # Inner driver — packaged as a closure so wait_for has
            # something cancellable.  We cannot put `yield` inside
            # wait_for directly.
            async def _drive() -> None:
                async for ev in self.inner.run_async(ctx):
                    events.append(ev)

            await asyncio.wait_for(_drive(), timeout=self.timeout_seconds)
            break  # success — fall through to flush events

        except BaseException as exc:
            cls = _classify(exc)

            if cls is None:
                # Unclassified — re-raise unchanged.  IsolatedFailureWrapper
                # (analysts) or the backtest driver (strategist) handles it.
                raise

            remaining[cls] -= 1

            # Emit the per-tick retry-counter state_delta event BEFORE
            # checking exhaustion, so the terminal-log row reflects the
            # attempt even when the next decision is "raise".
            yield Event(
                state_delta = {
                    self.retry_state_key: _merge_increment(
                        ctx.session.state.get(self.retry_state_key) or {},
                        cls,
                    ),
                },
            )

            if remaining[cls] <= 0:
                _log_exhausted(self.inner.name, cls, exc, self.policies, remaining)
                raise

            _log_retry(self.inner.name, cls, exc, remaining)

            # attempts_consumed_for_class — fed to exp-jitter for the
            # 429 path so the backoff grows attempt-by-attempt.  No-op
            # for "immediate" policies.
            attempts_consumed = self.policies[cls].max_attempts - remaining[cls]
            await _sleep_per_policy(self.policies[cls], attempt_n=attempts_consumed)
            continue

    # Reached only on success — flush buffered inner events in order.
    for ev in events:
        yield ev
```

### 7.3. Invariants preserved from today

1. **Event buffering of the inner.**  `state_delta` events from a failed
   attempt are discarded; only the successful attempt's events flush.
2. **Wrapper-emitted events are NOT buffered.**  The retry-counter
   `state_delta` event yielded between attempts goes through immediately
   so the running total is visible to downstream callbacks even mid-tick.
3. **Single-LLM-call wrap only.**  Wrapping a `SequentialAgent` still
   breaks `inject_session_state` (the existing strategist warning still
   applies).
4. **`tenacity` no longer used by this module.**  A small
   `_compute_exp_jitter(attempt_n, base, max)` helper replaces
   `wait_exponential_jitter`.  Identical formula.  Saves one dependency
   from the hot path.
5. **HTTP-request cancellation on timeout.**  `asyncio.wait_for` cancels
   `_drive()`, which propagates `CancelledError` into the genai SDK's
   in-flight HTTP call.  Trust the SDK's cancellation path; do not add
   extra cleanup.

### 7.4. Logging contract

Two log helpers in the module:

```python
def _log_retry(name, cls, exc, remaining):
    """Per-attempt WARNING — emitted just before sleep-and-retry."""
    _LOGGER.warning(
        "llm_retry_attempt",
        extra={
            "kind":               "llm_retry_attempt",
            "agent":              name,
            "retry_class":        cls,
            "exc_type":           type(exc).__name__,
            "exc_message":        str(exc),
            "remaining_attempts": {c: r for c, r in remaining.items()},
        },
    )

def _log_exhausted(name, cls, exc, policies, remaining):
    """One ERROR row per terminal exhaustion — fires once per wrapper run
    that gives up on a class."""
    _LOGGER.error(
        "llm_retry_exhausted",
        extra={
            "kind":            "llm_retry_exhausted",
            "agent":           name,
            "exhausted_class": cls,
            "exc_type":        type(exc).__name__,
            "exc_message":     str(exc),
            "attempts_used":   {
                c: policies[c].max_attempts - r
                for c, r in remaining.items()
            },
        },
    )
```

Both are structured WARNING/ERROR (`extra=`) records — captured by the
existing log handler.  No format changes elsewhere.

---

## 8. `max_output_tokens` wiring at factory sites

Three near-identical changes — one per LlmAgent construction site.

### 8.1. News per-ticker factory

`src/agents/analysts/news/per_ticker.py::build_news_branch_for_ticker`:

```python
from google.genai import types as genai_types
from config.analysts import get_analysts_config

llm_caps = get_analysts_config().news.llm

llm = LlmAgent(
    name                    = f"NewsAnalyst_{ticker}",
    model                   = model,
    instruction             = instruction,
    output_schema           = TickerVerdict,
    output_key              = f"temp:news_verdict_{ticker}",
    before_model_callback   = before_cb,
    after_model_callback    = after_cb,
    generate_content_config = genai_types.GenerateContentConfig(
        max_output_tokens = llm_caps.max_output_tokens,
    ),
)

retrying = RetryingAgentWrapper(
    name            = f"NewsAnalyst_{ticker}_retrying",
    inner           = llm,
    timeout_seconds = llm_caps.timeout_seconds,
    policies        = build_retry_policies(
        timeout_retries = llm_caps.timeout_retries,
        schema_retries  = llm_caps.schema_retries,
    ),
    retry_state_key = "temp:_obs_news_retries",
)
```

### 8.2. Fundamental per-ticker factory

Symmetric to News — read `get_analysts_config().fundamental.llm`, pass
`retry_state_key="temp:_obs_fundamental_retries"`.

### 8.3. Strategist factory

`src/agents/strategist/agent.py::build_strategist`:

```python
from config.strategist import get_strategist_config

llm_caps = get_strategist_config().llm

llm = LlmAgent(
    name                    = "Strategist",
    model                   = model_name,
    instruction             = STRATEGIST_INSTRUCTION,
    output_schema           = StrategistDecision,
    output_key              = "strategist_decision",
    after_agent_callback    = _strategist_validation_callback,
    before_model_callback   = before_model,
    after_model_callback    = after_model,
    generate_content_config = genai_types.GenerateContentConfig(
        max_output_tokens = llm_caps.max_output_tokens,
    ),
)

wrapped_llm = RetryingAgentWrapper(
    name            = "StrategistLlmRetrying",
    inner           = llm,
    timeout_seconds = llm_caps.timeout_seconds,
    policies        = build_retry_policies(
        timeout_retries = llm_caps.timeout_retries,
        schema_retries  = llm_caps.schema_retries,
    ),
    retry_state_key = "temp:_obs_strategist_retries",
)
```

### 8.4. Note on ADK defaults

Setting `generate_content_config` overrides ADK's implicit per-model
default for `max_output_tokens` (typically 8192 for Gemini 2.5 Flash).
The values in §5.5 sit well above the realistic verdict size and well
below the Vertex hard cap — chosen to kill runaways without ever
clipping legitimate output.

---

## 9. Terminal-log integration

### 9.1. Per-tick state accumulator

Each retry wrapper writes its incremented counter to a per-analyst key
in session state, via a yielded `Event(state_delta=...)`:

- `temp:_obs_news_retries`
- `temp:_obs_fundamental_retries`
- `temp:_obs_strategist_retries`

Each value is a `dict[str, int]` keyed by retry class (`rate_limit`,
`timeout`, `schema`).  Absent class == 0.

The 12-ticker News fan-out aggregates into one key — the summary row
for "news" reflects total retries across all tickers, matching the row's
"12/12" shape today.

### 9.2. Helper for merge-increment

```python
def _merge_increment(
    current: dict[str, int],
    cls:     str,
) -> dict[str, int]:
    """Return a new dict equal to ``current`` with ``current[cls]`` += 1.

    Pure function — does not mutate ``current``.  Used by the wrapper to
    build the state_delta payload for the per-tick retry accumulator.
    """
    out      = dict(current)
    out[cls] = out.get(cls, 0) + 1
    return out
```

### 9.3. `emit_analyst_summary` extension

`src/observability/terminal_log.py::emit_analyst_summary` gains an
optional `retries: dict[str, int] | None = None` parameter.

When `retries` is `None`, empty, or all-zero, no suffix is rendered.

When at least one class is non-zero, append a `· retries
<class>×<count>` suffix per non-zero class (full names, not
abbreviations):

```
news       : 12/12 ✓ · 3.4s · 24.0k tok
fundamental: 11/12 ✓ · 4.1s · 22.0k tok · retries rate_limit×2
strategist : 1/1  ✓ · 2.1s · 8.4k tok  · retries timeout×1 schema×2
```

Class names are rendered exactly as `rate_limit`, `timeout`, `schema`.

### 9.4. Caller-side wiring

Each existing `emit_analyst_summary(...)` call site reads its analyst's
retry key from session state and passes it through:

- `agents/analysts/news/joiner.py` —
  `retries=state.get("temp:_obs_news_retries") or {}`
- `agents/analysts/fundamental/joiner.py` —
  `retries=state.get("temp:_obs_fundamental_retries") or {}`
- `agents/strategist/agent.py::_strategist_validation_callback` —
  `retries=state.get("temp:_obs_strategist_retries") or {}`

### 9.5. Tick boundaries

The orchestrator already clears `temp:*` keys between ticks — the
retry accumulators reset automatically with no new logic.

---

## 10. Terminal failure behaviour

### 10.1. Analyst — no behavioural change

`IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))` continues to:

- Catch any post-retry-exhaustion exception in the wrapper.
- Log the existing structured `branch_failed` WARNING.
- Return without further events.
- The downstream joiner synthesises a no-data verdict from the missing
  `temp:<analyst>_verdict_<TICKER>` key.

A `pydantic.ValidationError` that today fails silently first-attempt
will now retry up to 3 times before reaching the isolation boundary.
Strictly an improvement.

### 10.2. Strategist — no behavioural change

The strategist's `RetryingAgentWrapper` has no enclosing
`IsolatedFailureWrapper`.  Terminal exhaustion (any class) raises out
of the `SequentialAgent` and aborts the tick.  The backtest driver's
abort-ratio logic handles it at the run level.

### 10.3. New: `llm_retry_exhausted` ERROR log

Fires once per wrapper run that exhausts any class (see §7.4).  Reads
the `attempts_used` snapshot:

```
ERROR llm_retry_exhausted agent=Strategist exhausted_class=schema
  attempts_used={'rate_limit': 0, 'timeout': 1, 'schema': 3}
```

Makes the failure mode obvious in run logs without trace-diving.

The unclassified branch (`cls is None`) does **not** emit this log —
those errors are real bugs, not budget exhaustion, and keep their
existing propagation paths.

---

## 11. Testing strategy

### 11.1. Tier A — `RetryingAgentWrapper` unit tests

`tests/unit/agents/test_llm_retry.py` — extend with fake-inner agents
that raise specified exceptions on the first N attempts:

| Test | Asserts |
|---|---|
| `test_rate_limit_retries_up_to_max_then_raises` | 6 consecutive 429s raise after attempt 5; 4 + success yields. |
| `test_timeout_retries_up_to_max_then_raises` | Inner sleeps past timeout 4× raises after attempt 3; 2 + success yields. |
| `test_schema_retries_up_to_max_then_raises` | `ValidationError` 4× raises after attempt 3; 2 + success yields. |
| `test_independent_budgets_per_class` | 429, timeout, 429, schema, success — all 5 attempts run; success yields. |
| `test_unclassified_exception_propagates_immediately` | `ValueError` raises immediately, no budget touched. |
| `test_strategist_contract_violation_not_retried` | `StrategistContractViolation` propagates immediately. |
| `test_immediate_backoff_does_not_sleep_meaningfully` | Patch `asyncio.sleep`; assert 0 (or no call) on timeout/schema; non-zero on rate_limit. |
| `test_event_buffer_discards_failed_attempt_events` | Inner yields E1 then raises; on retry yields E2 then succeeds.  Wrapper yields only E2. |
| `test_retry_emits_state_delta_event_for_obs_counter` | After a retry, the wrapper has yielded an Event with `state_delta={"temp:_obs_…_retries": {"<class>": 1}}` *before* the inner's success events. |
| `test_exhaustion_emits_structured_error_log` | `caplog` shows exactly one `llm_retry_exhausted` ERROR with `exhausted_class` and `attempts_used`. |

### 11.2. Tier B — config loader unit tests

- `tests/unit/config/test_retry_429.py` — load good JSON; reject
  `max_attempts<1`; reject negative delays; reject `max_delay <
  base_delay`.  Largely a rename of the existing `test_llm_retry.py`.
- `tests/unit/config/test_analysts_config.py` — load `news.llm` /
  `fundamental.llm`; assert defaults; reject `timeout_seconds<=0`;
  reject `max_output_tokens<256`; reject `timeout_retries<1`; reject
  `schema_retries<1`.
- `tests/unit/config/test_strategist_config.py` — same for
  `strategist.llm`.

### 11.3. Tier C — factory wiring tests

- `tests/analysts/test_per_ticker_branch.py` — extend to assert each
  branch's `LlmAgent.generate_content_config.max_output_tokens` matches
  `config/analysts.json`, and the surrounding `RetryingAgentWrapper`'s
  `timeout_seconds`, `policies`, and `retry_state_key` match.
- Strategist equivalent (existing or new) — same assertions.

### 11.4. Tier C — one end-to-end smoke

One tick run with a fake LlmAgent (no live API; honours the
no-live-API hard rule in `docs/test-policy.md`) where the fake raises
a `ValidationError` on first call then succeeds.  Asserts:

- The verdict is produced normally.
- `temp:_obs_news_retries` ends the tick as `{"schema": 1}`.

### 11.5. Out of scope for automated tests

- Real Vertex 429 / real timeout — would need live API.  Forbidden by
  the no-live-API policy.
- HTTP-request cancellation cleanup when `asyncio.wait_for` fires.
  This is the genai SDK's contract; we trust it.

### 11.6. Manual sanity check

After implementation: run `scripts/replay_backtest.py` on a known
window with `max_output_tokens` temporarily clamped to 50 (provokes
schema failures).  Confirm the terminal summary rows show the new
`· retries schema×N` suffix.  Not an automated test — a one-off
behavioural validation.

---

## 12. Migration notes

### 12.1. Config file rename

`config/llm_retry.json` → `config/retry_429.json`.  Both the file move
and the loader module rename (`src/config/llm_retry.py` →
`src/config/retry_429.py`) land in the same commit so no transient
inconsistent state exists on `main`.

`config/README.md` is updated in the same commit.

### 12.2. JSON shape change

The new file's JSON shape is **identical** to today's — only the file
name changes.  The new per-agent `llm` blocks are pure additions.  No
existing JSON consumers break.

### 12.3. Python import path changes

Only two consumer paths import the renamed module:

- `src/agents/llm_retry.py::RetryingAgentWrapper` (the wrapper itself).
- `src/contract/evidence.py` — uses it for the `verdict_rationale`
  prompt-budget computation.  This import is updated.

Other call sites import via `agents.llm_retry`, which is unchanged.

### 12.4. Backwards compatibility shim

None.  The codebase is pre-deployment (see
`project_stockbot_deployment_state.md`) — no running instance to keep
compatible with.  Clean rename.

---

## 13. Risks & open questions

### 13.1. Risk — wrapper's mid-attempt `state_delta` event interaction

The wrapper emits its own `state_delta` event between failed and retried
attempts.  This event is observed by the outer ADK Runner immediately
and applied to `ctx.session.state` — but it lands *before* the
successful attempt's buffered events.  Verify in a unit test
(`test_retry_emits_state_delta_event_for_obs_counter`) that ordering
is consistent: counter increment → buffered inner events flush.

### 13.2. Risk — `asyncio.wait_for` cancellation under ADK

The Google ADK Runner has its own async iteration semantics.  Verify
that cancelling `_drive()` mid-iteration does not leave session state
in a partial-write condition.  Mitigated by the event-buffering
invariant: state mutation only lands on success.  Worth an explicit
note in the wrapper docstring.

### 13.3. Open question — should the `_log_retry` per-attempt WARNING be
demoted to INFO on subsequent successful attempts?

Today's `_make_before_sleep` emits WARNING on every retry.  Under the
new structure, a tick that retried once and succeeded looks no
different in WARN-level logs from one that retried 3 times and
exhausted.  The new `llm_retry_exhausted` ERROR closes that gap for
exhaustion cases.  Leave per-retry WARNINGs as-is for v1; revisit if
log noise becomes an issue.

### 13.4. Open question — do we want a "retry budget" alarm in the
backtest summary?

A tick that consumed 4/5 rate-limit budgets is healthy but ominous.  No
new alarm in v1 — the per-tick suffix in the terminal log surfaces it
already.  Backlog candidate if backtest summaries start needing it.

---

## 14. File-by-file change inventory

| File | Change |
|---|---|
| `config/llm_retry.json` | **Rename** to `config/retry_429.json`.  Update `_comment` for the narrower scope. |
| `config/analysts.json` | **Extend** — add `news.llm` and `fundamental.llm` blocks. |
| `config/strategist.json` | **Extend** — add `llm` block. |
| `config/README.md` | **Update** — rename row, extend descriptions. |
| `src/config/llm_retry.py` | **Rename** to `src/config/retry_429.py`.  Rename `RetryConfig` → `Retry429Policy`; `get_retry_config` → `get_retry_429_policy`. |
| `src/config/analysts.py` | **Extend** — add `LlmCaps` model; attach as `NewsCaps.llm` and `FundamentalCaps.llm`. |
| `src/config/strategist.py` | **Extend** — add `LlmCaps`; attach to `StrategistConfig.llm`. |
| `src/agents/llm_retry.py` | **Rewrite internals.**  New ctor args (`timeout_seconds`, `policies`, `retry_state_key`).  Hand-rolled retry loop; remove `tenacity` import.  New `_classify`, `_log_retry`, `_log_exhausted`, `_merge_increment`, `_compute_exp_jitter`, `_sleep_per_policy` helpers.  Existing public API surface preserved (class name + `inner` field). |
| `src/contract/evidence.py` | Update import path for the renamed module. |
| `src/agents/analysts/news/per_ticker.py` | Pass `generate_content_config` to LlmAgent; pass `timeout_seconds`, `policies`, `retry_state_key` to wrapper. |
| `src/agents/analysts/fundamental/per_ticker.py` | Same. |
| `src/agents/strategist/agent.py` | Same for the strategist's LlmAgent + wrapper. |
| `src/agents/analysts/news/joiner.py` | Pass `retries=state.get("temp:_obs_news_retries") or {}` to `emit_analyst_summary`. |
| `src/agents/analysts/fundamental/joiner.py` | Same with the fundamental key. |
| `src/observability/terminal_log.py` | Extend `emit_analyst_summary` with optional `retries` kwarg; render the suffix when non-empty. |
| `tests/unit/agents/test_llm_retry.py` | Extend per Tier A. |
| `tests/unit/config/test_llm_retry.py` | **Rename** to `test_retry_429.py`; adjust import paths. |
| `tests/unit/config/test_analysts_config.py` | Extend per Tier B. |
| `tests/unit/config/test_strategist_config.py` | Extend per Tier B. |
| `tests/analysts/test_per_ticker_branch.py` | Extend per Tier C. |
| Strategist factory test (existing or new) | Per Tier C. |
| End-to-end smoke test | Per §11.4. |

---

## 15. Acceptance criteria

1. `config/retry_429.json` exists with the rate-limit policy; the old
   `config/llm_retry.json` does not exist.
2. `config/analysts.json` and `config/strategist.json` both carry the
   new `llm` block with all four keys (`timeout_seconds`,
   `max_output_tokens`, `timeout_retries`, `schema_retries`).
3. `config/README.md` describes every new field per the project's
   config convention.
4. Every LLM-calling agent in the pipeline is constructed with
   `GenerateContentConfig(max_output_tokens=...)` matching its config
   block.
5. Every retry wrapper is constructed with `timeout_seconds`,
   `policies`, and `retry_state_key` matching its analyst's (or the
   strategist's) config block.
6. The full pytest suite passes locally:
   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest tests/ -v
   ```
7. `ruff check src/` is clean:
   ```bash
   .venv/bin/python -m ruff check src/
   ```
8. A manual replay (§11.6) shows the new `· retries <class>×<count>`
   suffix on at least one summary row.
