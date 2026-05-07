# Phase 1 — Multi-Agent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 multi-agent trading pipeline specified in `docs/superpowers/specs/2026-05-06-multi-agent-system-design.md` — four ADK analyst agents, a strategist, deterministic risk gate, executor, memory writer, and snapshotter, deployed as an hourly Cloud Run Job against Trading 212's paper account.

**Architecture:** A single ADK `SequentialAgent` runs once per scheduled tick. Inside it, a `ParallelAgent` runs four LlmAgent analysts (Technical / Fundamental / Sentiment / Smart Money) whose data is fetched by `before_agent_callback`s. A Gemini Pro strategist fuses their signals into target portfolio weights, a deterministic Python risk gate clamps and translates to orders, a Trading 212 executor submits them, and post-stages persist memory + equity snapshots.

**Tech Stack:** Python 3.11, Google ADK (`google-adk`), Pydantic v2, asyncio, pytest + pytest-asyncio, SQLAlchemy + SQLite (dev) / Cloud SQL Postgres (prod), Vertex AI (Gemini Flash + Pro + text-embedding-005), Trading 212 REST API, GCP (Cloud Run Jobs, Cloud Scheduler, Cloud Build, Artifact Registry, Secret Manager, Cloud SQL).

**Reading order:** Phases run sequentially. Within a phase, tasks are ordered by dependency. Pre-existing code is acknowledged where relevant — do not rewrite the data layer.

---

## Pre-existing infrastructure (do NOT rebuild)

The following is already implemented in the repo and is the foundation this plan builds on:

- `data/__init__.py` — public surface re-exports `get_stock_stats`, `get_stock_news`, `get_social_sentiment`, `get_insider_trades`, `get_public_figure_trades`, `get_notable_holders`, `get_company_filings`, plus models and rate-limit primitives.
- `data/aggregator.py` — `get_stock_signal_bundle(ticker)` fans out all seven providers concurrently with graceful per-provider degradation.
- `data/providers/*.py` — seven async provider functions wrapping yfinance, finnhub, edgartools (filings + insiders + SC 13D/G holders), and Quiver. **Quiver status:** free tier currently unavailable; `get_public_figure_trades` soft-fails to `[]` when `QUIVER_QUANT_API_KEY` is unset. The new `get_notable_holders` (SC 13D/13G + amendments via EDGAR) fills the smart-money gap until Quiver is restored — at which point both signals run side-by-side with no code change.
- `data/models/*.py` — Pydantic models: `StockStats`, `OHLCBar`, `NewsArticle`, `SocialSentiment`, `Filing`, `InsiderTrade`, `PoliticianTrade`, `NotableHolder`, `StockSignalBundle`, `ProviderError`.
- `data/rate_limit.py` — async token-bucket limiters per source (FINNHUB, EDGAR, QUIVER, YFINANCE). `get_notable_holders` shares the EDGAR limiter.
- `data/retry.py` — `with_retry` wrapper.
- `data/settings.py` — pydantic-settings loader for `.env`.
- `requirements.txt` — pinned to `google-adk>=0.2.0`, `edgartools>=3.0`, etc.

**Convention enforced by the existing layer:** agents must import provider functions from `data` (not `data.providers.*`). The plan's analyst before_callbacks honour this.

---

## File structure (created by this plan)

```
StockBot/
├── agents/                                       # NEW
│   ├── __init__.py
│   ├── analysts/
│   │   ├── __init__.py
│   │   ├── _common.py                           # AnalystSignal base, validators
│   │   ├── technical/{__init__.py, schema.py, fetch.py, agent.py, prompts.py}
│   │   ├── fundamental/{__init__.py, schema.py, fetch.py, agent.py, prompts.py}
│   │   ├── sentiment/{__init__.py, schema.py, fetch.py, agent.py, prompts.py}
│   │   └── smart_money/{__init__.py, schema.py, fetch.py, agent.py, prompts.py}
│   ├── strategist/
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── prompts.py
│   │   └── agent.py
│   ├── risk_gate/
│   │   ├── __init__.py
│   │   ├── constraints.py                       # the 6-step clamp algorithm
│   │   ├── orders.py                             # weight-delta → Order translation
│   │   └── agent.py                              # BaseAgent wrapper
│   ├── executor/
│   │   ├── __init__.py
│   │   └── agent.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── schema.py                             # BufferEntry + projection
│   │   ├── embeddings.py
│   │   ├── compress.py                           # day_digest compressor
│   │   └── writer.py                             # BaseAgent
│   └── snapshot/
│       ├── __init__.py
│       └── agent.py
│
├── broker/                                       # NEW
│   ├── __init__.py
│   ├── protocol.py                               # Broker Protocol
│   ├── portfolio.py                              # Portfolio dataclass
│   ├── fake.py                                    # FakeBroker for tests
│   └── trading212.py                              # Trading212Broker (paper / live)
│
├── orchestrator/                                 # NEW
│   ├── __init__.py
│   ├── state.py                                  # TickState shared schema
│   ├── stock_picker.py                           # static JSON → list[str]
│   ├── pipeline.py                               # builds the SequentialAgent
│   ├── tick.py                                   # entrypoint, runs once
│   └── persistence.py                            # SQL tables + DAOs
│
├── baselines/                                    # NEW
│   ├── __init__.py
│   ├── spy.py
│   ├── mlp.py
│   └── evaluate.py
│
├── config/                                       # NEW
│   └── watchlist.json                            # {"tickers": [...]}
│
├── deploy/                                       # NEW
│   ├── Dockerfile
│   ├── cloudbuild.yaml
│   ├── scheduler.yaml
│   └── README.md                                  # one-time GCP setup runbook
│
├── scripts/                                      # extends existing
│   ├── plot_equity.py                            # NEW
│   └── replay_backtest.py                        # NEW
│
└── tests/                                        # NEW
    ├── __init__.py
    ├── conftest.py                               # shared fixtures
    ├── fixtures/                                 # snapshot data
    │   ├── stock_stats_aapl.json
    │   ├── ...
    ├── unit/
    │   ├── test_risk_gate_constraints.py
    │   ├── test_risk_gate_orders.py
    │   ├── test_memory_writer.py
    │   ├── test_memory_compress.py
    │   └── test_position_lifecycle.py
    ├── analysts/
    │   ├── test_technical.py
    │   ├── test_fundamental.py
    │   ├── test_sentiment.py
    │   └── test_smart_money.py
    ├── integration/
    │   ├── test_strategist_with_stub_signals.py
    │   ├── test_executor_with_fake_broker.py
    │   └── test_pipeline_e2e.py
    └── replay/
        └── test_replay_30days.py
```

---

## Phase A — Project skeleton & test infrastructure

**Why first:** every subsequent phase needs a working test harness, the right git baseline, and a clean module skeleton. We commit in tiny chunks so any rollback target is sane.

### Task A1: First commit of pre-existing code

**Files:** none new — establishes the git baseline so future commits show meaningful diffs.

- [ ] **Step 1: Inspect what's untracked**

```bash
git status
```

Expected: shows existing files (data/, scripts/, requirements.txt, docs/, .env, .gitignore) as untracked. `.env` should appear ignored.

- [ ] **Step 2: Stage everything except secrets**

```bash
git add data/ scripts/ docs/ requirements.txt .gitignore .claude/CLAUDE.md
git status
```

