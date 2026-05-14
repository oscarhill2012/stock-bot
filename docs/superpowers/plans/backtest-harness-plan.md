# Backtest Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end backtester that drives the unchanged live pipeline against the SVB-stress 2023-03 window from a local SQLite cache, producing per-tick traces and per-Fill decision snapshots.

**Architecture:** A thin driver loops over historical tick timestamps (NYSE open + close), threads an explicit `as_of` kwarg through every provider fetch, and swaps the active provider for each domain to a `cache` upstream that reads from a point-in-time-filtered SQLite store. The live `build_pipeline()` is reused verbatim; analyst code is untouched aside from picking up `as_of` from the existing fetch wrappers.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, Pydantic v2, Google ADK, `pandas_market_calendars`, matplotlib, pytest, FakeBroker.

---

## File Structure

**New files (all under `src/backtest/` plus `scripts/` + `config/` + `tests/`):**

```
src/backtest/
├── __init__.py
├── windows.py              # era-window config loader
├── schedule.py             # tick-schedule generator
├── decision_logger.py      # post-Fill snapshot writer (lives outside backtest-only path)
├── cache/
│   ├── __init__.py
│   ├── schema.py           # SQLAlchemy DDL for the golden store
│   ├── store.py            # CachedDataStore — read/write with PIT filter
│   └── fetcher.py          # one-time cache fill from live providers
├── providers/
│   ├── __init__.py
│   ├── _store_handle.py    # module-level singleton wiring
│   ├── stats_cache.py
│   ├── news_cache.py
│   ├── social_sentiment_cache.py   # returns None
│   ├── insider_trades_cache.py
│   ├── politician_trades_cache.py
│   ├── notable_holders_cache.py
│   └── filings_cache.py
├── driver.py               # tick loop
├── runner.py               # one full run: setup → driver → reporting
└── reporting.py            # equity curve + metrics.md + forward-return backfill

scripts/
├── backtest_fetch.py
├── backtest_run.py
└── backtest_report.py

config/
├── backtest_windows.json
└── backtest_settings.json

tests/unit/backtest/
├── __init__.py
├── test_windows.py
├── test_schedule.py
├── test_cache_store.py
├── test_cache_providers.py
└── test_decision_logger.py

tests/integration/backtest/
├── __init__.py
├── test_fetcher_idempotent.py
├── test_driver_one_tick.py
├── test_driver_failure_threshold.py
└── test_end_to_end_smoke.py
```

**Modified files (the only invasive migration):**

- `src/data/registry.py` — add `set_active_provider(domain, name)` helper.
- `src/data/__init__.py` — add `as_of: datetime = ...` kwarg to all seven wrappers.
- `src/data/aggregator.py` — add `as_of` kwarg, thread to all `dispatch()` calls.
- `src/agents/analysts/{technical,fundamental,news,social,smart_money}/fetch.py` — pull `as_of` from `state["as_of"]` (default `datetime.utcnow()`) and pass to wrapper calls.
- `src/agents/executor/agent.py` — invoke `DecisionLogger.on_executions(state)` at end of `_run_async_impl` (no-op unless `state["_decision_logger"]` is set).

---

## Phase A — Foundation: config + windows + schedule

### Task A1: Era-window config file

**Files:**
- Create: `config/backtest_windows.json`

- [ ] **Step 1: Create the v1 single-window config**

```json
{
  "svb-stress-2023-03": {
    "start": "2023-03-06",
    "end":   "2023-04-07",
    "notes": "SVB / Signature collapse, regional banking stress. First v1 window."
  }
}
```

- [ ] **Step 2: Document in `config/README.md`**

Append a section:

```markdown
### `backtest_windows.json`

Era-keyed historical windows for the backtest harness. Each entry:

- `start` / `end`: ISO date strings (inclusive); tick schedule covers NYSE business days in the range.
- `notes`: free-form description of the regime this window captures.

Add new windows by editing this file — no code changes needed.
```

- [ ] **Step 3: Commit**

```bash
git add config/backtest_windows.json config/README.md
git commit -m "feat(backtest): add era-window config for svb-stress-2023-03"
```

### Task A2: Backtest settings config

**Files:**
- Create: `config/backtest_settings.json`

- [ ] **Step 1: Create the settings file**

```json
{
  "cache_path":            "backtests/cache/store.sqlite",
  "runs_root":             "backtests/runs",
  "ticks_per_day":         ["open", "close"],
  "tz":                    "America/New_York",
  "open_time":             "09:30",
  "close_time":            "16:00",
  "failed_tick_abort_ratio": 0.10,
  "fake_broker_starting_cash": 100000.0,
  "forward_return_horizons_days": [1, 5, 20],
  "default_lookback_days": {
    "news":              30,
    "insider_trades":    90,
    "politician_trades": 90,
    "notable_holders":   365,
    "filings":           365
  }
}
```

- [ ] **Step 2: Document in `config/README.md`**

Append a section describing each key briefly. Single sentence per key is fine.

- [ ] **Step 3: Commit**

```bash
git add config/backtest_settings.json config/README.md
git commit -m "feat(backtest): add backtest_settings.json with cache + run defaults"
```

### Task A3: Window loader — failing test first

**Files:**
- Create: `tests/unit/backtest/__init__.py` (empty)
- Create: `tests/unit/backtest/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the era-window config loader."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backtest.windows import Window, load_windows


def test_load_windows_parses_svb_fixture(tmp_path: Path) -> None:
    """Canonical fixture parses into a dict[str, Window]."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"svb-stress-2023-03": '
        '{"start": "2023-03-06", "end": "2023-04-07", "notes": "test"}}'
    )

    windows = load_windows(cfg)

    assert set(windows) == {"svb-stress-2023-03"}
    w = windows["svb-stress-2023-03"]
    assert isinstance(w, Window)
    assert w.start == date(2023, 3, 6)
    assert w.end   == date(2023, 4, 7)
    assert w.notes == "test"


def test_load_windows_rejects_inverted_range(tmp_path: Path) -> None:
    """end < start must raise."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "2023-04-07", "end": "2023-03-06", "notes": ""}}'
    )

    with pytest.raises(ValueError, match="end .* before start"):
        load_windows(cfg)


def test_load_windows_rejects_malformed_date(tmp_path: Path) -> None:
    """Non-ISO date strings raise pydantic ValidationError."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "not-a-date", "end": "2023-04-07", "notes": ""}}'
    )

    with pytest.raises(Exception):  # pydantic ValidationError
        load_windows(cfg)
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_windows.py -v
```

Expected: ImportError on `backtest.windows`.

- [ ] **Step 3: Implement `src/backtest/windows.py`**

Create `src/backtest/__init__.py` (empty), then `src/backtest/windows.py`:

```python
"""Era-window config loader for the backtest harness.

Reads ``config/backtest_windows.json`` and returns a dict of validated
``Window`` records keyed by the era slug (e.g. ``"svb-stress-2023-03"``).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, model_validator


class Window(BaseModel):
    """One historical era window — inclusive ``[start, end]`` date range."""

    start: date
    end:   date
    notes: str = ""

    @model_validator(mode="after")
    def _check_range(self) -> "Window":
        # Reject backwards ranges early; downstream tick schedule would silently
        # yield zero ticks otherwise, which is the worst kind of "nothing happens".
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) before start ({self.start})")
        return self


def load_windows(path: Path) -> dict[str, Window]:
    """Load and validate every window definition in the JSON file at ``path``."""
    raw = json.loads(Path(path).read_text())
    return {key: Window.model_validate(value) for key, value in raw.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_windows.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/__init__.py src/backtest/windows.py tests/unit/backtest/
git commit -m "feat(backtest): add era-window config loader with validation"
```

### Task A4: Tick schedule generator — failing test first

**Files:**
- Create: `tests/unit/backtest/test_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the tick-schedule generator."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from backtest.schedule import Tick, generate_ticks

NY = ZoneInfo("America/New_York")


def test_generate_ticks_skips_weekends() -> None:
    """Friday 2023-03-10 → Monday 2023-03-13 yields 4 ticks (Fri open/close, Mon open/close)."""
    ticks = generate_ticks(date(2023, 3, 10), date(2023, 3, 13))

    expected = [
        Tick(as_of=datetime(2023, 3, 10,  9, 30, tzinfo=NY), phase="open"),
        Tick(as_of=datetime(2023, 3, 10, 16,  0, tzinfo=NY), phase="close"),
        Tick(as_of=datetime(2023, 3, 13,  9, 30, tzinfo=NY), phase="open"),
        Tick(as_of=datetime(2023, 3, 13, 16,  0, tzinfo=NY), phase="close"),
    ]
    assert ticks == expected


def test_generate_ticks_skips_nyse_holidays() -> None:
    """2023-04-07 is Good Friday — NYSE closed. The schedule must skip it."""
    ticks = generate_ticks(date(2023, 4, 6), date(2023, 4, 10))

    tick_dates = {t.as_of.date() for t in ticks}
    assert date(2023, 4, 7)  not in tick_dates   # Good Friday
    assert date(2023, 4, 6)  in tick_dates       # Thursday
    assert date(2023, 4, 10) in tick_dates       # Monday


def test_generate_ticks_empty_range() -> None:
    """A range covering only a weekend yields zero ticks."""
    # 2023-03-11 (Sat) → 2023-03-12 (Sun)
    assert generate_ticks(date(2023, 3, 11), date(2023, 3, 12)) == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_schedule.py -v
```

Expected: ImportError on `backtest.schedule`.

- [ ] **Step 3: Implement `src/backtest/schedule.py`**

```python
"""Tick-schedule generator.

Yields ``Tick(as_of, phase)`` pairs over NYSE business days in a date range,
emitting one tick at the configured open time and one at the close time per
session.  Holidays and weekends are skipped via ``pandas_market_calendars``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

Phase = Literal["open", "close"]

# NYSE calendar — singleton lookup is cheap, but instantiating per call is too.
_NYSE = mcal.get_calendar("NYSE")
_NY   = ZoneInfo("America/New_York")
_OPEN_TIME  = time(9, 30)
_CLOSE_TIME = time(16, 0)


@dataclass(frozen=True)
class Tick:
    """One scheduled tick — timezone-aware NY-local ``as_of`` plus phase tag."""

    as_of: datetime
    phase: Phase


def generate_ticks(start: date, end: date) -> list[Tick]:
    """Return open + close ticks for every NYSE business day in ``[start, end]``.

    Holidays and early-close days are handled via ``pandas_market_calendars``;
    weekends fall out naturally.  Returned list is sorted by ``as_of``.
    """
    sessions = _NYSE.valid_days(start_date=start, end_date=end)

    ticks: list[Tick] = []
    for ts in sessions:
        d = ts.date()
        ticks.append(Tick(datetime.combine(d, _OPEN_TIME,  tzinfo=_NY), "open"))
        ticks.append(Tick(datetime.combine(d, _CLOSE_TIME, tzinfo=_NY), "close"))
    return ticks
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_schedule.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/schedule.py tests/unit/backtest/test_schedule.py
git commit -m "feat(backtest): add NYSE tick-schedule generator (open + close)"
```

---

## Phase B — Cache: schema + store with point-in-time filter

### Task B1: Cache schema DDL

**Files:**
- Create: `src/backtest/cache/__init__.py` (empty)
- Create: `src/backtest/cache/schema.py`

- [ ] **Step 1: Write the schema module**

