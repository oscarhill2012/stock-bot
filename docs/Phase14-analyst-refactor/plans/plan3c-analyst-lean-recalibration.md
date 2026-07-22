# Plan 3c — Analyst Lean Recalibration & Anchored Horizons

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Follow each task in order. Every task is a
> self-contained TDD cycle: write the failing test, watch it fail, implement, watch it
> pass, commit. Do not skip the failing-test step. Do not batch commits across tasks. If a
> step's observed behaviour diverges from what the plan predicts, STOP and re-read the
> source file before improvising.

**Spec:** `docs/Phase14-analyst-refactor/specs/analyst-lean-recalibration.md` — every
decision in it is settled (marked *Decision 2026-07-21*). Background diagnosis (context
only): `docs/Phase14-analyst-refactor/strategist-inverted-signal-diagnosis.md`.

**Sequencing:** Lands *after* Plan 3 (news-drift rebuild) and Plan 3b (technical
three-reads + horizon precursors), whose surfaces this plan revises. It is numbered `3c`
for lineage — it re-tunes the analyst leans that Plans 3/3b populated, and makes the
digest aggregate load-bearing, without renumbering Plans 4–5.

**Goal:** Recalibrate all three analyst leans so they stop being contrarian-by-construction
or bearish-by-default, anchor every directional lean to a dated data event (P2/P3), stop
counting abstains as votes (P4), and make the deterministic digest aggregate load-bearing —
rendered in the prompt, persisted every tick, and scored on the scoreboard (P6). Root cause
of the `first-month-5` loss: two of three analysts fought their own context and the news
edge was dropped on the floor within one tick, while the aggregate that should have combined
them was silently never persisted.

**Architecture:** Five independently-landable work packages. (1) A straight persistence
bug fix moves the `TickerEvidence` write loop from the pre-strategist evidence writer to the
post-strategist decision writer and makes an empty write raise. (2) The deterministic
technical verdict is rebuilt from a contrarian 5-day reversal into a config-weighted
trend/momentum composite anchored to the 200-day-MA state-flip date. (3) The fundamental LLM
doctrine is recalibrated to what Lazy Prices actually supports (sentiment-signed, trigger-
rare, magnitude-capped, filing-date-anchored). (4) The news analyst gains a per-run
last-fire record so a fired catalyst decays smoothly across the drift window instead of
self-zeroing next tick, and abstains stop entering the digest as neutral votes. (5) The
digest aggregate is rendered, its weights move to `config/digest.json`, and it is scored as
a pseudo-analyst with a stance-vs-aggregate agreement rate.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK, TA-Lib (`talib`), NumPy/pandas,
SQLAlchemy/SQLite, pytest.

## Global Constraints

Every task's requirements implicitly include this section.

- **British English everywhere** — code identifiers, comments, docs, prose (`behaviour`,
  `normalise`, `analyse`, `colour`, `optimise`).
- **Comment-heavy code** — every function gets a docstring (purpose, parameters, return
  value); non-trivial logic gets inline comments; blank lines separate logical blocks.
- **Config convention** — every tunable lives in `config/*.json`; each addition/removal
  updates `config/README.md` in the *same* task. Never hardcode a config value in source.
- **High-value tuning-knob markers** — the spec flags four sites as high-value tuning knobs
  (technical composite weights + horizon; the 8-K thesis-breaking event list; the news
  decay rate/horizon; the digest aggregate weights). At each of these sites the code MUST
  carry a comment reading `# HIGH-VALUE TUNING KNOB:` explaining the direction of the
  trade-off, so a later tuner finds it by grep.
- **Loud failures** — prefer raises over silent null/empty/neutral degradation; tests
  assert positive signals (the composite fired with the right sign; the aggregate row count
  equals tickers × ticks), not merely absence of errors.
- **`as_of` / PIT discipline** — every read of `state["as_of"]` goes through
  `resolve_as_of`; any datetime written to ADK state is ISO-stringified first (the backtest
  `DatabaseSessionService` cannot hold `datetime`). The news last-fire record and every
  rendered anchor date follow this.
- **ADK temp-handle rule** — never mutate `adk_session.state["temp:*"]` after
  `create_session`; the module-level per-run stores in this plan (news last-fire) follow the
  same singleton + `reset_*()` discipline as `NewsHistoryStore`, driven from
  `backtest/driver.py`, not from ADK state mutation.
- **No `max_length` on LLM prose fields** — do NOT add `max_length` to any field on
  `LlmTickerVerdict` / `AnalystReport`; prose bounds are stated in the prompt only (Vertex
  pad-toward-cap pathology).
- **No orphaned artefacts** — when a change supersedes old code (reversal config keys, the
  pre-strategist ticker-evidence loop, the dangling aggregate instruction), delete the old
  cleanly: no dead functions, stale config, commented-out blocks, or half-migrated call
  sites.
- **Shell conventions** — never prefix Bash commands with `cd`; the tool already runs in the
  project root. Tests: `.venv/bin/python -m pytest <path> -v`. Scoped scripts:
  `PYTHONPATH=src .venv/bin/python -m scripts.<name>`. Lint:
  `.venv/bin/python -m ruff check src/`.
- **One commit per task** — each task ends with a single commit; do not batch across tasks.
  Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## Cross-plan assumptions (verify once, do not re-litigate)

- Plans 3 and 3b have landed: the technical extractor already emits `vol_regime_z` /
  `trend_state`, `derive_technical_verdict` is the contrarian-reversal version, the news
  subsystem has `NewsHistoryStore` + `partition_articles_by_staleness`, and
  `LlmTickerVerdict.to_ticker_verdict(*, horizon_days)` injects config horizons. This plan
  edits those surfaces; it does not recreate them. If any is absent, STOP.
- The golden cache stores **provider data only** (prices, filings, news, insider). Analyst
  verdicts are generated live each run and never replayed from cache — so schema changes to
  `AnalystVerdict` (adding `abstain` / `carried`) are safe: no stale cached verdict is ever
  re-validated.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/agents/contract/evidence_writer.py` | Drop the `TickerEvidence` loop (keep only `AnalystEvidence`) | 1 |
| `src/agents/strategist/decision_writer.py` | Own the `TickerEvidence` loop; raise on empty | 1 |
| `src/agents/analysts/heuristics.py` | `TechnicalHeuristics` — composite weights + horizon, retire reversal knobs | 2 |
| `config/analyst_heuristics.json` | Technical composite config | 2 |
| `src/contract/extractors/technical.py` | `ma200_state`, `ma200_flip_days`; composite `derive_technical_verdict` | 3, 4 |
| `src/contract/strategist_prompt.py` | Technical anchor/horizon render; aggregate block; tag vocab | 5, 14 |
| `config/analysts.json` | Fundamental trigger/cap/decay/8-K keys | 6 |
| `src/agents/analysts/fundamental/prompts.py` | Lazy-Prices doctrine rewrite | 7 |
| `src/agents/analysts/fundamental/joiner.py` | Deterministic magnitude clamp + anchor injection | 8 |
| `src/contract/extractors/fundamental.py` | Filing-anchor/decay feature emission | 8 |
| `src/agents/analysts/news/last_fire.py` | Per-run last-fire record store (new) | 9 |
| `src/backtest/driver.py` | Reset last-fire store per window | 9 |
| `src/agents/analysts/news/joiner.py` | Persist fires; mark abstains | 9, 11 |
| `src/contract/evidence.py` | `abstain` + `carried` flags on `AnalystVerdict` | 11 |
| `src/agents/strategist/context_shim.py` | Numeric carried-signal synthesis before digest | 10 |
| `src/contract/digest.py` | Exclude abstains from aggregation | 11 |
| `src/agents/analysts/news/prompts.py` | STEP-3 wording | 11 |
| `config/digest.json` | Aggregate weights (new) | 13 |
| `src/contract/digest.py` | Load weights from config/digest.json | 13 |
| `src/backtest/scoreboard.py` | Aggregate pseudo-analyst + agreement-rate | 15 |
| `config/README.md` | Document every config change | 2, 6, 9, 13 |

---

## PHASE 0 — Persistence bug fix (independent; lands first)

### Task 1: Move the TickerEvidence write loop to the decision writer and make it loud

**Files:**
- Modify: `src/agents/contract/evidence_writer.py` (delete the `TickerEvidence` loop + its
  doc/imports)
- Modify: `src/agents/strategist/decision_writer.py` (add the loop; raise on empty)
- Test: `tests/unit/agents/contract/test_evidence_writer.py`,
  `tests/unit/agents/strategist/test_decision_writer.py`

**Interfaces:**
- Consumes: `state["temp:ticker_evidence_objects"]` (written by
  `StrategistContextShim`, which runs *inside* `_build_strategist()` — pipeline position 3),
  and `orchestrator.persistence.save_ticker_evidence(session, *, tick_id, ticker, aggregate,
  weights, analyst_count, recorded_at)`.
- Produces: one `TickerEvidenceRow` per watchlist ticker, written post-strategist.

**Background (verified):** `build_pipeline` wires the `SequentialAgent` as
`[_build_analyst_pool, build_evidence_writer, _build_strategist,
build_strategist_decision_writer, RiskGateAgent, build_executor, build_snapshotter]`
(`src/orchestrator/pipeline.py:159-170`). `EvidenceWriter` runs at position 2 — *before*
`_build_strategist` at position 3 — but `temp:ticker_evidence_objects` is only produced by
the strategist's context shim inside position 3. So the writer's
`state.get("temp:ticker_evidence_objects", []) or []` loop
(`evidence_writer.py:107-122`) silently wrote **zero** `ticker_evidence` rows for the whole
`first-month-5` run. `StrategistDecisionWriter` (position 4) already runs post-strategist and
already resolves `recorded_at` via `resolve_as_of`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/agents/strategist/test_decision_writer.py` a test proving the decision
writer now persists ticker-evidence and raises when the list is empty despite a non-empty
watchlist. Use the project's existing in-memory SQLite fixture pattern for
`orchestrator.persistence` (mirror the setup already in
`tests/unit/agents/contract/test_evidence_writer.py`; read it first for the exact
`Base.metadata.create_all` / `Session` boilerplate).

