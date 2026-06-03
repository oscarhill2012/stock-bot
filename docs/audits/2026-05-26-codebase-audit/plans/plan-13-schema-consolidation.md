# Plan 13 — Contract Schema Two-Shape Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the `TickerVerdict` / `LlmTickerVerdict` two-shape pattern in `src/contract/evidence.py` and adjacent silent-degradation footguns (`feature_warnings`, `last_price` sentinels, the duplicated insider extractor path) into a single, loud-failing surface so the contract layer has one canonical verdict shape, one inflate path, one price sentinel, and one insider feature path.

**Architecture:** Keep `LlmTickerVerdict` as the LLM emit-schema (its three structural commitments — required fields, declaration order, no `max_length` — are load-bearing against the Vertex constrained decoder and explicitly documented). Surface a single `LlmTickerVerdict.to_ticker_verdict(ticker: str) -> TickerVerdict` conversion method so both joiners stop hand-rolling `model_validate({**raw_v, "ticker": ticker})`. Replace `TickerEvidence.last_price: float | None = None` with a `PositiveFloat | None` (raises on `0.0` or negative) so the downstream "treat `None` or `0.0` as no price" sentinel split collapses. Wire `AnalystEvidence.feature_warnings` to actually carry extractor-emitted warnings (or delete the field + persisted column if no extractor populates it). Retire the legacy `Form4Bundle` insider extractor branch in `src/contract/extractors/fundamental.py` once we have verified no live producer still hands the contract the legacy shape.

**Tech Stack:** Pydantic v2, pytest, SQLite (persistence layer for `feature_warnings_json` column).

---

## 1. Trust contract

**Trusts** (plans landed before this one):

- **Plan 02** owns `AnalystVerdict.rationale` / `report` split and the `_report_required_when_data_present` model-validator. Plan 13 builds on that vocabulary and does not re-touch the rationale collapse (A-049 is **out of scope** here).
- **Plan 05** owns provider loud-fail conversions. Any `feature_warnings` wiring here can assume providers raise (not silently empty-fill) on missing data; warnings carry extractor-side issues only.
- **Plan 07** has retired the legacy strategist `_strategist_validation_callback` and the parallel pre-collapse stance vocabulary.
- **Plan 11** has consolidated the `tests/` tree, introduced shared `tests/_helpers/` (notably `assert_no_silent_degradation` and `make_tick_state`), moved `tests/contract/test_llm_ticker_verdict.py` under `tests/unit/contract/`, and explicitly left A-051/A-053/A-054/A-055 src-side echoes to **this** plan.
- **Plan 12** has executed the §3 out-of-scope handover that originated this plan.

**Trusts to land** (downstream of this plan): nothing — Plan 13 is the last in the audit remediation sequence.

---

## 2. Scope-check table

Verified against `docs/audits/2026-05-26-codebase-audit/FINDINGS.md` lines 361–390.

| Finding | What it asks | Src-side files touched | Test-side echo (deferred by Plan 11) |
|---|---|---|---|
| **A-051** `TickerVerdict` / `LlmTickerVerdict` two-shape pattern | Investigate `LooseToStrict` mixin or a single conversion helper between the strict-canonical and LLM-emit shapes. | `src/contract/evidence.py:158-266`; consumers: `src/agents/analysts/news/joiner.py:60-95`, `src/agents/analysts/fundamental/joiner.py:60-95`, `src/agents/strategist/evidence_view.py:88-103`. | `tests/unit/contract/test_llm_ticker_verdict.py` (already moved by Plan 11 Block A); add a new identity test that the inflate path lives in one place. |
| **A-053** `feature_warnings` declared but never populated | Wire emission or delete the field + column. | `src/contract/evidence.py:303` (field); `src/agents/analysts/news/joiner.py:103`, `src/agents/analysts/fundamental/joiner.py:114`, `src/agents/analysts/_common.py:170`, `src/contract/digest.py:81`, `src/agents/contract/evidence_writer.py:101`, `src/orchestrator/persistence.py:279-331` (column). | `tests/unit/contract/test_evidence.py::test_evidence_feature_warnings_default_empty` (assertion needs updating to a positive-population test, not just "empty by default"). |
| **A-054** Insider extractor legacy vs flat-list paths | Retire legacy `Form4Bundle` branch if no live producer. | `src/contract/extractors/fundamental.py:344-405,481-577,672-689` (the if/else gate at 672 plus the legacy implementation at 481-572). | None deferred — Plan 11 didn't have a matching cementing test. Add a new test asserting the extractor raises when handed the legacy `insider:` key (assuming legacy is retired). |
| **A-055** `TickerEvidence.last_price` `None` vs `0.0` sentinels | Refactor to `PositiveFloat \| None`. | `src/contract/ticker_evidence.py:50-63` (field); producers `src/contract/digest.py:264,311`, `src/agents/strategist/context_shim.py:268-285`, consumer `src/contract/strategist_prompt.py:659-660`. | None named explicitly by Plan 11, but `tests/unit/contract/test_strategist_prompt_layout.py` exercises the renderer's `None`/`0.0` split — add a `pytest.raises(ValidationError)` for the `0.0` case. |

### Adjacent findings considered and excluded

- **A-049** `AnalystVerdict.rationale` / `report.summary` overlap — explicitly owned by Plan 02. Plan 13 builds on the post-Plan-02 schema; do not re-touch.
- **A-050** `digest._fill_missing` silent neutral-fill — owned by Plan 12 (§4). Adjacent to A-053 (both silent-degradation) but the digest fill is a distinct concern (synthesising a missing-slot verdict, not warning emission).
- **A-052** Invariants-doc carve-out test — owned by Plan 11 Block B (delete-or-rewrite). Not a src-side schema concern.
- **A-082** Seven dormant data schemas — adjacent (also schema-duplication / dead-code) but the dormancy criterion is "zero producers AND zero consumers in `src/data/models/`" and Plan 12 §4.4 already routes it. Excluded to avoid re-scoping.

