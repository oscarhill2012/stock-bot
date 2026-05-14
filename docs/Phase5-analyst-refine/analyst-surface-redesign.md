# Analyst Surface Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop wasting input on Fundamental, recover LLM analyst cognition via a structured `report` field, cap LLM cost across ticks with a hash-cache, fix the surface-trace fidelity bug, and slow tick cadence to 2/day (DST-aware).

**Architecture:** Additive layered refactor. Split `StockStats` → `PriceHistory` + `CompanyRatios` (no behaviour change). Add `AnalystReport` alongside `AnalystVerdict`. Wrap LLM analysts with a disk JSON memoisation cache keyed on a blake2b hash of inputs + a prompt-version fingerprint. Refactor the duplicated `_make_llm_trace_*` helpers into one shared utility in `observability.trace` that captures both system instruction *and* user contents. Tick cadence moves from hardcoded to `config/schedule.json` with ET-keyed times.

**Tech Stack:** Python 3.12, Pydantic v2, Google ADK (`LlmAgent`, `CallbackContext`, `LlmRequest`, `LlmResponse`), pytest, blake2b (stdlib `hashlib`), yfinance.

**Reference spec:** `docs/superpowers/specs/analyst-surface-redesign-design.md` — read this first for the *why*. This plan covers the *how*.

**Cross-cutting conventions** (per `.claude/CLAUDE.md` and global `~/.claude/CLAUDE.md`):
- Comment non-trivial logic. Every function gets a docstring describing purpose, parameters, return value.
- British English in identifiers, comments, docs, prose (e.g. `colour`, `behaviour`, `normalise`, `optimise`).
- Whitespace for legibility — blank lines separating logical blocks.
- Bash invocations: run commands directly from project root; do **not** prepend `cd "/home/oscarhill2012/Documents/Repository/StockBot" && ...`.
- After any non-trivial code change, append a dated entry to `graphify-out/graph_delta.md`. **Never `git add` or commit anything under `graphify-out/`** — the directory is gitignored.

**Test runner** (used throughout):

```bash
.venv/bin/python -m pytest tests/path/to/test.py -v
.venv/bin/python -m ruff check src/ tests/
```

---

## Task 1: Data model split — `PriceHistory` + `CompanyRatios`

**Files:**
- Create: `src/data/models/price_history.py`
- Create: `src/data/models/company_ratios.py`
- Modify: `src/data/models/__init__.py` — add re-exports, drop `StockStats` from `__all__`
- Modify: `src/data/models/market.py` — keep `OHLCBar`; delete `StockStats`
- Modify: `src/data/models/bundle.py` — replace `stats: StockStats | None` field
- Modify: `src/data/providers/stats/yfinance.py` — split `_fetch_stats` into `_fetch_price_history` + `_fetch_company_ratios`; register two providers
- Modify: `src/data/__init__.py` — replace `get_stock_stats` with `get_price_history` + `get_company_ratios`
- Modify: `src/agents/analysts/technical/fetch.py` — call both new providers
- Modify: `src/agents/analysts/fundamental/fetch.py` — call only `get_company_ratios`
- Modify: `src/contract/extractors/technical.py` — adapt to new state shape
- Modify: `src/contract/extractors/fundamental.py` — read from `ratios` dict
- Test: `tests/unit/data/test_price_history.py` (new)
- Test: `tests/unit/data/test_company_ratios.py` (new)
- Test: `tests/unit/data/test_providers_split.py` (new)
- Test: `tests/unit/test_analyst_fetchers.py` — update mocks
- Test: `tests/unit/test_fundamental_fetch.py` — update mocks
- Test: `tests/unit/data/test_aggregator.py` — update model references

### Step 1.1: Write failing test for `PriceHistory`

- [ ] **Create the test file** `tests/unit/data/test_price_history.py`:

```python
"""Unit tests for the ``PriceHistory`` pydantic model."""
from __future__ import annotations

from datetime import datetime

import pytest

from data.models import OHLCBar
from data.models.price_history import PriceHistory


def _bar(ts: str, close: float) -> OHLCBar:
    """Build a minimal OHLCBar for testing."""
    return OHLCBar(
        timestamp=datetime.fromisoformat(ts),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def test_price_history_round_trips_through_model_dump() -> None:
    """A model_dump round-trip preserves ticker and bars."""
    ph = PriceHistory(
        ticker="AAPL",
        bars=[_bar("2026-05-01T00:00:00", 100.0), _bar("2026-05-02T00:00:00", 101.0)],
    )

    payload = ph.model_dump()
    assert payload["ticker"] == "AAPL"
    assert len(payload["bars"]) == 2

    restored = PriceHistory.model_validate(payload)
    assert restored == ph


def test_price_history_accepts_empty_bars() -> None:
    """An empty history is a valid state — e.g. an unknown ticker."""
    ph = PriceHistory(ticker="ZZZZ", bars=[])
    assert ph.bars == []
```

