# Plan D — Cleanup, Persistence, and Legacy Retirement

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained: a fresh subagent should be able to pick it up with only this plan file + the spec at `docs/Phase4-stratergist-and-analysts/spec.md` + the current repo state.

**Goal:** Retire the legacy `<Analyst>Signal` / `AttributionSignalsRow` / `attribution_writer` lineage. Persist `AnalystEvidence` and `TickerEvidence` rows so the new contract is durable in SQLite. Remove the dual-emit scaffolding installed in Plan B. End state: each analyst emits `AnalystEvidence` only; the strategist consumes `TickerEvidence` only; the DB stores both row types; nothing references the old signal schemas.

**Architecture:** Two new ORM rows (`AnalystEvidenceRow`, `TickerEvidenceRow`) and one new `BaseAgent` (`EvidenceWriter`) replace `AttributionWriter`. The four analysts stop dual-emitting — they now write only to `state["{analyst}_evidence"]`. `MemoryWriter` is migrated off the legacy `*_signals` keys. The legacy `<Analyst>Signal` Pydantic schemas, `AttributionSignalsRow`, `save_attribution_signal`, and the `src/agents/attribution/` package are deleted. The four superseded `docs/superpowers/` design + plan files are removed; the Phase 4 directory becomes the single source of truth.

**Tech Stack:** SQLAlchemy 2 ORM (`Mapped`/`mapped_column`), Google ADK `BaseAgent`, Pydantic v2, pytest, ruff.

**Pre-deployment context:** No live or paper bot is running. There is no production data in the SQLite DB to migrate — schema changes can land directly. No backwards-compatibility shims, no feature flags.

**Predecessor plans:** Plan A (`plan-A-contract-scaffolding.md`) and Plan B (`plan-B-extractors-dual-emit.md`) and Plan C (`plan-C-strategist-v2.md`) MUST all be merged before starting Plan D. After Plan C, the strategist consumes `state["ticker_evidence"]` (rendered string) and `state["ticker_evidence_objects"]` (list[TickerEvidence dump]); the four analysts still dual-emit (legacy `*_signals` + new `*_evidence`); `AttributionWriter` still persists the legacy `*_signals`. Plan D removes the dual-emit and rewires persistence.

**Absorbed hygiene items:** Plan D also picks up six follow-ups carried over from the Phase 4 chunk audits, because the files they touch overlap with Plan D's existing scope. These land in a consolidated `Task D9` at the end of the plan rather than being woven through D1–D8 (which keeps the existing TDD flow of those tasks intact). The full Phase-4 follow-up inventory lives in `post-phase4-backlog.md`; the items deferred *out* of Plan D are tracked there and in `plan-E-strategist-hardening.md`.

---

## File Structure

**New files (3):**
- `src/agents/contract/__init__.py` — package marker
- `src/agents/contract/evidence_writer.py` — `EvidenceWriter` BaseAgent
- `tests/integration/test_evidence_writer.py` — round-trip test for both row types

**Modified files (12):**
- `src/orchestrator/persistence.py` — add `AnalystEvidenceRow` + `TickerEvidenceRow` + `save_analyst_evidence` + `save_ticker_evidence`; remove `AttributionSignalsRow` + `save_attribution_signal`
- `src/orchestrator/pipeline.py` — replace `build_attribution_writer` import + invocation with `build_evidence_writer`
- `src/agents/analysts/_common.py` — remove `make_dual_emit_callback` (or rename to `make_evidence_callback` — see Task D3); legacy `AnalystSignal` + `make_exhaustive_validator` deleted
- `src/agents/analysts/technical/agent.py` — drop `output_schema=TechnicalSignal` + `output_key="technical_signals"`; use evidence-only callback
- `src/agents/analysts/fundamental/agent.py` — same change
- `src/agents/analysts/sentiment/agent.py` — same change
- `src/agents/analysts/smart_money/agent.py` — same change
- `src/agents/analysts/technical/schema.py` — delete `TechnicalSignal` (file remains empty or is removed; see task)
- `src/agents/analysts/fundamental/schema.py` — delete `FundamentalSignal`
- `src/agents/analysts/sentiment/schema.py` — delete `SentimentSignal`
- `src/agents/analysts/smart_money/schema.py` — delete `SmartMoneySignal`
- `src/agents/memory/writer.py` — read `state["ticker_evidence_objects"]` instead of `state["smart_money_signals"]`; drop legacy reads

**Deleted files (10):**
- `src/agents/attribution/__init__.py`
- `src/agents/attribution/writer.py`
- `tests/integration/test_attribution_writer.py`
- `docs/superpowers/specs/strategist-council-design.md`
- `docs/superpowers/specs/exit-rules-and-telemetry-design.md`
- `docs/superpowers/specs/strategist-v2-design.md`
- `docs/superpowers/specs/analyst-strategist-contract-design.md`
- `docs/superpowers/plans/strategist-council.md`
- `docs/superpowers/plans/exit-rules-and-telemetry.md`
- `docs/superpowers/plans/strategist-v2.md`
- `docs/superpowers/plans/analyst-strategist-contract.md`

(`docs/superpowers/specs/data-provider-shell-design.md`, `docs/superpowers/plans/data-provider-shell.md`, and `docs/superpowers/backlog.md` are kept — only the Goal-1 / Goal-2 lineage is retired.)

---

## Task D1: Add `AnalystEvidenceRow` + `TickerEvidenceRow` ORM rows

