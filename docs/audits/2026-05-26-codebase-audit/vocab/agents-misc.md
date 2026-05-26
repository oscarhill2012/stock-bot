# agents-misc — vocabulary inventory

Exhaustive list of state keys, schema fields, config keys, internal verbs /
functions, and wrapper class names introduced or consumed by the modules
under `agents-misc`. One line each.

## State keys (read)

- `state["strategist_decision"]` — read by `MemoryWriter` (dict-or-pydantic).
- `state["memory_buffer"]` — read by `MemoryWriter` (rehydrated to `BufferEntry`).
- `state["day_digest"]` — read by `MemoryWriter` (string default `""`).
- `state["executions"]` — read by `MemoryWriter` for `executions_count`.
- `state["smart_money_evidence"]` — read by `MemoryWriter._has_real_smart_money`.
- `state["as_of"]` — read by `MemoryWriter` and `SnapshotterAgent` (via `resolve_as_of`).
- `state["tick_id"]` — read by `SnapshotterAgent`.
- `state["tick_phase"]` — read by `SnapshotterAgent` (passed to price provider).
- `state["starting_capital"]` — read by `SnapshotterAgent` (anchor on first tick).
- `state["spy_start_price"]` — read by `SnapshotterAgent` (anchor on first tick).
- `state[<retry_state_key>]` — read by `RetryingAgentWrapper` (e.g. `temp:_obs_strategist_retries`).

## State keys (write)

- `state["memory_buffer"]` — written by `MemoryWriter` (direct mutation + state_delta).
- `state["day_digest"]` — written by `MemoryWriter` (direct mutation + state_delta).
- `state["starting_capital"]` — written by `SnapshotterAgent` on first tick.
- `state["spy_start_price"]` — written by `SnapshotterAgent` on first tick.
- `state["last_snapshot"]` — written by `SnapshotterAgent` (direct + state_delta).
- `state[<retry_state_key>]` — incremented by `RetryingAgentWrapper` per retry.
- `state[<schema_error_state_key>]` — written by `RetryingAgentWrapper` before each schema retry (e.g. `temp:_last_schema_error`).

## Schema fields (`BufferEntry`)

- `timestamp: datetime`
- `decision_tag: str`
- `reasoning_summary: str` (max_length=120)
- `smart_money_seen: bool`
- `is_repeat: bool` (default False)
- `executions_count: int`
- `embedding: list[float] | None`

## Schema fields (`MemoryProjection`) — unused in production

- `recent: list[BufferEntry]`
- `tag_frequency: dict[str, int]`
- classmethod `from_buffer(buffer, n_recent=8, min_freq=3)`

## Schema fields (`RetryPolicy`)

- `max_attempts: int` (1..20)
- `backoff: Literal["immediate", "exp_jitter"]`
- `base_delay_seconds: float`
- `max_delay_seconds: float`

## Snapshot dict keys (`state["last_snapshot"]`)

- `tick_id`
- `recorded_at` (ISO string)
- `bot_total_value`
- `bot_cash`
- `bot_positions_value`
- `bot_position_count`
- `spy_price`
- `spy_value_if_held`
- `bot_return_pct`
- `spy_return_pct`
- `excess_return_pct`
- `holdings_breakdown`

## Config keys

- `config/retry_429.json` — consumed by `build_retry_policies` →
  `get_retry_429_policy()` → `max_attempts`, `base_delay_seconds`,
  `max_delay_seconds`.
- `config/models.json::memory_compressor` — consumed by
  `_default_llm_compress` via `get_models_config()`.
- `config/models.json::memory_embedding` — consumed by `_default_embed`
  via `get_models_config()`.
- `config/analysts.json` and `config/strategist.json` — consumed
  *indirectly* by the per-ticker branch factories that call
  `build_retry_policies(timeout_retries=..., schema_retries=...)`.

## Module constants

