# Spec A — Surgical correctness and input quality

**Status:** Draft for one-hit execution.
**Originating session:** `docs/spec-prep/2026-05-22-spec-prep-grounding.md`
**Sibling spec:** Foundational thesis-memory (in flight in parallel — Spec B).
**Followup spec:** Enrichment memory (sketch only — Spec C).

---

## 1. Context

The first-test backtest run (`backtests/baseline-2025-09/runs/first-test/`,
46/60 ticks, baseline window 2025-09-02 → 2025-10-13) surfaced a set of
correctness, observability, and input-quality issues that block any meaningful
follow-up backtesting. None of them depend on memory design; all of them would
silently corrupt or starve the memory work the moment it lands.

This spec bundles the surgical fixes into one execution pass.  The originating
session walked every proposed fix against `docs/contract-invariants.md` and
confirmed they either respect existing invariants (R) or extend areas the
contract is silent on (E).  No fix questions a load-bearing invariant.

The three analysis reports (computational, market, LLM) live under
`backtests/baseline-2025-09/runs/first-test/report/`. The grounding doc
synthesises them; this spec is self-contained and citation-rich so an
implementer does not need to swap between sources mid-task.

---

## 2. Scope

**In scope.** Twenty-one fixes total — nine S-band, two D-band, five LLM-bundle, five R-band:

| Group       | Items                                               |
|-------------|-----------------------------------------------------|
| S-band (9)  | S1, S2, S3, S4, S5, S6, S7, S8, S9                  |
| D-band (2)  | D1 (news report enforcement), D2 (fundamental bullish reachability) |
| LLM bundle (5) | H4 (derived rationale budget), M1 (anti-truncation guard), M4 (news bearish nudge), M3 (drop dead Social rows), M5 (worked-example ticker) |
| R-band (5)  | R1 (remove cash floor), R2 (widen max-delta), R3 (lift turnover ceiling), R4 (constants → JSON config), R5 (prompt restates surviving rules) |

**Out of scope (pushed to other specs / backlog).**

- D3 — carry-forward in `derivation.py:192-200`. Tightly entangled with
  prompt redesign and thesis memory.  **→ Spec B (foundational thesis-memory).**
- §E persistence for `positions` + `thesis` rows.  **→ Spec B.**
- §E persistence for `memory_buffer` + `day_digest` rows; experiential
  memory; learning behaviours; regime context.  **→ Spec C (enrichment memory).**
- Strategist prompt redesign for prior-thesis usage and "starting from flat
  portfolio" framing.  **→ Spec B.**
- N1 — strategist `state_delta` propagation. Not needed: every Rule-1 venue
  is already correct per the originating session's walk.  The frozen-rationale
  symptom is a §E persistence gap, not plumbing.
- SPY-return discrepancy in `src/backtest/reporting.py` (recomputed −3.31 pp
  vs reported −4.55 pp).  Likely a forward-return-backfill anchor mismatch;
  worth a one-line check during S4 if convenient but explicitly not gated on
  this spec.
- Asymmetric tagging concern (vocab refactor for `planned_sale_dominant` →
  `routine_sale_dominant` for naming clarity).  **→ Backlog candidate.**
- Extending the derived prompt-budget pattern (H4) to summary / driver caps.
  Only rationale has empirical retry evidence.  **→ Backlog candidate.**

---

## 3. Contract anchors

This spec respects the following load-bearing parts of
`docs/contract-invariants.md`. The full doc is the canonical source; this
section is for in-spec lookup.

- **§A field schema.** `positions`, `reference_prices`, `strategist_decision`,
  `news_verdicts`, `fundamental_verdicts` rows touched.
- **§B Phase 2.** Tick-scoped fields populated fresh from source of truth at
  every tick.  S1 fixes a Phase 1 placement that should be Phase 2.
- **§C Rule 1.** State mutation rides on `EventActions(state_delta=...)`.
  S3 removes a hot spot.
- **§C Rule 8.** Observability is additive and contract-neutral.  S4, S5
  (decision logger arm), S7, S8 all live here.
- **§D additive carve-outs.** Backtest-only artefact paths.  Several S-fixes
  are §D1 (observability differs between lifecycles).

---

## 4. S-band — surgical correctness fixes

Each S-fix is transcribed verbatim from the originating session's analysis
with minor wording for spec-context.  The code citations are point-of-fix;
the verification commands are runnable from project root with the established
`PYTHONPATH=src .venv/bin/python ...` invocation.

### S1 — `reference_prices` PIT-clamp at Phase 2

**Symptom.** Every `audit/*.tick.json` for tick 1 carries `max_ts =
window.end` (2025-10-13) on SPY / XLK / XLF / XLE / XLV / XLY / XLP / XLI /
XLB / XLRE / XLU / XLC, while `as_of = 2025-09-02T13:30`. Tripwire
`any_filter_key_after_as_of` fires once on tick 1.

**Root cause.** `_seed_reference_prices` is invoked at Phase 1 (run-start)
with `end=window.end`; the §B contract requires the tick-scoped
`reference_prices` row to be populated fresh from its source of truth at
**Phase 2** (per tick).  The technical extractor masks the symptom downstream
with a defence-in-depth re-clamp.

**Code citations.**
- `src/backtest/runner.py:469-473` — call site (Phase 1)
- `src/backtest/runner.py:64-107` — body of `_seed_reference_prices`, no `as_of` cap
- `src/contract/extractors/technical.py:128-135` — defence-in-depth re-clamp

**Fix.** Move the seed call into the Phase 2 boundary so it fires per-tick.
Either:
- Relocate the call into the per-tick lifecycle wrapper (preferred — matches
  the contract literally), OR
- PIT-clamp by `as_of` at seed time and keep the Phase 1 call (still
  contract-conformant because the cross-tick portion is refreshed on every
  Phase 2 read).

**Recommendation.** Phase 2 placement.  The contract names Phase 2; respect
the literal phrasing.

**Verification.** Unit test in `tests/backtest/test_reference_prices.py`:
seed Phase 2 with a fixed `as_of`, assert no `reference_prices[symbol]` row
has any bar with `ts > as_of`.  Re-run an existing tick's audit regenerator;
`any_filter_key_after_as_of` should not fire.

**Status.** **R** — respects §B Phase 2.

**Live-safe.** Yes — Phase 2 fires every tick in both lifecycles.

---

### S2 — Executor `del positions[ticker]` only on true close

**Symptom.** `executor/agent.py:156` deletes the thesis on any SELL,
including a 1 % trim.  With `MAX_DELTA_PER_TICKER = 0.01`, the thesis is
wiped before the position is actually closed — contradicting the §A
`positions` row note that the thesis book persists for the life of the
position.

**Code citations.**
- `src/agents/executor/agent.py:156` — over-eager delete
- `src/agents/executor/agent.py:97`  — `TradeLogRow` write coupled to the same
  branch (writes on every SELL, including 1 % trims)