```python
import pytest


@pytest.mark.asyncio
async def test_decision_writer_persists_one_ticker_evidence_row_per_ticker(db_session):
    """The post-strategist writer persists exactly one TickerEvidenceRow per ticker."""
    from agents.strategist.decision_writer import build_strategist_decision_writer
    from orchestrator.persistence import TickerEvidenceRow

    te_objects = [
        _ticker_evidence_dump("AAPL"),   # helper builds a valid TickerEvidence.model_dump(mode="json")
        _ticker_evidence_dump("MSFT"),
    ]
    state = {
        "tickers": ["AAPL", "MSFT"],
        "tick_id": "run-1-2025-09-02T14:00:00-mid",
        "as_of": "2025-09-02T14:00:00",
        "temp:ticker_evidence_objects": te_objects,
        "strategist_decision": _minimal_decision_dump(["AAPL", "MSFT"]),
    }

    writer = build_strategist_decision_writer(db_session)
    _ = [ev async for ev in _run(writer, state)]

    rows = db_session.query(TickerEvidenceRow).all()
    assert len(rows) == 2                                   # POSITIVE: rows were written
    assert {r.ticker for r in rows} == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_decision_writer_raises_on_empty_ticker_evidence_with_tickers(db_session):
    """Empty ticker-evidence while the watchlist is non-empty is the silent-degradation
    bug — it must raise, never no-op."""
    from agents.strategist.decision_writer import build_strategist_decision_writer

    state = {
        "tickers": ["AAPL"],
        "tick_id": "run-1-x-mid",
        "as_of": "2025-09-02T14:00:00",
        "temp:ticker_evidence_objects": [],                # the bug's fingerprint
        "strategist_decision": _minimal_decision_dump(["AAPL"]),
    }

    writer = build_strategist_decision_writer(db_session)
    with pytest.raises(ValueError, match="ticker_evidence"):
        _ = [ev async for ev in _run(writer, state)]
```

Add to `tests/unit/agents/contract/test_evidence_writer.py` a test proving the evidence
writer no longer writes ticker-evidence (it only owns `AnalystEvidence` now):

```python
@pytest.mark.asyncio
async def test_evidence_writer_no_longer_writes_ticker_evidence(db_session):
    """TickerEvidence persistence moved to the decision writer; the evidence writer
    must not touch ticker_evidence even when the key is present in state."""
    from agents.contract.evidence_writer import build_evidence_writer
    from orchestrator.persistence import TickerEvidenceRow

    state = {
        "tickers": ["AAPL"],
        "tick_id": "run-1-x-mid",
        "as_of": "2025-09-02T14:00:00",
        "technical_evidence": [],
        "temp:ticker_evidence_objects": [_ticker_evidence_dump("AAPL")],
    }

    writer = build_evidence_writer(db_session)
    _ = [ev async for ev in _run(writer, state)]

    assert db_session.query(TickerEvidenceRow).count() == 0
```

(Write the `_run`, `_ticker_evidence_dump`, `_minimal_decision_dump`, and `db_session`
helpers/fixtures against the real `TickerEvidence` / `StrategistDecision` schemas — read
`contract/ticker_evidence.py` and `agents/strategist/schema.py` to build valid dumps.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_decision_writer.py tests/unit/agents/contract/test_evidence_writer.py -v`
Expected: the two decision-writer tests FAIL (`TickerEvidenceRow` count 0 / no raise), the
evidence-writer test FAILS (row count 1 — the loop still runs there).

- [ ] **Step 3: Delete the TickerEvidence loop from `evidence_writer.py`**

In `src/agents/contract/evidence_writer.py`:

1. Delete the entire `for te in state.get("temp:ticker_evidence_objects", []) or []:` block
   (lines ~103-122) including its leading comment.
2. Change the module docstring's first line from
   `"""Persist AnalystEvidence + TickerEvidence rows after every tick.` to
   `"""Persist AnalystEvidence rows after every tick.` and drop the `TickerEvidence`
   sentences from the docstring body and the `EvidenceWriter` class docstring (leave only
   the `AnalystEvidenceRow`/`save_analyst_evidence` description).
3. Remove the now-unused `save_ticker_evidence` name from the lazy import line
   (`from orchestrator.persistence import save_analyst_evidence, save_ticker_evidence` →
   `from orchestrator.persistence import save_analyst_evidence`).

Leave the `AnalystEvidence` loop, the `resolve_as_of` timestamp resolution, and the final
`self.db_session.commit()` untouched.

- [ ] **Step 4: Add the TickerEvidence loop to `decision_writer.py`**

In `src/agents/strategist/decision_writer.py::_run_async_impl`, restructure so ticker-
evidence is written every tick (it does not depend on a decision existing) and stances only
when a decision exists. Replace the body from the `state = ctx.session.state` line down to
the final `self.db_session.commit()` with:

```python
        state = ctx.session.state

        # Lazy imports keep the module importable without ADK/ORM in tests.
        from agents.strategist.schema import StrategistDecision
        from orchestrator.persistence import save_ticker_evidence, save_ticker_stance

        # Timestamp shared across every row written this invocation.  Prefer
        # state["as_of"] (the backtest replay clock) so replay is deterministic;
        # fall back to wall-clock only on live runs.
        recorded_at = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="decision_writer",
        )
        tick_id: str = state.get("tick_id", "unknown")

        # ── TickerEvidence persistence (moved here from the pre-strategist
        # EvidenceWriter, which ran BEFORE the context shim produced this key and
        # so silently wrote zero rows — the first-month-5 silent-degradation bug).
        # The shim always emits one dump per watchlist ticker, so an empty list
        # while the watchlist is non-empty means the aggregate never reached us:
        # raise loudly rather than no-op (loud-failure convention).
        ticker_evidence_objects = state.get("temp:ticker_evidence_objects", []) or []
        tickers: list[str] = state.get("tickers", []) or []

        if tickers and not ticker_evidence_objects:
            raise ValueError(
                "decision_writer: temp:ticker_evidence_objects is empty but the "
                f"watchlist has {len(tickers)} ticker(s) — the strategist context "
                "shim did not produce the aggregate. Refusing to silently drop "
                "ticker_evidence rows."
            )

        for te in ticker_evidence_objects:
            # Accept dicts (post-JSON state round-trip) or model instances.
            te_dict = te if isinstance(te, dict) else te.model_dump()
            save_ticker_evidence(
                self.db_session,
                tick_id=tick_id,
                ticker=te_dict["ticker"],
                aggregate=te_dict["aggregate"],
                weights=te_dict.get("weights", {}),
                # len(per_analyst) = number of analysts aggregated into this row.
                analyst_count=len(te_dict.get("per_analyst", {})),
                recorded_at=recorded_at,
            )

        # ── Stance persistence — only when a decision was emitted this tick.
        raw_decision = state.get("strategist_decision")
        if raw_decision:
            if isinstance(raw_decision, StrategistDecision):
                decision = raw_decision
            else:
                decision = StrategistDecision.model_validate(raw_decision)

            for stance in decision.stances:
                # intent=None is rejected upstream by derive_decision_fields; the
                # "update" fallback is an unreachable safety net.
                action = stance.intent or "update"
                save_ticker_stance(
                    self.db_session,
                    tick_id=tick_id,
                    decision_tag=decision.decision_tag,
                    recorded_at=recorded_at,
                    stance=stance.model_dump(mode="json"),
                    lifecycle_action=action,
                )

        self.db_session.commit()
        return
        yield  # required to make this a generator function
```

Keep the `db_session is None` short-circuit at the top of the method unchanged. Remove the
now-superseded standalone `raw_as_of`/`recorded_at` block and the old
`if not raw_decision: return` early-exit (its logic is now the `if raw_decision:` guard).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_decision_writer.py tests/unit/agents/contract/test_evidence_writer.py -v`
Expected: PASS. Then run the full suite to catch fixture fallout:
`.venv/bin/python -m pytest tests/ -q` and `.venv/bin/python -m ruff check src/`.

- [ ] **Step 6: Commit**

```bash
git add src/agents/contract/evidence_writer.py src/agents/strategist/decision_writer.py tests/unit/agents/contract/test_evidence_writer.py tests/unit/agents/strategist/test_decision_writer.py
git commit -m "fix(strategist): move TickerEvidence persistence post-strategist and raise on empty

The pre-strategist EvidenceWriter ran before the context shim produced
temp:ticker_evidence_objects, silently writing zero ticker_evidence rows.
Ownership moves to the post-strategist decision writer; an empty list with a
non-empty watchlist now raises.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## PHASE A — Technical composite lean

Replaces the contrarian 5-day reversal lean (Plan 3b) with a config-weighted trend/momentum
composite anchored to the 200-day-MA state-flip date. Evidence base: Moskowitz/Ooi/Pedersen
2012 (TSMOM), George & Hwang 2004 (52w-high anchoring), Faber 2007 / Brock et al. 1992 (200d
MA state), Barroso & Santa-Clara 2015 (vol-scaled momentum).

### Task 2: Rework the technical heuristics config for the composite

**Files:**
- Modify: `src/agents/analysts/heuristics.py` (`TechnicalHeuristics`)
- Modify: `config/analyst_heuristics.json` (`technical` block)
- Modify: `config/README.md`
- Test: `tests/unit/test_analyst_heuristics.py`

**Interfaces:**
- Produces the field surface consumed by Tasks 3–5: `trend_weight`, `anchor_52w_weight`,
  `rel_strength_weight` (composite votes, must sum to 1.0), `composite_neutral_band`,
  `horizon_days` (60), plus the **retained** knobs `near_52w_extreme_pct`,
  `vol_regime_window`, `vol_regime_extreme_z`, `vol_ratio_breakout`, `vol_ratio_dry_up`,
  `magnitude_cap`, `beta_confidence_damping_enabled`.

**Retirement (no orphaned artefacts):** the reversal knobs have no consumer once
`derive_technical_verdict` is rewritten (Task 4). Remove from schema, JSON, and README:
`reversal_neutral_band_pct`, `reversal_magnitude_scale`, `reversal_confidence_base`,
`reversal_horizon_days`.

- [ ] **Step 1: Write the failing test**

Replace the `TechnicalHeuristics` assertions in `tests/unit/test_analyst_heuristics.py`
with the composite surface:

```python
def test_technical_heuristics_composite_surface():
    """The technical config exposes composite-weight knobs and drops reversal ones."""
    h = load_heuristics().technical

    # Composite vote weights — sum to 1.0.
    assert 0.0 <= h.trend_weight <= 1.0
    assert 0.0 <= h.anchor_52w_weight <= 1.0
    assert 0.0 <= h.rel_strength_weight <= 1.0
    assert abs((h.trend_weight + h.anchor_52w_weight + h.rel_strength_weight) - 1.0) < 1e-9

    assert 0.0 <= h.composite_neutral_band <= 1.0
    assert 1 <= h.horizon_days <= 252

    # Retained context knobs.
    assert h.near_52w_extreme_pct > 0.0
    assert 2 <= h.vol_regime_window <= 252
    assert h.vol_regime_extreme_z > 0.0
    assert 0.0 < h.magnitude_cap <= 1.0

    # Retired reversal knobs are gone (extra="forbid" rejects them in JSON too).
    for dead in (
        "reversal_neutral_band_pct", "reversal_magnitude_scale",
        "reversal_confidence_base", "reversal_horizon_days",
    ):
        assert not hasattr(h, dead), f"retired field still present: {dead}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py::test_technical_heuristics_composite_surface -v`
Expected: FAIL — new fields absent, reversal fields still present.

- [ ] **Step 3: Rewrite the `TechnicalHeuristics` class**

In `src/agents/analysts/heuristics.py`, replace the reversal-block fields (the four
`reversal_*` Fields with their docstrings) with the composite block, and add a
`model_validator` enforcing the weight sum. Keep the Read-2 (`vol_regime_*`) and retained
context blocks unchanged.

