# Plan 3b — Technical Three-Reads Rebuild + Horizon Precursors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Follow each task in order. Every task is a
> self-contained TDD cycle: write the failing test, watch it fail, implement, watch it
> pass, commit. Do not skip the failing-test step. Do not batch commits across tasks. If a
> step's observed behaviour diverges from what the plan predicts, STOP and re-read the
> source file before improvising.

**Sequencing:** Lands *after* Plan 3 (news-drift rebuild) and Plan 1b (filing-similarity),
and *before* the held eval. It is a **pre-eval** plan: it attacks analyst churn at its root
cause (horizon-blindness) with *information* before any structural enforcement is
considered. There is deliberately **no enforced holding gate** in this plan — no new stance
field, no new thesis field, no risk-gate veto. Churn is to be **measured** after horizons
are visible, then re-assessed. Numbered `3b` for lineage — it revises the analyst *surface*
that Plans 1/1b/3 populate, without renumbering Plans 4–5.

**Goal:** (1) Rebuild the deterministic technical analyst into three independent,
literature-backed multi-day reads — a **short-term reversal** lean (the one directional
headline), a **volatility-regime** number, and a **trend-state** number — surfaced to the
strategist with no forced blending and no all-agree gate. (2) Stop every LLM analyst
self-reporting `horizon_days`; populate each analyst's horizon **deterministically from
config**. (3) Re-surface those horizons to the strategist as honest, mechanistic,
non-prescriptive prose — the primary root-cause fix for churn — alongside prose explaining
the three technical reads, plus a prompt-hygiene sweep.

**Architecture:** The technical path stays fully deterministic (TA-Lib features →
`derive_technical_verdict` → `AnalystVerdict`). The reversal read *inverts* the old
trend-following momentum sign: it leans **against** the recent 5-day move (Jegadeesh 1990;
Lehmann 1990), with a neutral band. Volatility-regime is a z-score of ATR% versus its own
trailing window (Moreira–Muir 2017; safe at daily cadence via volatility clustering —
Engle 1982 / Bollerslev 1986). Trend-state is the continuous `last/ma200 − 1`. Only the
reversal read drives the verdict's lean/magnitude/confidence; vol-regime and trend-state
are rendered feature numbers with explanatory interpreters, **not** blended into the lean.
Horizons stop being an LLM emit field: `LlmTickerVerdict` drops `horizon_days`,
`to_ticker_verdict(*, horizon_days=…)` injects the config value at the joiner boundary, and
the technical extractor sets it from a new config constant.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK, TA-Lib (`talib`), NumPy/pandas,
pytest.

## Global Constraints

Every task's requirements implicitly include this section.

- **British English everywhere** — code identifiers, comments, docs, prose (`behaviour`,
  `normalise`, `analyse`, `colour`).
- **Comment-heavy code** — every function gets a docstring (purpose, parameters, return
  value); non-trivial logic gets inline comments; blank lines between logical blocks.
- **Config convention** — every tunable lives in `config/*.json`; each addition/removal
  updates `config/README.md` in the *same* task. Never hardcode a config value in source.
- **Loud failures** — prefer raises over silent null/empty/neutral degradation; tests
  assert positive signals (the reversal fired with the right sign; the horizon was
  injected), not merely absence of errors.
- **No new strategist-facing schema** — this plan adds **no** field to `TickerStance`,
  `PositionThesis`, or `StrategistDecision`, and **no** risk-gate/`_verb_dispatch` veto.
  The churn fix is information (visible horizons + honest technical prose), not enforcement.
- **Backtest PIT rules** — every read of `state["as_of"]` goes through `resolve_as_of`; any
  datetime written to ADK state is ISO-stringified first. No task here writes ADK state
  directly; do not regress this.
- **Two-tier char-cap / no `max_length` on LLM prose** — do **not** add `max_length` to any
  field on `LlmTickerVerdict` / `AnalystReport`; prose bounds are stated in the prompt only
  (Vertex pad-toward-cap pathology).
- **Structured fields before prose on emit schemas** — declare structured commitment fields
  ahead of free-text ones (existing `LlmTickerVerdict` ordering).
- **Shell conventions** — never prefix Bash commands with `cd`; run from the project root.
  Tests: `PYTHONPATH=src .venv/bin/python -m pytest <path> -v`. Scripts:
  `PYTHONPATH=src .venv/bin/python -m scripts.<name>`.
- **One commit per task** — each task ends with a single commit; do not batch across tasks.
  Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Cross-plan assumption (verify once, in Task 6)

The golden cache stores **provider data only** (prices, filings, news, insider). LLM
analyst verdicts are generated **live** every run under the analyst's current
`output_schema`; they are **not** replayed from cache. Removing `horizon_days` from the
`LlmTickerVerdict` emit schema is therefore safe — no stale cached verdict carrying the old
field is ever re-validated. Task 6 Step 1 confirms this before the schema change lands.

---

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/agents/analysts/heuristics.py` | `TechnicalHeuristics` — retire momentum/RSI knobs, add three-reads knobs | 1 |
| `config/analyst_heuristics.json` | Technical config values | 1 |
| `config/README.md` | Document technical + news config changes | 1, 5 |
| `src/contract/extractors/technical.py` | Add `vol_regime_z` + `trend_state` features; rewrite `derive_technical_verdict` | 2, 3 |
| `src/contract/strategist_prompt.py` | Two new technical bullets + interpreters; horizon-precursor render | 4, 9 |
| `src/config/analysts.py` | `NewsCaps.drift_horizon_days` | 5 |
| `config/analysts.json` | News `drift_horizon_days` value | 5 |
| `src/contract/evidence.py` | Drop `horizon_days` from `LlmTickerVerdict`; `to_ticker_verdict(*, horizon_days)`; refresh comments | 6 |
| `src/agents/analysts/news/joiner.py` | Inject news horizon at inflation | 6 |
| `src/agents/analysts/fundamental/joiner.py` | Inject fundamental horizon at inflation | 6 |
| `src/agents/analysts/news/prompts.py` | Strip horizon self-report | 7 |
| `src/agents/analysts/fundamental/prompts.py` | Strip horizon self-report emit line | 8 |
| `src/agents/strategist/prompts.py` | Technical-reads + horizon-reading prose; hygiene | 10 |

---

## PHASE 1 — Technical three-reads rebuild

### Task 1: Retire momentum/RSI heuristics; add three-reads config

**Files:**
- Modify: `src/agents/analysts/heuristics.py` (`TechnicalHeuristics`)
- Modify: `config/analyst_heuristics.json` (`technical` block)
- Modify: `config/README.md` (technical thresholds table)
- Test: `tests/unit/test_analyst_heuristics.py`

**Interfaces:**
- Produces: the new `TechnicalHeuristics` field surface consumed by Tasks 2 and 3 —
  `reversal_neutral_band_pct: float`, `reversal_magnitude_scale: float`,
  `reversal_confidence_base: float`, `reversal_horizon_days: int`,
  `vol_regime_window: int`, `vol_regime_elevated_z: float`, plus the **retained** fields
  `vol_ratio_breakout`, `vol_ratio_dry_up`, `near_52w_extreme_pct`, `magnitude_cap`,
  `beta_confidence_damping_enabled`.

> **Retirement (consequential — confirm before executing this plan):** the old
> momentum/RSI/suppression knobs no longer have a consumer once `derive_technical_verdict`
> is rewritten (Task 3). This task removes them from the schema, the JSON, and the README.
> Removed: `rsi_overbought`, `rsi_oversold`, `rsi_mean_reversion`,
> `pct_change_momentum_scale`, `momentum_neutral_band_pct`, `confidence_base`,
> `confidence_boost_step`, `confidence_penalty_step`, `atr_high_volatility_pct`,
> `suppress_bearish_under_golden_cross`, `suppress_bullish_under_death_cross`,
> `directional_52w_confidence`, `momentum_band_confidence_floor`. The identically-named
> fields on `SocialHeuristics` (`confidence_base`, `confidence_boost_step`,
> `confidence_penalty_step`) are a **separate class** and are NOT touched.

- [ ] **Step 1: Write the failing test**

Replace the `TechnicalHeuristics` assertions in `tests/unit/test_analyst_heuristics.py`
with the new surface (find the existing technical test and swap its body):

```python
def test_technical_heuristics_new_three_reads_surface():
    """The technical config exposes the three-reads knobs and drops the retired ones."""
    h = load_heuristics().technical

    # New reversal knobs.
    assert 0.0 <= h.reversal_neutral_band_pct <= 1.0
    assert h.reversal_magnitude_scale > 0.0
    assert 0.0 <= h.reversal_confidence_base <= 1.0
    assert 1 <= h.reversal_horizon_days <= 60

    # New volatility-regime knobs.
    assert 2 <= h.vol_regime_window <= 252
    assert h.vol_regime_elevated_z > 0.0

    # Retained context knobs.
    assert h.vol_ratio_breakout > 1.0
    assert 0.0 < h.vol_ratio_dry_up < 1.0
    assert h.near_52w_extreme_pct > 0.0
    assert 0.0 < h.magnitude_cap <= 1.0

    # Retired knobs are gone (extra="forbid" would reject them in JSON anyway).
    for dead in (
        "rsi_overbought", "rsi_oversold", "rsi_mean_reversion",
        "pct_change_momentum_scale", "momentum_neutral_band_pct",
        "confidence_base", "confidence_boost_step", "confidence_penalty_step",
        "atr_high_volatility_pct", "suppress_bearish_under_golden_cross",
        "suppress_bullish_under_death_cross", "directional_52w_confidence",
        "momentum_band_confidence_floor",
    ):
        assert not hasattr(h, dead), f"retired field still present: {dead}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py::test_technical_heuristics_new_three_reads_surface -v`