- `BUFFER_MAX = 24` (`memory/writer.py:17`) — unused.
- `BUFFER_EVICT_AT = 25` (`memory/writer.py:18`).
- `DIGEST_BUDGET = 2000` (`memory/compress.py:8`).
- `REPEAT_WINDOW = 4` (`memory/dedup.py:9`).
- `COSINE_THRESHOLD = 0.85` (`memory/dedup.py:10`).
- `_compress_llm` (module-level slot, `memory/compress.py:9`) — unused override target.
- `_embedding_provider` (module-level slot, `memory/embeddings.py:7`) — unused override target.

## Wrapper / agent class names

- `IsolatedFailureWrapper` — `src/agents/isolated_failure.py:27`.
- `RetryingAgentWrapper` — `src/agents/llm_retry.py:648`.
- `RetryPolicy` — `src/agents/llm_retry.py:375`.
- `MemoryWriter` — `src/agents/memory/writer.py:84`.
- `SnapshotterAgent` — `src/agents/snapshot/agent.py:14`.
- Module-level singleton `memory_writer = MemoryWriter()` — `memory/writer.py:195`
  (unused; pipeline uses `_build_memory_writer()` factory).

## Internal verbs / functions

### `isolated_failure.py`

- `IsolatedFailureWrapper.__init__(name, inner, analyst, ticker)`.
- `IsolatedFailureWrapper._run_async_impl(ctx)`.

### `llm_retry.py`

- `_is_rate_limit(exc)` — predicate.
- `_is_timeout(exc)` — predicate.
- `_is_schema_error(exc)` — predicate.
- `_find_validation_error(exc)` — chain walker.
- `_format_schema_error_for_llm(exc)` — prompt-feedback formatter.
- `_classify(exc) → "rate_limit" | "timeout" | "schema" | None`.
- `_compute_exp_jitter(attempt_n, base, max_)`.
- `_sleep_per_policy(policy, attempt_n)`.
- `_merge_increment(current, cls)`.
- `build_retry_policies(timeout_retries, schema_retries)`.
- `_log_retry(agent_name, cls, exc, remaining)`.
- `_log_exhausted(agent_name, cls, exc, policies, remaining)`.
- `RetryingAgentWrapper.__init__(name, inner, timeout_seconds, policies, retry_state_key, schema_error_state_key)`.
- `RetryingAgentWrapper._run_async_impl(ctx)`.

### `memory/writer.py`

- `_has_real_smart_money(state) → bool`.
- `append_with_eviction(buffer, new_entry, day_digest, compress_fn=None)`.
- `MemoryWriter._run_async_impl(ctx)`.

### `memory/compress.py`

- `set_compress_llm(fn)` — unused setter.
- `compress(prev_digest, evicted_entry, llm_fn=None)`.
- `_default_llm_compress(prev_digest, new_summary)`.

### `memory/dedup.py`

- `detect_repeat(new_entry, recent_buffer, embed_fn) → bool`.

### `memory/embeddings.py`

- `set_embedding_provider(fn)` — unused setter.
- `embed(text)`.
- `_default_embed(text)` — wraps `tenacity.retry` around Vertex.
- `cosine_similarity(a, b) → float`.

### `memory/schema.py`

- `BufferEntry` (Pydantic).
- `MemoryProjection.from_buffer(buffer, n_recent, min_freq)` — unused.

### `snapshot/agent.py`

- `SnapshotterAgent._run_async_impl(ctx)`.
- `build_snapshotter(broker, db_session=None) → SnapshotterAgent`.

## Structured-log `kind` values emitted

- `"branch_failed"` (`isolated_failure.py`).
- `"llm_retry_attempt"` (`llm_retry._log_retry`).
- `"llm_retry_exhausted"` (`llm_retry._log_exhausted`).

## External imports of note

- `from data.timeguard import resolve_as_of` — used by both `memory/writer.py`
  and `snapshot/agent.py` for as_of coercion.
- `from data import get_price_history` — lazy import in `snapshot/agent.py`
  for the SPY fetch.
- `from orchestrator.persistence import save_portfolio_snapshot` — lazy
  import in `snapshot/agent.py` for DB write (when `db_session` provided).
- `from config.retry_429 import get_retry_429_policy` — used by
  `build_retry_policies`.
- `from config.models import get_models_config` — used by both
  `_default_llm_compress` and `_default_embed`.
