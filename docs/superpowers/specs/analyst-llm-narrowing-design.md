# Analyst LLM Narrowing — Design Spec

> **Status:** specced. Implementation plan to follow under `docs/superpowers/plans/`.
> **Phase:** Post-Phase-4. Parallel to Plan E (strategist hardening); does not block or depend on it.

This spec narrows the analyst tier so every surviving LLM call is justified by a job that only an LLM can do (reading prose). The two analysts whose LLM had no information advantage over their deterministic feature extractor are converted to pure-Python `BaseAgent`s. The two analysts whose underlying data is genuinely narrative (SEC filing excerpts; news headlines) keep their LLM but with a closed-vocabulary, prose-only mandate that is RAG-ready.

---

## Why

Phase 4 introduced deterministic per-analyst feature extractors plus a deterministic digest. With that contract in place, the per-analyst LLM verdicts became, in two of four cases, parasitic: they re-derive a `lean / magnitude / confidence` triple from the same numbers the extractor already exposes, with no prose available to add anything the extractor cannot. Concretely:

- **Technical** — the LLM sees OHLCV; the extractor sees the same OHLCV reduced to RSI / momentum / volatility / 52w-distance. The LLM has no narrative slice. There is no future RAG enhancement that gives technical a prose channel — OHLCV does not have one.
- **Smart_money** — the LLM sees congressional filings reduced to dollar amounts and sides. The current prompt asks it to "weigh 13D vs 13G, executive vs director" — categorical scoring, not prose reading. Any future RAG over 13D *letters* or Form-4 *footnotes* would be a distinct narrative agent; today's LLM has no letters or footnotes to read.
- **Fundamental** — `risk_factors_excerpt` and `mda_excerpt` are present in every filing dict (`edgartools` extracts them in-process). Prose is genuinely available. The current prompt under-uses it.
- **Sentiment** — news headlines + article summaries are present. Headlines need *catalyst classification* and *novelty detection* — both prose-shaped jobs the extractor's polarity arithmetic cannot do.

The Phase 4 contract makes this swap clean. `AnalystEvidence` carries `verdict: AnalystVerdict` as one field. Replacing that verdict's source (LLM → deterministic rule) does not touch the digest, the strategist prompt, the persistence ORM, the risk gate, or the executor.

## Goals

1. Every surviving analyst LLM has a prose-only mandate the deterministic extractor cannot fulfil.
2. Every analyst (deterministic or LLM) emits the same `AnalystEvidence` shape, so downstream code is unchanged.
3. `key_factors` on every verdict becomes a closed-vocabulary tag list, indexable by a future KB without backfill.
4. Heuristic thresholds and tag vocabularies live in `config/analyst_heuristics.json`, not in code.
5. The refactor is the first **live-LLM-validated** milestone for the project: a surface-trace dry-run captures full JSON at every pipeline boundary for manual inspection.

## Non-goals

- Designing or implementing RAG, vector stores, or filings KBs. The spec only requires that this refactor leaves a clean slot for that work.
- Sparse / triggered LLM execution (only call fundamental when a new filing has landed). The current every-tick batched call survives; sparseness is a natural follow-up after KB lands.
- Persisting cross-filing diffs as separate columns. The suffix-tag scheme (`risk:*_added | risk:*_removed | risk:*_intensified`) carries that information inside the existing `key_factors` JSON.
- Embedding columns, prompt versioning systems, or LLM cost telemetry.
- Touching the strategist's prompt, agent code, or schema. Plan E covers strategist hardening separately.

---

## Per-analyst decisions

| Analyst | Today | After | Rationale |
|---|---|---|---|
| **Technical** | `LlmAgent` reads OHLCV dump | `BaseAgent` runs `extract_technical_features` then `derive_technical_verdict(features) → AnalystVerdict` | No prose now, no prose ever. RAG cannot help. Strip with no future regret. |
| **Smart_money** | `LlmAgent` reads filings dump | `BaseAgent` runs `extract_smart_money_features` then `derive_smart_money_verdict(features) → AnalystVerdict` | Today's LLM only classifies counts; it does not read letters or footnotes. A future narrative LLM that reads 13D letters would be a distinct agent. |
| **Sentiment** | `LlmAgent` with generic "look at headlines" prompt | `LlmAgent` kept; prompt narrowed to catalyst classification + novelty + materiality, drawn from a closed vocabulary | Headlines are genuine prose. Becomes the seat for future news-RAG. |
| **Fundamental** | `LlmAgent` with generic "look at MD&A" prompt | `LlmAgent` kept; prompt narrowed to risk-factor + MD&A reading only, drawn from a closed vocabulary | MD&A and Risk Factors are genuine prose, already in the data. Becomes the seat for future filings-RAG. |

