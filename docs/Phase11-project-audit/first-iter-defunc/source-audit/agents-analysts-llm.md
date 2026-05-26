# Source audit — `src/agents/analysts/fundamental/` and `src/agents/analysts/news/`

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 12 (6 per package: `__init__.py`, `agent.py`, `fetch_agent.py`, `fetch.py`, `joiner.py`, `per_ticker.py`, `prompts.py` — `__init__.py` counted once per package; 7×2 − no — 12 distinct .py modules across the two packages)
**Findings:** 0 P0 · 2 P1 · 4 P2 · 3 P3

## Summary

These two packages implement the Phase-9 per-ticker LLM analyst fan-out:
`SequentialAgent[FetchAgent, ParallelAgent[per-ticker LlmAgent×N], JoinerAgent]`.
The two pipelines are deliberately structurally symmetric (per the spec) and
the audit confirms this symmetry holds in shape — both have BaseAgent fetcher,
identical `IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))` wrapping,
mirrored joiner logic, and matching `temp:<analyst>_data` /
`temp:<analyst>_context_<TICKER>` / `temp:<analyst>_verdict_<TICKER>` /
`temp:_obs_<analyst>_calls` key conventions. Drift between the two is minor
(prompt brace styling for closed-vocab options, one missing `__future__`
import in the fundamental package init, a hardcoded `"30d"` label in the
fundamental prompt). The main themes are: (1) silent-failure surfaces in the
fetch agents that swallow per-provider exceptions into empty placeholders
without surfacing `is_no_data` upstream, and feeding the LLM context for a
ticker with no usable data without flagging — the LLM is then expected to
self-declare `is_no_data`, which is fragile; (2) doc/code drift, mostly in
the surrounding documentation (`config/README.md` still names the retired
`build_news_analyst` / `build_fundamental_analyst` factories — flagged here
because the audited modules' docstrings reference the retired names too) and
contract-side: §A has no row for the `news_evidence` / `fundamental_evidence`
keys that the joiners write to state every tick. No P0 contract violations
or live parallel old/new branches were found — the Phase-9 split is clean,
the legacy batched factories are fully removed, and `output_key` uniqueness
across the four analyst branches is satisfied per §C-Rule 4.

## Findings

### Fundamental

### P1-01 · C5 silent-failure attractor · `FundamentalFetchAgent` swallows three independent provider errors per ticker into empty payloads

- **Location:** `src/agents/analysts/fundamental/fetch_agent.py:118-163` (three sequential `try / except Exception:` blocks around `get_company_ratios`, `get_company_filings`, `get_insider_trades`).
- **Confidence:** medium
- **Description:**
  Each of the three provider calls is wrapped in its own broad `except Exception` that downgrades to `ratios_payload = None`, `filings_payload = []`, or `Form4Bundle(trades=[], derivatives=[])` and emits only a `_LOGGER.warning(...)`. The LLM downstream is still invoked for the ticker with a near-empty `temp:fundamental_context_<TICKER>` block; the LLM is expected to self-declare `is_no_data=true`, but nothing structural prevents the LLM from producing a confident `bullish` / `bearish` verdict on the basis of the empty context. There is no `feature_warning` propagated to the joiner, no upstream signal that the data is degraded, and no test in scope asserts that the warning is emitted (`test-policy §A.7` line: "Verify the logs the code claims to emit actually fire"). This is the recurring silent-failure attractor class — three independent fail-soft branches with no surfacing to the contract.
- **Suggested action:**
  Treat per-provider failure as a tracked degradation: write a structured `temp:fundamental_fetch_warnings_<TICKER>` entry (or surface it through `AnalystEvidence.feature_warnings` once the joiner reads it) and let the joiner promote it into the evidence record. At minimum, when *all three* provider calls have failed for a ticker, short-circuit the per-ticker LLM call and synthesise the no-data `TickerVerdict` directly — the LLM has nothing to reason about and is a token-budget sink for that ticker.

### P2-01 · C7 doc/code drift · "INSIDER ACTIVITY (30d, structured)" label is hardcoded in the prompt while the actual lookback comes from config

- **Location:** `src/agents/analysts/fundamental/prompts.py:52`; the lookback that drives the data window lives in `src/agents/analysts/fundamental/fetch.py:110` and reads `get_config().defaults.insider_lookback_days`.
- **Confidence:** high
- **Description:**
  The prompt template literally says `-- INSIDER ACTIVITY (30d, structured) --`, but the matching label in the fetch helper (`fetch.py:110`) interpolates `insider_lookback_days` from `config/data.json`. If the config value ever drifts from 30, the prompt continues to tell the LLM the window is 30 days while the data covers a different window — silent-drift between what the LLM is told and what the LLM is given.
- **Suggested action:**
  Add a `{insider_lookback_days}` placeholder to the template and render it in `build_fundamental_instruction` from `get_config().defaults.insider_lookback_days`, the same way `rationale_max` etc. are already wired. Drop the literal `30d`.

### P2-02 · C7 doc/code drift · Fundamental package `__init__.py` lacks `from __future__ import annotations` while the news sibling has it

- **Location:** `src/agents/analysts/fundamental/__init__.py` (missing); `src/agents/analysts/news/__init__.py:11` (has it).
- **Confidence:** high
- **Description:**
  Symmetry-by-design pipelines should match boilerplate. The news package init starts with `from __future__ import annotations`; fundamental's does not. Both files only re-export a factory and `__all__`, so neither *needs* it today — but the inconsistency is the kind of drift that compounds and was called out in the dispatch as a C7 finding.
- **Suggested action:**
  Add `from __future__ import annotations` to `src/agents/analysts/fundamental/__init__.py` (or remove it from the news sibling) so the two init files match.

### P2-03 · C7 doc/code drift · Prompt rendering shows closed-vocab options inside braces for News but not for Fundamental

- **Location:** `src/agents/analysts/news/prompts.py:127-129` vs `src/agents/analysts/fundamental/prompts.py:151-154`.
- **Confidence:** high
- **Description:**
  News passes `catalyst_options = "{" + " | ".join(...) + "}"` etc. so the rendered prompt reads `catalyst:<type> ∈ {earnings | guidance | downgrade}`. Fundamental passes `guidance_options = " | ".join(vocab.guidance)` so the rendered prompt reads `guidance:<value> ∈ raised | lowered | maintained` — no surrounding braces. Two structurally identical sibling LLMs are getting subtly different vocabulary-syntax conventions; the test files in `tests/unit/test_*_prompt_render.py` (out of scope here, reference-only) check substring presence and would not catch this drift.
- **Suggested action:**
  Pick one convention and apply it to both prompt builders. The braces form (news) is slightly clearer to the LLM that the options are a closed set; either is defensible, but the two siblings should agree.

### P3-01 · C7 doc/code drift · Module docstrings reference retired `build_fundamental_analyst` factory; `config/README.md` still names both retired factories

- **Location:** `src/agents/analysts/fundamental/__init__.py:8`, `src/agents/analysts/fundamental/agent.py:12`, `src/agents/analysts/news/__init__.py:8`, `src/agents/analysts/news/agent.py:11` (all retained intentionally as "retired in Phase 9" notes); separately `config/README.md:413-414` still routes the reader to the *retired* names.
- **Confidence:** high
- **Description:**
  The in-module docstrings naming `build_fundamental_analyst` / `build_news_analyst` are deliberate Phase-9 farewell notes and they are useful — leave them. The blocker is `config/README.md` lines 413-414: the table still tells the reader the model IDs are "read by `…::build_news_analyst`" / "`…::build_fundamental_analyst`". Those symbols no longer exist (`grep -rn` confirms only docstrings reference them). A reader following the README will land on a dead symbol.
- **Suggested action:**
  Update `config/README.md` lines 413-414 to point at `build_news_branch` / `build_fundamental_branch`. **Cross-subsystem — file the README fix under `config/README.md` in consolidation, not against this audit's subsystem.**

### News

### P1-02 · C5 silent-failure attractor · `NewsFetchAgent` swallows provider failure into an empty article list with no upstream signal

- **Location:** `src/agents/analysts/news/fetch_agent.py:80-86` (`try / except Exception:` around `get_stock_news`).
- **Confidence:** medium
- **Description:**
  Identical shape to P1-01 on the Fundamental side. A failed `get_stock_news` call collapses to `articles = []`, the per-ticker context block becomes `(no news available)`, and the LLM is still invoked for the ticker. The prompt instructs the LLM `is_no_data true if no headlines in the window` — but `(no news available)` from a *fetch failure* and `(no news available)` from a *real empty window* are indistinguishable to the LLM and to every downstream consumer. No `caplog` test in scope asserts the warning fires, no warning is propagated as evidence-channel metadata, and no flag distinguishes degraded from genuinely-empty input.
- **Suggested action:**
  Same shape as the suggested action for P1-01: surface a structured per-ticker fetch warning (either via a `temp:news_fetch_warnings_<TICKER>` accumulator the joiner consumes, or by promoting it onto `AnalystEvidence.feature_warnings`) so a real fetch failure is visibly distinct from an empty news window in the evidence record the strategist sees.

### P2-04 · C4 contract violation · `news_evidence` and `fundamental_evidence` are written every tick but have no §A row

- **Location:** `src/agents/analysts/news/joiner.py:138-140` (writes `news_evidence` in the state_delta); `src/agents/analysts/fundamental/joiner.py:148-150` (writes `fundamental_evidence`). Consumers: `src/agents/strategist/context_shim.py:177-178`, `src/agents/contract/evidence_writer.py:28-29`.
- **Confidence:** medium
- **Description:**
  Both joiners yield a state_delta that writes two keys: `<analyst>_verdicts` (the §A row currently exists for both) and `<analyst>_evidence`. The evidence keys carry the per-ticker `AnalystEvidence` dumps that the strategist's `context_shim` and the snapshot's `evidence_writer` read in the same tick. Per §A's stated scope ("fields that … carry an agent's output across an agent boundary"), `news_evidence` and `fundamental_evidence` qualify and should have rows. They currently do not. This is contract-doc drift on the §A table itself — file against the doc, not against this subsystem's code. (Reciprocal note: the §A `news_verdicts` / `fundamental_verdicts` rows do say "Yielded as a list of per-ticker verdict dicts" via the joiner, so the writer side is acknowledged; only the evidence twin is missing.) Note also that `technical_evidence` and `social_evidence` likely have the same gap — flag for consolidation.
- **Suggested action:**
  Add §A rows for `news_evidence` and `fundamental_evidence` (and likely `technical_evidence` / `social_evidence`) owned by the corresponding joiner/analyst, lifetime tick-scoped, source-of-truth = the joiner's `AnalystEvidence` construction. **File under `subsystem: docs/contract-invariants` in consolidation per §2-C7 routing instruction.**

### P3-02 · C5 silent-failure attractor · Joiner re-validates `raw_v` and silently overwrites the LLM's `ticker` field

- **Location:** `src/agents/analysts/news/joiner.py:81` and `src/agents/analysts/fundamental/joiner.py:92` (`TickerVerdict.model_validate({**raw_v, "ticker": ticker})`).
- **Confidence:** low
- **Description:**
  The joiners build `{**raw_v, "ticker": ticker}` so the watchlist ticker overrides whatever the LLM put in the verdict's `ticker` field. The prompt already mandates the LLM emit the watchlist ticker verbatim — but if the LLM emits the wrong ticker (e.g. picked the wrong company in a multi-ticker context window), the silent override hides it. Nothing logs the mismatch. This is a low-severity silent-failure attractor: the bug class it hides (cross-ticker contamination) would manifest as wrong-ticker reasoning *with the right ticker label*, and no monitoring would surface it.
- **Suggested action:**
  Compare `raw_v.get("ticker")` against the bound ticker before the override; on mismatch log a structured warning (`kind="ticker_mismatch"`) so the issue is visible. Keep the override for safety; just stop hiding it.

### P3-03 · C3 overabstraction (minor) · Redundant `if _obs_calls or tickers:` guard around the observability summary emit

- **Location:** `src/agents/analysts/news/joiner.py:119-129`, `src/agents/analysts/fundamental/joiner.py:130-140`.
- **Confidence:** high
- **Description:**
  Both joiners have:
  ```python
  if _obs_calls or tickers:
      import os
      if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
          emit_analyst_summary(...)
  ```
  The inner env-var gate is the real switch. The outer `_obs_calls or tickers` is effectively always true when there is any work to do, and `emit_analyst_summary` is presumably defensive against an empty call list anyway. The outer guard adds nothing. Also: `import os` inside the function body is unusual style — `os` is already imported by `per_ticker.py` at module top; the joiner can hoist it.
- **Suggested action:**
  Drop the outer `if _obs_calls or tickers:` guard and move `import os` to the module top in both joiners. Tiny cleanup, no behavioural change.
