# Phase 4 — Strategist v2 + Analyst → Strategist Contract (consolidated spec)

> **Status:** specced. Implementation broken into four self-contained plans (A → B → C → D) under this same directory. Each plan is small enough to execute via `superpowers:subagent-driven-development` without touching the others.

This spec replaces and consolidates the prior `strategist-v2-design.md` and `analyst-strategist-contract-design.md`. Those two designs were tightly coupled — the strategist rewrite needed to consume the new analyst contract, and the contract rewrite was motivated by what the strategist needed to see. Both plans landed at ~1.5–3k lines each, large enough to be risky as standalones. The consolidation re-slices the work along **risk-of-integration** boundaries instead of along **agent boundaries**, so each PR is shippable on its own and the strategist's prompt + agent code is touched exactly once.

---

## Why both at once

The strategist's primary input is the analysts' output. Today:

- Each analyst (technical, fundamental, sentiment, smart_money) returns a free-form `AnalystSignal` with a string verdict + confidence + a free-form evidence dict.
- The strategist sees four flat lists of signals injected as `{technical_signals}` / `{fundamental_signals}` / etc. and is asked to weigh them in plain English.
- Held-position context is an unstructured `Active Positions: {positions}` blob.

The two follow-on goals — Goal 1 (single-strategist v2 with per-ticker stance) and Goal 2 (analyst → strategist contract) — both rewrite this surface. Doing them as two PRs that each touch the strategist prompt, agent, schema, and pipeline causes double-churn. Doing them as one giant PR is too risky to land. The four-plan slice below threads the needle.

---

## The four plans, by integration risk

### Plan A — Contract scaffolding (purely additive)

**Risk:** zero. Pure new code, nothing imports it yet, nothing changes.

**Adds:** `src/contract/{evidence,ticker_evidence,digest}.py`, `src/config/digest.py`, fixtures under `tests/fixtures/contract/`, the `pandas-ta` dependency.

**Defines:**
- `AnalystVerdict` — `direction: Literal["bullish","bearish","neutral"]`, `confidence: float [0,1]`, `rationale: str (≤160)`, `is_no_data: bool`.
- `AnalystEvidence` — `ticker`, `analyst: Literal["technical","fundamental","sentiment","smart_money"]`, `features: dict[str, float]`, `verdict: AnalystVerdict`.
- `AggregateVerdict` — `direction`, `magnitude: float [0,1]`, `weights_used: dict[str, float]`.
- `TickerEvidence` — `ticker`, `tick_id`, `recorded_at`, `per_analyst: dict[str, AnalystEvidence]`, `aggregate: AggregateVerdict`, `disagreement_score: float [0,1]`.
- `build_ticker_evidence(per_analyst, ticker, tick_id, recorded_at, weights) → TickerEvidence`. The aggregator math: weighted vote with `DIRECTION_DEAD_ZONE = 0.15` margin, variance-based disagreement, neutral-fill for missing analysts. Default weights all 1.0 (`DEFAULT_ANALYST_WEIGHTS = {"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0}`); knob lives in `src/config/digest.py`.

**Doesn't change:** any existing analyst, the strategist, the pipeline, persistence. After Plan A merges, the bot runs identically; Plan A's modules are dead until Plan B imports them.

### Plan B — Per-analyst extractors + dual-emit

**Risk:** low–medium. Each analyst gains a deterministic feature extractor and starts emitting *both* the legacy `<Analyst>Signal` AND the new `AnalystEvidence`. Strategist still consumes only the legacy signals.

**Adds:** `src/contract/extractors/{technical,fundamental,sentiment,smart_money}.py` — each extractor takes the same upstream data the analyst already pulls, returns a `dict[str, float]` of features.

**Modifies:** the four analyst agents to:
1. Run the feature extractor before/after the LLM call.
2. After the LLM call, build an `AnalystEvidence` from the extractor's features + a coerced `AnalystVerdict` (re-using the LLM's existing direction + confidence + rationale).
3. Write **both** the legacy signal (state key `{analyst}_signals`) **and** the new evidence (state key `{analyst}_evidence`).

**Why dual-emit:** Plan C will switch the strategist to consume `ticker_evidence` (built per-tick from the four `*_evidence` lists). While Plan B is being merged + observed, the legacy contract still works end-to-end. Dual-emit lasts only until Plan D removes it.

**Doesn't change:** strategist prompt, strategist agent, pipeline wiring, persistence. Bot still runs as before; we've just started writing additional state.