Expected: green files staged; `.env` not in the list (it's gitignored).

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: import pre-existing data layer + spec"
```

- [ ] **Step 4: Tag the baseline**

```bash
git tag pre-agents-baseline
git log --oneline -1
```

Expected: shows the new commit hash.

---

### Task A2: Skeleton directories + empty `__init__.py`

**Files:**
- Create: `agents/__init__.py`, `agents/analysts/__init__.py`, `agents/strategist/__init__.py`, `agents/risk_gate/__init__.py`, `agents/executor/__init__.py`, `agents/memory/__init__.py`, `agents/snapshot/__init__.py`
- Create: `broker/__init__.py`
- Create: `orchestrator/__init__.py`
- Create: `baselines/__init__.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/analysts/__init__.py`, `tests/integration/__init__.py`, `tests/replay/__init__.py`
- Create: `config/`, `deploy/`, `tests/fixtures/` (no `__init__.py`)

- [ ] **Step 1: Create the directories and empty `__init__.py` files**

```bash
mkdir -p agents/analysts/technical agents/analysts/fundamental agents/analysts/sentiment agents/analysts/smart_money agents/strategist agents/risk_gate agents/executor agents/memory agents/snapshot broker orchestrator baselines config deploy tests/unit tests/analysts tests/integration tests/replay tests/fixtures
for d in agents agents/analysts agents/analysts/technical agents/analysts/fundamental agents/analysts/sentiment agents/analysts/smart_money agents/strategist agents/risk_gate agents/executor agents/memory agents/snapshot broker orchestrator baselines tests tests/unit tests/analysts tests/integration tests/replay; do touch "$d/__init__.py"; done
```

- [ ] **Step 2: Verify the layout**

```bash
find agents broker orchestrator baselines tests -name __init__.py | sort
```

Expected: 17 `__init__.py` files listed.

- [ ] **Step 3: Commit**

```bash
git add agents/ broker/ orchestrator/ baselines/ config/ deploy/ tests/
git commit -m "chore: scaffold agents/broker/orchestrator/tests directories"
```

---

### Task A3: pytest configuration

**Files:**
- Create: `pytest.ini`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = -ra --strict-markers
markers =
    integration: requires real LLM or external API
    replay: long-running historical backtest
filterwarnings =
    ignore::DeprecationWarning:pydantic.*
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
"""Shared fixtures for the StockBot test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_path():
    """Return absolute path to a named JSON fixture under tests/fixtures/."""
    def _get(name: str) -> Path:
        p = FIXTURES / name
        if not p.exists():
            pytest.fail(f"missing fixture: {p}")
        return p
    return _get


@pytest.fixture
def load_fixture(fixture_path):
    """Load a JSON fixture as a Python object."""
    def _load(name: str):
        with fixture_path(name).open() as f:
            return json.load(f)
    return _load
```

- [ ] **Step 3: Verify pytest discovers the suite (with no tests yet, exit code 5 = no tests collected)**

```bash
.venv/Scripts/python -m pytest --collect-only
```

Expected: "no tests ran in 0.0xs" or similar. No errors.

- [ ] **Step 4: Commit**

```bash
git add pytest.ini tests/conftest.py
git commit -m "chore: add pytest config + shared fixture loader"
```

---

### Task A4: Lint + type-check baseline

**Files:**
- Create: `pyproject.toml`
- Modify: `requirements.txt` to add `mypy`, `ruff`

- [ ] **Step 1: Add dev tools to `requirements.txt`**

Append to existing `requirements.txt`:

```
# === Lint + type ===
ruff>=0.4.0
mypy>=1.10.0
```

Then install:

```bash
.venv/Scripts/pip install -r requirements.txt
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
ignore = ["E501"]  # line length handled by formatter

[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true
ignore_missing_imports = true
files = ["agents", "broker", "orchestrator", "baselines"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

- [ ] **Step 3: Verify ruff passes on existing code**

```bash
.venv/Scripts/python -m ruff check data scripts
```

Expected: "All checks passed!" — or fix any issues that surface on the existing data/.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "chore: add ruff + mypy config and dev deps"
```

---

## Phase B — Broker layer

**Why now:** the executor and snapshotter both depend on a `Broker` Protocol. The fake implementation lets us TDD risk-gate→executor without Trading 212 in the loop.

### Task B1: Portfolio model + Broker Protocol

**Files:**
- Create: `broker/portfolio.py`
- Create: `broker/protocol.py`
- Create: `tests/unit/test_portfolio.py`

- [ ] **Step 1: Write `tests/unit/test_portfolio.py`**

```python
import pytest
from broker.portfolio import Portfolio, Position


def test_total_value_includes_cash_and_positions():
    p = Portfolio(
        cash=1000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=150.0, last_price=200.0)},
    )
    assert p.total_value == 1000.0 + 10 * 200.0


def test_current_weights_sum_to_one_minus_cash_ratio():
    p = Portfolio(
        cash=200.0,
        positions={
            "AAPL": Position(quantity=10, avg_cost=150.0, last_price=200.0),  # $2000
            "MSFT": Position(quantity=5, avg_cost=300.0, last_price=400.0),    # $2000
        },
    )
    weights = p.current_weights()
    # total = 200 + 2000 + 2000 = 4200; AAPL = 2000/4200, MSFT = 2000/4200
    assert weights["AAPL"] == pytest.approx(2000 / 4200)
    assert weights["MSFT"] == pytest.approx(2000 / 4200)
    assert sum(weights.values()) == pytest.approx((4200 - 200) / 4200)


def test_empty_portfolio_returns_empty_weights():
    p = Portfolio(cash=1000.0, positions={})
    assert p.current_weights() == {}
    assert p.total_value == 1000.0
```

- [ ] **Step 2: Run the test, confirm failure**

```bash
.venv/Scripts/python -m pytest tests/unit/test_portfolio.py -v
```

Expected: FAIL with `ModuleNotFoundError: broker.portfolio`.

- [ ] **Step 3: Write `broker/portfolio.py`**

```python
"""Portfolio + Position dataclasses. No I/O — see broker.protocol."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Position(BaseModel):
    quantity: float
    avg_cost: float
    last_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price


class Portfolio(BaseModel):
    cash: float
    positions: dict[str, Position] = Field(default_factory=dict)

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def current_weights(self) -> dict[str, float]:
        total = self.total_value
        if total == 0:
            return {}
        return {t: p.market_value / total for t, p in self.positions.items()}
```

- [ ] **Step 4: Write `broker/protocol.py`**

```python
"""Broker Protocol — single abstraction for paper / live / fake."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from .portfolio import Portfolio


class Fill(BaseModel):
    id: str
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float
    price: float


class BrokerRejection(Exception):
    """Broker refused the order. Logged but doesn't crash the tick."""


class Broker(Protocol):
    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill: ...

    async def position_size(self, ticker: str) -> float: ...

    async def get_portfolio(self) -> Portfolio: ...
```

- [ ] **Step 5: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_portfolio.py -v
git add broker/portfolio.py broker/protocol.py tests/unit/test_portfolio.py
git commit -m "feat(broker): add Portfolio model and Broker Protocol"
```

Expected: 3 passed.

---

### Task B2: FakeBroker for tests

**Files:**
- Create: `broker/fake.py`
- Create: `tests/unit/test_fake_broker.py`

- [ ] **Step 1: Write `tests/unit/test_fake_broker.py`**

```python
import pytest

from broker.fake import FakeBroker
from broker.protocol import BrokerRejection


@pytest.mark.asyncio
async def test_buy_creates_position():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    fill = await b.submit_market("AAPL", "BUY", 10)
    assert fill.price == 200.0
    assert fill.quantity == 10
    p = await b.get_portfolio()
    assert p.cash == 10_000 - 2000
    assert p.positions["AAPL"].quantity == 10


@pytest.mark.asyncio
async def test_sell_reduces_position():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    await b.submit_market("AAPL", "BUY", 10)
    await b.submit_market("AAPL", "SELL", 4)
    p = await b.get_portfolio()
    assert p.positions["AAPL"].quantity == 6
    assert p.cash == pytest.approx(10_000 - 2000 + 800)


@pytest.mark.asyncio
async def test_buy_with_insufficient_cash_raises():
    b = FakeBroker(starting_cash=100.0, prices={"AAPL": 200.0})
    with pytest.raises(BrokerRejection):
        await b.submit_market("AAPL", "BUY", 10)


@pytest.mark.asyncio
async def test_sell_more_than_held_raises():
    b = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 200.0})
    with pytest.raises(BrokerRejection):
        await b.submit_market("AAPL", "SELL", 5)
```

- [ ] **Step 2: Run, confirm 4 failures (module missing)**

```bash
.venv/Scripts/python -m pytest tests/unit/test_fake_broker.py -v
```

- [ ] **Step 3: Write `broker/fake.py`**

```python
"""Deterministic in-memory broker for tests.

Holds prices, cash, and positions in dicts. Submit orders and they fill
at the configured price. No randomness — every test is reproducible.
"""
from __future__ import annotations

import itertools
from typing import Literal

from .portfolio import Portfolio, Position
from .protocol import BrokerRejection, Fill


class FakeBroker:
    def __init__(self, starting_cash: float, prices: dict[str, float]):
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._prices = dict(prices)
        self._order_seq = itertools.count(1)

    def set_price(self, ticker: str, price: float) -> None:
        self._prices[ticker] = price
        if ticker in self._positions:
            self._positions[ticker].last_price = price

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill:
        if ticker not in self._prices:
            raise BrokerRejection(f"no price for {ticker}")
        price = self._prices[ticker]
        notional = quantity * price

        if action == "BUY":
            if notional > self._cash:
                raise BrokerRejection(
                    f"insufficient cash: need {notional}, have {self._cash}"
                )
            self._cash -= notional
            existing = self._positions.get(ticker)
            if existing:
                new_qty = existing.quantity + quantity
                new_cost = (existing.avg_cost * existing.quantity + notional) / new_qty
                self._positions[ticker] = Position(
                    quantity=new_qty, avg_cost=new_cost, last_price=price
                )
            else:
                self._positions[ticker] = Position(
                    quantity=quantity, avg_cost=price, last_price=price
                )
        else:  # SELL
            existing = self._positions.get(ticker)
            if existing is None or existing.quantity < quantity:
                held = existing.quantity if existing else 0
                raise BrokerRejection(f"sell {quantity} > held {held} of {ticker}")
            self._cash += notional
            new_qty = existing.quantity - quantity
            if new_qty == 0:
                del self._positions[ticker]
            else:
                self._positions[ticker] = Position(
                    quantity=new_qty, avg_cost=existing.avg_cost, last_price=price
                )

        return Fill(
            id=f"fake-{next(self._order_seq)}",
            ticker=ticker,
            action=action,
            quantity=quantity,
            price=price,
        )

    async def position_size(self, ticker: str) -> float:
        return self._positions[ticker].quantity if ticker in self._positions else 0.0

    async def get_portfolio(self) -> Portfolio:
        return Portfolio(cash=self._cash, positions=dict(self._positions))
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_fake_broker.py -v
git add broker/fake.py tests/unit/test_fake_broker.py
git commit -m "feat(broker): add deterministic FakeBroker for tests"
```

Expected: 4 passed.

---

### Task B3: Trading212Broker (paper mode)

**Files:**
- Create: `broker/trading212.py`
- Create: `tests/unit/test_trading212_request_construction.py`

This task validates request **construction** only — we don't hit the live API in CI. Live verification happens during deployment (Phase P).

- [ ] **Step 1: Write request-construction test**

```python
"""Verify Trading212Broker builds requests correctly. No network calls."""
from unittest.mock import AsyncMock

import pytest

from broker.trading212 import Trading212Broker