**Files:**
- Modify: `src/orchestrator/persistence.py`
- Test: `tests/integration/test_evidence_persistence.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_evidence_persistence.py
"""AnalystEvidenceRow + TickerEvidenceRow round-trip."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import (
    Base,
    AnalystEvidenceRow,
    TickerEvidenceRow,
    save_analyst_evidence,
    save_ticker_evidence,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def test_save_analyst_evidence_round_trip(db_session):
    save_analyst_evidence(
        db_session,
        tick_id="2026-05-08T14:00:00Z",
        analyst="technical",
        ticker="AAPL",
        verdict={
            "lean": "bullish",
            "magnitude": 0.6,
            "confidence": 0.7,
            "rationale": "uptrend with low volatility",
            "key_factors": ["rsi_14: 62"],
            "is_no_data": False,
        },
        features={"rsi_14": 62.0, "atr_pct_14": 0.018},
        feature_warnings=[],
    )
    db_session.commit()
    rows = db_session.query(AnalystEvidenceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.tick_id == "2026-05-08T14:00:00Z"
    assert r.analyst == "technical"
    assert r.ticker == "AAPL"
    assert r.lean == "bullish"
    assert r.magnitude == pytest.approx(0.6)
    assert r.confidence == pytest.approx(0.7)
    assert r.is_no_data is False
    assert json.loads(r.features_json) == {"rsi_14": 62.0, "atr_pct_14": 0.018}
    assert json.loads(r.key_factors_json) == ["rsi_14: 62"]


def test_save_ticker_evidence_round_trip(db_session):
    save_ticker_evidence(
        db_session,
        tick_id="2026-05-08T14:00:00Z",
        ticker="AAPL",
        aggregate={
            "lean": "bullish",
            "magnitude": 0.45,
            "confidence": 0.6,
            "disagreement": 0.12,
            "summary": "3/4 analysts bullish with low disagreement",
        },
        weights={"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0},
        analyst_count=4,
    )
    db_session.commit()
    rows = db_session.query(TickerEvidenceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.ticker == "AAPL"
    assert r.lean == "bullish"
    assert r.disagreement == pytest.approx(0.12)
    assert r.analyst_count == 4
    assert json.loads(r.weights_json) == {
        "technical": 1.0,
        "fundamental": 1.0,
        "sentiment": 1.0,
        "smart_money": 1.0,
    }
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/integration/test_evidence_persistence.py -v
```
Expected: FAIL with `ImportError: cannot import name 'AnalystEvidenceRow'`.

- [ ] **Step 3: Implement the rows + savers**

Open `src/orchestrator/persistence.py`. Locate the `AttributionSignalsRow` class around line 144 — leave it in place for now (Task D6 deletes it). Append new declarations at the end of the file (or before `# ── TradeLog`/whichever block sits last):

```python
# ── AnalystEvidence ───────────────────────────────────────────────────

class AnalystEvidenceRow(Base):
    """One row per analyst per ticker per tick. Mirrors `AnalystEvidence` Pydantic shape."""

    __tablename__ = "analyst_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)

    lean: Mapped[str] = mapped_column(String)
    magnitude: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(String, default="")
    key_factors_json: Mapped[str] = mapped_column(String, default="[]")
    is_no_data: Mapped[bool] = mapped_column(Boolean, default=False)

    features_json: Mapped[str] = mapped_column(String, default="{}")
    feature_warnings_json: Mapped[str] = mapped_column(String, default="[]")


def save_analyst_evidence(
    session: Session,
    *,
    tick_id: str,
    analyst: str,
    ticker: str,
    verdict: dict,
    features: dict,
    feature_warnings: list[str],
) -> None:
    """Persist one AnalystEvidence row."""
    from datetime import timezone
    row = AnalystEvidenceRow(
        tick_id=tick_id,
        recorded_at=datetime.now(tz=timezone.utc),
        analyst=analyst,
        ticker=ticker,
        lean=verdict["lean"],
        magnitude=float(verdict["magnitude"]),
        confidence=float(verdict["confidence"]),
        rationale=verdict.get("rationale", ""),
        key_factors_json=json.dumps(verdict.get("key_factors", [])),
        is_no_data=bool(verdict.get("is_no_data", False)),
        features_json=json.dumps(features),
        feature_warnings_json=json.dumps(feature_warnings),
    )
    session.add(row)
    session.flush()


# ── TickerEvidence ────────────────────────────────────────────────────

class TickerEvidenceRow(Base):
    """One row per ticker per tick — aggregated cross-analyst stance."""

    __tablename__ = "ticker_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    ticker: Mapped[str] = mapped_column(String, index=True)

    lean: Mapped[str] = mapped_column(String)
    magnitude: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    disagreement: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(String, default="")

    weights_json: Mapped[str] = mapped_column(String, default="{}")
    analyst_count: Mapped[int] = mapped_column(Integer, default=0)


def save_ticker_evidence(
    session: Session,
    *,
    tick_id: str,
    ticker: str,
    aggregate: dict,
    weights: dict,
    analyst_count: int,
) -> None:
    """Persist one TickerEvidence row."""
    from datetime import timezone
    row = TickerEvidenceRow(
        tick_id=tick_id,
        recorded_at=datetime.now(tz=timezone.utc),
        ticker=ticker,
        lean=aggregate["lean"],
        magnitude=float(aggregate["magnitude"]),
        confidence=float(aggregate["confidence"]),
        disagreement=float(aggregate["disagreement"]),
        summary=aggregate.get("summary", ""),
        weights_json=json.dumps(weights),
        analyst_count=int(analyst_count),
    )
    session.add(row)
    session.flush()
```

If `Boolean` is not yet imported, extend the import line near the top:

```python
from sqlalchemy import Boolean, DateTime, Float, Integer, String  # add Boolean if missing
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/integration/test_evidence_persistence.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

```
.venv/bin/python -m pytest tests/ -v
```
Expected: all green (no behaviour changes yet — only new declarations).

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/persistence.py tests/integration/test_evidence_persistence.py
git commit -m "feat(contract): persist AnalystEvidenceRow + TickerEvidenceRow"
```

---

## Task D2: `EvidenceWriter` agent