```python
class TechnicalHeuristics(_Frozen):
    """Thresholds for the deterministic technical verdict (trend/momentum composite).

    The verdict's lean/magnitude/confidence come from a config-weighted vote of
    three literature-backed reads: the 200-day-MA trend state, 52-week-extreme
    anchoring, and 20-day relative strength vs SPY.  The volatility-regime read
    damps confidence but never votes on the lean (Barroso & Santa-Clara 2015).
    """

    # ── Composite vote weights (must sum to 1.0) ────────────────────────────
    # HIGH-VALUE TUNING KNOB: these weights and horizon_days below are the
    # primary lever on the technical lean.  Raising trend_weight makes the
    # analyst more trend-following (slower, fewer flips); raising
    # rel_strength_weight makes it more cross-sectional-momentum.  The
    # scoreboard forward-return sweep is the intended tuner (spec Validation).
    trend_weight: float        = Field(ge=0.0, le=1.0)
    """Weight on the 200-day-MA trend vote (+1 above / -1 below; crosses corroborate)."""

    anchor_52w_weight: float   = Field(ge=0.0, le=1.0)
    """Weight on the 52-week-anchor vote (+1 near high / -1 near low / 0 otherwise)."""

    rel_strength_weight: float = Field(ge=0.0, le=1.0)
    """Weight on the 20-day relative-strength-vs-SPY vote (sign; sector tiebreak)."""

    composite_neutral_band: float = Field(ge=0.0, le=1.0)
    """|weighted score| at or below which the lean collapses to neutral."""

    horizon_days: int = Field(ge=1, le=252)
    """Trading-day horizon the composite trend read targets (literature-informed 60).

    Written onto ``AnalystVerdict.horizon_days`` by ``derive_technical_verdict``.
    The scoreboard forward-return sweep (20/40/60/90) sets the final value.
    """

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "TechnicalHeuristics":
        """Reject a mis-specified vote so magnitudes stay interpretable in [0,1]."""
        total = self.trend_weight + self.anchor_52w_weight + self.rel_strength_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"technical composite weights must sum to 1.0; got {total:.6f}"
            )
        return self
```

Add `model_validator` to the pydantic import at the top of the file if not already present.

- [ ] **Step 4: Rewrite the `technical` block in `config/analyst_heuristics.json`**

```json
  "technical": {
    "trend_weight": 0.50,
    "anchor_52w_weight": 0.25,
    "rel_strength_weight": 0.25,
    "composite_neutral_band": 0.10,
    "horizon_days": 60,
    "vol_regime_window": 60,
    "vol_regime_extreme_z": 1.5,
    "vol_ratio_breakout": 1.3,
    "vol_ratio_dry_up": 0.7,
    "near_52w_extreme_pct": 5.0,
    "magnitude_cap": 1.0,
    "beta_confidence_damping_enabled": false
  },
```

- [ ] **Step 5: Update `config/README.md`**

In the `analyst_heuristics.json` technical section, delete the four `reversal_*` rows and add:

```markdown
| `technical.trend_weight` | float [0–1] | Weight on the 200d-MA trend vote in the composite lean (+1 above / −1 below MA200; golden/death cross corroborates). With `anchor_52w_weight` + `rel_strength_weight` must sum to 1.0. **High-value tuning knob.** Default **0.50**. |
| `technical.anchor_52w_weight` | float [0–1] | Weight on the 52-week-anchor vote (+1 within `near_52w_extreme_pct` of the high, −1 near the low, else 0) — George & Hwang 2004. Default **0.25**. |
| `technical.rel_strength_weight` | float [0–1] | Weight on the 20d relative-strength-vs-SPY vote (sign; sector ETF as tiebreak). Default **0.25**. |
| `technical.composite_neutral_band` | float [0–1] | `abs(weighted score)` at/below which the lean collapses to neutral. Default **0.10**. |
| `technical.horizon_days` | int [1–252] | Trading-day horizon the composite targets, written onto `AnalystVerdict.horizon_days`. Literature-informed start; scoreboard sweep decides the final value. **High-value tuning knob.** Default **60**. |
```

Confirm the retained-knob rows (`vol_regime_window`, `vol_regime_extreme_z`,
`vol_ratio_breakout`, `vol_ratio_dry_up`, `near_52w_extreme_pct`, `magnitude_cap`,
`beta_confidence_damping_enabled`) still exist; leave them.

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py -v`
Expected: PASS. (Task 4 rewrites `test_derive_technical_verdict.py`; do not run the whole
suite green here — scope to this file.)

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/heuristics.py config/analyst_heuristics.json config/README.md tests/unit/test_analyst_heuristics.py
git commit -m "feat(technical): replace reversal knobs with composite-weight config

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Extractor — emit the 200-day-MA state and its flip anchor

**Files:**
- Modify: `src/contract/extractors/technical.py` (`_KEYS`, `_zero_features`,
  `extract_technical_features`)
- Test: `tests/unit/contract/extractors/test_technical.py`

**Interfaces:**
- Produces two new nullable feature keys, computed only when ≥ 200 bars are present:
  - `ma200_state: float` — `+1.0` when the latest close is at/above its 200-day simple
    moving average, `-1.0` when below.
  - `ma200_flip_days: float` — number of sessions since the most recent `ma200_state`
    crossing (the anchor for P2's "above 200d MA since <date>" render). `0.0` on the flip
    session itself.
  Both consumed by Task 4 (composite magnitude/anchor) and Task 5 (render). `trend_state`
  (continuous distance, from Plan 3b) is retained and still drives the trend *vote* sign.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/contract/extractors/test_technical.py` (reuse the `_ramp_bars` helper
already in the file; if absent, add a simple oldest-first OHLCV ramp builder):

```python
def test_ma200_state_positive_on_uptrend():
    """A price above its rolling 200d SMA yields ma200_state = +1.0."""
    raw = {"bars": _ramp_bars(260, start=50.0, step=0.5)}   # steady climb → last > MA200
    feats = extract_technical_features(raw, "TEST")
    assert feats["ma200_state"] == 1.0
    assert "ma200_flip_days" in feats
    assert feats["ma200_flip_days"] >= 0.0


def test_ma200_state_absent_without_enough_history():
    """Fewer than 200 bars → both MA200 anchor keys are omitted (nullable convention)."""
    raw = {"bars": _ramp_bars(120)}
    feats = extract_technical_features(raw, "TEST")
    assert "ma200_state" not in feats
    assert "ma200_flip_days" not in feats


def test_ma200_flip_days_counts_sessions_since_the_last_cross():
    """A series that dips below then recovers above MA200 reports a small flip age."""
    # 220 rising bars, then a sharp late dip that pushes the last close under MA200,
    # then a 3-session recovery back above — flip age should be the recovery length.
    bars = _ramp_bars(220, start=50.0, step=0.5)
    dip = [dict(b, close=b["close"] - 40.0, high=b["high"] - 40.0, low=b["low"] - 40.0)
           for b in _ramp_bars(6, start=155.0, step=0.0)]
    recover = [dict(b, close=200.0, high=200.5, low=199.5) for b in _ramp_bars(3)]
    feats = extract_technical_features({"bars": bars + dip + recover}, "TEST")
    assert feats["ma200_state"] == 1.0
    assert 0.0 <= feats["ma200_flip_days"] <= 5.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -k "ma200" -v`
Expected: FAIL — keys not emitted.

- [ ] **Step 3: Register the keys**

In `src/contract/extractors/technical.py`, extend the `_KEYS` tuple (after the
`"trend_state",` entry from Plan 3b):

```python
    # Plan 3c composite-anchor additions (nullable — need >=200 bars):
    "ma200_state",       # +1.0 above / -1.0 below the 200-day SMA (trend vote corroborator)
    "ma200_flip_days",   # sessions since the last ma200_state crossing (P2 anchor)
```

Add both to the `_NULLABLE` set in `_zero_features` so they are never seeded to `0.0`.

- [ ] **Step 4: Compute the anchor in `extract_technical_features`**

Add a block after the pct-change windows are computed (needs the full `close` series). Use a
rolling 200-bar SMA computed elementwise so the flip age can be found:

```python
    # --- 200-day MA state + flip anchor (Plan 3c) ---------------------------
    # A boolean above/below series over a rolling 200-bar simple MA; the anchor
    # is the number of sessions since that above/below state last changed.  Both
    # keys stay absent below 200 bars (nullable convention → renderer skips).
    if len(close) >= 200:
        sma200 = close.rolling(window=200).mean()

        # Elementwise sign of (close - MA200) over the valid (non-NaN) tail.
        # +1 at/above the MA, -1 below.  numpy where keeps it branch-free.
        valid_mask = sma200.notna().to_numpy()
        above = np.where(
            close.to_numpy(dtype=float)[valid_mask]
            >= sma200.to_numpy(dtype=float)[valid_mask],
            1.0, -1.0,
        )

        if len(above) > 0:
            out["ma200_state"] = float(above[-1])

            # Walk back from the last session while the state is unchanged; the
            # count of unchanged prior sessions is the flip age (0 on the flip
            # session itself).
            flip_age = 0
            for prev in reversed(above[:-1]):
                if prev == above[-1]:
                    flip_age += 1
                else:
                    break
            out["ma200_flip_days"] = float(flip_age)
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -v`
Expected: PASS for the new tests; pre-existing feature tests unaffected (new keys are
absent unless computable, so exact-key-set assertions on short series still hold). Lint:
`.venv/bin/python -m ruff check src/contract/extractors/technical.py`.

- [ ] **Step 6: Commit**

```bash
git add src/contract/extractors/technical.py tests/unit/contract/extractors/test_technical.py
git commit -m "feat(technical): emit ma200_state and ma200_flip_days anchor features

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Rewrite `derive_technical_verdict` as the weighted composite

**Files:**
- Modify: `src/contract/extractors/technical.py` (`derive_technical_verdict`)
- Test: `tests/unit/test_derive_technical_verdict.py`

**Interfaces:**
- Consumes: `TechnicalHeuristics` composite surface (Task 2), and features `trend_state`,
  `ma200_state`, `golden_cross`/`death_cross`, `dist_from_high_52w_pct` /
  `dist_from_low_52w_pct`, `relative_strength_vs_spy_20d` (and
  `relative_strength_vs_sector_20d` as tiebreak), `vol_regime_z`, `pct_change_20d`.
- Produces: an `AnalystVerdict` whose lean/magnitude/confidence come from the composite,
  `horizon_days = h.horizon_days`, and `key_factors` carrying the new tag vocabulary
  `trend_follow_up`/`trend_follow_down`, `anchor_52w_high`/`anchor_52w_low`,
  `rel_strength_confirm`/`rel_strength_diverge` (replacing `reversal_up_fade`/
  `reversal_down_bounce`/`reversal_neutral`), plus retained context tags
  (`vol_regime_extreme`, `trend_above_ma200`/`trend_below_ma200`, `vol_breakout`/
  `vol_dry_up`, `near_52w_high`/`near_52w_low`, `golden_cross`/`death_cross`).

- [ ] **Step 1: Rewrite the test module**

Replace the body of `tests/unit/test_derive_technical_verdict.py` with a composite-focused
suite. Update the `_tech()` factory to the new field surface:

```python
"""Table-driven tests for the trend/momentum composite technical verdict."""
from __future__ import annotations

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _tech(**overrides) -> TechnicalHeuristics:
    base = dict(
        trend_weight=0.50, anchor_52w_weight=0.25, rel_strength_weight=0.25,
        composite_neutral_band=0.10, horizon_days=60,
        vol_regime_window=60, vol_regime_extreme_z=1.5,
        vol_ratio_breakout=1.3, vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0, magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )
    base.update(overrides)
    return TechnicalHeuristics(**base)