### Finding-numbering sanity check

FINDINGS.md numbering tops out at **A-097**. The earlier handover prompt mentioned "A-101"; that ID does not exist. Plan 13 only references A-051, A-053, A-054, A-055 — all verified present at lines 361, 374, 380, 386 of FINDINGS.md.

---

## 3. Schema decision section

### Decision 1: `TickerVerdict` ↔ `LlmTickerVerdict` (A-051)

**Survivor:** Both classes survive. They are not redundant — they encode a deliberate split:

- `LlmTickerVerdict` is the **LLM emit-schema**. Its three structural commitments (all-required fields, declaration-order-first-for-structured, no `max_length` on prose) are documented at `src/contract/evidence.py:191-221` as load-bearing against Vertex's constrained-decoder repetition pathology. The 2026-05-25 backtest audit on `post-mem-test-5` is the receipt. Deleting it would re-introduce the dominant failure mode.
- `TickerVerdict` is the **canonical downstream shape**. It carries the optional `rationale` (post-Plan-02), accepts `report: None` for deterministic analysts (Technical, SmartMoney), and is what persistence / decision logger / strategist evidence-view all read.

**What gets consolidated:** the **conversion path**. Both joiners (`news`, `fundamental`) hand-roll `TickerVerdict.model_validate({**raw_v, "ticker": ticker})` at the LLM→canonical boundary. This is the duplicate.

**Deletion targets:** none of the classes. The duplicated inflate logic in both joiners is replaced by a single conversion method on `LlmTickerVerdict`.

**Loud-failure invariant:** the new conversion method raises a `ValueError` (not silently drops fields) when given an `LlmTickerVerdict` whose `report` is `None` and `is_no_data is False` — this is already covered by `AnalystVerdict._report_required_when_data_present` post-conversion, but the method asserts the invariant pre-conversion so the failure site names the LLM, not a generic downstream validator.

### Decision 2: `TickerEvidence.last_price` sentinel (A-055)

**Survivor:** `TickerEvidence.last_price: PositiveFloat | None`.

**Justification:** today the field's docstring tells consumers to "treat `None` and `0.0` as no price" — a two-sentinel split is a silent-failure pattern (`feedback_silent_failures_loud_tests`). One sentinel (`None`) is correct; `0.0` should raise at schema validation so the upstream that fed it (the technical extractor's `last_close=0.0` no-bars case at `context_shim.py:275-277`) is forced to coerce to `None` at its emission site, not downstream.

**Deletion targets:** the second clause of `strategist_prompt.py:659` (`te.last_price > 0`) becomes redundant once `0.0` is impossible at schema level — drop it.

### Decision 3: `feature_warnings` (A-053)

**Survivor — decision branch:** the field's fate depends on whether *any* extractor in `src/contract/extractors/` has a real warning to emit. The audit's `FINDINGS.md:374-378` flags it as "wire emission or delete". Plan 13 runs a survey at Task 8 and picks one of the two paths there; both branches are fully spelled out below. The plan does **not** silently pick a branch — it requires the implementing agent to commit to the survey outcome before proceeding.

**Loud-failure invariant:** if the wiring branch is chosen, `feature_warnings` must never be `[""]` or `["unknown"]` — empty placeholders. Extractors emit one structured string per warning OR an empty list. Task 8 includes an assertion that the suite contains at least one test that exercises a populated `feature_warnings` end-to-end.

### Decision 4: Insider extractor legacy `Form4Bundle` branch (A-054)

**Survivor:** the flat-list path at `src/contract/extractors/fundamental.py:672-686` (Phase 7).

**Deletion targets:** the legacy branch at lines 481-572 (`_extract_insider_features_legacy`) AND the else-branch at 687-689 that dispatches to it. The audit's "investigate (retire legacy if no live producer)" qualifier requires Task 9 to first verify zero live producers feed the legacy `insider:` key (typed `Form4Bundle`) — *grep evidence shows producers do still feed it* (`src/agents/analysts/fundamental/fetch.py:219`, `fetch_agent.py:177`, `report_cache.py:520-536`). Therefore the retirement is **two-step**: migrate producers to emit the flat-list shape, then delete the legacy extractor branch.

**Loud-failure invariant:** post-migration, `extract_fundamental_features` raises `KeyError("insider_trades")` when handed a legacy payload with only `insider:` — the silent fall-back is gone.

---

## 4. File structure

Files created in this plan:

- `src/contract/evidence.py` — gain one method on `LlmTickerVerdict` (existing file).
- `tests/unit/contract/test_llm_to_ticker_inflate.py` — new identity-of-inflate test.
- `tests/unit/contract/test_last_price_sentinel.py` — new `PositiveFloat` raises-on-zero test.
- `tests/unit/contract/test_feature_warnings_wiring.py` — new wiring assertion (one of two branches).
- `tests/unit/contract/test_insider_extractor_no_legacy.py` — new "legacy key raises" test.

Files modified:

- `src/contract/evidence.py` (A-051 method; possibly A-053 if delete branch)
- `src/contract/ticker_evidence.py` (A-055)
- `src/contract/digest.py` (A-055 propagation; A-053 wiring if applicable)
- `src/contract/strategist_prompt.py` (A-055 cleanup)
- `src/contract/extractors/fundamental.py` (A-054)
- `src/agents/strategist/context_shim.py` (A-055 producer coercion)
- `src/agents/analysts/news/joiner.py` (A-051 inflate call; A-053 wiring if applicable)
- `src/agents/analysts/fundamental/joiner.py` (A-051 inflate call; A-053 wiring if applicable)
- `src/agents/analysts/_common.py` (A-053 wiring if applicable)
- `src/agents/analysts/fundamental/fetch.py` / `fetch_agent.py` / `src/agents/analysts/report_cache.py` (A-054 producer migration)
- `src/agents/contract/evidence_writer.py` (A-053 wiring or column removal)
- `src/orchestrator/persistence.py` (A-053 column removal if delete branch)
- Several tests refreshed in-pass.

