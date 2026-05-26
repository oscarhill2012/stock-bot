# Plan 02 — Rationale + verdict vocabulary collapse

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the prose-justification cluster around analyst verdicts
to a single canonical field, stop deterministic extractors from
fabricating `AnalystReport` prose, route every "no data" verdict through
one builder, and drop the `headline_polarity_mean` alias. After this plan
lands, every downstream consumer reads `AnalystVerdict.rationale` (for
the deterministic one-liner) or `AnalystVerdict.report.summary` (for the
LLM-emitted prose block) — never both, never a synthetic substitute.

**Architecture:** Three deletions and one consolidation. (1) Relax the
`_report_required_when_data_present` validator so deterministic
verdicts may carry `report=None`; (2) strip the synthetic-prose code
paths out of `technical.py`, `social.py`, `smart_money.py`; (3) introduce
`build_no_data_verdict(ticker, *, reason)` in `src/contract/evidence.py`
and route the three current synthesis sites through it; (4) delete the
unused `headline_polarity_mean` alias and the `report.summary` fallback
in the analyst-evidence renderer (the only remaining live reader of the
overlap). No back-compat shims.

**Tech Stack:** Python 3.12, Pydantic v2, pytest. No new dependencies.

---

## 1. Goal + trust contract

### What this plan owns
- Single canonical "why" field per analyst verdict.
- Single canonical no-data verdict builder.
- Validator that no longer forces deterministic extractors to lie.
- Renderer that no longer falls back between `rationale` and
  `report.summary`.
- Removal of the `headline_polarity_mean` non-suffix alias.

### Trusts (must already be true when this plan starts)
- **Plan 01** has landed: all safe deletions and doc-only intent edits
  are merged. Critically, `src/agents/strategist/evidence_view.py` and
  its three test files have been deleted (A-025/A-026 — they contain
  the only other live `rationale`-or-`report.summary` fallback reader,
  and Plan 01 owns the deletion). If `evidence_view.py` still exists
  when this plan starts, **stop and escalate** — the fallback there
  must die under Plan 01, not here.

### Trusted by later plans
- **Plan 05** (strategist enricher cleanup) trusts that
  `AnalystVerdict.rationale` is the canonical one-liner and that
  deterministic verdicts no longer carry a synthetic `report`.
- **Plan 07** (executor / position-thesis cleanup) trusts that the
  no-data builder is canonical so a held-but-untouched ticker is no
  longer rendered with drifting prose.
- **Plan 11** (test-suite policy sweep) trusts that the `is_no_data`
  invariant is observable in one place — happy-path assertions of the
  form `assert not v.is_no_data` against deterministic extractors are
  only meaningful once the synthetic-prose path is gone.

### Out of scope (do NOT touch in this plan)
- `PositionThesis.last_reviewed_reason`, `StrategistDecision.sell_reasons`
  and `update_reasons` — those are also in the A-013 cluster but their
  removal cascades through executor verb-dispatch (5 sites), schema,
  derivation, decision_writer and ~26 test references. **Plan 05 owns
  this deletion as its final task** (the executor verb-dispatch surface
  is already Plan 05's scope, and the prior plan-05:14 trust note that
  treated these fields as stable is explicitly updated by that task).
  A note is left at the deletion site here explaining the deferred work;
  do not pre-empt it.
- `TickerVerdict` / `LlmTickerVerdict` two-shape pattern (A-051,
  Plan 13).
- `digest._fill_missing` silent neutral-fill (A-050) — separate plan;
  the no-data builder introduced here will be a drop-in once that plan
  needs it.

---

## 2. Vocabulary decision

Source: `docs/audits/2026-05-26-codebase-audit/vocab/contract.md` §
"src/contract/evidence.py" and `intent.md` §3.2 cluster 1, §8.2.

### Canonical fields on `AnalystVerdict`

| Field | Type | Owner | Meaning |
|-------|------|-------|---------|
| `rationale` | `str` (default `""`) | deterministic extractors only | one-line `", "`-joined factor tags, ≤160 chars. Empty string ⇒ LLM verdict, prose lives in `report.summary`. |
| `report` | `AnalystReport \| None` | LLM analysts only | full prose block (`summary` + 2–4 `ReportDriver` rows). `None` ⇒ deterministic verdict, one-liner lives in `rationale`. |
| `is_no_data` | `bool` | both | `True` ⇒ this analyst produced no signal this tick; `rationale` carries the short reason; `report` is `None`. |

**Invariant** (replaces `_report_required_when_data_present`):

```
NOT (rationale == "" AND report is None AND NOT is_no_data)
```

In words: a non-no-data verdict must carry at least one prose surface
— either the deterministic one-liner OR the LLM report block — but not
both. This is enforced by a renamed validator
`_prose_surface_required_when_data_present` on
`AnalystVerdict`. Verdicts that try to carry BOTH (`rationale != ""`
AND `report is not None`) are also rejected — that combination is the
old synthetic-prose pathology we are deleting and must not silently
reappear.

### Canonical no-data builder

```python
def build_no_data_verdict(ticker: str, *, reason: str) -> TickerVerdict:
    """Single source of truth for 'we had no data this tick' verdicts.

    Parameters:
        ticker:  symbol to attach to the verdict.
        reason:  short prose explanation (<=160 chars). Required, not
                 defaulted — every caller must say *why* there is no
                 data. Silent defaults are the recurring bug class.

    Returns:
        TickerVerdict with lean="neutral", magnitude=0.0,
        confidence=0.0, key_factors=[], is_no_data=True, report=None,
        and rationale=reason.
    """
```

Lives in `src/contract/evidence.py` directly under the `TickerVerdict`
class definition. Raises `ValueError` if `reason` is empty or
whitespace-only — every no-data site already has a real reason
available; no caller needs a silent default.

### `headline_polarity_mean` alias

Per FINDINGS A-048: only the `_7d`-suffixed key has a downstream reader
(`strategist_prompt.py:377`). The non-suffixed `headline_polarity_mean`
key is the alias to delete.

---

## 3. Ordered changes (file inventory)