- `src/agents/executor/agent.py:202-210` — already-correct state_delta yield
  (Rule 1 conformant; the bug is purely the bookkeeping rule)

**Fix.** Only `del positions[order.ticker]` when the post-fill broker-
remaining quantity equals zero.  Query via `broker.get_portfolio()` after the
fill, or compute from prior `state["portfolio"]` minus `fill.quantity`.
Similarly, only write `TradeLogRow` on true close.

**Verification.** Unit test in `tests/executor/test_executor_bookkeeping.py`:
execute a 1 % trim via `FakeBroker` against a held position; assert
`state["positions"][ticker]` survives.  Execute a full exit; assert it is
deleted and a single `TradeLogRow` is written.  Extend the existing tests as
needed.

**Status.** **R** — respects §A `positions` row note.

**Live-safe.** Yes — pipeline code; `Trading212Broker` and `FakeBroker` both
expose the broker interface used here.

**Note.** Because §E persistence is not yet implemented, this fix does **not**
make positions cross-tick-durable in live.  It makes the in-tick bookkeeping
correct so that when §E lands (Spec B), the thesis it persists is not
mid-trim corrupted.

---

### S3 — `_report_cache_hits_for_audit` via state_delta or obs/logs

**Symptom.** `agents/analysts/report_cache.py:579` does
`state.setdefault("_report_cache_hits_for_audit", []).append(...)` from
inside per-ticker sub-agents.  The driver drains it at `driver.py:310`.  The
in-tick callback carve-out (added 2026-05-20) does **not** apply — these are
full `BaseAgent`s, not `after_agent_callback`s.

Audit `cache_hits` sums to 26 across all 46 ticks; structured-log
`cache_hit` events sum to 469.  Live would see 0/469 because direct mutation
is silently dropped on real session backends.

**Code citations.**
- `src/agents/analysts/report_cache.py:579` — direct state mutation
- `src/backtest/driver.py:310` — drain

**Fix.** Two valid paths; pick the cheaper one:

1. Yield audit hits via `state_delta` from each per-ticker sub-agent (proper
   Rule 1 conformance).
2. **Recommendation:** Have the audit reader consume `obs/logs/` directly the
   way the rest of the audit subsystem does.  Rule 8 permits this — observability
   is additive.  This removes a Rule 1 hot spot rather than fixing it.

**Verification.** After fix: audit `cache_hits` count should match
structured-log `cache_hit` event count on the same run (≈469, not 26).
Single assertion in the audit regenerator's existing tests.

**Status.** **R** — respects Rule 1 (by relocating, not by adding compliance).

**Live-safe.** Yes — and currently broken in live, not just backtest.

---

### S4 — Span-name prefix bugs in `reporting.py`

**Symptom.** `report/metrics.md` shows "LLM tokens — input 0, output 0, total
0 across 0 model calls" despite the trace file containing 42 `generate_content
gemini-2.5-flash-lite` spans + 1 `generate_content gemini-2.5-pro` span, all
carrying `gen_ai.usage.input_tokens` / `output_tokens`.  Per-agent latency is
similarly blank.

**Root cause.** `reporting.py:581, 590` use exact `==` against
`"generate_content"` / `"invoke_agent"`.  ADK emits `"generate_content
<model_id>"` and `"invoke_agent <agent_name>"`.

**Code citations.**
- `src/backtest/reporting.py:581` — `if name == "generate_content":`
- `src/backtest/reporting.py:590` — `if name == "invoke_agent":`
- `src/backtest/reporting.py:95`  — related: `fill_count = len(trade_rows)`
  counts closed round-trips only ("Total fills: 3" when there were 135 broker
  fills; compounds with S2's over-eager `del`)

**Fix.** Change exact match to `name.startswith(...)`.  Read
`gen_ai.agent.name` attribute rather than parsing the suffix for the agent name.
Rename "Total fills" → "Closed round-trips" or count opens as well.

**Verification.** Re-run reporting on the existing `obs/traces/*.json` for
the baseline-2025-09 first-test run; assert "Total tokens" > 0 and the
metrics file reports a non-zero per-agent latency table.

**Status.** **R** — Rule 8 observability.

**Live-safe.** N/A — backtest-only artefact, §D1 carve-out.

---

### S5 — Insider `.model_dump()` + decision_logger strict serialiser

**Symptom.**
`decisions/2025-09-15T13-30-00p00-00__MSFT__buy.json[analyst_inputs.fundamental.insider]`
is a 2 292-char string starting `"trades=[InsiderTrade(ticker='MSFT', ...)"`
— Python repr of a `Form4Bundle` instead of a JSON dict.

**Root cause.** `fetch_agent.py:165-169` stores the `Form4Bundle` Pydantic
instance directly while sibling fields use `.model_dump()`.
`decision_logger.py:136` uses `json.dumps(..., default=str)` which falls back
to `repr()` on the Pydantic instance and silently emits a string.

**Code citations.**
- `src/agents/analysts/fundamental/fetch_agent.py:165-169` — missing `.model_dump()`
- `src/backtest/decision_logger.py:25-33`  — `_coerce` only handles top-level coercion, not nested Pydantic models in lists/dicts
- `src/backtest/decision_logger.py:136` — `json.dumps(snapshot, indent=2, default=str)`

**Fix.** Two-line change at `fetch_agent.py:165-169` to call `.model_dump()`.
Tighten `decision_logger.py` to replace `default=str` with a recursive
serialiser that raises loudly on un-dumpable types so the next regression is
not silent.

**Verification.** Existing decision-logger test extended: assert
`analyst_inputs.fundamental.insider` round-trips as a JSON dict (not a Python
repr string).  Add a negative test: pass an un-dumpable type, assert a
serialisation error is raised.

**Important.** The LLM is **not** exposed to this — analysts read formatted
text from `temp:fundamental_context_<TICKER>`.  Tick-level signal is fine;
future RAG corpus is what was being corrupted.

**Status.** **R** — Rule 8.

**Live-safe.** First fix is pipeline (lifecycle-symmetric); second is §D1
backtest-only but harmless to live.

---

### S6 — `decision_tag` enum

**Symptom.** `decision_tag` is the constant string `"catalyst_driven_entry"`
across all 46 ticks regardless of whether the decision is an opening BUY, a
1 % ramp, a trim, a full exit, or a hold-flat.

**Fix.** Derive the tag from prior-vs-new weight in `derivation.py`.
Categories must be sufficient for memory (Spec B / Spec C) to key on intent
rather than action:

| Tag        | Condition (prior, new)                         |
|------------|------------------------------------------------|
| `entry`    | prior == 0.0 AND new > 0.0                     |
| `ramp`     | 0.0 < prior < new                              |
| `trim`     | prior > new > 0.0                              |
| `exit`     | prior > 0.0 AND new == 0.0                     |
| `hold_flat`| prior == 0.0 AND new == 0.0                    |
| `hold`     | prior == new AND prior > 0.0                   |

**Recommendation.** Derive in `derivation.py` rather than as a post-hoc
enrichment step.  Derivation is the single source of truth that both the
trace writer and the decision logger read from; post-hoc would mean two
consumers must each apply the enrichment.

**Verification.** Unit test on the derivation function: feed prior and new
weight pairs covering all six categories; assert the tag for each.

**Status.** **E** — extends §A `strategist_decision` content shape (currently
uncontracted).

**Pre-condition for Spec B.** Any memory writer keyed on decision intent
must see a discriminating tag — without S6, memory keyed on `decision_tag`
sees `catalyst_driven_entry` for every row and cannot distinguish entries
from trims from holds.

**Live-safe.** Yes — pipeline code.

---

### S7 — Suppressed tick-1 strategist trace exception

**Symptom.** `observability/trace.py:163` `contextlib.suppress(Exception)`
silently swallows the tick-1 `03_strategist` failure.  The LLM did run
(terminal log shows 38.6 s strategist call) but the trace dropped.

**Code citation.** `src/observability/trace.py:163`.

**Fix.** Add `logger.exception(...)` inside the `suppress` so single-tick
drops are not invisible.

**Verification.** Inject an exception inside the `03_strategist` span
recording in a unit test; assert the logger emits an exception record and
the suppress still keeps the run alive.

**Status.** **R** — Rule 8.

**Live-safe.** N/A — backtest trace writer, §D1.

---

### S8 — Tripwire renames

**Symptom.** Two tripwires fire benignly on every (relevant) tick,
drowning out genuine signal in the audit summary:

- `midnight_utc_timestamps_seen` — 46/46 ticks; date-only sources promoted
  to midnight is steady state.
- `open_tick_sameday_bar` — 23/23 open ticks; provider strips the same-day
  bar before consumer sees it.

**Code citations.**
- `src/backtest/audit/telemetry.py:184` — `hour == 0 and minute == 0` check
- `src/backtest/audit/tripwires.py:71-72` — definitions
- `src/backtest/providers/price_history_cache.py:92-93` — strips same-day bar

**Fix.** Rename both to `*_advisory` (or drop them — they are not actionable
signals).  Document in the tripwires module why they are benign.

**Verification.** After rename: assert the two renamed tripwires no longer
appear in the `tripwires_fired` summary count of "actionable" tripwires.

**Status.** **R** — Rule 8.

**Live-safe.** N/A — backtest audit, §D1.

---

### S9 — Tenacity retry warnings carry the wrapped agent name

**Symptom.** Across the run's 28 retry warnings (logger
`agents.llm_retry`), every emitted record reports `<unknown>` for the
agent that was retrying.  The LLM analysis had to fall back to an
adjacent-row heuristic (matching each retry to the next
`stockbot.tick.calls` row in the same `obs/logs/*.json`) to attribute
retries to News / Fundamental / Strategist — an attribution surface that
will become unworkable once memory writes start landing their own retries
into the same log stream.