**Files:**
- Create: `src/agents/contract/__init__.py`
- Create: `src/agents/contract/evidence_writer.py`
- Test: `tests/integration/test_evidence_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_evidence_writer.py
"""EvidenceWriter persists analyst + ticker evidence from session state."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.contract.evidence_writer import EvidenceWriter, build_evidence_writer
from orchestrator.persistence import (
    Base,
    AnalystEvidenceRow,
    TickerEvidenceRow,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _evidence(analyst, ticker, lean="bullish"):
    return {
        "analyst": analyst,
        "ticker": ticker,
        "tick_id": "2026-05-08T14:00:00Z",
        "recorded_at": "2026-05-08T14:00:00Z",
        "verdict": {
            "lean": lean,
            "magnitude": 0.5,
            "confidence": 0.6,
            "rationale": f"{analyst} rationale",
            "key_factors": [f"{analyst} factor"],
            "is_no_data": False,
        },
        "features": {f"{analyst}_feature": 1.0},
        "feature_warnings": [],
    }


def _ticker_evidence(ticker):
    return {
        "ticker": ticker,
        "tick_id": "2026-05-08T14:00:00Z",
        "recorded_at": "2026-05-08T14:00:00Z",
        "per_analyst": {
            "technical": _evidence("technical", ticker),
            "fundamental": _evidence("fundamental", ticker),
        },
        "aggregate": {
            "lean": "bullish",
            "magnitude": 0.45,
            "confidence": 0.6,
            "disagreement": 0.1,
            "summary": "2/2 bullish",
        },
        "weights": {"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0},
    }


@pytest.mark.asyncio
async def test_evidence_writer_persists_both_row_types(db_session):
    writer = EvidenceWriter(db_session=db_session)
    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "technical_evidence": [_evidence("technical", "AAPL")],
        "fundamental_evidence": [_evidence("fundamental", "AAPL")],
        "sentiment_evidence": [],
        "smart_money_evidence": [],
        "ticker_evidence_objects": [_ticker_evidence("AAPL")],
    }
    ctx = MagicMock()
    ctx.session.state = state
    async for _ in writer._run_async_impl(ctx):
        pass

    analyst_rows = db_session.query(AnalystEvidenceRow).all()
    assert len(analyst_rows) == 2
    assert {r.analyst for r in analyst_rows} == {"technical", "fundamental"}

    ticker_rows = db_session.query(TickerEvidenceRow).all()
    assert len(ticker_rows) == 1
    assert ticker_rows[0].ticker == "AAPL"
    assert ticker_rows[0].analyst_count == 2


@pytest.mark.asyncio
async def test_evidence_writer_no_db_is_noop():
    writer = EvidenceWriter(db_session=None)
    ctx = MagicMock()
    ctx.session.state = {}
    async for _ in writer._run_async_impl(ctx):
        pass


def test_factory_returns_named_agent():
    w = build_evidence_writer(db_session=None)
    assert w.name == "EvidenceWriter"
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/integration/test_evidence_writer.py -v
```
Expected: FAIL with `ModuleNotFoundError: agents.contract.evidence_writer`.

- [ ] **Step 3: Implement the package + writer**

Create `src/agents/contract/__init__.py`:

```python
"""Contract-side agents (writers, etc.) for the new analyst → strategist surface."""
```

Create `src/agents/contract/evidence_writer.py`:

```python
"""Persist AnalystEvidence + TickerEvidence rows after every tick."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


_EVIDENCE_KEYS = (
    ("technical_evidence", "technical"),
    ("fundamental_evidence", "fundamental"),
    ("sentiment_evidence", "sentiment"),
    ("smart_money_evidence", "smart_money"),
)


class EvidenceWriter(BaseAgent):
    """Reads `state["{analyst}_evidence"]` + `state["ticker_evidence_objects"]` and writes both row types."""

    name: str = "EvidenceWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate

        from orchestrator.persistence import save_analyst_evidence, save_ticker_evidence

        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")

        for state_key, analyst in _EVIDENCE_KEYS:
            for ev in state.get(state_key, []) or []:
                ev_dict = ev if isinstance(ev, dict) else ev.model_dump()
                save_analyst_evidence(
                    self.db_session,
                    tick_id=tick_id,
                    analyst=analyst,
                    ticker=ev_dict["ticker"],
                    verdict=ev_dict["verdict"],
                    features=ev_dict.get("features", {}),
                    feature_warnings=ev_dict.get("feature_warnings", []),
                )

        for te in state.get("ticker_evidence_objects", []) or []:
            te_dict = te if isinstance(te, dict) else te.model_dump()
            save_ticker_evidence(
                self.db_session,
                tick_id=tick_id,
                ticker=te_dict["ticker"],
                aggregate=te_dict["aggregate"],
                weights=te_dict.get("weights", {}),
                analyst_count=len(te_dict.get("per_analyst", {})),
            )

        self.db_session.commit()
        return
        yield  # required to make this a generator


def build_evidence_writer(db_session=None) -> EvidenceWriter:
    return EvidenceWriter(db_session=db_session)
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/integration/test_evidence_writer.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agents/contract/ tests/integration/test_evidence_writer.py
git commit -m "feat(contract): add EvidenceWriter agent for AnalystEvidence + TickerEvidence persistence"
```

---

## Task D3: Drop dual-emit from each analyst — evidence-only

**Background:** Plan B installed `make_dual_emit_callback` in `src/agents/analysts/_common.py`, which both validated `state["{analyst}_signals"]` (legacy) and produced `state["{analyst}_evidence"]` (new). Plan C made the strategist consume only the evidence side. Now the legacy state key is unused — remove it.