Expected: FAIL — `AttributeError`/`ValidationError` (new fields not on the model yet).

- [ ] **Step 3: Replace the `TechnicalHeuristics` class body**

In `src/agents/analysts/heuristics.py`, replace the entire `TechnicalHeuristics` class
(the `class TechnicalHeuristics(_Frozen): ...` block, from its `"""..."""` docstring down to
just before `class SocialHeuristics`) with:

```python
class TechnicalHeuristics(_Frozen):
    """Thresholds for the deterministic technical verdict (three independent reads).

    The verdict's lean/magnitude/confidence reflect ONLY the short-term reversal
    read.  The volatility-regime and trend-state reads are surfaced as rendered
    feature numbers with explanatory prose (see ``contract.strategist_prompt``);
    they are deliberately NOT blended into the lean.
    """

    # ── Read 1: short-term reversal (the one directional lean) ──────────────
    reversal_neutral_band_pct: float = Field(ge=0.0, le=1.0)
    """Neutral band for the contrarian 5-day reversal lean.

    ``pct_change_5d`` is a fractional return (0.03 = +3 %).  When
    ``abs(pct_change_5d)`` is at or below this band the analyst abstains
    (``lean="neutral"``): the recent move is too small to be a mean-reversion
    candidate.  Outside the band the lean is CONTRARIAN — it leans AGAINST the
    recent move (a recent up-move → bearish fade; a recent down-move → bullish
    bounce), inverting the old trend-following sign (Jegadeesh 1990; Lehmann
    1990).  Tune via ``config/analyst_heuristics.json`` without a code change.
    """

    reversal_magnitude_scale: float = Field(gt=0.0)
    """Scales the size of the faded move into a magnitude.

    ``magnitude = min(abs(pct_change_5d) * this, magnitude_cap)``.  With the
    default 8.0 a 5 % 5-day move yields magnitude 0.40 and a 12.5 % move
    saturates the cap.
    """

    reversal_confidence_base: float = Field(ge=0.0, le=1.0)
    """Confidence at the neutral-band edge for a directional reversal lean.

    Confidence ramps linearly from this base (at the band edge) to 1.0 (at
    twice the band width), so a borderline fade is low-confidence and a large
    dislocation is high-confidence.  A neutral lean carries confidence 0.0 (no
    directional view to be confident about).
    """

    reversal_horizon_days: int = Field(ge=1, le=60)
    """Trading-day horizon the reversal read targets (5–10 days).

    Written onto ``AnalystVerdict.horizon_days`` by ``derive_technical_verdict``
    so the strategist can read the technical analyst's own horizon.  Kept short
    and deliberately away from the sub-24h overnight bounce.
    """

    # ── Read 2: volatility regime (risk number; NOT blended into the lean) ──
    vol_regime_window: int = Field(ge=2, le=252)
    """Trailing window (in valid ATR% samples) for the volatility-regime z-score.

    ``vol_regime_z = (atr_pct[-1] - mean(atr_pct[-window:])) / std(...)`` — a
    self-relative read of how stressed volatility is versus the ticker's own
    recent history (Moreira–Muir 2017; safe daily via volatility clustering,
    Engle 1982 / Bollerslev 1986).
    """

    vol_regime_elevated_z: float = Field(gt=0.0)
    """``abs(vol_regime_z)`` at or above which the ``vol_regime_elevated`` tag fires.

    A risk flag only — it does NOT alter the reversal lean.
    """

    # ── Retained context knobs (drive corroborating TAGS only) ─────────────
    vol_ratio_breakout: float = Field(gt=1.0)
    vol_ratio_dry_up: float   = Field(gt=0.0, lt=1.0)
    near_52w_extreme_pct: float = Field(gt=0.0)
    magnitude_cap: float        = Field(gt=0.0, le=1.0)

    beta_confidence_damping_enabled: bool = Field(default=False)
    """Gate the ``beta_confidence_damping`` technical feature (strategist-facing
    context only; ``derive_technical_verdict`` never reads it).  Ships disabled.
    """
```

- [ ] **Step 4: Replace the `technical` block in `config/analyst_heuristics.json`**

```json
  "technical": {
    "reversal_neutral_band_pct": 0.03,
    "reversal_magnitude_scale": 8.0,
    "reversal_confidence_base": 0.5,
    "reversal_horizon_days": 7,
    "vol_regime_window": 60,
    "vol_regime_elevated_z": 1.5,
    "vol_ratio_breakout": 1.3,
    "vol_ratio_dry_up": 0.7,
    "near_52w_extreme_pct": 5.0,
    "magnitude_cap": 1.0,
    "beta_confidence_damping_enabled": false
  },
```

- [ ] **Step 5: Update `config/README.md`**

In the `analyst_heuristics.json` technical section, delete the rows for every retired field
(`rsi_overbought`, `rsi_oversold`, `rsi_mean_reversion`, `pct_change_momentum_scale`,
`momentum_neutral_band_pct`, `confidence_base`, `confidence_boost_step`,
`confidence_penalty_step`, `atr_high_volatility_pct`, `suppress_bearish_under_golden_cross`,
`suppress_bullish_under_death_cross`, `directional_52w_confidence`,
`momentum_band_confidence_floor`) and add:

```markdown
| `technical.reversal_neutral_band_pct` | float [0–1] | Neutral band for the **contrarian** 5-day reversal lean. `pct_change_5d` is fractional (0.03 = 3 %). Inside the band → `lean="neutral"`; outside → lean AGAINST the recent move (up-move ⇒ bearish fade, down-move ⇒ bullish bounce) — Jegadeesh 1990 / Lehmann 1990. Default **0.03**. |
| `technical.reversal_magnitude_scale` | float > 0 | `magnitude = min(abs(pct_change_5d) * scale, magnitude_cap)`. Default **8.0** (a 5 % move ⇒ 0.40). |
| `technical.reversal_confidence_base` | float [0–1] | Confidence at the band edge for a directional reversal; ramps to 1.0 at twice the band width. Neutral leans carry 0.0. Default **0.5**. |
| `technical.reversal_horizon_days` | int [1–60] | Trading-day horizon the reversal read targets (5–10d). Written onto `AnalystVerdict.horizon_days`. Default **7**. |
| `technical.vol_regime_window` | int [2–252] | Trailing window (valid ATR% samples) for the volatility-regime z-score. Default **60**. |
| `technical.vol_regime_elevated_z` | float > 0 | `abs(vol_regime_z)` at/above which the `vol_regime_elevated` risk tag fires. Default **1.5**. |
| `technical.vol_ratio_breakout` | float > 1 | Volume-ratio threshold for the `vol_breakout` context tag. Default **1.3**. |
| `technical.vol_ratio_dry_up` | float (0–1) | Volume-ratio threshold for the `vol_dry_up` context tag. Default **0.7**. |
| `technical.near_52w_extreme_pct` | float > 0 | Proximity (%) to the 52-week high/low for the `near_52w_high` / `near_52w_low` context tags. Default **5.0**. |
| `technical.magnitude_cap` | float (0–1] | Upper bound on the technical verdict magnitude. Default **1.0**. |
| `technical.beta_confidence_damping_enabled` | bool | Gate the strategist-facing `beta_confidence_damping` feature. `derive_technical_verdict` never reads it. Default **false**. |
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py -v`
Expected: PASS. (The retirement will break `test_derive_technical_verdict.py` and
`test_technical.py` — those are rewritten in Tasks 3 and 2 respectively. Do not run the
whole suite green here; scope the run to this file.)

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/heuristics.py config/analyst_heuristics.json config/README.md tests/unit/test_analyst_heuristics.py
git commit -m "feat(technical): retire momentum/RSI heuristics, add three-reads config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Extractor — add `vol_regime_z` and `trend_state` features

