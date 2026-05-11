# Plan B — Per-Analyst Extractors with Dual-Emit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained — a fresh subagent should be able to pick it up cold using only this file + the spec at `docs/Phase4-stratergist-and-analysts/spec.md`.

**Goal:** Add a deterministic feature extractor for each of the four analysts (technical, fundamental, sentiment, smart_money) and have each analyst agent emit *both* the legacy `<Analyst>Signal` AND the new `AnalystEvidence` to session state. Strategist still consumes only the legacy signals — Plan C is what flips that.

**Architecture:** New `src/contract/extractors/` package — one extractor per analyst, each takes the same upstream data the analyst's fetch callback already pulls (state key `state["{analyst}_data"]`) and returns a `dict[str, float]` of features. A new shared after-callback factory `make_dual_emit_callback` chains the existing exhaustive validator with an evidence-building step. Each analyst agent gains the new after-callback wired up.

**Translation from legacy `AnalystSignal` → new `AnalystVerdict`:**
- `direction` → `lean` (1:1, identical literal values).
- `confidence` → `confidence` (1:1).
- `key_factors` → `key_factors` (carried as a structured list — DO NOT collapse into rationale; this is the substrate the future knowledge-base will pattern-match against).
- `rationale` (new field, not in legacy `AnalystSignal`) is rendered from `key_factors` joined by `" | "`, truncated to 160 chars. This keeps the human-readable string for prompts while the structured list survives for the KB.
- `magnitude` (new field, not in legacy) is set equal to `confidence` during dual-emit. The legacy schema can't distinguish "how-far-from-neutral" from "how-sure"; Plan D's evidence-only callback re-prompts the LLM for a real magnitude. Treat the dual-emit value as a placeholder.
- `is_no_data` is derived from the extractor's `is_no_data` feature (smart_money only).

The `AnalystEvidence` envelope gains `tick_id` (from `state["tick_id"]`) and `recorded_at` (now), plus `feature_warnings` (empty list during dual-emit — the legacy fetch callbacks don't surface warnings; Plan D's per-extractor warnings get plumbed through there).

**Tech Stack:** Python 3.11+, Pydantic v2, pandas + pandas-ta (added in Plan A), pytest.

**Reference reading before starting:**
- `docs/Phase4-stratergist-and-analysts/spec.md` — feature catalogue table (locked per analyst)
- `src/agents/analysts/_common.py` — `AnalystSignal` shape, `make_exhaustive_validator` pattern
- `src/agents/analysts/{technical,fundamental,sentiment,smart_money}/{agent,fetch,schema,prompts}.py` — existing analyst structure
- `src/contract/{evidence,ticker_evidence,digest}.py` — types added in Plan A
- `src/data/models/` — provider data shapes (e.g. `market.py`, `filings.py`, `news.py`, `sentiment.py`, `trades.py`)

**Project conventions:**
- PYTHONPATH root = `src/`. Import as `from contract.extractors.technical import extract_technical_features`.
- Run pytest as `.venv/bin/python -m pytest` on Linux/macOS, or `.venv\Scripts\python -m pytest` on Windows.
- One commit per task. Conventional Commits prefixes.

**Pre-requisites:** Plan A merged.

---

## Task B1: Add `make_dual_emit_callback` shared helper