---

## 5. Ordered changes

Each task ends with a commit. Absolute paths throughout.

### Task 1 — Add `LlmTickerVerdict.to_ticker_verdict` (A-051 survivor method)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/evidence.py`
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_llm_to_ticker_inflate.py` (new)

- [ ] **Step 1: Write the failing test.**

```python
# tests/unit/contract/test_llm_to_ticker_inflate.py
"""Identity-of-inflate: the LLM→canonical conversion lives in one place."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import (
    AnalystReport,
    LlmTickerVerdict,
    ReportDriver,
    TickerVerdict,
)


def _make_llm_verdict(*, is_no_data: bool = False, report=None) -> LlmTickerVerdict:
    """Build a minimal valid LlmTickerVerdict for inflate testing."""

    if report is None:
        report = AnalystReport(
            summary="A short gestalt sentence describing the lean.",
            drivers=[
                ReportDriver(name="catalyst", direction="bull", weight=0.6, body="x"),
                ReportDriver(name="risk",     direction="bear", weight=0.4, body="y"),
            ],
        )
    return LlmTickerVerdict(
        ticker      = "AAPL",
        lean        = "bullish",
        magnitude   = 0.5,
        confidence  = 0.5,
        is_no_data  = is_no_data,
        key_factors = ["x"],
        report      = report,
    )


def test_to_ticker_verdict_returns_canonical_shape():
    """LlmTickerVerdict.to_ticker_verdict yields a TickerVerdict with the same ticker."""

    llm = _make_llm_verdict()
    canonical = llm.to_ticker_verdict()

    assert isinstance(canonical, TickerVerdict)
    assert canonical.ticker     == "AAPL"
    assert canonical.lean       == "bullish"
    assert canonical.rationale  == ""              # downstream default — LLM no longer emits it
    assert canonical.report is not None
    assert canonical.is_no_data is False


def test_to_ticker_verdict_propagates_is_no_data_branch():
    """A no-data LLM emit converts to a TickerVerdict that still carries report=None-or-present per Plan 02."""

    # is_no_data=True still requires report under LlmTickerVerdict (schema-required),
    # but the inflated TickerVerdict honours AnalystVerdict's
    # _report_required_when_data_present validator: report may be present even
    # when is_no_data=True (a one-line "no data" summary).
    llm = _make_llm_verdict(is_no_data=True)
    canonical = llm.to_ticker_verdict()
    assert canonical.is_no_data is True
    assert canonical.report is not None


def test_inflate_does_not_silently_drop_fields():
    """Round-trip via model_dump produces no field drift between LLM and canonical."""

    llm = _make_llm_verdict()
    canonical = llm.to_ticker_verdict()
    dumped = canonical.model_dump()

    # The five LLM-emitted scalar fields must match by value.
    for k in ("ticker", "lean", "magnitude", "confidence", "is_no_data"):
        assert dumped[k] == getattr(llm, k)
    assert dumped["key_factors"] == llm.key_factors
    assert dumped["report"]["summary"] == llm.report.summary
```

- [ ] **Step 2: Run the test, confirm it fails.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_llm_to_ticker_inflate.py -v`
Expected: FAIL with `AttributeError: 'LlmTickerVerdict' object has no attribute 'to_ticker_verdict'`.

- [ ] **Step 3: Implement the method on `LlmTickerVerdict` in `src/contract/evidence.py`.**

Insert after the existing `_ticker_non_empty` validator (around line 266):

```python
    def to_ticker_verdict(self) -> TickerVerdict:
        """Inflate this narrow LLM emit-schema into the canonical TickerVerdict.

        Sole conversion point between the LLM emit-shape and the downstream
        canonical shape — every joiner and consumer goes through this method,
        so the strict-shape boundary is named and singular.

        ``rationale`` defaults to ``""`` on the canonical side: LLM analysts no
        longer emit it (the field's pad-toward-cap pressure was the root cause
        of the 2026-05-25 repetition pathology — see this class's docstring).
        Deterministic analysts populate ``rationale`` directly via
        ``TickerVerdict(rationale=..., ...)`` and never traverse this method.

        Raises
        ------
        ValueError
            If post-conversion the canonical shape would itself be invalid (the
            ``AnalystVerdict._report_required_when_data_present`` validator
            fires) — re-raised so the failure site names the LLM, not a
            downstream consumer.  This is the loud-failure surface that
            replaces the old silent
            ``TickerVerdict.model_validate({**raw_v, "ticker": ticker})``
            pattern duplicated across joiners.
        """

        # ``model_dump`` strips Pydantic's runtime model and emits a plain dict;
        # ``rationale`` is absent (the LLM never emitted it), so the canonical
        # constructor takes the default "" — exactly the downstream contract.
        payload = self.model_dump()
        return TickerVerdict.model_validate(payload)
```

- [ ] **Step 4: Run the test, confirm it passes.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_llm_to_ticker_inflate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/contract/evidence.py tests/unit/contract/test_llm_to_ticker_inflate.py
git commit -m "$(cat <<'EOF'
feat(contract): single LLM→canonical inflate path via LlmTickerVerdict.to_ticker_verdict (A-051)

Sole conversion site between the load-bearing LLM emit-schema
(LlmTickerVerdict — required fields, declaration order, no max_length)
and the canonical downstream shape (TickerVerdict — rationale default,
report optional for deterministic analysts).  Replaces the duplicated
TickerVerdict.model_validate({**raw_v, "ticker": ticker}) pattern in
the news and fundamental joiners (migrated in the next task).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 2 — Migrate joiners to use the new method (A-051 consumer migration)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/news/joiner.py:60-95`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/fundamental/joiner.py:60-95`

- [ ] **Step 1: Update the news joiner.**

Replace the `else` branch at `src/agents/analysts/news/joiner.py:77-84` with:

```python
            else:
                # Validate against the strict LLM emit-schema first (re-validates
                # what ADK's output_schema already enforced on write, so downstream
                # consumers can rely on the shape unconditionally), then inflate
                # via the sole canonical-conversion method.  Raises loudly if the
                # post-conversion canonical shape is invalid.
                llm_v          = LlmTickerVerdict.model_validate({**raw_v, "ticker": ticker})
                ticker_verdict = llm_v.to_ticker_verdict()
                verdict        = AnalystVerdict.model_validate(
                    {k: v for k, v in ticker_verdict.model_dump().items() if k != "ticker"}
                )
```

Update the import at line 23 from:

```python
from contract.evidence import AnalystEvidence, AnalystVerdict, TickerVerdict, VerdictBatch
```

to:

```python
from contract.evidence import (
    AnalystEvidence,
    AnalystVerdict,
    LlmTickerVerdict,
    TickerVerdict,
    VerdictBatch,
)
```

- [ ] **Step 2: Apply the identical change to `src/agents/analysts/fundamental/joiner.py`.**

Same `else` body shape (lines 88-95 in the current file); same import update at line 26.

- [ ] **Step 3: Run the relevant test suites.**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/ tests/unit/contract/ -v`
Expected: green. No new skips.

- [ ] **Step 4: Run the full contract + analyst integration tests.**

Run: `.venv/bin/python -m pytest tests/integration/test_analyst_pool.py tests/integration/test_pipeline_composition.py -v`
Expected: green.

- [ ] **Step 5: Commit.**

```bash
git add src/agents/analysts/news/joiner.py src/agents/analysts/fundamental/joiner.py
git commit -m "$(cat <<'EOF'
refactor(analysts): joiners use LlmTickerVerdict.to_ticker_verdict (A-051)

Both joiners now validate the raw LLM payload against LlmTickerVerdict
first, then inflate through the canonical conversion method.  Loud
failure surface: any LLM verdict that would have been silently coerced
by the old TickerVerdict.model_validate({**raw_v, "ticker": ticker})
path now raises with a message that names the LLM emit-schema as the
violating shape.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 3 — Add `PositiveFloat` constraint to `TickerEvidence.last_price` (A-055 survivor)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/ticker_evidence.py:45-63`
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_last_price_sentinel.py` (new)

- [ ] **Step 1: Write the failing test.**

```python
# tests/unit/contract/test_last_price_sentinel.py
"""One sentinel for last_price: None means absent, every concrete value is positive."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _make_evidence(*, last_price):
    """Build a minimal TickerEvidence with the given last_price under test."""

    return TickerEvidence(
        ticker      = "AAPL",
        tick_id     = "T-1",
        recorded_at = datetime(2026, 1, 1, tzinfo=UTC),
        per_analyst = {},
        aggregate   = AggregateVerdict(
            lean         = "neutral",
            magnitude    = 0.0,
            confidence   = 0.0,
            disagreement = 0.0,
            summary      = "0/0",
        ),
        weights     = {},
        last_price  = last_price,
    )


def test_last_price_none_is_accepted():
    """None remains the canonical 'no price available' sentinel."""

    ev = _make_evidence(last_price=None)
    assert ev.last_price is None


def test_last_price_positive_float_is_accepted():
    """Any positive float passes."""

    ev = _make_evidence(last_price=123.45)
    assert ev.last_price == 123.45


def test_last_price_zero_raises():
    """0.0 is no longer a silent 'no price' sentinel — must be coerced to None upstream."""

    with pytest.raises(ValidationError):
        _make_evidence(last_price=0.0)


def test_last_price_negative_raises():
    """Negative prices have never been valid — assert the constraint is live."""

    with pytest.raises(ValidationError):
        _make_evidence(last_price=-1.0)
```

- [ ] **Step 2: Run the test, confirm `test_last_price_zero_raises` and `test_last_price_negative_raises` fail.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_last_price_sentinel.py -v`
Expected: 2 PASS (None, positive), 2 FAIL (zero and negative are currently accepted).

- [ ] **Step 3: Tighten the field in `src/contract/ticker_evidence.py`.**

Change the import block (line 20) from:

```python
from pydantic import BaseModel, Field
```

to:

```python
from pydantic import BaseModel, Field, PositiveFloat
```

Replace lines 45-63 (the `TickerEvidence` class) with:

```python
class TickerEvidence(BaseModel):
    """One row of evidence the strategist sees for a ticker on a tick.

    ``last_price`` carries the live close at evidence-build time so the
    strategist's per-ticker renderer can show "where the ticker is trading
    right now" in the section header — see ``contract.strategist_prompt``.

    ``None`` is the sole "no price" sentinel.  Any zero or negative value
    raises at schema validation: the upstream that fed it (typically the
    technical extractor's ``last_close=0.0`` no-bars case) must coerce to
    ``None`` at its emission site so the absence is loud, not silent.
    """

    ticker:      str
    tick_id:     str
    recorded_at: datetime
    per_analyst: dict[str, AnalystEvidence]
    aggregate:   AggregateVerdict
    weights:     dict[str, float]
    last_price:  PositiveFloat | None = None
```

- [ ] **Step 4: Run the test, confirm all four pass.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_last_price_sentinel.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/contract/ticker_evidence.py tests/unit/contract/test_last_price_sentinel.py
git commit -m "$(cat <<'EOF'
refactor(contract): TickerEvidence.last_price is PositiveFloat | None (A-055)

Removes the two-sentinel pattern (None OR 0.0 means "no price") — None
is now the sole "absent" sentinel, and 0.0 raises at schema validation.
Upstream producers must coerce no-data to None at the emission site
(see Task 4 for the context_shim coercion fix); failures are loud at
the boundary, not silently rendered as "$0.00" downstream.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 4 — Fix producers to honour the new `last_price` constraint (A-055 consumer migration)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/strategist/context_shim.py:268-285`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/strategist_prompt.py:659-660`

- [ ] **Step 1: Run the full suite to find producers that now raise.**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: failures localised to producers that pass `0.0` (most likely the technical-extractor fallback path in `context_shim.py`).

- [ ] **Step 2: Confirm `context_shim.py:268-277` already coerces correctly.**

Read `src/agents/strategist/context_shim.py:264-286`. The existing logic already gates on `held.last_price > 0` and `float(raw_lc) > 0`, so `last_price` should remain `None` when neither source is positive. No change expected here, but **explicitly verify** by re-reading the block — if any branch can leak a `0.0` to `build_ticker_evidence`, fix it to leave `last_price` at `None`.

- [ ] **Step 3: Simplify the renderer in `src/contract/strategist_prompt.py:659-660`.**

Replace:

```python
    if te.last_price is not None and te.last_price > 0:
        parts.append(f"=== {te.ticker}  ${te.last_price:,.2f} ===")
```

with:

```python
    # last_price is PositiveFloat | None (see contract.ticker_evidence) — the
    # `> 0` clause is now redundant; the schema guarantees positivity when set.
    if te.last_price is not None:
        parts.append(f"=== {te.ticker}  ${te.last_price:,.2f} ===")
```

- [ ] **Step 4: Re-run the suite.**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green.

- [ ] **Step 5: Commit.**

```bash
git add src/contract/strategist_prompt.py src/agents/strategist/context_shim.py
git commit -m "$(cat <<'EOF'
refactor(strategist): drop redundant last_price>0 guard in prompt renderer (A-055)

PositiveFloat | None on TickerEvidence.last_price means the renderer's
defensive `> 0` clause can no longer fire — schema is the source of
truth.  context_shim's existing producer-side gating already coerces
0.0 → None at emission so no upstream change is required.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 5 — Survey: does any extractor have a real `feature_warnings` to emit? (A-053 decision gate)

**Files:** none modified in this task — survey only.

- [ ] **Step 1: List every file under `src/contract/extractors/` and read each for failure modes the extractor catches and swallows.**

Run: `ls src/contract/extractors/ && grep -rn "except\|nan\|isnan\|missing\|fallback\|default" src/contract/extractors/`

- [ ] **Step 2: For each silent-recovery branch, record the answer to: "does the consumer have any way today to know this branch fired?"**

Write the findings into the commit message of the next task. Two outcomes are possible:

- **Branch A — at least one extractor has a real warning to emit:** proceed to Task 6 (wire emission). Skip Task 7.
- **Branch B — no extractor has any warning to emit:** the field is genuinely dead. Skip Task 6 and proceed to Task 7 (delete field + column).

- [ ] **Step 3: Decision recorded.**

State the chosen branch explicitly in the next commit message. Do not start Task 6 or Task 7 until the decision is on paper.

### Task 6 — Wire `feature_warnings` emission (A-053 — Branch A only)

**Skip this task if Task 5 chose Branch B.**

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/extractors/<file>.py` (one or more, per Task 5's survey)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/news/joiner.py:103`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/fundamental/joiner.py:114`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/_common.py:170`
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_feature_warnings_wiring.py` (new)

- [ ] **Step 1: Refactor extractor signature.**

Pick one of:

- **Option (a) — co-return:** change the extractor to return `tuple[dict[str, float], list[str]]` and update callers.
- **Option (b) — out-param:** change the extractor to accept `warnings: list[str]` and append in-place; callers pass an empty list at the call site.

Recommend (a) — pure return matches the existing codebase idiom and avoids the alias-mutation footgun.

- [ ] **Step 2: Joiners pass the returned warnings into `AnalystEvidence(feature_warnings=...)` instead of the current hard-coded `[]`.**

For each of news/joiner.py:103, fundamental/joiner.py:114, _common.py:170 — replace `feature_warnings = []` with the returned list.

- [ ] **Step 3: Write the failing positive-population test.**

```python
# tests/unit/contract/test_feature_warnings_wiring.py
"""feature_warnings must carry at least one structured string when the
extractor has detected a degraded input — empty list only when the input
was clean."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

# Replace this import with whichever extractor Task 5 identified as having
# a genuine warning source.  The test is a positive identity assertion that
# the wiring carries the string end-to-end into AnalystEvidence.
from contract.extractors.fundamental import extract_fundamental_features
from contract.evidence import AnalystEvidence, AnalystVerdict


def test_extractor_emits_warning_on_known_degraded_input():
    """Hand the extractor a payload with the specific degraded shape Task 5
    found and assert at least one warning is emitted."""

    # Construct the degraded input shape recorded in Task 5's survey.
    raw = {...}  # filled in from Task 5's findings

    features, warnings = extract_fundamental_features(raw, "AAPL")
    assert warnings, "extractor must emit at least one warning for this input"
    assert all(isinstance(w, str) and w for w in warnings), \
        "warnings must be non-empty strings — no '' or None placeholders"
```

If Task 5 yielded no "known degraded shape" the test cannot be written — that itself is evidence to use Branch B, not Branch A. Stop and re-do Task 5's decision.

- [ ] **Step 4: Run, watch fail, implement extractor changes, watch pass.**

- [ ] **Step 5: Commit.**

```bash
git add src/contract/extractors/ src/agents/analysts/news/joiner.py src/agents/analysts/fundamental/joiner.py src/agents/analysts/_common.py tests/unit/contract/test_feature_warnings_wiring.py
git commit -m "$(cat <<'EOF'
feat(contract): wire feature_warnings emission in extractors (A-053)

Survey (Task 5) found <N> extractor branches that today silently
recover from degraded inputs.  Each now returns a structured warning
string alongside its features dict; the news + fundamental joiners
propagate the list into AnalystEvidence.feature_warnings instead of
the hard-coded [] placeholder.  Downstream consumers can now
distinguish "extractor returned 0.0 because the input was missing"
from "extractor returned a real 0.0".

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 7 — Delete `feature_warnings` field + column (A-053 — Branch B only)

**Skip this task if Task 5 chose Branch A.**

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/evidence.py:303`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/digest.py:81`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/news/joiner.py:103`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/fundamental/joiner.py:114`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/_common.py:170`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/contract/evidence_writer.py:101`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/persistence.py:279,290,306,331`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_evidence.py` (drop the now-dead default-empty test)
- Modify: every other test that passes `feature_warnings=[]` (see Task 5 grep output)

- [ ] **Step 1: Delete the field from `AnalystEvidence`.**

Drop line 303 (`feature_warnings: list[str] = Field(default_factory=list)`).

- [ ] **Step 2: Drop every `feature_warnings=[]` constructor argument.**

For each file in the modify list above (and any test caught by grep), remove the kwarg. Use:

```bash
grep -rln "feature_warnings" tests/ src/
```

to enumerate sites; edit each by hand (no `sed` — Edit tool preferred per project policy).

- [ ] **Step 3: Drop the SQLite column.**

`src/orchestrator/persistence.py:279` — delete the `feature_warnings_json` column. The function signature at line 290 loses the `feature_warnings: list[str]` parameter. The writer at line 331 loses the `feature_warnings_json=...` assignment.

**Schema note:** since the project is pre-deployment (no live DB to migrate, per `project_stockbot_deployment_state`), no Alembic migration is required. Existing test DBs are rebuilt from `Base.metadata.create_all` on every run.

- [ ] **Step 4: Run the suite.**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green.

- [ ] **Step 5: Commit.**

```bash
git add src/contract/evidence.py src/contract/digest.py src/agents/analysts/ src/agents/contract/evidence_writer.py src/orchestrator/persistence.py tests/
git commit -m "$(cat <<'EOF'
chore(contract): delete unused feature_warnings field + column (A-053)

Task 5's survey found zero extractor branches that have a real warning
to emit — every silent recovery in src/contract/extractors/ already
raises (post-Plan-05) or has no failure mode worth surfacing.  The
field has been dead since introduction; remove it from AnalystEvidence,
the persistence column, the evidence_writer, and every fixture that
hard-coded `feature_warnings=[]`.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 8 — Migrate insider producers off the typed `Form4Bundle` payload key (A-054 step 1/2)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/fundamental/fetch.py:198-235`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/fundamental/fetch_agent.py:140-180`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/report_cache.py:330-360,519-540`

- [ ] **Step 1: Confirm the producers.**

Run: `grep -rn "\"insider\":" src/`
Expected: three call sites (`fetch.py`, `fetch_agent.py`, `report_cache.py`) emit a payload with the typed `insider: Form4Bundle` key. The downstream extractor branches on `if "insider_trades" in raw` (the flat-list path) and falls back to the typed path otherwise.

- [ ] **Step 2: Change each producer to emit BOTH the flat-list shape AND drop the typed key.**

For each producer, replace the `"insider": insider_bundle` line in the returned dict with:

```python
                # Flat-list shape — Phase 7 extractor path.  The typed
                # Form4Bundle is dumped to two flat lists (common + derivative)
                # so the contract extractor sees the same shape regardless of
                # provider (Phase 7 unified emission).
                "insider_trades":            [t.model_dump() for t in insider_bundle.trades],
                "insider_derivative_trades": [d.model_dump() for d in insider_bundle.derivatives],
```

Verify the bundle is `Form4Bundle(trades=[], derivatives=[])` when fetch fails, so the two lists are always present (possibly empty).

- [ ] **Step 3: Run the extractor + analyst tests.**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/ tests/unit/agents/analysts/fundamental/ tests/integration/test_analyst_pool.py -v`
Expected: green — the extractor's flat-list path now fires for every call.

- [ ] **Step 4: Commit.**

```bash
git add src/agents/analysts/fundamental/fetch.py src/agents/analysts/fundamental/fetch_agent.py src/agents/analysts/report_cache.py
git commit -m "$(cat <<'EOF'
refactor(fundamental): producers emit insider_trades flat-list shape (A-054 step 1/2)

Every producer that previously emitted "insider: Form4Bundle" now emits
the two-list "insider_trades" + "insider_derivative_trades" shape
expected by the Phase 7 extractor path.  The typed key is dropped to
force the next task (step 2/2) to delete the legacy extractor branch
without a silent fall-through.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 9 — Delete the legacy `Form4Bundle` extractor branch (A-054 step 2/2)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/extractors/fundamental.py:481-572,687-689`
- Test:   `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_insider_extractor_no_legacy.py` (new)

- [ ] **Step 1: Write the failing "legacy raises" test.**

```python
# tests/unit/contract/test_insider_extractor_no_legacy.py
"""The legacy 'insider: Form4Bundle' payload path is retired — handing
the extractor that shape now raises rather than silently degrading."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contract.extractors.fundamental import extract_fundamental_features
from data.models import Form4Bundle


def test_legacy_insider_key_raises():
    """Payload with only the typed 'insider' key (no 'insider_trades') raises."""

    raw = {
        "ratios":  {},
        "filings": [],
        "insider": Form4Bundle(trades=[], derivatives=[]),
    }

    with pytest.raises(KeyError):
        extract_fundamental_features(raw, "AAPL")


def test_flat_list_shape_still_works():
    """Phase 7 flat-list shape continues to extract cleanly."""

    raw = {
        "ratios":                   {},
        "filings":                  [],
        "insider_trades":           [],
        "insider_derivative_trades": [],
    }

    features = extract_fundamental_features(raw, "AAPL")
    assert features["insider_n_buys_30d"]  == 0.0
    assert features["insider_n_sells_30d"] == 0.0
```

- [ ] **Step 2: Confirm the first test fails (legacy currently falls back silently) and the second passes.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_insider_extractor_no_legacy.py -v`
Expected: 1 FAIL, 1 PASS.

- [ ] **Step 3: Delete the legacy branch.**

In `src/contract/extractors/fundamental.py`:

- Delete the `else: insider_sub = raw.get("insider"); out.update(_extract_insider_features_legacy(insider_sub, now))` branch at lines 687-689.
- Delete `_extract_insider_features_legacy` (lines 481-572).
- Drop the now-unused `importlib`, `_f` import if they were used only by the legacy branch (re-grep within the file to check).

After deletion, the insider gate becomes an unconditional flat-list read with a `KeyError` raise if the key is absent:

```python
    # --- insider trades (Phase 7 flat-list path — sole supported shape) ---
    if "insider_trades" not in raw:
        raise KeyError(
            "insider_trades missing from fundamental payload — every producer "
            "must emit the Phase 7 flat-list shape "
            "(insider_trades + insider_derivative_trades); the legacy "
            "'insider: Form4Bundle' key was retired in Plan 13 (A-054)."
        )

    trades_flat   = raw.get("insider_trades") or []
    derivs_flat   = raw.get("insider_derivative_trades") or []
    last_price_for_derivs = _f((stats_sub or {}).get("last_price"))

    out.update(_insider_aggregates_from_flat(trades_flat, as_of_date))
    out.update(_derivative_aggregates(derivs_flat, last_price_for_derivs, as_of_date))

    # Legacy derivative counts from the flat deriv list (for _KEYS back-compat).
    out["insider_derivative_exercise_count"] = float(
        sum(1 for d in derivs_flat if (d.get("transaction_code") or "") == "M")
    )
    out["insider_derivative_grant_count"] = float(
        sum(1 for d in derivs_flat if (d.get("transaction_code") or "") == "A")
    )

    return out
```

- [ ] **Step 4: Re-run the test plus full suite.**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_insider_extractor_no_legacy.py tests/ -q`
Expected: green.

- [ ] **Step 5: Commit.**

```bash
git add src/contract/extractors/fundamental.py tests/unit/contract/test_insider_extractor_no_legacy.py
git commit -m "$(cat <<'EOF'
refactor(contract): delete legacy Form4Bundle insider extractor branch (A-054)

Step 2/2.  Producers were migrated to the flat-list payload shape in
the previous commit; the extractor's typed-bundle fallback at
fundamental.py:687-689 and the 90-LoC _extract_insider_features_legacy
helper at 481-572 are now dead.  Removed both.  A missing
'insider_trades' key now raises KeyError — silent fall-back is gone.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 10 — Pick up Plan 11 Block E echo for A-051

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_llm_ticker_verdict.py`

Plan 11's §1.1 noted `tests/contract/test_llm_ticker_verdict.py` overlaps A-051 and was relocated to `tests/unit/contract/`. Confirm the relocated file still asserts (a) the LLM-emit schema rejects optional `report`, (b) `extra="forbid"` is honoured, and (c) `to_ticker_verdict` is the only inflate path (cross-link to the Task 1 test).

- [ ] **Step 1: Re-read the relocated file.**

Run: `cat tests/unit/contract/test_llm_ticker_verdict.py 2>/dev/null || echo "NOT YET RELOCATED — Plan 11 not landed"`

If absent, abort and request Plan 11 land first.

- [ ] **Step 2: Append (or update) a `test_to_ticker_verdict_is_sole_inflate_site` assertion.**

```python
def test_to_ticker_verdict_is_sole_inflate_site():
    """Grep guard: no callsite outside contract/evidence.py inflates LlmTickerVerdict
    via TickerVerdict.model_validate({..., 'ticker': ticker})."""

    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3] / "src"
    offenders: list[str] = []
    pattern = re.compile(r"TickerVerdict\.model_validate\s*\(\s*\{\s*\*\*")
    for py in root.rglob("*.py"):
        if py.name == "evidence.py" and py.parent.name == "contract":
            continue                            # the single allowed site
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(py.relative_to(root.parent)))

    assert not offenders, (
        "Inflate-path regression: callers below should use "
        "LlmTickerVerdict.to_ticker_verdict(), not the raw model_validate "
        f"pattern. Offenders: {offenders}"
    )