```python
"""SQLAlchemy DDL for the golden backtest cache store.

One SQLite file at ``backtests/cache/store.sqlite``.  Every time-bearing
column is indexed for fast point-in-time reads.  Critical correctness rule:
filter on the *filing* / *publication* timestamp, never on the
*transaction* date — a Form 4 trade can predate its filing by days.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, String,
)
from sqlalchemy.orm import DeclarativeBase


SCHEMA_VERSION = 1


class CacheBase(DeclarativeBase):
    """Declarative base for every table in the cache store."""


class OHLCVBarRow(CacheBase):
    """Daily OHLCV bar.  PK ``(ticker, date)``."""

    __tablename__ = "ohlcv_bars"
    ticker:   str   = Column(String,  primary_key=True)
    date:     date  = Column(Date,    primary_key=True)
    open:     float = Column(Float)
    high:     float = Column(Float)
    low:      float = Column(Float)
    close:    float = Column(Float)
    volume:   int   = Column(Integer)
    adj_close: float = Column(Float)

    __table_args__ = (Index("ix_ohlcv_ticker_date", "ticker", "date"),)


class MarketMetaRow(CacheBase):
    """Daily fundamentals / market-meta snapshot.  PK ``(ticker, as_of_date)``."""

    __tablename__ = "market_meta"
    ticker:        str   = Column(String, primary_key=True)
    as_of_date:    date  = Column(Date,   primary_key=True)
    market_cap:    float = Column(Float)
    trailing_pe:   float = Column(Float)
    forward_pe:    float = Column(Float)
    beta:          float = Column(Float)
    dividend_yield: float = Column(Float)
    ma_50:         float = Column(Float)
    ma_200:        float = Column(Float)
    sector:        str   = Column(String)
    long_name:     str   = Column(String)

    __table_args__ = (Index("ix_meta_ticker_asof", "ticker", "as_of_date"),)


class FilingRow(CacheBase):
    """SEC filing.  PK ``accession_no``."""

    __tablename__ = "filings"
    accession_no:        str       = Column(String, primary_key=True)
    ticker:              str       = Column(String, index=True)
    form_type:           str       = Column(String)
    filed_at:            datetime  = Column(DateTime)
    title:               str       = Column(String)
    url:                 str       = Column(String)
    risk_factors_excerpt: str      = Column(String)
    mda_excerpt:         str       = Column(String)

    __table_args__ = (Index("ix_filings_ticker_filed", "ticker", "filed_at"),)


class NewsArticleRow(CacheBase):
    """News article.  PK ``(ticker, url)``."""

    __tablename__ = "news_articles"
    ticker:       str      = Column(String,  primary_key=True)
    url:          str      = Column(String,  primary_key=True)
    headline:     str      = Column(String)
    summary:      str      = Column(String)
    source:       str      = Column(String)
    published_at: datetime = Column(DateTime)
    sentiment:    float    = Column(Float)

    __table_args__ = (Index("ix_news_ticker_pub", "ticker", "published_at"),)


class InsiderTradeRow(CacheBase):
    """Insider Form 4 row.  PK ``(accession_no, row_idx)``."""

    __tablename__ = "insider_trades"
    accession_no:      str      = Column(String,  primary_key=True)
    row_idx:           int      = Column(Integer, primary_key=True)
    ticker:            str      = Column(String,  index=True)
    insider_name:      str      = Column(String)
    insider_title:     str      = Column(String)
    side:              str      = Column(String)
    shares:            float    = Column(Float)
    price_per_share:   float    = Column(Float)
    transaction_date:  date     = Column(Date)
    filed_at:          datetime = Column(DateTime)
    form_type:         str      = Column(String)

    __table_args__ = (Index("ix_insider_ticker_filed", "ticker", "filed_at"),)


class PoliticianTradeRow(CacheBase):
    """Politician trade disclosure.  PK is a synthetic SHA1 of the tuple."""

    __tablename__ = "politician_trades"
    row_hash:         str  = Column(String, primary_key=True)
    ticker:           str  = Column(String, index=True)
    politician:       str  = Column(String)
    chamber:          str  = Column(String)
    party:            str  = Column(String)
    side:             str  = Column(String)
    transaction_date: date = Column(Date)
    disclosure_date:  date = Column(Date)
    amount_min_usd:   float = Column(Float)
    amount_max_usd:   float = Column(Float)

    __table_args__ = (
        Index("ix_pol_ticker_disc", "ticker", "disclosure_date"),
    )


class NotableHolderRow(CacheBase):
    """13D / 13G / 13F filing.  PK ``accession_no``."""

    __tablename__ = "notable_holders"
    accession_no:  str      = Column(String,  primary_key=True)
    ticker:        str      = Column(String,  index=True)
    holder:        str      = Column(String)
    form_type:     str      = Column(String)
    intent:        str      = Column(String)
    is_amendment:  bool     = Column(Boolean)
    filed_at:      datetime = Column(DateTime)
    url:           str      = Column(String)

    __table_args__ = (Index("ix_holders_ticker_filed", "ticker", "filed_at"),)


class CacheRunRow(CacheBase):
    """Audit log of fetcher runs.  Surrogate ``run_id`` PK."""

    __tablename__ = "cache_runs"
    run_id:          str      = Column(String,   primary_key=True)
    started_at:      datetime = Column(DateTime)
    finished_at:     datetime = Column(DateTime)
    window_key:      str      = Column(String)
    ticker:          str      = Column(String,   index=True)
    domain:          str      = Column(String,   index=True)
    source_provider: str      = Column(String)
    rows_written:    int      = Column(Integer)
    status:          str      = Column(String)   # "ok" | "error" | "partial"
    error:           str      = Column(String)


class MetaRow(CacheBase):
    """Single-row table holding schema version + creation timestamp."""

    __tablename__ = "meta"
    schema_version: int      = Column(Integer,  primary_key=True)
    created_at:     datetime = Column(DateTime)


def create_all(engine) -> None:
    """Create every table on the supplied SQLAlchemy engine."""
    CacheBase.metadata.create_all(engine)
```

- [ ] **Step 2: Commit**

```bash
git add src/backtest/cache/__init__.py src/backtest/cache/schema.py
git commit -m "feat(backtest): add cache schema DDL with point-in-time indexes"
```

### Task B2: CachedDataStore — failing test first (PIT filter is the headline)

**Files:**
- Create: `tests/unit/backtest/test_cache_store.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the point-in-time-filtered cache store.

The point-in-time property is the most important correctness rule in the
whole harness: reads must NEVER return a row whose canonical timestamp is
after the supplied ``as_of``.  Lookahead bias would silently invalidate
every backtest.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import NewsArticle, OHLCBar


@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    """Fresh empty cache store rooted in a temp dir."""
    return CachedDataStore(tmp_path / "store.sqlite")


def test_news_read_excludes_future_articles(store: CachedDataStore) -> None:
    """Articles published after ``as_of`` must not be returned."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/1", headline="Past",
            summary="", source="t", published_at=datetime(2023, 3, 8, tzinfo=UTC),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/2", headline="Future",
            summary="", source="t", published_at=datetime(2023, 3, 20, tzinfo=UTC),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert [a.headline for a in result] == ["Past"]


def test_news_read_respects_lookback_lower_bound(store: CachedDataStore) -> None:
    """Articles older than ``lookback_days`` before ``as_of`` are excluded."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/old", headline="Too Old",
            summary="", source="t",
            published_at=datetime(2023, 1, 1, tzinfo=UTC),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/recent", headline="Recent",
            summary="", source="t",
            published_at=datetime(2023, 3, 10, tzinfo=UTC),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert [a.headline for a in result] == ["Recent"]


def test_ohlcv_read_returns_inclusive_range(store: CachedDataStore) -> None:
    """``read_ohlcv(start, end)`` returns bars with date in ``[start, end]``."""
    bars = [
        OHLCBar(ticker="AAPL", date=date(2023, 3, d), open=1, high=2,
                low=0.5, close=1.5, volume=100, adj_close=1.5)
        for d in (6, 7, 8, 9, 10)
    ]
    store.write_ohlcv("AAPL", bars)

    result = store.read_ohlcv("AAPL", date(2023, 3, 7), date(2023, 3, 9))

    assert [b.date for b in result] == [
        date(2023, 3, 7), date(2023, 3, 8), date(2023, 3, 9),
    ]


def test_write_is_idempotent_on_primary_key(store: CachedDataStore) -> None:
    """Re-writing the same news article is a no-op, not a duplicate row."""
    article = NewsArticle(
        ticker="AAPL", url="https://x/dup", headline="H",
        summary="", source="t", published_at=datetime(2023, 3, 8, tzinfo=UTC),
    )
    store.write_news("AAPL", [article])
    store.write_news("AAPL", [article])

    result = store.read_news(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )
    assert len(result) == 1
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py -v
```

Expected: ImportError on `backtest.cache.store`.

- [ ] **Step 3: Implement `src/backtest/cache/store.py`**

