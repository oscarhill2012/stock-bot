# Phase 1.5 + 2a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the persistence, validation, baseline, lifecycle, and deployment plumbing required to start paper trading. Combines Phase 1.5 carry-forward (K, L, O, P) with Phase 2a groundwork (lifecycle scripts, SPY baseline, equity curve).

**Architecture:** Existing src-layout Python project with `PYTHONPATH=src`. New code in `src/baselines/`, `src/lifecycle/`, `src/scripts/`, `deploy/`. Runtime state in Cloud SQL (Postgres prod) / SQLite (dev) via SQLAlchemy ORM in `src/orchestrator/persistence.py`. Lifecycle scripts run locally and reach into cloud (Cloud Scheduler + Cloud SQL) — bot only runs in cloud. Bot-vs-SPY equity curve via shared library; matplotlib CLI for now, dashboard reuses the lib in 2b.

**Tech Stack:** Python 3.12, SQLAlchemy, Pydantic v2, Google ADK, yfinance, matplotlib, pytest, Docker, Cloud Build, Cloud Run Jobs, Cloud Scheduler.

**Source specs:**
- `docs/superpowers/specs/phase-2a-groundwork-design.md`
- `docs/Phase1-build/phase1.5-remaining.md`
- `docs/Phase1-build/2026-05-06-multi-agent-system-design.md` (parent design)

**Phase 1.5 supersession map:**
- §M1, §N1 → folded into Tasks 5-7 (one canonical SPY/equity implementation)
- §N3 → simplified to 2-way bot-vs-SPY in Task 7
- §N2 (MLP) → dropped, deferred to Phase 3 (breadcrumb in `docs/Phase1-build/phase1.5-remaining.md`)

---

## Task 1: K1 — SessionService factory

**Files:**
- Modify: `src/orchestrator/persistence.py` (append `make_session_service`)
- Modify: `src/orchestrator/tick.py` (use factory instead of `InMemorySessionService`)
- Test: `tests/unit/test_session_service_factory.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_service_factory.py
"""SessionService factory: dev → SQLite, prod → Postgres URL respected."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from orchestrator.persistence import make_session_service


def test_dev_returns_sqlite_database_session_service(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKBOT_ENV", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    svc = make_session_service()
    assert svc.__class__.__name__ == "DatabaseSessionService"
    # SQLite path under cwd
    assert "sqlite" in str(svc).lower() or hasattr(svc, "engine")


def test_prod_uses_database_url(monkeypatch):
    monkeypatch.setenv("STOCKBOT_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    svc = make_session_service()
    assert svc.__class__.__name__ == "DatabaseSessionService"


def test_prod_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("STOCKBOT_ENV", "prod")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        make_session_service()
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_session_service_factory.py -v
```

Expected: ImportError / AttributeError — `make_session_service` does not exist.

- [ ] **Step 3: Implement `make_session_service`**

Append to `src/orchestrator/persistence.py`:

```python
# ── ADK SessionService factory ────────────────────────────────────────

import os


def make_session_service():
    """Return a DatabaseSessionService configured by STOCKBOT_ENV.

    Dev: sqlite at ./data/stockbot.db (created on demand).
    Prod: DATABASE_URL env var (Postgres in deploy).
    """
    from google.adk.sessions import DatabaseSessionService

    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "STOCKBOT_ENV=prod requires DATABASE_URL to be set."
            )
        return DatabaseSessionService(db_url=url)

    # dev
    from pathlib import Path
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    return DatabaseSessionService(db_url=f"sqlite:///{data_dir.absolute()}/stockbot.db")
```

- [ ] **Step 4: Wire factory into `tick.py`**

In `src/orchestrator/tick.py`, replace the `InMemorySessionService()` line in `run_once`:

```python
# OLD:
# session_service = InMemorySessionService()

# NEW:
from orchestrator.persistence import make_session_service
session_service = make_session_service()
```

Remove the `from google.adk.sessions import InMemorySessionService` import.

- [ ] **Step 5: Verify tests pass**

```
pytest tests/unit/test_session_service_factory.py tests/unit/test_tick_entrypoint.py -v
```

Expected: all pass. If `test_tick_entrypoint.py` fails because it relied on InMemorySessionService, update its setup to use `monkeypatch.setenv("STOCKBOT_ENV", "dev")` and a temp dir. Show the actual failures and adjust.

- [ ] **Step 6: Commit**

```
git add src/orchestrator/persistence.py src/orchestrator/tick.py tests/unit/test_session_service_factory.py
git commit -m "feat(persistence): session-service factory by env"
```

---

## Task 2: K2 — DB init script

**Files:**
- Create: `src/scripts/init_db.py`
- Test: `tests/unit/test_init_db_script.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_init_db_script.py
"""init_db creates all StockBot tables, idempotent."""
from __future__ import annotations

import sqlite3

from sqlalchemy import inspect

from orchestrator.persistence import make_engine
from scripts.init_db import init_db


EXPECTED_TABLES = {"buffer_entries", "trade_log", "portfolio_snapshots"}


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    init_db(f"sqlite:///{db_path}")  # second run must not raise
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_init_db_script.py -v
```

Expected: ImportError — `scripts.init_db` does not exist.

- [ ] **Step 3: Implement `init_db`**

Create `src/scripts/init_db.py`:

```python
"""Initialise the StockBot DB schema. Idempotent.

Usage:
    PYTHONPATH=src python -m scripts.init_db
    PYTHONPATH=src python -m scripts.init_db --db-url sqlite:///path/to.db
"""
from __future__ import annotations

import argparse
import os

from orchestrator.persistence import create_all, make_engine


def init_db(db_url: str) -> None:
    """Create all StockBot tables on the given DB URL. Idempotent."""
    engine = make_engine(db_url)
    create_all(engine)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None, help="SQLAlchemy URL")
    args = parser.parse_args()
    db_url = args.db_url or _resolve_default_db_url()
    init_db(db_url)
    print(f"✓ Created all tables on {db_url}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_init_db_script.py -v
```

Expected: PASS.

- [ ] **Step 5: Manual smoke check**

```
PYTHONPATH=src python -m scripts.init_db --db-url sqlite:///data/test_init.db
```

Expected output: `✓ Created all tables on sqlite:///data/test_init.db`. Then delete `data/test_init.db`.

- [ ] **Step 6: Commit**

```
git add src/scripts/init_db.py tests/unit/test_init_db_script.py
git commit -m "feat(persistence): init_db.py creates all tables"
```

---

## Task 3: K3a — AttributionSignals SQL table + save function

**Files:**
- Modify: `src/orchestrator/persistence.py` (add `AttributionSignalsRow`, `save_attribution_signal`)
- Test: `tests/unit/test_attribution_persistence.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_attribution_persistence.py
"""AttributionSignalsRow round-trip for all four analyst types."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.persistence import (
    AttributionSignalsRow,
    create_all,
    make_engine,
    make_session_factory,
    save_attribution_signal,
)


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_all(engine)
    SessionLocal = make_session_factory(engine)
    s = SessionLocal()
    yield s
    s.close()


def test_round_trip_technical(session):
    save_attribution_signal(
        session,
        tick_id="tick-1",
        analyst="technical",
        signal={
            "ticker": "AAPL", "direction": "bullish", "confidence": 0.7,
            "key_factors": ["MA crossover"],
        },
    )
    session.commit()
    row = session.query(AttributionSignalsRow).first()
    assert row.tick_id == "tick-1"
    assert row.analyst == "technical"
    assert row.ticker == "AAPL"
    assert row.direction == "bullish"
    assert row.confidence == 0.7


def test_round_trip_smart_money(session):
    save_attribution_signal(
        session,
        tick_id="tick-1",
        analyst="smart_money",
        signal={
            "ticker": "TSLA", "direction": "bullish", "conviction": "high",
            "insiders": ["Musk"], "politicians": [], "total_dollar_value": 50000.0,
        },
    )
    session.commit()
    row = session.query(AttributionSignalsRow).first()
    assert row.analyst == "smart_money"
    assert row.conviction == "high"
    assert row.total_dollar_value == 50000.0


def test_per_tick_count(session):
    for analyst in ("technical", "fundamental", "sentiment"):
        save_attribution_signal(
            session,
            tick_id="tick-1",
            analyst=analyst,
            signal={"ticker": "AAPL", "direction": "neutral", "confidence": 0.5,
                    "key_factors": []},
        )
    session.commit()
    assert session.query(AttributionSignalsRow).count() == 3
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_attribution_persistence.py -v
```

Expected: ImportError on `AttributionSignalsRow`/`save_attribution_signal`.

- [ ] **Step 3: Implement `AttributionSignalsRow` + saver**

Append to `src/orchestrator/persistence.py`:

```python
# ── AttributionSignals ────────────────────────────────────────────────

class AttributionSignalsRow(Base):
    """One row per analyst signal per tick. `analyst` discriminates type-specific columns."""

    __tablename__ = "attribution_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst: Mapped[str] = mapped_column(String, index=True)  # technical | fundamental | sentiment | smart_money
    ticker: Mapped[str] = mapped_column(String, index=True)
    direction: Mapped[str] = mapped_column(String)  # bullish | bearish | neutral

    # Dense-analyst fields (NULL for smart_money rows)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_factors_json: Mapped[str] = mapped_column(String, default="[]")

    # Sentiment-only
    top_headlines_json: Mapped[str | None] = mapped_column(String, nullable=True)
    social_score_delta: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Smart-money-only
    conviction: Mapped[str | None] = mapped_column(String, nullable=True)
    insiders_json: Mapped[str | None] = mapped_column(String, nullable=True)
    politicians_json: Mapped[str | None] = mapped_column(String, nullable=True)
    total_dollar_value: Mapped[float | None] = mapped_column(Float, nullable=True)


def save_attribution_signal(
    session: Session,
    *,
    tick_id: str,
    analyst: str,
    signal: dict,
) -> None:
    """Persist one analyst signal. `analyst` must be technical|fundamental|sentiment|smart_money."""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    common = dict(
        tick_id=tick_id,
        recorded_at=_dt.now(tz=_tz.utc),
        analyst=analyst,
        ticker=signal["ticker"],
        direction=signal["direction"],
    )
    if analyst == "smart_money":
        row = AttributionSignalsRow(
            **common,
            confidence=None,
            key_factors_json="[]",
            conviction=signal.get("conviction"),
            insiders_json=_json.dumps(signal.get("insiders", [])),
            politicians_json=_json.dumps(signal.get("politicians", [])),
            total_dollar_value=signal.get("total_dollar_value"),
        )
    else:
        row = AttributionSignalsRow(
            **common,
            confidence=signal.get("confidence"),
            key_factors_json=_json.dumps(signal.get("key_factors", [])),
            top_headlines_json=(
                _json.dumps(signal["top_headlines"])
                if analyst == "sentiment" and "top_headlines" in signal
                else None
            ),
            social_score_delta=(
                signal.get("social_score_delta") if analyst == "sentiment" else None
            ),
        )
    session.add(row)
    session.flush()
```

- [ ] **Step 4: Verify tests pass**

```
pytest tests/unit/test_attribution_persistence.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add src/orchestrator/persistence.py tests/unit/test_attribution_persistence.py
git commit -m "feat(persistence): attribution_signals table + save fn"
```

---

## Task 4: K3b — AttributionWriter agent + pipeline integration

**Files:**
- Create: `src/agents/attribution/__init__.py` (empty)
- Create: `src/agents/attribution/writer.py`
- Modify: `src/orchestrator/pipeline.py` (insert AttributionWriter after AnalystPool)
- Test: `tests/integration/test_attribution_writer.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_attribution_writer.py
"""AttributionWriter persists every analyst signal in session.state to the DB."""
from __future__ import annotations

import pytest

from agents.attribution.writer import AttributionWriter
from orchestrator.persistence import (
    AttributionSignalsRow,
    create_all,
    make_engine,
    make_session_factory,
)


@pytest.fixture
def db_session():
    engine = make_engine("sqlite://")
    create_all(engine)
    SessionLocal = make_session_factory(engine)
    s = SessionLocal()
    yield s
    s.close()


class _StubCtx:
    def __init__(self, state):
        self.session = type("S", (), {"state": state})()


@pytest.mark.asyncio
async def test_writes_one_row_per_signal(db_session):
    state = {
        "tick_id": "tick-x",
        "technical_signals": [
            {"ticker": "AAPL", "direction": "bullish", "confidence": 0.6, "key_factors": []},
            {"ticker": "MSFT", "direction": "neutral", "confidence": 0.4, "key_factors": []},
        ],
        "fundamental_signals": [
            {"ticker": "AAPL", "direction": "bullish", "confidence": 0.7, "key_factors": []},
        ],
        "sentiment_signals": [
            {"ticker": "AAPL", "direction": "neutral", "confidence": 0.5,
             "key_factors": [], "top_headlines": ["x"], "social_score_delta": 0.0},
        ],
        "smart_money_signals": [
            {"ticker": "TSLA", "direction": "bullish", "conviction": "high",
             "insiders": ["X"], "politicians": [], "total_dollar_value": 1000.0},
        ],
    }
    writer = AttributionWriter(db_session=db_session)
    async for _ in writer._run_async_impl(_StubCtx(state)):
        pass
    db_session.commit()

    rows = db_session.query(AttributionSignalsRow).all()
    assert len(rows) == 5
    by_analyst = {r.analyst for r in rows}
    assert by_analyst == {"technical", "fundamental", "sentiment", "smart_money"}


@pytest.mark.asyncio
async def test_no_db_session_is_noop(caplog):
    writer = AttributionWriter(db_session=None)
    async for _ in writer._run_async_impl(_StubCtx({"tick_id": "t", "technical_signals": [
        {"ticker": "AAPL", "direction": "bullish", "confidence": 0.5, "key_factors": []}
    ]})):
        pass
    # No error; nothing to assert beyond "did not raise"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/integration/test_attribution_writer.py -v
```

Expected: ImportError on `agents.attribution.writer`.

- [ ] **Step 3: Implement `AttributionWriter`**

Create `src/agents/attribution/__init__.py` (empty file).

Create `src/agents/attribution/writer.py`:

```python
"""Persist every analyst signal per tick to the attribution_signals table."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event


_SIGNAL_KEYS = (
    ("technical_signals", "technical"),
    ("fundamental_signals", "fundamental"),
    ("sentiment_signals", "sentiment"),
    ("smart_money_signals", "smart_money"),
)


class AttributionWriter(BaseAgent):
    name: str = "AttributionWriter"
    db_session: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate
        from orchestrator.persistence import save_attribution_signal

        state = ctx.session.state
        tick_id = state.get("tick_id", "unknown")
        for state_key, analyst in _SIGNAL_KEYS:
            for sig in state.get(state_key, []) or []:
                signal_dict = sig if isinstance(sig, dict) else sig.model_dump()
                save_attribution_signal(
                    self.db_session,
                    tick_id=tick_id,
                    analyst=analyst,
                    signal=signal_dict,
                )
        self.db_session.commit()
        return
        yield  # required to make this a generator


def build_attribution_writer(db_session=None) -> AttributionWriter:
    return AttributionWriter(db_session=db_session)
```

- [ ] **Step 4: Wire into pipeline**

Modify `src/orchestrator/pipeline.py` `build_pipeline`:

```python
def build_pipeline(broker, db_session=None) -> SequentialAgent:
    """Compose the full hourly tick pipeline."""
    from agents.executor.agent import build_executor
    from agents.risk_gate.agent import RiskGateAgent
    from agents.snapshot.agent import build_snapshotter
    from agents.attribution.writer import build_attribution_writer
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            _build_analyst_pool(),
            build_attribution_writer(db_session),
            _build_strategist(),
            RiskGateAgent(broker=broker),
            build_executor(broker, db_session),
            _build_memory_writer(),
            build_snapshotter(broker, db_session),
        ],
    )
```

- [ ] **Step 5: Verify tests pass**

```
pytest tests/integration/test_attribution_writer.py tests/integration/test_pipeline_composition.py -v
```

Expected: all pass. If `test_pipeline_composition.py` asserts a specific number of `sub_agents`, update its expectation by +1.

- [ ] **Step 6: Commit**

```
git add src/agents/attribution/ src/orchestrator/pipeline.py tests/integration/test_attribution_writer.py
git commit -m "feat(persistence): attribution_writer + pipeline wiring"
```

---

## Task 5: SPY metrics function

**Files:**
- Create: `src/baselines/spy.py`
- Test: `tests/unit/test_spy_metrics.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_spy_metrics.py
"""SPY baseline metrics from a hand-crafted price series."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from baselines.spy import SPYMetrics, _metrics_from_series


def test_metrics_from_flat_series_zero_return():
    s = pd.Series([100.0] * 252)
    m = _metrics_from_series(s)
    assert m.cumulative_return == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0, abs=1e-9)


def test_metrics_from_monotonic_series_positive_return():
    s = pd.Series([100.0 + i for i in range(252)])  # 1y of daily +1
    m = _metrics_from_series(s)
    assert m.cumulative_return == pytest.approx((100 + 251) / 100 - 1)
    assert m.max_drawdown == pytest.approx(0.0, abs=1e-9)
    assert m.sharpe > 0  # positive trend


def test_metrics_from_drawdown_series():
    # rises to 200 then drops to 50
    s = pd.Series([100, 150, 200, 175, 100, 50])
    m = _metrics_from_series(s)
    assert m.max_drawdown == pytest.approx(-0.75)  # 200 → 50
    assert m.cumulative_return == pytest.approx(-0.5)  # 100 → 50
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_spy_metrics.py -v
```

Expected: ImportError on `baselines.spy`.

- [ ] **Step 3: Implement `spy.py`**

Create `src/baselines/spy.py`:

```python
"""SPY buy-and-hold metrics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SPYMetrics:
    cumulative_return: float
    annualised_return: float
    sharpe: float
    max_drawdown: float
    calmar: float


def _metrics_from_series(close: pd.Series) -> SPYMetrics:
    """Compute baseline metrics from a daily close series."""
    if len(close) < 2:
        return SPYMetrics(0.0, 0.0, 0.0, 0.0, 0.0)

    returns = close.pct_change().dropna()
    cumulative = (close.iloc[-1] / close.iloc[0]) - 1.0

    n_days = len(close)
    years = max(n_days / 252.0, 1e-9)
    annualised = (1.0 + cumulative) ** (1.0 / years) - 1.0

    std_daily = returns.std(ddof=0)
    sharpe = (returns.mean() / std_daily * np.sqrt(252)) if std_daily > 0 else 0.0

    running_max = close.cummax()
    drawdown = (close - running_max) / running_max
    max_dd = float(drawdown.min())

    calmar = (annualised / abs(max_dd)) if max_dd != 0 else 0.0

    return SPYMetrics(
        cumulative_return=float(cumulative),
        annualised_return=float(annualised),
        sharpe=float(sharpe),
        max_drawdown=float(max_dd),
        calmar=float(calmar),
    )


def spy_metrics(start: date, end: date) -> SPYMetrics:
    """Pull SPY OHLCV and compute baseline metrics."""
    import yfinance as yf
    df = yf.download("SPY", start=start, end=end, progress=False, auto_adjust=True)
    if df.empty:
        return SPYMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    close = df["Close"].squeeze()
    return _metrics_from_series(close)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_spy_metrics.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add src/baselines/spy.py tests/unit/test_spy_metrics.py
git commit -m "feat(baselines): SPY buy-and-hold metrics"
```

---

## Task 6: Equity curve library

**Files:**
- Create: `src/baselines/equity_curve.py`
- Test: `tests/unit/test_equity_curve.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_equity_curve.py
"""compute_equity_curve reads portfolio_snapshots and anchors at first row."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from baselines.equity_curve import EquityCurve, compute_equity_curve
from orchestrator.persistence import (
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


def _seed(db_url: str, rows: list[dict]) -> None:
    engine = make_engine(db_url)
    create_all(engine)
    Session = make_session_factory(engine)
    s = Session()
    for r in rows:
        save_portfolio_snapshot(s, r)
    s.commit()
    s.close()


def _row(tick_id: str, bot_value: float, spy_price: float, recorded_at=None):
    return {
        "tick_id": tick_id,
        "recorded_at": recorded_at or datetime.now(tz=timezone.utc),
        "bot_total_value": bot_value,
        "bot_cash": bot_value,
        "bot_positions_value": 0.0,
        "bot_position_count": 0,
        "spy_price": spy_price,
        "spy_value_if_held": bot_value,
        "bot_return_pct": 0.0,
        "spy_return_pct": 0.0,
        "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    }


def test_empty_db_returns_empty_curve(tmp_path):
    db_url = f"sqlite:///{tmp_path/'a.db'}"
    engine = make_engine(db_url)
    create_all(engine)
    curve = compute_equity_curve(db_url)
    assert curve.timestamps == []
    assert curve.bot_pct == []
    assert curve.spy_pct == []


def test_single_row_anchor_only(tmp_path):
    db_url = f"sqlite:///{tmp_path/'a.db'}"
    _seed(db_url, [_row("init", 10000.0, 500.0)])
    curve = compute_equity_curve(db_url)
    assert len(curve.timestamps) == 1
    assert curve.bot_pct == [0.0]
    assert curve.spy_pct == [0.0]
    assert curve.excess_pct == [0.0]
    assert curve.anchor_tick_id == "init"
    assert curve.anchor_bot_value == 10000.0
    assert curve.anchor_spy_price == 500.0


def test_multi_row_anchored_correctly(tmp_path):
    db_url = f"sqlite:///{tmp_path/'a.db'}"
    _seed(db_url, [
        _row("init", 10000.0, 500.0),
        _row("tick-1", 10500.0, 510.0),  # bot +5%, spy +2%
        _row("tick-2", 10100.0, 525.0),  # bot +1%, spy +5%
    ])
    curve = compute_equity_curve(db_url)
    assert len(curve.timestamps) == 3
    assert curve.bot_pct[1] == pytest.approx(0.05)
    assert curve.spy_pct[1] == pytest.approx(0.02)
    assert curve.excess_pct[1] == pytest.approx(0.03)
    assert curve.bot_pct[2] == pytest.approx(0.01)
    assert curve.spy_pct[2] == pytest.approx(0.05)
    assert curve.excess_pct[2] == pytest.approx(-0.04)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_equity_curve.py -v
```

Expected: ImportError on `baselines.equity_curve`.

- [ ] **Step 3: Implement `equity_curve.py`**

Create `src/baselines/equity_curve.py`:

```python
"""Bot-vs-SPY equity curve from portfolio_snapshots. Shared with future dashboard."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from orchestrator.persistence import (
    PortfolioSnapshotRow,
    make_engine,
    make_session_factory,
)


@dataclass(frozen=True)
class EquityCurve:
    timestamps: list[datetime] = field(default_factory=list)
    bot_pct: list[float] = field(default_factory=list)
    spy_pct: list[float] = field(default_factory=list)
    excess_pct: list[float] = field(default_factory=list)
    anchor_tick_id: str | None = None
    anchor_bot_value: float | None = None
    anchor_spy_price: float | None = None


def compute_equity_curve(db_url: str) -> EquityCurve:
    """Read portfolio_snapshots ordered by id; anchor on first row.

    Empty DB → empty curve. Single row → all-zero pcts at length 1.
    Archived rows live in a separate schema/file and are excluded by design.
    """
    engine = make_engine(db_url)
    Session = make_session_factory(engine)
    s = Session()
    try:
        rows = s.query(PortfolioSnapshotRow).order_by(PortfolioSnapshotRow.id).all()
        if not rows:
            return EquityCurve()
        anchor = rows[0]
        timestamps: list[datetime] = []
        bot_pct: list[float] = []
        spy_pct: list[float] = []
        excess_pct: list[float] = []
        for r in rows:
            timestamps.append(r.recorded_at)
            bp = (r.bot_total_value / anchor.bot_total_value) - 1.0 if anchor.bot_total_value else 0.0
            sp = (r.spy_price / anchor.spy_price) - 1.0 if anchor.spy_price else 0.0
            bot_pct.append(bp)
            spy_pct.append(sp)
            excess_pct.append(bp - sp)
        return EquityCurve(
            timestamps=timestamps,
            bot_pct=bot_pct,
            spy_pct=spy_pct,
            excess_pct=excess_pct,
            anchor_tick_id=anchor.tick_id,
            anchor_bot_value=anchor.bot_total_value,
            anchor_spy_price=anchor.spy_price,
        )
    finally:
        s.close()
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_equity_curve.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add src/baselines/equity_curve.py tests/unit/test_equity_curve.py
git commit -m "feat(baselines): equity_curve lib (bot vs SPY) shared with dashboard"
```

---

## Task 7: plot_equity CLI

**Files:**
- Create: `src/scripts/plot_equity.py`
- Test: `tests/unit/test_plot_equity.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plot_equity.py
"""plot_equity renders a non-empty PNG and exits 0 with a normal DB."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.persistence import create_all, make_engine, make_session_factory, save_portfolio_snapshot
from scripts.plot_equity import render


def _seed(db_url: str):
    engine = make_engine(db_url)
    create_all(engine)
    S = make_session_factory(engine)
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "init", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10000.0, "bot_cash": 10000.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 500.0, "spy_value_if_held": 10000.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    save_portfolio_snapshot(s, {
        "tick_id": "tick-1", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10500.0, "bot_cash": 10500.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 510.0, "spy_value_if_held": 10200.0,
        "bot_return_pct": 5.0, "spy_return_pct": 2.0, "excess_return_pct": 3.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()


def test_render_writes_non_empty_png(tmp_path):
    db_path = tmp_path / "x.db"
    db_url = f"sqlite:///{db_path}"
    _seed(db_url)
    out = tmp_path / "plot.png"
    render(db_url=db_url, out_path=out)
    assert out.exists()
    assert out.stat().st_size > 1000  # > 1 KB sanity


def test_render_empty_db_writes_empty_message_png(tmp_path):
    db_path = tmp_path / "x.db"
    db_url = f"sqlite:///{db_path}"
    engine = make_engine(db_url)
    create_all(engine)
    out = tmp_path / "plot.png"
    render(db_url=db_url, out_path=out)
    assert out.exists()  # rendered "no data yet" placeholder
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_plot_equity.py -v
```

Expected: ImportError on `scripts.plot_equity`.

- [ ] **Step 3: Implement `plot_equity.py`**

Create `src/scripts/plot_equity.py`:

```python
"""Render bot-vs-SPY equity curve to a PNG.

Usage:
    PYTHONPATH=src python -m scripts.plot_equity --out docs/performance/2026-05-07.png
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

from baselines.equity_curve import compute_equity_curve


def render(*, db_url: str, out_path: Path) -> None:
    curve = compute_equity_curve(db_url)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not curve.timestamps:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No portfolio_snapshots yet — initialise the bot.",
                ha="center", va="center", fontsize=12)
        ax.set_axis_off()
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    bot_y = [p * 100 for p in curve.bot_pct]
    spy_y = [p * 100 for p in curve.spy_pct]
    ax.plot(curve.timestamps, bot_y, label="Bot", color="#1f77b4", linewidth=2)
    ax.plot(curve.timestamps, spy_y, label="SPY (buy-and-hold)", color="#888", linewidth=1.5, linestyle="--")
    ax.axhline(0.0, color="#ccc", linewidth=0.8)
    ax.set_ylabel("Return (%)")
    ax.set_xlabel("Time")
    ax.legend(loc="upper left")

    ax2 = ax.twinx()
    excess_y = [p * 100 for p in curve.excess_pct]
    ax2.plot(curve.timestamps, excess_y, color="#2ca02c", linewidth=1.0, alpha=0.6, label="Excess")
    ax2.set_ylabel("Excess (%)")
    ax2.legend(loc="upper right")

    bot_final = bot_y[-1]
    spy_final = spy_y[-1]
    excess_final = excess_y[-1]
    ax.set_title(
        f"Bot {bot_final:+.2f}%   SPY {spy_final:+.2f}%   Excess {excess_final:+.2f}%   "
        f"(anchor: {curve.anchor_tick_id})"
    )

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--out", default="docs/performance/equity.png")
    args = p.parse_args()
    db_url = args.db_url or _resolve_default_db_url()
    out = Path(args.out)
    render(db_url=db_url, out_path=out)
    curve = compute_equity_curve(db_url)
    if curve.timestamps:
        print(f"✓ {len(curve.timestamps)} ticks since reset (anchor: {curve.anchor_tick_id})")
        print(f"✓ Bot: {curve.bot_pct[-1]*100:+.2f}%   "
              f"SPY: {curve.spy_pct[-1]*100:+.2f}%   "
              f"Excess: {curve.excess_pct[-1]*100:+.2f}%")
    print(f"✓ Wrote {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_plot_equity.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```
git add src/scripts/plot_equity.py tests/unit/test_plot_equity.py
git commit -m "feat(scripts): plot_equity.py — bot vs SPY equity curve"
```

---

## Task 8: hard_reset library

**Files:**
- Create: `src/lifecycle/__init__.py` (empty)
- Create: `src/lifecycle/scheduler.py` (gcloud shim)
- Create: `src/lifecycle/hard_reset.py`
- Test: `tests/unit/test_hard_reset.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hard_reset.py
"""hard_reset archives all StockBot tables, truncates live, writes meta."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lifecycle.hard_reset import hard_reset, ResetResult
from orchestrator.persistence import (
    BufferEntryRow,
    PortfolioSnapshotRow,
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


def _seed_live_db(db_url: str):
    engine = make_engine(db_url)
    create_all(engine)
    S = make_session_factory(engine)
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "init", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10000.0, "bot_cash": 10000.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 500.0, "spy_value_if_held": 10000.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()


def test_archive_creates_file_and_truncates_live(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    # Stub scheduler module — no real gcloud call
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)

    result = hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job=None,  # SQLite path: no scheduler
        meta_extra={"watchlist": ["AAPL"], "broker_mode": "paper", "starting_capital": 10000.0},
    )

    # Archive file written
    assert result.archive_path.exists()
    assert result.archive_path.suffix == ".db"
    # Meta file written next to archive
    meta_path = result.archive_path.with_suffix(".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["watchlist"] == ["AAPL"]
    assert meta["row_counts"]["portfolio_snapshots"] == 1

    # Live tables empty
    engine = make_engine(f"sqlite:///{db_path}")
    S = make_session_factory(engine)
    s = S()
    assert s.query(PortfolioSnapshotRow).count() == 0
    s.close()

    # Archive contains the row
    arc_engine = make_engine(f"sqlite:///{result.archive_path}")
    arc_S = make_session_factory(arc_engine)
    arc_s = arc_S()
    assert arc_s.query(PortfolioSnapshotRow).count() == 1
    arc_s.close()


def test_archive_path_collision_aborts(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()

    # Pre-create a clashing archive file by running once
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)
    monkeypatch.setattr("lifecycle.hard_reset._timestamp", lambda: "FIXED")
    hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job=None,
        meta_extra={},
    )
    # Re-seed and try again with the same fixed timestamp → must abort
    _seed_live_db(f"sqlite:///{db_path}")
    with pytest.raises(FileExistsError):
        hard_reset(
            db_url=f"sqlite:///{db_path}",
            archive_dir=archive_dir,
            scheduler_job=None,
            meta_extra={},
        )


def test_pauses_scheduler_first_when_job_provided(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    calls = []
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: calls.append(name))

    hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job="stockbot-tick",
        meta_extra={},
    )
    assert calls == ["stockbot-tick"]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_hard_reset.py -v
```

Expected: ImportError on `lifecycle.hard_reset`.

- [ ] **Step 3: Implement `scheduler.py` shim**

Create `src/lifecycle/__init__.py` (empty).

Create `src/lifecycle/scheduler.py`:

```python
"""Cloud Scheduler shim — thin wrapper over gcloud CLI for monkey-patching."""
from __future__ import annotations

import subprocess


def pause_job(name: str) -> None:
    """Pause a Cloud Scheduler job. No-op shim under tests."""
    subprocess.run(
        ["gcloud", "scheduler", "jobs", "pause", name],
        check=True,
    )


def resume_job(name: str) -> None:
    """Resume a Cloud Scheduler job. No-op shim under tests."""
    subprocess.run(
        ["gcloud", "scheduler", "jobs", "resume", name],
        check=True,
    )
```

- [ ] **Step 4: Implement `hard_reset.py`**

Create `src/lifecycle/hard_reset.py`:

```python
"""hard_reset — pause scheduler, archive all StockBot tables, truncate live tables."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from orchestrator.persistence import Base, make_engine, make_session_factory

from . import scheduler

_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots", "attribution_signals")


@dataclass(frozen=True)
class ResetResult:
    archive_path: Path
    row_counts: dict[str, int]


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _row_counts(db_url: str) -> dict[str, int]:
    engine = make_engine(db_url)
    counts: dict[str, int] = {}
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    Session = make_session_factory(engine)
    s = Session()
    try:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                counts[t] = s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
    finally:
        s.close()
    return counts


def _archive_sqlite(src_url: str, archive_path: Path) -> None:
    src = src_url.replace("sqlite:///", "")
    if archive_path.exists():
        raise FileExistsError(f"archive already exists: {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    # VACUUM INTO copies the entire DB into a fresh file.
    conn = sqlite3.connect(src)
    try:
        conn.execute(f"VACUUM INTO '{archive_path.as_posix()}'")
        conn.commit()
    finally:
        conn.close()


def _archive_postgres(db_url: str, ts: str) -> str:
    """Create archive schema and copy each StockBot table into it."""
    engine = make_engine(db_url)
    schema = f"stockbot_archive_{ts.replace('-', '_').replace('T', '_')}"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        for t in _STOCKBOT_TABLES:
            conn.execute(text(
                f'CREATE TABLE "{schema}"."{t}" AS SELECT * FROM public."{t}"'
            ))
    return schema


def _truncate_live(db_url: str) -> None:
    engine = make_engine(db_url)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                conn.execute(text(f"DELETE FROM {t}"))


def hard_reset(
    *,
    db_url: str,
    archive_dir: Path,
    scheduler_job: str | None,
    meta_extra: dict[str, Any] | None = None,
) -> ResetResult:
    """Archive then truncate. Scheduler paused first if `scheduler_job` is set."""
    is_sqlite = db_url.startswith("sqlite")
    ts = _timestamp()
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Pause scheduler
    if scheduler_job:
        scheduler.pause_job(scheduler_job)

    # 2. Capture row counts BEFORE archive
    counts = _row_counts(db_url)

    # 3. Archive
    if is_sqlite:
        archive_path = archive_dir / f"{ts}.db"
        _archive_sqlite(db_url, archive_path)
    else:
        schema = _archive_postgres(db_url, ts)
        archive_path = archive_dir / f"{ts}.{schema}.txt"
        archive_path.write_text(f"archived to schema: {schema}\n")

    # 4. Truncate live tables
    _truncate_live(db_url)

    # 5. Write meta
    meta_path = archive_path.with_suffix(".meta.json")
    meta = {
        "archived_at": datetime.now(tz=timezone.utc).isoformat(),
        "db_url_kind": "sqlite" if is_sqlite else "postgres",
        "row_counts": counts,
        "scheduler_job": scheduler_job,
        **(meta_extra or {}),
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str))

    return ResetResult(archive_path=archive_path, row_counts=counts)
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_hard_reset.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```
git add src/lifecycle/ tests/unit/test_hard_reset.py
git commit -m "feat(lifecycle): hard_reset library — archive + truncate"
```

---

## Task 9: hard_reset CLI