```

- [ ] **Step 3: Run, confirm green (Task 2 already migrated both joiners).**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_llm_ticker_verdict.py -v`
Expected: green.

- [ ] **Step 4: Commit.**

```bash
git add tests/unit/contract/test_llm_ticker_verdict.py
git commit -m "$(cat <<'EOF'
test(contract): grep-guard that to_ticker_verdict is the sole inflate site (A-051, Plan 11 Block E)

Picks up the Block E hand-off Plan 11 routed to this plan: assert no
other file in src/ uses the raw `TickerVerdict.model_validate({**raw_v,
'ticker': ticker})` pattern.  Fails loudly the first time a future
contributor re-introduces an inline inflate path.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Task 11 — Full-suite gate + cleanup

- [ ] **Step 1: Run the full suite.**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green. Skip count must not have risen from the post-Plan-12 baseline.

- [ ] **Step 2: Re-run ruff.**

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: clean.

---

## 6. Test strategy

The plan asserts three flavours of test invariant:

1. **Identity (one canonical site).**
   - `test_to_ticker_verdict_returns_canonical_shape` — only one method does LLM→canonical inflate.
   - `test_to_ticker_verdict_is_sole_inflate_site` (Plan 11 Block E echo) — grep guard against future regressions.

2. **Loud-failure (silent coercion now raises).**
   - `test_last_price_zero_raises` and `test_last_price_negative_raises` — the two-sentinel `last_price` pattern is dead.
   - `test_legacy_insider_key_raises` — the legacy `Form4Bundle` payload now raises `KeyError`.
   - `test_inflate_does_not_silently_drop_fields` — the new conversion method round-trips losslessly.
   - (Branch A only) `test_extractor_emits_warning_on_known_degraded_input` — `feature_warnings` carries real strings, not `[""]`.

3. **Plan 11 Block E hand-offs landed.**
   - Plan 11's §5 explicitly defers A-051's src-side schema-collapse to **this** plan. The relocated `tests/unit/contract/test_llm_ticker_verdict.py` plus the new `test_llm_to_ticker_inflate.py` cover the test-side echo.
   - Plan 11 does not list a hand-off for A-053, A-054, A-055 by name — they fell entirely outside Plan 11's scope per `feedback_test_audit_scope_tests_only`. Tasks 5–9 introduce both the src-side fix and its first-class test in the same patch.

The plan does NOT add cementing tests for the dead behaviour — under `feedback_silent_failures_loud_tests` the correct shape is a `pytest.raises(...)` at the boundary, which is what Tasks 3, 6/7, and 9 deliver.

---

## 7. Risks and silent-regression checklist

| Risk | Surface | Mitigation |
|---|---|---|
| **Persistence layer still serialises the old `TickerVerdict` shape from a DB row.** | `src/orchestrator/persistence.py`, `src/agents/contract/evidence_writer.py`, decision_logger JSON. | Task 1 changes no field on `TickerVerdict` — only adds a method on `LlmTickerVerdict`. The persisted JSON shape is unchanged. |
| **ADK `output_schema` re-validates `raw_v` against `TickerVerdict` somewhere we missed.** | `src/agents/analysts/cache_callbacks.py:14,27,51,59,106,125,136,361`. | The cache writes `TickerVerdict` dicts but the cache is unaware of the inflate path — it stores whatever the LLM produced (now an `LlmTickerVerdict`-shaped dict). Verify in Task 2: the new joiner `model_validate` against `LlmTickerVerdict` must accept a cache-hit payload. If the cache writes the old `TickerVerdict` shape, Task 2 will fail loudly (extra fields rejected via `extra="forbid"`). Resolution: drain the cache or add a one-shot migration in the cache reader. **Mark as a follow-up in the Task 2 PR if discovered.** |
| **Decision logger snapshots include `last_price: 0.0` historically.** | `src/backtest/decision_logger.py`. | Pre-deployment — no historical snapshots in production. Test fixtures (Task 4 sweep) catch any in-tree producer. |
| **Branch A's wiring leaves the field semi-populated** (some extractors emit, some hard-code `[]`). | All three joiners + `_common.py`. | Task 6's test must exercise at least one wired path AND assert the unwired paths still emit `[]` correctly. If the survey finds N wirable extractors, exercise N. |
| **Insider producer migration drops the typed `Form4Bundle` from places that still read it directly (not via the extractor).** | `src/agents/analysts/report_cache.py:536` reads `triad.get("insider")`. | Task 8's `grep "\"insider\":"` must catch every read site, not just write sites. Add a `grep "\.get(\"insider\")"` pass before deleting and follow up each hit. |
| **`PositiveFloat` rejects `inf` / `nan`.** | Strategist prompt rendering of a degenerate price feed. | Acceptable — `inf` / `nan` should never reach the schema; if they do, raising is the right behaviour (loud failure). |
| **A-053 Branch B deletes a column that an Alembic migration would need.** | `src/orchestrator/persistence.py:279`. | Pre-deployment (`project_stockbot_deployment_state`): no live DB, no migration story required. Test DBs are recreated from `create_all`. |

---

## 8. Definition of done

- [ ] `LlmTickerVerdict.to_ticker_verdict()` exists and is the sole LLM→canonical inflate site (`grep` guard test green).
- [ ] Both joiners (`news`, `fundamental`) use the new method; no other file in `src/` matches `TickerVerdict.model_validate({**raw_v, "ticker": ticker})`.
- [ ] `TickerEvidence.last_price` is `PositiveFloat | None`; `0.0` and negatives raise `ValidationError`; the renderer's `> 0` guard is gone.
- [ ] `AnalystEvidence.feature_warnings` is either (Branch A) wired to extractor emissions with at least one positive-population test green, OR (Branch B) deleted everywhere — field, persisted column, evidence_writer plumbing, every test fixture.
- [ ] `_extract_insider_features_legacy` is removed; every producer emits the flat-list `insider_trades` / `insider_derivative_trades` shape; the extractor raises `KeyError` on the legacy `insider:` key.
- [ ] Full `pytest tests/ -q` is green.
- [ ] Full `ruff check src/ tests/` is clean.
- [ ] PR description cross-references Plan 11's Block E hand-off table and ticks off the A-051 entry.

---

## Self-review notes

- **Spec coverage:** A-051 → Tasks 1, 2, 10. A-053 → Tasks 5 (decision), 6 or 7 (execution). A-054 → Tasks 8, 9. A-055 → Tasks 3, 4. A-049 explicitly excluded (Plan 02 owns it). No findings dropped.
- **Placeholder scan:** the only "TBD"-flavoured content is the Task 6 test body's `raw = {...}` — this is intentional and gated on Task 5's survey result, which the implementing agent must fill in. The plan refuses to silently invent a degraded shape that may not exist; the alternative (Branch B) is fully specified.
- **Type consistency:** `LlmTickerVerdict.to_ticker_verdict() -> TickerVerdict` matches the import in the migrated joiners; `PositiveFloat | None` is consistent across the field declaration, the test assertions, and the renderer comment.
- **Trust-contract sanity:** the plan only consumes work owned by Plans 02, 05, 07, 11, 12 — it does not assume any later plan will land (there is none).
- **Loud-failure invariants:** every consolidation site has a corresponding `pytest.raises(...)` test. Per `feedback_silent_failures_loud_tests`, no consolidation is allowed to silently coerce the formerly-two shapes into one.