```python
"""Cache store façade — read/write keyed on (ticker, as_of, domain).

Readers honour the point-in-time filter: rows whose canonical timestamp is
after the supplied ``as_of`` are never returned.  Writers are idempotent on
the primary key — re-running the fetcher is safe.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backtest.cache.schema import (
    FilingRow, InsiderTradeRow, MarketMetaRow, MetaRow, NewsArticleRow,
    NotableHolderRow, OHLCVBarRow, PoliticianTradeRow, SCHEMA_VERSION,
    create_all,
)
from data.models import (
    Filing, InsiderTrade, NewsArticle, NotableHolder, OHLCBar,
    PoliticianTrade, StockStats,
)


class CachedDataStore:
    """SQLite-backed read/write façade over the golden cache.

    Methods are grouped by domain; every reader applies the point-in-time
    filter required for lookahead-free backtests.
    """

    def __init__(self, path: Path) -> None:
        """Open (or create) the SQLite file at ``path``; initialise schema."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{path}", future=True)
        create_all(self._engine)
        self._ensure_meta()

    # ── meta / schema version ────────────────────────────────────────────
    def _ensure_meta(self) -> None:
        """Insert the schema-version row if the meta table is empty."""
        with Session(self._engine) as s:
            existing = s.execute(select(MetaRow)).scalar_one_or_none()
            if existing is None:
                s.add(MetaRow(schema_version=SCHEMA_VERSION,
                              created_at=datetime.now(tz=UTC)))
                s.commit()

    # ── OHLCV ────────────────────────────────────────────────────────────
    def write_ohlcv(self, ticker: str, bars: list[OHLCBar]) -> None:
        """Upsert daily OHLCV bars for ``ticker``."""
        with Session(self._engine) as s:
            for b in bars:
                stmt = sqlite_insert(OHLCVBarRow).values(
                    ticker=ticker, date=b.date, open=b.open, high=b.high,
                    low=b.low, close=b.close, volume=b.volume,
                    adj_close=b.adj_close,
                ).on_conflict_do_nothing(index_elements=["ticker", "date"])
                s.execute(stmt)
            s.commit()

    def read_ohlcv(self, ticker: str, start: date, end: date) -> list[OHLCBar]:
        """Return bars in ``[start, end]`` inclusive, sorted by date ascending."""
        with Session(self._engine) as s:
            rows = s.execute(
                select(OHLCVBarRow)
                .where(OHLCVBarRow.ticker == ticker,
                       OHLCVBarRow.date   >= start,
                       OHLCVBarRow.date   <= end)
                .order_by(OHLCVBarRow.date)
            ).scalars().all()
            return [OHLCBar.model_validate(r, from_attributes=True) for r in rows]

    # ── Market meta ──────────────────────────────────────────────────────
    def write_market_meta(
        self, ticker: str, snapshot: StockStats, as_of_date: date,
    ) -> None:
        """Upsert one market-meta snapshot for ``(ticker, as_of_date)``."""
        with Session(self._engine) as s:
            stmt = sqlite_insert(MarketMetaRow).values(
                ticker=ticker, as_of_date=as_of_date,
                market_cap=snapshot.market_cap,
                trailing_pe=snapshot.trailing_pe,
                forward_pe=snapshot.forward_pe, beta=snapshot.beta,
                dividend_yield=snapshot.dividend_yield,
                ma_50=snapshot.ma_50, ma_200=snapshot.ma_200,
                sector=snapshot.sector, long_name=snapshot.long_name,
            ).on_conflict_do_nothing(
                index_elements=["ticker", "as_of_date"],
            )
            s.execute(stmt)
            s.commit()

    def read_market_meta(
        self, ticker: str, as_of: datetime,
    ) -> StockStats | None:
        """Return the latest meta row with ``as_of_date <= as_of.date()``."""
        with Session(self._engine) as s:
            row = s.execute(
                select(MarketMetaRow)
                .where(MarketMetaRow.ticker     == ticker,
                       MarketMetaRow.as_of_date <= as_of.date())
                .order_by(MarketMetaRow.as_of_date.desc())
                .limit(1)
            ).scalar_one_or_none()
            return None if row is None else StockStats.model_validate(
                row, from_attributes=True,
            )

    # ── News ─────────────────────────────────────────────────────────────
    def write_news(self, ticker: str, articles: list[NewsArticle]) -> None:
        """Upsert news articles for ``ticker``."""
        with Session(self._engine) as s:
            for a in articles:
                stmt = sqlite_insert(NewsArticleRow).values(
                    ticker=ticker, url=a.url, headline=a.headline,
                    summary=a.summary, source=a.source,
                    published_at=a.published_at, sentiment=a.sentiment,
                ).on_conflict_do_nothing(index_elements=["ticker", "url"])
                s.execute(stmt)
            s.commit()

    def read_news(
        self, ticker: str, as_of: datetime, lookback_days: int = 30,
    ) -> list[NewsArticle]:
        """Return articles in ``(as_of - lookback_days, as_of]``, descending."""
        lower = as_of - timedelta(days=lookback_days)
        with Session(self._engine) as s:
            rows = s.execute(
                select(NewsArticleRow)
                .where(NewsArticleRow.ticker       == ticker,
                       NewsArticleRow.published_at <= as_of,
                       NewsArticleRow.published_at >  lower)
                .order_by(NewsArticleRow.published_at.desc())
            ).scalars().all()
            return [NewsArticle.model_validate(r, from_attributes=True)
                    for r in rows]

    # ── Filings ──────────────────────────────────────────────────────────
    def write_filings(self, ticker: str, filings: list[Filing]) -> None:
        """Upsert SEC filings for ``ticker``."""
        with Session(self._engine) as s:
            for f in filings:
                stmt = sqlite_insert(FilingRow).values(
                    accession_no=f.accession_no, ticker=ticker,
                    form_type=f.form_type, filed_at=f.filed_at,
                    title=f.title, url=f.url,
                    risk_factors_excerpt=f.risk_factors_excerpt,
                    mda_excerpt=f.mda_excerpt,
                ).on_conflict_do_nothing(index_elements=["accession_no"])
                s.execute(stmt)
            s.commit()

    def read_filings(
        self, ticker: str, as_of: datetime, lookback_days: int = 365,
    ) -> list[Filing]:
        """Return filings with ``filed_at <= as_of`` within the lookback window."""
        lower = as_of - timedelta(days=lookback_days)
        with Session(self._engine) as s:
            rows = s.execute(
                select(FilingRow)
                .where(FilingRow.ticker   == ticker,
                       FilingRow.filed_at <= as_of,
                       FilingRow.filed_at >  lower)
                .order_by(FilingRow.filed_at.desc())
            ).scalars().all()
            return [Filing.model_validate(r, from_attributes=True)
                    for r in rows]

    # ── Insider trades ───────────────────────────────────────────────────
    def write_insider_trades(
        self, ticker: str, trades: list[InsiderTrade],
    ) -> None:
        """Upsert Form-4 insider trades for ``ticker``."""
        with Session(self._engine) as s:
            for idx, t in enumerate(trades):
                stmt = sqlite_insert(InsiderTradeRow).values(
                    accession_no=t.accession_no, row_idx=idx,
                    ticker=ticker, insider_name=t.insider_name,
                    insider_title=t.insider_title, side=t.side,
                    shares=t.shares, price_per_share=t.price_per_share,
                    transaction_date=t.transaction_date,
                    filed_at=t.filed_at, form_type=t.form_type,
                ).on_conflict_do_nothing(
                    index_elements=["accession_no", "row_idx"],
                )
                s.execute(stmt)
            s.commit()

    def read_insider_trades(
        self, ticker: str, as_of: datetime, lookback_days: int = 90,
    ) -> list[InsiderTrade]:
        """Return insider trades filtered by ``filed_at`` — never ``transaction_date``.

        Form 4 trades can be transacted days before they are filed; filtering on
        ``transaction_date`` would leak future information.
        """
        lower = as_of - timedelta(days=lookback_days)
        with Session(self._engine) as s:
            rows = s.execute(
                select(InsiderTradeRow)
                .where(InsiderTradeRow.ticker   == ticker,
                       InsiderTradeRow.filed_at <= as_of,
                       InsiderTradeRow.filed_at >  lower)
                .order_by(InsiderTradeRow.filed_at.desc())
            ).scalars().all()
            return [InsiderTrade.model_validate(r, from_attributes=True)
                    for r in rows]

    # ── Politician trades ────────────────────────────────────────────────
    def write_politician_trades(
        self, ticker: str, trades: list[PoliticianTrade],
    ) -> None:
        """Upsert politician trades for ``ticker``.

        PK is a synthetic SHA1 of ``(ticker, politician, transaction_date, side,
        amount_min_usd, amount_max_usd)`` because the upstream feed has no
        natural identifier.
        """
        import hashlib

        with Session(self._engine) as s:
            for t in trades:
                key = "|".join([
                    ticker, t.politician, str(t.transaction_date),
                    t.side, str(t.amount_min_usd), str(t.amount_max_usd),
                ])
                row_hash = hashlib.sha1(key.encode()).hexdigest()
                stmt = sqlite_insert(PoliticianTradeRow).values(
                    row_hash=row_hash, ticker=ticker,
                    politician=t.politician, chamber=t.chamber,
                    party=t.party, side=t.side,
                    transaction_date=t.transaction_date,
                    disclosure_date=t.disclosure_date,
                    amount_min_usd=t.amount_min_usd,
                    amount_max_usd=t.amount_max_usd,
                ).on_conflict_do_nothing(index_elements=["row_hash"])
                s.execute(stmt)
            s.commit()

    def read_politician_trades(
        self, ticker: str, as_of: datetime, lookback_days: int = 90,
    ) -> list[PoliticianTrade]:
        """Return politician trades by ``COALESCE(disclosure_date, transaction_date)``.

        Disclosure (when the public learned) is the correct PIT filter; the
        transaction itself can predate disclosure by up to 45 days under the
        STOCK Act.
        """
        from sqlalchemy import func

        lower = (as_of - timedelta(days=lookback_days)).date()
        pit = func.coalesce(
            PoliticianTradeRow.disclosure_date,
            PoliticianTradeRow.transaction_date,
        )
        with Session(self._engine) as s:
            rows = s.execute(
                select(PoliticianTradeRow)
                .where(PoliticianTradeRow.ticker == ticker,
                       pit <= as_of.date(),
                       pit >  lower)
                .order_by(pit.desc())
            ).scalars().all()
            return [PoliticianTrade.model_validate(r, from_attributes=True)
                    for r in rows]

    # ── Notable holders ──────────────────────────────────────────────────
    def write_notable_holders(
        self, ticker: str, holders: list[NotableHolder],
    ) -> None:
        """Upsert 13D/13G/13F filings for ``ticker``."""
        with Session(self._engine) as s:
            for h in holders:
                stmt = sqlite_insert(NotableHolderRow).values(
                    accession_no=h.accession_no, ticker=ticker,
                    holder=h.holder, form_type=h.form_type,
                    intent=h.intent, is_amendment=h.is_amendment,
                    filed_at=h.filed_at, url=h.url,
                ).on_conflict_do_nothing(index_elements=["accession_no"])
                s.execute(stmt)
            s.commit()

    def read_notable_holders(
        self, ticker: str, as_of: datetime, lookback_days: int = 365,
    ) -> list[NotableHolder]:
        """Return 13D/13G/13F filings with ``filed_at <= as_of``."""
        lower = as_of - timedelta(days=lookback_days)
        with Session(self._engine) as s:
            rows = s.execute(
                select(NotableHolderRow)
                .where(NotableHolderRow.ticker   == ticker,
                       NotableHolderRow.filed_at <= as_of,
                       NotableHolderRow.filed_at >  lower)
                .order_by(NotableHolderRow.filed_at.desc())
            ).scalars().all()
            return [NotableHolder.model_validate(r, from_attributes=True)
                    for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py -v
```

Expected: 4 passed.  If any Pydantic model field names differ from the column names assumed above, fix the schema/store column names — the model is the source of truth.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/cache/store.py tests/unit/backtest/test_cache_store.py
git commit -m "feat(backtest): add CachedDataStore with point-in-time read filter"
```

---

## Phase C — `as_of` migration through the live data layer

This is the only invasive migration: every provider fetch signature, every wrapper in `src/data/__init__.py`, the `aggregator`, and the analyst fetch callbacks pick up an explicit `as_of: datetime` kwarg.  The default of `datetime.utcnow()` preserves live behaviour exactly.

### Task C1: Registry helper for in-process provider swap

**Files:**
- Modify: `src/data/registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/test_registry_swap.py`:

```python
"""Tests for the in-process provider-swap helper."""
from __future__ import annotations

import pytest

from data import registry
from data.config import get_config


def test_set_active_provider_round_trips() -> None:
    """``set_active_provider`` updates the config and ``restore`` reverts it."""
    original = get_config().providers["news"]

    restore = registry.set_active_provider("news", "cache")
    assert get_config().providers["news"] == "cache"

    restore()
    assert get_config().providers["news"] == original


def test_set_active_provider_rejects_unknown_domain() -> None:
    """Unknown domain name raises ValueError."""
    with pytest.raises(ValueError, match="unknown domain"):
        registry.set_active_provider("not_a_domain", "cache")
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_registry_swap.py -v
```

Expected: `AttributeError: module 'data.registry' has no attribute 'set_active_provider'`.

- [ ] **Step 3: Implement the helper**

Append to `src/data/registry.py`:

```python
def set_active_provider(domain: str, name: str) -> Callable[[], None]:
    """Swap the active provider for ``domain`` in-process; return a restore fn.

    Used by the backtest runner to point every live domain at the ``cache``
    provider for the duration of a run.  Live (production) code never calls
    this — the active provider is read from ``config/data.json``.

    Returns a zero-arg callable that restores the previous mapping; the
    runner uses this in a ``try/finally`` so a crashed run does not leave
    the in-process config pointing at ``cache``.
    """
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain!r}")
    cfg = get_config()
    previous = cfg.providers[domain]
    cfg.providers[domain] = name

    def _restore() -> None:
        """Revert ``providers[domain]`` to the value captured at swap time."""
        get_config().providers[domain] = previous

    return _restore
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_registry_swap.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/data/registry.py tests/unit/data/test_registry_swap.py
git commit -m "feat(data): add set_active_provider helper for backtest swap"
```

### Task C2: Thread `as_of` through aggregator + wrappers

**Files:**
- Modify: `src/data/aggregator.py`
- Modify: `src/data/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/test_as_of_threading.py`:

```python
"""Tests that ``as_of`` is forwarded to every wrapper + aggregator dispatch."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from data import (
    get_company_filings, get_insider_trades, get_public_figure_trades,
    get_social_sentiment, get_stock_news, get_stock_stats,
    get_notable_holders,
)


FIXED = datetime(2023, 3, 15, 9, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("fn,domain", [
    (get_stock_stats,           "stats"),
    (get_stock_news,            "news"),
    (get_social_sentiment,      "social_sentiment"),
    (get_insider_trades,        "insider_trades"),
    (get_public_figure_trades,  "politician_trades"),
    (get_notable_holders,       "notable_holders"),
    (get_company_filings,       "filings"),
])
async def test_wrapper_forwards_as_of(fn, domain) -> None:
    """Every wrapper threads ``as_of`` into the dispatch kwargs."""
    with patch("data.registry.dispatch", new=AsyncMock(return_value=None)) as m:
        await fn("AAPL", as_of=FIXED)

    assert m.await_args.kwargs.get("as_of") == FIXED
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_as_of_threading.py -v
```

Expected: `TypeError: get_stock_stats() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Patch every wrapper in `src/data/__init__.py`**

For each wrapper, add `as_of: datetime = ...` and forward it to `_dispatch`.  The default is computed lazily inside the wrapper body — *not* in the signature default — so every call freezes the clock at call time rather than at import time:

```python
from datetime import UTC, datetime


async def get_stock_stats(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    *,
    as_of: datetime | None = None,
):
    """Fetch stats snapshot for ``ticker`` as of ``as_of`` (default: now, UTC)."""
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    return await _dispatch(
        "stats", ticker.upper(),
        period=period, interval=interval, as_of=as_of,
    )
```

Apply the identical pattern to all seven wrappers (`get_stock_news`, `get_social_sentiment`, `get_insider_trades`, `get_public_figure_trades`, `get_notable_holders`, `get_company_filings`).

- [ ] **Step 4: Patch `src/data/aggregator.py`**

Add `as_of: datetime | None = None` to `get_stock_signal_bundle`'s signature.  Replace the in-body `today = date.today()` / `datetime.now(tz=UTC)` calls with values derived from `as_of`.  Thread `as_of=as_of` into every `_safe(dispatch(...))` call.  Default lookback dates are now ``as_of.date() - timedelta(days=N)``, not ``date.today() - timedelta(days=N)``.

- [ ] **Step 5: Run wrapper tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_as_of_threading.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Run the existing data + aggregator test suites to verify live behaviour is preserved**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/data/ tests/unit/data/ -v
```

Expected: all previously-passing tests still pass (live behaviour is the `as_of=None → datetime.now(UTC)` default).

- [ ] **Step 7: Commit**

```bash
git add src/data/__init__.py src/data/aggregator.py tests/unit/data/test_as_of_threading.py
git commit -m "feat(data): add explicit as_of kwarg to every fetch wrapper + aggregator"
```

### Task C3: Thread `as_of` through analyst fetch callbacks

**Files:**
- Modify: `src/agents/analysts/technical/fetch.py`
- Modify: `src/agents/analysts/fundamental/fetch.py`
- Modify: `src/agents/analysts/news/fetch.py`
- Modify: `src/agents/analysts/social/fetch.py`
- Modify: `src/agents/analysts/smart_money/fetch.py`
- Modify: `src/agents/analysts/_common.py` (evidence `recorded_at` stamp)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_analyst_fetch_as_of.py`:

```python
"""Tests that each analyst fetch callback reads ``state['as_of']`` and forwards it."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