**Files:**
- Modify: `src/contract/extractors/technical.py` (`_KEYS`, `_zero_features`,
  `_emit_ratios_features`, `extract_technical_features`; add `_vol_regime_window`)
- Test: `tests/unit/contract/extractors/test_technical.py`

**Interfaces:**
- Consumes: `TechnicalHeuristics.vol_regime_window` (Task 1).
- Produces: two new nullable feature keys, computed only when data suffices —
  `vol_regime_z: float` (z-score of ATR% vs its trailing window) and
  `trend_state: float` (`ratios.last_price / two_hundred_day_average − 1`). Consumed by
  Tasks 3 (verdict tags) and 4 (strategist bullets).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/contract/extractors/test_technical.py`:

```python
import numpy as np

from contract.extractors.technical import extract_technical_features


def _ramp_bars(n: int, start: float = 100.0, step: float = 0.5) -> list[dict]:
    """Build ``n`` synthetic OHLCV bars on a gentle linear ramp (oldest first)."""
    bars = []
    for i in range(n):
        close = start + i * step
        bars.append(
            {
                "timestamp": f"2025-01-{(i % 28) + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1_000_000 + i,
            }
        )
    return bars


def test_trend_state_from_ma200():
    """trend_state = last_price / two_hundred_day_average - 1 when ratios carry ma200."""
    raw = {
        "bars": _ramp_bars(30),
        "ratios": {"last_price": 110.0, "two_hundred_day_average": 100.0},
    }
    feats = extract_technical_features(raw, "TEST")
    assert feats["trend_state"] == float(110.0 / 100.0 - 1.0)


def test_trend_state_absent_without_ma200():
    """No ma200 → trend_state key is omitted (nullable convention), not 0.0."""
    raw = {"bars": _ramp_bars(30), "ratios": {"last_price": 110.0}}
    feats = extract_technical_features(raw, "TEST")
    assert "trend_state" not in feats


def test_vol_regime_z_emitted_with_enough_history():
    """A long-enough bar series yields a finite vol_regime_z z-score."""
    raw = {"bars": _ramp_bars(120)}
    feats = extract_technical_features(raw, "TEST")
    assert "vol_regime_z" in feats
    assert np.isfinite(feats["vol_regime_z"])


def test_vol_regime_z_absent_when_history_too_short():
    """Fewer valid ATR% samples than the window → vol_regime_z omitted."""
    raw = {"bars": _ramp_bars(20)}
    feats = extract_technical_features(raw, "TEST")
    assert "vol_regime_z" not in feats
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -k "trend_state or vol_regime_z" -v`
Expected: FAIL — keys not emitted yet.

- [ ] **Step 3: Add the two keys to `_KEYS` and the nullable set**

In `src/contract/extractors/technical.py`, extend the `_KEYS` tuple — add these two entries
immediately after `"last_close",`:

```python
    # Phase 3b three-reads additions (both nullable — omitted when not computable):
    "vol_regime_z",   # z-score of ATR% vs its own trailing window (Read 2)
    "trend_state",    # last_price / 200d MA - 1 (Read 3)
```

In `_zero_features`, add both to the `_NULLABLE` set so they are never seeded to `0.0`:

```python
    _NULLABLE = {
        "vol_ratio_20d",
        "pct_change_20d",
        "beta_confidence_damping",
        "vol_regime_z",
        "trend_state",
    }
```

- [ ] **Step 4: Add the `_vol_regime_window` config helper**

Immediately after `_beta_damping_enabled` in the same file:

```python
def _vol_regime_window() -> int:
    """Trailing-window length for the volatility-regime z-score.

    Reads ``technical.vol_regime_window`` from the validated heuristics config.
    Imported lazily (mirroring ``_beta_damping_enabled``) to dodge the import
    cycle between this module and ``agents.analysts.heuristics``.  Falls back to
    60 if the config cannot be loaded — a safe default that simply widens the
    minimum history needed before the z-score is emitted.

    Returns
    -------
    int
        The configured window length, or 60 on any config-load failure.
    """
    try:
        from agents.analysts.heuristics import load_heuristics  # noqa: PLC0415

        return load_heuristics().technical.vol_regime_window
    except Exception:  # noqa: BLE001 — config load is best-effort; degrade to 60
        return 60
```

- [ ] **Step 5: Emit `trend_state` in `_emit_ratios_features`**

In `_emit_ratios_features`, immediately after the golden/death-cross block (the
`if last is not None and ma50 is not None and ma200 is not None:` stanza) and before the
beta block, add:

```python
    # Read 3 — trend state: continuous distance of price from the 200-day MA.
    # A signed fraction (0.05 = 5 % above the 200-day MA).  Persistent regime
    # context; surfaced as a number, not blended into the technical lean.
    if last is not None and ma200 is not None and ma200 > 0:
        out["trend_state"] = last / ma200 - 1.0
```

- [ ] **Step 6: Emit `vol_regime_z` inside the ATR block**

In `extract_technical_features`, replace the ATR block (the
`# --- ATR(14) as a percentage of last close ---` stanza, `if len(df) >= 15: ...`) with:

```python
    # --- ATR(14) as a percentage of last close (+ Read 2 volatility regime) --
    # ATR needs high, low, close arrays and at least 15 bars.  We compute the
    # ATR% as a full elementwise series (not just the last bar) so the
    # volatility-regime z-score can measure the latest reading against the
    # ticker's OWN recent ATR% history (self-relative — Moreira-Muir 2017).
    if len(df) >= 15:
        high_arr  = df["high"].to_numpy(dtype=float)
        low_arr   = df["low"].to_numpy(dtype=float)
        close_arr = df["close"].to_numpy(dtype=float)

        atr_arr = talib.ATR(high_arr, low_arr, close_arr, timeperiod=14)

        # ATR as a percentage of the contemporaneous close, elementwise.  Guard
        # divide-by-zero (a genuinely zero close) by mapping it to NaN, which is
        # then excluded from both the "last reading" and the z-score window.
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct_arr = np.where(close_arr > 0, atr_arr / close_arr * 100.0, np.nan)

        last_atr_pct = atr_pct_arr[-1] if len(atr_pct_arr) > 0 else np.nan

        if not np.isnan(last_atr_pct):
            out["atr_pct_14"] = float(last_atr_pct)

            # Read 2 — volatility regime: z-score of the latest ATR% against its
            # own trailing window.  Emitted only when enough VALID (non-NaN)
            # samples exist and the window has non-zero dispersion; otherwise the
            # key stays absent (nullable convention → renderer skips it).
            window = _vol_regime_window()
            valid  = atr_pct_arr[~np.isnan(atr_pct_arr)]

            if len(valid) >= window:
                recent = valid[-window:]
                mu     = float(recent.mean())
                sigma  = float(recent.std())

                if sigma > 0:
                    out["vol_regime_z"] = float((last_atr_pct - mu) / sigma)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -v`
Expected: PASS for the four new tests. Pre-existing tests in this file that assert on the
retired verdict path are handled in Task 3; if any assert only on feature extraction they
should still pass. If a pre-existing feature test breaks purely because `_KEYS` grew,
update its expected-key set to include the two new nullable keys (they are absent unless
computable, so exact-key-set assertions on short series are unaffected).

- [ ] **Step 8: Commit**

