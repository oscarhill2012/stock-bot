# Source audit — `src/agents/analysts/{technical,social,smart_money}` + analyst-level plumbing

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 11 (3 analyst pairs + 5 plumbing files)
**Findings:** 1 P0 · 5 P1 · 5 P2 · 2 P3

## Summary

The three deterministic analyst packages (Technical, Social, SmartMoney) share an
identical three-hook BaseAgent shape: `before_agent_callback` fetches raw data
into `temp:<analyst>_data`, `_run_async_impl` runs the deterministic extractor
plus heuristic, and `make_evidence_callback` (shared in `_common.py`) materialises
`AnalystEvidence`. The shape is already well-factored — `_common.py` carries the
post-extractor evidence loop and the four-analyst extractor signature is uniform
— so duplication between the three modules is mostly mechanical (boilerplate
`__init__` plumbing, identical `_build_*` factory wrappers, identical trace
labelling) rather than real logic divergence.

Three themes dominate the findings. (1) **SmartMoney is the odd one out and
broken on multiple axes** — pipeline-shelved, fetch writes a non-`temp:` key
that `_common.make_evidence_callback` cannot find, `_run_async_impl` writes
verdicts directly to state (Rule 1 violation), and the typed-Pydantic raw-shape
is the only one of the three that diverges from the `temp:<analyst>_data`
contract. (2) **`as_of` coercion is inconsistent across the three analysts** —
Technical does the right thing (`resolve_as_of` at the top of
`_run_async_impl`), Social and SmartMoney both call `state.get("as_of") or None`
and pass the raw ISO string through to the extractor, contradicting the
"`as_of` boundary coercion is mandatory" feedback rule. (3) **Dead code in
`report_cache.py`** — `log_cache_hit_to_state` is documented as a no-op since
S3 but every call site still invokes it; both call sites should be inlined out.

Cross-subsystem dependency for the consolidator: the SmartMoney findings here
imply a §A row decision for `contract-invariants.md` (currently the four
"contract-bearing" verdict rows do not include `smart_money_verdicts` /
`smart_money_evidence`). The `_common.py` extractor signature also is reused by
the per-ticker News/Fundamental branches — any change to it has cross-cutting
impact on the fundamental/news audit subsystems.

## Findings

### P0-01 · C4 contract violation · `smart_money/fetch.py` writes a non-`temp:` key that breaks `make_evidence_callback`

- **Location:** `src/agents/analysts/smart_money/fetch.py:135`, `src/agents/analysts/smart_money/agent.py:117`, `src/agents/analysts/_common.py:98`
- **Confidence:** high
- **Description:**
  `smart_money_fetch_callback` writes `state["smart_money_data"] = ...`, and
  `SmartMoneyAnalyst._run_async_impl` reads back from the same bare key. But the
  shared `make_evidence_callback` (which runs as the analyst's
  `after_agent_callback`) reads `state[f"temp:{analyst}_data"]` — i.e.
  `state["temp:smart_money_data"]`. That key never exists, so the
  `data: dict = state.get(f"temp:{analyst}_data", {}) or {}` fall-through hands
  the extractor `{}` for every ticker on the evidence-build pass. (The
  `_run_async_impl` body still produces verdicts because it reads the bare key;
  but the evidence record's `features` are computed from empty input and any
  cross-pass shape comparison silently fails.) The pipeline currently shelves
  the SmartMoney analyst at `src/orchestrator/pipeline.py:88` so the bug does not
  fire in production today — but the comment there says the analyst is
  re-enabled by a "one-line uncomment", which makes this a P0 latent foot-gun.
  Compare Technical and Social, which both write and read `temp:<analyst>_data`
  consistently end-to-end (see `technical/fetch.py:90`, `social/fetch.py:77`,
  `_common.py:98`). Phase 2-style cross-tick rules also prefer the `temp:`
  prefix for invocation-scoped raw-data buffers (Rule 2 / `state.py:71-75`
  block-comment).
- **Suggested action:**
  Rename to `state["temp:smart_money_data"]` in both `fetch.py:135` and
  `agent.py:117`; remove the corresponding bare field from `orchestrator/state.py:80`
  in the same pass. Cross-check the decision-logger consumer at
  `backtest/decision_logger.py:314-317`, which also reads the bare key.

### P1-01 · C4 contract violation · `SmartMoneyAnalyst._run_async_impl` writes verdicts directly to state