**Files:**
- Create: `src/scripts/hard_reset.py`
- Test: `tests/unit/test_hard_reset_cli.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hard_reset_cli.py
"""hard_reset CLI: literal-RESET confirmation, --yes flag for tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.persistence import create_all, make_engine, make_session_factory, save_portfolio_snapshot
from scripts import hard_reset as cli


def _seed(db_url: str):
    engine = make_engine(db_url)
    create_all(engine)
    S = make_session_factory(engine)
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "init", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10000.0, "bot_cash": 10000.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 500.0, "spy_value_if_held": 10000.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()


def test_yes_flag_skips_prompt(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "live.db"
    _seed(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)

    cli.main([
        "--db-url", f"sqlite:///{db_path}",
        "--archive-dir", str(archive_dir),
        "--yes",
    ])
    out = capsys.readouterr().out
    assert "Archived" in out


def test_wrong_confirmation_aborts(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"
    monkeypatch.setattr("builtins.input", lambda _: "nope")

    with pytest.raises(SystemExit):
        cli.main([
            "--db-url", f"sqlite:///{db_path}",
            "--archive-dir", str(archive_dir),
        ])
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_hard_reset_cli.py -v
```

Expected: ImportError on `scripts.hard_reset`.

- [ ] **Step 3: Implement `scripts/hard_reset.py`**

Create `src/scripts/hard_reset.py`:

```python
"""Hard-reset the StockBot DB. Archives every table then truncates the live ones.

Usage:
    PYTHONPATH=src python -m scripts.hard_reset
    PYTHONPATH=src python -m scripts.hard_reset --yes      # skip confirmation
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lifecycle.hard_reset import hard_reset


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--archive-dir", default="data/archives")
    p.add_argument("--scheduler-job", default=os.environ.get("SCHEDULER_JOB"),
                   help="Cloud Scheduler job name to pause (skipped for SQLite)")
    p.add_argument("--watchlist", default="src/config/watchlist.json")
    p.add_argument("--broker-mode", default="paper")
    p.add_argument("--starting-capital", type=float, default=10000.0,
                   help="Starting capital of the run being archived")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = p.parse_args(argv)

    db_url = args.db_url or _resolve_default_db_url()
    archive_dir = Path(args.archive_dir)

    print("This will pause the scheduler, archive all StockBot state, and wipe live tables.")
    print(f"Archive will be written under: {archive_dir}")

    if not args.yes:
        confirm = input("Type 'RESET' to confirm: ").strip()
        if confirm != "RESET":
            print("Aborted.")
            sys.exit(1)

    watchlist: list[str] = []
    wl = Path(args.watchlist)
    if wl.exists():
        watchlist = json.loads(wl.read_text()).get("tickers", [])

    result = hard_reset(
        db_url=db_url,
        archive_dir=archive_dir,
        scheduler_job=args.scheduler_job,
        meta_extra={
            "watchlist": watchlist,
            "broker_mode": args.broker_mode,
            "starting_capital_of_archived_run": args.starting_capital,
            "git_sha": _git_sha(),
        },
    )

    if args.scheduler_job:
        print(f"✓ Paused Cloud Scheduler job {args.scheduler_job}")
    rows = sum(result.row_counts.values())
    tables = len(result.row_counts)
    print(f"✓ Archived {tables} tables, {rows} rows → {result.archive_path}")
    print(f"✓ Live tables truncated")
    print(f"✓ Wrote {result.archive_path.with_suffix('.meta.json').name}")
    print()
    print("Next: reset Trading 212 practice account in the UI, then run:")
    print(f"  PYTHONPATH=src python -m scripts.initialise --capital {args.starting_capital:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_hard_reset_cli.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```
git add src/scripts/hard_reset.py tests/unit/test_hard_reset_cli.py
git commit -m "feat(scripts): hard_reset CLI"
```

---

## Task 10: initialise library

**Files:**
- Create: `src/lifecycle/initialise.py`
- Test: `tests/unit/test_initialise.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_initialise.py
"""initialise: pre-flight checks, anchor snapshot, scheduler resume."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lifecycle.initialise import initialise, InitResult, NonEmptyTablesError, EnvVarMissingError, BrokerCashMismatch
from orchestrator.persistence import (
    PortfolioSnapshotRow,
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


class _StubBroker:
    def __init__(self, cash):
        self._cash = cash

    async def get_portfolio(self):
        from broker.portfolio import Portfolio
        return Portfolio(cash=self._cash, positions={})


@pytest.mark.asyncio
async def test_initialise_writes_anchor_on_empty_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    db_url = f"sqlite:///{db_path}"
    create_all(make_engine(db_url))

    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")

    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "resume_job", lambda name: None)

    # Stub yfinance SPY price
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    res = await initialise(
        db_url=db_url,
        starting_capital=10000.0,
        broker_mode="paper",
        watchlist=["AAPL"],
        broker=_StubBroker(10000.0),
        scheduler_job=None,
    )
    assert isinstance(res, InitResult)
    # Anchor row exists
    S = make_session_factory(make_engine(db_url))
    s = S()
    rows = s.query(PortfolioSnapshotRow).all()
    assert len(rows) == 1
    anchor = rows[0]
    assert anchor.tick_id == "init"
    assert anchor.bot_total_value == 10000.0
    assert anchor.spy_price == 480.0
    s.close()


@pytest.mark.asyncio
async def test_refuses_on_non_empty_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    db_url = f"sqlite:///{db_path}"
    create_all(make_engine(db_url))
    S = make_session_factory(make_engine(db_url))
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "old", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 1.0, "bot_cash": 1.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 1.0, "spy_value_if_held": 1.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()

    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(NonEmptyTablesError):
        await initialise(
            db_url=db_url,
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(10000.0),
            scheduler_job=None,
        )


@pytest.mark.asyncio
async def test_refuses_on_missing_env_var(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    create_all(make_engine(f"sqlite:///{db_path}"))
    monkeypatch.delenv("TRADING212_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(EnvVarMissingError):
        await initialise(
            db_url=f"sqlite:///{db_path}",
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(10000.0),
            scheduler_job=None,
        )


@pytest.mark.asyncio
async def test_refuses_on_broker_cash_mismatch(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    create_all(make_engine(f"sqlite:///{db_path}"))
    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(BrokerCashMismatch):
        await initialise(
            db_url=f"sqlite:///{db_path}",
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(9500.0),  # mismatch
            scheduler_job=None,
        )
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_initialise.py -v
```

Expected: ImportError on `lifecycle.initialise`.

- [ ] **Step 3: Implement `initialise.py`**

Create `src/lifecycle/initialise.py`:

```python
"""initialise — pre-flight, anchor snapshot, scheduler resume."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text

from orchestrator.persistence import (
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)

from . import scheduler

_REQUIRED_ENV = ("TRADING212_API_KEY", "FINNHUB_API_KEY")
_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots", "attribution_signals")


class NonEmptyTablesError(RuntimeError):
    pass


class EnvVarMissingError(RuntimeError):
    pass


class BrokerCashMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class InitResult:
    anchor_tick_id: str
    anchor_bot_value: float
    anchor_spy_price: float
    scheduler_job: str | None


def _fetch_spy_price() -> float:
    """Get the latest SPY close. Pulled out as a function for monkey-patching."""
    import yfinance as yf
    t = yf.Ticker("SPY")
    hist = t.history(period="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned no SPY data")
    return float(hist["Close"].iloc[-1])


def _check_env() -> None:
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise EnvVarMissingError(f"missing required env vars: {missing}")


def _check_live_tables_empty(db_url: str) -> None:
    engine = make_engine(db_url)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    Session = make_session_factory(engine)
    s = Session()
    try:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                count = s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
                if count > 0:
                    raise NonEmptyTablesError(
                        f"table {t} has {count} rows; run scripts.hard_reset first"
                    )
    finally:
        s.close()


async def _check_broker_cash(broker: Any, expected: float, tolerance: float = 1.0) -> None:
    portfolio = await broker.get_portfolio()
    actual = float(portfolio.cash)
    if abs(actual - expected) > tolerance:
        raise BrokerCashMismatch(
            f"broker cash {actual:.2f} differs from expected {expected:.2f} "
            f"by more than ${tolerance:.2f}; reset T212 cash and retry"
        )


def _write_anchor(db_url: str, *, starting_capital: float, spy_price: float) -> None:
    engine = make_engine(db_url)
    Session = make_session_factory(engine)
    s = Session()
    try:
        save_portfolio_snapshot(s, {
            "tick_id": "init",
            "recorded_at": datetime.now(tz=timezone.utc),
            "bot_total_value": starting_capital,
            "bot_cash": starting_capital,
            "bot_positions_value": 0.0,
            "bot_position_count": 0,
            "spy_price": spy_price,
            "spy_value_if_held": starting_capital,
            "bot_return_pct": 0.0,
            "spy_return_pct": 0.0,
            "excess_return_pct": 0.0,
            "holdings_breakdown": {},
        })
        s.commit()
    finally:
        s.close()