def _feats(**overrides) -> dict:
    # Non-no-data baseline (dodges the no-data fingerprint).
    feats = {"rsi_14": 55.0, "atr_pct_14": 2.0, "pct_change_5d": 0.0, "pct_change_20d": 0.01}
    feats.update(overrides)
    return feats


def test_all_three_agree_bullish():
    """Above MA200 + near 52w high + positive rel-strength → high-confidence bullish."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03),
        _tech(),
    )
    assert v.lean == "bullish"
    assert "trend_follow_up" in v.key_factors
    assert "anchor_52w_high" in v.key_factors
    assert "rel_strength_confirm" in v.key_factors
    assert v.confidence >= 0.8            # 3/3 agreement


def test_all_three_agree_bearish():
    """Below MA200 + near 52w low + negative rel-strength → bearish."""
    v = derive_technical_verdict(
        _feats(trend_state=-0.08, ma200_state=-1.0, dist_from_low_52w_pct=1.0,
               relative_strength_vs_spy_20d=-0.03),
        _tech(),
    )
    assert v.lean == "bearish"
    assert "trend_follow_down" in v.key_factors


def test_split_vote_inside_band_is_neutral():
    """Trend up but rel-strength down and no 52w anchor → score inside band → neutral."""
    v = derive_technical_verdict(
        _feats(trend_state=0.02, ma200_state=1.0, relative_strength_vs_spy_20d=-0.03),
        _tech(),
    )
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0


def test_trend_dominates_by_weight():
    """Trend (0.50) outvotes a lone opposing rel-strength (0.25) → follows trend."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=-0.01),
        _tech(),
    )
    assert v.lean == "bullish"


def test_magnitude_capped():
    """Magnitude never exceeds magnitude_cap."""
    v = derive_technical_verdict(
        _feats(trend_state=0.9, ma200_state=1.0, dist_from_high_52w_pct=-0.5,
               relative_strength_vs_spy_20d=0.5),
        _tech(magnitude_cap=0.6),
    )
    assert v.magnitude <= 0.6


def test_vol_regime_damps_confidence_not_lean():
    """A stressed vol regime lowers confidence but never flips the lean."""
    calm = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03, vol_regime_z=0.0),
        _tech(),
    )
    stressed = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03, vol_regime_z=3.0),
        _tech(),
    )
    assert stressed.lean == calm.lean == "bullish"
    assert stressed.confidence < calm.confidence
    assert "vol_regime_extreme" in stressed.key_factors


def test_horizon_days_from_config():
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=0.03),
        _tech(horizon_days=40),
    )
    assert v.horizon_days == 40


def test_no_data_fingerprint_still_fires():
    v = derive_technical_verdict(
        {"rsi_14": 0.0, "atr_pct_14": 0.0, "pct_change_5d": 0.0}, _tech(),
    )
    assert v.is_no_data is True