- **Location:** `src/agents/analysts/smart_money/agent.py:153`
- **Confidence:** high
- **Description:**
  `SmartMoneyAnalyst._run_async_impl` ends with
  `state["smart_money_verdicts"] = verdicts; return; yield` — a direct dict write
  followed by an empty generator gate. Both Technical and Social, by contrast,
  yield an `Event(actions=EventActions(state_delta={...}))` (see
  `technical/agent.py:150-154`, `social/agent.py:133-137`) per §C-Rule 1. The
  in-tick callback carve-out does not apply (this is a `_run_async_impl`, not an
  `after_agent_callback`). The bug is currently dormant only because SmartMoney
  is shelved in the pipeline; on re-enable, `state["smart_money_verdicts"]`
  would be lost on any non-`InMemorySessionService` backend, which Spec B has
  now made the live default. The agent's own docstring (lines 13-16) even
  describes this as "the same pattern used by TechnicalAnalyst,
  SocialAnalyst" — but Technical and Social have since moved to `state_delta`;
  the docstring is stale (a C7 doc/code drift in its own right).
- **Suggested action:**
  Replace the direct write with a yielded `Event` whose
  `actions=EventActions(state_delta={"smart_money_verdicts": verdicts})`,
  matching Technical and Social. Drop the trailing `return; yield` gate (the
  yield in the Event path makes the function a real async generator). Update
  the class docstring to remove the "same pattern as RiskGateAgent / MemoryWriter"
  claim.

### P1-02 · C5 silent-failure attractor · `social/agent.py` and `smart_money/agent.py` skip `resolve_as_of`