**Files:**
- Modify: `src/agents/analysts/_common.py`
- Modify: `src/agents/analysts/technical/agent.py`
- Modify: `src/agents/analysts/fundamental/agent.py`
- Modify: `src/agents/analysts/sentiment/agent.py`
- Modify: `src/agents/analysts/smart_money/agent.py`
- Test: `tests/agents/analysts/test_evidence_callback.py` (new — replaces dual-emit test)

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/analysts/test_evidence_callback.py
"""make_evidence_callback writes only AnalystEvidence — no legacy *_signals."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.analysts._common import make_evidence_callback
from contract.evidence import AnalystEvidence
from contract.extractors.technical import extract_technical


def test_writes_only_evidence_state_key():
    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "tickers": ["AAPL"],
        "technical_data": {"AAPL": {"close": [100.0] * 30, "volume": [1.0e6] * 30}},
        # LLM verdict (would normally come from the LLM response — here we fake state)
        "technical_verdicts": [
            {
                "ticker": "AAPL",
                "lean": "bullish",
                "magnitude": 0.5,
                "confidence": 0.6,
                "rationale": "trend",
                "key_factors": ["rsi"],
                "is_no_data": False,
            }
        ],
    }
    cb = make_evidence_callback(
        analyst="technical",
        extractor=extract_technical,
        verdicts_state_key="technical_verdicts",
    )
    ctx = SimpleNamespace(state=state)
    cb(ctx)

    assert "technical_evidence" in state
    assert isinstance(state["technical_evidence"], list)
    ev = state["technical_evidence"][0]
    AnalystEvidence.model_validate(ev)
    assert "technical_signals" not in state  # legacy key MUST be gone
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/agents/analysts/test_evidence_callback.py -v
```
Expected: FAIL with `ImportError: cannot import name 'make_evidence_callback'`.

- [ ] **Step 3: Replace `make_dual_emit_callback` with `make_evidence_callback`**

Open `src/agents/analysts/_common.py`. Locate `make_dual_emit_callback` (added by Plan B). Rewrite the function as `make_evidence_callback` that does only the evidence side:

```python
def make_evidence_callback(
    *,
    analyst: str,
    extractor,
    verdicts_state_key: str,
):
    """Build an after_agent_callback that:
       1. Reads per-ticker verdicts from `state[verdicts_state_key]`.
       2. Runs `extractor(ticker, ticker_data)` to compute features + warnings.
       3. Builds an AnalystEvidence per ticker and writes the list to `state["{analyst}_evidence"]`.

    The LLM verdict shape is the dict form of `AnalystVerdict` (lean/magnitude/confidence/rationale/key_factors/is_no_data).
    """
    from datetime import datetime, timezone
    from contract.evidence import AnalystEvidence, AnalystVerdict

    def _callback(ctx):
        state = ctx.state
        tickers = state.get("tickers", []) or []
        tick_id = state.get("tick_id", "unknown")
        recorded_at = datetime.now(tz=timezone.utc).isoformat()
        data = state.get(f"{analyst}_data", {}) or {}
        verdicts_by_ticker = {
            v["ticker"]: v for v in (state.get(verdicts_state_key, []) or [])
        }

        evidence_list = []
        for t in tickers:
            features, warnings = extractor(t, data.get(t, {}))
            v = verdicts_by_ticker.get(t)
            if v is None:
                # Missing verdict from LLM — synthesize a no-data evidence row
                verdict = AnalystVerdict(
                    lean="neutral",
                    magnitude=0.0,
                    confidence=0.0,
                    rationale="no verdict from LLM",
                    key_factors=[],
                    is_no_data=True,
                )
            else:
                verdict = AnalystVerdict.model_validate(v)
            ev = AnalystEvidence(
                analyst=analyst,
                ticker=t,
                tick_id=tick_id,
                recorded_at=recorded_at,
                verdict=verdict,
                features=features,
                feature_warnings=warnings,
            )
            evidence_list.append(ev.model_dump(mode="json"))

        state[f"{analyst}_evidence"] = evidence_list
        # Note: do NOT write `state[f"{analyst}_signals"]` — the legacy key is retired.
        return None

    return _callback
```

Delete the old `make_dual_emit_callback` and (if still present) `make_exhaustive_validator` plus the legacy `AnalystSignal` Pydantic class. The new file should keep only what is still in use: `make_evidence_callback`. (The `<Analyst>Signal` schemas and validator are removed entirely in Task D7.)

- [ ] **Step 4: Update each analyst agent to use the new callback + drop `output_schema`**

Open `src/agents/analysts/technical/agent.py`. Replace any reference to `make_dual_emit_callback` / `output_schema=TechnicalSignal` / `output_key="technical_signals"` with the evidence-only path. The LLM output now lands directly into `technical_verdicts` (a free-form list — the LLM is instructed to produce verdict dicts):

```python
"""Technical analyst: deterministic feature extractor + LLM verdict, evidence-only output."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from agents.analysts.technical.fetch import technical_fetch_callback
from agents.analysts.technical.prompts import TECHNICAL_INSTRUCTION
from contract.extractors.technical import extract_technical


def _build_technical_analyst() -> LlmAgent:
    return LlmAgent(
        name="TechnicalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=TECHNICAL_INSTRUCTION,
        output_key="technical_verdicts",
        before_agent_callback=technical_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="technical",
            extractor=extract_technical,
            verdicts_state_key="technical_verdicts",
        ),
    )


technical_analyst = _build_technical_analyst()
```

Repeat for `fundamental`, `sentiment`, and `smart_money` — same pattern, just swap the analyst name, extractor import, and prompt import. The `*_verdicts` state key is the new LLM output bucket; `*_signals` is gone everywhere.

> **Note for the prompt files:** Plan B already adjusted analyst prompts to emit verdict dicts. If a prompt file still says "produce TechnicalSignal" or references `direction` instead of `lean`, update it now to match the `AnalystVerdict` schema (`lean`/`magnitude`/`confidence`/`rationale`/`key_factors`/`is_no_data`). This is a doc-only change — the Pydantic class is gone, so the prompt becomes the single source of shape.

- [ ] **Step 5: Run targeted tests**

```
.venv/bin/python -m pytest tests/agents/analysts/ -v
```
Expected: green. Any test that asserted on `state["{analyst}_signals"]` should already have been migrated by Plan B's tests; if any survive, retire them now.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/ tests/agents/analysts/test_evidence_callback.py
git commit -m "refactor(analysts): drop dual-emit, evidence-only output to state[{analyst}_evidence]"
```