@pytest.mark.asyncio
async def test_buy_constructs_correct_request():
    client = AsyncMock()
    client.post.return_value.json = AsyncMock(return_value={
        "id": "abc-123",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": 1.5,
        "filledPrice": 200.0,
    })
    client.post.return_value.raise_for_status = lambda: None

    b = Trading212Broker(mode="paper", api_key="K", http_client=client,
                         instrument_map={"AAPL": "AAPL_US_EQ"})
    fill = await b.submit_market("AAPL", "BUY", 1.5)

    client.post.assert_called_once()
    call = client.post.call_args
    assert call.kwargs["json"] == {
        "instrumentCode": "AAPL_US_EQ", "quantity": 1.5
    }
    assert "/api/v0/equity/orders/market" in call.args[0]
    assert call.kwargs["headers"]["Authorization"] == "K"
    assert fill.price == 200.0
    assert fill.quantity == 1.5


@pytest.mark.asyncio
async def test_sell_uses_negative_quantity():
    client = AsyncMock()
    client.post.return_value.json = AsyncMock(return_value={
        "id": "abc-2", "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": -1.0, "filledPrice": 199.0,
    })
    client.post.return_value.raise_for_status = lambda: None

    b = Trading212Broker(mode="paper", api_key="K", http_client=client,
                         instrument_map={"AAPL": "AAPL_US_EQ"})
    await b.submit_market("AAPL", "SELL", 1.0)

    body = client.post.call_args.kwargs["json"]
    assert body["quantity"] == -1.0  # Trading 212 uses sign for direction


@pytest.mark.asyncio
async def test_paper_uses_demo_base_url():
    b = Trading212Broker(mode="paper", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" in b.base_url


@pytest.mark.asyncio
async def test_live_uses_live_base_url():
    b = Trading212Broker(mode="live", api_key="K",
                         http_client=AsyncMock(), instrument_map={})
    assert "demo" not in b.base_url
    assert "trading212" in b.base_url
```

- [ ] **Step 2: Run, confirm failure**

```bash
.venv/Scripts/python -m pytest tests/unit/test_trading212_request_construction.py -v
```

- [ ] **Step 3: Write `broker/trading212.py`**

```python
"""Trading 212 REST client. Paper (demo) and live mode behind a flag.

The Trading 212 API uses a single integer-signed quantity for direction
(positive = buy, negative = sell). We translate from our Action enum.
Instrument codes are mapped from plain tickers via `instrument_map`
(loaded from config; first-tick startup populates it from
GET /equity/instruments).
"""
from __future__ import annotations

from typing import Literal

import httpx

from .portfolio import Portfolio, Position
from .protocol import BrokerRejection, Fill

PAPER_BASE = "https://demo.trading212.com"
LIVE_BASE = "https://live.trading212.com"


class Trading212Broker:
    def __init__(
        self,
        *,
        mode: Literal["paper", "live"],
        api_key: str,
        http_client: httpx.AsyncClient,
        instrument_map: dict[str, str],
    ):
        self.mode = mode
        self.base_url = PAPER_BASE if mode == "paper" else LIVE_BASE
        self._api_key = api_key
        self._client = http_client
        self._instruments = dict(instrument_map)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._api_key, "Content-Type": "application/json"}

    def _instrument(self, ticker: str) -> str:
        if ticker not in self._instruments:
            raise BrokerRejection(f"unknown instrument for {ticker}")
        return self._instruments[ticker]

    async def submit_market(
        self, ticker: str, action: Literal["BUY", "SELL"], quantity: float
    ) -> Fill:
        signed_qty = quantity if action == "BUY" else -quantity
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v0/equity/orders/market",
                json={"instrumentCode": self._instrument(ticker), "quantity": signed_qty},
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
        except httpx.HTTPStatusError as e:
            raise BrokerRejection(f"HTTP {e.response.status_code}: {e.response.text}") from e

        return Fill(
            id=str(data["id"]),
            ticker=ticker,
            action=action,
            quantity=abs(float(data["filledQuantity"])),
            price=float(data["filledPrice"]),
        )

    async def position_size(self, ticker: str) -> float:
        resp = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
        code = self._instrument(ticker)
        for pos in data:
            if pos["ticker"] == code:
                return float(pos["quantity"])
        return 0.0

    async def get_portfolio(self) -> Portfolio:
        # Account endpoint for cash, portfolio endpoint for positions.
        acct = await self._client.get(
            f"{self.base_url}/api/v0/equity/account/cash",
            headers=self._headers(),
        )
        acct.raise_for_status()
        cash = float((await acct.json() if callable(getattr(acct, "json", None)) else acct.json())["free"])

        port = await self._client.get(
            f"{self.base_url}/api/v0/equity/portfolio",
            headers=self._headers(),
        )
        port.raise_for_status()
        items = await port.json() if callable(getattr(port, "json", None)) else port.json()

        # Reverse-map instrument codes back to tickers
        rev = {v: k for k, v in self._instruments.items()}
        positions: dict[str, Position] = {}
        for it in items:
            code = it["ticker"]
            if code not in rev:
                continue
            positions[rev[code]] = Position(
                quantity=float(it["quantity"]),
                avg_cost=float(it["averagePrice"]),
                last_price=float(it["currentPrice"]),
            )
        return Portfolio(cash=cash, positions=positions)
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_trading212_request_construction.py -v
git add broker/trading212.py tests/unit/test_trading212_request_construction.py
git commit -m "feat(broker): add Trading212Broker with paper/live mode"
```

Expected: 4 passed.

---

### Task B4: Wire `broker/__init__.py` exports

**Files:**
- Modify: `broker/__init__.py`

- [ ] **Step 1: Replace empty `broker/__init__.py`**

```python
"""Broker layer — Portfolio, Protocol, Fake, Trading 212."""
from .fake import FakeBroker
from .portfolio import Portfolio, Position
from .protocol import Broker, BrokerRejection, Fill
from .trading212 import Trading212Broker

__all__ = [
    "Broker",
    "BrokerRejection",
    "FakeBroker",
    "Fill",
    "Portfolio",
    "Position",
    "Trading212Broker",
]
```

- [ ] **Step 2: Verify import works**

```bash
.venv/Scripts/python -c "from broker import Broker, FakeBroker, Trading212Broker, Portfolio; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add broker/__init__.py
git commit -m "chore(broker): re-export public surface"
```

---

## Phase C — Risk gate

**Why now:** the risk gate is pure Python with the most safety-critical logic. Building it now lets us TDD it ahead of any agent integration. It also independently validates the constraint algorithm in §4.3 of the spec.

### Task C1: Constants + `ClampRecord` + `Order` schema

**Files:**
- Create: `agents/risk_gate/__init__.py` (already exists empty — replace)
- Create: `orchestrator/state.py` — early stub for `Order` and `ClampRecord` (used by risk gate, executor, snapshotter)

- [ ] **Step 1: Write `orchestrator/state.py` (initial cut — adds more later)**

```python
"""Shared state schemas — TickState built incrementally across phases.

Each phase appends its fields. This keeps a single source of truth so
agents and tests don't drift on type names.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── constants ────────────────────────────────────────────────────────
MIN_HELD_WEIGHT: float = 0.001                # below this = "not held"
MAX_POSITION_WEIGHT: float = 0.20
CASH_FLOOR_WEIGHT: float = 0.10
MAX_DELTA_PER_TICKER: float = 0.01
MAX_TOTAL_TURNOVER: float = 0.30
ORDER_EPSILON: float = 1e-6                   # ignore deltas smaller than this


# ── orders + clamp telemetry (used by risk_gate, executor) ───────────
class Order(BaseModel):
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: float                           # fractional shares ok
    est_price: float


class ClampRecord(BaseModel):
    rule: Literal[
        "max_position", "max_delta", "cash_floor", "max_turnover", "no_short"
    ]
    ticker: str | None
    before: float
    after: float


class Execution(BaseModel):
    order: Order
    status: Literal["filled", "rejected", "partial"]
    actual_price: float | None = None
    actual_quantity: float | None = None
    slippage_bps: float | None = None
    broker_order_id: str | None = None
    error: str | None = None
```

- [ ] **Step 2: Verify import**

```bash
.venv/Scripts/python -c "from orchestrator.state import Order, ClampRecord, Execution, MIN_HELD_WEIGHT, MAX_POSITION_WEIGHT; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add orchestrator/state.py
git commit -m "feat(state): add Order/ClampRecord/Execution + risk constants"
```

---

### Task C2: No-shorts clamp

**Files:**
- Create: `agents/risk_gate/constraints.py`
- Create: `tests/unit/test_risk_gate_constraints.py`

We TDD each clamp in order. After this task, we have `_clamp_negatives` only.

- [ ] **Step 1: Write the failing test**

```python
"""Tier 1 unit tests for each risk-gate clamp, in algorithm order."""
import pytest

from agents.risk_gate.constraints import _clamp_negatives, ClampRecord


def test_clamp_negatives_zeros_negative_weights():
    weights = {"AAPL": -0.05, "MSFT": 0.10, "NVDA": -0.02}
    clamps: list[ClampRecord] = []
    _clamp_negatives(weights, clamps)
    assert weights == {"AAPL": 0.0, "MSFT": 0.10, "NVDA": 0.0}
    rules = [c.rule for c in clamps]
    assert rules == ["no_short", "no_short"]


def test_clamp_negatives_no_op_when_all_positive():
    weights = {"AAPL": 0.10, "MSFT": 0.05}
    clamps: list[ClampRecord] = []
    _clamp_negatives(weights, clamps)
    assert weights == {"AAPL": 0.10, "MSFT": 0.05}
    assert clamps == []
```

- [ ] **Step 2: Run, confirm failure**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
```

- [ ] **Step 3: Implement `_clamp_negatives`**

Write `agents/risk_gate/constraints.py`:

```python
"""The risk gate's six clamping steps, in fixed order.