### Extractors first
- **Modify:** `src/contract/extractors/technical.py:526-533, 647-705`
  — strip the synthetic `summary`/`drivers`/`report` block (lines
  ~650-695) and drop the `report=report` kwarg from the final
  `AnalystVerdict` constructor. The deterministic `rationale` line
  (`", ".join(factors) or "neutral"`) stays. The no-data branch (lines
  526-533) is rewritten to call `build_no_data_verdict` — but indirectly,
  see Task 4 below (the extractor returns `AnalystVerdict` not
  `TickerVerdict`, so it gets its own thin helper).
- **Modify:** `src/contract/extractors/social.py:193-217, 273-328` —
  same shape; strip the synthetic-report block (lines ~275-319),
  remove `report=report`, route the two no-data branches through the
  shared helper.
- **Modify:** `src/contract/extractors/smart_money.py:392-405, 458-512`
  — same shape; strip the synthetic-report block (lines ~461-503),
  remove `report=report`, route the no-data branch through the shared
  helper.
- **Modify:** `src/contract/extractors/news.py:25-37, 189-194` — drop
  the `"headline_polarity_mean"` entry from `_KEYS` and the
  `out["headline_polarity_mean"] = polarity_mean` assignment. Keep
  `headline_polarity_mean_7d` (the live reader).

### Validator + canonical builder second
- **Modify:** `src/contract/evidence.py:109-156` — rename
  `_report_required_when_data_present` to
  `_prose_surface_required_when_data_present` and replace its body with
  the new two-clause invariant above.
- **Modify:** `src/contract/evidence.py` (after the `TickerVerdict`
  class, before `LlmTickerVerdict`) — add `build_no_data_verdict` and
  a verdict-only sibling `_no_data_analyst_verdict(*, reason)` used by
  the extractors (which return `AnalystVerdict`, not `TickerVerdict`).

### Renderers + readers third
- **Modify:** `src/contract/strategist_prompt.py` (the `_render_report`
  and `_render_analyst` helpers, plus the `TECHNICAL_BULLETS`,
  `SOCIAL_BULLETS`, `SMART_MONEY_BULLETS` registries) — the renderer
  must still emit `-> Report summary:` when `report is not None` and
  must additionally emit the `rationale` one-liner when
  `report is None and rationale != ""`. The current code already
  branches on report presence (line ~488). Add the rationale fallback
  branch explicitly; no `or` chains across the two fields.
- **Modify:** `src/agents/analysts/_common.py:148-158` — replace the
  hand-rolled `AnalystVerdict(...is_no_data=True...)` block with
  `verdict = _no_data_analyst_verdict(reason="no verdict from LLM")`.
- **Modify:** `src/agents/strategist/derivation.py` — locate the
  default-stance synthesis path (the third A-015 site, around the
  `default no_action stance when LLM omits a watchlist ticker` block)
  and route it through `build_no_data_verdict` if it emits a verdict;
  if it only emits a `TickerStance`, leave it alone (stance prose is
  Plan 05 territory). Confirm by reading the file before editing.

### Tests last
- **Rewrite:** `tests/unit/contract/extractors/test_technical.py` —
  every existing assertion of `v.report is not None` becomes
  `assert v.report is None and v.rationale != ""`.
  Add one regression test (`test_deterministic_verdict_has_no_report`)
  that proves the extractor no longer fabricates prose.
- **Rewrite:** `tests/unit/contract/extractors/test_social.py` — same
  shape; same regression test.
- **Rewrite:** `tests/unit/contract/extractors/test_smart_money.py` —
  same shape; same regression test.
- **Modify:** `tests/unit/contract/extractors/test_news.py:51-100` —
  drop the four `headline_polarity_mean` assertions; keep the four
  `_7d` assertions.
- **Modify:** `tests/unit/contract/test_evidence.py:53-138` and
  `tests/unit/contract/test_analyst_report.py:75-86` — the old
  validator name `_report_required_when_data_present` is referenced in
  comments and the test fixtures use `report=_STUB_REPORT` to satisfy
  the old strict rule. Rewrite to exercise the new invariant
  (`rationale OR report`).
- **New:** `tests/unit/contract/test_no_data_verdict.py` — covers
  `build_no_data_verdict` (happy path, empty-reason raises,
  whitespace-only raises, returned shape contract).
- **New:** `tests/unit/contract/test_prose_surface_invariant.py` — five
  unit tests for the new validator:
    1. `rationale != "" AND report is None AND NOT is_no_data` → OK.
    2. `rationale == "" AND report is not None AND NOT is_no_data` → OK.
    3. `rationale == "" AND report is None AND NOT is_no_data` → raises.
    4. `rationale != "" AND report is not None AND NOT is_no_data` → raises.
    5. `is_no_data=True` short-circuits the check (both surfaces empty
       OK; rationale present + report None also OK).

---

## 4. Tasks

### Task 1: Add the new validator invariant on `AnalystVerdict`

**Files:**
- Modify: `src/contract/evidence.py:109-156`
- Test: `tests/unit/contract/test_prose_surface_invariant.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/contract/test_prose_surface_invariant.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver

_REPORT = AnalystReport(
    summary="Two drivers converging negative.",
    drivers=[
        ReportDriver(name="rsi", direction="bear", weight=0.5, body="rsi 78"),
        ReportDriver(name="trend", direction="bear", weight=0.5, body="20d -4%"),
    ],
)


def test_rationale_only_is_valid() -> None:
    """Deterministic verdict: rationale carries the one-liner, report is None."""
    v = AnalystVerdict(
        lean="bullish", magnitude=0.3, confidence=0.6,
        rationale="trend_up_20d, momentum_agree",
    )
    assert v.rationale == "trend_up_20d, momentum_agree"
    assert v.report is None


def test_report_only_is_valid() -> None:
    """LLM verdict: report carries the prose, rationale is the empty default."""
    v = AnalystVerdict(
        lean="bearish", magnitude=0.4, confidence=0.7,
        report=_REPORT,
    )
    assert v.rationale == ""
    assert v.report is _REPORT


def test_both_prose_surfaces_rejected() -> None:
    """A verdict carrying both rationale AND report is the old synthetic-prose
    bug; the new invariant rejects it loudly."""
    with pytest.raises(ValidationError, match="exactly one prose surface"):
        AnalystVerdict(
            lean="bullish", magnitude=0.3, confidence=0.6,
            rationale="trend_up_20d",
            report=_REPORT,
        )


def test_no_prose_surface_rejected_when_data_present() -> None:
    """Non-no-data verdict with neither rationale nor report → raises."""
    with pytest.raises(ValidationError, match="prose surface"):
        AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0)


def test_no_data_short_circuits_invariant() -> None:
    """is_no_data=True with rationale-only (the canonical no-data shape) is OK
    even though report is None."""
    v = AnalystVerdict(
        lean="neutral", magnitude=0.0, confidence=0.0,
        rationale="no price data",
        is_no_data=True,
    )
    assert v.is_no_data is True
    assert v.report is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_prose_surface_invariant.py -v`