- [ ] **Run it to confirm it fails** (module doesn't exist yet):

```bash
.venv/bin/python -m pytest tests/unit/data/test_price_history.py -v
```
Expected: `ModuleNotFoundError: No module named 'data.models.price_history'`

### Step 1.2: Implement `PriceHistory`

- [ ] **Create** `src/data/models/price_history.py`:

```python
"""``PriceHistory`` — OHLCV bars for one ticker, ordered oldest -> newest."""
from __future__ import annotations

from pydantic import BaseModel

from .market import OHLCBar


class PriceHistory(BaseModel):
    """Daily OHLCV bars for one ticker, ordered oldest -> newest.

    Replaces the ``history`` field of the retired ``StockStats`` model. The
    Technical analyst is the only consumer.

    Parameters
    ----------
    ticker:
        Upper-cased symbol the bars belong to.
    bars:
        List of ``OHLCBar`` records. May be empty for tickers the provider
        has no coverage of.
    """

    ticker: str
    bars: list[OHLCBar]
```

- [ ] **Run the test — passes:**

```bash
.venv/bin/python -m pytest tests/unit/data/test_price_history.py -v
```

### Step 1.3: Write failing test for `CompanyRatios`

- [ ] **Create** `tests/unit/data/test_company_ratios.py`:

```python
"""Unit tests for the ``CompanyRatios`` pydantic model."""
from __future__ import annotations

from data.models.company_ratios import CompanyRatios


def test_company_ratios_round_trip_with_all_fields() -> None:
    """A fully-populated CompanyRatios survives model_dump → model_validate."""
    cr = CompanyRatios(
        ticker="AAPL",
        long_name="Apple Inc.",
        sector="Technology",
        market_cap=3.0e12,
        trailing_pe=36.2,
        forward_pe=31.3,
        beta=1.25,
        dividend_yield=0.005,
        fifty_day_average=210.0,
        two_hundred_day_average=190.0,
        last_price=215.7,
    )

    payload = cr.model_dump()
    restored = CompanyRatios.model_validate(payload)
    assert restored == cr


def test_company_ratios_accepts_all_optionals_none() -> None:
    """Every fundamental field is optional — yfinance returns sparse data."""
    cr = CompanyRatios(ticker="ZZZZ")
    assert cr.market_cap is None
    assert cr.long_name is None
```

- [ ] **Run to confirm it fails:**

```bash
.venv/bin/python -m pytest tests/unit/data/test_company_ratios.py -v
```
Expected: `ModuleNotFoundError: No module named 'data.models.company_ratios'`

### Step 1.4: Implement `CompanyRatios`

- [ ] **Create** `src/data/models/company_ratios.py`:

```python
"""``CompanyRatios`` — scalar fundamentals + summary stats for one ticker."""
from __future__ import annotations

from pydantic import BaseModel


class CompanyRatios(BaseModel):
    """Scalar company-level fundamentals + summary stats for one ticker.

    Replaces every non-history field of the retired ``StockStats`` model. The
    fifty-day and two-hundred-day moving averages live here (not in
    ``PriceHistory``) because yfinance serves them as scalars; they are summary
    statistics, not OHLCV bars.

    Every fundamental field is optional — yfinance returns sparse data for many
    tickers; the provider normalises non-finite floats to ``None``.

    Parameters
    ----------
    ticker:
        Upper-cased symbol the ratios belong to.
    long_name:
        Display name (e.g. ``"Apple Inc."``) when available.
    sector:
        GICS sector string when available.
    market_cap, trailing_pe, forward_pe, beta, dividend_yield,
    fifty_day_average, two_hundred_day_average, last_price:
        Self-explanatory fundamental scalars. ``last_price`` is the most recent
        trade price reported by yfinance.
    """

    ticker: str
    long_name: str | None = None
    sector: str | None = None

    market_cap: float | None              = None
    trailing_pe: float | None             = None
    forward_pe: float | None              = None
    beta: float | None                    = None
    dividend_yield: float | None          = None
    fifty_day_average: float | None       = None
    two_hundred_day_average: float | None = None
    last_price: float | None              = None
```

- [ ] **Run the test — passes:**

```bash
.venv/bin/python -m pytest tests/unit/data/test_company_ratios.py -v
```

### Step 1.5: Re-export new models, drop `StockStats`

- [ ] **Edit** `src/data/models/__init__.py`:

```python
"""Pydantic models for every data-source provider.

Re-exported flat for convenience: ``from data.models import CompanyRatios``.
"""
from .bundle import ProviderError, StockSignalBundle
from .company_ratios import CompanyRatios
from .filings import Filing
from .market import OHLCBar
from .news import NewsArticle
from .price_history import PriceHistory
from .sentiment import SocialSentiment, SocialSentimentSnapshot
from .trades import (
    Form4Bundle,
    InsiderDerivativeTrade,
    InsiderTrade,
    NotableHolder,
    PoliticianTrade,
    TradeSide,
)

__all__ = [
    "CompanyRatios",
    "Filing",
    "Form4Bundle",
    "InsiderDerivativeTrade",
    "InsiderTrade",
    "NewsArticle",
    "NotableHolder",
    "OHLCBar",
    "PoliticianTrade",
    "PriceHistory",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    "TradeSide",
]
```

- [ ] **Edit** `src/data/models/market.py` — delete the `StockStats` class entirely. Keep the `OHLCBar` class and update the module docstring:

```python
"""Market-data primitives — output of the price-history provider."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OHLCBar(BaseModel):
    """One price bar from yfinance history (OHLCV adjusted for splits and dividends)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
```

### Step 1.6: Update the signal bundle

- [ ] **Read** `src/data/models/bundle.py` to find the `stats: StockStats | None = None` field.

- [ ] **Edit** `src/data/models/bundle.py`:
  - Replace `from .market import StockStats` with `from .company_ratios import CompanyRatios` and `from .price_history import PriceHistory`.
  - Replace `stats: StockStats | None = None` with two fields:

```python
    price_history: PriceHistory | None = None
    ratios: CompanyRatios | None = None
```

### Step 1.7: Split the yfinance provider into two registered providers

- [ ] **Rewrite** `src/data/providers/stats/yfinance.py`:

```python
"""yfinance providers — split into price history + company ratios.

The underlying yfinance call is shared per-ticker per-tick by an in-memory
LRU cache keyed on ``(symbol, period, interval)`` so that requesting both
``price_history`` and ``ratios`` for the same ticker does not double the
yfinance hit. The cache is cleared between ticks because each tick mints a
fresh process state via the orchestrator's session bootstrap.
"""
from __future__ import annotations

import asyncio
import math
from functools import lru_cache
from typing import Any

import yfinance as yf

from data.registry import register
from data.retry import with_retry

from ...models import CompanyRatios, OHLCBar, PriceHistory


def _f(d: dict[str, Any], *keys: str) -> float | None:
    """Try each key in order; return the first finite float found, or ``None``."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


@lru_cache(maxsize=128)
def _yt_raw(symbol: str, period: str, interval: str) -> dict[str, Any]:
    """Fetch the raw yfinance payload once per ``(symbol, period, interval)``.

    Returns a dict with ``history`` (DataFrame), ``info`` (dict), and
    ``fast_info`` (dict). Shared between the price-history and ratios providers
    so a single tick that needs both pays only one yfinance round-trip.
    """
    yt = yf.Ticker(symbol)
    df = yt.history(period=period, interval=interval, auto_adjust=True)

    info: dict[str, Any] = {}
    try:
        info = yt.info or {}
    except Exception:
        info = {}

    fast: dict[str, Any] = {}
    try:
        fast = dict(yt.fast_info) if yt.fast_info else {}
    except Exception:
        fast = {}

    return {"history": df, "info": info, "fast": fast}


@with_retry
def _fetch_price_history(symbol: str, period: str, interval: str) -> PriceHistory:
    """Project the yfinance OHLCV frame into a ``PriceHistory``."""
    raw = _yt_raw(symbol, period, interval)
    df = raw["history"]

    bars: list[OHLCBar] = []
    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            bars.append(
                OHLCBar(
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0) or 0),
                )
            )

    return PriceHistory(ticker=symbol, bars=bars)


@with_retry
def _fetch_company_ratios(symbol: str, period: str, interval: str) -> CompanyRatios:
    """Project the yfinance ``info`` + ``fast_info`` dicts into a ``CompanyRatios``."""
    raw = _yt_raw(symbol, period, interval)
    info = raw["info"]
    fast = raw["fast"]

    return CompanyRatios(
        ticker=symbol,
        long_name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        market_cap=_f(info, "marketCap") or _f(fast, "market_cap", "marketCap"),
        trailing_pe=_f(info, "trailingPE"),
        forward_pe=_f(info, "forwardPE"),
        beta=_f(info, "beta"),
        dividend_yield=_f(info, "dividendYield"),
        fifty_day_average=_f(info, "fiftyDayAverage")
        or _f(fast, "fifty_day_average", "fiftyDayAverage"),
        two_hundred_day_average=_f(info, "twoHundredDayAverage")
        or _f(fast, "two_hundred_day_average", "twoHundredDayAverage"),
        last_price=_f(fast, "last_price", "lastPrice")
        or _f(info, "currentPrice", "regularMarketPrice"),
    )


@register(
    domain="price_history",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_price_history(
    ticker: str, *, period: str = "1y", interval: str = "1d"
) -> PriceHistory:
    """Async wrapper for the price-history fetch — runs the blocking call off-thread."""
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_price_history, symbol, period, interval)


@register(
    domain="company_ratios",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_company_ratios(
    ticker: str, *, period: str = "1y", interval: str = "1d"
) -> CompanyRatios:
    """Async wrapper for the ratios fetch — runs the blocking call off-thread."""
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_company_ratios, symbol, period, interval)
```

- [ ] **Edit** `config/data.json` — replace the `"stats": "yfinance"` provider entry with two entries:

```json
{
  "providers": {
    "price_history": "yfinance",
    "company_ratios": "yfinance",
    "news": "finnhub",
    "social_sentiment": "finnhub",
    "insider_trades": "edgar",
    "politician_trades": "quiver",
    "notable_holders": "edgar",
    "filings": "edgar"
  },
  ...
}
```

(Leave the `defaults` block intact.)

### Step 1.8: Replace `get_stock_stats` with two functions

- [ ] **Edit** `src/data/__init__.py`:

  - Remove `StockStats` from the `from .models import (...)` block and replace with `CompanyRatios`, `PriceHistory`.
  - Replace `async def get_stock_stats(...)` with:

```python
async def get_price_history(
    ticker: str, period: str = "1y", interval: str = "1d"
):
    """Fetch OHLCV history for ``ticker`` via the active price-history provider."""
    return await _dispatch("price_history", ticker.upper(), period=period, interval=interval)


async def get_company_ratios(
    ticker: str, period: str = "1y", interval: str = "1d"
):
    """Fetch scalar fundamentals for ``ticker`` via the active ratios provider."""
    return await _dispatch("company_ratios", ticker.upper(), period=period, interval=interval)
```

  - Update `__all__` — drop `"get_stock_stats"` and `"StockStats"`; add `"get_price_history"`, `"get_company_ratios"`, `"CompanyRatios"`, `"PriceHistory"`.

### Step 1.9: Add a provider-split test

- [ ] **Create** `tests/unit/data/test_providers_split.py`:

```python
"""Smoke test that the two yfinance providers project from the same raw payload.

We do not hit the network — the test patches ``yf.Ticker`` to return a fake.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from data.providers.stats import yfinance as prov


def _fake_yf_ticker(symbol: str) -> MagicMock:
    """Build a fake yfinance Ticker with a tiny history + info payload."""
    df = pd.DataFrame(
        {
            "Open":   [100.0, 101.0],
            "High":   [102.0, 103.0],
            "Low":    [ 99.0, 100.0],
            "Close":  [101.0, 102.5],
            "Volume": [1_000.0, 1_200.0],
        },
        index=pd.DatetimeIndex([datetime(2026, 5, 1), datetime(2026, 5, 2)]),
    )
    t = MagicMock()
    t.history.return_value = df
    t.info = {"trailingPE": 20.1, "longName": "Test Co", "sector": "Tech"}
    t.fast_info = {"last_price": 102.5}
    return t


def test_price_history_and_ratios_share_underlying_call() -> None:
    """Fetching both for the same ticker must not double-call yfinance."""
    # Clear the lru_cache so the test is hermetic.
    prov._yt_raw.cache_clear()

    with patch.object(prov.yf, "Ticker", side_effect=_fake_yf_ticker) as ticker_mock:
        ph = prov._fetch_price_history("AAPL", "1y", "1d")
        cr = prov._fetch_company_ratios("AAPL", "1y", "1d")

    # The lru_cache guarantees one Ticker construction per (symbol, period, interval).
    assert ticker_mock.call_count == 1
    assert ph.ticker == "AAPL"
    assert len(ph.bars) == 2
    assert ph.bars[-1].close == 102.5
    assert cr.trailing_pe == 20.1
    assert cr.last_price == 102.5
```

- [ ] **Run it:**

```bash
.venv/bin/python -m pytest tests/unit/data/test_providers_split.py -v
```
Expected: PASS.

### Step 1.10: Wire the technical fetch callback to both providers

- [ ] **Edit** `src/agents/analysts/technical/fetch.py`:

```python
"""Technical analyst data fetch callback.

Fetches the OHLCV price history *and* scalar company ratios for every
watchlist ticker. Writes ``state["technical_data"][ticker]`` with two sub-keys:

- ``price_history`` — dict from ``PriceHistory.model_dump()``; the extractor
  reads bars from here.
- ``ratios`` — dict from ``CompanyRatios.model_dump()``; reserved for future
  cross-feature work (e.g. dividend-yield-aware overrides). Not required by
  the current extractor.
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import get_company_ratios, get_price_history
from observability.trace import _trace_maybe

logger = logging.getLogger(__name__)


async def technical_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch ``PriceHistory`` and ``CompanyRatios`` for every watchlist ticker."""
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    technical_data: dict[str, dict] = {}

    for ticker in tickers:
        # --- price history ---
        try:
            ph = await get_price_history(ticker)
            ph_payload = ph.model_dump() if hasattr(ph, "model_dump") else ph
        except Exception as exc:
            logger.warning("price_history fetch failed for %s: %s", ticker, exc)
            ph_payload = None

        # --- ratios ---
        try:
            cr = await get_company_ratios(ticker)
            cr_payload = cr.model_dump() if hasattr(cr, "model_dump") else cr
        except Exception as exc:
            logger.warning("company_ratios fetch failed for %s: %s", ticker, exc)
            cr_payload = None

        technical_data[ticker] = {
            "price_history": ph_payload,
            "ratios":        cr_payload,
        }

    state["technical_data"] = technical_data

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "01_fetch_technical", technical_data)

    return None
```

### Step 1.11: Update the technical extractor for the new shape

The extractor already supports `raw.get("price_history") or raw.get("history")` (see `src/contract/extractors/technical.py:108`). The new payload places bars at `raw["price_history"]["bars"]`, not `raw["price_history"]` directly.

- [ ] **Edit** `src/contract/extractors/technical.py` — replace the line:

```python
    history = raw.get("price_history") or raw.get("history") or []
```

with:

```python
    # Phase 5 redesign: technical_data[ticker]["price_history"] is now a dict
    # from PriceHistory.model_dump() — bars live under the ``"bars"`` sub-key.
    # Legacy ``"history"`` top-level key falls back for any unmigrated caller.
    ph_payload = raw.get("price_history")
    if isinstance(ph_payload, dict):
        history = ph_payload.get("bars") or []
    else:
        history = ph_payload or raw.get("history") or []
```

Also update the 52-week high/low lookup at lines 162–170 — they used to read from `raw.get("high_52w")` / `raw.get("low_52w")`, which were keys on the old `StockStats` model. The new `CompanyRatios` model does not carry these; if the extractor used them, they were previously always `None`. Confirm by reading the surrounding code; if the keys never come back populated, leave the fallback paths in place and add a comment noting the model split removed these.

- [ ] **Verify by running** `tests/unit/test_derive_technical_verdict.py`:

```bash
.venv/bin/python -m pytest tests/unit/test_derive_technical_verdict.py -v
```
Expected: PASS.

### Step 1.12: Wire the fundamental fetch callback to ratios only

- [ ] **Edit** `src/agents/analysts/fundamental/fetch.py`:

  - Replace the import line `from data import get_company_filings, get_insider_trades, get_stock_stats` with `from data import get_company_filings, get_company_ratios, get_insider_trades`.
  - Replace the `--- stats ---` block (lines ~226–234) with:

```python
        # --- ratios ---
        # Fundamental no longer drags the 252-row OHLCV history with it; the
        # split data-model (Phase 5 redesign) means only the scalar ratios
        # come along. The Technical analyst is the sole consumer of bars.
        try:
            ratios_obj = await get_company_ratios(ticker)
            ratios_payload = (
                ratios_obj.model_dump() if hasattr(ratios_obj, "model_dump") else ratios_obj
            )
        except Exception as exc:
            logger.warning("company_ratios fetch failed for %s: %s", ticker, exc)
            ratios_payload = None
```

  - Replace `"stats": stats_payload,` in the per-ticker dict assignment with `"ratios": ratios_payload,`.
  - Update the module docstring's example layout to show `"ratios"` instead of `"stats"`.

### Step 1.13: Update the fundamental extractor

- [ ] **Read** `src/contract/extractors/fundamental.py` to find every site that reads `raw["stats"][...]`.

- [ ] **Edit** the extractor — rename `stats` → `ratios` throughout the dict-key lookups. Field names inside the dict (`trailing_pe`, `market_cap`, etc.) are unchanged, so this is a one-key rename.

- [ ] **Run the affected tests:**

```bash
.venv/bin/python -m pytest tests/unit/test_extract_fundamental_features.py -v
```

If any test feeds a mock with a `"stats"` key, update those fixtures to `"ratios"`.

### Step 1.14: Update existing analyst-fetcher and fundamental-fetch tests

- [ ] **Edit** `tests/unit/test_analyst_fetchers.py` — search for `get_stock_stats` and replace each call site with appropriate patches for `get_price_history` / `get_company_ratios`. Build fakes using `PriceHistory(ticker="AAPL", bars=[])` and `CompanyRatios(ticker="AAPL")`.

- [ ] **Edit** `tests/unit/test_fundamental_fetch.py` — replace `get_stock_stats` patches with `get_company_ratios` patches; return a `CompanyRatios` instance instead of a `StockStats`.

- [ ] **Edit** `tests/unit/data/test_aggregator.py` — replace `StockStats` import with `PriceHistory` and `CompanyRatios`; rebuild the fake to return both.

- [ ] **Run the broad data + analyst test suite:**

```bash
.venv/bin/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py tests/unit/test_fundamental_fetch.py tests/unit/test_derive_technical_verdict.py tests/unit/test_extract_fundamental_features.py -v
```
Expected: all PASS.

### Step 1.15: Lint + commit

- [ ] **Lint:**

```bash
.venv/bin/python -m ruff check src/ tests/
```

- [ ] **Append to** `graphify-out/graph_delta.md` (local-only — do NOT `git add` this file):

```
## 2026-05-14 — split StockStats into PriceHistory + CompanyRatios

Phase 5 analyst-surface redesign Task 1.

- New nodes: src/data/models/price_history.py (PriceHistory),
  src/data/models/company_ratios.py (CompanyRatios).
- Removed: data.models.market.StockStats, data.get_stock_stats.
- New edges: agents.analysts.technical.fetch -> data.get_price_history,
  data.get_company_ratios; agents.analysts.fundamental.fetch ->
  data.get_company_ratios. yfinance provider now registers two domains
  (price_history, company_ratios) sharing one cached raw call.
```

- [ ] **Commit:**

```bash
git add src/data/models/price_history.py src/data/models/company_ratios.py \
        src/data/models/__init__.py src/data/models/market.py src/data/models/bundle.py \
        src/data/providers/stats/yfinance.py src/data/__init__.py \
        src/agents/analysts/technical/fetch.py src/agents/analysts/fundamental/fetch.py \
        src/contract/extractors/technical.py src/contract/extractors/fundamental.py \
        config/data.json \
        tests/unit/data/test_price_history.py tests/unit/data/test_company_ratios.py \
        tests/unit/data/test_providers_split.py tests/unit/data/test_aggregator.py \
        tests/unit/test_analyst_fetchers.py tests/unit/test_fundamental_fetch.py
git commit -m "$(cat <<'EOF'
feat(phase5): split StockStats into PriceHistory + CompanyRatios

Fundamental no longer drags 252 OHLCV rows it never reads. The yfinance
provider registers two domains sharing one cached raw call so we keep the
single round-trip cost. get_stock_stats retired.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Trace fidelity fix — shared callback utility, captures system instruction

**Files:**
- Modify: `src/observability/trace.py` — add `make_llm_trace_callbacks(section_name, model)`
- Modify: `src/agents/analysts/news/agent.py` — replace duplicated `_make_llm_trace_*` with the shared helper
- Modify: `src/agents/analysts/fundamental/agent.py` — same
- Modify: `src/agents/strategist/agent.py` (if a trace helper lives there too — verify in step 2.1)
- Test: `tests/unit/test_llm_trace_callbacks.py` (new)

### Step 2.1: Locate every duplicate trace helper

- [ ] **Run** to enumerate every site:

```bash
grep -rn "_make_llm_trace_before\|_make_llm_trace_after" src/
```

Confirm the sites are `news/agent.py`, `fundamental/agent.py`, and possibly the strategist's agent module. Make a note of each module path and the section label string it currently uses (`"03_news_llm"`, `"03_fundamental_llm"`, etc.) — these labels must be preserved verbatim by the shared helper, otherwise old trace dashboards break.

### Step 2.2: Write a failing regression test

- [ ] **Create** `tests/unit/test_llm_trace_callbacks.py`:

```python
"""Regression tests for the shared LLM trace callback utility.

Covers the Phase 5 trace-fidelity fix: the captured prompt MUST include both
the system instruction (where `{news_context}` is filled) AND the user-side
contents. The pre-fix helper only captured ``llm_request.contents`` and
silently dropped the system instruction.
"""
from __future__ import annotations

from types import SimpleNamespace

from observability.trace import TraceWriter, make_llm_trace_callbacks


class _FakeState(dict):
    """Dict-like state object with the same ``.get`` interface ADK uses."""


def _fake_part(text: str) -> SimpleNamespace:
    """Build a fake LlmRequest content part exposing a ``.text`` attribute."""
    return SimpleNamespace(text=text)


def _fake_content(text: str) -> SimpleNamespace:
    """Build a fake LlmRequest content with a list of parts."""
    return SimpleNamespace(parts=[_fake_part(text)])


def _fake_request(system_text: str, user_text: str) -> SimpleNamespace:
    """Build a fake LlmRequest with both system instruction and user contents."""
    config = SimpleNamespace(system_instruction=_fake_content(system_text))
    return SimpleNamespace(config=config, contents=[_fake_content(user_text)])


def _fake_response(text: str) -> SimpleNamespace:
    """Build a fake LlmResponse with a single text part."""
    return SimpleNamespace(content=_fake_content(text))


def test_before_callback_captures_system_and_user_text() -> None:
    """The captured prompt must concatenate system + user under labelled headings."""
    tw = TraceWriter()
    state = _FakeState({"_trace": tw})
    ctx = SimpleNamespace(state=state)

    before, _after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    before(ctx, _fake_request(system_text="SYSTEM:Articles for AAPL", user_text="USER:Run tick"))

    captured = tw._sections["03_news_llm_in"]["prompt"]
    assert "=== system ===" in captured
    assert "SYSTEM:Articles for AAPL" in captured
    assert "=== user ===" in captured
    assert "USER:Run tick" in captured


def test_after_callback_overwrites_pending_marker() -> None:
    """After-callback replaces the ``(pending)`` placeholder with the model response."""
    tw = TraceWriter()
    state = _FakeState({"_trace": tw})
    ctx = SimpleNamespace(state=state)

    before, after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    before(ctx, _fake_request("sys", "usr"))
    after(ctx, _fake_response("VERDICT_JSON"))

    out_section = tw._sections["03_news_llm_out"]
    assert out_section["response"] == "VERDICT_JSON"
    assert out_section["model"] == "gemini-test"


def test_callbacks_are_noops_without_trace_writer() -> None:
    """No trace writer in state -> both callbacks return without raising."""
    state = _FakeState()
    ctx = SimpleNamespace(state=state)

    before, after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    assert before(ctx, _fake_request("sys", "usr")) is None
    assert after(ctx, _fake_response("out")) is None
```

- [ ] **Run to confirm failure:**

```bash
.venv/bin/python -m pytest tests/unit/test_llm_trace_callbacks.py -v
```
Expected: `ImportError: cannot import name 'make_llm_trace_callbacks' from 'observability.trace'`.

### Step 2.3: Implement the shared utility

The Google ADK `LlmRequest` exposes the rendered system instruction on `llm_request.config.system_instruction`, which is a `Content` (same shape as the entries in `llm_request.contents`). Each `Content` has a `.parts` list of `Part`s; text parts expose `.text`.

- [ ] **Append** to `src/observability/trace.py` (after the existing `_trace_maybe` function):

```python
from typing import Callable, Tuple  # add near the top of the file


def _extract_content_text(content: Any) -> str:
    """Concatenate every text part of a single ADK ``Content`` into one string.

    Parameters
    ----------
    content:
        An ADK ``Content`` object — has a ``.parts`` list whose entries may
        carry a ``.text`` attribute. Non-text parts are silently skipped.

    Returns
    -------
    str
        The concatenated text, or an empty string if no text parts exist.
    """
    if content is None:
        return ""

    parts = getattr(content, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)

    return "\n".join(chunks)


def make_llm_trace_callbacks(section_name: str, *, model: str) -> Tuple[Callable, Callable]:
    """Build paired before/after model callbacks that capture the LLM round-trip.

    Captures BOTH the rendered system instruction (which contains the
    ``{news_context}`` / ``{fundamental_context}`` placeholders after ADK
    substitution) AND the user-side ``contents``. Pre-Phase-5 helpers only
    captured ``contents`` and silently dropped the system instruction, which
    meant every surface trace was missing the actual article / filing text the
    LLM saw.

    The captured prompt is structured as::

        === system ===
        <rendered system instruction>
        === user ===
        <user contents>

    Both callbacks are no-ops when ``state["_trace"]`` is not a
    ``TraceWriter`` — production runs pay a single dict lookup.

    Parameters
    ----------
    section_name:
        Base label for the trace section (e.g. ``"03_news_llm"``). The
        before-callback writes to ``{section_name}_in``; the after-callback
        writes to ``{section_name}_out``.
    model:
        Model identifier string to record alongside the prompt + response
        (e.g. ``"gemini-2.5-flash-lite"``).

    Returns
    -------
    (before, after):
        Two callables matching ADK's ``before_model_callback`` and
        ``after_model_callback`` signatures.
    """

    def _state_writer(ctx: Any) -> "TraceWriter | None":
        """Look up the TraceWriter on ``ctx.state``; return None if absent."""
        state = ctx.state
        try:
            tw = state.get("_trace")
        except (AttributeError, TypeError):
            return None
        return tw if isinstance(tw, TraceWriter) else None

    def _before(callback_context: Any, llm_request: Any) -> None:
        """Capture system + user prompt portions into the trace writer."""
        tw = _state_writer(callback_context)
        if tw is None:
            return None

        # System instruction (where {news_context} / {fundamental_context}
        # / {tickers} are substituted) lives on llm_request.config.system_instruction.
        config = getattr(llm_request, "config", None)
        system_text = _extract_content_text(getattr(config, "system_instruction", None))

        # User contents — the historical capture target.
        user_chunks: list[str] = []
        for content in (getattr(llm_request, "contents", None) or []):
            user_chunks.append(_extract_content_text(content))
        user_text = "\n---\n".join(c for c in user_chunks if c)

        prompt = (
            "=== system ===\n"
            f"{system_text or '(no system instruction)'}\n"
            "=== user ===\n"
            f"{user_text or '(no user content)'}"
        )

        tw.llm_pair(section_name, prompt=prompt, response="(pending)", model=model)
        return None

    def _after(callback_context: Any, llm_response: Any) -> None:
        """Overwrite the ``(pending)`` placeholder with the model's response text."""
        tw = _state_writer(callback_context)
        if tw is None:
            return None

        response_text = _extract_content_text(getattr(llm_response, "content", None))

        tw._sections[f"{section_name}_out"] = {
            "model": model,
            "response": response_text or "(no text parts)",
        }
        return None

    return _before, _after
```

- [ ] **Run the test:**

```bash
.venv/bin/python -m pytest tests/unit/test_llm_trace_callbacks.py -v
```
Expected: all 3 PASS.

### Step 2.4: Replace the duplicate helpers in the news agent

- [ ] **Edit** `src/agents/analysts/news/agent.py`:

  - Delete the entire `_make_llm_trace_before` function (lines ~50–97).
  - Delete the entire `_make_llm_trace_after` function (lines ~100–148).
  - Delete the unused imports (`CallbackContext`, `LlmRequest`, `LlmResponse`, `genai_types`) — verify they aren't referenced elsewhere in the module first via `grep -n` in the file.
  - Replace the imports block with:

```python
from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import NewsVocabulary, load_heuristics
from contract.evidence import VerdictBatch
from contract.extractors.news import extract_news_features
from observability.trace import make_llm_trace_callbacks
```

  - Inside `_build_news_analyst`, replace the trace-callback wiring with:

```python
    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    before_cb = None
    after_cb = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        before_cb, after_cb = make_llm_trace_callbacks("03_news_llm", model=model)
```

### Step 2.5: Mirror the change in the fundamental agent

- [ ] **Read** `src/agents/analysts/fundamental/agent.py` to confirm the duplicate helpers there use section label `"03_fundamental_llm"`.

- [ ] **Edit** `src/agents/analysts/fundamental/agent.py` — apply the exact same deletions + replacement, using `make_llm_trace_callbacks("03_fundamental_llm", model=model)`.

### Step 2.6: Mirror the change in the strategist (if applicable)

- [ ] **Run** `grep -rn "llm_pair\|_make_llm_trace\|make_llm_trace" src/agents/strategist/` to confirm whether the strategist also has a duplicate helper.

- [ ] **If yes**, edit the strategist agent module the same way; the section label is whatever the existing helper passes to `llm_pair` (likely `"05_strategist_llm"`).

- [ ] **If no**, skip and note in the commit message.

### Step 2.7: Verify with the full pre-existing trace test

- [ ] **Run** the existing trace writer test plus the new test:

```bash
.venv/bin/python -m pytest tests/unit/test_trace_writer.py tests/unit/test_trace_maybe_noop.py tests/unit/test_llm_trace_callbacks.py -v
```
Expected: all PASS.

### Step 2.8: Lint + commit

- [ ] **Lint:**

```bash
.venv/bin/python -m ruff check src/ tests/
```

- [ ] **Append to** `graphify-out/graph_delta.md`:

```
## 2026-05-14 — shared LLM trace callback utility

Phase 5 analyst-surface redesign Task 2.

- New node: observability.trace.make_llm_trace_callbacks (+ helper
  _extract_content_text).
- Removed: news/agent._make_llm_trace_before/after,
  fundamental/agent._make_llm_trace_before/after
  (and strategist's pair if present).
- Behaviour change: trace now records system instruction (where
  {news_context} / {fundamental_context} are filled) in addition to
  user contents.
```

- [ ] **Commit:**

```bash
git add src/observability/trace.py src/agents/analysts/news/agent.py \
        src/agents/analysts/fundamental/agent.py \
        tests/unit/test_llm_trace_callbacks.py
# Add src/agents/strategist/agent.py only if Step 2.6 changed it.
git commit -m "$(cat <<'EOF'
fix(phase5): capture system instruction in LLM surface traces

Pre-fix, the per-analyst before_model_callback only walked
llm_request.contents and silently dropped llm_request.config.system_instruction
— where {news_context} / {fundamental_context} are rendered. Surface traces
have been missing the article + filing text the LLM actually saw since the
trace harness landed.

Consolidates the duplicated _make_llm_trace_before/after helpers across
news, fundamental (and strategist) agents into a single
observability.trace.make_llm_trace_callbacks factory.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Externalise truncation caps to `config/analysts.json`

**Files:**
- Create: `config/analysts.json`
- Modify: `config/README.md` — add entry for the new file
- Create: `src/config/analysts.py` — loader module (alongside the existing config loaders)
- Modify: `src/agents/analysts/news/fetch.py` — read caps from config at module import (or first call)
- Modify: `src/agents/analysts/fundamental/fetch.py` — same
- Test: `tests/unit/config/test_analysts_config.py` (new)

### Step 3.1: Survey the existing config loader pattern

- [ ] **Read** the file `src/data/config.py` (referenced by `src/data/__init__.py` as `from .config import get_config`) to understand the project's loader idiom (likely a single `get_config()` returning a Pydantic model).

- [ ] **Look at** `config/analyst_heuristics.json` and the loader that reads it — likely under `src/agents/analysts/heuristics.py`. Mirror the same pattern for `analysts.json`.

### Step 3.2: Write a failing test for the config loader

- [ ] **Create** `tests/unit/config/__init__.py` if it doesn't already exist (empty file).

- [ ] **Create** `tests/unit/config/test_analysts_config.py`:

```python
"""Unit tests for the analysts.json config loader."""
from __future__ import annotations

import json
from pathlib import Path

from config.analysts import AnalystsConfig, load_analysts_config


def test_load_analysts_config_default_values(tmp_path: Path) -> None:
    """A minimal config file populates fields with the documented defaults."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    cfg = load_analysts_config(path=cfg_file)
    assert isinstance(cfg, AnalystsConfig)
    assert cfg.news.max_articles_per_ticker == 20
    assert cfg.fundamental.max_filing_mda_chars == 1500
    assert cfg.cache.enabled is True
    assert cfg.cache.directory == "cache/reports"


def test_load_analysts_config_rejects_negative_caps(tmp_path: Path) -> None:
    """Negative truncation caps must fail validation — they are sentinel-poisoning."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": -1, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)
```

- [ ] **Run to confirm failure:**

```bash
.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'config.analysts'`.

### Step 3.3: Implement the loader

- [ ] **Create** `src/config/__init__.py` if the directory doesn't exist (empty file).

- [ ] **Create** `src/config/analysts.py`:

```python
"""Loader for ``config/analysts.json`` — truncation caps + cache settings.

A Pydantic-validated wrapper around the JSON file at the project root. The
module-level singleton ``get_analysts_config()`` is the production entry
point; ``load_analysts_config(path=...)`` exists for tests that want to feed
a custom file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# to this file.
_DEFAULT_PATH = Path("config/analysts.json")


class NewsCaps(BaseModel):
    """Truncation caps for the News analyst's LLM context."""
    max_articles_per_ticker: int = Field(ge=1, le=200)
    max_summary_chars:       int = Field(ge=1, le=10_000)


class FundamentalCaps(BaseModel):
    """Truncation caps for the Fundamental analyst's LLM context."""
    max_filing_mda_chars:        int = Field(ge=1, le=20_000)
    max_filing_risk_chars:       int = Field(ge=1, le=20_000)
    max_insider_footnotes:       int = Field(ge=0, le=50)
    max_insider_footnote_chars:  int = Field(ge=1, le=5_000)


class CacheSettings(BaseModel):
    """Report-cache toggle + on-disk storage directory (gitignored)."""
    enabled:   bool
    directory: str


class AnalystsConfig(BaseModel):
    """Top-level shape of ``config/analysts.json``."""
    news:        NewsCaps
    fundamental: FundamentalCaps
    cache:       CacheSettings


def load_analysts_config(*, path: Path | None = None) -> AnalystsConfig:
    """Read and validate ``config/analysts.json``.

    Parameters
    ----------
    path:
        Override the default path (used by tests).

    Returns
    -------
    AnalystsConfig
        Validated configuration object.
    """
    p = path or _DEFAULT_PATH
    payload = json.loads(p.read_text())
    return AnalystsConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_analysts_config() -> AnalystsConfig:
    """Production entry point — cached load of the default config path."""
    return load_analysts_config()
```

- [ ] **Run the test:**

```bash
.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v
```
Expected: PASS.

### Step 3.4: Create the config file + README entry

- [ ] **Create** `config/analysts.json`:

```json
{
  "news": {
    "max_articles_per_ticker": 20,
    "max_summary_chars": 500
  },
  "fundamental": {
    "max_filing_mda_chars": 1500,
    "max_filing_risk_chars": 1500,
    "max_insider_footnotes": 5,
    "max_insider_footnote_chars": 400
  },
  "cache": {
    "enabled": true,
    "directory": "cache/reports"
  }
}
```

- [ ] **Edit** `config/README.md` — add a new section describing every key. Mirror the existing file's tone and per-key format. Include:
  - `news.max_articles_per_ticker` — max article count per ticker fed to the News LLM. Range 1–200. Default 20.
  - `news.max_summary_chars` — max characters of each article's summary kept in the prompt. Range 1–10000. Default 500.
  - `fundamental.max_filing_mda_chars`, `max_filing_risk_chars` — char caps on filing excerpts. Default 1500 each.
  - `fundamental.max_insider_footnotes` — max insider footnote count. Default 5.
  - `fundamental.max_insider_footnote_chars` — char cap per footnote. Default 400.
  - `cache.enabled` — toggle the LLM report cache. When `false`, every tick re-prompts (matches pre-redesign behaviour). Default `true`.
  - `cache.directory` — on-disk cache root. Always under the gitignored `cache/` tree. Default `cache/reports`.

### Step 3.5: Wire News + Fundamental fetches to the config

- [ ] **Edit** `src/agents/analysts/news/fetch.py`:

  - Add at the top of the module:

```python
from config.analysts import get_analysts_config
```

  - Replace the module-level `_MAX_HEADLINES = 10` and `_MAX_SUMMARY_CHARS = 300` constants with a small helper that reads from config lazily (avoids loader running at import time, which simplifies testing):

```python
def _caps() -> tuple[int, int]:
    """Return ``(max_articles, max_summary_chars)`` from the analysts config."""
    cfg = get_analysts_config().news
    return cfg.max_articles_per_ticker, cfg.max_summary_chars
```

  - Inside `_build_ticker_news_context`, replace `recent = articles[:_MAX_HEADLINES]` with `max_articles, max_summary_chars = _caps(); recent = articles[:max_articles]`.

  - Replace `f"       {summary[:_MAX_SUMMARY_CHARS]}"` with `f"       {summary[:max_summary_chars]}"`.

- [ ] **Edit** `src/agents/analysts/fundamental/fetch.py` analogously:

  - Add `from config.analysts import get_analysts_config`.
  - Replace `_MAX_FOOTNOTES = 5` and `_MAX_FOOTNOTE_CHARS = 200` constants with a `_caps()` helper returning the four fundamental cap values.
  - Replace `mda[:500]` and `risk_fac[:500]` with `mda[:caps.max_filing_mda_chars]` and `risk_fac[:caps.max_filing_risk_chars]` (call `_caps()` once at the top of `_build_ticker_context`).
  - Replace `note[:_MAX_FOOTNOTE_CHARS]` and `footnotes[:_MAX_FOOTNOTES]` with the config values.

### Step 3.6: Run all affected tests

- [ ] **Run:**

```bash
.venv/bin/python -m pytest tests/unit/test_fundamental_fetch.py tests/unit/config/ tests/unit/test_news_prompt_render.py -v
```
Expected: PASS (existing tests should not break — the defaults match the prior hardcoded values where the spec specifies "After", or the looser cap is a widening).

### Step 3.7: Lint + commit

- [ ] **Lint:**

```bash
.venv/bin/python -m ruff check src/ tests/
```

- [ ] **Append to** `graphify-out/graph_delta.md`:

```
## 2026-05-14 — externalise analyst truncation caps to config/analysts.json

Phase 5 analyst-surface redesign Task 3.

- New nodes: config/analysts.py (AnalystsConfig, NewsCaps, FundamentalCaps,
  CacheSettings, load_analysts_config, get_analysts_config).
- New file: config/analysts.json.
- Removed: _MAX_HEADLINES, _MAX_SUMMARY_CHARS, _MAX_FOOTNOTES,
  _MAX_FOOTNOTE_CHARS module-level constants.
```

- [ ] **Commit:**

```bash
git add config/analysts.json config/README.md src/config/__init__.py \
        src/config/analysts.py src/agents/analysts/news/fetch.py \
        src/agents/analysts/fundamental/fetch.py \
        tests/unit/config/__init__.py tests/unit/config/test_analysts_config.py
git commit -m "$(cat <<'EOF'
refactor(phase5): externalise analyst truncation caps to config/analysts.json

News + Fundamental fetch callbacks now read article / summary / filing /
footnote caps from the validated AnalystsConfig instead of module-level
constants. Caps widened per the surface-redesign spec — News 10→20 articles,
300→500 summary chars; Fundamental 500→1500 filing-excerpt chars; footnote
200→400 chars.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `AnalystReport` schema + News/Fundamental prompt extension

**Files:**
- Modify: `src/contract/evidence.py` — add `ReportDriver`, `AnalystReport`; extend `AnalystVerdict.report` and `TickerVerdict`
- Modify: `src/agents/analysts/news/prompts.py` — append the report instructions
- Modify: `src/agents/analysts/fundamental/prompts.py` — same
- Modify: `src/agents/analysts/_common.py` — the after-callback must pass `verdict.report` through into the persisted evidence
- Test: `tests/unit/contract/test_analyst_report.py` (new)
- Test: `tests/unit/test_news_prompt_render.py` — assert the new instructions appear
- Test: `tests/unit/test_fundamental_prompt_render.py` — same

### Step 4.1: Write a failing test for the schema

- [ ] **Create** `tests/unit/contract/test_analyst_report.py`:

```python
"""Unit tests for the AnalystReport / ReportDriver schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver


def _driver(name: str = "EU App Store ruling", direction: str = "bear", weight: float = 0.5) -> ReportDriver:
    return ReportDriver(name=name, direction=direction, weight=weight, body="x" * 50)


def test_report_round_trips() -> None:
    """Two drivers, summary populated -> survives model_dump round-trip."""
    rpt = AnalystReport(
        summary="Two converging negatives this tick.",
        drivers=[_driver(), _driver(name="Gemini push", weight=0.3)],
    )
    restored = AnalystReport.model_validate(rpt.model_dump())
    assert restored == rpt


def test_report_rejects_empty_drivers() -> None:
    """An LLM emitting zero drivers fails — the prompt mandates 2-4."""
    with pytest.raises(ValidationError):
        AnalystReport(summary="x", drivers=[])


def test_report_rejects_too_many_drivers() -> None:
    """More than 4 drivers is dilution — reject."""
    drivers = [_driver(name=f"D{i}") for i in range(5)]
    with pytest.raises(ValidationError):
        AnalystReport(summary="x", drivers=drivers)


def test_driver_weight_outside_unit_range_rejected() -> None:
    """A driver weight must lie in [0, 1]."""
    with pytest.raises(ValidationError):
        ReportDriver(name="x", direction="bull", weight=1.5, body="y")


def test_driver_direction_closed_vocabulary() -> None:
    """Direction is restricted to bull/bear/neutral."""
    with pytest.raises(ValidationError):
        ReportDriver(name="x", direction="sideways", weight=0.5, body="y")  # type: ignore[arg-type]


def test_verdict_report_field_defaults_to_none() -> None:
    """Deterministic analysts emit AnalystVerdict without a report -> None."""
    v = AnalystVerdict(
        lean="neutral", magnitude=0.0, confidence=0.0,
        rationale="x", key_factors=[],
    )
    assert v.report is None
```

- [ ] **Run to confirm failure:**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_analyst_report.py -v
```
Expected: `ImportError: cannot import name 'AnalystReport' from 'contract.evidence'`.

### Step 4.2: Implement the schemas

- [ ] **Edit** `src/contract/evidence.py`:

  - Add the new models after `AnalystName` and before `AnalystVerdict`:

```python
class ReportDriver(BaseModel):
    """One driver of an LLM analyst's lean — a labelled, weighted reason.

    Drivers complement the closed-vocab ``key_factors`` field on
    ``AnalystVerdict``: tags are machine-aggregatable; drivers are
    strategist-readable prose with relative weighting.
    """

    name:      str   = Field(min_length=1, max_length=60)
    direction: Literal["bull", "bear", "neutral"]
    weight:    float = Field(ge=0.0, le=1.0)
    body:      str   = Field(min_length=1, max_length=1_000)


class AnalystReport(BaseModel):
    """LLM analyst's qualitative reasoning, paired with the verdict.

    Populated only by the LLM analysts (News, Fundamental). Deterministic
    analysts (Technical, SmartMoney, Social) leave ``AnalystVerdict.report``
    as ``None`` — their cognition is fully captured by the verdict + extractor
    features and they have no prose to summarise.
    """

    summary: str               = Field(min_length=1, max_length=2_000)
    drivers: list[ReportDriver] = Field(min_length=2, max_length=4)
```

  - Extend `AnalystVerdict` with an optional `report` field. The existing field set is unchanged; append:

```python
class AnalystVerdict(BaseModel):
    """LLM-emitted directional call for one ticker."""

    lean: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=160)
    key_factors: list[str] = Field(default_factory=list, max_length=8)
    is_no_data: bool = False

    # New in Phase 5 redesign: LLM analysts populate this; deterministic
    # analysts leave it None. The Strategist prompt surface keys off presence
    # to decide whether to render a "Drivers:" block.
    report: AnalystReport | None = None
```

`TickerVerdict` inherits from `AnalystVerdict` so it automatically gains the `report` field — no further edit needed there.

- [ ] **Run the test:**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_analyst_report.py -v
```
Expected: all 6 PASS.

### Step 4.3: Extend the News prompt

- [ ] **Edit** `src/agents/analysts/news/prompts.py`. After the existing decision-rule block and before the `MUST cover ALL tickers` line, append the report instructions. The full revised `_TEMPLATE` becomes:

```python
_TEMPLATE = """You are the News analyst.

For each ticker in the batch, read the supplied headlines and article
summaries. Output a structured verdict per ticker using ONLY the closed
vocabulary below.

Closed vocabulary (use these tags ONLY in key_factors):

  catalyst:<type>     ∈ {catalyst_options}
  novelty:<level>     ∈ {novelty_options}
  direction:<value>   ∈ {direction_options}
  material:<bool>     when material to a long-only fund

For each ticker output a JSON object with fields:
  ticker       string (must be one of the watchlist tickers)
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤160 chars naming the dominant catalyst
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no headlines in the window
  report       object — see schema below; omit only when is_no_data=true.

Report schema:
  summary  3-5 sentences of connective tissue covering the gestalt this
           tick — not a bullet list. Argue your lean.
  drivers  2-4 entries. Each driver:
    name       short label (4-6 words)
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised
    body       2-3 sentences explaining the driver. Do NOT cite source URLs;
               synthesise.

The report is your reasoning; the verdict is your conclusion. They must be
consistent — the lean and direction-weighted driver mix should agree.

Decision rule:
- Lean ← direction: positive → bullish; negative → bearish; mixed/none → neutral.
- Magnitude ← novelty × material weight: high novelty + material → higher magnitude.
- Confidence scales with headline count; fewer than 3 articles caps confidence low.
- Conflicting direction signals across articles → mixed → neutral with low confidence.

MUST cover ALL tickers: {tickers}

--- HEADLINES & SUMMARIES ---
{news_context}
"""
```

- [ ] **Edit** `tests/unit/test_news_prompt_render.py` — add an assertion that the rendered instruction contains the substring `"Report schema:"` and `"drivers  2-4 entries"`. Read the test file first to follow its style; the existing assertions remain.

### Step 4.4: Extend the Fundamental prompt

- [ ] **Read** `src/agents/analysts/fundamental/prompts.py` to understand its template structure (mirrors news/prompts.py).

- [ ] **Edit** `src/agents/analysts/fundamental/prompts.py` — append a Report schema block analogous to the News one. Use the same wording for the schema (summary, drivers) so the LLM is asked for the same shape. The decision-rule block above stays.

- [ ] **Edit** `tests/unit/test_fundamental_prompt_render.py` — add an assertion that the rendered instruction contains `"Report schema:"`.

### Step 4.5: Persist `report` through the after-callback

The after-callback in `src/agents/analysts/_common.py` converts each `TickerVerdict` into an `AnalystEvidence`. The new `report` field is on `AnalystVerdict` (the parent class), so as long as the callback passes the whole verdict into `AnalystEvidence.verdict`, the field is preserved automatically.

- [ ] **Read** `src/agents/analysts/_common.py` — confirm the callback constructs `AnalystEvidence(... verdict=v ...)` where `v` is the `TickerVerdict`. If yes, no change needed; the field flows through.

- [ ] **If the callback only forwards subset fields** (e.g. it does `AnalystVerdict(lean=v.lean, ...)`), edit it to pass `report=v.report` as well.

### Step 4.6: Verify

- [ ] **Run:**

```bash
.venv/bin/python -m pytest tests/unit/contract/ tests/unit/test_news_prompt_render.py tests/unit/test_fundamental_prompt_render.py -v
```
Expected: PASS.

### Step 4.7: Lint + commit

- [ ] **Lint** and append to `graphify-out/graph_delta.md`:

```
## 2026-05-14 — AnalystReport schema + LLM analyst prompt extension

Phase 5 analyst-surface redesign Task 4.

- New nodes: contract.evidence.ReportDriver, contract.evidence.AnalystReport.
- AnalystVerdict gains optional .report field (None for deterministic
  analysts, populated by News + Fundamental).
- News + Fundamental prompts instruct the LLM to emit a 2-4 driver report
  alongside the existing verdict.
```

- [ ] **Commit:**

```bash
git add src/contract/evidence.py src/agents/analysts/news/prompts.py \
        src/agents/analysts/fundamental/prompts.py src/agents/analysts/_common.py \
        tests/unit/contract/test_analyst_report.py \
        tests/unit/test_news_prompt_render.py tests/unit/test_fundamental_prompt_render.py
git commit -m "$(cat <<'EOF'
feat(phase5): AnalystReport schema + News/Fundamental prompt extension

LLM analysts now emit a structured ``report`` (summary + 2-4 weighted
drivers) alongside the existing closed-vocab verdict. Strategist surface
will pick this up in the next task. Deterministic analysts leave
``AnalystVerdict.report = None``.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Strategist prompt restructure — per-ticker block with feature bullets + report

**Files:**
- Create: `src/contract/strategist_prompt.py` — per-ticker block renderer + feature-bullet registry
- Modify: `src/agents/strategist/prompts.py` (or wherever the Strategist instruction template lives — verify in step 5.1) — invoke the renderer
- Modify: `src/agents/strategist/agent.py` — make sure a state key carrying the rendered text is populated before the strategist runs
- Test: `tests/unit/contract/test_strategist_prompt_layout.py` (new)
- Test: `tests/integration/test_strategist_v2_smoke.py` — extend fixture to include a `report`

### Step 5.1: Locate the strategist prompt rendering surface

- [ ] **Read** `src/agents/strategist/agent.py` and the sibling `prompts.py` (also `schema.py`, `derivation.py`, `evidence_view.py`) to identify:
  - Which state key carries the per-ticker evidence string sent to the LLM (likely populated by `evidence_view.py`).
  - Whether the template uses an ADK placeholder (`{evidence_view}` or similar) — if yes, we'll write into that key. If the prompt is assembled inline in Python, we'll insert the renderer call there.

### Step 5.2: Write a failing test for the renderer

- [ ] **Create** `tests/unit/contract/__init__.py` if missing.

- [ ] **Create** `tests/unit/contract/test_strategist_prompt_layout.py`:

```python
"""Snapshot-style test for the per-ticker block the Strategist sees."""
from __future__ import annotations

from datetime import datetime

from contract.evidence import (
    AnalystEvidence,
    AnalystReport,
    AnalystVerdict,
    ReportDriver,
)
from contract.strategist_prompt import render_ticker_block
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _tech_evidence() -> AnalystEvidence:
    return AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="t",
        recorded_at=datetime(2026, 5, 13),
        features={
            "rsi_14":                  76.0,
            "pct_change_20d":           0.123,
            "pct_change_5d":            0.041,
            "dist_from_high_52w_pct": -0.0,
            "dist_from_low_52w_pct":   84.2,
            "vol_ratio_20d":            1.10,
            "atr_pct_14":               2.07,
        },
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean="bearish", magnitude=0.49, confidence=0.90,
            rationale="trend_up_20d, rsi_overbought, near_52w_high",
            key_factors=["trend_up_20d", "rsi_overbought", "near_52w_high"],
        ),
    )


def _news_evidence() -> AnalystEvidence:
    return AnalystEvidence(
        ticker="AAPL",
        analyst="news",
        tick_id="t",
        recorded_at=datetime(2026, 5, 13),
        features={"article_count_7d": 50.0},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean="neutral", magnitude=0.3, confidence=0.7,
            rationale="catalyst:legal, novelty:low",
            key_factors=["catalyst:legal", "catalyst:regulatory", "novelty:low", "direction:mixed"],
            report=AnalystReport(
                summary="Two converging negatives this tick.",
                drivers=[
                    ReportDriver(name="EU App Store ruling", direction="bear", weight=0.5,
                                 body="EU mandates third-party stores. Material to services revenue."),
                    ReportDriver(name="Gemini on Android push", direction="bear", weight=0.3,
                                 body="Search distribution risk widens."),
                ],
            ),
        ),
    )


def _ticker_evidence() -> TickerEvidence:
    return TickerEvidence(
        ticker="AAPL",
        tick_id="t",
        recorded_at=datetime(2026, 5, 13),
        per_analyst={
            "technical": _tech_evidence(),
            "news":      _news_evidence(),
        },
        aggregate=AggregateVerdict(
            lean="bearish", magnitude=0.4, confidence=0.8,
            disagreement=0.2, summary="1 bearish / 1 neutral",
        ),
        weights={"technical": 1.0, "news": 1.0},
    )


def test_render_block_contains_section_headers_and_features() -> None:
    """The rendered block names each analyst, surfaces features as bullets, and shows the report."""
    block = render_ticker_block(_ticker_evidence())

    assert "=== AAPL ===" in block
    assert "[Technical]" in block
    assert "RSI(14):" in block
    assert "76.0" in block
    assert "[News]" in block
    assert "Report summary:" in block
    assert "EU App Store ruling" in block
    assert "Drivers:" in block


def test_render_block_omits_report_section_when_none() -> None:
    """Deterministic analysts (report=None) get only the feature bullets, no Drivers block."""
    ev = _ticker_evidence()
    block = render_ticker_block(ev)
    # The Technical analyst has no report and should not get a Drivers: line.
    technical_section = block.split("[News]")[0]
    assert "Drivers:" not in technical_section
    assert "Report summary:" not in technical_section
```

- [ ] **Run to confirm failure:**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v
```
Expected: `ImportError: cannot import name 'render_ticker_block' from 'contract.strategist_prompt'`.

### Step 5.3: Implement the renderer + bullet registry

- [ ] **Create** `src/contract/strategist_prompt.py`:

```python
"""Renderer for the per-ticker block in the Strategist prompt.

For each of the five analyst slots, this module emits a uniform-looking
section showing the analyst's verdict header, its deterministic features as
labelled bullets (where applicable), and — for LLM analysts — its
``AnalystReport`` summary + driver list.

The feature-bullet registries below are the source of truth for what numerics
the Strategist sees. Adding a new feature is a one-line entry.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from contract.evidence import AnalystEvidence, AnalystReport
from contract.ticker_evidence import TickerEvidence


# ---------------------------------------------------------------------------
# Feature-bullet registry
# ---------------------------------------------------------------------------
# Each entry: (feature_key, human_label, formatter)
# A None-formatter means "skip if the key is missing".

@dataclass(frozen=True)
class Bullet:
    """One labelled feature line in the strategist prompt."""
    key:    str
    label:  str
    format: Callable[[float], str]


def _pct_signed(v: float) -> str:
    """Format a fraction as a signed percentage with one decimal."""
    return f"{v * 100:+.1f}%"


def _pct_unscaled_signed(v: float) -> str:
    """Format an already-percentage value with a sign."""
    return f"{v:+.1f}%"


def _float_1dp(v: float) -> str:
    """Format a float with one decimal place."""
    return f"{v:.1f}"


def _ratio_2dp(v: float) -> str:
    """Format a multiplier like ``1.10x``."""
    return f"{v:.2f}x"


TECHNICAL_BULLETS: list[Bullet] = [
    Bullet("rsi_14",                  "RSI(14):",                _float_1dp),
    Bullet("pct_change_20d",          "20d momentum:",           _pct_signed),
    Bullet("pct_change_5d",           "5d momentum:",            _pct_signed),
    Bullet("dist_from_high_52w_pct",  "Distance from 52w high:", _pct_unscaled_signed),
    Bullet("dist_from_low_52w_pct",   "Distance from 52w low:",  _pct_unscaled_signed),
    Bullet("vol_ratio_20d",           "Volume vs 20d avg:",      _ratio_2dp),
    Bullet("atr_pct_14",              "ATR%(14):",               _float_1dp),
]


# Fundamental + News + Social + SmartMoney bullet registries — fill in once
# the extractor key catalogues are confirmed for each analyst. Each entry is
# the same Bullet shape as above.
FUNDAMENTAL_BULLETS: list[Bullet] = [
    # Examples — replace with the real keys from contract/extractors/fundamental.py:
    Bullet("pe_trailing",       "P/E (trailing):",      _float_1dp),
    Bullet("pe_forward",        "P/E (forward):",       _float_1dp),
    Bullet("insider_net_30d",   "Insider net 30d ($):", lambda v: f"{v:,.0f}"),
]

NEWS_BULLETS: list[Bullet] = [
    Bullet("article_count_7d", "Article count 7d:", _float_1dp),
]

SMART_MONEY_BULLETS: list[Bullet] = []  # extend once extractor keys are known
SOCIAL_BULLETS:      list[Bullet] = []

_REGISTRY: dict[str, list[Bullet]] = {
    "technical":    TECHNICAL_BULLETS,
    "fundamental":  FUNDAMENTAL_BULLETS,
    "news":         NEWS_BULLETS,
    "smart_money":  SMART_MONEY_BULLETS,
    "social":       SOCIAL_BULLETS,
}


def _render_features(ev: AnalystEvidence) -> list[str]:
    """Render the feature-bullet block for one analyst."""
    bullets = _REGISTRY.get(ev.analyst, [])
    lines: list[str] = []
    for b in bullets:
        if b.key not in ev.features:
            continue
        lines.append(f"  {b.label:<26} {b.format(ev.features[b.key])}")
    return lines


def _render_report(report: AnalystReport) -> list[str]:
    """Render an LLM analyst's report (summary + drivers)."""
    lines = ["  -> Report summary:", f"     {report.summary}", "  -> Drivers:"]
    for d in report.drivers:
        lines.append(f"       * {d.name}  ({d.direction}, w={d.weight:.2f}):")
        lines.append(f"         {d.body}")
    return lines


def _render_analyst(ev: AnalystEvidence) -> list[str]:
    """Render one analyst's slot — header + features + (optional) report."""
    analyst_label = ev.analyst.replace("_", " ").title()

    if ev.verdict.is_no_data:
        return [f"[{analyst_label}]  is_no_data: true"]

    header = (
        f"[{analyst_label}]  "
        f"lean: {ev.verdict.lean}  "
        f"magnitude: {ev.verdict.magnitude:.2f}  "
        f"confidence: {ev.verdict.confidence:.2f}"
    )
    lines = [header]
    lines.extend(_render_features(ev))

    if ev.verdict.key_factors:
        lines.append(f"  -> Closed-vocab tags: {', '.join(ev.verdict.key_factors)}")

    if ev.verdict.report is not None:
        lines.extend(_render_report(ev.verdict.report))

    return lines


def render_ticker_block(te: TickerEvidence) -> str:
    """Render the full per-ticker block sent to the Strategist for one ticker.

    Section ordering: Technical, Fundamental, News, SmartMoney, Social. Any
    analyst absent from ``te.per_analyst`` is rendered as ``is_no_data: true``
    via the digest's neutral-fill convention.

    Parameters
    ----------
    te:
        The ``TickerEvidence`` aggregate produced by ``contract.digest``.

    Returns
    -------
    str
        Multi-line block ready for direct inclusion in the strategist prompt.
    """
    out: list[str] = [f"=== {te.ticker} ===", ""]
    for analyst in ("technical", "fundamental", "news", "smart_money", "social"):
        ev = te.per_analyst.get(analyst)
        if ev is None:
            out.append(f"[{analyst.replace('_', ' ').title()}]  is_no_data: true")
            out.append("")
            continue
        out.extend(_render_analyst(ev))
        out.append("")

    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Run the test:**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_strategist_prompt_layout.py -v
```
Expected: PASS.

### Step 5.4: Wire the renderer into the strategist prompt path

- [ ] **Based on the audit in 5.1**, edit the strategist module that builds the LLM prompt to call `render_ticker_block(...)` for each ticker in the evidence view, joining the blocks with `"\n"`, and place the result either in the prompt string or in the state key referenced by the instruction template.

- [ ] **Edit** the strategist's instruction block to add (or update) the one-line guidance:

```
Where an analyst's report contradicts its lean, the lean is the analyst's
final call — treat the report as their reasoning, not their conclusion. You
may still override an analyst, but you must write down which signal you chose
to overweight and why.
```

(Locate the exact insertion point — usually at the end of the existing rule list, before the per-ticker block placeholder.)

### Step 5.5: Extend integration smoke test fixtures

- [ ] **Read** `tests/integration/test_strategist_v2_smoke.py` to find where it builds the LLM analyst verdicts.

- [ ] **Edit** the fixture to add a populated `AnalystReport` on the news (and fundamental if present) verdicts. The strategist response shape is unchanged; this just confirms the new prompt surface doesn't break the existing run.

- [ ] **Run:**

```bash
.venv/bin/python -m pytest tests/integration/test_strategist_v2_smoke.py -v
```
Expected: PASS.

### Step 5.6: Lint + commit

- [ ] **Lint** + append to `graphify-out/graph_delta.md`:

```
## 2026-05-14 — Strategist sees per-ticker feature bullets + LLM reports

Phase 5 analyst-surface redesign Task 5.

- New node: contract.strategist_prompt.render_ticker_block + feature-bullet
  registries.
- Strategist instruction gets a short addition on lean-vs-report priority.
- All five analysts now appear at equal visual weight in the prompt.
```

- [ ] **Commit:**

```bash
git add src/contract/strategist_prompt.py \
        src/agents/strategist/  \
        tests/unit/contract/test_strategist_prompt_layout.py \
        tests/integration/test_strategist_v2_smoke.py
# Restrict the strategist staged path to the file(s) actually edited.
git commit -m "$(cat <<'EOF'
feat(phase5): per-ticker strategist surface — feature bullets + LLM reports

Strategist now sees every analyst at equal visual weight: deterministic
analysts surface their numeric features as labelled bullets, LLM analysts
add the new AnalystReport summary + drivers block. Feature-bullet registry
lives in contract.strategist_prompt so adding a feature is a one-liner.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Report cache — hash-based memoisation for News + Fundamental LLM calls

**Files:**
- Create: `src/agents/analysts/report_cache.py` — hash + disk-IO primitives, version constants
- Modify: `src/agents/analysts/news/agent.py` — `before_model_callback` consults cache; `after_model_callback` writes cache
- Modify: `src/agents/analysts/fundamental/agent.py` — same
- Modify: `src/agents/analysts/news/fetch.py` — surface the article list in a state key the cache hook can read
- Modify: `src/agents/analysts/fundamental/fetch.py` — surface ratios + filings + insider in state for hashing
- Test: `tests/unit/agents/test_report_cache_hash.py` (new) — hash stability + sensitivity
- Test: `tests/integration/test_news_cache_roundtrip.py` (new)
- Test: `tests/integration/test_news_cache_invalidation.py` (new)
- Test: `tests/integration/test_news_cache_prompt_version.py` (new)

### Step 6.1: Write failing hash tests

- [ ] **Create** `tests/unit/agents/test_report_cache_hash.py`:

```python
"""Unit tests for the report-cache hash primitives."""
from __future__ import annotations

from datetime import date, datetime

from agents.analysts.report_cache import (
    fundamental_hash_inputs,
    news_hash_inputs,
)
from data.models import (
    CompanyRatios,
    Filing,
    Form4Bundle,
    InsiderTrade,
    NewsArticle,
)


def _article(url: str, published: str) -> NewsArticle:
    """Build a minimal NewsArticle for testing."""
    return NewsArticle(
        url=url,
        title="t",
        summary="s",
        published_at=datetime.fromisoformat(published),
        source="src",
        ticker="AAPL",
    )


def test_news_hash_stable_under_reordering() -> None:
    """The hash must be insensitive to article ordering."""
    a = _article("https://a", "2026-05-13T10:00:00")
    b = _article("https://b", "2026-05-13T11:00:00")
    assert news_hash_inputs([a, b]) == news_hash_inputs([b, a])


def test_news_hash_changes_on_new_article() -> None:
    """Adding a single article must invalidate the cache."""
    a = _article("https://a", "2026-05-13T10:00:00")
    b = _article("https://b", "2026-05-13T11:00:00")
    c = _article("https://c", "2026-05-13T12:00:00")
    assert news_hash_inputs([a, b]) != news_hash_inputs([a, b, c])


def test_fundamental_hash_stable_under_float_jitter() -> None:
    """Float jitter at the 5th decimal place must NOT bust the cache."""
    r1 = CompanyRatios(ticker="AAPL", trailing_pe=36.23879)
    r2 = CompanyRatios(ticker="AAPL", trailing_pe=36.23880)
    bundle = Form4Bundle(trades=[], derivatives=[])
    assert fundamental_hash_inputs(r1, [], bundle) == fundamental_hash_inputs(r2, [], bundle)


def test_fundamental_hash_changes_on_new_filing() -> None:
    """Adding a Filing must invalidate the cache."""
    r = CompanyRatios(ticker="AAPL", trailing_pe=36.0)
    bundle = Form4Bundle(trades=[], derivatives=[])
    f1 = Filing(
        ticker="AAPL", form_type="10-Q", filed_at=date(2026, 5, 1),
        accession_no="A1", mda_excerpt="m", risk_factors_excerpt="r",
    )
    f2 = Filing(
        ticker="AAPL", form_type="8-K",  filed_at=date(2026, 5, 10),
        accession_no="A2", mda_excerpt="m", risk_factors_excerpt="r",
    )
    assert fundamental_hash_inputs(r, [f1], bundle) != fundamental_hash_inputs(r, [f1, f2], bundle)


def test_fundamental_hash_changes_on_new_insider_trade() -> None:
    """Adding a Form 4 trade must invalidate the cache."""
    r = CompanyRatios(ticker="AAPL", trailing_pe=36.0)
    t = InsiderTrade(
        insider_name="J Doe", insider_title="CFO", side="sell",
        shares=1000, price_per_share=210.0,
        transaction_date=date(2026, 5, 12), is_10b5_1=False,
    )
    b1 = Form4Bundle(trades=[],  derivatives=[])
    b2 = Form4Bundle(trades=[t], derivatives=[])
    assert fundamental_hash_inputs(r, [], b1) != fundamental_hash_inputs(r, [], b2)
```

(Adapt the `Filing` / `InsiderTrade` constructor kwargs to match the actual field names — read `src/data/models/filings.py` and `src/data/models/trades.py` and fix the fixture accordingly.)

- [ ] **Run to confirm failure:**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_report_cache_hash.py -v
```
Expected: `ModuleNotFoundError: No module named 'agents.analysts.report_cache'`.

### Step 6.2: Implement the cache primitives

- [ ] **Create** `src/agents/analysts/report_cache.py`:

```python
"""Hash-based LLM report cache — memoises (verdict, report) on input identity.

Cache layout: ``<root>/<analyst>/<ticker>.json``. Each file is a single-entry
record; the next miss overwrites. ``<root>`` is read from
``config/analysts.json`` -> ``cache.directory`` and is always under the
gitignored ``cache/`` tree.

The cache key is ``(input_hash, prompt_version)``:

- ``input_hash``       — blake2b digest of the analyst's view of the world for
                         this ticker (article URL+published tuples for News;
                         ratios + filing accession numbers + Form 4 records
                         for Fundamental).
- ``prompt_version``   — short string baked into the analyst module; bump
                         when the prompt template or closed vocabulary
                         changes to invalidate every cached entry.

Both pieces must match for a hit. Anything else is a miss -> LLM is called
-> cache is overwritten with the fresh ``(verdict, report)``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path
from typing import Any

from data.models import (
    CompanyRatios,
    Filing,
    Form4Bundle,
    NewsArticle,
)


# ---------------------------------------------------------------------------
# Prompt-version fingerprints — bump when prompt or closed vocab changes.
# ---------------------------------------------------------------------------
NEWS_PROMPT_VERSION         = "2026-05-14-a"
FUNDAMENTAL_PROMPT_VERSION  = "2026-05-14-a"


def _digest(payload: Any) -> str:
    """Return a hex blake2b digest of a JSON-serialised payload."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"blake2b:{blake2b(blob, digest_size=16).hexdigest()}"


def news_hash_inputs(articles: list[NewsArticle]) -> str:
    """Hash the News analyst's view of the world for one ticker.

    The hash is sensitive to the set of article (URL, published_at) pairs only —
    summary text drift does not bust the cache, but a new article rolling in
    or an old one rolling out does.
    """
    items = sorted(
        (
            a.url if hasattr(a, "url") else a.get("url", ""),
            (a.published_at.isoformat() if hasattr(a, "published_at")
             else a.get("published_at", "")),
        )
        for a in articles
    )
    return _digest(items)


def fundamental_hash_inputs(
    ratios:  CompanyRatios,
    filings: list[Filing],
    insider: Form4Bundle,
) -> str:
    """Hash the Fundamental analyst's view of the world for one ticker.

    Ratios floats are rounded to 4 decimal places so insignificant jitter
    (e.g. ``pe = 36.23879 -> 36.23880``) does not bust the cache. Filings are
    keyed by accession number; insider trades by ``(name, date, shares,
    price_per_share)``; derivatives by ``(name, date, transaction_code)``.
    """
    ratios_payload = {
        k: (round(v, 4) if isinstance(v, float) else v)
        for k, v in ratios.model_dump().items()
    }

    payload = {
        "ratios": ratios_payload,
        "filings": sorted(f.accession_no for f in filings),
        "insider_trades": sorted(
            (
                t.insider_name,
                t.transaction_date.isoformat(),
                t.shares,
                round(t.price_per_share, 2),
            )
            for t in (insider.trades if insider else [])
        ),
        "insider_derivatives": sorted(
            (
                d.insider_name,
                d.transaction_date.isoformat(),
                d.transaction_code,
            )
            for d in (insider.derivatives if insider else [])
        ),
    }
    return _digest(payload)


# ---------------------------------------------------------------------------
# Disk IO
# ---------------------------------------------------------------------------

def _cache_path(root: Path, analyst: str, ticker: str) -> Path:
    """Path to the cache file for one ``(analyst, ticker)`` pair."""
    return root / analyst / f"{ticker.upper()}.json"


def read_cache(
    root: Path, analyst: str, ticker: str, *, input_hash: str, prompt_version: str,
) -> dict | None:
    """Load the cache entry iff both ``input_hash`` and ``prompt_version`` match.

    Returns
    -------
    dict | None
        ``{"verdict": ..., "report": ...}`` on a hit; ``None`` on a miss
        (no file, hash mismatch, or version mismatch). Any IO / JSON error is
        treated as a miss — the LLM call is the safe fallback.
    """
    path = _cache_path(root, analyst, ticker)
    if not path.exists():
        return None

    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None

    if record.get("input_hash") != input_hash:
        return None
    if record.get("prompt_version") != prompt_version:
        return None

    return {"verdict": record.get("verdict"), "report": record.get("report")}


def write_cache(
    root: Path, analyst: str, ticker: str,
    *,
    input_hash:     str,
    prompt_version: str,
    verdict:        dict,
    report:         dict | None,
) -> None:
    """Atomically write a fresh cache entry.

    Uses ``os.replace`` for atomicity so partial writes don't leave the file
    in an unparseable state. Creates parent directories as needed.
    """
    path = _cache_path(root, analyst, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "input_hash":     input_hash,
        "prompt_version": prompt_version,
        "verdict":        verdict,
        "report":         report,
        "stored_at":      datetime.now(timezone.utc).isoformat(),
    }

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)
```

- [ ] **Run the hash tests:**

```bash
.venv/bin/python -m pytest tests/unit/agents/test_report_cache_hash.py -v
```
Expected: PASS.

### Step 6.3: Surface input data in state for the cache hook

The `before_model_callback` runs after `before_agent_callback` (the fetch), so the fetches have already populated `state["news_data"]` and `state["fundamental_data"]`. The cache hook reads those keys directly.

- [ ] **Confirm the state layout** — no edits to the fetch callbacks are needed for the cache *itself*, but it is useful to also surface the typed objects (not just `model_dump()` dicts) so the hash function doesn't need to re-deserialise. If easier, the cache hook can re-build typed objects from the dicts.

- [ ] **Decision:** keep state dict-typed; the hash function above is already tolerant of both objects and `model_dump()` dicts for News articles. Make the same allowance in `fundamental_hash_inputs` if needed — read the production state shape and adapt.

### Step 6.4: Wire the cache into the News agent

The cache hook short-circuits the LLM by returning a fully-populated `genai_types.Content` from `before_model_callback`. ADK respects the early return and skips the LLM round-trip; the subsequent `after_agent_callback` (which builds `AnalystEvidence`) still runs against the cached verdict written into `state["news_verdicts"]`.

- [ ] **Edit** `src/agents/analysts/news/agent.py`. Replace the `_build_news_analyst` function body with cache-aware wiring:

```python
def _build_news_analyst(vocab: NewsVocabulary) -> LlmAgent:
    """Construct a fresh ``NewsAnalyst`` LlmAgent with closed-vocab prompt + cache."""
    instruction = build_news_instruction(vocab)
    model = "gemini-2.5-flash-lite"

    # Attach LLM trace callbacks only in trace mode — zero-cost gate.
    trace_before = None
    trace_after  = None
    if os.environ.get("STOCKBOT_TRACE") == "1":
        trace_before, trace_after = make_llm_trace_callbacks("03_news_llm", model=model)

    cache_before, cache_after = _build_news_cache_callbacks()

    # Chain: cache first (may short-circuit), then trace.
    before_cb = _chain_before(cache_before, trace_before)
    after_cb  = _chain_after(cache_after, trace_after)

    return LlmAgent(
        name="NewsAnalyst",
        model=model,
        instruction=instruction,
        output_schema=VerdictBatch,
        output_key="news_verdicts",
        before_agent_callback=news_fetch_callback,
        after_agent_callback=make_evidence_callback(
            analyst="news",
            extractor=extract_news_features,
            verdicts_state_key="news_verdicts",
        ),
        before_model_callback=before_cb,
        after_model_callback=after_cb,
    )
```

  - Above that function, add helpers (the chain helpers are tiny wrappers that call each callback in order and short-circuit on the first non-None return from `before`):

```python
from pathlib import Path

from agents.analysts.report_cache import (
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
    read_cache,
    write_cache,
)
from config.analysts import get_analysts_config
from contract.evidence import VerdictBatch as _VerdictBatch  # already imported above


def _build_news_cache_callbacks():
    """Return (before, after) hooks that consult/update the news report cache."""
    cfg = get_analysts_config().cache
    root = Path(cfg.directory)

    def _before(callback_context, llm_request):
        """Short-circuit the LLM if every watchlist ticker hits the cache."""
        if not cfg.enabled:
            return None

        state = callback_context.state
        tickers: list[str] = state.get("tickers", []) or []
        news_data: dict = state.get("news_data", {}) or {}

        cached_verdicts = []
        for ticker in tickers:
            articles = (news_data.get(ticker) or {}).get("news") or []
            input_hash = news_hash_inputs(articles)
            hit = read_cache(
                root, "news", ticker,
                input_hash=input_hash, prompt_version=NEWS_PROMPT_VERSION,
            )
            if hit is None:
                return None  # any miss -> run the full LLM call
            v = hit["verdict"]
            if hit["report"] is not None:
                v = {**v, "report": hit["report"]}
            cached_verdicts.append({**v, "ticker": ticker})

        # All tickers hit -> write the cached batch into the output_key and
        # short-circuit the LLM by returning a synthetic Content response.
        state["news_verdicts"] = _VerdictBatch.model_validate(
            {"verdicts": cached_verdicts}
        ).model_dump()

        # Emit a trace marker if a writer is active.
        try:
            tw = state.get("_trace")
        except (AttributeError, TypeError):
            tw = None
        if tw is not None:
            from observability.trace import TraceWriter
            if isinstance(tw, TraceWriter):
                tw.llm_pair(
                    "03_news_llm",
                    prompt=f"(cache hit — all tickers, prompt_version={NEWS_PROMPT_VERSION})",
                    response="(loaded from cache/reports/news/<ticker>.json)",
                    model="cache",
                )

        # Returning a Content object skips the model call. An empty text part
        # is sufficient because we've already populated state["news_verdicts"].
        from google.genai import types as genai_types
        return genai_types.Content(parts=[genai_types.Part.from_text(text="(cached)")])

    def _after(callback_context, llm_response):
        """On a real LLM response, persist (verdict, report) per ticker."""
        if not cfg.enabled:
            return None

        state = callback_context.state
        batch = state.get("news_verdicts") or {}
        if isinstance(batch, dict):
            verdicts = batch.get("verdicts", [])
        else:
            verdicts = getattr(batch, "verdicts", [])

        news_data: dict = state.get("news_data", {}) or {}

        for v in verdicts:
            v_dict = v if isinstance(v, dict) else v.model_dump()
            ticker = v_dict.get("ticker")
            if not ticker:
                continue
            articles = (news_data.get(ticker) or {}).get("news") or []
            input_hash = news_hash_inputs(articles)
            verdict_payload = {k: val for k, val in v_dict.items() if k != "report"}
            report_payload  = v_dict.get("report")
            write_cache(
                root, "news", ticker,
                input_hash=input_hash, prompt_version=NEWS_PROMPT_VERSION,
                verdict=verdict_payload, report=report_payload,
            )

        return None

    return _before, _after


def _chain_before(*callbacks):
    """Run before-callbacks in order; first non-None return short-circuits."""
    callbacks = [c for c in callbacks if c is not None]
    if not callbacks:
        return None

    def _chained(ctx, llm_request):
        for cb in callbacks:
            result = cb(ctx, llm_request)
            if result is not None:
                return result
        return None
    return _chained


def _chain_after(*callbacks):
    """Run after-callbacks in order; all are invoked unconditionally."""
    callbacks = [c for c in callbacks if c is not None]
    if not callbacks:
        return None

    def _chained(ctx, llm_response):
        for cb in callbacks:
            cb(ctx, llm_response)
        return None
    return _chained
```

### Step 6.5: Mirror the wiring in the Fundamental agent

- [ ] **Edit** `src/agents/analysts/fundamental/agent.py` — mirror the same `_build_*_cache_callbacks`, `_chain_before`, `_chain_after` pattern. Differences:
  - Section label `"03_fundamental_llm"`.
  - Cache directory subkey is `"fundamental"`.
  - Hash function is `fundamental_hash_inputs(ratios, filings, insider)`. Reconstruct typed objects from `state["fundamental_data"][ticker]`:

```python
            triad = state.get("fundamental_data", {}).get(ticker) or {}
            ratios_dict  = triad.get("ratios")  or {"ticker": ticker}
            filings_raw  = triad.get("filings") or []
            insider_obj  = triad.get("insider") or Form4Bundle(trades=[], derivatives=[])
            ratios   = CompanyRatios.model_validate(ratios_dict)
            filings  = [Filing.model_validate(f) if isinstance(f, dict) else f for f in filings_raw]
            input_hash = fundamental_hash_inputs(ratios, filings, insider_obj)
```

  - Use `FUNDAMENTAL_PROMPT_VERSION` and `output_key="fundamental_verdicts"` (verify the actual output key name in the existing agent).

### Step 6.6: Integration test — cache hit on identical inputs

- [ ] **Create** `tests/integration/test_news_cache_roundtrip.py`:

```python
"""Integration test: News analyst is short-circuited by the report cache on a 2nd run."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Build a minimal harness that calls the cache's before/after hooks directly,
# in place of running the full ADK pipeline — far more hermetic.

from agents.analysts.news.agent import _build_news_cache_callbacks
from data.models import NewsArticle


@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """Point AnalystsConfig at a tmp_path cache directory."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500, "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5, "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": str(tmp_path / "cache")},
    }))
    from config import analysts as cfg_mod
    cfg_mod.get_analysts_config.cache_clear()
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", cfg_file)
    yield tmp_path / "cache"
    cfg_mod.get_analysts_config.cache_clear()