**Root cause.** `src/agents/llm_retry.py:317` uses
`before_sleep_log(_LOGGER, logging.WARNING)` — tenacity's stock helper
emits the exception type and attempt number but has no hook for the
wrapped agent's identity.

**Code citations.**
- `src/agents/llm_retry.py:317` — current `before_sleep_log` call site
- `src/agents/llm_retry.py:76`  — `_LOGGER = logging.getLogger(__name__)`

**Fix.** Replace the stock helper with a small closure that captures
`self.inner.name` and logs it alongside the exception type and attempt
number:

```python
def _make_before_sleep(name: str) -> Callable[[RetryCallState], None]:
    """Build a tenacity ``before_sleep`` hook that attributes each retry
    warning to the wrapped agent.

    The stock ``before_sleep_log`` helper does not surface the inner
    agent's name; later log analysis cannot tell which agent retried
    without an adjacent-row heuristic.  The closure captures the name at
    wrapper-construction time so every retry record carries it.
    """

    def _hook(retry_state: RetryCallState) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        _LOGGER.warning(
            "Retrying %s after %s (attempt %s)",
            name,
            type(exc).__name__ if exc else "<unknown>",
            retry_state.attempt_number,
        )

    return _hook

# ... at the AsyncRetrying call site:
before_sleep = _make_before_sleep(self.inner.name),
```

The exact wording of the warning is the implementer's choice; the
contract is that the record carries the inner agent name so the next
LLM-quality analysis can group retries by analyst without a join.

**Verification.** Unit test in `tests/agents/test_llm_retry.py`:
configure a stub inner agent named `"TestAnalyst"` that raises a
retryable exception once then succeeds; assert the captured log record's
message contains `"TestAnalyst"`.

**Status.** **R** — Rule 8 observability; additive, contract-neutral.

**Live-safe.** Yes — pipeline file used by both lifecycles.

---

## 5. D-band — input-quality fixes

### D1 — News report enforcement

**Diagnosis.** The "report block drop" headline is misleading.  The path
`NewsAnalyst_<TICKER>` LLM call → ADK `output_schema` validation →
`temp:news_verdict_<TICKER>` → `NewsJoinerAgent` → `news_verdicts` does not
lose data anywhere.  The joiner (`src/agents/analysts/news/joiner.py:81-84`)
faithfully passes whatever it received and only synthesises a no-data
placeholder when the per-ticker key is *absent* (branch crash).

The actual symptom is the LLM emitting `report: null` on 282 / 917 (30.7 %)
of verdicts with `is_no_data: false`.  This is **schema-valid** today
because `src/contract/evidence.py:113` declares
`report: AnalystReport | None = None`.  The prompt says *"omit only when
is_no_data=true"* but the LLM violates that instruction at the rate above and
the schema does not enforce it.

Fundamental has the same loophole at 30 / 834 (3.6 %).

**Fix — three coupled changes.**

#### D1.1 Schema validator at the contract boundary

Add a Pydantic `model_validator(mode="after")` to `AnalystVerdict` in
`src/contract/evidence.py` that raises `ValueError` when
`is_no_data == False` and `report is None`:

```python
@model_validator(mode="after")
def _report_required_when_data_present(self) -> "AnalystVerdict":
    """Reject verdicts that claim data but omit the report block.

    LLM analysts must emit ``report`` whenever ``is_no_data=False`` — the
    strategist reads the prose to weigh evidence.  Schema-level enforcement
    is the source of truth; the prompt instruction is the LLM-facing
    statement of the same rule.
    """
    if not self.is_no_data and self.report is None:
        raise ValueError(
            "report is required when is_no_data=False — "
            "the analyst must emit a summary + drivers block "
            "alongside the verdict"
        )
    return self
```