```bash
git add src/contract/extractors/technical.py tests/unit/contract/extractors/test_technical.py
git commit -m "feat(technical): extract vol_regime_z and trend_state features

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Rewrite `derive_technical_verdict` to the reversal lean

**Files:**
- Modify: `src/contract/extractors/technical.py` (`derive_technical_verdict`; remove the
  now-unused `from math import copysign` import)
- Test: `tests/unit/test_derive_technical_verdict.py`

**Interfaces:**
- Consumes: the new `TechnicalHeuristics` surface (Task 1) and the `vol_regime_z` /
  `trend_state` features (Task 2).
- Produces: an `AnalystVerdict` whose lean/magnitude/confidence reflect ONLY the reversal
  read, with `horizon_days = h.reversal_horizon_days`, and `key_factors` carrying the
  reversal tag plus context tags (`vol_regime_elevated`, `trend_above_ma200` /
  `trend_below_ma200`, `vol_breakout` / `vol_dry_up`, `near_52w_high` / `near_52w_low`,
  `golden_cross` / `death_cross`).

- [ ] **Step 1: Rewrite the test module**

Replace the body of `tests/unit/test_derive_technical_verdict.py` with a self-contained
heuristics factory and the reversal-focused cases (keep the module's existing imports for
`extract`/`AnalystVerdict` if present; add what is missing):

```python
"""Table-driven tests for the rewritten reversal-based technical verdict."""
from __future__ import annotations

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _tech(**overrides) -> TechnicalHeuristics:
    """Build a TechnicalHeuristics with sane defaults, overridable per-test."""
    base = dict(
        reversal_neutral_band_pct=0.03,
        reversal_magnitude_scale=8.0,
        reversal_confidence_base=0.5,
        reversal_horizon_days=7,
        vol_regime_window=60,
        vol_regime_elevated_z=1.5,
        vol_ratio_breakout=1.3,
        vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0,
        magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )
    base.update(overrides)
    return TechnicalHeuristics(**base)


def _feats(**overrides) -> dict:
    """Minimal computable feature dict (dodges the no-data fingerprint)."""
    feats = {"rsi_14": 55.0, "atr_pct_14": 2.0, "pct_change_5d": 0.0, "pct_change_20d": 0.01}
    feats.update(overrides)
    return feats


def test_recent_up_move_fades_bearish():
    """A recent 5-day UP move is faded DOWN (contrarian)."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.08), _tech())
    assert v.lean == "bearish"
    assert "reversal_up_fade" in v.key_factors


def test_recent_down_move_bounces_bullish():
    """A recent 5-day DOWN move is faded UP (contrarian)."""
    v = derive_technical_verdict(_feats(pct_change_5d=-0.08), _tech())
    assert v.lean == "bullish"
    assert "reversal_down_bounce" in v.key_factors


def test_inside_band_is_neutral_zero_confidence():
    """A move inside the neutral band yields a neutral, zero-confidence verdict."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.01), _tech())
    assert v.lean == "neutral"
    assert v.confidence == 0.0
    assert v.magnitude == 0.0
    assert "reversal_neutral" in v.key_factors


def test_magnitude_scales_and_caps():
    """Magnitude = min(|pct5| * scale, cap)."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.05), _tech())
    assert abs(v.magnitude - 0.40) < 1e-9
    capped = derive_technical_verdict(_feats(pct_change_5d=0.50), _tech())
    assert capped.magnitude == 1.0


def test_confidence_ramps_from_base_to_one():
    """At the band edge confidence == base; at ≥2× band it saturates to 1.0."""
    edge = derive_technical_verdict(_feats(pct_change_5d=0.03), _tech())
    assert abs(edge.confidence - 0.5) < 1e-9
    far = derive_technical_verdict(_feats(pct_change_5d=0.06), _tech())
    assert abs(far.confidence - 1.0) < 1e-9


def test_horizon_days_from_config():
    """The reversal horizon is written onto the verdict."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.08), _tech(reversal_horizon_days=9))
    assert v.horizon_days == 9


def test_vol_regime_and_trend_are_tags_not_lean():
    """Vol-regime and trend-state surface as tags and never flip the reversal lean."""
    v = derive_technical_verdict(
        _feats(pct_change_5d=0.08, vol_regime_z=2.5, trend_state=0.10),
        _tech(),
    )
    # Lean still driven ONLY by the reversal read.
    assert v.lean == "bearish"
    assert "vol_regime_elevated" in v.key_factors
    assert "trend_above_ma200" in v.key_factors


def test_no_data_fingerprint_still_fires():
    """All three core indicators absent/zero → canonical no-data verdict."""
    v = derive_technical_verdict({"rsi_14": 0.0, "atr_pct_14": 0.0, "pct_change_5d": 0.0}, _tech())
    assert v.is_no_data is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py -v`
Expected: FAIL — old verdict logic produces momentum-based leans, wrong signs/tags.

- [ ] **Step 3: Replace `derive_technical_verdict`**

Replace the entire `derive_technical_verdict` function body (from its `def` line through the
final `return AnalystVerdict(...)`) with:

```python
def derive_technical_verdict(
    features: dict[str, float],
    h: TechnicalHeuristics,
) -> AnalystVerdict:
    """Map the technical feature vector to an ``AnalystVerdict`` — three reads.

    Pure function — no I/O, no globals.  Safe for table-driven unit tests.

    The verdict's lean/magnitude/confidence reflect ONLY the short-term
    reversal read (Read 1).  The volatility-regime (Read 2) and trend-state
    (Read 3) reads, plus the volume / 52-week / crossover context, are surfaced
    as ``key_factors`` tags and rendered feature numbers — they are NEVER
    blended into the lean.

    Read 1 — short-term reversal (Jegadeesh 1990; Lehmann 1990): lean CONTRARIAN
    to ``pct_change_5d`` (a recent up-move fades bearish; a recent down-move
    bounces bullish), with a neutral band.  This deliberately inverts the old
    trend-following sign.

    Parameters
    ----------
    features:
        Output of ``extract_technical_features``.
    h:
        Validated ``TechnicalHeuristics`` config section.

    Returns
    -------
    AnalystVerdict
        Lean/magnitude/confidence from the reversal read, ``horizon_days`` from
        ``h.reversal_horizon_days``, and context tags in ``key_factors``.
    """
    # Deferred runtime imports — avoids the circular import that arises when
    # loading this module triggers agents.analysts.__init__.
    from contract.evidence import AnalystVerdict  # noqa: PLC0415

    # --- No-data fingerprint (unchanged) -------------------------------------
    # rsi_14 and atr_pct_14 default to 0.0 (need >=15 bars); pct_change_20d is
    # absent (None) below 21 bars.  All three absent/zero = no usable history.
    pct20_raw = features.get("pct_change_20d")

    if (
        features["rsi_14"] == 0
        and pct20_raw is None
        and features["atr_pct_14"] == 0
    ):
        from contract.evidence import _no_data_analyst_verdict  # noqa: PLC0415

        return _no_data_analyst_verdict(reason="no price data")

    factors: list[str] = []

    # === READ 1 — short-term reversal (the ONE directional lean) =============
    # Contrarian on the 5-day return: lean AGAINST the recent short-term move.
    pct5 = features["pct_change_5d"]
    band = h.reversal_neutral_band_pct

    if pct5 > band:
        # Recent up-move → fade DOWN.
        lean = "bearish"
        factors.append("reversal_up_fade")
    elif pct5 < -band:
        # Recent down-move → fade UP.
        lean = "bullish"
        factors.append("reversal_down_bounce")
    else:
        lean = "neutral"
        factors.append("reversal_neutral")

    # Magnitude scales with the size of the move being faded, capped.
    magnitude = min(abs(pct5) * h.reversal_magnitude_scale, h.magnitude_cap)

    # Confidence ramps from the base (at the band edge) to 1.0 (at 2x the band).
    # A neutral read carries no directional confidence (and no magnitude).
    if lean == "neutral":
        confidence = 0.0
        magnitude = 0.0
    else:
        excess     = min(max((abs(pct5) - band) / band, 0.0), 1.0) if band > 0 else 1.0
        confidence = h.reversal_confidence_base + (1.0 - h.reversal_confidence_base) * excess
        confidence = max(0.0, min(1.0, confidence))

    # === READ 2 — volatility regime (risk tag; NOT blended into the lean) ====
    vol_z = features.get("vol_regime_z")
    if vol_z is not None and abs(vol_z) >= h.vol_regime_elevated_z:
        factors.append("vol_regime_elevated")

    # === READ 3 — trend state (regime tag; NOT blended into the lean) ========
    trend = features.get("trend_state")
    if trend is not None:
        factors.append("trend_above_ma200" if trend >= 0 else "trend_below_ma200")

    # --- Corroborating context tags (do NOT alter the reversal lean) ---------
    vol_ratio = features.get("vol_ratio_20d")
    if vol_ratio is not None and not (isinstance(vol_ratio, float) and math.isnan(vol_ratio)):
        if vol_ratio > h.vol_ratio_breakout:
            factors.append("vol_breakout")
        elif vol_ratio < h.vol_ratio_dry_up:
            factors.append("vol_dry_up")

    dist_high = features.get("dist_from_high_52w_pct", -100.0)
    dist_low  = features.get("dist_from_low_52w_pct",   100.0)

    if abs(dist_high) <= h.near_52w_extreme_pct:
        factors.append("near_52w_high")

    if dist_low <= h.near_52w_extreme_pct:
        factors.append("near_52w_low")

    if features.get("golden_cross", 0.0) >= 1.0:
        factors.append("golden_cross")

    if features.get("death_cross", 0.0) >= 1.0:
        factors.append("death_cross")

    # --- Rationale (A-016): compact ", "-joined factor list, capped 160 chars -
    rationale = (", ".join(factors) or "neutral")[:160]

    return AnalystVerdict(
        lean=lean,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        key_factors=factors,
        is_no_data=False,
        horizon_days=h.reversal_horizon_days,
    )