**Files:**
- Modify: `src/agents/analysts/_common.py`
- Create: `tests/unit/agents/analysts/__init__.py` (empty if missing)
- Create: `tests/unit/agents/analysts/test_dual_emit.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/analysts/test_dual_emit.py`:
```python
"""Dual-emit callback tests — Tier 1, no LLM."""
from __future__ import annotations

from typing import Any

import pytest

from agents.analysts._common import AnalystSignal, make_dual_emit_callback
from contract.evidence import AnalystEvidence


class _State(dict):
    pass


class _Ctx:
    def __init__(self, state: dict):
        self.state = state


def _fake_extractor(raw: Any, ticker: str) -> dict[str, float]:
    """Toy extractor: returns one feature key per ticker for assertion."""
    return {"toy_feature": 1.0}


def _state_with(tickers, signals, data) -> _State:
    return _State(
        tick_id="2026-05-08T14:00:00Z",
        tickers=tickers,
        technical_signals=signals,
        technical_data=data,
    )


def test_dual_emit_writes_evidence_for_each_signal():
    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.7,
                          key_factors=["RSI 42"]).model_dump(),
            AnalystSignal(ticker="MSFT", direction="neutral", confidence=0.4).model_dump(),
        ],
        {"AAPL": {"x": 1}, "MSFT": {"x": 2}},
    )
    callback = make_dual_emit_callback(
        analyst="technical",
        signals_key="technical_signals",
        data_key="technical_data",
        evidence_key="technical_evidence",
        extractor=_fake_extractor,
    )
    out = callback(_Ctx(state))
    assert out is None  # no re-prompt — exhaustive

    evidence_list = state["technical_evidence"]
    assert len(evidence_list) == 2

    parsed = [AnalystEvidence.model_validate(e) for e in evidence_list]
    by_ticker = {e.ticker: e for e in parsed}
    assert by_ticker["AAPL"].verdict.lean == "bullish"
    assert by_ticker["AAPL"].verdict.confidence == 0.7
    # During dual-emit, magnitude proxies confidence (legacy can't separate them).
    assert by_ticker["AAPL"].verdict.magnitude == 0.7
    assert by_ticker["AAPL"].features == {"toy_feature": 1.0}
    assert by_ticker["AAPL"].analyst == "technical"
    assert by_ticker["AAPL"].tick_id == "2026-05-08T14:00:00Z"
    assert by_ticker["AAPL"].feature_warnings == []


def test_dual_emit_preserves_key_factors_as_structured_list():
    """Legacy AnalystSignal.key_factors must survive as a list on AnalystVerdict —
    NOT collapsed into rationale. This list is the future knowledge-base lookup
    primitive (backlog B2)."""
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.6,
                          key_factors=["RSI cooled", "uptrend intact", "volume up"]).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    cb(_Ctx(state))

    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert ev.verdict.key_factors == ["RSI cooled", "uptrend intact", "volume up"]
    # Rationale is the joined factors (for prompt readability)
    assert "RSI cooled" in ev.verdict.rationale
    assert "uptrend intact" in ev.verdict.rationale


def test_dual_emit_truncates_rationale_to_160_chars():
    long_factor = "x" * 200
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.5,
                          key_factors=[long_factor]).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert len(ev.verdict.rationale) <= 160


def test_dual_emit_reprompts_on_missing_tickers():
    """If the LLM missed tickers, we still re-prompt rather than silently filling."""
    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.7).model_dump(),
        ],
        {"AAPL": {}, "MSFT": {}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _fake_extractor)
    out = cb(_Ctx(state))
    # Re-prompt content for missing MSFT
    assert out is not None
    assert "MSFT" in out.parts[0].text
    # No evidence written when re-prompting
    assert state.get("technical_evidence") in (None, [])


def test_dual_emit_handles_empty_features_gracefully():
    """If extractor returns {}, evidence still validates with empty features."""
    state = _state_with(
        ["AAPL"],
        [
            AnalystSignal(ticker="AAPL", direction="neutral", confidence=0.0).model_dump(),
        ],
        {"AAPL": {}},
    )
    cb = make_dual_emit_callback(
        "technical", "technical_signals", "technical_data", "technical_evidence",
        extractor=lambda raw, ticker: {},
    )
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["technical_evidence"][0])
    assert ev.features == {}


def test_dual_emit_smart_money_no_data_flag_propagates():
    """smart_money extractor's `is_no_data` feature must set the verdict's
    is_no_data flag so the digest aggregator drops the verdict from voting."""
    state = _State(
        tick_id="t",
        tickers=["AAPL"],
        smart_money_signals=[
            AnalystSignal(ticker="AAPL", direction="neutral", confidence=0.0).model_dump(),
        ],
        smart_money_data={"AAPL": {}},
    )
    cb = make_dual_emit_callback(
        analyst="smart_money",
        signals_key="smart_money_signals",
        data_key="smart_money_data",
        evidence_key="smart_money_evidence",
        extractor=lambda raw, ticker: {"is_no_data": 1.0},
    )
    cb(_Ctx(state))
    ev = AnalystEvidence.model_validate(state["smart_money_evidence"][0])
    assert ev.verdict.is_no_data is True


def test_dual_emit_isolates_ticker_data_to_extractor():
    """Extractor is called with the per-ticker slice of `state[data_key]`, not the whole dict."""
    seen: list = []

    def _spy(raw: Any, ticker: str) -> dict[str, float]:
        seen.append((ticker, raw))
        return {}

    state = _state_with(
        ["AAPL", "MSFT"],
        [
            AnalystSignal(ticker="AAPL", direction="bullish", confidence=0.5).model_dump(),
            AnalystSignal(ticker="MSFT", direction="bearish", confidence=0.5).model_dump(),
        ],
        {"AAPL": {"price": 100}, "MSFT": {"price": 200}},
    )
    cb = make_dual_emit_callback("technical", "technical_signals", "technical_data",
                                  "technical_evidence", _spy)
    cb(_Ctx(state))
    by_ticker = dict(seen)
    assert by_ticker["AAPL"] == {"price": 100}
    assert by_ticker["MSFT"] == {"price": 200}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/test_dual_emit.py -v`
Expected: FAIL with `ImportError: cannot import name 'make_dual_emit_callback'`.

- [ ] **Step 3: Add `make_dual_emit_callback` to `_common.py`**

Open `src/agents/analysts/_common.py`. Add to the existing file (do NOT replace — keep `AnalystSignal` and `make_exhaustive_validator`):

```python
# ── Dual-emit (legacy AnalystSignal + new AnalystEvidence) ────────────────────

from datetime import datetime, timezone
from typing import Callable

from contract.evidence import AnalystEvidence, AnalystName, AnalystVerdict


def make_dual_emit_callback(
    analyst: AnalystName,
    signals_key: str,
    data_key: str,
    evidence_key: str,
    extractor: Callable[[Any, str], dict[str, float]],
):
    """Return an `after_agent_callback` that:

    1. Validates exhaustiveness (re-prompts if any watchlist ticker missing).
    2. For each emitted legacy `<Analyst>Signal`, runs the per-ticker feature
       extractor against `state[data_key][ticker]` and builds an `AnalystEvidence`
       in the new richer shape.
    3. Writes the evidence list to `state[evidence_key]` (alongside the existing
       `state[signals_key]` — both keys coexist for the duration of dual-emit).

    Translation rules (legacy `AnalystSignal` → new `AnalystVerdict`):
      - `direction`     → `lean`               (1:1)
      - `confidence`    → `confidence`         (1:1)
      - `key_factors`   → `key_factors`        (carried as a list — KB primitive)
      - `key_factors`   → `rationale`          (joined string for prompt readability,
                                                truncated to 160 chars)
      - (no source)     → `magnitude`          (set equal to confidence — placeholder
                                                until Plan D's evidence-only callback
                                                re-prompts the LLM for a real value)
      - extractor's `is_no_data` feature → `verdict.is_no_data`

    The legacy signal stays untouched; downstream consumers (`attribution_writer`,
    `memory_writer`) still read it. Plan C will start reading `state[evidence_key]`
    in the strategist; Plan D drops the legacy path entirely.
    """
    exhaustive = make_exhaustive_validator(signals_key)

    def _callback(callback_context: CallbackContext) -> Optional[genai_types.Content]:
        # 1) Exhaustiveness check first
        out = exhaustive(callback_context)
        if out is not None:
            return out

        # 2) Build evidence list
        state = callback_context.state
        signals_raw = state.get(signals_key, []) or []
        per_ticker_data = state.get(data_key, {}) or {}
        tick_id = state.get("tick_id", "unknown")
        recorded_at = datetime.now(tz=timezone.utc)

        evidence_list: list[dict] = []
        for sig in signals_raw:
            sig_dict = sig if isinstance(sig, dict) else sig.model_dump()
            ticker = sig_dict["ticker"]
            features = extractor(per_ticker_data.get(ticker, {}), ticker)
            key_factors = list(sig_dict.get("key_factors", []) or [])

            rationale = " | ".join(key_factors)
            if not rationale:
                rationale = f"{analyst} {sig_dict['direction']}"
            rationale = rationale[:160]

            confidence = float(sig_dict["confidence"])
            evidence = AnalystEvidence(
                ticker=ticker,
                analyst=analyst,
                tick_id=tick_id,
                recorded_at=recorded_at,
                features=features,
                feature_warnings=[],
                verdict=AnalystVerdict(
                    lean=sig_dict["direction"],
                    magnitude=confidence,
                    confidence=confidence,
                    rationale=rationale,
                    key_factors=key_factors[:8],
                    is_no_data=bool(features.get("is_no_data", 0.0) >= 1.0),
                ),
            )
            evidence_list.append(evidence.model_dump(mode="json"))

        state[evidence_key] = evidence_list
        return None

    return _callback
```