def test_second_run_hits_cache(cache_root):
    """Identical article set on two consecutive runs -> 2nd run short-circuits the LLM."""
    before, after = _build_news_cache_callbacks()

    articles = [
        NewsArticle(
            url="https://x", title="t", summary="s",
            published_at="2026-05-13T10:00:00", source="src", ticker="AAPL",
        ).model_dump()
    ]

    class _Ctx:
        state = {
            "tickers": ["AAPL"],
            "news_data": {"AAPL": {"news": articles}},
        }

    # First run — cache miss, simulate an LLM response by populating the output key.
    assert before(_Ctx, llm_request=None) is None
    _Ctx.state["news_verdicts"] = {
        "verdicts": [{
            "ticker": "AAPL", "lean": "neutral", "magnitude": 0.3, "confidence": 0.7,
            "rationale": "x", "key_factors": [], "is_no_data": False,
            "report": {"summary": "s", "drivers": [
                {"name": "n1", "direction": "neutral", "weight": 0.5, "body": "b"},
                {"name": "n2", "direction": "neutral", "weight": 0.5, "body": "b"},
            ]},
        }],
    }
    after(_Ctx, llm_response=None)

    # Second run with identical inputs — before-callback returns a non-None Content
    # (short-circuit) and writes verdicts into state from the cache.
    _Ctx.state.pop("news_verdicts", None)
    short_circuit = before(_Ctx, llm_request=None)
    assert short_circuit is not None
    assert _Ctx.state["news_verdicts"]["verdicts"][0]["ticker"] == "AAPL"