Because `agents/llm_retry.py` already classifies `pydantic.ValidationError`
as retryable, ADK's `output_schema` rejection triggers an automatic retry
up to the configured cap.  The change applies to both News and Fundamental
since they share `AnalystVerdict` — fundamental's lower rate gets the same
treatment for free.

#### D1.2 Prompt instruction strengthening

Restate the report-required rule as a hard rule in both prompts.  Current
wording (`agents/analysts/news/prompts.py:61`,
`agents/analysts/fundamental/prompts.py:77`):

> `report       object — see schema below; omit only when is_no_data=true.`

Strengthen to:

> `report       object — see schema below.  REQUIRED whenever is_no_data=false;`
> `             emit at minimum a summary plus 2 drivers.  Omit ONLY when`
> `             is_no_data=true.`

#### D1.3 Strategist evidence visibility safety net

When retries are exhausted and `news_verdicts` somehow still arrives with
`report=None` on a `is_no_data=False` verdict (degenerate case — should be
near-impossible after D1.1), the strategist evidence renderer
(`src/agents/strategist/evidence_view.py`) inserts an explicit placeholder so
the strategist sees the absence as data rather than silently reasoning over
less evidence:

> `(no report this tick — analyst compliance failure)`

This is defence-in-depth: if D1.1 holds, the branch never fires; if a future
schema regression re-introduces the loophole, the visibility line surfaces
it immediately.

**Acceptable trade-off (called out explicitly).** D1.1 raises news retry
rate from ~3 % to ~30 %+ during the rollout backtest.  Expected impact per
60-tick window:

- ~285 extra LLM calls (mostly news, single retry each)
- ~$0.85 extra LLM cost
- News-branch tail latency +30-50 % (median ~12s → worst-tick ~18s)

Worth it: the strategist is silently reasoning over less evidence on ~31 %
of news rows today.  Closing that loop matters more than the latency hit on
a non-deployed bot.

**Verification.**

- D1.1: unit test in `tests/contract/test_evidence_schema.py` — construct
  `{"lean": "bullish", "magnitude": 0.5, "confidence": 0.6, "rationale": "x",
  "key_factors": [], "is_no_data": False, "report": None}`; assert
  `AnalystVerdict.model_validate` raises.  Round-trip a valid verdict to
  confirm no regression.
- D1.2: snapshot test on the rendered news + fundamental prompts confirming
  the strengthened wording is present and the old wording is absent.
- D1.3: render strategist evidence with a synthetic verdict carrying
  `report=None, is_no_data=False`; assert the rendered text contains
  `(no report this tick — analyst compliance failure)`.

**Note on `news_evidence` corpus.** Once D1.1 is in place, no synthesised
placeholders pollute the corpus — every non-no-data row carries a real
report.  This is load-bearing for Spec C (experiential memory / RAG): the
corpus is clean.

**Status.** **R** — respects §A `news_verdicts` content shape; tightens the
schema where it was previously silently permissive.

**Live-safe.** Yes — schema + prompt + evidence renderer; all pipeline code.

---

### D2 — Fundamental bullish reachability (minimal correction)

**Diagnosis.** The Fundamental analyst emitted 0 bullish verdicts across 920
calls in the run.  Top key-factor tags were dominated by
`insider:planned_sale_dominant = 230`,
`insider:discretionary_sale_dominant = 170`, and
`cluster_selling = 132` — routine 10b5-1 activity that the LLM was reading
as bearish.

**Root cause.** `agents/analysts/fundamental/prompts.py:93-101` has a
triple-AND-conjunction bullish trigger (`cluster open-market buys + raised
guidance + confident tone → strongly bullish`) that is structurally
unreachable for mega-cap watchlists where most insider activity is routine
sales.  Combined with the absence of an explicit *"routine 10b5-1 sales are
NOT bearish"* anchor, the LLM defaults bearish on any high-`planned_sale_ratio`
ticker.

The deterministic numeric features (`src/contract/extractors/fundamental.py`)
are correct ratios.  The interpretation lives entirely in the LLM's prompt
decision rule.

**Design principle.** The LLM is here to *reason* from the evidence.  Our
job is to remove prescription that boxes the LLM out of reasoning paths it
should have, not to replace one decision tree with another, larger one.

**Fix.** One change: rewrite the decision rule with neutral anchors and
anti-pattern corrections; no closed-vocab changes, no worked examples.

#### D2.1 Decision-rule rewrite

Replace `agents/analysts/fundamental/prompts.py:93-101` with:

```
Decision guidance (anchors — reason from the evidence; this is not a
decision tree):

- Lean reflects the dominant signal across guidance, tone, risk-factor
  changes, and insider activity.  Use the full bullish / bearish range as
  the evidence supports.

- Routine 10b5-1 (planned) sales are pre-scheduled and disclosed in advance.
  They are NEUTRAL signal — NOT bearish.
- Discretionary open-market sales are bearish; clusters of them are
  strongly so.

- Absence of insider activity is neutral, not bearish — default to neutral
  with low confidence when there is nothing material to say.

- Going-concern language present → strongly bearish (overrides other signals).
- Conflicting inputs → neutral with low confidence.
```

No prescriptive bullish trigger.  No `cluster buys + raised guidance + tone`
AND-conjunction.  The LLM reasons over the MD&A excerpts, the insider
numerics, and the footnotes; the only structural guidance is the two
anti-patterns (routine 10b5-1 is neutral; absence is neutral) and the
going-concern override.

#### D2.2 No closed-vocab changes

The existing `insider_signals` vocabulary in
`src/agents/analysts/heuristics.py` stays untouched in this spec.  Renaming
`planned_sale_dominant` → `routine_sale_dominant` for semantic clarity is a
backlog candidate; it has migration surface (memory keys, audit reports,
decision logger may hardcode names) that is not worth taking on inside this
surgical pass.

#### D2.3 No worked examples

Worked examples can over-anchor the LLM on a particular narrative shape.
The rule rewrite by itself is the minimal correction; we resist adding
examples until we have evidence of where the LLM still gets stuck.

**What this does NOT promise.** This does not guarantee the LLM emits
bullish at any particular rate.  It removes the *structural* impossibility
by deleting the AND-conjunction and correcting the routine-10b5-1 mis-read.
If a post-fix backtest still shows skewed lean distribution, prompt tuning
is a follow-up backed by fresh evidence rather than guesswork.

**Verification.**

- Snapshot test on the rendered fundamental prompt confirming the new
  decision-rule wording is present and the old AND-conjunction is gone.