```

- [ ] **Step 4: Remove the now-unused `copysign` import**

At the top of the file, delete the line `from math import copysign` (the module-level
`import math` is retained — the NaN guard still uses `math.isnan`). Confirm with:

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/contract/extractors/technical.py`
Expected: no `F401` unused-import warning for `copysign`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py tests/unit/contract/extractors/test_technical.py -v`
Expected: PASS. Also re-run Task 1's file to confirm no regression:
`PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_analyst_heuristics.py -v`.

- [ ] **Step 6: Commit**

```bash
git add src/contract/extractors/technical.py tests/unit/test_derive_technical_verdict.py
git commit -m "feat(technical): rewrite verdict as contrarian short-term reversal lean

Lean/magnitude/confidence now reflect only the 5-day reversal read; vol-regime
and trend-state surface as tags. Horizon set from reversal_horizon_days.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Strategist bullets — surface the two new reads

**Files:**
- Modify: `src/contract/strategist_prompt.py` (`TECHNICAL_BULLETS`; add
  `_vol_regime_band` + `_trend_state_band` interpreters)
- Test: `tests/unit/contract/test_strategist_prompt_layout.py`

**Interfaces:**
- Consumes: `vol_regime_z` / `trend_state` features (Task 2).
- Produces: two rendered bullets with explanatory interpreters in the `[Technical]` block.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/contract/test_strategist_prompt_layout.py`:

```python
def test_technical_block_renders_vol_regime_and_trend_state():
    """The technical bullets render the two new reads with prose interpreters."""
    from contract.strategist_prompt import _render_features, TECHNICAL_BULLETS

    feats = {"vol_regime_z": 2.4, "trend_state": 0.12}
    lines = "\n".join(_render_features(feats, TECHNICAL_BULLETS))

    assert "Volatility regime (z):" in lines
    assert "(elevated vs own history)" in lines
    assert "Trend vs 200d MA:" in lines
    assert "(above 200d MA)" in lines
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py::test_technical_block_renders_vol_regime_and_trend_state -v`
Expected: FAIL — bullets/interpreters absent.

- [ ] **Step 3: Add the two interpreters**

In `src/contract/strategist_prompt.py`, immediately after `_death_cross_band`:

```python
def _vol_regime_band(v: float) -> str:
    """Prose annotation for the ATR%-vs-own-history volatility z-score.

    Read 2 of the technical rebuild.  A positive z means the ticker is more
    volatile than its own recent history (stressed regime); a negative z means
    calmer than usual.  Qualitative only — the ``vol_regime_elevated`` verdict
    tag carries the config-driven threshold; this annotation gives the
    strategist a plain-language read of the number.

    Parameters
    ----------
    v:
        ``vol_regime_z`` feature value (standard deviations from the trailing
        mean of ATR%).

    Returns
    -------
    str
        ``"(elevated vs own history)"`` at or above +1.5,
        ``"(calm vs own history)"`` at or below -1.5, else ``"(normal)"``.
    """
    if v >= 1.5:
        return "(elevated vs own history)"

    if v <= -1.5:
        return "(calm vs own history)"

    return "(normal)"


def _trend_state_band(v: float) -> str:
    """Prose annotation for the continuous distance from the 200-day MA.

    Read 3 of the technical rebuild.  Persistent regime context: price above
    the 200-day MA is a structural up-trend, below is a structural down-trend.
    Surfaced as context only — it does NOT drive the technical lean.

    Parameters
    ----------
    v:
        ``trend_state`` feature value (signed fraction; 0.05 = 5 % above MA200).

    Returns
    -------
    str
        ``"(above 200d MA)"`` when non-negative, else ``"(below 200d MA)"``.
    """
    return "(above 200d MA)" if v >= 0 else "(below 200d MA)"
```

- [ ] **Step 4: Add the two bullets to `TECHNICAL_BULLETS`**

Insert into the `TECHNICAL_BULLETS` list. Put `vol_regime_z` right after the `atr_pct_14`
bullet, and `trend_state` right after the golden/death-cross pair:

```python
    # Read 2 — volatility regime: z-score of ATR% vs the ticker's own history.
    ("vol_regime_z",           "Volatility regime (z):",   _plain,              _vol_regime_band),
```
```python
    # Read 3 — trend state: continuous distance of price from the 200-day MA.
    ("trend_state",            "Trend vs 200d MA:",        _pct_signed,         _trend_state_band),
```

(Both keys are nullable — absent bullets are silently skipped by `_render_features`, so
tickers without ratios/history simply omit these lines.)

- [ ] **Step 5: Sweep the stale rationale-tag comment**

The reversal rewrite (Task 3) stops emitting the `rsi_overbought` rationale tag, so the
example comment in `strategist_prompt.py` referring to it is now stale. Find the comment
line reading:

```python
          -> Rationale tags: trend_up_20d, rsi_overbought
```

and update it to reference the tags the rewritten verdict actually emits:

```python
          -> Rationale tags: reversal_up_fade, vol_regime_elevated, trend_above_ma200
```

(This is documentation hygiene only — verified during planning as the sole remaining
reference to a retired field outside the two rewrite targets; there is no live consumer.)

- [ ] **Step 6: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: PASS. If a pre-existing layout test asserts an exact `TECHNICAL_BULLETS` length or
an exact rendered-block snapshot, update its expected value to include the two new bullets.

- [ ] **Step 7: Commit**

```bash
git add src/contract/strategist_prompt.py tests/unit/contract/test_strategist_prompt_layout.py
git commit -m "feat(strategist): render vol_regime_z and trend_state technical bullets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## PHASE 2 — Horizon calibration (LLM stops self-reporting `horizon_days`)

### Task 5: Add `NewsCaps.drift_horizon_days`

**Files:**
- Modify: `src/config/analysts.py` (`NewsCaps`)
- Modify: `config/analysts.json` (`news` block)
- Modify: `config/README.md` (analysts.json news section)
- Test: `tests/unit/config/test_analysts_config.py`

**Interfaces:**
- Produces: `get_analysts_config().news.drift_horizon_days: int` (default 5), consumed by
  the news joiner in Task 6.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/config/test_analysts_config.py`:

```python
def test_news_drift_horizon_days_default():
    """News exposes a config-driven drift horizon (default 5 trading days)."""
    from config.analysts import get_analysts_config

    assert get_analysts_config().news.drift_horizon_days == 5
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_analysts_config.py::test_news_drift_horizon_days_default -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add the field to `NewsCaps`**

In `src/config/analysts.py`, add to the `NewsCaps` field block (after
`dedup_title_similarity_threshold`), and document it in the class docstring's Attributes
list:

```python
    # Phase 3b — trading-day drift horizon the news analyst's verdict targets.
    # Post-news drift (PEAD and analogues) plays out over ~1 week; the LLM no
    # longer self-reports this — the joiner injects it at inflation time.
    drift_horizon_days:                 int   = Field(ge=1,   le=60,    default=5)
```

Docstring addition (under Attributes):

```
    drift_horizon_days:
        Trading-day horizon the news verdict targets — injected onto
        ``horizon_days`` at the joiner boundary (the LLM no longer self-reports
        it).  Post-news drift operates over roughly a week.
```