```

- [ ] **Run:**

```bash
.venv/bin/python -m pytest tests/integration/test_news_cache_roundtrip.py -v
```
Expected: PASS.

### Step 6.7: Integration tests — invalidation + prompt version

- [ ] **Create** `tests/integration/test_news_cache_invalidation.py` — same harness as above, but add a second article between the two runs and assert the second `before` call returns `None` (cache miss).

- [ ] **Create** `tests/integration/test_news_cache_prompt_version.py` — same harness, but between runs monkeypatch `agents.analysts.report_cache.NEWS_PROMPT_VERSION` to `"v2"` and assert the second `before` call returns `None`.

- [ ] **Run:**

```bash
.venv/bin/python -m pytest tests/integration/test_news_cache_roundtrip.py \
                          tests/integration/test_news_cache_invalidation.py \
                          tests/integration/test_news_cache_prompt_version.py -v
```
Expected: all PASS.

### Step 6.8: Make sure `cache/` is gitignored

- [ ] **Check** `.gitignore`:

```bash
grep -n "^cache/\|^/cache/" .gitignore
```

- [ ] **If absent**, append `cache/` to `.gitignore`.

### Step 6.9: Lint + commit

- [ ] **Lint** + append to `graphify-out/graph_delta.md`:

```
## 2026-05-14 — LLM report cache for News + Fundamental