**Per-analyst feature catalogue** (locked here; extractors don't pad with extra keys without a spec change):

| Analyst | Features (all `float`) |
|---|---|
| `technical` | `rsi_14`, `pct_change_5d`, `pct_change_20d`, `vol_ratio_20d` (vs 50d), `atr_pct_14`, `dist_from_high_52w_pct`, `dist_from_low_52w_pct` |
| `fundamental` | `pe_trailing`, `pe_forward`, `peg`, `revenue_growth_yoy`, `profit_margin`, `debt_to_equity`, `fcf_yield_pct`, `roe`, `analyst_rating_avg` (1=strong sell, 5=strong buy) |
| `sentiment` | `news_count_7d`, `pct_news_positive_7d`, `pct_news_negative_7d`, `headline_polarity_mean_7d` (-1…+1), `social_volume_z` (vs 30d, optional → 0.0 if no provider) |
| `smart_money` | `n_politicians`, `n_buys_30d`, `n_sells_30d`, `total_dollar_value_buys`, `total_dollar_value_sells`, `net_flow_dollar`, `is_no_data` (1.0/0.0 — neutral-fill flag for sparse coverage) |

Sparseness rule: `smart_money` returns `is_no_data = 1.0` and zeros elsewhere when no filings cover this ticker; the aggregator then treats its `verdict.direction` as `"neutral"` regardless.

### Plan C — Strategist v2 against new contract (touches strategist exactly once)

**Risk:** high (it's the rewrite), but **touches the strategist prompt + agent code exactly once**. Plan A and Plan B never touched them.

**Adds (all under `src/agents/strategist/`):**
- `stance_schema.py` — `TickerStance` model: `ticker`, `preferred_weight: float [0,1]`, `conviction: float [0,1]`, `rationale: str (≤140)`, `horizon: Literal["intraday","swing","long_term"] | None`, `target_price: float | None`, `stop_price: float | None`, `catalyst: str | None (≤80)`, `close_reason: str | None (≤120)`, `trim_reason: str | None (≤120)`.
- `lifecycle.py` — `derive_lifecycle_action(current_weight, preferred_weight) -> Literal["open","close","trim","add","hold"]`. Constants: `OPEN_EPSILON = 0.005`, `SIZE_CHANGE_EPSILON = 0.02`. Rules: `current ≤ ε ∧ preferred > ε` → open; `current > ε ∧ preferred ≤ ε` → close; `current > ε ∧ preferred + δ < current` → trim; `current > ε ∧ preferred > current + δ` → add; otherwise → hold.
- `derivation.py` — `derive_legacy_fields(stances, TickContext) → DerivedFields(target_weights, new_positions, close_reasons, trim_reasons)`. Server-side; runs in the after-agent callback so `risk_gate` / `executor` / `memory_writer` keep their existing input shape.
- `held_view.py` — `render_held_positions_view(positions, portfolio) → str`. Renders one block per held ticker: opened-at + price + current weight, rationale, target/stop with % from open, horizon, catalyst (if set), live price + unrealised PnL.

**Modifies:**
- `src/agents/strategist/schema.py` — `StrategistDecision` gains `stances: list[TickerStance]` and `trim_reasons: dict[str, str]`. Existing fields (`target_weights`, `decision_tag`, `reasoning`, `updated_thesis`, `confidence`, `new_positions`, `close_reasons`) stay; the after-callback fills them in from the derived fields. `PositionThesis` gains `opened_tick_id: str = ""`.
- `src/agents/strategist/prompts.py` — full template rewrite. New slots: `{held_positions_view}`, `{ticker_evidence}` (rendered TickerEvidence list, one block per ticker, pulled from Plan A/B output), `{tickers}`. Removed: `{technical_signals}`, `{fundamental_signals}`, `{sentiment_signals}`, `{smart_money_signals}`, `Active Positions: {positions}`.
- `src/agents/strategist/agent.py` — full rewrite. `before_agent_callback = _held_view_before_callback + _ticker_evidence_before_callback` (renders `held_positions_view` and `ticker_evidence` strings). `after_agent_callback = _strategist_validation_callback` (exhaustiveness over `state["tickers"]`, lifecycle hint enforcement, `derive_legacy_fields` populates legacy decision keys).
- `src/orchestrator/pipeline.py` — passes the new callbacks; adds `StrategistDecisionWriter` between `Strategist` and `RiskGate`.
- `src/orchestrator/persistence.py` — new ORM `TickerStanceRow`; `TradeLogRow` gains `opening_tick_id: str | None` and `closing_tick_id: str | None` (indexed, nullable). New helper `save_ticker_stance`.
- `src/agents/executor/agent.py` — on BUY: write `state["positions"][ticker] = strategist_decision.new_positions[ticker]`. On SELL: persist `TradeLogRow` with `opening_tick_id` (from thesis) and `closing_tick_id` (from `state["tick_id"]`).

**Adds (writer agent):** `src/agents/strategist/decision_writer.py` — `StrategistDecisionWriter` `BaseAgent` writes one `TickerStanceRow` per stance per tick.

**State key migration:** strategist now reads `state["ticker_evidence"]` (built from `state["technical_evidence"]` + `state["fundamental_evidence"]` + `state["sentiment_evidence"]` + `state["smart_money_evidence"]`, all populated by Plan B). The legacy `state["{analyst}_signals"]` keys are still being written by analysts (dual-emit) and still consumed by `attribution_writer` and `memory_writer` until Plan D.

### Plan D — Cleanup (final consolidation)

**Risk:** low–medium. Drops dual-emit, retires legacy persistence path, finalises ORM.

**Adds:**
- `src/orchestrator/persistence.py` — new ORM `AnalystEvidenceRow` (one per analyst per ticker per tick) + `TickerEvidenceRow` (one per ticker per tick, indexed by `tick_id` and `ticker`).
- `src/agents/contract/evidence_writer.py` — `EvidenceWriter` `BaseAgent` persists both row types, runs in pipeline between AnalystPool and Strategist (replaces `attribution_writer`).

**Modifies:**
- Each analyst agent — drops legacy `<Analyst>Signal` emit; only emits `AnalystEvidence` to `state["{analyst}_evidence"]`.
- `src/agents/memory/writer.py` — reads `state["ticker_evidence"]` instead of the four legacy `*_signals` lists.
- `src/orchestrator/pipeline.py` — replaces `attribution_writer` with `evidence_writer`.

**Removes:**
- `src/agents/attribution/writer.py` and the `AttributionSignalsRow` ORM (file deletion + ORM declaration removal).
- The `*_signals` state keys (no consumer left after `memory_writer` migrates).
- The legacy `<Analyst>Signal` Pydantic schemas in `src/agents/analysts/*/schema.py` (file simplifies to just `<Analyst>Evidence` + verdict re-export).
- `docs/superpowers/specs/{strategist-council,exit-rules-and-telemetry,strategist-v2,analyst-strategist-contract}-design.md` and the matching plan files (replaced by this directory).

---

## Cross-cutting design decisions

These hold across all four plans.

### 1. Code-only digest (Plan A defines, Plan C consumes)

The strategist sees per-ticker `TickerEvidence` — *not* four flat lists of analyst signals. The aggregator is deterministic Python, not another LLM call. The strategist's job is to **react to the digested evidence + held-position context**, not to re-aggregate analysts.

```
build_ticker_evidence:
    weighted_dirs = sum(weight[a] * sign(direction[a]) * confidence[a] for a in analysts)
    if abs(weighted_dirs) < DIRECTION_DEAD_ZONE: aggregate.direction = "neutral"
    else: aggregate.direction = sign(weighted_dirs)
    aggregate.magnitude = abs(weighted_dirs) / sum(weight[a] for a in analysts)
    disagreement_score = variance({sign(direction[a]) * confidence[a]}) normalised to [0,1]
```

Why dead-zone: prevents flip-flopping when one analyst at low confidence drags the aggregate across zero. `0.15` is the magnitude threshold below which we report `"neutral"` regardless of the sign of the weighted sum.

### 2. Per-ticker stance, not flat target_weights

The strategist emits one `TickerStance` per watchlist ticker. The `after_agent_callback` derives `target_weights` / `new_positions` / `close_reasons` / `trim_reasons` from the stances using the lifecycle rules. This means:

- The LLM never has to think about portfolio-wide weight allocation; it picks a `preferred_weight` per ticker and the derivation enforces constraints.
- `risk_gate` / `executor` / `memory_writer` see the same `target_weights` / `new_positions` shape they always saw — no downstream rewrites.
- Each stance is persisted as a `TickerStanceRow`, so we can ask "what was our stance on AAPL the tick before we sold?" later.

### 3. Held-position context goes into the prompt

The strategist needs to see what it bought, why, and where the targets/stops sit. Without it, "stop_price set 3 days ago" is invisible to the next decision. The `held_positions_view` block replaces the unstructured `Active Positions: {positions}` line.

### 4. Outcome attribution via tick_id FKs

Adding `TradeLogRow.opening_tick_id` + `closing_tick_id` lets the future knowledge-base loop join "this trade closed at +9% PnL" back to "the strategist's stance and the digested evidence on the tick that opened it." Substrate for Goal 3, no current consumer.

### 5. Validation reprompts, not crashes

The strategist's `after_agent_callback` returns a `genai_types.Content` re-prompt when the LLM emits an invalid stance set (missing tickers, off-watchlist tickers, open without horizon/target/stop, close without close_reason, trim without trim_reason). Reprompts cost a retry; crashes lose the tick.

### 6. Lifecycle is derived, not declared

The LLM doesn't say "I'm opening" or "I'm closing." It says `preferred_weight = 0.08`. `derive_lifecycle_action(current=0.0, preferred=0.08)` returns `"open"`. This means the LLM can't lie about what it's doing — the action falls out of the math.

### 7. Stickiness of `target_price` / `stop_price`

Once set on `PositionThesis` at open, they are sticky unless the strategist explicitly updates them in a subsequent stance. A future plan (B4 in `docs/superpowers/backlog.md`) covers trailing-stop logic; v2 keeps it manual.

### 8. SmartMoney sparseness handling

`smart_money` covers maybe 10–20% of the watchlist (insider/political filings are sparse). The extractor returns `is_no_data = 1.0` when the filings provider has nothing for this ticker. The aggregator's neutral-fill rule then ignores its verdict. This prevents "no data" from being read as a strong neutral signal.

---

## Pre-deployment context

The bot is **not running anywhere** yet — no live, no paper. There is no in-flight position book to migrate, no historical telemetry to backfill compatibility for. The four-plan slice is therefore optimised for "ship correctness, not migration window." Specifically:

- Plan A's purely-additive scaffolding doesn't need a feature flag.
- Plan B's dual-emit isn't a "rolling out gradually to production" affordance — it's just so Plan B and Plan C can land as separate PRs without breaking each other.
- Plan D drops the legacy code path completely; no compatibility shim, no deprecation window.

If/when paper trading starts before Plan D is merged, the cleanup gets slightly more scrutiny (don't drop the existing `AttributionSignalsRow` rows mid-run). Until then, the only thing protected is "main branch's tests pass after every PR."

---

## Test strategy

- **Tier 1 (no LLM, run on every commit):** Pydantic schema round-trips, lifecycle derivation truth tables, digest math (direction sign, magnitude, dead zone, disagreement score), held-view rendering golden output, validation callback's reprompt branches, ORM persistence round-trips.
- **Tier 2 (gated by `RUN_LLM_TESTS=1`):** one strategist smoke that runs `strategist_agent` against fixture state with one held position and asserts a parseable exhaustive `TickerStance` set comes back. Per analyst: one smoke that runs the analyst against captured-data fixtures and asserts the `AnalystEvidence` parses + features dict has all expected keys.
- **Tier 3 (manual smoke):** `scripts/smoke_run.py --ticks 3` end-to-end, verify `TickerStanceRow` + `AnalystEvidenceRow` + `TickerEvidenceRow` rows land in the dev SQLite.

Test fixtures live under `tests/fixtures/contract/` (one captured-data fixture per analyst, each tick_id-stamped).

---

## Open questions consciously deferred

These are flagged in `docs/superpowers/backlog.md` and **not** in scope for Phase 4:

- **B2 — Knowledge base:** the loop that *reads* `TickerEvidenceRow` + outcome FKs to bias future decisions. Long arc, gated on weeks of paper data.
- **B3 — Sub-tick exit evaluation:** flash-crash protection. Independent of the contract.
- **B4 — Trailing stops / target ratchet:** mechanical stop revision. Independent.
- **B5 — Per-evidence-key analyst weighting:** "smart_money's `n_politicians` is more predictive than `total_dollar_value_buys`." Refinement on top of Plan A's per-family weights.
- **B6 — `RiskClampRow` persistence:** trivial follow-up.
- **B7 — Cost / latency telemetry per tick:** ad-hoc today; will be needed for Goal 3.
- **B8 — Decision replay tooling:** counterfactual re-runs over frozen tick state. Validates Goal 3 experiments.

---

## Implementation order

Execute the plans in `A → B → C → D` order. Each plan ends in a green test suite and a clean working tree. Each plan is invocable via `superpowers:subagent-driven-development` with no shared in-memory state across plans.

- [Plan A — Contract scaffolding](./plan-A-contract-scaffolding.md)
- [Plan B — Per-analyst extractors with dual-emit](./plan-B-extractors-dual-emit.md)
- [Plan C — Strategist v2 against new contract](./plan-C-strategist-v2.md)
- [Plan D — Cleanup](./plan-D-cleanup.md)