Each `_clamp_*` mutates `weights` in place and appends to `clamps`. The
public `apply_constraints` driver chains them in the order specified by
the spec (§4.3 of the design doc).
"""
from __future__ import annotations

from orchestrator.state import (
    CASH_FLOOR_WEIGHT,
    MAX_DELTA_PER_TICKER,
    MAX_POSITION_WEIGHT,
    MAX_TOTAL_TURNOVER,
    ClampRecord,
)


def _clamp_negatives(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    for t, w in list(weights.items()):
        if w < 0:
            clamps.append(ClampRecord(rule="no_short", ticker=t, before=w, after=0.0))
            weights[t] = 0.0
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): no-shorts clamp"
```

Expected: 2 passed.

---

### Task C3: Max-position clamp

**Files:**
- Modify: `agents/risk_gate/constraints.py`
- Modify: `tests/unit/test_risk_gate_constraints.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/unit/test_risk_gate_constraints.py`:

```python
from agents.risk_gate.constraints import _clamp_max_position


def test_clamp_max_position_caps_oversized():
    weights = {"AAPL": 0.50, "MSFT": 0.10}
    clamps: list[ClampRecord] = []
    _clamp_max_position(weights, clamps)
    assert weights == {"AAPL": 0.20, "MSFT": 0.10}
    assert len(clamps) == 1
    assert clamps[0].rule == "max_position"
    assert clamps[0].ticker == "AAPL"
    assert clamps[0].before == 0.50
    assert clamps[0].after == 0.20


def test_clamp_max_position_no_op_when_within_cap():
    weights = {"AAPL": 0.20, "MSFT": 0.15}
    clamps: list[ClampRecord] = []
    _clamp_max_position(weights, clamps)
    assert weights == {"AAPL": 0.20, "MSFT": 0.15}
    assert clamps == []
```

- [ ] **Step 2: Run, confirm failure**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
```

- [ ] **Step 3: Implement `_clamp_max_position`**

Append to `agents/risk_gate/constraints.py`:

```python
def _clamp_max_position(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    for t, w in list(weights.items()):
        if w > MAX_POSITION_WEIGHT:
            clamps.append(
                ClampRecord(rule="max_position", ticker=t, before=w, after=MAX_POSITION_WEIGHT)
            )
            weights[t] = MAX_POSITION_WEIGHT
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): max-position clamp"
```

Expected: 4 passed.

---

### Task C4: Cash-floor clamp (proportional scaling)

**Files:**
- Modify: `agents/risk_gate/constraints.py`
- Modify: `tests/unit/test_risk_gate_constraints.py`

- [ ] **Step 1: Append failing tests**

```python
from agents.risk_gate.constraints import _clamp_cash_floor


def test_cash_floor_scales_when_sum_over_threshold():
    weights = {"AAPL": 0.50, "MSFT": 0.50}     # sum = 1.0, must shrink to 0.90
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert sum(weights.values()) == pytest.approx(0.90)
    assert weights["AAPL"] == pytest.approx(0.45)
    assert weights["MSFT"] == pytest.approx(0.45)
    assert len(clamps) == 2
    assert all(c.rule == "cash_floor" for c in clamps)


def test_cash_floor_noop_when_under_threshold():
    weights = {"AAPL": 0.40, "MSFT": 0.40}     # sum = 0.80, fine
    clamps: list[ClampRecord] = []
    _clamp_cash_floor(weights, clamps)
    assert weights == {"AAPL": 0.40, "MSFT": 0.40}
    assert clamps == []
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement `_clamp_cash_floor`**

Append to `agents/risk_gate/constraints.py`:

```python
def _clamp_cash_floor(weights: dict[str, float], clamps: list[ClampRecord]) -> None:
    total = sum(weights.values())
    threshold = 1.0 - CASH_FLOOR_WEIGHT
    if total <= threshold:
        return
    scale = threshold / total
    for t in list(weights.keys()):
        before = weights[t]
        after = before * scale
        if before != after:
            clamps.append(
                ClampRecord(rule="cash_floor", ticker=t, before=before, after=after)
            )
            weights[t] = after
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): cash-floor proportional scaling"
```

Expected: 6 passed.

---

### Task C5: Per-ticker delta clamp

**Files:**
- Modify: `agents/risk_gate/constraints.py`
- Modify: `tests/unit/test_risk_gate_constraints.py`

- [ ] **Step 1: Append failing tests**

```python
from agents.risk_gate.constraints import _clamp_max_delta


def test_max_delta_caps_per_ticker_buy():
    proposed = {"AAPL": 0.10}
    current = {"AAPL": 0.05}                   # delta = +0.05, must cap at +0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == pytest.approx(0.06)
    assert clamps[0].rule == "max_delta"


def test_max_delta_caps_per_ticker_sell():
    proposed = {"AAPL": 0.0}
    current = {"AAPL": 0.05}                   # delta = -0.05, must cap at -0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == pytest.approx(0.04)


def test_max_delta_no_op_within_threshold():
    proposed = {"AAPL": 0.06}
    current = {"AAPL": 0.05}                   # delta = +0.01 — exactly at threshold
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["AAPL"] == 0.06
    assert clamps == []


def test_max_delta_handles_new_position():
    proposed = {"NVDA": 0.05}
    current = {}                               # opening — full 0.05 must clamp to 0.01
    clamps: list[ClampRecord] = []
    _clamp_max_delta(proposed, current, clamps)
    assert proposed["NVDA"] == pytest.approx(0.01)
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement `_clamp_max_delta`**

Append to `agents/risk_gate/constraints.py`:

```python
def _clamp_max_delta(
    proposed: dict[str, float],
    current: dict[str, float],
    clamps: list[ClampRecord],
) -> None:
    for t, p in list(proposed.items()):
        c = current.get(t, 0.0)
        delta = p - c
        if abs(delta) > MAX_DELTA_PER_TICKER:
            capped = MAX_DELTA_PER_TICKER if delta > 0 else -MAX_DELTA_PER_TICKER
            new_w = c + capped
            clamps.append(
                ClampRecord(rule="max_delta", ticker=t, before=p, after=new_w)
            )
            proposed[t] = new_w
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): per-ticker delta cap"
```

Expected: 10 passed.

---

### Task C6: Total turnover clamp (proportional scaling on deltas)

**Files:**
- Modify: `agents/risk_gate/constraints.py`
- Modify: `tests/unit/test_risk_gate_constraints.py`

- [ ] **Step 1: Append failing tests**

```python
from agents.risk_gate.constraints import _clamp_max_turnover


def test_turnover_scales_when_sum_over_threshold():
    proposed = {"AAPL": 0.20, "MSFT": 0.20, "NVDA": 0.20}
    current  = {"AAPL": 0.0,  "MSFT": 0.0,  "NVDA": 0.0}
    # total |delta| = 0.60; must scale all to total = 0.30 (each ÷ 2)
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert sum(abs(proposed[t] - current.get(t, 0.0)) for t in proposed) == pytest.approx(0.30)
    assert proposed["AAPL"] == pytest.approx(0.10)


def test_turnover_noop_when_under_threshold():
    proposed = {"AAPL": 0.10, "MSFT": 0.10}
    current  = {"AAPL": 0.0,  "MSFT": 0.0}     # total |delta| = 0.20, fine
    clamps: list[ClampRecord] = []
    _clamp_max_turnover(proposed, current, clamps)
    assert proposed == {"AAPL": 0.10, "MSFT": 0.10}
    assert clamps == []
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement `_clamp_max_turnover`**

Append:

```python
def _clamp_max_turnover(
    proposed: dict[str, float],
    current: dict[str, float],
    clamps: list[ClampRecord],
) -> None:
    deltas = {t: proposed[t] - current.get(t, 0.0) for t in proposed}
    turnover = sum(abs(d) for d in deltas.values())
    if turnover <= MAX_TOTAL_TURNOVER:
        return
    scale = MAX_TOTAL_TURNOVER / turnover
    for t in list(proposed.keys()):
        before = proposed[t]
        new_delta = deltas[t] * scale
        after = current.get(t, 0.0) + new_delta
        if before != after:
            clamps.append(
                ClampRecord(rule="max_turnover", ticker=t, before=before, after=after)
            )
            proposed[t] = after
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): total turnover proportional scaling"
```

Expected: 12 passed.

---

### Task C7: Driver `apply_constraints` (chains all clamps)

**Files:**
- Modify: `agents/risk_gate/constraints.py`
- Modify: `tests/unit/test_risk_gate_constraints.py`

- [ ] **Step 1: Append integration-of-clamps test**

```python
from agents.risk_gate.constraints import apply_constraints


def test_apply_constraints_runs_in_documented_order():
    # Negative weight + oversized weight + sum > 0.90 + delta > 1% — all hit.
    proposed = {"AAPL": -0.05, "MSFT": 0.50, "NVDA": 0.45}
    current  = {"AAPL": 0.0,   "MSFT": 0.0,  "NVDA": 0.0}
    clamps = apply_constraints(proposed, current)
    # AAPL clamped to 0 (no_short)
    assert proposed["AAPL"] == 0.0
    # max_position then cash_floor leave per-ticker <=0.20 and sum <=0.90 …
    # … then max_delta clamps each remaining buy to +0.01 from 0
    assert proposed["MSFT"] == pytest.approx(0.01)
    assert proposed["NVDA"] == pytest.approx(0.01)
    rules = [c.rule for c in clamps]
    assert "no_short" in rules
    assert "max_position" in rules
    assert "max_delta" in rules
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Implement `apply_constraints`**

Append:

```python
def apply_constraints(
    proposed: dict[str, float],
    current: dict[str, float],
) -> list[ClampRecord]:
    """Mutate `proposed` to satisfy all hard rules. Returns clamp telemetry.

    Order — significant. See spec §4.3:
      1. no_short    → clamp negatives to 0
      2. max_position → cap each weight at MAX_POSITION_WEIGHT
      3. cash_floor  → scale all so sum ≤ 1 - CASH_FLOOR_WEIGHT
      4. max_delta   → clamp |target - current| per ticker to MAX_DELTA_PER_TICKER
      5. max_turnover → scale deltas so sum |delta| ≤ MAX_TOTAL_TURNOVER
    """
    clamps: list[ClampRecord] = []
    _clamp_negatives(proposed, clamps)
    _clamp_max_position(proposed, clamps)
    _clamp_cash_floor(proposed, clamps)
    _clamp_max_delta(proposed, current, clamps)
    _clamp_max_turnover(proposed, current, clamps)
    return clamps
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_constraints.py -v
git add agents/risk_gate/constraints.py tests/unit/test_risk_gate_constraints.py
git commit -m "feat(risk_gate): apply_constraints driver chains all clamps"
```

Expected: 13 passed.

---

### Task C8: Weights → Orders translation

**Files:**
- Create: `agents/risk_gate/orders.py`
- Create: `tests/unit/test_risk_gate_orders.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agents.risk_gate.orders import weights_to_orders
from broker import Portfolio, Position


def test_buy_order_when_target_above_current():
    portfolio = Portfolio(cash=10_000.0, positions={})
    target = {"AAPL": 0.10}    # 10% of $10k = $1000
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert len(orders) == 1
    assert orders[0].ticker == "AAPL"
    assert orders[0].action == "BUY"
    assert orders[0].quantity == pytest.approx(5.0)   # $1000 / $200
    assert orders[0].est_price == 200.0


def test_sell_order_when_target_below_current():
    portfolio = Portfolio(
        cash=8_000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=200.0, last_price=200.0)},
    )                                                  # total = 10k, AAPL @ 20%
    target = {"AAPL": 0.10}                            # halve it
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert len(orders) == 1
    assert orders[0].action == "SELL"
    assert orders[0].quantity == pytest.approx(5.0)


def test_no_order_when_delta_below_epsilon():
    portfolio = Portfolio(
        cash=8_000.0,
        positions={"AAPL": Position(quantity=10, avg_cost=200.0, last_price=200.0)},
    )
    target = {"AAPL": 0.20}                            # already at target
    prices = {"AAPL": 200.0}
    orders = weights_to_orders(target, portfolio, prices)
    assert orders == []


def test_orders_for_multiple_tickers():
    portfolio = Portfolio(cash=10_000.0, positions={})
    target = {"AAPL": 0.10, "MSFT": 0.05}
    prices = {"AAPL": 200.0, "MSFT": 100.0}
    orders = weights_to_orders(target, portfolio, prices)
    tickers = {o.ticker for o in orders}
    assert tickers == {"AAPL", "MSFT"}
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Write `agents/risk_gate/orders.py`**

```python
"""Translate post-clamp target weights into broker Orders."""
from __future__ import annotations

from broker import Portfolio
from orchestrator.state import ORDER_EPSILON, Order


def weights_to_orders(
    target: dict[str, float],
    portfolio: Portfolio,
    prices: dict[str, float],
) -> list[Order]:
    """Diff target vs current weights and emit BUY/SELL orders.

    `prices` is the most recent close per ticker — used both for the
    notional calculation and as `est_price` for slippage telemetry.
    """
    total = portfolio.total_value
    current = portfolio.current_weights()
    orders: list[Order] = []
    for ticker, new_w in target.items():
        old_w = current.get(ticker, 0.0)
        delta_w = new_w - old_w
        if abs(delta_w) < ORDER_EPSILON:
            continue
        if ticker not in prices:
            raise ValueError(f"no price for {ticker}")
        notional = abs(delta_w) * total
        qty = notional / prices[ticker]
        action = "BUY" if delta_w > 0 else "SELL"
        orders.append(
            Order(ticker=ticker, action=action, quantity=qty, est_price=prices[ticker])
        )
    return orders
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_risk_gate_orders.py -v
git add agents/risk_gate/orders.py tests/unit/test_risk_gate_orders.py
git commit -m "feat(risk_gate): weights → Orders translation"
```

Expected: 4 passed.

---

### Task C9: Position-lifecycle contract validators

**Files:**
- Create: `agents/risk_gate/lifecycle.py`
- Create: `tests/unit/test_position_lifecycle.py`

The strategist must include `new_positions` for every weight 0→>MIN, and `close_reasons` for every >MIN→0. This validator runs **after** clamping (so we check the *post-clamp* weights, not the strategist's raw output).

- [ ] **Step 1: Write the failing test**

```python
import pytest

from agents.risk_gate.lifecycle import (
    StrategistContractViolation,
    validate_lifecycle_contract,
)
from orchestrator.state import MIN_HELD_WEIGHT


def test_opening_without_thesis_raises():
    with pytest.raises(StrategistContractViolation, match="Opening NVDA"):
        validate_lifecycle_contract(
            new_weights={"NVDA": 0.05},
            current_weights={"NVDA": 0.0},
            new_positions={},
            close_reasons={},
        )


def test_closing_without_reason_raises():
    with pytest.raises(StrategistContractViolation, match="Closing AAPL"):
        validate_lifecycle_contract(
            new_weights={"AAPL": 0.0},
            current_weights={"AAPL": 0.05},
            new_positions={},
            close_reasons={},
        )


def test_holding_below_min_treated_as_closed():
    # current 0.0005 < MIN; new 0.0008 also < MIN — no transition, no contract
    validate_lifecycle_contract(
        new_weights={"AAPL": 0.0008},
        current_weights={"AAPL": 0.0005},
        new_positions={},
        close_reasons={},
    )


def test_open_with_thesis_and_close_with_reason_passes():
    from agents.risk_gate.lifecycle import _stub_position_thesis  # test-only helper
    validate_lifecycle_contract(
        new_weights={"NVDA": 0.05, "AAPL": 0.0},
        current_weights={"NVDA": 0.0, "AAPL": 0.05},
        new_positions={"NVDA": _stub_position_thesis("NVDA")},
        close_reasons={"AAPL": "thesis invalidated"},
    )
```

- [ ] **Step 2: Run, confirm failure**

- [ ] **Step 3: Write `agents/risk_gate/lifecycle.py`**

```python
"""Strategist contract checks for position open/close transitions.