---

## Task D4: Migrate `MemoryWriter` off legacy `*_signals`

**Files:**
- Modify: `src/agents/memory/writer.py`
- Test: `tests/agents/memory/test_writer.py` (modify if it asserts on `smart_money_signals`)

- [ ] **Step 1: Find the legacy reference**

In `src/agents/memory/writer.py`, the line:

```python
smart_money_seen=bool(state.get("smart_money_signals")),
```

reads the now-deleted state key. Replace it with a check against the new evidence list — "seen" means at least one smart-money evidence row has `is_no_data == False`.

- [ ] **Step 2: Write the failing test**

```python
# tests/agents/memory/test_writer_smart_money_seen.py
"""MemoryWriter.smart_money_seen reflects new state[smart_money_evidence] shape."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.memory.writer import MemoryWriter


@pytest.mark.asyncio
async def test_smart_money_seen_true_when_real_evidence(monkeypatch):
    writer = MemoryWriter()
    state = {
        "strategist_decision": {
            "decision_tag": "test",
            "reasoning": "x",
            "updated_thesis": "t",
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
        "smart_money_evidence": [
            {"ticker": "AAPL", "verdict": {"is_no_data": False, "lean": "bullish",
                                            "magnitude": 0.4, "confidence": 0.5,
                                            "rationale": "x", "key_factors": []}},
        ],
    }
    ctx = MagicMock()
    ctx.session.state = state

    # Stub the embed/dedup paths to avoid network
    import agents.memory.writer as W
    monkeypatch.setattr(W, "detect_repeat", lambda *a, **kw: __import__("asyncio").sleep(0, result=False))
    monkeypatch.setattr(W, "embed", lambda *a, **kw: [0.0])

    async for _ in writer._run_async_impl(ctx):
        pass
    assert state["memory_buffer"][-1]["smart_money_seen"] is True


@pytest.mark.asyncio
async def test_smart_money_seen_false_when_only_no_data(monkeypatch):
    writer = MemoryWriter()
    state = {
        "strategist_decision": {
            "decision_tag": "test",
            "reasoning": "x",
            "updated_thesis": "t",
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
        "smart_money_evidence": [
            {"ticker": "AAPL", "verdict": {"is_no_data": True, "lean": "neutral",
                                            "magnitude": 0.0, "confidence": 0.0,
                                            "rationale": "no data", "key_factors": []}},
        ],
    }
    ctx = MagicMock()
    ctx.session.state = state

    import agents.memory.writer as W
    monkeypatch.setattr(W, "detect_repeat", lambda *a, **kw: __import__("asyncio").sleep(0, result=False))
    monkeypatch.setattr(W, "embed", lambda *a, **kw: [0.0])

    async for _ in writer._run_async_impl(ctx):
        pass
    assert state["memory_buffer"][-1]["smart_money_seen"] is False
```

- [ ] **Step 3: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/agents/memory/test_writer_smart_money_seen.py -v
```
Expected: at least one FAIL — `smart_money_seen` defaults to `False` because the writer still reads `smart_money_signals`.

- [ ] **Step 4: Update `MemoryWriter`**

Replace the relevant block in `src/agents/memory/writer.py`:

```python
def _has_real_smart_money(state) -> bool:
    """True iff at least one smart-money evidence row has is_no_data == False."""
    for ev in state.get("smart_money_evidence", []) or []:
        verdict = ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)
        if verdict is None:
            continue
        is_no_data = (
            verdict.get("is_no_data")
            if isinstance(verdict, dict)
            else getattr(verdict, "is_no_data", False)
        )
        if not is_no_data:
            return True
    return False
```

…and change the `BufferEntry` construction:

```python
new_entry = BufferEntry(
    timestamp=datetime.now(tz=timezone.utc),
    decision_tag=...,
    reasoning_summary=...,
    smart_money_seen=_has_real_smart_money(state),
    executions_count=len(executions),
)
```

Move the `datetime` / `timezone` imports to the top of the file while you're there — the existing `__import__("datetime").datetime.now(...)` pattern was a workaround for circular-import paranoia that no longer applies.

- [ ] **Step 5: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/agents/memory/test_writer_smart_money_seen.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agents/memory/writer.py tests/agents/memory/test_writer_smart_money_seen.py
git commit -m "refactor(memory): migrate smart_money_seen flag to read smart_money_evidence"
```

---

## Task D5: Pipeline swap — replace `AttributionWriter` with `EvidenceWriter`

**Files:**
- Modify: `src/orchestrator/pipeline.py`
- Modify: `tests/integration/test_pipeline_composition.py`

- [ ] **Step 1: Update the failing pipeline composition test**

Open `tests/integration/test_pipeline_composition.py`. Find the assertion `assert names[1] == "AttributionWriter"` and change it to `"EvidenceWriter"`. Adjust any earlier comments that reference `AttributionWriter`.

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/integration/test_pipeline_composition.py -v
```
Expected: FAIL — pipeline still wires `build_attribution_writer`.

- [ ] **Step 3: Update `pipeline.py`**

In `src/orchestrator/pipeline.py`:

```python
def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.contract.evidence_writer import build_evidence_writer
    from agents.strategist.decision_writer import StrategistDecisionWriter  # added in Plan C
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_evidence_writer(db_session),
            _build_strategist(),
            StrategistDecisionWriter(db_session=db_session),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