- [ ] **Step 4: Add the value to `config/analysts.json`**

In the `news` block, add `"drift_horizon_days": 5,` (place it after
`dedup_title_similarity_threshold`).

- [ ] **Step 5: Update `config/README.md`**

In the analysts.json news section table, add:

```markdown
| `news.drift_horizon_days` | int [1–60] | Trading-day horizon the news verdict targets. Injected onto `horizon_days` at the joiner (the LLM no longer self-reports it). Post-news drift ≈ 1 week. Default **5**. |
```

- [ ] **Step 6: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/config/analysts.py config/analysts.json config/README.md tests/unit/config/test_analysts_config.py
git commit -m "feat(news): add config-driven drift_horizon_days (default 5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Drop `horizon_days` from the LLM emit schema; inject at the joiner

**Files:**
- Modify: `src/contract/evidence.py` (`LlmTickerVerdict.horizon_days` removed;
  `to_ticker_verdict(*, horizon_days)`; refresh the `AnalystVerdict.horizon_days` comment)
- Modify: `src/agents/analysts/news/joiner.py` (inject news horizon)
- Modify: `src/agents/analysts/fundamental/joiner.py` (inject fundamental horizon)
- Test: `tests/unit/contract/test_llm_to_ticker_inflate.py`,
  `tests/contract/test_llm_ticker_verdict.py`,
  `tests/unit/agents/analysts/news/test_joiner.py`,
  `tests/unit/agents/analysts/fundamental/test_joiner.py`

**Interfaces:**
- Consumes: `get_analysts_config().news.drift_horizon_days` (Task 5),
  `get_analysts_config().fundamental.filing_delta_horizon_days` (existing).
- Produces: `LlmTickerVerdict.to_ticker_verdict(*, horizon_days: int) -> TickerVerdict` —
  the sole inflation method, now horizon-injecting. `AnalystVerdict.horizon_days` stays
  (`default=1, ge=1`) for the deterministic/no-data/synthesised paths.

> **Assumption VERIFIED during planning (2026-07-17) — re-confirm only if the codebase has
> moved on.** Three facts, checked before this plan was finalised, make the field removal
> safe:
> 1. **Golden cache stores provider data only.** Its tables (`src/backtest/cache/schema.py`)
>    are `ohlcv_bars, company_ratios, filings, news_articles, insider_trades,
>    politician_trades, notable_holders` (+ `cache_runs`, `meta`). There is **no** verdict or
>    evidence table — no cached `LlmTickerVerdict` is ever re-validated on replay.
> 2. **`AnalystEvidenceRow` is a run-artifact DB, not a replay source.** It lives in
>    `src/orchestrator/persistence.py`, is written *live* during a run from the **canonical
>    `AnalystVerdict`** (which *keeps* `horizon_days`), and is read post-hoc by
>    `src/backtest/scoreboard.py` for scoring only. It is never fed back into the pipeline,
>    so dropping the field from the *emit* schema does not touch it.
> 3. **`raw_v` is a run-scoped ADK `temp:` key** (`temp:news_verdict_<TICKER>` /
>    `temp:fundamental_verdict_<TICKER>`), written and consumed inside the *same* run — no
>    payload carrying the old field survives into a future run.
>
> **Ordering caveat (do NOT run a live LLM pipeline mid-branch).** `LlmTickerVerdict` is
> `extra="forbid"`, and until Tasks 7/8 strip the prompt the LLM still *emits* `horizon_days`.
> So Task 6 (schema drops the field → forbidden) and Tasks 7/8 (prompt stops emitting it) are
> **coupled**: between those commits a *live* LLM run would raise a `ValidationError` in one
> direction or the other. This does not bite us — there is no live/paper instance
> (pre-deployment), the eval runs only after everything merges, and every per-task test is
> self-contained (each builds its own `raw_v` and stays green at its own commit boundary). The
> executor must simply **not fire a live/paper analyst pipeline on a half-applied branch**;
> per-task `pytest` runs are fine throughout.

- [ ] **Step 1: Re-confirm the assumption (fast sanity check)**

Run: `grep -rln "verdict\|evidence" src/backtest/cache/schema.py; grep -rn "temp:news_verdict_\|temp:fundamental_verdict_\|LlmTickerVerdict" src/backtest/ src/agents/analysts/`
Expected: the first grep prints **nothing** (no verdict/evidence table in the golden-cache
schema); the second shows the `temp:*_verdict_*` keys written by the live analyst branches
and read by the joiners **within the same run**, with no backtest-cache persistence of
`LlmTickerVerdict` payloads. If either expectation fails (a cached/replayed verdict path has
appeared since planning), STOP and raise it before proceeding.

- [ ] **Step 2: Write the failing tests**

In `tests/unit/contract/test_llm_to_ticker_inflate.py`, replace the horizon-carrying
inflation test with the injection contract:

```python
def test_to_ticker_verdict_injects_horizon():
    """horizon_days is injected as a keyword, not emitted by the LLM."""
    from contract.evidence import LlmTickerVerdict, AnalystReport, Driver

    llm = LlmTickerVerdict(
        ticker="AAPL",
        lean="bullish",
        magnitude=0.4,
        confidence=0.7,
        is_no_data=False,
        key_factors=["catalyst:earnings"],
        report=AnalystReport(
            summary="fresh beat",
            drivers=[Driver(name="beat", direction="bull", weight=1.0, body="EPS beat")],
        ),
    )

    tv = llm.to_ticker_verdict(horizon_days=5)
    assert tv.horizon_days == 5


def test_llm_ticker_verdict_rejects_horizon_days_field():
    """horizon_days is no longer part of the emit schema (extra='forbid')."""
    import pytest
    from pydantic import ValidationError
    from contract.evidence import LlmTickerVerdict, AnalystReport, Driver

    with pytest.raises(ValidationError):
        LlmTickerVerdict(
            ticker="AAPL", lean="bullish", magnitude=0.4, confidence=0.7,
            is_no_data=False, horizon_days=5, key_factors=["x"],
            report=AnalystReport(
                summary="s",
                drivers=[Driver(name="d", direction="bull", weight=1.0, body="b")],
            ),
        )
```

(Adjust the `AnalystReport` / `Driver` import names to match `evidence.py` — read the file
to confirm the driver class name before writing.)

- [ ] **Step 3: Run to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_llm_to_ticker_inflate.py -k "inject or rejects_horizon" -v`
Expected: FAIL — `to_ticker_verdict()` currently takes no kwargs; `horizon_days` still
accepted by the schema.

- [ ] **Step 4: Remove `horizon_days` from `LlmTickerVerdict`**

In `src/contract/evidence.py`, delete the `LlmTickerVerdict.horizon_days` field and its
comment block (the `# Trading days the lean should hold. REQUIRED ...` comment through
`horizon_days: int = Field(ge=1)`).

- [ ] **Step 5: Make `to_ticker_verdict` inject the horizon**

Change the signature and body:

```python
    def to_ticker_verdict(self, *, horizon_days: int) -> TickerVerdict:
        """Inflate this narrow LLM emit-schema into the canonical TickerVerdict.

        Sole conversion point between the LLM emit-shape and the downstream
        canonical shape — every joiner and consumer goes through this method.

        ``horizon_days`` is injected here as a REQUIRED keyword, sourced by the
        caller from ``config/analysts.json`` (news → ``news.drift_horizon_days``;
        fundamental → ``fundamental.filing_delta_horizon_days``).  The LLM no
        longer self-reports it — a self-reported horizon was hallucinated and
        silently collapsed long-horizon signals toward the default; making the
        caller inject a config value keeps every analyst's horizon honest and
        deterministic.

        ``rationale`` defaults to ``""`` on the canonical side (LLM analysts do
        not emit it).

        Parameters
        ----------
        horizon_days:
            Trading-day horizon to stamp on the canonical verdict.

        Returns
        -------
        TickerVerdict
            The canonical downstream shape.

        Raises
        ------
        ValueError
            If the post-conversion canonical shape is invalid (the
            ``AnalystVerdict`` prose-surface validator fires) — re-raised so the
            failure site names the LLM, not a downstream consumer.
        """
        # ``model_dump`` strips the runtime model to a plain dict; the LLM never
        # emitted ``horizon_days`` (removed from this schema) so we set it from
        # the injected keyword before validating the canonical shape.
        payload = self.model_dump()
        payload["horizon_days"] = horizon_days

        return TickerVerdict.model_validate(payload)
```