Phase 5 analyst-surface redesign Task 6.

- New node: agents.analysts.report_cache (news_hash_inputs,
  fundamental_hash_inputs, read_cache, write_cache, prompt-version
  constants).
- News + Fundamental agents wrap their LLM calls with cache-aware
  before/after callbacks. Cache hit short-circuits the LLM by returning
  a synthetic Content from before_model_callback.
- Disk layout: cache/reports/<analyst>/<ticker>.json (gitignored).
```

- [ ] **Commit:**

```bash
git add src/agents/analysts/report_cache.py \
        src/agents/analysts/news/agent.py \
        src/agents/analysts/fundamental/agent.py \
        tests/unit/agents/test_report_cache_hash.py \
        tests/integration/test_news_cache_roundtrip.py \
        tests/integration/test_news_cache_invalidation.py \
        tests/integration/test_news_cache_prompt_version.py \
        .gitignore
git commit -m "$(cat <<'EOF'
feat(phase5): hash-based LLM report cache for News + Fundamental

(verdict, report) is memoised on the analyst's input set (article URL+date
tuples for News; ratios + filing accession numbers + Form 4 records for
Fundamental) plus a prompt-version fingerprint. A cache hit short-circuits
the LLM round-trip; the verdict and report are loaded straight into state.

Disk layout: cache/reports/<analyst>/<ticker>.json. The directory is
gitignored. Editing a prompt template requires bumping NEWS_PROMPT_VERSION
/ FUNDAMENTAL_PROMPT_VERSION to invalidate cached entries.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Tick cadence — 2/day, DST-aware, ET-keyed config