If `tests/unit/agents/analysts/__init__.py` does not exist, create it empty.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/test_dual_emit.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/_common.py tests/unit/agents/analysts/__init__.py tests/unit/agents/analysts/test_dual_emit.py
git commit -m "feat(analysts): add make_dual_emit_callback helper for legacy + evidence emit"
```

---

## Task B2: Implement `extractors/technical.py`

**Files:**
- Create: `src/contract/extractors/__init__.py` (empty)
- Create: `src/contract/extractors/technical.py`
- Create: `tests/unit/contract/extractors/__init__.py` (empty)
- Create: `tests/fixtures/contract/__init__.py` (empty if missing)
- Create: `tests/fixtures/contract/technical_aapl.json` (captured-data fixture)
- Create: `tests/unit/contract/extractors/test_technical.py`

- [ ] **Step 1: Write the failing test**

Create the fixture `tests/fixtures/contract/technical_aapl.json` (a captured snapshot of what `state["technical_data"]["AAPL"]` would contain — a `StockStats` dump). Keep it small and synthetic but plausible:
```json
{
  "ticker": "AAPL",
  "price_history": [
    {"date": "2026-04-08", "open": 170.0, "high": 172.0, "low": 169.0, "close": 171.5, "volume": 50000000},
    {"date": "2026-04-09", "open": 171.5, "high": 173.0, "low": 170.5, "close": 172.8, "volume": 48000000},
    {"date": "2026-04-10", "open": 172.8, "high": 174.5, "low": 172.0, "close": 174.0, "volume": 52000000},
    {"date": "2026-04-11", "open": 174.0, "high": 175.5, "low": 173.5, "close": 175.0, "volume": 49000000},
    {"date": "2026-04-14", "open": 175.0, "high": 176.0, "low": 174.0, "close": 175.5, "volume": 47000000},
    {"date": "2026-04-15", "open": 175.5, "high": 177.0, "low": 174.5, "close": 176.5, "volume": 51000000},
    {"date": "2026-04-16", "open": 176.5, "high": 178.0, "low": 176.0, "close": 177.5, "volume": 50000000},
    {"date": "2026-04-17", "open": 177.5, "high": 179.0, "low": 177.0, "close": 178.5, "volume": 53000000},
    {"date": "2026-04-18", "open": 178.5, "high": 180.0, "low": 178.0, "close": 179.5, "volume": 49000000},
    {"date": "2026-04-21", "open": 179.5, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50000000},
    {"date": "2026-04-22", "open": 180.5, "high": 182.0, "low": 180.0, "close": 181.5, "volume": 52000000},
    {"date": "2026-04-23", "open": 181.5, "high": 183.0, "low": 181.0, "close": 182.5, "volume": 51000000},
    {"date": "2026-04-24", "open": 182.5, "high": 184.0, "low": 182.0, "close": 183.5, "volume": 53000000},
    {"date": "2026-04-25", "open": 183.5, "high": 185.0, "low": 183.0, "close": 184.5, "volume": 50000000},
    {"date": "2026-04-28", "open": 184.5, "high": 186.0, "low": 184.0, "close": 185.5, "volume": 52000000},
    {"date": "2026-04-29", "open": 185.5, "high": 187.0, "low": 185.0, "close": 186.5, "volume": 51000000},
    {"date": "2026-04-30", "open": 186.5, "high": 188.0, "low": 186.0, "close": 187.5, "volume": 53000000},
    {"date": "2026-05-01", "open": 187.5, "high": 189.0, "low": 187.0, "close": 188.5, "volume": 50000000},
    {"date": "2026-05-02", "open": 188.5, "high": 190.0, "low": 188.0, "close": 189.5, "volume": 52000000},
    {"date": "2026-05-05", "open": 189.5, "high": 191.0, "low": 189.0, "close": 190.5, "volume": 51000000},
    {"date": "2026-05-06", "open": 190.5, "high": 192.0, "low": 190.0, "close": 191.5, "volume": 53000000},
    {"date": "2026-05-07", "open": 191.5, "high": 193.0, "low": 191.0, "close": 192.5, "volume": 50000000},
    {"date": "2026-05-08", "open": 192.5, "high": 194.0, "low": 192.0, "close": 193.5, "volume": 52000000}
  ],
  "high_52w": 200.0,
  "low_52w": 150.0
}
```

Note: the fixture is intentionally a steady uptrend so RSI > 50 and `pct_change_5d > 0`. If your existing `StockStats` model uses different field names (check `src/data/models/market.py`), adjust the fixture and the extractor in step 3 to match. The extractor accepts any dict shape — keys it doesn't recognise are ignored.

Create `tests/unit/contract/extractors/test_technical.py`:
```python
"""Technical feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.technical import extract_technical_features

FIXTURE = Path("tests/fixtures/contract/technical_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    expected = {
        "rsi_14", "pct_change_5d", "pct_change_20d",
        "vol_ratio_20d", "atr_pct_14",
        "dist_from_high_52w_pct", "dist_from_low_52w_pct",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_uptrend_fixture_has_positive_5d_change(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["pct_change_5d"] > 0


def test_uptrend_fixture_rsi_above_50(aapl_data):
    features = extract_technical_features(aapl_data, ticker="AAPL")
    # Steady uptrend should put RSI in the 50–100 range
    assert features["rsi_14"] > 50.0
    assert features["rsi_14"] <= 100.0


def test_dist_from_52w_high_negative(aapl_data):
    """Latest close (193.5) is below 52w high (200) → negative percent."""
    features = extract_technical_features(aapl_data, ticker="AAPL")
    assert features["dist_from_high_52w_pct"] < 0


def test_handles_empty_data_gracefully():
    """Empty data → all-zero features (no exception)."""
    features = extract_technical_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_short_history_gracefully():
    """Too few price bars to compute RSI(14) → returns 0.0 for indicators that need history."""
    short = {
        "ticker": "AAPL",
        "price_history": [
            {"date": "2026-05-07", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            {"date": "2026-05-08", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
        ],
    }
    features = extract_technical_features(short, ticker="AAPL")
    # Should not raise. RSI/ATR should be 0.0 (insufficient history).
    assert features["rsi_14"] == 0.0
    assert features["atr_pct_14"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'contract.extractors.technical'`.

- [ ] **Step 3: Write the extractor**

Create `src/contract/extractors/__init__.py` empty.

Create `src/contract/extractors/technical.py`:
```python
"""Technical analyst deterministic feature extractor.