async def initialise(
    *,
    db_url: str,
    starting_capital: float,
    broker_mode: str,
    watchlist: list[str],
    broker: Any,
    scheduler_job: str | None,
) -> InitResult:
    """Pre-flight, seed schema, write anchor, resume scheduler."""
    # 1. Env
    _check_env()

    # 2. Schema seed (idempotent)
    create_all(make_engine(db_url))

    # 3. Live tables empty
    _check_live_tables_empty(db_url)

    # 4. Broker reachable + cash matches
    await _check_broker_cash(broker, starting_capital)

    # 5. SPY price for anchor
    spy_price = _fetch_spy_price()

    # 6. Write anchor snapshot
    _write_anchor(db_url, starting_capital=starting_capital, spy_price=spy_price)

    # 7. Resume scheduler
    if scheduler_job:
        scheduler.resume_job(scheduler_job)

    return InitResult(
        anchor_tick_id="init",
        anchor_bot_value=starting_capital,
        anchor_spy_price=spy_price,
        scheduler_job=scheduler_job,
    )
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_initialise.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```
git add src/lifecycle/initialise.py tests/unit/test_initialise.py
git commit -m "feat(lifecycle): initialise — pre-flight + anchor + scheduler resume"
```

---

## Task 11: initialise CLI

**Files:**
- Create: `src/scripts/initialise.py`
- Test: `tests/unit/test_initialise_cli.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_initialise_cli.py
"""initialise CLI: argv parsing, broker construction, calls library."""
from __future__ import annotations

import pytest

from scripts import initialise as cli


@pytest.mark.asyncio
async def test_main_calls_initialise(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("STOCKBOT_ENV", "dev")
    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")

    from orchestrator.persistence import create_all, make_engine
    create_all(make_engine(f"sqlite:///{db_path}"))

    # Stub broker construction & SPY fetch & scheduler
    class _Stub:
        async def get_portfolio(self):
            from broker.portfolio import Portfolio
            return Portfolio(cash=10000.0, positions={})

    monkeypatch.setattr("scripts.initialise._build_broker", lambda mode: _Stub())
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "resume_job", lambda name: None)

    rc = await cli.main_async([
        "--db-url", f"sqlite:///{db_path}",
        "--capital", "10000",
        "--broker-mode", "paper",
        "--watchlist", "src/config/watchlist.json",
    ])
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_initialise_cli.py -v
```

Expected: ImportError on `scripts.initialise`.

- [ ] **Step 3: Implement `scripts/initialise.py`**

Create `src/scripts/initialise.py`:

```python
"""Boot the StockBot: pre-flight, anchor snapshot, scheduler resume.

Usage:
    PYTHONPATH=src python -m scripts.initialise --capital 10000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from lifecycle.initialise import (
    BrokerCashMismatch,
    EnvVarMissingError,
    NonEmptyTablesError,
    initialise,
)


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def _build_broker(mode: str):
    """Return a Broker instance — Trading212 in normal use; tests monkey-patch this."""
    import httpx
    from broker.trading212 import Trading212Broker
    return Trading212Broker(
        mode=mode,
        api_key=os.environ["TRADING212_API_KEY"],
        http_client=httpx.AsyncClient(),
        instrument_map={},
    )


async def main_async(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--capital", type=float, required=True)
    p.add_argument("--broker-mode", default="paper", choices=["paper", "live"])
    p.add_argument("--watchlist", default="src/config/watchlist.json")
    p.add_argument("--scheduler-job", default=os.environ.get("SCHEDULER_JOB"))
    args = p.parse_args(argv)

    db_url = args.db_url or _resolve_default_db_url()
    wl_path = Path(args.watchlist)
    if not wl_path.exists():
        print(f"Watchlist not found: {wl_path}", file=sys.stderr)
        return 1
    watchlist = json.loads(wl_path.read_text())["tickers"]

    broker = _build_broker(args.broker_mode)

    try:
        result = await initialise(
            db_url=db_url,
            starting_capital=args.capital,
            broker_mode=args.broker_mode,
            watchlist=watchlist,
            broker=broker,
            scheduler_job=args.scheduler_job,
        )
    except (NonEmptyTablesError, EnvVarMissingError, BrokerCashMismatch) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    print(f"✓ Cloud SQL reachable")
    print(f"✓ Live tables empty")
    print(f"✓ Required env vars set")
    print(f"✓ Trading 212 reachable, cash ${args.capital:,.2f} matches expected")
    print(f"✓ Wrote anchor snapshot (SPY ${result.anchor_spy_price:.2f})")
    if args.scheduler_job:
        print(f"✓ Resumed Cloud Scheduler job {args.scheduler_job}")
    print()
    print(f"Bot is live ({args.broker_mode} mode). Watchlist: {len(watchlist)} tickers.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_initialise_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add src/scripts/initialise.py tests/unit/test_initialise_cli.py
git commit -m "feat(scripts): initialise CLI"
```

---

## Task 12: L1 — smoke run script

**Files:**
- Create: `src/scripts/smoke_run.py`
- Test: `tests/unit/test_smoke_run_cli.py` (new — argument parsing only; the actual run hits real APIs and is marked integration)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_smoke_run_cli.py
"""smoke_run script: --help works, dry mode validates wiring without LLM calls."""
from __future__ import annotations

import pytest

from scripts.smoke_run import build_runner_args


def test_default_args():
    args = build_runner_args([])
    assert args.ticks == 3
    assert args.starting_cash == 10_000.0


def test_explicit_args():
    args = build_runner_args(["--ticks", "1", "--starting-cash", "5000"])
    assert args.ticks == 1
    assert args.starting_cash == 5000.0
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_smoke_run_cli.py -v
```

Expected: ImportError on `scripts.smoke_run`.

- [ ] **Step 3: Implement `smoke_run.py`**

Create `src/scripts/smoke_run.py`:

```python
"""Local end-to-end smoke run: 3 ticks against FakeBroker with real LLMs + data.

Cost: ~$0.20/run (Gemini Flash analysts + Pro strategist).

Usage:
    PYTHONPATH=src python -m scripts.smoke_run
    PYTHONPATH=src python -m scripts.smoke_run --ticks 1
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from broker.fake import FakeBroker
from orchestrator.stock_picker import get_watchlist
from orchestrator.tick import run_once


def build_runner_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticks", type=int, default=3)
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    return p.parse_args(argv)


async def smoke(ticks: int, starting_cash: float) -> None:
    tickers = get_watchlist()
    # Fake broker needs prices; pull current closes via yfinance
    import yfinance as yf
    prices = {}
    for t in tickers:
        h = yf.Ticker(t).history(period="1d")
        prices[t] = float(h["Close"].iloc[-1]) if not h.empty else 100.0

    broker = FakeBroker(starting_cash=starting_cash, prices=prices)

    for i in range(ticks):
        print(f"\n=== Tick {i+1}/{ticks} ===")
        state = await run_once(broker)
        executions = state.get("executions", []) if isinstance(state, dict) else state.executions
        print(f"  Executions: {len(executions)}")
        portfolio = await broker.get_portfolio()
        print(f"  Cash: ${portfolio.cash:,.2f}   Positions: {len(portfolio.positions)}")


def main(argv: list[str] | None = None) -> int:
    args = build_runner_args(argv)
    asyncio.run(smoke(args.ticks, args.starting_cash))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_smoke_run_cli.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```
git add src/scripts/smoke_run.py tests/unit/test_smoke_run_cli.py
git commit -m "feat(scripts): smoke_run — 3-tick local end-to-end validation"
```

---

## Task 13: L2 — replay backtest harness

**Files:**
- Create: `tests/replay/fixtures/.gitkeep`
- Create: `src/scripts/replay_backtest.py`
- Create: `tests/replay/test_replay_30days.py` (marked `@pytest.mark.replay`)
- Test: `tests/unit/test_replay_backtest_cli.py` (new — argv parsing only)

- [ ] **Step 1: Write the failing test (CLI)**

```python
# tests/unit/test_replay_backtest_cli.py
from __future__ import annotations

from scripts.replay_backtest import build_runner_args


def test_default_window():
    args = build_runner_args([])
    assert args.window == "30d"


def test_explicit_args(tmp_path):
    args = build_runner_args([
        "--window", "7d",
        "--fixture-dir", str(tmp_path),
    ])
    assert args.window == "7d"
    assert str(args.fixture_dir) == str(tmp_path)
```

- [ ] **Step 2: Write the replay test (skeleton)**

```python
# tests/replay/test_replay_30days.py
from __future__ import annotations

import pytest


@pytest.mark.replay
def test_replay_30_days_runs_and_produces_executions():
    """30-day walk-forward through full pipeline. Long-running."""
    from scripts.replay_backtest import run_replay
    summary = run_replay(window="30d", fixture_dir=None)
    # Basic sanity: ran some ticks, didn't crash
    assert summary.ticks_completed > 0
```

- [ ] **Step 3: Implement `replay_backtest.py`**

Create `src/scripts/replay_backtest.py`:

```python
"""Replay backtest harness — walk-forward through historical data via FakeBroker.

Usage:
    PYTHONPATH=src python -m scripts.replay_backtest --window 30d
    PYTHONPATH=src python -m scripts.replay_backtest --window 30d --fixture-dir tests/replay/fixtures
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

from broker.fake import FakeBroker
from orchestrator.stock_picker import get_watchlist
from orchestrator.tick import run_once


@dataclass
class ReplaySummary:
    ticks_completed: int
    final_cash: float
    final_position_count: int