**Files:**
- Create or modify: `config/schedule.json` (locate first — see Step 7.1)
- Modify: `config/README.md` — describe `schedule.json`
- Modify: whichever runner / cron / scheduler reads the schedule
- Test: `tests/unit/test_scheduler_yaml.py` (if it exists) or `tests/unit/test_schedule_config.py` (new)

### Step 7.1: Locate the current scheduler

- [ ] **Find** the existing schedule:

```bash
grep -rln "tick_times\|schedule\|cron" config/ src/ scripts/ 2>/dev/null
```

- [ ] **Read** the candidates and identify (a) the file where cadence is configured today and (b) the runner code that reads it.

### Step 7.2: Decide the DST mechanism

- [ ] **Choose** based on what's already in the codebase:
  - If a cron-style scheduler is in use → set times as `TZ=America/New_York` in the cron entries. Cron understands timezone files; DST handled by the OS.
  - If an in-process scheduler (`apscheduler`, custom asyncio loop) is in use → use `zoneinfo.ZoneInfo("America/New_York")` to localise the configured time then convert to UTC at scheduling time.

### Step 7.3: Write the config

- [ ] **Create or replace** `config/schedule.json`:

```json
{
  "ticks_per_day": 2,
  "tick_times_et": ["09:45", "16:30"],
  "comment": "09:45 ET runs ~15 min after NYSE open; 16:30 ET runs ~30 min after close. Times are interpreted in America/New_York and DST is handled by the scheduler. Headroom to add 12:30 ET once cache + reports prove themselves on paper data."
}
```