Runs AFTER clamping so we check post-clamp weights — the strategist
may have proposed an open that the gate clamped to zero, in which case
no thesis is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from orchestrator.state import MIN_HELD_WEIGHT


class StrategistContractViolation(RuntimeError):
    """Strategist failed to honour position-lifecycle invariants."""


def validate_lifecycle_contract(
    *,
    new_weights: dict[str, float],
    current_weights: dict[str, float],
    new_positions: dict[str, Any],
    close_reasons: dict[str, str],
) -> None:
    for t, new_w in new_weights.items():
        was_open = current_weights.get(t, 0.0) >= MIN_HELD_WEIGHT
        will_be_open = new_w >= MIN_HELD_WEIGHT
        if not was_open and will_be_open and t not in new_positions:
            raise StrategistContractViolation(
                f"Opening {t} (current 0 → {new_w}) without PositionThesis"
            )
        if was_open and not will_be_open and t not in close_reasons:
            raise StrategistContractViolation(
                f"Closing {t} ({current_weights.get(t)} → {new_w}) without close_reason"
            )


def _stub_position_thesis(ticker: str):
    """Test helper. Real PositionThesis comes in Phase F."""
    from pydantic import BaseModel

    class _PositionThesisStub(BaseModel):
        ticker: str
        opened_at: datetime
        opened_price: float = 0.0
        opened_tag: str = "test"
        rationale: str = ""
        horizon: str = "swing"
        last_reviewed_at: datetime
        last_review_note: str = ""

    return _PositionThesisStub(
        ticker=ticker,
        opened_at=datetime.now(tz=timezone.utc),
        last_reviewed_at=datetime.now(tz=timezone.utc),
    )
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/Scripts/python -m pytest tests/unit/test_position_lifecycle.py -v
git add agents/risk_gate/lifecycle.py tests/unit/test_position_lifecycle.py
git commit -m "feat(risk_gate): position lifecycle contract validators"
```

Expected: 4 passed.

---

The plan continues with these phases. Each task in subsequent phases follows the same template — failing test → minimal implementation → green → commit.

---

## Phase D — Memory layer (8 tasks: D1–D8)

**Why now:** memory writer's interface is independent of analysts and strategist; building it ahead lets us test the bounded-buffer + dedup math in isolation.

### Task D1: `BufferEntry`, `MemoryProjection` schemas

**Files:** `agents/memory/schema.py`, `tests/unit/test_memory_schema.py`

Tests: BufferEntry validation (max_length on reasoning_summary), MemoryProjection's `tag_frequency` builder.

Implementation: pydantic `BufferEntry` (timestamp, decision_tag, reasoning_summary ≤120, smart_money_seen, is_repeat, executions_count, embedding optional), `MemoryProjection.from_buffer(buffer, n_recent=8, min_freq=3)`.

Commit: `feat(memory): BufferEntry + MemoryProjection schemas`.

### Task D2: Embedding helper (mockable)

**Files:** `agents/memory/embeddings.py`, `tests/unit/test_embeddings.py`

Tests: cosine similarity is symmetric and ∈ [-1, 1]; `cosine_similarity([1,0,0], [1,0,0]) == 1.0`; `cosine_similarity([1,0,0], [0,1,0]) == 0.0`.

Implementation: `cosine_similarity(a, b)` with numpy; `async def embed(text: str) -> list[float]` calling Vertex AI `text-embedding-005` via `google-genai` (already a transitive dep of google-adk). Wrap with `tenacity` retry. Provide `set_embedding_provider(fn)` test-injection point so unit tests can stub.

Commit: `feat(memory): cosine similarity + embed() helper`.

### Task D3: Tag-collision check

**Files:** `agents/memory/dedup.py`, `tests/unit/test_dedup.py`

Tests: `await detect_repeat(new_entry, recent_buffer, embed_fn)` sets `is_repeat=True` only when (a) tag matches at least one of last 4 entries AND (b) cosine ≥ 0.85; uses stub `embed_fn` to avoid network.

Implementation: ~25 lines.

Commit: `feat(memory): tag-collision dedup`.

### Task D4: Day-digest compressor

**Files:** `agents/memory/compress.py`, `tests/unit/test_memory_compress.py`

Tests: `compress(prev_digest, evicted_entry, llm_fn)` returns digest ≤2000 chars; uses stub `llm_fn` callable that returns canned text. When current digest + evicted summary < 2000 chars, just append; only call LLM when truncation needed.

Implementation: deterministic concat path + LLM compression path; the LLM is called via Gemini Flash (`from google import genai` client) when length exceeds budget; supply a `set_compress_llm(fn)` test injection.

Commit: `feat(memory): day_digest compressor with LLM fallback`.

### Task D5: Buffer eviction logic

**Files:** Add to `agents/memory/writer.py` (start file), `tests/unit/test_memory_eviction.py`

Tests:
- `append(buffer, new_entry)` returns same list when len < 24.
- `append(buffer, new_entry)` evicts oldest when len reaches 25.
- Evicted entry passes through `compress()` and the digest is updated.

Implementation: `async def append_with_eviction(buffer, new_entry, day_digest, compress_fn) -> tuple[list[BufferEntry], str]`.

Commit: `feat(memory): rolling buffer eviction`.

### Task D6: MemoryWriter as ADK BaseAgent

**Files:** Complete `agents/memory/writer.py`, `tests/unit/test_memory_writer_agent.py`

Tests: subclass of `google.adk.agents.BaseAgent`; running it on a session whose `state.strategist_decision` is set:
- Appends a new BufferEntry.
- Updates `state.thesis` to `strategist_decision.updated_thesis`.
- Calls dedup.

Implementation:
```python
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext

class MemoryWriter(BaseAgent):
    name: str = "MemoryWriter"

    async def _run_async_impl(self, ctx: InvocationContext):
        # build BufferEntry, dedup, append+evict, write back to state
        ...
        yield  # ADK BaseAgents yield events; here we yield none — pure state mutation
```

Commit: `feat(memory): MemoryWriter ADK agent wraps eviction + dedup`.

### Task D7: Persistence wiring (BufferEntry → SQL row)

**Files:** Add to `orchestrator/persistence.py` (start file), `tests/unit/test_buffer_persistence.py`

Tests: round-trip a BufferEntry through SQLAlchemy (SQLite in-memory) and assert equality.

Implementation: SQLAlchemy ORM table `buffer_entries` matching the BufferEntry schema; DAO `save_buffer_entry(session, entry, tick_id)` and `load_recent_buffer(session, tick_id, limit=24)`.

Commit: `feat(persistence): buffer_entries SQL table + DAO`.

### Task D8: Memory writer integration test (with persistence)

**Files:** `tests/integration/test_memory_writer_integration.py`

Tests: use SQLite in-memory, run `MemoryWriter._run_async_impl` against an `InvocationContext` whose `state` is a real ADK session, verify SQL row appears.

Commit: `test(memory): integration test against in-memory SQLite`.

---

## Phase E — Analyst signals + Smart Money gate

### Task E1: AnalystSignal base + per-analyst signal schemas

**Files:** `agents/analysts/_common.py`, `agents/analysts/technical/schema.py`, `agents/analysts/fundamental/schema.py`, `agents/analysts/sentiment/schema.py`, `agents/analysts/smart_money/schema.py`, `tests/unit/test_analyst_schemas.py`

Tests: each Pydantic model rejects out-of-range confidence, enforces `key_factors` ≤3 items, etc. SmartMoneySignal forbids `direction="neutral"`.

Implementation per spec §6 — these are pure pydantic classes; ~10 lines each.

Commit: `feat(analysts): per-analyst signal schemas`.

### Task E2: Exhaustive-output validator

**Files:** add to `agents/analysts/_common.py`, `tests/unit/test_exhaustive_validator.py`

Tests:
- Validator returns None when every watchlist ticker has a signal.
- Returns the missing-tickers list when some are missing.
- Used as `after_agent_callback`: when it sees missing tickers, returns content that re-prompts the LLM with `you missed: [...]`.

Implementation: `def make_exhaustive_validator(state_key, watchlist_key)` returning a callback that checks `state[state_key]` covers `state[watchlist_key]`. ADK callback signature: `(callback_context: CallbackContext) -> Optional[types.Content]`.

Commit: `feat(analysts): exhaustive-output validator + retry callback`.

### Task E3: Smart Money gate logic

**Files:** `agents/analysts/smart_money/fetch.py`, `tests/unit/test_smart_money_gate.py`

Tests:
- Empty insider data + empty politician data + empty notable-holder data → returns `state.smart_money_signals = []`, returns `Content` to skip LLM.
- Insider Form 4 ≥$100k present → does NOT return content; `state.smart_money_data` populated.
- Politician trade in last 30d present → does NOT return content.
- SC 13D / 13G filing (or amendment) in last 90d present → does NOT return content. 13D (active intent) and 13D/A amendments are weighted higher than 13G (passive index-style) by the gate.

Implementation: `async def smart_money_fetch_callback(callback_context)` calls `get_insider_trades(ticker, lookback_days=14)` + `get_public_figure_trades(ticker, lookback_days=30)` + `get_notable_holders(ticker, lookback_days=90)` for each watchlist ticker, builds `SmartMoneyBundle`, runs gate logic, writes `state.smart_money_data` + (if gated) `state.smart_money_signals`. Returns `types.Content(parts=[Part(text="no smart money signal — skipping")], role="model")` when gate fires.

> **Quiver substitution note:** while Quiver's free tier is unavailable, `get_public_figure_trades` returns `[]` and the gate runs on `insiders + notable_holders` only. Restoring Quiver = drop `QUIVER_QUANT_API_KEY` into `.env`; politician trades resume populating without code change. The `SmartMoneyBundle` schema must already carry `notable_holders` so the LLM prompt and downstream signals don't need to change when Quiver returns.

Commit: `feat(analysts/smart_money): has-signal gate in before_callback`.

### Task E4: Per-analyst fetch callbacks (Technical, Fundamental, Sentiment)

**Files:** `agents/analysts/technical/fetch.py`, `agents/analysts/fundamental/fetch.py`, `agents/analysts/sentiment/fetch.py`, `tests/unit/test_analyst_fetchers.py`

Tests for each: mock `data.get_stock_stats` (etc.) and assert the callback writes the right key into state, with the right shape.

Implementation: each callback iterates `state.tickers`, calls the appropriate provider function from `data.*`, writes `state.<analyst>_data` (a dict keyed by ticker).

Commit: `feat(analysts): per-analyst data fetch callbacks`.

---

## Phase F — Analyst LlmAgents

### Task F1: Technical analyst (canonical pattern)

**Files:** `agents/analysts/technical/prompts.py`, `agents/analysts/technical/agent.py`, `agents/analysts/technical/__init__.py`, `tests/analysts/test_technical.py` (Tier 2 — real LLM, marked `@pytest.mark.integration`)

Tests:
- Tier 1 unit: agent constructs without error and has expected name + output_key.
- Tier 2 integration (`@pytest.mark.integration`): given a known-good fixture for AAPL OHLCV, the analyst emits a `TechnicalSignal` with valid direction + confidence.

Implementation:
```python
from google.adk.agents import LlmAgent
from .schema import TechnicalSignal
from .fetch import technical_fetch_callback
from agents.analysts._common import make_exhaustive_validator
from .prompts import TECHNICAL_INSTRUCTION

technical_analyst = LlmAgent(
    name="TechnicalAnalyst",
    model="gemini-2.0-flash-001",
    instruction=TECHNICAL_INSTRUCTION,
    output_schema=list[TechnicalSignal],
    output_key="technical_signals",
    before_agent_callback=technical_fetch_callback,
    after_agent_callback=make_exhaustive_validator("technical_signals", "tickers"),
)
```

Prompt template (from spec §4.1, encoding the dense-vs-sparse rule and `key_factors` requirements).

Commit: `feat(analysts): TechnicalAnalyst LlmAgent`.

### Task F2: Fundamental analyst

Same pattern as F1; differences:
- `before_callback` calls `get_company_filings`.
- Prompt focuses on revenue trend, margin, debt load, segment performance from 10-K/10-Q/8-K excerpts.
- `output_schema=list[FundamentalSignal]`.

Commit: `feat(analysts): FundamentalAnalyst LlmAgent`.

### Task F3: Sentiment analyst

Same pattern; differences:
- `before_callback` calls `get_stock_news` + `get_social_sentiment`.
- Prompt: weighs headline severity, recency, and the social_score_delta.
- `output_schema=list[SentimentSignal]` (note extra fields: `top_headlines`, `social_score_delta`).

Commit: `feat(analysts): SentimentAnalyst LlmAgent`.

### Task F4: Smart Money analyst

Same pattern; differences:
- `before_callback` is the gating callback from E3.
- Prompt: emphasises "be a bias channel — only opine on tickers with actual activity"; explicitly NOT exhaustive.
- No exhaustive-output validator (sparse signal is the design).
- `output_schema=list[SmartMoneySignal]`.

Commit: `feat(analysts): SmartMoneyAnalyst LlmAgent`.

### Task F5: AnalystPool ParallelAgent

**Files:** `agents/analysts/__init__.py` (update), `tests/integration/test_analyst_pool.py`

Tests: ParallelAgent runs all 4 analysts concurrently; with stubbed `before_callbacks` that write deterministic data, the pool's output is reproducible.

Implementation:
```python
from google.adk.agents import ParallelAgent
from .technical import technical_analyst
from .fundamental import fundamental_analyst
from .sentiment import sentiment_analyst
from .smart_money import smart_money_analyst