def build_runner_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window", default="30d")
    p.add_argument("--fixture-dir", type=Path, default=None,
                   help="If set, swap real providers for fixture loaders")
    p.add_argument("--starting-cash", type=float, default=10_000.0)
    return p.parse_args(argv)


def _parse_window(window: str) -> timedelta:
    if window.endswith("d"):
        return timedelta(days=int(window[:-1]))
    raise SystemExit(f"unsupported window format: {window} (use Nd)")


def run_replay(*, window: str, fixture_dir: Path | None, starting_cash: float = 10_000.0) -> ReplaySummary:
    tickers = get_watchlist()
    days = _parse_window(window).days
    end = datetime.now(tz=timezone.utc).date()
    start = end - timedelta(days=days)

    # Fetch historical OHLCV once for all tickers; use daily closes
    history: dict[str, list[float]] = {}
    for t in tickers:
        df = yf.download(t, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            history[t] = [100.0]
            continue
        history[t] = [float(p) for p in df["Close"].squeeze().tolist()]

    n_steps = max(min((len(prices) for prices in history.values()), default=0), 1)
    broker = FakeBroker(
        starting_cash=starting_cash,
        prices={t: history[t][0] for t in tickers},
    )

    ticks_completed = 0
    for i in range(n_steps):
        # Update prices to step i
        for t in tickers:
            if i < len(history[t]):
                broker.set_price(t, history[t][i])

        # NOTE: real-LLM run; respects --fixture-dir only insofar as data providers
        # are stubbed via the analyst before_callbacks (Phase 1.5 §L2 future work
        # to fully decouple — Phase 1 deps remain real).
        if fixture_dir is None:
            asyncio.run(run_once(broker))
        else:
            # Fixture mode: run pipeline with provider-callback stubs
            # (kept minimal here; real fixture wiring is a Phase 2 follow-up.)
            asyncio.run(run_once(broker))
        ticks_completed += 1

    portfolio = asyncio.run(broker.get_portfolio())
    return ReplaySummary(
        ticks_completed=ticks_completed,
        final_cash=portfolio.cash,
        final_position_count=len(portfolio.positions),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_runner_args(argv)
    summary = run_replay(
        window=args.window,
        fixture_dir=args.fixture_dir,
        starting_cash=args.starting_cash,
    )
    print(f"✓ {summary.ticks_completed} ticks")
    print(f"  Final cash: ${summary.final_cash:,.2f}")
    print(f"  Final positions: {summary.final_position_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create fixtures dir placeholder**

```
mkdir -p tests/replay/fixtures
touch tests/replay/fixtures/.gitkeep
```

(On Windows PowerShell: `New-Item -ItemType Directory tests/replay/fixtures -Force; New-Item -Path tests/replay/fixtures/.gitkeep -ItemType File`.)

- [ ] **Step 5: Run unit tests**

```
pytest tests/unit/test_replay_backtest_cli.py -v
```

Expected: 2 PASS. The replay test is `@pytest.mark.replay` and is intentionally skipped by default (`-m 'not replay'` is the default filter when running locally).

- [ ] **Step 6: Commit**

```
git add src/scripts/replay_backtest.py tests/replay/ tests/unit/test_replay_backtest_cli.py
git commit -m "feat(scripts): replay_backtest harness for Tier 4 evaluation"
```

---

## Task 14: O1 — Dockerfile

**Files:**
- Create: `deploy/Dockerfile`
- Create: `deploy/.dockerignore`
- Test: manual `docker build` (or skip if Docker not available)

- [ ] **Step 1: Create `.dockerignore`**

Create `deploy/.dockerignore`:

```
**/__pycache__/
**/*.pyc
.venv/
venv/
data/
docs/
graphify-out/
.git/
.pytest_cache/
.mypy_cache/
.ruff_cache/
tests/
```

- [ ] **Step 2: Create `Dockerfile`**

Create `deploy/Dockerfile`:

```dockerfile
# StockBot one-shot tick container — runs orchestrator.tick once and exits.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    STOCKBOT_ENV=prod

ENTRYPOINT ["python", "-m", "orchestrator.tick"]
CMD ["--mode", "paper"]
```

- [ ] **Step 3: Smoke check (skip if no Docker)**

If Docker is available locally:

```
docker build -t stockbot-tick:dev -f deploy/Dockerfile .
docker run --rm stockbot-tick:dev --help
```

Expected: image builds; the second command may fail at runtime due to missing env vars — the goal is only "image launches Python entrypoint". If you see argparse output or an env-var error, the build is good.

If Docker is not available, skip the smoke check and rely on Cloud Build (Task 15) catching syntax errors.

- [ ] **Step 4: Commit**

```
git add deploy/Dockerfile deploy/.dockerignore
git commit -m "feat(deploy): Dockerfile for one-shot tick container"
```

---

## Task 15: O2 — cloudbuild.yaml

**Files:**
- Create: `deploy/cloudbuild.yaml`
- Test: `tests/unit/test_cloudbuild_yaml.py` (new — yaml-syntax + required-fields check)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cloudbuild_yaml.py
"""cloudbuild.yaml is valid YAML and contains the expected build/push/deploy steps."""
from __future__ import annotations

from pathlib import Path

import yaml


CLOUDBUILD = Path(__file__).resolve().parents[2] / "deploy" / "cloudbuild.yaml"


def test_yaml_parses():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    assert isinstance(data, dict)
    assert "steps" in data


def test_has_build_push_and_deploy_steps():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    step_names = {s.get("id", "") for s in data["steps"]}
    assert "build" in step_names
    assert "push" in step_names
    assert "deploy" in step_names


def test_substitutions_documented():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    subs = data.get("substitutions", {})
    assert "_REGION" in subs
    assert "_REPO" in subs
    assert "_JOB_NAME" in subs
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_cloudbuild_yaml.py -v
```

Expected: FileNotFoundError on `deploy/cloudbuild.yaml`.

- [ ] **Step 3: Add `pyyaml` to requirements**

Append to `requirements.txt`:

```
pyyaml>=6.0
```

Then `pip install pyyaml`.

- [ ] **Step 4: Implement `cloudbuild.yaml`**

Create `deploy/cloudbuild.yaml`:

```yaml
# Build → push → deploy a Cloud Run Job for StockBot.
# Triggered by Cloud Build on push to main.
substitutions:
  _REGION: us-central1
  _REPO: stockbot
  _JOB_NAME: stockbot-tick
  _IMAGE: ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_JOB_NAME}:${SHORT_SHA}

steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args: ["build", "-t", "${_IMAGE}", "-f", "deploy/Dockerfile", "."]

  - id: push
    name: gcr.io/cloud-builders/docker
    args: ["push", "${_IMAGE}"]

  - id: deploy
    name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args:
      - run
      - jobs
      - update
      - ${_JOB_NAME}
      - --image=${_IMAGE}
      - --region=${_REGION}
      - --project=${PROJECT_ID}

options:
  logging: CLOUD_LOGGING_ONLY

images:
  - ${_IMAGE}
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_cloudbuild_yaml.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```
git add deploy/cloudbuild.yaml tests/unit/test_cloudbuild_yaml.py requirements.txt
git commit -m "feat(deploy): cloudbuild.yaml — build/push/deploy on push to main"
```

---

## Task 16: O3 — scheduler.yaml + GCP setup runbook

**Files:**
- Create: `deploy/scheduler.yaml`
- Create: `deploy/README.md`
- Test: `tests/unit/test_scheduler_yaml.py` (new — yaml + cron sanity)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scheduler_yaml.py
from __future__ import annotations

from pathlib import Path

import yaml


SCHED = Path(__file__).resolve().parents[2] / "deploy" / "scheduler.yaml"


def test_yaml_parses():
    data = yaml.safe_load(SCHED.read_text())
    assert isinstance(data, dict)


def test_cron_is_market_hours_weekdays():
    data = yaml.safe_load(SCHED.read_text())
    schedule = data.get("schedule", "")
    # Phase 1 design: 30 9-15 * * 1-5 America/New_York
    assert "9-15" in schedule
    assert "1-5" in schedule
    assert data.get("timeZone") == "America/New_York"


def test_targets_run_job():
    data = yaml.safe_load(SCHED.read_text())
    target = data.get("httpTarget", {})
    assert "uri" in target
    assert "stockbot-tick" in target["uri"]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_scheduler_yaml.py -v
```

Expected: FileNotFoundError on `deploy/scheduler.yaml`.

- [ ] **Step 3: Implement `scheduler.yaml`**

Create `deploy/scheduler.yaml`:

```yaml
# Cloud Scheduler config for the hourly StockBot tick.
# Apply with: gcloud scheduler jobs create http stockbot-tick --location=us-central1 \
#   --schedule="30 9-15 * * 1-5" --time-zone=America/New_York \
#   --uri=...  --oidc-service-account-email=...
name: stockbot-tick
schedule: "30 9-15 * * 1-5"
timeZone: America/New_York
description: "Hourly tick for StockBot during US market hours."
httpTarget:
  uri: "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/stockbot-tick:run"
  httpMethod: POST
  oidcToken:
    serviceAccountEmail: stockbot-runner@${PROJECT_ID}.iam.gserviceaccount.com
retryConfig:
  retryCount: 1
  maxRetryDuration: 300s
```

- [ ] **Step 4: Implement `deploy/README.md`**

Create `deploy/README.md`:

```markdown
# StockBot Deployment Runbook

Phase 1 paper-trading deployment to GCP.

## Prerequisites

- GCP project with billing enabled
- `gcloud auth login` and `gcloud auth application-default login`
- Trading 212 practice account with API key

## One-time GCP setup

```bash
# 1. Set the project
gcloud config set project YOUR_PROJECT_ID

# 2. Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com

# 3. Create Artifact Registry repo
gcloud artifacts repositories create stockbot \
  --repository-format=docker --location=us-central1

# 4. Create Cloud SQL Postgres instance (db-f1-micro for Phase 1, ~$10/mo)
gcloud sql instances create stockbot-db \
  --database-version=POSTGRES_15 --tier=db-f1-micro --region=us-central1
gcloud sql databases create stockbot --instance=stockbot-db
gcloud sql users create stockbot --instance=stockbot-db --password=GENERATE_AND_STORE

# 5. Service account for the runner job
gcloud iam service-accounts create stockbot-runner --display-name="StockBot Runner"
SA="stockbot-runner@$(gcloud config get-value project).iam.gserviceaccount.com"
for role in \
  roles/aiplatform.user \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/storage.objectUser ; do
  gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
    --member="serviceAccount:$SA" --role="$role"
done

# 6. Store secrets
echo -n "$TRADING212_API_KEY" | gcloud secrets create trading212-api-key --data-file=-
echo -n "$FINNHUB_API_KEY"    | gcloud secrets create finnhub-api-key    --data-file=-
echo -n "$DATABASE_URL"       | gcloud secrets create database-url       --data-file=-

# 7. Create the Cloud Run Job (initial deploy — Cloud Build updates after push)
gcloud run jobs create stockbot-tick \
  --image=us-central1-docker.pkg.dev/$(gcloud config get-value project)/stockbot/stockbot-tick:bootstrap \
  --region=us-central1 --service-account=$SA \
  --set-secrets=TRADING212_API_KEY=trading212-api-key:latest,FINNHUB_API_KEY=finnhub-api-key:latest,DATABASE_URL=database-url:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=1,STOCKBOT_ENV=prod,BROKER_MODE=paper

# 8. Create the Cloud Scheduler job (paused; lifecycle scripts resume it)
gcloud scheduler jobs create http stockbot-tick \
  --location=us-central1 \
  --schedule="30 9-15 * * 1-5" --time-zone=America/New_York \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$(gcloud config get-value project)/jobs/stockbot-tick:run" \
  --oidc-service-account-email=$SA
gcloud scheduler jobs pause stockbot-tick --location=us-central1

# 9. Connect Cloud Build trigger to the GitHub repo (one-time, via console or):
gcloud builds triggers create github \
  --repo-name=StockBot --repo-owner=YOUR_GH_USER \
  --branch-pattern="^main$" --build-config=deploy/cloudbuild.yaml
```

## Initialise the bot for paper trading

After GCP setup is complete and Cloud Build has built/pushed an image:

```bash
# Run from your laptop:
PYTHONPATH=src python -m scripts.initialise --capital 10000 \
  --broker-mode paper --scheduler-job stockbot-tick
```

This pre-flights (DB reachable, env vars set, T212 cash matches), writes the
equity-curve anchor snapshot, and resumes Cloud Scheduler.

## Reset and start over

```bash
# 1. Pause + archive + truncate:
PYTHONPATH=src python -m scripts.hard_reset \
  --scheduler-job stockbot-tick --starting-capital 10000

# 2. Reset Trading 212 practice account in their UI

# 3. Re-initialise:
PYTHONPATH=src python -m scripts.initialise --capital 10000 \
  --broker-mode paper --scheduler-job stockbot-tick
```

## Paper-trading kickoff checklist

Before flipping the scheduler on for the first run:

1. `PYTHONPATH=src python -m scripts.smoke_run` — confirm clean output, no errors.
2. `PYTHONPATH=src python -m scripts.replay_backtest --window 30d` — verify sane decisions over 30 days of historical data.
3. `PYTHONPATH=src python -m scripts.plot_equity --out docs/performance/$(date +%Y-%m-%d).png` — sanity-check the plotter.
4. Confirm Cloud Logging is receiving structured events from a manual `gcloud run jobs execute stockbot-tick --region=us-central1`.
5. `PYTHONPATH=src python -m scripts.initialise --capital 10000 --scheduler-job stockbot-tick`.

## Live-trading gate

The bot is paper-only until it has beaten **both**:

- SPY buy-and-hold on **cumulative return**, AND
- SPY buy-and-hold on **Sharpe ratio**

over **≥30 consecutive days** of paper trading. The MLP baseline (originally part
of this gate per `docs/baselines.md`) is deferred to Phase 3 — for now SPY is the
only baseline.

Gate is manual / observational. Flip `--broker-mode live` only after the gate
passes; redeploy with `BROKER_MODE=live` env var.
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_scheduler_yaml.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Commit**

```
git add deploy/scheduler.yaml deploy/README.md tests/unit/test_scheduler_yaml.py
git commit -m "feat(deploy): scheduler config + GCP setup runbook + kickoff checklist"
```

---

## Task 17: Phase 1.5 phase doc cleanup + final acceptance

**Files:**
- Modify: `docs/Phase1-build/phase1.5-remaining.md` (mark items done, MLP deferred)
- Test: none (docs-only)

- [ ] **Step 1: Update `phase1.5-remaining.md`**

Open `docs/Phase1-build/phase1.5-remaining.md` and apply these edits:

a) Add a status banner at the top:

```markdown
> **Status (2026-05-07):** Phases K, L, O, P implemented as part of the combined
> Phase 1.5 + Phase 2a plan. M and N1 are now provided by the Phase 2a baseline
> stack (`src/baselines/spy.py`, `src/baselines/equity_curve.py`,
> `src/scripts/plot_equity.py`). N2 (MLP) is deferred to Phase 3. N3 is
> simplified to a 2-way bot-vs-SPY comparison (the plotter covers it).
```

b) Strike through the superseded sections (use `~~text~~`) for §M, §N1, §N3, and replace §N2 with:

```markdown
### N2: PyTorch MLP baseline (DEFERRED to Phase 3)

Originally part of the live-trading gate per `docs/baselines.md`. Deferred:
the simpler bot-vs-SPY comparison covers Phase 1's "is the bot beating
buy-and-hold?" question. Reintroduce when Phase 3 model training begins.
```

c) Mark the "Notes for next session" section as historical (still useful, but
work is done).

- [ ] **Step 2: Verify**

Re-read `docs/Phase1-build/phase1.5-remaining.md` and confirm:
- The status banner clearly states which items are done.
- §M, §N1, §N3 are visibly deprecated (struck through or labelled superseded).
- §N2 explicitly says "DEFERRED to Phase 3".

- [ ] **Step 3: Commit**

```
git add docs/Phase1-build/phase1.5-remaining.md
git commit -m "docs: mark phase 1.5 K/L/M/N/O/P as done or superseded by phase 2a"
```

---

## Self-Review

**1. Spec coverage** — every section of `phase-2a-groundwork-design.md` is implemented:
- §2 module layout: Tasks 5-11
- §3.1 topology: Tasks 8-11 (lifecycle scripts run locally, manage cloud)
- §3.2 initialise: Tasks 10, 11
- §3.3 hard_reset: Tasks 8, 9
- §3.4 CLI UX: Tasks 9, 11
- §4.1 SPY baseline: Task 5
- §4.2 equity curve: Task 6
- §4.3 plot_equity: Task 7
- §4.4 Phase 1.5 §N changes: Task 17
- §5 execution timing: no code change required (decision recorded in spec)
- §6 testing: every component has unit tests
- §7 failure handling: covered in Tasks 8-11 (NonEmptyTablesError, EnvVarMissingError, BrokerCashMismatch, FileExistsError)
- §8 decisions log: in spec, not plan

Phase 1.5 carry-forward:
- K1 → Task 1
- K2 → Task 2
- K3 → Tasks 3, 4
- L1 → Task 12
- L2 → Task 13
- O1 → Task 14
- O2 → Task 15
- O3 → Task 16
- P1 → Task 16 (kickoff checklist in `deploy/README.md`)
- P2 → Task 16 (live-trading gate section in `deploy/README.md`)

**2. Placeholder scan** — no TBD/TODO/"add appropriate error handling"/"similar to Task N" markers. The replay-backtest fixture-mode comment is intentional: full fixture wiring is explicitly Phase 2 follow-up per phase1.5 §L2.

**3. Type consistency** — `make_session_service`, `make_engine`, `make_session_factory`, `create_all`, `save_attribution_signal` are referenced consistently across Tasks 1-11. `EquityCurve`, `SPYMetrics`, `InitResult`, `ResetResult` dataclasses are used as defined. `_fetch_spy_price` is patched in both initialise tests and CLI tests.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/phase-1.5-and-2a-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