### Step 7.4: Update the runner

- [ ] **Edit** the runner module found in 7.1 to:
  - Read `tick_times_et` from `config/schedule.json`.
  - For each entry, build today's tick datetime in `America/New_York`, convert to UTC via `astimezone(timezone.utc)`, and schedule from there.

- [ ] **Add a small unit test** (`tests/unit/test_schedule_config.py` if no test exists yet) verifying:
  - The config file parses.
  - Each `tick_times_et` string is a valid `HH:MM` 24-hour time.
  - The list has exactly `ticks_per_day` entries.

### Step 7.5: Update `config/README.md`

- [ ] **Append** an entry describing `schedule.json` with both fields and a note that times are interpreted in `America/New_York` and survive DST.

### Step 7.6: Lint + commit

- [ ] **Run** lint + the new schedule test + any pre-existing schedule tests.

- [ ] **Append to** `graphify-out/graph_delta.md`:

```
## 2026-05-14 — tick cadence reduced to 2/day with DST-aware ET-keyed config

Phase 5 analyst-surface redesign Task 7.

- New / modified: config/schedule.json (ticks_per_day=2,
  tick_times_et=["09:45","16:30"]).
- Runner converts ET times via zoneinfo("America/New_York") so DST
  transitions are handled correctly.
```

- [ ] **Commit:**