analyst_pool = ParallelAgent(
    name="AnalystPool",
    sub_agents=[technical_analyst, fundamental_analyst, sentiment_analyst, smart_money_analyst],
)
```

Commit: `feat(analysts): AnalystPool ParallelAgent`.

---

## Phase G — Strategist

### Task G1: PositionThesis + StrategistDecision schemas

**Files:** `agents/strategist/schema.py`, `tests/unit/test_strategist_schema.py`

Tests: validation rules — `target_weights` must be ≤0.20 per (defensive), `reasoning` ≤300 chars, `updated_thesis` ≤500, `new_positions[ticker].horizon` is one of three literals.

Commit: `feat(strategist): PositionThesis + StrategistDecision schemas`.

### Task G2: Strategist prompt template

**Files:** `agents/strategist/prompts.py`, `tests/unit/test_strategist_prompt_template.py`

Tests: rendered prompt contains all required sections — current portfolio, all 4 signal lists, memory projection, active positions, smart-money bias instruction.

Implementation: a multi-line string with `{state_field}` interpolation for all the things the strategist reads. Reads spec §4.2 for the bias instruction wording.

Commit: `feat(strategist): prompt template with smart-money bias`.

### Task G3: Strategist LlmAgent

**Files:** `agents/strategist/agent.py`, `agents/strategist/__init__.py`, `tests/integration/test_strategist_with_stub_signals.py`

Tests: stub all 4 signal lists in state, run the agent, verify it emits a valid `StrategistDecision` (Tier 2, real LLM).

Implementation:
```python
strategist_agent = LlmAgent(
    name="Strategist",
    model="gemini-2.0-pro-001",
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
)
```

Commit: `feat(strategist): Strategist LlmAgent`.

### Task G4: Structural validation + retry callback

**Files:** add to `agents/strategist/agent.py`, `tests/unit/test_strategist_validators.py`

Tests:
- Missing-tickers detection: if `target_weights` doesn't cover every watchlist ticker, validator returns retry content.
- Off-watchlist tickers: if `target_weights` has an extra ticker, validator rejects.
- Lifecycle contract violation invokes retry.

Implementation: `after_agent_callback` that runs the structural + lifecycle checks and emits a retry-Content with the error message embedded if violation found. ADK pattern: callback returns `Content` to override the agent's normal output (forces re-execution via the agent's response parsing).

Commit: `feat(strategist): structural + lifecycle validation callbacks`.

---

## Phase H — Executor + Trade log

### Task H1: TradeLog SQL table + DAO

**Files:** add to `orchestrator/persistence.py`, `tests/unit/test_trade_log.py`

Tests: round-trip a `TradeLogEntry` through SQLite in-memory.

Commit: `feat(persistence): trade_log table + DAO`.

### Task H2: Executor BaseAgent

**Files:** `agents/executor/agent.py`, `tests/integration/test_executor_with_fake_broker.py`

Tests:
- Idempotency: setting `state.last_executed_tick_id` prevents re-execution.
- Successful BUY appends to `state.executions` with status="filled".
- Rejection appends with status="rejected", continues.
- Closing a position pops from `state.positions` and appends to `trade_log`.

Implementation: BaseAgent that reads `state.final_orders`, calls `broker.submit_market`, manages `state.positions` and `state.executions`, writes trade_log on close.

Commit: `feat(executor): Executor BaseAgent with idempotency + trade log`.

---

## Phase I — Snapshotter + RiskGate BaseAgent

### Task I1: PortfolioSnapshot SQL table + DAO

**Files:** add to `orchestrator/persistence.py`, `tests/unit/test_snapshot_persistence.py`

Tests: round-trip a `PortfolioSnapshot` row.

Commit: `feat(persistence): portfolio_snapshots table`.

### Task I2: Snapshotter BaseAgent

**Files:** `agents/snapshot/agent.py`, `tests/integration/test_snapshotter.py`

Tests:
- Reads portfolio + fetches SPY (mocked yfinance) → writes `PortfolioSnapshot` row.
- Computes `excess_return_pct = bot_return_pct - spy_return_pct`.
- `starting_capital` parameter sourced from session state on first ever tick, then frozen.

Implementation: BaseAgent. SPY fetch via `yfinance.Ticker("SPY").history(period="1d")["Close"][-1]`.

Commit: `feat(snapshot): Snapshotter writes equity-curve rows`.

### Task I3: RiskGate BaseAgent

**Files:** `agents/risk_gate/agent.py`, `tests/integration/test_risk_gate_agent.py`

Tests:
- Reads `state.strategist_decision`, applies `apply_constraints` + `validate_lifecycle_contract` + `weights_to_orders`, writes `state.final_orders` and `state.risk_clamps_applied`.
- Contract violation raises `StrategistContractViolation` (caught and converted to retry signal upstream).

Implementation: BaseAgent assembling C1-C9 building blocks.

Commit: `feat(risk_gate): RiskGate BaseAgent wraps constraints + orders`.

---

## Phase J — Orchestration

### Task J1: TickState complete schema (extend `orchestrator/state.py`)

**Files:** modify `orchestrator/state.py`, `tests/unit/test_tick_state.py`

Add the rest of the TickState fields (signal lists, positions, memory_buffer, day_digest, thesis, etc.). Tests validate Pydantic constraints and serialization round-trips.

Commit: `feat(state): complete TickState schema`.

### Task J2: stock_picker (static JSON)

**Files:** `orchestrator/stock_picker.py`, `config/watchlist.json`, `tests/unit/test_stock_picker.py`

Tests: `get_watchlist()` returns the JSON list; raises a clear error if file missing.

Implementation: ~15 lines.

`config/watchlist.json` initial content:

```json
{"tickers": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "CRM"]}
```

Commit: `feat(orchestrator): static watchlist via stock_picker.get_watchlist()`.

### Task J3: Pipeline composition

**Files:** `orchestrator/pipeline.py`, `tests/integration/test_pipeline_composition.py`

Tests: `build_pipeline(broker)` returns a `SequentialAgent` with all 6 stages in the right order; agent names match the spec.

Implementation:
```python
from google.adk.agents import SequentialAgent
from agents.analysts import analyst_pool
from agents.strategist import strategist_agent
from agents.risk_gate.agent import risk_gate_agent
from agents.executor.agent import build_executor
from agents.memory.writer import memory_writer
from agents.snapshot.agent import build_snapshotter

def build_pipeline(broker, db_session) -> SequentialAgent:
    return SequentialAgent(
        name="HourlyTick",
        sub_agents=[
            analyst_pool,
            strategist_agent,
            risk_gate_agent,
            build_executor(broker, db_session),
            memory_writer,
            build_snapshotter(broker, db_session),
        ],
    )
```

Commit: `feat(orchestrator): pipeline composes the SequentialAgent`.

### Task J4: tick.py entrypoint (one-shot, idempotent)

**Files:** `orchestrator/tick.py`, `tests/integration/test_tick_entrypoint.py`

Tests:
- Running `tick.run_once(broker, session)` end-to-end produces an `Execution` list and a `PortfolioSnapshot`.
- Running it again with the same `tick_id` is a no-op (idempotency).

Implementation: builds a session via `DatabaseSessionService` (SQLite in dev, Postgres in prod via env var), runs the pipeline once with `runner.run_async`, exits.

Commit: `feat(orchestrator): tick.py entrypoint runs once and exits`.

### Task J5: Pipeline e2e with mocked LLMs (Tier 3)

**Files:** `tests/integration/test_pipeline_e2e.py`

Tests: full pipeline against `FakeBroker` with all LLMs stubbed to return canned signals/decisions. Verifies state propagation through all 6 stages.

Use ADK's `LlmRequest` / response stubbing pattern. Patch each `LlmAgent`'s underlying model with a fake that returns pre-canned JSON matching the output_schema.

Commit: `test: pipeline e2e with mocked LLMs (Tier 3)`.

---

## Phase K — Persistence (extends J)

### Task K1: SessionService configuration

**Files:** add to `orchestrator/persistence.py`, `tests/unit/test_session_factory.py`

Tests:
- Dev mode (`STOCKBOT_ENV=dev`) returns SQLite-backed `DatabaseSessionService`.
- Prod mode (`STOCKBOT_ENV=prod`) constructs the Postgres URL from `CLOUD_SQL_*` env vars.

Implementation:
```python
def make_session_service():
    env = os.environ.get("STOCKBOT_ENV", "dev")
    if env == "dev":
        return DatabaseSessionService(db_url="sqlite:///./stockbot.db")
    return DatabaseSessionService(db_url=os.environ["DATABASE_URL"])