- [ ] **Step 6: Refresh the `AnalystVerdict.horizon_days` comment**

Replace the `# Phase 14: how many TRADING DAYS ...` comment above
`AnalystVerdict.horizon_days` with:

```python
    # How many TRADING DAYS the analyst expects this lean to remain valid.
    # Populated deterministically: the technical extractor writes
    # ``reversal_horizon_days``; the news and fundamental joiners inject their
    # config horizons via ``LlmTickerVerdict.to_ticker_verdict(horizon_days=…)``.
    # The default of 1 covers the no-data / synthesised-neutral branches only —
    # the LLM no longer self-reports this field.
```

- [ ] **Step 7: Inject the horizon at both joiners**

In `src/agents/analysts/news/joiner.py`, change the inflation call:

```python
                llm_v = LlmTickerVerdict.model_validate({**raw_v, "ticker": ticker})
                ticker_verdict = llm_v.to_ticker_verdict(
                    horizon_days=get_analysts_config().news.drift_horizon_days,
                )
```

In `src/agents/analysts/fundamental/joiner.py`:

```python
                llm_v = LlmTickerVerdict.model_validate({**raw_v, "ticker": ticker})
                ticker_verdict = llm_v.to_ticker_verdict(
                    horizon_days=get_analysts_config().fundamental.filing_delta_horizon_days,
                )
```