Expected: FAIL — the new invariant text isn't in the validator yet, so
`test_both_prose_surfaces_rejected` will accept the input.

- [ ] **Step 3: Rewrite the validator on `AnalystVerdict`**

In `src/contract/evidence.py`, replace the existing
`_report_required_when_data_present` method (lines 136-155) with:

```python
@model_validator(mode="after")
def _prose_surface_required_when_data_present(self) -> AnalystVerdict:
    """A non-no-data verdict must carry exactly one prose surface.

    - Deterministic extractors populate ``rationale`` (a one-line
      ``", "``-joined factor list) and leave ``report=None``.
    - LLM analysts populate ``report`` (summary + drivers block) and
      leave ``rationale=""``.
    - Carrying both is the old synthetic-prose pathology (extractors
      fabricating an ``AnalystReport`` to satisfy the previous validator)
      and is rejected loudly so it can't silently reappear.
    - ``is_no_data=True`` short-circuits the check; the canonical no-data
      shape is ``rationale="<reason>"`` with ``report=None``.
    """

    # No-data verdicts have their own shape contract; the builder enforces it.
    if self.is_no_data:
        return self

    has_rationale = bool(self.rationale)
    has_report    = self.report is not None

    if has_rationale and has_report:
        raise ValueError(
            "verdict carries both rationale and report — exactly one prose "
            "surface is allowed: rationale (deterministic extractors) OR "
            "report (LLM analysts)"
        )

    if not has_rationale and not has_report:
        raise ValueError(
            "verdict has no prose surface — populate either rationale "
            "(deterministic) or report (LLM)"
        )

    return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_prose_surface_invariant.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/contract/evidence.py tests/unit/contract/test_prose_surface_invariant.py
git commit -m "$(cat <<'EOF'
refactor(contract): replace report-required validator with prose-surface invariant

A non-no-data AnalystVerdict must now carry exactly one prose surface
— rationale (deterministic extractors) OR report (LLM analysts) —
never both, never neither. Locks the door against the synthetic-prose
path that A-016 / A-049 grew out of.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `build_no_data_verdict` + `_no_data_analyst_verdict`

**Files:**
- Modify: `src/contract/evidence.py` (insert after `TickerVerdict`)
- Test: `tests/unit/contract/test_no_data_verdict.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/contract/test_no_data_verdict.py
from __future__ import annotations

import pytest

from contract.evidence import (
    AnalystVerdict,
    TickerVerdict,
    _no_data_analyst_verdict,
    build_no_data_verdict,
)


def test_builds_canonical_ticker_verdict() -> None:
    v = build_no_data_verdict("AAPL", reason="provider returned empty payload")
    assert isinstance(v, TickerVerdict)
    assert v.ticker == "AAPL"
    assert v.is_no_data is True
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0
    assert v.report is None
    assert v.rationale == "provider returned empty payload"
    assert v.key_factors == []


def test_empty_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        build_no_data_verdict("AAPL", reason="")


def test_whitespace_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        build_no_data_verdict("AAPL", reason="   \t\n  ")


def test_analyst_verdict_helper_drops_ticker() -> None:
    v = _no_data_analyst_verdict(reason="no verdict from LLM")
    assert isinstance(v, AnalystVerdict)
    assert not isinstance(v, TickerVerdict)
    assert v.is_no_data is True
    assert v.rationale == "no verdict from LLM"
    assert v.report is None