- **Location:** `src/agents/analysts/social/agent.py:110`, `src/agents/analysts/smart_money/agent.py:121`
- **Confidence:** high
- **Description:**
  Both bodies use `as_of = state.get("as_of") or None` and pass the raw value
  to the extractor. In the backtest lifecycle, `state["as_of"]` is an ISO-8601
  string (the backtest driver writes it as a string because `DatabaseSessionService`
  cannot serialise raw `datetime`s — see comment in `technical/agent.py:111-118`).
  The user-memory entry "as_of boundary coercion is mandatory" requires every
  read of `state["as_of"]` to go through `data.timeguard.resolve_as_of`. Social
  is currently saved by the extractor being clock-free ("`as_of: …` — reserved
  for future velocity computation" — `social.py:88`), and SmartMoney is saved by
  being pipeline-shelved. Both are landmines: the moment the social extractor
  starts using the timestamp (e.g. for snapshot recency) or SmartMoney is
  re-enabled, a backtest tick will pass a `str` where a `datetime` is expected
  and the failure mode is whatever the extractor does on `str.timestamp()` — a
  classic silent-degradation attractor. Technical does it correctly at
  `technical/agent.py:118-120`.
- **Suggested action:**
  Replace `state.get("as_of") or None` with
  `resolve_as_of(state.get("as_of"), allow_wallclock=True, site="<analyst>/agent")`
  in both modules, matching `technical/agent.py`. Drop the "extractor is clock-free
  so this doesn't matter" comments — that is exactly the assumption that turns a
  P1 into a P0 on the day a future extractor change reads `as_of`.

### P1-03 · C4 contract violation · evidence callback writes to `state["<analyst>_evidence"]` directly

- **Location:** `src/agents/analysts/_common.py:175`
- **Confidence:** medium
- **Description:**
  `make_evidence_callback` is wired as an `after_agent_callback` on Technical,
  Social, and (currently shelved) SmartMoney, and ends with
  `state[f"{analyst}_evidence"] = evidence_list`. ADK's
  `_handle_after_agent_callback` auto-yields a `state_delta` event from the
  delta-tracking `CallbackContext.state`, so the write is durable — i.e. it
  rides Rule 1's auto-yielded carve-out described in `contract-invariants.md`
  §C-Rule 1 "Auto-yielded delta-tracked callback writes". So this is conformant
  by construction *provided the runtime is ADK ≥ 1.34 and the state object is
  a real `CallbackContext.state` proxy*. The audit caveat is that the
  invariants doc only names `user:`-prefixed keys in its example of the
  auto-yielded path; the `<analyst>_evidence` keys are not user-scoped, are
  tick-scoped, and are not in the §A table at all — they only show up in
  `evidence_writer.py:27-31` as durable database persistence sources. If the
  evidence keys are intended to ride the auto-yield path, that intent should be
  documented; if they are meant to be `temp:`-prefixed (like the data keys),
  they are misnamed.
- **Suggested action:**
  Either (a) add a §A row for each `<analyst>_evidence` key with
  "auto-yielded callback write" listed as the persistence mechanism, or (b)
  rename to `temp:<analyst>_evidence` and update `EvidenceWriter` to read the
  prefixed key. Defer to consolidation — both options are non-trivial.

### P1-04 · C5 silent-failure attractor · fetch callbacks swallow per-ticker exceptions to `None` / `[]`

- **Location:** `src/agents/analysts/technical/fetch.py:70-71,78-79`, `src/agents/analysts/social/fetch.py:59-61`, `src/agents/analysts/smart_money/fetch.py:112-114,123-125`
- **Confidence:** high
- **Description:**
  Every fetch callback wraps each provider call in `try / except Exception` and
  drops to a benign `None` / `[]` on failure with only a `logger.warning(...)`.
  The downstream extractor then sees the empty payload and emits a no-data
  feature vector, which the deterministic heuristic converts into a `neutral`
  verdict carrying `is_no_data=True`. The pipeline runs end-to-end with zero
  signal but no surface raises. Per test-policy §A.7 and the
  "silent failures are the recurring bug class" memory entry, this is the
  canonical attractor: a real API outage looks identical to "the ticker has
  no congressional trades". The `branch_failed` event the test-policy alludes
  to is never emitted because there is no exception to bubble. The `except
  Exception:` clause is also broader than necessary — provider implementations
  raise specific HTTP / parse exceptions.
- **Suggested action:**
  Narrow the `except` to the specific exception types each provider documents,
  let unknown exceptions propagate (the analyst's enclosing
  `IsolatedFailureWrapper` will degrade gracefully at the *branch* level rather
  than per ticker), and at minimum convert the warning into a structured log
  record with `kind="provider_fetch_failed"` so the obs-log audit can spot
  systematic outages.

### P1-05 · C1 dead code · `log_cache_hit_to_state` is a no-op but still called

- **Location:** `src/agents/analysts/report_cache.py:547-582`, `src/agents/analysts/cache_callbacks.py:216-222`
- **Confidence:** high
- **Description:**
  `log_cache_hit_to_state` is documented as "No-op — audit drains report-cache
  hits from `obs/logs/` since S3." The function body is literally `return None`.
  Every cache-hit path in `cache_callbacks.py:_before` still calls it and packs
  five keyword arguments at the call site. Verified callers via
  `grep -rn "log_cache_hit_to_state"` — only `cache_callbacks.py:80,216` and
  the definition itself. The function is preserved purely as a vestigial
  signature; removing the call site changes nothing observable.
- **Suggested action:**
  Delete `log_cache_hit_to_state` from `report_cache.py` and remove the call
  at `cache_callbacks.py:216-222`. Keep the structured `report_cache_hit` log
  emit at line 224-234 — that is the audit source of truth per the docstring.

### P2-01 · C7 doc/code drift · `SmartMoneyAnalyst` docstring claims a pattern it no longer follows

- **Location:** `src/agents/analysts/smart_money/agent.py:13-16, 105-108`
- **Confidence:** high
- **Description:**
  The class docstring says SmartMoney writes verdicts "directly to session
  state (same pattern as `TechnicalAnalyst`, `SocialAnalyst`, `RiskGateAgent`,
  and `MemoryWriter`)". Technical and Social no longer use direct writes — they
  yield `state_delta` events (see P1-01). The `_run_async_impl` docstring at
  line 105-108 makes the same claim. RiskGate/MemoryWriter parity is also
  worth a re-check during the consolidation pass.
- **Suggested action:**
  When P1-01 is fixed, the docstring updates land in the same patch.

### P2-02 · C3 overabstraction-inverted · `_build_<analyst>` factories add nothing over the module-level singleton

- **Location:** `src/agents/analysts/technical/agent.py:162-174`, `src/agents/analysts/social/agent.py:145-157`, `src/agents/analysts/smart_money/agent.py:168-183`
- **Confidence:** medium
- **Description:**
  Each module exposes both a module-level singleton (`technical_analyst`,
  `social_analyst`, `smart_money_analyst`) AND a private `_build_<analyst>`
  factory whose only behaviour is `load_heuristics().<sub>` (lru-cached) plus a
  fresh class construction. The pipeline (`orchestrator/pipeline.py:66-67`)
  uses the factory; tests use the singleton. The factories' "fresh closure per
  call" claim in the docstring is true but moot — heuristics are frozen
  Pydantic models, callbacks are pure functions, and the two construction
  paths produce indistinguishable objects. The cost is three near-identical
  10-LOC factories and a mental model where readers wonder why both exist. The
  fix is *more* sharing, not less: move the body into `_common.py` as
  `build_deterministic_analyst(cls, heuristics_section_name)` parameterised by
  the analyst class.
- **Suggested action:**
  Promote `_build_<analyst>` and the singleton construction into a single
  factory in `_common.py`; have each `agent.py` expose only the class and a
  one-line module-level singleton.

### P2-03 · C3 overabstraction-inverted · three near-identical `_run_async_impl` bodies

- **Location:** `src/agents/analysts/technical/agent.py:82-154`, `src/agents/analysts/social/agent.py:82-137`, `src/agents/analysts/smart_money/agent.py:88-160`
- **Confidence:** medium
- **Description:**
  The three deterministic-analyst run-loops differ only in three places:
  (a) which raw-data state key they read, (b) which extractor + heuristic-derive
  pair they call, (c) which trace label they emit. Otherwise the loop body is
  identical: pull tickers, resolve clock, build a list of verdict dicts with a
  `ticker` field, trace, yield the `state_delta`. Today's `_common.py` carries
  the post-extractor `make_evidence_callback` but stops short of generalising
  the run-loop itself. A single `make_deterministic_run_impl(*, analyst,
  extractor, derive_verdict, data_key, trace_label)` helper would collapse
  three 70-LOC `_run_async_impl` bodies into one ~25-LOC shared implementation.
  Filing as medium because the proposed shared helper has not been written and
  the loop-body differences (e.g. Technical needing `state` passed to the
  extractor for `reference_prices`, Social iterating over `social_data.items()`
  not `tickers`) need a careful merge.
- **Suggested action:**
  Add a `run_deterministic_analyst(...)` helper to `_common.py` and have each
  analyst's `_run_async_impl` delegate to it. Fold the `as_of` coercion fix
  (P1-02) into the same helper so all three sites pick up the correct
  behaviour at once.

### P2-04 · C3 overabstraction · `make_evidence_callback`'s `verdicts_state_key` parameter is redundant with `analyst`

- **Location:** `src/agents/analysts/_common.py:27-178`
- **Confidence:** medium
- **Description:**
  Every call site passes `verdicts_state_key=f"{analyst}_verdicts"` —
  `"technical"` → `"technical_verdicts"`, `"social"` → `"social_verdicts"`,
  `"smart_money"` → `"smart_money_verdicts"` (see `agent.py:74-78` in each
  package). The parameter exists only to keep the helper "generic" in a way
  the codebase does not exercise. Removing it eliminates one source of
  config drift between the three call sites and makes the helper's contract
  (`state["{analyst}_verdicts"]` is read, `state["{analyst}_evidence"]` is
  written) symmetric and self-evident.
- **Suggested action:**
  Drop `verdicts_state_key` from the signature; derive it from `analyst`
  inside the helper. Same for the implicit `temp:{analyst}_data` read key.

### P2-05 · C2 parallel old/new branches · two-shape verdict unwrap in `make_evidence_callback`

- **Location:** `src/agents/analysts/_common.py:106-110`
- **Confidence:** medium
- **Description:**
  The helper handles two shapes: deterministic analysts write
  `list[dict]`, while LlmAgent analysts write `{"verdicts": [...]}` (per
  ADK's `output_schema=VerdictBatch`). After Phase 9 the LlmAgent analysts
  (News, Fundamental) became per-ticker, and the joiner agents now own the
  consolidation step. The `{"verdicts": [...]}` shape is therefore a legacy
  branch — no current call site of `make_evidence_callback` lands the wrapped
  shape into state (verified: News/Fundamental no longer wire this callback;
  only Technical/Social/SmartMoney do, all three emit `list[dict]` directly).
  The branch survives as defensive code with no live consumer.
- **Suggested action:**
  Delete the `isinstance(raw, dict) and "verdicts" in raw` branch in
  `_common.py:107-110`. Cross-check during consolidation against the
  fundamental/news joiner audit — if any future path reintroduces the wrapped
  shape, the helper should reject it loudly rather than silently coerce.

### P3-01 · C7 doc/code drift · `social/__init__.py` describes the wrong agent shape

- **Location:** `src/agents/analysts/social/__init__.py:1-7`
- **Confidence:** high
- **Description:**
  The package docstring says "The Social analyst is a `LlmAgent` whose
  `before_agent_callback` (fetch) computes verdicts deterministically and
  returns a skip-Content so the LLM is never invoked." The actual `SocialAnalyst`
  in `agent.py:40` is a `BaseAgent` subclass and has been since the Phase 5
  redesign. The `__init__.py` documents an architecture that was retired
  multiple commits ago.
- **Suggested action:**
  Replace the docstring with: "Deterministic Social analyst — `BaseAgent`
  subclass that runs `extract_social_features` + `derive_social_verdict` in
  `_run_async_impl` and exposes the verdict list via a `state_delta` event."
  Land alongside any other change to this package.

### P3-02 · C7 doc/code drift · `_common.py` module docstring references a removed class

- **Location:** `src/agents/analysts/_common.py:1-13`
- **Confidence:** medium
- **Description:**
  The module docstring says "The legacy `AnalystSignal` Pydantic class is also
  removed — the four per-analyst `schema.py` subclasses are deleted alongside
  it (see D3 option-a)." This is fine as historical record but reads as
  active-tense ongoing work. Confirm `AnalystSignal` is truly gone (verified —
  `grep -rn AnalystSignal src/` returns nothing) and rewrite the paragraph in
  past tense or drop the reference entirely.
- **Suggested action:**
  Trim the D3 historical commentary to a single sentence (or remove). The
  current docstring lifecycle commentary is more useful as a commit message
  than as standing module documentation.