After the refactor: two `BaseAgent` analysts, two `LlmAgent` analysts, same `ParallelAgent` pool, same evidence contract.

---

## Deterministic verdict heuristics

Both functions live next to their existing extractor under `src/contract/extractors/`. Both consume only the locked feature catalogue defined in Phase 4 Plan A; neither extends the catalogue. Thresholds come from `config/analyst_heuristics.json` (see Configuration below).

### `derive_technical_verdict(features, h: TechnicalHeuristics) → AnalystVerdict`

- **`is_no_data`** when `features["rsi_14"] == 0 and features["pct_change_20d"] == 0 and features["atr_pct_14"] == 0` (the extractor's zero-on-empty fingerprint). Returns `lean="neutral"`, `magnitude=0`, `confidence=0`, `is_no_data=True`.
- **Lean** from a composite trend sign: `sign(pct_change_20d)` weighted by agreement with `sign(pct_change_5d)`. Two flips override the trend:
  - `rsi_14 > h.rsi_overbought` → cap lean at neutral; flip to bearish if `pct_change_5d > 0` (exhaustion).
  - `rsi_14 < h.rsi_oversold` → cap at neutral; flip to bullish if `pct_change_5d < 0` (capitulation).
- **Magnitude**: `clamp(|pct_change_20d| × h.pct_change_momentum_scale, 0, h.magnitude_cap)`; boosted when `vol_ratio_20d > h.vol_ratio_breakout`, dampened when `vol_ratio_20d < h.vol_ratio_dry_up`.
- **Confidence**: starts at `h.confidence_base`; `+h.confidence_boost_step` when 5d and 20d momentum agree; `+h.confidence_boost_step` when within `h.near_52w_extreme_pct` of either 52w extreme; `-h.confidence_penalty_step` when `atr_pct_14 > h.atr_high_volatility_pct`. Clamped `[0, 1]`.
- **`key_factors`**: drawn from the closed vocabulary
  `{trend_up_20d, trend_down_20d, momentum_agree, momentum_disagree, rsi_overbought, rsi_oversold, near_52w_high, near_52w_low, vol_breakout, vol_dry_up, high_volatility}`.
  The function picks every tag that fired.
- **`rationale`**: short template assembled from the fired key_factors (e.g. `"trend_up_20d + vol_breakout, but rsi_overbought"`). Deterministic, ≤160 chars.

### `derive_smart_money_verdict(features, h: SmartMoneyHeuristics) → AnalystVerdict`

- **`is_no_data`** when `features["is_no_data"] == 1.0` (extractor flag). Returns neutral / 0 / 0 / `is_no_data=True`.
- **Lean** from `sign(net_flow_dollar)`.
- **Magnitude**: `clamp(|net_flow_dollar| / (total_dollar_value_buys + total_dollar_value_sells + 1), 0, h.magnitude_cap)` — flow asymmetry, not absolute dollar size.
- **Confidence**: floor of `h.lone_filer_confidence_floor` when only one filer or one trade; ceiling of `h.consensus_confidence_ceiling` when `n_politicians ≥ h.multi_filer_min_count` and `(n_buys_30d + n_sells_30d) ≥ h.high_activity_trade_count`; linearly interpolated between.
- **`key_factors`**: closed vocabulary
  `{net_buying, net_selling, multi_filer_consensus, lone_filer, high_volume_flow, mixed_activity}`.
- **`rationale`**: template from fired key_factors, ≤160 chars.

Both functions are pure (no I/O, no globals), deterministic, and unit-testable as plain Python.

---

## Narrowed LLM mandates

Both LLM analysts keep their `LlmAgent` shape, their fetch callback, their `make_evidence_callback` after-callback, and their `output_key` state-write convention. What changes is the prompt and the vocabulary it enforces.

### FundamentalAnalyst — prose-only mandate

The prompt is rebuilt against a single rule: the LLM reasons over `risk_factors_excerpt` and `mda_excerpt` *only*. Numeric ratios are removed from the prompt entirely; those features are already in the extractor's output and feed the aggregate independently. The prompt instructs the model to classify, per ticker:

- `guidance`: one of `{raised, maintained, lowered, none}`.
- `going_concern`: boolean.
- `new_risks` / `removed_risks`: ≤3 each from the closed risk vocabulary.
- `mda_tone`: one of `{confident, cautious, defensive, mixed}`.

The model then derives an `AnalystVerdict` from these structured findings:

- **lean** from `(guidance, mda_tone)` jointly (e.g. `guidance=raised + tone=confident → bullish`).
- **magnitude** from severity of findings (e.g. `going_concern=true` → high; minor risk additions → low).
- **confidence** from filing recency (`days_since_filed`).
- **rationale**: ≤160 chars naming the dominant finding.
- **`key_factors`**: structured tags only, using the prefix-vocabulary scheme:
  - `guidance:<value>` from the guidance vocabulary.
  - `tone:<value>` from the tone vocabulary.
  - `risk:<value>` from the risk vocabulary, optionally suffixed with `_added | _removed | _intensified` when the comparison vs. the prior filing in the dump warrants.
  - `going_concern:true` when flagged.
- **`is_no_data`**: true when no excerpts are present.

The full prompt template lives in `src/agents/analysts/fundamental/prompts.py`. The vocabulary placeholders (`{guidance_options}`, `{tone_options}`, `{risk_tags}`) are substituted at agent-construction time from `config/analyst_heuristics.json` so adding a tag is a config change, not a code change.

### SentimentAnalyst — prose-only mandate

Same shape, different vocabulary. The LLM reasons over headlines + article summaries only; polarity statistics are removed from the prompt. Per ticker, the model classifies:

- `dominant_catalyst` from the catalyst vocabulary.
- `novelty` from the novelty vocabulary.
- `direction` from the direction vocabulary.
- `material`: boolean (would a long-only fund act on this?).

Derivation:

- **lean** from `direction` (positive → bullish, negative → bearish, mixed/none → neutral).
- **magnitude** from `novelty × material`.
- **confidence** scales with headline count (low if `< 3` articles).
- **`key_factors`**: `[catalyst:<type>, novelty:<level>, direction:<value>, material:<bool>]`.
- **`is_no_data`**: true when no headlines in window.

The prompt template lives in `src/agents/analysts/sentiment/prompts.py` and resolves vocabulary placeholders at construction time, identical to fundamental.

---

## Contract invariants — what does NOT change

| Surface | Status |
|---|---|
| `AnalystEvidence` Pydantic schema | unchanged |
| `AnalystVerdict` Pydantic schema | unchanged |
| `TickerEvidence` / `AggregateVerdict` schema | unchanged |
| `build_ticker_evidence` / digest math | unchanged |
| State key `{analyst}_data` (fetch output) | unchanged |
| State key `{analyst}_evidence` (analyst output) | unchanged |
| State key `ticker_evidence` (digest output) | unchanged |
| `EvidenceWriter` and `AnalystEvidenceRow` / `TickerEvidenceRow` ORM | unchanged |
| Strategist prompt, agent, schema | unchanged |
| `risk_gate`, `executor`, `memory_writer` | unchanged |
| `ParallelAgent` analyst pool composition | unchanged (children swap class, pool is invariant) |

The state key `{analyst}_verdicts` is *internal* to LLM analysts (their `output_key`). Deterministic analysts do not write this key — they go straight from features → verdict → evidence.

A pre-spec audit confirmed no downstream code parses the *content* of `rationale` or `key_factors` as free text:

- `evidence_view.py:57` treats them as opaque strings.
- `digest.py` reads only `lean`, `confidence`, `magnitude`, `is_no_data`.
- `EvidenceWriter` JSON-dumps the verdict.
- `memory/writer.py` reads aggregate fields only.

---

## Configuration

### `config/analyst_heuristics.json` (new file)

```json
{
  "technical": {
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "pct_change_momentum_scale": 4.0,
    "vol_ratio_breakout": 1.5,
    "vol_ratio_dry_up": 0.7,
    "atr_high_volatility_pct": 5.0,
    "near_52w_extreme_pct": 5.0,
    "confidence_base": 0.5,
    "confidence_boost_step": 0.2,
    "confidence_penalty_step": 0.3,
    "magnitude_cap": 1.0
  },
  "smart_money": {
    "multi_filer_min_count": 3,
    "high_activity_trade_count": 5,
    "lone_filer_confidence_floor": 0.1,
    "consensus_confidence_ceiling": 0.9,
    "magnitude_cap": 1.0
  },
  "fundamental_vocabulary": {
    "guidance":  ["raised", "maintained", "lowered", "none"],
    "tone":      ["confident", "cautious", "defensive", "mixed"],
    "risks":     ["regulatory", "litigation", "cybersecurity", "supply_chain",
                  "macro", "competition", "key_person", "debt_refinance",
                  "going_concern", "guidance_change", "customer_concentration"]
  },
  "sentiment_vocabulary": {
    "catalysts": ["earnings", "guidance", "m_and_a", "regulatory",
                  "product_launch", "legal", "macro", "downgrade",
                  "upgrade", "none"],
    "novelty":   ["high", "medium", "low"],
    "direction": ["positive", "negative", "mixed", "none"]
  },
  "golden_set": {
    "min_direction_agreement_pct": 70
  }
}
```

The risk vocabulary's suffix scheme (`_added | _removed | _intensified`) is documented in `config/README.md`; the JSON lists the base tags only, the suffix combinations are implied by the prompt template.

### `src/agents/analysts/heuristics.py` (new module)

Frozen Pydantic models for each section (`TechnicalHeuristics`, `SmartMoneyHeuristics`, `FundamentalVocabulary`, `SentimentVocabulary`, `GoldenSetConfig`) plus the top-level `AnalystHeuristics`. Field validators enforce ranges (`rsi_overbought ∈ [50, 100]`, `confidence_base ∈ [0, 1]`, etc.). The loader function `load_heuristics()` reads the JSON, validates into `AnalystHeuristics`, and caches via `functools.lru_cache(maxsize=1)` — same pattern as `src/data/config.py:get_config()`.

### Injection

Each agent factory takes its config section at construction:

- `build_technical_analyst(h: TechnicalHeuristics) → BaseAgent`
- `build_smart_money_analyst(h: SmartMoneyHeuristics) → BaseAgent`
- `build_fundamental_analyst(vocab: FundamentalVocabulary) → LlmAgent`
- `build_sentiment_analyst(vocab: SentimentVocabulary) → LlmAgent`

`src/orchestrator/pipeline.py::build_pipeline()` calls `load_heuristics()` once and threads each section into the corresponding factory. The `ParallelAgent` composition is otherwise identical.

### Lifecycle integration

`src/orchestrator/lifecycle.py::initialise()` gains one new check:

```python
def _check_heuristics() -> None:
    """Fail-fast load of analyst heuristics. Surfaces JSON errors at boot."""
    load_heuristics()  # raises ValidationError if malformed
```

Matches the `_check_env` / `_check_broker_cash` pattern. Misconfiguration prevents startup rather than crashing on tick 1.

### Hot-reload — explicitly not supported

Heuristics load once at startup and cache. Changing a threshold requires a bot restart. Consistent with `data.json` treatment and avoids mid-tick consistency hazards across the parallel pool.

---

## Persistence — KB-readiness without migration

No ORM schema changes are required. The existing `AnalystEvidenceRow` already carries `features` (JSON), `verdict` (JSON, containing `rationale` and `key_factors`), and `feature_warnings` (JSON list). The discipline this spec adds:

- Every `key_factors` entry is a tag from the closed vocabulary. Bare tags (`trend_up_20d`, `net_buying`) for the deterministic analysts; prefixed (`guidance:lowered`, `risk:cybersecurity_added`) for the LLM analysts.
- The suffix scheme `_added | _removed | _intensified` on risk tags encodes cross-filing comparison without a new column.
- `rationale` becomes templated (deterministic analysts) or structured-summary (LLM analysts) rather than free-form.

### One new index

A composite index on `AnalystEvidenceRow(analyst, ticker, recorded_at)` is added in the same PR. Justification: useful immediately for per-ticker history retrieval (replay, debug); essential when the future KB scans per-ticker history. Declared as a SQLAlchemy `Index(...)` on the model; picked up by `create_all()` automatically (project is not on Alembic yet).

### Example future-KB query (informational; not implemented)

```sql
-- "When did AAPL first add cybersecurity to its risk factors?"
SELECT MIN(recorded_at)
FROM analyst_evidence
WHERE ticker = 'AAPL'
  AND analyst = 'fundamental'
  AND json_extract(verdict, '$.key_factors') LIKE '%risk:cybersecurity_added%';
```

The exact JSON-query syntax differs between SQLite (`json_extract`) and Postgres (`jsonb @>`), but the data shape supports both. Including this example in the spec so future readers see the projection path without re-deriving it.

### What we explicitly do NOT add

- No embeddings column. Vector retrieval is a Phase-6 stack decision.
- No `prior_evidence_id` foreign key. Suffix tags capture the comparison.
- No new tables. `kb_entry`, `narrative_note`, `filing_excerpt_store` are out of scope.
- No retention / TTL fields.

---

## Surface tracing — first live-LLM validation

This is the milestone for the project's first real LLM run. T1 unit tests and T2 integration smoke tests validate structure; only a real-LLM, single-ticker trace validates that the new prompts and the new contract land what we think they do.

### Trace file shape

One JSON file per tick at `docs/surface-traces/<tick_id>-<ticker>.json` (gitignored), with ordered, labelled sections — one per pipeline boundary. Sections are stage-numbered so the file reads top-to-bottom as the data's journey:

| Stage | Sections | Captured |
|---|---|---|
| 01 — Fetch | `01_fetch_{analyst}` (×4) | Raw `{analyst}_data` payload for the tested ticker |
| 02 — Deterministic verdicts | `02_{technical,smart_money}_verdict` | features + verdict for the tested ticker |
| 03 — LLM verdicts | `03_{fundamental,sentiment}_llm_in`, `_llm_out`, `_verdict` | Rendered prompt, raw LLM response, parsed verdict |
| 04 — Digest | `04_digest` | `ticker_evidence` payload |
| 05 — Strategist | `05_strategist_llm_in`, `_llm_out`, `_decision` | Rendered strategist prompt, raw LLM response, parsed `StrategistDecision` |
| 06 — Risk gate | `06_risk_gate_in`, `_out` | Proposed weights, clamped weights, clamp records |
| 07 — Broker | `07_broker_calls` | List of broker method/args/result triples |

Every LLM call writes an in/out pair. Sections carry `state_keys` references where applicable so the file is grep-able when something goes wrong.

### Implementation — minimal-intrusion `TraceWriter`

A `TraceWriter` class at `src/observability/trace.py` (new module):

```python
class TraceWriter:
    """Append-only JSON snapshot collector for one tick.

    Production runs do not instantiate this; the trace_tick.py entrypoint
    sets state["_trace"] to an instance, and every callback opportunistically
    routes through state.get("_trace").
    """
    def snapshot(self, label: str, payload: dict, *, state_keys: list[str] | None = None) -> None: ...
    def llm_pair(self, label_base: str, prompt: str, response: str, *, model: str) -> None: ...
    def finalise(self, out_path: Path) -> None: ...
```

Wiring is opt-in via a sentinel in state. Production runs leave `state["_trace"]` unset, and `_trace_maybe(state, ...)` no-ops on a single dict lookup. Touchpoints:

- Fetch callbacks: one snapshot at end (`01_fetch_<analyst>`).
- `run_deterministic_analyst`: one snapshot at end (`02_<analyst>_verdict`).
- `make_evidence_callback`: one snapshot at end (`03_<analyst>_verdict`).
- LLM agents (fundamental, sentiment, strategist): ADK `before_model_callback` / `after_model_callback` hooks attached only in trace mode, calling `trace.llm_pair(...)`.
- Digest builder: snapshot after `build_ticker_evidence` (`04_digest`).
- Risk gate, executor: before/after snapshots (`06_*`, `07_broker_calls`).

### Entrypoint

`scripts/trace_tick.py`, invoked as:

```bash
PYTHONPATH=src python -m scripts.trace_tick --ticker AAPL [--out docs/surface-traces/]
```

Behaviour:

1. Loads heuristics, builds the full production pipeline (real LLMs, paper broker — exactly production wiring).
2. Overrides the watchlist to `[--ticker]` only.
3. Attaches a `TraceWriter` to initial state.
4. Runs one tick via `orchestrator.tick.run_once`.
5. Writes `docs/surface-traces/<tick_id>-<ticker>.json`.
6. On exception in any stage: flushes the partial trace, exits non-zero.

`docs/surface-traces/` is added to `.gitignore`.

### What surface-tracing validates for this refactor

- **Closed-vocabulary adherence**: did the LLM emit `risk:debt_refinance` or invent `risk:debt_problems`? Grepable in the trace.
- **Prose-only narrowing**: does fundamental still reason about numeric ratios in its rationale despite the prompt? Visible in `03_fundamental_llm_out`.
- **Evidence-shape rendering**: does the strategist's prompt block (`05_strategist_llm_in`) carry the new tags in the expected shape?
- **End-to-end correctness**: does a real tick complete with real broker calls?

The PR's acceptance gate is: T1 + T2 pass, plus at least one clean surface trace exists for at least one ticker, and the strategist prompt block in step 5's trace shows the new evidence shape rendering correctly.

---

## Test strategy

### Tier 1 — unit (no LLM, runs in CI)

| File | Coverage |
|---|---|
| `tests/unit/test_derive_technical_verdict.py` | Table-driven cases for `derive_technical_verdict`: empty data, overbought-flip, oversold-flip, momentum agree/disagree, vol breakout, near-52w-high, high-volatility penalty. ~15 cases. |
| `tests/unit/test_derive_smart_money_verdict.py` | Table-driven cases for `derive_smart_money_verdict`: no-data, single filer, multi-filer consensus, lone filer high volume, mixed buys/sells. ~12 cases. |
| `tests/unit/test_analyst_heuristics.py` | Schema validation: malformed JSON, out-of-range fields, missing sections, unknown keys all raise `ValidationError`. |
| `tests/unit/test_evidence_row_persistence.py` (extend) | Round-trip a verdict with `key_factors=["risk:cybersecurity_added", "tone:defensive"]` and confirm JSON serialisation preserves the colon-and-underscore tag shape. |
| `tests/unit/test_evidence_index.py` (new) | Introspect SQLAlchemy metadata; assert the composite `(analyst, ticker, recorded_at)` index is declared. |
| `tests/unit/test_lifecycle_initialise.py` (extend) | Add a `_check_heuristics()` failure-path case. |
| `tests/unit/test_fundamental_prompt_render.py` | Vocabulary placeholders resolve correctly; rendered prompt does not contain unresolved `{}`-tokens. |
| `tests/unit/test_sentiment_prompt_render.py` | Same for sentiment. |

Each `derive_*_verdict` test is parameterised on a fixture `*Heuristics` so threshold changes are testable without rebuilding the agent.

### Tier 2 — integration smoke

| File | Coverage |
|---|---|
| `tests/integration/test_analyst_pool_smoke.py` (new, `@pytest.mark.integration`) | Build the full `AnalystPool` against canned fixtures, run a single tick with the deterministic analysts and LLM analysts mocked, assert state keys populated correctly. |
| `tests/integration/test_pipeline_composition.py` (extend) | Confirm the post-refactor pipeline still wires end-to-end. |

### Tier 3 — live surface trace

| Artefact | Purpose |
|---|---|
| `scripts/trace_tick.py` | Entrypoint. Produces the trace. |
| `docs/surface-traces/<tick_id>-<ticker>.json` | One trace per acceptance run. Reviewed manually. |

### Golden-set sanity (T1, parameterised by config)

A test in `tests/unit/test_golden_set.py` runs the existing analyst fixtures through both new `derive_*_verdict` functions and asserts the output is in the same lean direction as the current LLM-emitted verdict ≥ `golden_set.min_direction_agreement_pct` of the time (default 70). Tunable via config to loosen/tighten the gate. Not a regression bar — a sanity check that the rule-based verdict hasn't drifted into a different worldview.

---

## Rollout — single PR, ordered commits

Each commit is independently green (CI passes); the whole PR lands together because the surface-trace validation depends on all of it.

1. **Config + heuristics models.** Add `config/analyst_heuristics.json`, `src/agents/analysts/heuristics.py`, update `config/README.md`, wire `_check_heuristics()` into `initialise()`. No agent changes yet. Tests: T1 schema validation + lifecycle init.
2. **Deterministic technical analyst.** Add `derive_technical_verdict` next to `extract_technical_features`. New `TechnicalAnalyst(BaseAgent)` replacing the LlmAgent. Delete the old prompt module. Update the factory and pipeline wiring. Tests: T1 derive + render.
3. **Deterministic smart_money analyst.** Same shape as step 2 for smart_money.
4. **Narrowed fundamental LLM.** Rewrite `prompts.py` using vocabulary placeholders. Inject `FundamentalVocabulary` at construction. Tests: T1 prompt render + canned-LLM-output schema validation.
5. **Narrowed sentiment LLM.** Same shape as step 4 for sentiment.
6. **Persistence index.** Add the composite index declaration to `AnalystEvidenceRow`. Tests: T1 metadata-introspection test.
7. **Surface tracing.** Add `src/observability/trace.py`, wire `_trace_maybe(...)` no-op hooks into the touchpoints. Add `scripts/trace_tick.py`. Add `docs/surface-traces/` to `.gitignore`. No production-path behaviour change.
8. **Live validation.** Run `trace_tick.py --ticker AAPL` (or another agreed sample). File the resulting trace under `docs/surface-traces/`. Eyeball it. Iterate prompts if needed. Acceptance gate.

Steps 1–7 are mechanical and CI-validated. Step 8 is manual and is the gate that closes the PR.

---

## Things explicitly out of scope

- Strategist prompt or agent changes (Plan E handles strategist hardening; this PR does not touch it).
- Sparse / triggered LLM execution.
- Any RAG, KB, or vector-store work.
- Embedding columns, prompt-versioning systems, LLM cost telemetry.
- Replay / backtest tooling for the deterministic analysts (trivial to write when actually needed).
- Automated regression on LLM output (no golden-LLM-response test — too brittle on a real model).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Closed-vocabulary prompt instruction is non-binding; LLM emits tags outside the vocabulary | T1 schema-validation tests catch invalid tags in canned outputs; the surface trace catches them in live runs. If the model proves unreliable, add an after-callback that filters/rejects out-of-vocab tags before evidence is built. |
| Deterministic verdict diverges in spirit from what the LLM was doing | Golden-set sanity test (70% direction agreement, configurable) catches gross drift. |
| Threshold tuning becomes a moving target | All thresholds are in one JSON file with documented ranges and required Pydantic validation. No hot-reload, so changes are atomic across restarts. |
| Surface trace files leak sensitive provider data into the repo | `docs/surface-traces/` is in `.gitignore` from day one. The directory is a debug artefact, not committed material. |
| Risk-tag suffix scheme (`_added`, `_removed`, `_intensified`) becomes wrong when the prior filing is absent | Prompt instruction: emit suffixed tags only when prior filing exists in the dump; otherwise emit the bare tag. |

---

## Open follow-ups (not blocking this PR)

- Sparse-execution gate ("only run fundamental LLM when a new filing has landed since last tick") — natural Phase-6 work once a KB exists to query for filing recency.
- RAG / KB layer over `risk_factors_excerpt`, `mda_excerpt`, headlines, and earnings call transcripts (Phase 6).
- LLM cost telemetry — easy add once we care about it.
- Promote the deterministic verdict heuristics to a learned model when enough `AnalystEvidence` history accumulates (Goal-3 substrate already in place).
- 13D-letter / Form-4-footnote narrative analyst (new sibling LLM that the current refactor does not strip — explicitly leaves room).