def test_reversal_tags_are_gone():
    """The retired reversal vocabulary must never appear."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=0.03),
        _tech(),
    )
    for dead in ("reversal_up_fade", "reversal_down_bounce", "reversal_neutral"):
        assert dead not in v.key_factors
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py -v`
Expected: FAIL — current code emits reversal leans/tags.

- [ ] **Step 3: Replace the composite core of `derive_technical_verdict`**

Keep the no-data fingerprint block unchanged. Replace everything from `factors: list[str] =
[]` through the READ-3 trend-state tag block with the composite. Retain the vol_ratio /
52w-proximity / crossover context tags below it, and the final `AnalystVerdict(...)`
construction, updating `horizon_days=h.horizon_days`:

```python
    factors: list[str] = []

    # === Composite lean — config-weighted vote of three literature reads ======
    # HIGH-VALUE TUNING KNOB: the weights live in config (Task 2); this function
    # only combines the votes.  Each vote is in {-1, 0, +1}; the weighted score
    # is in [-1, +1]; the lean is its sign outside composite_neutral_band.

    # Vote 1 — trend vs the 200-day MA (George & Hwang / Faber).  Prefer the
    # discrete ma200_state anchor when present; fall back to the continuous
    # trend_state sign.  Golden/death cross corroborates (does not add a vote).
    trend = features.get("trend_state")
    ma_state = features.get("ma200_state")
    if ma_state is not None:
        trend_vote = 1.0 if ma_state >= 0 else -1.0
    elif trend is not None:
        trend_vote = 1.0 if trend >= 0 else -1.0
    else:
        trend_vote = 0.0

    if trend_vote > 0:
        factors.append("trend_follow_up")
    elif trend_vote < 0:
        factors.append("trend_follow_down")

    # Vote 2 — 52-week anchoring.  Near the high is bullish, near the low bearish
    # (George & Hwang 2004: the 52w-high anchor dominates past-return momentum).
    dist_high = features.get("dist_from_high_52w_pct")
    dist_low  = features.get("dist_from_low_52w_pct")
    anchor_vote = 0.0
    if dist_high is not None and abs(dist_high) <= h.near_52w_extreme_pct:
        anchor_vote = 1.0
        factors.append("anchor_52w_high")
    elif dist_low is not None and dist_low <= h.near_52w_extreme_pct:
        anchor_vote = -1.0
        factors.append("anchor_52w_low")

    # Vote 3 — 20-day relative strength vs SPY; sector ETF breaks a zero.
    rel = features.get("relative_strength_vs_spy_20d")
    if rel is None or rel == 0.0:
        rel = features.get("relative_strength_vs_sector_20d")
    if rel is not None and rel != 0.0:
        rel_vote = 1.0 if rel > 0 else -1.0
        # "confirm" when it agrees with the trend vote, "diverge" otherwise —
        # gives the strategist a one-glance read of internal agreement.
        if trend_vote != 0.0 and rel_vote == trend_vote:
            factors.append("rel_strength_confirm")
        else:
            factors.append("rel_strength_diverge")
    else:
        rel_vote = 0.0

    score = (
        h.trend_weight * trend_vote
        + h.anchor_52w_weight * anchor_vote
        + h.rel_strength_weight * rel_vote
    )

    if score > h.composite_neutral_band:
        lean = "bullish"
    elif score < -h.composite_neutral_band:
        lean = "bearish"
    else:
        lean = "neutral"

    # Magnitude scales with |score| and the strength of the continuous trend /
    # relative-strength inputs, capped.  A neutral read carries no magnitude.
    if lean == "neutral":
        magnitude = 0.0
        confidence = 0.0
    else:
        strength = abs(score)
        # Blend in continuous input strength so a 3/3 vote on a large dislocation
        # reads bigger than a 3/3 vote on a marginal one.
        cont = min(abs(trend or 0.0) * 2.0, 0.5) + min(abs(rel or 0.0) * 5.0, 0.5)
        magnitude = min((strength + cont) / 2.0, h.magnitude_cap)

        # Confidence = component agreement × volatility damping (Barroso &
        # Santa-Clara 2015).  Agreement counts how many non-zero votes share the
        # lean's sign; 3/3 → 1.0, 2/3 → ~0.67, 1/1 → 1.0 of the votes cast.
        votes = [v for v in (trend_vote, anchor_vote, rel_vote) if v != 0.0]
        lean_sign = 1.0 if lean == "bullish" else -1.0
        agreeing = sum(1 for v in votes if (v > 0) == (lean_sign > 0))
        agreement = agreeing / len(votes) if votes else 0.0

        vol_z = features.get("vol_regime_z")
        damping = 1.0 / (1.0 + max(0.0, vol_z)) if vol_z is not None else 1.0

        confidence = max(0.0, min(1.0, agreement * damping))

    # Volatility-regime risk tag (fires at either tail; does NOT vote).
    vol_z = features.get("vol_regime_z")
    if vol_z is not None and abs(vol_z) >= h.vol_regime_extreme_z:
        factors.append("vol_regime_extreme")

    # Trend-state regime tag (context; the vote above already used it).
    if trend is not None:
        factors.append("trend_above_ma200" if trend >= 0 else "trend_below_ma200")
```

Below this, keep the existing `vol_ratio` breakout/dry-up, `near_52w_high`/`near_52w_low`
(these context tags may duplicate the anchor tags — that is fine, they render as context),
and `golden_cross`/`death_cross` blocks unchanged, then the rationale line and the return,
with `horizon_days=h.horizon_days`.

> Note the local `dist_high`/`dist_low` are now fetched with `.get()` returning `None`;
> update the trailing context-tag block that previously used
> `features.get("dist_from_high_52w_pct", -100.0)` to guard `None` before comparison, or
> reuse the `dist_high`/`dist_low` locals defined above.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py tests/unit/contract/extractors/test_technical.py tests/unit/test_analyst_heuristics.py -v`
Expected: PASS. Lint: `.venv/bin/python -m ruff check src/contract/extractors/technical.py`.

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/technical.py tests/unit/test_derive_technical_verdict.py
git commit -m "feat(technical): rewrite verdict as config-weighted trend/momentum composite

Lean is now sign(0.50*trend + 0.25*52w-anchor + 0.25*rel-strength) outside a
neutral band; confidence is component agreement times volatility damping;
horizon from technical.horizon_days. Reversal vocabulary retired.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Render the technical anchor + horizon in the strategist prompt

**Files:**
- Modify: `src/contract/strategist_prompt.py` (`_render_analyst` horizon line;
  `_HORIZON_PROSE`; the `TECHNICAL_BULLETS` / interpreters and the doc example tag list)
- Test: `tests/unit/contract/test_strategist_prompt_layout.py`

**Interfaces:**
- Consumes: `ma200_flip_days` feature + `horizon_days` on the verdict.
- Produces: a P2 anchor line under the technical block reading
  `above 200d MA since <date> (<n> sessions), day N of ~60` when a directional lean has an
  MA200 anchor; the horizon-prose gate for `technical` is updated to the composite horizon.

- [ ] **Step 1: Write the failing test**

```python
def test_technical_block_renders_ma200_anchor_and_horizon():
    """A directional technical lean renders the 200d-MA anchor with a session count."""
    from contract.strategist_prompt import render_ticker_block
    # Build a TickerEvidence whose technical verdict is bullish with ma200_flip_days=30
    # and horizon_days=60 (use the module's test factory / a hand-built TickerEvidence).
    te = _ticker_evidence_with_technical(
        lean="bullish", horizon_days=60,
        features={"ma200_state": 1.0, "ma200_flip_days": 30.0, "trend_state": 0.08},
        key_factors=["trend_follow_up", "trend_above_ma200"],
    )
    block = render_ticker_block(te)
    assert "200d MA" in block
    assert "30 sessions" in block
    assert "day" in block and "~60" in block
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py::test_technical_block_renders_ma200_anchor_and_horizon -v`
Expected: FAIL — anchor line not rendered.

- [ ] **Step 3: Update the horizon/anchor render in `_render_analyst`**

The horizon line (`strategist_prompt.py:729-732`) currently renders
`horizon: ~{h}d — {prose}` gated on a non-neutral lean. For `technical`, augment it with the
MA200 anchor derived from `ev.features["ma200_flip_days"]`. Add, immediately after the
existing horizon-line append:

```python
    # Plan 3c — P2 anchor: for a directional technical lean, show the 200d-MA
    # state and how many sessions it has held, plus where we sit in the horizon
    # window, so the strategist can tell "same signal, ageing" from "born today".
    if name == "technical" and v.lean != "neutral" and ev.features:
        flip_days = ev.features.get("ma200_flip_days")
        ma_state  = ev.features.get("ma200_state")
        if flip_days is not None and ma_state is not None:
            side = "above" if ma_state >= 0 else "below"
            sessions = int(flip_days)
            lines.append(
                f"  anchor: {side} 200d MA for {sessions} sessions "
                f"(day {sessions} of ~{v.horizon_days})"
            )
```

Update `_HORIZON_PROSE["technical"]` prose (and the `## Reading the technical reads and
analyst horizons` note at `agents/strategist/prompts.py:202-206`) so the quoted technical
horizon reads ~60 trading days (trend/momentum) rather than the retired ~5-day reversal.

- [ ] **Step 4: Sweep the stale doc example**

The `render_ticker_block` docstring example (`strategist_prompt.py:811-815`) still shows
`reversal_up_fade`. Update the example tag line to the composite vocabulary:
`-> Rationale tags: trend_follow_up, anchor_52w_high, rel_strength_confirm`.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: PASS. Update any layout snapshot test that pins the old reversal line.

- [ ] **Step 6: Commit**

```bash
git add src/contract/strategist_prompt.py src/agents/strategist/prompts.py tests/unit/contract/test_strategist_prompt_layout.py
git commit -m "feat(strategist): render technical 200d-MA anchor and composite horizon

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## PHASE B — Fundamental recalibration

Corrects the Lazy Prices doctrine to what the paper supports: sentiment-signed (not
bearish-by-default), trigger-rare, magnitude-capped, and anchored to the filing release date.

### Task 6: Fundamental config — trigger threshold, cap, decay, 8-K anchor list

**Files:**
- Modify: `config/analysts.json` (`fundamental` block)
- Modify: `src/config/analysts.py` (`FundamentalCaps` or equivalent model)
- Modify: `config/README.md`
- Test: `tests/unit/config/test_analysts_config.py`

**Interfaces:**
- Produces: `get_analysts_config().fundamental` fields
  `filing_delta_trigger_similarity` (float; delta fires only when the primary cosine
  similarity is at/below this — approximating the paper's bottom-quintile cut),
  `filing_delta_magnitude_cap` (float ≤ 1.0; deterministic clamp on filing-delta-driven
  magnitude absent a going-concern catalyst), `filing_delta_decay` (bool/float knob for
  linear magnitude decay past horizon exhaustion), and `thesis_breaking_8k_items`
  (list[str] of 8-K item codes that re-anchor the clock mid-quarter). Consumed by Tasks 7
  and 8.

- [ ] **Step 1: Write the failing test**

```python
def test_fundamental_recalibration_config():
    from config.analysts import get_analysts_config
    f = get_analysts_config().fundamental

    assert 0.0 <= f.filing_delta_trigger_similarity <= 1.0
    assert 0.0 < f.filing_delta_magnitude_cap <= 1.0
    assert isinstance(f.thesis_breaking_8k_items, list)
    assert f.thesis_breaking_8k_items                       # non-empty
    # 8-K item codes look like "5.02" (departures), "2.06" (impairment), etc.
    assert all(isinstance(x, str) for x in f.thesis_breaking_8k_items)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py::test_fundamental_recalibration_config -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add the fields to the model**

In `src/config/analysts.py`, add to the fundamental caps model (read the file to confirm the
class name; it mirrors `NewsCaps`), with docstrings and the tuning-knob marker on the 8-K
list:

```python
    # Filing-delta lean fires only when the primary lexical similarity to the
    # prior-year filing is at/below this — approximating the Lazy Prices bottom-
    # quintile "changer" cut on our watchlist.  Mid-range deltas are no-signal.
    filing_delta_trigger_similarity: float = Field(ge=0.0, le=1.0, default=0.85)

    # Deterministic cap on filing-delta-driven magnitude, absent a going-concern
    # catalyst.  Per-name Lazy Prices alpha is a weak tilt (18-58bps/mo L/S at
    # portfolio level) — the clamp stops the LLM rendering it as a fresh catalyst.
    filing_delta_magnitude_cap: float = Field(gt=0.0, le=1.0, default=0.4)

    # Whether filing-delta magnitude decays linearly toward neutral once the
    # drift window (filing_delta_horizon_days from the filing date) is exhausted.
    filing_delta_decay: bool = Field(default=True)

    # HIGH-VALUE TUNING KNOB: 8-K item codes that re-anchor the fundamental clock
    # mid-quarter (a fresh sign may be taken).  WIDENING re-admits sign churn;
    # NARROWING delays reaction to real thesis breaks.  Codes: 5.02 exec
    # departure, 2.06 material impairment, 4.02 non-reliance, 1.03 bankruptcy,
    # 2.04 accelerated debt, 3.01 delisting.
    thesis_breaking_8k_items: list[str] = Field(
        default_factory=lambda: ["1.03", "2.04", "2.06", "3.01", "4.02", "5.02"],
    )
```

- [ ] **Step 4: Add the values to `config/analysts.json`**

In the `fundamental` block (after `filing_delta_horizon_days`):

```json
    "filing_delta_trigger_similarity": 0.85,
    "filing_delta_magnitude_cap":      0.4,
    "filing_delta_decay":              true,
    "thesis_breaking_8k_items":        ["1.03", "2.04", "2.06", "3.01", "4.02", "5.02"],
```

- [ ] **Step 5: Update `config/README.md`**

Add rows to the analysts.json fundamental table:

```markdown
| `fundamental.filing_delta_trigger_similarity` | float [0–1] | The filing-delta lean fires only when the primary lexical cosine similarity to the prior-year filing is at/below this (large change ≈ Lazy Prices bottom quintile). Above it, the delta is no-signal. Default **0.85**. |
| `fundamental.filing_delta_magnitude_cap` | float (0–1] | Deterministic cap on filing-delta-driven verdict magnitude, absent a going-concern-tier catalyst. Default **0.4**. |
| `fundamental.filing_delta_decay` | bool | Whether filing-delta magnitude decays linearly toward neutral once `filing_delta_horizon_days` from the filing date is exhausted. Default **true**. |
| `fundamental.thesis_breaking_8k_items` | list[str] | 8-K item codes that re-anchor the fundamental clock mid-quarter (permit a fresh sign). **High-value tuning knob** — widening re-admits churn, narrowing delays real thesis breaks. Default `["1.03","2.04","2.06","3.01","4.02","5.02"]`. |
```

- [ ] **Step 6: Run to verify it passes, then commit**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v`
Expected: PASS.

```bash
git add config/analysts.json src/config/analysts.py config/README.md tests/unit/config/test_analysts_config.py
git commit -m "feat(fundamental): add filing-delta trigger/cap/decay and 8-K anchor config

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Rewrite the Lazy-Prices doctrine in the fundamental prompt

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py` (the sign-convention, "Forming the
  lean", and hard-rule stanzas ~lines 195-320)
- Test: `tests/unit/agents/analysts/fundamental/test_prompts.py` (assert the prompt text
  encodes the new doctrine; read the file for the existing render-function entry point)

This task edits prompt prose, not code logic. The going-concern override (R-tier, ~line 292)
and R1 (10b5-1 neutral) / R2 (boilerplate-not-evidence) hard rules are **retained
verbatim**. Change only the four doctrinal points the spec corrects.

- [ ] **Step 1: Write the failing test**

```python
def test_fundamental_prompt_is_sentiment_signed_not_bearish_default():
    from agents.analysts.fundamental.prompts import build_fundamental_prompt  # confirm name
    text = build_fundamental_prompt(_minimal_ticker_context())               # confirm signature

    # New doctrine present:
    assert "sentiment" in text.lower()
    assert "cap" in text.lower() and "0.4" in text
    # Old bearish-by-default doctrine gone:
    assert "BEARISH by default" not in text
    assert "score substantive\n  change as bearish" not in text
```

Read `prompts.py` to confirm the render-function name/signature before finalising the test.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_prompts.py -k sentiment -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite the four doctrinal stanzas**

1. **Sentiment-signed (replaces "BEARISH by default").** Rewrite the `THE SIGN CONVENTION
   (Lazy Prices)` block so substantive change is signed by the *sentiment* of the surviving
   language, not defaulted bearish. New framing: "A substantive year-over-year change is a
   SIGNAL whose direction follows the sentiment of what changed. Sharpened risk language,
   new legal proceedings, commitment downgrades, executive departures → bearish. Removed risk
   bullets, resolved litigation, upgraded commitment language, positive-tone additions →
   bullish (the paper's ~14% positive-sentiment changers predict significantly positive
   returns). Only when the change's sentiment is genuinely ambiguous does it default toward
   caution." Drop the super-majority ("unambiguously positive") test for the bullish branch.

2. **Trigger rarity.** Add to the "How to analyse" section: "The filing-delta lean only
   fires when the change is genuinely large — the `scale:` line flags this via
   `filing_delta_trigger_similarity`. Mid-range deltas (typical-or-less change for this
   firm) are NOT a filing-delta signal: lean on ratios/insiders/8-Ks alone, or neutral."

3. **Magnitude cap.** Add: "Filing-delta-driven magnitude is a WEAK per-name tilt (Lazy
   Prices L/S alpha is 18-58bps/month at the portfolio level). Cap filing-delta magnitude at
   ~0.4 unless a going-concern-tier catalyst is present. A deterministic clamp enforces this
   downstream, so do not exceed it." (The clamp lands in Task 8.)

4. **Long-only honesty.** Add to "Forming the lean": "In a long-only book the durable Lazy
   Prices edge is the short leg — the signal's main job is to AVOID/underweight changers. Its
   bullish side (non-changers) is weak and fast-reverting: keep bullish filing-delta calls
   low-magnitude."

Also replace the standalone "Genuine ABSENCE of change is quiet-bullish … ≤ 0.4" paragraph's
default-bearish framing with the sentiment-signed version, keeping the "performed comparison
required" marker-semantics guard.

- [ ] **Step 4: Run to verify it passes; commit**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/ -v`
Expected: PASS.

```bash
git add src/agents/analysts/fundamental/prompts.py tests/unit/agents/analysts/fundamental/test_prompts.py
git commit -m "feat(fundamental): recalibrate Lazy-Prices doctrine to sentiment-signed, capped, trigger-rare

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Deterministic magnitude clamp + filing-date anchor/decay

**Files:**
- Modify: `src/agents/analysts/fundamental/joiner.py` (post-LLM clamp)
- Modify: `src/contract/extractors/fundamental.py` (filing-anchor feature)
- Test: `tests/unit/agents/analysts/fundamental/test_joiner.py`,
  `tests/unit/contract/extractors/test_fundamental.py`

**Interfaces:**
- Consumes: `get_analysts_config().fundamental.filing_delta_magnitude_cap`,
  `filing_delta_decay`, `filing_delta_horizon_days`, and the going-concern tag
  (`going_concern:true` in `key_factors`) / the anchor filing's `filed`/`period_of_report`
  date.
- Produces: fundamental `AnalystEvidence` whose magnitude is clamped to the cap unless a
  going-concern catalyst is present, decayed linearly toward neutral past horizon
  exhaustion, and whose features carry `filing_anchor_days` (sessions since the anchor
  filing) for the render.

- [ ] **Step 1: Write the failing tests**

```python
# test_joiner.py
@pytest.mark.asyncio
async def test_fundamental_magnitude_clamped_absent_going_concern(monkeypatch):
    """An LLM magnitude above the cap is clamped when no going-concern tag is present."""
    verdict = await _run_joiner_with_llm_verdict(
        lean="bearish", magnitude=0.9, key_factors=["guidance_change:true"],
    )
    assert verdict.magnitude <= 0.4


@pytest.mark.asyncio
async def test_going_concern_bypasses_the_clamp():
    """Going-concern is a thesis-break catalyst — magnitude is NOT clamped."""
    verdict = await _run_joiner_with_llm_verdict(
        lean="bearish", magnitude=0.9, key_factors=["going_concern:true"],
    )
    assert verdict.magnitude > 0.4
```

```python
# test_fundamental.py
def test_filing_anchor_days_emitted_from_filed_date():
    raw = {"current_filings": [{"filed": "2025-08-01", "period_of_report": "20250630",
                                "form_type": "10-Q"}]}
    feats = extract_fundamental_features(raw, "TEST", as_of=date(2025, 9, 1))
    assert feats["filing_anchor_days"] >= 0.0
```

Confirm the raw-dict field names against `extract_fundamental_features` and the fundamental
fetch shapes before finalising.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_joiner.py tests/unit/contract/extractors/test_fundamental.py -k "clamp or going_concern or anchor" -v`
Expected: FAIL.

- [ ] **Step 3: Add the clamp in the fundamental joiner**

In `src/agents/analysts/fundamental/joiner.py`, after
`ticker_verdict = llm_v.to_ticker_verdict(horizon_days=...)`, clamp before building the
`AnalystVerdict`:

```python
                # Deterministic magnitude clamp (Lazy Prices is a weak per-name
                # tilt).  A going-concern-tier catalyst re-anchors the thesis and
                # bypasses the cap; everything else is bounded so the LLM cannot
                # render a filing delta as a fresh material catalyst.
                fcfg = get_analysts_config().fundamental
                tags = ticker_verdict.key_factors or []
                going_concern = any(t.startswith("going_concern") and t.endswith("true")
                                    for t in tags)
                if not going_concern and ticker_verdict.magnitude > fcfg.filing_delta_magnitude_cap:
                    ticker_verdict = ticker_verdict.model_copy(
                        update={"magnitude": fcfg.filing_delta_magnitude_cap},
                    )

                # Linear decay past horizon exhaustion (anchored to the filing
                # date via the extractor's filing_anchor_days feature).  Applied
                # after the clamp so a decayed cap is still a cap.
                if fcfg.filing_delta_decay:
                    anchor_days = (features or {}).get("filing_anchor_days")
                    horizon = fcfg.filing_delta_horizon_days
                    if anchor_days is not None and anchor_days > horizon:
                        # Fully decayed once one full horizon has elapsed past exhaustion.
                        overshoot = min((anchor_days - horizon) / horizon, 1.0)
                        decayed = ticker_verdict.magnitude * (1.0 - overshoot)
                        ticker_verdict = ticker_verdict.model_copy(
                            update={"magnitude": decayed},
                        )
```

(Extract `features` before this block if the joiner computes it after — reorder so the
extractor runs first. Read the joiner to place the block correctly relative to
`extract_fundamental_features`.)

- [ ] **Step 4: Emit `filing_anchor_days` in the extractor**

In `src/contract/extractors/fundamental.py::extract_fundamental_features`, compute
`filing_anchor_days` from the most recent periodic filing's `filed` date (fall back to
`period_of_report`), as sessions/days before `as_of`. Register it in `_KEYS` (nullable — omit
when no dated filing is present). Reuse the existing `_parse_dt` helper.

- [ ] **Step 5: Run to verify they pass; commit**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/ tests/unit/contract/extractors/test_fundamental.py -v`
Expected: PASS. Lint the two files.

```bash
git add src/agents/analysts/fundamental/joiner.py src/contract/extractors/fundamental.py tests/unit/agents/analysts/fundamental/test_joiner.py tests/unit/contract/extractors/test_fundamental.py
git commit -m "feat(fundamental): clamp filing-delta magnitude and emit filing anchor/decay

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## PHASE C — News last-fire record & abstain semantics

Adds the "downstream process" the news prompt already promises: a fired catalyst decays
smoothly across the drift window instead of self-zeroing next tick, and abstains stop
entering the digest as neutral votes.

### Task 9: Per-run last-fire store + reset discipline + joiner persist

**Files:**
- Create: `src/agents/analysts/news/last_fire.py`
- Modify: `src/backtest/driver.py` (reset per window, beside `reset_news_history_store()`)
- Modify: `src/agents/analysts/news/joiner.py` (record on directional verdicts)
- Test: `tests/unit/agents/analysts/news/test_last_fire.py`

**Interfaces (pinned — Task 10 consumes these):**

```python
@dataclass
class LastFire:
    lean: str          # "bullish" | "bearish"
    magnitude: float
    confidence: float
    fired_at: str      # ISO-8601 string (as_of discipline)

class NewsLastFireStore:
    def record(self, ticker: str, *, lean: str, magnitude: float,
               confidence: float, fired_at: str) -> None: ...
    def get(self, ticker: str) -> LastFire | None: ...

def get_news_last_fire_store() -> NewsLastFireStore: ...
def reset_news_last_fire_store() -> None: ...
```

- [ ] **Step 1: Write the failing tests**

```python
def test_record_and_get_roundtrip():
    from agents.analysts.news.last_fire import NewsLastFireStore
    store = NewsLastFireStore()
    store.record("STLD", lean="bullish", magnitude=0.7, confidence=0.8,
                 fired_at="2025-09-17T14:00:00")
    rec = store.get("STLD")
    assert rec.lean == "bullish" and rec.magnitude == 0.7
    assert rec.fired_at == "2025-09-17T14:00:00"


def test_new_fire_overwrites():
    from agents.analysts.news.last_fire import NewsLastFireStore
    store = NewsLastFireStore()
    store.record("STLD", lean="bullish", magnitude=0.7, confidence=0.8, fired_at="2025-09-17T14:00:00")
    store.record("STLD", lean="bearish", magnitude=0.5, confidence=0.6, fired_at="2025-09-25T14:00:00")
    assert store.get("STLD").lean == "bearish"


def test_reset_swaps_instance():
    from agents.analysts.news.last_fire import get_news_last_fire_store, reset_news_last_fire_store
    first = get_news_last_fire_store()
    reset_news_last_fire_store()
    assert get_news_last_fire_store() is not first
    reset_news_last_fire_store()


def test_get_absent_ticker_returns_none():
    from agents.analysts.news.last_fire import NewsLastFireStore
    assert NewsLastFireStore().get("NOPE") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_last_fire.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/agents/analysts/news/last_fire.py`**

Mirror `history.py`'s module-singleton + reset discipline. Store `fired_at` as an ISO string
(never a `datetime`) so nothing datetime-shaped can leak into ADK state via any downstream
dump. Full docstrings; comment the per-run/PIT lifecycle.

- [ ] **Step 4: Reset per window in the driver**

In `src/backtest/driver.py`, beside the existing `reset_news_history_store()` call
(line ~276), add `reset_news_last_fire_store()` with a one-line comment, and import it at
the top beside the history reset import.

- [ ] **Step 5: Record fires in the news joiner**

In `src/agents/analysts/news/joiner.py`, after `ticker_verdict` is built for the non-no-data
branch, record a fire whenever the lean is directional:

```python
                # Persist the fire so subsequent abstain ticks can carry a
                # decayed version of this catalyst (Task 10) instead of the
                # signal self-zeroing next tick.  fired_at is ISO-stringified
                # per the as_of state convention.
                if ticker_verdict.lean in ("bullish", "bearish"):
                    get_news_last_fire_store().record(
                        ticker,
                        lean=ticker_verdict.lean,
                        magnitude=ticker_verdict.magnitude,
                        confidence=ticker_verdict.confidence,
                        fired_at=recorded_at.isoformat(),
                    )
```

Import `get_news_last_fire_store` at the top of the joiner.

- [ ] **Step 6: Run to verify they pass; commit**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v`
Expected: PASS.

```bash
git add src/agents/analysts/news/last_fire.py src/backtest/driver.py src/agents/analysts/news/joiner.py tests/unit/agents/analysts/news/test_last_fire.py
git commit -m "feat(news): add per-run last-fire store and record directional verdicts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Numeric carried-signal synthesis in the context shim

**Files:**
- Modify: `src/agents/strategist/context_shim.py` (`_run_async_impl`, per-ticker loop)
- Test: `tests/unit/agents/strategist/test_context_shim.py`

**Interfaces:**
- Consumes: `NewsLastFireStore` (Task 9), the `carried` flag on `AnalystVerdict` (Task 11 —
  co-planned sibling; trust it lands and patch the field name in-pass if it differs).
- Produces: for a ticker whose news evidence is an abstain but a live last-fire record
  exists, a **synthetic decayed news `AnalystEvidence`** flagged `carried=True` substituted
  into `per_analyst["news"]` **before** `build_ticker_evidence` is called — so `digest.py`
  stays a pure function of the verdicts handed to it (spec *Decision 2026-07-21*).

**Sequencing note:** Task 11 adds the `abstain`/`carried` flags and the digest exclusion.
This task and Task 11 are co-planned — implement Task 11 first if the flags are not yet
present, or land them together. The synthesis reads `news_ev.verdict.abstain`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_abstain_news_carries_decayed_last_fire():
    """When news abstains but a live fire exists, the shim substitutes a decayed
    carried verdict before digesting — the aggregate does not cliff to neutral."""
    from agents.analysts.news.last_fire import get_news_last_fire_store, reset_news_last_fire_store
    reset_news_last_fire_store()
    # Fired 5 trading days ago, drift_horizon_days=20 → ~75% magnitude survives.
    get_news_last_fire_store().record(
        "STLD", lean="bullish", magnitude=0.80, confidence=0.80,
        fired_at="2025-09-17T14:00:00",
    )
    state = _shim_state_with_abstain_news("STLD", as_of="2025-09-24T14:00:00")

    shim = StrategistContextShim()
    delta = await _collect_delta(shim, state)

    te = _find_ticker_evidence(delta, "STLD")
    news = te["per_analyst"]["news"]
    assert news["verdict"]["carried"] is True
    assert news["verdict"]["lean"] == "bullish"
    assert 0.5 < news["verdict"]["magnitude"] < 0.80        # decayed, not zeroed
    reset_news_last_fire_store()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -k carried -v`
Expected: FAIL.

- [ ] **Step 3: Add the synthesis before the digest call**

In `context_shim._run_async_impl`, inside the per-ticker loop (after `per_analyst` is
assembled, before `build_ticker_evidence`), substitute a decayed carried verdict when the
news slot is an abstain and a live fire exists:

```python
            # Numeric news carry (spec Decision 2026-07-21): a fired catalyst
            # must decay smoothly across the drift window, not cliff to neutral
            # the tick after it fired (the STLD one-tick round-trip).  We
            # substitute the abstain with a synthetic decayed verdict BEFORE
            # digesting so digest.py stays a pure function of its inputs.
            news_ev = per_analyst.get("news")
            if news_ev is not None and getattr(news_ev.verdict, "abstain", False):
                carried = _carried_news_evidence(news_ev, recorded_at, tick_id)
                if carried is not None:
                    per_analyst["news"] = carried
```

Add the helper (module-level, in `context_shim.py`):

```python
def _carried_news_evidence(abstain_ev, recorded_at, tick_id):
    """Build a decayed, carried news AnalystEvidence from the last-fire record.

    HIGH-VALUE TUNING KNOB: the decay is linear over drift_horizon_days.  A
    slower decay holds catalysts longer (fewer re-entries); a faster one reverts
    to abstain sooner.  Returns None when no live fire exists for the ticker or
    the fire has fully decayed (past the horizon), in which case the original
    abstain stands and news simply is not a vote this tick (P4).

    Parameters
    ----------
    abstain_ev:
        The news AnalystEvidence marked abstain this tick (carries the ticker).
    recorded_at:
        The tick's resolved as_of datetime.
    tick_id:
        The current tick id, stamped onto the synthetic evidence.

    Returns
    -------
    AnalystEvidence | None
        A carried, decayed news evidence, or None to leave the abstain in place.
    """
    from agents.analysts.news.last_fire import get_news_last_fire_store
    from config.analysts import get_analysts_config
    from contract.evidence import AnalystEvidence, AnalystVerdict

    rec = get_news_last_fire_store().get(abstain_ev.ticker)
    if rec is None:
        return None

    fired_at = datetime.fromisoformat(rec.fired_at)
    horizon = get_analysts_config().news.drift_horizon_days
    elapsed_days = max(0, (recorded_at - fired_at).days)

    if elapsed_days >= horizon:
        return None                                   # fully decayed → not a vote

    decay = 1.0 - (elapsed_days / horizon)            # linear over the window
    magnitude = rec.magnitude * decay
    confidence = rec.confidence * decay

    verdict = AnalystVerdict(
        lean=rec.lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=(f"carried catalyst from {rec.fired_at[:10]} "
                   f"(day {elapsed_days}/{horizon}, decayed)"),
        key_factors=["carried"],
        is_no_data=False,
        carried=True,
    )
    return AnalystEvidence(
        analyst="news",
        ticker=abstain_ev.ticker,
        tick_id=tick_id,
        recorded_at=recorded_at,
        verdict=verdict,
        features=abstain_ev.features,
    )
```

- [ ] **Step 4: Run to verify it passes; commit**

Run: `.venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -v`
Expected: PASS.

```bash
git add src/agents/strategist/context_shim.py tests/unit/agents/strategist/test_context_shim.py
git commit -m "feat(strategist): carry decayed news catalyst into the digest as a synthetic verdict

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: `abstain`/`carried` flags, digest exclusion, STEP-3 wording

**Files:**
- Modify: `src/contract/evidence.py` (`AnalystVerdict` — add `abstain`, `carried`)
- Modify: `src/agents/analysts/news/joiner.py` (mark abstains)
- Modify: `src/contract/digest.py` (exclude abstains from aggregation)
- Modify: `src/agents/analysts/news/prompts.py` (STEP-3 wording)
- Test: `tests/unit/contract/test_digest.py`,
  `tests/unit/agents/analysts/news/test_joiner.py`, `tests/unit/contract/test_evidence.py`

**Interfaces:**
- Produces: `AnalystVerdict.abstain: bool = False` (a data-present "no view", distinct from
  `is_no_data`) and `AnalystVerdict.carried: bool = False` (provenance for the synthetic
  decayed news verdict). The digest treats `is_no_data OR abstain` as excluded from the
  weighted sum, mean confidence, disagreement, and the summary counts.

- [ ] **Step 1: Write the failing tests**

```python
# test_evidence.py
def test_analyst_verdict_abstain_and_carried_default_false():
    from contract.evidence import AnalystVerdict
    v = AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x")
    assert v.abstain is False and v.carried is False


# test_digest.py
def test_abstain_excluded_from_aggregate():
    """An abstaining analyst is not averaged in as a neutral vote (P4)."""
    from contract.digest import build_ticker_evidence
    per_analyst = {
        "technical":   _ev("technical", lean="bullish", confidence=0.8),
        "fundamental": _ev("fundamental", lean="bullish", confidence=0.8),
        "news":        _ev("news", lean="neutral", confidence=0.2, abstain=True),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t1", _NOW, {"technical":1.0,"fundamental":1.0,"news":1.0})
    # With news abstaining, the aggregate reflects only the two bullish analysts.
    assert te.aggregate.lean == "bullish"
    assert "1 neutral" not in te.aggregate.summary       # abstain not counted as neutral
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_evidence.py tests/unit/contract/test_digest.py -k "abstain or carried" -v`
Expected: FAIL.

- [ ] **Step 3: Add the flags to `AnalystVerdict`**

In `src/contract/evidence.py::AnalystVerdict`, after `is_no_data`:

```python
    # Data-present "no view" — distinct from is_no_data (no data at all).  The
    # news STEP-3 abstain sets this; the digest excludes it from the aggregate
    # so an abstain is not averaged in as a neutral vote (spec P4).
    abstain: bool = False

    # Provenance flag: True on the synthetic decayed news verdict the strategist
    # context shim substitutes for an abstain when a live last-fire record
    # exists (spec Decision 2026-07-21).  Renders as a "carried" tag.
    carried: bool = False
```

These are canonical-only fields; `LlmTickerVerdict` must NOT gain them (the LLM does not emit
them). Confirm `to_ticker_verdict` still round-trips (they default False, so it does).

- [ ] **Step 4: Mark abstains in the news joiner**

In `news/joiner.py`, when inflating a directional-or-neutral LLM verdict, set `abstain=True`
on the canonical `AnalystVerdict` when the news lean is neutral and it is not `is_no_data`
(STEP-3 "no fresh surprise today"):

```python
                verdict = AnalystVerdict.model_validate(
                    {k: v for k, v in ticker_verdict.model_dump().items() if k != "ticker"}
                )
                # A neutral, data-present news verdict is a STEP-3 abstain, not a
                # vote — mark it so the digest excludes it (P4).
                if verdict.lean == "neutral" and not verdict.is_no_data:
                    verdict = verdict.model_copy(update={"abstain": True})
```

- [ ] **Step 5: Exclude abstains in the digest**

In `src/contract/digest.py`, add a module-level predicate and use it in the four places that
currently test `ev.verdict.is_no_data` (`_weighted_signed_confidences`, `_disagreement`,
`_summary`, and the `_aggregate` mean-confidence comprehension):

```python
def _excluded(ev: AnalystEvidence | None) -> bool:
    """Whether an analyst's evidence is excluded from the aggregate.

    No-data (never had input) and abstain (had input, formed no view — spec P4)
    both count as "not a vote": excluded from the weighted sum, mean confidence,
    disagreement, and the bullish/neutral/bearish summary counts.
    """
    return ev is None or ev.verdict.is_no_data or ev.verdict.abstain
```

Replace each `if ev is None or ev.verdict.is_no_data:` / `if ev is not None and not
ev.verdict.is_no_data` guard with the `_excluded(ev)` equivalent. Note `_fill_missing` still
synthesises `is_no_data=True` fills — those remain excluded via `_excluded`.

- [ ] **Step 6: Update STEP-3 wording in the news prompt**

In `src/agents/analysts/news/prompts.py`, STEP 3 (line ~84) already says the neutral means
"no NEW news event today — absence of new information" and that the model is not tracking
prior catalysts. Tighten it to state that this neutral is an **abstain** (a "no view",
excluded from the combine), not a neutral vote — one sentence, keeping the "you are not
tracking a window" framing intact (the carry now happens numerically downstream in the
context shim, so the prompt stays honest that the *analyst* does not track it).

- [ ] **Step 7: Run to verify they pass; commit**

Run: `.venv/bin/python -m pytest tests/unit/contract/ tests/unit/agents/analysts/news/ -v`
Expected: PASS.

```bash
git add src/contract/evidence.py src/agents/analysts/news/joiner.py src/contract/digest.py src/agents/analysts/news/prompts.py tests/unit/contract/test_evidence.py tests/unit/contract/test_digest.py tests/unit/agents/analysts/news/test_joiner.py
git commit -m "feat(news): mark STEP-3 abstains and exclude them from the digest aggregate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## PHASE D — Aggregate load-bearing

### Task 12: Render the aggregate per ticker and make the dangling instruction true

**Files:**
- Modify: `src/contract/strategist_prompt.py` (`render_ticker_block` — add an aggregate
  header/footer)
- Modify: `src/agents/strategist/prompts.py` (the dangling instruction at line ~198)
- Test: `tests/unit/contract/test_strategist_prompt_layout.py`

**Interfaces:**
- Consumes: `TickerEvidence.aggregate` (lean, magnitude, confidence, disagreement, summary)
  and the `carried` provenance where a synthetic news verdict contributed.
- Produces: an aggregate line in every per-ticker block, making the strategist prompt's
  "Treat the digested aggregate as a deterministic input" instruction reference a number the
  template actually prints.

- [ ] **Step 1: Write the failing test**

```python
def test_ticker_block_renders_aggregate_line():
    from contract.strategist_prompt import render_ticker_block
    te = _ticker_evidence(aggregate_lean="bullish", aggregate_mag=0.42,
                          aggregate_conf=0.66, disagreement=0.10)
    block = render_ticker_block(te)
    assert "Aggregate" in block
    assert "bullish" in block
    assert "0.42" in block
    assert "disagreement" in block.lower()


def test_aggregate_line_flags_carried_news():
    from contract.strategist_prompt import render_ticker_block
    te = _ticker_evidence_with_carried_news()
    block = render_ticker_block(te)
    assert "carried" in block.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -k aggregate -v`
Expected: FAIL.

- [ ] **Step 3: Render the aggregate in `render_ticker_block`**

Add an aggregate summary line to the per-ticker block (after the per-analyst blocks, before
the divider). Read `render_ticker_block` for the exact assembly point:

```python
    agg = te.aggregate
    carried = any(
        ev.verdict.carried
        for ev in te.per_analyst.values()
    )
    carried_note = "  [news carried]" if carried else ""
    lines.append(
        f"[Aggregate]  lean: {agg.lean}  magnitude: {agg.magnitude:.2f}  "
        f"confidence: {agg.confidence:.2f}  disagreement: {agg.disagreement:.2f}"
        f"{carried_note}"
    )
    if agg.summary:
        lines.append(f"  {agg.summary}")
```

- [ ] **Step 4: Make the dangling instruction true**

The instruction at `agents/strategist/prompts.py:198` ("Treat the digested aggregate as a
deterministic input; you may disagree with it …") now references a rendered number. Keep the
"you may disagree" licence (guard-rail against over-anchoring). No wording change is required
beyond confirming it now points at the `[Aggregate]` line; add a short clause naming the line
if it aids clarity.

- [ ] **Step 5: Run to verify it passes; commit**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: PASS. Update any full-block snapshot test to include the aggregate line.

```bash
git add src/contract/strategist_prompt.py src/agents/strategist/prompts.py tests/unit/contract/test_strategist_prompt_layout.py
git commit -m "feat(strategist): render the digest aggregate per ticker with carried provenance

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Promote aggregate weights to `config/digest.json`

**Files:**
- Create: `config/digest.json`
- Modify: `src/contract/digest.py` (load weights from config)
- Modify: `config/README.md`
- Modify: `src/agents/strategist/context_shim.py` (consume the loaded weights)
- Test: `tests/unit/contract/test_digest.py`

**Interfaces:**
- Produces: `contract.digest.load_analyst_weights() -> dict[str, float]` reading
  `config/digest.json`, replacing the `DEFAULT_ANALYST_WEIGHTS` module constant (the existing
  comment in `digest.py` already anticipates this promotion). `context_shim` passes the
  loaded weights into `build_ticker_evidence` instead of importing the constant.

- [ ] **Step 1: Write the failing test**

```python
def test_load_analyst_weights_from_config():
    from contract.digest import load_analyst_weights
    w = load_analyst_weights()
    assert w == {"technical": 1.0, "fundamental": 1.0, "news": 1.0}
    assert not hasattr(__import__("contract.digest", fromlist=["x"]), "DEFAULT_ANALYST_WEIGHTS")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_digest.py -k weights -v`
Expected: FAIL.

- [ ] **Step 3: Create `config/digest.json`**

```json
{
  "analyst_weights": {
    "technical": 1.0,
    "fundamental": 1.0,
    "news": 1.0
  }
}
```

- [ ] **Step 4: Add the loader and delete the constant**

In `src/contract/digest.py`, replace the `DEFAULT_ANALYST_WEIGHTS` constant (and its long
explanatory comment about shelved analysts) with a cached loader. Keep the shelved-analyst
guidance as the loader docstring, and carry the tuning-knob marker:

```python
@lru_cache(maxsize=1)
def load_analyst_weights() -> dict[str, float]:
    """Per-analyst digest weights, read from ``config/digest.json``.

    HIGH-VALUE TUNING KNOB: these are the natural target of scoreboard-driven
    tuning (spec P6).  Only analysts BOTH wired into the pipeline AND consumed
    by the strategist context shim belong here — a phantom entry both spams
    missing_analyst_slot WARNINGs and dilutes every aggregate magnitude
    (denominator is sum of weights).  Shelved: smart_money, social (see git
    history for revival steps).
    """
    import json
    from pathlib import Path
    raw = json.loads(Path("config/digest.json").read_text())
    return dict(raw["analyst_weights"])
```

Update every import of `DEFAULT_ANALYST_WEIGHTS` (grep: `context_shim.py` line ~63 and its
call site line ~383) to call `load_analyst_weights()` instead. Confirm no other importer
remains: `grep -rn "DEFAULT_ANALYST_WEIGHTS" src/ tests/` must come back empty.

- [ ] **Step 5: Document in `config/README.md`**

Add a `config/digest.json` entry describing `analyst_weights` as the per-analyst digest
weights (high-value tuning knob), naming the shelved-analyst constraint.

- [ ] **Step 6: Run to verify it passes; commit**

Run: `.venv/bin/python -m pytest tests/unit/contract/test_digest.py tests/unit/agents/strategist/test_context_shim.py -v`
Expected: PASS. `grep -rn "DEFAULT_ANALYST_WEIGHTS" src/ tests/` → empty.

```bash
git add config/digest.json src/contract/digest.py src/agents/strategist/context_shim.py config/README.md tests/unit/contract/test_digest.py
git commit -m "feat(digest): promote analyst weights to config/digest.json

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: Score the aggregate as a pseudo-analyst + stance-vs-aggregate agreement

**Files:**
- Modify: `src/backtest/scoreboard.py` (join the aggregate as a pseudo-analyst row; add an
  agreement-rate metric)
- Test: `tests/unit/backtest/test_scoreboard.py`

**Interfaces:**
- Consumes: the `ticker_evidence` rows now persisted every tick (Task 1) and the
  `ticker_stances` rows.
- Produces: an `"aggregate"` analyst row in `ScoreboardResult.analysts`, scored on
  forward returns exactly like a real analyst (so "is the aggregator adding value?" is
  measurable), plus a per-run stance-vs-aggregate agreement rate (what fraction of strategist
  stances matched the aggregate lean — ~100% means the LLM layer is redundant above the
  combine).

- [ ] **Step 1: Write the failing tests**

```python
def test_aggregate_scored_as_pseudo_analyst(tmp_path):
    """The aggregate joins the scoreboard as its own analyst row."""
    db = _seed_run_db(tmp_path)                 # seeds analyst_evidence + ticker_evidence
    result = build_analyst_scoreboard(db, _cache(), horizons=[20, 60])
    assert "aggregate" in result.analysts


def test_stance_vs_aggregate_agreement_rate(tmp_path):
    """Agreement rate is the fraction of stances whose direction matched the aggregate."""
    db = _seed_run_db_with_stances(tmp_path)    # 3 of 4 stances agree with the aggregate lean
    rate = stance_vs_aggregate_agreement(db)
    assert abs(rate - 0.75) < 1e-9
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_scoreboard.py -k "aggregate or agreement" -v`
Expected: FAIL.

- [ ] **Step 3: Read `ticker_evidence` into the scoreboard**

`build_analyst_scoreboard` currently reads `analyst_evidence`. Add a pass that reads the
`ticker_evidence` rows' `aggregate` (lean/magnitude/confidence) and joins them under the
synthetic analyst name `"aggregate"`, scored on the same forward-return horizons as real
analysts. Default the aggregate's primary horizon to `max(horizons)` unless
`primary_horizon_by_analyst` names it. Reuse the existing per-analyst scoring machinery — the
aggregate is just another `(analyst, horizon, subset)` producer.

- [ ] **Step 4: Add the agreement-rate function**

Add a pure function `stance_vs_aggregate_agreement(db_path) -> float` that joins
`ticker_stances` to `ticker_evidence` on `(tick_id, ticker)` and returns the fraction of
stances whose direction (buy/add ≈ bullish, trim/exit ≈ bearish; map from `lifecycle_action`
/ stance intent) matched the aggregate lean. Document the mapping in the docstring. Surface
it in the run's `metrics.md` via `src/backtest/reporting.py` if that is where per-run metrics
are emitted (read `reporting.py`; add the line only if the metrics table lives there).

- [ ] **Step 5: Run to verify they pass; commit**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_scoreboard.py -v`
Expected: PASS.

```bash
git add src/backtest/scoreboard.py tests/unit/backtest/test_scoreboard.py
git commit -m "feat(backtest): score the digest aggregate as a pseudo-analyst with agreement rate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Acceptance criteria (spec Validation section)

These are the plan's definition of done. Run after all tasks land.

- [ ] **Full suite + lint green.** `.venv/bin/python -m pytest tests/ -q` and
  `.venv/bin/python -m ruff check src/`.

- [ ] **`ticker_evidence` row count == tickers × ticks** (positive-signal assertion, not
  just "no error") on the first-month re-run — the persistence fix (Task 1) proven at
  integration scale.

- [ ] **First-month re-run of `long-baseline-2025`.** Re-run the first month and compare
  before/after (reference run:
  `backtests/long-baseline-2025/runs/first-month-5/db.sqlite`) on the named cases:
  - **MPWR** — expect bullish technical (was pure sign(pct_change_5d)-flipped), capped
    fundamental, news fires persisting rather than self-zeroing.
  - **FSLR / STLD** — no more 29/29 bearish fundamental; the STLD-style one-tick round-trip
    (opened 09-17 on a fire, closed 09-18 when the signal self-zeroed) must not recur — the
    aggregate now decays smoothly (~0.70 → 0.66 → … → 0 across the window).
  - **DOV / FDS** — no more bounce/knife buys from the retired reversal lean.

- [ ] **Churn metrics before/after:** lean flips per ticker (technical was ~6.8×/ticker/month
  vs 200d-state ~0.8×), round-trip count, median holding period, decision_tag distribution.

- [ ] **Scoreboard forward-return sweep** (20/40/60/90) to empirically place
  `technical.horizon_days` once the composite lean exists; the aggregate pseudo-analyst
  scored in the same pass, plus the stance-vs-aggregate agreement rate.

- [ ] **Selection alpha vs the watchlist** is the success metric here — not absolute return.
  Watchlist weakness (−1.70% equal-weight vs SPY +5.8%) is a separate track (out of scope).

## Out of scope (sequenced next, per spec)

- Strategist deployment posture ("cash is an active bearish allocation") softening — measured
  after this package lands.
- Thesis-memory / horizon-holding machinery (old lever "C") — deferred; anchors + sticky
  signals are expected to remove most of its motivation. Revisit only if churn metrics say
  otherwise.
- Watchlist construction.

---

## Self-review notes (for the executor)

- **Task 10 ↔ Task 11 coupling:** the carried-signal synthesis (Task 10) reads
  `verdict.abstain` and writes `verdict.carried`, both added in Task 11. If executing
  strictly in order, land Task 11's schema flags first or land the two together; the tests in
  Task 10 assume the flags exist.
- **Task 1 raise vs empty watchlist:** the raise is gated on `tickers` being non-empty, so a
  degenerate empty-watchlist pipeline does not trip it.
- **LLM prompt tasks (7, 11 STEP-3):** these edit prompt prose; the tests assert the doctrine
  text is present/absent rather than pinning exact wording, so minor phrasing latitude is
  fine as long as the four corrections (sentiment-signing, trigger rarity, magnitude cap,
  long-only honesty) are unmistakably encoded.