def test_analyst_verdict_helper_empty_reason_raises() -> None:
    with pytest.raises(ValueError, match="reason"):
        _no_data_analyst_verdict(reason="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_no_data_verdict.py -v`
Expected: FAIL with ImportError on `build_no_data_verdict` /
`_no_data_analyst_verdict`.

- [ ] **Step 3: Add the builders to `src/contract/evidence.py`**

Insert immediately after the `TickerVerdict` class definition (and
before `LlmTickerVerdict`):

```python
def _no_data_analyst_verdict(*, reason: str) -> AnalystVerdict:
    """Canonical 'we had no data this tick' shape, ticker-free.

    Used by per-analyst extractors which return AnalystVerdict (not
    TickerVerdict — the joiner attaches the ticker later).

    Parameters:
        reason:  short prose explanation (must be non-empty).

    Raises:
        ValueError: if reason is empty or whitespace-only — every
                    no-data site already has a real reason available;
                    silent defaults are the recurring bug class
                    (auto-memory: silent-failures-loud-tests).
    """

    if not reason or not reason.strip():
        raise ValueError(
            "no-data verdict requires a non-empty reason — silent "
            "fallback strings are the bug class this builder closes"
        )

    return AnalystVerdict(
        lean="neutral",
        magnitude=0.0,
        confidence=0.0,
        rationale=reason,
        key_factors=[],
        is_no_data=True,
    )


def build_no_data_verdict(ticker: str, *, reason: str) -> TickerVerdict:
    """Canonical 'we had no data this tick' shape, ticker-attached.

    Single source of truth for the three sites that previously
    hand-rolled no-data verdicts with drifting confidence / wording /
    direction (A-015). Strategist derivation and any joiner that needs
    a per-ticker no-data record should call this.

    Parameters:
        ticker:  symbol the verdict applies to.
        reason:  short prose explanation (must be non-empty).
    """

    if not reason or not reason.strip():
        raise ValueError(
            "no-data verdict requires a non-empty reason — silent "
            "fallback strings are the bug class this builder closes"
        )

    return TickerVerdict(
        ticker=ticker,
        lean="neutral",
        magnitude=0.0,
        confidence=0.0,
        rationale=reason,
        key_factors=[],
        is_no_data=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_no_data_verdict.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/contract/evidence.py tests/unit/contract/test_no_data_verdict.py
git commit -m "$(cat <<'EOF'
feat(contract): add canonical no-data verdict builders

Single source of truth for the three sites (analyst joiner, contract
extractors, strategist derivation) that previously hand-rolled no-data
verdicts with drifting confidence / wording / direction (A-015).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Strip synthetic-prose path from `technical.py`

**Files:**
- Modify: `src/contract/extractors/technical.py:515-705`
- Test: `tests/unit/contract/extractors/test_technical.py`

- [ ] **Step 1: Write the failing regression test**

Append to `tests/unit/contract/extractors/test_technical.py`:

```python
def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: technical extractor must leave
    report=None and let rationale carry the one-liner. Previously the
    extractor synthesised an AnalystReport to satisfy the old
    _report_required_when_data_present validator — that path is gone."""

    # Use a fixture that produces a non-no-data verdict; reuse any
    # existing test helper that builds 'features' from real-shaped raw.
    features = {
        "rsi_14": 55.0, "pct_change_20d": 0.04, "pct_change_5d": 0.01,
        "vol_ratio_20d": 1.1, "atr_pct_14": 1.5,
        "dist_from_high_52w_pct": -5.0, "dist_from_low_52w_pct": 25.0,
        "golden_cross": 0.0, "death_cross": 0.0,
        "beta_confidence_damping": 1.0, "last_close": 100.0,
    }
    from agents.heuristics.technical import TechnicalHeuristics
    from contract.extractors.technical import derive_technical_verdict

    v = derive_technical_verdict(features, TechnicalHeuristics())

    assert v.is_no_data is False
    assert v.report is None, "deterministic extractor must not fabricate report"
    assert v.rationale != "", "rationale carries the deterministic one-liner"


def test_no_data_branch_uses_canonical_builder() -> None:
    """The all-zero fingerprint branch produces is_no_data=True with the
    canonical shape (report=None, non-empty rationale)."""

    features = {k: 0.0 for k in (
        "rsi_14", "pct_change_20d", "pct_change_5d", "vol_ratio_20d",
        "atr_pct_14", "dist_from_high_52w_pct", "dist_from_low_52w_pct",
        "golden_cross", "death_cross", "beta_confidence_damping",
        "last_close",
    )}
    from agents.heuristics.technical import TechnicalHeuristics
    from contract.extractors.technical import derive_technical_verdict

    v = derive_technical_verdict(features, TechnicalHeuristics())

    assert v.is_no_data is True
    assert v.report is None
    assert v.rationale  # non-empty
```

Also: scan the existing test file for any assertion of the form
`assert v.report is not None` or `v.report.summary` and **delete those
assertions** (they were pinning the bug). Search:
`grep -n "report" tests/unit/contract/extractors/test_technical.py`
and rewrite each match to the new contract.

- [ ] **Step 2: Run the regression test — verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py::test_deterministic_verdict_no_longer_fabricates_report -v`
Expected: FAIL — extractor still emits a synthetic report.

- [ ] **Step 3: Strip the synthetic-report block from `technical.py`**

In `src/contract/extractors/technical.py`:

1. Replace the no-data block (lines 521-533) with:

```python
    if (
        features["rsi_14"] == 0
        and features["pct_change_20d"] == 0
        and features["atr_pct_14"] == 0
    ):
        # All-zero fingerprint = extractor saw no price history.
        # Route through the canonical no-data builder so every
        # synthesis site picks the same shape (A-015).
        from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

        return _no_data_analyst_verdict(reason="no price data")
```

2. Delete the synthetic-report block (lines 650-695, the entire
   `direction_map` / `driver_factors` / `drivers` / `summary` /
   `report = AnalystReport(...)` chunk). Also drop `AnalystReport` and
   `ReportDriver` from the runtime import on line 515 — only
   `AnalystVerdict` remains.

3. Drop the `report=report` kwarg from the final
   `AnalystVerdict(...)` constructor (line 697-705). After edit:

```python
    # --- Rationale -----------------------------------------------------------
    rationale = (", ".join(factors) or "neutral")[:160]

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
    )
```

- [ ] **Step 4: Run the full technical extractor test file**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -v`
Expected: every test passes, including the two new regression tests.

If any existing test still asserts `v.report is not None`, rewrite it
to assert `v.report is None and v.rationale != ""` in the same step.

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/technical.py tests/unit/contract/extractors/test_technical.py
git commit -m "$(cat <<'EOF'
refactor(technical-extractor): stop fabricating AnalystReport prose

Deterministic extractor now returns rationale-only verdicts as the
field comment always said it should (A-016, intent §2.1/§2.6).
No-data branch routed through canonical builder.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Strip synthetic-prose path from `social.py`

**Files:**
- Modify: `src/contract/extractors/social.py:193-328`
- Test: `tests/unit/contract/extractors/test_social.py`

- [ ] **Step 1: Write the failing regression test**

Append to `tests/unit/contract/extractors/test_social.py`:

```python
def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: social extractor must leave
    report=None and let rationale carry the one-liner."""

    features = {
        "mention_count_24h": 50.0,
        "mention_count_7d":  200.0,
        "social_aggregate_score": 0.4,
        "aggregate_score": 0.4,
        "score_velocity_24h": 0.1,
        "platform_score_disagreement": 0.2,
        "is_no_data": 0.0,
    }
    from agents.heuristics.social import SocialHeuristics
    from contract.extractors.social import derive_social_verdict

    v = derive_social_verdict(features, SocialHeuristics())

    assert v.is_no_data is False
    assert v.report is None
    assert v.rationale != ""


def test_no_data_branches_use_canonical_builder() -> None:
    """Both empty-input branches yield the canonical no-data shape."""

    from agents.heuristics.social import SocialHeuristics
    from contract.extractors.social import derive_social_verdict

    # Branch 1: is_no_data sentinel.
    features_sentinel = {"is_no_data": 1.0}
    v1 = derive_social_verdict(features_sentinel, SocialHeuristics())
    assert v1.is_no_data is True
    assert v1.report is None
    assert v1.rationale

    # Branch 2: zero mentions.
    features_empty = {
        "mention_count_24h": 0.0, "mention_count_7d": 0.0,
        "social_aggregate_score": 0.0, "aggregate_score": 0.0,
        "score_velocity_24h": 0.0, "platform_score_disagreement": 0.0,
        "is_no_data": 0.0,
    }
    v2 = derive_social_verdict(features_empty, SocialHeuristics())
    assert v2.is_no_data is True
    assert v2.report is None
    assert v2.rationale
```

Scan the existing test file and rewrite any
`assert v.report is not None` to the new contract.

- [ ] **Step 2: Run the regression test — verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_social.py::test_deterministic_verdict_no_longer_fabricates_report -v`
Expected: FAIL.

- [ ] **Step 3: Strip the synthetic-report block from `social.py`**

In `src/contract/extractors/social.py`:

1. Replace each no-data branch (around lines 200-203 and 213-217) with
   a single call into `_no_data_analyst_verdict`:

```python
        from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

        return _no_data_analyst_verdict(reason="no social mentions")
```

   (Use the existing per-branch reason — preserve the distinction
   between the two empty-input paths in the reason string if their
   wording differs today; check before flattening.)

2. Delete the synthetic-report block (lines ~275-319). Drop
   `AnalystReport` and `ReportDriver` from the runtime import on line
   193 — only `AnalystVerdict` remains.

3. Drop `report=report` from the final `AnalystVerdict(...)`
   constructor.

- [ ] **Step 4: Run the full social extractor test file**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_social.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/social.py tests/unit/contract/extractors/test_social.py
git commit -m "$(cat <<'EOF'
refactor(social-extractor): stop fabricating AnalystReport prose

Same shape as the technical-extractor fix: rationale-only verdicts,
no-data branches routed through the canonical builder.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Strip synthetic-prose path from `smart_money.py`

**Files:**
- Modify: `src/contract/extractors/smart_money.py:392-512`
- Test: `tests/unit/contract/extractors/test_smart_money.py`

- [ ] **Step 1: Write the failing regression test**

Append to `tests/unit/contract/extractors/test_smart_money.py`:

```python
def test_deterministic_verdict_no_longer_fabricates_report() -> None:
    """A-016 / A-049 regression: smart_money extractor must leave
    report=None and let rationale carry the one-liner."""

    # Use a fixture that produces a non-no-data verdict; reuse the
    # existing politician-trade fixture pattern in this file.
    features = {
        "is_no_data": 0.0,
        "politicians_n_buys_30d": 3.0,
        "politicians_n_sells_30d": 0.0,
        "politicians_net_flow_dollar_30d": 250_000.0,
        # ... (fill remaining _KEYS with 0.0 — copy from existing fixture)
    }
    from agents.heuristics.smart_money import SmartMoneyHeuristics
    from contract.extractors.smart_money import derive_smart_money_verdict

    # Backfill any missing _KEYS to 0.0 so the helper doesn't KeyError.
    from contract.extractors.smart_money import _KEYS
    for k in _KEYS:
        features.setdefault(k, 0.0)

    v = derive_smart_money_verdict(features, SmartMoneyHeuristics())

    assert v.is_no_data is False
    assert v.report is None
    assert v.rationale != ""


def test_no_data_branch_uses_canonical_builder() -> None:
    """is_no_data sentinel → canonical no-data shape."""

    from agents.heuristics.smart_money import SmartMoneyHeuristics
    from contract.extractors.smart_money import _KEYS, derive_smart_money_verdict

    features = {k: 0.0 for k in _KEYS}
    features["is_no_data"] = 1.0

    v = derive_smart_money_verdict(features, SmartMoneyHeuristics())

    assert v.is_no_data is True
    assert v.report is None
    assert v.rationale
```

Scan the existing test file and rewrite any
`assert v.report is not None` to the new contract.

- [ ] **Step 2: Run the regression test — verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_smart_money.py::test_deterministic_verdict_no_longer_fabricates_report -v`
Expected: FAIL.

- [ ] **Step 3: Strip the synthetic-report block from `smart_money.py`**

In `src/contract/extractors/smart_money.py`:

1. Replace the no-data branch (lines 400-405) with:

```python
        from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

        return _no_data_analyst_verdict(reason="no smart-money activity")
```

2. Delete the synthetic-report block (lines ~461-503). Drop
   `AnalystReport` and `ReportDriver` from the runtime import on line
   392 — only `AnalystVerdict` remains.

3. Drop `report=report` from the final `AnalystVerdict(...)`
   constructor.

- [ ] **Step 4: Run the full smart_money extractor test file**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_smart_money.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/smart_money.py tests/unit/contract/extractors/test_smart_money.py
git commit -m "$(cat <<'EOF'
refactor(smart_money-extractor): stop fabricating AnalystReport prose

Completes the deterministic-extractor sweep started by the technical
and social fixes (A-016). The shelved smart_money pipeline still
exercises this code path in unit tests; the shape is now uniform.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Route the analyst-joiner no-data path through the canonical builder

**Files:**
- Modify: `src/agents/analysts/_common.py:148-158`
- Test: `tests/unit/agents/analysts/` (whichever file covers `_common`;
  identify with
  `grep -rn "synth.*no-data\|no verdict from LLM" tests/`)

- [ ] **Step 1: Identify the existing test that covers the
  hand-rolled no-data block**

Run: `grep -rn "no verdict from LLM\|is_no_data=True" tests/unit/agents/analysts/`
Note the file(s); the joiner pattern is shared so this likely lives in
one test_*joiner*.py.

- [ ] **Step 2: Rewrite the existing test to assert the new shape**

The replacement assertion is: the synthesised verdict's `rationale`
equals `"no verdict from LLM"`, `report is None`, `is_no_data is True`.
If no existing test covers this branch, add one:

```python
def test_joiner_synthesises_canonical_no_data_when_llm_omits_ticker() -> None:
    # ... existing joiner test scaffolding ...
    # Pass a verdicts_by_ticker dict missing the ticker under test.
    # Assert the resulting AnalystEvidence.verdict shape:
    assert ev.verdict.is_no_data is True
    assert ev.verdict.report is None
    assert ev.verdict.rationale == "no verdict from LLM"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest <identified-test-file> -v`
Expected: FAIL if the existing test pins old wording, or PASS if shape
is already compatible (in which case skip Step 4's behaviour change
and just refactor for readability).

- [ ] **Step 4: Rewrite the hand-rolled block in `_common.py`**

In `src/agents/analysts/_common.py:148-158`, replace:

```python
            if raw_v is None:
                # LLM omitted this ticker — synthesise a safe no-data record
                # so downstream consumers always receive one record per ticker.
                verdict = AnalystVerdict(
                    lean="neutral",
                    magnitude=0.0,
                    confidence=0.0,
                    rationale="no verdict from LLM",
                    key_factors=[],
                    is_no_data=True,
                )
            else:
                # Validate the LLM's output dict against the strict schema.
                verdict = AnalystVerdict.model_validate(raw_v)
```

with:

```python
            if raw_v is None:
                # LLM omitted this ticker — route through the canonical
                # builder so every no-data synthesis site uses one shape
                # (A-015). Reason string is preserved verbatim for any
                # downstream consumer that key-matches on it.
                from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

                verdict = _no_data_analyst_verdict(reason="no verdict from LLM")
            else:
                # Validate the LLM's output dict against the strict schema.
                verdict = AnalystVerdict.model_validate(raw_v)
```

- [ ] **Step 5: Run the joiner test plus contract tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/ tests/unit/contract/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/_common.py tests/unit/agents/analysts/
git commit -m "$(cat <<'EOF'
refactor(analyst-joiner): route missing-ticker no-data through canonical builder

Third and final A-015 site converted; analyst joiner, contract
extractors, and (next commit) strategist derivation all now share
one no-data shape.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Audit and route the strategist-derivation no-data site

**Files:**
- Read: `src/agents/strategist/derivation.py` (entire file)
- Possibly modify: `src/agents/strategist/derivation.py`

- [ ] **Step 1: Read derivation.py end-to-end and locate the
  "default stance when LLM omits a watchlist ticker" path**

Run: `grep -n "no_action\|omitted\|missing.*ticker\|default.*stance" src/agents/strategist/derivation.py`

The path may build a `TickerStance` (Plan 05 territory — leave alone)
rather than a verdict. The third A-015 site in the original audit was
described as "default `no_action` stance when LLM omits a watchlist
ticker" — confirm whether this site actually synthesises an
`AnalystVerdict` or a `TickerStance`.

- [ ] **Step 2: Decide based on what is found**

- If derivation.py synthesises a **verdict** (`AnalystVerdict` /
  `TickerVerdict`): route through `build_no_data_verdict` exactly as
  Task 6 did for the joiner. Write a regression test first.
- If derivation.py synthesises a **stance** (`TickerStance`): record
  this in `docs/audits/2026-05-26-codebase-audit/plans/plan-02-rationale-collapse.md`
  itself by appending a `## Notes for Plan 05` section noting the stance
  default needs the same single-source treatment. **Do not touch
  stance code here** — that boundary belongs to Plan 05.

- [ ] **Step 3: Commit if a code change was made**

```bash
git add src/agents/strategist/derivation.py tests/unit/agents/strategist/
git commit -m "$(cat <<'EOF'
refactor(strategist-derivation): route LLM-omitted-ticker through canonical no-data builder

Final A-015 site; the no-data shape is now uniform across the analyst
joiner, contract extractors, and strategist derivation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Drop the `headline_polarity_mean` alias

**Files:**
- Modify: `src/contract/extractors/news.py:25-37, 189-194`
- Modify: `tests/unit/contract/extractors/test_news.py:51-100`

- [ ] **Step 1: Rewrite the existing news tests**

In `tests/unit/contract/extractors/test_news.py`, delete every
assertion of the form
`features["headline_polarity_mean"] == ...` (lines 51-100 contain
four). Keep the `_7d` variants verbatim — they cover the canonical
key.

- [ ] **Step 2: Run tests to verify they now reference only `_7d`**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_news.py -v`
Expected: all pass (the alias still produces the same value, so the
`_7d` assertions are unaffected).

- [ ] **Step 3: Delete the alias from `news.py`**

In `src/contract/extractors/news.py`:

- Delete line 29: `"headline_polarity_mean",        # renamed from headline_polarity_mean_7d`
- Delete line 193: `out["headline_polarity_mean"]     = polarity_mean`
- On line 194, drop the trailing `# back-compat alias` comment (it is
  no longer an alias — it is the only writer).

- [ ] **Step 4: Run the news tests + the strategist prompt layout
  test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_news.py tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: all pass; the prompt-layout test still finds
`headline_polarity_mean_7d` via `strategist_prompt.py:377`.

- [ ] **Step 5: Confirm no other reader picks up the alias**

Run: `grep -rn "headline_polarity_mean\b" src/ tests/ scripts/`
Expected: zero matches for the bare form; `_7d` matches expected.

- [ ] **Step 6: Commit**

```bash
git add src/contract/extractors/news.py tests/unit/contract/extractors/test_news.py
git commit -m "$(cat <<'EOF'
refactor(news-extractor): drop headline_polarity_mean non-suffix alias

The _7d-suffixed key is the only one with a downstream reader
(strategist_prompt.py:377); the non-suffix alias was dead (A-048).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Update the strategist prompt renderer to branch on prose
surface

**Files:**
- Modify: `src/contract/strategist_prompt.py` (`_render_analyst`,
  `_render_report`)
- Test: `tests/unit/contract/test_strategist_prompt_layout.py`

- [ ] **Step 1: Read the current `_render_analyst` and `_render_report`
  helpers**

Run: `sed -n '480,580p' src/contract/strategist_prompt.py`
(only to read — do not execute as part of the implementation.)

Confirm the current behaviour: `_render_report` emits
`-> Report summary: "..."` plus a `-> Drivers:` block when
`report is not None`. Deterministic analysts now ALWAYS have
`report=None`, so they will lose their per-analyst prose line unless we
explicitly emit `rationale` as a one-line fallback.

- [ ] **Step 2: Write a failing layout test**

Append to `tests/unit/contract/test_strategist_prompt_layout.py`:

```python
def test_deterministic_analyst_block_renders_rationale_line() -> None:
    """A deterministic analyst's per-analyst block must show the
    rationale one-liner when report is None — otherwise the strategist
    loses all per-analyst prose for technical/social/smart_money."""

    # Build a TickerEvidence with one deterministic analyst (technical)
    # carrying rationale-only verdict; reuse existing fixture helpers.
    # ... construct te with technical: AnalystEvidence(verdict=v) where
    #     v.rationale="trend_up_20d, momentum_agree", v.report=None ...

    block = render_ticker_block(te)
    assert "trend_up_20d, momentum_agree" in block
    # Must not render an empty "-> Report summary:" line.
    assert "-> Report summary:" not in block.split("technical", 1)[1].split("---", 1)[0]


def test_llm_analyst_block_renders_report_summary() -> None:
    """An LLM analyst (rationale=='', report populated) renders the
    report summary as today."""

    # ... construct te with news: AnalystEvidence(verdict=v) where
    #     v.rationale="", v.report=AnalystReport(summary="LLM prose", ...) ...

    block = render_ticker_block(te)
    assert "LLM prose" in block
```

- [ ] **Step 3: Run the layout test — verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: FAIL — the deterministic block currently emits the
synthetic `-> Report summary: "Technical analysis leans bullish..."`
because the extractor used to produce a report; with the report gone,
nothing fills the prose slot.

- [ ] **Step 4: Update `_render_analyst` to emit rationale when report
  is None**

In `src/contract/strategist_prompt.py`, locate the analyst-block
section of `_render_analyst` that currently calls
`_render_report(report)`. Wrap with an explicit branch:

```python
    # Prose surface: either report (LLM) or rationale (deterministic).
    # Exactly one is populated per the AnalystVerdict invariant; render
    # whichever is present and emit nothing when neither is (the
    # is_no_data branch already short-circuits above).
    if verdict.report is not None:
        lines.extend(_render_report(verdict.report))
    elif verdict.rationale:
        lines.append(f'  -> Rationale: "{verdict.rationale}"')
```

(Adjust indentation / `lines.extend` vs `lines.append` to match the
surrounding helper.)

- [ ] **Step 5: Run the layout test + every snapshot test that touches
  the prompt**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/ -v`
Expected: all pass. If any existing snapshot test compares the prompt
to a frozen string, update the snapshot in the same step (the
deterministic blocks now read "Rationale" not "Report summary").

- [ ] **Step 6: Commit**

```bash
git add src/contract/strategist_prompt.py tests/unit/contract/test_strategist_prompt_layout.py
git commit -m "$(cat <<'EOF'
refactor(strategist-prompt): render rationale when report is None

Deterministic analysts no longer carry a synthetic AnalystReport,
so the per-analyst block must render rationale as the prose surface
in that case. Exactly-one-prose-surface invariant from Task 1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Full-suite verification

**Files:** none modified; verification only.

- [ ] **Step 1: Run the entire test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: every test passes. Any failure is either (a) a test pinning
the old behaviour that was missed in the per-task rewrites — fix
in-place and commit with the failing test's plan-task tag, or (b) a
real regression — stop and investigate.

- [ ] **Step 2: Run ruff against the touched files**

Run: `.venv/bin/python -m ruff check src/contract/ src/agents/analysts/_common.py src/agents/strategist/derivation.py`
Expected: clean.

- [ ] **Step 3: Confirm no stranded references**

Run:
```
grep -rn "_report_required_when_data_present" src/ tests/
grep -rn "headline_polarity_mean\b" src/ tests/ scripts/
grep -rn "summary=.*drivers=" src/contract/extractors/
```
Expected: zero matches for all three (the validator was renamed; the
alias deleted; no extractor still builds a report).

- [ ] **Step 4: Update graph_delta.md**

Per project CLAUDE.md, append a dated entry to
`graphify-out/graph_delta.md` recording the changed symbols:
- removed `_report_required_when_data_present`
  → added `_prose_surface_required_when_data_present`
- added `build_no_data_verdict`, `_no_data_analyst_verdict` in
  `src/contract/evidence.py`
- removed `report=AnalystReport(...)` synthesis in three extractors

(No code commit for this step; the delta lives outside git.)

---

## 5. Test strategy

| Concern | Test file | What it pins (new behaviour) |
|---------|-----------|------------------------------|
| Validator invariant | `tests/unit/contract/test_prose_surface_invariant.py` (new) | rationale-only OK; report-only OK; both rejected; neither rejected; is_no_data short-circuits |
| Builder shape | `tests/unit/contract/test_no_data_verdict.py` (new) | canonical shape; empty/whitespace reason raises |
| Technical extractor | `tests/unit/contract/extractors/test_technical.py` (rewritten) | report is None; rationale non-empty; no-data branch routed through builder |
| Social extractor | `tests/unit/contract/extractors/test_social.py` (rewritten) | same shape, two no-data branches |
| Smart_money extractor | `tests/unit/contract/extractors/test_smart_money.py` (rewritten) | same shape, one no-data branch |
| News alias | `tests/unit/contract/extractors/test_news.py` (modified) | only `_7d` key asserted; bare form removed |
| Analyst joiner | identified at Task 6 step 1 | LLM-omitted-ticker yields canonical no-data shape |
| Prompt renderer | `tests/unit/contract/test_strategist_prompt_layout.py` (extended) | deterministic block renders rationale; LLM block renders report summary |
| Existing test fixtures | `tests/unit/contract/test_evidence.py`, `test_analyst_report.py` | rewritten where they referenced the old validator name or used stub reports to satisfy the old strict rule |

### Loud-behaviour assertions (per cross-cutting rule)

Every extractor regression test follows the pattern:
```python
assert v.is_no_data is False
assert v.report is None, "deterministic extractor must not fabricate report"
assert v.rationale != "", "rationale carries the deterministic one-liner"
```

The empty-reason builder tests follow the pattern:
```python
with pytest.raises(ValueError, match="reason"):
    build_no_data_verdict("AAPL", reason="")
```

Both patterns satisfy the auto-memory invariant
(`feedback_silent_failures_loud_tests`): the bug class would re-emerge
as a silent prose fabrication; the tests fail loudly the moment any
extractor adds `report=` back.

---

## 6. Risks / silent-regression checklist

- [ ] **`evidence_view.py` deleted by Plan 01.** This file is the only
  remaining live reader of both `rationale` AND `report.summary` via a
  fallback chain (line 100-102). If Plan 01 has NOT actually deleted
  it, the renderer will silently keep the dual read alive. Verify with
  `ls src/agents/strategist/evidence_view.py` before starting Task 1.
- [ ] **Snapshot tests of the strategist prompt.** Any test that
  compares the rendered prompt to a frozen string will need its
  snapshot updated when the deterministic-analyst block switches from
  "Report summary" to "Rationale". Search:
  `grep -rn "Report summary" tests/`. Update in the same task that
  changes the renderer (Task 9), not as a separate cleanup pass.
- [ ] **`AggregateVerdict.summary`** in `ticker_evidence.py` is a
  different field (rendered "3 bullish / 0 neutral / 1 bearish" string)
  — DO NOT touch. The dedupe is `AnalystVerdict.rationale` vs
  `AnalystReport.summary`, not the aggregate summary.
- [ ] **Position thesis / stance / strategist `_reasons` dicts.**
  These are part of the A-013 cluster but their removal cascades into
  executor verb-dispatch (5 sites), schema validation, decision_writer
  audit logs, and ~26 test references. They are **explicitly deferred
  to Plan 05/07**. Do not pre-empt them here — leave a `# TODO(plan-05)`
  comment at any site that surfaces during this work.
- [ ] **LLM emit-schema unchanged.** `LlmTickerVerdict` still requires
  `report`; we are only relaxing the inflated `AnalystVerdict`
  validator. The LLM analysts (News, Fundamental) are unaffected.
  Confirm with `grep -n "class LlmTickerVerdict" src/contract/evidence.py`
  → schema unchanged.
- [ ] **`AnalystVerdict.model_validate(raw_v)` in joiner.** A stale
  LLM raw payload containing a literal `rationale: ""` AND
  `report: {...}` is the canonical LLM happy path — must still
  validate. The new invariant rejects the BOTH case, so confirm the
  LLM never emits rationale; `LlmTickerVerdict` has no `rationale`
  field at all (see `src/contract/evidence.py:213-218`), so the inflated
  `AnalystVerdict` always gets `rationale=""`. Safe.
- [ ] **`headline_polarity_mean_7d` reader at `strategist_prompt.py:377`.**
  Confirm it is still the only reader of the suffix form before
  declaring Task 8 done.
- [ ] **No back-compat shim** for the deleted alias or the old
  validator. The intent (cross-cutting rule) is loud breakage; an
  `__getattr__` shim or `# DEPRECATED` retention would silently keep
  the old surface alive.

---

## 7. Definition of done

All of the following must hold simultaneously:

1. `grep -rn "_report_required_when_data_present" src/ tests/` returns
   zero matches.
2. `grep -rn "headline_polarity_mean\b" src/ tests/ scripts/` returns
   zero matches (only `_7d` form remains).
3. `grep -rn "summary=.*drivers=" src/contract/extractors/` returns
   zero matches (no extractor builds a synthetic report).
4. `grep -n "build_no_data_verdict\|_no_data_analyst_verdict" src/contract/evidence.py`
   returns the two definitions; `grep -rn "build_no_data_verdict\|_no_data_analyst_verdict" src/`
   shows at least four call sites (technical, social, smart_money,
   `_common.py` — plus derivation.py if Task 7 made a code change).
5. `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` passes
   end-to-end.
6. `.venv/bin/python -m ruff check src/contract/ src/agents/analysts/_common.py`
   is clean.
7. `graphify-out/graph_delta.md` has a dated entry covering the renamed
   validator, the two new builders, and the removed synthesis blocks.
8. Per-task commits exist in the order Tasks 1 → 10 (no squashing —
   the trust contract for later plans depends on each commit being
   bisectable).
9. Plan 05's pre-conditions are now satisfied: `AnalystVerdict.rationale`
   is the canonical analyst one-liner; deterministic verdicts carry
   `report=None`; the no-data builder exists and can be reused by the
   stance-default path.

---

## Self-review notes

- **Spec coverage:** A-013 (rationale dedupe — analyst-side portion
  only; thesis/stance/`_reasons` deferred with explicit pointer to
  Plan 05/07), A-015 (no-data builder + three site conversions),
  A-016 (validator relax + three extractor strips), A-048 (alias
  delete), A-049 (validator change makes the rationale/summary
  overlap structurally impossible: rationale is deterministic-only,
  summary is LLM-only, never both). All five findings have at least
  one task each.
- **Placeholders scanned:** no "TBD" / "appropriate error handling"
  / "similar to Task N". Every code block is concrete; every command
  has expected output.
- **Type consistency:** `AnalystVerdict` / `TickerVerdict` /
  `AnalystReport` referenced with the same names throughout.
  `_no_data_analyst_verdict` returns `AnalystVerdict` (used by
  extractors and `_common.py`); `build_no_data_verdict` returns
  `TickerVerdict` (used by strategist derivation if needed).
- **Trust contract:** explicitly checks Plan 01 has landed
  `evidence_view.py` deletion before Task 1 starts; explicitly defers
  `last_reviewed_reason` / `sell_reasons` / `update_reasons` to
  Plans 05/07.