FIXED = datetime(2023, 3, 15, 9, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("module,patch_target", [
    ("agents.analysts.technical.fetch",   "agents.analysts.technical.fetch.get_stock_stats"),
    ("agents.analysts.news.fetch",        "agents.analysts.news.fetch.get_stock_news"),
    ("agents.analysts.social.fetch",      "agents.analysts.social.fetch.get_social_sentiment"),
])
async def test_callback_forwards_state_as_of(module, patch_target) -> None:
    """Each analyst's fetch callback passes ``as_of`` from state into its wrapper."""
    import importlib
    m = importlib.import_module(module)
    callback = next(
        v for k, v in vars(m).items() if k.endswith("_fetch_callback")
    )

    state = {"tickers": ["AAPL"], "as_of": FIXED}
    ctx   = SimpleNamespace(state=state)

    with patch(patch_target, new=AsyncMock(return_value=None)) as p:
        await callback(ctx)

    assert p.await_args.kwargs.get("as_of") == FIXED
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_analyst_fetch_as_of.py -v
```

Expected: assertion error — `as_of` is not in `await_args.kwargs`.

- [ ] **Step 3: Patch each callback**

For every analyst fetch callback, replace the body's per-ticker fetch line with:

```python
from datetime import UTC, datetime  # add to imports

async def technical_fetch_callback(callback_context: CallbackContext):
    """..."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    # Pull the historical clock from session state; default to wall-clock for live.
    as_of = state.get("as_of") or datetime.now(tz=UTC)

    technical_data = {}
    for ticker in tickers:
        try:
            stats = await get_stock_stats(ticker, as_of=as_of)
        except Exception as exc:
            logger.warning(...)
            stats = None
        technical_data[ticker] = stats.model_dump() if hasattr(stats, "model_dump") else stats
    state["technical_data"] = technical_data
    _trace_maybe(state, "01_fetch_technical", technical_data)
```

Apply the identical "pull `as_of` once, pass to wrapper" pattern in the remaining four callbacks.

**Also update `src/agents/analysts/_common.py`** so the evidence `recorded_at` stamp reads from session state, matching the strategist's existing pattern (`src/agents/strategist/agent.py:90-95`):

```python
# src/agents/analysts/_common.py — replace the unconditional wall-clock read
recorded_at = state.get("as_of") or datetime.now(tz=UTC)
```

Single-line change; live behaviour is unchanged (state has no `as_of` → wall-clock fallback), backtest stamps deterministically from the tick.

- [ ] **Step 4: Run analyst + e2e tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_analyst_fetch_as_of.py tests/agents/ -v
```

Expected: new tests pass; all pre-existing analyst tests pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/ tests/unit/agents/test_analyst_fetch_as_of.py
git commit -m "feat(analysts): pull as_of from state and forward to every fetch wrapper"
```

---

### Task C4: Uniform `as_of` kwarg on every extractor

**Files:**
- Modify: `src/contract/extractors/fundamental.py` (actually uses `as_of`)
- Modify: `src/contract/extractors/technical.py` (accepts silently)
- Modify: `src/contract/extractors/news.py` (accepts silently)
- Modify: `src/contract/extractors/social.py` (accepts silently)
- Modify: `src/contract/extractors/smart_money.py` (accepts silently)
- Modify: whichever analyst-side shim invokes `extractor=extract_<name>_features` (locate via `grep -rn 'extractor=extract_' src/agents/analysts/`)

**Rationale**

`extract_fundamental_features` reads wall-clock (`datetime.now(tz=UTC)`) to derive `days_since_last_filing` and the 30-day insider window — replaying the same raw input on different days produces different feature values. Other extractors are clock-free today but plug into the *same* shim slot, so adding `as_of` to fundamental alone would force introspection at the call site. Cleanest fix: every extractor gains an ignored `*, as_of: datetime | None = None` kwarg for signature uniformity; fundamental actually uses it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/contract/test_extractor_as_of.py`:

```python
"""``as_of`` drives time-delta features for fundamental; other extractors accept it silently."""
from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import pytest

from contract.extractors.fundamental import extract_fundamental_features


def test_fundamental_days_since_last_filing_uses_as_of() -> None:
    """Same raw bundle, two ``as_of`` values → two different ``days_since_last_filing``."""
    raw = {"filings": [{"filed_at": "2023-01-01T00:00:00+00:00", "form": "10-K"}]}

    early = extract_fundamental_features(
        raw, ticker="AAPL", as_of=datetime(2023, 1, 31, tzinfo=UTC),
    )
    late = extract_fundamental_features(
        raw, ticker="AAPL", as_of=datetime(2023, 6, 30, tzinfo=UTC),
    )
    assert late["days_since_last_filing"] > early["days_since_last_filing"]


@pytest.mark.parametrize("module_path", [
    "contract.extractors.technical",
    "contract.extractors.news",
    "contract.extractors.social",
    "contract.extractors.smart_money",
])
def test_clock_free_extractors_accept_as_of(module_path: str) -> None:
    """Every extractor accepts ``as_of`` so the analyst shim can pass it uniformly."""
    module = importlib.import_module(module_path)
    extractor = next(
        v for k, v in vars(module).items()
        if k.startswith("extract_") and callable(v)
    )
    assert "as_of" in inspect.signature(extractor).parameters
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/test_extractor_as_of.py -v
```

Expected: signature checks fail; `days_since_last_filing` test fails (early/late collapse to the same wall-clock value).

- [ ] **Step 3: Add the kwarg to every extractor**

Every public extractor gains a keyword-only `as_of`:

```python
def extract_<name>_features(
    raw: Mapping[str, Any],
    ticker: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """... existing docstring ..."""
```

Clock-free extractors (technical, news, social, smart_money) ignore the kwarg — accepting it silently so the analyst shim can pass it uniformly.

`fundamental.py` actually uses it:

```python
# was: now = datetime.now(tz=UTC)
now = as_of if as_of is not None else datetime.now(tz=UTC)
```

Live behaviour unchanged (default = wall-clock); backtest passes the historical timestamp.

- [ ] **Step 4: Update the analyst extractor shim** to pass `as_of=state.get("as_of") or datetime.now(tz=UTC)` from session state to the extractor — same pattern as Task C3.

- [ ] **Step 5: Run the full contract + analyst suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/contract/ tests/agents/ -v
```

Expected: new tests pass; all pre-existing tests pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/contract/extractors/ src/agents/analysts/ tests/unit/contract/test_extractor_as_of.py
git commit -m "feat(extractors): uniform as_of kwarg; fundamental honours it for deterministic time-deltas"
```

---

## Phase D — Cache providers

Each domain gets one cache provider, registered with the existing shell as `upstream="cache"`.  The cache providers honour the same `as_of` contract as live providers, but read from `CachedDataStore` instead of going to network.

### Task D1: Shared store handle for cache providers

**Files:**
- Create: `src/backtest/providers/__init__.py` (empty)
- Create: `src/backtest/providers/_store_handle.py`

- [ ] **Step 1: Write the helper**

```python
"""Module-level singleton wiring for the cache providers.

The runner instantiates ``CachedDataStore`` once and calls ``set_store(store)``;
every cache provider reads from this singleton.  Keeping the wiring in one
module keeps the providers themselves stateless and trivially testable.
"""
from __future__ import annotations

from backtest.cache.store import CachedDataStore

_store: CachedDataStore | None = None


def set_store(store: CachedDataStore) -> None:
    """Install the cache store the providers should read from this run."""
    global _store
    _store = store


def get_store() -> CachedDataStore:
    """Return the configured store; raise if the runner has not called ``set_store``."""
    if _store is None:
        raise RuntimeError(
            "cache providers used before runner called set_store(); "
            "this should never happen in a real backtest run"
        )
    return _store


def clear_store() -> None:
    """Reset the singleton — used between tests."""
    global _store
    _store = None
```

- [ ] **Step 2: Commit**

```bash
git add src/backtest/providers/__init__.py src/backtest/providers/_store_handle.py
git commit -m "feat(backtest): add shared store-handle singleton for cache providers"
```

### Task D2: Cache providers — failing tests first (round-trip equivalence)

**Files:**
- Create: `tests/unit/backtest/test_cache_providers.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests that each cache provider returns the same Pydantic shape as live."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers import _store_handle
from data.models import NewsArticle, StockStats


@pytest.fixture(autouse=True)
def _wire_store(tmp_path: Path):
    """Each test gets a fresh in-temp-dir store, cleared after."""
    store = CachedDataStore(tmp_path / "store.sqlite")
    _store_handle.set_store(store)
    yield store
    _store_handle.clear_store()


@pytest.mark.asyncio
async def test_news_cache_returns_pydantic_articles(_wire_store) -> None:
    """``news_cache.fetch`` returns ``list[NewsArticle]`` filtered by ``as_of``."""
    from backtest.providers import news_cache

    _wire_store.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL", url="https://x/1", headline="H",
            summary="", source="t",
            published_at=datetime(2023, 3, 10, tzinfo=UTC),
        ),
    ])

    result = await news_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], NewsArticle)


@pytest.mark.asyncio
async def test_social_cache_returns_none(_wire_store) -> None:
    """Social sentiment is deliberately unavailable in v1 backtest — return None."""
    from backtest.providers import social_sentiment_cache

    result = await social_sentiment_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result is None
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_cache_providers.py -v
```

Expected: ImportError on `backtest.providers.news_cache`.

- [ ] **Step 3: Implement each cache provider**

`src/backtest/providers/news_cache.py`:

```python
"""News provider that reads from the cache store instead of going to network."""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import NewsArticle
from data.registry import register


@register("news", "cache", upstream="cache", rate_per_minute=1_000_000, burst=1_000)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 30,
    **_unused,
) -> list[NewsArticle]:
    """Return news for ``ticker`` published at or before ``as_of``."""
    return get_store().read_news(ticker, as_of=as_of, lookback_days=lookback_days)
```

`src/backtest/providers/stats_cache.py`:

```python
"""Stats provider that reads market-meta + recent OHLCV from the cache store."""
from __future__ import annotations

from datetime import datetime, timedelta

from backtest.providers._store_handle import get_store
from data.models import StockStats
from data.registry import register


@register("stats", "cache", upstream="cache", rate_per_minute=1_000_000, burst=1_000)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused,
) -> StockStats | None:
    """Return the latest market-meta snapshot at or before ``as_of``.

    The cache materialises a daily snapshot, so ``period`` / ``interval`` are
    accepted for signature compatibility but ignored — backtest analysts that
    need historical bars query the OHLCV table separately via the aggregator.
    """
    return get_store().read_market_meta(ticker, as_of=as_of)
```

`src/backtest/providers/social_sentiment_cache.py`:

```python
"""Social-sentiment cache provider — deliberately returns None in v1.

Historical social-sentiment ingestion is tracked as a separate backlog item
(see ``docs/superpowers/backlog.md``).  The strategist already tolerates a
``None`` social evidence field, so the analyst pool degrades gracefully.
"""
from __future__ import annotations

from datetime import datetime

from data.registry import register


@register(
    "social_sentiment", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(ticker: str, *, as_of: datetime, **_unused) -> None:
    """Always return ``None`` — backlog item B19 will populate this domain."""
    return None
```

`src/backtest/providers/insider_trades_cache.py`:

```python
"""Insider-trades cache provider."""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import InsiderTrade
from data.registry import register


@register(
    "insider_trades", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[InsiderTrade]:
    """Return insider trades filed at or before ``as_of``."""
    return get_store().read_insider_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
```

`src/backtest/providers/politician_trades_cache.py`:

```python
"""Politician-trades cache provider."""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import PoliticianTrade
from data.registry import register


@register(
    "politician_trades", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Return politician trades disclosed at or before ``as_of``."""
    return get_store().read_politician_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
```

`src/backtest/providers/notable_holders_cache.py`:

```python
"""Notable-holders (13D/13G/13F) cache provider."""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import NotableHolder
from data.registry import register


@register(
    "notable_holders", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 365,
    **_unused,
) -> list[NotableHolder]:
    """Return notable-holder filings at or before ``as_of``."""
    return get_store().read_notable_holders(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
```

`src/backtest/providers/filings_cache.py`:

```python
"""SEC filings cache provider."""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import Filing
from data.registry import register


@register(
    "filings", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 365,
    **_unused,
) -> list[Filing]:
    """Return filings filed at or before ``as_of``."""
    return get_store().read_filings(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_cache_providers.py -v
```

Expected: 2 passed.  Verify by hand that every cache provider sets `upstream="cache"` so the existing rate-limiter map distinguishes it from live upstreams.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/providers/ tests/unit/backtest/test_cache_providers.py
git commit -m "feat(backtest): add cache providers for all 7 data domains"
```

---

## Phase E — Fetcher (one-time cache fill)

### Task E1: Fetcher orchestration — failing test first

**Files:**
- Create: `tests/integration/backtest/__init__.py` (empty)
- Create: `tests/integration/backtest/test_fetcher_idempotent.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests that the cache fetcher is idempotent across re-runs."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backtest.cache.fetcher import Fetcher
from backtest.cache.store import CachedDataStore
from backtest.windows import Window
from data.models import OHLCBar


@pytest.mark.asyncio
async def test_fetcher_skips_completed_combinations(tmp_path: Path) -> None:
    """Re-running the fetcher does not re-call providers for ok-marked rows."""
    store = CachedDataStore(tmp_path / "store.sqlite")
    window = Window(start=date(2023, 3, 6), end=date(2023, 3, 10), notes="")

    fake_provider = AsyncMock(return_value=[
        OHLCBar(ticker="AAPL", date=date(2023, 3, d), open=1, high=2, low=0.5,
                close=1.5, volume=100, adj_close=1.5)
        for d in range(6, 11)
    ])

    fetcher = Fetcher(
        store=store, window_key="svb-test", window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": fake_provider},   # one-domain test
        live_providers_for_domain={"ohlcv": "yfinance"},
    )

    await fetcher.run()
    first_call_count = fake_provider.await_count

    await fetcher.run()
    assert fake_provider.await_count == first_call_count, \
        "second run must not call the provider again"
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_fetcher_idempotent.py -v
```

Expected: ImportError on `backtest.cache.fetcher`.

- [ ] **Step 3: Implement the fetcher**

`src/backtest/cache/fetcher.py`:

```python
"""One-time cache fill from live providers.

Idempotent: if a ``cache_runs`` row already exists with ``status='ok'`` for a
``(window_key, ticker, domain)`` triple, the corresponding fetch is skipped.
Failed runs (``status='error'``) and partial runs (no row) are retried.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backtest.cache.schema import CacheRunRow
from backtest.cache.store import CachedDataStore
from backtest.windows import Window

logger = logging.getLogger(__name__)

# Map of domain name → CachedDataStore writer method name.  The fetcher
# resolves the writer by attribute lookup so adding a domain is one map entry
# plus one provider function — no large switch statement.
_WRITER_BY_DOMAIN: dict[str, str] = {
    "ohlcv":             "write_ohlcv",
    "market_meta":       "write_market_meta",
    "news":              "write_news",
    "filings":           "write_filings",
    "insider_trades":    "write_insider_trades",
    "politician_trades": "write_politician_trades",
    "notable_holders":   "write_notable_holders",
}


class Fetcher:
    """Drive a one-time cache fill across (window × watchlist × domain)."""

    def __init__(
        self,
        *,
        store: CachedDataStore,
        window_key: str,
        window: Window,
        watchlist: list[str],
        provider_fns: dict[str, Callable[..., Awaitable[Any]]],
        live_providers_for_domain: dict[str, str],
    ) -> None:
        """Wire the fetcher with everything it needs to fill the cache.

        Parameters
        ----------
        store:
            The shared golden cache to write into.
        window_key, window:
            Era slug + resolved date range.
        watchlist:
            Tickers to fetch.
        provider_fns:
            Domain → async fetch function (the *live* provider, not the cache
            provider).  Injected so tests can stub them.
        live_providers_for_domain:
            Domain → provider name string, recorded in ``cache_runs.source_provider``.
        """
        self._store      = store
        self._window_key = window_key
        self._window     = window
        self._watchlist  = watchlist
        self._provider_fns = provider_fns
        self._live_for_domain = live_providers_for_domain

    async def run(self) -> None:
        """Walk every (ticker, domain) and fetch — skipping completed rows."""
        for ticker in self._watchlist:
            for domain, fn in self._provider_fns.items():
                if self._already_ok(ticker, domain):
                    logger.info("skip %s/%s — already cached", ticker, domain)
                    continue
                await self._fetch_one(ticker, domain, fn)

    def _already_ok(self, ticker: str, domain: str) -> bool:
        """Return True iff a prior fetch row exists with ``status='ok'``."""
        with Session(self._store._engine) as s:
            row = s.execute(
                select(CacheRunRow)
                .where(CacheRunRow.window_key == self._window_key,
                       CacheRunRow.ticker     == ticker,
                       CacheRunRow.domain     == domain,
                       CacheRunRow.status     == "ok")
            ).scalar_one_or_none()
            return row is not None

    async def _fetch_one(self, ticker: str, domain: str, fn) -> None:
        """Fetch + persist one (ticker, domain) combo; record audit row."""
        started = datetime.now(tz=UTC)
        run_id  = uuid.uuid4().hex
        status  = "ok"
        error: str | None = None
        rows_written = 0

        try:
            results = await fn(
                ticker,
                start=self._window.start,
                end=self._window.end,
            )
            writer_name = _WRITER_BY_DOMAIN[domain]
            getattr(self._store, writer_name)(ticker, results)
            rows_written = len(results) if hasattr(results, "__len__") else 0
        except Exception as exc:
            status, error = "error", repr(exc)
            logger.exception("fetch failed for %s/%s", ticker, domain)

        with Session(self._store._engine) as s:
            s.add(CacheRunRow(
                run_id=run_id, started_at=started,
                finished_at=datetime.now(tz=UTC),
                window_key=self._window_key, ticker=ticker, domain=domain,
                source_provider=self._live_for_domain[domain],
                rows_written=rows_written, status=status, error=error or "",
            ))
            s.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_fetcher_idempotent.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/cache/fetcher.py tests/integration/backtest/__init__.py tests/integration/backtest/test_fetcher_idempotent.py
git commit -m "feat(backtest): add idempotent cache fetcher"
```

### Task E2: `scripts/backtest_fetch.py` CLI

**Files:**
- Create: `scripts/__init__.py` if not present
- Create: `scripts/backtest_fetch.py`

- [ ] **Step 1: Verify scripts package**

```bash
test -f scripts/__init__.py || touch scripts/__init__.py
```

- [ ] **Step 2: Implement the CLI**

`scripts/backtest_fetch.py`:

```python
"""CLI: fill the backtest cache for one window × the configured watchlist.

Usage:
    PYTHONPATH=src python -m scripts.backtest_fetch --window svb-stress-2023-03
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from backtest.cache.fetcher import Fetcher
from backtest.cache.store import CachedDataStore
from backtest.windows import load_windows
from data import (
    get_company_filings, get_insider_trades, get_public_figure_trades,
    get_notable_holders, get_stock_news, get_stock_stats,
)
from data.config import get_config

# OHLCV is fetched via yfinance directly through a thin helper — analysts
# do not have a wrapper for "historical bars over a range", so this stays
# scoped to the fetcher.


def _build_provider_fns() -> dict:
    """Return the domain → live-provider fetch function map for the fetcher."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")

    async def _ohlcv(ticker, *, start, end):
        """Fetch daily bars in ``[start, end]`` via yfinance.

        Lives inline because no analyst wrapper covers a date-range bar fetch.
        """
        import yfinance as yf
        from data.models import OHLCBar

        df = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False)
        bars = []
        for d, row in df.iterrows():
            bars.append(OHLCBar(
                ticker=ticker, date=d.date(),
                open=float(row["Open"]),   high=float(row["High"]),
                low=float(row["Low"]),     close=float(row["Close"]),
                volume=int(row["Volume"]), adj_close=float(row["Adj Close"]),
            ))
        return bars

    async def _market_meta(ticker, *, start, end):
        """One stats snapshot at the window end — fundamentals move slowly."""
        from datetime import datetime, time
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        snap = await get_stock_stats(ticker, as_of=as_of)
        return [(snap, end)] if snap is not None else []

    async def _news(ticker, *, start, end):
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        return await get_stock_news(ticker, as_of=as_of)

    async def _filings(ticker, *, start, end):
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        return await get_company_filings(ticker, as_of=as_of)

    async def _insider(ticker, *, start, end):
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        return await get_insider_trades(ticker, as_of=as_of)

    async def _politician(ticker, *, start, end):
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        return await get_public_figure_trades(ticker, as_of=as_of)

    async def _holders(ticker, *, start, end):
        as_of = datetime.combine(end, time(16, 0), tzinfo=NY)
        return await get_notable_holders(ticker, as_of=as_of)

    return {
        "ohlcv":             _ohlcv,
        "market_meta":       _market_meta,
        "news":              _news,
        "filings":           _filings,
        "insider_trades":    _insider,
        "politician_trades": _politician,
        "notable_holders":   _holders,
    }


async def _main_async(args: argparse.Namespace) -> None:
    """Resolve config, build the fetcher, run."""
    settings = json.loads(Path("config/backtest_settings.json").read_text())
    watchlist = json.loads(Path("config/watchlist.json").read_text())["tickers"]

    windows = load_windows(Path("config/backtest_windows.json"))
    window  = windows[args.window]

    store = CachedDataStore(Path(settings["cache_path"]))
    live_for_domain = {**get_config().providers, "ohlcv": "yfinance",
                       "market_meta": get_config().providers["stats"]}

    fetcher = Fetcher(
        store=store, window_key=args.window, window=window,
        watchlist=watchlist,
        provider_fns=_build_provider_fns(),
        live_providers_for_domain=live_for_domain,
    )
    await fetcher.run()


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Fill the backtest cache.")
    parser.add_argument("--window", required=True,
                        help="window key in config/backtest_windows.json")
    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the CLI (skips on no network)**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window svb-stress-2023-03
```

Expected: cache file appears at `backtests/cache/store.sqlite` with rows for each (ticker, domain).  If the network is unavailable, the cache_runs table records the errors and the CLI exits cleanly.

- [ ] **Step 4: Commit**

```bash
git add scripts/__init__.py scripts/backtest_fetch.py
git commit -m "feat(backtest): add backtest_fetch CLI to fill the cache for a window"
```

---

## Phase F — Decision logger

### Task F1: DecisionLogger — failing test first

**Files:**
- Create: `tests/unit/backtest/test_decision_logger.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests that DecisionLogger writes one JSON file per executed Fill."""
from __future__ import annotations

import json
from pathlib import Path

from backtest.decision_logger import DecisionLogger


def test_logs_one_file_per_filled_execution(tmp_path: Path) -> None:
    """Two filled executions in one tick produce two snapshot files."""
    logger = DecisionLogger(output_dir=tmp_path, window_key="svb-stress-2023-03")

    state = {
        "as_of": "2023-03-13T09:30:00-04:00",
        "tick_phase": "open",
        "tick_id": "tick-1",
        "executions": [
            {"order": {"ticker": "SIVB", "action": "SELL", "quantity": 120,
                        "est_price": 42.5},
             "status": "filled", "actual_price": 42.31, "actual_quantity": 120,
             "broker_order_id": "b1"},
            {"order": {"ticker": "AAPL", "action": "BUY", "quantity": 50,
                        "est_price": 150.0},
             "status": "filled", "actual_price": 150.10, "actual_quantity": 50,
             "broker_order_id": "b2"},
        ],
        "evidence_view": {"SIVB": {"technical": {}, "fundamental": {}}},
        "strategist_decision": {
            "ticker_stances": {"SIVB": {"action": "SELL"}, "AAPL": {"action": "BUY"}},
            "close_reasons":  {"SIVB": "thesis broken"},
        },
        "clamps": [],
    }

    logger.on_executions(state)

    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2
    assert any("SIVB__sell" in f for f in files)
    assert any("AAPL__buy"  in f for f in files)

    # One sample file is well-formed and contains the expected top-level keys.
    sample = json.loads((tmp_path / files[0]).read_text())
    for key in ("decision_id", "tick", "ticker", "side", "execution",
                "analyst_inputs", "analyst_outputs", "strategist_view",
                "strategist_decision", "risk_gate", "forward_returns"):
        assert key in sample, f"missing key: {key}"
    assert sample["forward_returns"] is None  # backfilled by reporting


def test_skips_rejected_executions(tmp_path: Path) -> None:
    """A rejected order does not produce a decision snapshot."""
    logger = DecisionLogger(output_dir=tmp_path, window_key="x")

    state = {
        "as_of": "2023-03-13T09:30:00-04:00", "tick_phase": "open",
        "tick_id": "tick-1",
        "executions": [{
            "order": {"ticker": "X", "action": "BUY", "quantity": 1,
                       "est_price": 1.0},
            "status": "rejected", "error": "insufficient funds",
        }],
        "evidence_view": {}, "strategist_decision": {}, "clamps": [],
    }

    logger.on_executions(state)

    assert list(tmp_path.glob("*.json")) == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_decision_logger.py -v
```

Expected: ImportError on `backtest.decision_logger`.

- [ ] **Step 3: Implement the logger**

`src/backtest/decision_logger.py`:

```python
"""Per-Fill decision snapshot writer.

Registered as a post-execution hook on the live pipeline.  Lives outside the
backtest-only path so the RAG-seed corpus also accumulates from live paper
trading once the bot is deployed.  Activated by setting
``state['_decision_logger']`` to a ``DecisionLogger`` instance; absent that
key the hook is a no-op (same posture as the trace writer).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _coerce(value: Any) -> Any:
    """Best-effort JSON-friendly coercion for Pydantic-or-dict mixed payloads."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


class DecisionLogger:
    """Writes one JSON snapshot per executed (non-rejected) order."""

    def __init__(self, output_dir: Path, window_key: str) -> None:
        """Initialise the writer.

        Parameters
        ----------
        output_dir:
            Directory to write ``<as_of>__<TICKER>__<side>.json`` files into.
        window_key:
            Era slug recorded in the per-decision ``tick.window_key`` field.
        """
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._window_key = window_key

    def on_executions(self, state: dict) -> None:
        """Walk ``state['executions']`` and write one file per filled order."""
        executions = state.get("executions", [])
        as_of   = state.get("as_of")
        phase   = state.get("tick_phase", "")

        for ex in executions:
            if ex.get("status") != "filled":
                continue

            order  = ex["order"]
            ticker = order["ticker"]
            side   = order["action"].lower()

            snapshot = self._build_snapshot(
                state, ex, ticker=ticker, side=side, as_of=as_of, phase=phase,
            )
            slug = _slug(as_of)
            outpath = self._dir / f"{slug}__{ticker}__{side}.json"

            try:
                outpath.write_text(json.dumps(snapshot, indent=2, default=str))
            except Exception:  # never let a logger failure abort the tick
                logger.exception("failed to write decision snapshot %s", outpath)

    def _build_snapshot(
        self, state: dict, ex: dict, *,
        ticker: str, side: str, as_of: Any, phase: str,
    ) -> dict:
        """Assemble one self-contained decision JSON object."""
        ev_view  = state.get("evidence_view", {}).get(ticker, {})
        decision = state.get("strategist_decision", {}) or {}
        stance   = (decision.get("ticker_stances") or {}).get(ticker, {})
        close_reason = (decision.get("close_reasons") or {}).get(ticker, "")
        clamps   = [c for c in state.get("clamps", []) if c.get("ticker") == ticker]

        return {
            "decision_id": f"{_slug(as_of)}__{ticker}__{side}",
            "tick":   {"as_of": str(as_of), "phase": phase,
                       "window_key": self._window_key,
                       "tick_id": state.get("tick_id")},
            "ticker": ticker,
            "side":   side,
            "execution": {
                "order_qty":   ex["order"]["quantity"],
                "fill_price":  ex.get("actual_price"),
                "fill_qty":    ex.get("actual_quantity"),
                "status":      ex.get("status"),
                "broker_order_id": ex.get("broker_order_id"),
                "slippage_bps":    ex.get("slippage_bps"),
            },
            "analyst_inputs": {
                "technical":   state.get("technical_data",   {}).get(ticker),
                "fundamental": state.get("fundamental_data", {}).get(ticker),
                "news":        state.get("news_data",        {}).get(ticker),
                "smart_money": state.get("smart_money_data", {}).get(ticker),
                "social":      state.get("social_data",      {}).get(ticker),
            },
            "analyst_outputs": _coerce(ev_view),
            "strategist_view": {
                "ticker_evidence":     _coerce(ev_view),
                "held_view_at_decision": _coerce(state.get("held_view", {}).get(ticker)),
            },
            "strategist_decision": {
                "stance":             _coerce(stance),
                "close_reason":       close_reason,
                "reasoning_excerpt":  decision.get("reasoning_excerpt", ""),
            },
            "risk_gate":     {"clamps": _coerce(clamps)},
            "forward_returns": None,
        }


def _slug(as_of: Any) -> str:
    """Filename-safe ISO timestamp slug."""
    return str(as_of).replace(":", "-").replace("+", "p").replace(" ", "T")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_decision_logger.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/decision_logger.py tests/unit/backtest/test_decision_logger.py
git commit -m "feat(backtest): add per-Fill DecisionLogger snapshot writer"
```

### Task F2: Wire the logger into the Executor

**Files:**
- Modify: `src/agents/executor/agent.py`

- [ ] **Step 1: Update the executor end-of-run**

Append the following at the end of `_run_async_impl`, after the `_trace_maybe` call:

```python
        # Decision-snapshot hook — no-op in live runs that do not set
        # ``state["_decision_logger"]``.  Backtest runner installs one per run.
        dl = state.get("_decision_logger")
        if dl is not None:
            try:
                dl.on_executions(dict(state))
            except Exception:  # defensive: logger must never abort the tick
                pass
```

- [ ] **Step 2: Add a regression test**

Create `tests/unit/agents/test_executor_decision_hook.py`:

```python
"""The executor invokes a registered ``DecisionLogger`` after submitting orders."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.state import Order


@pytest.mark.asyncio
async def test_executor_calls_decision_logger_on_each_fill(tmp_path) -> None:
    """Filled orders fan out to ``state['_decision_logger'].on_executions``."""
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    agent  = ExecutorAgent(broker=broker, db_session=None)
    fake_logger = MagicMock()

    state = {
        "tick_id": "t1",
        "final_orders": [
            Order(ticker="AAPL", action="BUY", quantity=1, est_price=150.0)
                 .model_dump(),
        ],
        "positions": {},
        "strategist_decision": {"new_positions": {"AAPL": {
            "opened_price": 150.0, "horizon": "swing",
            "rationale": "test", "opened_tag": "test",
            "opened_at": "2023-03-13T09:30:00+00:00",
        }}},
        "_decision_logger": fake_logger,
    }
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))

    async for _ in agent._run_async_impl(ctx):
        pass

    fake_logger.on_executions.assert_called_once()
```

- [ ] **Step 3: Run the new test + the existing executor tests**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/test_executor_decision_hook.py tests/agents/test_executor*.py -v
```

Expected: new test passes; existing executor tests still pass (live behaviour preserved by the `dl is None` guard).

- [ ] **Step 4: Commit**

```bash
git add src/agents/executor/agent.py tests/unit/agents/test_executor_decision_hook.py
git commit -m "feat(executor): invoke registered DecisionLogger after each tick"
```

---

## Phase G — Driver + Runner + run CLI

### Task G1: Driver — failing test first

**Files:**
- Create: `tests/integration/backtest/test_driver_one_tick.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests that the driver runs one tick end-to-end against a hand-populated cache."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.driver  import Driver
from backtest.schedule import Tick
from backtest.providers import _store_handle
from broker.fake import FakeBroker
from data.models import OHLCBar


@pytest.fixture
def cache(tmp_path: Path) -> CachedDataStore:
    """Cache pre-populated with one ticker, one bar."""
    store = CachedDataStore(tmp_path / "cache.sqlite")
    store.write_ohlcv("AAPL", [
        OHLCBar(ticker="AAPL", date=date(2023, 3, 13), open=150, high=152,
                low=149, close=151, volume=1_000, adj_close=151),
    ])
    _store_handle.set_store(store)
    yield store
    _store_handle.clear_store()


@pytest.mark.asyncio
async def test_driver_produces_one_trace_file(tmp_path: Path, cache) -> None:
    """One scheduled tick → one trace file under ``<run>/traces/``."""
    from agents.executor.agent import ExecutorAgent  # smoke-import

    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 150.0})
    driver = Driver(
        broker=broker,
        run_dir=tmp_path,
        window_key="test",
    )
    schedule = [Tick(
        as_of=datetime(2023, 3, 13, 9, 30, tzinfo=UTC), phase="open",
    )]

    state = {"tickers": ["AAPL"], "watchlist": ["AAPL"]}
    await driver.run(state, schedule)

    traces = list((tmp_path / "traces").glob("*.json"))
    assert len(traces) == 1
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_driver_one_tick.py -v
```

Expected: ImportError on `backtest.driver`.

- [ ] **Step 3: Implement the driver**

`src/backtest/driver.py`:

```python
"""Tick loop driver — runs the unchanged live pipeline once per scheduled tick.

The driver is deliberately thin: pre-tick setup (compute ``as_of``, attach a
fresh ``TraceWriter``), call ``pipeline.run_async(state)``, post-tick flush
the trace.  Mid-tick failures are caught, recorded in the manifest, and the
driver advances to the next tick unless the configured failure ratio is
exceeded.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from google.adk.runners import InMemoryRunner

from backtest.schedule import Tick
from observability.trace import TraceWriter
from orchestrator.pipeline import build_pipeline

logger = logging.getLogger(__name__)


class Driver:
    """Loop over scheduled ticks and invoke the live pipeline for each."""

    def __init__(
        self,
        *,
        broker,
        run_id: str,
        run_dir: Path,
        window_key: str,
        db_session: Any = None,
        decision_logger: Any = None,
        failure_abort_ratio: float = 0.10,
    ) -> None:
        """Wire the driver.  ``run_dir`` should already exist."""
        self._broker      = broker
        self._run_id      = run_id
        self._run_dir     = Path(run_dir)
        self._window_key  = window_key
        self._db_session  = db_session
        self._dl          = decision_logger
        self._ratio       = failure_abort_ratio
        self._traces_dir  = self._run_dir / "traces"
        self._traces_dir.mkdir(parents=True, exist_ok=True)

        self._pipeline    = build_pipeline(broker, db_session)
        self._failed:  list[dict] = []
        self._total:   int = 0

    async def run(self, state: dict, schedule: list[Tick]) -> None:
        """Execute every tick in ``schedule``, mutating ``state`` in place."""
        for tick in schedule:
            self._total += 1
            tw = TraceWriter()
            state["_trace"]           = tw
            state["as_of"]            = tick.as_of
            state["tick_phase"]       = tick.phase
            # Deterministic tick_id: per-run DB means no cross-run collision risk,
            # so a stable composite of (run_id, as_of, phase) lets reruns of the
            # same window emit comparable trace files and decision logs.
            state["tick_id"]          = f"{self._run_id}-{tick.as_of.isoformat()}-{tick.phase}"
            state["_decision_logger"] = self._dl

            # Update FakeBroker price to the day's open or close.
            self._refresh_broker_prices(state["watchlist"], tick)

            try:
                await self._run_one_tick(state)
            except Exception as exc:
                logger.exception("tick %s failed", tick.as_of)
                self._failed.append({
                    "as_of": str(tick.as_of),
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                })
                if self._total > 0 and len(self._failed) / self._total > self._ratio:
                    self._write_manifest_status("aborted")
                    raise RuntimeError(
                        f"failed-tick ratio {len(self._failed)}/{self._total}"
                        f" exceeded threshold {self._ratio}",
                    ) from exc

            tw.finalise(self._traces_dir / f"{_slug(tick.as_of)}.json")

        self._write_manifest_status(
            "completed_with_failures" if self._failed else "completed",
        )

    async def _run_one_tick(self, state: dict) -> None:
        """Drive the pipeline once via ADK's InMemoryRunner."""
        runner = InMemoryRunner(agent=self._pipeline)
        session = await runner.session_service.create_session(
            app_name="backtest", user_id="backtest",
            session_id=state["tick_id"], state=state,
        )
        async for _ in runner.run_async(
            user_id="backtest", session_id=session.id, new_message=None,
        ):
            pass

        # Pull session state back into ``state`` so the next tick sees positions etc.
        latest = await runner.session_service.get_session(
            app_name="backtest", user_id="backtest", session_id=session.id,
        )
        state.update(dict(latest.state))

    def _refresh_broker_prices(self, tickers: list[str], tick: Tick) -> None:
        """Set FakeBroker prices to the day's open or close from the cache."""
        from backtest.providers._store_handle import get_store
        store = get_store()
        for t in tickers:
            bars = store.read_ohlcv(t, tick.as_of.date(), tick.as_of.date())
            if not bars:
                continue
            bar = bars[0]
            self._broker.set_price(t, bar.open if tick.phase == "open" else bar.close)

    def _write_manifest_status(self, status: str) -> None:
        """Patch ``manifest.json`` with current status + failed-ticks list."""
        path = self._run_dir / "manifest.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        manifest["status"]        = status
        manifest["failed_ticks"]  = self._failed
        manifest["ticks_total"]   = self._total
        manifest["ticks_failed"]  = len(self._failed)
        path.write_text(json.dumps(manifest, indent=2, default=str))


def _slug(as_of) -> str:
    """Filename-safe ISO timestamp slug."""
    return str(as_of).replace(":", "-").replace("+", "p").replace(" ", "T")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_driver_one_tick.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/driver.py tests/integration/backtest/test_driver_one_tick.py
git commit -m "feat(backtest): add tick-loop driver around the unchanged live pipeline"
```

### Task G2: Failure threshold test

**Files:**
- Create: `tests/integration/backtest/test_driver_failure_threshold.py`

- [ ] **Step 1: Write the test**

```python
"""Tests that the driver aborts past the configured failed-tick ratio."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backtest.driver  import Driver
from backtest.schedule import Tick
from broker.fake import FakeBroker


@pytest.mark.asyncio
async def test_aborts_above_threshold(tmp_path: Path) -> None:
    """If more than 10% of ticks fail, the driver raises and writes status='aborted'."""
    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 1.0})

    # Pre-populate manifest so the writer has something to patch.
    (tmp_path / "manifest.json").write_text("{}")

    driver = Driver(
        broker=broker, run_dir=tmp_path, window_key="t",
        failure_abort_ratio=0.10,
    )

    schedule = [
        Tick(as_of=datetime(2023, 3, d, tzinfo=UTC), phase="open")
        for d in range(6, 16)   # 10 ticks
    ]

    with patch.object(driver, "_run_one_tick",
                      new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="exceeded threshold"):
            await driver.run({"watchlist": [], "tickers": []}, schedule)

    import json
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
```

- [ ] **Step 2: Run to verify it passes**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_driver_failure_threshold.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/backtest/test_driver_failure_threshold.py
git commit -m "test(backtest): driver aborts past the failed-tick threshold"
```

### Task G3: Runner — one full run

**Files:**
- Create: `src/backtest/runner.py`

- [ ] **Step 1: Implement the runner**

```python
"""End-to-end run orchestrator: window + watchlist → cache wiring → driver."""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backtest.cache.store import CachedDataStore
from backtest.decision_logger import DecisionLogger
from backtest.driver          import Driver
from backtest.providers       import _store_handle
# Importing the cache provider modules triggers their @register decorators.
from backtest.providers import (
    filings_cache, insider_trades_cache, news_cache, notable_holders_cache,
    politician_trades_cache, social_sentiment_cache, stats_cache,  # noqa: F401
)
from backtest.schedule import generate_ticks
from backtest.windows  import load_windows
from broker.fake import FakeBroker
from data import registry
from data.registry import DOMAINS
from orchestrator.persistence import create_all, make_engine

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Summary of one backtest run, returned to the CLI."""

    run_id:   str
    run_dir:  Path
    status:   str


class Runner:
    """One end-to-end backtest run."""

    def __init__(self, *, settings_path: Path = Path("config/backtest_settings.json"),
                 windows_path:  Path = Path("config/backtest_windows.json"),
                 watchlist_path: Path = Path("config/watchlist.json")) -> None:
        """Load config files; defer actual run setup to ``.run()``."""
        self._settings  = json.loads(settings_path.read_text())
        self._windows   = load_windows(windows_path)
        self._watchlist = json.loads(watchlist_path.read_text())["tickers"]

    def run(self, window_key: str, watchlist: list[str] | None = None) -> RunResult:
        """Materialise the run, drive every tick, return a ``RunResult``."""
        import asyncio
        return asyncio.run(self._run_async(window_key, watchlist))

    async def _run_async(self, window_key: str,
                         watchlist: list[str] | None) -> RunResult:
        window  = self._windows[window_key]
        wl      = watchlist or self._watchlist
        run_id  = f"{window_key}-{_git_sha7()}"
        run_dir = Path(self._settings["runs_root"]) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        store   = CachedDataStore(Path(self._settings["cache_path"]))
        _store_handle.set_store(store)

        # Drop tickers with no bars in the window.
        skipped: list[str] = []
        wl_filtered: list[str] = []
        for t in wl:
            if store.read_ohlcv(t, window.start, window.end):
                wl_filtered.append(t)
            else:
                skipped.append(t)

        # Swap every domain to its cache provider.  Capture the restore
        # callables so a crashed run does not leak state into a later test
        # or live invocation.
        restores: list = []
        for domain in DOMAINS:
            restores.append(registry.set_active_provider(domain, "cache"))

        broker = FakeBroker(
            starting_cash=self._settings["fake_broker_starting_cash"],
            prices={t: 0.0 for t in wl_filtered},
        )
        engine = make_engine(f"sqlite:///{run_dir / 'db.sqlite'}")
        create_all(engine)
        from sqlalchemy.orm import sessionmaker
        Session    = sessionmaker(bind=engine)
        db_session = Session()

        dl = DecisionLogger(
            output_dir=run_dir / "decisions", window_key=window_key,
        )

        manifest = {
            "run_id":       run_id,
            "window_key":   window_key,
            "window":       {"start": str(window.start), "end": str(window.end)},
            "watchlist":    wl_filtered,
            "skipped_tickers": skipped,
            "git_sha":      _git_sha_full(),
            "started_at":   datetime.now(tz=UTC).isoformat(),
            "status":       "running",
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        driver = Driver(
            broker=broker, run_id=run_id, run_dir=run_dir, window_key=window_key,
            db_session=db_session, decision_logger=dl,
            failure_abort_ratio=self._settings["failed_tick_abort_ratio"],
        )
        schedule = generate_ticks(window.start, window.end)
        state    = {"tickers": wl_filtered, "watchlist": wl_filtered}

        status = "completed"
        try:
            await driver.run(state, schedule)
        except RuntimeError as exc:
            logger.error("run aborted: %s", exc)
            status = "aborted"
        finally:
            for r in restores:
                r()
            _store_handle.clear_store()

        # Re-read manifest (driver wrote the status); add finished_at.
        manifest = json.loads((run_dir / "manifest.json").read_text())
        manifest["finished_at"] = datetime.now(tz=UTC).isoformat()
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        return RunResult(run_id=run_id, run_dir=run_dir,
                         status=manifest.get("status", status))


def _git_sha7() -> str:
    """Return the short git SHA for the current HEAD; ``unknown`` on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _git_sha_full() -> str:
    """Return the full git SHA for the current HEAD; ``unknown`` on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"
```

- [ ] **Step 2: Commit**

```bash
git add src/backtest/runner.py
git commit -m "feat(backtest): add Runner — full run orchestration around Driver"
```

### Task G4: `scripts/backtest_run.py` CLI

**Files:**
- Create: `scripts/backtest_run.py`

- [ ] **Step 1: Implement the CLI**

```python
"""CLI: drive one full backtest run for a configured window.

Usage:
    PYTHONPATH=src python -m scripts.backtest_run --window svb-stress-2023-03
"""
from __future__ import annotations

import argparse
import logging
import sys

from backtest.runner import Runner


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run one backtest window.")
    parser.add_argument("--window", required=True,
                        help="window key in config/backtest_windows.json")
    args = parser.parse_args()

    result = Runner().run(args.window)

    print(f"run_id:  {result.run_id}")
    print(f"run_dir: {result.run_dir}")
    print(f"status:  {result.status}")

    if result.status == "aborted":
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/backtest_run.py
git commit -m "feat(backtest): add backtest_run CLI"
```

---

## Phase H — Reporting (equity curve, metrics, forward-return backfill)

### Task H1: Equity curve + metrics

**Files:**
- Create: `src/backtest/reporting.py`

- [ ] **Step 1: Implement the reporting module**

```python
"""End-of-window reporting: equity curve, metrics, forward-return backfill.

Reads ``PortfolioSnapshotRow`` from the run's ``db.sqlite`` and produces
``equity_curve.png`` and ``metrics.md``.  The forward-return backfill walks
``decisions/*.json`` and patches each file in place with +1d / +5d / +20d
returns from the cache — the supervision signal a future RAG retriever wants.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering for CI / nightly cron
import matplotlib.pyplot as plt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backtest.cache.store import CachedDataStore
from orchestrator.persistence import PortfolioSnapshotRow

logger = logging.getLogger(__name__)


def report(run_dir: Path, settings: dict) -> None:
    """Generate ``report/equity_curve.png`` + ``report/metrics.md``; backfill forwards."""
    run_dir = Path(run_dir)
    report_dir = run_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{run_dir / 'db.sqlite'}", future=True)
    with Session(engine) as s:
        rows = s.execute(
            select(PortfolioSnapshotRow)
            .order_by(PortfolioSnapshotRow.taken_at),
        ).scalars().all()
        equity = [(r.taken_at, float(r.total_value)) for r in rows]

    if not equity:
        logger.warning("no portfolio snapshots in %s — skipping report", run_dir)
        return

    _write_equity_curve(equity, report_dir / "equity_curve.png")
    _write_metrics(equity, report_dir / "metrics.md")

    cache = CachedDataStore(Path(settings["cache_path"]))
    horizons = settings["forward_return_horizons_days"]
    _backfill_forward_returns(run_dir / "decisions", cache, horizons)


def _write_equity_curve(series: list[tuple[datetime, float]], outpath: Path) -> None:
    """Render a single-series equity curve PNG."""
    xs = [t for t, _ in series]
    ys = [v for _, v in series]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(xs, ys, label="Portfolio")
    ax.set_xlabel("Time")
    ax.set_ylabel("Portfolio value ($)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath)
    plt.close(fig)


def _write_metrics(series: list[tuple[datetime, float]], outpath: Path) -> None:
    """Compute and write Markdown metrics: total return, Sharpe, max DD."""
    import statistics

    start_v = series[0][1]
    end_v   = series[-1][1]
    total_return = (end_v - start_v) / start_v

    # Daily returns (assumes ticks evenly spaced; first-cut approximation).
    rets = []
    for (_, v0), (_, v1) in zip(series, series[1:]):
        if v0 != 0:
            rets.append((v1 - v0) / v0)

    if len(rets) >= 2 and statistics.pstdev(rets) > 0:
        sharpe = (statistics.mean(rets) / statistics.pstdev(rets)) * (252 ** 0.5)
    else:
        sharpe = float("nan")

    peak = series[0][1]
    max_dd = 0.0
    for _, v in series:
        peak = max(peak, v)
        if peak > 0:
            max_dd = min(max_dd, (v - peak) / peak)

    outpath.write_text(
        "# Backtest metrics\n\n"
        f"- Total return: **{total_return:+.2%}**\n"
        f"- Sharpe (annualised, 252d): **{sharpe:.2f}**\n"
        f"- Max drawdown: **{max_dd:+.2%}**\n"
        f"- Ticks recorded: **{len(series)}**\n"
    )


def _backfill_forward_returns(
    decisions_dir: Path, cache: CachedDataStore, horizons_days: list[int],
) -> None:
    """For each decision JSON, patch ``forward_returns`` using cached OHLCV."""
    if not decisions_dir.exists():
        return

    for path in decisions_dir.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text())
            ticker      = snapshot["ticker"]
            entry_price = snapshot["execution"].get("fill_price")
            tick_as_of  = snapshot["tick"].get("as_of")
            if entry_price is None or tick_as_of is None:
                continue
            entry_date = _parse_date(tick_as_of)

            forwards: dict[str, float | None] = {}
            for h in horizons_days:
                target = entry_date + timedelta(days=h)
                bars = cache.read_ohlcv(ticker, target, target + timedelta(days=4))
                if not bars:
                    forwards[f"+{h}d"] = None
                    continue
                forwards[f"+{h}d"] = (bars[0].close - entry_price) / entry_price

            snapshot["forward_returns"] = forwards
            path.write_text(json.dumps(snapshot, indent=2, default=str))
        except Exception:
            logger.exception("forward-return backfill failed for %s", path)


def _parse_date(as_of: str) -> date:
    """Best-effort parse of the snapshot's ISO datetime string."""
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).date()
```

- [ ] **Step 2: Test the metrics computation**

Create `tests/unit/backtest/test_reporting.py`:

```python
"""Tests for end-of-window metrics computation."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from backtest.reporting import _write_metrics


def test_metrics_compute_total_return(tmp_path: Path) -> None:
    """Total return is (end - start) / start."""
    series = [
        (datetime(2023, 3, 6,  tzinfo=UTC), 100_000.0),
        (datetime(2023, 3, 7,  tzinfo=UTC), 105_000.0),
    ]
    _write_metrics(series, tmp_path / "metrics.md")

    text = (tmp_path / "metrics.md").read_text()
    assert "+5.00%" in text
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/test_reporting.py -v
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add src/backtest/reporting.py tests/unit/backtest/test_reporting.py
git commit -m "feat(backtest): add equity curve + metrics + forward-return backfill"
```

### Task H2: `scripts/backtest_report.py` CLI

**Files:**
- Create: `scripts/backtest_report.py`

- [ ] **Step 1: Implement the CLI**

```python
"""CLI: regenerate the report for an existing run directory.

Usage:
    PYTHONPATH=src python -m scripts.backtest_report --run-id svb-stress-2023-03-abc1234
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from backtest.reporting import report


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Regenerate a backtest report.")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    settings = json.loads(Path("config/backtest_settings.json").read_text())
    run_dir  = Path(settings["runs_root"]) / args.run_id

    report(run_dir, settings)
    print(f"report written under {run_dir / 'report'}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/backtest_report.py
git commit -m "feat(backtest): add backtest_report CLI for regenerating reports"
```

### Task H3: Wire reporting into the Runner

**Files:**
- Modify: `src/backtest/runner.py`

- [ ] **Step 1: Patch `_run_async`**

After the manifest is re-read and `finished_at` is written, add:

```python
        # Generate the report unconditionally — if the run aborted, the report
        # still tells us what *did* happen up to the abort point.
        try:
            from backtest.reporting import report
            report(run_dir, self._settings)
        except Exception:
            logger.exception("report generation failed for %s", run_id)
```

- [ ] **Step 2: Commit**

```bash
git add src/backtest/runner.py
git commit -m "feat(backtest): generate report at end of every run (including aborts)"
```

---

## Phase I — End-to-end smoke test

### Task I1: Tiny-fixture end-to-end run

**Files:**
- Create: `tests/integration/backtest/test_end_to_end_smoke.py`

- [ ] **Step 1: Write the test**

```python
"""Smoke test: full Runner over a 3-day micro-window against a fixture cache.

Marked ``@pytest.mark.slow`` so it runs in nightly CI only.  The point is to
exercise the entire stack end-to-end — cache providers, ``as_of`` migration,
analyst fetch, pipeline, FakeBroker, DecisionLogger, reporting — against a
deterministic data set with no network or LLM dependency.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.runner       import Runner
from data.models           import OHLCBar, StockStats


@pytest.fixture
def fixture_cache(tmp_path: Path) -> Path:
    """Materialise a 3-business-day cache for a single ticker."""
    cache_path = tmp_path / "cache" / "store.sqlite"
    store = CachedDataStore(cache_path)
    for d in (date(2023, 3, 13), date(2023, 3, 14), date(2023, 3, 15)):
        store.write_ohlcv("AAPL", [
            OHLCBar(ticker="AAPL", date=d, open=150.0, high=151.0,
                    low=149.0, close=150.5, volume=1_000, adj_close=150.5),
        ])
    store.write_market_meta("AAPL", StockStats(
        ticker="AAPL", market_cap=2_500_000_000_000, trailing_pe=28.0,
        forward_pe=26.0, beta=1.2, dividend_yield=0.005,
        ma_50=148.0, ma_200=145.0, sector="Technology", long_name="Apple Inc.",
    ), as_of_date=date(2023, 3, 10))
    return cache_path


@pytest.mark.slow
def test_end_to_end_run_produces_full_artefact_tree(
    tmp_path: Path, fixture_cache: Path, monkeypatch,
) -> None:
    """One Runner.run() produces manifest, traces, decisions, equity curve, metrics."""
    # Write a settings file pointing at the fixture cache + tmp runs root.
    settings = {
        "cache_path": str(fixture_cache),
        "runs_root":  str(tmp_path / "runs"),
        "ticks_per_day": ["open", "close"], "tz": "America/New_York",
        "open_time": "09:30", "close_time": "16:00",
        "failed_tick_abort_ratio": 1.0,   # never abort in the smoke test
        "fake_broker_starting_cash": 100_000.0,
        "forward_return_horizons_days": [1],
        "default_lookback_days": {
            "news": 30, "insider_trades": 90, "politician_trades": 90,
            "notable_holders": 365, "filings": 365,
        },
    }
    settings_path = tmp_path / "backtest_settings.json"
    settings_path.write_text(json.dumps(settings))

    windows = {"smoke": {"start": "2023-03-13", "end": "2023-03-15", "notes": ""}}
    windows_path = tmp_path / "backtest_windows.json"
    windows_path.write_text(json.dumps(windows))

    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    runner = Runner(
        settings_path=settings_path, windows_path=windows_path,
        watchlist_path=watchlist_path,
    )
    result = runner.run("smoke")

    assert result.status in {"completed", "completed_with_failures"}
    assert (result.run_dir / "manifest.json").exists()
    assert (result.run_dir / "traces").exists()
    assert (result.run_dir / "report" / "metrics.md").exists()
    assert (result.run_dir / "report" / "equity_curve.png").exists()
```

- [ ] **Step 2: Run the smoke test (marked slow — opt-in)**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```

Expected: 1 passed.  If the test fails because the strategist LLM is being invoked, set `STOCKBOT_LLM_MOCK=1` (or whatever pattern the codebase uses to mock the LLM in tests) and re-run.  Diagnosing this is part of the smoke test — surfacing the LLM-mocking gap is half the point of the test.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/backtest/test_end_to_end_smoke.py
git commit -m "test(backtest): add nightly end-to-end smoke test (slow)"
```

### Task I2: Update `graph_delta.md` and CLAUDE.md context

**Files:**
- Modify: `graphify-out/graph_delta.md` (local-only — never `git add`)
- Modify: `.claude/CLAUDE.md` if helpful

- [ ] **Step 1: Append a graph-delta entry**

Add a dated section under `## YYYY-MM-DD — Backtest harness v1` summarising the new `src/backtest/` subtree, the cache providers as a new `cache` upstream, and the `as_of` migration across `src/data/__init__.py`, `src/data/aggregator.py`, and the five analyst fetch callbacks.

This is local-only — **do not** `git add` `graphify-out/`.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Covered by |
|---|---|
| Cache schema (§ Cache schema) | Task B1 |
| CachedDataStore (§ Cache store) | Task B2 |
| `as_of` migration (§ Architecture overview point 3) | Tasks C1–C3 |
| Cache providers (§ Cache providers) | Tasks D1–D2 |
| Fetcher (§ Fetcher) | Tasks E1–E2 |
| Era window config (§ Era window config) | Task A1 |
| Driver / Runner (§ Driver / Runner) | Tasks G1–G4 |
| Decision logger (§ Decision logger) | Tasks F1–F2 |
| Reporting + forward-return backfill (§ Data flow — end of window) | Tasks H1–H3 |
| Testing strategy (§ Testing strategy) | Tier 1 in A3/A4/B2/C/D2/F1/H1; Tier 2 in E1, G1, G2; Tier 3 in I1 |
| Error handling — failed-tick threshold (§ Error handling) | Task G2 |
| Backlog items (§ Backlog) | Already appended in spec PR a188680 |

**2. Placeholder scan:** every code step contains real code; no `TBD`, no "handle appropriately", no "similar to Task N".  The only soft spots are the `Pydantic model field names` note in B2 step 4 and the LLM-mocking hint in I1 step 2 — both are explicit "if X, do Y" instructions, not placeholders.

**3. Type consistency:**

- `as_of: datetime` is consistently a UTC-or-NY-tz-aware datetime everywhere except the cache's `as_of_date: date` columns (deliberate — daily snapshots have day-granularity).
- `Tick` dataclass is `(as_of: datetime, phase: Literal["open", "close"])` everywhere.
- `Window` Pydantic model uses ISO dates throughout.
- `set_active_provider(domain, name)` returns a `Callable[[], None]` restore handle — used in Runner G3 as `restores.append(...)` and unwound in `finally`.
- `DecisionLogger.on_executions(state)` signature is identical at the executor wire-up (F2) and the test (F1).
- Provider `register(...)` decorator everywhere uses keyword-only `upstream`, `rate_per_minute`, `burst` — matches the live `src/data/registry.py` signature read in pre-work.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/backtest-harness-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

Which approach?