> If the `StrategistDecisionWriter` line is already present from Plan C, leave it in place — only swap the writer at index 1.

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/integration/test_pipeline_composition.py -v
```
Expected: pass.

- [ ] **Step 5: Run full integration suite**

```
.venv/bin/python -m pytest tests/integration/ -v
```
Expected: all green. The legacy `test_attribution_writer.py` will fail at this point because `AttributionWriter` is no longer wired but its source file still exists; it will be deleted in Task D6.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/pipeline.py tests/integration/test_pipeline_composition.py
git commit -m "feat(pipeline): swap AttributionWriter for EvidenceWriter"
```

---

## Task D6: Delete `attribution/` package + `AttributionSignalsRow`

**Files:**
- Delete: `src/agents/attribution/__init__.py`
- Delete: `src/agents/attribution/writer.py`
- Delete: `tests/integration/test_attribution_writer.py`
- Modify: `src/orchestrator/persistence.py` — remove `AttributionSignalsRow` + `save_attribution_signal`

- [ ] **Step 1: Confirm no remaining references**

```
.venv/bin/python -m pytest tests/ --collect-only 2>&1 | head -40
```

Then:

```bash
grep -rn "AttributionWriter\|AttributionSignalsRow\|save_attribution_signal\|attribution_writer\|build_attribution_writer" src/ tests/
```

Expected (after Task D5): the only remaining hits are inside `src/agents/attribution/` and `tests/integration/test_attribution_writer.py` themselves. If you see any others, fix them before deleting — likely leftover docs or imports.

- [ ] **Step 2: Delete the files**

```bash
git rm src/agents/attribution/__init__.py
git rm src/agents/attribution/writer.py
git rm tests/integration/test_attribution_writer.py
# Remove the now-empty directory if git left it behind
test -d src/agents/attribution && rmdir src/agents/attribution || true
```

- [ ] **Step 3: Strip `AttributionSignalsRow` + `save_attribution_signal` from `persistence.py`**

In `src/orchestrator/persistence.py`, remove the `# ── AttributionSignals` block (the class declaration around line 144 plus the `save_attribution_signal` function around line 171). The associated `attribution_signals` table will simply not be created on fresh DBs going forward.

- [ ] **Step 4: Run full suite**

```
.venv/bin/python -m pytest tests/ -v
```
Expected: all green.

- [ ] **Step 5: Run ruff to catch dead imports**

```
.venv/bin/python -m ruff check src/ tests/
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add -u src/agents/attribution src/orchestrator/persistence.py tests/integration/test_attribution_writer.py
git commit -m "refactor(persistence): retire AttributionWriter + AttributionSignalsRow"
```

---

## Task D7: Delete legacy `<Analyst>Signal` Pydantic schemas

**Files:**
- Modify: `src/agents/analysts/technical/schema.py`
- Modify: `src/agents/analysts/fundamental/schema.py`
- Modify: `src/agents/analysts/sentiment/schema.py`
- Modify: `src/agents/analysts/smart_money/schema.py`

After Task D3, the analyst agents no longer set `output_schema=<Analyst>Signal`. Confirm nothing else imports these classes and then delete them.

- [ ] **Step 1: Confirm no remaining references**

```bash
grep -rn "TechnicalSignal\|FundamentalSignal\|SentimentSignal\|SmartMoneySignal\|AnalystSignal" src/ tests/
```

Expected: no hits in `src/`. There may still be hits in `tests/` if Plan B's exploratory tests asserted on the Pydantic shape — retire those tests; the contract is now `AnalystEvidence`.

- [ ] **Step 2: Delete each schema file**

Each file is a thin Pydantic class plus a docstring. Delete the file outright:

```bash
git rm src/agents/analysts/technical/schema.py
git rm src/agents/analysts/fundamental/schema.py
git rm src/agents/analysts/sentiment/schema.py
git rm src/agents/analysts/smart_money/schema.py
```

If any analyst's `__init__.py` re-exports the deleted class, drop the line.

- [ ] **Step 3: Run full suite**

```
.venv/bin/python -m pytest tests/ -v
```
Expected: all green.

- [ ] **Step 4: Run ruff**

```
.venv/bin/python -m ruff check src/ tests/
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add -u src/agents/analysts/
git commit -m "refactor(analysts): delete legacy <Analyst>Signal Pydantic classes"
```

---

## Task D8: Final regression pass + retire superseded docs + graphify delta

**Files:**
- Delete: `docs/superpowers/specs/strategist-council-design.md`
- Delete: `docs/superpowers/specs/exit-rules-and-telemetry-design.md`
- Delete: `docs/superpowers/specs/strategist-v2-design.md`
- Delete: `docs/superpowers/specs/analyst-strategist-contract-design.md`
- Delete: `docs/superpowers/plans/strategist-council.md`
- Delete: `docs/superpowers/plans/exit-rules-and-telemetry.md`
- Delete: `docs/superpowers/plans/strategist-v2.md`
- Delete: `docs/superpowers/plans/analyst-strategist-contract.md`
- Modify: `docs/superpowers/backlog.md` — point Goal-1 / Goal-2 references at `docs/Phase4-stratergist-and-analysts/spec.md`
- Modify: `graphify-out/graph_delta.md` — append a final dated entry covering the doc reorg

- [ ] **Step 1: Full test sweep**

```
.venv/bin/python -m pytest tests/ -v
```
Expected: all green.

```
.venv/bin/python -m ruff check src/ tests/
```
Expected: clean.

```bash
grep -rn "AnalystSignal\|TechnicalSignal\|FundamentalSignal\|SentimentSignal\|SmartMoneySignal\|AttributionSignalsRow\|attribution_writer\|build_attribution_writer\|AttributionWriter\|save_attribution_signal\|technical_signals\|fundamental_signals\|sentiment_signals\|smart_money_signals" src/ tests/
```
Expected: zero hits in `src/`. `tests/` should also be empty (any remaining hits are stale tests — retire them or stop here and ask the user).

- [ ] **Step 2: Delete superseded docs**