```

Commit: `feat(persistence): session-service factory by env`.

### Task K2: Schema migration script

**Files:** `orchestrator/persistence.py` `create_all()` function, `scripts/init_db.py`

A one-shot `python -m scripts.init_db` that runs Alembic-style schema creation (use SQLAlchemy `metadata.create_all()` for simplicity in Phase 1; introduce alembic later if migrations get complex).

Commit: `feat(persistence): init_db.py creates all tables`.

### Task K3: AttributionSignals SQL table

**Files:** add to `orchestrator/persistence.py`, `tests/unit/test_attribution_persistence.py`

Tests: serialize each signal type (TechnicalSignal, FundamentalSignal, SentimentSignal, SmartMoneySignal) into the same `attribution_signals` table (discriminator column `analyst`).

Add a hook in the pipeline (`after_pipeline_callback`?) or in MemoryWriter to persist signals each tick.

Commit: `feat(persistence): attribution_signals table + per-tick write`.

---

## Phase L — Local end-to-end validation

### Task L1: Smoke run against FakeBroker

**Files:** `scripts/smoke_run.py`

Run `python -m scripts.smoke_run` — instantiates a FakeBroker with $10,000 cash, runs three consecutive ticks against the real LLMs and the existing data layer (will hit Finnhub/yfinance/edgartools — small request count, fine on free tier), prints the resulting executions and the final portfolio.

This is the first place we exercise the *whole* system with real LLMs but a fake broker. Cost: ~$0.20 per run.

Commit: `feat: smoke_run script for local end-to-end validation`.

### Task L2: Replay backtest harness

**Files:** `scripts/replay_backtest.py`, `tests/replay/test_replay_30days.py`

Replay 30 days of historical bars (cached via yfinance) through the pipeline. The data layer must be made cacheable for replay — add a `--fixture-dir` flag to the harness that swaps providers for fixture loaders (use `unittest.mock` to monkeypatch `data.*` functions).

Commit: `feat: replay_backtest harness for Tier 4 evaluation`.

---

## Phase M — Equity plotter

### Task M1: plot_equity.py

**Files:** `scripts/plot_equity.py`

Reads `portfolio_snapshots` table, plots:
- bot total_value over time
- spy_value_if_held over time
- excess_return_pct as a bar chart at the bottom

Outputs `docs/performance/<date>.png`.

Tests: snapshot test of the matplotlib figure (write to a temp file, check it exists and is non-empty).

Commit: `feat(scripts): plot_equity.py — bot vs SPY equity curve`.

---

## Phase N — Baselines

### Task N1: SPY baseline

**Files:** `baselines/spy.py`, `tests/unit/test_baseline_spy.py`

Per `docs/baselines.md`: pull SPY OHLCV via yfinance, compute cumulative return, annualised return, Sharpe, max drawdown, Calmar.

Commit: `feat(baselines): SPY buy-and-hold baseline`.

### Task N2: PyTorch MLP baseline

**Files:** `baselines/mlp.py`, `tests/unit/test_baseline_mlp.py`

Per `docs/2026-05-06-mlp-model.md`: 11-feature MLP (rolling returns, volume changes, vol, MA gaps, RSI), walk-forward training, threshold trading rule.

Tests: feature engineering correctness on a known fixture; model training converges loss < 0.7 BCE on 1y of fake data.

Commit: `feat(baselines): MLPBaseline matches docs/2026-05-06-mlp-model.md`.

### Task N3: Evaluation harness (3-way comparison)

**Files:** `baselines/evaluate.py`, `tests/integration/test_baseline_evaluate.py`

Per `docs/baselines.md`: runs StockBot replay + SPY + MLP through the same backtester over the same window, writes a single comparison table to `docs/performance/<date>.md` with the pass/fail line.

Commit: `feat(baselines): evaluate.py runs 3-way comparison`.

---

## Phase O — Cloud deployment

### Task O1: Dockerfile

**Files:** `deploy/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY broker/ ./broker/
COPY orchestrator/ ./orchestrator/
COPY data/ ./data/
COPY config/ ./config/
COPY baselines/ ./baselines/

ENV PYTHONUNBUFFERED=1 \
    STOCKBOT_ENV=prod

ENTRYPOINT ["python", "-m", "orchestrator.tick"]
```

Test: build it locally:

```bash
docker build -t stockbot-tick:dev -f deploy/Dockerfile .
docker run --rm stockbot-tick:dev --help
```

Commit: `feat(deploy): Dockerfile`.

### Task O2: cloudbuild.yaml

**Files:** `deploy/cloudbuild.yaml`

```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args: [build, -t, '$_REGION-docker.pkg.dev/$PROJECT_ID/stockbot/tick:$SHORT_SHA',
           -f, deploy/Dockerfile, .]
  - name: gcr.io/cloud-builders/docker
    args: [push, '$_REGION-docker.pkg.dev/$PROJECT_ID/stockbot/tick:$SHORT_SHA']
  - name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args:
      - run
      - jobs
      - update
      - stockbot-tick
      - --image=$_REGION-docker.pkg.dev/$PROJECT_ID/stockbot/tick:$SHORT_SHA
      - --region=$_REGION
substitutions:
  _REGION: us-central1
options:
  logging: CLOUD_LOGGING_ONLY
```

Commit: `feat(deploy): cloudbuild.yaml`.

### Task O3: scheduler.yaml + setup runbook

**Files:** `deploy/scheduler.yaml`, `deploy/README.md`

Scheduler: cron `30 9-15 * * 1-5` America/New_York targeting the Cloud Run Job's execute endpoint.

Runbook (`deploy/README.md`): step-by-step bash commands for one-time GCP setup — enable APIs, create service account with required roles (`aiplatform.user`, `cloudsql.client`, `secretmanager.secretAccessor`, `storage.objectUser`), provision Cloud SQL instance + DB, create Secret Manager entries, push image, deploy job, create scheduler.

Commit: `feat(deploy): scheduler config + GCP setup runbook`.

---

## Phase P — Final acceptance

### Task P1: Manual paper-trading kickoff checklist

Add to `deploy/README.md` a final checklist (deploys are observed, not automated):

1. Run `scripts/smoke_run.py` against FakeBroker, confirm clean output.
2. Run `scripts/replay_backtest.py --window 30d` — verify the bot produces sane decisions.
3. Run `baselines/evaluate.py` on the replay window — confirm the comparison report writes to `docs/performance/<date>.md`.
4. `git tag v0.1.0-paper`.
5. Push to `main` → Cloud Build → first Cloud Run Job execution.
6. Observe Cloud Logging for one full market day (7 ticks).
7. Read the next morning's `plot_equity.py` PNG.

### Task P2: Live-trading gate

A second checklist that activates only after ≥30 calendar days of paper trading where the bot beats both baselines on Sharpe AND cumulative return:

1. Switch `BROKER_MODE=paper` to `BROKER_MODE=live` in Cloud Run Job env.
2. Re-run image build (no code change — just env).
3. Manual approval step in Cloud Build before deploy.

This task isn't implemented in code; it's documentation. Plan execution stops here.

---

## Self-review (run on the plan, not subagent dispatch)

**1. Spec coverage:** every section of the spec has at least one task.

| Spec section | Task(s) |
|---|---|
| 2. Top-level pipeline | J3, J4 |
| 3. Module layout | A2 + every subsequent task creates from this |
| 4.1 Four analysts | E1, E3, E4, F1-F4 |
| 4.2 Strategist | G1-G4 |
| 4.3 RiskGate | C1-C9, I3 |
| 4.4 Executor | H1, H2 |
| 4.5 MemoryWriter | D1-D8 |
| 4.6 Snapshotter | I1, I2 |
| 4.7 Stock picker | J2 |
| 5. State schema | C1, J1 |
| 6. Pydantic models | E1, G1, D1, C1 |
| 7. Constraints summary | C1-C9 |
| 8. Memory tiering | K (in-band) + Phase 2 (out-of-scope) |
| 9. Cloud deployment | O1-O3 |
| 10. Failure handling | E2, E3 retry callbacks; H2 idempotency |
| 11. Alerting | O3 (in runbook); concrete Cloud Monitoring alerts deferred to runbook |
| 12. Testing strategy | every task has Tier 1; Tier 2 in F1-F4 G3; Tier 3 in J5; Tier 4 in L2; Tier 5 manual in P1 |
| 13. Phase 2 roadmap | NOT IMPLEMENTED (correctly out of scope) |
| 14. Decisions log | reflected in the architectural choices throughout |
| 15. Open questions | parameterised — token-bucket rates already in `data/rate_limit.py`, watchlist in `config/watchlist.json`, model versions pinned in F1-F4/G3 |

**2. Placeholder scan:** no `TBD`, `TODO`, "implement later", or "add appropriate error handling" — every step has explicit code or commands.

**3. Type consistency:**
- `Order`, `ClampRecord`, `Execution` defined in `orchestrator/state.py` (C1) and used identically in C8, H2, I3.
- `Portfolio`, `Position` defined in B1, used in B2, B3, C8, H2, I2.
- `BufferEntry`, `MemoryProjection` in D1, used in D5, D6, G2.
- `PositionThesis`, `StrategistDecision` in G1, used in G3, G4, H2.
- `AnalystSignal` base in E1, extended consistently in F1-F4.
- `Broker` Protocol in B1, used by B2 (`FakeBroker`), B3 (`Trading212Broker`), H2, I2.

**4. Scope check:** one cohesive Phase 1 plan. Phase 2 items (load_memory tool, tool-using analysts, agent stock picker, LoopAgent critique, custom dashboard, PagerDuty/Slack, live trading) are explicitly excluded and listed in spec §13.

---

**End of plan.**