```bash
git add config/schedule.json config/README.md <runner_path> tests/unit/test_schedule_config.py
git commit -m "$(cat <<'EOF'
chore(phase5): reduce tick cadence to 2/day, DST-aware ET-keyed schedule

Tick times move into config/schedule.json as ET strings; the runner
converts via zoneinfo("America/New_York") so EDT/EST transitions are
handled automatically. 09:45 ET + 16:30 ET — headroom to add a midday
12:30 ET tick once the cache + richer reports prove themselves on paper
data.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Final acceptance: surface-trace dry run

After all seven tasks ship, run the surface-trace harness against AAPL and inspect the result.

- [ ] **Run the trace:**

```bash
STOCKBOT_TRACE=1 PYTHONPATH=src .venv/bin/python -m scripts.trace_tick --ticker AAPL
```

- [ ] **Open** the resulting JSON under `docs/surface-traces/trace-*.json` and verify:

  1. `01_fetch_fundamental.data.AAPL` does **not** contain `price_history` or `bars` (no 252 OHLCV rows).
  2. `01_fetch_fundamental.data.AAPL.ratios` is populated; `01_fetch_technical.data.AAPL.price_history.bars` is populated.
  3. `03_news_llm_in.prompt` contains both `=== system ===` and `=== user ===` headings, and the system block contains the headline text.
  4. `03_news_llm_out.response` parses as a `VerdictBatch` whose first verdict has a populated `report` with 2-4 drivers.
  5. The same for `03_fundamental_llm_in/out`.
  6. **Second run** of `trace_tick` against the same ticker (no underlying article change): `03_news_llm_in.model == "cache"` and `prompt` starts with `"(cache hit"`.

If any item fails, file a follow-up issue under `docs/superpowers/specs/analyst-surface-redesign-design.md`. The acceptance gate stays under that file's § 8 "Live-trace acceptance".

---

## Self-review checklist (performed)

- **Spec coverage:** §1 Data model split → Task 1. §2 Report schema → Task 4. §3 Strategist prompt → Task 5. §4 Report cache → Task 6. §5 Trace fidelity → Task 2. §6 Tick cadence → Task 7. §7 Configuration → Task 3 (caps) + Task 6 (cache subsection) + Task 7 (schedule). §8 Testing — every subsection covered by Step 1.x/2.x/.../6.x test additions.
- **Placeholder scan:** every step contains exact paths, complete code, exact commands. The only "TBD" is the deliberate audit-then-edit in Step 5.1 (strategist prompt path) — the spec acknowledges this implementation detail.
- **Type consistency:** `PriceHistory.bars`, `CompanyRatios.ticker`, `AnalystReport.drivers`, `ReportDriver.weight`, `news_hash_inputs(list[NewsArticle])`, `fundamental_hash_inputs(CompanyRatios, list[Filing], Form4Bundle)`, prompt-version constants `NEWS_PROMPT_VERSION` + `FUNDAMENTAL_PROMPT_VERSION` — names are referenced identically across tasks.
- **British-English review:** comments and docstrings use `behaviour`, `normalise`, `synthesise`, `colour-free` text (no en-US spellings introduced).