Add `from config.analysts import get_analysts_config` to each joiner's imports if not
already present (grep each file's import block first; add only if missing).

- [ ] **Step 8: Run the tests to verify they pass**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/contract/test_llm_to_ticker_inflate.py \
  tests/contract/test_llm_ticker_verdict.py \
  tests/unit/agents/analysts/news/test_joiner.py \
  tests/unit/agents/analysts/fundamental/test_joiner.py -v
```
Expected: PASS. Any existing test that constructed `LlmTickerVerdict(horizon_days=…)` or
called `to_ticker_verdict()` with no args must be updated: drop the field from the
constructor and pass `horizon_days=` to the method. Fix each such call.

- [ ] **Step 9: Commit**

```bash
git add src/contract/evidence.py src/agents/analysts/news/joiner.py src/agents/analysts/fundamental/joiner.py tests/
git commit -m "refactor(evidence): inject horizon_days at the joiner, drop it from LLM emit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Strip horizon self-report from the news prompt

**Files:**
- Modify: `src/agents/analysts/news/prompts.py` (`_TEMPLATE`)
- Test: `tests/unit/agents/analysts/news/test_prompts.py`

**Interfaces:**
- Consumes: nothing new. Produces: a news instruction with no `horizon_days` self-report.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/agents/analysts/news/test_prompts.py`:

```python
def test_news_prompt_has_no_horizon_self_report():
    """The news prompt no longer instructs the LLM to emit horizon_days."""
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.prompts import build_news_instruction

    instr = build_news_instruction(load_heuristics().news_vocabulary)

    assert "horizon_days" not in instr
    assert "Set horizon_days to roughly 5" not in instr
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/news/test_prompts.py::test_news_prompt_has_no_horizon_self_report -v`
Expected: FAIL — the template still mentions `horizon_days`.

- [ ] **Step 3: Edit `_TEMPLATE` in `news/prompts.py`**

Make these exact removals/reworks:

1. STEP 3, first bullet — change
   `Lean WITH the surprise direction. Set horizon_days to roughly 5.` to
   `Lean WITH the surprise direction.`
2. STEP 3, second bullet — change
   `continuation lean is justified at REDUCED magnitude and confidence; set horizon_days to the remaining window (e.g. 3–15).`
   to
   `continuation lean is justified at REDUCED magnitude and confidence.`
3. Delete the whole standalone paragraph:
   `horizon_days is REQUIRED: the number of TRADING DAYS you expect your lean to remain valid. ~5 for a fresh surprise; longer (up to ~15, matching the remaining-window range in STEP 3) only for mid-window drift continuation; 1 for a neutral no-surprise verdict.`
   (and the blank line after it).
4. In the OUTPUT CONTRACT field list, delete the two lines:
   `- horizon_days: integer >= 1 — trading days the lean should hold (see`
   `  STEP 3).`
5. In `report.summary` guidance, change
   `state the surprise (or its absence), the drift-window position, and the horizon.`
   to
   `state the surprise (or its absence) and the drift-window position.`
6. In the SHAPE EXAMPLE JSON object, delete the line `  "horizon_days": 5,`.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/news/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/prompts.py tests/unit/agents/analysts/news/test_prompts.py
git commit -m "refactor(news): strip horizon_days self-report from the prompt

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Strip horizon self-report from the fundamental prompt

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py` (`_TEMPLATE`)
- Test: `tests/unit/agents/analysts/fundamental/test_prompts.py`

**Interfaces:**
- Produces: a fundamental instruction that no longer instructs the LLM to *emit*
  `horizon_days`. The analytic prose framing the 3–6 month drift window
  (`{filing_horizon_days}`-day references) is **retained** — it informs the LLM's reasoning;
  only the emit instruction and the example field are removed.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/agents/analysts/fundamental/test_prompts.py`:

```python
def test_fundamental_prompt_has_no_horizon_emit_instruction():
    """The fundamental prompt no longer tells the LLM to emit horizon_days."""
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.fundamental.prompts import build_fundamental_instruction

    instr = build_fundamental_instruction(load_heuristics().fundamental_vocabulary)

    # The emit field is gone from the OUTPUT CONTRACT and the shape example.
    assert "horizon_days  integer" not in instr
    assert '"horizon_days":' not in instr
    # But the analytic drift-window prose (in trading days) is retained.
    assert "trading days" in instr
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_prompts.py::test_fundamental_prompt_has_no_horizon_emit_instruction -v`
Expected: FAIL.

- [ ] **Step 3: Edit `_TEMPLATE` in `fundamental/prompts.py`**

1. In the OUTPUT CONTRACT field list, delete the three lines:
   `  horizon_days  integer — emit exactly {filing_horizon_days}.  This is the`
   `                trading-day drift window of the filing-delta signal`
   `                (3–6 months); it is fixed for this analyst.`
2. In the SHAPE EXAMPLE JSON object, delete the line
   `  "horizon_days": {filing_horizon_days},`.
3. Leave the `{filing_horizon_days}` references in the sign-convention and "How to analyse"
   prose (lines describing the 3–6 month / N-trading-day window) untouched — they are
   analytic guidance, not an emit instruction. `build_fundamental_instruction` still
   substitutes `filing_horizon_days`, so no change to the build function is needed.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/prompts.py tests/unit/agents/analysts/fundamental/test_prompts.py
git commit -m "refactor(fundamental): strip horizon_days emit instruction from the prompt

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## PHASE 3 — Strategist horizon precursors + technical-read prose + hygiene

### Task 9: Render an honest horizon precursor per analyst

**Files:**
- Modify: `src/contract/strategist_prompt.py` (`_render_analyst`; add `_HORIZON_PROSE`)
- Test: `tests/unit/contract/test_strategist_prompt_layout.py`

**Interfaces:**
- Consumes: `AnalystVerdict.horizon_days` (populated by Tasks 3/6).
- Produces: a mechanistic, non-prescriptive `horizon:` line under each analyst header — the
  primary root-cause fix for churn (the strategist can finally see how long each analyst
  expects its lean to hold). This is **information, not a trust ranking and not an
  instruction to hold**.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/contract/test_strategist_prompt_layout.py`:

```python
def test_analyst_block_renders_horizon_precursor():
    """Each analyst header is followed by an honest, mechanistic horizon line."""
    from contract.strategist_prompt import _render_analyst
    from contract.evidence import AnalystEvidence, AnalystVerdict

    ev = AnalystEvidence(
        analyst="technical",
        ticker="AAPL",
        tick_id="t1",
        recorded_at="2025-09-02T00:00:00",
        verdict=AnalystVerdict(
            lean="bearish", magnitude=0.4, confidence=0.7,
            rationale="reversal_up_fade", key_factors=["reversal_up_fade"],
            is_no_data=False, horizon_days=7,
        ),
        features={"rsi_14": 55.0},
    )

    block = _render_analyst("technical", ev)

    assert "horizon: ~7d" in block
    assert "mean-reversion" in block  # mechanistic prose, not a directive
```

(Confirm `AnalystEvidence`'s required constructor fields by reading `evidence.py` before
finalising the fixture — mirror the shape already used elsewhere in this test module.)

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py::test_analyst_block_renders_horizon_precursor -v`
Expected: FAIL — no horizon line rendered.

- [ ] **Step 3: Add the mechanistic-prose map**

In `src/contract/strategist_prompt.py`, after the `_ANALYST_ORDER` definition:

```python
# Mechanistic, non-prescriptive prose describing what each analyst's horizon
# MEANS — NOT a trust ranking and NOT an instruction to hold.  Rendered next to
# the analyst's own ``horizon_days`` so the strategist can reason about how long
# a lean is expected to stay live before its edge decays.  ``{h}`` is filled
# with the verdict's ``horizon_days``.
_HORIZON_PROSE: dict[str, str] = {
    "technical":   "short-term mean-reversion read; its edge decays within ~{h} trading days",
    "fundamental": "filing-delta drift; plays out over ~{h} trading days (3-6 months)",
    "news":        "post-news drift; typically live for ~{h} trading days after the surprise",
}
```

- [ ] **Step 4: Render the precursor in `_render_analyst`**

In `_render_analyst`, immediately after the `lines: list[str] = [header]` line, add:

```python
    # ── Horizon precursor ────────────────────────────────────────────────────
    # Surface the analyst's OWN horizon as honest, mechanistic context.  This is
    # the churn root-cause fix: the strategist was horizon-blind, so a 3-6 month
    # fundamental lean and a ~1-week news lean read as equally urgent.  The prose
    # is descriptive (how long the edge lasts), never prescriptive (it does not
    # tell the strategist to hold).
    prose_tmpl = _HORIZON_PROSE.get(name)
    if prose_tmpl is not None:
        prose = prose_tmpl.format(h=v.horizon_days)
        lines.append(f"  horizon: ~{v.horizon_days}d — {prose}")
```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v`
Expected: PASS. Update any exact-snapshot layout test to include the new horizon line.

- [ ] **Step 6: Commit**

```bash
git add src/contract/strategist_prompt.py tests/unit/contract/test_strategist_prompt_layout.py
git commit -m "feat(strategist): render honest per-analyst horizon precursors

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Strategist prompt — reading-the-reads prose + hygiene sweep

**Files:**
- Modify: `src/agents/strategist/prompts.py` (`_RAW_INSTRUCTION`)
- Test: `tests/unit/agents/strategist/` (add a focused prompt-content test; create the file
  if the directory has none — mirror an existing strategist test's imports)

**Interfaces:**
- Consumes: nothing new. Produces: a `## Reading the technical reads and analyst horizons`
  subsection teaching the strategist how to read the reversal lean, the vol-regime z, the
  trend-state number, and the horizon precursors — mechanistically and non-prescriptively.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_prompt_content.py`:

```python
"""Content guards for the strategist instruction template."""
from agents.strategist.prompts import STRATEGIST_INSTRUCTION


def test_prompt_explains_the_three_technical_reads_and_horizons():
    """The strategist prompt teaches the three technical reads and horizon precursors."""
    instr = STRATEGIST_INSTRUCTION

    assert "Reading the technical reads and analyst horizons" in instr
    # The three reads named.
    assert "reversal" in instr.lower()
    assert "Volatility regime" in instr
    assert "200d MA" in instr
    # Horizons framed as information, not a hold instruction.
    assert "horizon" in instr.lower()


def test_prompt_has_no_duplicate_holding_discipline_header():
    """Prompt hygiene: the holding-discipline guidance appears exactly once."""
    assert STRATEGIST_INSTRUCTION.count("### Holding discipline") == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_prompt_content.py -v`
Expected: FAIL on the first test (new section absent).

- [ ] **Step 3: Add the reading-the-reads subsection**

In `src/agents/strategist/prompts.py`, in `_RAW_INSTRUCTION`, immediately after the
`## Reading analyst reports` block (after the `...call out the disagreement in your
rationale when you do.` paragraph and its trailing blank line), insert:

```text
## Reading the technical reads and analyst horizons

The technical analyst now gives you three INDEPENDENT reads — do not expect
them to agree, and do not average them:

- **Lean (short-term reversal).** The technical lean is a CONTRARIAN 5-10 day
  mean-reversion call: it leans against the recent short-term move (a sharp
  recent rise reads bearish, a sharp drop reads bullish). It is the analyst's
  one directional headline. Its edge is short — see its horizon line.
- **Volatility regime (z).** A self-relative risk read: how stressed this
  ticker's volatility is versus its own recent history. Elevated regime is a
  reason to size smaller and widen your tolerance, not a directional signal.
- **Trend vs 200d MA.** Persistent structural context: above the 200-day MA is
  a structural up-trend, below is a down-trend. It frames the reversal lean; it
  does not override it.

Each analyst also prints a ``horizon:`` line — how long that analyst expects
its lean to stay live before the edge decays. This is INFORMATION, not a hold
rule: a ~3-6 month fundamental lean and a ~1-week news lean should not be
churned on the same cadence. When a short-horizon lean fades, that is the edge
expiring, not new evidence — do not trade against a still-live longer-horizon
thesis just because a shorter one has rolled off. Weigh the horizons; do not
obey them.
```

- [ ] **Step 4: Hygiene sweep — remove stale/duplicate artifacts**

Read `_RAW_INSTRUCTION` end-to-end and apply only genuine de-duplication (do not rewrite
working guidance):

- Confirm `### Holding discipline` and `### Conviction-weighted position sizing` each appear
  exactly once (the content test guards the former).
- The `### Forbidden fields by verb` line already states `horizon` no longer exists — leave
  it; it is correct and now consistent with the emit-schema change in Task 6.
- If the sweep finds no other true duplicate, that is a valid outcome — record "no further
  duplicates found" in the commit body rather than inventing edits.

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_prompt_content.py -v`
Expected: PASS.

- [ ] **Step 6: Full-suite regression check**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`
Expected: green. Investigate any red — likely an exact-snapshot prompt/layout test needing
its expected value refreshed for the new horizon line or technical bullets.

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/agents/strategist/test_prompt_content.py
git commit -m "feat(strategist): teach the three technical reads + horizon precursors; prompt hygiene

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (completed by the plan author)

**1. Spec coverage** — the four confirmed scope items all map to tasks:
- (A) Technical rebuild → Tasks 1 (config), 2 (features), 3 (verdict), 4 (bullets).
- (B) Horizon calibration ourselves → Tasks 5 (news config), 6 (schema + joiners), 7/8
  (strip self-report).
- (C) Strategist horizon precursors → Tasks 9 (per-analyst horizon line) + 10 (reading
  prose).
- (D) Prompt hygiene sweep → Tasks 7, 8, and 10 Step 4.
- Explicitly **out** (confirmed dropped): enforced holding gate, `review_due_tick`,
  `thesis_break`, risk-gate veto, `_verb_dispatch` gate, any new stance/thesis field.

**2. Placeholder scan** — every code step carries complete code; every command has an
expected result. Three points intentionally defer to a read-then-confirm (they are
verification steps, not placeholders): Task 6 Step 1 (cached-verdict assumption), the
`Driver`/`AnalystReport` class-name confirmation in Tasks 6/9, and `AnalystEvidence` fixture
shape in Task 9 — each names exactly what to check and where.

**3. Type consistency** — `derive_technical_verdict(features, h)` signature unchanged;
`to_ticker_verdict(*, horizon_days: int)` used identically at both joiners;
`reversal_horizon_days`/`vol_regime_window`/`vol_regime_elevated_z` field names match
between the schema (Task 1), the extractor (Task 2), and the verdict (Task 3); the two new
feature keys `vol_regime_z`/`trend_state` match between extractor, verdict tags, and
strategist bullets; `_HORIZON_PROSE` keys match `_ANALYST_ORDER`.

---

## Execution Handoff

**Consequential-decision flag (per user-global CLAUDE.md):** Task 1 **deletes** thirteen
`TechnicalHeuristics` fields and Task 6 **removes** `horizon_days` from the LLM emit schema.
Both are deliberate and reversible, but they are structural — confirm you are happy with the
retirement list (Task 1) and the no-cached-verdicts assumption (Task 6 Step 1) before
execution begins.

Plan complete and saved to
`docs/Phase14-analyst-refactor/plans/plan3b-technical-reads-and-horizon-precursors.md`.
Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between
tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch
execution with checkpoints for review.

Which approach?