```bash
git rm docs/superpowers/specs/strategist-council-design.md
git rm docs/superpowers/specs/exit-rules-and-telemetry-design.md
git rm docs/superpowers/specs/strategist-v2-design.md
git rm docs/superpowers/specs/analyst-strategist-contract-design.md
git rm docs/superpowers/plans/strategist-council.md
git rm docs/superpowers/plans/exit-rules-and-telemetry.md
git rm docs/superpowers/plans/strategist-v2.md
git rm docs/superpowers/plans/analyst-strategist-contract.md
```

(`data-provider-shell-design.md` and `data-provider-shell.md` stay — they belong to a different track.)

- [ ] **Step 3: Update `docs/superpowers/backlog.md`**

Open `docs/superpowers/backlog.md`. Find any entries that link to the eight deleted files or describe Goal 1 / Goal 2 as separate in-flight specs. Replace those pointers with a single line referencing the consolidated spec:

> Strategist v2 + Analyst → Strategist contract are now consolidated under `docs/Phase4-stratergist-and-analysts/spec.md`, broken into four plans (A: contract scaffolding, B: extractors with dual-emit, C: strategist v2, D: cleanup).

Leave the Tier 1 / Tier 2 / Tier 3 idea entries (B1–B8) in place — they describe future work, not the now-completed Phase 4. If any of them said "see analyst-strategist-contract-design.md," update the cross-link to `docs/Phase4-stratergist-and-analysts/spec.md`.

- [ ] **Step 4: Append a dated graphify delta entry**

Open `graphify-out/graph_delta.md`. Append (do NOT replace) a new dated section at the end of the file. Use today's date in `YYYY-MM-DD` format:

```markdown
## YYYY-MM-DD — Phase 4 docs reorganisation + legacy retirement

Strategist v2 + Analyst→Strategist contract specs/plans replaced by `docs/Phase4-stratergist-and-analysts/`. Legacy `<Analyst>Signal` schemas, `AttributionWriter`, and `AttributionSignalsRow` retired. New persistence rows `AnalystEvidenceRow` + `TickerEvidenceRow` plus the `EvidenceWriter` agent now own the contract write path.

- New nodes: `src/agents/contract/evidence_writer.py` (EvidenceWriter), `AnalystEvidenceRow`, `TickerEvidenceRow`, `save_analyst_evidence`, `save_ticker_evidence`
- New edges: `HourlyTick` pipeline now sequences `AnalystPool → EvidenceWriter → Strategist → StrategistDecisionWriter → RiskGate → Executor → MemoryWriter → Snapshotter`
- Removed nodes: `src/agents/attribution/writer.py` (AttributionWriter, build_attribution_writer), `AttributionSignalsRow`, `save_attribution_signal`, `TechnicalSignal`, `FundamentalSignal`, `SentimentSignal`, `SmartMoneySignal`, `AnalystSignal`, `make_dual_emit_callback`, `make_exhaustive_validator`
- Removed docs: `docs/superpowers/specs/{strategist-council-design,exit-rules-and-telemetry-design,strategist-v2-design,analyst-strategist-contract-design}.md`, `docs/superpowers/plans/{strategist-council,exit-rules-and-telemetry,strategist-v2,analyst-strategist-contract}.md`
- `MemoryWriter.smart_money_seen` now derives from `state["smart_money_evidence"]` instead of the deleted `smart_money_signals` key
```