- Snapshot test that the new anchors ("Routine 10b5-1 ... NOT bearish",
  "Absence of insider activity is neutral, not bearish", "Going-concern
  language present") all appear.

Post-rollout (out of band of unit tests): re-run the baseline-2025-09 window
and inspect the fundamental lean distribution.  If still 0 bullish across a
20-stock window with multiple raised-guidance tickers, the LLM is stuck on
something deeper than the AND-conjunction; that diagnosis is the next step.

**Status.** **R** — respects §A `fundamental_verdicts` content shape.

**Live-safe.** Yes — pipeline code (prompt file).

---

## 6. LLM-quality bundle (folded in)

Four smaller fixes that fold cleanly into the same prompt / config / evidence
surface we are already editing.

### H4 — Derived rationale prompt budget

**Symptom.** 6 of 28 LLM retries in the run were `pydantic.ValidationError:
string_too_long` on the rationale field — the LLM exceeded the +15 % schema
slack (230 chars on a 200-char prompt budget).

**Fix.** Tighten what we tell the LLM about its rationale budget, derived
from config so the relationship survives any future cap adjustment.

In `src/config/analysts.py` (the analyst config dataclass), add a new field
alongside `verdict_rationale_max_chars`:

```python
verdict_rationale_prompt_headroom_chars: int = 50
```

Add a derived property on the config:

```python
@property
def verdict_rationale_prompt_budget(self) -> int:
    """Prompt-facing rationale budget — the value the LLM is told.

    Derived from the schema-facing cap minus the configured headroom so
    raising or lowering ``verdict_rationale_max_chars`` automatically
    re-tunes what the LLM is asked to produce.  The result is clamped on
    both sides:
      * lower bound 40 — a meaningless or negative budget can never reach
        the prompt (catches headroom > cap misconfigurations);
      * upper bound ``verdict_rationale_max_chars`` — the prompt budget
        can never exceed the schema cap, defeating the purpose (catches
        negative-headroom misconfigurations).
    """
    budget = self.verdict_rationale_max_chars - self.verdict_rationale_prompt_headroom_chars
    return max(40, min(self.verdict_rationale_max_chars, budget))
```

In `src/agents/analysts/news/prompts.py` and
`src/agents/analysts/fundamental/prompts.py`, change the format-call argument:

```python
# old
rationale_max = out_caps.verdict_rationale_max_chars

# new
rationale_max = out_caps.verdict_rationale_prompt_budget
```

The schema cap (`schema_cap(verdict_rationale_max_chars)` ≈ 230) is
**unchanged** — the LLM's natural overshoot is still absorbed.  Only what we
*tell* the LLM tightens.

Update `config/README.md` with the new field's purpose.

**Why this shape (not just hardcode 150 in the prompt).** The user-stated
goal is that lowering the prompt budget cannot leave an artefact if the
schema cap is later adjusted.  Two config fields with a derived property
keep the two values in lockstep.

**Verification.**

- Snapshot test on rendered prompts confirming the prompt now says
  `≤<derived-budget>` (currently `150` for the default 200/50 pair).
- Unit test on the derived property covering the four interesting cases:
  headroom < 0 → clamps to `verdict_rationale_max_chars`;
  headroom == 0 → returns the full cap;
  headroom == 50 (default) → returns `cap - 50`;
  headroom > cap → clamps to 40.

### M1 — Anti-truncation guard

**Symptom.** 5 of 28 LLM retries were JSON-truncation EOF errors caused by
the model running into `max_output_tokens` while repeating a token (sample
payload tails: `AMZN_AMZN_AMZN…`, `\n\n\n…`, `00000…`).

**Fix.** Add one line near the top of both LLM-analyst prompt templates,
before the `--- TICKER DATA ---` block:

```
Stop emitting if you are about to repeat a token or symbol three or more
times in a row.  Return the verdict as-is and never emit filler tokens.
```

**Verification.** Snapshot test confirming the guard line is present in
both `news/prompts.py` and `fundamental/prompts.py` rendered output.

### M4 — News bearish-direction nudge

**Symptom.** News verdict stance distribution was 467 bullish vs 25 bearish
across the run.  The catalyst tag `catalyst:product_launch` dominated at
1 090 occurrences; the LLM was rounding any non-unambiguously-bad news up to
bullish/neutral.

**Fix.** Add to the news prompt's existing decision-rule block
(`news/prompts.py:77-81`):

```
- Bearish is appropriate for missed guidance, downgrade, supplier loss,
  executive departure, regulatory action, or adverse legal outcome — do
  NOT default to neutral when evidence is materially negative.
```

Same reasoning-respecting principle as D2: corrective anchor, not new
prescription.

**Verification.** Snapshot test confirming the bearish-trigger guidance is
present.

### M3 — Drop dead Social rows from strategist evidence

**Symptom.** The strategist's per-ticker evidence block renders
`[Social] is_no_data: true` for all 20 tickers on every tick (no social
provider is wired).  ~30 chars × 20 tickers = ~600 chars of dead attention
per strategist call.

**Fix.** In `src/agents/strategist/evidence_view.py`, when rendering the
per-ticker analyst block, omit the `[Social]` row entirely when its verdict
has `is_no_data == True`.  When a Social verdict carries data, render
normally.  The change preserves room for Social to come back when a provider
is wired without further code change.

**Verification.** Render strategist evidence on a synthetic state where all
Social verdicts are `is_no_data=True`; assert no `[Social]` lines appear.
Render with one Social row populated; assert the populated row appears and
the others remain hidden.

### M5 — Strategist worked-examples ticker

**Symptom.** `src/agents/strategist/prompts.py:101-117` worked examples
both use `AAPL` — a known mild-bias source where the model latches onto the
specific ticker when reasoning about the example shape.

**Fix.** Change to a generic `XYZ` placeholder.  Pure cosmetic.

**Verification.** Snapshot test confirming `XYZ` appears in worked examples
and `AAPL` does not.

---

## 7. R-band — strategist rule relaxations

**Diagnosis.** The first-test market analysis attributed roughly 3.7 pp of
the −3.31 pp SPY gap to the strategist holding 78 % cash across the whole
window.  Three rules combine to produce that drag:

- `CASH_FLOOR_WEIGHT = 0.10` directly forbids deployment above 90 %.
- `MAX_DELTA_PER_TICKER = 0.01` means a 5 % conviction takes 5 ticks to
  express and an 8 % position takes a whole trading day to build.
- The strategist prompt at `prompts.py:86-88` restates both ceilings, so
  even loosening the deterministic clamps would leave the LLM trained on
  the stricter envelope.

A second, structural problem is that these constants live in
`src/orchestrator/state.py:9-13` rather than `config/` — directly violating
the project's *"all configuration in `config/*.json`"* convention spelled out
in the root `CLAUDE.md`.  Without addressing that, every future relaxation
becomes a code edit + redeploy rather than a JSON edit + restart.

**Design principle.** Aggressively widen the envelope so the strategist's
job is "weigh evidence" rather than "thread a 10 % needle".  Tell the LLM
explicitly what the gate enforces — no hidden ceilings — and source the
values shown to the LLM from the same config the gate reads, so the prompt
and gate stay in lockstep through any future tuning.

### R1 — Remove the cash floor

**Fix.** `CASH_FLOOR_WEIGHT`: 0.10 → 0.00.  Removes the proportional scale-
down step at `src/agents/risk_gate/constraints.py:31-46` for the default
config; the function stays in place so a future operator can re-introduce a
floor without a code change.

**Status.** **R** — respects the existing constraint pipeline; only the
default value changes.

**Live-safe.** Yes — pipeline constant.

### R2 — Widen `MAX_DELTA_PER_TICKER`

**Fix.** `MAX_DELTA_PER_TICKER`: 0.01 → 0.05.  Five-times faster ramp so a
5 % conviction expresses in one tick instead of five.  Still slow enough
that LLM noise can't whipsaw a position from 0 % to 20 % in a single tick.

**Status.** **R** — respects the existing constraint pipeline.

**Live-safe.** Yes.

### R3 — Lift `MAX_TOTAL_TURNOVER`

**Fix.** `MAX_TOTAL_TURNOVER`: 0.30 → 0.50.  The old value was sized for a
0.01 per-ticker cap (≤30 tickers churning at 1 % each).  With R2 in place
the per-ticker cap of 0.05 × 10 active names already hits 0.50, so leaving
turnover at 0.30 would artificially bind the new per-ticker headroom.
0.50 keeps the all-tickers-churn case feasible without permitting a single
tick to rebuild the whole book.

**Status.** **R** — respects the existing constraint pipeline.

**Live-safe.** Yes.

### R4 — Migrate constants to `config/risk_gate.json`

**Fix.** Move the five risk constants from
`src/orchestrator/state.py:9-13` into a new JSON file `config/risk_gate.json`:

```json
{
    "min_held_weight":       0.001,
    "max_position_weight":   0.20,
    "cash_floor_weight":     0.00,
    "max_delta_per_ticker":  0.05,
    "max_total_turnover":    0.50
}
```

Add a loader `src/config/risk_gate.py` mirroring the
`src/config/strategist.py` pattern — a `get_risk_gate_config()` helper that
returns a frozen dataclass with the same field names.

Update `src/orchestrator/state.py:9-13` to import the resolved values and
re-export them as module-level constants:

```python
from config.risk_gate import get_risk_gate_config as _get_risk_cfg

_risk = _get_risk_cfg()

MIN_HELD_WEIGHT      = _risk.min_held_weight
MAX_POSITION_WEIGHT  = _risk.max_position_weight
CASH_FLOOR_WEIGHT    = _risk.cash_floor_weight
MAX_DELTA_PER_TICKER = _risk.max_delta_per_ticker
MAX_TOTAL_TURNOVER   = _risk.max_total_turnover
```

That keeps every existing call site
(`src/agents/risk_gate/constraints.py:4-10`,
 `src/agents/risk_gate/agent.py:12`) working unchanged — they still
`from orchestrator.state import ...` the same names.

Update `config/README.md` to describe the new file and each field.

**Status.** **R** — respects the project's config convention; no behaviour
change beyond what R1/R2/R3 already changed.

**Live-safe.** Yes — pipeline + config; both lifecycles read the same JSON.

### R5 — Prompt restates the surviving rules (config-driven)

**Why this is non-optional alongside R4.** If we put numbers in
`config/risk_gate.json` but hardcode them as literals in the prompt, then
later changing `max_delta_per_ticker` to 0.10 silently leaves the LLM still
believing the cap is 5 %.  The two must move together.

**Fix.** Extend the existing build-time substitution pattern in
`src/agents/strategist/prompts.py` — the same machinery that already
substitutes `{{STANCE_RATIONALE_MAX}}` etc. from `config/strategist.json`.

Add four new markers populated from the risk-gate config loader at module
import:

```python
from config.risk_gate import get_risk_gate_config

_RISK              = get_risk_gate_config()
_MAX_POSITION_PCT  = int(round(_RISK.max_position_weight  * 100))
_MAX_DELTA_PCT     = int(round(_RISK.max_delta_per_ticker * 100))
_MAX_TURNOVER_PCT  = int(round(_RISK.max_total_turnover   * 100))
_CASH_FLOOR_PCT    = int(round(_RISK.cash_floor_weight    * 100))
```

Replace `src/agents/strategist/prompts.py:86-88` with the following template
text (markers shown literally; the `.replace()` chain at the bottom of the
module substitutes them in):

```
preferred_weight: float in [0.0, 1.0].  Long-only — 0.0 is the floor.

Hard rules the risk gate enforces after you respond (so a stance that
violates them will be clamped — propose values that already respect them):
- Single-ticker weight capped at {{MAX_POSITION_PCT}}%.
- Per-ticker weight change capped at {{MAX_DELTA_PCT}}% per tick — if you
  want to size up faster, the gate will trim your delta back to
  {{MAX_DELTA_PCT}}% and you ramp over multiple ticks.
- Total per-tick turnover (sum of |deltas| across watchlist) capped at
  {{MAX_TURNOVER_PCT}}%.
{{CASH_FLOOR_STANZA}}
```

The `{{CASH_FLOOR_STANZA}}` marker is conditionally rendered at module
import based on the loaded `cash_floor_weight`:

- if `cash_floor_weight == 0.0`:
  `- No cash floor — full deployment is permitted when conviction supports it.`
- if `cash_floor_weight > 0.0`:
  `- Watchlist weight sum capped at {{100 − CASH_FLOOR_PCT}}% (cash reserve ≥{{CASH_FLOOR_PCT}}%).`

This keeps the prompt accurate if a future operator re-introduces a floor
via config edit alone.

Add the four (plus stanza) substitutions to the existing `.replace()` chain
at the bottom of `prompts.py`.

**Status.** **R** — extends the prompt's existing substitution machinery.

**Live-safe.** Yes — prompt module.

### R-band verification

| Fix | Verification                                                                                                                                              |
|-----|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| R1  | Risk-gate unit test: post-clamp sum of weights can reach 1.0 (no cash-floor scaling) on a synthetic stance set summing to 1.0.                             |
| R2  | Risk-gate unit test: per-ticker delta of 0.05 passes unchanged; delta of 0.06 is clamped to 0.05.                                                          |
| R3  | Risk-gate unit test: total turnover of 0.50 passes unchanged; 0.51 is proportionally scaled.                                                               |
| R4  | Unit test on loader: assert each field of `get_risk_gate_config()` matches the JSON contents.  Existing `state.py` constants still importable by name.     |
| R5  | Snapshot test on rendered strategist prompt: contains the literal strings "20%", "5%", "50%", "No cash floor".  Then patch `config/risk_gate.json` to `cash_floor_weight=0.05, max_delta_per_ticker=0.02, max_total_turnover=0.40`, reload the module, re-render — assert the snapshot now contains "2%", "40%", "Cash reserve ≥5%".  Catches the prompt-config drift case explicitly. |

### Reasonable-bounds table (the new values aren't arbitrary)

| Constant                | Old   | New   | Why this value                                                                                          |
|-------------------------|-------|-------|---------------------------------------------------------------------------------------------------------|
| `cash_floor_weight`     | 0.10  | 0.00  | Pre-deployment paper bot; cash drag has no upside.  Operator can re-add via config if needed.           |
| `max_delta_per_ticker`  | 0.01  | 0.05  | One-tick expression of a 5 % conviction.  Still ≤ ¼ of `max_position_weight`, so a single tick can't fully load any ticker. |
| `max_total_turnover`    | 0.30  | 0.50  | Rescales to the new per-ticker cap (10 active names × 5 % = 50 %).  Permits a full re-allocation across 2 ticks worst-case. |
| `max_position_weight`   | 0.20  | 0.20  | Unchanged.  Concentration guard on a 20-stock watchlist.                                                |
| `min_held_weight`       | 0.001 | 0.001 | Unchanged.  Tiny lifecycle threshold, not load-bearing.                                                 |

### What we deliberately did NOT add

- A `MIN_INVESTED_WEIGHT` floor (the H1 callout in the market analysis).
  Adds prescription where the user-stated direction is *"give the LLM room
  to reason"*.  If post-rollout backtest shows the strategist still parks
  in cash despite R1, that becomes the next iteration's evidence.
- Conviction-scaled `max_delta` (e.g. `0.01 + conviction × 0.04`).  Adds
  complexity; a flat 0.05 captures most of the benefit.  Backlog candidate.
- Per-ticker stop-price enforcement at the risk gate.  The strategist
  already articulates stops on every non-zero stance (schema-enforced); the
  gate currently doesn't act on them.  Closing that loop is its own
  consequential design decision and belongs in a later spec.

---

## 8. Testing strategy

One verification per fix.  Most are point fixes; the testing budget is
correspondingly small.  Backtest-level verification is out of band — re-run
`baseline-2025-09` after landing and inspect:

- Bullish-fundamental rate > 0 across the 20-stock watchlist (D2 sanity).
- Missing-report rate ≈ 0 (D1.1 effect).
- Retry count breakdown: HTTP-429s unchanged, string_too_long ≈ 0 (H4),
  json_invalid ≈ 0 (M1), validation-driven retries ≤ ~30 % of news calls (D1.1).
- Token count > 0 in `report/metrics.md` (S4).
- `audit/*.tick.json` `cache_hits` matches structured-log count (S3).
- Cash weight ≪ 0.78 in the steady state once positions ramp (R1/R2 effect).
  Concentration spread across more than 3 tickers (no longer veto-bound).

| Fix  | Verification                                                                                                                                |
|------|---------------------------------------------------------------------------------------------------------------------------------------------|
| S1   | Phase 2 seed at fixed `as_of` produces no bars with `ts > as_of`.  Audit `any_filter_key_after_as_of` does not fire.                         |
| S2   | 1 % trim survives in `state["positions"]`; full exit deletes and writes one `TradeLogRow`.                                                  |
| S3   | Audit `cache_hits` count matches structured-log `cache_hit` count.                                                                          |
| S4   | "Total tokens" > 0 in metrics; per-agent latency table is non-empty.                                                                        |
| S5   | `decisions/*.json` `analyst_inputs.fundamental.insider` round-trips as JSON dict.  Strict serialiser raises on un-dumpable input.            |
| S6   | Derivation function tags each (prior, new) pair into the correct enum value across all six categories.                                      |
| S7   | Injected exception in `03_strategist` span recording logs via `logger.exception`.                                                           |
| S8   | Renamed tripwires no longer counted as "actionable" in the audit summary.                                                                   |
| S9   | Retry warning from a stub agent named `"TestAnalyst"` contains `"TestAnalyst"` in the log record (not `<unknown>`).                          |
| D1.1 | `AnalystVerdict.model_validate` raises when `is_no_data=False, report=None`; valid verdict round-trips unchanged.                            |
| D1.2 | Rendered news + fundamental prompts contain the strengthened "REQUIRED whenever is_no_data=false" wording.                                  |
| D1.3 | Strategist evidence renders `(no report this tick — analyst compliance failure)` for the degenerate case.                                  |
| D2.1 | Rendered fundamental prompt contains the new anchors and not the old AND-conjunction wording.                                               |
| H4   | Rendered prompts cite the derived budget value.  Derived property clamps to 40 on misconfig.                                                |
| M1   | Anti-truncation guard line present in both LLM-analyst prompts.                                                                             |
| M4   | News bearish guidance present in rendered news prompt.                                                                                      |
| M3   | All-no-data Social → no `[Social]` lines in strategist evidence; one populated → that row renders.                                          |
| M5   | `XYZ` in strategist worked examples; `AAPL` absent.                                                                                         |
| R1   | Synthetic stances summing to 1.0 survive the cash-floor step unchanged.                                                                     |
| R2   | Per-ticker delta of 0.05 unclamped; 0.06 clamped to 0.05.                                                                                   |
| R3   | Total turnover of 0.50 unclamped; 0.51 proportionally scaled.                                                                               |
| R4   | Loader fields match `config/risk_gate.json` contents; legacy imports from `orchestrator.state` still resolve.                               |
| R5   | Rendered prompt contains the literal `20%`, `5%`, `50%`, `No cash floor`.  Patching the JSON to `{0.05, 0.02, 0.40}` flips the snapshot to `2%`, `40%`, `Cash reserve ≥5%`. |

---

## 9. Implementation order

The twenty fixes group into five bands of overlapping file surface;
ordering matters only where one band's verification depends on another's
behaviour.  The five R-band items thread into the existing bands rather
than forming their own — R4 into config layer (band 1), R1/R2/R3 as
data-only consequences of R4, R5 into the strategist prompt (band 4).

1. **Config + schema layer first.**
   - H4 config field + derived property.
   - D1.1 schema validator on `AnalystVerdict`.
   - **R4** new `config/risk_gate.json` + loader + `state.py` re-export.
   - Unblocks downstream prompt tests because rendered prompts depend on
     config values for both the analyst rationale budget and the
     strategist's risk-rule restatement.

2. **Pipeline code.** All independent of each other; can be parallelised.
   - S1 — Phase 2 seed of `reference_prices`.
   - S2 — Executor bookkeeping.
   - S3 — Cache-hits audit relocated.
   - S5 — Insider `.model_dump()` + strict decision-logger serialiser.
   - S6 — `decision_tag` derivation.
   - S9 — Tenacity `before_sleep` closure carries the wrapped agent name.
   - **R1, R2, R3** are pure data — they land *automatically* with R4
     because the default values in `config/risk_gate.json` already encode
     them.  No additional code change.

3. **Analyst prompts.** All in the analyst prompt files.
   - D1.2 prompt strengthening.
   - D2.1 fundamental decision-rule rewrite.
   - H4 switch from `verdict_rationale_max_chars` to
     `verdict_rationale_prompt_budget`.
   - M1 anti-truncation guard (news + fundamental).
   - M4 news bearish-direction nudge.

4. **Strategist prompt + evidence renderer.**  All in
   `src/agents/strategist/evidence_view.py` and
   `src/agents/strategist/prompts.py`.
   - D1.3 visibility safety net.
   - M3 drop dead Social rows.
   - M5 worked-example ticker rename.
   - **R5** prompt restates surviving rules with config-driven values.

5. **Observability / backtest reporting.**  All in backtest-only files.
   - S4 span-name prefix bugs.
   - S7 strategist trace exception logging.
   - S8 tripwire renames.

Within each band, parallelism is fine.  Bands (3) and (4) read from band (1)
so (1) must land first — band (4) in particular depends on R4 because R5's
prompt substitution sources its values from the new risk-gate config
loader.  Bands (2) and (5) are independent of (1) but should land before
any backtest re-run.

---

## 10. Live-symmetry summary

All fixes are lifecycle-symmetric (live ≡ backtest contractually).  Several
are §D1 carve-outs that live in backtest-only artefact paths and have no live
counterpart.

| Fix    | Lives in                                                            | Live-safe?           |
|--------|---------------------------------------------------------------------|----------------------|
| S1     | Pipeline / lifecycle wrapper                                        | ✓                    |
| S2     | `src/agents/executor/agent.py`                                      | ✓                    |
| S3     | Analyst pipeline / observability                                    | ✓ (also broken in live today) |
| S4     | `src/backtest/reporting.py`                                         | N/A — §D1 carve-out  |
| S5     | `src/agents/analysts/fundamental/fetch_agent.py` + decision logger  | ✓ (logger is §D1)    |
| S6     | `src/agents/strategist/derivation.py`                               | ✓                    |
| S7     | `src/observability/trace.py`                                        | N/A — §D1            |
| S8     | `src/backtest/audit/`                                               | N/A — §D1            |
| S9     | `src/agents/llm_retry.py`                                           | ✓                    |
| D1.1   | `src/contract/evidence.py`                                          | ✓                    |
| D1.2   | News + Fundamental prompts                                          | ✓                    |
| D1.3   | `src/agents/strategist/evidence_view.py`                            | ✓                    |
| D2.1   | `src/agents/analysts/fundamental/prompts.py`                        | ✓                    |
| H4     | `src/config/analysts.py` + prompts                                  | ✓                    |
| M1     | News + Fundamental prompts                                          | ✓                    |
| M4     | News prompt                                                         | ✓                    |
| M3     | `src/agents/strategist/evidence_view.py`                            | ✓                    |
| M5     | `src/agents/strategist/prompts.py`                                  | ✓                    |
| R1–R3  | `config/risk_gate.json` (default values)                            | ✓                    |
| R4     | `config/risk_gate.json` + new `src/config/risk_gate.py` + `src/orchestrator/state.py` re-export | ✓ |
| R5     | `src/agents/strategist/prompts.py` (build-time substitution)        | ✓                    |

Nothing in this spec relies on in-process state survival between ticks.
Cold-start one Cloud Run Job per tick, every fix still does the right thing.

---

## 11. Cross-spec dependencies

This spec's value is independent of Spec B (foundational thesis-memory) but
**Spec B's value is gated on this spec landing**:

- **S2** — without it, the first 1 % trim wipes the thesis Spec B just
  persisted.  Memory becomes self-defeating.
- **S6** — without it, any memory writer keyed on decision intent sees
  `catalyst_driven_entry` for every row and cannot discriminate.
- **S1** — without it, any macro / benchmark memory consumer sees future
  bars.
- **D1.1** — without it, the per-tick news evidence Spec B / Spec C will
  archive has 30 % of rows carrying `report: null`, corrupting the corpus
  before it grows.
- **D2.1** — without it, the fundamental verdict distribution is 0 bullish,
  meaning the memory system cannot learn from positive fundamental
  precedents (one stuck arm of the closed-loop learning).

**R-band note.** None of the rule relaxations gate Spec B — the cash floor
and ramp speed are independent of the thesis-memory work.  They do
*amplify* Spec B's value: a strategist deploying 40 % of capital across 8
tickers writes a far richer memory corpus than one parking 78 % in cash
across 3.  Ship order is unaffected; benefit compounds.

Spec A ships first.  Spec B brainstorms in parallel and ships immediately
after.

---

## 12. Backlog candidates surfaced

Three follow-up ideas that emerged during the brainstorm but fell out of
scope.  To be proposed for `docs/superpowers/backlog.md` after this spec is
approved.

1. **Vocab rename — `planned_sale_dominant` → `routine_sale_dominant`.**
   Tighten naming semantics now that the LLM prompt explicitly recasts
   10b5-1 sales as neutral.  Migration surface: memory keys, audit reports,
   decision logger.  Best done after Spec B has landed so the memory writer
   participates in the migration.

2. **Extend derived-budget pattern (H4) to summary / driver-name / driver-body
   caps.**  Only rationale has empirical retry evidence today, so YAGNI for
   the surgical spec.  Worth revisiting if a future backtest shows
   string_too_long retries on the other fields.

3. **Diagnose SPY-return discrepancy in `src/backtest/reporting.py`.**
   `report/metrics.md` shows vs-SPY −4.55 pp; recomputed from
   `db.sqlite::portfolio_snapshots` is −3.31 pp.  Likely a
   forward-return-backfill anchor mismatch.  One-line investigation, worth a
   dedicated short pass after Spec A lands.

4. **Conviction-scaled max-delta.**  Replace the flat `max_delta_per_ticker`
   with `0.01 + conviction × 0.04` (or similar), so high-conviction stances
   ramp faster than low-conviction ones.  Adds complexity to the constraint
   loop and the prompt restatement; revisit if a post-R2 backtest still
   shows undersized expressions of conviction.

5. **Per-ticker stop-price enforcement at the risk gate.**  Strategist
   already articulates `stop_price` on every non-zero stance.  The gate
   could clamp positions to zero when the broker-reported last price
   crosses the stop.  Its own design decision (stop-loss policy, slippage
   handling) — worth a dedicated spec.

6. **`MIN_INVESTED_WEIGHT` floor on the strategist prompt.**  If post-R1
   backtest still shows the strategist parking in cash, consider explicit
   prompt-side instruction to deploy ≥X % of capital when at least one
   actionable signal exists.

---

**End of spec.**