Input: the dict that lives under `state["technical_data"][ticker]` — typically
a dump of the project's `StockStats` model. The function is forgiving about
field shape: missing keys default to no-data (0.0 features) rather than raising.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd
import pandas_ta as ta


_KEYS = (
    "rsi_14",
    "pct_change_5d",
    "pct_change_20d",
    "vol_ratio_20d",
    "atr_pct_14",
    "dist_from_high_52w_pct",
    "dist_from_low_52w_pct",
)


def _zero_features() -> dict[str, float]:
    return {k: 0.0 for k in _KEYS}


def _df_from_history(history: list[Mapping[str, Any]]) -> pd.DataFrame | None:
    if not history:
        return None
    df = pd.DataFrame(history)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return None
    df = df[list(needed)].astype(float)
    return df


def extract_technical_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Compute the locked technical feature catalogue from raw OHLCV history.

    Returns a `dict[str, float]` with exactly the keys in the spec.
    """
    out = _zero_features()
    if not raw:
        return out

    history = raw.get("price_history") or raw.get("history") or []
    df = _df_from_history(history)
    if df is None or len(df) < 2:
        return out

    close = df["close"]

    # pct change windows
    if len(close) > 5:
        out["pct_change_5d"] = float((close.iloc[-1] / close.iloc[-6]) - 1.0)
    if len(close) > 20:
        out["pct_change_20d"] = float((close.iloc[-1] / close.iloc[-21]) - 1.0)

    # RSI 14
    if len(close) >= 15:
        rsi = ta.rsi(close, length=14)
        if rsi is not None and not rsi.empty and not np.isnan(rsi.iloc[-1]):
            out["rsi_14"] = float(rsi.iloc[-1])

    # ATR 14 as % of last close
    if len(df) >= 15:
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        if atr is not None and not atr.empty and not np.isnan(atr.iloc[-1]):
            last_close = float(close.iloc[-1])
            if last_close > 0:
                out["atr_pct_14"] = float(atr.iloc[-1] / last_close * 100.0)

    # Volume ratio: avg vol over last 20d / avg vol over last 50d
    if len(df) >= 50:
        vol = df["volume"]
        v20 = float(vol.iloc[-20:].mean())
        v50 = float(vol.iloc[-50:].mean())
        if v50 > 0:
            out["vol_ratio_20d"] = v20 / v50

    # Distance from 52w high / low (in percent — negative = below high)
    high_52w = raw.get("high_52w")
    low_52w = raw.get("low_52w")
    last_close = float(close.iloc[-1])
    if high_52w and high_52w > 0:
        out["dist_from_high_52w_pct"] = float((last_close / high_52w - 1.0) * 100.0)
    if low_52w and low_52w > 0:
        out["dist_from_low_52w_pct"] = float((last_close / low_52w - 1.0) * 100.0)

    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_technical.py -v`
Expected: PASS (7 tests).

If a test fails because the `StockStats` model your codebase actually uses has different field names (e.g. `bars` instead of `price_history`), update both the fixture and the extractor's lookups. Keep the locked output keys unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/__init__.py src/contract/extractors/technical.py tests/unit/contract/extractors/__init__.py tests/fixtures/contract/__init__.py tests/fixtures/contract/technical_aapl.json tests/unit/contract/extractors/test_technical.py
git commit -m "feat(contract): add technical feature extractor"
```

---

## Task B3: Implement `extractors/fundamental.py`

**Files:**
- Create: `src/contract/extractors/fundamental.py`
- Create: `tests/fixtures/contract/fundamental_aapl.json`
- Create: `tests/unit/contract/extractors/test_fundamental.py`

- [ ] **Step 1: Write the fixture + test**

Create `tests/fixtures/contract/fundamental_aapl.json`. Use the keys from your project's fundamentals model — common shapes look like:
```json
{
  "ticker": "AAPL",
  "trailing_pe": 28.5,
  "forward_pe": 26.0,
  "peg": 2.1,
  "revenue_growth_yoy": 0.045,
  "profit_margin": 0.247,
  "debt_to_equity": 1.82,
  "free_cash_flow": 95000000000,
  "market_cap": 3000000000000,
  "return_on_equity": 1.45,
  "analyst_rating_avg": 4.1
}
```

Adjust the field names to match `src/data/models/`'s actual fundamentals shape — find with `Grep "trailing_pe|profit_margin" src/data/models/`. The extractor's field-lookup code (step 3) and the fixture must agree.

Create `tests/unit/contract/extractors/test_fundamental.py`:
```python
"""Fundamental feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.fundamental import extract_fundamental_features