(If `graph_delta.md` is already long enough that the file's existing rebuild trigger applies, mention to the user that `/graphify . --update` is due.)

- [ ] **Step 5: Final sweep**

```bash
grep -rn "strategist-council\|exit-rules-and-telemetry\|strategist-v2\|analyst-strategist-contract" docs/
```
Expected: only references inside `docs/Phase4-stratergist-and-analysts/` (which legitimately mention the predecessor names) and inside the new `graph_delta.md` entry.

```
.venv/bin/python -m pytest tests/ -v
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -u docs/superpowers graphify-out/graph_delta.md docs/Phase4-stratergist-and-analysts
git commit -m "docs(phase4): retire superseded specs/plans, update backlog + graph delta"
```

---

## Task D9: Absorbed hygiene sweep (6 follow-ups from Phase 4 backlog)

This task lands six follow-ups flagged by the Phase 4 chunk audits whose target files already overlap with Plan D's scope. Doing them here avoids touching the same files twice and keeps the `post-phase4-backlog.md` inventory shrinking as Plan D progresses.

**Files (all already touched by earlier Plan D tasks; this task adds focused edits):**
- Modify: `src/orchestrator/persistence.py`
- Modify: `src/agents/strategist/__init__.py`
- Modify: `src/agents/strategist/decision_writer.py`
- Modify: `src/agents/memory/writer.py`
- Modify: `src/agents/contract/evidence_writer.py` *(created in D2)*
- Modify: `tests/unit/orchestrator/test_persistence_ticker_stance.py`
- Modify: `tests/unit/test_attribution_persistence.py` *(if it still exists post-D6; otherwise skip)*
- Modify: `tests/unit/test_trade_log_tick_id_fks.py`
- Modify: `tests/unit/test_buffer_persistence.py`
- Modify: `tests/unit/test_snapshot_persistence.py`

- [ ] **Step 1: FU-06 — composite UNIQUE on `TickerStanceRow`**

`TickerStanceRow` enforces "one stance per ticker per tick" by caller convention only. Promote that invariant to the database layer.

In `src/orchestrator/persistence.py`, add a `__table_args__` declaration to `TickerStanceRow`:

```python
from sqlalchemy import UniqueConstraint

class TickerStanceRow(Base):
    __tablename__ = "ticker_stance"
    __table_args__ = (
        UniqueConstraint("tick_id", "ticker", name="uq_ticker_stance_tick_ticker"),
    )
    # ... existing columns unchanged ...
```

Add a regression test in `tests/unit/orchestrator/test_persistence_ticker_stance.py` (or a new sibling file) that asserts `IntegrityError` is raised when two rows with the same `(tick_id, ticker)` are flushed.

- [ ] **Step 2: FU-08 — `sessionmaker(bind=engine)` → `Session(bind=engine)` sweep**

The Plan-C-era persistence test fixtures still use the SQLAlchemy 1.x `sessionmaker(bind=engine)()` pattern. SQLAlchemy 2 prefers the direct `Session(bind=engine)` form. Sweep these five files:

- `tests/unit/orchestrator/test_persistence_ticker_stance.py`
- `tests/unit/test_attribution_persistence.py` *(only if it survived D6; otherwise this entry is moot)*
- `tests/unit/test_trade_log_tick_id_fks.py`
- `tests/unit/test_buffer_persistence.py`
- `tests/unit/test_snapshot_persistence.py`

Replace each occurrence:

```python
# Before
from sqlalchemy.orm import sessionmaker
Session = sessionmaker(bind=engine)
with Session() as session:
    ...

# After
from sqlalchemy.orm import Session
with Session(bind=engine) as session:
    ...
```

Also rename any `db` fixture to `session` while you're in the file (FU-20) so naming matches the rest of the test tree.

- [ ] **Step 3: FU-09 — `src/agents/strategist/__init__.py` re-export audit**

Open `src/agents/strategist/__init__.py`. List every name it re-exports. For each, run:

```bash
grep -rn "from agents.strategist import <name>" src/ tests/
```

Remove any re-export with zero callers outside `agents.strategist.*`. In particular: confirm whether the module-level `strategist_agent` singleton (currently kept alive only by `tests/integration/test_strategist_v2_smoke.py`) is the right public handle, or whether the smoke test should import the agent from a more specific path. Document the decision in the `__init__.py` module docstring.

- [ ] **Step 4: FU-15 + FU-17 — async-generator gate idiom + `AsyncGenerator` import sweep**

Pick **one** idiom for the no-op async generator gate used in `BaseAgent` subclasses and apply it consistently:

- Plan C's `decision_writer.py` uses `return; yield` (lines 48, 56, 101 in the post-merge file).
- Older writers under `src/agents/attribution/` use `if False: yield`.

The recommended idiom is **`return; yield`** (one line, intent obvious to readers familiar with PEP 525). Sweep:

- `src/agents/contract/evidence_writer.py` (the new D2 writer)
- `src/agents/memory/writer.py`
- Any surviving writers Plan D didn't delete

In the same sweep, replace any `from typing import AsyncGenerator` with `from collections.abc import AsyncGenerator` (UP035). `decision_writer.py` is already on the `collections.abc` form — match it.

- [ ] **Step 5: FU-16 — stale comment in `persistence.py`**

`src/orchestrator/persistence.py` has a comment near `TradeLogRow.opening_tick_id` / `closing_tick_id` that says these are "set by the executor when opening / closing." Since Plan C, the *opening* FK is actually populated by the strategist callback path via `PositionThesis.opened_tick_id` flowing through `executor.BUY`. Update the comment to reflect the real flow:

```python
# opening_tick_id: copied from PositionThesis.opened_tick_id when executor.BUY
#   writes the position. closing_tick_id: stamped by executor.SELL with the
#   tick that triggered the close.
```

- [ ] **Step 6: Verification**

```
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check src/ tests/
```

Expected: all green, ruff clean. The new UNIQUE constraint test (Step 1) should appear in the pass count.

- [ ] **Step 7: Commit**

Single commit for the whole sweep:

```bash
git add -u src/ tests/
git commit -m "chore(phase4): absorbed hygiene sweep — TickerStance UNIQUE, Session(bind=), async-gen idiom, UP035, stale comment"
```

---

## End-state checklist

After Plan D merges, the repo should match all of:

- [ ] `src/agents/attribution/` does not exist
- [ ] `src/agents/contract/evidence_writer.py` exists and is the sole writer of `analyst_evidence` + `ticker_evidence` rows
- [ ] `src/orchestrator/persistence.py` defines `AnalystEvidenceRow` + `TickerEvidenceRow` + `TickerStanceRow` + `TradeLogRow` (with `opening_tick_id` / `closing_tick_id` from Plan C); does NOT define `AttributionSignalsRow`
- [ ] Each analyst's `agent.py` uses `make_evidence_callback` and writes only `state["{analyst}_evidence"]`
- [ ] No file under `src/` references `AnalystSignal`, `TechnicalSignal`, `FundamentalSignal`, `SentimentSignal`, `SmartMoneySignal`, `AttributionWriter`, `AttributionSignalsRow`, `save_attribution_signal`, or any `*_signals` state key
- [ ] `MemoryWriter.smart_money_seen` is computed from `smart_money_evidence`
- [ ] `docs/superpowers/specs/` and `docs/superpowers/plans/` contain only the `data-provider-shell-*` pair and the `backlog.md` index
- [ ] `docs/Phase4-stratergist-and-analysts/` contains: `spec.md`, `plan-A-contract-scaffolding.md`, `plan-B-extractors-dual-emit.md`, `plan-C-strategist-v2.md`, `plan-D-cleanup.md`
- [ ] `graphify-out/graph_delta.md` ends with the Phase 4 reorganisation entry
- [ ] `pytest tests/` and `ruff check src/ tests/` both pass
- [ ] `TickerStanceRow` declares a `UniqueConstraint("tick_id", "ticker", ...)` (D9 / FU-06)
- [ ] No test fixture uses `sessionmaker(bind=engine)` — only `Session(bind=engine)` (D9 / FU-08)
- [ ] All `BaseAgent._run_async_impl` no-op generators use the `return; yield` idiom (D9 / FU-15)
- [ ] No file under `src/` imports `AsyncGenerator` from `typing` — only from `collections.abc` (D9 / FU-17)