FIXTURE = Path("tests/fixtures/contract/fundamental_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = {
        "pe_trailing", "pe_forward", "peg",
        "revenue_growth_yoy", "profit_margin", "debt_to_equity",
        "fcf_yield_pct", "roe", "analyst_rating_avg",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    for k, v in features.items():
        assert isinstance(v, float), f"{k} = {v!r}"


def test_pe_values_carried_through(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    assert features["pe_trailing"] == pytest.approx(28.5)
    assert features["pe_forward"] == pytest.approx(26.0)


def test_fcf_yield_computed_from_fcf_and_market_cap(aapl_data):
    features = extract_fundamental_features(aapl_data, ticker="AAPL")
    expected = (95_000_000_000 / 3_000_000_000_000) * 100
    assert features["fcf_yield_pct"] == pytest.approx(expected, rel=0.01)


def test_handles_empty_data_gracefully():
    features = extract_fundamental_features({}, ticker="AAPL")
    for v in features.values():
        assert v == 0.0


def test_handles_zero_market_cap_in_fcf_yield():
    features = extract_fundamental_features(
        {"free_cash_flow": 1_000_000, "market_cap": 0}, ticker="AAPL"
    )
    assert features["fcf_yield_pct"] == 0.0  # no divide-by-zero
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_fundamental.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the extractor**

Create `src/contract/extractors/fundamental.py`:
```python
"""Fundamental analyst deterministic feature extractor."""
from __future__ import annotations

from typing import Any, Mapping

_KEYS = (
    "pe_trailing", "pe_forward", "peg",
    "revenue_growth_yoy", "profit_margin", "debt_to_equity",
    "fcf_yield_pct", "roe", "analyst_rating_avg",
)


def _zero_features() -> dict[str, float]:
    return {k: 0.0 for k in _KEYS}


def _f(value: Any) -> float:
    """Coerce to float, returning 0.0 on None / non-numeric."""
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN check
        return 0.0
    return f


def extract_fundamental_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Pull the locked fundamental feature catalogue from raw fundamentals dict."""
    out = _zero_features()
    if not raw:
        return out

    out["pe_trailing"]        = _f(raw.get("trailing_pe") or raw.get("pe_trailing"))
    out["pe_forward"]         = _f(raw.get("forward_pe") or raw.get("pe_forward"))
    out["peg"]                = _f(raw.get("peg"))
    out["revenue_growth_yoy"] = _f(raw.get("revenue_growth_yoy") or raw.get("revenue_growth"))
    out["profit_margin"]      = _f(raw.get("profit_margin"))
    out["debt_to_equity"]     = _f(raw.get("debt_to_equity"))
    out["roe"]                = _f(raw.get("return_on_equity") or raw.get("roe"))
    out["analyst_rating_avg"] = _f(raw.get("analyst_rating_avg"))

    # FCF yield = FCF / market_cap (as percent). Guard against zero market cap.
    fcf = _f(raw.get("free_cash_flow") or raw.get("fcf"))
    mcap = _f(raw.get("market_cap"))
    if mcap > 0:
        out["fcf_yield_pct"] = fcf / mcap * 100.0

    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_fundamental.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/fundamental.py tests/fixtures/contract/fundamental_aapl.json tests/unit/contract/extractors/test_fundamental.py
git commit -m "feat(contract): add fundamental feature extractor"
```

---

## Task B4: Implement `extractors/sentiment.py`

**Files:**
- Create: `src/contract/extractors/sentiment.py`
- Create: `tests/fixtures/contract/sentiment_aapl.json`
- Create: `tests/unit/contract/extractors/test_sentiment.py`

- [ ] **Step 1: Write the fixture + test**

Create `tests/fixtures/contract/sentiment_aapl.json` (adjust field names to match `src/data/models/news.py` / `sentiment.py`):
```json
{
  "ticker": "AAPL",
  "news_items": [
    {"published": "2026-05-08T12:00:00Z", "headline": "AAPL beats earnings", "polarity": 0.8},
    {"published": "2026-05-07T09:00:00Z", "headline": "AAPL launches new product", "polarity": 0.6},
    {"published": "2026-05-06T15:00:00Z", "headline": "Analyst raises target", "polarity": 0.5},
    {"published": "2026-05-05T11:00:00Z", "headline": "AAPL faces lawsuit", "polarity": -0.4},
    {"published": "2026-05-04T10:00:00Z", "headline": "Steady quarter ahead", "polarity": 0.1},
    {"published": "2026-05-03T08:00:00Z", "headline": "Supply chain stable", "polarity": 0.2},
    {"published": "2026-05-02T14:00:00Z", "headline": "Mixed analyst day", "polarity": 0.0}
  ],
  "social_volume_z": 1.4
}
```

Create `tests/unit/contract/extractors/test_sentiment.py`:
```python
"""Sentiment feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.sentiment import extract_sentiment_features

FIXTURE = Path("tests/fixtures/contract/sentiment_aapl.json")


@pytest.fixture
def aapl_data():
    return json.loads(FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    expected = {
        "news_count_7d", "pct_news_positive_7d", "pct_news_negative_7d",
        "headline_polarity_mean_7d", "social_volume_z",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_news_count_matches_fixture(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    assert features["news_count_7d"] == 7.0


def test_positive_share_calculated(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    # 5 of 7 items have polarity > 0 → ~71%
    assert features["pct_news_positive_7d"] == pytest.approx(5 / 7 * 100, rel=0.01)


def test_polarity_mean(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    polarities = [0.8, 0.6, 0.5, -0.4, 0.1, 0.2, 0.0]
    assert features["headline_polarity_mean_7d"] == pytest.approx(sum(polarities) / len(polarities), rel=0.01)


def test_social_volume_z_passthrough(aapl_data):
    features = extract_sentiment_features(aapl_data, ticker="AAPL")
    assert features["social_volume_z"] == pytest.approx(1.4)


def test_handles_empty_news():
    features = extract_sentiment_features({"news_items": []}, ticker="AAPL")
    assert features["news_count_7d"] == 0.0
    assert features["pct_news_positive_7d"] == 0.0
    assert features["headline_polarity_mean_7d"] == 0.0


def test_handles_missing_social_volume():
    """social_volume_z is optional — defaults to 0.0 when no provider supplies it."""
    features = extract_sentiment_features({"news_items": []}, ticker="AAPL")
    assert features["social_volume_z"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_sentiment.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the extractor**

Create `src/contract/extractors/sentiment.py`:
```python
"""Sentiment analyst deterministic feature extractor."""
from __future__ import annotations

from typing import Any, Mapping

_KEYS = (
    "news_count_7d",
    "pct_news_positive_7d",
    "pct_news_negative_7d",
    "headline_polarity_mean_7d",
    "social_volume_z",
)


def _zero_features() -> dict[str, float]:
    return {k: 0.0 for k in _KEYS}


def extract_sentiment_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Compute the sentiment feature catalogue from raw news + sentiment data.

    Caller is expected to have already filtered news_items to the last 7 days
    (the analyst's fetch callback is the right place for that). This function
    just summarises whatever it's given.
    """
    out = _zero_features()
    if not raw:
        return out

    items = raw.get("news_items") or raw.get("news") or []
    n = len(items)
    out["news_count_7d"] = float(n)

    if n > 0:
        polarities: list[float] = []
        positives = 0
        negatives = 0
        for item in items:
            try:
                p = float(item.get("polarity", 0.0))
            except (TypeError, ValueError):
                p = 0.0
            polarities.append(p)
            if p > 0:
                positives += 1
            elif p < 0:
                negatives += 1

        out["pct_news_positive_7d"] = positives / n * 100.0
        out["pct_news_negative_7d"] = negatives / n * 100.0
        out["headline_polarity_mean_7d"] = sum(polarities) / n

    sv = raw.get("social_volume_z")
    if sv is not None:
        try:
            out["social_volume_z"] = float(sv)
        except (TypeError, ValueError):
            out["social_volume_z"] = 0.0

    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_sentiment.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/sentiment.py tests/fixtures/contract/sentiment_aapl.json tests/unit/contract/extractors/test_sentiment.py
git commit -m "feat(contract): add sentiment feature extractor"
```

---

## Task B5: Implement `extractors/smart_money.py`

**Files:**
- Create: `src/contract/extractors/smart_money.py`
- Create: `tests/fixtures/contract/smart_money_aapl.json`
- Create: `tests/fixtures/contract/smart_money_no_data.json`
- Create: `tests/unit/contract/extractors/test_smart_money.py`

- [ ] **Step 1: Write the fixtures + test**

Create `tests/fixtures/contract/smart_money_aapl.json`:
```json
{
  "ticker": "AAPL",
  "filings": [
    {"filer_id": "P_PELOSI", "side": "BUY", "amount": 250000, "filed": "2026-04-30"},
    {"filer_id": "P_PELOSI", "side": "BUY", "amount": 100000, "filed": "2026-05-02"},
    {"filer_id": "P_TUBERVILLE", "side": "SELL", "amount": 50000, "filed": "2026-05-05"},
    {"filer_id": "P_CRENSHAW", "side": "BUY", "amount": 75000, "filed": "2026-05-06"}
  ]
}
```

Create `tests/fixtures/contract/smart_money_no_data.json`:
```json
{
  "ticker": "TSLA",
  "filings": []
}
```

Adjust field names to match `src/data/models/trades.py` if your provider uses different keys (e.g. `transactions` instead of `filings`).

Create `tests/unit/contract/extractors/test_smart_money.py`:
```python
"""Smart-money feature extractor tests — Tier 1, no LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.extractors.smart_money import extract_smart_money_features

AAPL_FIXTURE = Path("tests/fixtures/contract/smart_money_aapl.json")
NODATA_FIXTURE = Path("tests/fixtures/contract/smart_money_no_data.json")


@pytest.fixture
def aapl_data():
    return json.loads(AAPL_FIXTURE.read_text())


@pytest.fixture
def empty_data():
    return json.loads(NODATA_FIXTURE.read_text())


def test_extracts_required_keys(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    expected = {
        "n_politicians", "n_buys_30d", "n_sells_30d",
        "total_dollar_value_buys", "total_dollar_value_sells",
        "net_flow_dollar", "is_no_data",
    }
    assert set(features.keys()) == expected


def test_all_features_are_floats(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    for v in features.values():
        assert isinstance(v, float)


def test_unique_filer_count(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    # Three distinct filers in the fixture
    assert features["n_politicians"] == 3.0


def test_buy_sell_counts(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["n_buys_30d"] == 3.0
    assert features["n_sells_30d"] == 1.0


def test_dollar_totals(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["total_dollar_value_buys"] == 250_000 + 100_000 + 75_000
    assert features["total_dollar_value_sells"] == 50_000.0
    assert features["net_flow_dollar"] == (425_000 - 50_000)


def test_is_no_data_zero_when_filings_present(aapl_data):
    features = extract_smart_money_features(aapl_data, ticker="AAPL")
    assert features["is_no_data"] == 0.0


def test_is_no_data_one_when_no_filings(empty_data):
    features = extract_smart_money_features(empty_data, ticker="TSLA")
    assert features["is_no_data"] == 1.0
    assert features["n_politicians"] == 0.0
    assert features["n_buys_30d"] == 0.0
    assert features["total_dollar_value_buys"] == 0.0


def test_is_no_data_one_when_empty_dict():
    features = extract_smart_money_features({}, ticker="UNKNOWN")
    assert features["is_no_data"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_smart_money.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the extractor**

Create `src/contract/extractors/smart_money.py`:
```python
"""Smart-money analyst deterministic feature extractor.

Sparseness is the rule, not the exception — most tickers will have zero filings.
The `is_no_data` feature is the signal to the aggregator that this analyst's
verdict should be ignored for this ticker (`fill_missing` semantics in
`contract.digest`).
"""
from __future__ import annotations

from typing import Any, Mapping

_KEYS = (
    "n_politicians",
    "n_buys_30d",
    "n_sells_30d",
    "total_dollar_value_buys",
    "total_dollar_value_sells",
    "net_flow_dollar",
    "is_no_data",
)


def _zero_features() -> dict[str, float]:
    out = {k: 0.0 for k in _KEYS}
    out["is_no_data"] = 1.0  # default to no-data
    return out


def _amount(filing: Mapping[str, Any]) -> float:
    val = filing.get("amount") or filing.get("dollar_value") or 0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def extract_smart_money_features(raw: Mapping[str, Any], ticker: str) -> dict[str, float]:
    """Aggregate filings → counts + dollar totals + no-data flag.

    Caller is expected to have already filtered to the last 30 days; this
    function just summarises whatever it's given.
    """
    out = _zero_features()
    if not raw:
        return out

    filings = raw.get("filings") or raw.get("transactions") or []
    if not filings:
        return out

    out["is_no_data"] = 0.0

    filers: set[str] = set()
    n_buys = 0
    n_sells = 0
    total_buys = 0.0
    total_sells = 0.0

    for f in filings:
        filer = f.get("filer_id") or f.get("filer") or ""
        if filer:
            filers.add(str(filer))
        side = (f.get("side") or "").upper()
        amt = _amount(f)
        if side == "BUY":
            n_buys += 1
            total_buys += amt
        elif side == "SELL":
            n_sells += 1
            total_sells += amt

    out["n_politicians"]            = float(len(filers))
    out["n_buys_30d"]               = float(n_buys)
    out["n_sells_30d"]              = float(n_sells)
    out["total_dollar_value_buys"]  = total_buys
    out["total_dollar_value_sells"] = total_sells
    out["net_flow_dollar"]          = total_buys - total_sells

    return out
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/contract/extractors/test_smart_money.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/contract/extractors/smart_money.py tests/fixtures/contract/smart_money_aapl.json tests/fixtures/contract/smart_money_no_data.json tests/unit/contract/extractors/test_smart_money.py
git commit -m "feat(contract): add smart_money feature extractor with sparseness flag"
```

---

## Task B6: Wire the dual-emit callback into the technical analyst

**Files:**
- Modify: `src/agents/analysts/technical/agent.py`

- [ ] **Step 1: Replace the agent module**

Edit `src/agents/analysts/technical/agent.py` (full replacement):
```python
"""Technical analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.technical import extract_technical_features
from .fetch import technical_fetch_callback
from .prompts import TECHNICAL_INSTRUCTION
from .schema import TechnicalSignal


_after = make_dual_emit_callback(
    analyst="technical",
    signals_key="technical_signals",
    data_key="technical_data",
    evidence_key="technical_evidence",
    extractor=extract_technical_features,
)


technical_analyst = LlmAgent(
    name="TechnicalAnalyst",
    model="gemini-2.0-flash-001",
    instruction=TECHNICAL_INSTRUCTION,
    output_schema=list[TechnicalSignal],
    output_key="technical_signals",
    before_agent_callback=technical_fetch_callback,
    after_agent_callback=_after,
)


def _build_technical_analyst() -> LlmAgent:
    return LlmAgent(
        name="TechnicalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=TECHNICAL_INSTRUCTION,
        output_schema=list[TechnicalSignal],
        output_key="technical_signals",
        before_agent_callback=technical_fetch_callback,
        after_agent_callback=_after,
    )
```

- [ ] **Step 2: Run analyst tests for regression**

Run: `.venv/bin/python -m pytest tests/ -v -k "technical"`
Expected: All passing. The exhaustiveness behaviour is preserved by `make_dual_emit_callback` (it wraps the existing validator).

- [ ] **Step 3: Commit**

```bash
git add src/agents/analysts/technical/agent.py
git commit -m "feat(analyst-technical): dual-emit AnalystEvidence to state[technical_evidence]"
```

---

## Task B7: Wire the dual-emit callback into the fundamental analyst

**Files:**
- Modify: `src/agents/analysts/fundamental/agent.py`

- [ ] **Step 1: Replace the agent module**

Read the current `src/agents/analysts/fundamental/agent.py` first to confirm the `model` name, the `output_schema`, and the `before_agent_callback` it uses, then mirror Task B6's pattern. The replacement:
```python
"""Fundamental analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.fundamental import extract_fundamental_features
from .fetch import fundamental_fetch_callback
from .prompts import FUNDAMENTAL_INSTRUCTION
from .schema import FundamentalSignal


_after = make_dual_emit_callback(
    analyst="fundamental",
    signals_key="fundamental_signals",
    data_key="fundamental_data",
    evidence_key="fundamental_evidence",
    extractor=extract_fundamental_features,
)


fundamental_analyst = LlmAgent(
    name="FundamentalAnalyst",
    model="gemini-2.0-flash-001",
    instruction=FUNDAMENTAL_INSTRUCTION,
    output_schema=list[FundamentalSignal],
    output_key="fundamental_signals",
    before_agent_callback=fundamental_fetch_callback,
    after_agent_callback=_after,
)


def _build_fundamental_analyst() -> LlmAgent:
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=FUNDAMENTAL_INSTRUCTION,
        output_schema=list[FundamentalSignal],
        output_key="fundamental_signals",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=_after,
    )
```

If the existing module's import names differ (e.g. `fundamental_fetch` instead of `fundamental_fetch_callback`), keep the existing names — only swap in the new `after_agent_callback`.

- [ ] **Step 2: Run analyst tests**

Run: `.venv/bin/python -m pytest tests/ -v -k "fundamental"`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agents/analysts/fundamental/agent.py
git commit -m "feat(analyst-fundamental): dual-emit AnalystEvidence to state[fundamental_evidence]"
```

---

## Task B8: Wire the dual-emit callback into the sentiment analyst

**Files:**
- Modify: `src/agents/analysts/sentiment/agent.py`

- [ ] **Step 1: Replace the agent module**

Read the current `src/agents/analysts/sentiment/agent.py`, then apply the same pattern as Task B7:
```python
"""Sentiment analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.sentiment import extract_sentiment_features
from .fetch import sentiment_fetch_callback
from .prompts import SENTIMENT_INSTRUCTION
from .schema import SentimentSignal


_after = make_dual_emit_callback(
    analyst="sentiment",
    signals_key="sentiment_signals",
    data_key="sentiment_data",
    evidence_key="sentiment_evidence",
    extractor=extract_sentiment_features,
)


sentiment_analyst = LlmAgent(
    name="SentimentAnalyst",
    model="gemini-2.0-flash-001",
    instruction=SENTIMENT_INSTRUCTION,
    output_schema=list[SentimentSignal],
    output_key="sentiment_signals",
    before_agent_callback=sentiment_fetch_callback,
    after_agent_callback=_after,
)


def _build_sentiment_analyst() -> LlmAgent:
    return LlmAgent(
        name="SentimentAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SENTIMENT_INSTRUCTION,
        output_schema=list[SentimentSignal],
        output_key="sentiment_signals",
        before_agent_callback=sentiment_fetch_callback,
        after_agent_callback=_after,
    )
```

Match existing import names if they differ.

- [ ] **Step 2: Run analyst tests**

Run: `.venv/bin/python -m pytest tests/ -v -k "sentiment"`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agents/analysts/sentiment/agent.py
git commit -m "feat(analyst-sentiment): dual-emit AnalystEvidence to state[sentiment_evidence]"
```

---

## Task B9: Wire the dual-emit callback into the smart_money analyst

**Files:**
- Modify: `src/agents/analysts/smart_money/agent.py`

- [ ] **Step 1: Replace the agent module**

Read the current `src/agents/analysts/smart_money/agent.py` first, then mirror the pattern. Same structure as B7/B8 — the only difference is that `smart_money`'s data is sparse (most tickers have nothing). The extractor's `is_no_data` feature handles that; no extra logic needed in the agent.

```python
"""Smart-money analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.smart_money import extract_smart_money_features
from .fetch import smart_money_fetch_callback
from .prompts import SMART_MONEY_INSTRUCTION
from .schema import SmartMoneySignal


_after = make_dual_emit_callback(
    analyst="smart_money",
    signals_key="smart_money_signals",
    data_key="smart_money_data",
    evidence_key="smart_money_evidence",
    extractor=extract_smart_money_features,
)


smart_money_analyst = LlmAgent(
    name="SmartMoneyAnalyst",
    model="gemini-2.0-flash-001",
    instruction=SMART_MONEY_INSTRUCTION,
    output_schema=list[SmartMoneySignal],
    output_key="smart_money_signals",
    before_agent_callback=smart_money_fetch_callback,
    after_agent_callback=_after,
)


def _build_smart_money_analyst() -> LlmAgent:
    return LlmAgent(
        name="SmartMoneyAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SMART_MONEY_INSTRUCTION,
        output_schema=list[SmartMoneySignal],
        output_key="smart_money_signals",
        before_agent_callback=smart_money_fetch_callback,
        after_agent_callback=_after,
    )
```

Match existing import names if they differ.

- [ ] **Step 2: Run analyst tests**

Run: `.venv/bin/python -m pytest tests/ -v -k "smart_money"`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/agents/analysts/smart_money/agent.py
git commit -m "feat(analyst-smart_money): dual-emit AnalystEvidence to state[smart_money_evidence]"
```

---

## Task B10: Final regression pass

- [ ] **Step 1: Run all unit tests**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All passing.

- [ ] **Step 2: Run ruff**

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: zero new violations introduced by Plan B.

- [ ] **Step 3: Verify all four extractors import cleanly**

Run: `.venv/bin/python -c "from contract.extractors.technical import extract_technical_features; from contract.extractors.fundamental import extract_fundamental_features; from contract.extractors.sentiment import extract_sentiment_features; from contract.extractors.smart_money import extract_smart_money_features; from agents.analysts._common import make_dual_emit_callback; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Append graphify delta entry**

Edit `graphify-out/graph_delta.md`. Append at the end:
```markdown

## YYYY-MM-DD — Phase 4 Plan B: per-analyst extractors + dual-emit

Added deterministic feature extractors for all four analysts. Each analyst agent
now writes BOTH the legacy `<Analyst>Signal` (state["{analyst}_signals"]) and the
new `AnalystEvidence` (state["{analyst}_evidence"]). Strategist still consumes
only the legacy signals — Plan C flips that.

- New nodes: `src/contract/extractors/__init__.py`,
  `src/contract/extractors/{technical,fundamental,sentiment,smart_money}.py`
  (each defining `extract_{analyst}_features`).
- New edges:
  `agents.analysts._common.make_dual_emit_callback --uses--> AnalystEvidence + AnalystVerdict`;
  each analyst's `after_agent_callback` now wraps `make_dual_emit_callback` instead of
  `make_exhaustive_validator`.
- New state keys (write-only this plan): `technical_evidence`, `fundamental_evidence`,
  `sentiment_evidence`, `smart_money_evidence`.
- Legacy state keys still written + still read by `attribution_writer` /
  `memory_writer` (dual-emit phase).
- New fixtures: `tests/fixtures/contract/{technical_aapl,fundamental_aapl,sentiment_aapl,smart_money_aapl,smart_money_no_data}.json`.
```

Replace `YYYY-MM-DD` with today's date.

- [ ] **Step 5: Commit the delta entry**

```bash
git add graphify-out/graph_delta.md
git commit -m "docs(graphify): log Plan B per-analyst extractors + dual-emit"
```

---

## Done

Plan B merged. Each analyst still emits the legacy `<Analyst>Signal` shape — strategist + downstream agents see exactly the same inputs they did before. The new state keys `technical_evidence` / `fundamental_evidence` / `sentiment_evidence` / `smart_money_evidence` are populated but unused. Bot behaviour is unchanged.

**Next:** [Plan C — Strategist v2 against new contract](./plan-C-strategist-v2.md)
